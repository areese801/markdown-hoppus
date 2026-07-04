"""
Pure, JSON-serializable tool implementations for the MCP server
(spec §11, HOPPUS-58 read tools; HOPPUS-59 write tools).

Each public function here is the testable core behind one MCP tool.
Functions accept the merged configuration mapping (spec §12), resolve the
target vault under ``vaults_root``, build a fresh :class:`~hoppus.index.
indexer.Index` for it, and return plain dicts/lists containing only
JSON-serializable values. All note paths in outputs are vault-relative
POSIX strings (portable and stable across machines), always paired with
the note's title where available. This module never imports the MCP
SDK — the thin transport wrapper lives in :mod:`hoppus.mcp.server`.

**D2 non-interactive integrity contract (spec §19 D2, §7.3):** the write
tools (:func:`create_note`, :func:`update_note`, :func:`append_to_note`)
cannot run the interactive fuzzy correct-or-create prompt, so unresolved
``[[wikilinks]]`` in written content are gated by an ``on_unresolved``
parameter instead:

- ``"reject"`` (default): nothing is written; the tool returns
  ``{"status": "rejected", "reason": "unresolved_links", "unresolved":
  [...]}`` where each entry carries the dangling ``target``, its
  ``anchor``/``display`` parts, and near-match ``suggestions`` (note
  titles, best first) so the calling agent can retry with a corrected
  target.
- ``"create"``: each distinct missing target is bootstrapped as an
  empty note at the vault root (the §7.3 "none of these" path), then
  the write proceeds; created notes are listed under
  ``created_targets``.

No silent orphan notes are ever produced by default. Write tools are
stateless: after a successful write the on-disk vault is the source of
truth and the next tool call rebuilds the index. They return structured
error dicts (``{"status": "error", "reason": ...}``) instead of raising,
so agents always get machine-readable outcomes.
"""

from pathlib import Path
from typing import Any

from hoppus import fileops
from hoppus.graph import build_local_graph
from hoppus.index.indexer import Index
from hoppus.index.mentions import find_unlinked_mentions
from hoppus.integrity import audit_vault as _audit_vault
from hoppus.integrity import (
    create_missing_note,
    find_unresolved_wikilinks,
    resolve_new_note_target,
)
from hoppus.naming import validate_note_name
from hoppus.related import _section_bounds
from hoppus.search.engine import search as _search
from hoppus.search.operators import matches_tag, parse_query
from hoppus.vault import discover_vaults, resolve_default_vault

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
    :param vault_name: Vault to select; defaults to ``default_vault``,
        resolved gracefully (name match, else the lone discovered vault,
        HOPPUS-88).
    :returns: The vault's directory path.
    :raises VaultNotFound: If the Vaults Root is missing, no vault with
        the requested name exists under it, or no default vault can be
        resolved.
    """
    vaults_root = Path(config["vaults_root"]).expanduser()
    try:
        vaults = discover_vaults(vaults_root)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise VaultNotFound(str(error)) from error
    known = ", ".join(vault.name for vault in vaults) or "(none)"
    if vault_name is not None:
        for vault in vaults:
            if vault.name == vault_name:
                return vault.path
        raise VaultNotFound(
            f"No vault named {vault_name!r} under {vaults_root} (available: {known})"
        )
    name = config.get("default_vault")
    resolved = resolve_default_vault(str(name) if name else None, vaults)
    if resolved is None:
        raise VaultNotFound(
            f"No default_vault set; choose one of: {known} (under {vaults_root})"
        )
    return resolved.path


def resolve_note(index: Index, note_ref: str) -> Path:
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
    path = resolve_note(index, note)
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
    path = resolve_note(index, note)
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
    path = resolve_note(index, note)
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
    path = resolve_note(index, note)
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


def _error(reason: str, message: str, **extra: Any) -> dict[str, Any]:
    """
    Build the structured error dict returned by every write tool.

    :param reason: A stable machine-readable code (``"exists"``,
        ``"invalid_name"``, ``"note_not_found"``, ``"vault_not_found"``,
        ``"protected"``).
    :param message: A human-readable explanation.
    :param extra: Additional JSON-serializable fields to include.
    :returns: ``{"status": "error", "reason": reason, "message": ...}``.
    """
    return {"status": "error", "reason": reason, "message": message, **extra}


def _check_unresolved(text: str, index: Index, note_path: Path) -> list[dict[str, Any]]:
    """
    Run the shared D2 gate over content about to be written (§19 D2).

    Delegates to :func:`hoppus.integrity.find_unresolved_wikilinks` — the
    same §7.3 rule the TUI enforces interactively — and flattens each
    unresolved note wikilink into a JSON-safe dict with near-match
    suggestions (note titles, best first).

    :param text: The content about to be written.
    :param index: The built vault index to resolve against.
    :param note_path: The (possibly prospective) path of the note the
        content belongs to.
    :returns: One dict per unresolved occurrence, in document order,
        each with ``target``, ``anchor``, ``display``, ``suggestions``.
        An empty list means the content is clean.
    """
    return [
        {
            "target": occurrence.link.target,
            "anchor": occurrence.link.anchor,
            "display": occurrence.link.display,
            "suggestions": [note.title for note in occurrence.suggestions],
        }
        for occurrence in find_unresolved_wikilinks(text, index, note_path)
    ]


def _rejected(unresolved: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build the D2 rejection dict for a write with dangling wikilinks.

    :param unresolved: The occurrences from :func:`_check_unresolved`.
    :returns: The ``{"status": "rejected", ...}`` payload.
    """
    return {
        "status": "rejected",
        "reason": "unresolved_links",
        "unresolved": unresolved,
    }


