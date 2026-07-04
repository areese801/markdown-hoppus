"""
Tests for the keyboard-UX cluster (HOPPUS-92/93/94/97): vim-style
navigation and single-character defaults, the two-step ``q``/``qq``
quit, pane focus by number keys, and the ``?`` help overlay.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import Input, TabbedContent

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.help_overlay import HelpScreen
from hoppus.tui.modals.search_screen import SearchScreen
from hoppus.tui.panes.explorer import ExplorerPane

_NOTE_MD = """\
# First Note

Some content linking to [[Nested]].
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
    (vault / "Zed.md").write_text("# Zed\n", encoding="utf-8")
    (vault / "Sub" / "Nested.md").write_text("# Nested\n", encoding="utf-8")
    return vaults_root, "MyVault"


def make_app(
    tmp_path: Path, config_overrides: dict[str, Any] | None = None
) -> HoppusApp:
    """
    Build a HoppusApp rooted at a tiny temp vault.

    Args:
        tmp_path: pytest's per-test temp directory.
        config_overrides: Top-level keys to override in the defaults.

    Returns:
        An unmounted HoppusApp instance.
    """
    vaults_root, vault_name = make_vault(tmp_path)
    config = default_config()
    config["default_vault"] = vault_name
    config.update(config_overrides or {})
    return HoppusApp(config=config, vaults_root=vaults_root)


async def _wait_for_tree(pilot: Any, explorer: ExplorerPane) -> None:
    """
    Pause until the explorer tree has loaded its top-level entries.

    Args:
        pilot: The Textual Pilot driving the app.
        explorer: The explorer pane to wait on.
    """
    for _ in range(50):
        await pilot.pause()
        if explorer.last_line > 0:
            return
    raise AssertionError("Explorer tree never populated")


# -- HOPPUS-92: vim-style navigation ------------------------------------------


def test_explorer_j_k_move_the_cursor(tmp_path: Path) -> None:
    """
    With the explorer focused, ``j``/``k`` move the tree cursor.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            await _wait_for_tree(pilot, explorer)
            explorer.focus()
            explorer.move_cursor_to_line(0)
            await pilot.pause()
            await pilot.press("j")
            assert explorer.cursor_line == 1
            await pilot.press("j")
            assert explorer.cursor_line == 2
            await pilot.press("k")
            assert explorer.cursor_line == 1

    asyncio.run(run())


def test_explorer_h_l_collapse_and_expand(tmp_path: Path) -> None:
    """
    ``l`` expands the cursor directory; ``h`` collapses it, then jumps
    to the parent; ``g``/``G`` jump to the first/last line.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            await _wait_for_tree(pilot, explorer)
            explorer.focus()
            # Line 1 is the Sub/ directory (dirs sort first).
            explorer.move_cursor_to_line(1)
            await pilot.pause()
            node = explorer.cursor_node
            assert node is not None and node.allow_expand
            assert not node.is_expanded
            await pilot.press("l")
            await pilot.pause()
            assert node.is_expanded
            await pilot.press("h")
            await pilot.pause()
            assert not node.is_expanded
            # A second h on the collapsed dir jumps to the parent (root).
            await pilot.press("h")
            assert explorer.cursor_line == 0
            await pilot.press("G")
            assert explorer.cursor_line == explorer.last_line
            await pilot.press("g")
            assert explorer.cursor_line == 0

    asyncio.run(run())


def test_f_opens_search_and_inputs_keep_printable_keys(tmp_path: Path) -> None:
    """
    ``f`` opens the search screen (HOPPUS-92), and once its Input has
    focus, single-character binding keys (``f``, ``j``, ``q``, ``1``)
    are typed literally instead of triggering actions.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("f")
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)
            assert isinstance(app.focused, Input)
            await pilot.press(*"fjq1")
            assert app.focused.value == "fjq1"
            # No action fired: the app is still running, quit unarmed.
            assert not app._quit_armed
            assert app.return_code is None

    asyncio.run(run())


# -- HOPPUS-93: two-step quit ---------------------------------------------------


def test_single_q_arms_instead_of_quitting(tmp_path: Path) -> None:
    """
    One ``q`` does not exit — it arms the quit and stays in the app.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()
            assert app._quit_armed
            assert app.return_code is None

    asyncio.run(run())


