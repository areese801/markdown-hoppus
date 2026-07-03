"""
Integration tests for parsing + resolution (HOPPUS-23, spec §6.1/§6.2/§5.2).

These tests sit on top of the per-module unit tests (``test_ofm.py``,
``test_links.py``) and cover what those do not:

- An end-to-end link graph over the shared ``sample_vault``: every note is
  parsed with ``ofm.extract_links`` and every link resolved with a
  ``Resolver`` built from all notes, asserting exactly which links resolve
  to which files and which are intentionally unresolved.
- A coverage audit asserting a passing extraction *and* resolution check
  for every row of the spec §6.1 table.
- A coverage audit of the spec §6.2 resolution rules.
- Focused tests around the reserved characters OFM itself uses as
  separators (``#``, ``|``, ``^`` — spec §5.2), plus ``%%comment%%``
  handling, asserting real current behavior.
"""

from pathlib import Path

import pytest

from hoppus.model import Link, Note
from hoppus.parse.frontmatter import get_aliases, split_frontmatter
from hoppus.parse.links import Resolver
from hoppus.parse.ofm import extract_block_ids, extract_headings, extract_links

SRC = Path("Scratch.md")


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


def _graph_edges(
    note: Note, resolver: Resolver, vault: Path
) -> list[tuple[str, str | None]]:
    """
    Extract and resolve every link in a note, as (target, resolved) pairs.

    :param note: The note to parse.
    :param resolver: The vault-wide resolver.
    :param vault: The vault root, used to relativize resolved paths.
    :returns: One ``(raw target, vault-relative resolved path or None)``
        pair per link occurrence, in document order.
    """
    text = note.path.read_text(encoding="utf-8")
    edges = []
    for link in extract_links(text, note.path):
        resolved = resolver.resolve(link, current_note=note)
        edges.append(
            (link.target, str(resolved.relative_to(vault)) if resolved else None)
        )
    return edges


