"""
Link-integrity core: the correct-or-create pass on editor return
(spec §7.3, §9.6, §19 D1).

This module is the pure, unit-testable heart of the hard no-dangling
wikilink rule. It finds unresolved note wikilinks (with fuzzy "did you
mean?" suggestions), rewrites a single occurrence in place while
preserving ``|display`` and ``#anchor`` parts, and bootstraps missing
notes at the vault root for the "none of these" path — leaving the
prose exactly as written (Option A, spec §7.3).

Per D1, the interactive flow that consumes these helpers runs only on
return from a ``$EDITOR`` session; vault-open and watcher events are
report-only and never prompt. Attachment refs (``![[image.png]]``) and
standard markdown links are excluded here — they are report-only per
spec §7.4.

No Textual imports: the TUI (``hoppus.tui.app`` and the
``LinkIntegrityModal``) only supplies the decision loop.
"""

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from hoppus.fileops import create_note
from hoppus.index.indexer import Index
from hoppus.model import Link, Note
from hoppus.parse.links import Resolver
from hoppus.parse.ofm import _WIKILINK_RE, _mask, _parse_wikilink
from hoppus.search.fuzzy import rank

#: File extensions treated as attachments — unresolved refs to these are
#: report-only (spec §7.4), never part of the correct-or-create prompts.
_ATTACHMENT_EXTS = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "bmp",
        "svg",
        "webp",
        "avif",
        "ico",
        "pdf",
        "mp3",
        "mp4",
        "mov",
        "mkv",
        "wav",
        "ogg",
        "flac",
        "m4a",
        "webm",
        "zip",
        "tar",
        "gz",
        "7z",
    }
)

#: A user decision for one unresolved link: ``("correct", note)``,
#: ``("create", None)``, or ``("skip", None)``.
Decision = tuple[str, Note | None]


@dataclass(frozen=True)
class UnresolvedWikilink:
    """
    One unresolved wikilink occurrence flagged by the §7.3 hard rule.

    :param link: The parsed link (``resolved`` is None).
    :param start: Offset of the full ``[[...]]``/``![[...]]`` occurrence
        in the original text (including the ``!`` for embeds).
    :param end: End offset of the occurrence in the original text.
    :param suggestions: Near-match candidate notes, best first.
    """

    link: Link
    start: int
    end: int
    suggestions: tuple[Note, ...]


def _is_attachment_target(target: str) -> bool:
    """
    Report whether a wikilink target points at an attachment file.

    A target whose final path component ends in a known non-``.md``
    file extension (image, media, archive, PDF, …) is an attachment
    ref — report-only per spec §7.4, never prompted on.
    """
    suffix = PurePosixPath(target.partition("#")[0].strip()).suffix.lower()
    return suffix.lstrip(".") in _ATTACHMENT_EXTS if suffix else False


def find_unresolved_wikilinks(
    text: str,
    index: Index,
    current_note_path: Path,
    *,
    limit_suggestions: int = 5,
) -> list[UnresolvedWikilink]:
    """
    Find the §7.3 hard-rule violations in a note's text, in document
    order.

    Links are located on the masked text (frontmatter, code fences, and
    inline code blanked, mirroring ``hoppus.index.propagation``), so
    their offsets are valid in the original text and links inside code
    are never flagged. Collected occurrences are wikilinks (including
    note embeds ``![[Note]]``) whose target is a note — not an
    attachment (§7.4), not a same-note anchor — and does not resolve
    against the vault index. Each carries fuzzy "did you mean?"
    suggestions ranked over every note's title and aliases.

    :param text: The note's full original text.
    :param index: The built vault index to resolve against.
    :param current_note_path: Path of the note containing the links.
    :param limit_suggestions: Maximum number of near-match suggestions.
    :returns: Unresolved occurrences in document order.
    """
    notes = list(index.notes_by_path.values())
    resolver = Resolver(notes, index.vault_root, attachments=index._attachments)
    current_note = index.notes_by_path.get(current_note_path)

    unresolved: list[UnresolvedWikilink] = []
    for match in _WIKILINK_RE.finditer(_mask(text)):
        link = _parse_wikilink(match, current_note_path)
        if not link.target or _is_attachment_target(link.target):
            continue
        if resolver.resolve(link, current_note) is not None:
            continue
        ranked = rank(
            link.target,
            notes,
            key=lambda note: [note.title, *note.aliases],
            limit=limit_suggestions,
        )
        unresolved.append(
            UnresolvedWikilink(
                link=link,
                start=match.start(),
                end=match.end(),
                suggestions=tuple(note for note, _score in ranked),
            )
        )
    return unresolved


