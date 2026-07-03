"""
Tests for the File Explorer pane (HOPPUS-26): tree render + open-in-preview.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run``. Each test builds a tiny temp vault under a Vaults Root
so the explorer roots at ``vaults_root / default_vault``.
"""

import asyncio
from pathlib import Path

from rich.style import Style

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.explorer import ExplorerPane

_NOTE_MD = """\
# First Note

Some content linking to [[Nested]].
"""

_NESTED_MD = """\
# Nested

Nested note content.
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
    (vault / "image.png").write_bytes(b"\x00")
    (vault / ".hoppus").mkdir()
    (vault / ".hoppus" / "state.json").write_text("{}", encoding="utf-8")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    return vaults_root, "MyVault"


def make_app(vaults_root: Path, vault_name: str | None) -> HoppusApp:
    """
    Build a HoppusApp with an explicit config (no filesystem reads).

    Args:
        vaults_root: The Vaults Root directory.
        vault_name: The default vault name, or None for no default.

    Returns:
        An unmounted HoppusApp instance.
    """
    config = default_config()
    config["default_vault"] = vault_name
    return HoppusApp(config=config, vaults_root=vaults_root)


def test_explorer_shows_notes_and_hides_dot_dirs(tmp_path: Path) -> None:
    """
    The tree mounts rooted at the vault, listing notes and folders but
    never ``.hoppus``/``.obsidian`` (or any dot-directory).
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            assert Path(explorer.path) == vaults_root / vault_name
            names = {node.data.path.name for node in explorer.root.children}
            assert {"Note.md", "Sub", "image.png"} <= names
            assert ".hoppus" not in names
            assert ".obsidian" not in names

    asyncio.run(run())


def test_explorer_falls_back_to_vaults_root(tmp_path: Path) -> None:
    """
    With no default vault set, the explorer roots at the Vaults Root.
    """

    async def run() -> None:
        vaults_root, _ = make_vault(tmp_path)
        app = make_app(vaults_root, None)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            assert Path(explorer.path) == vaults_root

    asyncio.run(run())


def test_selecting_md_opens_note_in_preview(tmp_path: Path) -> None:
    """
    Selecting a ``.md`` node loads it into the preview and updates the
    app's ``note_title`` reactive (and thus the status line).
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        note_path = vaults_root / vault_name / "Note.md"
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            node = next(
                child
                for child in explorer.root.children
                if child.data.path == note_path
            )
            explorer.post_message(ExplorerPane.FileSelected(node, note_path))
            await pilot.pause()
            assert app.note_title == "Note"
            assert app.preview.note_path == note_path
            assert app.preview.vault_root == vaults_root / vault_name

    asyncio.run(run())


def test_selecting_attachment_is_a_no_op(tmp_path: Path) -> None:
    """
    Selecting an attachment does not open a note or raise.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        image_path = vaults_root / vault_name / "image.png"
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            node = next(
                child
                for child in explorer.root.children
                if child.data.path == image_path
            )
            explorer.post_message(ExplorerPane.FileSelected(node, image_path))
            await pilot.pause()
            assert app.note_title is None
            assert app.preview.note_path is None

    asyncio.run(run())


def test_attachment_labels_are_dimmed(tmp_path: Path) -> None:
    """
    Non-``.md`` file labels render dimmed; ``.md`` labels do not.
    """

    async def run() -> None:
        vaults_root, vault_name = make_vault(tmp_path)
        app = make_app(vaults_root, vault_name)
        async with app.run_test() as pilot:
            await pilot.pause()
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            by_name = {node.data.path.name: node for node in explorer.root.children}

            def is_dimmed(name: str) -> bool:
                label = explorer.render_label(by_name[name], Style(), Style())
                return any("dim" in str(span.style) for span in label.spans)

            assert is_dimmed("image.png")
            assert not is_dimmed("Note.md")
            assert not is_dimmed("Sub")

    asyncio.run(run())
