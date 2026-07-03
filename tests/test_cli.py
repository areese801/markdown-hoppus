"""
CLI tests for the typer app shared by ``hoppus`` / ``mark`` / ``hop``
(spec §1, §10): help output, the ``vaults`` subcommand against a hermetic
temp Vaults Root, stubbed subcommands, and console-script wiring.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import hoppus
from hoppus import cli

runner = CliRunner()

EXPECTED_SUBCOMMANDS = [
    "open",
    "find",
    "new",
    "daily",
    "capture",
    "preview",
    "index",
    "reindex",
    "audit",
    "doctor",
    "mcp",
    "vaults",
]


@pytest.fixture()
def vaults_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Create a temp Vaults Root with two vaults and point the CLI at it.

    Patches ``hoppus.cli.load_config`` so the test never reads the real
    ``~/.config`` or ``~/Notes``.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The temp Vaults Root path containing ``Personal/`` and ``Work/``.
    """
    root = tmp_path / "Notes"
    root.mkdir()
    (root / "Personal").mkdir()
    (root / "Work").mkdir()
    (root / ".hidden").mkdir()

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at the temp Vaults Root.
        """
        config = {"vaults_root": str(root), "default_vault": "Personal"}
        return config

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return root


def test_help_exits_zero_and_lists_subcommands() -> None:
    """
    ``--help`` exits 0 and shows the full subcommand tree.
    """
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for subcommand in EXPECTED_SUBCOMMANDS:
        assert subcommand in result.output


def test_bare_invocation_prints_tui_stub() -> None:
    """
    Bare invocation (no subcommand) prints the TUI placeholder notice.
    """
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0
    assert cli.NOT_IMPLEMENTED_SUFFIX in result.output


def test_version_flag() -> None:
    """
    ``--version`` prints the package version and exits 0.
    """
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert hoppus.__version__ in result.output


def test_vaults_lists_discovered_vaults(vaults_root: Path) -> None:
    """
    ``vaults`` lists each vault's name and path under the temp root.
    """
    result = runner.invoke(cli.app, ["vaults"])
    assert result.exit_code == 0
    assert "Personal" in result.output
    assert "Work" in result.output
    assert str(vaults_root / "Personal") in result.output
    assert ".hidden" not in result.output


def test_vaults_missing_root_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``vaults`` exits 1 with an error when the Vaults Root does not exist.
    """
    missing = tmp_path / "does-not-exist"

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at a nonexistent Vaults Root.
        """
        return {"vaults_root": str(missing), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    result = runner.invoke(cli.app, ["vaults"])
    assert result.exit_code == 1


def test_open_named_vault(vaults_root: Path) -> None:
    """
    ``open VAULT`` resolves the named vault and prints the TUI stub notice.
    """
    result = runner.invoke(cli.app, ["open", "Work"])
    assert result.exit_code == 0
    assert "Work" in result.output
    assert cli.NOT_IMPLEMENTED_SUFFIX in result.output


def test_open_defaults_to_configured_vault(vaults_root: Path) -> None:
    """
    ``open`` without an argument falls back to the configured default vault.
    """
    result = runner.invoke(cli.app, ["open"])
    assert result.exit_code == 0
    assert "Personal" in result.output


def test_open_unknown_vault_exits_nonzero(vaults_root: Path) -> None:
    """
    ``open`` with an unknown vault name exits 1.
    """
    result = runner.invoke(cli.app, ["open", "Nope"])
    assert result.exit_code == 1


@pytest.mark.parametrize(
    "args",
    [
        ["index"],
        ["reindex"],
        ["mcp"],
        ["new"],
        ["daily"],
        ["capture"],
        ["preview"],
    ],
)
def test_stubbed_subcommands_exit_zero(args: list[str]) -> None:
    """
    Every stubbed subcommand runs, exits 0, and prints its placeholder line.
    """
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0
    assert cli.NOT_IMPLEMENTED_SUFFIX in result.output


def test_package_main_delegates_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ``hoppus.main`` (the console-script target) delegates to the typer app.
    """
    calls: list[bool] = []
    monkeypatch.setattr(hoppus, "_cli_main", lambda: calls.append(True))
    hoppus.main()
    assert calls == [True]
