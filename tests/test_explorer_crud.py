"""
Pilot tests for the File Explorer CRUD flows (HOPPUS-34).

All tests run headless via ``App.run_test`` wrapped in ``asyncio.run``.
Modal outcomes are fed directly (``screen.dismiss(...)`` or the pane's
``_do_*`` callbacks) rather than via real key timing, which is flaky.
"""

import asyncio
from pathlib import Path

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.confirm import ConfirmModal
from hoppus.tui.modals.text_prompt import TextPromptModal
from hoppus.tui.panes.explorer import ExplorerPane

_NOTE_MD = """\
# Note

Links to [[Nested]].
"""

_NESTED_MD = """\
# Nested

Nested content.
"""


def make_vault(tmp_path: Path) -> tuple[Path, str]:
    """
    Build a tiny vault under a Vaults Root in a temp dir.

    Args:
        tmp_path: pytest's per-test temp directory.

    Returns:
        The (vaults_root, vault_name) pair for constructing the app.
    """
    vaults_root = tmp_path / "Notes"
    vault = vaults_root / "MyVault"
    (vault / "Sub").mkdir(parents=True)
    (vault / "Note.md").write_text(_NOTE_MD, encoding="utf-8")
    (vault / "Sub" / "Nested.md").write_text(_NESTED_MD, encoding="utf-8")
    (vault / ".obsidian").mkdir()
    return vaults_root, "MyVault"


def make_app(vaults_root: Path, vault_name: str) -> HoppusApp:
    """
    Build a HoppusApp with an explicit config (no filesystem reads).

    Args:
        vaults_root: The Vaults Root directory.
        vault_name: The default vault name.

    Returns:
        An unmounted HoppusApp instance.
    """
    config = default_config()
    config["default_vault"] = vault_name
    return HoppusApp(config=config, vaults_root=vaults_root)


def test_new_note_flow_creates_and_opens_note(tmp_path: Path) -> None:
    """
    ``action_new_note`` prompts, creates the note at the vault root,
    and opens it in the preview.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        vault = vaults_root / vault_name
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            explorer.action_new_note()
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)
            app.screen.dismiss("Inbox note")
            await pilot.pause()
            created = vault / "Inbox note.md"
            assert created.is_file()
            assert app.preview.note_path == created
            assert app.note_title == "Inbox note"

    asyncio.run(run())


def test_app_new_note_action_delegates_to_explorer(tmp_path: Path) -> None:
    """
    The app-level ``new_note`` action opens the explorer's prompt.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_new_note()
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)

    asyncio.run(run())


def test_reserved_name_is_rejected_without_creating(tmp_path: Path) -> None:
    """
    A reserved-character name never creates a file (spec §5.2).
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        vault = vaults_root / vault_name
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            before = set(vault.iterdir())
            await explorer._do_create_note("Bad|Name")
            await pilot.pause()
            assert set(vault.iterdir()) == before

    asyncio.run(run())


def test_delete_goes_through_confirm_modal(tmp_path: Path) -> None:
    """
    Delete asks for confirmation; Yes removes the file, No keeps it.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        note = vaults_root / vault_name / "Note.md"
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            node = next(
                child for child in explorer.root.children if child.data.path == note
            )
            explorer.move_cursor(node)

            explorer.action_delete_entry()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            app.screen.dismiss(False)
            await pilot.pause()
            assert note.is_file()

            explorer.action_delete_entry()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            app.screen.dismiss(True)
            await pilot.pause()
            assert not note.exists()

    asyncio.run(run())


def test_rename_flow_prompts_for_links_and_rewrites(tmp_path: Path) -> None:
    """
    Renaming a linked note prompts to update inbound links (config
    default ``prompt_before_link_update=True``) and rewrites them on
    Yes.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        vault = vaults_root / vault_name
        nested = vault / "Sub" / "Nested.md"
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            await explorer._do_rename(nested, "Renamed")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            app.screen.dismiss(True)
            await pilot.pause()
            assert (vault / "Sub" / "Renamed.md").is_file()
            assert not nested.exists()
            assert "[[Renamed]]" in (vault / "Note.md").read_text(encoding="utf-8")

    asyncio.run(run())


def test_move_flow_relocates_note(tmp_path: Path) -> None:
    """
    The move flow relocates a note into a vault-relative folder.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        vault = vaults_root / vault_name
        note = vault / "Note.md"
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            await explorer._do_move(note, "Sub")
            await pilot.pause()
            if isinstance(app.screen, ConfirmModal):
                app.screen.dismiss(True)
                await pilot.pause()
            assert (vault / "Sub" / "Note.md").is_file()
            assert not note.exists()

    asyncio.run(run())


def test_new_folder_flow_creates_folder(tmp_path: Path) -> None:
    """
    The new-folder flow creates a directory under the vault root.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        vault = vaults_root / vault_name
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            await explorer._do_create_folder(vault, "Projects")
            await pilot.pause()
            assert (vault / "Projects").is_dir()

    asyncio.run(run())
