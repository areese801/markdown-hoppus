"""
Tests for rename-propagation edge cases (HOPPUS-36, D6, spec §6.2, §14).

Covers shortest-unique-path re-simplification of links to *unrelated*
notes when a rename changes which filenames are vault-unique (both the
uniqueness-GAINED and uniqueness-LOST directions), the composed
``plan_rename_full`` flow, and the ``duplicates_existing_name``
collision predicate. Each test builds a small temp vault on disk and
indexes it with ``Index.build``.
"""

from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.index.propagation import (
    RenamePlan,
    duplicates_existing_name,
    plan_rename_full,
    resimplify_plan,
)
from hoppus.model import Note


def _write_vault(root: Path, files: dict[str, str]) -> None:
    """
    Create vault files from a relative-path → text mapping.
    """
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _notes(vault: Path) -> list[Note]:
    """
    Index a vault and return its notes.
    """
    return list(Index.build(vault).notes_by_path.values())


def _plan_full(vault: Path, old: Path, new: Path) -> RenamePlan:
    """
    Index a vault and plan a full rename against its pre-rename state.
    """
    return plan_rename_full(old, new, _notes(vault), vault)


class TestUniquenessGained:
    """
    A rename-away makes the surviving duplicate's filename unique, so
    path-qualified links to it shed their leading components (D6).
    """

    GAINED_FILES = {
        "Projects/Alpha.md": "The surviving Alpha.\n",
        "Archive/Alpha.md": "The Alpha being renamed away.\n",
        "Linker.md": (
            "See [[Projects/Alpha]] and [[Projects/Alpha|display]].\n"
            "Anchor [[Projects/Alpha#Heading]] and md [md](Projects/Alpha.md).\n"
            "Renamed one: [[Archive/Alpha]].\n"
        ),
    }

    def test_resimplify_after_removal(self, tmp_path: Path) -> None:
        """
        With the duplicate gone from the post state, links to the
        survivor simplify to the bare title, preserving display text,
        anchors, and markdown form.
        """
        root = tmp_path / "vault"
        _write_vault(root, self.GAINED_FILES)
        pre = _notes(root)
        post = [note for note in pre if note.path != root / "Archive" / "Alpha.md"]
        changes = resimplify_plan(pre, post, root)
        assert changes == {
            root / "Linker.md": (
                "See [[Alpha]] and [[Alpha|display]].\n"
                "Anchor [[Alpha#Heading]] and md [md](Alpha.md).\n"
                "Renamed one: [[Archive/Alpha]].\n"
            )
        }

    def test_full_rename_simplifies_survivor_links(self, tmp_path: Path) -> None:
        """
        Renaming Archive/Alpha to Archive/Beta rewrites links to the
        renamed note AND simplifies the survivor's links to [[Alpha]].
        """
        root = tmp_path / "vault"
        _write_vault(root, self.GAINED_FILES)
        plan = _plan_full(
            root, root / "Archive" / "Alpha.md", root / "Archive" / "Beta.md"
        )
        assert plan.changes == {
            root / "Linker.md": (
                "See [[Alpha]] and [[Alpha|display]].\n"
                "Anchor [[Alpha#Heading]] and md [md](Alpha.md).\n"
                "Renamed one: [[Beta]].\n"
            )
        }
        assert plan.link_count == 5

    def test_skip_excludes_notes(self, tmp_path: Path) -> None:
        """
        Paths in ``skip`` are never read or rewritten, so the helper
        composes with ``plan_rename`` without double-rewriting.
        """
        root = tmp_path / "vault"
        _write_vault(root, self.GAINED_FILES)
        pre = _notes(root)
        post = [note for note in pre if note.path != root / "Archive" / "Alpha.md"]
        changes = resimplify_plan(pre, post, root, skip={root / "Linker.md"})
        assert changes == {}


