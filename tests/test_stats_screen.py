"""
Tests for the vault stats view (spec §9.13, HOPPUS-53): pushing the
screen via ``action_vault_stats``, rendering the aggregated counts,
Escape closing, and direct rendering of a canned ``VaultStats``.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import Static

from hoppus.config import default_config
from hoppus.stats import VaultStats
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.stats_screen import StatsScreen


def make_vault(tmp_path: Path) -> Path:
    """
    Build a small temp vault: two linked notes and one orphan.

    :param tmp_path: The pytest temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "A.md").write_text("one two [[B]]\n", encoding="utf-8")
    (vault / "B.md").write_text("three four five\n", encoding="utf-8")
    (vault / "Orphan.md").write_text("six\n", encoding="utf-8")
    return vault


def make_app(vault_root: Path) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is the given directory.

    :param vault_root: Directory to use as the vault.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    return HoppusApp(config=config, vaults_root=vault_root)


def _line(screen: StatsScreen, widget_id: str) -> str:
    """
    Return one stats line's rendered text.

    :param screen: The mounted stats screen.
    :param widget_id: The Static widget id (without ``#``).
    :returns: The line's plain text.
    """
    return str(screen.query_one(f"#{widget_id}", Static).render())


def test_action_vault_stats_pushes_screen(tmp_path: Path) -> None:
    """
    ``action_vault_stats`` aggregates the vault and shows the counts.
    """

    async def run() -> None:
        app = make_app(make_vault(tmp_path))
        async with app.run_test() as pilot:
            app.action_vault_stats()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, StatsScreen)
            assert _line(screen, "stats-notes") == "Notes: 3"
            # A: 3 body words, B: 3, Orphan: 1.
            assert _line(screen, "stats-words") == "Total words: 7"
            assert _line(screen, "stats-orphans") == "Orphans: 1"
            assert _line(screen, "stats-unresolved") == "Unresolved links: 0"

    asyncio.run(run())


def test_escape_closes_stats_screen(tmp_path: Path) -> None:
    """
    Escape dismisses the stats view back to the main screen.
    """

    async def run() -> None:
        app = make_app(make_vault(tmp_path))
        async with app.run_test() as pilot:
            app.action_vault_stats()
            await pilot.pause()
            assert isinstance(app.screen, StatsScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, StatsScreen)

    asyncio.run(run())


def test_stats_screen_renders_canned_stats(tmp_path: Path) -> None:
    """
    A fed ``VaultStats`` renders every line, including top tags.
    """
    stats = VaultStats(
        note_count=42,
        total_words=1234,
        tag_count=3,
        top_tags=(("alpha", 5), ("beta", 2)),
        orphan_count=4,
        unresolved_link_count=6,
    )

    async def run() -> None:
        app = make_app(make_vault(tmp_path))
        async with app.run_test() as pilot:
            await app.push_screen(StatsScreen(stats))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, StatsScreen)
            assert _line(screen, "stats-notes") == "Notes: 42"
            assert _line(screen, "stats-words") == "Total words: 1234"
            assert _line(screen, "stats-tags") == "Distinct tags: 3"
            assert _line(screen, "stats-top-tags") == "Top tags: alpha (5), beta (2)"
            assert _line(screen, "stats-orphans") == "Orphans: 4"
            assert _line(screen, "stats-unresolved") == "Unresolved links: 6"

    asyncio.run(run())


def test_stats_screen_no_tags_line(tmp_path: Path) -> None:
    """
    An empty top_tags renders the "(none)" placeholder.
    """
    stats = VaultStats(
        note_count=0,
        total_words=0,
        tag_count=0,
        top_tags=(),
        orphan_count=0,
        unresolved_link_count=0,
    )

    async def run() -> None:
        app = make_app(make_vault(tmp_path))
        async with app.run_test() as pilot:
            await app.push_screen(StatsScreen(stats))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, StatsScreen)
            assert _line(screen, "stats-top-tags") == "Top tags: (none)"

    asyncio.run(run())
