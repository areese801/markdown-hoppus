"""
Open a note in the OS default application via the platform opener (spec §9.5).

The argv construction is a pure function (:func:`system_open_command`) keyed
off a platform string, so it is trivially testable; the subprocess side
effect lives in :func:`open_in_system_app`, which takes injectable ``which``
and ``run`` callables for hermetic tests. Per the zero-binary guarantee
(spec §3), the system opener is best-effort: a missing launcher raises
:class:`SystemOpenError` with a helpful message rather than crashing.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable


class SystemOpenError(Exception):
    """
    Raised when a note cannot be handed off to the system opener.

    Covers both a missing launcher binary (e.g., ``xdg-open`` not
    installed) and a failed launch. The message is user-facing so the
    TUI can surface it via a notification.
    """


def system_open_command(path: Path | str, *, platform: str) -> list[str]:
    """
    Build the argv that opens ``path`` with the OS default application.

    Pure function — it never inspects the environment or checks that the
    launcher exists; the caller decides how to handle a missing binary.

    Platform mapping (``platform`` is a ``sys.platform``-style string):

    - ``darwin`` (macOS) → ``open <path>``
    - ``win32`` / ``cygwin`` (Windows) → ``cmd /c start "" <path>``.
      The empty string is the window-title argument: ``start`` treats its
      first quoted argument as a title, so without it a quoted path would
      be consumed as the title and nothing would open.
    - anything else (Linux, BSDs) → ``xdg-open <path>``

    :param path: The file to open.
    :param platform: A ``sys.platform`` value (e.g., ``"darwin"``).
    :return: The argv list for the platform's opener.
    """
    if platform == "darwin":
        return ["open", str(path)]
    if platform in ("win32", "cygwin"):
        return ["cmd", "/c", "start", "", str(path)]
    return ["xdg-open", str(path)]


def open_in_system_app(
    path: Path | str,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., object] = subprocess.run,
) -> None:
    """
    Open ``path`` in the OS default application, fire-and-forget.

    Builds the opener argv via :func:`system_open_command`, verifies the
    launcher binary exists (skipped on Windows, where ``start`` is a
    ``cmd`` builtin and existence is assumed), then launches it without
    waiting for or capturing output.

    :param path: The file to open.
    :param platform: Platform override; defaults to ``sys.platform``.
    :param which: Binary lookup, injectable for tests.
    :param run: Subprocess launcher, injectable for tests.
    :raises SystemOpenError: If the launcher binary is missing or the
        launch itself fails.
    """
    resolved_platform = sys.platform if platform is None else platform
    argv = system_open_command(path, platform=resolved_platform)
    is_windows = resolved_platform in ("win32", "cygwin")
    if not is_windows and which(argv[0]) is None:
        raise SystemOpenError(
            f"System opener '{argv[0]}' not found; "
            "install it to open notes in the system default app"
        )
    try:
        run(argv)
    except Exception as error:
        raise SystemOpenError(f"Failed to open in system app: {error}") from error
