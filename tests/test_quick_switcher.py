"""
Tests for the quick switcher (HOPPUS-28): mount, live filtering,
preview open, editor routing, and Escape cancel.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import Input, OptionList

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.quick_switcher import QuickSwitcherModal, SwitcherResult


def make_app(vault_root: Path) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is the given directory.

    With no matching ``default_vault`` subdirectory present,
    ``_active_vault_path`` falls back to the vaults root, so pointing
    the vaults root at the sample vault makes it the active vault.

    :param vault_root: Directory to use as the vault.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    return HoppusApp(config=config, vaults_root=vault_root)


def _modal(app: HoppusApp) -> QuickSwitcherModal:
    """
    Return the mounted quick switcher modal.
    """
    screen = app.screen
    assert isinstance(screen, QuickSwitcherModal)
    return screen


def test_ctrl_o_mounts_switcher_with_all_notes(sample_vault: Path) -> None:
    """
    ``Ctrl+O`` pushes the modal listing every note in the vault.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.pause()
            modal = _modal(app)
            options = modal.query_one("#switcher-results", OptionList)
            # Index, Target Note, Tags Note, Projects/Alpha, Archive/Alpha.
            assert options.option_count == 5
            assert isinstance(app.focused, Input)

    asyncio.run(run())


def test_typing_filters_candidates_live(sample_vault: Path) -> None:
    """
    Each keystroke re-ranks: fewer rows, best match on top.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.pause()
            modal = _modal(app)
            options = modal.query_one("#switcher-results", OptionList)
            assert options.option_count == 5

            await pilot.press(*"target")
            await pilot.pause()
            assert 0 < options.option_count < 5
            assert modal._results[0].title == "Target Note"

    asyncio.run(run())


def test_alias_query_surfaces_note(sample_vault: Path) -> None:
    """
    A query matching only an alias still finds the note.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.press(*"the target")
            await pilot.pause()
            assert _modal(app)._results[0].title == "Target Note"

    asyncio.run(run())


def test_enter_opens_note_in_preview(sample_vault: Path) -> None:
    """
    Enter dismisses the modal and opens the top match in the preview.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.press(*"target")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, QuickSwitcherModal)
            assert app.note_title == "Target Note"
            assert app.word_count is not None and app.word_count > 0

    asyncio.run(run())


def test_editor_modifier_routes_to_editor_action(sample_vault: Path) -> None:
    """
    ``ctrl+e`` stashes the path and invokes ``action_open_editor``.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        invoked: list[bool] = []
        app.action_open_editor = lambda: invoked.append(True)  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.press(*"target")
            await pilot.pause()
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert not isinstance(app.screen, QuickSwitcherModal)
            assert invoked == [True]
            assert app._pending_editor_path == sample_vault / "Target Note.md"
            # The preview path was not taken.
            assert app.note_title is None

    asyncio.run(run())


def test_escape_cancels_without_navigation(sample_vault: Path) -> None:
    """
    Escape closes the modal with no selection and no navigation.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.pause()
            assert isinstance(app.screen, QuickSwitcherModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, QuickSwitcherModal)
            assert app.note_title is None
            assert app._pending_editor_path is None

    asyncio.run(run())


def test_arrow_keys_move_highlight_from_input(sample_vault: Path) -> None:
    """
    Down/up move the result highlight while the input keeps focus.
    """

    async def run() -> None:
        app = make_app(sample_vault)
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.pause()
            modal = _modal(app)
            options = modal.query_one("#switcher-results", OptionList)
            assert options.highlighted == 0
            await pilot.press("down")
            assert options.highlighted == 1
            await pilot.press("up")
            assert options.highlighted == 0
            assert isinstance(app.focused, Input)

    asyncio.run(run())


def test_switcher_result_defaults() -> None:
    """
    ``SwitcherResult`` defaults to the preview (non-edit) path.
    """
    result = SwitcherResult(path=Path("note.md"))
    assert result.edit is False
