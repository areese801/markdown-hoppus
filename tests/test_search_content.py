"""
Tests for the content-search backends (HOPPUS-45, spec §3, §9.8).

The headline property is PARITY: the ripgrep path and the pure-Python
path must return identical :class:`ContentMatch` lists. The ripgrep path
is exercised hermetically — no real rg is required — by monkeypatching
``subprocess.run`` with fakes that emit canned ``path:line:col:text``
output (built from the Python scanner's own results, so the two backends
are compared against the same corpus).
"""

import subprocess
from pathlib import Path

import pytest

from hoppus.search import content
from hoppus.search.content import (
    ContentMatch,
    _search_python,
    _search_ripgrep,
    resolve_backend,
    search_content,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """
    Build a small temp vault with known content for exact-match asserts.
    """
    (tmp_path / "Alpha.md").write_text(
        "# Alpha\nThe quick brown fox\nnothing here\nQUICK reflexes\n",
        encoding="utf-8",
    )
    (tmp_path / "Beta.md").write_text(
        "beta note\nslow but quick-witted\n",
        encoding="utf-8",
    )
    sub = tmp_path / "Projects"
    sub.mkdir()
    (sub / "Gamma.md").write_text("a quick project\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("quick but not markdown\n", encoding="utf-8")
    hidden_hoppus = tmp_path / ".hoppus"
    hidden_hoppus.mkdir()
    (hidden_hoppus / "cache.md").write_text("quick cached\n", encoding="utf-8")
    hidden_obsidian = tmp_path / ".obsidian"
    hidden_obsidian.mkdir()
    (hidden_obsidian / "workspace.md").write_text("quick state\n", encoding="utf-8")
    return tmp_path


def _rg_stdout(matches: list[ContentMatch]) -> str:
    """
    Render matches as rg ``--no-heading --line-number --column`` output.

    rg columns are 1-based, so the 0-based ``column`` is shifted back up.
    """
    return "".join(
        f"{match.path}:{match.line_number}:{match.column + 1}:{match.line}\n"
        for match in matches
    )


def _fake_run(stdout: str, returncode: int = 0):
    """
    Build a ``subprocess.run`` stand-in returning a canned rg result.
    """

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    return run


def _no_rg(name: str) -> None:
    """
    A ``which`` that never finds anything.
    """
    return None


def _rg_found(name: str) -> str | None:
    """
    A ``which`` that finds rg at a fixed fake path.
    """
    return "/fake/bin/rg" if name == "rg" else None


class TestPythonBackend:
    """
    The pure-Python scanner — the zero-binary reference behavior.
    """

    def test_exact_matches(self, vault: Path) -> None:
        """
        Matches carry (path, 1-based line, line text, 0-based column).
        """
        results = search_content("quick", vault, backend="python")
        assert results == [
            ContentMatch(vault / "Alpha.md", 2, "The quick brown fox", 4),
            ContentMatch(vault / "Alpha.md", 4, "QUICK reflexes", 0),
            ContentMatch(vault / "Beta.md", 2, "slow but quick-witted", 9),
            ContentMatch(vault / "Projects" / "Gamma.md", 1, "a quick project", 2),
        ]

    def test_case_insensitive_query(self, vault: Path) -> None:
        """
        An upper-case query hits lower-case lines and vice versa.
        """
        assert search_content("QuIcK", vault, backend="python") == search_content(
            "quick", vault, backend="python"
        )

    def test_no_matches(self, vault: Path) -> None:
        """
        A query with no hits returns an empty list.
        """
        assert search_content("zebra", vault, backend="python") == []

    def test_empty_and_whitespace_query(self, vault: Path) -> None:
        """
        Empty or whitespace-only queries return no matches.
        """
        assert search_content("", vault, backend="python") == []
        assert search_content("   \t", vault, backend="python") == []

    def test_first_hit_per_line_only(self, tmp_path: Path) -> None:
        """
        A line with several hits yields one match at the first column.
        """
        (tmp_path / "Multi.md").write_text("ab ab ab\n", encoding="utf-8")
        results = search_content("ab", tmp_path, backend="python")
        assert results == [ContentMatch(tmp_path / "Multi.md", 1, "ab ab ab", 0)]

    def test_excludes_hidden_dirs_and_non_md(self, vault: Path) -> None:
        """
        ``.hoppus``/``.obsidian`` and non-``.md`` files never match.
        """
        paths = {
            match.path for match in search_content("quick", vault, backend="python")
        }
        assert not any(".hoppus" in path.parts for path in paths)
        assert not any(".obsidian" in path.parts for path in paths)
        assert all(path.suffix == ".md" for path in paths)

    def test_unreadable_file_is_skipped(self, vault: Path) -> None:
        """
        A file that raises ``OSError`` on read is silently skipped.
        """
        results = _search_python("quick", [vault / "missing.md", vault / "Beta.md"])
        assert results == [
            ContentMatch(vault / "Beta.md", 2, "slow but quick-witted", 9)
        ]


class TestRipgrepParity:
    """
    The rg path must produce the exact list the Python path produces.
    """

    def test_parses_canned_rg_output_into_same_matches(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Canned rg output parses into the Python scanner's exact result.
        """
        expected = search_content("quick", vault, backend="python")
        monkeypatch.setattr(content.subprocess, "run", _fake_run(_rg_stdout(expected)))
        assert _search_ripgrep("quick", vault, "/fake/bin/rg") == expected
        assert (
            search_content("quick", vault, backend="ripgrep", which=_rg_found)
            == expected
        )

    def test_rg_exit_one_means_no_matches(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        rg exit code 1 is "no matches", not an error.
        """
        monkeypatch.setattr(content.subprocess, "run", _fake_run("", returncode=1))
        assert search_content("zebra", vault, backend="ripgrep", which=_rg_found) == []

    def test_malformed_rg_line_falls_back_to_python(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A garbage output line triggers the Python fallback, same results.
        """
        monkeypatch.setattr(content.subprocess, "run", _fake_run("not-an-rg-line\n"))
        assert search_content(
            "quick", vault, backend="ripgrep", which=_rg_found
        ) == search_content("quick", vault, backend="python")

    def test_subprocess_error_falls_back_to_python(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        An ``OSError`` from the rg launch triggers the Python fallback.
        """

        def boom(command, **kwargs):
            raise OSError("rg exploded")

        monkeypatch.setattr(content.subprocess, "run", boom)
        assert search_content(
            "quick", vault, backend="ripgrep", which=_rg_found
        ) == search_content("quick", vault, backend="python")

    def test_unexpected_exit_code_falls_back_to_python(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        rg exit code 2 (error) triggers the Python fallback.
        """
        monkeypatch.setattr(content.subprocess, "run", _fake_run("", returncode=2))
        assert search_content(
            "quick", vault, backend="ripgrep", which=_rg_found
        ) == search_content("quick", vault, backend="python")

    def test_path_containing_colon_parses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A path with a ``:`` still splits correctly on the rg path.
        """
        weird = tmp_path / "a:b"
        weird.mkdir()
        (weird / "Colon.md").write_text("find me: here\n", encoding="utf-8")
        expected = search_content("me: here", tmp_path, backend="python")
        assert expected == [ContentMatch(weird / "Colon.md", 1, "find me: here", 5)]
        monkeypatch.setattr(content.subprocess, "run", _fake_run(_rg_stdout(expected)))
        assert (
            search_content("me: here", tmp_path, backend="ripgrep", which=_rg_found)
            == expected
        )


class TestBackendSelection:
    """
    Backend selection: config value + rg availability → concrete backend.
    """

    def test_ripgrep_backend_without_rg_uses_python(self, vault: Path) -> None:
        """
        ``backend="ripgrep"`` with no rg silently runs the Python scan.
        """
        assert search_content(
            "quick", vault, backend="ripgrep", which=_no_rg
        ) == search_content("quick", vault, backend="python")

    def test_auto_uses_rg_when_found(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        ``backend="auto"`` invokes rg when ``which`` finds it (via a spy).
        """
        calls: list[list[str]] = []
        expected = search_content("quick", vault, backend="python")

        def spy_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, _rg_stdout(expected), "")

        monkeypatch.setattr(content.subprocess, "run", spy_run)
        assert (
            search_content("quick", vault, backend="auto", which=_rg_found) == expected
        )
        assert len(calls) == 1
        assert calls[0][0] == "/fake/bin/rg"

    def test_auto_uses_python_without_rg(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        ``backend="auto"`` never spawns a subprocess when rg is absent.
        """

        def forbidden(command, **kwargs):
            raise AssertionError("subprocess.run must not be called")

        monkeypatch.setattr(content.subprocess, "run", forbidden)
        assert search_content(
            "quick", vault, backend="auto", which=_no_rg
        ) == search_content("quick", vault, backend="python")

    def test_resolve_backend_mapping(self) -> None:
        """
        ``resolve_backend`` maps config value + rg availability.
        """
        rg_config = {"search": {"content_backend": "ripgrep"}}
        assert resolve_backend(rg_config, which=_rg_found) == "ripgrep"
        assert resolve_backend(rg_config, which=_no_rg) == "python"
        auto_config = {"search": {"content_backend": "auto"}}
        assert resolve_backend(auto_config, which=_rg_found) == "ripgrep"
        assert resolve_backend(auto_config, which=_no_rg) == "python"
        python_config = {"search": {"content_backend": "python"}}
        assert resolve_backend(python_config, which=_rg_found) == "python"
        assert resolve_backend({}, which=_no_rg) == "python"
        unknown = {"search": {"content_backend": "warp-drive"}}
        assert resolve_backend(unknown, which=_rg_found) == "ripgrep"
        assert resolve_backend(unknown, which=_no_rg) == "python"
