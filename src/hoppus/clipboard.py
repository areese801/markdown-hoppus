"""
Clipboard yanks (spec §9.14, HOPPUS-54).

Pure builders produce the three yankable strings — the note body, the
note's absolute path, and a ``[[wikilink]]`` to the note — and
:func:`osc52_sequence` encodes text as the terminal OSC 52 clipboard
escape sequence so copying works over SSH. The only side effect lives
in :func:`copy`, which routes to either ``pyperclip`` or an injected
terminal writer depending on the configured backend
(``clipboard.backend``: ``pyperclip`` | ``osc52``).
"""

import base64
import sys
from collections.abc import Callable
from pathlib import Path

import pyperclip

from hoppus.index.indexer import Index
from hoppus.model import Note
from hoppus.parse.links import Resolver


class ClipboardError(Exception):
    """
    Raised when a copy cannot be performed.

    Wraps ``pyperclip.PyperclipException`` (no clipboard tool available,
    e.g. a headless Linux box without ``xclip``/``xsel``) and unknown
    backend names, so callers get one clear exception to notify on
    instead of an unhandled crash.
    """


def yank_body(note_text: str) -> str:
    """
    Return the note's full text, unmodified.

    :param note_text: The note's complete source text.
    :returns: The same text, ready for the clipboard.
    """
    return note_text


def yank_path(note_path: Path) -> str:
    """
    Return the note's absolute path as a string.

    :param note_path: Path to the note; resolved when not already
        absolute.
    :returns: The absolute path string.
    """
    path = Path(note_path)
    if not path.is_absolute():
        path = path.resolve()
    return str(path)


def yank_wikilink(note: Note, index: Index) -> str:
    """
    Return a ``[[wikilink]]`` targeting a note.

    Uses the shortest-unique-path form (spec §6.2) via
    :meth:`hoppus.parse.links.Resolver.shortest_unique_name`: a note
    with a vault-unique filename yields ``[[Title]]``, while a
    duplicated filename yields a path-qualified form such as
    ``[[Projects/Alpha]]``.

    :param note: The target note.
    :param index: A built vault index supplying the note set and
        attachments for uniqueness checks.
    :returns: The wikilink text, brackets included.
    """
    resolver = Resolver(
        list(index.notes_by_path.values()),
        index.vault_root,
        attachments=index._attachments,
    )
    return f"[[{resolver.shortest_unique_name(note)}]]"


def osc52_sequence(text: str) -> str:
    """
    Encode text as an OSC 52 clipboard escape sequence.

    The exact format is ``ESC ] 52 ; c ; <base64> BEL`` — i.e.
    ``"\\x1b]52;c;" + base64(utf8(text)) + "\\x07"`` — where ``c``
    selects the system clipboard. Terminals supporting OSC 52 (and
    forwarding it over SSH) set their host clipboard when this sequence
    is written to the tty.

    :param text: The text to place on the clipboard.
    :returns: The complete escape sequence as a string.
    """
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\x1b]52;c;{encoded}\x07"


def _write_stdout(sequence: str) -> None:
    """
    Write an escape sequence to stdout and flush immediately.

    Flushing matters: the sequence must reach the terminal right away
    for the clipboard to update, not sit in a buffer.

    :param sequence: The OSC 52 sequence to emit.
    """
    sys.stdout.write(sequence)
    sys.stdout.flush()


def copy(
    text: str,
    *,
    backend: str = "pyperclip",
    write: Callable[[str], None] | None = None,
    copy_fn: Callable[[str], None] | None = None,
) -> None:
    """
    Copy text to the clipboard via the configured backend.

    With ``backend="osc52"`` the OSC 52 sequence for ``text`` is written
    to the terminal (default writer: ``sys.stdout.write`` + flush; pass
    ``write`` to inject a fake in tests). With ``backend="pyperclip"``
    the ``pyperclip.copy`` callable (or an injected ``copy_fn``) is
    invoked; when pyperclip has no clipboard tool available it raises
    ``PyperclipException``, which is re-raised as :class:`ClipboardError`
    so the caller can notify gracefully instead of crashing.

    :param text: The text to copy.
    :param backend: ``"pyperclip"`` or ``"osc52"``.
    :param write: Terminal writer for the OSC 52 backend.
    :param copy_fn: Clipboard callable for the pyperclip backend.
    :raises ClipboardError: When no clipboard mechanism is available or
        the backend name is unknown.
    """
    if backend == "osc52":
        writer = write if write is not None else _write_stdout
        writer(osc52_sequence(text))
        return
    if backend != "pyperclip":
        raise ClipboardError(f"Unknown clipboard backend: {backend!r}")
    fn = copy_fn if copy_fn is not None else pyperclip.copy
    try:
        fn(text)
    except pyperclip.PyperclipException as error:
        raise ClipboardError(
            "Copy failed: no clipboard tool available "
            "(try clipboard.backend: osc52 over SSH)"
        ) from error
