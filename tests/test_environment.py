"""
Unit tests for ``hoppus.environment`` (spec §7.5, HOPPUS-43).

Every dependency — the environment mapping, ``which``, the home directory,
the glob function, and vault discovery — is injected, so no test touches
the real machine, PATH, or home directory.
"""

from pathlib import Path
from typing import Any

from hoppus.environment import (
    DoctorReport,
    EditorInfo,
    check_config_health,
    check_vaults,
    detect_editor,
    detect_obsidian_nvim,
    find_binary,
    obsidian_nudge,
    run_doctor,
)
from hoppus.vault import Vault


def _config(encourage: bool = True, vaults_root: str = "/tmp") -> dict[str, Any]:
    """
    Build a minimal config dict for nudge/vault tests.
    """
    return {
        "vaults_root": vaults_root,
        "default_vault": "Personal",
        "editor_support": {"encourage_obsidian_nvim": encourage},
    }


def _nvim_editor() -> EditorInfo:
    """
    Return an EditorInfo describing nvim.
    """
    return EditorInfo(argv=("nvim",), basename="nvim", is_nvim=True)


class TestFindBinary:
    """
    ``find_binary`` with a fake ``which``.
    """

    def test_present_binary_returns_path(self) -> None:
        """
        A binary the fake ``which`` knows about resolves to its path.
        """
        assert find_binary("rg", which=lambda name: f"/usr/bin/{name}") == "/usr/bin/rg"

    def test_absent_binary_returns_none(self) -> None:
        """
        A binary missing from PATH resolves to None.
        """
        assert find_binary("fzf", which=lambda name: None) is None


class TestDetectEditor:
    """
    ``detect_editor`` against injected environments.
    """

    def test_visual_takes_precedence(self) -> None:
        """
        ``$VISUAL`` wins over ``$EDITOR`` and is shlex-split.
        """
        info = detect_editor({"VISUAL": "code --wait", "EDITOR": "nvim"})
        assert info.argv == ("code", "--wait")
        assert info.basename == "code"
        assert info.is_nvim is False

    def test_editor_nvim(self) -> None:
        """
        ``$EDITOR=nvim`` is detected as nvim.
        """
        info = detect_editor({"EDITOR": "nvim"})
        assert info.argv == ("nvim",)
        assert info.is_nvim is True

    def test_editor_nvim_with_path(self) -> None:
        """
        A full path to nvim still classifies as nvim via the basename.
        """
        info = detect_editor({"EDITOR": "/opt/homebrew/bin/nvim"})
        assert info.basename == "nvim"
        assert info.is_nvim is True

    def test_empty_environment_falls_back_to_nvim(self) -> None:
        """
        With neither variable set the fallback is ``nvim``.
        """
        info = detect_editor({})
        assert info.argv == ("nvim",)
        assert info.is_nvim is True

    def test_non_nvim_editor(self) -> None:
        """
        A non-nvim editor is not classified as nvim.
        """
        info = detect_editor({"EDITOR": "vim"})
        assert info.is_nvim is False


class TestDetectObsidianNvim:
    """
    ``detect_obsidian_nvim`` with a fake home and glob.
    """

    def test_found_under_lazy_path(self) -> None:
        """
        The lazy.nvim plugin path counts as detected.
        """

        def fake_glob(pattern: str) -> list[Path]:
            """
            Match only the lazy.nvim plugin directory.
            """
            if pattern.endswith(".local/share/nvim/lazy/obsidian.nvim"):
                return [Path(pattern)]
            return []

        assert (
            detect_obsidian_nvim(home=lambda: Path("/fake/home"), glob=fake_glob)
            is True
        )

    def test_nvim_dirs_exist_but_plugin_missing(self) -> None:
        """
        Existing nvim config dirs without the plugin yield False.
        """

        def fake_glob(pattern: str) -> list[Path]:
            """
            Match only the bare nvim config directory.
            """
            if pattern.endswith(".config/nvim"):
                return [Path(pattern)]
            return []

        assert (
            detect_obsidian_nvim(home=lambda: Path("/fake/home"), glob=fake_glob)
            is False
        )

    def test_no_nvim_dirs_at_all_is_unknown(self) -> None:
        """
        With no nvim directories the check genuinely can't tell (None).
        """
        assert (
            detect_obsidian_nvim(home=lambda: Path("/fake/home"), glob=lambda p: [])
            is None
        )


