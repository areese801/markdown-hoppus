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


@pytest.fixture()
def launched_apps(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """
    Patch ``HoppusApp.run`` to record the app instance instead of
    starting a live terminal.

    Args:
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        A list that receives each ``HoppusApp`` whose ``run`` was called.
    """
    launched: list[Any] = []
    monkeypatch.setattr(cli.HoppusApp, "run", lambda self: launched.append(self))
    return launched


def test_bare_invocation_launches_tui_on_default_vault(
    vaults_root: Path, launched_apps: list[Any]
) -> None:
    """
    Bare invocation (no subcommand) runs the real TUI on the default vault.
    """
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0
    assert cli.NOT_IMPLEMENTED_SUFFIX not in result.output
    assert len(launched_apps) == 1
    app = launched_apps[0]
    assert app.config["default_vault"] == "Personal"
    assert app.vaults_root == vaults_root


def test_bare_invocation_missing_root_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched_apps: list[Any]
) -> None:
    """
    Bare invocation exits 1 with a clear error when the Vaults Root is
    missing, without launching the TUI.
    """
    missing = tmp_path / "does-not-exist"

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at a nonexistent Vaults Root.
        """
        return {"vaults_root": str(missing), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 1
    assert launched_apps == []


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


def test_open_named_vault_launches_tui(
    vaults_root: Path, launched_apps: list[Any]
) -> None:
    """
    ``open VAULT`` runs the real TUI on the named vault.
    """
    result = runner.invoke(cli.app, ["open", "Work"])
    assert result.exit_code == 0
    assert cli.NOT_IMPLEMENTED_SUFFIX not in result.output
    assert len(launched_apps) == 1
    app = launched_apps[0]
    assert app.config["default_vault"] == "Work"
    assert app.vaults_root == vaults_root


def test_open_defaults_to_configured_vault(
    vaults_root: Path, launched_apps: list[Any]
) -> None:
    """
    ``open`` without an argument falls back to the configured default vault.
    """
    result = runner.invoke(cli.app, ["open"])
    assert result.exit_code == 0
    assert len(launched_apps) == 1
    assert launched_apps[0].config["default_vault"] == "Personal"


def test_open_unknown_vault_exits_nonzero(
    vaults_root: Path, launched_apps: list[Any]
) -> None:
    """
    ``open`` with an unknown vault name exits 1 without launching the TUI.
    """
    result = runner.invoke(cli.app, ["open", "Nope"])
    assert result.exit_code == 1
    assert launched_apps == []


@pytest.mark.parametrize(
    "args",
    [
        ["index"],
        ["reindex"],
        ["new"],
        ["preview"],
    ],
)
def test_stubbed_subcommands_exit_nonzero_on_stderr(args: list[str]) -> None:
    """
    Every stubbed subcommand prints its placeholder line to STDERR and
    exits with the stub exit code, so scripts and CI can detect the
    no-op (HOPPUS-71 F6).
    """
    result = runner.invoke(cli.app, args)
    assert result.exit_code == cli.STUB_EXIT_CODE
    assert cli.NOT_IMPLEMENTED_SUFFIX in result.stderr
    assert cli.NOT_IMPLEMENTED_SUFFIX not in result.stdout


def test_package_main_delegates_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ``hoppus.main`` (the console-script target) delegates to the typer app.
    """
    calls: list[bool] = []
    monkeypatch.setattr(hoppus, "_cli_main", lambda: calls.append(True))
    hoppus.main()
    assert calls == [True]
