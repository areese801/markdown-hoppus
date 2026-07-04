"""
CLI tests for ``preview`` (spec §10, §9.5, HOPPUS-56, HOPPUS-99):
the ``--browser`` path with the opener patched, and the terminal path
through the shared ``hoppus.render.terminal`` renderers — hermetic via
``CliRunner``; no real browser, glow binary, or network is ever touched.
"""

from pathlib import Path
from typing import Any

import pytest
from rich.text import Text
from typer.testing import CliRunner

from hoppus import cli

runner = CliRunner()


@pytest.fixture()
def note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Create a sample note and pin the CLI config to the builtin renderer.

    Also builds a vaults root with a ``Main`` vault (containing
    ``Real Note.md``) and an ``Other`` vault (containing ``Alt.md``) so
    the HOPPUS-72 note-name resolution path is exercisable.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The path of the sample Markdown note.
    """
    path = tmp_path / "note.md"
    path.write_text("# Hello\n\n```python\nprint(1)\n```\n", encoding="utf-8")

    vaults_root = tmp_path / "vaults"
    main = vaults_root / "Main"
    main.mkdir(parents=True)
    (main / "Real Note.md").write_text("# From The Vault\n", encoding="utf-8")
    other = vaults_root / "Other"
    other.mkdir()
    (other / "Alt.md").write_text("# Other Vault Note\n", encoding="utf-8")

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a minimal config selecting the builtin renderer.
        """
        return {
            "preview": {"browser_renderer": "builtin"},
            "vaults_root": str(vaults_root),
            "default_vault": "Main",
        }

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return path


