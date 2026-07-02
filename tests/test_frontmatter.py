"""
Tests for hoppus.parse.frontmatter (HOPPUS-19, spec §6.5, decision D4).

Covers parsing notes with/without frontmatter, the D4/§14 round-trip
byte-preservation guarantee when updating a single key, preservation of
arbitrary user keys, and tag parsing from both YAML-list and delimited
string forms (spec §6.4).
"""

from pathlib import Path

from hoppus.parse.frontmatter import (
    get_aliases,
    get_tags,
    split_frontmatter,
    update_frontmatter_key,
)

_NOTE_WITH_FRONTMATTER = """\
---
title: "John Doe"
aliases:
  - JD
  - Johnny
tags:
  - person
reports_to: Jane Smith
---

# John Doe

Body text here.
"""

_NOTE_WITHOUT_FRONTMATTER = """\
# Plain Note

No frontmatter at all, just body — even this --- dashed line.
"""

_NOTE_EMPTY_FRONTMATTER = """\
---
---
Body after empty frontmatter.
"""

# A note with comments, quoting, and a blank line inside frontmatter,
# for the strictest round-trip assertions.
_NOTE_FOR_ROUNDTRIP = """\
---
title: "Quoted Title"  # keep the quotes
aliases:
  - QT

custom_key: some value
tags:
  - one
  - two
---

Body line one.

Body line two.
"""


class TestSplitFrontmatter:
    """
    Parsing behavior of ``split_frontmatter``.
    """

    def test_note_with_frontmatter(self) -> None:
        """
        A standard frontmatter block yields the mapping and the body.
        """
        frontmatter, body = split_frontmatter(_NOTE_WITH_FRONTMATTER)
        assert frontmatter["title"] == "John Doe"
        assert list(frontmatter["aliases"]) == ["JD", "Johnny"]
        assert frontmatter["reports_to"] == "Jane Smith"
        assert body == "\n# John Doe\n\nBody text here.\n"

    def test_note_without_frontmatter(self) -> None:
        """
        A note without frontmatter yields an empty mapping and the
        unchanged full text.
        """
        frontmatter, body = split_frontmatter(_NOTE_WITHOUT_FRONTMATTER)
        assert dict(frontmatter) == {}
        assert body == _NOTE_WITHOUT_FRONTMATTER

    def test_empty_frontmatter_block(self) -> None:
        """
        An empty ``---``/``---`` block yields an empty mapping.
        """
        frontmatter, body = split_frontmatter(_NOTE_EMPTY_FRONTMATTER)
        assert dict(frontmatter) == {}
        assert body == "Body after empty frontmatter.\n"

    def test_unclosed_delimiter_is_not_frontmatter(self) -> None:
        """
        A leading ``---`` with no closing delimiter is not frontmatter.
        """
        text = "---\ntitle: broken\nno closing delimiter\n"
        frontmatter, body = split_frontmatter(text)
        assert dict(frontmatter) == {}
        assert body == text

    def test_arbitrary_keys_are_preserved(self) -> None:
        """
        Unknown user keys survive parsing alongside recognized ones
        (spec §6.5).
        """
        frontmatter, _ = split_frontmatter(_NOTE_WITH_FRONTMATTER)
        assert set(frontmatter.keys()) == {"title", "aliases", "tags", "reports_to"}

    def test_sample_vault_target_note(self, sample_vault: Path) -> None:
        """
        The shared fixture's Target Note parses to its known frontmatter.
        """
        text = (sample_vault / "Target Note.md").read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        assert get_aliases(frontmatter) == ["The Target", "target-note"]
        assert get_tags(frontmatter) == ["reference", "area/sub"]
        assert body.startswith("\n# Target Note\n")


