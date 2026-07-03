"""
Pure, JSON-serializable read-tool implementations for the MCP server
(spec §11, HOPPUS-58).

Each public function here is the testable core behind one MCP read tool.
Functions accept the merged configuration mapping (spec §12), resolve the
target vault under ``vaults_root``, build a fresh :class:`~hoppus.index.
indexer.Index` for it, and return plain dicts/lists containing only
JSON-serializable values. All note paths in outputs are vault-relative
POSIX strings (portable and stable across machines), always paired with
the note's title. This module never imports the MCP SDK — the thin
transport wrapper lives in :mod:`hoppus.mcp.server`.
"""

from pathlib import Path
from typing import Any

from hoppus.graph import build_local_graph
from hoppus.index.indexer import Index
from hoppus.index.mentions import find_unlinked_mentions
from hoppus.integrity import audit_vault as _audit_vault
from hoppus.search.engine import search as _search
from hoppus.search.operators import matches_tag, parse_query
from hoppus.vault import discover_vaults

_MD_SUFFIX = ".md"


class VaultNotFound(Exception):
    """
    Raised when the requested vault cannot be found under the Vaults Root.
    """


class NoteNotFound(Exception):
    """
    Raised when a note reference cannot be resolved to a note in the vault.
    """


def _resolve_vault(config: dict[str, Any], vault_name: str | None = None) -> Path:
    """
    Resolve a vault name to its directory under the configured Vaults Root.

    :param config: The merged configuration mapping (spec §12).
    :param vault_name: Vault to select; defaults to ``default_vault``.
    :returns: The vault's directory path.
    :raises VaultNotFound: If the Vaults Root is missing or no vault with
        the requested name exists under it.
    """
    vaults_root = Path(config["vaults_root"]).expanduser()
    name = vault_name or config["default_vault"]
    try:
        vaults = discover_vaults(vaults_root)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise VaultNotFound(str(error)) from error
    for vault in vaults:
        if vault.name == name:
            return vault.path
    known = ", ".join(vault.name for vault in vaults) or "(none)"
    raise VaultNotFound(
        f"No vault named {name!r} under {vaults_root} (available: {known})"
    )


def _resolve_note(index: Index, note_ref: str) -> Path:
    """
    Resolve a note reference to an absolute note path in the index.

    Accepts a vault-relative path (with or without the ``.md`` extension,
    case-insensitively) or a note title/alias (via the index's title and
    alias maps, mirroring the resolver's lookup semantics).

    :param index: The built vault index.
    :param note_ref: The note reference to resolve.
    :returns: The absolute path of the matching note.
    :raises NoteNotFound: If nothing matches, or a title/alias lookup is
        ambiguous across multiple notes.
    """
    ref = note_ref.strip().strip("/")
    if not ref:
        raise NoteNotFound("Empty note reference")

    candidates = [ref] if ref.lower().endswith(_MD_SUFFIX) else [ref + _MD_SUFFIX]
    relpaths = {
        _relpath(path, index.vault_root).lower(): path for path in index.notes_by_path
    }
    for candidate in candidates:
        match = relpaths.get(candidate.lower())
        if match is not None:
            return match

    name = ref[: -len(_MD_SUFFIX)] if ref.lower().endswith(_MD_SUFFIX) else ref
    for lookup in (index.title_index, index.alias_index):
        paths = lookup.get(name.lower(), [])
        if len(paths) == 1:
            return paths[0]
        if len(paths) > 1:
            options = ", ".join(
                sorted(_relpath(path, index.vault_root) for path in paths)
            )
            raise NoteNotFound(
                f"Note reference {note_ref!r} is ambiguous; candidates: {options}"
            )
    raise NoteNotFound(f"No note matching {note_ref!r} in the vault")


def _relpath(path: Path, vault_root: Path) -> str:
    """
    Return a note path as a vault-relative POSIX string.

    :param path: The absolute note path.
    :param vault_root: The vault root directory.
    :returns: The relative path with forward slashes.
    """
    return path.relative_to(vault_root).as_posix()


def _plain(value: Any) -> Any:
    """
    Convert a parsed-frontmatter value into plain JSON-serializable types.

    ruamel round-trip containers become builtin dicts/lists; scalars that
    JSON cannot encode (e.g. dates) become strings.

    :param value: Any parsed YAML value.
    :returns: An equivalent structure using only JSON-safe types.
    """
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _note_ref_dict(index: Index, path: Path) -> dict[str, Any]:
    """
    Return the standard ``{"title", "path"}`` reference for a note path.

    :param index: The built vault index.
    :param path: The absolute note path.
    :returns: Title plus vault-relative POSIX path.
    """
    note = index.notes_by_path[path]
    return {"title": note.title, "path": _relpath(path, index.vault_root)}


