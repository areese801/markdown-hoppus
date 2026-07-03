"""
CLI tests for ``hop find`` (spec §10, §3, HOPPUS-46).

Follows ``tests/test_cli.py``'s hermetic approach: ``hoppus.cli.load_config``
is monkeypatched at a temp Vaults Root, ``cli.find_binary`` fakes fzf/bat
detection, and ``cli.run_find`` is monkeypatched so no test spawns a real
``fzf``.
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
    Create a temp Vaults Root with a default vault holding one note.

    Patches ``hoppus.cli.load_config`` so the test never reads the real
    ``~/.config`` or ``~/Notes``.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The temp Vaults Root path containing ``Personal/Alpha.md``.
    """
    root = tmp_path / "Notes"
    root.mkdir()
    personal = root / "Personal"
    personal.mkdir()
    (personal / "Alpha.md").write_text("# Alpha\n", encoding="utf-8")

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at the temp Vaults Root.
        """
        return {"vaults_root": str(root), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return root


def test_find_without_fzf_exits_nonzero(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With no ``fzf`` on PATH, ``find`` exits 1 and the error names fzf.
    """
    monkeypatch.setattr(cli, "find_binary", lambda name: None)
    result = runner.invoke(cli.app, ["find"])
    assert result.exit_code == 1
    assert "fzf" in result.stderr


def test_find_prints_selected_path(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With fzf "present" and a faked selection, ``find`` prints the
    absolute note path and exits 0.
    """
    chosen = vaults_root / "Personal" / "Alpha.md"
    monkeypatch.setattr(cli, "find_binary", lambda name: "/fake/bin/fzf")

    def fake_run_find(index, query, *, fzf_path, preview_path=None) -> Path:
        """
        Assert the wiring and return the canned selection.
        """
        assert fzf_path == "/fake/bin/fzf"
        assert query == "alp"
        assert chosen in index.notes_by_path
        return chosen

    monkeypatch.setattr(cli, "run_find", fake_run_find)
    result = runner.invoke(cli.app, ["find", "alp"])
    assert result.exit_code == 0
    assert str(chosen) in result.output


def test_find_cancelled_prints_nothing_and_exits_zero(
    vaults_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A cancelled fzf session (run_find → None) exits 0 with no stdout.
    """
    monkeypatch.setattr(cli, "find_binary", lambda name: "/fake/bin/fzf")
    monkeypatch.setattr(
        cli, "run_find", lambda index, query, *, fzf_path, preview_path=None: None
    )
    result = runner.invoke(cli.app, ["find"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_find_unknown_default_vault_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A configured default vault that does not exist exits 1.
    """
    root = tmp_path / "Notes"
    root.mkdir()

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config whose default vault is missing.
        """
        return {"vaults_root": str(root), "default_vault": "Nope"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "find_binary", lambda name: "/fake/bin/fzf")
    result = runner.invoke(cli.app, ["find"])
    assert result.exit_code == 1
