"""
Link resolution tests (HOPPUS-21, spec §6.2), run against the shared
``sample_vault`` fixture.

Covers filename-based resolution, alias resolution, shortest-unique-path
disambiguation for duplicate titles, same-note and cross-note heading/block
anchors, attachment extension requirements, and non-existent targets.
"""

from pathlib import Path

import pytest

from hoppus.model import Link, Note
from hoppus.parse.frontmatter import get_aliases, split_frontmatter
from hoppus.parse.links import Resolver
from hoppus.parse.ofm import extract_block_ids, extract_headings, extract_links


def _load_note(path: Path) -> Note:
    """
    Build a Note from a file on disk using the parse helpers.

    :param path: Path to a ``.md`` note file.
    :returns: A populated Note with aliases, headings, and block ids.
    """
    text = path.read_text(encoding="utf-8")
    frontmatter, _body = split_frontmatter(text)
    return Note(
        title=path.stem,
        path=path,
        aliases=get_aliases(frontmatter),
        headings=extract_headings(text),
        block_ids=extract_block_ids(text),
        frontmatter=dict(frontmatter),
    )


def _wikilink(target: str, source: Path, anchor: str | None = None) -> Link:
    """
    Build an unresolved wikilink Link for resolution tests.
    """
    return Link(source=source, target=target, anchor=anchor)


@pytest.fixture(scope="module")
def notes(sample_vault: Path) -> dict[str, Note]:
    """
    Load every note in the sample vault, keyed by vault-relative path.
    """
    return {
        str(path.relative_to(sample_vault)): _load_note(path)
        for path in sorted(sample_vault.rglob("*.md"))
        if ".obsidian" not in path.parts
    }


@pytest.fixture(scope="module")
def resolver(sample_vault: Path, notes: dict[str, Note]) -> Resolver:
    """
    Build a Resolver over the sample vault's notes and attachments.
    """
    attachments = [
        path
        for path in sorted(sample_vault.rglob("*"))
        if path.is_file() and path.suffix != ".md" and ".obsidian" not in path.parts
    ]
    return Resolver(list(notes.values()), sample_vault, attachments=attachments)


def test_plain_title_resolution(resolver: Resolver, notes: dict[str, Note]) -> None:
    """
    A bare ``[[Target Note]]`` resolves by filename to the note's path.
    """
    link = _wikilink("Target Note", notes["Index.md"].path)
    assert resolver.resolve(link) == notes["Target Note.md"].path
    assert link.resolved == notes["Target Note.md"].path


def test_alias_resolution(resolver: Resolver, notes: dict[str, Note]) -> None:
    """
    A note's frontmatter aliases resolve links to that note (spec §6.2).
    """
    for alias in ("The Target", "target-note"):
        link = _wikilink(alias, notes["Index.md"].path)
        assert resolver.resolve(link) == notes["Target Note.md"].path


def test_duplicate_title_disambiguation(
    resolver: Resolver, notes: dict[str, Note]
) -> None:
    """
    Path-qualified links pick the right Alpha; a bare ``[[Alpha]]`` is
    ambiguous and does not resolve.
    """
    source = notes["Index.md"].path
    projects = _wikilink("Projects/Alpha", source)
    archive = _wikilink("Archive/Alpha", source)
    ambiguous = _wikilink("Alpha", source)
    assert resolver.resolve(projects) == notes["Projects/Alpha.md"].path
    assert resolver.resolve(archive) == notes["Archive/Alpha.md"].path
    assert resolver.resolve(ambiguous) is None
    assert ambiguous.resolved is None


def test_shortest_unique_name(resolver: Resolver, notes: dict[str, Note]) -> None:
    """
    A unique filename yields the bare title; a duplicated filename yields
    just enough leading path components to disambiguate.
    """
    assert resolver.shortest_unique_name(notes["Target Note.md"]) == "Target Note"
    assert resolver.shortest_unique_name(notes["Projects/Alpha.md"]) == (
        "Projects/Alpha"
    )
    assert resolver.shortest_unique_name(notes["Archive/Alpha.md"]) == ("Archive/Alpha")


def test_same_note_anchors(resolver: Resolver, notes: dict[str, Note]) -> None:
    """
    ``[[#Heading]]`` and ``[[#^block-id]]`` resolve within the current note.
    """
    index = notes["Index.md"]
    heading = _wikilink("", index.path, anchor="Local Heading")
    block = _wikilink("", index.path, anchor="^local-block")
    assert resolver.resolve(heading, current_note=index) == index.path
    assert resolver.anchor_resolves(heading, current_note=index)
    assert resolver.resolve(block, current_note=index) == index.path
    assert resolver.anchor_resolves(block, current_note=index)


def test_heading_and_block_anchor_resolvability(
    resolver: Resolver, notes: dict[str, Note]
) -> None:
    """
    ``#Heading`` resolves against the target's headings and ``#^block-id``
    against its block ids; missing anchors report False but the link still
    resolves to the file (spec §7.4 handles them softly).
    """
    source = notes["Index.md"].path
    target_path = notes["Target Note.md"].path
    heading = _wikilink("Target Note", source, anchor="Section One")
    nested = _wikilink("Target Note", source, anchor="Target Note#Section One")
    block = _wikilink("Target Note", source, anchor="^quote-1")
    missing_heading = _wikilink("Target Note", source, anchor="No Such Heading")
    missing_block = _wikilink("Target Note", source, anchor="^no-such-block")
    assert resolver.resolve(heading) == target_path
    assert resolver.anchor_resolves(heading)
    assert resolver.anchor_resolves(nested)
    assert resolver.anchor_resolves(block)
    assert resolver.resolve(missing_heading) == target_path
    assert not resolver.anchor_resolves(missing_heading)
    assert not resolver.anchor_resolves(missing_block)


def test_nonexistent_target(resolver: Resolver, notes: dict[str, Note]) -> None:
    """
    A wikilink to a note that does not exist resolves to None.
    """
    link = _wikilink("No Such Note", notes["Index.md"].path)
    assert resolver.resolve(link) is None
    assert link.resolved is None


def test_attachment_requires_extension(
    resolver: Resolver, sample_vault: Path, notes: dict[str, Note]
) -> None:
    """
    Non-``.md`` targets must include their extension (spec §6.2): with it
    the attachment resolves; without it there is no match.
    """
    source = notes["Index.md"].path
    with_ext = _wikilink("image.png", source)
    without_ext = _wikilink("image", source)
    assert resolver.resolve(with_ext) == sample_vault / "attachments" / "image.png"
    assert resolver.resolve(without_ext) is None


def test_extracted_index_links_resolve(
    resolver: Resolver, notes: dict[str, Note]
) -> None:
    """
    End-to-end: every wikilink extracted from Index.md resolves (bare
    ``[[Alpha]]`` never appears there — the fixture uses qualified forms).
    """
    index = notes["Index.md"]
    text = index.path.read_text(encoding="utf-8")
    wikilinks = [link for link in extract_links(text, index.path) if link.is_wikilink]
    assert wikilinks
    for link in wikilinks:
        assert resolver.resolve(link, current_note=index) is not None, link.target


def test_markdown_link_resolution(resolver: Resolver, notes: dict[str, Note]) -> None:
    """
    A standard ``[text](Target Note.md)`` link resolves to the vault file;
    external URLs do not resolve.
    """
    source = notes["Index.md"].path
    md_link = Link(source=source, target="Target Note.md", is_wikilink=False)
    external = Link(source=source, target="https://example.com/x.md", is_wikilink=False)
    assert resolver.resolve(md_link) == notes["Target Note.md"].path
    assert resolver.resolve(external) is None