def _bootstrap_targets(
    vault_root: Path, unresolved: list[dict[str, Any]]
) -> list[str] | dict[str, Any]:
    """
    Create the missing notes for an ``on_unresolved="create"`` write.

    Each distinct unresolved target is bootstrapped as an empty note at
    the vault root (§7.3 "none of these" path). All derived names are
    validated up front, so an invalid target creates nothing.

    :param vault_root: The vault root directory.
    :param unresolved: The occurrences from :func:`_check_unresolved`.
    :returns: The created notes' vault-relative paths, or a structured
        error dict when a target name is invalid.
    """
    names: list[str] = []
    for entry in unresolved:
        name = resolve_new_note_target(entry["target"])
        if name not in names:
            names.append(name)
    for name in names:
        if validate_note_name(name):
            return _error(
                "invalid_name",
                f"Cannot bootstrap note for invalid link target {name!r}",
                target=name,
            )
    created: list[str] = []
    for name in names:
        path = create_missing_note(vault_root, name)
        created.append(_relpath(path, vault_root))
    return created


def create_note(
    config: dict[str, Any],
    name: str,
    content: str = "",
    *,
    vault: str | None = None,
    on_unresolved: str = "reject",
) -> dict[str, Any]:
    """
    Create a new note at the vault root with the given content (§11).

    The name is validated (§5.2) and collisions are refused. The content
    passes through the D2 gate: unresolved wikilinks reject the write by
    default, or bootstrap their targets when ``on_unresolved="create"``
    (see the module docstring). Wikilinks targeting the new note itself
    are not flagged — they resolve once the note exists.

    :param config: The merged configuration mapping.
    :param name: The note title (``.md`` is appended).
    :param content: The initial note body.
    :param vault: Vault name; defaults to the configured default vault.
    :param on_unresolved: ``"reject"`` (default) or ``"create"``.
    :returns: ``{"status": "created", "path", "created_targets"}``, a
        D2 rejection dict, or a structured error dict.
    """
    try:
        vault_root = _resolve_vault(config, vault)
    except VaultNotFound as error:
        return _error("vault_not_found", str(error))
    stem = name.strip()
    if validate_note_name(stem):
        return _error("invalid_name", f"Invalid note name: {name!r}")
    target_path = vault_root / f"{stem}{_MD_SUFFIX}"
    if target_path.exists():
        return _error("exists", f"Note already exists: {target_path.name}")

    index = Index.build(vault_root)
    unresolved = [
        entry
        for entry in _check_unresolved(content, index, target_path)
        if resolve_new_note_target(entry["target"]).lower() != stem.lower()
    ]
    created_targets: list[str] = []
    if unresolved:
        if on_unresolved != "create":
            return _rejected(unresolved)
        result = _bootstrap_targets(vault_root, unresolved)
        if isinstance(result, dict):
            return result
        created_targets = result

    path = fileops.create_note(vault_root, stem)
    path.write_text(content, encoding="utf-8")
    return {
        "status": "created",
        "path": _relpath(path, vault_root),
        "created_targets": created_targets,
    }


