"""
YAML frontmatter parsing and round-trip-preserving writes via ruamel.yaml
(spec §6.5, decision D4).

Frontmatter is a YAML block delimited by ``---`` lines at the very top of
a note. Recognized keys are ``aliases`` and ``tags``, but arbitrary user
keys are always preserved (spec §6.5). Writes go through ruamel.yaml in
round-trip mode so that updating a single key leaves every other line of
the note byte-identical (spec §14, decision D4).
"""

import io
import re
from typing import Any

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap

_DELIMITER = "---"


def _make_yaml() -> YAML:
    """
    Build a ruamel YAML instance configured for round-trip preservation.

    Round-trip (``typ="rt"``) mode keeps key order and comments;
    ``preserve_quotes`` keeps original scalar quoting; the indent settings
    match Obsidian's conventional ``  - item`` sequence style; a very
    large width prevents re-wrapping of long scalar lines.

    :returns: A configured ``YAML`` instance.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 2**16
    return yaml


def _split_raw(text: str) -> tuple[str | None, str]:
    """
    Split note text into the raw frontmatter YAML string and the body.

    :param text: Full note text.
    :returns: ``(raw_yaml, body)`` where ``raw_yaml`` is the text between
        the ``---`` delimiters (excluding them), or ``None`` when the note
        has no frontmatter block; ``body`` is everything after the closing
        delimiter line (or the full text when there is no frontmatter).
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _DELIMITER:
        return None, text
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == _DELIMITER:
            raw = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return raw, body
    return None, text


def split_frontmatter(text: str) -> tuple[CommentedMap, str]:
    """
    Parse a note's leading YAML frontmatter block.

    Handles notes with no frontmatter (empty mapping, full text returned
    as body), an empty frontmatter block, and standard frontmatter.

    Invalid frontmatter — a block that fails to parse as YAML (e.g.
    Obsidian template placeholders like ``{{date}}``) or that parses to
    a non-mapping — is treated as frontmatter-less, matching Obsidian's
    leniency: an empty mapping is returned and the note stays usable.
    Use :func:`frontmatter_parse_error` to detect and report this case.

    :param text: Full note text.
    :returns: ``(frontmatter, body)`` where ``frontmatter`` is a
        round-trip-capable mapping (empty when absent or invalid) and
        ``body`` is the note text after the closing delimiter.
    """
    raw, body = _split_raw(text)
    if raw is None:
        return CommentedMap(), body
    try:
        data = _make_yaml().load(raw)
    except YAMLError:
        return CommentedMap(), body
    if data is None or not isinstance(data, CommentedMap):
        return CommentedMap(), body
    return data, body


def frontmatter_parse_error(text: str) -> str | None:
    """
    Report why a note's frontmatter block is invalid, if it is.

    Complements the leniency of :func:`split_frontmatter`: callers that
    surface problems (the audit path) use this to flag notes whose
    frontmatter was silently treated as absent.

    :param text: Full note text.
    :returns: A one-line description of the parse failure, or None when
        the note has no frontmatter block or it parses to a mapping (or
        an empty block).
    """
    raw, _body = _split_raw(text)
    if raw is None:
        return None
    try:
        data = _make_yaml().load(raw)
    except YAMLError as error:
        first_line = str(error).strip().splitlines()
        return first_line[0] if first_line else "invalid YAML"
    if data is not None and not isinstance(data, CommentedMap):
        return f"frontmatter is not a mapping (got {type(data).__name__})"
    return None


def _key_line_spans(
    raw: str,
) -> tuple[list[str], list[tuple[Any, int, int]]] | None:
    """
    Map each top-level frontmatter key to its line span within ``raw``.

    A key's span runs from its own starting line up to (but excluding)
    the next top-level key's starting line; the last key's span extends
    to the end of the block, so trailing comments and blank lines belong
    to the preceding key.

    :param raw: The raw frontmatter YAML text (between the delimiters).
    :returns: ``(lines, spans)`` where ``lines`` is ``raw`` split with
        line endings kept and ``spans`` is ``[(key, start, end), ...]``
        in source order — or None when spans cannot be computed (invalid
        YAML, a non-mapping, an empty block, or keys not at column 0).
    """
    try:
        data = _make_yaml().load(raw)
    except YAMLError:
        return None
    if not isinstance(data, CommentedMap) or not data:
        return None
    lines = raw.splitlines(keepends=True)
    starts: list[tuple[int, Any]] = []
    for entry in data.keys():
        position = data.lc.data.get(entry)
        if position is None or position[1] != 0:
            return None
        starts.append((position[0], entry))
    starts.sort()
    spans = [
        (entry, line_no, starts[i + 1][0] if i + 1 < len(starts) else len(lines))
        for i, (line_no, entry) in enumerate(starts)
    ]
    return lines, spans


