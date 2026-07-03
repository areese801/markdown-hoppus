"""
Pure transclusion helpers for embed rendering and anchor scrolling
(spec §9.5 — HOPPUS-31).

Textual's ``Markdown`` widget cannot render ``![[...]]`` embeds inline, so
the preview pane pre-expands them into the note text before the OFM →
Markdown transform. Everything here is TUI-free and deterministic:

- :func:`slice_heading_section` / :func:`slice_block` cut a heading section
  or a ``^block-id`` block out of a note's text, using markdown-it token
  line maps (never a hand-rolled Markdown parser).
- :func:`block_line` maps a block id to its 0-based source line, so the
  preview can scroll a ``[[Note#^block-id]]`` click to the right block.
- :func:`expand_embeds` replaces every embed with transcluded content, one
  level deep by default, with a depth *and* cycle guard so mutually
  embedding notes (``A`` embeds ``B`` embeds ``A``) always terminate.

Unresolvable or non-transcludable embeds become subtle single-line
placeholders (blockquotes) rather than errors — link *repair* is Epic 5's
concern, not this module's.
"""

import re
from collections.abc import Callable
from pathlib import Path

from markdown_it import MarkdownIt

from hoppus.model import Link
from hoppus.parse import ofm
from hoppus.parse.frontmatter import split_frontmatter
from hoppus.render.ofm_markdown import LinkTarget

_EMBED_SOURCE = Path("embed.md")


