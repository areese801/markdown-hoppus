"""
Tests for the vault switcher (HOPPUS-30): modal listing and live
filtering, in-app vault switching, and defensive handling of missing
vaults.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Input, OptionList

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.vault_switcher import VaultSwitcherModal
from hoppus.tui.panes.explorer import ExplorerPane


@pytest.fixture()
def vaults_root(tmp_path: Path) -> Path:
    """
    Build a temp Vaults Root containing two small vaults.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The Vaults Root as a ``Path``.
    """
    for vault_name in ("Personal", "Work"):
        vault = tmp_path / vault_name
        vault.mkdir()
        (vault / "Home.md").write_text(
            f"# {vault_name} Home\n\nWelcome to {vault_name}.\n", encoding="utf-8"
        )
        (vault / "Other.md").write_text(
            f"# {vault_name} Other\n\nMore notes.\n", encoding="utf-8"
        )
    return tmp_path


def make_app(vaults_root: Path, default_vault: str | None = None) -> HoppusApp:
    """
    Build a HoppusApp rooted at the given Vaults Root.

    :param vaults_root: Directory containing the vaults.
    :param default_vault: Optional default vault name for the config.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    if default_vault is not None:
        config["default_vault"] = default_vault
    return HoppusApp(config=config, vaults_root=vaults_root)


def _modal(app: HoppusApp) -> VaultSwitcherModal:
    """
    Return the mounted vault switcher modal.
    """
    screen = app.screen
    assert isinstance(screen, VaultSwitcherModal)
    return screen


def test_modal_lists_all_vaults_and_preselects_current(vaults_root: Path) -> None:
    """
    The modal lists every discovered vault and highlights the current one.
    """

    async def run() -> None:
        app = make_app(vaults_root, default_vault="Work")
        async with app.run_test() as pilot:
            await pilot.press("v")
            await pilot.pause()
            modal = _modal(app)
            assert modal._results == ["Personal", "Work"]
            options = modal.query_one("#vault-switcher-results", OptionList)
            assert options.option_count == 2
            assert options.highlighted == 1
            assert isinstance(app.focused, Input)

    asyncio.run(run())


def test_typing_filters_vaults_live(vaults_root: Path) -> None:
    """
    Each keystroke re-ranks the vault list; best match on top.
    """

    async def run() -> None:
        app = make_app(vaults_root)
        async with app.run_test() as pilot:
            await pilot.press("v")
            await pilot.pause()
            modal = _modal(app)
            options = modal.query_one("#vault-switcher-results", OptionList)
            assert options.option_count == 2

            await pilot.press(*"pers")
            await pilot.pause()
            assert modal._results[0] == "Personal"
            assert options.option_count < 2

    asyncio.run(run())


def test_enter_switches_active_vault(vaults_root: Path) -> None:
    """
    Enter dismisses the modal and switches the active vault.
    """

    async def run() -> None:
        app = make_app(vaults_root, default_vault="Personal")
        async with app.run_test() as pilot:
            await pilot.press("v")
            await pilot.press(*"work")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, VaultSwitcherModal)
            assert app.vault_name == "Work"

    asyncio.run(run())


def test_escape_cancels_without_switching(vaults_root: Path) -> None:
    """
    Escape closes the modal and leaves the active vault untouched.
    """

    async def run() -> None:
        app = make_app(vaults_root, default_vault="Personal")
        async with app.run_test() as pilot:
            await pilot.press("v")
            await pilot.pause()
            assert isinstance(app.screen, VaultSwitcherModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, VaultSwitcherModal)
            assert app.vault_name == "Personal"

    asyncio.run(run())


def test_switch_vault_updates_panes_and_status(vaults_root: Path) -> None:
    """
    ``switch_vault`` re-roots the explorer, resets the preview, and
    clears the note title/word count.
    """

    async def run() -> None:
        app = make_app(vaults_root, default_vault="Personal")
        async with app.run_test() as pilot:
            await app.open_note(
                vaults_root / "Personal" / "Home.md",
                vault_root=vaults_root / "Personal",
            )
            await pilot.pause()
            assert app.note_title == "Home"

            await app.switch_vault("Work")
            await pilot.pause()

            assert app.vault_name == "Work"
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            assert Path(explorer.path) == vaults_root / "Work"
            preview = app.preview
            assert preview.note_path is None
            assert preview.raw_mode is False
            assert preview.vault_root == vaults_root / "Work"
            assert app.note_title is None
            assert app.word_count is None

    asyncio.run(run())


def test_open_note_works_after_switching(vaults_root: Path) -> None:
    """
    A note from the newly active vault loads in the preview.
    """

    async def run() -> None:
        app = make_app(vaults_root, default_vault="Personal")
        async with app.run_test() as pilot:
            await app.switch_vault("Work")
            await pilot.pause()
            await app.open_note(vaults_root / "Work" / "Home.md")
            await pilot.pause()
            assert app.note_title == "Home"
            assert app.preview.note_path == vaults_root / "Work" / "Home.md"

    asyncio.run(run())


def test_switch_to_missing_vault_notifies_without_crash(vaults_root: Path) -> None:
    """
    Switching to a vault that no longer exists is a gentle no-op.
    """

    async def run() -> None:
        app = make_app(vaults_root, default_vault="Personal")
        async with app.run_test() as pilot:
            await app.switch_vault("Ghost")
            await pilot.pause()
            assert app.vault_name == "Personal"
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            assert Path(explorer.path) == vaults_root / "Personal"

    asyncio.run(run())


def test_switcher_with_empty_vaults_root_notifies(tmp_path: Path) -> None:
    """
    With no vaults under the root, pressing ``v`` notifies instead of
    pushing the modal.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("v")
            await pilot.pause()
            assert not isinstance(app.screen, VaultSwitcherModal)

    asyncio.run(run())
