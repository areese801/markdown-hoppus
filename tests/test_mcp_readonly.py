"""
Tests for the ``mcp.read_only`` gate and the per-tool MCP coverage
guarantee (spec §11/§14, HOPPUS-60).

Covers three things:

- the registration gate: ``read_only: True`` exposes only the nine read
  tools, the default exposes all fourteen, and an unregistered write
  tool cannot mutate the vault;
- the §14 headline "agent-authored links can't dangle" guarantee, on
  both the ``reject`` and ``create`` branches of the D2 contract;
- a table-driven smoke exercising every MCP tool function once, so the
  suite structurally guarantees each tool has at least one test.

Everything is headless: no transport is ever started.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest

from hoppus.config import default_config
from hoppus.mcp import tools
from hoppus.mcp.server import (
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    build_server,
    registered_tool_names,
    write_tools_enabled,
)

VAULT_NAME = "Main"

_TARGET_MD = """\
# Target Note

Some existing body text. #topic
"""

_LINKER_MD = """\
# Linker

Points at [[Target Note]] explicitly.
"""


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    """
    Build a small clean vault (no pre-existing integrity issues) with a
    link target and a linker note.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root directory.
    """
    root = tmp_path / "Vaults" / VAULT_NAME
    root.mkdir(parents=True)
    (root / "Target Note.md").write_text(_TARGET_MD, encoding="utf-8")
    (root / "Linker.md").write_text(_LINKER_MD, encoding="utf-8")
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


def _read_only(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return ``config`` with ``mcp.read_only`` switched on.

    :param config: The base merged-config dict.
    :returns: The same dict, mutated for convenience.
    """
    config.setdefault("mcp", {})["read_only"] = True
    return config


def test_default_config_registers_all_tools(config: dict[str, Any]) -> None:
    """
    With the default ``read_only: False``, all fourteen tools register.
    """
    assert write_tools_enabled(config)
    names = registered_tool_names(config)
    assert set(names) == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
    assert len(names) == 14


def test_read_only_registers_only_read_tools(config: dict[str, Any]) -> None:
    """
    ``mcp.read_only: True`` exposes the nine read tools and none of the
    five write tools.
    """
    cfg = _read_only(config)
    assert not write_tools_enabled(cfg)
    names = registered_tool_names(cfg)
    assert set(names) == set(READ_TOOL_NAMES)
    assert set(names).isdisjoint(WRITE_TOOL_NAMES)


def test_read_only_blocks_writes_end_to_end(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    Under ``read_only: True`` a write attempt against the server fails
    (the tool is simply not registered) and the vault is untouched.
    """
    server = build_server(_read_only(config))
    with pytest.raises(Exception, match="create_note"):
        asyncio.run(
            server.call_tool("create_note", {"name": "Sneaky", "content": "hi\n"})
        )
    assert not (vault_root / "Sneaky.md").exists()


def test_agent_cannot_dangle_reject_branch(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    §14 headline, reject branch: a write carrying a dangling
    ``[[Ghost]]`` under the DEFAULT ``on_unresolved`` is rejected, so
    the vault stays clean — the audit reports zero unresolved links.
    """
    result = tools.create_note(config, "New Note", "See [[Ghost]].\n")
    assert result["status"] == "rejected"
    assert not (vault_root / "New Note.md").exists()

    update = tools.update_note(config, "Linker", "Now see [[Ghost]].\n")
    assert update["status"] == "rejected"
    assert (vault_root / "Linker.md").read_text(encoding="utf-8") == _LINKER_MD

    audit = tools.audit_vault(config)
    assert audit["by_kind"].get("unresolved_link", 0) == 0
    assert all(issue["target"] != "Ghost" for issue in audit["issues"])


def test_agent_cannot_dangle_create_branch(
    config: dict[str, Any], vault_root: Path
) -> None:
    """
    §14 headline, create branch: ``on_unresolved="create"`` bootstraps
    the missing target so the written link resolves — the audit reports
    zero unresolved links afterward.
    """
    result = tools.create_note(
        config, "New Note", "See [[Ghost]].\n", on_unresolved="create"
    )
    assert result["status"] == "created"
    assert (vault_root / "Ghost.md").exists()

    audit = tools.audit_vault(config)
    assert audit["by_kind"].get("unresolved_link", 0) == 0
    assert all(issue["target"] != "Ghost" for issue in audit["issues"])


def _smoke_calls(cfg: dict[str, Any]) -> dict[str, Callable[[], Any]]:
    """
    Build one valid call per MCP tool function against the temp vault.

    Write calls are ordered so each precondition holds when the table
    runs top to bottom (create before update/append/rename/delete).

    :param cfg: The merged-config dict for the temp vault.
    :returns: Mapping of tool name to a zero-argument callable.
    """
    return {
        "list_vaults": lambda: tools.list_vaults(cfg),
        "list_notes": lambda: tools.list_notes(cfg),
        "get_note": lambda: tools.get_note(cfg, "Target Note"),
        "search_notes": lambda: tools.search_notes(cfg, "Target"),
        "list_tags": lambda: tools.list_tags(cfg),
        "get_backlinks": lambda: tools.get_backlinks(cfg, "Target Note"),
        "get_links": lambda: tools.get_links(cfg, "Linker"),
        "neighbors": lambda: tools.neighbors(cfg, "Target Note"),
        "audit_vault": lambda: tools.audit_vault(cfg),
        "create_note": lambda: tools.create_note(cfg, "Smoke Note", "Body.\n"),
        "update_note": lambda: tools.update_note(cfg, "Smoke Note", "New body.\n"),
        "append_to_note": lambda: tools.append_to_note(cfg, "Smoke Note", "More.\n"),
        "rename_note": lambda: tools.rename_note(cfg, "Smoke Note", "Smoked Note"),
        "delete_note": lambda: tools.delete_note(cfg, "Smoked Note"),
    }


def test_every_mcp_tool_is_exercised(config: dict[str, Any]) -> None:
    """
    Completeness guarantee (§14): every one of the fourteen MCP tool
    functions runs once against a temp vault and returns a well-formed
    payload (write tools a success ``status``, read tools a list/dict).
    """
    calls = _smoke_calls(config)
    assert set(calls) == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
    for name, call in calls.items():
        result = call()
        if name in WRITE_TOOL_NAMES:
            assert result["status"] in {
                "created",
                "updated",
                "appended",
                "renamed",
                "deleted",
            }, name
        else:
            assert isinstance(result, (list, dict)), name
