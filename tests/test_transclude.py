"""
Unit tests for the pure transclusion helpers (HOPPUS-31).

Covers ``slice_heading_section`` (including nested ``H1#H2`` anchors,
section-until-next-same-level extent, and missing headings),
``slice_block``, ``block_line``, and ``expand_embeds`` across the note /
heading / block / attachment / unresolved cases plus the depth and cycle
guards. No TUI is involved anywhere in this file.
"""

from pathlib import Path

from hoppus.render.ofm_markdown import LinkTarget
from hoppus.render.transclude import (
    block_line,
    expand_embeds,
    slice_block,
    slice_heading_section,
)

DOC = """\
---
tags: [x]
---

# Title

Intro paragraph. ^intro

## Section One

Alpha content. ^block-1

- item one ^block-2
- item two

### Sub Section

Nested content.

## Section Two

Omega content.

```python
code = True  # ^fake-id
```
"""


# -- slice_heading_section ---------------------------------------------------


def test_slice_section_until_next_same_level_heading() -> None:
    """
    A section runs from its heading to just before the next same-level one.
    """
    section = slice_heading_section(DOC, "Section One")
    assert section is not None
    assert section.startswith("## Section One")
    assert "Alpha content." in section
    assert "### Sub Section" in section
    assert "Nested content." in section
    assert "Section Two" not in section


def test_slice_section_nested_anchor_uses_last_segment() -> None:
    """
    A nested ``H1#H2`` anchor matches on its last segment.
    """
    nested = slice_heading_section(DOC, "Title#Section Two")
    assert nested is not None
    assert nested.startswith("## Section Two")
    assert "Omega content." in nested


def test_slice_section_matches_case_insensitively() -> None:
    """
    Heading matching is case-insensitive, mirroring Obsidian.
    """
    assert slice_heading_section(DOC, "sECTION oNE") == slice_heading_section(
        DOC, "Section One"
    )


def test_slice_section_top_level_runs_to_end() -> None:
    """
    An H1 with no later same-or-higher heading extends to end of text.
    """
    section = slice_heading_section(DOC, "Title")
    assert section is not None
    assert "Intro paragraph." in section
    assert "Omega content." in section


def test_slice_section_sub_section_stops_at_higher_level() -> None:
    """
    An H3 section ends at the next H2 (a higher-level heading).
    """
    section = slice_heading_section(DOC, "Sub Section")
    assert section is not None
    assert "Nested content." in section
    assert "Section Two" not in section


def test_slice_section_missing_heading_returns_none() -> None:
    """
    A heading absent from the note yields None.
    """
    assert slice_heading_section(DOC, "No Such Heading") is None


# -- slice_block / block_line --------------------------------------------------


def test_slice_block_paragraph_strips_marker() -> None:
    """
    A paragraph block is returned without its ``^block-id`` marker.
    """
    assert slice_block(DOC, "block-1") == "Alpha content."


def test_slice_block_accepts_leading_caret() -> None:
    """
    The block id may be passed with or without its leading ``^``.
    """
    assert slice_block(DOC, "^block-1") == slice_block(DOC, "block-1")


def test_slice_block_list_item_is_innermost_block() -> None:
    """
    A block id on a list item returns just that item, not the whole list.
    """
    block = slice_block(DOC, "block-2")
    assert block == "- item one"


def test_slice_block_missing_or_masked_returns_none() -> None:
    """
    Absent ids and ids inside code fences yield None.
    """
    assert slice_block(DOC, "nope") is None
    assert slice_block(DOC, "fake-id") is None


def test_block_line_reports_zero_based_source_line() -> None:
    """
    ``block_line`` returns the 0-based line index carrying the id.
    """
    lines = DOC.split("\n")
    line = block_line(DOC, "block-1")
    assert line is not None
    assert lines[line] == "Alpha content. ^block-1"
    assert block_line(DOC, "^intro") == lines.index("Intro paragraph. ^intro")
    assert block_line(DOC, "missing") is None
    assert block_line(DOC, "fake-id") is None


# -- expand_embeds -------------------------------------------------------------


_TARGET_TEXT = """\
---
tags: [t]
---

# B

Body of B with a [[Wikilink]].

## Sec

Sec content line. ^bid
"""


