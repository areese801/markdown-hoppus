"""
Editor-return integrity pass tests (spec §7.3, §9.6, D1, HOPPUS-39).

Headless Textual Pilot tests (``App.run_test`` inside ``asyncio.run``,
matching the suite). ``_run_integrity_pass`` is driven directly with the
modal loop stubbed — no subprocess, no ``suspend()``, no live editor —
proving a correct and a create decision both take effect on disk and
that the pass honors the ``enforce_no_dangling_wikilinks`` toggle.
"""

import asyncio
from pathlib import Path

from hoppus.config import default_config
from hoppus.integrity import Decision, UnresolvedWikilink
from hoppus.tui.app import HoppusApp

BODY = "…reviewed the roadmap with [[Jane Smtih]] and kicked off [[Q3 Planning]].\n"
FIXED = "…reviewed the roadmap with [[Jane Smith]] and kicked off [[Q3 Planning]].\n"


def make_vault(tmp_path: Path) -> Path:
    """
    Build the §7.3 worked-example vault: Jane Smith exists, Q3 doesn't.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Jane Smith.md").write_text("A person.\n", encoding="utf-8")
    (vault / "Meeting Notes.md").write_text(BODY, encoding="utf-8")
    return vault


def stub_prompts(app: HoppusApp, log: list[str]) -> None:
    """
    Replace the modal loop with canned §7.3 worked-example decisions.

    The typo prompt answers "correct → top suggestion"; the missing
    note answers "none of these" (create). Prompted targets are
    appended to ``log`` in prompt order.
    """

    async def fake_prompt(unresolved: UnresolvedWikilink) -> Decision:
        log.append(unresolved.link.target)
        if unresolved.link.target == "Jane Smtih":
            return ("correct", unresolved.suggestions[0])
        return ("create", None)

    app._prompt_unresolved = fake_prompt  # type: ignore[method-assign]


def test_integrity_pass_corrects_and_creates(tmp_path: Path) -> None:
    """
    The worked example end-to-end through the app: the typo is fixed in
    the file, the missing note is created at the vault root, and the
    prose for the created link is untouched (Option A).
    """
    vault = make_vault(tmp_path)
    note = vault / "Meeting Notes.md"

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        prompted: list[str] = []
        stub_prompts(app, prompted)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_integrity_pass(note)

            assert prompted == ["Jane Smtih", "Q3 Planning"]
            assert note.read_text(encoding="utf-8") == FIXED
            created = vault / "Q3 Planning.md"
            assert created.is_file()
            assert created.read_text(encoding="utf-8") == ""
            assert created in app._index.notes_by_path

    asyncio.run(run())


def test_integrity_pass_skip_leaves_file_untouched(tmp_path: Path) -> None:
    """
    Skip decisions leave the file byte-identical and create nothing.
    """
    vault = make_vault(tmp_path)
    note = vault / "Meeting Notes.md"

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)

        async def skip_all(unresolved: UnresolvedWikilink) -> Decision:
            return ("skip", None)

        app._prompt_unresolved = skip_all  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_integrity_pass(note)
            assert note.read_text(encoding="utf-8") == BODY
            assert not (vault / "Q3 Planning.md").exists()

    asyncio.run(run())


def test_integrity_pass_respects_enforce_toggle(tmp_path: Path) -> None:
    """
    With ``enforce_no_dangling_wikilinks`` off, no prompt ever fires.
    """
    vault = make_vault(tmp_path)
    note = vault / "Meeting Notes.md"

    async def run() -> None:
        config = default_config()
        config["link_integrity"]["enforce_no_dangling_wikilinks"] = False
        app = HoppusApp(config=config, vaults_root=vault)

        async def explode(unresolved: UnresolvedWikilink) -> Decision:
            raise AssertionError("prompted despite the toggle being off")

        app._prompt_unresolved = explode  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_integrity_pass(note)
            assert note.read_text(encoding="utf-8") == BODY

    asyncio.run(run())
