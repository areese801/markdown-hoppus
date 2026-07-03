"""
Tests for the in-TUI search screen (spec §9.8, HOPPUS-44): mounting via
the search action, live result population, operator queries, choosing a
result into the preview, bad-query resilience, and Escape cancel.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import Input, OptionList

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.search_screen import SearchScreen


def make_app(vault_root: Path) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is the given directory.

    :param vault_root: Directory to use as the vault.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    return HoppusApp(config=config, vaults_root=vault_root)


def _screen(app: HoppusApp) -> SearchScreen:
    """
    Return the mounted search screen.
    """
    screen = app.screen
    assert isinstance(screen, SearchScreen)
    return screen


def test_search_action_mounts_screen(sample_vault: Path) -> None:
    """
    The search binding pushes the screen with the input focused.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()
            screen = _screen(app)
            assert isinstance(app.focused, Input)
            options = screen.query_one("#search-results", OptionList)
            assert options.option_count == 0

    asyncio.run(run())


def test_typing_populates_ranked_results(sample_vault: Path) -> None:
    """
    A typed query populates results, best title match on top.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()
            screen = _screen(app)
            screen.update_results("target")
            await pilot.pause()
            options = screen.query_one("#search-results", OptionList)
            assert options.option_count > 0
            assert screen._results[0].title == "Target Note"

    asyncio.run(run())


def test_operator_query_filters_results(sample_vault: Path) -> None:
    """
    An operators-only query lists exactly the surviving notes.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()
            screen = _screen(app)
            screen.update_results("path:Projects/")
            await pilot.pause()
            assert [result.title for result in screen._results] == ["Alpha"]
            assert screen._results[0].path == sample_vault / "Projects" / "Alpha.md"

    asyncio.run(run())


def test_enter_opens_chosen_note_in_preview(sample_vault: Path) -> None:
    """
    Enter dismisses with the highlighted result and opens the preview.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()
            screen = _screen(app)
            screen.update_results("title:Target")
            await pilot.pause()
            assert screen._results[0].title == "Target Note"
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, SearchScreen)
            assert app.preview.note_path == sample_vault / "Target Note.md"
            assert app.note_title == "Target Note"

    asyncio.run(run())


def test_bad_query_never_crashes_screen(sample_vault: Path) -> None:
    """
    A pathological query yields an empty list, not an exception.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()
            screen = _screen(app)
            screen.update_results('tag:"unbalanced ::: //')
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)

    asyncio.run(run())


def test_escape_cancels_without_navigation(sample_vault: Path) -> None:
    """
    Escape closes the screen with no selection and no navigation.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+f")
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SearchScreen)
            assert app.note_title is None

    asyncio.run(run())
