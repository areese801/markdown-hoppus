"""
Tests for the pure vault-statistics layer (spec §9.13, HOPPUS-53):
text metrics (:func:`hoppus.stats.count_words` /
:func:`hoppus.stats.count_chars`) and :func:`compute_vault_stats`
aggregation over small temp vaults built with ``Index.build``.
"""

from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.stats import VaultStats, compute_vault_stats, count_chars, count_words


def _write(vault: Path, name: str, text: str) -> Path:
    """
    Write one note into a temp vault and return its path.

    :param vault: The vault root directory.
    :param name: The note filename (with ``.md``).
    :param text: The note body.
    :returns: The written path.
    """
    path = vault / name
    path.write_text(text, encoding="utf-8")
    return path


def _build_vault(tmp_path: Path) -> Path:
    """
    Build the canonical small stats vault.

    Four notes: ``A`` links to ``B`` (resolved), ``Orphan`` has no
    links either way, and ``Dangling`` carries one unresolved
    ``[[Nope]]`` wikilink. Tags: ``#alpha`` in two notes, ``#beta`` in
    one.

    :param tmp_path: The pytest temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(vault, "A.md", "one two three [[B]] #alpha\n")
    _write(vault, "B.md", "four five #alpha #beta\n")
    _write(vault, "Orphan.md", "six seven eight nine\n")
    _write(vault, "Dangling.md", "ten [[Nope]]\n")
    return vault


def test_count_words() -> None:
    """
    Words are whitespace-split non-empty tokens.
    """
    assert count_words("") == 0
    assert count_words("   \n\t ") == 0
    assert count_words("one") == 1
    assert count_words("one two  three") == 3
    assert count_words("  leading and\ntrailing  \n") == 3


def test_count_chars() -> None:
    """
    Chars are ``len(text)``, whitespace included.
    """
    assert count_chars("") == 0
    assert count_chars("abc") == 3
    assert count_chars("a b\nc") == 5


def test_compute_vault_stats_counts(tmp_path: Path) -> None:
    """
    Note count and total words aggregate over every indexed note.
    """
    vault = _build_vault(tmp_path)
    stats = compute_vault_stats(Index.build(vault))
    assert isinstance(stats, VaultStats)
    assert stats.note_count == 4
    # A: 5 tokens, B: 4, Orphan: 4, Dangling: 2 (body word counts).
    assert stats.total_words == 15


def test_compute_vault_stats_tags(tmp_path: Path) -> None:
    """
    Distinct tags are counted and top_tags is sorted count-desc, then tag.
    """
    vault = _build_vault(tmp_path)
    stats = compute_vault_stats(Index.build(vault))
    assert stats.tag_count == 2
    assert stats.top_tags == (("alpha", 2), ("beta", 1))


def test_compute_vault_stats_orphans(tmp_path: Path) -> None:
    """
    Orphans are notes with no inbound backlinks and no resolved
    outbound wikilinks: ``Orphan`` and ``Dangling`` (its only link is
    unresolved) count; linked ``A`` and ``B`` do not.
    """
    vault = _build_vault(tmp_path)
    stats = compute_vault_stats(Index.build(vault))
    assert stats.orphan_count == 2


def test_compute_vault_stats_unresolved_links(tmp_path: Path) -> None:
    """
    A dangling ``[[Nope]]`` contributes exactly one unresolved link.
    """
    vault = _build_vault(tmp_path)
    stats = compute_vault_stats(Index.build(vault))
    assert stats.unresolved_link_count == 1


def test_compute_vault_stats_empty_vault(tmp_path: Path) -> None:
    """
    An empty vault aggregates to all-zero stats.
    """
    vault = tmp_path / "empty"
    vault.mkdir()
    stats = compute_vault_stats(Index.build(vault))
    assert stats == VaultStats(
        note_count=0,
        total_words=0,
        tag_count=0,
        top_tags=(),
        orphan_count=0,
        unresolved_link_count=0,
    )


def test_compute_vault_stats_is_deterministic(tmp_path: Path) -> None:
    """
    Two aggregations of the same vault produce equal snapshots.
    """
    vault = _build_vault(tmp_path)
    first = compute_vault_stats(Index.build(vault))
    second = compute_vault_stats(Index.build(vault))
    assert first == second
