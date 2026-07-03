"""
Tests for the hoppus command palette provider (HOPPUS-29, spec §9.4).

The provider is exercised directly (headless) rather than by driving the
palette overlay UI: within ``App.run_test`` we bind a provider to the
running app's screen and call ``discover``/``search``, wrapped in
``asyncio.run`` so no async test plugin is required.
"""

import asyncio
from typing import Any

from textual.command import DiscoveryHit, Hit

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.command_palette import (
    HOPPUS_COMMANDS,
    HoppusCommand,
    HoppusCommandProvider,
)


def make_app() -> HoppusApp:
    """
    Build a HoppusApp with the default config (no filesystem reads).

    Returns:
        An unmounted HoppusApp instance.
    """
    return HoppusApp(config=default_config())


def test_registry_specs_are_well_formed() -> None:
    """
    Every registry entry is a HoppusCommand naming an existing app action.
    """
    assert len(HOPPUS_COMMANDS) == 20
    titles = [spec.title for spec in HOPPUS_COMMANDS]
    assert len(set(titles)) == len(titles)
    for spec in HOPPUS_COMMANDS:
        assert isinstance(spec, HoppusCommand)
        assert spec.title
        assert hasattr(HoppusApp, f"action_{spec.action}")


def test_app_registers_provider() -> None:
    """
    HoppusApp keeps Textual's default commands and adds the provider.
    """
    assert HoppusCommandProvider in HoppusApp.COMMANDS
    assert HoppusApp.COMMANDS > {HoppusCommandProvider}


def test_discover_yields_every_command() -> None:
    """
    ``discover`` yields a DiscoveryHit per registered command.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            provider = HoppusCommandProvider(app.screen)
            hits = [hit async for hit in provider.discover()]
            assert len(hits) == len(HOPPUS_COMMANDS)
            assert all(isinstance(hit, DiscoveryHit) for hit in hits)
            titles = {str(hit.display) for hit in hits}
            assert {"Reindex vault", "Open daily note", "Quick switcher"} <= titles

    asyncio.run(run())


def test_search_matches_and_callback_runs_action() -> None:
    """
    ``search`` scores commands with the fuzzy matcher; invoking a hit's
    callback runs the corresponding app action.
    """

    async def run() -> None:
        app = make_app()
        ran: list[tuple[str, Any]] = []
        original = app.run_action

        async def spy(action: str, *args: Any, **kwargs: Any) -> Any:
            ran.append((action, args))
            return await original(action, *args, **kwargs)

        app.run_action = spy  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.pause()
            provider = HoppusCommandProvider(app.screen)
            hits = [hit async for hit in provider.search("reindex")]
            assert len(hits) == 1
            hit = hits[0]
            assert isinstance(hit, Hit)
            assert hit.score > 0
            assert hit.help == "Rescan the vault and rebuild the index"
            await hit.command()
            await pilot.pause()
            assert ("reindex", ()) in ran
            notifications = list(app._notifications)
            assert any("Reindex" in n.message for n in notifications)

    asyncio.run(run())


def test_search_nonsense_query_yields_nothing() -> None:
    """
    A query matching no command titles yields no hits.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            provider = HoppusCommandProvider(app.screen)
            hits = [hit async for hit in provider.search("zzqxjwv")]
            assert hits == []

    asyncio.run(run())
