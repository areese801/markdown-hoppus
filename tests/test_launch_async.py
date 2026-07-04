"""
Non-blocking launch tests (HOPPUS-78, F18).

On mount the index build + integrity audit must run in a thread worker
so the TUI is responsive immediately on large vaults: ``on_mount``
returns before the problems count is populated, the status line shows a
transient ⟳ indexing… segment while the build runs, index-dependent
actions fired before the first build completes notify instead of
blocking, and the empty main pane shows a hint until a note opens.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run``. Mid-indexing tests hold the build on a
``threading.Event`` (opting out of the conftest settle fixture with
``@pytest.mark.no_index_settle``) so nothing is timing-dependent.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from textual.widgets import Static

import hoppus.index.watcher as watcher_module
from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.tui.app import HoppusApp, StatusLine


def make_vault(tmp_path: Path) -> Path:
    """
    Build a vault with exactly one unresolved wikilink (one problem).
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("See [[Nowhere]].\n", encoding="utf-8")
    (vault / "Beta.md").write_text("No links.\n", encoding="utf-8")
    return vault


def make_recording_app(vault: Path) -> HoppusApp:
    """
    Build a HoppusApp rooted at the vault, recording every notify.
    """
    app = HoppusApp(config=default_config(), vaults_root=vault)
    app.recorded_notifications = []  # type: ignore[attr-defined]
    original_notify = app.notify

    def recording_notify(message: str, **kwargs: object) -> None:
        app.recorded_notifications.append(message)  # type: ignore[attr-defined]
        original_notify(message, **kwargs)  # type: ignore[arg-type]

    app.notify = recording_notify  # type: ignore[method-assign]
    return app


def gate_index_build(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[threading.Event, list[int]]:
    """
    Make ``Index.build`` block on an event, recording its thread id.

    :param monkeypatch: pytest's monkeypatch fixture.
    :returns: The release event and the list of builder thread idents.
    """
    real_build = Index.build
    gate = threading.Event()
    build_threads: list[int] = []

    def gated_build(cls: type[Index], vault_root: Path) -> Index:
        build_threads.append(threading.get_ident())
        assert gate.wait(timeout=10), "index-build gate was never released"
        return real_build(vault_root)

    monkeypatch.setattr(Index, "build", classmethod(gated_build))
    return gate, build_threads


@pytest.mark.no_index_settle
def test_mount_does_not_block_on_index_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Mount returns while the build runs; the ⟳ indicator flips to ⚠ N.
    """
    vault = make_vault(tmp_path)
    gate, build_threads = gate_index_build(monkeypatch)

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The build is still gated, yet the app is already up and
            # processing input: on_mount did not run it synchronously.
            assert app._indexing is True
            assert app._problem_count == 0
            status = str(app.query_one(StatusLine).render())
            assert "⟳ indexing…" in status
            assert "problems" not in status

            gate.set()
            assert app._launch_worker is not None
            await app._launch_worker.wait()
            await pilot.pause()
            assert app._indexing is False
            assert app._problem_count == 1
            status = str(app.query_one(StatusLine).render())
            assert "indexing" not in status
            assert "⚠ 1 problems" in status
            # The worker populated the shared cache off the main thread.
            assert app._index is not None
            assert app._index_root == vault
            main_thread = threading.get_ident()
            assert build_threads
            assert all(ident != main_thread for ident in build_threads)

    asyncio.run(run())


@pytest.mark.no_index_settle
def test_actions_before_first_build_notify_indexing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Index-dependent actions fired mid-build explain instead of hanging.
    """
    vault = make_vault(tmp_path)
    gate, _build_threads = gate_index_build(monkeypatch)

    async def run() -> None:
        app = make_recording_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._indexing is True

            app.action_search()
            await pilot.pause()
            assert any(
                "Indexing the vault" in message
                for message in app.recorded_notifications  # type: ignore[attr-defined]
            )
            # No search screen was pushed and nothing blocked.
            assert len(app.screen_stack) == 1

            gate.set()
            assert app._launch_worker is not None
            await app._launch_worker.wait()
            await pilot.pause()
            assert app._indexing is False

    asyncio.run(run())


def test_launch_worker_starts_watcher_with_cached_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The watcher starts after the build and reuses the worker's index.
    """
    vault = make_vault(tmp_path)

    class FakeObserver:
        def schedule(self, handler: object, path: str, recursive: bool = False):
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def join(self) -> None:
            pass

    monkeypatch.setattr(watcher_module, "Observer", FakeObserver)

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test():
            assert app._indexing is False
            assert app._index is not None
            assert app._index_root == vault
            assert app._watcher is not None
            assert app._watcher.index is app._index

    asyncio.run(run())


def test_empty_pane_hint_shows_until_a_note_opens(tmp_path: Path) -> None:
    """
    The main pane hints when no note is open, and clears on open.
    """
    vault = make_vault(tmp_path)

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            hint = app.query_one("#preview-empty", Static)
            assert hint.display
            assert "No note open" in str(hint.render())

            await app.open_note(vault / "Beta.md", vault_root=vault)
            await pilot.pause()
            assert not hint.display

            await app.preview.clear()
            await pilot.pause()
            assert hint.display

    asyncio.run(run())
