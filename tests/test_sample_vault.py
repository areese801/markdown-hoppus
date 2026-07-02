"""
Structural sanity checks for the shared ``sample_vault`` fixture (HOPPUS-64).
"""

from pathlib import Path


def test_vault_dir_exists(sample_vault: Path) -> None:
    """
    Assert the vault root exists and is a directory.
    """
    assert sample_vault.is_dir()


def test_duplicate_title_notes_exist(sample_vault: Path) -> None:
    """
    Assert both same-filename notes exist in different folders.
    """
    assert (sample_vault / "Projects" / "Alpha.md").is_file()
    assert (sample_vault / "Archive" / "Alpha.md").is_file()


def test_obsidian_dir_exists(sample_vault: Path) -> None:
    """
    Assert the foreign ``.obsidian/`` state directory and token file exist.
    """
    assert (sample_vault / ".obsidian").is_dir()
    assert (sample_vault / ".obsidian" / "app.json").is_file()


def test_attachment_exists(sample_vault: Path) -> None:
    """
    Assert the embedded attachment file exists and is non-empty.
    """
    image = sample_vault / "attachments" / "image.png"
    assert image.is_file()
    assert image.stat().st_size >= 1
