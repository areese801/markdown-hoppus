"""
Quick capture / inbox (spec §9.11, HOPPUS-51).

Pure, unit-testable core for the "new quick note" flow: drop a
timestamped (or explicitly titled) note into the configured **inbox**
folder (``inbox.folder``, default ``"."`` = the vault root, per the
GTD convention) without interrupting the current context.

Naming rules:

- With a ``title``, the note's stem is the (stripped) title, validated
  against the reserved-name rules (spec §5.2) via
  :func:`hoppus.naming.validate_note_name`.
- Without a title, the stem is a timestamp formatted with
  ``timestamp_format`` (default ``%Y-%m-%d %H%M%S``). The default
  deliberately avoids ``:`` (``%H%M%S``, not ``%H:%M:%S``) — the colon
  is reserved by Obsidian and by several filesystems.
- Collisions are refused, never overwritten: an existing note with the
  same name raises :class:`FileExistsError` (two captures within the
  same second can legitimately surface this).

Determinism: every function takes an explicit ``now``
:class:`~datetime.datetime` — the caller supplies the clock (the
CLI/TUI pass ``datetime.now()``), so tests can inject a fixed instant.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from hoppus.naming import validate_note_name

DEFAULT_TIMESTAMP_FORMAT = "%Y-%m-%d %H%M%S"
"""Default timestamp stem format. Colon-free — ``:`` is reserved."""


def capture_filename(
    *,
    now: datetime,
    title: str | None = None,
    timestamp_format: str = DEFAULT_TIMESTAMP_FORMAT,
) -> str:
    """
    Derive a capture note's stem (no ``.md`` extension). Pure.

    :param now: The capture instant (caller-supplied clock).
    :param title: Optional explicit title; when given and non-empty
        (after stripping), it becomes the stem verbatim.
    :param timestamp_format: ``strftime`` format for the timestamp stem
        used when no title is given. The default avoids ``:`` because
        Obsidian and some filesystems reserve it.
    :returns: The note stem — the stripped title, or the formatted
        timestamp.
    """
    if title is not None and title.strip():
        return title.strip()
    return now.strftime(timestamp_format)


def capture_note(
    vault_root: Path,
    config: dict[str, Any],
    *,
    text: str = "",
    title: str | None = None,
    now: datetime,
) -> Path:
    """
    Write a quick-capture note into the configured inbox folder.

    The inbox directory is ``vault_root / config["inbox"]["folder"]``
    (``"."`` — the default — means the vault root itself) and is
    created if missing. The filename comes from
    :func:`capture_filename`; the stem is validated against the
    reserved-name rules (spec §5.2) and an existing note is never
    overwritten.

    :param vault_root: The vault root directory.
    :param config: The loaded hoppus config (``inbox.folder`` is read,
        defaulting to ``"."``).
    :param text: The note body (may be empty).
    :param title: Optional explicit title; omitted/empty means a
        timestamped stem.
    :param now: The capture instant (caller-supplied clock).
    :returns: The path of the created note.
    :raises ValueError: If the derived stem contains reserved tokens.
    :raises FileExistsError: If the target note already exists (e.g. a
        timestamp collision from two captures within one second).
    :raises OSError: If the inbox folder or note cannot be written.
    """
    folder = str(config.get("inbox", {}).get("folder", "."))
    inbox_dir = vault_root if folder in ("", ".") else vault_root / folder
    stem = capture_filename(now=now, title=title)
    problems = validate_note_name(stem)
    if problems:
        raise ValueError(f"Invalid note name {stem!r}: reserved {' '.join(problems)}")
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / f"{stem}.md"
    if path.exists():
        raise FileExistsError(f"Note already exists: {path}")
    path.write_text(text, encoding="utf-8")
    return path
