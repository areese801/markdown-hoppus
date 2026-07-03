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

    Thin single-target wrapper around ``_rewrite_targets``.

    :returns: ``(new_text, rewritten_link_count)`` — the text is
        unchanged when the count is zero.
    """
    return _rewrite_targets(text, note, resolver, {old_path: new_target})


def _rewrite_targets(
    text: str,
    note: Note,
    resolver: Resolver,
    targets: dict[Path, str],
) -> tuple[str, int]:
    """
    Rewrite one note's links to any of several target notes.

    Links are located on the masked text (code and frontmatter blanked)
    so their offsets are valid in the original text, then each edit
    replaces only the link's target span in the original. Each link is
    resolved with ``resolver`` and, when it resolves to a key of
    ``targets``, its target portion is rewritten to the mapped new form
    — preserving the embed ``!`` prefix, ``|display`` text,
    ``#anchor``/``#^block`` anchors, markdown ``[text]``, and any
    ``#fragment``. Rewrites that would produce identical text (e.g. a
    same-name folder move that keeps every link valid) are skipped.

    :param text: The note's full original text.
    :param note: The note containing the links.
    :param resolver: The resolver representing the vault state the
        links were written against.
    :param targets: Resolved note path → new shortest-unique target.
    :returns: ``(new_text, rewritten_link_count)`` — the text is
        unchanged when the count is zero.
    """
    masked = _mask(text)
    edits: list[tuple[int, int, str]] = []

    for match in _WIKILINK_RE.finditer(masked):
        link = _parse_wikilink(match, note.path)
        if not link.target:
            continue
        new_target = targets.get(resolver.resolve(link, note))
        if new_target is not None:
            inner = _new_wikilink_inner(match.group(2), new_target)
            if inner != match.group(2):
                edits.append((match.start(2), match.end(2), inner))

    no_wikilinks = _WIKILINK_RE.sub(_blank, masked)
    for match in _MD_LINK_RE.finditer(no_wikilinks):
        link = _parse_md_link(match, note.path)
        new_target = targets.get(resolver.resolve(link, note))
        if new_target is not None:
            target = _new_md_target(match.group(2), new_target)
            if target != match.group(2):
                edits.append((match.start(2), match.end(2), target))

    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, len(edits)


def resimplify_plan(
    pre_notes: Iterable[Note],
    post_notes: Iterable[Note],
    vault_root: Path,
    *,
    attachments: Iterable[Path] = (),
    read_text: Callable[[Path], str] = _read_text,
    skip: set[Path] | frozenset[Path] = frozenset(),
) -> dict[Path, str]:
    """
    Re-simplify links whose targets' shortest unique names changed (D6).

    A rename/move/delete changes which filenames are vault-unique, so
    links to *unrelated* notes may need to gain or shed leading path
    components to stay correct and minimal (spec §6.2). For every note
    present in both states whose ``shortest_unique_name`` differs
    between a pre-state and a post-state resolver, all inbound links to
    that note (across the post-state notes) are rewritten to its new
    minimal form — preserving display text, anchors, embed prefixes,
    markdown ``[text]``, and fragments, and never touching links inside
    code blocks or frontmatter.

    Links are resolved against the *pre* state (the state they were
    written against), so a link that lost uniqueness (a bare
    ``[[Alpha]]`` made ambiguous by a rename) is still attributed to
    the note it originally pointed at rather than silently pointing at
    the wrong note.

    :param pre_notes: Every note in the vault before the operation.
    :param post_notes: Every note in the vault after the operation.
    :param vault_root: The vault root directory.
    :param attachments: Non-``.md`` vault file paths, for resolution.
    :param read_text: Callable returning a note file's text; defaults
        to a UTF-8 disk read.
    :param skip: Post-state note paths to leave untouched (e.g. the
        renamed note itself, so this composes with ``plan_rename``
        without double-rewriting).
    :returns: Note path → new full text, for every changed note.
    """
    changes, _ = _resimplify(
        list(pre_notes),
        list(post_notes),
        vault_root,
        attachments=list(attachments),
        read_text=read_text,
        skip=skip,
    )
    return changes


def _resimplify(
    pre_notes: list[Note],
    post_notes: list[Note],
    vault_root: Path,
    *,
    attachments: list[Path],
    read_text: Callable[[Path], str],
    skip: set[Path] | frozenset[Path],
) -> tuple[dict[Path, str], int]:
    """
    Compute re-simplification rewrites plus the rewritten-link count.

    Shared implementation behind ``resimplify_plan`` (which drops the
    count) and ``plan_rename_full`` (which folds it into the plan's
    ``link_count``).

    :returns: ``(changes, rewritten_link_count)``.
    """
    pre_resolver = Resolver(pre_notes, vault_root, attachments=attachments)
    post_resolver = Resolver(post_notes, vault_root, attachments=attachments)
    pre_by_path = {note.path: note for note in pre_notes}

    targets: dict[Path, str] = {}
    for note in post_notes:
        pre_note = pre_by_path.get(note.path)
        if pre_note is None:
            continue
        post_name = post_resolver.shortest_unique_name(note)
        if pre_resolver.shortest_unique_name(pre_note) != post_name:
            targets[note.path] = post_name

    changes: dict[Path, str] = {}
    count = 0
    if not targets:
        return changes, count
    for note in post_notes:
        if note.path in skip:
            continue
        text = read_text(note.path)
        rewritten, rewrites = _rewrite_targets(text, note, pre_resolver, targets)
        if rewrites:
            changes[note.path] = rewritten
            count += rewrites
    return changes, count


def plan_rename_full(
    old_path: Path,
    new_path: Path,
    notes: Iterable[Note],
    vault_root: Path,
    *,
    attachments: Iterable[Path] = (),
    read_text: Callable[[Path], str] = _read_text,
) -> RenamePlan:
    """
    Plan a rename including uniqueness re-simplification (D6).

    Runs the normal ``plan_rename`` (rewriting inbound links to the
    renamed note) and merges in ``resimplify_plan`` for every *other*
    note whose shortest unique name changed as a side effect of the
    rename. A note edited by both passes gets both sets of rewrites
    applied sequentially to the same text.

    :param old_path: The note's current path (pre-rename).
    :param new_path: The note's path after the rename/move.
    :param notes: Every note in the vault, reflecting the pre-rename
        state (e.g. from ``Index.notes_by_path.values()``).
    :param vault_root: The vault root directory.
    :param attachments: Non-``.md`` vault file paths, for resolution.
    :param read_text: Callable returning a note file's text; defaults
        to a UTF-8 disk read.
    :returns: The merged plan describing every changed note's new text.
    :raises ValueError: If no note in ``notes`` sits at ``old_path``.
    """
    pre_notes = list(notes)
    attachment_paths = list(attachments)
    plan = plan_rename(
        old_path,
        new_path,
        pre_notes,
        vault_root,
        attachments=attachment_paths,
        read_text=read_text,
    )

    old_note = next(note for note in pre_notes if note.path == old_path)
    new_note = replace(old_note, title=new_path.stem, path=new_path)
    post_notes = [new_note if note.path == old_path else note for note in pre_notes]

    def _layered_read(path: Path) -> str:
        """
        Read a note's text, preferring the rename pass's rewritten text.
        """
        if path in plan.changes:
            return plan.changes[path]
        return read_text(path)

    changes, count = _resimplify(
        pre_notes,
        post_notes,
        vault_root,
        attachments=attachment_paths,
        read_text=_layered_read,
        skip={old_path, new_path},
    )
    plan.changes.update(changes)
    plan.link_count += count
    return plan


def duplicates_existing_name(new_path: Path, notes: Iterable[Note]) -> Path | None:
    """
    Detect a rename-into-existing-name collision (D6).

    Renaming a note so its filename stem duplicates another note's stem
    in a different folder is allowed by Obsidian (duplicate stems live
    in different folders) but must not silently merge — callers use
    this predicate to warn and trigger the lost-uniqueness handling.
    Matching is case-insensitive, mirroring Obsidian. Renaming onto the
    exact same path as an existing file is refused upstream by
    ``hoppus.fileops`` and is out of scope here.

    :param new_path: The proposed post-rename note path.
    :param notes: Every note in the vault (pre-rename state); when the
        rename keeps the stem and only moves folders, the caller should
        exclude the note being moved.
    :returns: The path of an existing note in a different folder with
        the same stem, or None when the new name is vault-unique.
    """
    stem = new_path.stem.lower()
    for note in notes:
        if note.path.parent != new_path.parent and note.path.stem.lower() == stem:
            return note.path
    return None


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
