"""
Tests for rename/move inbound-link propagation (HOPPUS-35, spec §6.2, §14).

Each test builds a small temp vault on disk, indexes it with
``Index.build``, and asserts that ``plan_rename`` rewrites exactly the
links that point at the renamed note — preserving display text,
anchors, embed prefixes, and markdown ``[text]`` — while leaving
unrelated links, code blocks, and prose byte-identical.
"""

from pathlib import Path

import pytest

from hoppus.index.indexer import Index
from hoppus.index.propagation import RenamePlan, apply_plan, plan_rename

TARGET_BODY = "# Heading\n\nA block. ^b1\n"

LINKER_BODY = """\
Intro prose with [[Target]] inline.
Alias link [[Target|see]] here.
Anchor [[Target#Heading]] and block [[Target#^b1]].
Embed: ![[Target]]
Markdown [md](Target.md) and [md2](Target.md#Heading).
Unrelated [[Other]] and [ext](https://example.com/Target.md) stay.

```
A fenced [[Target]] link never rewrites.
```
"""

LINKER_EXPECTED = """\
Intro prose with [[Renamed]] inline.
Alias link [[Renamed|see]] here.
Anchor [[Renamed#Heading]] and block [[Renamed#^b1]].
Embed: ![[Renamed]]
Markdown [md](Renamed.md) and [md2](Renamed.md#Heading).
Unrelated [[Other]] and [ext](https://example.com/Target.md) stay.

```
A fenced [[Target]] link never rewrites.
```
"""

OTHER_BODY = "Plain prose, a [[Linker]] link, nothing about the target.\n"


def _write_vault(root: Path, files: dict[str, str]) -> None:
    """
    Create vault files from a relative-path → text mapping.
    """
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _plan(vault: Path, old: Path, new: Path) -> RenamePlan:
    """
    Index a vault and plan a rename against its pre-rename state.
    """
    index = Index.build(vault)
    return plan_rename(old, new, index.notes_by_path.values(), vault)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """
    A vault with one target note linked every which way from Linker.md.
    """
    root = tmp_path / "vault"
    _write_vault(
        root,
        {
            "Target.md": TARGET_BODY,
            "Linker.md": LINKER_BODY,
            "Other.md": OTHER_BODY,
        },
    )
    return root


class TestRename:
    """
    Renaming a note rewrites every inbound link form (spec §14).
    """

    def test_all_inbound_forms_rewritten(self, vault: Path) -> None:
        """
        Plain, aliased, anchored, block-anchored, embed, and markdown
        links all point at the new name; display/anchor/embed/[text]
        are preserved; unrelated links, prose, and fenced code are
        byte-identical.
        """
        plan = _plan(vault, vault / "Target.md", vault / "Renamed.md")
        assert plan.changes == {vault / "Linker.md": LINKER_EXPECTED}
        assert plan.link_count == 7

    def test_untouched_notes_absent_from_changes(self, vault: Path) -> None:
        """
        Notes without inbound links to the renamed note do not appear
        in the plan at all.
        """
        plan = _plan(vault, vault / "Target.md", vault / "Renamed.md")
        assert vault / "Other.md" not in plan.changes
        assert vault / "Target.md" not in plan.changes

    def test_same_name_move_needs_no_rewrites(self, vault: Path) -> None:
        """
        Moving a vault-unique note into a subfolder keeps every inbound
        link resolving as written, so the plan is empty.
        """
        plan = _plan(vault, vault / "Target.md", vault / "Sub" / "Target.md")
        assert plan.changes == {}
        assert plan.link_count == 0

    def test_missing_note_raises(self, vault: Path) -> None:
        """
        Planning a rename for a path not in the note set is an error.
        """
        with pytest.raises(ValueError, match="Nope.md"):
            _plan(vault, vault / "Nope.md", vault / "Renamed.md")


class TestPathQualifiedMove:
    """
    Moves that change which path components are needed (spec §6.2).
    """

    def test_move_updates_path_qualified_links(self, tmp_path: Path) -> None:
        """
        With duplicate filenames, path-qualified wikilinks and markdown
        links follow the note to its new folder using the post-move
        shortest unique path.
        """
        root = tmp_path / "vault"
        _write_vault(
            root,
            {
                "Projects/Target.md": TARGET_BODY,
                "Archive/Target.md": "The other Target.\n",
                "Linker.md": (
                    "See [[Projects/Target]] and [md](Projects/Target.md), "
                    "not [[Archive/Target]].\n"
                ),
            },
        )
        old = root / "Projects" / "Target.md"
        new = root / "Projects" / "Sub" / "Target.md"
        plan = _plan(root, old, new)
        assert plan.changes == {
            root / "Linker.md": (
                "See [[Sub/Target]] and [md](Sub/Target.md), not [[Archive/Target]].\n"
            )
        }
        assert plan.link_count == 2


class TestApplyPlan:
    """
    Applying a plan writes exactly the changed files.
    """

    def test_apply_writes_only_changed_files(self, vault: Path) -> None:
        """
        Changed notes are rewritten on disk; untouched notes keep their
        original bytes, and the note file itself is not moved.
        """
        plan = _plan(vault, vault / "Target.md", vault / "Renamed.md")
        written: list[Path] = []

        def spy_write(path: Path, text: str) -> None:
            written.append(path)
            path.write_text(text, encoding="utf-8")

        apply_plan(plan, write_text=spy_write)
        assert written == [vault / "Linker.md"]
        assert (vault / "Linker.md").read_text(encoding="utf-8") == LINKER_EXPECTED
        assert (vault / "Other.md").read_text(encoding="utf-8") == OTHER_BODY
        assert (vault / "Target.md").exists()
        assert not (vault / "Renamed.md").exists()