class TestObsidianNudge:
    """
    The nudge gate: encourage AND nvim AND plugin not positively detected.
    """

    def test_nudge_when_nvim_and_plugin_missing(self) -> None:
        """
        Encourage on + nvim + plugin False → nudge string.
        """
        nudge = obsidian_nudge(_config(encourage=True), _nvim_editor(), False)
        assert isinstance(nudge, str)
        assert "obsidian.nvim" in nudge

    def test_nudge_when_plugin_unknown(self) -> None:
        """
        Plugin status None (unknown) still yields the nudge.
        """
        assert obsidian_nudge(_config(encourage=True), _nvim_editor(), None) is not None

    def test_suppressed_by_config(self) -> None:
        """
        ``encourage_obsidian_nvim: false`` suppresses the nudge entirely.
        """
        assert obsidian_nudge(_config(encourage=False), _nvim_editor(), False) is None

    def test_no_nudge_for_non_nvim_editor(self) -> None:
        """
        A non-nvim editor never gets the nudge.
        """
        editor = EditorInfo(argv=("code", "--wait"), basename="code", is_nvim=False)
        assert obsidian_nudge(_config(encourage=True), editor, False) is None

    def test_no_nudge_when_plugin_detected(self) -> None:
        """
        A positively detected plugin means no nudge.
        """
        assert obsidian_nudge(_config(encourage=True), _nvim_editor(), True) is None


class TestConfigHealth:
    """
    ``check_config_health`` warnings.
    """

    def test_healthy_config(self, tmp_path: Path) -> None:
        """
        An existing Vaults Root directory yields no warnings.
        """
        assert check_config_health(_config(vaults_root=str(tmp_path))) == []

    def test_missing_vaults_root_warns(self, tmp_path: Path) -> None:
        """
        A missing Vaults Root produces a human-readable warning.
        """
        warnings = check_config_health(
            _config(vaults_root=str(tmp_path / "does-not-exist"))
        )
        assert len(warnings) == 1
        assert "does not exist" in warnings[0]

    def test_vaults_root_not_a_directory_warns(self, tmp_path: Path) -> None:
        """
        A Vaults Root pointing at a file produces a warning.
        """
        target = tmp_path / "notes.txt"
        target.write_text("not a directory\n", encoding="utf-8")
        warnings = check_config_health(_config(vaults_root=str(target)))
        assert len(warnings) == 1
        assert "not a directory" in warnings[0]


class TestCheckVaults:
    """
    ``check_vaults`` with an injected discover function.
    """

    def test_successful_discovery(self) -> None:
        """
        Discovered vaults come back with no error.
        """
        vault = Vault(name="Personal", path=Path("/fake/Notes/Personal"))
        root, vaults, error = check_vaults(
            _config(vaults_root="/fake/Notes"), discover=lambda path: [vault]
        )
        assert root == Path("/fake/Notes")
        assert vaults == [vault]
        assert error is None

    def test_missing_root_yields_error_string(self) -> None:
        """
        A FileNotFoundError from discovery becomes (root, None, message).
        """

        def failing_discover(path: Path) -> list[Vault]:
            """
            Simulate a missing Vaults Root.
            """
            raise FileNotFoundError(f"Vaults Root not found: {path}")

        root, vaults, error = check_vaults(
            _config(vaults_root="/fake/missing"), discover=failing_discover
        )
        assert root == Path("/fake/missing")
        assert vaults is None
        assert error is not None
        assert "missing" in error