class TestEndToEndLinkGraph:
    """
    The full sample-vault link graph: parse every note, resolve every link,
    and assert exactly where each edge lands.
    """

    def test_index_note_edges(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        Index.md exercises every §6.1 syntax; every one of its links must
        resolve, including both duplicate-Alpha qualified forms, same-note
        anchors (which resolve to Index.md itself), attachment embeds, and
        the standard markdown link.
        """
        edges = _graph_edges(notes["Index.md"], resolver, sample_vault)
        assert edges == [
            ("Target Note", "Target Note.md"),
            ("Target Note", "Target Note.md"),
            ("Target Note", "Target Note.md"),
            ("Target Note", "Target Note.md"),
            ("", "Index.md"),
            ("", "Index.md"),
            ("Target Note", "Target Note.md"),
            ("Target Note", "Target Note.md"),
            ("image.png", "attachments/image.png"),
            ("image.png", "attachments/image.png"),
            ("Target Note.md", "Target Note.md"),
            ("Projects/Alpha", "Projects/Alpha.md"),
            ("Archive/Alpha", "Archive/Alpha.md"),
        ]

    def test_cross_note_edges(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        The remaining notes' outbound edges: Target Note links back to
        Index; each Alpha links to the other via its qualified path; the
        Tags Note has no links at all.
        """
        assert _graph_edges(notes["Target Note.md"], resolver, sample_vault) == [
            ("Index", "Index.md")
        ]
        assert _graph_edges(notes["Projects/Alpha.md"], resolver, sample_vault) == [
            ("Archive/Alpha", "Archive/Alpha.md")
        ]
        assert _graph_edges(notes["Archive/Alpha.md"], resolver, sample_vault) == [
            ("Projects/Alpha", "Projects/Alpha.md")
        ]
        assert _graph_edges(notes["Tags Note.md"], resolver, sample_vault) == []

    def test_whole_vault_has_no_dangling_wikilinks(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        Vault-wide invariant (spec §6.3): every wikilink written in the
        sample vault resolves. The fixture is deliberately dangling-free.
        """
        for note in notes.values():
            text = note.path.read_text(encoding="utf-8")
            for link in extract_links(text, note.path):
                if link.is_wikilink:
                    assert resolver.resolve(link, current_note=note) is not None, (
                        f"{note.path.name}: [[{link.target}]] did not resolve"
                    )

    def test_intentionally_unresolved_links(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        Links that must NOT resolve: a bare [[Alpha]] (ambiguous between
        Projects/ and Archive/), a missing note, a missing attachment, and
        an external markdown URL.
        """
        source = notes["Index.md"].path
        for link in (
            Link(source=source, target="Alpha"),
            Link(source=source, target="No Such Note"),
            Link(source=source, target="missing.png", is_embed=True),
            Link(source=source, target="https://example.com/x.md", is_wikilink=False),
        ):
            assert resolver.resolve(link) is None
            assert link.resolved is None

    def test_alias_edges_reach_target_note(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        Wikilinks written against Target Note's frontmatter aliases land on
        Target Note.md, end to end from raw text (spec §6.2 aliases).
        """
        target = notes["Target Note.md"].path
        for text in ("See [[The Target]].", "See [[target-note|shown]]."):
            (link,) = extract_links(text, notes["Index.md"].path)
            assert resolver.resolve(link) == target


class TestSpec61CoverageAudit:
    """
    One passing extraction + resolution check per row of the spec §6.1
    table, run against the sample vault.
    """

    @pytest.mark.parametrize(
        ("snippet", "target", "anchor", "display", "is_embed", "is_wikilink"),
        [
            ("[[Target Note]]", "Target Note", None, None, False, True),
            ("[[Target Note|Display]]", "Target Note", None, "Display", False, True),
            (
                "[[Target Note#Section One]]",
                "Target Note",
                "Section One",
                None,
                False,
                True,
            ),
            ("[[Target Note#^quote-1]]", "Target Note", "^quote-1", None, False, True),
            ("![[Target Note]]", "Target Note", None, None, True, True),
            (
                "![[Target Note#Section One]]",
                "Target Note",
                "Section One",
                None,
                True,
                True,
            ),
            (
                "![[Target Note#^quote-1]]",
                "Target Note",
                "^quote-1",
                None,
                True,
                True,
            ),
            ("![[image.png]]", "image.png", None, None, True, True),
            ("![[image.png|300]]", "image.png", None, "300", True, True),
            ("[text](Target Note.md)", "Target Note.md", None, "text", False, False),
        ],
        ids=[
            "plain-wikilink",
            "display-wikilink",
            "heading-wikilink",
            "block-wikilink",
            "note-embed",
            "heading-embed",
            "block-embed",
            "attachment-embed",
            "attachment-embed-size-hint",
            "standard-markdown-link",
        ],
    )
    def test_row_extracts_and_resolves(
        self,
        resolver: Resolver,
        notes: dict[str, Note],
        sample_vault: Path,
        snippet: str,
        target: str,
        anchor: str | None,
        display: str | None,
        is_embed: bool,
        is_wikilink: bool,
    ) -> None:
        """
        Each table row's syntax parses into the expected Link fields and
        resolves to a real sample-vault file.
        """
        (link,) = extract_links(snippet, notes["Index.md"].path)
        assert (link.target, link.anchor, link.display) == (target, anchor, display)
        assert (link.is_embed, link.is_wikilink) == (is_embed, is_wikilink)
        resolved = resolver.resolve(link, current_note=notes["Index.md"])
        expected = (
            "attachments/image.png" if target == "image.png" else "Target Note.md"
        )
        assert resolved == sample_vault / expected
        if anchor is not None:
            assert resolver.anchor_resolves(link)

    def test_row_nested_heading_wikilink(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        Nested heading form ``[[Note#H1#H2]]`` (§6.1 row 3, parenthetical):
        both heading components must exist on the target.
        """
        (link,) = extract_links(
            "[[Target Note#Target Note#Section One]]", notes["Index.md"].path
        )
        assert link.anchor == "Target Note#Section One"
        assert resolver.resolve(link) == sample_vault / "Target Note.md"
        assert resolver.anchor_resolves(link)

    def test_row_current_note_heading_and_block(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        ``[[#Heading]]`` and ``[[#^block-id]]`` (§6.1 rows 5-6) resolve to
        the current note and their anchors check against it.
        """
        index = notes["Index.md"]
        for snippet, anchor in (
            ("[[#Local Heading]]", "Local Heading"),
            ("[[#^local-block]]", "^local-block"),
        ):
            (link,) = extract_links(snippet, index.path)
            assert link.target == ""
            assert link.anchor == anchor
            assert resolver.resolve(link, current_note=index) == index.path
            assert resolver.anchor_resolves(link, current_note=index)

    def test_row_markdown_link_to_relative_path(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        ``[text](path/to/file.ext)`` (§6.1 row 10, second form) resolves
        when the path points at a known vault file.
        """
        (link,) = extract_links("[img](attachments/image.png)", notes["Index.md"].path)
        assert not link.is_wikilink
        assert resolver.resolve(link) == sample_vault / "attachments" / "image.png"

    def test_row_trailing_block_id_defines_target(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        A trailing ``^block-id`` (§6.1 last row) is a link *target*
        definition: it is extracted by ``extract_block_ids`` and makes
        ``[[Note#^block-id]]`` anchor-resolvable.
        """
        assert extract_block_ids("A quotable line. ^quote-1\n") == ["quote-1"]
        assert "quote-1" in notes["Target Note.md"].block_ids
        (link,) = extract_links("[[Target Note#^quote-1]]", notes["Index.md"].path)
        assert resolver.anchor_resolves(link)


class TestSpec62CoverageAudit:
    """
    The spec §6.2 resolution rules, each exercised against the vault.
    """

    def test_resolution_is_by_filename_not_path(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        A bare filename resolves regardless of the target's folder: links
        written from any note reach Projects-nested files without a path
        when the filename is vault-unique, and folder location of the
        *source* note never matters.
        """
        for source in (notes["Index.md"], notes["Projects/Alpha.md"]):
            (link,) = extract_links("[[Target Note]]", source.path)
            assert resolver.resolve(link) == sample_vault / "Target Note.md"

    def test_md_extension_is_optional_for_notes(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        ``[[Target Note]]`` and ``[[Target Note.md]]`` both resolve; the
        extension is optional for ``.md`` targets (spec §6.2).
        """
        for target in ("Target Note", "Target Note.md"):
            link = Link(source=notes["Index.md"].path, target=target)
            assert resolver.resolve(link) == sample_vault / "Target Note.md"

    def test_non_md_targets_require_extension(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        Attachment wikilinks must include their extension (spec §6.2).
        """
        source = notes["Index.md"].path
        with_ext = Link(source=source, target="image.png", is_embed=True)
        without_ext = Link(source=source, target="image", is_embed=True)
        assert resolver.resolve(with_ext) == (
            sample_vault / "attachments" / "image.png"
        )
        assert resolver.resolve(without_ext) is None

    def test_ambiguity_requires_shortest_unique_path(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        Duplicate-filename targets (spec §6.2): bare [[Alpha]] is ambiguous
        and unresolved; one leading path component disambiguates; and
        ``shortest_unique_name`` emits exactly that minimal form.
        """
        source = notes["Index.md"].path
        assert resolver.resolve(Link(source=source, target="Alpha")) is None
        assert (
            resolver.resolve(Link(source=source, target="Projects/Alpha"))
            == notes["Projects/Alpha.md"].path
        )
        assert resolver.shortest_unique_name(notes["Projects/Alpha.md"]) == (
            "Projects/Alpha"
        )
        assert resolver.shortest_unique_name(notes["Archive/Alpha.md"]) == (
            "Archive/Alpha"
        )
        assert resolver.shortest_unique_name(notes["Target Note.md"]) == "Target Note"

    def test_alias_resolution_from_frontmatter(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        ``aliases:`` frontmatter entries resolve, case-insensitively,
        mirroring Obsidian (spec §6.2).
        """
        target = notes["Target Note.md"].path
        source = notes["Index.md"].path
        for alias in ("The Target", "the target", "TARGET-NOTE"):
            assert resolver.resolve(Link(source=source, target=alias)) == target

    def test_heading_and_block_anchor_resolvability(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        ``#Heading`` checks the target's headings and ``#^block-id`` its
        block ids (spec §6.2); a missing anchor reports False while the
        file link itself still resolves (spec §7.4 soft handling).
        """
        source = notes["Index.md"].path
        good_heading = Link(source=source, target="Target Note", anchor="Section Two")
        good_block = Link(source=source, target="Target Note", anchor="^quote-1")
        bad_heading = Link(source=source, target="Target Note", anchor="Nope")
        bad_block = Link(source=source, target="Target Note", anchor="^nope")
        for link in (good_heading, good_block, bad_heading, bad_block):
            assert resolver.resolve(link) == notes["Target Note.md"].path
        assert resolver.anchor_resolves(good_heading)
        assert resolver.anchor_resolves(good_block)
        assert not resolver.anchor_resolves(bad_heading)
        assert not resolver.anchor_resolves(bad_block)

    def test_anchor_on_unresolvable_target_reports_false(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        An anchor cannot resolve when its target note does not: both the
        missing-note and ambiguous-duplicate cases report False.
        """
        source = notes["Index.md"].path
        missing = Link(source=source, target="No Such Note", anchor="Heading")
        ambiguous = Link(source=source, target="Alpha", anchor="Heading")
        assert not resolver.anchor_resolves(missing)
        assert not resolver.anchor_resolves(ambiguous)


class TestReservedCharacters:
    """
    Spec §5.2 reserved characters — focused on the separators OFM itself
    uses (``#``, ``|``, ``^``) and ``%%`` comments. Assertions pin real,
    current behavior.
    """

    def test_hash_and_pipe_split_in_one_link(
        self, resolver: Resolver, notes: dict[str, Note], sample_vault: Path
    ) -> None:
        """
        ``[[Note#Heading|Display]]`` splits target/anchor/display on the
        first ``#`` and ``|`` and still resolves end to end.
        """
        (link,) = extract_links(
            "[[Target Note#Section One|shown text]]", notes["Index.md"].path
        )
        assert (link.target, link.anchor, link.display) == (
            "Target Note",
            "Section One",
            "shown text",
        )
        assert resolver.resolve(link) == sample_vault / "Target Note.md"
        assert resolver.anchor_resolves(link)

    def test_second_pipe_stays_in_display(self) -> None:
        """
        Only the first ``|`` separates the display text; later pipes are
        kept verbatim in the display (current behavior).
        """
        (link,) = extract_links("[[Note#H|a|b]]", SRC)
        assert (link.target, link.anchor, link.display) == ("Note", "H", "a|b")

    def test_caret_without_hash_stays_in_target(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        ``^`` only means "block anchor" after ``#``. A bare ``[[Note^x]]``
        keeps the caret in the target — which, being a reserved character
        in filenames (spec §5.2), never matches a real file.
        """
        (link,) = extract_links("[[Target Note^quote-1]]", notes["Index.md"].path)
        assert link.target == "Target Note^quote-1"
        assert link.anchor is None
        assert resolver.resolve(link) is None

    def test_empty_anchor_and_empty_display(self) -> None:
        """
        Degenerate separators: ``[[Note#]]`` yields an empty anchor and
        ``[[Note|]]`` an empty display (present but blank — distinct from
        None); ``[[]]`` is not a link at all.
        """
        (hash_only,) = extract_links("[[Note#]]", SRC)
        assert (hash_only.target, hash_only.anchor) == ("Note", "")
        (pipe_only,) = extract_links("[[Note|]]", SRC)
        assert (pipe_only.target, pipe_only.display) == ("Note", "")
        assert extract_links("[[]]", SRC) == []

    def test_empty_anchor_does_not_resolve(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        An empty ``#`` anchor still resolves to the file but the anchor
        itself reports unresolvable (no heading is the empty string).
        """
        (link,) = extract_links("[[Target Note#]]", notes["Index.md"].path)
        assert resolver.resolve(link) == notes["Target Note.md"].path
        assert not resolver.anchor_resolves(link)

    def test_percent_comment_is_not_a_link_or_tag(self) -> None:
        """
        A bare ``%%comment%%`` contains no link or tag syntax and yields
        nothing from either extractor.
        """
        from hoppus.parse.ofm import extract_tags

        text = "Visible text %%just a comment%% more text."
        assert extract_links(text, SRC) == []
        assert extract_tags(text) == set()

    def test_link_syntax_inside_percent_comment_is_still_extracted(self) -> None:
        """
        Current behavior: the parser does not mask ``%%...%%`` comment
        interiors, so a wikilink written inside one IS extracted. Comment
        masking is out of scope for the parser (spec §5.2 reserves ``%%``
        for filenames/targets only; nothing mandates comment stripping).
        """
        (link,) = extract_links("%%hidden [[Note]] here%%", SRC)
        assert link.target == "Note"

    def test_reserved_characters_in_target_never_resolve(
        self, resolver: Resolver, notes: dict[str, Note]
    ) -> None:
        """
        Targets containing filename-reserved characters (spec §5.2) parse
        without error but resolve to nothing — no vault file can carry
        those names.
        """
        source = notes["Index.md"].path
        for target in ("Bad|Name", "Bad^Name", "Bad%%Name"):
            link = Link(source=source, target=target)
            assert resolver.resolve(link) is None