class TestUpdateFrontmatterKey:
    """
    Round-trip byte preservation of ``update_frontmatter_key`` (D4/§14).
    """

    def test_replacing_one_key_preserves_all_other_lines(self) -> None:
        """
        Replacing one key leaves every other line byte-identical,
        including comments, quoting, and blank lines.
        """
        updated = update_frontmatter_key(_NOTE_FOR_ROUNDTRIP, "custom_key", "new value")
        original_lines = _NOTE_FOR_ROUNDTRIP.splitlines(keepends=True)
        updated_lines = updated.splitlines(keepends=True)
        assert len(updated_lines) == len(original_lines)
        for original, new in zip(original_lines, updated_lines, strict=True):
            if original == "custom_key: some value\n":
                assert new == "custom_key: new value\n"
            else:
                assert new == original

    def test_noop_rewrite_is_byte_identical(self) -> None:
        """
        Re-setting a key to its current value reproduces the note
        byte-for-byte.
        """
        updated = update_frontmatter_key(
            _NOTE_FOR_ROUNDTRIP, "custom_key", "some value"
        )
        assert updated == _NOTE_FOR_ROUNDTRIP

    def test_adding_a_key_preserves_existing_lines(self) -> None:
        """
        Adding a brand-new key only appends; existing lines are untouched.
        """
        updated = update_frontmatter_key(_NOTE_FOR_ROUNDTRIP, "brand_new", "hello")
        for line in _NOTE_FOR_ROUNDTRIP.splitlines(keepends=True):
            assert line in updated.splitlines(keepends=True)
        frontmatter, _ = split_frontmatter(updated)
        assert frontmatter["brand_new"] == "hello"

    def test_update_note_without_frontmatter_creates_block(self) -> None:
        """
        Updating a key on a frontmatter-less note creates a block and
        keeps the body byte-identical.
        """
        updated = update_frontmatter_key(_NOTE_WITHOUT_FRONTMATTER, "tags", ["new"])
        frontmatter, body = split_frontmatter(updated)
        assert get_tags(frontmatter) == ["new"]
        assert body == _NOTE_WITHOUT_FRONTMATTER

    def test_arbitrary_keys_survive_update(self) -> None:
        """
        Updating one key never drops unknown user keys (spec §6.5).
        """
        updated = update_frontmatter_key(_NOTE_WITH_FRONTMATTER, "tags", ["employee"])
        frontmatter, _ = split_frontmatter(updated)
        assert frontmatter["reports_to"] == "Jane Smith"
        assert frontmatter["title"] == "John Doe"
        assert get_tags(frontmatter) == ["employee"]

    def test_sample_vault_roundtrip(self, sample_vault: Path) -> None:
        """
        Updating one key of a fixture note leaves all other lines
        byte-identical.
        """
        text = (sample_vault / "Target Note.md").read_text(encoding="utf-8")
        updated = update_frontmatter_key(text, "status", "reviewed")
        original_lines = text.splitlines(keepends=True)
        updated_lines = updated.splitlines(keepends=True)
        assert [
            line for line in updated_lines if line not in ("status: reviewed\n",)
        ] == original_lines


class TestAccessors:
    """
    ``get_aliases`` and ``get_tags`` accessor behavior.
    """

    def test_tags_from_yaml_list(self) -> None:
        """
        Tags given as a YAML list parse in order.
        """
        frontmatter, _ = split_frontmatter("---\ntags:\n  - alpha\n  - beta\n---\nx\n")
        assert get_tags(frontmatter) == ["alpha", "beta"]

    def test_tags_from_space_delimited_string(self) -> None:
        """
        Tags given as a space-delimited string split correctly (§6.4).
        """
        frontmatter, _ = split_frontmatter("---\ntags: alpha beta gamma\n---\nx\n")
        assert get_tags(frontmatter) == ["alpha", "beta", "gamma"]

    def test_tags_from_comma_delimited_string(self) -> None:
        """
        Tags given as a comma-delimited string split correctly (§6.4).
        """
        frontmatter, _ = split_frontmatter('---\ntags: "alpha, beta,gamma"\n---\nx\n')
        assert get_tags(frontmatter) == ["alpha", "beta", "gamma"]

    def test_missing_keys_yield_empty_lists(self) -> None:
        """
        Absent ``aliases``/``tags`` keys yield empty lists.
        """
        frontmatter, _ = split_frontmatter("---\ntitle: x\n---\nbody\n")
        assert get_tags(frontmatter) == []
        assert get_aliases(frontmatter) == []

    def test_aliases_from_list_and_string(self) -> None:
        """
        Aliases accept both a YAML list and a single string.
        """
        from_list, _ = split_frontmatter("---\naliases:\n  - A\n  - B\n---\nx\n")
        assert get_aliases(from_list) == ["A", "B"]
        from_string, _ = split_frontmatter("---\naliases: Solo\n---\nx\n")
        assert get_aliases(from_string) == ["Solo"]
