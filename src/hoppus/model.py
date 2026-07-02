"""
Domain model dataclasses: Note, Link, Tag (spec §5.1, §6.1).

Pure data structures with no I/O and no parsing logic. Parsing lives in
``hoppus.parse`` and resolution in ``hoppus.parse.links`` (later stories);
these classes only *represent* the vault's concepts.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Note:
    """
    A single ``.md`` note in a vault.

    A note's title is its filename without the ``.md`` extension (spec
    §5.1). All other fields are extracted metadata used by the index
    (spec §8).

    :param title: Filename stem, e.g. ``"John Doe"`` for ``John Doe.md``.
    :param path: Path to the note file on disk.
    :param aliases: Alternate names from ``aliases:`` frontmatter.
    :param headings: Heading texts, in document order.
    :param block_ids: Block ids defined via trailing ``^block-id``.
    :param frontmatter: Parsed YAML frontmatter mapping.
    :param word_count: Word count of the note body.
    :param mtime: File modification time (epoch seconds).
    """

    title: str
    path: Path
    aliases: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)
    word_count: int = 0
    mtime: float = 0.0


@dataclass
class Link:
    """
    A single link occurrence within a note (spec §6.1).

    Models every OFM link variant as data without resolving it:

    - ``[[Note]]`` — wikilink to a note by title.
    - ``[[Note|Display]]`` — wikilink with display text.
    - ``[[Note#Heading]]`` / ``[[Note#^block-id]]`` — anchored wikilink.
    - ``[[#Heading]]`` / ``[[#^block-id]]`` — same-note anchor
      (``target`` is the empty string).
    - ``![[...]]`` — embed/transclusion (``is_embed=True``).
    - ``[text](note.md)`` — standard Markdown link (``is_wikilink=False``).

    :param source: Path of the note containing the link.
    :param target: Raw target as written (may be ``""`` for same-note
        anchors, or an attachment name like ``image.png``).
    :param anchor: Heading (``"Heading"``, nested ``"H1#H2"``) or block
        (``"^block-id"``) anchor, or None.
    :param display: Per-link display text (``|Display``), or None.
    :param is_embed: True for ``![[...]]`` embeds.
    :param is_wikilink: True for ``[[...]]``; False for ``[text](path)``.
    :param resolved: Resolved target path, filled in by the resolution
        layer (spec §6.2); None until resolved.
    """

    source: Path
    target: str
    anchor: str | None = None
    display: str | None = None
    is_embed: bool = False
    is_wikilink: bool = True
    resolved: Path | None = None


@dataclass(frozen=True)
class Tag:
    """
    A tag label, possibly nested (spec §6.4).

    Tags come from inline ``#tag`` occurrences and frontmatter ``tags:``
    entries. Nesting uses ``/``, e.g. ``area/subarea``.

    :param name: Full tag name without the leading ``#``.
    """

    name: str

    @property
    def parts(self) -> tuple[str, ...]:
        """
        Return the nested components of the tag.

        ``Tag("area/sub").parts`` is ``("area", "sub")``.
        """
        return tuple(self.name.split("/"))

    @property
    def parent(self) -> "Tag | None":
        """
        Return the parent tag for a nested tag, or None for a top-level tag.

        ``Tag("area/sub").parent`` is ``Tag("area")``.
        """
        if "/" not in self.name:
            return None
        return Tag(self.name.rsplit("/", 1)[0])

    def __str__(self) -> str:
        """
        Return the tag rendered with its leading ``#``.
        """
        return f"#{self.name}"
