"""
Pure unit tests for :mod:`hoppus.templates` (spec §9.10, HOPPUS-50):
variable substitution with a fixed injected clock, template listing,
rendering, and template-seeded note creation on a temp vault.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from hoppus.config import default_config
from hoppus.templates import (
    list_templates,
    new_note_from_template,
    render_template,
    resolve_templates_folder,
    substitute_variables,
)

_NOW = datetime(2026, 7, 2, 13, 45)


def make_config() -> dict[str, Any]:
    """
    Return a default config (templates folder ``Templates``).
    """
    return default_config()


# -- substitute_variables ------------------------------------------------------


def test_substitutes_date_time_and_title() -> None:
    """
    ``{{date}}``, ``{{time}}``, and ``{{title}}`` fill from the
    injected clock and title.
    """
    text = "# {{title}}\nCreated {{date}} at {{time}}\n"
    result = substitute_variables(text, now=_NOW, title="My Note")
    assert result == "# My Note\nCreated 2026-07-02 at 13:45\n"


def test_inline_date_format_override_wins() -> None:
    """
    ``{{date:%Y}}`` uses the inline format, not the default.
    """
    assert substitute_variables("{{date:%Y}}", now=_NOW) == "2026"


def test_inline_time_format_override_wins() -> None:
    """
    ``{{time:%H}}`` uses the inline format, not the default.
    """
    assert substitute_variables("{{time:%H}}", now=_NOW) == "13"


def test_configured_formats_apply() -> None:
    """
    The ``date_format``/``time_format`` arguments drive the plain
    ``{{date}}``/``{{time}}`` tokens.
    """
    result = substitute_variables(
        "{{date}} {{time}}",
        now=_NOW,
        date_format="%d.%m.%Y",
        time_format="%H%M",
    )
    assert result == "02.07.2026 1345"


def test_unknown_tokens_left_untouched() -> None:
    """
    Foreign ``{{...}}`` tokens (e.g. Templater syntax) survive verbatim.
    """
    text = "{{foo}} {{tp.date.now}} {{title:weird}}"
    assert substitute_variables(text, now=_NOW, title="X") == text


def test_no_tokens_returns_identical_text() -> None:
    """
    A template with no tokens comes back byte-identical.
    """
    text = "# Plain\n\nJust prose — no variables here.\n"
    assert substitute_variables(text, now=_NOW, title="X") == text


def test_title_defaults_to_empty_string() -> None:
    """
    Without an explicit title, ``{{title}}`` becomes the empty string.
    """
    assert substitute_variables("[{{title}}]", now=_NOW) == "[]"


def test_repeated_tokens_all_substituted() -> None:
    """
    Every occurrence of a token is replaced, not just the first.
    """
    result = substitute_variables("{{date}} {{date}}", now=_NOW)
    assert result == "2026-07-02 2026-07-02"


# -- list_templates ------------------------------------------------------------


def test_list_templates_sorted(tmp_path: Path) -> None:
    """
    Templates come back sorted by name; non-``.md`` files are skipped.
    """
    folder = tmp_path / "Templates"
    folder.mkdir()
    (folder / "zebra.md").write_text("z", encoding="utf-8")
    (folder / "Alpha.md").write_text("a", encoding="utf-8")
    (folder / "notes.txt").write_text("not a template", encoding="utf-8")
    result = list_templates(tmp_path, make_config())
    assert [path.name for path in result] == ["Alpha.md", "zebra.md"]


def test_list_templates_absent_folder_is_empty(tmp_path: Path) -> None:
    """
    A vault without a templates folder yields [] (never raises).
    """
    assert list_templates(tmp_path, make_config()) == []


def test_list_templates_honors_configured_folder(tmp_path: Path) -> None:
    """
    The folder name comes from ``config["templates"]["folder"]``.
    """
    config = make_config()
    config["templates"]["folder"] = "Blueprints"
    folder = tmp_path / "Blueprints"
    folder.mkdir()
    (folder / "note.md").write_text("x", encoding="utf-8")
    assert [path.name for path in list_templates(tmp_path, config)] == ["note.md"]


def test_list_templates_falls_back_to_underscore_templates(tmp_path: Path) -> None:
    """
    A vault with ``_templates/`` (and no ``Templates/``) is discovered
    via the well-known fallback (HOPPUS-75 F15).
    """
    folder = tmp_path / "_templates"
    folder.mkdir()
    (folder / "meeting.md").write_text("m", encoding="utf-8")
    result = list_templates(tmp_path, make_config())
    assert [path.name for path in result] == ["meeting.md"]


def test_list_templates_prefers_configured_folder_over_fallback(
    tmp_path: Path,
) -> None:
    """
    When the configured folder exists, fallbacks are ignored
    (HOPPUS-75 F15).
    """
    configured = tmp_path / "Templates"
    configured.mkdir()
    (configured / "real.md").write_text("r", encoding="utf-8")
    fallback = tmp_path / "_templates"
    fallback.mkdir()
    (fallback / "shadowed.md").write_text("s", encoding="utf-8")
    result = list_templates(tmp_path, make_config())
    assert [path.name for path in result] == ["real.md"]


# -- resolve_templates_folder ----------------------------------------------------


def test_resolve_returns_configured_folder_when_present(tmp_path: Path) -> None:
    """
    The configured folder wins whenever it exists.
    """
    (tmp_path / "Templates").mkdir()
    assert resolve_templates_folder(tmp_path, make_config()) == tmp_path / "Templates"


def test_resolve_falls_back_to_well_known_names(tmp_path: Path) -> None:
    """
    ``_templates`` is tried when the configured folder is absent.
    """
    (tmp_path / "_templates").mkdir()
    folder = resolve_templates_folder(tmp_path, make_config())
    assert folder == tmp_path / "_templates"


def test_resolve_returns_configured_path_when_nothing_exists(tmp_path: Path) -> None:
    """
    With no candidate present, the configured (nonexistent) path comes
    back so callers can report exactly what was looked for.
    """
    folder = resolve_templates_folder(tmp_path, make_config())
    assert folder == tmp_path / "Templates"
    assert not folder.exists()


# -- render_template -----------------------------------------------------------


def test_render_template_uses_config_formats(tmp_path: Path) -> None:
    """
    Rendering reads the file and applies the config's formats.
    """
    template = tmp_path / "t.md"
    template.write_text("# {{title}}\n{{date}} {{time}}\n", encoding="utf-8")
    config = make_config()
    config["templates"]["date_format"] = "%d/%m/%Y"
    config["templates"]["time_format"] = "%H.%M"
    result = render_template(template, now=_NOW, title="T", config=config)
    assert result == "# T\n02/07/2026 13.45\n"


def test_render_template_missing_file_raises_oserror(tmp_path: Path) -> None:
    """
    A missing template raises OSError for the caller to notify on.
    """
    with pytest.raises(OSError):
        render_template(tmp_path / "gone.md", now=_NOW, config=make_config())


# -- new_note_from_template ----------------------------------------------------


def _seed_template(tmp_path: Path) -> Path:
    """
    Create ``Templates/note.md`` in a temp vault and return its path.
    """
    folder = tmp_path / "Templates"
    folder.mkdir()
    template = folder / "note.md"
    template.write_text("# {{title}}\n{{date}}\n", encoding="utf-8")
    return template


def test_new_note_created_at_root_with_rendered_content(tmp_path: Path) -> None:
    """
    The note lands at the vault root, seeded with the rendered
    template; the title defaults to the note name.
    """
    template = _seed_template(tmp_path)
    path = new_note_from_template(
        tmp_path, "Fresh", template, now=_NOW, config=make_config()
    )
    assert path == tmp_path / "Fresh.md"
    assert path.read_text(encoding="utf-8") == "# Fresh\n2026-07-02\n"


def test_new_note_collision_refused(tmp_path: Path) -> None:
    """
    An existing note with the same name is never overwritten.
    """
    template = _seed_template(tmp_path)
    (tmp_path / "Taken.md").write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        new_note_from_template(
            tmp_path, "Taken", template, now=_NOW, config=make_config()
        )
    assert (tmp_path / "Taken.md").read_text(encoding="utf-8") == "original"


def test_new_note_invalid_name_refused(tmp_path: Path) -> None:
    """
    Reserved characters in the name raise ValueError (spec §5.2).
    """
    template = _seed_template(tmp_path)
    with pytest.raises(ValueError):
        new_note_from_template(
            tmp_path, "bad/name", template, now=_NOW, config=make_config()
        )


def test_broken_template_leaves_no_note_behind(tmp_path: Path) -> None:
    """
    A missing template raises before the note is created.
    """
    with pytest.raises(OSError):
        new_note_from_template(
            tmp_path, "Fresh", tmp_path / "gone.md", now=_NOW, config=make_config()
        )
    assert not (tmp_path / "Fresh.md").exists()