def test_double_q_quits(tmp_path: Path) -> None:
    """
    A rapid ``qq`` exits without any further confirmation.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.press("q")
        assert app.return_code == 0

    asyncio.run(run())


def test_escape_cancels_the_armed_quit(tmp_path: Path) -> None:
    """
    The cancel path stays in the app: Escape disarms, and the next
    single ``q`` arms again rather than exiting.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert app._quit_armed
            await pilot.press("escape")
            assert not app._quit_armed
            assert app.return_code is None
            await pilot.press("q")
            await pilot.pause()
            assert app._quit_armed
            assert app.return_code is None

    asyncio.run(run())


def test_any_other_key_cancels_the_armed_quit(tmp_path: Path) -> None:
    """
    A non-quit key (here ``2``, which focuses the main pane) disarms.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert app._quit_armed
            await pilot.press("2")
            assert not app._quit_armed
            assert app.return_code is None

    asyncio.run(run())


# -- HOPPUS-94: pane focus by number --------------------------------------------


def test_number_keys_focus_panes(tmp_path: Path) -> None:
    """
    ``1``/``2``/``3`` focus the explorer, main pane, and backlinks.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("2")
            assert app.focused is app.query_one("#main-pane")
            await pilot.press("3")
            assert app.focused is app.query_one("#backlinks-pane")
            await pilot.press("1")
            assert app.focused is app.query_one("#explorer-pane")
            active_tab = app.query_one("#left-sidebar", TabbedContent).active
            assert active_tab == "tab-explorer"

    asyncio.run(run())


def test_pane_3_notifies_when_sidebar_hidden(tmp_path: Path) -> None:
    """
    With the right sidebar hidden, ``3`` is a guarded no-op.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("backslash")
            assert not app.right_sidebar.display
            await pilot.press("2")
            await pilot.press("3")
            assert app.focused is app.query_one("#main-pane")

    asyncio.run(run())


def test_tab_still_cycles_panes(tmp_path: Path) -> None:
    """
    Number keys coexist with ``Tab`` cycling.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("2")
            assert app.focused is app.query_one("#main-pane")
            await pilot.press("tab")
            assert app.focused is not None
            lineage_ids = {node.id for node in app.focused.ancestors_with_self}
            assert "right-sidebar" in lineage_ids

    asyncio.run(run())


def test_pane_focus_keys_are_remappable(tmp_path: Path) -> None:
    """
    ``keymap: focus_pane_2`` rebinds the main-pane focus key.
    """

    async def run() -> None:
        app = make_app(tmp_path, {"keymap": {"focus_pane_2": "9"}})
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("9")
            assert app.focused is app.query_one("#main-pane")

    asyncio.run(run())


# -- HOPPUS-97: help overlay ----------------------------------------------------


def _help_rows(screen: HelpScreen) -> list[tuple[str, str]]:
    """
    Flatten a help screen's sections into (key, description) rows.

    Args:
        screen: The mounted help overlay.

    Returns:
        Every row across every section.
    """
    return [row for _title, rows in screen._sections for row in rows]


def test_question_mark_opens_help_with_current_keys(tmp_path: Path) -> None:
    """
    ``?`` opens the overlay; it lists the live keymap, including a
    config-remapped key, the vim explorer keys, and pane numbers.
    """

    async def run() -> None:
        app = make_app(tmp_path, {"keymap": {"search": "/"}})
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, HelpScreen)
            rows = _help_rows(screen)
            # The remapped search key is shown, not the default.
            assert ("/", "Search") in rows
            assert ("f", "Search") not in rows
            assert ("q", "Quit") in rows
            assert ("1", "Explorer pane") in rows
            assert ("j", "Down") in rows
            assert ("?", "Help") in rows
            # The overlay actually renders one line per row.
            assert len(screen.query(".help-row")) == len(rows)

    asyncio.run(run())


def test_help_overlay_toggles_and_escape_closes(tmp_path: Path) -> None:
    """
    ``?`` toggles the overlay closed; so does ``Escape``.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("question_mark")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

    asyncio.run(run())
