"""
Tests for the pure MCP write-tool functions (spec §11, §19 D2,
HOPPUS-59).

Runs :mod:`hoppus.mcp.tools` write tools against small purpose-built
temp vaults. Everything is headless: no MCP server or transport is ever
started. The headline coverage is the D2 non-interactive integrity
contract — unresolved wikilinks reject the write by default (returning
the dangling targets plus suggestions, writing nothing), and
``on_unresolved="create"`` bootstraps the missing targets at the vault
root before writing.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from hoppus.config import default_config
from hoppus.mcp import tools

VAULT_NAME = "Main"

_TARGET_MD = """\
# Target Note

Some existing body text.
"""

_LINKER_MD = """\
# Linker

Points at [[Target Note]] explicitly.
"""

_SECTIONED_MD = """\
# Sectioned

Intro paragraph.

## Log

- old entry

## Other

Unrelated tail.
"""


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    """
    Build a small vault with a link target, a linker, and a sectioned
    note, plus a protected ``.hoppus`` state file.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root directory.
    """
    root = tmp_path / "Vaults" / VAULT_NAME
    root.mkdir(parents=True)
    (root / "Target Note.md").write_text(_TARGET_MD, encoding="utf-8")
    (root / "Linker.md").write_text(_LINKER_MD, encoding="utf-8")
    (root / "Sectioned.md").write_text(_SECTIONED_MD, encoding="utf-8")
    state = root / ".hoppus"
    state.mkdir()
    (state / "state.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture()
def config(vault_root: Path) -> dict[str, Any]:
    """
    Build a config whose Vaults Root contains the test vault.

    :param vault_root: The vault built by the ``vault_root`` fixture.
    :returns: The merged-config dict pointing at the temp Vaults Root.
    """
    cfg = default_config()
    cfg["vaults_root"] = str(vault_root.parent)
    cfg["default_vault"] = VAULT_NAME
    return cfg


def test_create_note_clean_content(config: dict[str, Any], vault_root: Path) -> None:
    """
    ``create_note`` with clean content creates the file at the vault
    root with the given content.
    """
    result = tools.create_note(config, "Fresh Note", "Hello world.\n")
    assert result["status"] == "created"
    assert result["path"] == "Fresh Note.md"
    assert result["created_targets"] == []
    created = vault_root / "Fresh Note.md"
    assert created.read_text(encoding="utf-8") == "Hello world.\n"
    assert json.dumps(result)


def test_create_note_rejects_unresolved_by_default(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    D2 headline: a dangling ``[[Ghost]]`` with the default
    ``on_unresolved="reject"`` rejects the write and writes nothing.
    """
    result = tools.create_note(config, "New Note", "See [[Ghost]].\n")
    assert result["status"] == "rejected"
    assert result["reason"] == "unresolved_links"
    assert [entry["target"] for entry in result["unresolved"]] == ["Ghost"]
    assert isinstance(result["unresolved"][0]["suggestions"], list)
    assert not (vault_root / "New Note.md").exists()
    assert not (vault_root / "Ghost.md").exists()
    assert json.dumps(result)


def test_create_note_rejection_carries_near_match_suggestions(
    config: dict[str, Any],
) -> None:
    """
    The rejection payload suggests near-match note titles so the agent
    can retry with a corrected target.
    """
    result = tools.create_note(config, "New Note", "See [[Targt Note]].\n")
    assert result["status"] == "rejected"
    assert "Target Note" in result["unresolved"][0]["suggestions"]


