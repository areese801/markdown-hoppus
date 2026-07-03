"""
Tests for the thin MCP server wrapper (spec §11, HOPPUS-58).

Only assembles the server and introspects its registered tools — no
transport is ever started and ``run()`` is never called.
"""

import asyncio
from typing import Any

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
