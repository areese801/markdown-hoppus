"""
Obsidian-compatible templates and variable substitution (spec §9.10,
HOPPUS-50).

Pure, unit-testable core for the "new note from template" flow (and the
foundation for daily notes, HOPPUS-49). Templates are plain ``.md``
files living under ``templates.folder`` and are shared verbatim with
Obsidian (spec §14). Inserting a template substitutes the core-Templates
variables ``{{date}}``, ``{{time}}``, and ``{{title}}`` using the
configurable formats from ``config["templates"]``.

Token grammar (Obsidian core Templates):

- ``{{date}}`` — the current date, formatted with ``date_format``.
- ``{{time}}`` — the current time, formatted with ``time_format``.
- ``{{date:FORMAT}}`` / ``{{time:FORMAT}}`` — inline ``strftime``
  format override; the inline format wins over the config default.
- ``{{title}}`` — the new note's title.
- Any other ``{{...}}`` token (e.g. foreign Templater syntax) is left
  untouched — never errored on, never blanked.

Determinism: every function that formats dates takes an explicit
``now`` :class:`~datetime.datetime` — the *caller* supplies the clock
(the TUI passes ``datetime.now()``); nothing here calls an argless
``datetime.now()`` itself, so tests can inject a fixed instant.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from hoppus.fileops import create_note

_TOKEN_RE = re.compile(r"\{\{(date|time)(?::([^{}]+))?\}\}|\{\{title\}\}")

TEMPLATE_FOLDER_FALLBACKS = ("_templates", "templates")
"""
Well-known template folder names tried (in order) when the configured
``templates.folder`` is absent. Real Obsidian vaults commonly use
``_templates`` instead of the default ``Templates`` (HOPPUS-75 F15).
"""


def resolve_templates_folder(vault_root: Path, config: dict[str, Any]) -> Path:
    """
    Resolve the vault's templates folder, with well-known fallbacks.

    The configured ``templates.folder`` (default ``Templates``) always
    wins when it exists. When it is absent, the well-known sibling
    names in :data:`TEMPLATE_FOLDER_FALLBACKS` are tried in order —
    deterministic, never a scan for arbitrary folders (HOPPUS-75 F15).
    When nothing exists, the configured (nonexistent) path is returned
    so callers can report exactly what was looked for.

    :param vault_root: The vault root directory.
    :param config: The loaded hoppus config.
    :returns: The resolved templates folder path (may not exist).
    """
    configured = vault_root / str(
        config.get("templates", {}).get("folder", "Templates")
    )
    if configured.is_dir():
        return configured
    for name in TEMPLATE_FOLDER_FALLBACKS:
        candidate = vault_root / name
        if candidate.is_dir():
            return candidate
    return configured


def substitute_variables(
    text: str,
    *,
    now: datetime,
    title: str = "",
    date_format: str = "%Y-%m-%d",
    time_format: str = "%H:%M",
) -> str:
    """
    Replace Obsidian core-Templates tokens in template text.

    Substitutes ``{{date}}``, ``{{time}}`` (with optional inline
    ``{{date:FORMAT}}`` / ``{{time:FORMAT}}`` overrides) and
    ``{{title}}``; every other ``{{...}}`` token is left untouched.

    :param text: The template text.
    :param now: The instant to format — supplied by the caller so the
        function stays pure and deterministic (never calls
        ``datetime.now()`` itself).
    :param title: Replacement for ``{{title}}``.
    :param date_format: ``strftime`` format for ``{{date}}`` (an inline
        ``{{date:FORMAT}}`` wins over this default).
    :param time_format: ``strftime`` format for ``{{time}}`` (an inline
        ``{{time:FORMAT}}`` wins over this default).
    :returns: The text with all known tokens substituted.
    """

    def _replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        if kind is None:
            return title
        override = match.group(2)
        if kind == "date":
            return now.strftime(override if override is not None else date_format)
        return now.strftime(override if override is not None else time_format)

    return _TOKEN_RE.sub(_replace, text)


def list_templates(vault_root: Path, config: dict[str, Any]) -> list[Path]:
    """
    List the vault's template files, sorted by name.

    Looks for ``.md`` files directly under the folder resolved by
    :func:`resolve_templates_folder` — the configured
    ``templates.folder`` when it exists, otherwise the well-known
    ``_templates``/``templates`` fallbacks (HOPPUS-75 F15).

    :param vault_root: The vault root directory.
    :param config: The loaded hoppus config.
    :returns: Sorted (case-insensitively by name, stable) list of
        template paths; empty when no templates folder is present
        (never raises).
    """
    folder = resolve_templates_folder(vault_root, config)
    if not folder.is_dir():
        return []
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix == ".md"),
        key=lambda path: path.name.lower(),
    )


def render_template(
    template_path: Path,
    *,
    now: datetime,
    title: str = "",
    config: dict[str, Any],
) -> str:
    """
    Read a template file and substitute its variables.

    Uses the ``date_format``/``time_format`` from
    ``config["templates"]`` as the defaults for ``{{date}}`` and
    ``{{time}}``.

    :param template_path: The template ``.md`` file to read.
    :param now: The instant to format (caller-supplied clock).
    :param title: Replacement for ``{{title}}``.
    :param config: The loaded hoppus config.
    :returns: The rendered template text.
    :raises OSError: If the template file cannot be read — the caller
        (e.g. the TUI) should catch this and notify the user.
    """
    templates_config = config.get("templates", {})
    text = template_path.read_text(encoding="utf-8")
    return substitute_variables(
        text,
        now=now,
        title=title,
        date_format=str(templates_config.get("date_format", "%Y-%m-%d")),
        time_format=str(templates_config.get("time_format", "%H:%M")),
    )


def new_note_from_template(
    vault_root: Path,
    name: str,
    template_path: Path,
    *,
    now: datetime,
    config: dict[str, Any],
) -> Path:
    """
    Create ``<name>.md`` at the vault root, seeded from a template.

    Delegates validation and creation to
    :func:`hoppus.fileops.create_note` (create-at-root convention,
    spec §5.2 — refuses collisions loudly), then writes the rendered
    template into the new file. ``{{title}}`` defaults to the note
    name. The template is rendered *before* the note is created so a
    broken template never leaves an empty file behind.

    :param vault_root: The vault root directory.
    :param name: The new note's title (``.md`` is appended).
    :param template_path: The template to render.
    :param now: The instant to format (caller-supplied clock).
    :param config: The loaded hoppus config.
    :returns: The path of the created note.
    :raises ValueError: If the name is invalid (spec §5.2).
    :raises FileExistsError: If a note with that name already exists.
    :raises OSError: If the template cannot be read or the note cannot
        be written.
    """
    content = render_template(template_path, now=now, title=name.strip(), config=config)
    path = create_note(vault_root, name)
    path.write_text(content, encoding="utf-8")
    return path
