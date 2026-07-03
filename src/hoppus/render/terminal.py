"""
Terminal rendering for the preview pane (spec §9.5).

Three renderers, in order of preference:

- **Native**: :func:`render_native` pre-transforms OFM via
  ``transform_ofm`` so Textual's ``Markdown`` widget renders
  ``[[wikilinks]]`` and ``![[embeds]]`` as clickable ``hoppus://`` links.
- **Raw fallback**: :func:`render_raw` wraps the untransformed note text
  in a Rich ``Syntax`` view (toggleable; also the automatic fallback when
  a note fails to render natively).
- **Optional glow**: :func:`render_glow` pipes the note through the
  ``glow`` binary when it is installed, returning its ANSI output as a
  Rich ``Text``. Glow is never required — :func:`select_renderer` only
  chooses it when ``preview.renderer`` is ``"glow"`` *and* the binary is
  present (detected via ``shutil.which``); otherwise it falls back to
  native.
"""

import shutil
import subprocess
from typing import Any

from rich.syntax import Syntax
from rich.text import Text

from hoppus.render.ofm_markdown import transform_ofm

GLOW_BINARY = "glow"

_GLOW_TIMEOUT_SECONDS = 10.0


def render_native(text: str) -> str:
    """
    Return navigable Markdown for Textual's ``Markdown`` widget.

    :param text: Full note text (frontmatter included).
    :returns: The note with OFM wikilinks/embeds rewritten as standard
        ``[label](hoppus://...)`` links (see ``hoppus.render.ofm_markdown``).
    """
    return transform_ofm(text)


def render_raw(text: str) -> Syntax:
    """
    Return a syntax-highlighted view of the raw, untransformed Markdown.

    :param text: Full note text.
    :returns: A Rich ``Syntax`` renderable using the ``markdown`` lexer.
    """
    return Syntax(text, "markdown", word_wrap=True)


def glow_available() -> bool:
    """
    Report whether the ``glow`` binary is on ``$PATH``.

    :returns: True when ``shutil.which`` finds the binary.
    """
    return shutil.which(GLOW_BINARY) is not None


def select_renderer(config: dict[str, Any]) -> str:
    """
    Choose the preview renderer from config, falling back to native.

    :param config: The merged hoppus configuration.
    :returns: ``"glow"`` only when ``preview.renderer`` is ``"glow"``
        and the binary is present; ``"native"`` otherwise.
    """
    preview = config.get("preview") or {}
    if preview.get("renderer") == "glow" and glow_available():
        return "glow"
    return "native"


def render_glow(text: str) -> Text | None:
    """
    Render a note through the ``glow`` binary, if available.

    Any failure — missing binary, non-zero exit, timeout, or OS error —
    returns None so the caller can fall back to the native renderer.

    :param text: Full note text.
    :returns: Glow's styled output as a Rich ``Text`` (ANSI decoded),
        or None when glow is unavailable or fails.
    """
    if not glow_available():
        return None
    try:
        result = subprocess.run(
            [GLOW_BINARY, "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=_GLOW_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return Text.from_ansi(result.stdout)
