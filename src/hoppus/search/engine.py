"""
Search engine: executes a parsed query against a vault index (spec §9.8).

Semantics: every operator term is an AND filter over the full note set
(``tag:``, ``title:``, ``path:``, and frontmatter ``<key>:<value>``).
Bare terms then rank the surviving notes by title/alias fuzzy score and
by full-text content matches, unioned into one deduplicated result list.
With no bare terms, all surviving notes are returned as title-only
results. Ordering is deterministic: score descending, then title, then
path.

All file I/O is funneled through :func:`hoppus.search.content.
search_content` and the prebuilt :class:`hoppus.index.indexer.Index`,
so the engine stays testable against temp vaults.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.model import Note
from hoppus.search.content import search_content
from hoppus.search.fuzzy import rank
from hoppus.search.operators import (
    ParsedQuery,
    Term,
    matches_frontmatter,
    matches_path,
    matches_tag,
    matches_title,
)


@dataclass(frozen=True)
class SearchResult:
    """
    One deduplicated search hit: a single note.

    :param path: Path of the matching note.
    :param title: The note's title.
    :param score: Ranking score (higher is better; 0.0 for title-only
        or content-only hits with no fuzzy title score).
    :param snippet: A matching content line for preview, or None for
        title-only hits.
    :param line_number: 1-based line number of the snippet, or None.
    """

    path: Path
    title: str
    score: float
    snippet: str | None = None
    line_number: int | None = None


def _satisfies(term: Term, note: Note, index: Index) -> bool:
    """
    Test one operator term against one note.

    :param term: A non-bare term (``term.field`` is not None).
    :param note: The candidate note.
    :param index: The vault index (supplies tags and the vault root).
    :returns: True when the note satisfies the term.
    """
    if term.field == "tag":
        return matches_tag(index.note_tags.get(note.path, set()), term.value)
    if term.field == "title":
        return matches_title(note, term.value)
    if term.field == "path":
        return matches_path(note, index.vault_root, term.value)
    return matches_frontmatter(note.frontmatter, str(term.field), term.value)


def _filter_notes(parsed: ParsedQuery, index: Index) -> list[Note]:
    """
    Apply every operator term as an AND filter over the full note set.

    :param parsed: The parsed query.
    :param index: The vault index.
    :returns: The surviving notes, in index order.
    """
    operators = parsed.operator_terms()
    return [
        note
        for note in index.notes_by_path.values()
        if all(_satisfies(term, note, index) for term in operators)
    ]


def search(
    parsed: ParsedQuery,
    index: Index,
    *,
    backend: str = "auto",
    which: Callable[[str], str | None] = shutil.which,
    limit: int | None = None,
) -> list[SearchResult]:
    """
    Execute a parsed query against a vault index (spec §9.8).

    Operator terms AND-filter the note set; bare terms then rank the
    survivors by title/alias fuzzy score (best across all bare terms
    joined) and union in full-text content matches, attaching a snippet
    and line number when the match was in the body. A note appears at
    most once (best score, representative snippet). With no bare terms,
    every surviving note is returned title-only, sorted by title.

    :param parsed: The parsed query.
    :param index: A built vault index.
    :param backend: Content-search backend (``auto``/``ripgrep``/
        ``python``), forwarded to :func:`search_content`.
    :param which: Injectable PATH lookup for rg detection.
    :param limit: Maximum number of results, or None for all.
    :returns: Deduplicated results ordered by score descending, then
        title, then path.
    """
    surviving = _filter_notes(parsed, index)
    bare = parsed.bare_terms()
    if not bare:
        results = [
            SearchResult(path=note.path, title=note.title, score=0.0)
            for note in surviving
        ]
        results.sort(key=lambda result: (result.title.lower(), str(result.path)))
        return results[:limit] if limit is not None else results

    by_path: dict[Path, SearchResult] = {}
    fuzzy_query = " ".join(bare)
    for note, score in rank(
        fuzzy_query, surviving, key=lambda note: [note.title, *note.aliases]
    ):
        by_path[note.path] = SearchResult(path=note.path, title=note.title, score=score)

    surviving_by_path = {note.path: note for note in surviving}
    for term in bare:
        matches = search_content(
            term,
            index.vault_root,
            backend=backend,
            which=which,
            notes=surviving,
        )
        for match in matches:
            note = surviving_by_path.get(match.path)
            if note is None:
                continue
            existing = by_path.get(match.path)
            if existing is None:
                by_path[match.path] = SearchResult(
                    path=note.path,
                    title=note.title,
                    score=0.0,
                    snippet=match.line,
                    line_number=match.line_number,
                )
            elif existing.snippet is None:
                by_path[match.path] = replace(
                    existing, snippet=match.line, line_number=match.line_number
                )

    results = sorted(
        by_path.values(),
        key=lambda result: (-result.score, result.title.lower(), str(result.path)),
    )
    return results[:limit] if limit is not None else results