def correct_target(text: str, unresolved: UnresolvedWikilink, new_target: str) -> str:
    """
    Rewrite one unresolved occurrence's target in place (spec §7.3).

    Only the span ``[unresolved.start:unresolved.end]`` changes: the
    inner text is rebuilt as ``new_target#anchor|display``, preserving
    the ``#anchor`` and ``|display`` parts exactly and re-prefixing the
    embed ``!`` when present. All other prose stays byte-identical.

    :param text: The note's full original text.
    :param unresolved: The occurrence to correct.
    :param new_target: The corrected target (typically the chosen
        note's shortest unique name).
    :returns: The text with that one occurrence corrected.
    """
    link = unresolved.link
    inner = new_target
    if link.anchor:
        inner += f"#{link.anchor}"
    if link.display:
        inner += f"|{link.display}"
    bang = "!" if link.is_embed else ""
    return f"{text[: unresolved.start]}{bang}[[{inner}]]{text[unresolved.end :]}"


def resolve_new_note_target(target: str) -> str:
    """
    Return the bare note name to create for a "none of these" decision.

    Strips any ``#anchor`` and ``|display`` parts, leaving just the
    note portion of the target (``Link.target`` is already bare, but
    raw inner text is accepted too).

    :param target: The unresolved link target.
    :returns: The note name (no ``.md`` extension).
    """
    return target.partition("#")[0].partition("|")[0].strip()


def create_missing_note(vault_root: Path, target: str) -> Path:
    """
    Bootstrap the missing note for a "none of these" decision (§7.3).

    Creates an empty note at the vault root via
    :func:`hoppus.fileops.create_note` (GTD convention, spec §5.2). The
    prose containing the link is left exactly as written — Option A.

    :param vault_root: The vault root directory.
    :param target: The unresolved link target.
    :returns: The path of the created note.
    :raises ValueError: If the derived name is invalid.
    :raises FileExistsError: If the note already exists.
    """
    return create_note(vault_root, resolve_new_note_target(target))


def apply_integrity_decisions(
    text: str,
    unresolved: Sequence[UnresolvedWikilink],
    decisions: Sequence[Decision],
    vault_root: Path,
    resolver: Resolver,
) -> tuple[str, list[Path]]:
    """
    Apply the user's per-link decisions to a note's text (spec §7.3).

    Decisions pair positionally with ``unresolved`` (both in document
    order). ``("correct", note)`` rewrites that occurrence to the
    note's shortest unique name; ``("create", None)`` bootstraps the
    missing note at the vault root and leaves the prose untouched
    (Option A); ``("skip", None)`` leaves everything as-is.

    Offset handling: all corrections are collected first and applied in
    reverse offset order, so every occurrence's original span stays
    valid — earlier edits can never shift a later (already recorded)
    span. Two corrections in one file therefore both land exactly.
    Duplicate "create" decisions for the same note name create it once.

    :param text: The note's full original text (the text the
        ``unresolved`` offsets were computed against).
    :param unresolved: The unresolved occurrences, in document order.
    :param decisions: One decision per occurrence, same order.
    :param vault_root: The vault root, for note creation.
    :param resolver: Resolver used to render each corrected target as
        its shortest unique name.
    :returns: ``(new_text, created_paths)``.
    :raises ValueError: If the sequences differ in length.
    """
    if len(unresolved) != len(decisions):
        raise ValueError(
            f"Got {len(decisions)} decisions for {len(unresolved)} unresolved links"
        )

    corrections: list[tuple[UnresolvedWikilink, str]] = []
    created: list[Path] = []
    created_names: set[str] = set()
    for occurrence, (action, note) in zip(unresolved, decisions, strict=True):
        if action == "correct":
            if note is None:
                raise ValueError("A 'correct' decision requires a note")
            corrections.append((occurrence, resolver.shortest_unique_name(note)))
        elif action == "create":
            name = resolve_new_note_target(occurrence.link.target)
            if name not in created_names:
                created.append(create_missing_note(vault_root, name))
                created_names.add(name)
        elif action != "skip":
            raise ValueError(f"Unknown decision action: {action!r}")

    for occurrence, new_target in sorted(
        corrections, key=lambda pair: pair[0].start, reverse=True
    ):
        text = correct_target(text, occurrence, new_target)
    return text, created


def resolve_editor_command(environ: Mapping[str, str]) -> list[str]:
    """
    Resolve the editor argv prefix from the environment (spec §9.6).

    Rule: prefer ``$VISUAL``, then ``$EDITOR`` — each split with
    ``shlex`` so values carrying flags (``"code --wait"``) are honored
    — else fall back to ``["nvim"]``. The fallback is returned
    unconditionally without checking that the binary exists; ``vi`` is
    the caller's second fallback if launching ``nvim`` fails. This
    keeps the function pure and env-only.

    :param environ: The process environment (e.g. ``os.environ``).
    :returns: The editor argv prefix; append the file path to run it.
    """
    for var in ("VISUAL", "EDITOR"):
        value = environ.get(var, "").strip()
        if value:
            return shlex.split(value)
    return ["nvim"]
