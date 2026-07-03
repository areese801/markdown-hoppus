"""
Pure link-integrity core tests (spec §7.3, §9.6, §19 D1, HOPPUS-39).

Exercises ``hoppus.integrity`` against temp vaults: the §7.3 two-prompt
worked example, alias/anchor/embed preservation on correction, the
attachment/markdown/same-note-anchor/code-fence exclusions, multi-edit
offset handling, and the §9.6 editor-command resolution rule.
"""

from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.integrity import (
    UnresolvedWikilink,
    apply_integrity_decisions,
    correct_target,
    create_missing_note,
    find_unresolved_wikilinks,
    resolve_editor_command,
    resolve_new_note_target,
)
from hoppus.parse.links import Resolver


def build_vault(tmp_path: Path, files: dict[str, str]) -> Path:
    """
    Create a temp vault populated with the given files.

    :param tmp_path: pytest's temp directory.
    :param files: Relative filename → file content.
    :returns: The vault root path.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    for name, content in files.items():
        path = vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return vault


def resolver_for(index: Index) -> Resolver:
    """
    Build a Resolver mirroring the app's integrity-pass construction.
    """
    return Resolver(
        list(index.notes_by_path.values()),
        index.vault_root,
        attachments=index._attachments,
    )


def find(vault: Path, note_name: str) -> tuple[str, Index, list[UnresolvedWikilink]]:
    """
    Read a note, build the index, and run the unresolved-link scan.

    :returns: ``(text, index, unresolved)``.
    """
    path = vault / note_name
    text = path.read_text(encoding="utf-8")
    index = Index.build(vault)
    return text, index, find_unresolved_wikilinks(text, index, path)


class TestWorkedExample:
    """
    The §7.3 worked example: one typo correction, one create-at-root.
    """

    BODY = "…reviewed the roadmap with [[Jane Smtih]] and kicked off [[Q3 Planning]].\n"

    def test_two_prompts_correct_then_create(self, tmp_path: Path) -> None:
        """
        Two unresolved links in document order; correct → in-line fix,
        create → new note at vault root with prose untouched (Option A).
        """
        vault = build_vault(
            tmp_path,
            {"Jane Smith.md": "A person.\n", "Meeting Notes.md": self.BODY},
        )
        text, index, unresolved = find(vault, "Meeting Notes.md")

        assert [u.link.target for u in unresolved] == ["Jane Smtih", "Q3 Planning"]
        first = unresolved[0]
        assert first.suggestions, "expected did-you-mean suggestions"
        assert first.suggestions[0].title == "Jane Smith"

        decisions = [("correct", first.suggestions[0]), ("create", None)]
        new_text, created = apply_integrity_decisions(
            text, unresolved, decisions, vault, resolver_for(index)
        )

        assert new_text == (
            "…reviewed the roadmap with [[Jane Smith]] "
            "and kicked off [[Q3 Planning]].\n"
        )
        assert created == [vault / "Q3 Planning.md"]
        assert created[0].is_file()
        assert created[0].read_text(encoding="utf-8") == ""

    def test_created_note_resolves_on_rescan(self, tmp_path: Path) -> None:
        """
        After the create decision, the untouched link resolves.
        """
        vault = build_vault(
            tmp_path,
            {"Jane Smith.md": "A person.\n", "Meeting Notes.md": self.BODY},
        )
        text, index, unresolved = find(vault, "Meeting Notes.md")
        new_text, _created = apply_integrity_decisions(
            text,
            unresolved,
            [("correct", unresolved[0].suggestions[0]), ("create", None)],
            vault,
            resolver_for(index),
        )
        (vault / "Meeting Notes.md").write_text(new_text, encoding="utf-8")
        _text, _index, remaining = find(vault, "Meeting Notes.md")
        assert remaining == []


class TestCorrectionPreservation:
    """
    Corrections preserve ``|alias``, ``#anchor``, and the embed ``!``.
    """

    def test_alias_anchor_and_embed_preserved(self, tmp_path: Path) -> None:
        """
        ``[[Jane Smtih|Jane]]``, ``[[Jane Smtih#Q2]]``, and
        ``![[Jane Smtih]]`` all correct to Jane Smith intact.
        """
        body = "See [[Jane Smtih|Jane]] re [[Jane Smtih#Q2]].\n\n![[Jane Smtih]]\n"
        vault = build_vault(tmp_path, {"Jane Smith.md": "A person.\n", "Note.md": body})
        text, index, unresolved = find(vault, "Note.md")
        assert len(unresolved) == 3

        jane = index.notes_by_path[vault / "Jane Smith.md"]
        new_text, created = apply_integrity_decisions(
            text,
            unresolved,
            [("correct", jane)] * 3,
            vault,
            resolver_for(index),
        )
        assert new_text == (
            "See [[Jane Smith|Jane]] re [[Jane Smith#Q2]].\n\n![[Jane Smith]]\n"
        )
        assert created == []

    def test_correct_target_edits_single_occurrence(self, tmp_path: Path) -> None:
        """
        ``correct_target`` touches only the given occurrence's span.
        """
        body = "[[Jane Smtih]] met [[Jane Smtih]].\n"
        vault = build_vault(tmp_path, {"Jane Smith.md": "A person.\n", "Note.md": body})
        text, _index, unresolved = find(vault, "Note.md")
        assert len(unresolved) == 2
        fixed = correct_target(text, unresolved[1], "Jane Smith")
        assert fixed == "[[Jane Smtih]] met [[Jane Smith]].\n"


class TestExclusions:
    """
    Only §7.3 note wikilinks are flagged — everything else is excluded.
    """

    def test_attachment_embed_excluded(self, tmp_path: Path) -> None:
        """
        An unresolved ``![[diagram.png]]`` is §7.4 report-only.
        """
        vault = build_vault(tmp_path, {"Note.md": "See ![[diagram.png]].\n"})
        _text, _index, unresolved = find(vault, "Note.md")
        assert unresolved == []

    def test_same_note_anchor_and_markdown_link_excluded(self, tmp_path: Path) -> None:
        """
        ``[[#Heading]]`` and ``[t](Missing.md)`` never prompt.
        """
        body = "Jump to [[#Heading]] or [t](Missing.md).\n\n# Heading\n"
        vault = build_vault(tmp_path, {"Note.md": body})
        _text, _index, unresolved = find(vault, "Note.md")
        assert unresolved == []

    def test_code_fence_not_flagged(self, tmp_path: Path) -> None:
        """
        Wikilinks inside code fences and inline code are never flagged.
        """
        body = "```\n[[Ghost Note]]\n```\n\nAlso `[[Ghost Note]]` inline.\n"
        vault = build_vault(tmp_path, {"Note.md": body})
        _text, _index, unresolved = find(vault, "Note.md")
        assert unresolved == []

    def test_note_embed_included(self, tmp_path: Path) -> None:
        """
        A note embed ``![[Missing Note]]`` IS a §7.3 case.
        """
        vault = build_vault(tmp_path, {"Note.md": "![[Missing Note]]\n"})
        _text, _index, unresolved = find(vault, "Note.md")
        assert [u.link.target for u in unresolved] == ["Missing Note"]
        assert unresolved[0].link.is_embed


class TestOffsetHandling:
    """
    Multiple corrections in one file all land at the right spots.
    """

    def test_two_corrections_both_land(self, tmp_path: Path) -> None:
        """
        Corrections apply in reverse offset order, so both edits land
        exactly even though the first changes the text length.
        """
        body = "Start [[Alpha Ntoe]] middle [[Beta Ntoe]] end.\n"
        vault = build_vault(
            tmp_path,
            {
                "Alpha Note.md": "Alpha.\n",
                "Beta Note.md": "Beta.\n",
                "Note.md": body,
            },
        )
        text, index, unresolved = find(vault, "Note.md")
        assert len(unresolved) == 2

        alpha = index.notes_by_path[vault / "Alpha Note.md"]
        beta = index.notes_by_path[vault / "Beta Note.md"]
        new_text, created = apply_integrity_decisions(
            text,
            unresolved,
            [("correct", alpha), ("correct", beta)],
            vault,
            resolver_for(index),
        )
        assert new_text == "Start [[Alpha Note]] middle [[Beta Note]] end.\n"
        assert created == []

    def test_duplicate_creates_create_once(self, tmp_path: Path) -> None:
        """
        Two create decisions for the same target create one file.
        """
        body = "[[New Idea]] and again [[New Idea]].\n"
        vault = build_vault(tmp_path, {"Note.md": body})
        text, index, unresolved = find(vault, "Note.md")
        assert len(unresolved) == 2
        new_text, created = apply_integrity_decisions(
            text,
            unresolved,
            [("create", None), ("create", None)],
            vault,
            resolver_for(index),
        )
        assert new_text == body
        assert created == [vault / "New Idea.md"]


class TestCreateHelpers:
    """
    "None of these" creation helpers.
    """

    def test_resolve_new_note_target_strips_anchor_and_display(self) -> None:
        """
        Anchor and display parts never leak into the created name.
        """
        assert resolve_new_note_target("Q3 Planning") == "Q3 Planning"
        assert resolve_new_note_target("Q3 Planning#Goals") == "Q3 Planning"
        assert resolve_new_note_target("Q3 Planning|the plan") == "Q3 Planning"

    def test_create_missing_note_at_vault_root(self, tmp_path: Path) -> None:
        """
        The note is bootstrapped empty at the vault root (spec §5.2).
        """
        vault = build_vault(tmp_path, {})
        path = create_missing_note(vault, "Q3 Planning#Goals")
        assert path == vault / "Q3 Planning.md"
        assert path.read_text(encoding="utf-8") == ""


class TestResolveEditorCommand:
    """
    §9.6: ``$VISUAL`` then ``$EDITOR`` (shlex-split), fallback nvim.
    """

    def test_visual_wins_over_editor(self) -> None:
        """
        ``$VISUAL`` is preferred when both are set.
        """
        env = {"VISUAL": "code --wait", "EDITOR": "vim"}
        assert resolve_editor_command(env) == ["code", "--wait"]

    def test_editor_with_flags_is_shlex_split(self) -> None:
        """
        Flags in ``$EDITOR`` are honored via shlex.
        """
        assert resolve_editor_command({"EDITOR": "vim -u NONE"}) == [
            "vim",
            "-u",
            "NONE",
        ]

    def test_empty_env_falls_back_to_nvim(self) -> None:
        """
        With neither variable set, the fallback is ``["nvim"]``.
        """
        assert resolve_editor_command({}) == ["nvim"]
        assert resolve_editor_command({"VISUAL": "  ", "EDITOR": ""}) == ["nvim"]
