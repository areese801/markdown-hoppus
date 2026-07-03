"""
Bookmarks / starred notes persistence (spec §9.12, HOPPUS-52).

Bookmarks live in ``<vault>/.hoppus/bookmarks.yaml`` — the vault's own
hoppus state directory (spec §5.2), never synced to Obsidian. The file
is a YAML document with a single top-level key::

    bookmarks:
      - Projects/Alpha.md
      - Inbox.md

Entries are VAULT-RELATIVE POSIX path strings so the file stays stable
and portable across machines and operating systems. All functions are
pure filesystem operations (no TUI coupling) and degrade gracefully: a
missing, empty, or malformed file reads as "no bookmarks" and never
raises.
"""

from pathlib import Path, PurePosixPath

from ruamel.yaml import YAML

_STATE_DIR = ".hoppus"
_FILENAME = "bookmarks.yaml"
_KEY = "bookmarks"


def _bookmarks_file(vault_root: Path) -> Path:
    """
    Return the path of the vault's bookmarks file.

    :param vault_root: The vault root directory.
    :returns: ``<vault_root>/.hoppus/bookmarks.yaml``.
    """
    return Path(vault_root) / _STATE_DIR / _FILENAME


def _to_relative(vault_root: Path, note_path: Path) -> str:
    """
    Convert a note path to its stored vault-relative POSIX form.

    Paths already relative are kept as-is (normalized to POSIX).

    :param vault_root: The vault root directory.
    :param note_path: Absolute or vault-relative note path.
    :returns: The vault-relative POSIX path string.
    """
    path = Path(note_path)
    if path.is_absolute():
        path = path.relative_to(Path(vault_root))
    return str(PurePosixPath(*path.parts))


def load_bookmarks(vault_root: Path) -> list[Path]:
    """
    Read the vault's bookmarks, returning absolute note paths.

    Preserves the stored order, drops empty entries, and de-dupes
    keeping the first occurrence. A missing, empty, or malformed file
    yields an empty list — this function never raises over bad state.

    :param vault_root: The vault root directory.
    :returns: Absolute ``Path``s of the bookmarked notes, in order.
    """
    file = _bookmarks_file(vault_root)
    try:
        text = file.read_text(encoding="utf-8")
        data = YAML(typ="safe").load(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get(_KEY)
    if not isinstance(entries, list):
        return []
    root = Path(vault_root)
    seen: set[str] = set()
    paths: list[Path] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            continue
        if entry in seen:
            continue
        seen.add(entry)
        paths.append(root / PurePosixPath(entry))
    return paths


def save_bookmarks(vault_root: Path, paths: list[Path]) -> None:
    """
    Write the bookmark list to ``<vault>/.hoppus/bookmarks.yaml``.

    Paths are stored as vault-relative POSIX strings. The ``.hoppus/``
    state directory is created when absent.

    :param vault_root: The vault root directory.
    :param paths: Note paths (absolute or vault-relative) to persist.
    """
    file = _bookmarks_file(vault_root)
    file.parent.mkdir(parents=True, exist_ok=True)
    entries = [_to_relative(vault_root, path) for path in paths]
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    with file.open("w", encoding="utf-8") as handle:
        yaml.dump({_KEY: entries}, handle)


def is_bookmarked(vault_root: Path, note_path: Path) -> bool:
    """
    Report whether a note is currently bookmarked.

    :param vault_root: The vault root directory.
    :param note_path: Absolute or vault-relative note path.
    :returns: True when the note is in the bookmark list.
    """
    target = _to_relative(vault_root, note_path)
    return any(
        _to_relative(vault_root, path) == target for path in load_bookmarks(vault_root)
    )


def toggle_bookmark(vault_root: Path, note_path: Path) -> bool:
    """
    Star or unstar a note, persisting the change immediately.

    Adds the note when absent (appended at the end) and removes it when
    present. Safe against duplicates in the stored file — a removal
    drops every occurrence.

    :param vault_root: The vault root directory.
    :param note_path: Absolute or vault-relative note path.
    :returns: The NEW state: True when the note is now bookmarked.
    """
    target = _to_relative(vault_root, note_path)
    current = load_bookmarks(vault_root)
    remaining = [path for path in current if _to_relative(vault_root, path) != target]
    if len(remaining) == len(current):
        remaining.append(Path(vault_root) / PurePosixPath(target))
        now_bookmarked = True
    else:
        now_bookmarked = False
    save_bookmarks(vault_root, remaining)
    return now_bookmarked
