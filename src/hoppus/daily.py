"""
Daily notes (spec §9.9, HOPPUS-49).

Pure, unit-testable core for the "open today's daily note" flow. The
daily note is Obsidian-compatible: its filename comes from
``daily_notes.date_format`` (default ``%Y-%m-%d`` → ``2026-07-03.md``),
it lives in ``daily_notes.folder``, and a brand-new note is optionally
seeded from ``daily_notes.template`` (a vault-relative path) via
:func:`hoppus.templates.render_template`.

Rules:

- **Idempotence**: if today's note already exists it is returned
  untouched — never overwritten, never duplicated. Re-invoking the
  command always lands on the same file.
- **Seeding**: a new note is seeded from the configured template when
  that template file exists (``{{title}}`` renders as the date string);
  a missing template silently yields an empty note — never an error.

Determinism: every function takes an explicit ``now``
:class:`~datetime.datetime` — the caller supplies the clock (the
CLI/TUI pass ``datetime.now()``), so tests can inject a fixed instant.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from hoppus.templates import render_template


def _daily_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return the ``daily_notes`` config section with spec §12 defaults.

    :param config: The loaded hoppus config.
    :returns: A dict with ``folder``, ``date_format``, and ``template``
        keys, falling back to the documented defaults when absent.
    """
    section = config.get("daily_notes", {})
    return {
        "folder": str(section.get("folder", "Daily")),
        "date_format": str(section.get("date_format", "%Y-%m-%d")),
        "template": str(section.get("template", "Templates/daily.md")),
    }


def daily_note_path(vault_root: Path, config: dict[str, Any], *, now: datetime) -> Path:
    """
    Compute today's daily-note path. Pure — performs no I/O.

    :param vault_root: The vault root directory.
    :param config: The loaded hoppus config.
    :param now: The instant that defines "today" (caller-supplied clock).
    :returns: ``vault_root / daily_notes.folder / <formatted date>.md``.
    """
    daily = _daily_config(config)
    return vault_root / daily["folder"] / f"{now.strftime(daily['date_format'])}.md"


def open_or_create_daily(
    vault_root: Path, config: dict[str, Any], *, now: datetime
) -> tuple[Path, bool]:
    """
    Return today's daily note, creating it on first use.

    Idempotent: when the note already exists it is returned untouched
    (``created`` is False) — never overwritten, so re-invoking always
    opens the same file. On first use, the daily-notes folder is
    created if needed and the note is seeded from
    ``daily_notes.template`` when that vault-relative template file
    exists (``{{title}}`` renders as the formatted date string); a
    missing template yields an empty note rather than an error.

    Registers nothing with the index — the caller reindexes.

    :param vault_root: The vault root directory.
    :param config: The loaded hoppus config.
    :param now: The instant that defines "today" (caller-supplied clock).
    :returns: ``(path, created)`` — the note's path and whether this
        call created it.
    :raises OSError: If the folder or note cannot be written.
    """
    path = daily_note_path(vault_root, config, now=now)
    if path.exists():
        return path, False
    template_path = vault_root / _daily_config(config)["template"]
    content = ""
    if template_path.is_file():
        content = render_template(
            template_path, now=now, title=path.stem, config=config
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path, True
