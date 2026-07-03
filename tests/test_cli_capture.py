"""
CLI tests for ``hop capture`` (spec §10, §9.11, HOPPUS-51) against a
hermetic temp Vaults Root, mirroring ``tests/test_cli.py``.
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
            "inbox": {"folder": "."},
        }

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return root


def test_capture_creates_note_in_inbox_and_prints_path(vaults_root: Path) -> None:
    """
    ``hop capture "some text"`` writes an inbox note and prints its path.
    """
    result = runner.invoke(cli.app, ["capture", "some text"])
    assert result.exit_code == 0
    printed = Path(result.output.strip())
    assert printed.is_absolute()
    assert printed.parent == vaults_root / "Personal"
    assert printed.suffix == ".md"
    assert printed.read_text(encoding="utf-8") == "some text"


def test_capture_without_text_creates_empty_note(vaults_root: Path) -> None:
    """
    ``hop capture`` with no TEXT creates an empty timestamped note.
    """
    result = runner.invoke(cli.app, ["capture"])
    assert result.exit_code == 0
    printed = Path(result.output.strip())
    assert printed.read_text(encoding="utf-8") == ""


def test_capture_unknown_default_vault_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``hop capture`` exits 1 when the default vault does not exist.
    """
    root = tmp_path / "Notes"
    root.mkdir()

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config whose default vault is absent from the root.
        """
        return {"vaults_root": str(root), "default_vault": "Nope"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    result = runner.invoke(cli.app, ["capture", "text"])
    assert result.exit_code == 1
