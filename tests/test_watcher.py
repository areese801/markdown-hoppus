"""
VaultWatcher tests (HOPPUS-37): incremental index updates and graceful
degradation.

The handler is driven directly (``_handle`` and synthetic watchdog
events) — no real filesystem watching or timing. Degradation and the
start/stop lifecycle are exercised with monkeypatched/fake observers so
no observer thread ever runs.
"""

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent, FileMovedEvent

import hoppus.index.watcher as watcher_module
from hoppus.index.indexer import Index
from hoppus.index.watcher import VaultWatcher


def make_vault(tmp_path: Path) -> Path:
    """
    Build a tiny writable vault: two linked notes plus skip dirs.

    :param tmp_path: pytest's per-test temp dir.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("Link to [[Beta]].\n", encoding="utf-8")
    (vault / "Beta.md").write_text("Beta body.\n", encoding="utf-8")
    (vault / ".hoppus").mkdir()
    (vault / ".obsidian").mkdir()
    return vault


def test_created_file_is_added_to_index(tmp_path: Path) -> None:
    """
    A created ``.md`` file lands in the index and fires ``on_change``.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    seen: list[Path] = []
    watcher = VaultWatcher(index, on_change=seen.append)

    new_note = vault / "Gamma.md"
    new_note.write_text("Gamma links to [[Alpha]].\n", encoding="utf-8")
    watcher._handle(new_note, "created")

    assert new_note in index.notes_by_path
    assert new_note in index.backlinks[vault / "Alpha.md"]
    assert seen == [new_note]


def test_modified_file_is_updated_in_index(tmp_path: Path) -> None:
    """
    A modified ``.md`` file is re-parsed: word count and links update.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    watcher = VaultWatcher(index)

    beta = vault / "Beta.md"
    beta.write_text("Beta now links to [[Alpha]] too.\n", encoding="utf-8")
    watcher._handle(beta, "modified")

    assert index.notes_by_path[beta].word_count == 6
    assert beta in index.backlinks[vault / "Alpha.md"]


def test_deleted_file_is_removed_from_index(tmp_path: Path) -> None:
    """
    A deleted ``.md`` file is removed and its links unresolve.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    watcher = VaultWatcher(index)

    beta = vault / "Beta.md"
    beta.unlink()
    watcher._handle(beta, "deleted")

    assert beta not in index.notes_by_path
    alpha_links = index.links[vault / "Alpha.md"]
    assert all(link.resolved is None for link in alpha_links)


def test_moved_file_is_reindexed_under_new_path(tmp_path: Path) -> None:
    """
    A move event removes the old path and indexes the new one.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    watcher = VaultWatcher(index)

    old = vault / "Beta.md"
    new = vault / "Beta Renamed.md"
    old.rename(new)
    watcher._handler.on_moved(FileMovedEvent(str(old), str(new)))

    assert old not in index.notes_by_path
    assert new in index.notes_by_path


def test_modified_event_dispatch_routes_through_handler(tmp_path: Path) -> None:
    """
    A synthetic watchdog modified event applies via the handler.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    watcher = VaultWatcher(index)

    beta = vault / "Beta.md"
    beta.write_text("One two three four.\n", encoding="utf-8")
    watcher._handler.on_modified(FileModifiedEvent(str(beta)))

    assert index.notes_by_path[beta].word_count == 4


@pytest.mark.parametrize(
    "relative",
    [".hoppus/state.md", ".obsidian/app.md", "notes.txt", "image.png"],
)
def test_skip_dirs_and_non_md_paths_are_ignored(tmp_path: Path, relative: str) -> None:
    """
    Paths in ``.hoppus``/``.obsidian`` or without ``.md`` are ignored.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    calls: list[Path] = []
    watcher = VaultWatcher(index, on_change=calls.append)

    path = vault / relative
    path.write_text("ignored\n", encoding="utf-8")
    watcher._handle(path, "created")

    assert path not in index.notes_by_path
    assert calls == []


def test_path_outside_vault_is_ignored(tmp_path: Path) -> None:
    """
    A ``.md`` path outside the vault root is ignored.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    watcher = VaultWatcher(index)

    outside = tmp_path / "Elsewhere.md"
    outside.write_text("outside\n", encoding="utf-8")
    watcher._handle(outside, "created")

    assert outside not in index.notes_by_path


def test_handler_exception_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A failure inside ``reindex_file`` is logged, not raised.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    watcher = VaultWatcher(index)

    def boom(path: Path) -> None:
        raise RuntimeError("bad event")

    monkeypatch.setattr(index, "reindex_file", boom)
    watcher._handle(vault / "Alpha.md", "modified")


class _FakeObserver:
    """
    Observer stand-in recording lifecycle calls (no threads).
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.scheduled: list[tuple[object, str, bool]] = []

    def schedule(self, handler: object, path: str, recursive: bool = False) -> None:
        self.scheduled.append((handler, path, recursive))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        pass


def test_start_and_stop_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``start``/``stop`` toggle ``available`` and tolerate repeat calls.
    """
    vault = make_vault(tmp_path)
    watcher = VaultWatcher(Index.build(vault))
    fakes: list[_FakeObserver] = []

    def make_fake() -> _FakeObserver:
        fake = _FakeObserver()
        fakes.append(fake)
        return fake

    monkeypatch.setattr(watcher_module, "Observer", make_fake)
    assert watcher.available is False

    watcher.start()
    watcher.start()
    assert watcher.available is True
    assert len(fakes) == 1
    assert fakes[0].started
    assert fakes[0].scheduled == [(watcher._handler, str(vault), True)]

    watcher.stop()
    watcher.stop()
    assert watcher.available is False
    assert fakes[0].stopped


def test_start_degrades_when_observer_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An observer that raises on ``start`` leaves ``available`` False.
    """
    vault = make_vault(tmp_path)
    watcher = VaultWatcher(Index.build(vault))

    class _ExplodingObserver(_FakeObserver):
        def start(self) -> None:
            raise OSError("inotify limit reached")

    monkeypatch.setattr(watcher_module, "Observer", _ExplodingObserver)
    watcher.start()

    assert watcher.available is False
    watcher.stop()


def test_start_degrades_when_watchdog_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With watchdog unavailable (``Observer is None``), ``start`` no-ops.
    """
    vault = make_vault(tmp_path)
    watcher = VaultWatcher(Index.build(vault))

    monkeypatch.setattr(watcher_module, "Observer", None)
    watcher.start()

    assert watcher.available is False


def test_reindex_all_returns_fresh_index(tmp_path: Path) -> None:
    """
    ``reindex_all`` rebuilds from disk and swaps the live index.
    """
    vault = make_vault(tmp_path)
    old_index = Index.build(vault)
    watcher = VaultWatcher(old_index)

    (vault / "Delta.md").write_text("New note.\n", encoding="utf-8")
    fresh = watcher.reindex_all()

    assert fresh is not old_index
    assert fresh is watcher.index
    assert vault / "Delta.md" in fresh.notes_by_path
    assert vault / "Delta.md" not in old_index.notes_by_path
