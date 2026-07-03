"""
Pure unit tests for ``hoppus.find`` (spec §10, §3, HOPPUS-46).

Everything here is hermetic: indexes are built from temp vaults and the
fzf subprocess is replaced by injected fakes — no test requires a real
``fzf`` binary.
"""

import subprocess
from pathlib import Path

import pytest

from hoppus.find import (
    build_find_lines,
    fzf_command,
    parse_find_selection,
    run_find,
)
from hoppus.index.indexer import Index


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """
    Create a temp vault with nested notes, including duplicate titles.

    Returns:
        The vault root containing ``Alpha.md``, ``Beta.md``,
        ``projects/Alpha.md`` (duplicate title), and
        ``projects/deep/Gamma.md``.
    """
    root = tmp_path / "Vault"
    (root / "projects" / "deep").mkdir(parents=True)
    (root / "Alpha.md").write_text("# Alpha\n", encoding="utf-8")
    (root / "Beta.md").write_text("# Beta\n", encoding="utf-8")
    (root / "projects" / "Alpha.md").write_text("# Alpha two\n", encoding="utf-8")
    (root / "projects" / "deep" / "Gamma.md").write_text("# Gamma\n", encoding="utf-8")
    return root


@pytest.fixture()
def index(vault: Path) -> Index:
    """
    Build an index over the temp vault.
    """
    return Index.build(vault)


def test_build_find_lines_sorted_by_relative_path(index: Index) -> None:
    """
    Lines come out one per note, sorted by vault-relative path.
    """
    lines = build_find_lines(index)
    paths = [line.split("\t", 1)[1] for line in lines]
    assert paths == sorted(paths)
    assert len(lines) == 4


def test_build_find_lines_format(index: Index) -> None:
    """
    Each line is ``<title>\\t<relative path>`` with exactly one tab.
    """
    for line in build_find_lines(index):
        title, delimiter, relative = line.partition("\t")
        assert delimiter == "\t"
        assert "\t" not in title
        assert "\t" not in relative
        assert relative.endswith(".md")


def test_round_trip_every_note(index: Index) -> None:
    """
    Every line maps back to its exact note path, including notes with
    duplicate titles in different folders (the path column disambiguates).
    """
    resolved = {parse_find_selection(line, index) for line in build_find_lines(index)}
    assert resolved == set(index.notes_by_path)


def test_parse_find_selection_rejects_unknown_lines(index: Index) -> None:
    """
    Malformed or unknown selections map to None, not a bogus path.
    """
    assert parse_find_selection("", index) is None
    assert parse_find_selection("no delimiter here", index) is None
    assert parse_find_selection("Ghost\tnot/a/note.md", index) is None


def test_fzf_command_with_query() -> None:
    """
    A given query appears as ``--query``; core flags are always present.
    """
    argv = fzf_command("alpha", fzf_path="/usr/bin/fzf")
    assert argv[0] == "/usr/bin/fzf"
    assert argv[argv.index("--query") + 1] == "alpha"
    assert "--no-multi" in argv
    assert argv[argv.index("--prompt") + 1] == "note> "
    assert argv[argv.index("--delimiter") + 1] == "\t"


def test_fzf_command_without_query() -> None:
    """
    ``--query`` is omitted when no query is given (None or empty).
    """
    assert "--query" not in fzf_command(None, fzf_path="fzf")
    assert "--query" not in fzf_command("", fzf_path="fzf")


def test_fzf_command_preview_targets_path_field() -> None:
    """
    The preview references the path column ``{2}`` and uses ``bat``
    when ``preview_path`` is set, else ``cat``.
    """
    plain = fzf_command(None, fzf_path="fzf")
    preview = plain[plain.index("--preview") + 1]
    assert preview == "cat {2}"

    fancy = fzf_command(None, fzf_path="fzf", preview_path="/usr/bin/bat")
    preview = fancy[fancy.index("--preview") + 1]
    assert "{2}" in preview
    assert preview.startswith("/usr/bin/bat")
    assert "--color=always" in preview


def _fake_run(returncode: int, stdout: str):
    """
    Build a ``subprocess.run``-compatible fake with a canned result.

    Records the calls it receives on the returned function's ``calls``
    attribute.
    """
    calls: list[dict] = []

    def run(argv, **kwargs) -> subprocess.CompletedProcess:
        """
        Record the invocation and return the canned CompletedProcess.
        """
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    run.calls = calls
    return run


def test_run_find_returns_selected_path(index: Index, vault: Path) -> None:
    """
    A canned fzf selection maps back to the correct absolute note path.
    """
    line = f"Alpha\t{Path('projects') / 'Alpha.md'}"
    fake = _fake_run(0, line + "\n")
    result = run_find(index, "alp", fzf_path="/fake/fzf", run=fake)
    assert result == vault / "projects" / "Alpha.md"
    call = fake.calls[0]
    assert call["argv"][0] == "/fake/fzf"
    assert call["cwd"] == vault
    assert f"Alpha\t{Path('Alpha.md')}" in call["input"].splitlines()


def test_run_find_cancelled_returns_none(index: Index) -> None:
    """
    fzf exit 130 (Esc/Ctrl-C) is a cancel, not an error.
    """
    fake = _fake_run(130, "")
    assert run_find(index, None, fzf_path="/fake/fzf", run=fake) is None


def test_run_find_empty_stdout_returns_none(index: Index) -> None:
    """
    Exit 0 with empty stdout yields None rather than a crash.
    """
    fake = _fake_run(0, "")
    assert run_find(index, None, fzf_path="/fake/fzf", run=fake) is None


def test_run_find_unexpected_failure_raises(index: Index) -> None:
    """
    An unexpected fzf exit code (e.g. 2) raises RuntimeError.
    """
    fake = _fake_run(2, "")
    with pytest.raises(RuntimeError):
        run_find(index, None, fzf_path="/fake/fzf", run=fake)


def test_run_find_empty_vault_returns_none(tmp_path: Path) -> None:
    """
    A vault with no notes short-circuits to None without invoking fzf.
    """
    root = tmp_path / "Empty"
    root.mkdir()
    empty_index = Index.build(root)
    fake = _fake_run(0, "should not be called")
    assert run_find(empty_index, None, fzf_path="/fake/fzf", run=fake) is None
    assert fake.calls == []
