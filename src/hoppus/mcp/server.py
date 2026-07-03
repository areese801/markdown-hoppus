"""
Thin MCP server wrapper exposing the read tools over stdio (spec §11,
HOPPUS-58).

All tool logic lives in the pure, unit-testable :mod:`hoppus.mcp.tools`
module; this wrapper only registers one MCP tool per read function and
runs the FastMCP stdio transport. Write tools (HOPPUS-59) and the
``mcp.read_only`` flag (HOPPUS-60) are handled by later stories.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from hoppus.config import load_config
from hoppus.mcp import tools


def build_server(config: dict[str, Any] | None = None) -> FastMCP:
    """
    Assemble the hoppus MCP server with the nine read tools registered.

    Returned without starting any transport, so callers (and tests) can
    introspect the registered tools.

    :param config: The merged configuration mapping (spec §12); loaded
        via :func:`hoppus.config.load_config` when omitted.
    :returns: The configured :class:`FastMCP` server.
    """
    cfg = config if config is not None else load_config()
    server = FastMCP("hoppus")

    @server.tool()
    def list_vaults() -> list[dict[str, Any]]:
        """
        List the vaults under the configured Vaults Root, with note counts.
        """
        return tools.list_vaults(cfg)

    @server.tool()
    def list_notes(
        vault: str | None = None,
        folder: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List a vault's notes (title, vault-relative path, word count),
        optionally filtered by a folder prefix and/or a tag (nested tags
        match by prefix, case-insensitively).
        """
        return tools.list_notes(cfg, vault=vault, folder=folder, tag=tag)

    @server.tool()
    def get_note(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Return a note's raw content plus parsed metadata (title, aliases,
        tags, headings, block ids, frontmatter, word count). The note may
        be referenced by vault-relative path or by title/alias.
        """
        return tools.get_note(cfg, note, vault=vault)

    @server.tool()
    def search_notes(
        query: str, vault: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Search notes with fuzzy full-text terms and targeted operators:
        ``tag:``, ``title:``, ``path:``, and frontmatter ``key:value``.
        """
        return tools.search_notes(cfg, query, vault=vault, limit=limit)

    @server.tool()
    def list_tags(vault: str | None = None) -> list[dict[str, Any]]:
        """
        List every tag in a vault with the number of notes carrying it.
        """
        return tools.list_tags(cfg, vault=vault)

    @server.tool()
    def get_backlinks(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Return a note's inbound mentions: ``linked`` (notes that link to
        it) and ``unlinked`` (plain-text title/alias mentions without a
        link).
        """
        return tools.get_backlinks(cfg, note, vault=vault)

    @server.tool()
    def get_links(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Return a note's outbound links, split into ``resolved`` (with the
        target's title and path) and ``unresolved`` (dangling targets).
        """
        return tools.get_links(cfg, note, vault=vault)

    @server.tool()
    def neighbors(
        note: str, vault: str | None = None, degrees: int = 2
    ) -> dict[str, Any]:
        """
        Return the N-degree local link subgraph around a note: nodes with
        hop distance and edges with link direction.
        """
        return tools.neighbors(cfg, note, vault=vault, degrees=degrees)

    @server.tool()
    def audit_vault(vault: str | None = None) -> dict[str, Any]:
        """
        Audit a vault for unresolved wikilinks, broken anchors, missing
        attachments, and broken markdown links, with per-kind counts.
        """
        return tools.audit_vault(cfg, vault=vault)

    return server


def run(config: dict[str, Any] | None = None) -> None:
    """
    Build the server and serve it over the stdio transport.

    Blocks until the client disconnects; this is the entrypoint behind
    ``hop mcp`` (spec §11).

    :param config: The merged configuration mapping (spec §12); loaded
        via :func:`hoppus.config.load_config` when omitted.
    """
    build_server(config).run(transport="stdio")
