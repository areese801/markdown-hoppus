"""
Environment health checks for ``hop doctor`` (spec §7.5).

``hop doctor`` reports **environment** health — distinct from ``hop audit``,
which reports vault **content** health. The sections covered here:

- Presence of the optional accelerator binaries ``rg``, ``fzf``, ``glow``,
  and ``go-grip`` (the app must work with zero of them installed; they only
  enhance).
- The resolved ``$VISUAL``/``$EDITOR`` command and a best-effort check for
  whether ``obsidian.nvim`` appears installed, plus the gentle, dismissible
  nudge encouraging it when the editor is nvim and the plugin is absent.
  The nudge is independently toggleable via
  ``editor_support.encourage_obsidian_nvim`` (spec §7.6, §12).
- Config health (missing folders referenced by config) and Vaults Root
  reachability / vault discovery.
- The default vault's resolved templates folder (configured or well-known
  ``_templates``/``templates`` fallback), whether it exists, and how many
  templates were found — so a folder-name mismatch is visible instead of
  silently yielding zero templates (HOPPUS-75 F15).

All detection logic is pure-ish: the environment mapping, ``which``, the
home directory, and vault discovery are injectable so the checks unit-test
without touching the real machine.
"""

import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoppus.integrity import resolve_editor_command
from hoppus.templates import list_templates, resolve_templates_folder
from hoppus.vault import Vault, discover_vaults, resolve_default_vault

OPTIONAL_BINARIES = ("rg", "fzf", "glow", "go-grip")

NVIM_BASENAMES = frozenset({"nvim", "neovim"})

OBSIDIAN_NVIM_NUDGE = (
    "Tip: $EDITOR is nvim but obsidian.nvim was not detected. Installing it "
    "provides in-editor [[ completion for note links, markdown links, and "
    "tags. Disable this tip with editor_support.encourage_obsidian_nvim: "
    "false."
)

_PLUGIN_GLOBS = (
    ".local/share/nvim/lazy/obsidian.nvim",
    ".local/share/nvim/site/pack/*/start/obsidian.nvim",
    ".config/nvim/pack/*/start/obsidian.nvim",
)

_NVIM_CONFIG_DIRS = (
    ".config/nvim",
    ".local/share/nvim",
)


@dataclass(frozen=True)
class EditorInfo:
    """
    The resolved editor command and whether it is nvim.

    Attributes:
        argv: The editor argv prefix resolved from ``$VISUAL``/``$EDITOR``
            (fallback ``["nvim"]``), shlex-split.
        basename: The basename of the editor executable (argv[0]).
        is_nvim: True when the basename is nvim/neovim.
    """

    argv: tuple[str, ...]
    basename: str
    is_nvim: bool


