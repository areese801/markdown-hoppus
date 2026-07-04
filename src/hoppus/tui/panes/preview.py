"""
Preview pane: the main-pane widget rendering the active note (spec §9.5).

Renders natively by default (Textual ``Markdown`` fed by
``render_native``), through ``glow`` when configured *and* installed, and
falls back to a raw Rich ``Syntax`` view — either on demand (raw toggle)
or automatically when native rendering fails. ``set_note`` also pushes
the note title, vault-relative path (HOPPUS-95), and word count into
the app's reactive status-line state.

Link resolution for clicked ``hoppus://`` links happens here too: the
pane lazily builds an ``Index`` for its ``vault_root`` and resolves a
decoded ``LinkTarget`` with a ``Resolver`` (spec §6.2). The app's
``Markdown.LinkClicked`` handler calls :meth:`PreviewPane.open_target`.
"""

from pathlib import Path
from typing import Any

from textual._slug import slug
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownBlock

from hoppus.index.indexer import Index, build_note
from hoppus.model import Link, Note
from hoppus.parse.links import Resolver
from hoppus.render.ofm_markdown import LinkTarget, transform_ofm
from hoppus.render.terminal import (
    render_glow,
    render_native,
    render_raw,
    select_renderer,
)
from hoppus.render.transclude import block_line, expand_embeds


EMPTY_HINT = (
    "No note open.\n\nPress ^o to open a note, or Enter on a file in the Explorer."
)


