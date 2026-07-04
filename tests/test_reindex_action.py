"""
Reindex action tests (HOPPUS-37): the ``r`` keybind's manual fallback.

Headless Textual Pilot tests (``App.run_test`` wrapped in ``asyncio.run``,
matching the rest of the suite). ``action_reindex`` must rebuild the
active index, refresh the open note's backlinks, and notify — and be a
gentle no-op when no vault directory exists.
"""

import asyncio
from pathlib import Path

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.backlinks import BacklinksPane


def make_app(vaults_root: Path) -> HoppusApp:
    """
    Build a HoppusApp rooted at the given vaults root, recording notifies.

    :param vaults_root: Directory treated as the vaults root.
    :returns: An unmounted HoppusApp with a ``recorded_notifications``
        list of message strings.
    """
    app = HoppusApp(config=default_config(), vaults_root=vaults_root)
    app.recorded_notifications = []  # type: ignore[attr-defined]
    original_notify = app.notify

    def recording_notify(message: str, **kwargs: object) -> None:
        app.recorded_notifications.append(message)  # type: ignore[attr-defined]
        original_notify(message, **kwargs)  # type: ignore[arg-type]

    app.notify = recording_notify  # type: ignore[method-assign]
    return app


def test_reindex_rebuilds_index_and_notifies(sample_vault: Path) -> None:
    """
    ``action_reindex`` rebuilds the cached index and notifies.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The launch-time worker (HOPPUS-78) pre-populates the cache.
            initial_index = app._index
            assert initial_index is not None
            app.action_reindex()
            await pilot.pause()
            assert app._index is not None
            assert app._index is not initial_index
            assert app._index_root == sample_vault
            assert sample_vault / "Index.md" in app._index.notes_by_path
            assert "Reindexed" in app.recorded_notifications

    asyncio.run(run())


def test_reindex_picks_up_external_changes(tmp_path: Path) -> None:
    """
    A note written after the first build appears after reindex, and the
    open note's backlinks pane refreshes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("Alpha body.\n", encoding="utf-8")

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.pause()
            stale = app._index
            assert stale is not None

            (vault / "Beta.md").write_text(
                "Beta links to [[Alpha]].\n", encoding="utf-8"
            )
            app.action_reindex()
            await pilot.pause()

            assert app._index is not stale
            assert vault / "Beta.md" in app._index.notes_by_path
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            prompts = [
                str(pane.get_option_at_index(index).prompt)
                for index in range(pane.option_count)
            ]
            assert prompts == ["Beta  ·1"]

    asyncio.run(run())


def test_reindex_without_vault_is_gentle_noop(tmp_path: Path) -> None:
    """
    With no vault directory, ``action_reindex`` notifies and does nothing.
    """

    async def run() -> None:
        app = make_app(tmp_path / "missing")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_reindex()
            await pilot.pause()
            assert app._index is None
            assert "Reindex: no open vault" in app.recorded_notifications

    asyncio.run(run())