def find_binary(
    name: str, *, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    """
    Locate an optional accelerator binary on the PATH.

    Args:
        name: Binary name, e.g. ``rg`` or ``fzf``.
        which: Injectable PATH lookup (defaults to ``shutil.which``).

    Returns:
        The resolved path, or None when the binary is not found.
    """
    return which(name)


def detect_editor(environ: Mapping[str, str]) -> EditorInfo:
    """
    Detect the configured editor from the environment (spec §7.5, §9.6).

    Uses ``integrity.resolve_editor_command`` (prefer ``$VISUAL``, then
    ``$EDITOR``, fallback ``nvim``) and classifies whether it is nvim.

    Args:
        environ: The process environment (e.g. ``os.environ``).

    Returns:
        An :class:`EditorInfo` describing the resolved editor.
    """
    argv = resolve_editor_command(environ)
    basename = Path(argv[0]).name
    return EditorInfo(
        argv=tuple(argv),
        basename=basename,
        is_nvim=basename in NVIM_BASENAMES,
    )


def _default_glob(pattern: str) -> list[Path]:
    """
    Glob an absolute pattern against the filesystem.

    Args:
        pattern: An absolute glob pattern.

    Returns:
        Matching paths (possibly empty).
    """
    anchor = Path(pattern).anchor or "/"
    return list(Path(anchor).glob(str(Path(pattern).relative_to(anchor))))


def detect_obsidian_nvim(
    *,
    home: Callable[[], Path] = Path.home,
    glob: Callable[[str], Iterable[Path]] = _default_glob,
) -> bool | None:
    """
    Best-effort check for whether ``obsidian.nvim`` appears installed.

    Looks under common plugin manager locations relative to the injected
    home directory: the lazy.nvim path
    (``~/.local/share/nvim/lazy/obsidian.nvim``) and native package paths
    (``.../pack/*/start/obsidian.nvim`` under both the nvim data and config
    directories).

    Args:
        home: Injectable callable returning the home directory.
        glob: Injectable callable expanding an absolute glob pattern to
            matching paths.

    Returns:
        True when the plugin is found; False when nvim config/data dirs
        exist but the plugin is not found; None when no nvim directories
        exist at all (genuinely can't tell).
    """
    base = home()
    for pattern in _PLUGIN_GLOBS:
        if any(glob(str(base / pattern))):
            return True
    if any(glob(str(base / directory)) for directory in _NVIM_CONFIG_DIRS):
        return False
    return None


def obsidian_nudge(
    config: Mapping[str, Any],
    editor_info: EditorInfo,
    plugin_detected: bool | None,
) -> str | None:
    """
    Return the gentle obsidian.nvim nudge when it applies (spec §7.5).

    The nudge fires only when all of the following hold:
    ``editor_support.encourage_obsidian_nvim`` is enabled in config, the
    editor is nvim, and the plugin was not positively detected (False or
    None both count as "not detected" — the check is best-effort).

    Args:
        config: The merged configuration (spec §12).
        editor_info: The detected editor.
        plugin_detected: Result of :func:`detect_obsidian_nvim`.

    Returns:
        The nudge string, or None when suppressed or inapplicable.
    """
    encourage = config.get("editor_support", {}).get("encourage_obsidian_nvim", True)
    if encourage and editor_info.is_nvim and plugin_detected is not True:
        return OBSIDIAN_NVIM_NUDGE
    return None


def check_config_health(config: Mapping[str, Any]) -> list[str]:
    """
    Return best-effort, human-readable config warnings (spec §7.5).

    Currently checks that the Vaults Root exists and is a directory.
    Deliberately light — deeper folder checks (daily notes, templates)
    are per-vault and belong to vault-content tooling.

    Args:
        config: The merged configuration (spec §12).

    Returns:
        Warning strings; an empty list means the config looks healthy.
    """
    warnings: list[str] = []
    vaults_root = Path(str(config.get("vaults_root", ""))).expanduser()
    if not vaults_root.exists():
        warnings.append(f"Vaults Root does not exist: {vaults_root}")
    elif not vaults_root.is_dir():
        warnings.append(f"Vaults Root is not a directory: {vaults_root}")
    return warnings


def check_vaults(
    config: Mapping[str, Any],
    *,
    discover: Callable[[Path], list[Vault]] = discover_vaults,
) -> tuple[Path, list[Vault] | None, str | None]:
    """
    Check Vaults Root reachability and discover vaults (spec §7.5).

    Args:
        config: The merged configuration (spec §12).
        discover: Injectable vault discovery (defaults to
            :func:`hoppus.vault.discover_vaults`).

    Returns:
        A tuple of (vaults_root, vaults-or-None, error-or-None). On a
        missing or non-directory Vaults Root the vaults slot is None and
        the error slot carries the message.
    """
    vaults_root = Path(str(config.get("vaults_root", ""))).expanduser()
    try:
        return vaults_root, discover(vaults_root), None
    except (FileNotFoundError, NotADirectoryError) as error:
        return vaults_root, None, str(error)


@dataclass
class DoctorReport:
    """
    Aggregated ``hop doctor`` findings (spec §7.5).

    Attributes:
        binaries: Optional accelerator name -> resolved path or None.
        editor_info: The detected editor.
        plugin_detected: obsidian.nvim status (True/False/None).
        nudge: The obsidian.nvim nudge string, or None when suppressed.
        config_warnings: Best-effort config health warnings, including
            a config-file parse error when the loader reported one
            (HOPPUS-87).
        config_path: The resolved global config file that was loaded,
            or None when no file was found and the built-in defaults
            are in use (HOPPUS-89).
        default_vault: The resolved default vault name — the configured
            value, or the lone discovered vault when the configured
            value is absent or matches nothing (HOPPUS-88) — or None
            when unresolvable.
        vaults_root: The configured Vaults Root.
        vault_names: Discovered vault names, or None on discovery error.
        vaults_error: Discovery error message, or None.
        templates_folder: The default vault's resolved templates folder
            (configured or well-known fallback, HOPPUS-75 F15), or None
            when the default vault was not discovered.
        templates_folder_exists: Whether that folder exists.
        template_count: Number of templates found in it.
    """

    binaries: dict[str, str | None]
    editor_info: EditorInfo
    plugin_detected: bool | None
    nudge: str | None
    config_warnings: list[str] = field(default_factory=list)
    config_path: Path | None = None
    default_vault: str | None = None
    vaults_root: Path = Path()
    vault_names: list[str] | None = None
    vaults_error: str | None = None
    templates_folder: Path | None = None
    templates_folder_exists: bool = False
    template_count: int = 0


def run_doctor(
    config: Mapping[str, Any],
    *,
    config_path: Path | None = None,
    config_error: str | None = None,
    environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    home: Callable[[], Path] = Path.home,
    glob: Callable[[str], Iterable[Path]] = _default_glob,
    discover: Callable[[Path], list[Vault]] = discover_vaults,
) -> DoctorReport:
    """
    Assemble the full environment health report from injected deps.

    Args:
        config: The merged configuration (spec §12).
        config_path: The resolved global config file that was loaded,
            or None when defaults are in use (HOPPUS-89).
        config_error: A config-file parse error from the loader, or
            None. Reported as the first config health warning so a
            malformed config marks the report unhealthy instead of
            crashing (HOPPUS-87).
        environ: The process environment.
        which: PATH lookup for optional binaries.
        home: Home directory provider for plugin detection.
        glob: Glob expansion for plugin detection.
        discover: Vault discovery.

    Returns:
        A fully populated :class:`DoctorReport`.
    """
    binaries = {name: find_binary(name, which=which) for name in OPTIONAL_BINARIES}
    editor_info = detect_editor(environ)
    plugin_detected = (
        detect_obsidian_nvim(home=home, glob=glob) if editor_info.is_nvim else None
    )
    nudge = obsidian_nudge(config, editor_info, plugin_detected)
    vaults_root, vaults, vaults_error = check_vaults(config, discover=discover)

    templates_folder: Path | None = None
    templates_folder_exists = False
    template_count = 0
    default_name = config.get("default_vault")
    default_vault = resolve_default_vault(
        str(default_name) if default_name else None, vaults or []
    )
    if default_vault is not None:
        config_dict = dict(config)
        templates_folder = resolve_templates_folder(default_vault.path, config_dict)
        templates_folder_exists = templates_folder.is_dir()
        template_count = len(list_templates(default_vault.path, config_dict))

    return DoctorReport(
        binaries=binaries,
        editor_info=editor_info,
        plugin_detected=plugin_detected,
        nudge=nudge,
        config_warnings=(
            ([config_error] if config_error else []) + check_config_health(config)
        ),
        config_path=config_path,
        default_vault=default_vault.name if default_vault is not None else None,
        vaults_root=vaults_root,
        vault_names=[vault.name for vault in vaults] if vaults is not None else None,
        vaults_error=vaults_error,
        templates_folder=templates_folder,
        templates_folder_exists=templates_folder_exists,
        template_count=template_count,
    )
