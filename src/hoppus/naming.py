"""
Note/folder name validation (spec §5.2, HOPPUS-34).

Obsidian reserves a small set of characters in filenames and link
targets: ``# | ^ : %% [[ ]]``. The single characters ``#``, ``|``,
``^``, and ``:`` are reserved anywhere in a name; ``%%``, ``[[``, and
``]]`` are reserved as substrings (single ``%``, ``[``, and ``]``
characters are fine). Names must also be non-empty (after stripping
whitespace) and must not contain path separators — creating nested
paths is the file explorer's job, not the name's.

This module is pure: no filesystem access, no TUI imports.
"""

RESERVED_CHARS: tuple[str, ...] = ("#", "|", "^", ":")
"""Single characters Obsidian reserves in note/folder names."""

RESERVED_SUBSTRINGS: tuple[str, ...] = ("%%", "[[", "]]")
"""Multi-character sequences Obsidian reserves in note/folder names."""

_PATH_SEPARATORS: tuple[str, ...] = ("/", "\\")

EMPTY_NAME = "(empty name)"
"""Sentinel token returned for empty or whitespace-only names."""


def validate_note_name(name: str) -> list[str]:
    """
    Check a proposed note/folder name against the reserved-token rules.

    :param name: The proposed name (a bare stem — no ``.md`` extension,
        no directory components).
    :returns: The reserved tokens present in the name, in a stable
        order (substrings, then characters, then separators). Empty or
        whitespace-only names yield ``[EMPTY_NAME]``. An empty list
        means the name is valid.
    """
    if not name.strip():
        return [EMPTY_NAME]
    problems: list[str] = []
    for token in RESERVED_SUBSTRINGS:
        if token in name:
            problems.append(token)
    for char in RESERVED_CHARS:
        if char in name:
            problems.append(char)
    for separator in _PATH_SEPARATORS:
        if separator in name:
            problems.append(separator)
    return problems
