"""
Anchor / attachment / markdown-link audit checks (spec §7.4, §7.6,
HOPPUS-42).

Pure tests over temp vaults: ``audit_note``/``audit_vault`` classify
broken anchors, missing attachments, and broken markdown links alongside
the §7.3 unresolved-wikilink rule — report-only, each check gated by its
independent ``link_integrity`` toggle (default on), code-fence safe, and
emitted in document order. ``AuditReport.by_kind`` groups stably.
"""

from pathlib import Path
from typing import Any

from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.integrity import (
    KIND_BROKEN_ANCHOR,
    KIND_BROKEN_MARKDOWN_LINK,
    KIND_MISSING_ATTACHMENT,
    KIND_UNRESOLVED_WIKILINK,
    audit_note,
    audit_vault,
)


def config_with(**overrides: Any) -> dict[str, Any]:
    """
    Return a default config with ``link_integrity`` toggles overridden.
    """
    config = default_config()
    config["link_integrity"].update(overrides)
    return config


def make_vault(tmp_path: Path, text: str) -> Path:
    """
    Build a temp vault: ``Existing.md`` (with a real heading and block
    id), ``Real.md``, a present ``here.png`` attachment, and ``Note.md``
    carrying the text under test.
    """
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "Existing.md").write_text(
        "# Real Heading\n\nA paragraph. ^known-block\n", encoding="utf-8"
    )
    (vault / "Real.md").write_text("No links.\n", encoding="utf-8")
    (vault / "here.png").write_bytes(b"\x00")
    (vault / "Note.md").write_text(text, encoding="utf-8")
    return vault


def audit_targets(
    tmp_path: Path, text: str, config: dict[str, Any] | None = None
) -> list[tuple[str, str]]:
    """
    Audit ``Note.md`` carrying ``text`` and return ``(kind, target)``
    pairs in emission order.
    """
    vault = make_vault(tmp_path, text)
    index = Index.build(vault)
    issues = audit_note(text, index, vault / "Note.md", config=config)
    return [(issue.kind, issue.target) for issue in issues]


def test_broken_anchor_reported(tmp_path: Path) -> None:
    """
    A resolved wikilink with a nonexistent heading anchor reports
    ``broken_anchor``.
    """
    issues = audit_targets(tmp_path, "See [[Existing#Nonexistent Heading]].\n")
    assert issues == [(KIND_BROKEN_ANCHOR, "Existing")]


def test_valid_anchors_do_not_report(tmp_path: Path) -> None:
    """
    Resolving heading and block anchors are silent.
    """
    text = "See [[Existing#Real Heading]] and [[Existing#^known-block]].\n"
    assert audit_targets(tmp_path, text) == []


def test_same_note_anchor_checked_against_current_note(tmp_path: Path) -> None:
    """
    ``[[#Heading]]`` resolves against the containing note itself.
    """
    text = "# Local\n\nGood [[#Local]] and bad [[#Missing]].\n"
    assert audit_targets(tmp_path, text) == [(KIND_BROKEN_ANCHOR, "")]


def test_broken_anchor_toggle_off(tmp_path: Path) -> None:
    """
    ``warn_missing_anchor: False`` suppresses anchor warnings only.
    """
    text = "See [[Existing#Nonexistent Heading]].\n"
    config = config_with(warn_missing_anchor=False)
    assert audit_targets(tmp_path, text, config) == []


def test_missing_attachments_reported(tmp_path: Path) -> None:
    """
    Unresolved attachment embeds and markdown attachment links report
    ``missing_attachment``.
    """
    text = "An embed ![[missing.png]] and a [pdf](missing.pdf).\n"
    assert audit_targets(tmp_path, text) == [
        (KIND_MISSING_ATTACHMENT, "missing.png"),
        (KIND_MISSING_ATTACHMENT, "missing.pdf"),
    ]


def test_present_attachment_does_not_report(tmp_path: Path) -> None:
    """
    Attachments that resolve are silent for both link forms.
    """
    text = "An embed ![[here.png]] and a [png](here.png).\n"
    assert audit_targets(tmp_path, text) == []


def test_missing_attachment_toggle_off(tmp_path: Path) -> None:
    """
    ``report_missing_attachments: False`` suppresses attachment issues
    only.
    """
    text = "An embed ![[missing.png]] and a [pdf](missing.pdf).\n"
    config = config_with(report_missing_attachments=False)
    assert audit_targets(tmp_path, text, config) == []


def test_broken_markdown_link_reported(tmp_path: Path) -> None:
    """
    An unresolved ``[text](path)`` note link reports
    ``broken_markdown_link``.
    """
    assert audit_targets(tmp_path, "A [text](nope.md) link.\n") == [
        (KIND_BROKEN_MARKDOWN_LINK, "nope.md")
    ]


