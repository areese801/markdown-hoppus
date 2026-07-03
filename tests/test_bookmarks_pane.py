"""
Bookmarks pane and star/unstar action tests (spec §9.12, HOPPUS-52).

Headless Textual Pilot tests (``App.run_test`` wrapped in
``asyncio.run``, matching the rest of the suite). ``action_toggle_star``
must persist to ``<vault>/.hoppus/bookmarks.yaml``, the Bookmarks pane
must list starred notes and navigate on selection, and bookmarks must
survive a simulated app reopen.
"""

import asyncio
from pathlib import Path

from hoppus.bookmarks import load_bookmarks
from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.bookmarks import BookmarksPane


def make_vault(tmp_path: Path) -> Path:
    """
    Create a minimal vault with two notes.

    :param tmp_path: pytest temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("# Alpha\n\nAlpha body.\n", encoding="utf-8")
    (vault / "Beta.md").write_text("# Beta\n\nBeta body.\n", encoding="utf-8")
    return vault


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


def pane_prompts(pane: BookmarksPane) -> list[str]:
    """
    Return the visible row prompts of a bookmarks pane.

    :param pane: The pane under inspection.
    :returns: One string per option row.
    """
    return [
        str(pane.get_option_at_index(index).prompt)
        for index in range(pane.option_count)
    ]


def test_toggle_star_adds_and_removes_from_pane(tmp_path: Path) -> None:
    """
    Starring the active note adds it to the Bookmarks pane and persists;
    a second toggle removes it again.
    """
    vault = make_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.pause()

            app.action_toggle_star()
            await pilot.pause()
            pane = app.query_one("#bookmarks-pane", BookmarksPane)
            assert pane_prompts(pane) == ["Alpha"]
            assert load_bookmarks(vault) == [vault / "Alpha.md"]
            assert "Starred" in app.recorded_notifications

            app.action_toggle_star()
            await pilot.pause()
            assert load_bookmarks(vault) == []
            assert "No bookmarks" in pane_prompts(pane)[0]
            assert "Unstarred" in app.recorded_notifications

    asyncio.run(run())


def test_toggle_star_without_note_notifies(tmp_path: Path) -> None:
    """
    With no active note, ``action_toggle_star`` notifies and does nothing.
    """
    vault = make_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_star()
            await pilot.pause()
            assert "No active note" in app.recorded_notifications
            assert load_bookmarks(vault) == []

    asyncio.run(run())


def test_selecting_bookmark_opens_note(tmp_path: Path) -> None:
    """
    Selecting a bookmark row navigates the preview to that note.
    """
    vault = make_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Beta.md", vault_root=vault)
            await pilot.pause()
            app.action_toggle_star()
            await pilot.pause()

            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.pause()
            assert app.preview.note_path == vault / "Alpha.md"

            pane = app.query_one("#bookmarks-pane", BookmarksPane)
            pane.highlighted = 0
            pane.action_select()
            await pilot.pause()
            assert app.preview.note_path == vault / "Beta.md"

    asyncio.run(run())


def test_bookmarks_survive_reopen(tmp_path: Path) -> None:
    """
    Bookmarks starred in one app session appear in a fresh session's
    Bookmarks pane (persistence across restarts).
    """
    vault = make_vault(tmp_path)

    async def first_session() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.pause()
            app.action_toggle_star()
            await pilot.pause()

    async def second_session() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#bookmarks-pane", BookmarksPane)
            assert pane_prompts(pane) == ["Alpha"]

    asyncio.run(first_session())
    assert load_bookmarks(vault) == [vault / "Alpha.md"]
    asyncio.run(second_session())


def test_pane_skips_deleted_notes(tmp_path: Path) -> None:
    """
    A bookmark whose file no longer exists is skipped in the pane
    (the YAML entry stays, invisible until the file returns).
    """
    vault = make_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.pause()
            app.action_toggle_star()
            await pilot.pause()

            (vault / "Alpha.md").unlink()
            app._refresh_bookmarks()
            await pilot.pause()
            pane = app.query_one("#bookmarks-pane", BookmarksPane)
            assert "No bookmarks" in pane_prompts(pane)[0]

    asyncio.run(run())
