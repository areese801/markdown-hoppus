"""
Pilot tests for the yank menu (spec §9.14, HOPPUS-54).

Runs the app headless against a temp vault, opens the yank menu with
``y``, and feeds each choice by dismissing the modal directly (rather
than relying on key timing). ``hoppus.clipboard.copy`` is patched to
capture what would be copied — no real clipboard is touched.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hoppus import clipboard
from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.yank_menu import (
    YANK_BODY,
    YANK_PATH,
    YANK_WIKILINK,
    YankMenuModal,
)

_BODY = "# Alpha\n\nSome body text linking [[Beta]].\n"


@pytest.fixture()
def vaults_root(tmp_path: Path) -> Path:
    """
    Build a temp Vaults Root with one small vault.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The Vaults Root as a ``Path``.
    """
    vault = tmp_path / "Personal"
    vault.mkdir()
    (vault / "Alpha.md").write_text(_BODY, encoding="utf-8")
    (vault / "Beta.md").write_text("# Beta\n", encoding="utf-8")
    return tmp_path


def make_app(vaults_root: Path) -> HoppusApp:
    """
    Build a HoppusApp rooted at the given Vaults Root.

    :param vaults_root: Directory containing the vaults.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    config["default_vault"] = "Personal"
    return HoppusApp(config=config, vaults_root=vaults_root)


def _run_yank(
    vaults_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
) -> tuple[list[tuple[str, str]], HoppusApp]:
    """
    Open Alpha.md, pick one yank choice, and capture the copy call.

    :param vaults_root: The temp Vaults Root.
    :param monkeypatch: pytest's monkeypatch fixture.
    :param choice: The yank kind to feed to the modal.
    :returns: ``(captured (text, backend) calls, the app)``.
    """
    captured: list[tuple[str, str]] = []

    def fake_copy(text: str, *, backend: str = "pyperclip", **_kwargs: Any) -> None:
        captured.append((text, backend))

    monkeypatch.setattr(clipboard, "copy", fake_copy)
    app = make_app(vaults_root)

    async def run() -> None:
        async with app.run_test() as pilot:
            vault = vaults_root / "Personal"
            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.press("y")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, YankMenuModal)
            modal.dismiss(choice)
            await pilot.pause()

    asyncio.run(run())
    return captured, app


def test_yank_body_copies_note_text(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Choosing Body copies the note's full text via the config backend.
    """
    captured, _app = _run_yank(vaults_root, monkeypatch, YANK_BODY)
    assert captured == [(_BODY, "pyperclip")]


def test_yank_path_copies_absolute_path(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Choosing Path copies the note's absolute path string.
    """
    captured, _app = _run_yank(vaults_root, monkeypatch, YANK_PATH)
    assert captured == [(str(vaults_root / "Personal" / "Alpha.md"), "pyperclip")]


def test_yank_wikilink_copies_shortest_unique_link(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Choosing Wikilink copies the ``[[Title]]`` shortest-unique form.
    """
    captured, _app = _run_yank(vaults_root, monkeypatch, YANK_WIKILINK)
    assert captured == [("[[Alpha]]", "pyperclip")]


def test_yank_menu_without_active_note_notifies(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``y`` with no active note notifies and pushes no modal.
    """
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        clipboard,
        "copy",
        lambda text, *, backend="pyperclip", **_kw: captured.append((text, backend)),
    )
    app = make_app(vaults_root)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("y")
            await pilot.pause()
            assert not isinstance(app.screen, YankMenuModal)

    asyncio.run(run())
    assert captured == []


def test_yank_clipboard_error_is_notified_not_raised(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A ``ClipboardError`` from ``copy`` never crashes the app.
    """

    def broken_copy(_text: str, *, backend: str = "pyperclip", **_kw: Any) -> None:
        raise clipboard.ClipboardError("no clipboard tool available")

    monkeypatch.setattr(clipboard, "copy", broken_copy)
    app = make_app(vaults_root)

    async def run() -> None:
        async with app.run_test() as pilot:
            vault = vaults_root / "Personal"
            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.press("y")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, YankMenuModal)
            modal.dismiss(YANK_BODY)
            await pilot.pause()

    asyncio.run(run())