def update_note(
    config: dict[str, Any],
    note: str,
    content: str,
    *,
    vault: str | None = None,
    on_unresolved: str = "reject",
) -> dict[str, Any]:
    """
    Replace an existing note's entire file content (§11).

    This is a full-body overwrite: ``content`` becomes the complete new
    file, frontmatter included. Preserving or updating frontmatter is
    the caller's responsibility for the MVP (fetch it via ``get_note``
    first). The new content passes through the D2 gate before anything
    is written (see the module docstring).

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param content: The full replacement file content.
    :param vault: Vault name; defaults to the configured default vault.
    :param on_unresolved: ``"reject"`` (default) or ``"create"``.
    :returns: ``{"status": "updated", "path", "created_targets"}``, a
        D2 rejection dict, or a structured error dict.
    """
    try:
        vault_root = _resolve_vault(config, vault)
        index = Index.build(vault_root)
        path = resolve_note(index, note)
    except VaultNotFound as error:
        return _error("vault_not_found", str(error))
    except NoteNotFound as error:
        return _error("note_not_found", str(error))

    unresolved = _check_unresolved(content, index, path)
    created_targets: list[str] = []
    if unresolved:
        if on_unresolved != "create":
            return _rejected(unresolved)
        result = _bootstrap_targets(vault_root, unresolved)
        if isinstance(result, dict):
            return result
        created_targets = result

    path.write_text(content, encoding="utf-8")
    return {
        "status": "updated",
        "path": _relpath(path, vault_root),
        "created_targets": created_targets,
    }


def _append_text(existing: str, text: str, heading: str | None) -> str:
    """
    Append text to a note body, at EOF or under a heading section.

    With no heading, the text lands at end-of-file (a newline is added
    first when the existing text lacks one). With a heading, the text is
    inserted at the end of that heading's section — after its last
    non-blank line, before the next same-or-higher-level heading —
    reusing the :mod:`hoppus.related` section-bounds logic (§7.2 style);
    a missing heading is bootstrapped at end-of-file, mirroring
    ``add_related_link``.

    :param existing: The note's current full text.
    :param text: The text to append.
    :param heading: The heading line to append under, or None for EOF.
    :returns: The updated full text.
    """
    addition = text if text.endswith("\n") else f"{text}\n"
    if heading is None:
        base = existing
        if base and not base.endswith("\n"):
            base += "\n"
        return base + addition
    lines = existing.split("\n")
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        base = existing
        if base:
            if not base.endswith("\n"):
                base += "\n"
            if not base.endswith("\n\n"):
                base += "\n"
        return f"{base}{heading.strip()}\n{addition}"
    heading_index, end = bounds
    position = heading_index + 1
    for j in range(heading_index + 1, end):
        if lines[j].strip():
            position = j + 1
    lines[position:position] = addition.split("\n")[:-1]
    return "\n".join(lines)


