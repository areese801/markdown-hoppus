"""
Unit tests for OFM extraction (HOPPUS-20): every link syntax in spec §6.1,
inline tag extraction with code-fence and heading exclusions (§6.4),
heading extraction, and trailing ``^block-id`` definitions.
"""

from pathlib import Path

from hoppus.model import Link, Tag
from hoppus.parse.ofm import (
    extract_block_ids,
    extract_headings,
    extract_links,
    extract_tags,
)

SRC = Path("Source.md")


def links_of(text: str) -> list[Link]:
    return extract_links(text, SRC)


def link_of(text: str) -> Link:
    links = links_of(text)
    assert len(links) == 1
    return links[0]


class TestWikilinks:
    """
    Rows 1-6 of the spec §6.1 table: non-embed wikilinks.
    """

    def test_plain_wikilink(self):
        link = link_of("See [[Note]].")
        assert link == Link(source=SRC, target="Note")
        assert link.is_wikilink and not link.is_embed
        assert link.resolved is None

    def test_display_text(self):
        link = link_of("See [[Note|Display]].")
        assert link.target == "Note"
        assert link.display == "Display"

    def test_heading_anchor(self):
        link = link_of("See [[Note#Heading]].")
        assert link.target == "Note"
        assert link.anchor == "Heading"

    def test_nested_heading_anchor(self):
        link = link_of("See [[Note#H1#H2]].")
        assert link.target == "Note"
        assert link.anchor == "H1#H2"

    def test_block_anchor(self):
        link = link_of("See [[Note#^block-id]].")
        assert link.target == "Note"
        assert link.anchor == "^block-id"

    def test_same_note_heading(self):
        link = link_of("See [[#Heading]].")
        assert link.target == ""
        assert link.anchor == "Heading"

    def test_same_note_block(self):
        link = link_of("See [[#^block-id]].")
        assert link.target == ""
        assert link.anchor == "^block-id"

    def test_anchor_and_display_combined(self):
        link = link_of("See [[Note#Heading|shown]].")
        assert (link.target, link.anchor, link.display) == (
            "Note",
            "Heading",
            "shown",
        )


class TestEmbeds:
    """
    Rows 7-9 of the spec §6.1 table: ``![[...]]`` embeds.
    """

    def test_note_embed(self):
        link = link_of("![[Note]]")
        assert link.target == "Note"
        assert link.is_embed and link.is_wikilink

    def test_heading_embed(self):
        link = link_of("![[Note#Heading]]")
        assert link.is_embed
        assert link.anchor == "Heading"

    def test_block_embed(self):
        link = link_of("![[Note#^block-id]]")
        assert link.is_embed
        assert link.anchor == "^block-id"

    def test_attachment_embed(self):
        link = link_of("![[image.png]]")
        assert link.target == "image.png"
        assert link.is_embed

    def test_attachment_embed_with_size_hint(self):
        link = link_of("![[image.png|300]]")
        assert link.target == "image.png"
        assert link.is_embed
        assert link.display == "300"

    def test_embed_vs_plain_wikilink(self):
        embed, plain = links_of("![[Note]] and [[Note]]")
        assert embed.is_embed
        assert not plain.is_embed


class TestMarkdownLinks:
    """
    Row 10 of the spec §6.1 table: standard ``[text](target)`` links.
    """

    def test_markdown_link(self):
        link = link_of("A [text](note.md) link.")
        assert link.target == "note.md"
        assert link.display == "text"
        assert not link.is_wikilink
        assert not link.is_embed

    def test_markdown_link_with_path(self):
        link = link_of("A [doc](path/to/file.ext) link.")
        assert link.target == "path/to/file.ext"

    def test_markdown_link_with_spaces_in_target(self):
        link = link_of("[the target](Target Note.md)")
        assert link.target == "Target Note.md"

    def test_markdown_image_is_not_extracted(self):
        assert links_of("![alt](image.png)") == []

    def test_wikilink_not_double_counted_as_md_link(self):
        links = links_of("[[Note]](not-a-md-target)")
        assert [link.is_wikilink for link in links] == [True]


class TestLinkExtractionBehavior:
    def test_document_order(self):
        text = "[b](b.md) then [[A]] then ![[C]]"
        assert [link.target for link in links_of(text)] == ["b.md", "A", "C"]

    def test_links_in_code_fence_excluded(self):
        text = "[[Real]]\n\n```\n[[Fenced]] and [md](fenced.md)\n```\n"
        assert [link.target for link in links_of(text)] == ["Real"]

    def test_links_in_inline_code_excluded(self):
        text = "Use `[[Not A Link]]` but do link [[Real]]."
        assert [link.target for link in links_of(text)] == ["Real"]

    def test_links_in_frontmatter_excluded(self):
        text = "---\nrelated: '[[Front]]'\n---\n\n[[Body]]\n"
        assert [link.target for link in links_of(text)] == ["Body"]

    def test_source_is_recorded(self):
        assert link_of("[[Note]]").source == SRC


