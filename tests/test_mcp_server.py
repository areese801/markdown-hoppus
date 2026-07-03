"""
Tests for the thin MCP server wrapper (spec §11, HOPPUS-58).

Only assembles the server and introspects its registered tools — no
transport is ever started and ``run()`` is never called.
"""

import asyncio
from typing import Any

import pytest

from hoppus import __version__
from hoppus.config import default_config
from hoppus.mcp.server import build_server

EXPECTED_READ_TOOLS = {
    "list_vaults",
    "list_notes",
    "get_note",
    "search_notes",
    "list_tags",
    "get_backlinks",
    "get_links",
    "neighbors",
    "audit_vault",
}

EXPECTED_WRITE_TOOLS = {
    "create_note",
    "update_note",
    "append_to_note",
    "rename_note",
    "delete_note",
}


def _config() -> dict[str, Any]:
    """
    Return a default config for hermetic server assembly.
    """
    return default_config()


def test_build_server_registers_all_read_tools() -> None:
    """
    ``build_server`` exposes exactly the spec-§11 read and write tools
    (write tools added by HOPPUS-59).
    """
    server = build_server(_config())
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_READ_TOOLS | EXPECTED_WRITE_TOOLS


def test_registered_tools_have_descriptions() -> None:
    """
    Every registered tool carries a non-empty description for agents.
    """
    server = build_server(_config())
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description and tool.description.strip()


def test_build_server_without_config_loads_defaults() -> None:
    """
    ``build_server()`` with no argument assembles via ``load_config``.
    """
    server = build_server()
    assert server.name == "hoppus"


def test_initialize_reports_app_version() -> None:
    """
    The server advertises the hoppus version, not the mcp SDK's
    (HOPPUS-72 F11).
    """
    server = build_server(_config())
    options = server._mcp_server.create_initialization_options()
    assert options.server_version == __version__


def test_tool_index_failure_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An unexpected index/IO failure inside a tool comes back as the
    structured ``{"status": "error", ...}`` dict the write tools use,
    not a raw exception (HOPPUS-72 F12).
    """

    def boom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise OSError("disk exploded")

    monkeypatch.setattr("hoppus.mcp.tools.list_notes", boom)
    server = build_server(_config())
    _content, structured = asyncio.run(server.call_tool("list_notes", {}))
    assert structured["result"] == {
        "status": "error",
        "reason": "internal_error",
        "message": "disk exploded",
    }


def test_tool_vault_lookup_failure_maps_to_stable_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A ``VaultNotFound`` raised by a read tool maps to the same stable
    ``vault_not_found`` reason the write tools return (HOPPUS-72 F12).
    """
    config = _config()
    config["vaults_root"] = "/no/such/vaults/root"
    server = build_server(config)
    _content, structured = asyncio.run(
        server.call_tool("list_tags", {"vault": "Missing"})
    )
    result = structured["result"]
    assert result["status"] == "error"
    assert result["reason"] == "vault_not_found"
    assert result["message"]
