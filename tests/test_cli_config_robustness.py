"""
End-to-end CLI tests for config robustness (HOPPUS-87/88/89).

Unlike ``tests/test_cli.py`` these tests exercise the *real*
``hoppus.config.load_config`` against a temp ``$XDG_CONFIG_HOME``, so the
malformed-config, partial-config, and resolved-config-path behaviors are
verified through the same code path a user hits.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hoppus import cli

runner = CliRunner()


@pytest.fixture()
def xdg_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point ``$XDG_CONFIG_HOME`` at a temp dir so the real config loader
    never touches ``~/.config``.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The path of the global config file inside the temp XDG home
        (not created).
    """
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg / "hoppus" / "config.yaml"


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


def _write_config(path: Path, text: str) -> None:
    """
    Write a global config file, creating parent directories.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_vaults(tmp_path: Path, *names: str) -> Path:
    """
    Create a temp Vaults Root containing the named vault directories.
    """
    root = tmp_path / "Notes"
    root.mkdir(exist_ok=True)
    for name in names:
        (root / name).mkdir()
    return root


class TestMalformedConfig:
    """
    HOPPUS-87: a YAML syntax error → clean error, never a traceback.
    """

    def test_vaults_reports_clean_error(self, xdg_config: Path) -> None:
        """
        ``hop vaults`` names the malformed file, exits 1, no traceback.
        """
        _write_config(xdg_config, "vaults_root: [unclosed\n")
        result = runner.invoke(cli.app, ["vaults"])
        assert result.exit_code == 1
        assert "Malformed config file" in result.stderr
        assert str(xdg_config) in result.stderr
        assert "hop doctor" in result.stderr
        assert "Traceback" not in result.stderr
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_bare_invocation_reports_clean_error(
        self, xdg_config: Path, launched_apps: list[Any]
    ) -> None:
        """
        Bare ``hop`` exits 1 without launching the TUI or crashing.
        """
        _write_config(xdg_config, "daily_notes:\n\tfolder: tabs-are-invalid\n")
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 1
        assert launched_apps == []
        assert "Malformed config file" in result.stderr
        assert "Traceback" not in result.stderr

    def test_doctor_reports_unhealthy_config_not_crash(self, xdg_config: Path) -> None:
        """
        ``hop doctor`` still completes (exit 0) and flags the config as
        unhealthy with the parse reason and the file path.
        """
        _write_config(xdg_config, "vaults_root: [unclosed\n")
        result = runner.invoke(cli.app, ["doctor"])
        assert result.exit_code == 0
        assert "Malformed config file" in result.output
        assert str(xdg_config) in result.output
        assert "Traceback" not in result.output


class TestPartialConfig:
    """
    HOPPUS-88: ``vaults_root`` set but no ``default_vault``.
    """

    def test_single_vault_is_used_as_default(
        self, xdg_config: Path, tmp_path: Path, launched_apps: list[Any]
    ) -> None:
        """
        With exactly one vault discovered, bare ``hop`` proceeds on it.
        """
        root = _make_vaults(tmp_path, "Solo")
        _write_config(xdg_config, f"vaults_root: {root}\ndefault_vault:\n")
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 0
        assert len(launched_apps) == 1
        assert launched_apps[0].config["default_vault"] == "Solo"

    def test_several_vaults_ask_user_to_choose(
        self, xdg_config: Path, tmp_path: Path, launched_apps: list[Any]
    ) -> None:
        """
        With several vaults and no default, bare ``hop`` exits 1 with
        "choose one of" guidance naming the vaults and the config file.
        """
        root = _make_vaults(tmp_path, "Personal", "Work")
        _write_config(xdg_config, f"vaults_root: {root}\ndefault_vault:\n")
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 1
        assert launched_apps == []
        assert "No default_vault set" in result.stderr
        assert "Personal" in result.stderr
        assert "Work" in result.stderr
        assert str(xdg_config) in result.stderr

    def test_open_with_explicit_vault_still_works(
        self, xdg_config: Path, tmp_path: Path, launched_apps: list[Any]
    ) -> None:
        """
        ``hop open VAULT`` bypasses the missing default entirely.
        """
        root = _make_vaults(tmp_path, "Personal", "Work")
        _write_config(xdg_config, f"vaults_root: {root}\ndefault_vault:\n")
        result = runner.invoke(cli.app, ["open", "Work"])
        assert result.exit_code == 0
        assert len(launched_apps) == 1
        assert launched_apps[0].config["default_vault"] == "Work"

    def test_vaults_still_lists(self, xdg_config: Path, tmp_path: Path) -> None:
        """
        ``hop vaults`` lists fine without any default vault (exit 0).
        """
        root = _make_vaults(tmp_path, "Personal", "Work")
        _write_config(xdg_config, f"vaults_root: {root}\ndefault_vault:\n")
        result = runner.invoke(cli.app, ["vaults"])
        assert result.exit_code == 0
        assert "Personal" in result.output
        assert "Work" in result.output

    def test_unmatched_default_with_single_vault_proceeds(
        self, xdg_config: Path, tmp_path: Path, launched_apps: list[Any]
    ) -> None:
        """
        Omitting ``default_vault`` (so the built-in ``Personal`` default
        matches nothing) still proceeds on the lone discovered vault.
        """
        root = _make_vaults(tmp_path, "Solo")
        _write_config(xdg_config, f"vaults_root: {root}\n")
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 0
        assert len(launched_apps) == 1
        assert launched_apps[0].config["default_vault"] == "Solo"


class TestDoctorResolvedConfigPath:
    """
    HOPPUS-89: ``hop doctor`` prints the resolved config file path.
    """

    def test_doctor_prints_config_file_path(
        self, xdg_config: Path, tmp_path: Path
    ) -> None:
        """
        With a config present, doctor names the exact file it loaded
        plus the resolved vaults_root and default_vault.
        """
        root = _make_vaults(tmp_path, "Personal", "Work")
        _write_config(xdg_config, f"vaults_root: {root}\ndefault_vault: Work\n")
        result = runner.invoke(cli.app, ["doctor"])
        assert result.exit_code == 0
        assert f"file: {xdg_config}" in result.output
        assert f"vaults_root: {root}" in result.output
        assert "default_vault: Work" in result.output

    def test_doctor_reports_no_config_found(self, xdg_config: Path) -> None:
        """
        With no config file, doctor says none was found (defaults in
        use) and still names where it looked.
        """
        result = runner.invoke(cli.app, ["doctor"])
        assert result.exit_code == 0
        assert "none found" in result.output
        assert str(xdg_config) in result.output
        assert "built-in defaults" in result.output