class TestUniquenessLost:
    """
    A rename creating a second copy of a filename makes previously-bare
    links ambiguous; they gain a path component instead of silently
    pointing at the wrong note (D6).
    """

    LOST_FILES = {
        "Projects/Alpha.md": "The original, previously unique Alpha.\n",
        "Archive/Beta.md": "About to be renamed to Archive/Alpha.\n",
        "Linker.md": (
            "Bare [[Alpha]] and [[Alpha|display]] and [[Alpha#H]].\n"
            "Markdown [md](Alpha.md) too.\n"
        ),
    }

    def test_bare_links_gain_path_component(self, tmp_path: Path) -> None:
        """
        After Archive/Beta becomes Archive/Alpha, bare links to the
        original Alpha are qualified as [[Projects/Alpha]].
        """
        root = tmp_path / "vault"
        _write_vault(root, self.LOST_FILES)
        plan = _plan_full(
            root, root / "Archive" / "Beta.md", root / "Archive" / "Alpha.md"
        )
        assert plan.changes == {
            root / "Linker.md": (
                "Bare [[Projects/Alpha]] and [[Projects/Alpha|display]] "
                "and [[Projects/Alpha#H]].\n"
                "Markdown [md](Projects/Alpha.md) too.\n"
            )
        }
        assert plan.link_count == 4

    def test_note_hit_by_both_passes(self, tmp_path: Path) -> None:
        """
        A note linking both the renamed note and the note that lost
        uniqueness ends up with BOTH rewrites applied to one text.
        """
        root = tmp_path / "vault"
        _write_vault(
            root,
            {
                "Projects/Alpha.md": "The original Alpha.\n",
                "Archive/Beta.md": "Renamed onto the Alpha stem.\n",
                "Both.md": "Renamed [[Beta]] and unrelated [[Alpha]] together.\n",
            },
        )
        plan = _plan_full(
            root, root / "Archive" / "Beta.md", root / "Archive" / "Alpha.md"
        )
        assert plan.changes == {
            root / "Both.md": (
                "Renamed [[Archive/Alpha]] and unrelated [[Projects/Alpha]] together.\n"
            )
        }
        assert plan.link_count == 2


class TestUntouchedContent:
    """
    Unrelated links, prose, and fenced code stay byte-identical (D6).
    """

    def test_unrelated_notes_and_fences_untouched(self, tmp_path: Path) -> None:
        """
        Notes whose uniqueness is unaffected keep every byte, and a
        code-fenced link to a re-simplified note never rewrites.
        """
        root = tmp_path / "vault"
        bystander = "Prose with [[Gamma]] and [ext](https://example.com/Alpha.md).\n"
        fenced = "Real [[Projects/Alpha]] link.\n\n```\nFenced [[Projects/Alpha]] stays.\n```\n"
        _write_vault(
            root,
            {
                "Projects/Alpha.md": "Alpha.\n",
                "Archive/Alpha.md": "Duplicate Alpha.\n",
                "Gamma.md": "Gamma.\n",
                "Bystander.md": bystander,
                "Fenced.md": fenced,
            },
        )
        plan = _plan_full(
            root, root / "Archive" / "Alpha.md", root / "Archive" / "Delta.md"
        )
        assert root / "Bystander.md" not in plan.changes
        assert plan.changes[root / "Fenced.md"] == (
            "Real [[Alpha]] link.\n\n```\nFenced [[Projects/Alpha]] stays.\n```\n"
        )
        assert (root / "Bystander.md").read_text(encoding="utf-8") == bystander


class TestDuplicatesExistingName:
    """
    The pure collision predicate for rename-into-existing-name (D6).
    """

    def test_detects_cross_folder_stem_collision(self, tmp_path: Path) -> None:
        """
        A proposed name whose stem matches another note's stem in a
        different folder returns that note's path, case-insensitively.
        """
        root = tmp_path / "vault"
        _write_vault(
            root,
            {"Projects/Alpha.md": "Alpha.\n", "Archive/Beta.md": "Beta.\n"},
        )
        notes = _notes(root)
        assert (
            duplicates_existing_name(root / "Archive" / "Alpha.md", notes)
            == root / "Projects" / "Alpha.md"
        )
        assert (
            duplicates_existing_name(root / "Archive" / "alpha.md", notes)
            == root / "Projects" / "Alpha.md"
        )

    def test_unique_name_returns_none(self, tmp_path: Path) -> None:
        """
        A vault-unique proposed stem is not a collision.
        """
        root = tmp_path / "vault"
        _write_vault(
            root,
            {"Projects/Alpha.md": "Alpha.\n", "Archive/Beta.md": "Beta.\n"},
        )
        assert (
            duplicates_existing_name(root / "Archive" / "Gamma.md", _notes(root))
            is None
        )
