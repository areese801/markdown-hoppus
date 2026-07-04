"""
Tags pane tests (HOPPUS-98).

Headless Textual Pilot tests exercise the left sidebar's Tags tab: the
pane lists the vault's real tags (frontmatter ∪ inline, nested tags
under their full name) with note counts, sorted by count then name;
selecting a tag opens the search screen pre-filled with the
``tag:<name>`` operator; the pane repopulates on reindex and vault
switch; and the ``t`` tab switch keeps working. All tests run via
``App.run_test`` wrapped in ``asyncio.run``, matching the suite.
"""

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import Input, TabbedContent

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.search_screen import SearchScreen
from hoppus.tui.panes.tags import TagsPane


def make_app(vault: Path, config_overrides: dict[str, Any] | None = None) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is ``vault``.

    :param vault: The vault directory under test.
    :param config_overrides: Optional top-level config overrides.
    :returns: An unmounted HoppusApp instance.
    """
    config = default_config()
    config["default_vault"] = vault.name
    config.update(config_overrides or {})
    return HoppusApp(config=config, vaults_root=vault.parent)


def option_titles(pane: TagsPane) -> list[str]:
    """
    Return the visible option prompts of the pane, top to bottom.

    :param pane: The tags pane under test.
    :returns: The plain-text prompt of each option.
    """
    return [
        str(pane.get_option_at_index(index).prompt)
        for index in range(pane.option_count)
    ]


def make_vault(tmp_path: Path) -> Path:
    """
    Build a small vault with frontmatter, inline, and nested tags.

    :param tmp_path: The test's temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Rocket.md").write_text(
        "---\ntags: [project, area/sub]\n---\n\n# Rocket\n\nBody with #inline.\n",
        encoding="utf-8",
    )
    (vault / "Diary.md").write_text(
        "# Diary\n\nMore #project notes and #area/sub again.\n",
        encoding="utf-8",
    )
    (vault / "Plain.md").write_text("# Plain\n\nNo tags here.\n", encoding="utf-8")
    return vault


def test_tags_listed_with_counts_after_launch(tmp_path: Path) -> None:
    """
    The Tags tab lists the vault's tags with note counts, sorted by
    count descending then name — nested tags keep their full name.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#tags-pane", TagsPane)
            assert option_titles(pane) == [
                "#area/sub  ·  2",
                "#project  ·  2",
                "#inline  ·  1",
            ]

    asyncio.run(run())


def test_t_switches_to_the_tags_tab(tmp_path: Path) -> None:
    """
    The ``t`` keybinding still activates the Tags tab (spec §9.16),
    which now hosts the real pane.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            left = app.query_one("#left-sidebar", TabbedContent)
            assert left.active == "tab-tags"
            assert app.query_one("#tags-pane", TagsPane).option_count > 0

    asyncio.run(run())


def test_selecting_a_tag_opens_prefilled_search(tmp_path: Path) -> None:
    """
    Selecting a tag opens the search screen pre-filled with
    ``tag:<name>`` and the matching notes already listed.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_tag_pane()
            pane = app.query_one("#tags-pane", TagsPane)
            titles = option_titles(pane)
            pane.highlighted = titles.index("#inline  ·  1")
            pane.action_select()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SearchScreen)
            assert screen.query_one("#search-input", Input).value == "tag:inline"
            paths = {result.path for result in screen._results}
            assert paths == {vault / "Rocket.md"}

    asyncio.run(run())


def test_nested_tag_selection_uses_full_tag_name(tmp_path: Path) -> None:
    """
    Selecting a nested tag feeds the full ``parent/child`` name to the
    ``tag:`` operator, matching both carrying notes.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#tags-pane", TagsPane)
            pane.highlighted = option_titles(pane).index("#area/sub  ·  2")
            pane.action_select()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SearchScreen)
            assert screen.query_one("#search-input", Input).value == "tag:area/sub"
            paths = {result.path for result in screen._results}
            assert paths == {vault / "Rocket.md", vault / "Diary.md"}

    asyncio.run(run())


def test_reindex_refreshes_the_tags_pane(tmp_path: Path) -> None:
    """
    A tag added on disk appears in the pane after a manual reindex.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#tags-pane", TagsPane)
            assert not any("brand-new" in title for title in option_titles(pane))
            (vault / "Fresh.md").write_text(
                "# Fresh\n\nTagged #brand-new.\n", encoding="utf-8"
            )
            app.action_reindex()
            await pilot.pause()
            assert "#brand-new  ·  1" in option_titles(pane)

    asyncio.run(run())


def test_vault_switch_repopulates_the_tags_pane(tmp_path: Path) -> None:
    """
    Switching vaults replaces the tag list with the new vault's tags.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        other = tmp_path / "Other"
        other.mkdir()
        (other / "Lone.md").write_text("# Lone\n\nJust #solo here.\n", encoding="utf-8")
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#tags-pane", TagsPane)
            assert any("project" in title for title in option_titles(pane))
            await app.switch_vault("Other")
            await pilot.pause()
            assert option_titles(pane) == ["#solo  ·  1"]

    asyncio.run(run())


def test_empty_vault_shows_no_tags_state(tmp_path: Path) -> None:
    """
    A vault without tags shows the disabled empty-state row.
    """

    async def run() -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Plain.md").write_text("# Plain\n", encoding="utf-8")
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#tags-pane", TagsPane)
            titles = option_titles(pane)
            assert len(titles) == 1
            assert "No tags" in titles[0]
            assert pane.get_option_at_index(0).disabled

    asyncio.run(run())
