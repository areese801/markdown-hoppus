"""
Unreadable / non-UTF-8 file isolation (HOPPUS-73).

One ``.md`` file that cannot be read (permission denied) or decoded as
UTF-8 (binary bytes, wrong encoding) must never abort the index build,
search, or the audit: ``Index.build`` keeps the file present as a
content-less stub note, every other note indexes normally, and the
audit surfaces the bad file as a report-only ``unreadable_file``
problem — the same seam as ``invalid_frontmatter`` (HOPPUS-67).
"""

import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hoppus import cli
from hoppus.index.indexer import Index
from hoppus.integrity import KIND_UNREADABLE_FILE, audit_vault
from hoppus.search.content import search_content

#: UTF-16-ish bytes with stray continuation bytes — not decodable as UTF-8.
_NON_UTF8_BYTES = b"\xff\xfe\x00bad bytes\x80\x81"

#: A PNG-like binary blob masquerading as a note.
_BINARY_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x01\x02\x03"

runner = CliRunner()


def make_vault(tmp_path: Path) -> Path:
    """
    Build a temp vault with one healthy note (linking to a bad one),
    one non-UTF-8 note, and one binary note.
    """
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "Good.md").write_text(
        "A healthy note linking to [[Latin]].\n", encoding="utf-8"
    )
    (vault / "Latin.md").write_bytes(_NON_UTF8_BYTES)
    (vault / "Binary.md").write_bytes(_BINARY_BYTES)
    return vault


def test_index_build_survives_non_utf8_and_binary_files(tmp_path: Path) -> None:
    """
    ``Index.build`` completes; bad files stay present as stub notes.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)

    assert (vault / "Good.md") in index.notes_by_path
    for name in ("Latin", "Binary"):
        stub = index.notes_by_path[vault / f"{name}.md"]
        assert stub.title == name
        assert stub.word_count == 0
        assert stub.aliases == []
        assert index.links[stub.path] == []
        assert index.note_tags[stub.path] == set()

    # The healthy note indexes normally and its link to the bad file
    # resolves (the file is present, so the wikilink is not dangling).
    good = index.notes_by_path[vault / "Good.md"]
    assert good.word_count > 0
    assert index.backlinks[vault / "Latin.md"] == {vault / "Good.md"}


def test_reindex_file_survives_unreadable_file(tmp_path: Path) -> None:
    """
    Incremental reindex of a bad file swaps in a stub, never raises.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Note.md"
    note.write_text("Fine at first.\n", encoding="utf-8")
    index = Index.build(vault)
    assert index.notes_by_path[note].word_count > 0

    note.write_bytes(_NON_UTF8_BYTES)
    index.reindex_file(note)
    stub = index.notes_by_path[note]
    assert stub.title == "Note"
    assert stub.word_count == 0


def test_audit_reports_unreadable_files(tmp_path: Path) -> None:
    """
    ``audit_vault`` reports one ``unreadable_file`` issue per bad file.
    """
    vault = make_vault(tmp_path)
    report = audit_vault(Index.build(vault))
    issues = report.by_kind().get(KIND_UNREADABLE_FILE, [])
    assert {issue.note_path for issue in issues} == {
        vault / "Latin.md",
        vault / "Binary.md",
    }
    for issue in issues:
        assert issue.target
        assert "\n" not in issue.target
        assert not issue.is_wikilink
    # No other issues: the healthy note's link resolves.
    assert report.count == len(issues)


def test_search_content_skips_unreadable_files(tmp_path: Path) -> None:
    """
    Both content-search backends survive bad files and still match the
    healthy note (spec §3 parity).
    """
    vault = make_vault(tmp_path)
    for backend in ("python", "auto"):
        matches = search_content("healthy", vault, backend=backend)
        assert [match.path for match in matches] == [vault / "Good.md"]


def test_permission_denied_file_is_isolated(tmp_path: Path) -> None:
    """
    A chmod-000 note becomes a stub plus an ``unreadable_file`` issue.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Open.md").write_text("Readable.\n", encoding="utf-8")
    locked = vault / "Locked.md"
    locked.write_text("Secret.\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("chmod 000 does not block reads here (e.g. running as root)")
        index = Index.build(vault)
        assert index.notes_by_path[locked].word_count == 0
        assert index.notes_by_path[vault / "Open.md"].word_count > 0
        report = audit_vault(index)
        issues = report.by_kind().get(KIND_UNREADABLE_FILE, [])
        assert [issue.note_path for issue in issues] == [locked]
    finally:
        locked.chmod(0o644)


def test_cli_audit_reports_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``hop audit`` prints the bad file with the ``unreadable file`` label
    and exits 1, instead of dying with a traceback.
    """
    root = tmp_path / "Notes"
    root.mkdir()
    personal = root / "Personal"
    personal.mkdir()
    (personal / "Good.md").write_text("All fine.\n", encoding="utf-8")
    (personal / "Latin.md").write_bytes(_NON_UTF8_BYTES)

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at the temp Vaults Root.
        """
        return {"vaults_root": str(root), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    result = runner.invoke(cli.app, ["audit", "Personal"])
    assert result.exit_code == 1
    assert "Latin.md" in result.output
    assert "(unreadable file)" in result.output
