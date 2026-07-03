"""
CLI kind labels for ``hop audit`` (spec §7.4, §10, HOPPUS-42).

Hermetic like ``tests/test_cli.py``: ``hoppus.cli.load_config`` is
monkeypatched at a temp Vaults Root. The audit output labels each issue
with its kind in written link form, honors the ``link_integrity``
toggles from the loaded config, and keeps the all-clear line for a
clean vault.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hoppus import cli
from hoppus.config import default_config

runner = CliRunner()


@pytest.fixture()
def vaults_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Create a temp Vaults Root whose ``Personal`` vault has one issue of
    each kind, plus a clean ``Work`` vault.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's monkeypatching fixture.

    Returns:
        The mutable config dict served by the patched ``load_config``.
    """
    root = tmp_path / "Notes"
    root.mkdir()

    personal = root / "Personal"
    personal.mkdir()
    (personal / "Existing.md").write_text("# Real Heading\n", encoding="utf-8")
    (personal / "Note.md").write_text(
        "See [[Nope]] and [[Existing#Bar]].\n"
        "An embed ![[img.png]] and a [x](f.pdf) and a [t](missing.md).\n",
        encoding="utf-8",
    )

    work = root / "Work"
    work.mkdir()
    (work / "A.md").write_text("Links to [[B]].\n", encoding="utf-8")
    (work / "B.md").write_text("No links.\n", encoding="utf-8")

    config = default_config()
    config["vaults_root"] = str(root)
    config["default_vault"] = "Personal"

    def fake_load_config(vault: Path | None = None) -> dict[str, Any]:
        """
        Return the shared config pointing at the temp Vaults Root.
        """
        return config

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    return config


def test_audit_labels_each_issue_with_its_kind(vaults_root: dict[str, Any]) -> None:
    """
    Every issue line shows the written link form and a kind label.
    """
    result = runner.invoke(cli.app, ["audit", "Personal"])
    assert result.exit_code == 1
    assert "Note.md: [[Nope]] (unresolved wikilink)" in result.output
    assert "Note.md: [[Existing#Bar]] (broken anchor)" in result.output
    assert "Note.md: ![[img.png]] (missing attachment)" in result.output
    assert "Note.md: [x](f.pdf) (missing attachment)" in result.output
    assert "Note.md: [t](missing.md) (broken markdown link)" in result.output


def test_audit_honors_config_toggles(vaults_root: dict[str, Any]) -> None:
    """
    Toggled-off checks vanish from the CLI report; others remain.
    """
    vaults_root["link_integrity"]["warn_missing_anchor"] = False
    vaults_root["link_integrity"]["report_broken_markdown_links"] = False

    result = runner.invoke(cli.app, ["audit", "Personal"])
    assert result.exit_code == 1
    assert "broken anchor" not in result.output
    assert "broken markdown link" not in result.output
    assert "(unresolved wikilink)" in result.output
    assert "(missing attachment)" in result.output


def test_audit_clean_vault_keeps_all_clear_line(vaults_root: dict[str, Any]) -> None:
    """
    A clean vault still prints the unchanged all-clear line.
    """
    result = runner.invoke(cli.app, ["audit", "Work"])
    assert result.exit_code == 0
    assert "No unresolved links in Work." in result.output
