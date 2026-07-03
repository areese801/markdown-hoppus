"""
Tests for note-name validation (spec §5.2 reserved characters,
HOPPUS-34): every reserved token is flagged, clean names pass, and
empty/whitespace/path-separator names are rejected.
"""

import pytest

from hoppus.naming import (
    EMPTY_NAME,
    RESERVED_CHARS,
    RESERVED_SUBSTRINGS,
    validate_note_name,
)


@pytest.mark.parametrize("char", RESERVED_CHARS)
def test_each_reserved_char_is_flagged(char: str) -> None:
    """
    Each of ``# | ^ :`` anywhere in a name is reported.
    """
    assert validate_note_name(f"My{char}Note") == [char]


@pytest.mark.parametrize("token", RESERVED_SUBSTRINGS)
def test_each_reserved_substring_is_flagged(token: str) -> None:
    """
    Each of ``%%``, ``[[``, ``]]`` as a substring is reported.
    """
    assert validate_note_name(f"My{token}Note") == [token]


def test_single_brackets_and_percent_are_allowed() -> None:
    """
    ``%``, ``[``, and ``]`` are only reserved when doubled.
    """
    assert validate_note_name("Q1 [draft] 50% done") == []


def test_multiple_problems_all_reported() -> None:
    """
    Every reserved token present appears in the result.
    """
    problems = validate_note_name("a#b|c%%d")
    assert set(problems) == {"#", "|", "%%"}


@pytest.mark.parametrize(
    "name", ["Inbox note", "2026-07-03 Standup", "John Doe", "Alpha (Projects)"]
)
def test_clean_names_pass(name: str) -> None:
    """
    Ordinary Obsidian-style titles validate cleanly.
    """
    assert validate_note_name(name) == []


@pytest.mark.parametrize("name", ["", " ", "\t", "   \n"])
def test_empty_and_whitespace_rejected(name: str) -> None:
    """
    Empty or whitespace-only names yield the empty-name sentinel.
    """
    assert validate_note_name(name) == [EMPTY_NAME]


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_path_separators_rejected(separator: str) -> None:
    """
    Names must be bare stems: no directory components.
    """
    assert separator in validate_note_name(f"Folder{separator}Note")
