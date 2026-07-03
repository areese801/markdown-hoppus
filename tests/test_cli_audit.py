"""
CLI tests for ``hop audit`` (spec §10, §19 D1, HOPPUS-40).

Follows ``tests/test_cli.py``'s hermetic approach: ``hoppus.cli.load_config``
is monkeypatched at a temp Vaults Root so no real config or ``~/Notes`` is
ever read. The audit is report-only, so issues never change the exit code —
only an unknown vault name exits 1.
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
    Create a temp Vaults Root with a dirty and a clean vault.

    ``Personal`` (the configured default) has two notes carrying three
    unresolved wikilinks; ``Work`` is fully resolved.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The temp Vaults Root path.
    """
    root = tmp_path / "Notes"
    root.mkdir()

    personal = root / "Personal"
    personal.mkdir()
    (personal / "Jane Smith.md").write_text("A person.\n", encoding="utf-8")
    (personal / "Meeting Notes.md").write_text(
        "With [[Jane Smtih]] we kicked off [[Q3 Planning]].\n", encoding="utf-8"
    )
    (personal / "Zed.md").write_text("See [[Nowhere]].\n", encoding="utf-8")

    work = root / "Work"
    work.mkdir()
    (work / "A.md").write_text("Links to [[B]].\n", encoding="utf-8")
    (work / "B.md").write_text("No links.\n", encoding="utf-8")

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return a config pointing at the temp Vaults Root.
        """
        return {"vaults_root": str(root), "default_vault": "Personal"}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return root


def test_audit_lists_unresolved_links_and_counts(vaults_root: Path) -> None:
    """
    ``audit VAULT`` reports each unresolved link with the counts.
    """
    result = runner.invoke(cli.app, ["audit", "Personal"])
    assert result.exit_code == 0
    assert "Personal: 3 unresolved link(s) across 2 note(s)" in result.output
    assert "Meeting Notes.md: [[Jane Smtih]]" in result.output
    assert "Meeting Notes.md: [[Q3 Planning]]" in result.output
    assert "Zed.md: [[Nowhere]]" in result.output


def test_audit_defaults_to_configured_vault(vaults_root: Path) -> None:
    """
    ``audit`` without an argument audits the configured default vault.
    """
    result = runner.invoke(cli.app, ["audit"])
    assert result.exit_code == 0
    assert "Personal" in result.output
    assert "[[Nowhere]]" in result.output


def test_audit_clean_vault_prints_all_clear(vaults_root: Path) -> None:
    """
    A vault with no unresolved links prints the all-clear line.
    """
    result = runner.invoke(cli.app, ["audit", "Work"])
    assert result.exit_code == 0
    assert "No unresolved links in Work." in result.output


def test_audit_unknown_vault_exits_nonzero(vaults_root: Path) -> None:
    """
    ``audit`` with an unknown vault name exits 1 with an error.
    """
    result = runner.invoke(cli.app, ["audit", "Nope"])
    assert result.exit_code == 1
