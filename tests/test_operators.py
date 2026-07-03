"""
Tests for the targeted-operator query grammar (spec §9.8, HOPPUS-44):
tokenizing, operator classification, quoting, the ``://`` guard, and
the pure note predicates.
"""

from pathlib import Path

from hoppus.model import Note
from hoppus.search.operators import (
    ParsedQuery,
    Term,
    matches_frontmatter,
    matches_path,
    matches_tag,
    matches_title,
    parse_query,
)


def make_note(title: str, path: str, **frontmatter: object) -> Note:
    """
    Build a minimal Note for predicate tests.

    :param title: The note title.
    :param path: The note path string.
    :param frontmatter: Frontmatter key/value pairs.
    :returns: The Note.
    """
    return Note(title=title, path=Path(path), frontmatter=dict(frontmatter))


# -- parse_query ---------------------------------------------------------------


def test_bare_terms_only() -> None:
    """
    Tokens without a colon are bare full-text terms, in order.
    """
    parsed = parse_query("alpha beta")
    assert parsed.terms == (Term(None, "alpha"), Term(None, "beta"))
    assert parsed.bare_terms() == ["alpha", "beta"]


def test_empty_query_yields_no_terms() -> None:
    """
    Empty and whitespace-only queries parse to zero terms.
    """
    assert parse_query("").terms == ()
    assert parse_query("   ").terms == ()


def test_tag_operator_nested_value() -> None:
    """
    ``tag:area/sub`` parses as a tag operator with the nested value.
    """
    parsed = parse_query("tag:area/sub")
    assert parsed.terms == (Term("tag", "area/sub"),)
    assert parsed.field_terms("tag") == ["area/sub"]


def test_title_operator() -> None:
    """
    ``title:foo`` parses as a title operator.
    """
    assert parse_query("title:foo").field_terms("title") == ["foo"]


def test_path_operator() -> None:
    """
    ``path:Meetings/`` parses as a path operator, value verbatim.
    """
    assert parse_query("path:Meetings/").field_terms("path") == ["Meetings/"]


def test_frontmatter_operator_quoted_value_keeps_space() -> None:
    """
    ``reports_to:"Jane Smith"`` parses as a frontmatter operator whose
    value preserves the quoted space.
    """
    parsed = parse_query('reports_to:"Jane Smith"')
    assert parsed.terms == (Term("reports_to", "Jane Smith"),)


def test_quoted_title_value_keeps_space() -> None:
    """
    ``title:"Q3 Planning"`` keeps the space in the operator value.
    """
    assert parse_query('title:"Q3 Planning"').field_terms("title") == ["Q3 Planning"]


def test_mixed_operators_and_bare_terms() -> None:
    """
    Operators and bare terms coexist, each classified independently.
    """
    parsed = parse_query("tag:work meeting title:standup notes")
    assert parsed.bare_terms() == ["meeting", "notes"]
    assert parsed.field_terms("tag") == ["work"]
    assert parsed.field_terms("title") == ["standup"]


def test_unbalanced_quote_falls_back_to_naive_split() -> None:
    """
    An unbalanced quote does not raise; the query naive-splits.
    """
    parsed = parse_query('alpha "beta')
    assert parsed.bare_terms() == ["alpha", '"beta']


def test_url_is_a_bare_term_not_an_operator() -> None:
    """
    ``https://x`` stays a bare term — ``://`` never splits an operator.
    """
    parsed = parse_query("https://example.com")
    assert parsed.terms == (Term(None, "https://example.com"),)


def test_empty_value_operator_is_ignored() -> None:
    """
    ``tag:`` (no value) is dropped entirely.
    """
    parsed = parse_query("tag: alpha")
    assert parsed.terms == (Term(None, "alpha"),)


def test_leading_colon_token_is_bare() -> None:
    """
    ``:foo`` has an empty field and is treated as a bare term.
    """
    assert parse_query(":foo").terms == (Term(None, ":foo"),)


def test_splits_on_first_colon_only() -> None:
    """
    ``key:a:b`` splits once: the value keeps its later colons.
    """
    assert parse_query("key:a:b").terms == (Term("key", "a:b"),)


def test_operator_field_is_lowercased() -> None:
    """
    ``TAG:Work`` normalizes the field, not the value.
    """
    assert parse_query("TAG:Work").terms == (Term("tag", "Work"),)


def test_parsed_query_accessors_on_empty() -> None:
    """
    Accessors are total on an empty query.
    """
    parsed = ParsedQuery(terms=())
    assert parsed.bare_terms() == []
    assert parsed.field_terms("tag") == []
    assert parsed.operator_terms() == []


# -- Predicates ----------------------------------------------------------------


def test_matches_tag_exact_and_nested_prefix() -> None:
    """
    ``tag:area`` matches both ``area`` and nested ``area/sub``.
    """
    assert matches_tag({"area"}, "area")
    assert matches_tag({"area/sub"}, "area")
    assert matches_tag({"area/sub"}, "area/sub")
    assert not matches_tag({"areas"}, "area")
    assert not matches_tag(set(), "area")


def test_matches_tag_case_insensitive() -> None:
    """
    Tag matching ignores case on both sides.
    """
    assert matches_tag({"Area/Sub"}, "area")
    assert matches_tag({"area"}, "AREA")


def test_matches_title_substring_case_insensitive() -> None:
    """
    Title matching is a case-insensitive substring test.
    """
    note = make_note("Q3 Planning", "/vault/Q3 Planning.md")
    assert matches_title(note, "q3 plan")
    assert matches_title(note, "PLANNING")
    assert not matches_title(note, "q4")


def test_matches_path_vault_relative_case_insensitive() -> None:
    """
    Path matching runs on the vault-relative path, ignoring case.
    """
    note = make_note("Standup", "/vault/Meetings/Standup.md")
    root = Path("/vault")
    assert matches_path(note, root, "meetings/")
    assert matches_path(note, root, "Standup.md")
    assert not matches_path(note, root, "vault")


def test_matches_path_outside_vault_falls_back() -> None:
    """
    A note outside the vault root still matches on its own path.
    """
    note = make_note("Elsewhere", "/other/Elsewhere.md")
    assert matches_path(note, Path("/vault"), "elsewhere")


def test_matches_frontmatter_substring_case_insensitive() -> None:
    """
    Frontmatter matching stringifies values and compares substrings.
    """
    fm = {"reports_to": "Jane Smith", "level": 3}
    assert matches_frontmatter(fm, "reports_to", "Jane Smith")
    assert matches_frontmatter(fm, "REPORTS_TO", "jane")
    assert matches_frontmatter(fm, "level", "3")
    assert not matches_frontmatter(fm, "reports_to", "John")
    assert not matches_frontmatter(fm, "missing", "x")


def test_matches_frontmatter_list_values() -> None:
    """
    List-valued frontmatter matches when any element matches.
    """
    fm = {"aliases": ["The Target", "target-note"]}
    assert matches_frontmatter(fm, "aliases", "target-note")
    assert not matches_frontmatter(fm, "aliases", "missing")
