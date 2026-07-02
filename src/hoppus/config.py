"""
Configuration loading: global ``~/.config/hoppus/config.yaml`` deep-merged
with per-vault ``<vault>/.hoppus/config.yaml`` overrides (spec §12).

The global config path honors ``$XDG_CONFIG_HOME``, falling back to
``~/.config``. Missing keys fall back to the documented defaults; unknown
keys are preserved for forward compatibility.
"""

import copy
import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

GLOBAL_CONFIG_RELPATH = Path("hoppus") / "config.yaml"
VAULT_CONFIG_RELPATH = Path(".hoppus") / "config.yaml"


def default_config() -> dict[str, Any]:
    """
    Return a fresh copy of the full default configuration (spec §12).

    Returns:
        A dict containing every documented key with its default value,
        including the §19 D1 addendum key ``link_integrity.prompt_on``.
    """
    return copy.deepcopy(
        {
            "vaults_root": "~/Notes",
            "default_vault": "Personal",
            "editor": None,
            "preview": {
                "renderer": "native",
                "browser_renderer": "builtin",
                "raw_fallback": True,
            },
            "search": {
                "content_backend": "auto",
                "fuzzy": True,
            },
            "graph": {
                "max_degrees": 5,
                "default_degrees": 2,
                "max_nodes": 60,
                "exclude_hub_threshold": None,
            },
            "link_integrity": {
                "enforce_no_dangling_wikilinks": True,
                "fuzzy_did_you_mean": True,
                "warn_missing_anchor": True,
                "report_missing_attachments": True,
                "report_broken_markdown_links": True,
                "new_note_location": "vault_root",
                "new_note_template": None,
                "related_heading": "## Related",
                "prompt_on": "editor_return_only",
            },
            "editor_support": {
                "encourage_obsidian_nvim": True,
            },
            "clipboard": {
                "backend": "pyperclip",
            },
            "daily_notes": {
                "folder": "Daily",
                "date_format": "%Y-%m-%d",
                "template": "Templates/daily.md",
            },
            "templates": {
                "folder": "Templates",
                "date_format": "%Y-%m-%d",
                "time_format": "%H:%M",
            },
            "inbox": {
                "folder": ".",
            },
            "files": {
                "prompt_before_link_update": True,
                "attachments_folder": "attachments",
            },
            "keymap": {},
            "mcp": {
                "read_only": False,
            },
        }
    )


def global_config_path() -> Path:
    """
    Return the global config file path, honoring ``$XDG_CONFIG_HOME``.

    Returns:
        ``$XDG_CONFIG_HOME/hoppus/config.yaml`` when the variable is set
        and non-empty, otherwise ``~/.config/hoppus/config.yaml``.
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / GLOBAL_CONFIG_RELPATH


def vault_config_path(vault: Path) -> Path:
    """
    Return the per-vault config override path for a vault directory.

    Args:
        vault: Path to the vault directory.

    Returns:
        ``<vault>/.hoppus/config.yaml``.
    """
    return Path(vault) / VAULT_CONFIG_RELPATH


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge ``override`` into ``base``, returning a new dict.

    Nested dicts are merged key-by-key; any other value type in
    ``override`` (including lists and None) replaces the base value
    wholesale. Neither input is mutated.

    Args:
        base: The lower-precedence mapping.
        override: The higher-precedence mapping.

    Returns:
        A new dict with ``override`` values winning at every level.
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """
    Load a YAML mapping from ``path`` via ruamel.yaml.

    Args:
        path: File to read.

    Returns:
        The parsed mapping as a plain dict; an empty dict if the file
        does not exist, is empty, or does not contain a mapping.
    """
    if not path.is_file():
        return {}
    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return _to_plain(data)


def _to_plain(value: Any) -> Any:
    """
    Convert ruamel container types to plain dicts/lists recursively.

    Args:
        value: Any parsed YAML value.

    Returns:
        The same structure using builtin dict/list types.
    """
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def load_config(vault: Path | None = None) -> dict[str, Any]:
    """
    Load the merged hoppus configuration.

    Precedence (lowest to highest): documented defaults, the global
    config at ``~/.config/hoppus/config.yaml`` (XDG-aware), then the
    optional per-vault override at ``<vault>/.hoppus/config.yaml``.
    Unknown keys from either file are preserved.

    Args:
        vault: Optional vault directory whose override should apply.

    Returns:
        The fully merged configuration dict.
    """
    config = default_config()
    config = deep_merge(config, _load_yaml_file(global_config_path()))
    if vault is not None:
        config = deep_merge(config, _load_yaml_file(vault_config_path(vault)))
    return config
