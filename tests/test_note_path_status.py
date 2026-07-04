"""
Tests for the status-line note path (HOPPUS-95).

Opening a note shows its vault-relative path in the status line
(nvim-style); clearing the preview or switching vaults removes it; deep
paths are elided from the left so the layout never breaks in narrow
terminals. All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run``, matching the rest of the suite.
"""

import asyncio
from pathlib import Path

from hoppus.config import default_config
from hoppus.tui.app import MAX_STATUS_PATH_CHARS, HoppusApp, StatusLine, elide_path


def make_app(vaults_root: Path) -> HoppusApp:
    """
    Build a HoppusApp rooted above the vault under test.

    :param vaults_root: Directory containing the vault under test.
    :returns: An unmounted HoppusApp instance.
    """
    return HoppusApp(config=default_config(), vaults_root=vaults_root)


def test_elide_path_keeps_short_paths_and_trims_long_ones() -> None:
    """
    ``elide_path`` returns short paths verbatim and elides long ones
    from the left, keeping the identifying tail.
    """
    assert elide_path("Projects/Alpha.md") == "Projects/Alpha.md"
    deep = "/".join(f"folder-{n:02d}" for n in range(8)) + "/Deep Note.md"
    elided = elide_path(deep)
    assert len(elided) == MAX_STATUS_PATH_CHARS
    assert elided.startswith("…")
    assert elided.endswith("Deep Note.md")


def test_open_note_shows_vault_relative_path(sample_vault: Path) -> None:
    """
    Opening a note puts its vault-relative path in the status line.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(
                sample_vault / "Projects" / "Alpha.md", vault_root=sample_vault
            )
            await pilot.pause()
            assert app.note_relpath == "Projects/Alpha.md"
            assert "Projects/Alpha.md" in str(app.query_one(StatusLine).render())

    asyncio.run(run())


def test_clearing_the_preview_clears_the_path(sample_vault: Path) -> None:
    """
    Closing the note (preview clear) removes the path segment.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(
                sample_vault / "Projects" / "Alpha.md", vault_root=sample_vault
            )
            await pilot.pause()
            assert "Projects/Alpha.md" in str(app.query_one(StatusLine).render())
            await app.preview.clear()
            await pilot.pause()
            status = str(app.query_one(StatusLine).render())
            assert app.note_relpath is None
            assert "Projects/Alpha.md" not in status

    asyncio.run(run())


def test_vault_switch_clears_the_path(sample_vault: Path, tmp_path: Path) -> None:
    """
    Switching vaults clears the note path along with the other
    note-scoped status segments.
    """

    async def run() -> None:
        vaults_root = tmp_path / "vaults"
        other = vaults_root / "Other"
        other.mkdir(parents=True)
        (other / "Lone Note.md").write_text("# Lone Note\n", encoding="utf-8")
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(
                sample_vault / "Target Note.md", vault_root=sample_vault
            )
            await pilot.pause()
            assert "Target Note.md" in str(app.query_one(StatusLine).render())
            app.vaults_root = vaults_root
            await app.switch_vault("Other")
            await pilot.pause()
            assert app.note_relpath is None
            assert "Target Note.md" not in str(app.query_one(StatusLine).render())

    asyncio.run(run())


def test_long_path_is_elided_in_the_status_line(tmp_path: Path) -> None:
    """
    A deeply nested note's path segment appears elided — the tail
    (filename) survives behind a leading ellipsis, so the segment never
    crowds out the rest of the status line.
    """

    async def run() -> None:
        vault = tmp_path / "vault"
        deep = vault / "a-very-long-folder-name" / "another-long-folder" / "more"
        deep.mkdir(parents=True)
        note = deep / "A Note With A Long Name.md"
        note.write_text("# A Note With A Long Name\n", encoding="utf-8")
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await app.open_note(note, vault_root=vault)
            await pilot.pause()
            assert app.note_relpath is not None
            assert len(app.note_relpath) > MAX_STATUS_PATH_CHARS
            status = str(app.query_one(StatusLine).render())
            assert "…" in status
            assert "A Note With A Long Name.md" in status
            assert app.note_relpath not in status

    asyncio.run(run())
