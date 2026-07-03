"""
Unit tests for on-demand unlinked-mention detection (HOPPUS-33).

Pure tests for ``hoppus.index.mentions.find_unlinked_mentions`` against a
small temp vault built per test module: plain-text mentions found, text
inside existing links and code never counted, whole-word boundaries,
alias matches, the noisy-title guard, count/order semantics, target
self-exclusion, the ``limit`` parameter, and unreadable-file skipping.
"""

from pathlib import Path

import pytest

from hoppus.index.indexer import Index
from hoppus.index.mentions import UnlinkedMention, find_unlinked_mentions

_WIDGET_MD = """\
---
aliases:
  - Gadget
  - wx
---

# Widget

The Widget note mentions its own title, which must never count.
"""

_DIARY_MD = """\
# Diary

Thought about the Widget today. Later, widget again (case-insensitive).
"""

_LINKED_ONLY_MD = """\
# Linked Only

A wikilink [[Widget]] and a markdown link [see](Widget.md) and inline
code `Widget` do not count.

```python
print("Widget")  # fenced code does not count either
```
"""

_SUBSTRING_MD = """\
# Substring

Widgets and Widgetry are not whole-word matches.
"""

_ALIAS_MD = """\
# Alias User

The Gadget shines. But wx is too short to be searched: wx wx wx.
"""

_SHORTY_MD = """\
# Ab

Nothing here.
"""

_SHORTY_MENTIONER_MD = """\
# Shorty Mentioner

Ab Ab Ab everywhere.
"""


@pytest.fixture()
def mention_vault(tmp_path: Path) -> Path:
    """
    Build a small vault exercising every mention-detection rule.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Widget.md").write_text(_WIDGET_MD, encoding="utf-8")
    (vault / "Diary.md").write_text(_DIARY_MD, encoding="utf-8")
    (vault / "Linked Only.md").write_text(_LINKED_ONLY_MD, encoding="utf-8")
    (vault / "Substring.md").write_text(_SUBSTRING_MD, encoding="utf-8")
    (vault / "Alias User.md").write_text(_ALIAS_MD, encoding="utf-8")
    (vault / "Ab.md").write_text(_SHORTY_MD, encoding="utf-8")
    (vault / "Shorty Mentioner.md").write_text(_SHORTY_MENTIONER_MD, encoding="utf-8")
    return vault


def read_utf8(path: Path) -> str:
    """
    Read a note body as UTF-8.

    :param path: The note path.
    :returns: The file contents.
    """
    return path.read_text(encoding="utf-8")


def target_note(index: Index, vault: Path, name: str):
    """
    Look up a note in the index by filename.

    :param index: The built vault index.
    :param vault: The vault root.
    :param name: The note filename.
    :returns: The ``Note``.
    """
    return index.notes_by_path[vault / name]


def test_plain_text_mentions_found_with_counts_and_order(
    mention_vault: Path,
) -> None:
    """
    Plain-text title and alias mentions are found, counted, and sorted
    by count descending then title case-insensitively.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")
    mentions = find_unlinked_mentions(target, index, read_utf8)
    assert mentions == [
        UnlinkedMention(source=mention_vault / "Diary.md", title="Diary", count=2),
        UnlinkedMention(
            source=mention_vault / "Alias User.md", title="Alias User", count=1
        ),
    ]


def test_mentions_inside_links_and_code_do_not_count(mention_vault: Path) -> None:
    """
    Occurrences only inside wikilinks, markdown links, inline code, or
    fenced code blocks never count as unlinked mentions.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")
    sources = {m.source for m in find_unlinked_mentions(target, index, read_utf8)}
    assert mention_vault / "Linked Only.md" not in sources


def test_whole_word_boundary_excludes_substrings(mention_vault: Path) -> None:
    """
    "Widgets"/"Widgetry" must not match the title "Widget".
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")
    sources = {m.source for m in find_unlinked_mentions(target, index, read_utf8)}
    assert mention_vault / "Substring.md" not in sources


def test_target_note_never_mentions_itself(mention_vault: Path) -> None:
    """
    The target's own body is excluded even though it contains its title.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")
    sources = {m.source for m in find_unlinked_mentions(target, index, read_utf8)}
    assert mention_vault / "Widget.md" not in sources


def test_short_alias_dropped_by_noisy_title_guard(mention_vault: Path) -> None:
    """
    The two-character alias "wx" is dropped, so its repeats never count.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")
    mentions = find_unlinked_mentions(target, index, read_utf8)
    alias_user = next(
        m for m in mentions if m.source == mention_vault / "Alias User.md"
    )
    assert alias_user.count == 1


def test_short_title_yields_empty_result(mention_vault: Path) -> None:
    """
    A title shorter than ``min_length`` leaves no searchable names.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Ab.md")
    assert find_unlinked_mentions(target, index, read_utf8) == []


def test_limit_truncates_results(mention_vault: Path) -> None:
    """
    ``limit`` keeps only the top-N mentions after sorting.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")
    mentions = find_unlinked_mentions(target, index, read_utf8, limit=1)
    assert len(mentions) == 1
    assert mentions[0].source == mention_vault / "Diary.md"


def test_unreadable_files_are_skipped(mention_vault: Path) -> None:
    """
    A source that raises ``OSError`` on read is skipped, not fatal.
    """
    index = Index.build(mention_vault)
    target = target_note(index, mention_vault, "Widget.md")

    def flaky_read(path: Path) -> str:
        """
        Raise for Diary.md, read everything else normally.

        :param path: The note path.
        :returns: The file contents.
        :raises OSError: For the Diary note.
        """
        if path.name == "Diary.md":
            raise OSError("simulated read failure")
        return read_utf8(path)

    sources = {m.source for m in find_unlinked_mentions(target, index, flaky_read)}
    assert mention_vault / "Diary.md" not in sources
    assert mention_vault / "Alias User.md" in sources
