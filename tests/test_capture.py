"""
Pure unit tests for quick capture (spec §9.11, HOPPUS-51).

Everything runs against temp vaults with a fixed injected
``datetime`` — no real clock, no real config files.
"""

from datetime import datetime
from pathlib import Path

import pytest

from hoppus.capture import capture_filename, capture_note

FIXED_NOW = datetime(2026, 7, 3, 14, 30, 59)


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    """
    Create a temp vault root directory.

    Args:
        tmp_path: pytest's per-test temp directory.

    Returns:
        The vault root path.
    """
    root = tmp_path / "Personal"
    root.mkdir()
    return root


def test_capture_filename_timestamp_stem_has_no_colon() -> None:
    """
    The default timestamp stem is colon-free (``:`` is reserved).
    """
    stem = capture_filename(now=FIXED_NOW)
    assert stem == "2026-07-03 143059"
    assert ":" not in stem


def test_capture_filename_uses_title_when_provided() -> None:
    """
    A non-empty title becomes the stem (stripped), ignoring the clock.
    """
    assert capture_filename(now=FIXED_NOW, title="Meeting notes") == "Meeting notes"
    assert capture_filename(now=FIXED_NOW, title="  padded  ") == "padded"


def test_capture_filename_blank_title_falls_back_to_timestamp() -> None:
    """
    An empty or whitespace-only title yields the timestamp stem.
    """
    assert capture_filename(now=FIXED_NOW, title="") == "2026-07-03 143059"
    assert capture_filename(now=FIXED_NOW, title="   ") == "2026-07-03 143059"


def test_capture_note_dot_folder_lands_at_vault_root(vault_root: Path) -> None:
    """
    ``inbox.folder: "."`` (GTD default) drops the note at the vault root.
    """
    config = {"inbox": {"folder": "."}}
    path = capture_note(vault_root, config, text="hello", now=FIXED_NOW)
    assert path == vault_root / "2026-07-03 143059.md"
    assert path.parent == vault_root
    assert path.read_text(encoding="utf-8") == "hello"


def test_capture_note_custom_inbox_folder_is_created_and_used(
    vault_root: Path,
) -> None:
    """
    A custom ``inbox.folder`` is created if missing and receives the note.
    """
    config = {"inbox": {"folder": "Inbox"}}
    assert not (vault_root / "Inbox").exists()
    path = capture_note(vault_root, config, text="idea", now=FIXED_NOW)
    assert path == vault_root / "Inbox" / "2026-07-03 143059.md"
    assert path.read_text(encoding="utf-8") == "idea"


def test_capture_note_missing_inbox_config_defaults_to_root(vault_root: Path) -> None:
    """
    A config without an ``inbox`` section falls back to the vault root.
    """
    path = capture_note(vault_root, {}, now=FIXED_NOW)
    assert path.parent == vault_root


def test_capture_note_title_stem_and_empty_body(vault_root: Path) -> None:
    """
    A provided title names the note; an omitted body writes empty text.
    """
    config = {"inbox": {"folder": "."}}
    path = capture_note(vault_root, config, title="Groceries", now=FIXED_NOW)
    assert path == vault_root / "Groceries.md"
    assert path.read_text(encoding="utf-8") == ""


def test_capture_note_reserved_char_title_raises(vault_root: Path) -> None:
    """
    A title containing reserved tokens (spec §5.2) raises ValueError.
    """
    config = {"inbox": {"folder": "."}}
    with pytest.raises(ValueError, match="Invalid note name"):
        capture_note(vault_root, config, title="a:b", now=FIXED_NOW)
    assert list(vault_root.iterdir()) == []


def test_capture_note_collision_raises_file_exists(vault_root: Path) -> None:
    """
    An existing note with the same name is refused, never overwritten.
    """
    config = {"inbox": {"folder": "."}}
    first = capture_note(vault_root, config, text="first", now=FIXED_NOW)
    with pytest.raises(FileExistsError):
        capture_note(vault_root, config, text="second", now=FIXED_NOW)
    assert first.read_text(encoding="utf-8") == "first"
