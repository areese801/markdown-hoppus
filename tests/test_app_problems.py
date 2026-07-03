"""
TUI "problems" indicator tests (spec §19 D1, HOPPUS-40).

Headless Textual Pilot tests (``App.run_test`` inside ``asyncio.run``)
over temp vaults: opening a vault full of unresolved links produces a
report-only status-line ⚠ segment — never a blocking modal — while a
clean vault leaves the status string unchanged. The D1 gate
(``link_integrity.prompt_on``) is honored on the open trigger.
"""

import asyncio
from pathlib import Path
from typing import Any

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp, StatusLine
from hoppus.tui.modals.link_integrity import LinkIntegrityModal


def make_dirty_vault(tmp_path: Path) -> Path:
    """
    Build a vault whose notes carry three unresolved wikilinks.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Jane Smith.md").write_text("A person.\n", encoding="utf-8")
    (vault / "Meeting Notes.md").write_text(
        "With [[Jane Smtih]] we kicked off [[Q3 Planning]].\n", encoding="utf-8"
    )
    (vault / "Zed.md").write_text("See [[Nowhere]].\n", encoding="utf-8")
    return vault


def make_clean_vault(tmp_path: Path) -> Path:
    """
    Build a vault whose links all resolve.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "A.md").write_text("Links to [[B]].\n", encoding="utf-8")
    (vault / "B.md").write_text("No links.\n", encoding="utf-8")
    return vault


def make_app(vault: Path, config: dict[str, Any] | None = None) -> HoppusApp:
    """
    Build a HoppusApp rooted directly at the temp vault.
    """
    return HoppusApp(config=config or default_config(), vaults_root=vault)


def assert_no_integrity_modal(app: HoppusApp) -> None:
    """
    Assert the screen stack contains no LinkIntegrityModal (D1).
    """
    assert not any(
        isinstance(screen, LinkIntegrityModal) for screen in app.screen_stack
    )


def test_open_dirty_vault_reports_problems_without_prompting(tmp_path: Path) -> None:
    """
    A vault full of unresolved links yields a ⚠ count, no prompt storm.
    """
    vault = make_dirty_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._problem_count == 3
            assert "⚠ 3 problems" in str(app.query_one(StatusLine).render())
            assert_no_integrity_modal(app)

            await app.open_note(vault / "Meeting Notes.md", vault_root=vault)
            await pilot.pause()
            assert app._problem_count == 3
            assert_no_integrity_modal(app)

    asyncio.run(run())


def test_clean_vault_status_line_has_no_problems_segment(tmp_path: Path) -> None:
    """
    A clean vault leaves the status line byte-identical (no ⚠).
    """
    vault = make_clean_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._refresh_problems()
            assert app._problem_count == 0
            text = str(app.query_one(StatusLine).render())
            assert "⚠" not in text
            assert "problems" not in text
            assert text == " hoppus · Personal · (no note) · – words"
            assert_no_integrity_modal(app)

    asyncio.run(run())


def test_reindex_refreshes_problem_count(tmp_path: Path) -> None:
    """
    Fixing a link on disk and reindexing drops the ⚠ count.
    """
    vault = make_dirty_vault(tmp_path)

    async def run() -> None:
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._problem_count == 3

            (vault / "Q3 Planning.md").write_text("", encoding="utf-8")
            (vault / "Nowhere.md").write_text("", encoding="utf-8")
            note = vault / "Meeting Notes.md"
            note.write_text(
                note.read_text(encoding="utf-8").replace("Jane Smtih", "Jane Smith"),
                encoding="utf-8",
            )
            app.action_reindex()
            await pilot.pause()
            assert app._problem_count == 0
            assert "⚠" not in str(app.query_one(StatusLine).render())

    asyncio.run(run())


def test_open_trigger_prompts_only_in_always_mode(tmp_path: Path) -> None:
    """
    The D1 gate: open_note runs the interactive pass only when
    ``link_integrity.prompt_on`` is "always".
    """
    vault = make_dirty_vault(tmp_path)

    async def run(prompt_on: str) -> list[Path]:
        config = default_config()
        config["link_integrity"]["prompt_on"] = prompt_on
        app = make_app(vault, config)
        passes: list[Path] = []

        async def record_pass(path: Path) -> None:
            passes.append(path)

        app._run_integrity_pass = record_pass  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.open_note(vault / "Meeting Notes.md", vault_root=vault)
            await pilot.pause()
        return passes

    assert asyncio.run(run("editor_return_only")) == []
    assert asyncio.run(run("always")) == [vault / "Meeting Notes.md"]