def append_to_note(
    config: dict[str, Any],
    note: str,
    text: str,
    *,
    vault: str | None = None,
    on_unresolved: str = "reject",
    heading: str | None = None,
) -> dict[str, Any]:
    """
    Append text to an existing note, at EOF or under a heading (§11).

    Only the appended text passes through the D2 gate (see the module
    docstring) — pre-existing dangling links in the note never block an
    append. When ``heading`` is given (e.g. ``"## Related"``), the text
    is inserted at the end of that section; a missing heading is
    bootstrapped at end-of-file.

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param text: The text to append.
    :param vault: Vault name; defaults to the configured default vault.
    :param on_unresolved: ``"reject"`` (default) or ``"create"``.
    :param heading: Optional heading line to append under.
    :returns: ``{"status": "appended", "path", "created_targets"}``, a
        D2 rejection dict, or a structured error dict.
    """
    try:
        vault_root = _resolve_vault(config, vault)
        index = Index.build(vault_root)
        path = resolve_note(index, note)
    except VaultNotFound as error:
        return _error("vault_not_found", str(error))
    except NoteNotFound as error:
        return _error("note_not_found", str(error))

    unresolved = _check_unresolved(text, index, path)
    created_targets: list[str] = []
    if unresolved:
        if on_unresolved != "create":
            return _rejected(unresolved)
        result = _bootstrap_targets(vault_root, unresolved)
        if isinstance(result, dict):
            return result
        created_targets = result

    existing = path.read_text(encoding="utf-8")
    path.write_text(_append_text(existing, text, heading), encoding="utf-8")
    return {
        "status": "appended",
        "path": _relpath(path, vault_root),
        "created_targets": created_targets,
    }


def rename_note(
    config: dict[str, Any],
    note: str,
    new_name: str,
    *,
    vault: str | None = None,
) -> dict[str, Any]:
    """
    Rename a note in place, propagating inbound links (§11, §6.2).

    Delegates to :func:`hoppus.fileops.rename_note` — the exact same
    propagation path the TUI uses — so every inbound wikilink and
    markdown link across the vault is rewritten to the new name.

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param new_name: The new title (``.md`` is appended).
    :param vault: Vault name; defaults to the configured default vault.
    :returns: ``{"status": "renamed", "old", "new", "links_updated"}``
        or a structured error dict.
    """
    try:
        vault_root = _resolve_vault(config, vault)
        index = Index.build(vault_root)
        path = resolve_note(index, note)
    except VaultNotFound as error:
        return _error("vault_not_found", str(error))
    except NoteNotFound as error:
        return _error("note_not_found", str(error))
    try:
        new_path, plan = fileops.rename_note(
            path, new_name, index, vault_root, apply_links=True
        )
    except ValueError as error:
        return _error("invalid_name", str(error))
    except FileExistsError as error:
        return _error("exists", str(error))
    return {
        "status": "renamed",
        "old": _relpath(path, vault_root),
        "new": _relpath(new_path, vault_root),
        "links_updated": plan.link_count,
    }


def delete_note(
    config: dict[str, Any], note: str, *, vault: str | None = None
) -> dict[str, Any]:
    """
    Delete a note from the vault (§11, guarded).

    Delegates to :func:`hoppus.fileops.delete_path`, which refuses to
    touch anything in the protected ``.hoppus``/``.obsidian`` state
    directories (§5.2) — such attempts return a structured
    ``reason="protected"`` error. The reference is resolved like the
    read tools' note argument; a vault-relative path that exists on
    disk but is not an indexed note (e.g. a non-markdown file) is also
    accepted so the guard applies uniformly.

    :param config: The merged configuration mapping.
    :param note: Note reference (vault-relative path or title/alias).
    :param vault: Vault name; defaults to the configured default vault.
    :returns: ``{"status": "deleted", "path"}`` or a structured error
        dict.
    """
    try:
        vault_root = _resolve_vault(config, vault)
        index = Index.build(vault_root)
    except VaultNotFound as error:
        return _error("vault_not_found", str(error))
    try:
        path = resolve_note(index, note)
    except NoteNotFound as error:
        ref = note.strip().strip("/")
        candidate = vault_root / ref
        if not ref or ".." in candidate.parts or not candidate.exists():
            return _error("note_not_found", str(error))
        path = candidate
    try:
        fileops.delete_path(path)
    except ValueError as error:
        return _error("protected", str(error))
    except FileNotFoundError as error:
        return _error("note_not_found", str(error))
    return {"status": "deleted", "path": _relpath(path, vault_root)}