class TestRunDoctor:
    """
    ``run_doctor`` assembles the full report from injected deps only.
    """

    def test_full_report_assembly(self) -> None:
        """
        Every section is populated without touching the real machine.
        """
        vault = Vault(name="Personal", path=Path("/fake/Notes/Personal"))
        report = run_doctor(
            _config(encourage=True, vaults_root="/fake/Notes"),
            environ={"EDITOR": "nvim"},
            which=lambda name: "/usr/bin/rg" if name == "rg" else None,
            home=lambda: Path("/fake/home"),
            glob=lambda pattern: [],
            discover=lambda path: [vault],
        )
        assert isinstance(report, DoctorReport)
        assert report.binaries["rg"] == "/usr/bin/rg"
        assert report.binaries["fzf"] is None
        assert report.binaries["glow"] is None
        assert report.binaries["go-grip"] is None
        assert report.editor_info.is_nvim is True
        assert report.plugin_detected is None
        assert report.nudge is not None
        assert report.vaults_root == Path("/fake/Notes")
        assert report.vault_names == ["Personal"]
        assert report.vaults_error is None
        assert any("does not exist" in warning for warning in report.config_warnings)

    def test_report_with_discovery_error_and_suppressed_nudge(self) -> None:
        """
        Discovery failure is captured, and encourage=False kills the nudge.
        """

        def failing_discover(path: Path) -> list[Vault]:
            """
            Simulate a missing Vaults Root.
            """
            raise FileNotFoundError(f"Vaults Root not found: {path}")

        report = run_doctor(
            _config(encourage=False, vaults_root="/fake/missing"),
            environ={"EDITOR": "nvim"},
            which=lambda name: None,
            home=lambda: Path("/fake/home"),
            glob=lambda pattern: [],
            discover=failing_discover,
        )
        assert report.nudge is None
        assert report.vault_names is None
        assert report.vaults_error is not None
        assert report.templates_folder is None
        assert report.templates_folder_exists is False
        assert report.template_count == 0

    def test_templates_reported_for_default_vault(self, tmp_path: Path) -> None:
        """
        The default vault's resolved templates folder (here the
        ``_templates`` fallback), its existence, and the template count
        are reported (HOPPUS-75 F15).
        """
        vault_dir = tmp_path / "Personal"
        folder = vault_dir / "_templates"
        folder.mkdir(parents=True)
        (folder / "meeting.md").write_text("m", encoding="utf-8")
        vault = Vault(name="Personal", path=vault_dir)
        report = run_doctor(
            _config(encourage=False, vaults_root=str(tmp_path)),
            environ={"EDITOR": "emacs"},
            which=lambda name: None,
            home=lambda: tmp_path / "home",
            glob=lambda pattern: [],
            discover=lambda path: [vault],
        )
        assert report.templates_folder == folder
        assert report.templates_folder_exists is True
        assert report.template_count == 1

    def test_templates_missing_folder_reported(self, tmp_path: Path) -> None:
        """
        A vault without any templates folder reports the configured
        path as absent with zero templates (HOPPUS-75 F15).
        """
        vault_dir = tmp_path / "Personal"
        vault_dir.mkdir()
        vault = Vault(name="Personal", path=vault_dir)
        report = run_doctor(
            _config(encourage=False, vaults_root=str(tmp_path)),
            environ={"EDITOR": "emacs"},
            which=lambda name: None,
            home=lambda: tmp_path / "home",
            glob=lambda pattern: [],
            discover=lambda path: [vault],
        )
        assert report.templates_folder == vault_dir / "Templates"
        assert report.templates_folder_exists is False
        assert report.template_count == 0

    def test_plugin_detection_skipped_for_non_nvim_editor(self) -> None:
        """
        A non-nvim editor short-circuits plugin detection to None.
        """
        report = run_doctor(
            _config(encourage=True, vaults_root="/fake/Notes"),
            environ={"EDITOR": "emacs"},
            which=lambda name: None,
            home=lambda: Path("/fake/home"),
            glob=lambda pattern: [Path(pattern)],
            discover=lambda path: [],
        )
        assert report.editor_info.is_nvim is False
        assert report.plugin_detected is None
        assert report.nudge is None
