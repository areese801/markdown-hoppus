"""
CLI tests for ``preview --browser`` (spec §10, §9.5, HOPPUS-56),
hermetic via ``CliRunner`` with the browser opener patched — no real
browser or network is ever touched.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hoppus import cli

runner = CliRunner()


@pytest.fixture()
def note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Create a sample note and pin the CLI config to the builtin renderer.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The path of the sample Markdown note.
    """
    path = tmp_path / "note.md"
    path.write_text("# Hello\n\n```python\nprint(1)\n```\n", encoding="utf-8")

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a minimal config selecting the builtin renderer.
        """
        return {"preview": {"browser_renderer": "builtin"}}

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


def test_preview_without_browser_stays_stubbed(note: Path) -> None:
    """
    Terminal preview (no ``--browser``) prints the stub notice to
    STDERR and exits with the stub exit code (HOPPUS-71 F6).
    """
    result = runner.invoke(cli.app, ["preview", str(note)])
    assert result.exit_code == cli.STUB_EXIT_CODE
    assert cli.NOT_IMPLEMENTED_SUFFIX in result.stderr
