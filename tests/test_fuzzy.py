"""
Tests for the fuzzy matcher (HOPPUS-28): ranking order, empty-query
passthrough, alias and substring hits.
"""

from dataclasses import dataclass, field

from hoppus.search.fuzzy import rank, score


@dataclass
class Item:
    """
    A minimal item with a title and aliases for ranking tests.
    """

    title: str
    aliases: list[str] = field(default_factory=list)


ITEMS = [
    Item("Weekly Review"),
    Item("Target Note", aliases=["The Target", "target-note"]),
    Item("Groceries"),
    Item("Index"),
]


def _key(item: Item) -> list[str]:
    """
    Candidate strings for an item: title plus every alias.
    """
    return [item.title, *item.aliases]


def test_rank_orders_best_match_first() -> None:
    """
    The closest title match ranks first, scores descending.
    """
    results = rank("weekly review", ITEMS, key=_key)
    assert results
    assert results[0][0].title == "Weekly Review"
    scores = [pair[1] for pair in results]
    assert scores == sorted(scores, reverse=True)


def test_rank_drops_non_matches() -> None:
    """
    Items with no similarity to the query are dropped entirely.
    """
    results = rank("groceries", ITEMS, key=_key)
    titles = [item.title for item, _score in results]
    assert titles[0] == "Groceries"
    assert "Index" not in titles


def test_empty_query_is_stable_passthrough() -> None:
    """
    An empty or whitespace query returns items as given, score 0.
    """
    for query in ("", "   "):
        results = rank(query, ITEMS, key=_key)
        assert [item for item, _score in results] == ITEMS
        assert all(item_score == 0.0 for _item, item_score in results)


def test_empty_query_honors_limit() -> None:
    """
    The passthrough path still applies ``limit``.
    """
    results = rank("", ITEMS, key=_key, limit=2)
    assert [item for item, _score in results] == ITEMS[:2]


def test_alias_hit_ranks_item() -> None:
    """
    A query matching only an alias still surfaces the item first.
    """
    results = rank("the target", ITEMS, key=_key)
    assert results[0][0].title == "Target Note"


def test_substring_hit_matches() -> None:
    """
    A short substring of a longer title counts as a match.
    """
    results = rank("targ", ITEMS, key=_key)
    assert results[0][0].title == "Target Note"


def test_rank_limit_truncates_matches() -> None:
    """
    ``limit`` caps the number of returned matches.
    """
    results = rank("e", ITEMS, key=_key, limit=1)
    assert len(results) <= 1


def test_rank_accepts_plain_string_key() -> None:
    """
    ``key`` may return a single string instead of an iterable.
    """
    results = rank("index", ITEMS, key=lambda item: item.title)
    assert results[0][0].title == "Index"


def test_score_is_case_insensitive() -> None:
    """
    Preprocessing folds case, so query casing does not matter.
    """
    assert score("TARGET NOTE", "target note") == 100.0
