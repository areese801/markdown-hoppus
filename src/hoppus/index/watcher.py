"""
File watcher: incremental index updates with graceful degradation
(spec §8, HOPPUS-37) plus self-write suppression and debounce
(decision D5, HOPPUS-38).

``VaultWatcher`` wraps a watchdog ``Observer`` over an ``Index``'s vault
root and applies create/modify/delete/move events for ``.md`` files via
``Index.reindex_file`` — covering both ``$EDITOR``-handoff edits and
external edits (e.g. Obsidian on the same vault). If watchdog is missing
or the observer fails to start, the watcher degrades gracefully
(``available`` stays ``False``) and the app falls back to manual
reindexing via :meth:`VaultWatcher.reindex_all` / the ``r`` keybind.

Decision D5 (spec) requires that hoppus's OWN writes — rename
propagation via ``apply_plan``, auto-created notes, frontmatter edits —
never re-trigger the index/integrity pass on their own output, which
would otherwise form a feedback loop. Two mechanisms implement this,
both driven by an injectable monotonic ``clock`` so tests are
deterministic:

- **Self-write suppression**: callers wrap their own writes with
  :meth:`VaultWatcher.self_write` (or call :meth:`suppress` /
  :meth:`suppress_many` directly). Suppression is a *time window*, not
  a one-shot token: a suppressed path stays suppressed until its TTL
  expires, so a single write that emits multiple filesystem events
  (create + modify) is fully covered. External edits to never-suppressed
  paths are unaffected.
- **Debounce**: rapid repeated events for the same path within
  ``debounce_interval`` are coalesced *leading-edge*: the first event is
  processed immediately and follow-ups inside the window are skipped.
  Because ``Index.reindex_file`` re-reads the file from disk at process
  time, the leading event already captures whatever bytes are present;
  a genuinely new edit after the window fires a fresh event that is
  processed normally, so the final state is never dropped. The tradeoff
  is one possibly-redundant reindex per burst rather than a delayed
  trailing-edge timer (which would need a background thread).
"""

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    FileSystemEvent = None  # type: ignore[assignment, misc]
    FileSystemEventHandler = object  # type: ignore[assignment, misc]
    Observer = None  # type: ignore[assignment, misc]

from hoppus.index.indexer import Index

logger = logging.getLogger(__name__)

_MD_SUFFIX = ".md"
_SKIP_DIRS = frozenset({".obsidian", ".hoppus"})


class _VaultEventHandler(FileSystemEventHandler):  # type: ignore[misc]
    """
    Watchdog event handler delegating to a ``VaultWatcher``.

    Directory events are ignored; file events route through
    ``VaultWatcher._handle``, which filters, applies, and never raises.
    Moves are handled as delete-old + reindex-new.
    """

    def __init__(self, watcher: "VaultWatcher") -> None:
        """
        :param watcher: The owning ``VaultWatcher``.
        """
        super().__init__()
        self._watcher = watcher

    def on_created(self, event: "FileSystemEvent") -> None:
        """Apply a file-created event."""
        if not event.is_directory:
            self._watcher._handle(Path(str(event.src_path)), "created")

    def on_modified(self, event: "FileSystemEvent") -> None:
        """Apply a file-modified event."""
        if not event.is_directory:
            self._watcher._handle(Path(str(event.src_path)), "modified")

    def on_deleted(self, event: "FileSystemEvent") -> None:
        """Apply a file-deleted event."""
        if not event.is_directory:
            self._watcher._handle(Path(str(event.src_path)), "deleted")

    def on_moved(self, event: "FileSystemEvent") -> None:
        """Apply a file-moved event as delete-old + reindex-new."""
        if event.is_directory:
            return
        self._watcher._handle(Path(str(event.src_path)), "deleted")
        self._watcher._handle(Path(str(event.dest_path)), "created")


