"""
Pure tests for daily notes (spec §9.9, HOPPUS-49).

Exercises :mod:`hoppus.daily` against temp vaults with a fixed injected
``datetime``: path computation with custom date formats, template
seeding, the missing-template fallback, and idempotence.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from hoppus.daily import daily_note_path, open_or_create_daily

FIXED_NOW = datetime(2026, 7, 3, 14, 30, 0)


@pytest.fixture()
def config() -> dict[str, Any]:
    """
    Return a minimal config with the default daily-notes section.
    """
    return {
        "daily_notes": {
            "folder": "Daily",
            "date_format": "%Y-%m-%d",
            "template": "Templates/daily.md",
        },
        "templates": {
            "folder": "Templates",
            "date_format": "%Y-%m-%d",
            "time_format": "%H:%M",
        },
    }


def test_daily_note_path_default_format(tmp_path: Path, config: dict[str, Any]) -> None:
    """
    The default format yields ``Daily/YYYY-MM-DD.md`` under the vault.
    """
    path = daily_note_path(tmp_path, config, now=FIXED_NOW)
    assert path == tmp_path / "Daily" / "2026-07-03.md"


def test_daily_note_path_custom_format_and_folder(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    """
    A custom ``date_format`` and ``folder`` are honored in the path.
    """
    config["daily_notes"]["folder"] = "Journal"
    config["daily_notes"]["date_format"] = "%d.%m.%Y"
    path = daily_note_path(tmp_path, config, now=FIXED_NOW)
    assert path == tmp_path / "Journal" / "03.07.2026.md"


def test_open_or_create_seeds_from_template(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    """
    A new note is seeded from the configured template, with the date
    string rendered as ``{{title}}``, and the folder is created.
    """
    templates_dir = tmp_path / "Templates"
    templates_dir.mkdir()
    (templates_dir / "daily.md").write_text("# {{title}}\n", encoding="utf-8")

    path, created = open_or_create_daily(tmp_path, config, now=FIXED_NOW)

    assert created is True
    assert path == tmp_path / "Daily" / "2026-07-03.md"
    assert path.read_text(encoding="utf-8") == "# 2026-07-03\n"


def test_open_or_create_without_template_seeds_title_heading(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    """
    A missing template falls back to a ``# <date>`` title heading —
    never an error, never a 0-byte file (HOPPUS-71 F7).
    """
    path, created = open_or_create_daily(tmp_path, config, now=FIXED_NOW)

    assert created is True
    assert path.read_text(encoding="utf-8") == "# 2026-07-03\n"


def test_open_or_create_is_idempotent(tmp_path: Path, config: dict[str, Any]) -> None:
    """
    A second call returns ``(same path, False)`` and leaves the
    existing file's contents untouched.
    """
    templates_dir = tmp_path / "Templates"
    templates_dir.mkdir()
    (templates_dir / "daily.md").write_text("# {{title}}\n", encoding="utf-8")

    first_path, first_created = open_or_create_daily(tmp_path, config, now=FIXED_NOW)
    first_path.write_text("# 2026-07-03\n\nMy edits.\n", encoding="utf-8")

    second_path, second_created = open_or_create_daily(tmp_path, config, now=FIXED_NOW)

    assert first_created is True
    assert second_created is False
    assert second_path == first_path
    assert second_path.read_text(encoding="utf-8") == "# 2026-07-03\n\nMy edits.\n"
