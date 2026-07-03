"""
On-demand unlinked-mention detection for the active note (spec §8, HOPPUS-33).

An *unlinked mention* is a plain-text occurrence of a note's title (or one
of its aliases) in another note's body where no explicit link exists.
Mentions are computed on demand for a single target note — never
precomputed vault-wide — by masking out frontmatter, code, and existing
links (reusing ``hoppus.parse.ofm``'s length-preserving masking helpers)
and then counting case-insensitive whole-word occurrences.

Noisy-title guard: very short titles or aliases (e.g. "a", "OK", "to")
match all over a vault and produce useless, overwhelming results. Names
shorter than ``min_length`` characters are therefore dropped from the
search set; if nothing survives, the note simply has no computable
unlinked mentions and an empty list is returned.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.model import Note
from hoppus.parse.ofm import _blank, _mask, _MD_LINK_RE, _WIKILINK_RE


@dataclass(frozen=True)
class UnlinkedMention:
    """
    One source note that mentions the target without linking to it.

    Attributes:
        source: Path of the mentioning note.
        title: Title of the mentioning note.
        count: Number of plain-text occurrences in that note.
    """

    source: Path
    title: str
    count: int


def find_unlinked_mentions(
    target: Note,
    index: Index,
    read_text: Callable[[Path], str],
    *,
    min_length: int = 3,
    limit: int | None = None,
) -> list[UnlinkedMention]:
    """
    Find notes that mention ``target`` in plain text without linking to it.

    The search set is the target's title plus its aliases, minus any name
    shorter than ``min_length`` (the noisy-title guard — short names like
    "a" or "to" would match everywhere and drown out real mentions). Every
    other note's body is masked (frontmatter, code blocks, inline code,
    wikilinks, markdown links) before counting case-insensitive whole-word
    occurrences, so text inside code or existing links never counts.

    Args:
        target: The active note whose mentions to find.
        index: The vault index supplying the candidate notes.
        read_text: Callable reading a note body from its path.
        min_length: Minimum name length to search for (noisy-title guard).
        limit: Optional maximum number of results.

    Returns:
        Mentions sorted by occurrence count (descending), then by source
        title (case-insensitive). Empty when no name survives the guard.
        Unreadable source files are skipped.
    """
    names = [
        name for name in [target.title, *target.aliases] if len(name) >= min_length
    ]
    if not names:
        return []
    alternation = "|".join(
        re.escape(name) for name in sorted(names, key=len, reverse=True)
    )
    pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)

    mentions: list[UnlinkedMention] = []
    for path, note in index.notes_by_path.items():
        if path == target.path:
            continue
        try:
            text = read_text(path)
        except OSError:
            continue
        masked = _mask(text)
        masked = _WIKILINK_RE.sub(_blank, masked)
        masked = _MD_LINK_RE.sub(_blank, masked)
        count = len(pattern.findall(masked))
        if count > 0:
            mentions.append(UnlinkedMention(source=path, title=note.title, count=count))

    mentions.sort(key=lambda mention: (-mention.count, mention.title.casefold()))
    return mentions if limit is None else mentions[:limit]
