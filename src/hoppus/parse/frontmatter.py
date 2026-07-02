"""
YAML frontmatter parsing and round-trip-preserving writes via ruamel.yaml
(spec §6.5, decision D4).

Frontmatter is a YAML block delimited by ``---`` lines at the very top of
a note. Recognized keys are ``aliases`` and ``tags``, but arbitrary user
keys are always preserved (spec §6.5). Writes go through ruamel.yaml in
round-trip mode so that updating a single key leaves every other line of
the note byte-identical (spec §14, decision D4).
"""

import io
import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

_DELIMITER = "---"


def _make_yaml() -> YAML:
    """
    Build a ruamel YAML instance configured for round-trip preservation.

    Round-trip (``typ="rt"``) mode keeps key order and comments;
    ``preserve_quotes`` keeps original scalar quoting; the indent settings
    match Obsidian's conventional ``  - item`` sequence style; a very
    large width prevents re-wrapping of long scalar lines.

    :returns: A configured ``YAML`` instance.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 2**16
    return yaml


def _split_raw(text: str) -> tuple[str | None, str]:
    """
    Split note text into the raw frontmatter YAML string and the body.

    :param text: Full note text.
    :returns: ``(raw_yaml, body)`` where ``raw_yaml`` is the text between
        the ``---`` delimiters (excluding them), or ``None`` when the note
        has no frontmatter block; ``body`` is everything after the closing
        delimiter line (or the full text when there is no frontmatter).
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _DELIMITER:
        return None, text
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == _DELIMITER:
            raw = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return raw, body
    return None, text


def split_frontmatter(text: str) -> tuple[CommentedMap, str]:
    """
    Parse a note's leading YAML frontmatter block.

    Handles notes with no frontmatter (empty mapping, full text returned
    as body), an empty frontmatter block, and standard frontmatter.

    :param text: Full note text.
    :returns: ``(frontmatter, body)`` where ``frontmatter`` is a
        round-trip-capable mapping (empty when absent) and ``body`` is the
        note text after the closing delimiter.
    """
    raw, body = _split_raw(text)
    if raw is None:
        return CommentedMap(), body
    data = _make_yaml().load(raw)
    if data is None:
        return CommentedMap(), body
    return data, body


def update_frontmatter_key(text: str, key: str, value: Any) -> str:
    """
    Set one frontmatter key and re-emit the full note text.

    Uses ruamel.yaml round-trip mode (decision D4) so key order, quoting,
    comments, and blank lines of untouched frontmatter — and the entire
    body — are preserved byte-for-byte. When the note has no frontmatter
    block, one is created at the top.

    :param text: Full note text.
    :param key: Frontmatter key to set or replace.
    :param value: New value for the key.
    :returns: The full note text with only that key changed.
    """
    raw, body = _split_raw(text)
    yaml = _make_yaml()
    data = yaml.load(raw) if raw is not None else None
    if data is None:
        data = CommentedMap()
    data[key] = value
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    return f"{_DELIMITER}\n{buffer.getvalue()}{_DELIMITER}\n{body}"


def get_aliases(frontmatter: CommentedMap) -> list[str]:
    """
    Return the ``aliases`` frontmatter key as a list of strings.

    Accepts either a YAML list or a single string value.

    :param frontmatter: Parsed frontmatter mapping.
    :returns: Alias names, or an empty list when absent.
    """
    raw = frontmatter.get("aliases")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]


def get_tags(frontmatter: CommentedMap) -> list[str]:
    """
    Return the ``tags`` frontmatter key as a list of strings.

    Handles both a YAML list and a space/comma-delimited string form
    (spec §6.4).

    :param frontmatter: Parsed frontmatter mapping.
    :returns: Tag names, or an empty list when absent.
    """
    raw = frontmatter.get("tags")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [tag for tag in re.split(r"[,\s]+", raw) if tag]
    return [str(item) for item in raw]
