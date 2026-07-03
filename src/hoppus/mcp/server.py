"""
Thin MCP server wrapper exposing the read and write tools over stdio
(spec §11, HOPPUS-58/HOPPUS-59).

All tool logic lives in the pure, unit-testable :mod:`hoppus.mcp.tools`
module; this wrapper only registers one MCP tool per function and runs
the FastMCP stdio transport.

The ``mcp.read_only`` config flag (spec §11/§12, HOPPUS-60, default
``False``) gates the write tools: when it is ``True``,
:func:`build_server` registers only the nine read tools and skips
:func:`_register_write_tools` entirely, so a read-only MCP context has
no tool that can mutate a vault.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from hoppus import __version__
from hoppus.config import load_config
from hoppus.mcp import tools

READ_TOOL_NAMES: tuple[str, ...] = (
    "list_vaults",
    "list_notes",
    "get_note",
    "search_notes",
    "list_tags",
    "get_backlinks",
    "get_links",
    "neighbors",
    "audit_vault",
)

WRITE_TOOL_NAMES: tuple[str, ...] = (
    "create_note",
    "update_note",
    "append_to_note",
    "rename_note",
    "delete_note",
)


def _tool_error(reason: str, error: Exception) -> dict[str, Any]:
    """
    Build the structured error dict shared with the write tools.

    :param reason: A stable machine-readable code (e.g.
        ``"vault_not_found"``, ``"note_not_found"``,
        ``"internal_error"``).
    :param error: The failure to report.
    :returns: ``{"status": "error", "reason": reason, "message": ...}``.
    """
    return {"status": "error", "reason": reason, "message": str(error)}


def _guarded(call: Callable[[], Any]) -> Any:
    """
    Run a tool body, converting failures into structured error dicts.

    Expected lookup failures map to the same stable reasons the write
    tools use; any other index/IO/parse failure becomes
    ``reason="internal_error"`` so MCP clients always receive the
    machine-readable ``{"status": "error", ...}`` shape instead of a
    raw exception string (HOPPUS-72 F12).

    :param call: The zero-argument tool body to run.
    :returns: The tool's result, or a structured error dict.
    """
    try:
        return call()
    except tools.VaultNotFound as error:
        return _tool_error("vault_not_found", error)
    except tools.NoteNotFound as error:
        return _tool_error("note_not_found", error)
    except Exception as error:
        return _tool_error("internal_error", error)


def write_tools_enabled(config: dict[str, Any]) -> bool:
    """
    Report whether the write tools should be registered for a config.

    :param config: The merged configuration mapping (spec §12).
    :returns: ``False`` when ``mcp.read_only`` is set, ``True`` otherwise.
    """
    return not config.get("mcp", {}).get("read_only", False)


def registered_tool_names(config: dict[str, Any] | None = None) -> list[str]:
    """
    Build a server for ``config`` and return its registered tool names.

    A test/introspection seam for the ``mcp.read_only`` gate; no
    transport is started.

    :param config: The merged configuration mapping (spec §12); loaded
        via :func:`hoppus.config.load_config` when omitted.
    :returns: The sorted names of the tools the server exposes.
    """
    server = build_server(config)
    return sorted(tool.name for tool in asyncio.run(server.list_tools()))


def build_server(config: dict[str, Any] | None = None) -> FastMCP:
    """
    Assemble the hoppus MCP server with the nine read tools registered,
    plus the five write tools unless ``mcp.read_only`` is set.

    Returned without starting any transport, so callers (and tests) can
    introspect the registered tools.

    :param config: The merged configuration mapping (spec §12); loaded
        via :func:`hoppus.config.load_config` when omitted.
    :returns: The configured :class:`FastMCP` server.
    """
    cfg = config if config is not None else load_config()
    server = FastMCP("hoppus")
    # FastMCP's constructor exposes no version parameter, so set it on
    # the underlying lowlevel server; otherwise `initialize` reports the
    # mcp SDK's version instead of the app's (HOPPUS-72 F11).
    server._mcp_server.version = __version__

    @server.tool()
    def list_vaults() -> list[dict[str, Any]] | dict[str, Any]:
        """
        List the vaults under the configured Vaults Root, with note counts.
        """
        return _guarded(lambda: tools.list_vaults(cfg))

    @server.tool()
    def list_notes(
        vault: str | None = None,
        folder: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """
        List a vault's notes (title, vault-relative path, word count),
        optionally filtered by a folder prefix and/or a tag (nested tags
        match by prefix, case-insensitively).
        """
        return _guarded(
            lambda: tools.list_notes(cfg, vault=vault, folder=folder, tag=tag)
        )

    @server.tool()
    def get_note(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Return a note's raw content plus parsed metadata (title, aliases,
        tags, headings, block ids, frontmatter, word count). The note may
        be referenced by vault-relative path or by title/alias.
        """
        return _guarded(lambda: tools.get_note(cfg, note, vault=vault))

    @server.tool()
    def search_notes(
        query: str, vault: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Search notes with fuzzy full-text terms and targeted operators:
        ``tag:``, ``title:``, ``path:``, and frontmatter ``key:value``.
        """
        return _guarded(
            lambda: tools.search_notes(cfg, query, vault=vault, limit=limit)
        )

    @server.tool()
    def list_tags(vault: str | None = None) -> list[dict[str, Any]] | dict[str, Any]:
        """
        List every tag in a vault with the number of notes carrying it.
        """
        return _guarded(lambda: tools.list_tags(cfg, vault=vault))

    @server.tool()
    def get_backlinks(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Return a note's inbound mentions: ``linked`` (notes that link to
        it) and ``unlinked`` (plain-text title/alias mentions without a
        link).
        """
        return _guarded(lambda: tools.get_backlinks(cfg, note, vault=vault))

    @server.tool()
    def get_links(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Return a note's outbound links, split into ``resolved`` (with the
        target's title and path) and ``unresolved`` (dangling targets).
        """
        return _guarded(lambda: tools.get_links(cfg, note, vault=vault))

    @server.tool()
    def neighbors(
        note: str, vault: str | None = None, degrees: int = 2
    ) -> dict[str, Any]:
        """
        Return the N-degree local link subgraph around a note: nodes with
        hop distance and edges with link direction.
        """
        return _guarded(
            lambda: tools.neighbors(cfg, note, vault=vault, degrees=degrees)
        )

    @server.tool()
    def audit_vault(vault: str | None = None) -> dict[str, Any]:
        """
        Audit a vault for unresolved wikilinks, broken anchors, missing
        attachments, and broken markdown links, with per-kind counts.
        """
        return _guarded(lambda: tools.audit_vault(cfg, vault=vault))

    if write_tools_enabled(cfg):
        _register_write_tools(server, cfg)
    return server


def _register_write_tools(server: FastMCP, cfg: dict[str, Any]) -> None:
    """
    Register the five write tools (spec §11, HOPPUS-59) on a server.

    Kept separate from :func:`build_server`'s read tools so the
    ``mcp.read_only`` flag (HOPPUS-60) can skip this call to run the
    server read-only.

    :param server: The FastMCP server to register the tools on.
    :param cfg: The merged configuration mapping (spec §12).
    """

    @server.tool()
    def create_note(
        name: str,
        content: str = "",
        vault: str | None = None,
        on_unresolved: str = "reject",
    ) -> dict[str, Any]:
        """
        Create a new note at the vault root. Unresolved ``[[wikilinks]]``
        in the content are gated by ``on_unresolved``: ``"reject"``
        (default) writes nothing and returns ``status="rejected"`` with
        the dangling targets plus near-match suggestions to retry with;
        ``"create"`` bootstraps each missing target as an empty note at
        the vault root, then writes. Collisions and invalid names return
        ``status="error"``.
        """
        return _guarded(
            lambda: tools.create_note(
                cfg, name, content, vault=vault, on_unresolved=on_unresolved
            )
        )

    @server.tool()
    def update_note(
        note: str,
        content: str,
        vault: str | None = None,
        on_unresolved: str = "reject",
    ) -> dict[str, Any]:
        """
        Replace an existing note's entire file content (frontmatter
        included — fetch it via ``get_note`` first if it must be kept).
        Unresolved ``[[wikilinks]]`` in the new content are gated by
        ``on_unresolved``: ``"reject"`` (default) writes nothing and
        returns the dangling targets plus suggestions; ``"create"``
        bootstraps the missing targets at the vault root, then writes.
        """
        return _guarded(
            lambda: tools.update_note(
                cfg, note, content, vault=vault, on_unresolved=on_unresolved
            )
        )

    @server.tool()
    def append_to_note(
        note: str,
        text: str,
        vault: str | None = None,
        on_unresolved: str = "reject",
        heading: str | None = None,
    ) -> dict[str, Any]:
        """
        Append text to an existing note — at end-of-file, or at the end
        of the given ``heading`` section (a missing heading is added at
        end-of-file). Unresolved ``[[wikilinks]]`` in the appended text
        are gated by ``on_unresolved``: ``"reject"`` (default) writes
        nothing and returns the dangling targets plus suggestions;
        ``"create"`` bootstraps the missing targets at the vault root,
        then appends.
        """
        return _guarded(
            lambda: tools.append_to_note(
                cfg,
                note,
                text,
                vault=vault,
                on_unresolved=on_unresolved,
                heading=heading,
            )
        )

    @server.tool()
    def rename_note(
        note: str, new_name: str, vault: str | None = None
    ) -> dict[str, Any]:
        """
        Rename a note, rewriting every inbound link across the vault
        (same propagation as the TUI). Returns the old/new paths and
        the number of links updated; collisions and invalid names
        return ``status="error"``.
        """
        return _guarded(lambda: tools.rename_note(cfg, note, new_name, vault=vault))

    @server.tool()
    def delete_note(note: str, vault: str | None = None) -> dict[str, Any]:
        """
        Delete a note file (folders delete recursively). The protected
        ``.hoppus``/``.obsidian`` state directories are never touched —
        such attempts return ``status="error"``.
        """
        return _guarded(lambda: tools.delete_note(cfg, note, vault=vault))


def run(config: dict[str, Any] | None = None) -> None:
    """
    Build the server and serve it over the stdio transport.

    Blocks until the client disconnects; this is the entrypoint behind
    ``hop mcp`` (spec §11).

    :param config: The merged configuration mapping (spec §12); loaded
        via :func:`hoppus.config.load_config` when omitted.
    """
    build_server(config).run(transport="stdio")
