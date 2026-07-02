"""
Unit tests for the domain model dataclasses (Note, Link, Tag).
"""

from pathlib import Path

from hoppus.model import Link, Note, Tag


class TestNote:
    def test_defaults(self):
        note = Note(title="John Doe", path=Path("People/John Doe.md"))
        assert note.title == "John Doe"
        assert note.path == Path("People/John Doe.md")
        assert note.aliases == []
        assert note.headings == []
        assert note.block_ids == []
        assert note.frontmatter == {}
        assert note.word_count == 0
        assert note.mtime == 0.0

    def test_default_lists_are_independent(self):
        a = Note(title="A", path=Path("A.md"))
        b = Note(title="B", path=Path("B.md"))
        a.aliases.append("alias")
        assert b.aliases == []

    def test_equality(self):
        kwargs = {"title": "A", "path": Path("A.md"), "aliases": ["Ay"]}
        assert Note(**kwargs) == Note(**kwargs)
        assert Note(title="A", path=Path("A.md")) != Note(title="B", path=Path("B.md"))


class TestLink:
    def test_plain_wikilink_defaults(self):
        link = Link(source=Path("Meeting.md"), target="John Doe")
        assert link.target == "John Doe"
        assert link.anchor is None
        assert link.display is None
        assert link.is_embed is False
        assert link.is_wikilink is True
        assert link.resolved is None

    def test_embed_vs_plain_wikilink(self):
        plain = Link(source=Path("A.md"), target="Note")
        embed = Link(source=Path("A.md"), target="Note", is_embed=True)
        assert not plain.is_embed
        assert embed.is_embed
        assert plain != embed

    def test_anchored_link_with_display(self):
        link = Link(
            source=Path("A.md"),
            target="Note",
            anchor="^block-id",
            display="see this",
        )
        assert link.anchor == "^block-id"
        assert link.display == "see this"

    def test_same_note_anchor(self):
        link = Link(source=Path("A.md"), target="", anchor="Heading")
        assert link.target == ""
        assert link.anchor == "Heading"

    def test_markdown_link(self):
        link = Link(
            source=Path("A.md"),
            target="path/to/note.md",
            display="text",
            is_wikilink=False,
        )
        assert not link.is_wikilink

    def test_resolved_target(self):
        link = Link(
            source=Path("A.md"),
            target="Note",
            resolved=Path("Sub/Note.md"),
        )
        assert link.resolved == Path("Sub/Note.md")


class TestTag:
    def test_simple_tag(self):
        tag = Tag("todo")
        assert tag.name == "todo"
        assert tag.parts == ("todo",)
        assert tag.parent is None
        assert str(tag) == "#todo"

    def test_nested_tag(self):
        tag = Tag("area/subarea")
        assert tag.parts == ("area", "subarea")
        assert tag.parent == Tag("area")
        assert str(tag) == "#area/subarea"

    def test_deeply_nested_parent_chain(self):
        tag = Tag("a/b/c")
        assert tag.parent == Tag("a/b")
        assert tag.parent.parent == Tag("a")
        assert tag.parent.parent.parent is None

    def test_equality_and_hashability(self):
        assert Tag("area/sub") == Tag("area/sub")
        assert Tag("area") != Tag("other")
        assert {Tag("a"), Tag("a"), Tag("b")} == {Tag("a"), Tag("b")}