def test_create_note_on_unresolved_create_bootstraps_target(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    D2 create path: ``on_unresolved="create"`` bootstraps ``Ghost.md``
    at the vault root, then writes the note; the link now resolves.
    """
    content = "See [[Ghost]] and [[Ghost]] again.\n"
    result = tools.create_note(config, "New Note", content, on_unresolved="create")
    assert result["status"] == "created"
    assert result["created_targets"] == ["Ghost.md"]
    assert (vault_root / "Ghost.md").exists()
    assert (vault_root / "New Note.md").read_text(encoding="utf-8") == content
    links = tools.get_links(config, "New Note")
    assert links["unresolved"] == []
    assert json.dumps(result)


def test_create_note_resolving_link_passes_regardless(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    A wikilink that resolves proceeds normally under the default
    ``on_unresolved="reject"``.
    """
    result = tools.create_note(config, "New Note", "See [[Target Note]].\n")
    assert result["status"] == "created"
    assert (vault_root / "New Note.md").exists()


def test_create_note_collision_and_invalid_name(config: dict[str, Any]) -> None:
    """
    Collisions and invalid names return structured error dicts.
    """
    collision = tools.create_note(config, "Target Note", "x")
    assert collision["status"] == "error"
    assert collision["reason"] == "exists"
    invalid = tools.create_note(config, "Bad|Name", "x")
    assert invalid["status"] == "error"
    assert invalid["reason"] == "invalid_name"
    assert json.dumps(collision) and json.dumps(invalid)


def test_update_note_overwrites_body(config: dict[str, Any], vault_root: Path) -> None:
    """
    ``update_note`` replaces the full file content.
    """
    result = tools.update_note(config, "Target Note", "# Target Note\n\nNew body.\n")
    assert result["status"] == "updated"
    assert result["path"] == "Target Note.md"
    text = (vault_root / "Target Note.md").read_text(encoding="utf-8")
    assert text == "# Target Note\n\nNew body.\n"
    assert json.dumps(result)


def test_update_note_rejects_unresolved_and_leaves_file_unchanged(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    D2 reject on update: the file keeps its original content.
    """
    result = tools.update_note(config, "Target Note", "Now with [[Ghost]].\n")
    assert result["status"] == "rejected"
    assert [entry["target"] for entry in result["unresolved"]] == ["Ghost"]
    text = (vault_root / "Target Note.md").read_text(encoding="utf-8")
    assert text == _TARGET_MD
    assert not (vault_root / "Ghost.md").exists()


def test_update_note_on_unresolved_create(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    D2 create on update: the missing target is bootstrapped and the
    body is overwritten.
    """
    result = tools.update_note(
        config, "Target Note", "Now with [[Ghost]].\n", on_unresolved="create"
    )
    assert result["status"] == "updated"
    assert result["created_targets"] == ["Ghost.md"]
    assert (vault_root / "Ghost.md").exists()
    text = (vault_root / "Target Note.md").read_text(encoding="utf-8")
    assert text == "Now with [[Ghost]].\n"


def test_update_unknown_note_is_structured_error(config: dict[str, Any]) -> None:
    """
    An unknown note reference returns ``reason="note_not_found"``.
    """
    result = tools.update_note(config, "No Such Note", "x")
    assert result["status"] == "error"
    assert result["reason"] == "note_not_found"
    assert json.dumps(result)


def test_append_to_note_at_eof(config: dict[str, Any], vault_root: Path) -> None:
    """
    ``append_to_note`` without a heading appends at end-of-file.
    """
    result = tools.append_to_note(config, "Target Note", "Appended line.")
    assert result["status"] == "appended"
    text = (vault_root / "Target Note.md").read_text(encoding="utf-8")
    assert text == _TARGET_MD + "Appended line.\n"
    assert json.dumps(result)


def test_append_to_note_under_heading(config: dict[str, Any], vault_root: Path) -> None:
    """
    With a heading, the text lands at the end of that section, before
    the next heading.
    """
    result = tools.append_to_note(config, "Sectioned", "- new entry", heading="## Log")
    assert result["status"] == "appended"
    text = (vault_root / "Sectioned.md").read_text(encoding="utf-8")
    log_start = text.index("## Log")
    other_start = text.index("## Other")
    entry_at = text.index("- new entry")
    assert log_start < text.index("- old entry") < entry_at < other_start


def test_append_to_note_rejects_unresolved(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    D2 reject on append: the file keeps its original content.
    """
    result = tools.append_to_note(config, "Target Note", "See [[Ghost]].")
    assert result["status"] == "rejected"
    assert [entry["target"] for entry in result["unresolved"]] == ["Ghost"]
    text = (vault_root / "Target Note.md").read_text(encoding="utf-8")
    assert text == _TARGET_MD


def test_append_to_note_on_unresolved_create(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    D2 create on append: the missing target is bootstrapped, then the
    text is appended.
    """
    result = tools.append_to_note(
        config, "Target Note", "See [[Ghost]].", on_unresolved="create"
    )
    assert result["status"] == "appended"
    assert result["created_targets"] == ["Ghost.md"]
    assert (vault_root / "Ghost.md").exists()
    text = (vault_root / "Target Note.md").read_text(encoding="utf-8")
    assert text.endswith("See [[Ghost]].\n")


def test_rename_note_propagates_inbound_links(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    ``rename_note`` moves the file and rewrites the linker's inbound
    link via the real propagation path, reporting the count.
    """
    result = tools.rename_note(config, "Target Note", "Renamed Note")
    assert result["status"] == "renamed"
    assert result["old"] == "Target Note.md"
    assert result["new"] == "Renamed Note.md"
    assert result["links_updated"] == 1
    assert not (vault_root / "Target Note.md").exists()
    assert (vault_root / "Renamed Note.md").exists()
    linker = (vault_root / "Linker.md").read_text(encoding="utf-8")
    assert "[[Renamed Note]]" in linker
    assert "[[Target Note]]" not in linker
    assert json.dumps(result)


def test_rename_note_collision_and_invalid_name(config: dict[str, Any]) -> None:
    """
    Rename collisions and invalid names return structured errors.
    """
    collision = tools.rename_note(config, "Target Note", "Linker")
    assert collision["status"] == "error"
    assert collision["reason"] == "exists"
    invalid = tools.rename_note(config, "Target Note", "Bad|Name")
    assert invalid["status"] == "error"
    assert invalid["reason"] == "invalid_name"


def test_delete_note_removes_file(config: dict[str, Any], vault_root: Path) -> None:
    """
    ``delete_note`` removes the note file.
    """
    result = tools.delete_note(config, "Sectioned")
    assert result["status"] == "deleted"
    assert result["path"] == "Sectioned.md"
    assert not (vault_root / "Sectioned.md").exists()
    assert json.dumps(result)


def test_delete_note_protects_state_directories(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    Deleting inside ``.hoppus`` returns a structured error and leaves
    the file untouched.
    """
    result = tools.delete_note(config, ".hoppus/state.json")
    assert result["status"] == "error"
    assert result["reason"] == "protected"
    assert (vault_root / ".hoppus" / "state.json").exists()
    assert json.dumps(result)


def test_delete_unknown_note_is_structured_error(config: dict[str, Any]) -> None:
    """
    Deleting an unknown note returns ``reason="note_not_found"``.
    """
    result = tools.delete_note(config, "No Such Note")
    assert result["status"] == "error"
    assert result["reason"] == "note_not_found"


def test_unknown_vault_is_structured_error(config: dict[str, Any]) -> None:
    """
    Write tools return ``reason="vault_not_found"`` for unknown vaults
    instead of raising.
    """
    result = tools.create_note(config, "X", "", vault="Nope")
    assert result["status"] == "error"
    assert result["reason"] == "vault_not_found"
    assert json.dumps(result)
