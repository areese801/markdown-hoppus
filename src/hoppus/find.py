"""
Standalone ``hop find`` fuzzy-search plumbing (spec §10, §3, HOPPUS-46).

``hop find`` is the one place an interactive external binary (``fzf``) is
expected; everywhere else stays pure-Python. This module keeps the
corpus-building logic pure and unit-testable and isolates the ``fzf``
subprocess behind an injectable ``run`` callable.

Line format
-----------

Each fzf input line is two tab-delimited columns::

    <title>\\t<vault-relative path>

Note titles are filename stems, so they can never contain a tab (or any
path separator); the tab therefore delimits the columns unambiguously and
the vault-relative path column round-trips a selection back to a unique
note. fzf is invoked with ``--delimiter`` set to a tab and its preview
targets the path column via the ``{2}`` field placeholder, with the
subprocess run from the vault root so relative paths resolve.
"""

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

COLUMN_DELIMITER = "\t"

FZF_PROMPT = "note> "


def build_find_lines(index) -> list[str]:
    """
    Build the fzf input lines for every note in the index.

    One line per note, in a stable order sorted by vault-relative path,
    formatted as ``<title>\\t<relative path>`` (see the module docstring).

    :param index: A built ``hoppus.index.indexer.Index``.
    :returns: The display lines, sorted by vault-relative path.
    """
    lines: list[str] = []
    for path, note in index.notes_by_path.items():
        relative = path.relative_to(index.vault_root)
        lines.append(f"{note.title}{COLUMN_DELIMITER}{relative}")
    return sorted(lines, key=lambda line: line.split(COLUMN_DELIMITER, 1)[1])


def parse_find_selection(line: str, index) -> Path | None:
    """
    Map a selected fzf line back to the note's absolute path.

    Splits on the tab delimiter and resolves the vault-relative path
    column against the index. Titles are filename stems and cannot
    contain tabs, so the split is unambiguous.

    :param line: The line fzf printed for the user's selection.
    :param index: The index the line was built from.
    :returns: The absolute note path, or None if the line does not map
        to a known note (e.g. empty or malformed output).
    """
    _, delimiter, relative = line.rstrip("\n").partition(COLUMN_DELIMITER)
    if not delimiter:
        return None
    path = index.vault_root / relative
    if path in index.notes_by_path:
        return path
    return None


def fzf_command(
    query: str | None, *, fzf_path: str, preview_path: str | None = None
) -> list[str]:
    """
    Build the fzf argv for the note picker (pure — no execution).

    The preview shows the note at the path column (``{2}``, tab
    delimiter), via ``bat --color=always`` when ``preview_path`` points
    at a ``bat`` binary, else plain ``cat``. Relative paths in the
    preview resolve because ``run_find`` executes fzf from the vault
    root.

    :param query: Initial ``--query`` value, or None/empty for none.
    :param fzf_path: Path to the ``fzf`` binary.
    :param preview_path: Path to a ``bat`` binary for colorized
        previews, or None to fall back to ``cat``.
    :returns: The argv list for ``subprocess.run``.
    """
    if preview_path:
        preview = f"{preview_path} --color=always {{2}}"
    else:
        preview = "cat {2}"
    argv = [
        fzf_path,
        "--prompt",
        FZF_PROMPT,
        "--no-multi",
        "--delimiter",
        COLUMN_DELIMITER,
        "--preview",
        preview,
    ]
    if query:
        argv.extend(["--query", query])
    return argv


def run_find(
    index,
    query: str | None,
    *,
    fzf_path: str,
    preview_path: str | None = None,
    environ: Mapping[str, str] = os.environ,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path | None:
    """
    Pipe the vault's notes through fzf and return the chosen note path.

    Feeds ``build_find_lines`` to fzf on stdin (executed from the vault
    root so the preview's relative paths resolve), captures the selected
    line, and maps it back via ``parse_find_selection``.

    :param index: A built ``hoppus.index.indexer.Index``.
    :param query: Initial fuzzy-search query, or None.
    :param fzf_path: Path to the ``fzf`` binary.
    :param preview_path: Optional path to ``bat`` for the preview.
    :param environ: Environment mapping passed to the subprocess.
    :param run: ``subprocess.run``-compatible callable (injectable so
        tests never spawn a real fzf).
    :returns: The absolute path of the selected note; None when the
        user cancelled (fzf exit 130), matched nothing (exit 1), or the
        output did not map to a note.
    :raises RuntimeError: If fzf fails with an unexpected exit code.
    """
    lines = build_find_lines(index)
    if not lines:
        return None
    argv = fzf_command(query, fzf_path=fzf_path, preview_path=preview_path)
    result = run(
        argv,
        input="\n".join(lines) + "\n",
        stdout=subprocess.PIPE,
        text=True,
        env=dict(environ),
        cwd=index.vault_root,
    )
    if result.returncode in (1, 130):
        return None
    if result.returncode != 0:
        raise RuntimeError(f"fzf exited with unexpected code {result.returncode}")
    selected = (result.stdout or "").strip()
    if not selected:
        return None
    return parse_find_selection(selected, index)
