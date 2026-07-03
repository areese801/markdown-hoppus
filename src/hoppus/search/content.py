"""
Full-text content search: ripgrep if present, pure-Python scan otherwise
(spec §3, §9.8).

The two backends must return IDENTICAL results — ripgrep is only a speed
accelerator, never a functional dependency (the zero-binary guarantee,
spec §3). Queries are treated as case-insensitive literal substrings, not
regexes. One :class:`ContentMatch` is emitted per matching *line*; the
``column`` records the first hit on that line only (0-based), which is
enough for MVP highlighting.

Config ``search.content_backend`` (spec §12) selects the backend:
``auto`` (rg if present), ``ripgrep`` (rg, silently falling back to the
pure scan when rg is missing or misbehaves), or ``python``.
"""

import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoppus.environment import find_binary
from hoppus.index.indexer import _iter_vault_files
from hoppus.model import Note

#: Seconds to wait for a ripgrep invocation before falling back to Python.
RIPGREP_TIMEOUT = 30.0

#: Parses rg's ``path:line:col:text`` output. The path part is matched
#: non-greedily up to a ``.md`` suffix followed by 1-based line/column
#: numbers, so colons inside the path or the matched text do not confuse
#: the split (we only ever glob ``*.md`` files).
_RG_LINE = re.compile(r"(?s)^(.+?\.md):(\d+):(\d+):(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class ContentMatch:
    """
    One matching line of a full-text content search (spec §9.8).

    Attributes:
        path: Absolute path of the ``.md`` file containing the match.
        line_number: 1-based line number of the matching line.
        line: Full text of the matching line, trailing newline stripped.
        column: 0-based offset of the *first* hit in the line, for
            highlighting. Later hits on the same line are not reported.
    """

    path: Path
    line_number: int
    line: str
    column: int


def resolve_backend(
    config: Mapping[str, Any],
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """
    Map the configured backend + rg availability to the concrete backend.

    Useful for ``hop doctor`` output and tests: it answers "which scanner
    would actually run?" without running it.

    :param config: The merged configuration (spec §12); reads
        ``search.content_backend`` (default ``auto``).
    :param which: Injectable PATH lookup (defaults to ``shutil.which``).
    :returns: ``"ripgrep"`` or ``"python"``. ``ripgrep``/``auto`` resolve
        to ``"python"`` when rg is not on the PATH; unknown config values
        are treated as ``auto``.
    """
    value = str(config.get("search", {}).get("content_backend", "auto"))
    if value == "python":
        return "python"
    if find_binary("rg", which=which):
        return "ripgrep"
    return "python"


def search_content(
    query: str,
    vault_root: Path,
    *,
    backend: str = "auto",
    which: Callable[[str], str | None] = shutil.which,
    notes: Iterable[Note] | None = None,
) -> list[ContentMatch]:
    """
    Search every ``.md`` file under a vault for lines containing a query.

    The query is a case-insensitive literal substring (NOT a regex).
    ``.hoppus/`` and ``.obsidian/`` directories are excluded, mirroring
    the indexer's walk (spec §5.2). Results are sorted deterministically
    by ``(path, line_number, column)`` and are identical regardless of
    the backend that produced them (spec §3 parity guarantee).

    :param query: The literal text to search for. Empty or
        whitespace-only queries return no matches.
    :param vault_root: The vault root directory.
    :param backend: ``"auto"`` (rg if present), ``"ripgrep"`` (rg,
        falling back to the pure scan when rg is missing or fails —
        never a hard failure), or ``"python"``. Unknown values are
        treated as ``"auto"``.
    :param which: Injectable PATH lookup for rg detection.
    :param notes: Optional pre-indexed notes (e.g. from
        ``Index.notes_by_path.values()``); when given, the pure-Python
        scan reads exactly these notes' files instead of re-walking the
        vault. The ripgrep path always walks ``vault_root`` — the shared
        exclusion rule yields the same file set.
    :returns: The sorted matches, possibly empty.
    """
    if not query.strip():
        return []
    vault_root = Path(vault_root).absolute()
    if backend == "python":
        return _search_python(query, _md_files(vault_root, notes))
    if backend == "ripgrep" or backend == "auto":
        rg_path = find_binary("rg", which=which)
        if rg_path:
            return _search_ripgrep(query, vault_root, rg_path, notes=notes)
        return _search_python(query, _md_files(vault_root, notes))
    return search_content(query, vault_root, backend="auto", which=which, notes=notes)


def _md_files(vault_root: Path, notes: Iterable[Note] | None = None) -> list[Path]:
    """
    List the ``.md`` files to scan, from pre-indexed notes or a fresh walk.

    :param vault_root: The vault root directory.
    :param notes: Optional pre-indexed notes whose paths to use.
    :returns: The note file paths.
    """
    if notes is not None:
        return [note.path for note in notes]
    note_paths, _attachments = _iter_vault_files(vault_root)
    return note_paths


def _search_python(query: str, files: Iterable[Path]) -> list[ContentMatch]:
    """
    Pure-Python content scan — the zero-binary reference backend.

    Reads each file (skipping any that raise ``OSError`` or
    ``UnicodeDecodeError`` — rg likewise treats non-UTF-8 files as
    binary), splits it into
    lines without re-adding newlines, and records one match per line that
    contains the query case-insensitively. Only the first hit per line is
    reported (its 0-based column), matching rg's per-line output.

    :param query: The literal text to search for (assumed non-blank).
    :param files: The ``.md`` files to scan.
    :returns: Matches sorted by ``(path, line_number, column)``.
    """
    needle = query.lower()
    matches: list[ContentMatch] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            column = line.lower().find(needle)
            if column != -1:
                matches.append(
                    ContentMatch(path=path, line_number=index, line=line, column=column)
                )
    matches.sort(key=lambda match: (str(match.path), match.line_number, match.column))
    return matches


def _search_ripgrep(
    query: str,
    vault_root: Path,
    rg_path: str,
    *,
    notes: Iterable[Note] | None = None,
) -> list[ContentMatch]:
    """
    Content scan via ripgrep, with an unconditional pure-Python safety net.

    Invokes rg with fixed-string, case-insensitive matching over ``*.md``
    files (excluding ``.hoppus``/``.obsidian``) and parses its
    ``path:line:col:text`` output. rg's 1-based columns are converted to
    the same 0-based columns the Python scanner emits, so parity holds.
    rg exit code 1 means "no matches" and returns ``[]``; any other
    failure (subprocess error, timeout, unexpected exit code, or a
    malformed output line) falls back to :func:`_search_python` — the
    ripgrep path must never be less correct than the zero-binary path.

    :param query: The literal text to search for (assumed non-blank).
    :param vault_root: The vault root directory (absolute).
    :param rg_path: Path to the rg executable.
    :param notes: Optional pre-indexed notes, forwarded to the Python
        fallback only.
    :returns: Matches sorted by ``(path, line_number, column)``.
    """
    command = [
        rg_path,
        "--fixed-strings",
        "--ignore-case",
        "--line-number",
        "--column",
        "--no-heading",
        "--color",
        "never",
        "--glob",
        "*.md",
        "--glob",
        "!.hoppus",
        "--glob",
        "!.obsidian",
        "--",
        query,
        str(vault_root),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=RIPGREP_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return _search_python(query, _md_files(vault_root, notes))
    if completed.returncode == 1:
        return []
    if completed.returncode != 0:
        return _search_python(query, _md_files(vault_root, notes))
    matches: list[ContentMatch] = []
    for raw_line in completed.stdout.splitlines():
        if not raw_line:
            continue
        parsed = _RG_LINE.match(raw_line)
        if parsed is None:
            return _search_python(query, _md_files(vault_root, notes))
        raw_path, line_number, column, line = parsed.groups()
        path = Path(raw_path)
        if not path.is_absolute():
            path = vault_root / path
        matches.append(
            ContentMatch(
                path=path,
                line_number=int(line_number),
                line=line,
                column=int(column) - 1,
            )
        )
    matches.sort(key=lambda match: (str(match.path), match.line_number, match.column))
    return matches
