"""
CLI tests for the typer app shared by ``hoppus`` / ``mark`` / ``hop``
(spec §1, §10): help output, the ``vaults``/``new``/``index``/``reindex``
subcommands against a hermetic temp Vaults Root, and console-script
wiring.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import hoppus
from hoppus import cli
from hoppus.config import global_config_path

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
    assert "No vault configured" in result.stderr
    assert "hop doctor" in result.stderr


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


def test_vaults_missing_root_exits_nonzero_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``vaults`` exits 1 and prints actionable first-run guidance naming
    the config file, both keys, and ``hop doctor`` when the Vaults Root
    does not exist (HOPPUS-74 F14).
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
    assert "No vault configured" in result.stderr
    assert str(global_config_path()) in result.stderr
    assert "vaults_root:" in result.stderr
    assert "default_vault:" in result.stderr
    assert "hop doctor" in result.stderr


def test_open_missing_root_exits_nonzero_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched_apps: list[Any]
) -> None:
    """
    ``open`` shows the same first-run guidance instead of launching the
    TUI onto a nonexistent Vaults Root (HOPPUS-74 F14).
    """
    missing = tmp_path / "does-not-exist"

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at a nonexistent Vaults Root.
        """
        return {"vaults_root": str(missing), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    result = runner.invoke(cli.app, ["open"])
    assert result.exit_code == 1
    assert launched_apps == []
    assert "No vault configured" in result.stderr
    assert "hop doctor" in result.stderr


def test_open_named_vault_launches_tui(
    vaults_root: Path, launched_apps: list[Any]
) -> None:
    """
    ``open VAULT`` runs the real TUI on the named vault.
    """
    result = runner.invoke(cli.app, ["open", "Work"])
    assert result.exit_code == 0
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


def test_help_has_no_placeholder_commands() -> None:
    """
    ``--help`` never advertises a not-yet-implemented placeholder
    (HOPPUS-99): every listed subcommand is real.
    """
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "not yet implemented" not in result.output


def test_new_creates_note_at_vault_root(vaults_root: Path) -> None:
    """
    ``new TITLE`` creates an empty note at the default vault's root and
    prints its absolute path (HOPPUS-99).
    """
    result = runner.invoke(cli.app, ["new", "Fresh Idea"])
    assert result.exit_code == 0
    created = vaults_root / "Personal" / "Fresh Idea.md"
    assert created.is_file()
    assert created.read_text(encoding="utf-8") == ""
    assert str(created) in result.output


def test_new_refuses_collision(vaults_root: Path) -> None:
    """
    ``new`` never overwrites an existing note — it errors and exits 1
    (spec §5.2).
    """
    existing = vaults_root / "Personal" / "Taken.md"
    existing.write_text("keep me\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["new", "Taken"])
    assert result.exit_code == 1
    assert "already exists" in result.stderr
    assert existing.read_text(encoding="utf-8") == "keep me\n"


def test_new_rejects_invalid_title(vaults_root: Path) -> None:
    """
    A title with reserved characters (spec §5.2) exits 1 with a clear
    message and creates nothing.
    """
    result = runner.invoke(cli.app, ["new", "Bad/Name"])
    assert result.exit_code == 1
    assert "Invalid name" in result.stderr
    assert not (vaults_root / "Personal" / "Bad").exists()


def test_new_without_title_exits_nonzero(vaults_root: Path) -> None:
    """
    ``new`` without a TITLE exits 1 with a clear message.
    """
    result = runner.invoke(cli.app, ["new"])
    assert result.exit_code == 1
    assert "requires a TITLE" in result.stderr


@pytest.mark.parametrize("command", ["index", "reindex"])
def test_index_and_reindex_print_summary(vaults_root: Path, command: str) -> None:
    """
    ``index`` and ``reindex`` build the default vault's index and print
    a note/link/tag summary, exiting 0 (HOPPUS-99).
    """
    personal = vaults_root / "Personal"
    (personal / "Alpha.md").write_text(
        "#projects\n\nSee [[Beta]] and [[Missing]].\n", encoding="utf-8"
    )
    (personal / "Beta.md").write_text("Back to [[Alpha]].\n", encoding="utf-8")
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 0
    assert "Personal: indexed 2 note(s), 3 link(s), 1 tag(s)" in result.output


@pytest.mark.parametrize("command", ["index", "reindex"])
def test_index_and_reindex_accept_vault_argument(
    vaults_root: Path, command: str
) -> None:
    """
    An explicit vault name selects that vault; an unknown name exits 1.
    """
    (vaults_root / "Work" / "Memo.md").write_text("Hi\n", encoding="utf-8")
    result = runner.invoke(cli.app, [command, "Work"])
    assert result.exit_code == 0
    assert "Work: indexed 1 note(s), 0 link(s), 0 tag(s)" in result.output

    result = runner.invoke(cli.app, [command, "Nope"])
    assert result.exit_code == 1
    assert "No vault named" in result.stderr


def test_package_main_delegates_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ``hoppus.main`` (the console-script target) delegates to the typer app.
    """
    calls: list[bool] = []
    monkeypatch.setattr(hoppus, "_cli_main", lambda: calls.append(True))
    hoppus.main()
    assert calls == [True]