def _make_vault() -> tuple[dict[Path, str], object, object]:
    """
    Build an in-memory fake vault with resolve/read_text callables.

    :returns: ``(files, resolve, read_text)`` for ``expand_embeds``.
    """
    files = {
        Path("/v/B.md"): _TARGET_TEXT,
        Path("/v/A.md"): "a-top ![[B]] a-bottom",
    }
    by_name = {path.stem: path for path in files}
    by_name["image.png"] = Path("/v/attachments/image.png")

    def resolve(target: LinkTarget) -> Path | None:
        return by_name.get(target.target)

    def read_text(path: Path) -> str:
        return files[path]

    return files, resolve, read_text


def test_expand_note_embed_strips_frontmatter() -> None:
    """
    ``![[Note]]`` becomes the note's body with frontmatter removed.
    """
    _, resolve, read_text = _make_vault()
    out = expand_embeds("before ![[B]] after", resolve=resolve, read_text=read_text)
    assert "Body of B" in out
    assert "Sec content line." in out
    assert "tags: [t]" not in out
    assert out.startswith("before ")
    assert out.endswith(" after")


def test_expand_heading_and_block_embeds() -> None:
    """
    Heading/block embeds transclude just the matching slice.
    """
    _, resolve, read_text = _make_vault()
    heading = expand_embeds("![[B#Sec]]", resolve=resolve, read_text=read_text)
    assert "Sec content line." in heading
    assert "Body of B" not in heading
    block = expand_embeds("![[B#^bid]]", resolve=resolve, read_text=read_text)
    assert block == "Sec content line."


def test_expand_missing_anchor_yields_placeholder() -> None:
    """
    An embed whose anchor is absent falls back to a placeholder line.
    """
    _, resolve, read_text = _make_vault()
    out = expand_embeds("![[B#Nope]]", resolve=resolve, read_text=read_text)
    assert "missing anchor" in out
    assert out.startswith("> ")
    out = expand_embeds("![[B#^nope]]", resolve=resolve, read_text=read_text)
    assert "missing anchor" in out


def test_expand_attachment_embed_yields_placeholder() -> None:
    """
    An embed resolving to a non-``.md`` file shows the attachment line.
    """
    _, resolve, read_text = _make_vault()
    out = expand_embeds("![[image.png|300]]", resolve=resolve, read_text=read_text)
    assert "📎" in out
    assert "image.png" in out
    assert "attachment" in out


def test_expand_unresolved_embed_yields_placeholder() -> None:
    """
    An embed with no resolvable target shows the unresolved line.
    """
    _, resolve, read_text = _make_vault()
    out = expand_embeds("![[Ghost]]", resolve=resolve, read_text=read_text)
    assert "unresolved embed" in out
    assert "Ghost" in out


def test_expand_preserves_non_embed_text_and_code() -> None:
    """
    Wikilinks, code fences, and inline code pass through verbatim.
    """
    _, resolve, read_text = _make_vault()
    text = "See [[B]].\n\n```\n![[B]]\n```\n\nInline `![[B]]` too.\n"
    assert expand_embeds(text, resolve=resolve, read_text=read_text) == text


def test_expand_default_depth_is_one_level() -> None:
    """
    Nested embeds inside transcluded content survive verbatim at depth 1.
    """
    _, resolve, read_text = _make_vault()
    out = expand_embeds("top ![[A]]", resolve=resolve, read_text=read_text)
    assert "a-top" in out
    assert "![[B]]" in out
    assert "Body of B" not in out


def test_expand_cycle_guard_terminates() -> None:
    """
    Mutually embedding notes (A ↔ B) terminate at any depth.
    """
    files = {
        Path("/v/A.md"): "a-top ![[B]]",
        Path("/v/B.md"): "b-top ![[A]]",
    }
    by_name = {path.stem: path for path in files}

    def resolve(target: LinkTarget) -> Path | None:
        return by_name.get(target.target)

    def read_text(path: Path) -> str:
        return files[path]

    out = expand_embeds(
        files[Path("/v/A.md")], resolve=resolve, read_text=read_text, depth=10
    )
    assert "b-top" in out
    assert "a-top" in out
    assert "cyclic embed" in out
