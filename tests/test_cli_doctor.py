"""
CLI tests for ``hop doctor`` (spec §7.5, §10, HOPPUS-43).

Follows ``tests/test_cli.py``'s hermetic approach: ``hoppus.cli.load_config``
is monkeypatched, and ``hoppus.cli.run_doctor`` is wrapped to inject a fake
environment, ``which``, home, glob, and vault discovery so no test reads the
real PATH, home directory, or ``~/Notes``. Doctor is report-only: problems
become report lines, never nonzero exits.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hoppus import cli, environment
from hoppus.vault import Vault

runner = CliRunner()

SECTION_HEADERS = [
    "Optional accelerators:",
    "Editor:",
    "Config health:",
    "Vaults:",
]


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    encourage: bool,
    editor: str = "nvim",
) -> Path:
    """
    Wire hermetic fakes into the CLI's doctor pipeline.

    Creates a real temp Vaults Root (so config health reports OK), patches
    ``cli.load_config`` to point at it with the requested nudge toggle, and
    wraps ``cli.run_doctor`` to inject a fake environment where ``$EDITOR``
    is the given editor, no optional binaries exist, and no obsidian.nvim
    plugin directories exist.

    Args:
        monkeypatch: pytest's monkeypatching fixture.
        tmp_path: pytest's per-test temp directory.
        encourage: Value for ``editor_support.encourage_obsidian_nvim``.
        editor: Value for the fake ``$EDITOR``.

    Returns:
        The temp Vaults Root path.
    """
    root = tmp_path / "Notes"
    root.mkdir()
    (root / "Personal").mkdir()
    (root / "Work").mkdir()

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at the temp Vaults Root.
        """
        return {
            "vaults_root": str(root),
            "default_vault": "Personal",
            "editor_support": {"encourage_obsidian_nvim": encourage},
        }

    def fake_run_doctor(config: dict[str, Any]) -> environment.DoctorReport:
        """
        Run the real doctor assembly with hermetic injected deps.
        """
        return environment.run_doctor(
            config,
            environ={"EDITOR": editor},
            which=lambda name: None,
            home=lambda: tmp_path / "home",
            glob=lambda pattern: [],
            discover=lambda path: [
                Vault(name="Personal", path=root / "Personal"),
                Vault(name="Work", path=root / "Work"),
            ],
        )

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)
    return root


def test_doctor_prints_all_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    ``doctor`` exits 0 and prints every report section.
    """
    _install_fakes(monkeypatch, tmp_path, encourage=True)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    for header in SECTION_HEADERS:
        assert header in result.output
    for binary in ("rg", "fzf", "glow", "go-grip"):
        assert binary in result.output
    assert "not found" in result.output
    assert "nvim: yes" in result.output
    assert "OK" in result.output
    assert "Personal" in result.output
    assert "Work" in result.output
    assert "discovered: 2" in result.output


def test_doctor_shows_nudge_when_nvim_and_no_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    With ``$EDITOR=nvim``, no plugin, and encourage on, the nudge appears.
    """
    _install_fakes(monkeypatch, tmp_path, encourage=True)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert environment.OBSIDIAN_NVIM_NUDGE in result.output


def test_doctor_nudge_suppressed_by_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    With ``encourage_obsidian_nvim: false`` the nudge is absent.
    """
    _install_fakes(monkeypatch, tmp_path, encourage=False)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert environment.OBSIDIAN_NVIM_NUDGE not in result.output


def test_doctor_no_nudge_for_non_nvim_editor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A non-nvim editor gets no nudge even with encourage on.
    """
    _install_fakes(monkeypatch, tmp_path, encourage=True, editor="emacs")
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "nvim: no" in result.output
    assert environment.OBSIDIAN_NVIM_NUDGE not in result.output


def test_doctor_missing_vaults_root_is_warning_not_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A missing Vaults Root is reported as a warning line; doctor still exits 0.
    """
    missing = tmp_path / "does-not-exist"

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at a nonexistent Vaults Root.
        """
        return {
            "vaults_root": str(missing),
            "default_vault": "Personal",
            "editor_support": {"encourage_obsidian_nvim": True},
        }

    def failing_discover(path: Path) -> list[Vault]:
        """
        Simulate a missing Vaults Root.
        """
        raise FileNotFoundError(f"Vaults Root not found: {path}")

    def fake_run_doctor(config: dict[str, Any]) -> environment.DoctorReport:
        """
        Run the real doctor assembly with a failing discovery.
        """
        return environment.run_doctor(
            config,
            environ={"EDITOR": "emacs"},
            which=lambda name: None,
            home=lambda: tmp_path / "home",
            glob=lambda pattern: [],
            discover=failing_discover,
        )

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "warning:" in result.output
    assert "does not exist" in result.output
