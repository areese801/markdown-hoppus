"""
Tests for the pure MCP read-tool functions (spec §11, HOPPUS-58).

Runs :mod:`hoppus.mcp.tools` against the shared ``sample_vault`` fixture
(via a Vaults Root that symlinks it in) plus a small purpose-built vault
containing a dangling wikilink and an unlinked mention. Everything is
headless: no MCP server or transport is ever started.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from hoppus.config import default_config
from hoppus.mcp import tools
from hoppus.mcp.tools import NoteNotFound, VaultNotFound

SAMPLE_VAULT_NAME = "Sample"
BROKEN_VAULT_NAME = "Broken"

SAMPLE_NOTE_PATHS = {
    "Archive/Alpha.md",
    "Index.md",
    "Projects/Alpha.md",
    "Tags Note.md",
    "Target Note.md",
}

_ZETA_MD = """\
# Zeta

A note with a dangling link: [[Missing Note]].
"""

_MENTIONER_MD = """\
# Mentioner

This note talks about Zeta twice: Zeta appears without any link.
"""


@pytest.fixture()
def config(sample_vault: Path, tmp_path: Path) -> dict[str, Any]:
    """
    Build a config whose Vaults Root contains the sample vault (as a
    symlink) plus a small vault with known integrity problems.

    :param sample_vault: The shared session-scoped sample vault.
    :param tmp_path: pytest's per-test temp directory.
    :returns: The merged-config dict pointing at the temp Vaults Root.
    """
    root = tmp_path / "Vaults"
    root.mkdir()
    (root / SAMPLE_VAULT_NAME).symlink_to(sample_vault)

    broken = root / BROKEN_VAULT_NAME
    broken.mkdir()
    (broken / "Zeta.md").write_text(_ZETA_MD, encoding="utf-8")
    (broken / "Mentioner.md").write_text(_MENTIONER_MD, encoding="utf-8")

    cfg = default_config()
    cfg["vaults_root"] = str(root)
    cfg["default_vault"] = SAMPLE_VAULT_NAME
    return cfg


def test_list_vaults_finds_sample_vault(config: dict[str, Any]) -> None:
    """
    ``list_vaults`` enumerates both vaults with correct note counts.
    """
    result = tools.list_vaults(config)
    by_name = {entry["name"]: entry for entry in result}
    assert set(by_name) == {SAMPLE_VAULT_NAME, BROKEN_VAULT_NAME}
    assert by_name[SAMPLE_VAULT_NAME]["note_count"] == 5
    assert by_name[BROKEN_VAULT_NAME]["note_count"] == 2
    assert json.dumps(result)


def test_list_vaults_missing_root_raises(config: dict[str, Any]) -> None:
    """
    A missing Vaults Root raises ``VaultNotFound``.
    """
    config["vaults_root"] = str(Path(config["vaults_root"]) / "nope")
    with pytest.raises(VaultNotFound):
        tools.list_vaults(config)


def test_list_notes_returns_all_sample_notes(config: dict[str, Any]) -> None:
    """
    ``list_notes`` lists every sample-vault note with title and word count.
    """
    result = tools.list_notes(config)
    assert {entry["path"] for entry in result} == SAMPLE_NOTE_PATHS
    by_path = {entry["path"]: entry for entry in result}
    assert by_path["Target Note.md"]["title"] == "Target Note"
    assert by_path["Target Note.md"]["word_count"] > 0
    assert json.dumps(result)


def test_list_notes_folder_filter(config: dict[str, Any]) -> None:
    """
    The folder filter narrows to notes under that folder prefix.
    """
    result = tools.list_notes(config, folder="Projects")
    assert [entry["path"] for entry in result] == ["Projects/Alpha.md"]


def test_list_notes_tag_filter_nested_prefix(config: dict[str, Any]) -> None:
    """
    The tag filter is case-insensitive and matches nested tags by prefix.
    """
    exact = tools.list_notes(config, tag="AREA/SUB")
    prefix = tools.list_notes(config, tag="area")
    expected = {"Tags Note.md", "Target Note.md"}
    assert {entry["path"] for entry in exact} == expected
    assert {entry["path"] for entry in prefix} == expected


def test_list_notes_combined_filters(config: dict[str, Any]) -> None:
    """
    Folder and tag filters combine with AND semantics.
    """
    result = tools.list_notes(config, folder="Projects", tag="area")
    assert result == []


def test_get_note_returns_raw_and_metadata(config: dict[str, Any]) -> None:
    """
    ``get_note`` returns raw text plus the parsed metadata for a note.
    """
    result = tools.get_note(config, "Target Note")
    assert result["path"] == "Target Note.md"
    assert result["title"] == "Target Note"
    assert "Introductory text." in result["raw"]
    assert result["aliases"] == ["The Target", "target-note"]
    assert result["tags"] == ["area/sub", "reference"]
    assert result["headings"] == ["Target Note", "Section One", "Section Two"]
    assert result["block_ids"] == ["quote-1"]
    assert result["frontmatter"]["tags"] == ["reference", "area/sub"]
    assert result["word_count"] > 0
    assert json.dumps(result)


def test_get_note_accepts_path_and_alias_references(config: dict[str, Any]) -> None:
    """
    A note resolves by relative path (with or without ``.md``) or alias.
    """
    assert tools.get_note(config, "Projects/Alpha.md")["title"] == "Alpha"
    assert tools.get_note(config, "Projects/Alpha")["path"] == "Projects/Alpha.md"
    assert tools.get_note(config, "The Target")["path"] == "Target Note.md"


def test_get_note_ambiguous_title_raises(config: dict[str, Any]) -> None:
    """
    A duplicate title without a folder path raises ``NoteNotFound``.
    """
    with pytest.raises(NoteNotFound, match="ambiguous"):
        tools.get_note(config, "Alpha")


def test_get_note_unknown_note_raises(config: dict[str, Any]) -> None:
    """
    An unknown note reference raises ``NoteNotFound``.
    """
    with pytest.raises(NoteNotFound):
        tools.get_note(config, "No Such Note")


def test_unknown_vault_raises(config: dict[str, Any]) -> None:
    """
    Every vault-scoped tool raises ``VaultNotFound`` for unknown vaults.
    """
    with pytest.raises(VaultNotFound, match="Nope"):
        tools.list_notes(config, vault="Nope")


def test_search_notes_bare_term(config: dict[str, Any]) -> None:
    """
    A bare full-text term finds the matching note with a snippet.
    """
    result = tools.search_notes(config, "Introductory")
    paths = [entry["path"] for entry in result]
    assert "Target Note.md" in paths
    hit = result[paths.index("Target Note.md")]
    assert "Introductory" in hit["snippet"]
    assert hit["line_number"] is not None
    assert json.dumps(result)


def test_search_notes_operators(config: dict[str, Any]) -> None:
    """
    ``tag:`` and ``title:`` operators filter as in §9.8.
    """
    tag_hits = tools.search_notes(config, "tag:index")
    assert [entry["path"] for entry in tag_hits] == ["Index.md"]
    title_hits = tools.search_notes(config, "title:Tags")
    assert [entry["path"] for entry in title_hits] == ["Tags Note.md"]


def test_list_tags_counts(config: dict[str, Any]) -> None:
    """
    ``list_tags`` returns every tag with its note count, sorted by tag.
    """
    result = tools.list_tags(config)
    counts = {entry["tag"]: entry["count"] for entry in result}
    assert counts == {
        "area/sub": 2,
        "closing-tag": 1,
        "frontmatter-tag": 1,
        "index": 1,
        "inline-tag": 1,
        "reference": 1,
    }
    assert [entry["tag"] for entry in result] == sorted(counts)
    assert json.dumps(result)


def test_get_backlinks_linked_sources(config: dict[str, Any]) -> None:
    """
    ``get_backlinks`` lists the notes linking to the target, plus the
    unlinked-mentions structure.
    """
    result = tools.get_backlinks(config, "Target Note")
    assert result["note"]["path"] == "Target Note.md"
    assert [entry["path"] for entry in result["linked"]] == ["Index.md"]
    assert isinstance(result["unlinked"], list)
    assert json.dumps(result)


def test_get_backlinks_unlinked_mentions(config: dict[str, Any]) -> None:
    """
    Plain-text mentions without links surface as unlinked mentions.
    """
    result = tools.get_backlinks(config, "Zeta", vault=BROKEN_VAULT_NAME)
    assert result["linked"] == []
    assert [entry["path"] for entry in result["unlinked"]] == ["Mentioner.md"]
    assert result["unlinked"][0]["count"] == 2


def test_get_links_resolved(config: dict[str, Any]) -> None:
    """
    ``get_links`` reports the outbound resolved links of a note with
    anchor/display/embed detail.
    """
    result = tools.get_links(config, "Index")
    assert result["note"]["path"] == "Index.md"
    assert result["unresolved"] == []
    resolved = result["resolved"]
    targets = {entry["path"] for entry in resolved}
    assert {
        "Target Note.md",
        "Projects/Alpha.md",
        "Archive/Alpha.md",
        "attachments/image.png",
        "Index.md",
    } == targets
    anchored = [
        entry
        for entry in resolved
        if entry["path"] == "Target Note.md" and entry["anchor"] == "Section One"
    ]
    assert anchored and anchored[0]["is_wikilink"]
    embeds = [entry for entry in resolved if entry["is_embed"]]
    assert any(entry["path"] == "attachments/image.png" for entry in embeds)
    displayed = [entry for entry in resolved if entry["display"] == "the target"]
    assert displayed
    assert json.dumps(result)


def test_get_links_unresolved(config: dict[str, Any]) -> None:
    """
    A dangling wikilink lands in the ``unresolved`` list with its raw
    target.
    """
    result = tools.get_links(config, "Zeta", vault=BROKEN_VAULT_NAME)
    assert [entry["target"] for entry in result["unresolved"]] == ["Missing Note"]
    assert result["resolved"] == []


def test_neighbors_local_subgraph(config: dict[str, Any]) -> None:
    """
    ``neighbors`` returns the expected 1-hop subgraph around ``Index``.
    """
    result = tools.neighbors(config, "Index", degrees=1)
    assert result["root"]["path"] == "Index.md"
    by_path = {node["path"]: node for node in result["nodes"]}
    assert by_path["Index.md"]["degree"] == 0
    assert {
        "Index.md",
        "Target Note.md",
        "Projects/Alpha.md",
        "Archive/Alpha.md",
    } <= set(by_path)
    assert by_path["Target Note.md"]["degree"] == 1
    assert result["truncated_count"] == 0
    edge_pairs = {(edge["source"], edge["target"]) for edge in result["edges"]}
    assert ("Index.md", "Target Note.md") in edge_pairs
    assert json.dumps(result)


def test_neighbors_unknown_note_raises(config: dict[str, Any]) -> None:
    """
    An unknown root note raises ``NoteNotFound``.
    """
    with pytest.raises(NoteNotFound):
        tools.neighbors(config, "Ghost Note")


def test_audit_vault_clean_sample(config: dict[str, Any]) -> None:
    """
    The sample vault audits clean: no issues of any kind.
    """
    result = tools.audit_vault(config)
    assert result["count"] == 0
    assert result["issues"] == []
    assert result["by_kind"] == {}
    assert json.dumps(result)


def test_audit_vault_reports_unresolved(config: dict[str, Any]) -> None:
    """
    A dangling wikilink shows up as an unresolved-wikilink issue.
    """
    result = tools.audit_vault(config, vault=BROKEN_VAULT_NAME)
    assert result["count"] == 1
    issue = result["issues"][0]
    assert issue["path"] == "Zeta.md"
    assert issue["target"] == "Missing Note"
    assert result["by_kind"] == {issue["kind"]: 1}
    assert json.dumps(result)


def test_resolve_vault_lone_vault_fallback(tmp_path: Path) -> None:
    """
    With no usable ``default_vault`` and exactly one vault, MCP tools
    fall back to the lone vault (HOPPUS-88).
    """
    root = tmp_path / "Vaults"
    (root / "Solo").mkdir(parents=True)
    cfg = default_config()
    cfg["vaults_root"] = str(root)
    cfg["default_vault"] = None
    assert tools._resolve_vault(cfg) == root / "Solo"


def test_resolve_vault_unset_default_with_several_raises(tmp_path: Path) -> None:
    """
    Several vaults and no default → ``VaultNotFound`` naming the
    choices, not a crash on a None name (HOPPUS-88).
    """
    root = tmp_path / "Vaults"
    (root / "Personal").mkdir(parents=True)
    (root / "Work").mkdir()
    cfg = default_config()
    cfg["vaults_root"] = str(root)
    cfg["default_vault"] = None
    with pytest.raises(VaultNotFound) as excinfo:
        tools._resolve_vault(cfg)
    assert "choose one of" in str(excinfo.value)
    assert "Personal" in str(excinfo.value)
