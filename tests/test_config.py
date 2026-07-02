"""
Tests for hoppus.config: defaults, deep-merge precedence, unknown-key
preservation, and XDG_CONFIG_HOME handling (spec §12, §5.3, §19 D1).
"""

from pathlib import Path

import pytest

from hoppus import config as config_mod
from hoppus.config import default_config, deep_merge, global_config_path, load_config


@pytest.fixture()
def xdg_home(tmp_path, monkeypatch):
    """
    Point XDG_CONFIG_HOME at a temp dir so tests never touch ~/.config.
    """
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_defaults_when_no_files_exist(xdg_home, tmp_path):
    config = load_config(vault=tmp_path / "Vault")
    assert config == default_config()
    assert config["vaults_root"] == "~/Notes"
    assert config["link_integrity"]["prompt_on"] == "editor_return_only"
    assert config["link_integrity"]["enforce_no_dangling_wikilinks"] is True
    assert config["mcp"]["read_only"] is False
    assert config["graph"]["default_degrees"] == 2
    assert config["keymap"] == {}


def test_default_config_returns_fresh_copies():
    first = default_config()
    first["graph"]["max_nodes"] = 999
    assert default_config()["graph"]["max_nodes"] == 60


def test_global_config_overrides_defaults(xdg_home, tmp_path):
    _write_yaml(
        xdg_home / "hoppus" / "config.yaml",
        "vaults_root: ~/Vaults\ngraph:\n  max_nodes: 100\n",
    )
    config = load_config(vault=tmp_path / "Vault")
    assert config["vaults_root"] == "~/Vaults"
    assert config["graph"]["max_nodes"] == 100
    assert config["graph"]["default_degrees"] == 2


def test_per_vault_overrides_global_at_nested_keys(xdg_home, tmp_path):
    _write_yaml(
        xdg_home / "hoppus" / "config.yaml",
        "daily_notes:\n  folder: Journal\n  date_format: '%Y%m%d'\n"
        "mcp:\n  read_only: false\n",
    )
    vault = tmp_path / "Vault"
    _write_yaml(
        vault / ".hoppus" / "config.yaml",
        "daily_notes:\n  folder: DailyLog\nmcp:\n  read_only: true\n",
    )
    config = load_config(vault=vault)
    assert config["daily_notes"]["folder"] == "DailyLog"
    assert config["daily_notes"]["date_format"] == "%Y%m%d"
    assert config["daily_notes"]["template"] == "Templates/daily.md"
    assert config["mcp"]["read_only"] is True


def test_unknown_keys_are_preserved(xdg_home, tmp_path):
    _write_yaml(
        xdg_home / "hoppus" / "config.yaml",
        "future_feature:\n  enabled: true\npreview:\n  new_option: 42\n",
    )
    vault = tmp_path / "Vault"
    _write_yaml(vault / ".hoppus" / "config.yaml", "vault_only_key: hello\n")
    config = load_config(vault=vault)
    assert config["future_feature"] == {"enabled": True}
    assert config["preview"]["new_option"] == 42
    assert config["preview"]["renderer"] == "native"
    assert config["vault_only_key"] == "hello"


def test_xdg_config_home_honored(tmp_path, monkeypatch):
    xdg = tmp_path / "custom-xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert global_config_path() == xdg / "hoppus" / "config.yaml"
    _write_yaml(xdg / "hoppus" / "config.yaml", "default_vault: Work\n")
    assert load_config()["default_vault"] == "Work"


def test_xdg_fallback_to_home_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config_mod.Path, "home", staticmethod(lambda: tmp_path))
    assert global_config_path() == tmp_path / ".config" / "hoppus" / "config.yaml"


def test_empty_xdg_var_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setattr(config_mod.Path, "home", staticmethod(lambda: tmp_path))
    assert global_config_path() == tmp_path / ".config" / "hoppus" / "config.yaml"


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1, "c": 2}}
    override = {"a": {"b": 9}}
    merged = deep_merge(base, override)
    assert merged == {"a": {"b": 9, "c": 2}}
    assert base == {"a": {"b": 1, "c": 2}}
    assert override == {"a": {"b": 9}}


def test_scalar_override_replaces_dict_and_vice_versa():
    assert deep_merge({"a": {"b": 1}}, {"a": "flat"})["a"] == "flat"
    assert deep_merge({"a": "flat"}, {"a": {"b": 1}})["a"] == {"b": 1}


def test_empty_or_non_mapping_yaml_files_are_ignored(xdg_home, tmp_path):
    _write_yaml(xdg_home / "hoppus" / "config.yaml", "")
    vault = tmp_path / "Vault"
    _write_yaml(vault / ".hoppus" / "config.yaml", "- just\n- a list\n")
    assert load_config(vault=vault) == default_config()
