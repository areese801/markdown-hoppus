"""
Pure word-count and vault-statistics aggregation (spec §9.13, HOPPUS-53).

Two layers, both side-effect free:

- Text metrics: :func:`count_words` and :func:`count_chars`, used by the
  TUI status line for the ACTIVE note's live word/char counts.
- Vault aggregation: :func:`compute_vault_stats` folds a built
  ``hoppus.index.indexer.Index`` into an immutable :class:`VaultStats`
  snapshot (note count, total words, tag counts, orphans, unresolved
  links) for the stats view. The TUI renders the dataclass; no
  aggregation logic lives in the widgets.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoppus.index.indexer import Index
from hoppus.integrity import KIND_UNRESOLVED_WIKILINK, audit_vault

#: Maximum number of (tag, note-count) pairs kept in ``top_tags``.
TOP_TAGS_LIMIT = 10


@dataclass(frozen=True)
class VaultStats:
    """
    An immutable snapshot of whole-vault statistics (spec §9.13).

    :param note_count: Number of notes in the vault index.
    :param total_words: Sum of every note's body word count.
    :param tag_count: Number of distinct tags across the vault.
    :param top_tags: The most-used tags as ``(tag, note_count)`` pairs,
        sorted by note count descending then tag name ascending, capped
        at :data:`TOP_TAGS_LIMIT`.
    :param orphan_count: Number of orphan notes (see
        :func:`compute_vault_stats` for the exact definition).
    :param unresolved_link_count: Number of unresolved ``[[wikilink]]``
        occurrences across the vault, per the integrity audit.
    """

    note_count: int
    total_words: int
    tag_count: int
    top_tags: tuple[tuple[str, int], ...]
    orphan_count: int
    unresolved_link_count: int


def count_words(text: str) -> int:
    """
    Count the words in a text: whitespace-split non-empty tokens.

    The status line uses this for the active note's live word count;
    it matches ``len(text.split())``, the same rule the indexer uses
    for ``Note.word_count``.

    :param text: The text to measure.
    :returns: The number of whitespace-separated tokens.
    """
    return len(text.split())


def count_chars(text: str) -> int:
    """
    Count the characters in a text (``len``, including whitespace).

    The status line uses this for the active note's live char count.

    :param text: The text to measure.
    :returns: The character count.
    """
    return len(text)


def _is_orphan(index: Index, path: Path) -> bool:
    """
    Decide whether one note is an orphan (spec §9.13).

    An orphan is a note nothing links to and that links to nothing:
    it has no inbound backlinks and no outbound RESOLVED wikilinks.
    Self-references (a note linking to itself, e.g. via a same-note
    anchor) are ignored in both directions, and unresolved (dangling)
    wikilinks do not count as outbound links — they are reported via
    ``unresolved_link_count`` instead.

    :param index: The built vault index.
    :param path: The note path to test.
    :returns: True when the note is an orphan.
    """
    inbound = {source for source in index.backlinks.get(path, set()) if source != path}
    if inbound:
        return False
    return not any(
        link.is_wikilink and link.resolved is not None and link.resolved != path
        for link in index.links.get(path, [])
    )


def compute_vault_stats(
    index: Index,
    *,
    config: Mapping[str, Any] | None = None,
) -> VaultStats:
    """
    Aggregate a built index into a :class:`VaultStats` snapshot.

    Deterministic and read-only:

    - ``note_count``: ``len(index.notes_by_path)``.
    - ``total_words``: sum of every ``Note.word_count`` (body words,
      frontmatter excluded, as computed by the indexer).
    - ``tag_count`` / ``top_tags``: from ``index.tags`` (tag → note
      paths); top tags sorted by note count descending then tag name,
      capped at :data:`TOP_TAGS_LIMIT`.
    - ``orphan_count``: notes with no inbound backlinks AND no outbound
      resolved wikilinks (see :func:`_is_orphan`).
    - ``unresolved_link_count``: the number of
      ``"unresolved_wikilink"`` issues reported by
      :func:`hoppus.integrity.audit_vault` — scoped to that kind only,
      so other audit kinds (broken anchors, missing attachments,
      broken markdown links) never inflate the count.

    :param index: The built vault index to aggregate.
    :param config: The merged configuration mapping (spec §12), passed
        through to the integrity audit; None runs the default checks.
    :returns: The aggregated snapshot.
    """
    notes = index.notes_by_path
    top_tags = tuple(
        (tag, len(paths))
        for tag, paths in sorted(
            index.tags.items(), key=lambda item: (-len(item[1]), item[0])
        )[:TOP_TAGS_LIMIT]
    )
    report = audit_vault(index, config=config)
    unresolved = len(report.by_kind().get(KIND_UNRESOLVED_WIKILINK, []))
    return VaultStats(
        note_count=len(notes),
        total_words=sum(note.word_count for note in notes.values()),
        tag_count=len(index.tags),
        top_tags=top_tags,
        orphan_count=sum(1 for path in notes if _is_orphan(index, path)),
        unresolved_link_count=unresolved,
    )
