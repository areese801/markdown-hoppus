"""
The Textual App: three-region layout, keymap, and status line (spec §9.1).

Regions: a tabbed left sidebar (Explorer · Tags · Bookmarks), a main pane
hosting the note preview (spec §9.5), and a toggleable right sidebar
(Backlinks). A header/status line shows the current vault name, active
note title, and word count; a footer shows key hints. The Explorer tab
hosts the File Explorer tree (spec §9.2); the remaining sidebar tabs are
placeholders until their stories land. The keymap
defaults come from spec §9.16 and are remappable via the ``keymap``
config section (see ``hoppus.tui.keymap``).
"""

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    DirectoryTree,
    Footer,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

from hoppus.config import load_config
from hoppus.index.indexer import Index
from hoppus.render.ofm_markdown import decode_href
from hoppus.tui.keymap import default_bindings, resolve_keymap_overrides
from hoppus.tui.modals.quick_switcher import QuickSwitcherModal, SwitcherResult
from hoppus.tui.panes.explorer import ExplorerPane
from hoppus.tui.panes.preview import PreviewPane


class PanePlaceholder(Static):
    """
    A focusable, clearly-labeled placeholder for a not-yet-built pane.
    """

    can_focus = True

    def __init__(self, label: str, *, id: str | None = None) -> None:
        """
        Args:
            label: The pane name to show in the placeholder text.
            id: Optional widget id.
        """
        super().__init__(
            f"[dim]PLACEHOLDER — {label} (coming in a later story)[/dim]", id=id
        )


class StatusLine(Static):
    """
    Header/status line: current vault name, note title, and word count.
    """

    def update_status(
        self, vault_name: str | None, note_title: str | None, word_count: int | None
    ) -> None:
        """
        Re-render the status line from the app's reactive state.

        Args:
            vault_name: Active vault name, or None when no vault is open.
            note_title: Active note title, or None when no note is open.
            word_count: Active note word count, or None when unknown.
        """
        vault = vault_name or "(no vault)"
        title = note_title or "(no note)"
        words = "– words" if word_count is None else f"{word_count} words"
        self.update(f" hoppus · {vault} · {title} · {words}")


