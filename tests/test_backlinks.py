"""
Backlinks pane tests (HOPPUS-32).

Headless Textual Pilot tests (``App.run_test`` wrapped in ``asyncio.run``,
matching the rest of the suite) exercise the right-sidebar backlinks pane
against the shared ``sample_vault``: linked mentions for the active note,
the empty state, updates when the active note changes, sorting and
duplicate-title handling, navigation on selection, and the vault-switch
reset. Pane state is built via ``show_backlinks(...)`` through the app's
``open_note`` funnel rather than driving real clicks.
"""

import asyncio
from pathlib import Path

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.backlinks import BacklinksPane


def make_app(vaults_root: Path) -> HoppusApp:
    """
    Build a HoppusApp rooted above the sample vault.

    :param vaults_root: Directory containing the vault under test.
    :returns: An unmounted HoppusApp instance.
    """
    return HoppusApp(config=default_config(), vaults_root=vaults_root)


def option_titles(pane: BacklinksPane) -> list[str]:
    """
    Return the visible option prompts of the pane, top to bottom.

    :param pane: The backlinks pane under test.
    :returns: The plain-text prompt of each option.
    """
    return [
        str(pane.get_option_at_index(index).prompt)
        for index in range(pane.option_count)
    ]


def test_backlinks_listed_for_note_with_inbound_links(sample_vault: Path) -> None:
    """
    Opening a note with inbound links lists the source titles.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(
                sample_vault / "Target Note.md", vault_root=sample_vault
            )
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert option_titles(pane) == ["Index"]

    asyncio.run(run())


def test_empty_state_for_note_without_inbound_links(sample_vault: Path) -> None:
    """
    Opening a note with no inbound links shows the empty state.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Tags Note.md", vault_root=sample_vault)
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            titles = option_titles(pane)
            assert len(titles) == 1
            assert "No backlinks" in titles[0]
            assert pane.get_option_at_index(0).disabled

    asyncio.run(run())


def test_pane_updates_when_active_note_changes(sample_vault: Path) -> None:
    """
    Switching the active note swaps in that note's backlink set.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            await app.open_note(
                sample_vault / "Target Note.md", vault_root=sample_vault
            )
            await pilot.pause()
            assert option_titles(pane) == ["Index"]
            # Projects/Alpha is linked from Index.md and Archive/Alpha.md;
            # titles sort case-insensitively.
            await app.open_note(
                sample_vault / "Projects" / "Alpha.md", vault_root=sample_vault
            )
            await pilot.pause()
            assert option_titles(pane) == ["Alpha", "Index"]

    asyncio.run(run())


def test_selecting_backlink_navigates_to_source(sample_vault: Path) -> None:
    """
    Selecting a backlink opens the source note (duplicate-title safe).
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(
                sample_vault / "Projects" / "Alpha.md", vault_root=sample_vault
            )
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert option_titles(pane) == ["Alpha", "Index"]
            # The "Alpha" row must map to Archive/Alpha.md by path, not
            # collide with the active Projects/Alpha.md title.
            pane.action_first()
            pane.action_select()
            await pilot.pause()
            assert app.preview.note_path == sample_vault / "Archive" / "Alpha.md"
            assert app.note_title == "Alpha"
            # Navigation went through open_note, so the pane refreshed too.
            assert option_titles(pane) == ["Alpha", "Index"]

    asyncio.run(run())


def test_vault_switch_clears_pane(sample_vault: Path, tmp_path: Path) -> None:
    """
    Switching vaults resets the pane to the empty state.
    """

    async def run() -> None:
        vaults_root = tmp_path / "vaults"
        other = vaults_root / "Other"
        other.mkdir(parents=True)
        (other / "Lone Note.md").write_text("# Lone Note\n", encoding="utf-8")
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(
                sample_vault / "Target Note.md", vault_root=sample_vault
            )
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert option_titles(pane) == ["Index"]
            app.vaults_root = vaults_root
            await app.switch_vault("Other")
            await pilot.pause()
            titles = option_titles(pane)
            assert len(titles) == 1
            assert "No backlinks" in titles[0]

    asyncio.run(run())


def test_right_sidebar_toggle_still_works(sample_vault: Path) -> None:
    """
    The ``\\`` toggle hides and shows the right (backlinks) sidebar.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            right = app.right_sidebar
            assert right.display
            app.action_toggle_right_sidebar()
            await pilot.pause()
            assert not right.display
            app.action_toggle_right_sidebar()
            await pilot.pause()
            assert right.display

    asyncio.run(run())