def test_resolving_markdown_link_does_not_report(tmp_path: Path) -> None:
    """
    A markdown link at a known vault note is silent; so are external
    URLs.
    """
    text = "A [text](Real.md) link and an [external](https://example.com).\n"
    assert audit_targets(tmp_path, text) == []


def test_broken_markdown_link_toggle_off(tmp_path: Path) -> None:
    """
    ``report_broken_markdown_links: False`` suppresses markdown-link
    issues only.
    """
    config = config_with(report_broken_markdown_links=False)
    assert audit_targets(tmp_path, "A [text](nope.md) link.\n", config) == []


def test_unresolved_wikilink_kind_and_toggle(tmp_path: Path) -> None:
    """
    Unresolved note wikilinks keep the ``unresolved_wikilink`` kind and
    are gated by ``enforce_no_dangling_wikilinks``.
    """
    text = "See [[Ghost]].\n"
    assert audit_targets(tmp_path / "on", text) == [(KIND_UNRESOLVED_WIKILINK, "Ghost")]
    config = config_with(enforce_no_dangling_wikilinks=False)
    assert audit_targets(tmp_path / "off", text, config) == []


def test_toggles_are_independent(tmp_path: Path) -> None:
    """
    Turning one check off never suppresses the other three.
    """
    text = (
        "See [[Ghost]] and [[Existing#Nope]].\n"
        "An embed ![[missing.png]] and a [text](nope.md) link.\n"
    )
    all_kinds = {
        KIND_UNRESOLVED_WIKILINK,
        KIND_BROKEN_ANCHOR,
        KIND_MISSING_ATTACHMENT,
        KIND_BROKEN_MARKDOWN_LINK,
    }
    for toggle, suppressed in [
        ("enforce_no_dangling_wikilinks", KIND_UNRESOLVED_WIKILINK),
        ("warn_missing_anchor", KIND_BROKEN_ANCHOR),
        ("report_missing_attachments", KIND_MISSING_ATTACHMENT),
        ("report_broken_markdown_links", KIND_BROKEN_MARKDOWN_LINK),
    ]:
        config = config_with(**{toggle: False})
        kinds = {kind for kind, _ in audit_targets(tmp_path / toggle, text, config)}
        assert kinds == all_kinds - {suppressed}


def test_code_fences_never_flag_any_kind(tmp_path: Path) -> None:
    """
    Links of every form inside code fences and inline code are silent.
    """
    text = (
        "```\n"
        "[[Ghost]] and [[Existing#Nope]] and ![[missing.png]]\n"
        "[text](nope.md)\n"
        "```\n"
        "Inline `[[Ghost]] [x](nope.md)` too.\n"
    )
    assert audit_targets(tmp_path, text) == []


def test_document_order_across_kinds(tmp_path: Path) -> None:
    """
    Issues of mixed kinds are emitted in document order within a note.
    """
    text = (
        "A [text](nope.md) link, then [[Existing#Nope]], "
        "then ![[missing.png]], then [[Ghost]].\n"
    )
    assert audit_targets(tmp_path, text) == [
        (KIND_BROKEN_MARKDOWN_LINK, "nope.md"),
        (KIND_BROKEN_ANCHOR, "Existing"),
        (KIND_MISSING_ATTACHMENT, "missing.png"),
        (KIND_UNRESOLVED_WIKILINK, "Ghost"),
    ]


def test_audit_vault_none_config_reports_full_superset(tmp_path: Path) -> None:
    """
    ``audit_vault(index)`` (config=None) runs all four checks, and
    ``by_kind`` groups the issues stably.
    """
    text = (
        "See [[Ghost]] and [[Existing#Nope]].\n"
        "An embed ![[missing.png]] and a [text](nope.md) link.\n"
    )
    vault = make_vault(tmp_path, text)
    report = audit_vault(Index.build(vault))

    assert report.count == 4
    assert report.note_count() == 1
    grouped = report.by_kind()
    assert list(grouped) == [
        KIND_UNRESOLVED_WIKILINK,
        KIND_BROKEN_ANCHOR,
        KIND_MISSING_ATTACHMENT,
        KIND_BROKEN_MARKDOWN_LINK,
    ]
    assert [issue.target for issues in grouped.values() for issue in issues] == [
        "Ghost",
        "Existing",
        "missing.png",
        "nope.md",
    ]


def test_issue_fields_carry_link_form(tmp_path: Path) -> None:
    """
    Issues record ``anchor``/``display`` and the wikilink/embed form.
    """
    text = "See [[Existing#Nope|shown]] and ![[missing.png]] and [t](nope.md).\n"
    vault = make_vault(tmp_path, text)
    index = Index.build(vault)
    anchor, attachment, md_link = audit_note(text, index, vault / "Note.md")

    assert (anchor.anchor, anchor.display) == ("Nope", "shown")
    assert (anchor.is_wikilink, anchor.is_embed) == (True, False)
    assert (attachment.is_wikilink, attachment.is_embed) == (True, True)
    assert (md_link.is_wikilink, md_link.display) == (False, "t")
