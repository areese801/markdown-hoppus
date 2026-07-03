"""
Tests for the TUI app shell (HOPPUS-25): layout, keymap, status line.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from typing import Any

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp, StatusLine
from hoppus.tui.panes.backlinks import BacklinksPane
from hoppus.tui.keymap import (
    DEFAULT_KEYMAP,
    default_bindings,
    normalize_key,
    resolve_keymap_overrides,
)


def make_app(config_overrides: dict[str, Any] | None = None) -> HoppusApp:
    """
    Build a HoppusApp with an explicit config (no filesystem reads).

    Args:
        config_overrides: Top-level keys to override in the defaults.

    Returns:
        An unmounted HoppusApp instance.
    """
    config = default_config()
    config.update(config_overrides or {})
    return HoppusApp(config=config)


def test_app_mounts_with_three_regions() -> None:
    """
    The app mounts with a left sidebar, main pane, and right sidebar.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-sidebar")
            main = app.query_one("#main-pane")
            right = app.query_one("#right-sidebar")
            assert left.display and main.display and right.display
            assert {tab.id for tab in left.query("TabPane")} == {
                "tab-explorer",
                "tab-tags",
                "tab-bookmarks",
            }
            assert app.query_one("#backlinks-pane", BacklinksPane)
            assert app.query_one(StatusLine)
            assert app.query_one("Footer")

    asyncio.run(run())


def test_backslash_toggles_right_sidebar() -> None:
    """
    Pressing ``\\`` hides the right sidebar, and again restores it.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            right = app.query_one("#right-sidebar")
            assert right.display
            await pilot.press("backslash")
            assert not right.display
            await pilot.press("backslash")
            assert right.display

    asyncio.run(run())


def test_tab_cycles_pane_focus() -> None:
    """
    Pressing ``Tab`` cycles focus left sidebar -> main -> right -> left.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()

            def focused_region() -> str | None:
                assert app.focused is not None
                lineage_ids = {node.id for node in app.focused.ancestors_with_self}
                for region_id in ("left-sidebar", "main-pane", "right-sidebar"):
                    if region_id in lineage_ids:
                        return region_id
                return None

            seen: list[str | None] = []
            for _ in range(3):
                await pilot.press("tab")
                seen.append(focused_region())
            assert set(seen) == {"left-sidebar", "main-pane", "right-sidebar"}

            # With the right sidebar hidden, Tab skips it.
            await pilot.press("backslash")
            regions_without_right = set()
            for _ in range(4):
                await pilot.press("tab")
                regions_without_right.add(focused_region())
            assert regions_without_right == {"left-sidebar", "main-pane"}

    asyncio.run(run())


def test_status_line_reflects_reactive_state() -> None:
    """
    The status line shows vault name, note title, and word count.
    """

    async def run() -> None:
        app = make_app({"default_vault": "Personal"})
        async with app.run_test() as pilot:
            await pilot.pause()
            status = app.query_one(StatusLine)
            assert "Personal" in str(status.render())
            assert "(no note)" in str(status.render())

            app.note_title = "Weekly Review"
            app.word_count = 1234
            app.vault_name = "Work"
            await pilot.pause()
            text = str(status.render())
            assert "Work" in text
            assert "Weekly Review" in text
            assert "1234 words" in text

    asyncio.run(run())


def test_keymap_override_is_honored_in_app() -> None:
    """
    A ``keymap`` config override rebinds an action to the new key.
    """

    async def run() -> None:
        app = make_app({"keymap": {"quit": "ctrl+q"}})
        async with app.run_test() as pilot:
            await pilot.pause()
            active = {
                key: binding.binding.id for key, binding in app.active_bindings.items()
            }
            assert active.get("ctrl+q") == "quit"
            assert active.get("q") != "quit"
            await pilot.press("ctrl+q")
        assert app.return_code == 0

    asyncio.run(run())


def test_default_bindings_cover_spec_keymap() -> None:
    """
    Every §9.16 action has a Binding with a matching remap id.
    """
    bindings = default_bindings()
    assert {binding.id for binding in bindings} == set(DEFAULT_KEYMAP)
    by_id = {binding.id: binding for binding in bindings}
    assert by_id["help"].key == "question_mark"
    assert by_id["toggle_right_sidebar"].key == "backslash"
    assert by_id["cycle_pane"].key == "tab"
    assert by_id["quit"].key == "q"
    assert by_id["command_palette"].key == "ctrl+p"


def test_resolve_keymap_overrides_normalizes_and_filters() -> None:
    """
    Overrides are normalized to Textual key names; unknown ids drop.
    """
    config = {
        "keymap": {
            "toggle_right_sidebar": "|",
            "help": "?",
            "quit": "ctrl+q",
            "not_a_real_action": "x",
            "search": None,
        }
    }
    overrides = resolve_keymap_overrides(config)
    assert overrides == {
        "toggle_right_sidebar": "vertical_line",
        "help": "question_mark",
        "quit": "ctrl+q",
    }
    assert resolve_keymap_overrides({}) == {}
    assert resolve_keymap_overrides({"keymap": "bogus"}) == {}
    assert normalize_key("\\") == "backslash"
    assert normalize_key("g") == "g"
