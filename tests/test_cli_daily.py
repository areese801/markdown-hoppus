"""
CLI tests for ``hop daily`` (spec §10, §9.9, HOPPUS-49): the subcommand
creates today's note on first use and idempotently reports the same
path on re-invocation, against a hermetic temp Vaults Root.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hoppus import cli

runner = CliRunner()


@pytest.fixture()
def vaults_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Create a temp Vaults Root with one vault and point the CLI at it.

    Patches ``hoppus.cli.load_config`` so the test never reads the real
    ``~/.config`` or ``~/Notes``.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The temp Vaults Root path containing ``Personal/``.
    """
    root = tmp_path / "Notes"
    root.mkdir()
    (root / "Personal").mkdir()

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at the temp Vaults Root.
        """
        return {
            "vaults_root": str(root),
            "default_vault": "Personal",
            "daily_notes": {
                "folder": "Daily",
                "date_format": "%Y-%m-%d",
                "template": "Templates/daily.md",
            },
        }

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return root


def test_daily_creates_and_prints_path(vaults_root: Path) -> None:
    """
    ``daily`` creates today's note and prints its absolute path.
    """
    result = runner.invoke(cli.app, ["daily"])
    assert result.exit_code == 0
    assert "(created)" in result.output

    daily_dir = vaults_root / "Personal" / "Daily"
    notes = list(daily_dir.glob("*.md"))
    assert len(notes) == 1
    assert str(notes[0]) in result.output
    content = notes[0].read_text(encoding="utf-8")
    assert content == f"# {notes[0].stem}\n"


def test_daily_is_idempotent(vaults_root: Path) -> None:
    """
    A second ``daily`` reports the same existing path, not a new file.
    """
    first = runner.invoke(cli.app, ["daily"])
    second = runner.invoke(cli.app, ["daily"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "(exists)" in second.output

    daily_dir = vaults_root / "Personal" / "Daily"
    notes = list(daily_dir.glob("*.md"))
    assert len(notes) == 1
    assert str(notes[0]) in second.output


def test_daily_missing_root_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``daily`` exits 1 when the Vaults Root does not exist.
    """
    missing = tmp_path / "does-not-exist"

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at a nonexistent Vaults Root.
        """
        return {"vaults_root": str(missing), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    result = runner.invoke(cli.app, ["daily"])
    assert result.exit_code == 1
