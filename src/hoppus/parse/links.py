"""
Link resolution rules: filename-based resolution, shortest unique path,
aliases, heading/block anchors (spec §6.2).

The ``Resolver`` is built from a plain collection of ``Note`` objects (plus
optional attachment paths) — it does not depend on the index layer. It
answers two questions:

- Which file does a ``Link`` point at? (``resolve`` — fills ``Link.resolved``.)
- Does the link's ``#Heading`` / ``#^block-id`` anchor exist on that note?
  (``anchor_resolves`` — reported softly; unresolved anchors never block
  resolution, per spec §7.4.)

It also provides the inverse operation needed by link authoring and rename
propagation: ``shortest_unique_name`` returns the fewest leading path
components that make a wikilink to a given note unambiguous.
"""

from pathlib import Path, PurePosixPath

from hoppus.model import Link, Note

_MD_SUFFIX = ".md"


def _target_parts(target: str) -> tuple[str, ...]:
    """
    Split a link target into lowercase path components for matching.

    Wikilink targets always use ``/`` as the separator regardless of
    platform. Matching is case-insensitive, mirroring Obsidian.
    """
    return tuple(part.lower() for part in target.split("/") if part)


class Resolver:
    """
    Resolve ``Link`` objects against a collection of notes (spec §6.2).

    :param notes: Every note in the vault.
    :param vault_root: The vault root directory; note and attachment paths
        are made relative to it for path-component matching.
    :param attachments: Non-``.md`` files in the vault (images, PDFs, …)
        that attachment links may target. Optional.
    """

    def __init__(
        self,
        notes: list[Note],
        vault_root: Path,
        attachments: list[Path] | None = None,
    ) -> None:
        """
        Build lookup structures from the note collection.

        :param notes: Every note in the vault.
        :param vault_root: The vault root directory.
        :param attachments: Non-``.md`` attachment file paths.
        """
        self._vault_root = vault_root
        self._root_parts = vault_root.parts
        self._notes = list(notes)
        self._attachments = list(attachments or [])

        self._note_parts: dict[Path, tuple[str, ...]] = {}
        self._notes_by_path: dict[Path, Note] = {}
        self._alias_index: dict[str, list[Note]] = {}
        for note in self._notes:
            rel = self._relative_parts(note.path)
            self._note_parts[note.path] = (*rel[:-1], PurePosixPath(rel[-1]).stem)
            self._notes_by_path[note.path] = note
            for alias in note.aliases:
                self._alias_index.setdefault(alias.lower(), []).append(note)

        self._attachment_parts: dict[Path, tuple[str, ...]] = {
            path: self._relative_parts(path) for path in self._attachments
        }
        self._all_parts: dict[Path, tuple[str, ...]] = {
            **self._note_parts,
            **self._attachment_parts,
        }

    def _relative_parts(self, path: Path) -> tuple[str, ...]:
        """
        Return a file's lowercase path components relative to the vault root.

        Falls back to the path's own components when the path is not under
        the vault root. Works on the precomputed part tuples (no live
        ``pathlib`` relative_to calls) — this runs once per file at
        construction and is on the hot path for large vaults.
        """
        parts = path.parts
        root = self._root_parts
        if len(parts) > len(root) and parts[: len(root)] == root:
            parts = parts[len(root) :]
        return tuple(part.lower() for part in parts)

    def resolve(self, link: Link, current_note: Note | None = None) -> Path | None:
        """
        Resolve a link to a file path, setting ``link.resolved``.

        Wikilinks resolve by filename (no extension needed for ``.md``;
        attachments must include theirs), then by shortest-unique-path
        disambiguation, then by aliases. Ambiguous targets do not resolve.
        Same-note anchors (``[[#Heading]]``) resolve to ``current_note``.
        Standard markdown links resolve by relative path when they point
        at a known vault file. Anchors never affect resolution here — see
        ``anchor_resolves`` (spec §7.4).

        :param link: The link occurrence to resolve.
        :param current_note: The note containing the link; required only
            for same-note anchor links.
        :returns: The resolved path, or None when the target is missing
            or ambiguous. ``link.resolved`` is set to the same value.
        """
        link.resolved = self._resolve_target(link, current_note)
        return link.resolved

    def _resolve_target(self, link: Link, current_note: Note | None) -> Path | None:
        """
        Compute the resolved path for a link without mutating it.
        """
        if not link.is_wikilink:
            return self._resolve_markdown(link.target)
        if not link.target:
            return current_note.path if current_note is not None else None
        suffix = PurePosixPath(link.target).suffix
        if suffix and suffix.lower() != _MD_SUFFIX:
            return self._match_unique(self._attachment_parts, link.target)
        return self._resolve_note(link.target)

    def _resolve_note(self, target: str) -> Path | None:
        """
        Resolve a note wikilink target, trying filenames then aliases.
        """
        stripped = (
            target[: -len(_MD_SUFFIX)]
            if target.lower().endswith(_MD_SUFFIX)
            else target
        )
        path = self._match_unique(self._note_parts, stripped)
        if path is not None:
            return path
        if "/" not in stripped:
            aliased = self._alias_index.get(stripped.lower(), [])
            if len(aliased) == 1:
                return aliased[0].path
        return None

    def _resolve_markdown(self, target: str) -> Path | None:
        """
        Resolve a standard ``[text](path)`` link against known vault files.

        External URLs and empty targets never resolve. The ``#fragment``
        part is ignored for file resolution.
        """
        if "://" in target:
            return None
        target = target.partition("#")[0]
        if not target:
            return None
        all_files = self._all_parts
        suffix = PurePosixPath(target).suffix
        if suffix.lower() == _MD_SUFFIX:
            target = target[: -len(_MD_SUFFIX)]
            all_files = self._note_parts
        return self._match_unique(all_files, target)

    @staticmethod
    def _match_unique(index: dict[Path, tuple[str, ...]], target: str) -> Path | None:
        """
        Return the single file whose trailing path components match the
        target, or None when there is no match or more than one.
        """
        wanted = _target_parts(target)
        if not wanted:
            return None
        matches = [
            path for path, parts in index.items() if parts[-len(wanted) :] == wanted
        ]
        return matches[0] if len(matches) == 1 else None

    def anchor_resolves(self, link: Link, current_note: Note | None = None) -> bool:
        """
        Report whether a link's anchor exists on its target note.

        ``#Heading`` (including nested ``H1#H2``) is checked against the
        target note's headings; ``#^block-id`` against its block ids.
        Matching is case-insensitive, mirroring Obsidian. Unresolved
        anchors are handled softly by the integrity layer (spec §7.4) —
        this only reports resolvability.

        :param link: The link whose anchor to check. Links without an
            anchor trivially resolve.
        :param current_note: The note containing the link, for same-note
            anchors.
        :returns: True when the anchor (or absence of one) resolves.
        """
        if link.anchor is None:
            return True
        note = self._note_for(link, current_note)
        if note is None:
            return False
        if link.anchor.startswith("^"):
            block_id = link.anchor[1:].lower()
            return any(block_id == known.lower() for known in note.block_ids)
        headings = {heading.lower() for heading in note.headings}
        return all(part.lower() in headings for part in link.anchor.split("#"))

    def _note_for(self, link: Link, current_note: Note | None) -> Note | None:
        """
        Return the Note object a link's anchor should be checked against.
        """
        if not link.target:
            return current_note
        path = self._resolve_target(link, current_note)
        if path is None:
            return None
        return self._notes_by_path.get(path)

    def shortest_unique_name(self, note: Note) -> str:
        """
        Return the shortest wikilink form that uniquely targets a note.

        Uses the fewest trailing path components needed to disambiguate
        (spec §6.2): a note with a vault-unique filename yields its bare
        title; a duplicated filename yields e.g. ``Projects/Alpha``.

        :param note: The target note.
        :returns: The target text to place inside ``[[...]]``.
        """
        rel = note.path
        try:
            rel = note.path.relative_to(self._vault_root)
        except ValueError:
            pass
        parts = (*rel.parts[:-1], rel.stem)
        for count in range(1, len(parts) + 1):
            candidate = "/".join(parts[-count:])
            if self._match_unique(self._note_parts, candidate) == note.path:
                return candidate
        return "/".join(parts)