class HoppusApp(App[None]):
    """
    The markdown-hoppus TUI shell (spec §9.1).
    """

    TITLE = "hoppus"
    BINDINGS = [  # type: ignore[assignment]
        *default_bindings(),
        Binding("m", "toggle_raw", "Raw view", show=False, id="toggle_raw"),
    ]

    CSS = """
    #status-line {
        dock: top;
        height: 1;
        background: $panel;
        color: $text;
    }
    #body {
        height: 1fr;
    }
    #left-sidebar {
        width: 28;
        border-right: solid $primary;
    }
    #main-pane {
        width: 1fr;
        padding: 1 2;
    }
    #right-sidebar {
        width: 32;
        border-left: solid $primary;
    }
    #right-sidebar > .sidebar-title {
        text-style: bold;
        padding: 0 1;
    }
    PanePlaceholder:focus {
        background: $boost;
    }
    """

    vault_name: reactive[str | None] = reactive(None)
    note_title: reactive[str | None] = reactive(None)
    word_count: reactive[int | None] = reactive(None)

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        vaults_root: Path | None = None,
    ) -> None:
        """
        Args:
            config: A merged configuration dict; loaded via
                ``load_config()`` when omitted.
            vaults_root: Optional vaults root directory; defaults to the
                configured ``vaults_root``.
        """
        super().__init__()
        self.config = config if config is not None else load_config()
        self.vaults_root = (
            Path(vaults_root)
            if vaults_root is not None
            else Path(str(self.config["vaults_root"])).expanduser()
        )
        # Stashed by the quick switcher's editor path for the future
        # $EDITOR handoff story (spec §9.6).
        self._pending_editor_path: Path | None = None

    def compose(self) -> ComposeResult:
        """
        Build the three-region layout plus status line and footer.
        """
        yield StatusLine(id="status-line")
        with Horizontal(id="body"):
            with TabbedContent(id="left-sidebar"):
                with TabPane("Explorer", id="tab-explorer"):
                    yield ExplorerPane(self._active_vault_path(), id="explorer-pane")
                with TabPane("Tags", id="tab-tags"):
                    yield PanePlaceholder("Tag pane", id="tags-placeholder")
                with TabPane("Bookmarks", id="tab-bookmarks"):
                    yield PanePlaceholder("Bookmarks", id="bookmarks-placeholder")
            yield PreviewPane(self.config, id="main-pane")
            with Vertical(id="right-sidebar"):
                yield Static("Backlinks", classes="sidebar-title")
                yield PanePlaceholder(
                    "Backlinks & unlinked mentions", id="backlinks-placeholder"
                )
        yield Footer()

    def on_mount(self) -> None:
        """
        Apply keymap overrides from config and seed the status line.
        """
        self.set_keymap(resolve_keymap_overrides(self.config))
        self.vault_name = self.config.get("default_vault")
        self._refresh_status_line()

    # -- File Explorer -------------------------------------------------------

    def _active_vault_path(self) -> Path:
        """
        Resolve the active vault directory for the File Explorer root.

        Uses ``vaults_root / vault_name`` when a vault name is known
        (the reactive, or the configured default before mount) and that
        directory exists; otherwise falls back to the Vaults Root itself
        so the pane still renders when no default vault is set.
        """
        name = self.vault_name or self.config.get("default_vault")
        if name:
            candidate = self.vaults_root / str(name)
            if candidate.is_dir():
                return candidate
        return self.vaults_root

    async def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        """
        Open a ``.md`` file chosen in the File Explorer (spec §9.2).

        Attachments (non-``.md`` files) produce a gentle notification;
        opening them in a system app is a later story.
        """
        event.stop()
        path = Path(event.path)
        if not path.is_file():
            return
        if path.suffix.lower() != ".md":
            self.notify(f"Not a note: {path.name}", severity="information", timeout=3)
            return
        explorer = self.query_one("#explorer-pane", ExplorerPane)
        await self.open_note(path, vault_root=Path(explorer.path))

    # -- Status line -------------------------------------------------------

    def _refresh_status_line(self) -> None:
        """
        Push the reactive vault/title/word-count state to the status line.
        """
        try:
            status = self.query_one(StatusLine)
        except Exception:
            return
        status.update_status(self.vault_name, self.note_title, self.word_count)

    def watch_vault_name(self) -> None:
        """React to vault changes."""
        self._refresh_status_line()

    def watch_note_title(self) -> None:
        """React to active-note changes."""
        self._refresh_status_line()

    def watch_word_count(self) -> None:
        """React to word-count changes."""
        self._refresh_status_line()

    # -- Preview -------------------------------------------------------------

    @property
    def preview(self) -> PreviewPane:
        """The main-pane note preview (spec §9.5)."""
        return self.query_one("#main-pane", PreviewPane)

    async def open_note(self, path: Path, vault_root: Path | None = None) -> None:
        """
        Show a note in the preview pane.

        Args:
            path: Path to the ``.md`` note.
            vault_root: Optional vault root for resolving clicked links;
                when omitted the preview keeps its current vault root.
        """
        preview = self.preview
        if vault_root is not None:
            preview.vault_root = vault_root
        await preview.set_note(path)

    async def action_toggle_raw(self) -> None:
        """
        Toggle the preview between rendered and raw Syntax views.
        """
        await self.preview.toggle_raw()

    async def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        """
        Navigate ``hoppus://`` links clicked in the preview (spec §9.5).

        Non-``hoppus://`` hrefs (e.g. ``http://``) are out of scope here;
        unresolved targets produce a gentle notification.
        """
        target = decode_href(event.href)
        if target is None:
            return
        event.stop()
        if not await self.preview.open_target(target):
            label = target.display or target.target or f"#{target.anchor}"
            self.notify(
                f"Couldn't resolve link: {label}", severity="warning", timeout=3
            )

    # -- Pane navigation ----------------------------------------------------

    @property
    def right_sidebar(self) -> Widget:
        """The right (backlinks) sidebar container."""
        return self.query_one("#right-sidebar")

    def _focus_targets(self) -> list[tuple[Widget, Widget]]:
        """
        Return (region, focus target) pairs for the visible panes.
        """
        left = self.query_one("#left-sidebar", TabbedContent)
        targets: list[tuple[Widget, Widget]] = [
            (left, left.query_one(Tabs)),
            (self.query_one("#main-pane"), self.query_one("#main-pane")),
        ]
        right = self.right_sidebar
        if right.display:
            targets.append((right, self.query_one("#backlinks-placeholder")))
        return targets

    def action_cycle_pane(self) -> None:
        """
        Move focus to the next visible pane (spec §9.16 ``Tab``).
        """
        targets = self._focus_targets()
        next_index = 0
        focused = self.focused
        if focused is not None:
            lineage = set(focused.ancestors_with_self)
            for index, (region, _target) in enumerate(targets):
                if region in lineage:
                    next_index = (index + 1) % len(targets)
                    break
        targets[next_index][1].focus()

    def action_toggle_right_sidebar(self) -> None:
        """
        Show or hide the right (backlinks) sidebar (spec §9.16 ``\\``).
        """
        right = self.right_sidebar
        right.display = not right.display
        if not right.display:
            focused = self.focused
            if focused is not None and right in set(focused.ancestors_with_self):
                self.action_cycle_pane()

    def action_tag_pane(self) -> None:
        """
        Switch the left sidebar to the Tags tab (spec §9.16 ``t``).
        """
        self.query_one("#left-sidebar", TabbedContent).active = "tab-tags"

    # -- Not-yet-implemented actions -----------------------------------------

    def _not_implemented(self, feature: str) -> None:
        """
        Notify that a keybound feature lands in a later story.

        Args:
            feature: Human-readable feature name for the notification.
        """
        self.notify(f"{feature}: not yet implemented", severity="warning", timeout=3)

    def action_help(self) -> None:
        """Help / keymap overlay (later story)."""
        self._not_implemented("Help overlay")

    def action_quick_switcher(self) -> None:
        """
        Open the quick switcher: fuzzy jump to any note (spec §9.3).

        Enter opens the chosen note in the preview; the editor modifier
        routes through ``action_open_editor`` (still a stub), stashing
        the chosen path on ``_pending_editor_path`` for the $EDITOR
        handoff story.
        """
        vault_root = self._active_vault_path()
        index = Index.build(vault_root)

        async def handle_result(result: SwitcherResult | None) -> None:
            if result is None:
                return
            if result.edit:
                self._pending_editor_path = result.path
                self.action_open_editor()
                return
            await self.open_note(result.path, vault_root=vault_root)

        self.push_screen(QuickSwitcherModal(index, vault_root), handle_result)

    def action_search(self) -> None:
        """In-TUI search (later story)."""
        self._not_implemented("Search")

    def action_open_editor(self) -> None:
        """Open in $EDITOR (later story)."""
        self._not_implemented("Open in $EDITOR")

    def action_open_system(self) -> None:
        """Open in system app (later story)."""
        self._not_implemented("Open in system app")

    def action_open_browser(self) -> None:
        """Open in browser (later story)."""
        self._not_implemented("Open in browser")

    def action_link_note(self) -> None:
        """Link this note to… (later story)."""
        self._not_implemented("Link to…")

    def action_toggle_graph(self) -> None:
        """Graph view (later story)."""
        self._not_implemented("Graph view")

    def action_graph_radius_up(self) -> None:
        """Increase graph hop radius (later story)."""
        self._not_implemented("Graph radius")

    def action_graph_radius_down(self) -> None:
        """Decrease graph hop radius (later story)."""
        self._not_implemented("Graph radius")

    def action_daily_note(self) -> None:
        """Daily note (later story)."""
        self._not_implemented("Daily note")

    def action_new_note(self) -> None:
        """New note (later story)."""
        self._not_implemented("New note")

    def action_new_note_from_template(self) -> None:
        """New note from template (later story)."""
        self._not_implemented("New note from template")

    def action_quick_capture(self) -> None:
        """Quick capture (later story)."""
        self._not_implemented("Quick capture")

    def action_toggle_star(self) -> None:
        """Star/unstar (later story)."""
        self._not_implemented("Star/unstar")

    def action_yank_menu(self) -> None:
        """Yank menu (later story)."""
        self._not_implemented("Yank menu")

    def action_reindex(self) -> None:
        """Reindex vault (later story)."""
        self._not_implemented("Reindex")

    def action_vault_switcher(self) -> None:
        """Vault switcher (later story)."""
        self._not_implemented("Vault switcher")
