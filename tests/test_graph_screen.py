"""
Pilot tests for the interactive graph screen (spec §9.7, HOPPUS-48).

Uses the John-Doe → Team Sync → {Jane, Bob} vault shape from the spec's
motivating use case: opening the graph on ``John Doe`` shows the meeting
note, raising the radius reveals the other people linked from it, and
selecting a person jumps to that note in the preview.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import OptionList, Static

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.graph_view import GraphScreen


@pytest.fixture
def people_vault(tmp_path: Path) -> Path:
    """
    Build the John-Doe → Team Sync → {Jane, Bob} vault (spec §9.7).

    :param tmp_path: Pytest temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "people"
    vault.mkdir()
    (vault / "John Doe.md").write_text("# John Doe\n", encoding="utf-8")
    (vault / "Jane Smith.md").write_text("# Jane Smith\n", encoding="utf-8")
    (vault / "Bob Jones.md").write_text("# Bob Jones\n", encoding="utf-8")
    (vault / "Team Sync.md").write_text(
        "# Team Sync\n\nWith [[John Doe]], [[Jane Smith]], and [[Bob Jones]].\n",
        encoding="utf-8",
    )
    return vault


def make_app(vault_root: Path, *, default_degrees: int = 1) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is the given directory.

    :param vault_root: Directory to use as the vault.
    :param default_degrees: Initial graph radius for the app config.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    config["graph"]["default_degrees"] = default_degrees
    return HoppusApp(config=config, vaults_root=vault_root)


def _screen(app: HoppusApp) -> GraphScreen:
    """
    Return the mounted graph screen.
    """
    screen = app.screen
    assert isinstance(screen, GraphScreen)
    return screen


def _rendered_text(screen: GraphScreen) -> str:
    """
    Join the screen's rendered rows into one searchable string.
    """
    options = screen.query_one("#graph-list", OptionList)
    return "\n".join(
        str(options.get_option_at_index(i).prompt) for i in range(options.option_count)
    )


def test_graph_without_active_note_notifies(people_vault: Path) -> None:
    """
    ``g`` with no open note is a guarded no-op (no screen pushed).
    """

    async def run() -> None:
        app = make_app(people_vault)
        async with app.run_test() as pilot:
            await pilot.press("g")
            await pilot.pause()
            assert not isinstance(app.screen, GraphScreen)

    asyncio.run(run())


def test_graph_renders_meeting_and_people(people_vault: Path) -> None:
    """
    Radius 2 from John Doe shows the meeting plus the other people.
    """

    async def run() -> None:
        app = make_app(people_vault, default_degrees=2)
        async with app.run_test() as pilot:
            await app.open_note(people_vault / "John Doe.md", vault_root=people_vault)
            await pilot.press("g")
            await pilot.pause()
            screen = _screen(app)
            text = _rendered_text(screen)
            assert "Team Sync" in text
            assert "Jane Smith" in text
            assert "Bob Jones" in text
            header = str(screen.query_one("#graph-header", Static).render())
            assert "John Doe" in header
            assert "2/5" in header

    asyncio.run(run())


def test_radius_keys_adjust_view_live(people_vault: Path) -> None:
    """
    ``+`` reveals the 2-hop people; ``-`` hides them again.
    """

    async def run() -> None:
        app = make_app(people_vault, default_degrees=1)
        async with app.run_test() as pilot:
            await app.open_note(people_vault / "John Doe.md", vault_root=people_vault)
            await pilot.press("g")
            await pilot.pause()
            screen = _screen(app)
            assert screen.radius == 1
            text = _rendered_text(screen)
            assert "Team Sync" in text
            assert "Jane Smith" not in text

            await pilot.press("plus")
            await pilot.pause()
            assert screen.radius == 2
            text = _rendered_text(screen)
            assert "Jane Smith" in text
            assert "Bob Jones" in text

            screen.action_radius_down()
            await pilot.pause()
            assert screen.radius == 1
            assert "Jane Smith" not in _rendered_text(screen)
            screen.action_radius_down()
            assert screen.radius == 1

    asyncio.run(run())


def test_radius_clamps_to_max_degrees(people_vault: Path) -> None:
    """
    ``+`` never raises the radius past ``graph.max_degrees``.
    """

    async def run() -> None:
        app = make_app(people_vault, default_degrees=1)
        app.config["graph"]["max_degrees"] = 2
        async with app.run_test() as pilot:
            await app.open_note(people_vault / "John Doe.md", vault_root=people_vault)
            await pilot.press("g")
            await pilot.pause()
            screen = _screen(app)
            screen.action_radius_up()
            screen.action_radius_up()
            screen.action_radius_up()
            assert screen.radius == 2

    asyncio.run(run())


def test_selecting_person_jumps_to_note(people_vault: Path) -> None:
    """
    Selecting a person node dismisses and opens it in the preview.
    """

    async def run() -> None:
        app = make_app(people_vault, default_degrees=2)
        async with app.run_test() as pilot:
            await app.open_note(people_vault / "John Doe.md", vault_root=people_vault)
            await pilot.press("g")
            await pilot.pause()
            screen = _screen(app)
            jane = people_vault / "Jane Smith.md"
            row = screen._row_paths.index(jane)
            screen.select_row(row)
            await pilot.pause()
            assert not isinstance(app.screen, GraphScreen)
            assert app.preview.note_path == jane
            assert app.note_title == "Jane Smith"

    asyncio.run(run())


def test_escape_cancels_and_g_toggles_closed(people_vault: Path) -> None:
    """
    Escape closes with no jump; ``g`` while open also closes the view.
    """

    async def run() -> None:
        app = make_app(people_vault, default_degrees=1)
        async with app.run_test() as pilot:
            john = people_vault / "John Doe.md"
            await app.open_note(john, vault_root=people_vault)
            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, GraphScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, GraphScreen)
            assert app.preview.note_path == john

            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, GraphScreen)
            await pilot.press("g")
            await pilot.pause()
            assert not isinstance(app.screen, GraphScreen)

    asyncio.run(run())
