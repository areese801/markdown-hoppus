"""
Pure-Python fuzzy matcher for the quick switcher and in-TUI search
(spec §9.3, §9.8).

Built on ``rapidfuzz``. The single entry point, :func:`rank`, is generic
over any item type: callers supply a ``key`` that maps an item to one or
more candidate strings (e.g. a note's title plus its aliases), and get
back the items ranked by fuzzy similarity to the query. Epic 6 extends
this module for content search; keep it small and dependency-light.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

from rapidfuzz import fuzz, utils

T = TypeVar("T")

#: Scores below this cutoff count as non-matches and are dropped.
DEFAULT_SCORE_CUTOFF = 50.0


def _candidate_strings(value: str | Iterable[str]) -> list[str]:
    """
    Normalize a ``key`` result into a list of candidate strings.

    :param value: A single string or an iterable of strings.
    :returns: The candidate strings as a list.
    """
    if isinstance(value, str):
        return [value]
    return list(value)


def score(query: str, candidate: str) -> float:
    """
    Score one candidate string against a query.

    Uses ``rapidfuzz``'s ``WRatio`` with default preprocessing
    (case-folding, punctuation stripping), so substring and
    out-of-order matches score well — an fzf-like feel.

    :param query: The user's query text.
    :param candidate: The string to score against the query.
    :returns: A similarity score in ``[0, 100]``.
    """
    return fuzz.WRatio(query, candidate, processor=utils.default_process)


def rank(
    query: str,
    items: Sequence[T],
    *,
    key: Callable[[T], str | Iterable[str]],
    limit: int | None = None,
    score_cutoff: float = DEFAULT_SCORE_CUTOFF,
) -> list[tuple[T, float]]:
    """
    Rank items by fuzzy similarity of their key strings to a query.

    Each item's score is the best score across all strings returned by
    ``key(item)`` (e.g. a note's title and every alias). Items scoring
    below ``score_cutoff`` are dropped. Results are sorted by score
    descending; ties keep the original item order (stable sort).

    An empty (or whitespace-only) query is a passthrough: every item is
    returned in its given order with a score of ``0.0``, honoring
    ``limit``.

    :param query: The user's query text.
    :param items: The items to rank.
    :param key: Maps an item to one candidate string or an iterable of
        candidate strings.
    :param limit: Maximum number of results to return, or None for all.
    :param score_cutoff: Minimum score for an item to count as a match.
    :returns: ``(item, score)`` pairs, best matches first.
    """
    if not query.strip():
        passthrough = [(item, 0.0) for item in items]
        return passthrough[:limit] if limit is not None else passthrough

    scored: list[tuple[T, float]] = []
    for item in items:
        best = max(
            (score(query, candidate) for candidate in _candidate_strings(key(item))),
            default=0.0,
        )
        if best >= score_cutoff:
            scored.append((item, best))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit] if limit is not None else scored
