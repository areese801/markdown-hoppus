"""
Tests for the Markdown preview + raw fallback (HOPPUS-27, spec §9.5).

Covers the ``hoppus.render.terminal`` renderers (native, raw, glow
detect-and-fallback) and the ``PreviewPane`` in the TUI: note rendering,
the raw toggle, automatic raw fallback, and click-to-open navigation of
``hoppus://`` wikilinks. All Pilot tests run headless via ``App.run_test``
wrapped in ``asyncio.run`` so no async test plugin is required.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import Markdown, Static

from hoppus.config import default_config
from hoppus.render import terminal
from hoppus.render.ofm_markdown import LinkTarget
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.preview import PreviewPane


def make_app(config_overrides: dict[str, Any] | None = None) -> HoppusApp:
    """
    Build a HoppusApp with an explicit config (no filesystem reads).

    Args:
        config_overrides: Top-level keys to override in the defaults.

    Returns:
        An unmounted HoppusApp instance.
    """
    config = default_config()
    config.update(config_overrides or {})
    return HoppusApp(config=config)


# -- hoppus.render.terminal ---------------------------------------------------


def test_render_native_transforms_wikilinks() -> None:
    """
    Native rendering rewrites wikilinks into ``hoppus://`` links.
    """
    rendered = terminal.render_native("See [[Target Note|the target]].")
    assert "[the target](hoppus://note?" in rendered
    assert "[[" not in rendered


def test_render_raw_is_markdown_syntax() -> None:
    """
    The raw view is a Rich Syntax renderable of the untransformed text.
    """
    text = "# Title\n\nA [[Wikilink]].\n"
    syntax = terminal.render_raw(text)
    assert isinstance(syntax, Syntax)
    assert syntax.code == text
    assert syntax.lexer is not None


def test_select_renderer_requires_config_and_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Glow is selected only when configured AND present on $PATH.
    """
    config = default_config()

    monkeypatch.setattr(terminal.shutil, "which", lambda name: "/usr/bin/glow")
    assert terminal.select_renderer(config) == "native"
    config["preview"]["renderer"] = "glow"
    assert terminal.select_renderer(config) == "glow"

    monkeypatch.setattr(terminal.shutil, "which", lambda name: None)
    assert terminal.select_renderer(config) == "native"
    assert terminal.select_renderer({}) == "native"


def test_render_glow_without_binary_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A missing glow binary yields None without spawning a subprocess.
    """
    monkeypatch.setattr(terminal.shutil, "which", lambda name: None)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess must not run when glow is absent")

    monkeypatch.setattr(terminal.subprocess, "run", boom)
    assert terminal.render_glow("# Note") is None


def test_render_glow_pipes_through_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    With glow present, its ANSI output comes back as a Rich Text.
    """
    monkeypatch.setattr(terminal.shutil, "which", lambda name: "/usr/bin/glow")
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="\x1b[1mNote\x1b[0m", stderr=""
        )

    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    rendered = terminal.render_glow("# Note")
    assert isinstance(rendered, Text)
    assert rendered.plain == "Note"
    assert seen["cmd"][0] == terminal.GLOW_BINARY
    assert seen["input"] == "# Note"


def test_render_glow_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A non-zero exit or subprocess error falls back to None.
    """
    monkeypatch.setattr(terminal.shutil, "which", lambda name: "/usr/bin/glow")
    monkeypatch.setattr(
        terminal.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )
    assert terminal.render_glow("# Note") is None

    def raise_timeout(cmd: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 1.0)

    monkeypatch.setattr(terminal.subprocess, "run", raise_timeout)
    assert terminal.render_glow("# Note") is None


# -- PreviewPane in the TUI ---------------------------------------------------


def test_preview_renders_note_and_updates_status(sample_vault: Path) -> None:
    """
    set_note mounts transformed Markdown and updates title/word count.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Target Note.md", sample_vault)
            await pilot.pause()
            markdown = app.query_one("#preview-markdown", Markdown)
            assert markdown.display
            assert not app.query_one("#preview-raw", Static).display
            assert "[Index](hoppus://note?" in markdown.source
            assert app.note_title == "Target Note"
            assert app.word_count > 0

    asyncio.run(run())


