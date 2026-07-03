"""
Pure, hermetic tests for the local browser renderer (spec §9.5,
HOPPUS-56): standalone HTML output, the no-egress guarantee (no
external references), temp-file writing, an injected opener that never
launches a real browser, the ``go-grip`` availability guard, and the
prohibition on the Python ``grip`` package.
"""

import re
import sys
from pathlib import Path

from hoppus.render import browser
from hoppus.render.browser import (
    go_grip_available,
    launch_go_grip,
    open_in_browser,
    render_html,
    write_preview_html,
)

SAMPLE = "# Hi\n\nSome *text*.\n\n```python\nprint(1)\n```\n"


def test_render_html_is_a_complete_document() -> None:
    """
    Rendering yields a full standalone document with the heading.
    """
    document = render_html(SAMPLE, title="Hi")
    assert document.startswith("<!doctype html>")
    assert "<head>" in document and "</html>" in document
    assert "<title>Hi</title>" in document
    assert "<h1>Hi</h1>" in document


def test_render_html_highlights_code_fences() -> None:
    """
    Python fences are Pygments-highlighted inside a highlight block.
    """
    document = render_html(SAMPLE)
    assert '<pre class="highlight">' in document
    assert "print" in document
    assert '<span class="' in document


def test_render_html_inlines_all_styling() -> None:
    """
    The stylesheet (GitHub-like + Pygments) is embedded inline.
    """
    document = render_html(SAMPLE)
    assert "<style>" in document
    assert ".highlight" in document
    assert "font-family" in document


def test_render_html_makes_no_external_references() -> None:
    """
    No-egress guarantee: no ``http(s)://`` in link/script/img
    attributes and no external tags at all.
    """
    document = render_html(SAMPLE, title="Hi")
    assert "<script" not in document
    assert "<link" not in document
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', document)


def test_render_html_renders_tables() -> None:
    """
    The table extension is enabled.
    """
    document = render_html("| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<table>" in document
    assert "<td>1</td>" in document


def test_render_html_escapes_title() -> None:
    """
    Titles are HTML-escaped into ``<title>``.
    """
    document = render_html("x", title="<b>&")
    assert "<title>&lt;b&gt;&amp;</title>" in document


def test_write_preview_html_creates_readable_file(tmp_path: Path) -> None:
    """
    The renderer writes a readable ``.html`` temp file.
    """
    path = write_preview_html(SAMPLE, title="Hi", tmp_dir=tmp_path)
    assert path.suffix == ".html"
    assert path.parent == tmp_path
    content = path.read_text(encoding="utf-8")
    assert content.startswith("<!doctype html>")
    assert "<h1>Hi</h1>" in content


def test_open_in_browser_uses_injected_opener(tmp_path: Path) -> None:
    """
    The injected opener records the ``file://`` URL; nothing launches.
    """
    html_path = tmp_path / "note.html"
    html_path.write_text("<!doctype html>", encoding="utf-8")
    opened: list[str] = []
    url = open_in_browser(html_path, open_browser=opened.append)
    assert opened == [url]
    assert url.startswith("file://")
    assert url.endswith("note.html")


def test_go_grip_available_toggles_with_fake_which() -> None:
    """
    Availability follows the injected ``which`` lookup.
    """
    assert go_grip_available(which=lambda name: f"/usr/bin/{name}") is True
    assert go_grip_available(which=lambda name: None) is False


def test_launch_go_grip_uses_injected_spawner(tmp_path: Path) -> None:
    """
    The injected spawner records the command; no process starts.
    """
    calls: list[list[str]] = []
    launch_go_grip(tmp_path / "note.md", spawn=calls.append)
    assert calls == [["go-grip", str(tmp_path / "note.md")]]


def test_module_never_imports_python_grip() -> None:
    """
    The Python ``grip`` package (which POSTs to GitHub) is never used.
    """
    source = Path(browser.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import grip|from grip)", source, re.MULTILINE)
    assert not any(name == "grip" or name.startswith("grip.") for name in sys.modules)
