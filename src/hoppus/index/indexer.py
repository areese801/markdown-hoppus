"""
Vault scan and in-memory index: notes, links, backlinks, tags, and
title/alias indexes with incremental updates (spec §8).

The index is built by scanning every ``.md`` file under a vault root
(skipping ``.obsidian/`` and ``.hoppus/``), parsing each into a ``Note``
plus its outbound ``Link``s and tags, and resolving all links with a
``Resolver`` (spec §6.2). Unlinked mentions are computed on demand
elsewhere, and file watching lives in ``hoppus.index.watcher`` — this
module only reads files, never writes them.
"""

import os
from pathlib import Path, PurePosixPath

from hoppus.model import Link, Note
from hoppus.parse.frontmatter import get_aliases, get_tags, split_frontmatter
from hoppus.parse.links import Resolver
from hoppus.parse.ofm import (
    extract_block_ids,
    extract_headings,
    extract_links,
    extract_tags,
)

_MD_SUFFIX = ".md"
_SKIP_DIRS = frozenset({".obsidian", ".hoppus"})


def build_note(path: Path) -> Note:
    """
    Build a ``Note`` from a ``.md`` file on disk.

    The title is the filename stem (spec §5.1); aliases come from
    frontmatter; headings and block ids from the OFM parser; the word
    count from the body (frontmatter excluded); and ``mtime`` from the
    file stat.

    :param path: Path to the ``.md`` file.
    :returns: The populated ``Note``.
    :raises OSError: If the file cannot be read or stat-ed.
    """
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    return Note(
        title=path.stem,
        path=path,
        aliases=get_aliases(frontmatter),
        headings=extract_headings(text),
        block_ids=extract_block_ids(text),
        frontmatter=frontmatter,
        word_count=len(body.split()),
        mtime=path.stat().st_mtime,
    )


def _scan_file(path: Path) -> tuple[Note, list[Link], set[str]]:
    """
    Parse one ``.md`` file into its note, outbound links, and tag names.

    Tags are the union of inline ``#tags`` and frontmatter ``tags:``
    (spec §6.4). Links are returned unresolved.

    :param path: Path to the ``.md`` file.
    :returns: ``(note, links, tags)``.
    """
    text = path.read_text(encoding="utf-8")
    note = build_note(path)
    links = extract_links(text, path)
    tags = {tag.name for tag in extract_tags(text)} | set(get_tags(note.frontmatter))
    return note, links, tags


def _iter_vault_files(vault_root: Path) -> tuple[list[Path], list[Path]]:
    """
    Recursively list a vault's note and attachment files.

    Skips ``.obsidian/`` and ``.hoppus/`` directories (spec §5.2).

    :param vault_root: The vault root directory.
    :returns: ``(note_paths, attachment_paths)``, each sorted for
        deterministic index construction.
    """
    note_paths: list[Path] = []
    attachment_paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if filename.lower().endswith(_MD_SUFFIX):
                note_paths.append(path)
            else:
                attachment_paths.append(path)
    return note_paths, attachment_paths


def _target_tail(target: str) -> str:
    """
    Return a link target's final path component, lowercased, without a
    ``.md`` extension.

    Used to decide whether a target's resolution could be affected by a
    change to a note of a given name.
    """
    tail = PurePosixPath(target.partition("#")[0]).name.lower()
    if tail.endswith(_MD_SUFFIX):
        tail = tail[: -len(_MD_SUFFIX)]
    return tail


