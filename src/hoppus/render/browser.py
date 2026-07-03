"""
Local browser rendering of a note (spec §9.5, HOPPUS-56).

Converts Markdown to a fully self-contained HTML document with
``markdown-it-py`` (fenced code + tables) and ``Pygments`` (code
highlighting), styled with an embedded GitHub-like stylesheet. The
document contains no external ``<link>``, ``<script>``, or remote
``<img>`` references, so opening it makes **zero network requests** —
note content never leaves the machine (spec §3).

Delivery choice (documented per HOPPUS-56): the rendered document is
written to a temporary ``.html`` file and opened via a ``file://`` URL
with :func:`webbrowser.open`. A static local file needs no HTTP server,
guarantees no egress, and is the most robust MVP; the opener is
injectable so tests never launch a real browser.

Optional accelerator: if the self-contained ``go-grip`` binary is on
the PATH *and* the user configured ``preview.browser_renderer:
go-grip``, callers may delegate to it via :func:`launch_go_grip`.
The Python ``grip`` package is **never** imported or invoked — it
POSTs note content to GitHub's API (spec §9.5).
"""

import html
import subprocess
import tempfile
import webbrowser
from collections.abc import Callable
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from hoppus.environment import find_binary

# GitHub-like styling, embedded so the document never references an
# external stylesheet, CDN, or remote font (no-egress guarantee).
_GITHUB_LIKE_CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica,
    Arial, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: #1f2328;
  background-color: #ffffff;
  max-width: 860px;
  margin: 0 auto;
  padding: 32px;
}
h1, h2, h3, h4, h5, h6 {
  margin-top: 24px;
  margin-bottom: 16px;
  font-weight: 600;
  line-height: 1.25;
}
h1 { font-size: 2em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }
h3 { font-size: 1.25em; }
p, blockquote, ul, ol, dl, table, pre { margin-top: 0; margin-bottom: 16px; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
code, pre {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
    monospace;
  font-size: 85%;
}
code {
  background-color: #f0f1f2;
  border-radius: 6px;
  padding: .2em .4em;
}
pre {
  background-color: #f6f8fa;
  border-radius: 6px;
  padding: 16px;
  overflow: auto;
  line-height: 1.45;
}
pre code { background-color: transparent; padding: 0; }
blockquote {
  color: #59636e;
  border-left: .25em solid #d1d9e0;
  padding: 0 1em;
  margin-left: 0;
}
table { border-collapse: collapse; display: block; overflow: auto; }
table th, table td { border: 1px solid #d1d9e0; padding: 6px 13px; }
table th { font-weight: 600; }
table tr:nth-child(2n) { background-color: #f6f8fa; }
hr { height: .25em; border: 0; background-color: #d1d9e0; margin: 24px 0; }
img { max-width: 100%; }
"""


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    """
    Highlight a fenced code block with Pygments for markdown-it-py.

    Args:
        code: The raw code-fence body.
        lang: The fence's info string (language), possibly empty.
        _attrs: Extra fence attributes (unused).

    Returns:
        A ``<pre class="highlight">...`` fragment (markdown-it-py uses
        highlighter output verbatim when it starts with ``<pre``), or
        an empty string to fall back to the default escaped rendering
        when no lexer matches.
    """
    if not lang:
        return ""
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""
    formatter = HtmlFormatter(nowrap=True)
    highlighted = pygments_highlight(code, lexer, formatter)
    return f'<pre class="highlight"><code>{highlighted}</code></pre>'


def render_html(markdown_text: str, *, title: str = "") -> str:
    """
    Render Markdown to a complete, standalone HTML document (pure).

    Uses markdown-it-py (CommonMark plus tables and strikethrough,
    fenced code enabled by default) with Pygments highlighting, and
    embeds a GitHub-like stylesheet plus the Pygments style sheet
    inline. The output references no external hosts — no remote
    ``<link>``, ``<script>``, ``<img>``, or fonts — so viewing it
    makes zero network requests. Relative/local image paths in the
    note are left as-is. OFM wikilinks are not rewritten here; they
    render as their literal ``[[...]]`` text (plain-markdown MVP,
    spec §9.5).

    Args:
        markdown_text: The note's Markdown source.
        title: Document title for ``<title>``; may be empty.

    Returns:
        The full HTML document as a string, starting with
        ``<!doctype html>``.
    """
    parser = MarkdownIt("commonmark", {"highlight": _highlight_code})
    parser.enable(["table", "strikethrough"])
    body = parser.render(markdown_text)
    pygments_css = HtmlFormatter().get_style_defs(".highlight")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        "<style>\n"
        f"{_GITHUB_LIKE_CSS}\n"
        f"{pygments_css}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}"
        "</body>\n"
        "</html>\n"
    )


def write_preview_html(
    markdown_text: str, *, title: str = "", tmp_dir: Path | None = None
) -> Path:
    """
    Write the rendered HTML document to a temporary file.

    Args:
        markdown_text: The note's Markdown source.
        title: Document title, forwarded to :func:`render_html`.
        tmp_dir: Directory for the temp file (default: the system
            temp directory).

    Returns:
        The path of the written ``.html`` file.
    """
    document = render_html(markdown_text, title=title)
    fd, name = tempfile.mkstemp(
        suffix=".html",
        prefix="hoppus-preview-",
        dir=str(tmp_dir) if tmp_dir is not None else None,
    )
    path = Path(name)
    with open(fd, "w", encoding="utf-8") as handle:
        handle.write(document)
    return path


def open_in_browser(
    html_path: Path, *, open_browser: Callable[[str], bool] = webbrowser.open
) -> str:
    """
    Open a local HTML file in the user's browser via its ``file://`` URL.

    The only side effect is the injected ``open_browser`` callable;
    tests pass a recorder so no real browser ever launches. A local
    ``file://`` URL never touches the network.

    Args:
        html_path: Path of the rendered HTML file.
        open_browser: URL opener (defaults to :func:`webbrowser.open`).

    Returns:
        The ``file://`` URL that was opened.
    """
    url = html_path.resolve().as_uri()
    open_browser(url)
    return url


def go_grip_available(*, which: Callable[[str], str | None] | None = None) -> bool:
    """
    Report whether the optional ``go-grip`` binary is on the PATH.

    This guards the optional renderer path: ``go-grip`` is a
    self-contained Go binary that renders locally, unlike the Python
    ``grip`` package (which POSTs to GitHub's API and is therefore
    never used — spec §9.5).

    Args:
        which: Injectable PATH lookup for tests (defaults to
            ``shutil.which`` via :func:`hoppus.environment.find_binary`).

    Returns:
        True when ``go-grip`` is found on the PATH.
    """
    if which is None:
        return find_binary("go-grip") is not None
    return find_binary("go-grip", which=which) is not None


def launch_go_grip(
    note_path: Path,
    *,
    spawn: Callable[..., object] = subprocess.Popen,
) -> None:
    """
    Delegate rendering to the local ``go-grip`` binary (optional path).

    Only called when ``go-grip`` is present on the PATH *and* the user
    configured ``preview.browser_renderer: go-grip``; the builtin
    renderer is the default. ``go-grip`` serves the note on localhost
    itself. The Python ``grip`` package is never used.

    Args:
        note_path: The Markdown note to render.
        spawn: Injectable process launcher (defaults to
            :class:`subprocess.Popen`) so tests never spawn a process.
    """
    spawn(["go-grip", str(note_path)])
