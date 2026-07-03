"""
Invalid-frontmatter isolation (HOPPUS-67).

One note whose YAML frontmatter fails to parse (e.g. an Obsidian
template with ``{{date}}`` placeholder tags) must not poison the index:
``split_frontmatter`` treats it as frontmatter-less, ``Index.build``
completes and indexes the note, and the audit path reports a
report-only ``invalid_frontmatter`` problem for it.
"""

from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.integrity import KIND_INVALID_FRONTMATTER, audit_vault
from hoppus.parse.frontmatter import frontmatter_parse_error, split_frontmatter

_TEMPLATE_NOTE = """\
---
tags:
  - {{date}}
  - {{date:YYYY}}-MM-DD
---

# Daily Template

Template body text with a [[Real]] link.
"""

_LIST_FRONTMATTER_NOTE = """\
---
- alpha
- beta
---

Body after a non-mapping frontmatter block.
"""


def make_vault(tmp_path: Path) -> Path:
    """
    Build a temp vault with one healthy note and one template note
    carrying ``{{date}}``-style invalid frontmatter.
    """
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "Real.md").write_text("No links.\n", encoding="utf-8")
    (vault / "Template.md").write_text(_TEMPLATE_NOTE, encoding="utf-8")
    return vault


def test_split_frontmatter_tolerates_invalid_yaml() -> None:
    frontmatter, body = split_frontmatter(_TEMPLATE_NOTE)
    assert dict(frontmatter) == {}
    assert "# Daily Template" in body
    assert "{{date}}" not in body


def test_split_frontmatter_tolerates_non_mapping_yaml() -> None:
    frontmatter, body = split_frontmatter(_LIST_FRONTMATTER_NOTE)
    assert dict(frontmatter) == {}
    assert "Body after a non-mapping frontmatter block." in body


def test_frontmatter_parse_error_reports_invalid_yaml() -> None:
    error = frontmatter_parse_error(_TEMPLATE_NOTE)
    assert error is not None
    assert "\n" not in error


def test_frontmatter_parse_error_none_for_valid_and_absent() -> None:
    assert frontmatter_parse_error("---\ntags:\n  - ok\n---\nBody.\n") is None
    assert frontmatter_parse_error("No frontmatter here.\n") is None
    assert frontmatter_parse_error("---\n---\nEmpty block.\n") is None


def test_index_build_survives_invalid_frontmatter(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    template = index.notes_by_path[vault / "Template.md"]
    assert template.title == "Template"
    assert dict(template.frontmatter) == {}
    assert template.aliases == []
    assert template.word_count > 0
    assert index.note_tags[vault / "Template.md"] == set()
    # The healthy note is untouched and the template's body links resolve.
    assert (vault / "Real.md") in index.notes_by_path
    assert index.backlinks[vault / "Real.md"] == {vault / "Template.md"}


def test_audit_reports_invalid_frontmatter(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    report = audit_vault(Index.build(vault))
    issues = report.by_kind().get(KIND_INVALID_FRONTMATTER, [])
    assert len(issues) == 1
    issue = issues[0]
    assert issue.note_path == vault / "Template.md"
    assert issue.target
    assert not issue.is_wikilink
