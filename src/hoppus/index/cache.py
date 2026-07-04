"""
Persistent index cache for instant relaunch (spec §8, HOPPUS-79 / F19).

A successfully built :class:`~hoppus.index.indexer.Index` is serialized to
``<vault>/.hoppus/index.<SCHEMA_VERSION>.json`` so the next launch can
render the UI immediately from the cached index while a fresh build
reconciles in the background. The format is plain JSON — never pickle —
so loading a cache can never execute code, and unknown/corrupt content
simply reads as "no cache".

Safety properties:

- **Atomic writes**: the cache is written to a temp file in ``.hoppus/``
  and moved into place with ``os.replace``, so a partial file is never
  visible.
- **Schema versioning**: :data:`SCHEMA_VERSION` is part of the filename
  and the header; any mismatch (or missing/corrupt/truncated JSON) makes
  :func:`load_index` return None so callers fall back to a clean build.
- **Fingerprinting**: a cheap vault fingerprint (hash of sorted
  ``(relpath, mtime_ns, size)`` tuples) is stored in the header via
  :func:`compute_fingerprint`. A loaded cache is always provisional —
  callers must reconcile with a background rebuild regardless — but the
  fingerprint lets them (and tests) detect staleness cheaply.

Paths are stored vault-relative in POSIX form (matching
``hoppus.bookmarks``) so a cache survives a vault moving between
machines; a fingerprint mismatch then triggers reconciliation as usual.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from hoppus.index.indexer import Index, _iter_vault_files
from hoppus.model import Link, Note

SCHEMA_VERSION = 1

_STATE_DIR = ".hoppus"


def cache_file(vault_root: Path) -> Path:
    """
    Return the path of the vault's index cache file.

    :param vault_root: The vault root directory.
    :returns: ``<vault_root>/.hoppus/index.<SCHEMA_VERSION>.json``.
    """
    return Path(vault_root) / _STATE_DIR / f"index.{SCHEMA_VERSION}.json"


def compute_fingerprint(vault_root: Path) -> str:
    """
    Compute a cheap fingerprint of a vault's current on-disk state.

    Hashes the sorted ``(relpath, mtime_ns, size)`` tuples of every file
    the indexer would scan (notes and attachments, honoring the same
    ``.obsidian/``/``.hoppus/`` skip rules), so the fingerprint changes
    whenever a file is added, removed, or modified. Stat failures skip
    the file rather than raising.

    :param vault_root: The vault root directory.
    :returns: A hex SHA-256 digest of the vault state.
    """
    root = Path(vault_root)
    note_paths, attachment_paths = _iter_vault_files(root)
    entries: list[tuple[str, int, int]] = []
    for path in note_paths + attachment_paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((_to_relative(root, path), stat.st_mtime_ns, stat.st_size))
    entries.sort()
    payload = json.dumps(entries, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cached_fingerprint(vault_root: Path) -> str | None:
    """
    Read the fingerprint stored in the vault's cache header, if any.

    :param vault_root: The vault root directory.
    :returns: The stored fingerprint, or None when there is no readable
        cache (missing, corrupt, or wrong schema).
    """
    data = _read_cache_data(vault_root)
    if data is None:
        return None
    fingerprint = data.get("fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def save_index(index: Index, vault_root: Path) -> None:
    """
    Persist an index to the vault's ``.hoppus/`` cache file, atomically.

    Serializes the index's notes, links, tags, and attachments as JSON
    together with the schema version and the vault's current
    fingerprint, writing via a temp file + ``os.replace`` so a partial
    cache is never visible. Never raises: serialization or write
    failures (unserializable frontmatter values are stringified first,
    but the disk can still fail) leave any existing cache untouched.

    :param index: The built index to persist.
    :param vault_root: The vault root directory.
    """
    root = Path(vault_root)
    try:
        payload = json.dumps(
            {
                "schema": SCHEMA_VERSION,
                "fingerprint": compute_fingerprint(root),
                "index": _serialize_index(index, root),
            },
            default=str,
        )
        target = cache_file(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=target.parent, prefix=target.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temp_path, target)
        except BaseException:
            os.unlink(temp_path)
            raise
    except Exception:
        return


def load_index(vault_root: Path) -> Index | None:
    """
    Load the vault's cached index, if a valid one exists.

    The returned index is always **provisional**: even when the stored
    fingerprint matches the vault's current state, callers must
    reconcile with a fresh background build before treating the data as
    authoritative (HOPPUS-79). A missing, corrupt, truncated, or
    wrong-schema cache reads as None — this function never raises over
    bad cache state.

    :param vault_root: The vault root directory.
    :returns: The reconstructed index, or None when no usable cache
        exists.
    """
    root = Path(vault_root)
    data = _read_cache_data(root)
    if data is None:
        return None
    try:
        return _deserialize_index(data["index"], root)
    except Exception:
        return None


def _read_cache_data(vault_root: Path) -> dict[str, Any] | None:
    """
    Read and schema-check the raw cache JSON, or None when unusable.
    """
    try:
        text = cache_file(vault_root).read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return None
    return data


def _to_relative(vault_root: Path, path: Path) -> str:
    """
    Convert a path under the vault to its stored POSIX-relative form.
    """
    return str(PurePosixPath(*Path(path).relative_to(vault_root).parts))


def _serialize_index(index: Index, vault_root: Path) -> dict[str, Any]:
    """
    Convert an index to its JSON-ready dict form.

    Backlinks and the title/alias indexes are derived state and are not
    stored — :func:`_deserialize_index` rebuilds them from the notes and
    resolved links.
    """
    notes: list[dict[str, Any]] = []
    for path, note in index.notes_by_path.items():
        notes.append(
            {
                "path": _to_relative(vault_root, path),
                "aliases": list(note.aliases),
                "headings": list(note.headings),
                "block_ids": list(note.block_ids),
                "frontmatter": _to_plain(note.frontmatter),
                "word_count": note.word_count,
                "mtime": note.mtime,
                "tags": sorted(index.note_tags.get(path, set())),
                "links": [
                    {
                        "target": link.target,
                        "anchor": link.anchor,
                        "display": link.display,
                        "is_embed": link.is_embed,
                        "is_wikilink": link.is_wikilink,
                        "resolved": (
                            _to_relative(vault_root, link.resolved)
                            if link.resolved is not None
                            else None
                        ),
                    }
                    for link in index.links.get(path, [])
                ],
            }
        )
    return {
        "notes": notes,
        "attachments": [_to_relative(vault_root, path) for path in index._attachments],
    }


def _to_plain(value: Any) -> Any:
    """
    Recursively convert ruamel container types to builtin dicts/lists.

    Scalar leaves are left as-is; anything ``json`` cannot encode is
    stringified by the ``default=str`` hook at dump time.
    """
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _deserialize_index(data: dict[str, Any], vault_root: Path) -> Index:
    """
    Reconstruct an :class:`Index` from its serialized dict form.

    Links are stored already resolved, so no vault file is read and no
    resolver pass runs; the derived maps (``notes``, backlinks, and the
    title/alias indexes) are rebuilt through the index's own insertion
    logic.

    :raises Exception: On any structural mismatch — the caller treats
        it as "no cache".
    """
    index = Index(vault_root)
    for entry in data["notes"]:
        path = vault_root / PurePosixPath(entry["path"])
        note = Note(
            title=path.stem,
            path=path,
            aliases=list(entry["aliases"]),
            headings=list(entry["headings"]),
            block_ids=list(entry["block_ids"]),
            frontmatter=dict(entry["frontmatter"]),
            word_count=int(entry["word_count"]),
            mtime=float(entry["mtime"]),
        )
        links = [
            Link(
                source=path,
                target=link["target"],
                anchor=link["anchor"],
                display=link["display"],
                is_embed=bool(link["is_embed"]),
                is_wikilink=bool(link["is_wikilink"]),
                resolved=(
                    vault_root / PurePosixPath(link["resolved"])
                    if link["resolved"] is not None
                    else None
                ),
            )
            for link in entry["links"]
        ]
        index._add_note(note, links, set(entry["tags"]))
    index._attachments = [
        vault_root / PurePosixPath(relpath) for relpath in data["attachments"]
    ]
    index._rebuild_backlinks()
    return index
