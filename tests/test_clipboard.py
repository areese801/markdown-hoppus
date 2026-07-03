"""
Tests for the pure clipboard helpers (spec §9.14, HOPPUS-54).

Covers the three yank content builders, the OSC 52 escape-sequence
encoding, and the ``copy`` side-effect seam with injected fakes — no
real clipboard is touched anywhere.
"""

import base64
from pathlib import Path

import pyperclip
import pytest

from hoppus.clipboard import (
    ClipboardError,
    copy,
    osc52_sequence,
    yank_body,
    yank_path,
    yank_wikilink,
)
from hoppus.index.indexer import Index


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """
    Build a temp vault with a unique note and a duplicated stem.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root.
    """
    (tmp_path / "Unique.md").write_text("# Unique\n\nBody text.\n", encoding="utf-8")
    for folder in ("Projects", "Archive"):
        directory = tmp_path / folder
        directory.mkdir()
        (directory / "Alpha.md").write_text(f"# {folder} Alpha\n", encoding="utf-8")
    return tmp_path


def test_yank_body_returns_text_unmodified() -> None:
    """
    The body yank is the note text, as-is.
    """
    text = "# Title\n\nLine one.\nLine two.\n"
    assert yank_body(text) == text


def test_yank_path_is_absolute(tmp_path: Path) -> None:
    """
    The path yank is the absolute path string.
    """
    note = tmp_path / "Note.md"
    result = yank_path(note)
    assert result == str(note)
    assert Path(result).is_absolute()


def test_yank_wikilink_unique_note_uses_bare_title(vault: Path) -> None:
    """
    A vault-unique filename yields the bare ``[[Title]]`` form.
    """
    index = Index.build(vault)
    note = index.notes_by_path[vault / "Unique.md"]
    assert yank_wikilink(note, index) == "[[Unique]]"


def test_yank_wikilink_duplicate_stem_is_path_qualified(vault: Path) -> None:
    """
    A duplicated filename yields the shortest path-qualified form.
    """
    index = Index.build(vault)
    note = index.notes_by_path[vault / "Projects" / "Alpha.md"]
    assert yank_wikilink(note, index) == "[[Projects/Alpha]]"


def test_osc52_sequence_format_and_roundtrip() -> None:
    """
    The OSC 52 sequence is ``ESC ] 52 ; c ; <base64> BEL`` and the
    payload round-trips back to the original UTF-8 text.
    """
    text = "hello [[wörld]]"
    sequence = osc52_sequence(text)
    assert sequence.startswith("\x1b]52;c;")
    assert sequence.endswith("\x07")
    payload = sequence[len("\x1b]52;c;") : -1]
    assert base64.b64decode(payload).decode("utf-8") == text
    assert payload == base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_copy_osc52_writes_sequence_to_injected_writer() -> None:
    """
    The osc52 backend sends the full escape sequence to ``write``.
    """
    written: list[str] = []
    copy("some text", backend="osc52", write=written.append)
    assert written == [osc52_sequence("some text")]


def test_copy_pyperclip_calls_injected_copy_fn() -> None:
    """
    The pyperclip backend delegates to the injected callable.
    """
    copied: list[str] = []
    copy("some text", backend="pyperclip", copy_fn=copied.append)
    assert copied == ["some text"]


def test_copy_pyperclip_failure_raises_clipboard_error() -> None:
    """
    A ``PyperclipException`` becomes a clear ``ClipboardError``, never
    an unhandled crash.
    """

    def broken(_text: str) -> None:
        raise pyperclip.PyperclipException("no clipboard mechanism")

    with pytest.raises(ClipboardError):
        copy("some text", backend="pyperclip", copy_fn=broken)


def test_copy_unknown_backend_raises_clipboard_error() -> None:
    """
    An unrecognized backend name raises ``ClipboardError``.
    """
    with pytest.raises(ClipboardError):
        copy("some text", backend="telepathy")
