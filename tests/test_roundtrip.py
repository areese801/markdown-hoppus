"""
Obsidian round-trip byte-compatibility guards (HOPPUS-65, spec §14, D4).

These tests protect hoppus's core promise: it must never corrupt a
user's plain-text vault. They assert that:

- building the index (and running the report-only audit, graph, and
  search paths) leaves every file on disk byte-identical;
- ``.obsidian/`` is never touched (no writes, additions, or deletions);
- a single-key frontmatter update via ruamel.yaml round-trip mode
  (decision D4) preserves key order, quoting, comments, other keys'
  bytes, and the entire body;
- a real write path (bookmarking) writes only under ``.hoppus/`` and
  leaves all notes and ``.obsidian/`` files byte-identical.
"""

from pathlib import Path

from hoppus.bookmarks import toggle_bookmark
from hoppus.graph import build_local_graph
from hoppus.index.indexer import Index
from hoppus.integrity import audit_vault
from hoppus.parse.frontmatter import split_frontmatter, update_frontmatter_key
from hoppus.search.content import search_content

_ORDERED_NOTE_MD = """\
---
title: "Quoted Title"
status: draft # keep this comment
priority: 2
tags:
  - alpha
  - 'beta'
extra:
  nested: yes
aliases: ["A One", "A Two"]
---

# Ordered Note

Body links to [[Plain Note]] and stays byte-identical.
"""

_PLAIN_NOTE_MD = """\
# Plain Note

No frontmatter here. Links: [[Ordered Note]], [[Ordered Note|alias]],
[[Ordered Note#Ordered Note]], and ![[image.png]].

A markdown link: [plain](Plain Note.md).

```
[[fake]] inside a fenced code block must not be treated as a link.
```

Inline #tag and nested #area/sub tags. ^block-one
"""

_WEIRD_NOTE_MD = """\
---
weird:   extra-spaces-after-colon
'single': value
"double":    also-spaced
list: [1, 2,   3]
---
Body directly after the closing delimiter, no blank line.
"""

_ALPHA_PROJECTS_MD = """\
---
tags: [project]
---

# Alpha

Duplicate stem lives in Projects/ and Archive/. See [[Archive/Alpha]].
"""

_ALPHA_ARCHIVE_MD = """\
# Alpha

Archived duplicate. See [[Projects/Alpha]] and [[Plain Note#^block-one]].
"""

_OBSIDIAN_APP_JSON = '{"livePreview": true,\n  "spellcheck":false }\n'

_PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00\x01\x02\xff"


def _snapshot(root: Path) -> dict[Path, bytes]:
    """
    Snapshot every file under a directory as raw bytes.

    Walks recursively, including hidden directories such as
    ``.obsidian/`` and binary attachments, so a snapshot comparison
    detects any created, modified, or deleted file.

    :param root: Directory to snapshot.
    :returns: Mapping of path-relative-to-root to the file's raw bytes.
    """
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _build_roundtrip_vault(root: Path) -> Path:
    """
    Write a realistic sample vault for byte-compatibility testing.

    Contains notes with and without frontmatter (deliberate key order,
    quoting variants, a comment, a nested map, non-standard whitespace),
    every OFM link form, inline and frontmatter tags, a fenced code
    block with a fake wikilink, duplicate-stem notes, a binary
    attachment, and an ``.obsidian/`` directory with a JSON file.

    :param root: Empty directory to populate.
    :returns: The vault root (same as ``root``).
    """
    (root / "Ordered Note.md").write_text(_ORDERED_NOTE_MD, encoding="utf-8")
    (root / "Plain Note.md").write_text(_PLAIN_NOTE_MD, encoding="utf-8")
    (root / "Weird Note.md").write_text(_WEIRD_NOTE_MD, encoding="utf-8")
    (root / "Projects").mkdir()
    (root / "Projects" / "Alpha.md").write_text(_ALPHA_PROJECTS_MD, encoding="utf-8")
    (root / "Archive").mkdir()
    (root / "Archive" / "Alpha.md").write_text(_ALPHA_ARCHIVE_MD, encoding="utf-8")
    (root / "attachments").mkdir()
    (root / "attachments" / "image.png").write_bytes(_PNG_BYTES)
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.json").write_text(_OBSIDIAN_APP_JSON, encoding="utf-8")
    return root


def test_readonly_open_is_noop_on_disk(tmp_path: Path) -> None:
    """
    Building the index and running every read-only path changes no bytes.

    Snapshot the vault, build the index, run the report-only audit, a
    local graph, and a content search, then re-snapshot and require
    byte-identical contents for every file — nothing created, modified,
    or deleted.
    """
    vault = _build_roundtrip_vault(tmp_path)
    before = _snapshot(vault)

    index = Index.build(vault)
    audit_vault(index)
    build_local_graph(index, vault / "Ordered Note.md")
    search_content("byte-identical", vault, backend="python")

    after = _snapshot(vault)
    assert after == before
    assert sorted(after) == sorted(before)


def test_obsidian_dir_is_never_touched(tmp_path: Path) -> None:
    """
    ``.obsidian/`` bytes and file set survive index builds and writes.

    Runs both the read-only open and hoppus write paths (a frontmatter
    update written back to a note, and a bookmark toggle), then asserts
    the ``.obsidian/`` directory has the exact same files with the exact
    same bytes.
    """
    vault = _build_roundtrip_vault(tmp_path)
    obsidian_before = _snapshot(vault / ".obsidian")

    index = Index.build(vault)
    audit_vault(index)
    note = vault / "Ordered Note.md"
    note.write_text(
        update_frontmatter_key(note.read_text(encoding="utf-8"), "status", "done"),
        encoding="utf-8",
    )
    toggle_bookmark(vault, note)

    obsidian_after = _snapshot(vault / ".obsidian")
    assert obsidian_after == obsidian_before
    assert sorted(obsidian_after) == sorted(obsidian_before)


