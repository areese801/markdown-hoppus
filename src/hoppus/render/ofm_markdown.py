"""
OFM → navigable Markdown transform for Textual's ``Markdown`` widget
(spec §9.5, §19 D3 spike — HOPPUS-24).

Decision: **pre-transform, not a custom widget.** Textual's ``Markdown`` /
``MarkdownViewer`` are CommonMark and render ``[[wikilinks]]`` / ``![[embeds]]``
as literal text, never emitting ``LinkClicked``. Rather than forking the widget
(large surface, breaks on Textual upgrades), :func:`transform_ofm` rewrites each
OFM link into a standard ``[label](href)`` link whose href uses a custom
``hoppus://`` scheme carrying the navigation payload as query parameters
(``target``, ``anchor``, ``display``, ``embed``). The widget then renders and
emits ``LinkClicked`` for them natively; :func:`decode_href` turns the clicked
href back into a :class:`LinkTarget` the app hands to the ``Resolver``
(spec §6.2). Ordinary Markdown links, code blocks/spans, and frontmatter pass
through untouched.

Encoding contract: query values are percent-encoded **twice**. markdown-it
preserves existing ``%XX`` escapes when normalizing hrefs, and Textual's
``LinkClicked`` unquotes the href exactly once — so a clicked href arrives
singly-encoded and :func:`decode_href` applies the final unquote. This keeps
``#``, ``&``, ``=``, and spaces in note titles/anchors unambiguous end to end.

Anchor scrolling in preview: for heading anchors, ``Markdown.goto_anchor()``
scrolls to the slugged heading block (nested ``H1#H2`` anchors use the last
segment). Block anchors (``^block-id``) have no widget id; the preview story
must map the block id to its source line via the note text and scroll the
containing ``MarkdownBlock`` (each block carries its source line range).

Limitations: embeds render as clickable links, not inline transclusions —
inline rendering is the preview story's job (it can pre-expand ``![[...]]``
content before calling this transform). Attachment size hints (``|300``) are
carried in ``display`` and ignored here.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from hoppus.model import Link
from hoppus.parse import ofm

SCHEME = "hoppus"

_HREF_PREFIX = f"{SCHEME}://note?"

_SOURCE_UNUSED = Path("unused.md")


@dataclass(frozen=True)
class LinkTarget:
    """
    The navigation payload decoded from a clicked ``hoppus://`` href.

    Mirrors the link-identifying fields of :class:`hoppus.model.Link` so the
    app can resolve it (spec §6.2) and scroll to the anchor.

    :param target: Raw wikilink target (``""`` for same-note anchors).
    :param anchor: Heading (``"Heading"``, nested ``"H1#H2"``) or block
        (``"^block-id"``) anchor, or None.
    :param display: Per-link display text, or None.
    :param is_embed: True if the source link was an ``![[...]]`` embed.
    """

    target: str
    anchor: str | None = None
    display: str | None = None
    is_embed: bool = False


def _encode_value(value: str) -> str:
    """
    Percent-encode a query value twice.

    Textual's ``LinkClicked`` unquotes the href once before delivery, so
    double-encoding here means the clicked href is still singly-encoded and
    reserved characters (``#``, ``&``, ``=``, spaces) never appear raw.
    """
    return quote(quote(value, safe=""), safe="")


def encode_href(link: Link) -> str:
    """
    Build a ``hoppus://`` href carrying a wikilink's navigation payload.

    :param link: An extracted OFM wikilink (``is_wikilink=True``).
    :returns: An href safe to place in a standard Markdown link.
    """
    params = [("target", link.target)]
    if link.anchor is not None:
        params.append(("anchor", link.anchor))
    if link.display is not None:
        params.append(("display", link.display))
    if link.is_embed:
        params.append(("embed", "1"))
    query = "&".join(f"{key}={_encode_value(value)}" for key, value in params)
    return _HREF_PREFIX + query


def decode_href(href: str) -> LinkTarget | None:
    """
    Decode a clicked ``hoppus://`` href back into a :class:`LinkTarget`.

    Expects the href as delivered by ``Markdown.LinkClicked`` (Textual has
    already unquoted it once); this applies the final unquote per value.

    :param href: The ``LinkClicked.href`` value.
    :returns: The decoded target, or None if the href is not ours (e.g. an
        ordinary ``http://`` link the app should open normally).
    """
    if not href.startswith(_HREF_PREFIX):
        return None
    fields: dict[str, str] = {}
    for pair in href[len(_HREF_PREFIX) :].split("&"):
        key, sep, value = pair.partition("=")
        if sep:
            fields[key] = unquote(value)
    return LinkTarget(
        target=fields.get("target", ""),
        anchor=fields.get("anchor"),
        display=fields.get("display"),
        is_embed=fields.get("embed") == "1",
    )


def _label(link: Link) -> str:
    """
    Choose the visible label for a transformed wikilink.

    Matches what the raw OFM shows the reader: the display text if given,
    else ``target``/``target#anchor``/``#anchor`` as written.
    """
    if link.display:
        return link.display
    if link.anchor is not None:
        return f"{link.target}#{link.anchor}" if link.target else f"#{link.anchor}"
    return link.target


def _rewrite(match: re.Match[str]) -> str:
    """
    Rewrite one wikilink/embed regex match as a standard Markdown link.
    """
    link = ofm._parse_wikilink(match, _SOURCE_UNUSED)
    return f"[{_label(link)}]({encode_href(link)})"


def transform_ofm(text: str) -> str:
    """
    Rewrite OFM wikilinks and embeds into standard, navigable Markdown links.

    Every §6.1 wikilink variant — plain, ``|display``, ``#Heading`` /
    ``#^block-id`` anchors, same-note ``[[#...]]``, and ``![[...]]`` embeds —
    becomes ``[label](hoppus://...)``, which Textual's ``Markdown`` widget
    renders as a link and reports via ``LinkClicked``. Ordinary Markdown
    links, frontmatter, code fences, and inline code are left untouched.

    :param text: Full note text (frontmatter included).
    :returns: The transformed Markdown, line structure preserved.
    """
    masked = ofm._mask(text)
    out: list[str] = []
    cursor = 0
    for match in ofm._WIKILINK_RE.finditer(masked):
        out.append(text[cursor : match.start()])
        out.append(_rewrite(match))
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)
