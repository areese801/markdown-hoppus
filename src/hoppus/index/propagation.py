"""
Rename/move inbound-link propagation (spec §6.2, §14).

Renaming or moving a note must rewrite every inbound link across the
vault — wikilinks (``[[Note]]``, ``[[Note|alias]]``, ``[[Note#anchor]]``,
``[[Note#^block]]``), embeds (``![[Note]]``), and standard markdown links
(``[text](Note.md)``) — while leaving unrelated links and prose
byte-identical.

This module is pure and caller-agnostic: ``plan_rename`` computes a
``RenamePlan`` describing which files change and their new full text,
and ``apply_plan`` is a convenience helper that writes the changed
files. Neither function moves the renamed note file itself — the
CRUD caller performs the actual rename and honors the
``files.prompt_before_link_update`` setting.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from hoppus.model import Note
from hoppus.parse.links import Resolver
from hoppus.parse.ofm import (
    _MD_LINK_RE,
    _WIKILINK_RE,
    _blank,
    _mask,
    _parse_md_link,
    _parse_wikilink,
)

_MD_SUFFIX = ".md"


@dataclass
class RenamePlan:
    """
    The computed effect of renaming/moving one note on the vault's links.

    Changed texts are keyed by the notes' *pre-rename* paths, so the
    plan can be applied before the note file itself is moved on disk.

    :param old_path: The note's path before the rename/move.
    :param new_path: The note's path after the rename/move.
    :param changes: Note path → new full text, for every note whose
        inbound links were rewritten. Untouched notes are absent.
    :param link_count: Total number of links rewritten across all notes.
    """

    old_path: Path
    new_path: Path
    changes: dict[Path, str] = field(default_factory=dict)
    link_count: int = 0


def _read_text(path: Path) -> str:
    """
    Read a note file as UTF-8 (the vault encoding, spec §5).
    """
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    """
    Write a note file as UTF-8 (the vault encoding, spec §5).
    """
    path.write_text(text, encoding="utf-8")


def _new_wikilink_inner(raw_inner: str, new_target: str) -> str:
    """
    Rebuild a wikilink's inner text with a new target.

    The raw inner text splits as ``target#anchor|display``; only the
    target portion is replaced, preserving the anchor (heading path or
    ``^block-id``) and display text verbatim.
    """
    target_part, pipe, display = raw_inner.partition("|")
    _, hash_, anchor = target_part.partition("#")
    inner = new_target
    if hash_:
        inner += "#" + anchor
    if pipe:
        inner += "|" + display
    return inner


def _new_md_target(raw_target: str, new_target: str) -> str:
    """
    Rebuild a markdown link's raw target with a new file path.

    Preserves a surrounding ``<...>`` wrapper and any ``#fragment``,
    replacing only the file-path portion with ``new_target + ".md"``.
    """
    wrapped = raw_target.startswith("<") and raw_target.endswith(">")
    inner = raw_target[1:-1] if wrapped else raw_target
    _, hash_, fragment = inner.partition("#")
    rebuilt = new_target + _MD_SUFFIX
    if hash_:
        rebuilt += "#" + fragment
    return f"<{rebuilt}>" if wrapped else rebuilt


def plan_rename(
    old_path: Path,
    new_path: Path,
    notes: Iterable[Note],
    vault_root: Path,
    attachments: Iterable[Path] = (),
    read_text: Callable[[Path], str] = _read_text,
) -> RenamePlan:
    """
    Compute the link rewrites required by renaming/moving one note.

    Every note body is scanned (over masked text, so links inside code
    blocks and frontmatter are never rewritten) and each link is
    resolved against the pre-rename vault state. Links that resolve to
    ``old_path`` have only their target portion rewritten to the
    post-rename shortest unique form (spec §6.2), preserving the embed
    ``!`` prefix, ``|display`` text, ``#anchor``/``#^block`` anchors,
    markdown ``[text]``, and any ``#fragment``. All other links and
    prose stay byte-identical.

    :param old_path: The note's current path (pre-rename).
    :param new_path: The note's path after the rename/move.
    :param notes: Every note in the vault, reflecting the pre-rename
        state (e.g. from ``Index.notes_by_path.values()``).
    :param vault_root: The vault root directory.
    :param attachments: Non-``.md`` vault file paths, for resolution.
    :param read_text: Callable returning a note file's text; defaults
        to a UTF-8 disk read.
    :returns: The plan describing every changed note's new full text.
    :raises ValueError: If no note in ``notes`` sits at ``old_path``.
    """
    pre_notes = list(notes)
    attachment_paths = list(attachments)
    old_note = next((note for note in pre_notes if note.path == old_path), None)
    if old_note is None:
        raise ValueError(f"No note at {old_path} in the provided note set")

    new_note = replace(old_note, title=new_path.stem, path=new_path)
    post_notes = [new_note if note.path == old_path else note for note in pre_notes]

    pre_resolver = Resolver(pre_notes, vault_root, attachments=attachment_paths)
    post_resolver = Resolver(post_notes, vault_root, attachments=attachment_paths)
    new_target = post_resolver.shortest_unique_name(new_note)

    plan = RenamePlan(old_path=old_path, new_path=new_path)
    for note in pre_notes:
        text = read_text(note.path)
        rewritten, count = _rewrite_note(text, note, pre_resolver, old_path, new_target)
        if count:
            plan.changes[note.path] = rewritten
            plan.link_count += count
    return plan


def _rewrite_note(
    text: str,
    note: Note,
    resolver: Resolver,
    old_path: Path,
    new_target: str,
) -> tuple[str, int]:
    """
    Rewrite one note's inbound links to the renamed note.

    Links are located on the masked text (code and frontmatter blanked)
    so their offsets are valid in the original text, then each edit
    replaces only the link's target span in the original. Rewrites
    that would produce identical text (e.g. a same-name folder move
    that keeps every link valid) are skipped.

    :returns: ``(new_text, rewritten_link_count)`` — the text is
        unchanged when the count is zero.
    """
    masked = _mask(text)
    edits: list[tuple[int, int, str]] = []

    for match in _WIKILINK_RE.finditer(masked):
        link = _parse_wikilink(match, note.path)
        if not link.target:
            continue
        if resolver.resolve(link, note) == old_path:
            inner = _new_wikilink_inner(match.group(2), new_target)
            if inner != match.group(2):
                edits.append((match.start(2), match.end(2), inner))

    no_wikilinks = _WIKILINK_RE.sub(_blank, masked)
    for match in _MD_LINK_RE.finditer(no_wikilinks):
        link = _parse_md_link(match, note.path)
        if resolver.resolve(link, note) == old_path:
            target = _new_md_target(match.group(2), new_target)
            if target != match.group(2):
                edits.append((match.start(2), match.end(2), target))

    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, len(edits)


def apply_plan(
    plan: RenamePlan,
    write_text: Callable[[Path, str], None] = _write_text,
) -> None:
    """
    Write every changed note text from a rename plan to disk.

    Only link text is written — the renamed note file itself is not
    moved; the caller performs the actual rename.

    :param plan: The plan produced by ``plan_rename``.
    :param write_text: Callable writing a file's text; defaults to a
        UTF-8 disk write.
    """
    for path, text in plan.changes.items():
        write_text(path, text)
