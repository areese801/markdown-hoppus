"""
Tests for the note-open / index / preview cluster (HOPPUS-118/122/117).

HOPPUS-118: opening a note must reuse the app's cached vault index for
wikilink resolution instead of rebuilding the whole vault per open — the
``PreviewPane`` no longer owns a second index. HOPPUS-122: when a vault
switch races the launch-time index build, the currently open note's
backlinks and preview refresh automatically once the index is ready, no
manual reopen needed. HOPPUS-117: the empty-pane hint names the LIVE
quick-switcher key (default ``o``, remap-aware), not a stale ``^o``.

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run`` so no async test plugin is required. The HOPPUS-122
race test holds the launch build on a ``threading.Event`` (opting out
of the conftest settle fixture with ``@pytest.mark.no_index_settle``)
so nothing is timing-dependent.
"""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Static

from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.backlinks import BacklinksPane


def make_app(vaults_root: Path, config: dict[str, Any] | None = None) -> HoppusApp:
    """
    Build a HoppusApp rooted at the given Vaults Root.

    :param vaults_root: Directory containing the vault(s) under test.
    :param config: Optional pre-built config; defaults are used when
        omitted.
    :returns: An unmounted HoppusApp instance.
    """
    return HoppusApp(config=config or default_config(), vaults_root=vaults_root)


def count_index_builds(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """
    Spy on ``Index.build``, recording every built vault root.

    :param monkeypatch: pytest's monkeypatch fixture.
    :returns: The (live) list of vault roots passed to ``Index.build``.
    """
    real_build = Index.build
    calls: list[Path] = []

    def counting_build(cls: type[Index], vault_root: Path) -> Index:
        calls.append(Path(vault_root))
        return real_build(vault_root)

    monkeypatch.setattr(Index, "build", classmethod(counting_build))
    return calls


# -- HOPPUS-118: note-open reuses the app's cached index ----------------------


def test_opening_notes_triggers_at_most_one_full_build(
    sample_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Opening several notes builds the vault index AT MOST once total.

    Before HOPPUS-118 the preview rebuilt its own full-vault index on
    every ``open_note`` (O(vault) per open); now it borrows the app's
    cached index, so repeated opens are index-build-free.
    """
    calls = count_index_builds(monkeypatch)

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            baseline = len(calls)
            for name in ("Index.md", "Target Note.md", "Tags Note.md"):
                await app.open_note(sample_vault / name, vault_root=sample_vault)
                await pilot.pause()
            # At most one build (the first open re-roots the cache from
            # the vaults root to the vault) — never one per open.
            assert len(calls) - baseline <= 1
            # And further opens are entirely build-free.
            settled = len(calls)
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            assert len(calls) == settled

    asyncio.run(run())


def test_preview_shares_the_apps_cached_index(sample_vault: Path) -> None:
    """
    The preview resolves wikilinks against the app's own index object.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            assert app._index is not None
            assert app.preview.index is app._index
            # Wikilink resolution still works through the shared index.
            rendered = app.preview._rendered
            assert rendered is not None
            assert "hoppus://" in rendered

    asyncio.run(run())


def test_reassigning_unchanged_vault_root_keeps_the_index(sample_vault: Path) -> None:
    """
    Setting ``vault_root`` to its current value must NOT drop the index.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            preview = app.preview
            assert preview.index is not None
            preview.vault_root = sample_vault
            assert preview.index is not None
            # An ACTUAL change still invalidates.
            preview.vault_root = sample_vault.parent
            assert preview.index is None

    asyncio.run(run())


# -- HOPPUS-122: backlinks populate after a deferred index build ---------------


@pytest.fixture()
def two_vaults(tmp_path: Path) -> Path:
    """
    Build a Vaults Root with two vaults; ``Work/Home.md`` has a backlink.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The Vaults Root as a ``Path``.
    """
    for vault_name in ("Personal", "Work"):
        vault = tmp_path / vault_name
        vault.mkdir()
        (vault / "Home.md").write_text(
            f"# {vault_name} Home\n\nWelcome.\n", encoding="utf-8"
        )
        (vault / "Other.md").write_text("# Other\n\nSee [[Home]].\n", encoding="utf-8")
    return tmp_path


@pytest.mark.no_index_settle
def test_backlinks_populate_after_vault_switch_with_deferred_build(
    two_vaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Switch vaults mid-launch-build, open a note: once the build lands,
    backlinks (and the preview) refresh WITHOUT a manual reopen.
    """
    real_build = Index.build
    gate = threading.Event()
    main_ident = threading.get_ident()

    def gated_build(cls: type[Index], vault_root: Path) -> Index:
        # Only the launch worker's off-thread build is held; app-thread
        # builds (tags pane, the post-switch rebuild) pass through.
        if threading.get_ident() != main_ident:
            assert gate.wait(timeout=10), "index-build gate was never released"
        return real_build(vault_root)

    monkeypatch.setattr(Index, "build", classmethod(gated_build))

    config: dict[str, Any] = default_config()
    config["default_vault"] = "Personal"
    config["watcher"] = {"enabled": False}

    async def run() -> None:
        app = make_app(two_vaults, config=config)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._indexing is True

            await app.switch_vault("Work")
            await pilot.pause()
            work = two_vaults / "Work"
            await app.open_note(work / "Home.md", vault_root=work)
            await pilot.pause()
            # The index is not ready yet: backlinks could not populate.
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert work / "Other.md" not in pane._paths.values()

            gate.set()
            assert app._launch_worker is not None
            await app._launch_worker.wait()
            await pilot.pause()
            await pilot.pause()

            # The ready index refreshed the open note automatically.
            assert app._indexing is False
            assert app._index_root == work
            assert app.preview.index is app._index
            assert work / "Other.md" in pane._paths.values()

    asyncio.run(run())


def test_backlinks_populate_after_plain_vault_switch(two_vaults: Path) -> None:
    """
    The settled path: switch vaults, open a note, backlinks are there.
    """
    config: dict[str, Any] = default_config()
    config["default_vault"] = "Personal"

    async def run() -> None:
        app = make_app(two_vaults, config=config)
        async with app.run_test() as pilot:
            await app.switch_vault("Work")
            await pilot.pause()
            work = two_vaults / "Work"
            await app.open_note(work / "Home.md", vault_root=work)
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert work / "Other.md" in pane._paths.values()

    asyncio.run(run())


# -- HOPPUS-117: the empty-pane hint names the live quick-switcher key ---------


def test_empty_hint_names_default_quick_switcher_key(tmp_path: Path) -> None:
    """
    With the default keymap, the hint names ``o`` — never a stale ``^o``.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("# Alpha\n", encoding="utf-8")

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test():
            hint = str(app.query_one("#preview-empty", Static).render())
            assert "Press o to open a note" in hint
            assert "^o" not in hint
            assert "Enter on a file in the Explorer" in hint

    asyncio.run(run())


def test_empty_hint_follows_quick_switcher_remap(tmp_path: Path) -> None:
    """
    A ``keymap:`` remap of the quick switcher changes the hint too.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("# Alpha\n", encoding="utf-8")
    config: dict[str, Any] = default_config()
    config["keymap"] = {"quick_switcher": "ctrl+o"}

    async def run() -> None:
        app = make_app(vault, config=config)
        async with app.run_test():
            hint = str(app.query_one("#preview-empty", Static).render())
            assert "Press ctrl+o to open a note" in hint

    asyncio.run(run())
