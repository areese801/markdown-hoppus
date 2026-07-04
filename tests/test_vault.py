"""
Tests for Vaults Root / Vault discovery and switching (spec §5, §9.15).
"""

from pathlib import Path

import pytest

from hoppus.vault import Vault, VaultManager, discover_vaults, resolve_default_vault


def _snapshot(directory: Path) -> dict[str, bytes]:
    """
    Return a mapping of relative file paths to contents under a directory.
    """
    return {
        str(child.relative_to(directory)): child.read_bytes()
        for child in sorted(directory.rglob("*"))
        if child.is_file()
    }


@pytest.fixture
def vaults_root(tmp_path: Path) -> Path:
    """
    Build a temp Vaults Root with two real vaults and one symlinked vault.

    The ``Personal`` vault contains a pre-existing ``.obsidian/`` directory
    that must never be modified. A dotted directory and a stray file are
    included to verify they are not discovered as vaults.
    """
    root = tmp_path / "Notes"
    root.mkdir()

    personal = root / "Personal"
    personal.mkdir()
    (personal / ".obsidian").mkdir()
    (personal / ".obsidian" / "app.json").write_text('{"theme": "moonstone"}')
    (personal / "Inbox note.md").write_text("# Inbox\n")

    work = root / "Work"
    work.mkdir()

    external = tmp_path / "elsewhere" / "Shared"
    external.mkdir(parents=True)
    (root / "Shared").symlink_to(external)

    (root / ".hidden").mkdir()
    (root / "stray.txt").write_text("not a vault\n")

    return root


class TestDiscoverVaults:
    """
    Vault enumeration under a Vaults Root.
    """

    def test_finds_all_vaults_including_symlink(self, vaults_root: Path) -> None:
        """
        Discovery finds real directories and symlinked directories.
        """
        vaults = discover_vaults(vaults_root)
        assert [vault.name for vault in vaults] == ["Personal", "Shared", "Work"]

    def test_symlinked_vault_keeps_root_relative_path(self, vaults_root: Path) -> None:
        """
        A symlinked vault's path stays under the Vaults Root (unresolved).
        """
        shared = discover_vaults(vaults_root)[1]
        assert shared.name == "Shared"
        assert shared.path == vaults_root / "Shared"
        assert shared.path.is_symlink()

    def test_skips_hidden_dirs_and_files(self, vaults_root: Path) -> None:
        """
        Dot-directories and plain files are not vaults.
        """
        names = {vault.name for vault in discover_vaults(vaults_root)}
        assert ".hidden" not in names
        assert "stray.txt" not in names

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        """
        A nonexistent Vaults Root raises FileNotFoundError.
        """
        with pytest.raises(FileNotFoundError):
            discover_vaults(tmp_path / "nope")

    def test_non_directory_root_raises(self, tmp_path: Path) -> None:
        """
        A file passed as the Vaults Root raises NotADirectoryError.
        """
        file_path = tmp_path / "file.txt"
        file_path.write_text("hi\n")
        with pytest.raises(NotADirectoryError):
            discover_vaults(file_path)


class TestVault:
    """
    Vault object surface: name, path, and state-directory helpers.
    """

    def test_hoppus_dir_not_created_by_discovery(self, vaults_root: Path) -> None:
        """
        Discovery alone must not create ``.hoppus/`` directories.
        """
        for vault in discover_vaults(vaults_root):
            assert not vault.hoppus_dir.exists()

    def test_ensure_hoppus_dir_creates_on_demand(self, vaults_root: Path) -> None:
        """
        The create-on-demand helper makes ``.hoppus/`` and is idempotent.
        """
        vault = discover_vaults(vaults_root)[0]
        created = vault.ensure_hoppus_dir()
        assert created == vault.path / ".hoppus"
        assert created.is_dir()
        assert vault.ensure_hoppus_dir() == created

    def test_obsidian_dir_path(self, vaults_root: Path) -> None:
        """
        The ``.obsidian/`` path is exposed for read-only use.
        """
        vault = Vault(name="Personal", path=vaults_root / "Personal")
        assert vault.obsidian_dir == vaults_root / "Personal" / ".obsidian"


