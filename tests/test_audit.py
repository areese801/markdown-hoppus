"""
Report-only vault audit tests (spec §10, §19 D1, HOPPUS-40).

Pure tests over temp vaults: ``audit_vault`` collects exactly the
unresolved wikilinks (grouped by note, stable sorted-path order),
inherits ``find_unresolved_wikilinks``'s exclusions (attachments,
same-note anchors, code fences), skips unreadable notes, and never
prompts or mutates. ``should_prompt_on`` is the D1 gate matrix.
"""

from pathlib import Path
from unittest.mock import patch

import hoppus.integrity
from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.integrity import AuditIssue, AuditReport, audit_vault, should_prompt_on
from hoppus.parse.links import Resolver


def make_vault(tmp_path: Path) -> Path:
    """
    Build a temp vault mixing clean notes with unresolved wikilinks.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Jane Smith.md").write_text("A person.\n", encoding="utf-8")
    (vault / "Clean.md").write_text("Links to [[Jane Smith]] only.\n", encoding="utf-8")
    (vault / "Meeting Notes.md").write_text(
        "With [[Jane Smtih]] we kicked off [[Q3 Planning]].\n", encoding="utf-8"
    )
    (vault / "Zed.md").write_text("See [[Nowhere]].\n", encoding="utf-8")
    return vault


def test_audit_vault_reports_only_unresolved_in_stable_order(tmp_path: Path) -> None:
    """
    Exactly the unresolved links are reported, sorted by note path.
    """
    vault = make_vault(tmp_path)
    report = audit_vault(Index.build(vault))

    assert report.count == 3
    assert report.note_count() == 2
    assert [(issue.note_path.name, issue.target) for issue in report.issues] == [
        ("Meeting Notes.md", "Jane Smtih"),
        ("Meeting Notes.md", "Q3 Planning"),
        ("Zed.md", "Nowhere"),
    ]
    assert all(issue.kind == "unresolved_wikilink" for issue in report.issues)

    grouped = report.by_note()
    assert [path.name for path in grouped] == ["Meeting Notes.md", "Zed.md"]
    assert [issue.target for issue in grouped[vault / "Meeting Notes.md"]] == [
        "Jane Smtih",
        "Q3 Planning",
    ]


def test_audit_vault_excludes_attachments_anchors_and_code_fences(
    tmp_path: Path,
) -> None:
    """
    Attachment refs, same-note anchors, and fenced links never report.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "missing.png").write_bytes(b"\x00")
    (vault / "Note.md").write_text(
        "An attachment ![[missing.png]] and a same-note anchor [[#Heading]].\n"
        "\n"
        "```\n"
        "A fenced [[Ghost]] link.\n"
        "```\n"
        "\n"
        "# Heading\n",
        encoding="utf-8",
    )
    report = audit_vault(Index.build(vault))
    assert report.count == 0
    assert report.issues == ()


def test_audit_vault_captures_display_and_anchor(tmp_path: Path) -> None:
    """
    Issues carry the link's ``|display`` and ``#anchor`` parts.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text(
        "See [[Ghost#Section|the ghost]].\n", encoding="utf-8"
    )
    report = audit_vault(Index.build(vault))
    assert report.issues == (
        AuditIssue(
            note_path=vault / "Note.md",
            target="Ghost",
            kind="unresolved_wikilink",
            display="the ghost",
            anchor="Section",
        ),
    )


def test_audit_vault_reports_unreadable_notes(tmp_path: Path) -> None:
    """
    A note whose text cannot be read is reported as an
    ``unreadable_file`` issue, never fatal and never silently dropped
    (HOPPUS-73).
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    broken = vault / "Meeting Notes.md"

    def read_text(path: Path) -> str:
        """
        Raise for the broken note, read the rest normally.
        """
        if path == broken:
            raise OSError("unreadable")
        return path.read_text(encoding="utf-8")

    report = audit_vault(index, read_text=read_text)
    assert [(issue.kind, issue.target) for issue in report.issues] == [
        ("unreadable_file", "OSError: unreadable"),
        ("unresolved_wikilink", "Nowhere"),
    ]
    assert report.issues[0].note_path == broken


def test_audit_vault_clean_and_empty_vaults_report_zero(tmp_path: Path) -> None:
    """
    A clean vault and an empty vault both produce a zero-count report.
    """
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "A.md").write_text("Links to [[B]].\n", encoding="utf-8")
    (clean / "B.md").write_text("No links.\n", encoding="utf-8")
    assert audit_vault(Index.build(clean)).count == 0

    empty = tmp_path / "empty"
    empty.mkdir()
    empty_report = audit_vault(Index.build(empty))
    assert empty_report.count == 0
    assert empty_report.note_count() == 0
    assert empty_report.by_note() == {}


def test_audit_vault_builds_resolver_once(tmp_path: Path) -> None:
    """
    The resolver is constructed once per audit, not once per note
    (HOPPUS-70): per-note construction made the audit O(n²).
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)

    with patch.object(
        hoppus.integrity, "Resolver", side_effect=Resolver
    ) as constructor:
        report = audit_vault(index)

    assert constructor.call_count == 1
    assert report.count == 3


def test_audit_report_is_a_plain_frozen_record() -> None:
    """
    AuditReport is a small tuple-backed record with counting helpers.
    """
    issue = AuditIssue(
        note_path=Path("Note.md"), target="Ghost", kind="unresolved_wikilink"
    )
    report = AuditReport(issues=(issue, issue))
    assert report.count == 2
    assert report.note_count() == 1


def test_should_prompt_on_gate_matrix() -> None:
    """
    D1 gate: editor_return always; open/watcher only in "always" mode.
    """
    default = default_config()
    assert default["link_integrity"]["prompt_on"] == "editor_return_only"

    always = default_config()
    always["link_integrity"]["prompt_on"] = "always"

    assert should_prompt_on(default, "editor_return") is True
    assert should_prompt_on(always, "editor_return") is True

    for trigger in ("open", "watcher"):
        assert should_prompt_on(default, trigger) is False
        assert should_prompt_on(always, trigger) is True

    assert should_prompt_on(default, "bogus") is False
    assert should_prompt_on(always, "bogus") is False
    assert should_prompt_on({}, "open") is False