class VaultWatcher:
    """
    Watch a vault directory and keep an ``Index`` up to date (spec §8).

    Attributes:

    - ``index``: The live ``Index`` being maintained (replaced by
      :meth:`reindex_all`).
    - ``on_change``: Optional callback invoked with the affected path
      after each applied update, so a UI can refresh its panes.
    - ``available``: ``True`` while live watching is running; ``False``
      before :meth:`start`, after :meth:`stop`, or when watchdog is
      unavailable / the observer failed to start (graceful degradation —
      callers fall back to manual reindexing).
    """

    def __init__(
        self,
        index: Index,
        on_change: Callable[[Path], None] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        debounce_interval: float = 0.1,
    ) -> None:
        """
        :param index: The vault index to keep up to date.
        :param on_change: Optional callback invoked with the affected
            path after each applied update.
        :param clock: Monotonic time source for suppression TTLs and
            debounce windows; injectable for deterministic tests.
        :param debounce_interval: Seconds within which repeated events
            for the same path are coalesced (leading-edge).
        """
        self.index = index
        self.on_change = on_change
        self.available = False
        self.clock = clock
        self.debounce_interval = debounce_interval
        self._observer: object | None = None
        self._handler = _VaultEventHandler(self)
        self._suppressed_until: dict[Path, float] = {}
        self._last_processed: dict[Path, float] = {}

    # -- Self-write suppression (D5, HOPPUS-38) --------------------------------

    def suppress(self, path: Path, *, ttl: float = 2.0) -> None:
        """
        Mark ``path`` as self-written for the next ``ttl`` seconds.

        Events for the path arriving within the window are ignored by
        :meth:`should_process`. The suppression is time-window based
        (NOT consumed on first event): one write commonly emits several
        filesystem events (create + modify), and all of them must be
        covered. It lapses only when ``ttl`` elapses.

        :param path: The path hoppus itself is about to write.
        :param ttl: Suppression window in seconds.
        """
        self._suppressed_until[self._key(path)] = self.clock() + ttl

    def suppress_many(self, paths: Iterable[Path], *, ttl: float = 2.0) -> None:
        """
        Suppress a batch of self-written paths (e.g. an ``apply_plan``
        rename-propagation pass that rewrites many files).

        :param paths: All paths the batch write touches.
        :param ttl: Suppression window in seconds, per path.
        """
        for path in paths:
            self.suppress(path, ttl=ttl)

    @contextmanager
    def self_write(self, path: Path, *, ttl: float = 2.0) -> Iterator[None]:
        """
        Context manager wrapping one of hoppus's own writes.

        Suppresses ``path`` on entry (covering events emitted during the
        write) and refreshes the suppression on exit so trailing events
        get the full ``ttl`` window measured from write completion.

        :param path: The path being written inside the ``with`` block.
        :param ttl: Suppression window in seconds.
        """
        self.suppress(path, ttl=ttl)
        try:
            yield
        finally:
            self.suppress(path, ttl=ttl)

    def _key(self, path: Path) -> Path:
        """
        Normalize a path for suppression/debounce bookkeeping.

        Resolution failures fall back to the path as given — never
        raise from the event path.

        :param path: Any event or caller-supplied path.
        :returns: The resolved path used as a dict key.
        """
        try:
            return path.resolve()
        except OSError:
            return path

    def _is_suppressed(self, path: Path) -> bool:
        """
        Report whether ``path`` is inside an active self-write window,
        pruning expired entries as a side effect.

        :param path: The event's file path.
        :returns: ``True`` when the event stems from a hoppus write.
        """
        now = self.clock()
        expired = [p for p, until in self._suppressed_until.items() if until <= now]
        for p in expired:
            del self._suppressed_until[p]
        return self._key(path) in self._suppressed_until

    def should_process(self, path: Path) -> bool:
        """
        Decide whether a filesystem event path affects the index.

        Only ``.md`` files under the vault root count, and anything
        inside ``.hoppus/`` or ``.obsidian/`` is ignored. Per decision
        D5 (HOPPUS-38), paths inside an active self-write suppression
        window (see :meth:`suppress`) are also rejected so hoppus's own
        writes never re-trigger the pass, while external edits to other
        paths still process normally.

        :param path: The event's file path.
        :returns: ``True`` when the event should be applied.
        """
        if path.suffix.lower() != _MD_SUFFIX:
            return False
        try:
            relative = path.resolve().relative_to(self.index.vault_root.resolve())
        except ValueError:
            return False
        if any(part in _SKIP_DIRS for part in relative.parts):
            return False
        return not self._is_suppressed(path)

    def _handle(self, path: Path, kind: str) -> None:
        """
        Apply one filesystem event to the index.

        Filters via :meth:`should_process`, debounces bursts (D5,
        HOPPUS-38), incrementally re-indexes the file
        (``Index.reindex_file`` handles add, update, and remove), then
        invokes ``on_change``. Every failure is caught and logged so one
        bad event never kills the observer thread.

        Debounce is leading-edge: the first event for a path processes
        immediately; further events for the same path within
        ``debounce_interval`` are skipped. The final state is safe
        because reindexing reads the file from disk, and any edit after
        the window emits a new event that processes normally.

        :param path: The affected file path.
        :param kind: Event kind (``created``/``modified``/``deleted``),
            used for logging only.
        """
        try:
            if not self.should_process(path):
                return
            key = self._key(path)
            now = self.clock()
            last = self._last_processed.get(key)
            if last is not None and now - last < self.debounce_interval:
                logger.debug("Debounced %s event for %s", kind, path)
                return
            self._last_processed[key] = now
            self.index.reindex_file(path)
            logger.info("Applied %s event for %s", kind, path)
            if self.on_change is not None:
                self.on_change(path)
        except Exception:
            logger.exception("Failed to apply %s event for %s", kind, path)

    def start(self) -> None:
        """
        Start live watching of the vault root (idempotent).

        Degrades gracefully: if watchdog is unavailable or constructing/
        starting the observer raises, ``available`` stays ``False``, a
        warning is logged, and no exception propagates — the app keeps
        working via manual reindexing (spec §8).
        """
        if self._observer is not None:
            return
        if Observer is None:
            logger.warning(
                "watchdog is unavailable; live vault watching disabled — "
                "falling back to manual reindex"
            )
            return
        try:
            observer = Observer()
            observer.schedule(self._handler, str(self.index.vault_root), recursive=True)
            observer.start()
        except Exception:
            logger.warning(
                "Could not start the vault watcher for %s; "
                "falling back to manual reindex",
                self.index.vault_root,
                exc_info=True,
            )
            return
        self._observer = observer
        self.available = True
        logger.info("Watching vault %s", self.index.vault_root)

    def stop(self) -> None:
        """
        Stop live watching (idempotent).

        Failures during shutdown are logged, never raised.
        """
        observer = self._observer
        self._observer = None
        self.available = False
        if observer is None:
            return
        try:
            observer.stop()  # type: ignore[attr-defined]
            observer.join()  # type: ignore[attr-defined]
        except Exception:
            logger.exception(
                "Error stopping the vault watcher for %s", self.index.vault_root
            )

    def reindex_all(self) -> Index:
        """
        Rebuild the whole index — the manual fallback (spec §8).

        Replaces ``self.index`` so subsequent events apply to the fresh
        index.

        :returns: The freshly built ``Index``.
        """
        self.index = Index.build(self.index.vault_root)
        logger.info("Rebuilt index for vault %s", self.index.vault_root)
        return self.index
