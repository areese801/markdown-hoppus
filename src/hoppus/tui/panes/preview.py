"""
Preview pane: the main-pane widget rendering the active note (spec §9.5).

Renders natively by default (Textual ``Markdown`` fed by
``render_native``), through ``glow`` when configured *and* installed, and
falls back to a raw Rich ``Syntax`` view — either on demand (raw toggle)
or automatically when native rendering fails. ``set_note`` also pushes
the note title and word count into the app's reactive status-line state.

Link resolution for clicked ``hoppus://`` links happens here too: the
pane lazily builds an ``Index`` for its ``vault_root`` and resolves a
decoded ``LinkTarget`` with a ``Resolver`` (spec §6.2). The app's
``Markdown.LinkClicked`` handler calls :meth:`PreviewPane.open_target`.
"""

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from hoppus.index.indexer import Index, build_note
from hoppus.model import Link
from hoppus.parse.links import Resolver
from hoppus.render.ofm_markdown import LinkTarget
from hoppus.render.terminal import (
    render_glow,
    render_native,
    render_raw,
    select_renderer,
)


class PreviewPane(VerticalScroll):
    """
    The main preview pane: native Markdown, raw toggle, optional glow.
    """

    can_focus = True

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

    def on_mount(self) -> None:
        """
        Start with the (empty) rendered view visible.
        """
        self.query_one("#preview-raw", Static).display = False

    # -- Rendering -----------------------------------------------------------

    async def set_note(self, path: Path) -> None:
        """
        Load a note from disk, render it, and update the app status line.

        Args:
            path: Path to the ``.md`` note to preview.
        """
        path = Path(path)
        self._text = path.read_text(encoding="utf-8")
        self.note_path = path
        await self._refresh_view()
        note = build_note(path)
        app = self.app
        if hasattr(app, "note_title"):
            app.note_title = note.title
            app.word_count = note.word_count

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
            await markdown.update(render_native(self._text))
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

    def resolve_target(self, target: LinkTarget) -> Path | None:
        """
        Resolve a decoded ``hoppus://`` link target to a note path.

        Args:
            target: The decoded navigation payload.

        Returns:
            The resolved path, or None when no vault root is set or the
            target is missing/ambiguous (spec §6.2).
        """
        index = self._ensure_index()
        if index is None:
            return None
        resolver = Resolver(list(index.notes_by_path.values()), index.vault_root)
        current = (
            index.notes_by_path.get(self.note_path)
            if self.note_path is not None
            else None
        )
        link = Link(
            source=self.note_path or index.vault_root,
            target=target.target,
            anchor=target.anchor,
            display=target.display,
            is_embed=target.is_embed,
        )
        return resolver.resolve(link, current)

    async def open_target(self, target: LinkTarget) -> bool:
        """
        Navigate to a clicked link target if it resolves to a note.

        Args:
            target: The decoded navigation payload.

        Returns:
            True when the target resolved and its note was loaded.
        """
        resolved = self.resolve_target(target)
        if resolved is None or resolved.suffix.lower() != ".md":
            return False
        await self.set_note(resolved)
        return True
