"""
Targeted search operator parsing: ``tag:``, ``title:``, ``path:``, and
frontmatter ``<key>:<value>`` (spec §9.8).

Grammar
-------

A raw query is a whitespace-separated sequence of tokens. Quoting is
honored via ``shlex`` (POSIX rules), so ``reports_to:"Jane Smith"`` and
``title:"Q3 Planning"`` each form a single token whose value keeps the
space. When quotes are unbalanced, ``shlex`` fails and the query falls
back to a naive whitespace split (every token taken literally).

Each token is classified by splitting on the FIRST colon:

- ``field:value`` with a recognized field (``tag``, ``title``, ``path``)
  is that targeted operator.
- ``key:value`` with any other non-empty key is a FRONTMATTER field
  match on ``key``.
- A token with no colon, or an empty field part (``:foo``), is a BARE
  full-text term.
- A colon immediately followed by ``//`` (as in ``http://x`` or
  ``https://x``) is NOT an operator separator — such tokens are bare
  terms.
- An operator with an empty value (``tag:``) is ignored entirely.

The predicates in this module are tiny, total, and pure (no I/O) so the
engine and tests can compose them freely.
"""

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoppus.model import Note

#: Fields with dedicated operator semantics; any other field is a
#: frontmatter key match (spec §9.8).
KNOWN_FIELDS = frozenset({"tag", "title", "path"})


@dataclass(frozen=True)
class Term:
    """
    One parsed query term.

    :param field: The operator key (``"tag"``, ``"title"``, ``"path"``,
        or a frontmatter key), or None for a bare full-text term.
    :param value: The term's value, with any quotes already stripped.
    """

    field: str | None
    value: str


@dataclass(frozen=True)
class ParsedQuery:
    """
    A structured search query: an ordered tuple of terms.

    :param terms: The parsed terms, in query order.
    """

    terms: tuple[Term, ...]

    def bare_terms(self) -> list[str]:
        """
        Return the values of all bare (full-text) terms, in order.
        """
        return [term.value for term in self.terms if term.field is None]

    def field_terms(self, field: str) -> list[str]:
        """
        Return the values of all terms for one operator field, in order.

        :param field: The operator key to select (case-sensitive as
            stored; parsing lowercases fields).
        """
        return [term.value for term in self.terms if term.field == field]

    def operator_terms(self) -> list[Term]:
        """
        Return every non-bare term, in query order.
        """
        return [term for term in self.terms if term.field is not None]


def _tokenize(raw: str) -> list[str]:
    """
    Split a raw query into tokens, honoring quotes when balanced.

    :param raw: The raw query string.
    :returns: The tokens; naive whitespace split on unbalanced quotes.
    """
    try:
        return shlex.split(raw, posix=True)
    except ValueError:
        return raw.split()


def _classify(token: str) -> Term | None:
    """
    Classify one token into a Term per the module grammar.

    :param token: A single (already unquoted) token.
    :returns: The Term, or None when the token should be dropped
        (an operator with an empty value).
    """
    field, sep, value = token.partition(":")
    if not sep or not field or value.startswith("//"):
        return Term(field=None, value=token)
    if not value:
        return None
    return Term(field=field.lower(), value=value)


def parse_query(raw: str) -> ParsedQuery:
    """
    Parse a raw query string into a :class:`ParsedQuery`.

    See the module docstring for the precise grammar (quoting,
    first-colon splitting, the ``://`` bare-term guard, and empty-value
    operator dropping).

    :param raw: The raw query string.
    :returns: The parsed query; empty input yields no terms.
    """
    terms = []
    for token in _tokenize(raw):
        term = _classify(token)
        if term is not None and term.value:
            terms.append(term)
    return ParsedQuery(terms=tuple(terms))


def matches_tag(note_tags: set[str], value: str) -> bool:
    """
    Test whether a note's tag set satisfies a ``tag:`` operator.

    Case-insensitive; a nested tag matches its prefix per Obsidian, so
    ``tag:area`` matches both ``area`` and ``area/sub``.

    :param note_tags: The note's tag names (no leading ``#``).
    :param value: The operator value.
    :returns: True when any tag equals the value or nests under it.
    """
    needle = value.lower()
    return any(
        tag.lower() == needle or tag.lower().startswith(needle + "/")
        for tag in note_tags
    )


def matches_title(note: Note, value: str) -> bool:
    """
    Test whether a note satisfies a ``title:`` operator.

    Case-insensitive substring match on the note title.

    :param note: The candidate note.
    :param value: The operator value.
    :returns: True on a substring match.
    """
    return value.lower() in note.title.lower()


def matches_path(note: Note, vault_root: Path, value: str) -> bool:
    """
    Test whether a note satisfies a ``path:`` operator.

    Case-insensitive substring match on the note's vault-relative path
    (POSIX separators); falls back to the absolute path when the note
    lies outside the vault root.

    :param note: The candidate note.
    :param vault_root: The vault root directory.
    :param value: The operator value.
    :returns: True on a substring match.
    """
    try:
        relative = note.path.relative_to(vault_root)
    except ValueError:
        relative = note.path
    return value.lower() in relative.as_posix().lower()


def matches_frontmatter(fm_dict: dict[str, Any], key: str, value: str) -> bool:
    """
    Test whether a frontmatter mapping satisfies a ``<key>:<value>``
    operator.

    The key lookup is case-insensitive. Values are stringified and
    compared as case-insensitive SUBSTRINGS (not strict equality), so
    ``reports_to:jane`` matches ``reports_to: Jane Smith``. List values
    match when any element matches.

    :param fm_dict: The note's parsed frontmatter mapping.
    :param key: The frontmatter key to look up.
    :param value: The operator value.
    :returns: True when the key exists and its value matches.
    """
    needle = value.lower()
    key_lower = key.lower()
    for fm_key, fm_value in fm_dict.items():
        if str(fm_key).lower() != key_lower:
            continue
        values = fm_value if isinstance(fm_value, list) else [fm_value]
        return any(needle in str(item).lower() for item in values)
    return False
