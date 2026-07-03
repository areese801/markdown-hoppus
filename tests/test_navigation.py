"""
Navigation tests for intercepted wikilink / embed handling (HOPPUS-31).

Headless Textual Pilot tests (``App.run_test`` wrapped in ``asyncio.run``,
matching the rest of the suite) drive ``PreviewPane.open_target`` and the
render pipeline directly for determinism, per spec §9.5: note / heading /
block / attachment navigation, inline embed transclusion, and the subtle
unresolved-link indicator.
"""

import asyncio
from pathlib import Path

from textual.widgets import Markdown

from hoppus.config import default_config
from hoppus.model import Link
from hoppus.render.ofm_markdown import (
    UNRESOLVED_MARKER,
    LinkTarget,
    transform_ofm,
)
from hoppus.tui.app import HoppusApp


def make_app(vaults_root: Path) -> HoppusApp:
    """
    Build a HoppusApp rooted above the sample vault.

    :param vaults_root: Directory containing the vault under test.
    :returns: An unmounted HoppusApp instance.
    """
    return HoppusApp(config=default_config(), vaults_root=vaults_root)


def test_note_link_opens_target(sample_vault: Path) -> None:
    """
    Activating a plain note link loads the target note (status updates).
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            assert app.note_title == "Index"
            handled = await app.preview.open_target(LinkTarget(target="Target Note"))
            await pilot.pause()
            assert handled
            assert app.note_title == "Target Note"

    asyncio.run(run())


def test_heading_anchor_link_scrolls_without_error(sample_vault: Path) -> None:
    """
    A cross-note heading-anchor link opens the note and scrolls cleanly.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            handled = await app.preview.open_target(
                LinkTarget(target="Target Note", anchor="Section One")
            )
            await pilot.pause()
            assert handled
            assert app.note_title == "Target Note"
            markdown = app.query_one("#preview-markdown", Markdown)
            # The anchor genuinely exists in the rendered document.
            assert markdown.goto_anchor("section-one")

    asyncio.run(run())


def test_same_note_anchor_scrolls_without_reload(sample_vault: Path) -> None:
    """
    A ``[[#Heading]]`` click scrolls the current note; no note change.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            handled = await app.preview.open_target(
                LinkTarget(target="", anchor="Local Heading")
            )
            await pilot.pause()
            assert handled
            assert app.note_title == "Index"
            handled = await app.preview.open_target(
                LinkTarget(target="", anchor="^local-block")
            )
            await pilot.pause()
            assert handled
            assert app.note_title == "Index"

    asyncio.run(run())


def test_block_anchor_link_scrolls_to_block(sample_vault: Path) -> None:
    """
    A ``[[Note#^block-id]]`` click opens the note and locates the block.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            handled = await app.preview.open_target(
                LinkTarget(target="Target Note", anchor="^quote-1")
            )
            await pilot.pause()
            assert handled
            assert app.note_title == "Target Note"

    asyncio.run(run())


def test_attachment_target_is_handled_without_navigation(sample_vault: Path) -> None:
    """
    An attachment link is acknowledged (handled) but opens no note.
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            handled = await app.preview.open_target(LinkTarget(target="image.png"))
            await pilot.pause()
            assert handled
            assert app.note_title == "Index"

    asyncio.run(run())


def test_embeds_render_transcluded_content_inline(sample_vault: Path) -> None:
    """
    Note/heading embeds transclude target content; attachments show a
    placeholder line (spec §9.5).
    """

    async def run() -> None:
        app = make_app(sample_vault.parent)
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", vault_root=sample_vault)
            await pilot.pause()
            rendered = app.preview._rendered
            assert rendered is not None
            # ![[Target Note]] pulls in the target's body, sans frontmatter.
            assert "Introductory text." in rendered
            assert "aliases:" not in rendered
            # ![[Target Note#Section One]] pulls in the section content.
            assert "Content under section one" in rendered
            # ![[image.png]] shows the attachment placeholder.
            assert "📎" in rendered
            assert "image.png" in rendered

    asyncio.run(run())


def test_unresolved_link_is_marked_and_unhandled(tmp_path: Path) -> None:
    """
    An unresolved wikilink gets the subtle marker; clicking it returns
    False so the app can notify.
    """

    async def run() -> None:
        vault = tmp_path / "MarkVault"
        vault.mkdir()
        (vault / "Real.md").write_text("# Real\n", encoding="utf-8")
        (vault / "Home.md").write_text(
            "See [[Real]] and [[Ghost Note]].\n", encoding="utf-8"
        )
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Home.md", vault_root=vault)
            await pilot.pause()
            rendered = app.preview._rendered
            assert rendered is not None
            assert f"[Ghost Note {UNRESOLVED_MARKER}](" in rendered
            assert "[Real](" in rendered
            assert f"[Real {UNRESOLVED_MARKER}](" not in rendered
            assert not await app.preview.open_target(LinkTarget(target="Ghost Note"))

    asyncio.run(run())


def test_transform_ofm_marks_only_unresolved_wikilinks() -> None:
    """
    ``is_resolved`` marks failing wikilinks but skips embeds; the
    default keeps today's unmarked output.
    """
    text = "A [[Real]] and [[Ghost]] and ![[Ghost]] embed."

    def is_resolved(link: Link) -> bool:
        return link.target == "Real"

    marked = transform_ofm(text, is_resolved=is_resolved)
    assert f"[Ghost {UNRESOLVED_MARKER}](" in marked
    assert "[Real](" in marked
    # Embeds are never marked here; transclusion handles them upstream.
    assert marked.count(UNRESOLVED_MARKER) == 1
    assert UNRESOLVED_MARKER not in transform_ofm(text)

    resolved_all = transform_ofm(text, is_resolved=lambda link: True)
    assert resolved_all == transform_ofm(text)