def _splice_key_update(old_raw: str, new_raw: str, key: str) -> str | None:
    """
    Rebuild an updated frontmatter block preserving untouched keys' bytes.

    Takes every unchanged key's lines verbatim from ``old_raw`` (keeping
    blank lines, trailing spaces, comments, and non-standard whitespace
    exactly) and only the updated key's lines from the ruamel re-emit in
    ``new_raw``. A brand-new key is appended after the original block.
    Lines before the first key (leading blank lines or comments, which
    ruamel drops on re-emit) are preserved from ``old_raw``.

    :param old_raw: The original raw frontmatter YAML text.
    :param new_raw: The full ruamel re-emit with ``key`` updated.
    :param key: The single key that was set.
    :returns: The spliced raw frontmatter text, or None when either side
        cannot be span-mapped (caller falls back to ``new_raw``).
    """
    old_view = _key_line_spans(old_raw)
    new_view = _key_line_spans(new_raw)
    if old_view is None or new_view is None:
        return None
    old_lines, old_spans = old_view
    new_lines, new_spans = new_view
    new_span_by_key = {entry: (start, end) for entry, start, end in new_spans}
    if key not in new_span_by_key:
        return None
    pieces = ["".join(old_lines[: old_spans[0][1]])]
    replaced = False
    for entry, start, end in old_spans:
        if entry == key:
            new_start, new_end = new_span_by_key[key]
            pieces.append("".join(new_lines[new_start:new_end]))
            replaced = True
        else:
            pieces.append("".join(old_lines[start:end]))
    if not replaced:
        new_start, new_end = new_span_by_key[key]
        pieces.append("".join(new_lines[new_start:new_end]))
    return "".join(pieces)


def update_frontmatter_key(text: str, key: str, value: Any) -> str:
    """
    Set one frontmatter key and re-emit the full note text.

    Uses ruamel.yaml round-trip mode (decision D4), then splices the
    re-emitted block so that only the updated key's lines change: every
    other frontmatter line — including blank lines inside the block,
    trailing spaces after empty-valued keys (``tags: ``), comments,
    quoting, and non-standard whitespace — and the entire body are
    preserved byte-for-byte. Setting a key to its current value is a
    byte-identical no-op. When the note has no frontmatter block, one is
    created at the top.

    Benign normalization (D4): the updated key's own line is re-emitted
    canonically by ruamel, so non-standard spacing on that one line
    (``key:   value``) collapses to a single space and an inline comment
    stays anchored to its original column.

    :param text: Full note text.
    :param key: Frontmatter key to set or replace.
    :param value: New value for the key.
    :returns: The full note text with only that key changed.
    """
    raw, body = _split_raw(text)
    yaml = _make_yaml()
    data = yaml.load(raw) if raw is not None else None
    if data is None:
        data = CommentedMap()
    if isinstance(data, CommentedMap) and key in data and data[key] == value:
        return text
    data[key] = value
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    new_raw = buffer.getvalue()
    if raw:
        spliced = _splice_key_update(raw, new_raw, key)
        if spliced is not None:
            new_raw = spliced
    return f"{_DELIMITER}\n{new_raw}{_DELIMITER}\n{body}"


def get_aliases(frontmatter: CommentedMap) -> list[str]:
    """
    Return the ``aliases`` frontmatter key as a list of strings.

    Accepts either a YAML list or a single string value.

    :param frontmatter: Parsed frontmatter mapping.
    :returns: Alias names, or an empty list when absent.
    """
    raw = frontmatter.get("aliases")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]


def get_tags(frontmatter: CommentedMap) -> list[str]:
    """
    Return the ``tags`` frontmatter key as a list of strings.

    Handles both a YAML list and a space/comma-delimited string form
    (spec §6.4).

    :param frontmatter: Parsed frontmatter mapping.
    :returns: Tag names, or an empty list when absent.
    """
    raw = frontmatter.get("tags")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [tag for tag in re.split(r"[,\s]+", raw) if tag]
    return [str(item) for item in raw]
