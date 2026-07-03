"""
File watcher: incremental index updates with graceful degradation
(spec §8, HOPPUS-37).

``VaultWatcher`` wraps a watchdog ``Observer`` over an ``Index``'s vault
root and applies create/modify/delete/move events for ``.md`` files via
``Index.reindex_file`` — covering both ``$EDITOR``-handoff edits and
external edits (e.g. Obsidian on the same vault). If watchdog is missing
or the observer fails to start, the watcher degrades gracefully
(``available`` stays ``False``) and the app falls back to manual
reindexing via :meth:`VaultWatcher.reindex_all` / the ``r`` keybind.

Self-write suppression and debounce are HOPPUS-38 (decision D5); the
:meth:`VaultWatcher.should_process` predicate is the seam where that
story plugs in.
"""

import logging
from collections.abc import Callable
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
    ) -> None:
        """
        :param index: The vault index to keep up to date.
        :param on_change: Optional callback invoked with the affected
            path after each applied update.
        """
        self.index = index
        self.on_change = on_change
        self.available = False
        self._observer: object | None = None
        self._handler = _VaultEventHandler(self)

    def should_process(self, path: Path) -> bool:
        """
        Decide whether a filesystem event path affects the index.

        Only ``.md`` files under the vault root count, and anything
        inside ``.hoppus/`` or ``.obsidian/`` is ignored. HOPPUS-38
        (decision D5) extends this seam with self-write suppression and
        debounce.

        :param path: The event's file path.
        :returns: ``True`` when the event should be applied.
        """
        if path.suffix.lower() != _MD_SUFFIX:
            return False
        try:
            relative = path.resolve().relative_to(self.index.vault_root.resolve())
        except ValueError:
            return False
        return not any(part in _SKIP_DIRS for part in relative.parts)

    def _handle(self, path: Path, kind: str) -> None:
        """
        Apply one filesystem event to the index.

        Filters via :meth:`should_process`, incrementally re-indexes the
        file (``Index.reindex_file`` handles add, update, and remove),
        then invokes ``on_change``. Every failure is caught and logged
        so one bad event never kills the observer thread.

        :param path: The affected file path.
        :param kind: Event kind (``created``/``modified``/``deleted``),
            used for logging only.
        """
        try:
            if not self.should_process(path):
                return
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