class PreviewPane(VerticalScroll):
    """
    The main preview pane: native Markdown, raw toggle, optional glow.
    """

    can_focus = True

    DEFAULT_CSS = """
    PreviewPane > #preview-empty {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, config: dict[str, Any], *, id: str | None = None) -> None:
        """
        Args:
            config: The merged hoppus configuration (``preview`` section
                selects the renderer).
            id: Optional widget id.
        """
        super().__init__(id=id)
        self._config = config
        self.note_path: Path | None = None
        self.raw_mode: bool = False
        self._text: str = ""
        self._rendered: str | None = None
        self._vault_root: Path | None = None
        self._index: Index | None = None

    @property
    def vault_root(self) -> Path | None:
        """The vault root used for link resolution, or None."""
        return self._vault_root

    @vault_root.setter
    def vault_root(self, value: Path | None) -> None:
        """Set the vault root and drop any index built for the old one."""
        self._vault_root = Path(value) if value is not None else None
        self._index = None

    def compose(self) -> ComposeResult:
        """
        Build the two stacked views: rendered Markdown and raw/glow text.
        """
        yield Markdown("", id="preview-markdown", open_links=False)
        yield Static("", id="preview-raw")
        yield Static(EMPTY_HINT, id="preview-empty")

    def on_mount(self) -> None:
        """
        Start with the empty-state hint visible (HOPPUS-78).
        """
        self.query_one("#preview-raw", Static).display = False
        self._show_empty_hint(self.note_path is None)

    def _relative_display(self, path: Path) -> str:
        """
        Render a note path relative to the vault root for the status
        line (HOPPUS-95).

        Args:
            path: The note's absolute path.

        Returns:
            The vault-relative path with POSIX separators, or the bare
            filename when no vault root is set or the note lies outside
            it.
        """
        if self._vault_root is not None:
            try:
                return path.relative_to(self._vault_root).as_posix()
            except ValueError:
                pass
        return path.name

    def _show_empty_hint(self, visible: bool) -> None:
        """
        Show or hide the centered no-note-open placeholder (HOPPUS-78).

        Args:
            visible: True to show the hint (no active note), False to
                hide it (a note is open).
        """
        self.query_one("#preview-empty", Static).display = visible

    # -- Rendering -----------------------------------------------------------

    async def set_note(self, path: Path) -> None:
        """
        Load a note from disk, render it, and update the app status line.

        Native rendering pre-expands ``![[...]]`` embeds into transcluded
        content and marks unresolved wikilinks with a subtle indicator
        (spec §9.5); raw mode keeps showing the untouched note text.

        A file that cannot be read or decoded as UTF-8 never crashes
        the pane (HOPPUS-73): a one-line placeholder is shown instead.

        Args:
            path: Path to the ``.md`` note to preview.
        """
        path = Path(path)
        try:
            self._text = path.read_text(encoding="utf-8")
            note = build_note(path)
        except (OSError, UnicodeDecodeError) as error:
            summary = " ".join(f"{type(error).__name__}: {error}".split())
            self._text = f"> ⚠ unreadable file: {summary}"
            note = Note(title=path.stem, path=path)
        self.note_path = path
        self._show_empty_hint(False)
        await self._refresh_view()
        app = self.app
        if hasattr(app, "note_title"):
            app.note_title = note.title
            app.word_count = note.word_count
        if hasattr(app, "note_relpath"):
            app.note_relpath = self._relative_display(path)

    async def clear(self) -> None:
        """
        Reset the pane to the empty (no note) state.

        Clears the active note path and text, leaves raw mode off, empties
        both the Markdown and raw views, shows the (empty) rendered view,
        and restores the no-note-open hint (HOPPUS-78).
        """
        self.note_path = None
        self.raw_mode = False
        self._text = ""
        self._rendered = None
        if hasattr(self.app, "note_relpath"):
            self.app.note_relpath = None
        await self.query_one("#preview-markdown", Markdown).update("")
        static = self.query_one("#preview-raw", Static)
        static.update("")
        self.query_one("#preview-markdown", Markdown).display = True
        static.display = False
        self._show_empty_hint(True)

    async def toggle_raw(self) -> None:
        """
        Switch between the rendered view and the raw Syntax view.
        """
        self.raw_mode = not self.raw_mode
        await self._refresh_view()

    async def _refresh_view(self) -> None:
        """
        Render ``self._text`` per the current mode and configuration.

        Raw mode shows the Rich ``Syntax`` view. Otherwise glow is used
        when configured and available; native rendering is the default,
        and a native render failure flips the pane into raw mode.
        """
        static = self.query_one("#preview-raw", Static)
        if self.raw_mode:
            static.update(render_raw(self._text))
            self._show_static()
            return
        if select_renderer(self._config) == "glow":
            rendered = render_glow(self._text)
            if rendered is not None:
                static.update(rendered)
                self._show_static()
                return
        markdown = self.query_one("#preview-markdown", Markdown)
        try:
            self._rendered = self._native_markdown()
            await markdown.update(self._rendered)
        except Exception:
            self.raw_mode = True
            static.update(render_raw(self._text))
            self._show_static()
            return
        markdown.display = True
        static.display = False

    def _show_static(self) -> None:
        """
        Display the raw/glow Static and hide the Markdown widget.
        """
        self.query_one("#preview-markdown", Markdown).display = False
        self.query_one("#preview-raw", Static).display = True

    # -- Link navigation ------------------------------------------------------

    def _ensure_index(self) -> Index | None:
        """
        Lazily build (and cache) the vault index for link resolution.
        """
        if self._vault_root is None:
            return None
        if self._index is None:
            self._index = Index.build(self._vault_root)
        return self._index

    def _resolution_context(self) -> tuple[Resolver, "Link | None"] | None:
        """
        Build a ``Resolver`` (notes + attachments) and the current note.

        Returns:
            ``(resolver, current_note)``, or None when no vault root is
            set. ``current_note`` is None when the active note is not in
            the index (e.g. no note open yet).
        """
        index = self._ensure_index()
        if index is None:
            return None
        resolver = index._make_resolver()
        current = (
            index.notes_by_path.get(self.note_path)
            if self.note_path is not None
            else None
        )
        return resolver, current

    def resolve_target(self, target: LinkTarget) -> Path | None:
        """
        Resolve a decoded ``hoppus://`` link target to a vault file path.

        Args:
            target: The decoded navigation payload.

        Returns:
            The resolved path (a note or an attachment), or None when no
            vault root is set or the target is missing/ambiguous
            (spec §6.2).
        """
        context = self._resolution_context()
        if context is None:
            return None
        resolver, current = context
        link = Link(
            source=self.note_path or self._vault_root or Path("."),
            target=target.target,
            anchor=target.anchor,
            display=target.display,
            is_embed=target.is_embed,
        )
        return resolver.resolve(link, current)

    def _native_markdown(self) -> str:
        """
        Produce the navigable Markdown for the native preview (spec §9.5).

        Embeds are pre-expanded into transcluded content one level deep,
        then OFM links are transformed with an ``is_resolved`` predicate
        so unresolved wikilinks carry a subtle marker. The plain
        ``render_native`` transform is computed first as the guaranteed
        baseline: when there is no vault root to resolve against, or any
        step of the enhanced pipeline fails, the note still renders.

        Returns:
            Markdown ready for the ``Markdown`` widget.
        """
        base = render_native(self._text)
        try:
            context = self._resolution_context()
            if context is None:
                return base
            resolver, current = context
            expanded = expand_embeds(
                self._text,
                resolve=self.resolve_target,
                read_text=lambda path: path.read_text(encoding="utf-8"),
                depth=1,
            )

            def link_resolved(link: Link) -> bool:
                return resolver.resolve(link, current) is not None

            return transform_ofm(expanded, is_resolved=link_resolved)
        except Exception:
            return base

    async def open_target(self, target: LinkTarget) -> bool:
        """
        Navigate to a clicked link target, honoring anchors and embeds.

        Same-note anchors (``[[#Heading]]`` / ``[[#^id]]``) scroll the
        current note without reloading it. Note targets load the note,
        then scroll to any heading/block anchor. Attachment targets are
        acknowledged with a notification (open-in-system-app is a later
        story). Unresolved targets return False so the app can notify.

        Args:
            target: The decoded navigation payload.

        Returns:
            True when the click was handled.
        """
        if not target.target:
            if target.anchor is None or self.note_path is None:
                return False
            self._scroll_to_anchor(target.anchor)
            return True
        resolved = self.resolve_target(target)
        if resolved is None:
            return False
        if resolved.suffix.lower() != ".md":
            self.notify(
                f"Attachment: {resolved.name} (open in system app comes later)",
                severity="information",
                timeout=3,
            )
            return True
        await self.set_note(resolved)
        if target.anchor is not None:
            self._scroll_to_anchor(target.anchor)
        return True

    def _scroll_to_anchor(self, anchor: str) -> None:
        """
        Best-effort scroll of the rendered view to a heading or block
        anchor, shared by the same-note and cross-note navigation paths.

        Heading anchors go through ``Markdown.goto_anchor`` (nested
        ``H1#H2`` anchors use the slug of the last segment). Block
        anchors (``^id``) are mapped to their source line and the
        covering ``MarkdownBlock`` is scrolled into view. When the
        anchor can't be located, scroll to the top and notify gently —
        never crash (spec §7.4 soft handling).

        Args:
            anchor: Heading text, nested ``H1#H2`` path, or ``^block-id``.
        """
        markdown = self.query_one("#preview-markdown", Markdown)
        if anchor.startswith("^"):
            source = (
                self._rendered
                if self._rendered is not None
                else render_native(self._text)
            )
            line = block_line(source, anchor[1:])
            block = self._block_at_line(markdown, line) if line is not None else None
            if block is not None:
                block.scroll_visible(top=True)
                return
        elif markdown.goto_anchor(slug(anchor.split("#")[-1].strip())):
            return
        self.scroll_home(animate=False)
        self.notify(f"Anchor not found: #{anchor}", severity="information", timeout=3)

    @staticmethod
    def _block_at_line(markdown: Markdown, line: int) -> MarkdownBlock | None:
        """
        Find the innermost ``MarkdownBlock`` covering a source line.

        Args:
            markdown: The rendered Markdown widget.
            line: 0-based line index into the widget's markdown source.

        Returns:
            The covering block with the smallest source range, or None.
        """
        best: MarkdownBlock | None = None
        best_span: int | None = None
        for block in markdown.query(MarkdownBlock):
            start, end = block.source_range
            span = end - start
            if start <= line < end and (best_span is None or span <= best_span):
                best, best_span = block, span
        return best
