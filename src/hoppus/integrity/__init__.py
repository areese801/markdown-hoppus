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

The report-only surface lives here too (spec §10, §19 D1):
:func:`audit_vault` walks a built index and collects every link issue —
unresolved wikilinks (§7.3), broken anchors, missing attachments, and
broken markdown links (§7.4), each gated by its ``link_integrity``
toggle (§7.6) — into an :class:`AuditReport` via :func:`audit_note`.
This is the shared core behind ``hop audit`` and the TUI "problems"
indicator. It never prompts, mutates, or creates anything.
:func:`should_prompt_on` is the pure gate that keeps vault-open and
watcher triggers report-only unless ``link_integrity.prompt_on`` is
``"always"``.

No Textual imports: the TUI (``hoppus.tui.app`` and the
``LinkIntegrityModal``) only supplies the decision loop.
"""

import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from hoppus.fileops import create_note
from hoppus.index.indexer import Index
from hoppus.model import Link, Note
from hoppus.parse.links import Resolver
from hoppus.parse.ofm import (
    _MD_LINK_RE,
    _WIKILINK_RE,
    _blank,
    _mask,
    _parse_md_link,
    _parse_wikilink,
)
from hoppus.search.fuzzy import rank

#: Issue kinds surfaced by the report-only audit (spec §7.3, §7.4).
KIND_UNRESOLVED_WIKILINK = "unresolved_wikilink"
KIND_BROKEN_ANCHOR = "broken_anchor"
KIND_MISSING_ATTACHMENT = "missing_attachment"
KIND_BROKEN_MARKDOWN_LINK = "broken_markdown_link"

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


@dataclass(frozen=True)
class AuditIssue:
    """
    One vault-content problem surfaced by the report-only audit
    (spec §10, §19 D1).

    :param note_path: Path of the note containing the issue.
    :param target: The link's raw target as written.
    :param kind: One of the ``KIND_*`` constants:
        :data:`KIND_UNRESOLVED_WIKILINK`, :data:`KIND_BROKEN_ANCHOR`,
        :data:`KIND_MISSING_ATTACHMENT`, or
        :data:`KIND_BROKEN_MARKDOWN_LINK`.
    :param display: The link's ``|display`` text, or None.
    :param anchor: The link's ``#anchor`` part, or None.
    :param is_wikilink: True for ``[[...]]``; False for ``[text](path)``.
    :param is_embed: True for ``![[...]]`` embeds.
    """

    note_path: Path
    target: str
    kind: str
    display: str | None = None
    anchor: str | None = None
    is_wikilink: bool = True
    is_embed: bool = False


@dataclass(frozen=True)
class AuditReport:
    """
    The report-only outcome of a vault audit (spec §10, §19 D1).

    A small, serialization-friendly record: just the issue tuple plus
    counting/grouping conveniences for the CLI and the TUI indicator.

    :param issues: Every collected issue, in stable (path, document)
        order.
    """

    issues: tuple[AuditIssue, ...]

    @property
    def count(self) -> int:
        """Total number of issues."""
        return len(self.issues)

    def by_note(self) -> dict[Path, list[AuditIssue]]:
        """
        Group the issues by note path, preserving the stable order.

        :returns: Mapping of note path to its issues, keys in first-seen
            order (which is sorted-path order for :func:`audit_vault`).
        """
        grouped: dict[Path, list[AuditIssue]] = {}
        for issue in self.issues:
            grouped.setdefault(issue.note_path, []).append(issue)
        return grouped

    def note_count(self) -> int:
        """Number of distinct notes with at least one issue."""
        return len({issue.note_path for issue in self.issues})

    def by_kind(self) -> dict[str, list[AuditIssue]]:
        """
        Group the issues by kind, preserving the stable order.

        :returns: Mapping of issue kind to its issues, keys in
            first-seen order.
        """
        grouped: dict[str, list[AuditIssue]] = {}
        for issue in self.issues:
            grouped.setdefault(issue.kind, []).append(issue)
        return grouped


def _integrity_toggles(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """
    Return the ``link_integrity`` section of a config, or an empty
    mapping (every toggle then falls back to its default: on).
    """
    if config is None:
        return {}
    return config.get("link_integrity", {})


def _issue_from_link(note_path: Path, link: Link, kind: str) -> AuditIssue:
    """
    Build an :class:`AuditIssue` carrying a link's written form.
    """
    return AuditIssue(
        note_path=note_path,
        target=link.target,
        kind=kind,
        display=link.display,
        anchor=link.anchor,
        is_wikilink=link.is_wikilink,
        is_embed=link.is_embed,
    )


def audit_note(
    text: str,
    index: Index,
    note_path: Path,
    *,
    config: Mapping[str, Any] | None = None,
) -> list[AuditIssue]:
    """
    Collect every report-only link issue in one note's text, in
    document order (spec §7.3, §7.4).

    Links are located on the masked text (frontmatter, code fences, and
    inline code blanked), so links inside code are never flagged. Four
    checks run, each gated by its ``link_integrity`` toggle (spec §7.6;
    a None config means all four are on, matching the defaults):

    - Unresolved note-target wikilinks (the §7.3 hard rule, via
      :func:`find_unresolved_wikilinks`), gated by
      ``enforce_no_dangling_wikilinks`` →
      :data:`KIND_UNRESOLVED_WIKILINK`.
    - Wikilinks/embeds whose attachment target does not resolve, and
      markdown links to a missing attachment, gated by
      ``report_missing_attachments`` → :data:`KIND_MISSING_ATTACHMENT`.
    - Other markdown links ``[text](path)`` that do not resolve
      (external URLs and bare ``#fragment`` targets are never flagged),
      gated by ``report_broken_markdown_links`` →
      :data:`KIND_BROKEN_MARKDOWN_LINK`.
    - Wikilinks that resolve to a note (including same-note anchors)
      whose ``#Heading``/``#^block`` anchor does not resolve there,
      gated by ``warn_missing_anchor`` → :data:`KIND_BROKEN_ANCHOR`.
      Never auto-created — report only.

    :param text: The note's full original text.
    :param index: The built vault index to resolve against.
    :param note_path: Path of the note containing the links.
    :param config: The merged configuration mapping (spec §12), or None
        for all checks on.
    :returns: The note's issues, in document order.
    """
    toggles = _integrity_toggles(config)
    check_wikilinks = toggles.get("enforce_no_dangling_wikilinks", True)
    check_anchors = toggles.get("warn_missing_anchor", True)
    check_attachments = toggles.get("report_missing_attachments", True)
    check_md_links = toggles.get("report_broken_markdown_links", True)

    notes = list(index.notes_by_path.values())
    resolver = Resolver(notes, index.vault_root, attachments=index._attachments)
    current_note = index.notes_by_path.get(note_path)
    masked = _mask(text)

    found: list[tuple[int, AuditIssue]] = []

    if check_wikilinks:
        for occurrence in find_unresolved_wikilinks(text, index, note_path):
            found.append(
                (
                    occurrence.start,
                    _issue_from_link(
                        note_path, occurrence.link, KIND_UNRESOLVED_WIKILINK
                    ),
                )
            )

    for match in _WIKILINK_RE.finditer(masked):
        link = _parse_wikilink(match, note_path)
        if link.target and _is_attachment_target(link.target):
            if check_attachments and resolver.resolve(link, current_note) is None:
                found.append(
                    (
                        match.start(),
                        _issue_from_link(note_path, link, KIND_MISSING_ATTACHMENT),
                    )
                )
            continue
        if resolver.resolve(link, current_note) is None:
            continue
        if check_anchors and not resolver.anchor_resolves(link, current_note):
            found.append(
                (
                    match.start(),
                    _issue_from_link(note_path, link, KIND_BROKEN_ANCHOR),
                )
            )

    for match in _MD_LINK_RE.finditer(_WIKILINK_RE.sub(_blank, masked)):
        link = _parse_md_link(match, note_path)
        if "://" in link.target or not link.target.partition("#")[0].strip():
            continue
        if resolver.resolve(link, current_note) is not None:
            continue
        if _is_attachment_target(link.target):
            if check_attachments:
                found.append(
                    (
                        match.start(),
                        _issue_from_link(note_path, link, KIND_MISSING_ATTACHMENT),
                    )
                )
        elif check_md_links:
            found.append(
                (
                    match.start(),
                    _issue_from_link(note_path, link, KIND_BROKEN_MARKDOWN_LINK),
                )
            )

    found.sort(key=lambda pair: pair[0])
    return [issue for _, issue in found]


def audit_vault(
    index: Index,
    *,
    config: Mapping[str, Any] | None = None,
    read_text: Callable[[Path], str] | None = None,
) -> AuditReport:
    """
    Collect every report-only link issue in a vault (D1, spec §7.4).

    Iterates the index's notes in stable (sorted-path) order, reads
    each note's text, and records the :class:`AuditIssue` list produced
    by :func:`audit_note` — unresolved wikilinks, broken anchors,
    missing attachments, and broken markdown links, each gated by its
    ``link_integrity`` toggle (spec §7.6). A None config runs all four
    checks, matching the config defaults. Notes whose text cannot be
    read (``OSError``) are skipped. This is the shared core behind
    ``hop audit`` and the TUI "problems" indicator: it never prompts,
    mutates, or creates anything.

    :param index: The built vault index to audit.
    :param config: The merged configuration mapping (spec §12), or None
        for all checks on.
    :param read_text: Optional reader (for tests); defaults to
        ``path.read_text(encoding="utf-8")``.
    :returns: The collected report.
    """
    if read_text is None:

        def read_text(path: Path) -> str:
            return path.read_text(encoding="utf-8")

    issues: list[AuditIssue] = []
    for note_path in sorted(index.notes_by_path):
        try:
            text = read_text(note_path)
        except OSError:
            continue
        issues.extend(audit_note(text, index, note_path, config=config))
    return AuditReport(issues=tuple(issues))


def should_prompt_on(config: Mapping[str, Any], trigger: str) -> bool:
    """
    Decide whether a trigger may run the interactive integrity pass.

    This is the pure D1 gate that prevents a prompt-storm: only
    ``"editor_return"`` always prompts. ``"open"`` and ``"watcher"``
    prompt only when ``link_integrity.prompt_on`` is ``"always"``
    (default ``"editor_return_only"`` keeps them report-only). Any
    unknown trigger is False.

    :param config: The merged configuration mapping (spec §12).
    :param trigger: One of ``"editor_return"``, ``"open"``,
        ``"watcher"``.
    :returns: True when the trigger may prompt interactively.
    """
    if trigger == "editor_return":
        return True
    if trigger in ("open", "watcher"):
        prompt_on = config.get("link_integrity", {}).get(
            "prompt_on", "editor_return_only"
        )
        return prompt_on == "always"
    return False


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
