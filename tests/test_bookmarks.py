"""
Pure persistence tests for bookmarks / starred notes (spec §9.12,
HOPPUS-52).

Exercises ``hoppus.bookmarks`` against temp vaults: toggle semantics,
persistence across fresh loads, graceful degradation over missing or
malformed files, the vault-relative POSIX on-disk format, and de-dupe.
"""

from pathlib import Path

from hoppus.bookmarks import (
    is_bookmarked,
    load_bookmarks,
    save_bookmarks,
    toggle_bookmark,
)


def make_vault(tmp_path: Path) -> Path:
    """
    Create a minimal vault with two notes (one nested).

    :param tmp_path: pytest temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Alpha.md").write_text("Alpha body.\n", encoding="utf-8")
    (vault / "Projects" / "Beta.md").write_text("Beta body.\n", encoding="utf-8")
    return vault


def test_toggle_adds_then_removes(tmp_path: Path) -> None:
    """
    ``toggle_bookmark`` adds when absent, removes when present, and
    returns the new state each time.
    """
    vault = make_vault(tmp_path)
    note = vault / "Alpha.md"
    assert not is_bookmarked(vault, note)
    assert toggle_bookmark(vault, note) is True
    assert is_bookmarked(vault, note)
    assert load_bookmarks(vault) == [note]
    assert toggle_bookmark(vault, note) is False
    assert not is_bookmarked(vault, note)
    assert load_bookmarks(vault) == []


def test_persistence_across_fresh_load(tmp_path: Path) -> None:
    """
    Saved bookmarks survive a fresh read from disk, in order.
    """
    vault = make_vault(tmp_path)
    alpha = vault / "Alpha.md"
    beta = vault / "Projects" / "Beta.md"
    save_bookmarks(vault, [beta, alpha])
    assert load_bookmarks(vault) == [beta, alpha]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """
    A vault with no ``.hoppus/bookmarks.yaml`` loads as no bookmarks.
    """
    vault = make_vault(tmp_path)
    assert load_bookmarks(vault) == []


def test_malformed_file_returns_empty(tmp_path: Path) -> None:
    """
    A corrupt bookmarks file degrades to an empty list without raising.
    """
    vault = make_vault(tmp_path)
    state = vault / ".hoppus"
    state.mkdir()
    file = state / "bookmarks.yaml"
    for garbage in ("{{{: not yaml [", "just a string", "bookmarks: 42", ""):
        file.write_text(garbage, encoding="utf-8")
        assert load_bookmarks(vault) == []
    # Toggling on top of a corrupt file starts fresh instead of raising.
    file.write_text("{{{: not yaml [", encoding="utf-8")
    assert toggle_bookmark(vault, vault / "Alpha.md") is True
    assert load_bookmarks(vault) == [vault / "Alpha.md"]


def test_stored_paths_are_vault_relative_posix(tmp_path: Path) -> None:
    """
    The on-disk format stores vault-relative POSIX strings.
    """
    vault = make_vault(tmp_path)
    save_bookmarks(vault, [vault / "Projects" / "Beta.md"])
    text = (vault / ".hoppus" / "bookmarks.yaml").read_text(encoding="utf-8")
    assert "Projects/Beta.md" in text
    assert str(vault) not in text
    assert "\\" not in text


def test_load_dedupes_and_drops_empty_entries(tmp_path: Path) -> None:
    """
    Duplicate entries collapse to the first occurrence; empty or
    non-string entries are dropped.
    """
    vault = make_vault(tmp_path)
    state = vault / ".hoppus"
    state.mkdir()
    (state / "bookmarks.yaml").write_text(
        "bookmarks:\n"
        "  - Projects/Beta.md\n"
        "  - Alpha.md\n"
        "  - Projects/Beta.md\n"
        "  - ''\n"
        "  - 7\n",
        encoding="utf-8",
    )
    assert load_bookmarks(vault) == [
        vault / "Projects" / "Beta.md",
        vault / "Alpha.md",
    ]


def test_toggle_creates_state_dir(tmp_path: Path) -> None:
    """
    The ``.hoppus/`` state directory is created on first write.
    """
    vault = make_vault(tmp_path)
    assert not (vault / ".hoppus").exists()
    toggle_bookmark(vault, vault / "Alpha.md")
    assert (vault / ".hoppus" / "bookmarks.yaml").is_file()
