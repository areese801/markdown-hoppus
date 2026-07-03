"""
Filesystem CRUD for notes and folders (spec §9.2, HOPPUS-34).

Pure(-ish) helpers the TUI calls to create, rename, move, and delete
vault entries. Every operation validates names via :mod:`hoppus.naming`,
refuses collisions loudly (never silently overwriting or merging), and
returns the resulting path. Rename/move compute a
:class:`~hoppus.index.propagation.RenamePlan` and — unless the caller
opts out — rewrite every inbound link across the vault (spec §6.2).
Link-prompt gating (``files.prompt_before_link_update``) is the UI
layer's job; these functions just honor an already-decided
``apply_links`` flag.

All functions take explicit paths/indexes so they unit-test cleanly
against temp directories. ``.hoppus/`` and ``.obsidian/`` are never
touched (spec §5.2).
"""

import shutil
from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.index.propagation import RenamePlan, apply_plan, plan_rename
from hoppus.naming import validate_note_name

_MD_SUFFIX = ".md"
_PROTECTED_DIRS = frozenset({".hoppus", ".obsidian"})


def _checked_name(name: str) -> str:
    """
    Strip and validate a proposed name, raising on reserved tokens.

    :param name: The proposed note/folder name.
    :returns: The stripped name.
    :raises ValueError: If the name is empty or contains reserved
        characters/substrings (spec §5.2).
    """
    stripped = name.strip()
    problems = validate_note_name(stripped)
    if problems:
        raise ValueError(f"Invalid name {name!r}: reserved {' '.join(problems)}")
    return stripped


def create_note(vault_root: Path, name: str) -> Path:
    """
    Create an empty note at the vault root (GTD convention, spec §5.2).

    :param vault_root: The vault root directory.
    :param name: The note title (``.md`` is appended).
    :returns: The path of the created note.
    :raises ValueError: If the name is invalid.
    :raises FileExistsError: If a file with that name already exists.
    """
    stem = _checked_name(name)
    target = vault_root / f"{stem}{_MD_SUFFIX}"
    if target.exists():
        raise FileExistsError(f"Note already exists: {target.name}")
    target.write_text("", encoding="utf-8")
    return target


def create_folder(parent: Path, name: str) -> Path:
    """
    Create a folder under a parent directory.

    :param parent: The directory to create the folder in.
    :param name: The folder name.
    :returns: The path of the created folder.
    :raises ValueError: If the name is invalid.
    :raises FileExistsError: If an entry with that name already exists.
    """
    target = parent / _checked_name(name)
    if target.exists():
        raise FileExistsError(f"Folder already exists: {target.name}")
    target.mkdir()
    return target


def rename_note(
    old_path: Path,
    new_name: str,
    index: Index,
    vault_root: Path,
    *,
    apply_links: bool = True,
) -> tuple[Path, RenamePlan]:
    """
    Rename a note in place, rewriting inbound links (spec §6.2).

    The note stays in its directory under a new validated stem. The
    link rewrites are applied *before* the file moves, because the
    plan's changes are keyed by pre-rename paths.

    :param old_path: The note's current path.
    :param new_name: The new title (``.md`` is appended).
    :param index: The current vault index (pre-rename state).
    :param vault_root: The vault root directory.
    :param apply_links: When False, skip the inbound-link rewrites
        (the plan is still returned for inspection).
    :returns: ``(new_path, plan)``.
    :raises ValueError: If the name is invalid or unchanged.
    :raises FileExistsError: If the target name is already taken.
    """
    stem = _checked_name(new_name)
    new_path = old_path.with_name(f"{stem}{_MD_SUFFIX}")
    if new_path == old_path:
        raise ValueError(f"Name unchanged: {old_path.stem}")
    if new_path.exists():
        raise FileExistsError(f"Note already exists: {new_path.name}")
    plan = plan_rename(
        old_path,
        new_path,
        index.notes_by_path.values(),
        vault_root,
        attachments=index._attachments,
    )
    if apply_links:
        apply_plan(plan)
    old_path.rename(new_path)
    return new_path, plan


def move_note(
    old_path: Path,
    dest_dir: Path,
    index: Index,
    vault_root: Path,
    *,
    apply_links: bool = True,
) -> tuple[Path, RenamePlan]:
    """
    Move a note (same name) into another folder, propagating links.

    :param old_path: The note's current path.
    :param dest_dir: The destination directory (must exist).
    :param index: The current vault index (pre-move state).
    :param vault_root: The vault root directory.
    :param apply_links: When False, skip the inbound-link rewrites.
    :returns: ``(new_path, plan)``.
    :raises ValueError: If the destination is not a directory or is
        the note's current directory.
    :raises FileExistsError: If the destination already has a file
        with the note's name.
    """
    if not dest_dir.is_dir():
        raise ValueError(f"Not a folder: {dest_dir}")
    new_path = dest_dir / old_path.name
    if new_path == old_path:
        raise ValueError(f"Note is already in {dest_dir.name or dest_dir}")
    if new_path.exists():
        raise FileExistsError(f"Note already exists: {new_path}")
    plan = plan_rename(
        old_path,
        new_path,
        index.notes_by_path.values(),
        vault_root,
        attachments=index._attachments,
    )
    if apply_links:
        apply_plan(plan)
    old_path.rename(new_path)
    return new_path, plan


def delete_path(path: Path) -> None:
    """
    Delete a note file, or a folder recursively.

    ``.hoppus/`` and ``.obsidian/`` (and anything inside them) are
    foreign/state directories and are never deleted (spec §5.2).

    :param path: The file or directory to delete.
    :raises ValueError: If the path is, or sits inside, a protected
        directory.
    :raises FileNotFoundError: If the path does not exist.
    """
    if any(part in _PROTECTED_DIRS for part in path.parts):
        raise ValueError(f"Refusing to delete protected state: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    else:
        raise FileNotFoundError(f"No such file or folder: {path}")
