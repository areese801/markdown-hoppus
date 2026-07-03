"""
Tests for the in-memory vault index (HOPPUS-22, spec §8).

Built on the shared ``sample_vault`` fixture. Incremental-update tests
copy the vault into a fresh temp dir first, since the fixture is
session-scoped and must stay pristine for other tests.
"""

import shutil
from pathlib import Path

import pytest

from hoppus.index.indexer import Index, build_note


@pytest.fixture
def index(sample_vault: Path) -> Index:
    """
    Build a fresh index over the shared sample vault.
    """
    return Index.build(sample_vault)


@pytest.fixture
def mutable_vault(sample_vault: Path, tmp_path: Path) -> Path:
    """
    Copy the sample vault into a per-test temp dir safe to mutate.
    """
    vault = tmp_path / "vault"
    shutil.copytree(sample_vault, vault)
    return vault


class TestBuildNote:
    """
    Building a single Note from a file (spec §8).
    """

    def test_note_fields(self, sample_vault: Path) -> None:
        """
        Title, aliases, headings, block ids, word count, and mtime are
        all populated from the file.
        """
        path = sample_vault / "Target Note.md"
        note = build_note(path)
        assert note.title == "Target Note"
        assert note.path == path
        assert note.aliases == ["The Target", "target-note"]
        assert note.headings == ["Target Note", "Section One", "Section Two"]
        assert note.block_ids == ["quote-1"]
        assert note.frontmatter["tags"] == ["reference", "area/sub"]
        assert note.word_count == len(
            path.read_text(encoding="utf-8").split("---\n")[2].split()
        )
        assert note.mtime == path.stat().st_mtime


class TestBuildIndex:
    """
    Full-vault scan: notes, links, backlinks, tags, name indexes.
    """

    def test_all_notes_indexed(self, index: Index, sample_vault: Path) -> None:
        """
        Every .md file is indexed; .obsidian/ content is not.
        """
        expected = {
            sample_vault / "Index.md",
            sample_vault / "Target Note.md",
            sample_vault / "Tags Note.md",
            sample_vault / "Projects" / "Alpha.md",
            sample_vault / "Archive" / "Alpha.md",
        }
        assert set(index.notes_by_path) == expected
        assert index.notes["Target Note"].aliases == ["The Target", "target-note"]
        assert index.notes["Index"].block_ids == ["local-block"]
        assert "Local Heading" in index.notes["Index"].headings

    def test_duplicate_titles_in_title_index(
        self, index: Index, sample_vault: Path
    ) -> None:
        """
        Both Alpha notes are tracked under one title-index entry.
        """
        assert sorted(index.title_index["alpha"]) == sorted(
            [
                sample_vault / "Archive" / "Alpha.md",
                sample_vault / "Projects" / "Alpha.md",
            ]
        )
        assert index.alias_index["the target"] == [sample_vault / "Target Note.md"]
        assert index.alias_index["target-note"] == [sample_vault / "Target Note.md"]

    def test_outbound_links_resolved(self, index: Index, sample_vault: Path) -> None:
        """
        Index.md's links resolve per spec §6.2, including duplicate-Alpha
        disambiguation, same-note anchors, embeds, and markdown links.
        """
        index_path = sample_vault / "Index.md"
        target_path = sample_vault / "Target Note.md"
        resolved_by_target = {
            (link.target, link.anchor): link.resolved
            for link in index.links[index_path]
        }
        assert resolved_by_target[("Target Note", None)] == target_path
        assert resolved_by_target[("Target Note", "Section One")] == target_path
        assert resolved_by_target[("Target Note", "^quote-1")] == target_path
        assert resolved_by_target[("", "Local Heading")] == index_path
        assert resolved_by_target[("", "^local-block")] == index_path
        assert (
            resolved_by_target[("Projects/Alpha", None)]
            == sample_vault / "Projects" / "Alpha.md"
        )
        assert (
            resolved_by_target[("Archive/Alpha", None)]
            == sample_vault / "Archive" / "Alpha.md"
        )
        assert (
            resolved_by_target[("image.png", None)]
            == sample_vault / "attachments" / "image.png"
        )
        assert resolved_by_target[("Target Note.md", None)] == target_path

    def test_backlinks(self, index: Index, sample_vault: Path) -> None:
        """
        Backlinks are the inverse of resolved note-to-note links.
        """
        index_path = sample_vault / "Index.md"
        target_path = sample_vault / "Target Note.md"
        projects_alpha = sample_vault / "Projects" / "Alpha.md"
        archive_alpha = sample_vault / "Archive" / "Alpha.md"
        assert index.backlinks[target_path] == {index_path}
        assert index.backlinks[index_path] == {target_path}
        assert index.backlinks[projects_alpha] == {index_path, archive_alpha}
        assert index.backlinks[archive_alpha] == {index_path, projects_alpha}
        assert index.backlinks[sample_vault / "Tags Note.md"] == set()

    def test_tag_union(self, index: Index, sample_vault: Path) -> None:
        """
        Inline and frontmatter tags are unioned per note (spec §6.4).
        """
        tags_note = sample_vault / "Tags Note.md"
        target_note = sample_vault / "Target Note.md"
        assert index.note_tags[tags_note] == {
            "frontmatter-tag",
            "inline-tag",
            "area/sub",
            "closing-tag",
        }
        assert index.note_tags[target_note] == {"reference", "area/sub"}
        assert index.note_tags[sample_vault / "Index.md"] == {"index"}
        assert index.tags["area/sub"] == {tags_note, target_note}
        assert "not-a-tag" not in index.tags