def test_frontmatter_single_key_update_preserves_everything_else() -> None:
    """
    Updating one frontmatter key preserves all other bytes (D4).

    Asserts that only the targeted key's value changes while key order,
    the inline comment, the other keys' exact quoting and whitespace,
    and the body after the frontmatter all remain byte-identical.
    """
    updated = update_frontmatter_key(_ORDERED_NOTE_MD, "status", "done")
    assert updated != _ORDERED_NOTE_MD

    original_lines = _ORDERED_NOTE_MD.splitlines(keepends=True)
    updated_lines = updated.splitlines(keepends=True)
    assert len(updated_lines) == len(original_lines)

    changed = [
        (old, new) for old, new in zip(original_lines, updated_lines) if old != new
    ]
    assert len(changed) == 1
    old_line, new_line = changed[0]
    assert old_line == "status: draft # keep this comment\n"
    # Benign ruamel behavior: the inline comment stays anchored to its
    # original column, so the shorter value gains one pad space.
    assert new_line == "status: done  # keep this comment\n"

    frontmatter, body = split_frontmatter(updated)
    _, original_body = split_frontmatter(_ORDERED_NOTE_MD)
    assert body == original_body
    assert frontmatter["status"] == "done"
    assert list(frontmatter.keys()) == [
        "title",
        "status",
        "priority",
        "tags",
        "extra",
        "aliases",
    ]


def test_frontmatter_same_value_update_is_noop() -> None:
    """
    Re-setting a key to its current value is a byte-for-byte no-op.

    For frontmatter that ruamel already considers canonical (single
    space after each colon), a same-value update returns the input
    unchanged — no body or other-key drift.
    """
    result = update_frontmatter_key(_ORDERED_NOTE_MD, "priority", 2)
    assert result == _ORDERED_NOTE_MD


def test_frontmatter_update_normalizes_only_the_changed_line() -> None:
    """
    Document the one remaining benign normalization (D4).

    Only the updated key's own line is re-emitted by ruamel, so its
    non-standard run of spaces after the colon (``weird:   x``)
    collapses to a single space. Every OTHER line — including other
    keys' non-standard spacing, quoting, and flow-sequence whitespace —
    is spliced back verbatim, and the body stays byte-identical
    (HOPPUS-71 F5).
    """
    updated = update_frontmatter_key(_WEIRD_NOTE_MD, "weird", "changed")

    _, original_body = split_frontmatter(_WEIRD_NOTE_MD)
    frontmatter, body = split_frontmatter(updated)
    assert body == original_body

    assert frontmatter["weird"] == "changed"
    assert "weird: changed\n" in updated
    assert "'single': value\n" in updated
    assert '"double":    also-spaced\n' in updated
    assert "list: [1, 2,   3]\n" in updated


# Built by concatenation so the trailing space after ``tags:`` cannot be
# silently stripped by editors or formatters.
_BLANK_LINE_NOTE_MD = (
    "---\n\ntitle: Leading Blank\ntags: \npriority: 1\n---\n\nBody stays put.\n"
)


def test_frontmatter_update_preserves_blank_line_and_trailing_space() -> None:
    """
    A genuine value change keeps pathological frontmatter bytes intact
    (HOPPUS-71 F5).

    Pins the two real-corpus regressions: a blank line INSIDE the
    frontmatter block (here, before the first key — the case ruamel
    drops on re-emit) and the trailing space after an empty-valued key
    (``tags: ``) both survive an update to a DIFFERENT key.
    """
    updated = update_frontmatter_key(_BLANK_LINE_NOTE_MD, "priority", 2)

    assert updated == _BLANK_LINE_NOTE_MD.replace("priority: 1", "priority: 2")
    assert "---\n\ntitle: Leading Blank\n" in updated
    assert "tags: \n" in updated


def test_frontmatter_same_value_update_is_noop_on_pathological_notes() -> None:
    """
    Re-setting a key to its current value is byte-identical even for
    frontmatter ruamel cannot round-trip (HOPPUS-71 F5).

    Mirrors the real-corpus sweep (same key, same value) that
    previously dropped a blank line inside one note's frontmatter and
    collapsed ``tags: `` to ``tags:`` in 14 others.
    """
    assert (
        update_frontmatter_key(_BLANK_LINE_NOTE_MD, "title", "Leading Blank")
        == _BLANK_LINE_NOTE_MD
    )
    assert update_frontmatter_key(_BLANK_LINE_NOTE_MD, "tags", None) == (
        _BLANK_LINE_NOTE_MD
    )


def test_bookmark_write_touches_only_hoppus_dir(tmp_path: Path) -> None:
    """
    Bookmarking writes only under ``.hoppus/`` (spec §5.2).

    After ``toggle_bookmark``, every pre-existing file — all notes, the
    attachment, and ``.obsidian/`` — must be byte-identical, and the
    only new files must live under ``.hoppus/``.
    """
    vault = _build_roundtrip_vault(tmp_path)
    before = _snapshot(vault)

    assert toggle_bookmark(vault, vault / "Plain Note.md") is True

    after = _snapshot(vault)
    new_paths = set(after) - set(before)
    assert new_paths
    assert all(path.parts[0] == ".hoppus" for path in new_paths)
    assert {path: after[path] for path in before} == before
