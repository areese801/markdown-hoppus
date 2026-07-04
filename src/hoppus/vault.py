"""
Vaults Root and Vault discovery and switching (spec §5, §9.15).

A *Vaults Root* is a single directory containing one or more *vaults* as
immediate subdirectories (default ``~/Notes``). A vault may be a symlink
pointing elsewhere on disk. Each vault keeps hoppus-specific state under
``.hoppus/``; an existing ``.obsidian/`` directory is treated as read-only
foreign state and is never written to, moved, or deleted.
"""

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_VAULTS_ROOT = Path("~/Notes")

HOPPUS_STATE_DIR = ".hoppus"
OBSIDIAN_STATE_DIR = ".obsidian"


@dataclass(frozen=True)
class Vault:
    """
    A single vault: a subdirectory of the Vaults Root.

    Attributes:
        name: The vault's directory name.
        path: Absolute path to the vault directory (symlinks preserved,
            not resolved, so vaults symlinked into the Vaults Root keep
            their root-relative identity).
    """

    name: str
    path: Path

    @property
    def hoppus_dir(self) -> Path:
        """
        Return the path to this vault's ``.hoppus/`` state directory.

        The directory is not created; use :meth:`ensure_hoppus_dir` for that.
        """
        return self.path / HOPPUS_STATE_DIR

    @property
    def obsidian_dir(self) -> Path:
        """
        Return the path to Obsidian's own ``.obsidian/`` state directory.

        This is read-only foreign state — hoppus never writes to, moves,
        or deletes it.
        """
        return self.path / OBSIDIAN_STATE_DIR

    def ensure_hoppus_dir(self) -> Path:
        """
        Create the ``.hoppus/`` state directory if missing and return it.
        """
        self.hoppus_dir.mkdir(exist_ok=True)
        return self.hoppus_dir


def discover_vaults(vaults_root: Path | None = None) -> list[Vault]:
    """
    Enumerate vaults under a Vaults Root.

    A vault is any immediate subdirectory of ``vaults_root``, including
    symlinks that resolve to directories. Hidden directories (dot-prefixed)
    are skipped. Results are sorted by name for stable ordering.

    Args:
        vaults_root: Directory containing vaults. Defaults to ``~/Notes``.

    Returns:
        A list of discovered :class:`Vault` objects.

    Raises:
        FileNotFoundError: If ``vaults_root`` does not exist.
        NotADirectoryError: If ``vaults_root`` is not a directory.
    """
    root = (vaults_root or DEFAULT_VAULTS_ROOT).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"Vaults Root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Vaults Root is not a directory: {root}")

    vaults = [
        Vault(name=entry.name, path=entry)
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    ]
    return sorted(vaults, key=lambda vault: vault.name)


def resolve_default_vault(name: str | None, vaults: list[Vault]) -> Vault | None:
    """
    Resolve the default vault gracefully (HOPPUS-88).

    Selection order: the vault whose name matches ``name``; otherwise,
    when exactly one vault exists, that lone vault (so a partial config
    with no usable ``default_vault`` still proceeds sanely).

    Args:
        name: The configured ``default_vault`` value, possibly None or
            empty.
        vaults: The vaults discovered under the Vaults Root.

    Returns:
        The resolved default vault, or None when several vaults exist
        and none matches — the caller must ask the user to choose.
    """
    if name:
        for vault in vaults:
            if vault.name == name:
                return vault
    if len(vaults) == 1:
        return vaults[0]
    return None


@dataclass
class VaultManager:
    """
    Discover vaults under a Vaults Root and track the active vault.

    Vault switching (spec §9.15) selects among discovered vaults by name;
    callers re-scan/re-index the newly active vault as needed.
    """

    vaults_root: Path = DEFAULT_VAULTS_ROOT
    _vaults: list[Vault] = field(default_factory=list)
    _active: Vault | None = None

    def __post_init__(self) -> None:
        """
        Expand the Vaults Root and run an initial discovery pass.
        """
        self.vaults_root = Path(self.vaults_root).expanduser()
        self.refresh()

    @property
    def vaults(self) -> list[Vault]:
        """
        Return the most recently discovered vaults.
        """
        return list(self._vaults)

    @property
    def active(self) -> Vault | None:
        """
        Return the currently active vault, or None if none is selected.
        """
        return self._active

    def refresh(self) -> list[Vault]:
        """
        Re-discover vaults under the Vaults Root and return them.

        If the active vault no longer exists after the refresh, the active
        selection is cleared.
        """
        self._vaults = discover_vaults(self.vaults_root)
        if self._active is not None and self._active not in self._vaults:
            self._active = None
        return self.vaults

    def get(self, name: str) -> Vault:
        """
        Return the discovered vault with the given name.

        Raises:
            KeyError: If no vault with that name was discovered.
        """
        for vault in self._vaults:
            if vault.name == name:
                return vault
        raise KeyError(f"No vault named {name!r} under {self.vaults_root}")

    def switch(self, name: str) -> Vault:
        """
        Make the named vault the active vault and return it.

        Raises:
            KeyError: If no vault with that name was discovered.
        """
        self._active = self.get(name)
        return self._active