class TestIncrementalReindex:
    """
    Single-file re-index: add, update, and remove (spec §8).
    """

    def test_modified_note_updates_maps(self, mutable_vault: Path) -> None:
        """
        Re-indexing a changed note refreshes its links, backlinks, and
        tags — including a new unresolved link staying unresolved.
        """
        index = Index.build(mutable_vault)
        tags_note = mutable_vault / "Tags Note.md"
        target_note = mutable_vault / "Target Note.md"
        assert tags_note not in index.backlinks[target_note]

        with tags_note.open("a", encoding="utf-8") as handle:
            handle.write("\nNow linking [[Target Note]] and [[Ghost Note]]. #fresh\n")
        index.reindex_file(tags_note)

        resolved = {link.target: link.resolved for link in index.links[tags_note]}
        assert resolved["Target Note"] == target_note
        assert resolved["Ghost Note"] is None
        assert tags_note in index.backlinks[target_note]
        assert "fresh" in index.note_tags[tags_note]
        assert index.tags["fresh"] == {tags_note}

    def test_added_note_resolves_previously_dangling_link(
        self, mutable_vault: Path
    ) -> None:
        """
        Creating the missing target and re-indexing it repairs the
        formerly unresolved link in another note.
        """
        index = Index.build(mutable_vault)
        tags_note = mutable_vault / "Tags Note.md"
        with tags_note.open("a", encoding="utf-8") as handle:
            handle.write("\nSee [[Ghost Note]].\n")
        index.reindex_file(tags_note)

        ghost = mutable_vault / "Ghost Note.md"
        ghost.write_text("# Ghost Note\n\nBoo. #spooky\n", encoding="utf-8")
        index.reindex_file(ghost)

        assert index.notes["Ghost Note"].path == ghost
        resolved = {link.target: link.resolved for link in index.links[tags_note]}
        assert resolved["Ghost Note"] == ghost
        assert index.backlinks[ghost] == {tags_note}
        assert index.tags["spooky"] == {ghost}

    def test_removed_note_purges_maps_and_unresolves_links(
        self, mutable_vault: Path
    ) -> None:
        """
        Deleting a note drops it everywhere; inbound links become
        unresolved and the duplicate title index shrinks.
        """
        index = Index.build(mutable_vault)
        index_path = mutable_vault / "Index.md"
        projects_alpha = mutable_vault / "Projects" / "Alpha.md"
        archive_alpha = mutable_vault / "Archive" / "Alpha.md"

        projects_alpha.unlink()
        index.reindex_file(projects_alpha)

        assert projects_alpha not in index.notes_by_path
        assert projects_alpha not in index.links
        assert projects_alpha not in index.backlinks
        assert index.title_index["alpha"] == [archive_alpha]
        assert index.notes["Alpha"].path == archive_alpha
        resolved = {
            link.target: link.resolved
            for link in index.links[index_path]
            if link.target.endswith("Alpha")
        }
        assert resolved["Projects/Alpha"] is None
        assert resolved["Archive/Alpha"] == archive_alpha
        assert index.backlinks[archive_alpha] == {index_path}