def test_raw_toggle_switches_views(sample_vault: Path) -> None:
    """
    The ``m`` binding flips to the raw Syntax view and back.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", sample_vault)
            await pilot.pause()
            markdown = app.query_one("#preview-markdown", Markdown)
            raw = app.query_one("#preview-raw", Static)
            assert markdown.display and not raw.display

            await pilot.press("m")
            assert raw.display and not markdown.display
            assert app.preview.raw_mode
            rendered = raw.content
            assert isinstance(rendered, Syntax)
            assert "[[Target Note]]" in rendered.code

            await pilot.press("m")
            assert markdown.display and not raw.display
            assert not app.preview.raw_mode

    asyncio.run(run())


def test_native_render_failure_falls_back_to_raw(
    sample_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    When the native render raises, the pane drops into raw mode.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            monkeypatch.setattr(
                "hoppus.tui.panes.preview.render_native",
                lambda text: (_ for _ in ()).throw(ValueError("bad markdown")),
            )
            await app.open_note(sample_vault / "Index.md", sample_vault)
            await pilot.pause()
            assert app.preview.raw_mode
            raw = app.query_one("#preview-raw", Static)
            assert raw.display
            assert isinstance(raw.content, Syntax)
            assert app.note_title == "Index"

    asyncio.run(run())


def test_wikilink_click_navigates_to_target(sample_vault: Path) -> None:
    """
    Clicking a ``hoppus://`` wikilink loads the target note.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", sample_vault)
            await pilot.pause()
            markdown = app.query_one("#preview-markdown", Markdown)
            href = "hoppus://note?target=Target%2520Note"
            markdown.post_message(Markdown.LinkClicked(markdown, href))
            await pilot.pause()
            assert app.preview.note_path == sample_vault / "Target Note.md"
            assert app.note_title == "Target Note"

    asyncio.run(run())


def test_unresolved_wikilink_click_notifies(sample_vault: Path) -> None:
    """
    A wikilink that does not resolve notifies instead of navigating.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", sample_vault)
            await pilot.pause()
            markdown = app.query_one("#preview-markdown", Markdown)
            href = "hoppus://note?target=No%2520Such%2520Note"
            markdown.post_message(Markdown.LinkClicked(markdown, href))
            await pilot.pause()
            assert app.preview.note_path == sample_vault / "Index.md"
            notifications = list(app._notifications)
            assert notifications
            assert "No Such Note" in notifications[-1].message

    asyncio.run(run())


def test_non_hoppus_links_are_ignored(sample_vault: Path) -> None:
    """
    http(s) hrefs are out of scope: no navigation, no notification.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await app.open_note(sample_vault / "Index.md", sample_vault)
            await pilot.pause()
            markdown = app.query_one("#preview-markdown", Markdown)
            markdown.post_message(
                Markdown.LinkClicked(markdown, "https://example.com/page")
            )
            await pilot.pause()
            assert app.preview.note_path == sample_vault / "Index.md"
            assert not list(app._notifications)

    asyncio.run(run())


def test_resolve_target_without_vault_root_is_none(tmp_path: Path) -> None:
    """
    Without a vault root the pane cannot resolve targets.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test():
            note = tmp_path / "Loose.md"
            note.write_text("# Loose\n", encoding="utf-8")
            await app.preview.set_note(note)
            assert app.preview.resolve_target(LinkTarget(target="Loose")) is None

    asyncio.run(run())


def test_glow_renderer_used_when_configured(
    sample_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With ``preview.renderer: glow`` and a (mocked) binary, glow output
    shows; without the binary the pane falls back to native.
    """

    async def run() -> None:
        config = default_config()
        config["preview"]["renderer"] = "glow"
        app = HoppusApp(config=config)
        async with app.run_test() as pilot:
            monkeypatch.setattr(terminal.shutil, "which", lambda name: "/usr/bin/glow")
            monkeypatch.setattr(
                terminal.subprocess,
                "run",
                lambda cmd, **kwargs: subprocess.CompletedProcess(
                    cmd, 0, stdout="styled glow output", stderr=""
                ),
            )
            await app.open_note(sample_vault / "Index.md", sample_vault)
            await pilot.pause()
            raw = app.query_one("#preview-raw", Static)
            assert raw.display
            assert isinstance(raw.content, Text)
            assert raw.content.plain == "styled glow output"

            # Binary disappears: detect-and-fallback to native.
            monkeypatch.setattr(terminal.shutil, "which", lambda name: None)
            await app.open_note(sample_vault / "Target Note.md")
            await pilot.pause()
            assert app.query_one("#preview-markdown", Markdown).display
            assert not raw.display

    asyncio.run(run())


def test_main_pane_is_preview_pane() -> None:
    """
    The main pane placeholder is replaced by the PreviewPane widget.
    """

    async def run() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.query_one("#main-pane"), PreviewPane)

    asyncio.run(run())
