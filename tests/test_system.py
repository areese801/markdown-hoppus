"""
Hermetic tests for the system opener (spec §9.5, HOPPUS-55).

No real subprocesses or opener binaries are touched: ``which`` and
``run`` are injected fakes throughout.
"""

from pathlib import Path

import pytest

from hoppus.system import SystemOpenError, open_in_system_app, system_open_command

NOTE = Path("/vault/Note.md")


class TestSystemOpenCommand:
    """
    Argv construction for each platform family.
    """

    def test_darwin_uses_open(self) -> None:
        """
        macOS uses the ``open`` launcher.
        """
        assert system_open_command(NOTE, platform="darwin") == ["open", str(NOTE)]

    def test_win32_uses_cmd_start_with_empty_title(self) -> None:
        """
        Windows routes through ``cmd /c start`` with the empty title arg.
        """
        argv = system_open_command(NOTE, platform="win32")
        assert argv == ["cmd", "/c", "start", "", str(NOTE)]

    def test_cygwin_matches_windows_form(self) -> None:
        """
        Cygwin is treated as Windows.
        """
        argv = system_open_command(NOTE, platform="cygwin")
        assert argv == ["cmd", "/c", "start", "", str(NOTE)]

    def test_linux_uses_xdg_open(self) -> None:
        """
        Linux (and other platforms) fall back to ``xdg-open``.
        """
        assert system_open_command(NOTE, platform="linux") == [
            "xdg-open",
            str(NOTE),
        ]


class TestOpenInSystemApp:
    """
    Side-effecting wrapper with injected ``which``/``run``.
    """

    def test_runs_opener_when_launcher_exists(self) -> None:
        """
        With a resolvable launcher, ``run`` gets the platform argv.
        """
        calls: list[list[str]] = []
        open_in_system_app(
            NOTE,
            platform="linux",
            which=lambda name: f"/usr/bin/{name}",
            run=lambda argv: calls.append(argv),
        )
        assert calls == [["xdg-open", str(NOTE)]]

    def test_missing_launcher_raises_without_running(self) -> None:
        """
        A missing launcher raises SystemOpenError and never calls ``run``.
        """
        calls: list[list[str]] = []
        with pytest.raises(SystemOpenError, match="xdg-open"):
            open_in_system_app(
                NOTE,
                platform="linux",
                which=lambda name: None,
                run=lambda argv: calls.append(argv),
            )
        assert calls == []

    def test_windows_skips_which_check(self) -> None:
        """
        On Windows ``start`` is a cmd builtin, so ``which`` is not consulted.
        """
        calls: list[list[str]] = []
        open_in_system_app(
            NOTE,
            platform="win32",
            which=lambda name: None,
            run=lambda argv: calls.append(argv),
        )
        assert calls == [["cmd", "/c", "start", "", str(NOTE)]]

    def test_run_failure_wrapped_in_system_open_error(self) -> None:
        """
        A launcher failure surfaces as SystemOpenError, not a raw crash.
        """

        def boom(argv: list[str]) -> None:
            raise OSError("launch failed")

        with pytest.raises(SystemOpenError, match="launch failed"):
            open_in_system_app(
                NOTE,
                platform="darwin",
                which=lambda name: f"/usr/bin/{name}",
                run=boom,
            )