def list_vaults(config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Enumerate the vaults under the configured Vaults Root (spec §11).

    :param config: The merged configuration mapping.
    :returns: One dict per vault with its ``name`` and ``note_count``.
    :raises VaultNotFound: If the Vaults Root is missing.
    """
    vaults_root = Path(config["vaults_root"]).expanduser()
    try:
        vaults = discover_vaults(vaults_root)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise VaultNotFound(str(error)) from error
    return [
        {"name": vault.name, "note_count": len(Index.build(vault.path).notes_by_path)}
        for vault in vaults
    ]


def list_notes(
    config: dict[str, Any],
    *,
    vault: str | None = None,
    folder: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """
    List a vault's notes, optionally filtered by folder and/or tag.

    The folder filter is a case-insensitive vault-relative prefix (e.g.
    ``"Projects"``). The tag filter is case-insensitive and matches
    nested tags by prefix, per the §9.8 ``tag:`` operator (``"area"``
    matches ``area/sub``).

    :param config: The merged configuration mapping.
    :param vault: Vault name; defaults to the configured default vault.
    :param folder: Optional folder prefix to filter by.
    :param tag: Optional tag to filter by.
    :returns: One dict per note with ``title``, ``path``, ``word_count``,
        sorted by path.
    :raises VaultNotFound: If the vault cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    prefix = folder.strip().strip("/").lower() if folder else None
    results: list[dict[str, Any]] = []
    for path in sorted(index.notes_by_path):
        note = index.notes_by_path[path]
        relpath = _relpath(path, vault_root)
        if prefix is not None and not relpath.lower().startswith(prefix + "/"):
            continue
        if tag is not None and not matches_tag(index.note_tags.get(path, set()), tag):
            continue
        results.append(
            {"title": note.title, "path": relpath, "word_count": note.word_count}
        )
    return results


def get_note(
    config: dict[str, Any], note: str, *, vault: str | None = None
) -> dict[str, Any]:
    """
    Return a note's raw content plus its parsed metadata (spec §11).

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param vault: Vault name; defaults to the configured default vault.
    :returns: A dict with ``title``, ``path``, ``raw``, ``aliases``,
        ``tags``, ``headings``, ``block_ids``, ``frontmatter``, and
        ``word_count``.
    :raises VaultNotFound: If the vault cannot be resolved.
    :raises NoteNotFound: If the note reference cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    path = _resolve_note(index, note)
    parsed = index.notes_by_path[path]
    return {
        "title": parsed.title,
        "path": _relpath(path, vault_root),
        "raw": path.read_text(encoding="utf-8"),
        "aliases": list(parsed.aliases),
        "tags": sorted(index.note_tags.get(path, set())),
        "headings": list(parsed.headings),
        "block_ids": list(parsed.block_ids),
        "frontmatter": _plain(parsed.frontmatter),
        "word_count": parsed.word_count,
    }


def search_notes(
    config: dict[str, Any],
    query: str,
    *,
    vault: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Search a vault with the §9.8 operator grammar (spec §11).

    Supports ``tag:``, ``title:``, ``path:``, frontmatter ``key:value``
    operators, and bare fuzzy full-text terms.

    :param config: The merged configuration mapping.
    :param query: The raw query string.
    :param vault: Vault name; defaults to the configured default vault.
    :param limit: Maximum number of results.
    :returns: One dict per hit with ``title``, ``path``, ``score``,
        ``snippet``, and ``line_number``, best matches first.
    :raises VaultNotFound: If the vault cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    parsed = parse_query(query)
    backend = config.get("search", {}).get("content_backend", "auto")
    results = _search(parsed, index, backend=backend, limit=limit)
    return [
        {
            "title": result.title,
            "path": _relpath(result.path, vault_root),
            "score": result.score,
            "snippet": result.snippet,
            "line_number": result.line_number,
        }
        for result in results
    ]


def list_tags(
    config: dict[str, Any], *, vault: str | None = None
) -> list[dict[str, Any]]:
    """
    List every tag in a vault with its note count (spec §11).

    :param config: The merged configuration mapping.
    :param vault: Vault name; defaults to the configured default vault.
    :returns: One dict per tag with ``tag`` and ``count``, sorted by tag.
    :raises VaultNotFound: If the vault cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    return [
        {"tag": tag, "count": len(paths)} for tag, paths in sorted(index.tags.items())
    ]


def get_backlinks(
    config: dict[str, Any], note: str, *, vault: str | None = None
) -> dict[str, Any]:
    """
    Return a note's linked and unlinked mentions (spec §11, §9.6).

    Linked mentions come from the index's backlink map; unlinked mentions
    are plain-text occurrences of the note's title/aliases in other notes
    that carry no explicit link.

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param vault: Vault name; defaults to the configured default vault.
    :returns: A dict with the target ``note`` reference, a ``linked``
        list of source notes, and an ``unlinked`` list of mentioning
        notes with occurrence counts.
    :raises VaultNotFound: If the vault cannot be resolved.
    :raises NoteNotFound: If the note reference cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    path = _resolve_note(index, note)
    linked = [
        _note_ref_dict(index, source)
        for source in sorted(index.backlinks.get(path, set()))
    ]
    mentions = find_unlinked_mentions(
        index.notes_by_path[path],
        index,
        lambda note_path: note_path.read_text(encoding="utf-8"),
    )
    unlinked = [
        {
            "title": mention.title,
            "path": _relpath(mention.source, vault_root),
            "count": mention.count,
        }
        for mention in mentions
    ]
    return {
        "note": _note_ref_dict(index, path),
        "linked": linked,
        "unlinked": unlinked,
    }


def get_links(
    config: dict[str, Any], note: str, *, vault: str | None = None
) -> dict[str, Any]:
    """
    Return a note's outbound links, split resolved vs unresolved (§11).

    Every link carries its raw ``target``, ``anchor``, ``display``, and
    ``is_embed``/``is_wikilink`` flags; resolved links additionally carry
    the resolved target's ``title`` and vault-relative ``path``.

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param vault: Vault name; defaults to the configured default vault.
    :returns: A dict with the source ``note`` reference plus ``resolved``
        and ``unresolved`` link lists.
    :raises VaultNotFound: If the vault cannot be resolved.
    :raises NoteNotFound: If the note reference cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    path = _resolve_note(index, note)
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for link in index.links.get(path, []):
        entry: dict[str, Any] = {
            "target": link.target,
            "anchor": link.anchor,
            "display": link.display,
            "is_embed": link.is_embed,
            "is_wikilink": link.is_wikilink,
        }
        if link.resolved is not None:
            entry["path"] = _relpath(link.resolved, vault_root)
            target_note = index.notes_by_path.get(link.resolved)
            entry["title"] = target_note.title if target_note else None
            resolved.append(entry)
        else:
            unresolved.append(entry)
    return {
        "note": _note_ref_dict(index, path),
        "resolved": resolved,
        "unresolved": unresolved,
    }


def neighbors(
    config: dict[str, Any],
    note: str,
    *,
    vault: str | None = None,
    degrees: int = 2,
) -> dict[str, Any]:
    """
    Return the N-degree local subgraph around a note (spec §11, §9.7).

    Traverses outbound links and backlinks up to ``degrees`` hops,
    applying the configured node budget and hub-exclusion threshold.

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param vault: Vault name; defaults to the configured default vault.
    :param degrees: Hop radius (clamped by the graph module's hard cap).
    :returns: A dict with the ``root`` reference, ``nodes`` (title, path,
        degree, truncated, link_count; the synthetic "+N more"
        placeholder has a None path), ``edges`` (source/target relative
        paths plus direction), ``truncated_count``, and ``radius``.
    :raises VaultNotFound: If the vault cannot be resolved.
    :raises NoteNotFound: If the note reference cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    path = _resolve_note(index, note)
    graph_config = config.get("graph", {})
    graph = build_local_graph(
        index,
        path,
        degrees=degrees,
        max_nodes=graph_config.get("max_nodes", 60),
        exclude_hub_threshold=graph_config.get("exclude_hub_threshold"),
    )
    nodes = [
        {
            "title": node.title,
            "path": None if node.truncated else _relpath(node.path, vault_root),
            "degree": node.degree,
            "truncated": node.truncated,
            "link_count": node.link_count,
        }
        for node in graph.nodes
    ]
    edges = [
        {
            "source": _relpath(edge.source, vault_root),
            "target": _relpath(edge.target, vault_root),
            "direction": edge.direction,
        }
        for edge in graph.edges
    ]
    return {
        "root": _note_ref_dict(index, path),
        "nodes": nodes,
        "edges": edges,
        "truncated_count": graph.truncated_count,
        "radius": graph.radius,
    }


def audit_vault(config: dict[str, Any], *, vault: str | None = None) -> dict[str, Any]:
    """
    Run the report-only vault audit and return its findings (spec §11).

    Surfaces unresolved wikilinks, broken anchors, missing attachments,
    and broken markdown links, gated by the ``link_integrity`` toggles.

    :param config: The merged configuration mapping.
    :param vault: Vault name; defaults to the configured default vault.
    :returns: A dict with the total ``count``, an ``issues`` list (each
        with ``path``, ``target``, ``kind``, ``anchor``, ``display``),
        and per-kind counts under ``by_kind``.
    :raises VaultNotFound: If the vault cannot be resolved.
    """
    vault_root = _resolve_vault(config, vault)
    index = Index.build(vault_root)
    report = _audit_vault(index, config=config)
    issues = [
        {
            "path": _relpath(issue.note_path, vault_root),
            "target": issue.target,
            "kind": issue.kind,
            "anchor": issue.anchor,
            "display": issue.display,
        }
        for issue in report.issues
    ]
    by_kind = {kind: len(items) for kind, items in report.by_kind().items()}
    return {"count": report.count, "issues": issues, "by_kind": by_kind}
