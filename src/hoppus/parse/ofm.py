"""
Obsidian-Flavored-Markdown extraction: wikilinks, embeds, block ids, and
inline tags (spec §6.1, §6.4).

This module performs *extraction only* — every link is returned with
``resolved=None``. Resolution against the vault index is a separate concern
(``hoppus.parse.links``, spec §6.2).

Wikilinks, embeds, block ids, and inline tags are Obsidian extensions, not
CommonMark, so they are matched with a dedicated regex layer. ``markdown-it-py``
supplies the block structure needed to exclude fenced/indented code blocks
(and to walk headings), and a small regex pass excludes inline code spans.
"""

import re
from pathlib import Path

from markdown_it import MarkdownIt

from hoppus.model import Link, Tag

_WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+)\]\]")

# Standard markdown link [text](target). The leading (?<!!) excludes
# ![alt](img) markdown images; wikilinks are blanked out before this runs.
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\[\]\n]*)\]\(([^()\n]+)\)")

# Obsidian tags may contain letters, digits, underscores, hyphens, and `/`
# for nesting; they must not be preceded by a word char or another `#`.
_TAG_RE = re.compile(r"(?<![\w#])#([\w/-]+)")

# A trailing block-id definition: `^block-id` at end of line, either after
# whitespace or as the whole line (spec §6.1, last row).
_BLOCK_ID_RE = re.compile(r"(?:^|[ \t])\^([A-Za-z0-9-]+)[ \t]*$", re.MULTILINE)

_INLINE_CODE_RE = re.compile(r"(`+)([^`]+?)\1")

_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")


def _blank(match: re.Match[str]) -> str:
    """
    Return a same-length blank replacement for a regex match.

    Length-preserving replacement keeps every surviving character at its
    original offset, so matches found later still report true positions.
    """
    return " " * len(match.group(0))


def _mask_frontmatter(text: str) -> str:
    """
    Blank out a leading YAML frontmatter block, preserving line structure.

    Frontmatter is delimited by ``---`` on the first line and a closing
    ``---`` (or ``...``) line (spec §6.5). Its content must never yield
    inline tags, links, headings, or block ids.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() in ("---", "..."):
            for j in range(i + 1):
                lines[j] = " " * len(lines[j])
            return "\n".join(lines)
    return text


def _mask_code(text: str) -> str:
    """
    Blank out fenced/indented code blocks and inline code spans.

    Fenced and indented code blocks are located via markdown-it-py's block
    tokens (line maps), then inline code spans are blanked with a regex.
    All replacements are length-preserving.
    """
    lines = text.split("\n")
    for token in MarkdownIt("commonmark").parse(text):
        if token.type in ("fence", "code_block") and token.map:
            start, end = token.map
            for i in range(start, min(end, len(lines))):
                lines[i] = " " * len(lines[i])
    return _INLINE_CODE_RE.sub(_blank, "\n".join(lines))


def _mask(text: str) -> str:
    """
    Blank out frontmatter, code blocks, and inline code spans.
    """
    return _mask_code(_mask_frontmatter(text))


def _parse_wikilink(match: re.Match[str], source: Path) -> Link:
    """
    Build a Link from a ``[[...]]`` / ``![[...]]`` regex match.

    The inner text is split as ``target#anchor|display``; the anchor keeps
    any nested heading path (``H1#H2``) or block marker (``^block-id``)
    verbatim. Attachment size hints (``![[image.png|300]]``) land in
    ``display`` and are harmless to downstream consumers.
    """
    inner = match.group(2)
    target_part, pipe, display = inner.partition("|")
    target, hash_, anchor = target_part.partition("#")
    return Link(
        source=source,
        target=target.strip(),
        anchor=anchor.strip() if hash_ else None,
        display=display.strip() if pipe else None,
        is_embed=match.group(1) == "!",
        is_wikilink=True,
    )


def _parse_md_link(match: re.Match[str], source: Path) -> Link:
    """
    Build a Link from a standard ``[text](target)`` regex match.

    The target is kept raw (minus surrounding ``<...>``), including any
    ``#fragment`` — markdown links are the portable form and are only
    report-checked, never anchor-resolved (spec §7.4).
    """
    target = match.group(2).strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return Link(
        source=source,
        target=target,
        display=match.group(1),
        is_embed=False,
        is_wikilink=False,
    )


def extract_links(text: str, source: Path) -> list[Link]:
    """
    Extract every OFM and standard markdown link occurrence, in document
    order (spec §6.1).

    Covers plain/display/anchored wikilinks, same-note anchors, note and
    attachment embeds, and ``[text](target)`` markdown links. Links inside
    code fences and inline code spans are excluded. No resolution happens
    here — every returned Link has ``resolved=None``.

    :param text: Full note text (frontmatter included; it is skipped).
    :param source: Path of the note containing the links.
    :returns: Links in the order they appear in the text.
    """
    masked = _mask(text)
    found: list[tuple[int, Link]] = []
    for match in _WIKILINK_RE.finditer(masked):
        found.append((match.start(), _parse_wikilink(match, source)))
    no_wikilinks = _WIKILINK_RE.sub(_blank, masked)
    for match in _MD_LINK_RE.finditer(no_wikilinks):
        found.append((match.start(), _parse_md_link(match, source)))
    found.sort(key=lambda pair: pair[0])
    return [link for _, link in found]


def extract_tags(text: str) -> set[Tag]:
    """
    Extract inline ``#tag`` occurrences, including nested ``#area/sub``
    (spec §6.4).

    Excluded: ``#`` inside fenced code blocks and inline code spans,
    heading lines starting with ``#``, tag-like text inside wikilinks
    (e.g. ``[[#Heading]]``), and purely numeric names (not valid Obsidian
    tags). Frontmatter ``tags:`` are a separate concern
    (``hoppus.parse.frontmatter``).

    :param text: Full note text.
    :returns: The set of inline tags found.
    """
    masked = _WIKILINK_RE.sub(_blank, _mask(text))
    lines = [
        " " * len(line) if _ATX_HEADING_RE.match(line) else line
        for line in masked.split("\n")
    ]
    tags = set()
    for match in _TAG_RE.finditer("\n".join(lines)):
        name = match.group(1)
        if not re.fullmatch(r"[\d/]+", name):
            tags.add(Tag(name))
    return tags


def extract_headings(text: str) -> list[str]:
    """
    Extract heading texts in document order.

    Uses markdown-it-py block parsing, so both ATX (``# H``) and setext
    headings are found, and ``#`` lines inside code fences are not.

    :param text: Full note text (frontmatter included; it is skipped).
    :returns: Heading texts, in document order.
    """
    tokens = MarkdownIt("commonmark").parse(_mask_frontmatter(text))
    return [
        tokens[i + 1].content.strip()
        for i, token in enumerate(tokens)
        if token.type == "heading_open"
    ]


def extract_block_ids(text: str) -> list[str]:
    """
    Extract trailing ``^block-id`` definitions, in document order
    (spec §6.1, last row).

    A block id trails a paragraph or list item (or sits on its own line
    immediately after a block). Ids inside code blocks, inline code, and
    wikilinks (``[[Note#^block-id]]``) are excluded. The returned ids do
    not include the leading ``^``.

    :param text: Full note text.
    :returns: Block ids, in document order.
    """
    masked = _WIKILINK_RE.sub(_blank, _mask(text))
    return [match.group(1) for match in _BLOCK_ID_RE.finditer(masked)]