class TestObsidianUntouched:
    """
    ``.obsidian/`` is read-only foreign state (spec §5.2).
    """

    def test_discovery_and_switching_never_touch_obsidian(
        self, vaults_root: Path
    ) -> None:
        """
        Discovery, switching, and ensure_hoppus_dir leave ``.obsidian/``
        byte-identical.
        """
        obsidian = vaults_root / "Personal" / ".obsidian"
        before = _snapshot(obsidian)

        manager = VaultManager(vaults_root=vaults_root)
        manager.switch("Personal")
        manager.switch("Work")
        manager.switch("Personal")
        manager.refresh()
        manager.get("Personal").ensure_hoppus_dir()

        assert obsidian.is_dir()
        assert _snapshot(obsidian) == before


class TestVaultManager:
    """
    Active-vault selection and switching (spec §9.15).
    """

    def test_initial_discovery_and_no_active(self, vaults_root: Path) -> None:
        """
        The manager discovers vaults on init with no active selection.
        """
        manager = VaultManager(vaults_root=vaults_root)
        assert [vault.name for vault in manager.vaults] == [
            "Personal",
            "Shared",
            "Work",
        ]
        assert manager.active is None

    def test_switch_sets_active(self, vaults_root: Path) -> None:
        """
        Switching selects the named vault, including a symlinked one.
        """
        manager = VaultManager(vaults_root=vaults_root)
        assert manager.switch("Work").name == "Work"
        assert manager.active is not None
        assert manager.active.name == "Work"
        assert manager.switch("Shared").path == vaults_root / "Shared"

    def test_switch_unknown_vault_raises(self, vaults_root: Path) -> None:
        """
        Switching to an unknown name raises KeyError and keeps state.
        """
        manager = VaultManager(vaults_root=vaults_root)
        manager.switch("Personal")
        with pytest.raises(KeyError):
            manager.switch("Nonexistent")
        assert manager.active is not None
        assert manager.active.name == "Personal"

    def test_refresh_picks_up_new_vault(self, vaults_root: Path) -> None:
        """
        Refresh discovers vaults added after initialization.
        """
        manager = VaultManager(vaults_root=vaults_root)
        (vaults_root / "Archive").mkdir()
        manager.refresh()
        assert "Archive" in {vault.name for vault in manager.vaults}

    def test_refresh_clears_active_if_vault_removed(self, vaults_root: Path) -> None:
        """
        A removed active vault is deselected on refresh.
        """
        manager = VaultManager(vaults_root=vaults_root)
        manager.switch("Work")
        (vaults_root / "Work").rmdir()
        manager.refresh()
        assert manager.active is None


class TestResolveDefaultVault:
    """
    Graceful default-vault resolution (HOPPUS-88).
    """

    def test_name_match_wins(self) -> None:
        """
        A configured name matching a vault resolves to that vault.
        """
        vaults = [
            Vault(name="Personal", path=Path("/n/Personal")),
            Vault(name="Work", path=Path("/n/Work")),
        ]
        resolved = resolve_default_vault("Work", vaults)
        assert resolved is not None
        assert resolved.name == "Work"

    def test_no_name_with_single_vault_uses_it(self) -> None:
        """
        With no ``default_vault`` and exactly one vault, that vault wins.
        """
        vault = Vault(name="Only", path=Path("/n/Only"))
        assert resolve_default_vault(None, [vault]) == vault
        assert resolve_default_vault("", [vault]) == vault

    def test_unmatched_name_with_single_vault_uses_it(self) -> None:
        """
        A name matching nothing still falls back to the lone vault.
        """
        vault = Vault(name="Only", path=Path("/n/Only"))
        assert resolve_default_vault("Personal", [vault]) == vault

    def test_no_name_with_several_vaults_is_unresolved(self) -> None:
        """
        Several vaults and no usable name → None; the caller must ask.
        """
        vaults = [
            Vault(name="Personal", path=Path("/n/Personal")),
            Vault(name="Work", path=Path("/n/Work")),
        ]
        assert resolve_default_vault(None, vaults) is None

    def test_no_vaults_is_unresolved(self) -> None:
        """
        With nothing discovered there is nothing to resolve.
        """
        assert resolve_default_vault(None, []) is None
        assert resolve_default_vault("Personal", []) is None