def test_preview_browser_renders_and_opens(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``preview PATH --browser`` renders locally, "opens" via the patched
    opener, and prints the HTML path.
    """
    opened: list[Path] = []

    def fake_open(html_path: Path) -> str:
        """
        Record the rendered path instead of launching a browser.
        """
        opened.append(html_path)
        return html_path.resolve().as_uri()

    monkeypatch.setattr(cli, "open_in_browser", fake_open)
    result = runner.invoke(cli.app, ["preview", str(note), "--browser"])
    assert result.exit_code == 0
    assert len(opened) == 1
    html_path = opened[0]
    assert html_path.suffix == ".html"
    assert str(html_path) in result.output
    content = html_path.read_text(encoding="utf-8")
    assert "<h1>Hello</h1>" in content
    assert content.startswith("<!doctype html>")


def test_preview_browser_missing_file_exits_nonzero(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A nonexistent PATH exits 1 without opening anything.
    """
    opened: list[Path] = []
    monkeypatch.setattr(cli, "open_in_browser", opened.append)
    result = runner.invoke(cli.app, ["preview", "/no/such/note.md", "--browser"])
    assert result.exit_code == 1
    assert opened == []


def test_preview_browser_without_path_exits_nonzero(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``preview --browser`` without a PATH exits 1.
    """
    result = runner.invoke(cli.app, ["preview", "--browser"])
    assert result.exit_code == 1


def test_preview_go_grip_path_is_guarded(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With ``browser_renderer: go-grip`` and the binary "present", the CLI
    delegates to go-grip instead of the builtin renderer.
    """

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config preferring the go-grip renderer.
        """
        return {"preview": {"browser_renderer": "go-grip"}}

    launched: list[Path] = []
    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "go_grip_available", lambda: True)
    monkeypatch.setattr(cli, "launch_go_grip", launched.append)
    result = runner.invoke(cli.app, ["preview", str(note), "--browser"])
    assert result.exit_code == 0
    assert launched == [note]
    assert "go-grip" in result.output


def test_preview_resolves_note_name_in_default_vault(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A PATH that is not a file is resolved as a note name within the
    default vault, matching ``audit``/MCP semantics (HOPPUS-72 F9).
    """
    opened: list[Path] = []

    def fake_open(html_path: Path) -> str:
        """
        Record the rendered path instead of launching a browser.
        """
        opened.append(html_path)
        return html_path.resolve().as_uri()

    monkeypatch.setattr(cli, "open_in_browser", fake_open)
    result = runner.invoke(cli.app, ["preview", "Real Note", "--browser"])
    assert result.exit_code == 0
    assert len(opened) == 1
    content = opened[0].read_text(encoding="utf-8")
    assert "<h1>From The Vault</h1>" in content


def test_preview_vault_option_selects_the_vault(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``--vault`` resolves the note name in the named vault instead of
    the default one (HOPPUS-72 F9).
    """
    opened: list[Path] = []
    monkeypatch.setattr(cli, "open_in_browser", opened.append)
    result = runner.invoke(cli.app, ["preview", "Alt", "--vault", "Other", "--browser"])
    assert result.exit_code == 0
    assert "<h1>Other Vault Note</h1>" in opened[0].read_text(encoding="utf-8")


def test_preview_explicit_path_wins_over_note_lookup(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A PATH that exists as a file is used directly — no vault lookup
    (HOPPUS-72 F9 resolution order).
    """
    opened: list[Path] = []
    monkeypatch.setattr(cli, "open_in_browser", opened.append)
    result = runner.invoke(cli.app, ["preview", str(note), "--browser"])
    assert result.exit_code == 0
    assert "<h1>Hello</h1>" in opened[0].read_text(encoding="utf-8")


def test_preview_unknown_note_name_exits_nonzero(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A reference that is neither a file nor a resolvable note name exits
    1 without opening anything (HOPPUS-72 F9).
    """
    opened: list[Path] = []
    monkeypatch.setattr(cli, "open_in_browser", opened.append)
    result = runner.invoke(cli.app, ["preview", "No Such Note", "--browser"])
    assert result.exit_code == 1
    assert opened == []


def test_preview_terminal_renders_note(note: Path) -> None:
    """
    Terminal preview (no ``--browser``) renders the note's Markdown to
    STDOUT and exits 0 (HOPPUS-99).
    """
    result = runner.invoke(cli.app, ["preview", str(note)])
    assert result.exit_code == 0
    assert "Hello" in result.output
    assert "print(1)" in result.output


def test_preview_terminal_resolves_note_name(note: Path) -> None:
    """
    Terminal preview resolves a note name in the default vault, same as
    the ``--browser`` path (HOPPUS-72 F9 semantics).
    """
    result = runner.invoke(cli.app, ["preview", "Real Note"])
    assert result.exit_code == 0
    assert "From The Vault" in result.output


def test_preview_terminal_uses_glow_when_selected(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With ``preview.renderer: glow`` and glow "available", the terminal
    preview prints glow's output instead of the native render.
    """
    monkeypatch.setattr(cli, "select_renderer", lambda config: "glow", raising=True)
    monkeypatch.setattr(
        cli, "render_glow", lambda text: Text("GLOW OUTPUT"), raising=True
    )
    result = runner.invoke(cli.app, ["preview", str(note)])
    assert result.exit_code == 0
    assert "GLOW OUTPUT" in result.output


def test_preview_terminal_falls_back_when_glow_fails(
    note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    When glow is selected but fails (returns None), the terminal preview
    falls back to the native renderer instead of erroring.
    """
    monkeypatch.setattr(cli, "select_renderer", lambda config: "glow", raising=True)
    monkeypatch.setattr(cli, "render_glow", lambda text: None, raising=True)
    result = runner.invoke(cli.app, ["preview", str(note)])
    assert result.exit_code == 0
    assert "Hello" in result.output


def test_preview_terminal_unknown_note_exits_nonzero(note: Path) -> None:
    """
    A reference that is neither a file nor a resolvable note name exits
    1 on the terminal path too.
    """
    result = runner.invoke(cli.app, ["preview", "No Such Note"])
    assert result.exit_code == 1


def test_preview_without_path_exits_nonzero(note: Path) -> None:
    """
    ``preview`` without a PATH exits 1 with a clear message.
    """
    result = runner.invoke(cli.app, ["preview"])
    assert result.exit_code == 1
    assert "requires a PATH" in result.stderr
