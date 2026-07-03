"""
Unlinked-mentions TUI tests (HOPPUS-33).

Headless Textual Pilot tests (``App.run_test`` wrapped in ``asyncio.run``,
matching the rest of the suite) for the on-demand unlinked-mentions
action: triggering it for an active note appends the section to the
backlinks pane, selecting an unlinked row navigates through
``open_note``, and triggering it with no active note is a gentle no-op.
"""

import asyncio
from pathlib import Path

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.backlinks import BacklinksPane

_ROCKET_MD = """\
# Rocket

The rocket note itself.
"""

_DIARY_MD = """\
# Diary

Thinking about the Rocket today. The rocket flies.
"""

_LINKED_MD = """\
# Linked

Only a wikilink here: [[Rocket]].
"""


def make_vault(tmp_path: Path) -> Path:
    """
    Build a tiny vault with one plain-text mentioner and one linker.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root (under a vaults root suitable for the app).
    """
    vault = tmp_path / "vaults" / "V"
    vault.mkdir(parents=True)
    (vault / "Rocket.md").write_text(_ROCKET_MD, encoding="utf-8")
    (vault / "Diary.md").write_text(_DIARY_MD, encoding="utf-8")
    (vault / "Linked.md").write_text(_LINKED_MD, encoding="utf-8")
    return vault


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


def test_action_populates_unlinked_section(tmp_path: Path) -> None:
    """
    Triggering the action appends the unlinked section for the active
    note: the plain-text mentioner appears with its count, the
    wikilink-only note does not.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = HoppusApp(config=default_config(), vaults_root=vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Rocket.md", vault_root=vault)
            await pilot.pause()
            app.action_unlinked_mentions()
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            titles = option_titles(pane)
            header_index = next(
                index
                for index, title in enumerate(titles)
                if "Unlinked mentions" in title
            )
            unlinked_titles = titles[header_index + 1 :]
            assert any("Diary" in t and "×2" in t for t in unlinked_titles)
            # Linked.md only wikilinks to Rocket, so it stays out of the
            # unlinked section (it still appears above, as a backlink).
            assert not any("Linked" in t for t in unlinked_titles)

    asyncio.run(run())


def test_pressing_u_triggers_the_action(tmp_path: Path) -> None:
    """
    The ``u`` keybinding runs the unlinked-mentions action.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = HoppusApp(config=default_config(), vaults_root=vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Rocket.md", vault_root=vault)
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert any("Unlinked mentions" in t for t in option_titles(pane))

    asyncio.run(run())


def test_selecting_unlinked_row_navigates_to_source(tmp_path: Path) -> None:
    """
    Selecting an unlinked-mention row opens the source note via the
    app's ``open_note`` funnel.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = HoppusApp(config=default_config(), vaults_root=vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Rocket.md", vault_root=vault)
            await pilot.pause()
            app.action_unlinked_mentions()
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            # The Diary row is the last (only enabled) unlinked option.
            pane.action_last()
            pane.action_select()
            await pilot.pause()
            assert app.preview.note_path == vault / "Diary.md"
            assert app.note_title == "Diary"

    asyncio.run(run())


def test_rerunning_action_replaces_section(tmp_path: Path) -> None:
    """
    Running the action twice replaces the section instead of stacking.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = HoppusApp(config=default_config(), vaults_root=vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Rocket.md", vault_root=vault)
            await pilot.pause()
            app.action_unlinked_mentions()
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            first = option_titles(pane)
            app.action_unlinked_mentions()
            await pilot.pause()
            assert option_titles(pane) == first

    asyncio.run(run())


def test_no_active_note_is_a_gentle_no_op(tmp_path: Path) -> None:
    """
    Without an active note the action notifies and changes nothing.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = HoppusApp(config=default_config(), vaults_root=vault.parent)
        async with app.run_test() as pilot:
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            before = option_titles(pane)
            app.action_unlinked_mentions()
            await pilot.pause()
            assert option_titles(pane) == before
            assert not any("Unlinked mentions" in t for t in before)

    asyncio.run(run())
