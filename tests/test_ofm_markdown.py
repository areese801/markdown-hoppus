"""
Tests for the OFM → navigable Markdown transform (HOPPUS-24, spec §19 D3).

Round-trip coverage for every §6.1 wikilink variant: OFM text →
``transform_ofm`` → clicked href (Textual unquotes once) → ``decode_href`` →
``LinkTarget`` matching the original link. Plus a headless Textual Pilot test
proving a mounted ``Markdown`` widget renders the transformed link and emits
``Markdown.LinkClicked`` with a decodable href.
"""

import asyncio
from pathlib import Path
from urllib.parse import unquote

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Markdown
from textual.widgets.markdown import MarkdownBlock

from hoppus.parse.ofm import extract_links
from hoppus.render.ofm_markdown import (
    LinkTarget,
    decode_href,
    encode_href,
    transform_ofm,
)

SRC = Path("Source.md")


def click_href(markdown_text: str) -> str:
    """
    Extract the transformed href and apply Textual's one-time unquote,
    mimicking what ``Markdown.LinkClicked.href`` delivers.
    """
    links = extract_links(markdown_text, SRC)
    assert len(links) == 1 and not links[0].is_wikilink
    return unquote(links[0].target)


def roundtrip(ofm_text: str) -> LinkTarget:
    transformed = transform_ofm(ofm_text)
    target = decode_href(click_href(transformed))
    assert target is not None
    return target


class TestRoundTrip:
    """
    OFM → markdown → clicked href → LinkTarget, per §6.1 variant.
    """

    def test_plain_wikilink(self):
        assert roundtrip("See [[Note]].") == LinkTarget(target="Note")

    def test_display_text(self):
        assert roundtrip("See [[Note|Display]].") == LinkTarget(
            target="Note", display="Display"
        )

    def test_heading_anchor(self):
        assert roundtrip("See [[Note#Heading]].") == LinkTarget(
            target="Note", anchor="Heading"
        )

    def test_nested_heading_anchor(self):
        assert roundtrip("See [[Note#H1#H2]].") == LinkTarget(
            target="Note", anchor="H1#H2"
        )

    def test_block_anchor(self):
        assert roundtrip("See [[Note#^block-id]].") == LinkTarget(
            target="Note", anchor="^block-id"
        )

    def test_same_note_heading(self):
        assert roundtrip("See [[#Heading]].") == LinkTarget(target="", anchor="Heading")

    def test_same_note_block(self):
        assert roundtrip("See [[#^block-id]].") == LinkTarget(
            target="", anchor="^block-id"
        )

    def test_note_embed(self):
        assert roundtrip("![[Note]]") == LinkTarget(target="Note", is_embed=True)

    def test_embed_with_heading(self):
        assert roundtrip("![[Note#Heading]]") == LinkTarget(
            target="Note", anchor="Heading", is_embed=True
        )

    def test_embed_with_block(self):
        assert roundtrip("![[Note#^block-id]]") == LinkTarget(
            target="Note", anchor="^block-id", is_embed=True
        )

    def test_attachment_embed(self):
        assert roundtrip("![[image.png]]") == LinkTarget(
            target="image.png", is_embed=True
        )

    def test_attachment_embed_with_size(self):
        assert roundtrip("![[image.png|300]]") == LinkTarget(
            target="image.png", display="300", is_embed=True
        )

    def test_anchor_and_display_together(self):
        assert roundtrip("[[Note#Heading|Shown]]") == LinkTarget(
            target="Note", anchor="Heading", display="Shown"
        )

    def test_reserved_characters_in_title(self):
        assert roundtrip("[[Q&A Notes = fun#What?|A & B]]") == LinkTarget(
            target="Q&A Notes = fun", anchor="What?", display="A & B"
        )


class TestTransform:
    """
    Transform output shape and pass-through behavior.
    """

    def test_label_is_display_text(self):
        assert transform_ofm("[[Note|Display]]").startswith("[Display](hoppus://")

    def test_label_is_target_with_anchor(self):
        assert transform_ofm("[[Note#Heading]]").startswith("[Note#Heading](")

    def test_same_note_label(self):
        assert transform_ofm("[[#Heading]]").startswith("[#Heading](")

    def test_ordinary_markdown_link_untouched(self):
        text = "A [text](note.md) link."
        assert transform_ofm(text) == text

    def test_code_fence_untouched(self):
        text = "```\n[[Not A Link]]\n```\n"
        assert transform_ofm(text) == text

    def test_inline_code_untouched(self):
        text = "Use `[[Not A Link]]` here."
        assert transform_ofm(text) == text

    def test_frontmatter_untouched(self):
        text = "---\ntitle: '[[Not A Link]]'\n---\n\n[[Real]]\n"
        transformed = transform_ofm(text)
        assert "title: '[[Not A Link]]'" in transformed
        assert "[Real](hoppus://" in transformed

    def test_surrounding_text_preserved(self):
        transformed = transform_ofm("before [[A]] middle [[B]] after")
        assert transformed.startswith("before [A](")
        assert " middle [B](" in transformed
        assert transformed.endswith(" after")


class TestDecodeHref:
    """
    Decoder edge cases beyond the round-trips.
    """

    def test_foreign_scheme_returns_none(self):
        assert decode_href("https://example.com") is None
        assert decode_href("note.md") is None

    def test_encode_href_has_no_raw_reserved_chars(self):
        links = extract_links("[[My Note#H1#H2|A & B]]", SRC)
        href = encode_href(links[0])
        query = href.split("?", 1)[1]
        for value in (pair.partition("=")[2] for pair in query.split("&")):
            assert not set(value) & set(" #=&")


class _PreviewApp(App[None]):
    """
    Minimal app mounting a Markdown widget with transformed OFM content.
    """

    def __init__(self, ofm_text: str) -> None:
        super().__init__()
        self._ofm_text = ofm_text
        self.clicked_hrefs: list[str] = []

    def compose(self) -> ComposeResult:
        yield Markdown(transform_ofm(self._ofm_text), open_links=False)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        self.clicked_hrefs.append(event.href)


@pytest.mark.parametrize(
    ("ofm_text", "expected"),
    [
        (
            "[[Target Note#Heading|Display]]",
            LinkTarget(target="Target Note", anchor="Heading", display="Display"),
        ),
        ("![[Target Note]]", LinkTarget(target="Target Note", is_embed=True)),
    ],
)
def test_pilot_click_emits_decodable_link_clicked(ofm_text, expected):
    """
    End-to-end intercept: activating a transformed wikilink in a live
    (headless) Markdown widget emits ``LinkClicked`` whose href decodes back
    to the original link's payload.
    """

    async def run() -> None:
        app = _PreviewApp(ofm_text)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click(MarkdownBlock, offset=(1, 0))
            await pilot.pause()
        assert len(app.clicked_hrefs) == 1
        assert decode_href(app.clicked_hrefs[0]) == expected

    asyncio.run(run())
