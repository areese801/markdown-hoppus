"""
Pure append/dedupe logic for the ``## Related`` link picker (spec §7.2,
HOPPUS-41).

The reading-view link picker appends deliberate connections as
``- [[Target]]`` bullets under a configurable ``## Related`` heading.
This module owns the text transformation only — no Textual imports, no
filesystem access — so the TUI stays thin and the rules unit-test
cleanly.

Section boundaries: the ``## Related`` section starts at the first line
whose trimmed text equals the heading and runs until the next heading of
the same or higher level (fewer or equal ``#`` characters), or
end-of-file. New bullets are inserted after the last non-blank line of
that section, so blank separator lines before the next heading are
preserved.

Dedupe: a wikilink already present anywhere in the section blocks the
append. Links are compared on their resolved target text — ``[[T]]``,
``[[T|display]]``, and ``[[T#anchor]]`` all resolve to ``T``.
"""

import re

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s")


def _heading_level(line: str) -> int | None:
    """
    Return the ``#`` count of a hash-style heading line, or None.

    :param line: A single line of markdown.
    :returns: The heading level (1–6), or None for a non-heading line.
    """
    match = _HEADING_RE.match(line.strip())
    return len(match.group(1)) if match else None


def _resolve_target(link_body: str) -> str:
    """
    Reduce a wikilink body to its bare target for dedupe comparison.

    Strips ``|display`` and ``#anchor`` suffixes, so ``T``, ``T|x``,
    and ``T#a`` all resolve to ``T``.

    :param link_body: The text between ``[[`` and ``]]``.
    :returns: The resolved target text, stripped.
    """
    body = link_body.split("|", 1)[0]
    body = body.split("#", 1)[0]
    return body.strip()


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    """
    Locate the heading's section within pre-split lines.

    :param lines: The file text split on newlines.
    :param heading: The heading line to match (trimmed, exact).
    :returns: ``(heading_index, end_index)`` where ``end_index`` is the
        index of the next same-or-higher-level heading (or
        ``len(lines)``), or None when the heading is absent.
    """
    wanted = heading.strip()
    heading_index = next(
        (i for i, line in enumerate(lines) if line.strip() == wanted), None
    )
    if heading_index is None:
        return None
    # A non-hash heading string can't be leveled; treat any subsequent
    # heading as a section boundary in that case.
    section_level = _heading_level(wanted) or 7
    end = len(lines)
    for j in range(heading_index + 1, len(lines)):
        level = _heading_level(lines[j])
        if level is not None and level <= section_level:
            end = j
            break
    return heading_index, end


def related_link_targets(text: str, *, heading: str = "## Related") -> set[str]:
    """
    Return the wikilink targets already present under the heading.

    Targets are resolved per :func:`_resolve_target`, so display-text
    and anchor variants collapse onto the bare target.

    :param text: The full note text.
    :param heading: The heading line marking the section.
    :returns: The set of resolved targets in the section (empty when
        the heading is absent).
    """
    lines = text.split("\n")
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        return set()
    heading_index, end = bounds
    targets: set[str] = set()
    for line in lines[heading_index + 1 : end]:
        for match in _WIKILINK_RE.finditer(line):
            resolved = _resolve_target(match.group(1))
            if resolved:
                targets.add(resolved)
    return targets


def add_related_link(text: str, target: str, *, heading: str = "## Related") -> str:
    """
    Append ``- [[target]]`` under the first ``heading`` section.

    Rules (spec §7.2):

    - No such heading: append the heading and bullet at end-of-file,
      preceded by a blank line when the file is non-empty and does not
      already end in one.
    - Heading present: insert the bullet at the end of its section —
      after the last non-blank line, before the next heading of the
      same or higher level (or EOF).
    - Dedupe: if a wikilink to ``target`` (including ``[[target|x]]``
      / ``[[target#a]]`` variants) already appears in the section, the
      text is returned unchanged.

    Everything outside the one inserted line (plus the heading and
    blank line when bootstrapping the section) is preserved
    byte-for-byte; trailing-newline presence is preserved too.

    :param text: The full note text.
    :param target: The wikilink target to append.
    :param heading: The heading line marking the section.
    :returns: The updated text, or ``text`` unchanged on a dedupe hit.
    """
    target_clean = target.strip()
    bullet = f"- [[{target_clean}]]"
    lines = text.split("\n")
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        if text == "":
            return f"{heading}\n{bullet}\n"
        base = text if text.endswith("\n") else f"{text}\n"
        if not base.endswith("\n\n"):
            base += "\n"
        return f"{base}{heading}\n{bullet}\n"
    heading_index, end = bounds
    if target_clean in related_link_targets(text, heading=heading):
        return text
    position = heading_index + 1
    for j in range(heading_index + 1, end):
        if lines[j].strip():
            position = j + 1
    lines.insert(position, bullet)
    return "\n".join(lines)