class TestTags:
    """
    Spec §6.4: inline tags, with code-fence, inline-code, and heading
    exclusions.
    """

    def test_simple_tag(self):
        assert extract_tags("A #tag here.") == {Tag("tag")}

    def test_nested_tag(self):
        assert extract_tags("Nested #area/sub here.") == {Tag("area/sub")}

    def test_tag_in_code_fence_excluded(self):
        text = "#real\n\n```python\n# comment with #fake\n```\n"
        assert extract_tags(text) == {Tag("real")}

    def test_tag_in_inline_code_excluded(self):
        assert extract_tags("Run `git push #fake` for #real.") == {Tag("real")}

    def test_heading_line_is_not_a_tag(self):
        assert extract_tags("# Heading Text\n\nBody #tag\n") == {Tag("tag")}

    def test_hash_mid_word_is_not_a_tag(self):
        assert extract_tags("issue#123 is not a tag") == set()

    def test_purely_numeric_tag_excluded(self):
        assert extract_tags("Not tags: #123 and #2026.") == set()

    def test_same_note_anchor_is_not_a_tag(self):
        assert extract_tags("[[#Heading]] and [[#^block-id]]") == set()

    def test_frontmatter_not_scanned_for_inline_tags(self):
        text = "---\ntitle: '#nope'\n---\n\nBody #yes\n"
        assert extract_tags(text) == {Tag("yes")}


class TestHeadings:
    def test_headings_in_order(self):
        text = "# One\n\ntext\n\n## Two\n\n### Three\n"
        assert extract_headings(text) == ["One", "Two", "Three"]

    def test_heading_inside_code_fence_excluded(self):
        text = "# Real\n\n```\n# Fake\n```\n"
        assert extract_headings(text) == ["Real"]

    def test_frontmatter_skipped(self):
        text = "---\ntags: [a]\n---\n\n# Only\n"
        assert extract_headings(text) == ["Only"]


class TestBlockIds:
    def test_trailing_block_id(self):
        assert extract_block_ids("A quotable line. ^quote-1\n") == ["quote-1"]

    def test_block_id_on_list_item(self):
        assert extract_block_ids("- item one ^li-1\n- item two\n") == ["li-1"]

    def test_block_id_reference_is_not_a_definition(self):
        assert extract_block_ids("See [[Note#^quote-1]]\n") == []

    def test_block_id_in_code_fence_excluded(self):
        assert extract_block_ids("```\ncode ^fake\n```\nreal ^ok\n") == ["ok"]

    def test_mid_line_caret_is_not_a_block_id(self):
        assert extract_block_ids("a ^mid token then more words\n") == []


class TestSampleVault:
    """
    Extraction against the shared sample vault's ``Index.md`` and
    ``Tags Note.md`` (built to exercise the full §6.1/§6.4 grammar).
    """

    def test_index_links_cover_every_syntax(self, sample_vault: Path):
        path = sample_vault / "Index.md"
        links = extract_links(path.read_text(encoding="utf-8"), path)

        def find(**want) -> Link:
            for link in links:
                if all(getattr(link, key) == value for key, value in want.items()):
                    return link
            raise AssertionError(f"no link matching {want!r} in {links!r}")

        plain = find(target="Target Note", anchor=None, display=None, is_embed=False)
        assert plain.is_wikilink and plain.resolved is None
        assert find(target="Target Note", display="the target", is_embed=False)
        assert find(target="Target Note", anchor="Section One", is_embed=False)
        assert find(target="Target Note", anchor="^quote-1")
        assert find(target="", anchor="Local Heading")
        assert find(target="", anchor="^local-block")
        assert find(target="Target Note", anchor=None, is_embed=True)
        assert find(target="Target Note", anchor="Section One", is_embed=True)
        assert find(target="image.png", display=None, is_embed=True)
        assert find(target="image.png", display="300", is_embed=True)
        assert find(target="Target Note.md", is_wikilink=False, display="the target")
        assert find(target="Projects/Alpha")
        assert find(target="Archive/Alpha")

    def test_index_headings_and_block_ids(self, sample_vault: Path):
        text = (sample_vault / "Index.md").read_text(encoding="utf-8")
        assert extract_headings(text) == ["Index", "Local Heading"]
        assert extract_block_ids(text) == ["local-block"]

    def test_tags_note_inline_tags(self, sample_vault: Path):
        text = (sample_vault / "Tags Note.md").read_text(encoding="utf-8")
        assert extract_tags(text) == {
            Tag("inline-tag"),
            Tag("area/sub"),
            Tag("closing-tag"),
        }

    def test_target_note_block_id(self, sample_vault: Path):
        text = (sample_vault / "Target Note.md").read_text(encoding="utf-8")
        assert extract_block_ids(text) == ["quote-1"]
        assert extract_headings(text) == [
            "Target Note",
            "Section One",
            "Section Two",
        ]
