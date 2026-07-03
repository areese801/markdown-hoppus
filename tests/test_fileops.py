"""
Tests for filesystem CRUD (HOPPUS-34): create at the vault root (GTD,
spec §5.2), collision refusal, rename/move with inbound-link
propagation (spec §6.2), and protected-state deletion guards.
"""

from pathlib import Path

import pytest

from hoppus.fileops import (
    create_folder,
    create_note,
    delete_path,
    move_note,
    rename_note,
)
from hoppus.index.indexer import Index

_TARGET_MD = """\
# Target

Target content.
"""

_LINKER_MD = """\
# Linker

A link to [[Target]] and one with display [[Target|the target]].
"""


def make_vault(tmp_path: Path) -> Path:
    """
    Build a tiny vault: a link target, a linker, and dot-state dirs.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "Target.md").write_text(_TARGET_MD, encoding="utf-8")
    (vault / "Linker.md").write_text(_LINKER_MD, encoding="utf-8")
    (vault / "Sub").mkdir()
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (vault / ".hoppus").mkdir()
    return vault


# -- create ---------------------------------------------------------------


def test_create_note_lands_at_vault_root(tmp_path: Path) -> None:
    """
    New notes are created empty at the vault root (GTD, spec §5.2).
    """
    vault = make_vault(tmp_path)
    path = create_note(vault, "Inbox note")
    assert path == vault / "Inbox note.md"
    assert path.read_text(encoding="utf-8") == ""


def test_create_note_collision_raises(tmp_path: Path) -> None:
    """
    Creating over an existing note raises, never overwrites.
    """
    vault = make_vault(tmp_path)
    with pytest.raises(FileExistsError):
        create_note(vault, "Target")
    assert (vault / "Target.md").read_text(encoding="utf-8") == _TARGET_MD


def test_create_note_reserved_name_raises(tmp_path: Path) -> None:
    """
    Reserved characters in the name raise ValueError (spec §5.2).
    """
    vault = make_vault(tmp_path)
    with pytest.raises(ValueError):
        create_note(vault, "Bad|Name")
    with pytest.raises(ValueError):
        create_note(vault, "   ")


def test_create_folder(tmp_path: Path) -> None:
    """
    Folders are created under the given parent; collisions raise.
    """
    vault = make_vault(tmp_path)
    path = create_folder(vault, "Projects")
    assert path.is_dir()
    with pytest.raises(FileExistsError):
        create_folder(vault, "Projects")
    with pytest.raises(ValueError):
        create_folder(vault, "Bad:Name")


# -- rename ---------------------------------------------------------------


def test_rename_moves_file_and_rewrites_links(tmp_path: Path) -> None:
    """
    Rename moves the note in place and rewrites inbound wikilinks.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    new_path, plan = rename_note(vault / "Target.md", "Renamed", index, vault)
    assert new_path == vault / "Renamed.md"
    assert new_path.is_file()
    assert not (vault / "Target.md").exists()
    linker = (vault / "Linker.md").read_text(encoding="utf-8")
    assert "[[Renamed]]" in linker
    assert "[[Renamed|the target]]" in linker
    assert "[[Target]]" not in linker
    assert plan.link_count == 2


def test_rename_without_apply_links_leaves_links_alone(tmp_path: Path) -> None:
    """
    ``apply_links=False`` moves the file but keeps inbound links.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    new_path, plan = rename_note(
        vault / "Target.md", "Renamed", index, vault, apply_links=False
    )
    assert new_path.is_file()
    assert (vault / "Linker.md").read_text(encoding="utf-8") == _LINKER_MD
    assert plan.link_count == 2


def test_rename_into_existing_name_refused(tmp_path: Path) -> None:
    """
    Renaming onto an existing note raises, never overwrites.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    with pytest.raises(FileExistsError):
        rename_note(vault / "Target.md", "Linker", index, vault)
    assert (vault / "Target.md").is_file()


def test_rename_invalid_or_unchanged_name_refused(tmp_path: Path) -> None:
    """
    Reserved characters and no-op renames raise ValueError.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    with pytest.raises(ValueError):
        rename_note(vault / "Target.md", "Bad#Name", index, vault)
    with pytest.raises(ValueError):
        rename_note(vault / "Target.md", "Target", index, vault)


# -- move -----------------------------------------------------------------


def test_move_into_subfolder(tmp_path: Path) -> None:
    """
    Move keeps the name, relocates the file, and links still resolve.
    """
    vault = make_vault(tmp_path)
    index = Index.build(vault)
    new_path, _plan = move_note(vault / "Target.md", vault / "Sub", index, vault)
    assert new_path == vault / "Sub" / "Target.md"
    assert new_path.is_file()
    assert not (vault / "Target.md").exists()
    rebuilt = Index.build(vault)
    links = rebuilt.links[vault / "Linker.md"]
    assert all(link.resolved == new_path for link in links)


def test_move_collision_and_bad_destination_refused(tmp_path: Path) -> None:
    """
    Moving onto an existing file or into a non-folder raises.
    """
    vault = make_vault(tmp_path)
    (vault / "Sub" / "Target.md").write_text("occupied\n", encoding="utf-8")
    index = Index.build(vault)
    with pytest.raises(FileExistsError):
        move_note(vault / "Target.md", vault / "Sub", index, vault)
    with pytest.raises(ValueError):
        move_note(vault / "Target.md", vault / "Nope", index, vault)
    with pytest.raises(ValueError):
        move_note(vault / "Target.md", vault, index, vault)


# -- delete ---------------------------------------------------------------


def test_delete_note_and_folder(tmp_path: Path) -> None:
    """
    ``delete_path`` removes a note file and a folder recursively.
    """
    vault = make_vault(tmp_path)
    (vault / "Sub" / "Nested.md").write_text("nested\n", encoding="utf-8")
    delete_path(vault / "Linker.md")
    assert not (vault / "Linker.md").exists()
    delete_path(vault / "Sub")
    assert not (vault / "Sub").exists()
    with pytest.raises(FileNotFoundError):
        delete_path(vault / "Linker.md")


def test_delete_never_touches_protected_state(tmp_path: Path) -> None:
    """
    ``.obsidian``/``.hoppus`` (and their contents) are never deleted.
    """
    vault = make_vault(tmp_path)
    for target in (
        vault / ".obsidian",
        vault / ".hoppus",
        vault / ".obsidian" / "app.json",
    ):
        with pytest.raises(ValueError):
            delete_path(target)
    assert (vault / ".obsidian" / "app.json").is_file()
    assert (vault / ".hoppus").is_dir()