def _heading_tokens(text: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """
    Parse a note into its lines and heading positions.

    Frontmatter is masked length-preservingly first, so token line maps
    still index into the original text.

    :param text: Full note text (frontmatter included).
    :returns: ``(lines, headings)`` where ``headings`` holds
        ``(start_line, level, heading_text)`` triples in document order.
    """
    lines = text.split("\n")
    tokens = MarkdownIt("commonmark").parse(ofm._mask_frontmatter(text))
    headings: list[tuple[int, int, str]] = []
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and token.map is not None:
            headings.append(
                (token.map[0], int(token.tag[1]), tokens[i + 1].content.strip())
            )
    return lines, headings


def slice_heading_section(text: str, heading: str) -> str | None:
    """
    Return a heading's section: the heading line through the line before
    the next heading of the same or higher level.

    Matching is on heading text, case-insensitive. A nested anchor path
    (``H1#H2``) matches on its last segment, mirroring how Obsidian
    anchors resolve to a single heading.

    :param text: Full note text (frontmatter included).
    :param heading: Heading text or nested ``H1#H2`` anchor path.
    :returns: The section text, or None when no heading matches.
    """
    wanted = heading.split("#")[-1].strip().lower()
    lines, headings = _heading_tokens(text)
    for position, (start, level, found) in enumerate(headings):
        if found.lower() != wanted:
            continue
        end = len(lines)
        for later_start, later_level, _ in headings[position + 1 :]:
            if later_level <= level:
                end = later_start
                break
        return "\n".join(lines[start:end])
    return None


def _find_block_id(text: str, block_id: str) -> re.Match[str] | None:
    """
    Locate a trailing ``^block-id`` definition in a note's text.

    Uses the same masking as ``ofm.extract_block_ids`` — code blocks,
    inline code, and wikilinks are blanked first (length-preservingly),
    so ids inside them never match. Matching is case-insensitive.

    :param text: Full note text.
    :param block_id: The block id, with or without the leading ``^``.
    :returns: The regex match against the masked text, or None.
    """
    wanted = block_id.lstrip("^").lower()
    masked = ofm._WIKILINK_RE.sub(ofm._blank, ofm._mask(text))
    for match in ofm._BLOCK_ID_RE.finditer(masked):
        if match.group(1).lower() == wanted:
            return match
    return None


def block_line(text: str, block_id: str) -> int | None:
    """
    Return the 0-based source line index carrying a ``^block-id``.

    Used by the preview pane to map a clicked block anchor onto the
    Markdown widget's block source ranges for scrolling.

    :param text: Full note text.
    :param block_id: The block id, with or without the leading ``^``.
    :returns: The line index, or None when the id is not defined.
    """
    match = _find_block_id(text, block_id)
    if match is None:
        return None
    return text.count("\n", 0, match.start(1))


def slice_block(text: str, block_id: str) -> str | None:
    """
    Return the block (paragraph, list item, or line) carrying a trailing
    ``^block-id``, with the id marker itself stripped.

    The block's extent comes from markdown-it token line maps: the
    innermost block token whose range covers the id's line wins (so a
    list item is returned, not its whole list). When no token covers the
    line, the single line is returned.

    :param text: Full note text.
    :param block_id: The block id, with or without the leading ``^``.
    :returns: The block text without the ``^block-id`` marker, or None
        when the id is not defined.
    """
    line = block_line(text, block_id)
    if line is None:
        return None
    lines = text.split("\n")
    start, end = line, line + 1
    best_span: int | None = None
    for token in MarkdownIt("commonmark").parse(ofm._mask_frontmatter(text)):
        if token.map is None or not token.map[0] <= line < token.map[1]:
            continue
        span = token.map[1] - token.map[0]
        if best_span is None or span <= best_span:
            start, end, best_span = token.map[0], token.map[1], span
    block = lines[start:end]
    marker = re.compile(rf"[ \t]*\^{re.escape(block_id.lstrip('^'))}[ \t]*$", re.I)
    block[line - start] = marker.sub("", block[line - start])
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(block)


def _transclusion(
    link: Link,
    resolve: Callable[[LinkTarget], Path | None],
    read_text: Callable[[Path], str],
    depth: int,
    seen: frozenset[Path],
) -> str:
    """
    Compute the replacement text for one ``![[...]]`` embed occurrence.

    :param link: The parsed embed ``Link``.
    :param resolve: Callable resolving a ``LinkTarget`` to a vault path.
    :param read_text: Callable reading a path's text content.
    :param depth: Remaining expansion depth for nested embeds.
    :param seen: Resolved paths already being transcluded (cycle guard).
    :returns: The transcluded content or a single-line placeholder.
    """
    written = link.target or f"#{link.anchor}"
    target = LinkTarget(
        target=link.target, anchor=link.anchor, display=link.display, is_embed=True
    )
    resolved = resolve(target)
    if resolved is None:
        return f"> ⚠ unresolved embed: {written}"
    if resolved.suffix.lower() != ".md":
        return f"> 📎 **{resolved.name}** — attachment (open in system app)"
    if resolved in seen:
        return f"> ⚠ cyclic embed: {written}"
    try:
        raw = read_text(resolved)
    except OSError:
        return f"> ⚠ unreadable embed: {written}"
    if link.anchor is None:
        _, content = split_frontmatter(raw)
        content = content.strip("\n")
    elif link.anchor.startswith("^"):
        sliced = slice_block(raw, link.anchor[1:])
        if sliced is None:
            return f"> ⚠ missing anchor in embed: {written}#{link.anchor}"
        content = sliced
    else:
        sliced = slice_heading_section(raw, link.anchor)
        if sliced is None:
            return f"> ⚠ missing anchor in embed: {written}#{link.anchor}"
        content = sliced
    return _expand(content, resolve, read_text, depth - 1, seen | {resolved})


def _expand(
    text: str,
    resolve: Callable[[LinkTarget], Path | None],
    read_text: Callable[[Path], str],
    depth: int,
    seen: frozenset[Path],
) -> str:
    """
    Replace every embed in ``text``, recursing while ``depth`` remains.

    Non-embed text (including plain ``[[wikilinks]]``, code, and
    frontmatter) is preserved verbatim; embeds inside code blocks or
    inline code never expand (they are masked out of detection).
    """
    if depth <= 0:
        return text
    masked = ofm._mask(text)
    out: list[str] = []
    cursor = 0
    for match in ofm._WIKILINK_RE.finditer(masked):
        if match.group(1) != "!":
            continue
        link = ofm._parse_wikilink(match, _EMBED_SOURCE)
        out.append(text[cursor : match.start()])
        out.append(_transclusion(link, resolve, read_text, depth, seen))
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


def expand_embeds(
    text: str,
    *,
    resolve: Callable[[LinkTarget], Path | None],
    read_text: Callable[[Path], str],
    depth: int = 1,
) -> str:
    """
    Replace each ``![[...]]`` embed in a note's text with transcluded
    content (spec §9.5).

    Rules, per embed:

    - Note embed (``![[Note]]``) → the target note's body, frontmatter
      stripped.
    - Heading / block embed (``![[Note#H]]`` / ``![[Note#^id]]``) → the
      matching section or block; a missing anchor yields a placeholder.
    - Attachment embed (resolves to a non-``.md`` file) → a placeholder
      line with an "open in system app" affordance.
    - Unresolved embed → a subtle placeholder line.

    Expansion is one level deep by default; nested embeds inside
    transcluded content survive verbatim unless ``depth`` allows more.
    A cycle guard tracks the chain of transcluded files, so mutually
    embedding notes terminate at any depth. All other text is preserved
    verbatim.

    :param text: Full note text (frontmatter included; it is preserved).
    :param resolve: Callable resolving a ``LinkTarget`` to a vault path
        (typically ``PreviewPane.resolve_target``).
    :param read_text: Callable reading a path's text content.
    :param depth: Maximum expansion depth (levels of nested embeds).
    :returns: The text with embeds expanded.
    """
    return _expand(text, resolve, read_text, depth, frozenset())