class Index:
    """
    In-memory vault index (spec §8).

    Attributes:

    - ``notes``: title → ``Note``. With duplicate titles (e.g. two
      ``Alpha.md`` files), this holds one of them arbitrarily — use
      ``title_index`` or ``notes_by_path`` for authoritative lookup.
    - ``notes_by_path``: note path → ``Note`` (authoritative note map).
    - ``links``: note path → outbound ``Link``s, each with ``resolved``
      set (``None`` for unresolved or ambiguous targets).
    - ``backlinks``: note path → set of note paths that link to it
      (linked mentions).
    - ``tags``: tag name → set of note paths carrying that tag.
    - ``note_tags``: note path → set of tag names (inline ∪ frontmatter,
      spec §6.4).
    - ``title_index``: lowercase title → list of note paths (duplicates
      preserved).
    - ``alias_index``: lowercase alias → list of note paths.
    """

    def __init__(self, vault_root: Path) -> None:
        """
        Create an empty index rooted at a vault directory.

        :param vault_root: The vault root directory.
        """
        self.vault_root = vault_root
        self.notes: dict[str, Note] = {}
        self.notes_by_path: dict[Path, Note] = {}
        self.links: dict[Path, list[Link]] = {}
        self.backlinks: dict[Path, set[Path]] = {}
        self.tags: dict[str, set[Path]] = {}
        self.note_tags: dict[Path, set[str]] = {}
        self.title_index: dict[str, list[Path]] = {}
        self.alias_index: dict[str, list[Path]] = {}
        self._attachments: list[Path] = []

    @classmethod
    def build(cls, vault_root: Path) -> "Index":
        """
        Scan a vault and build a fully-resolved index (spec §8).

        Reads every ``.md`` file under the vault root (skipping
        ``.obsidian/`` and ``.hoppus/``), then resolves all links with a
        ``Resolver`` built from the complete note set and computes
        backlinks.

        :param vault_root: The vault root directory.
        :returns: The populated index.
        """
        index = cls(vault_root)
        note_paths, index._attachments = _iter_vault_files(vault_root)
        for path in note_paths:
            note, links, tags = _scan_file(path)
            index._add_note(note, links, tags)
        resolver = index._make_resolver()
        for path in index.links:
            index._resolve_note_links(resolver, path)
        index._rebuild_backlinks()
        return index

    def reindex_file(self, path: Path) -> None:
        """
        Incrementally re-index a single ``.md`` file (spec §8).

        Handles all three cases: a new file (add), a changed file
        (update), and a deleted file (remove — when the path no longer
        exists on disk). Only the affected file is re-parsed; links in
        other notes are re-resolved only when their resolution could
        have changed (they pointed at this note, were unresolved, or
        target a name this note carried before or after the change).
        Backlinks are recomputed from the updated link maps.

        :param path: Path to the ``.md`` file that changed.
        """
        path = Path(path)
        affected_names: set[str] = set()
        old_note = self.notes_by_path.get(path)
        if old_note is not None:
            affected_names.add(old_note.title.lower())
            affected_names.update(alias.lower() for alias in old_note.aliases)
            self._remove_note(old_note)
        if path.is_file():
            note, links, tags = _scan_file(path)
            affected_names.add(note.title.lower())
            affected_names.update(alias.lower() for alias in note.aliases)
            self._add_note(note, links, tags)
        resolver = self._make_resolver()
        for source, note_links in self.links.items():
            if source == path:
                self._resolve_note_links(resolver, source)
                continue
            current = self.notes_by_path[source]
            for link in note_links:
                if (
                    link.resolved is None
                    or link.resolved == path
                    or _target_tail(link.target) in affected_names
                ):
                    resolver.resolve(link, current)
        self._rebuild_backlinks()

    def _make_resolver(self) -> Resolver:
        """
        Build a ``Resolver`` over the current note set and attachments.
        """
        return Resolver(
            list(self.notes_by_path.values()),
            self.vault_root,
            attachments=self._attachments,
        )

    def _resolve_note_links(self, resolver: Resolver, path: Path) -> None:
        """
        Resolve every outbound link of one note, setting ``Link.resolved``.
        """
        current = self.notes_by_path[path]
        for link in self.links[path]:
            resolver.resolve(link, current)

    def _add_note(self, note: Note, links: list[Link], tags: set[str]) -> None:
        """
        Insert one note and its parsed links/tags into all maps.

        Links are stored unresolved; the caller resolves them once the
        resolver reflects the full note set.
        """
        self.notes[note.title] = note
        self.notes_by_path[note.path] = note
        self.links[note.path] = links
        self.note_tags[note.path] = tags
        for tag in tags:
            self.tags.setdefault(tag, set()).add(note.path)
        self.title_index.setdefault(note.title.lower(), []).append(note.path)
        for alias in note.aliases:
            self.alias_index.setdefault(alias.lower(), []).append(note.path)

    def _remove_note(self, note: Note) -> None:
        """
        Remove one note and its links/tags from all maps.

        When a duplicate title remains after removal, ``notes`` is
        repointed at one of the surviving notes.
        """
        del self.notes_by_path[note.path]
        del self.links[note.path]
        for tag in self.note_tags.pop(note.path, set()):
            paths = self.tags.get(tag)
            if paths is not None:
                paths.discard(note.path)
                if not paths:
                    del self.tags[tag]
        self._drop_from_name_index(self.title_index, note.title, note.path)
        for alias in note.aliases:
            self._drop_from_name_index(self.alias_index, alias, note.path)
        if self.notes.get(note.title) is note:
            del self.notes[note.title]
            survivor = next(
                (n for n in self.notes_by_path.values() if n.title == note.title),
                None,
            )
            if survivor is not None:
                self.notes[note.title] = survivor

    @staticmethod
    def _drop_from_name_index(
        index: dict[str, list[Path]], name: str, path: Path
    ) -> None:
        """
        Remove one path from a lowercase-name index, pruning empty entries.
        """
        paths = index.get(name.lower())
        if paths is None:
            return
        if path in paths:
            paths.remove(path)
        if not paths:
            del index[name.lower()]

    def _rebuild_backlinks(self) -> None:
        """
        Recompute the backlinks map from the current resolved links.

        Backlinks record note-to-note linked mentions only: entries
        exist for every indexed note, and only resolved link targets
        that are notes contribute. Self-links are excluded.
        """
        self.backlinks = {path: set() for path in self.notes_by_path}
        for source, note_links in self.links.items():
            for link in note_links:
                target = link.resolved
                if (
                    target is not None
                    and target != source
                    and target in self.notes_by_path
                ):
                    self.backlinks[target].add(source)
