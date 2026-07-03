"""
Tests for the search engine (spec §9.8, HOPPUS-44): operator
AND-filtering, bare-term title/content union, dedupe, deterministic
ordering, and limits — all against temp vaults with the pure-Python
content backend.
"""

from pathlib import Path

import pytest

from hoppus.index.indexer import Index
from hoppus.search.engine import SearchResult, search
from hoppus.search.operators import parse_query

_MEETING_MD = """\
---
tags:
  - work/meetings
attendee: Jane Smith
---

# Standup

Discussed the quarterly zebra migration plan.
"""

_PROJECT_MD = """\
---
tags:
  - work
---

# Zebra Project

Notes about the zebra initiative.
"""

_PERSON_MD = """\
---
reports_to: Jane Smith
tags:
  - people
---

# John Doe

A colleague.
"""


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """
    Build a small temp vault with tags, frontmatter, and body content.

    :param tmp_path: pytest's per-test temp dir.
    :returns: The vault root.
    """
    meetings = tmp_path / "Meetings"
    meetings.mkdir()
    (meetings / "Standup.md").write_text(_MEETING_MD, encoding="utf-8")
    (tmp_path / "Zebra Project.md").write_text(_PROJECT_MD, encoding="utf-8")
    people = tmp_path / "People"
    people.mkdir()
    (people / "John Doe.md").write_text(_PERSON_MD, encoding="utf-8")
    return tmp_path


def run(query: str, vault_root: Path, limit: int | None = None) -> list[SearchResult]:
    """
    Parse and execute a query against a freshly built index.

    :param query: The raw query string.
    :param vault_root: The vault root.
    :param limit: Optional result cap.
    :returns: The search results.
    """
    index = Index.build(vault_root)
    return search(parse_query(query), index, backend="python", limit=limit)


def test_operator_and_filtering(vault: Path) -> None:
    """
    A ``tag:`` + ``path:`` query returns only notes satisfying both.
    """
    results = run("tag:work path:Meetings/", vault)
    assert [result.title for result in results] == ["Standup"]

    # tag:work alone matches both work notes (nested prefix included).
    results = run("tag:work", vault)
    assert {result.title for result in results} == {"Standup", "Zebra Project"}


def test_bare_term_title_fuzzy_hit(vault: Path) -> None:
    """
    A bare term matching a title returns that note with a real score.
    """
    results = run("zebra project", vault)
    assert results
    assert results[0].title == "Zebra Project"
    assert results[0].score > 0


def test_bare_term_content_only_hit_has_snippet(vault: Path) -> None:
    """
    A term matching only a body returns the note with snippet + line.
    """
    results = run("quarterly", vault)
    titles = [result.title for result in results]
    assert "Standup" in titles
    hit = next(result for result in results if result.title == "Standup")
    assert hit.snippet is not None and "quarterly" in hit.snippet
    assert hit.line_number is not None and hit.line_number > 0


def test_title_and_content_union_attaches_snippet(vault: Path) -> None:
    """
    A term hitting both a title and that note's body keeps the title
    score and attaches a content snippet.
    """
    results = run("zebra", vault)
    hit = next(result for result in results if result.title == "Zebra Project")
    assert hit.score > 0
    assert hit.snippet is not None and "zebra" in hit.snippet.lower()


def test_operators_only_returns_all_survivors_sorted(vault: Path) -> None:
    """
    An operators-only query returns title-only results sorted by title.
    """
    results = run("tag:work", vault)
    assert [result.title for result in results] == ["Standup", "Zebra Project"]
    assert all(result.snippet is None for result in results)
    assert all(result.score == 0.0 for result in results)


def test_frontmatter_key_value_match(vault: Path) -> None:
    """
    ``<key>:<value>`` filters on frontmatter, honoring quoted values.
    """
    results = run('reports_to:"Jane Smith"', vault)
    assert [result.title for result in results] == ["John Doe"]

    # A different frontmatter key on another note.
    results = run("attendee:jane", vault)
    assert [result.title for result in results] == ["Standup"]


def test_dedupe_by_path(vault: Path) -> None:
    """
    A note hit by both title fuzzy and content search appears once.
    """
    results = run("zebra", vault)
    paths = [result.path for result in results]
    assert len(paths) == len(set(paths))


def test_deterministic_ordering(vault: Path) -> None:
    """
    Repeated runs produce identical orderings (score, title, path).
    """
    first = run("zebra", vault)
    second = run("zebra", vault)
    assert first == second
    scores = [result.score for result in first]
    assert scores == sorted(scores, reverse=True)


def test_limit_honored(vault: Path) -> None:
    """
    ``limit`` caps the result count for both query shapes.
    """
    assert len(run("tag:work", vault, limit=1)) == 1
    assert len(run("zebra", vault, limit=1)) == 1


def test_operator_filter_restricts_content_search(vault: Path) -> None:
    """
    Bare-term content hits are restricted to operator survivors.
    """
    # "Jane Smith" appears in Standup and John Doe bodies/frontmatter;
    # the path: filter must exclude the Meetings note.
    results = run("path:People/ jane", vault)
    assert {result.title for result in results} <= {"John Doe"}


def test_no_match_returns_empty(vault: Path) -> None:
    """
    A query matching nothing returns an empty list.
    """
    assert run("tag:nonexistent", vault) == []
    assert run("xyzzyplugh", vault) == []
