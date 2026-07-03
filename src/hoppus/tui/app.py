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

import os
import subprocess
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

from hoppus import fileops, integrity, related
from hoppus.config import load_config
from hoppus.index.indexer import Index
from hoppus.index.mentions import find_unlinked_mentions
from hoppus.index.watcher import VaultWatcher
from hoppus.parse.links import Resolver
from hoppus.render.ofm_markdown import decode_href
from hoppus.search.engine import SearchResult
from hoppus.tui.command_palette import HoppusCommandProvider
from hoppus.tui.graph_view import GraphScreen
from hoppus.tui.keymap import default_bindings, resolve_keymap_overrides
from hoppus.tui.modals.link_integrity import LinkIntegrityModal
from hoppus.tui.modals.quick_switcher import QuickSwitcherModal, SwitcherResult
from hoppus.tui.modals.related_picker import RelatedChoice, RelatedPickerModal
from hoppus.tui.modals.search_screen import SearchScreen
from hoppus.tui.modals.vault_switcher import VaultSwitcherModal
from hoppus.tui.panes.backlinks import BacklinksPane
from hoppus.tui.panes.explorer import ExplorerPane
from hoppus.tui.panes.preview import PreviewPane
from hoppus.vault import discover_vaults


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
        self,
        vault_name: str | None,
        note_title: str | None,
        word_count: int | None,
        problems: int | None = None,
    ) -> None:
        """
        Re-render the status line from the app's reactive state.

        Args:
            vault_name: Active vault name, or None when no vault is open.
            note_title: Active note title, or None when no note is open.
            word_count: Active note word count, or None when unknown.
            problems: Report-only integrity issue count (spec §19 D1);
                the ⚠ segment appears only when this is > 0, so the
                string is unchanged for None/0.
        """
        vault = vault_name or "(no vault)"
        title = note_title or "(no note)"
        words = "– words" if word_count is None else f"{word_count} words"
        status = f" hoppus · {vault} · {title} · {words}"
        if problems:
            status += f" · ⚠ {problems} problems"
        self.update(status)


class HoppusApp(App[None]):
    """
    The markdown-hoppus TUI shell (spec §9.1).
    """

    TITLE = "hoppus"
    COMMANDS = App.COMMANDS | {HoppusCommandProvider}
    BINDINGS = [  # type: ignore[assignment]
        *default_bindings(),
        Binding("m", "toggle_raw", "Raw view", show=False, id="toggle_raw"),
        Binding(
            "u",
            "unlinked_mentions",
            "Unlinked mentions",
            show=False,
            id="unlinked_mentions",
        ),
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
    #backlinks-pane {
        height: 1fr;
        border: none;
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
        # Cached active-vault index for the backlinks pane (HOPPUS-32);
        # HOPPUS-33's unlinked mentions will reuse it.
        self._index: Index | None = None
        self._index_root: Path | None = None
        # Report-only integrity issue count for the status-line
        # "problems" indicator (spec §19 D1, HOPPUS-40).
        self._problem_count: int = 0
        # Live watcher slot (spec §8/D5, HOPPUS-38). The app does not
        # auto-start a watcher yet (HOPPUS-37 kept live watching off in
        # headless tests); when one is enabled, self-writes route through
        # _suppress_self_write so they never re-trigger the index pass.
        self._watcher: VaultWatcher | None = None

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
                yield BacklinksPane(id="backlinks-pane")
        yield Footer()

    def on_mount(self) -> None:
        """
        Apply keymap overrides from config and seed the status line.
        """
        self.set_keymap(resolve_keymap_overrides(self.config))
        self.vault_name = self.config.get("default_vault")
        self._refresh_problems()
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
        status.update_status(
            self.vault_name,
            self.note_title,
            self.word_count,
            problems=self._problem_count,
        )

    def _refresh_problems(self) -> None:
        """
        Recompute the report-only "problems" count (spec §19 D1).

        Runs :func:`hoppus.integrity.audit_vault` over the active
        vault's index and re-renders the status line with the count.
        Report-only: never prompts. Fully guarded — no active vault or
        a failed index build/audit resets the count to 0, never raises.
        """
        count = 0
        try:
            vault_root = self._active_vault_path()
            if vault_root.is_dir():
                # Reuse the cached index when it matches, but don't
                # populate the cache here: the audit is a passive
                # report and must not change when panes lazily index.
                if self._index is not None and self._index_root == vault_root:
                    index = self._index
                else:
                    index = Index.build(vault_root)
                count = integrity.audit_vault(index).count
        except Exception:
            count = 0
        self._problem_count = count
        self._refresh_status_line()

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
        root = vault_root if vault_root is not None else preview.vault_root
        if root is not None and root.is_dir():
            self.query_one("#backlinks-pane", BacklinksPane).show_backlinks(
                path, self._active_index(root), root
            )
        self._refresh_problems()
        # D1 gate: opening a note is a report-only trigger by default —
        # the indicator above is the only surface. Only the escape hatch
        # (link_integrity.prompt_on = "always") may prompt here.
        if integrity.should_prompt_on(self.config, "open"):
            self.run_worker(self._run_integrity_pass(path), exclusive=False)

    def _active_index(self, vault_root: Path) -> Index:
        """
        Return the cached index for the active vault, building it once.

        Rebuilds when ``vault_root`` differs from the cached root;
        :meth:`switch_vault` invalidates the cache on vault change.

        Args:
            vault_root: The vault root to index.
        """
        if self._index is None or self._index_root != vault_root:
            self._index = Index.build(vault_root)
            self._index_root = vault_root
        return self._index

    def action_unlinked_mentions(self) -> None:
        """
        Compute and show unlinked mentions for the active note (HOPPUS-33).

        On-demand only (spec §8): searches the vault for plain-text
        occurrences of the active note's title/aliases and appends the
        results to the backlinks pane. Converting a mention into a real
        link arrives with the editing stories, so for now this is
        display + navigation only.
        """
        note_path = self.preview.note_path
        if note_path is None:
            self.notify("No active note", severity="information", timeout=3)
            return
        vault_root = self.preview.vault_root
        if vault_root is None or not vault_root.is_dir():
            self.notify("No open vault", severity="information", timeout=3)
            return
        index = self._active_index(vault_root)
        note = index.notes_by_path.get(Path(note_path))
        if note is None:
            self.notify(
                "Active note is not in the index", severity="warning", timeout=3
            )
            return
        mentions = find_unlinked_mentions(
            note, index, read_text=lambda path: path.read_text(encoding="utf-8")
        )
        self.query_one("#backlinks-pane", BacklinksPane).show_unlinked_mentions(
            mentions, vault_root
        )
        if mentions:
            self.notify(
                "Convert-to-link arrives with editing (a later story)",
                severity="information",
                timeout=3,
            )

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
            targets.append((right, self.query_one("#backlinks-pane")))
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

    def _suppress_self_write(self, *paths: Path) -> None:
        """
        Register hoppus-originated writes with the live watcher, if any
        (spec D5, HOPPUS-38).

        No-ops when no watcher is running, and never raises: a failed
        suppression must not break the write it was guarding.

        :param paths: Every path the app's own write touches.
        """
        if self._watcher is None:
            return
        try:
            self._watcher.suppress_many(paths)
        except Exception:
            self.log.error(f"Failed to suppress self-write for {paths}")

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
        """
        Open the in-TUI search screen (spec §9.8, HOPPUS-44).

        Live fuzzy search over titles and content with targeted
        operators; Enter opens the chosen note in the preview.
        """
        vault_root = self._active_vault_path()
        index = self._active_index(vault_root)
        backend = str(self.config.get("search", {}).get("content_backend", "auto"))

        async def handle_result(result: SearchResult | None) -> None:
            if result is None:
                return
            await self.open_note(result.path, vault_root=vault_root)

        self.push_screen(
            SearchScreen(index, vault_root, backend=backend), handle_result
        )

    def action_open_editor(self) -> None:
        """
        Open the active note in ``$EDITOR`` (spec §9.6, HOPPUS-39).

        Resolves the editor argv from ``$VISUAL``/``$EDITOR`` (fallback
        ``nvim``), suspends the TUI's alternate screen, and runs the
        editor as a subprocess. On return, a worker runs the
        link-integrity pass (spec §7.3, D1: editor-return only), then
        reindexes and refreshes the preview. Failures notify — the app
        never crashes over a broken editor setup.
        """
        path = self._pending_editor_path
        self._pending_editor_path = None
        if path is None and self.preview.note_path is not None:
            path = Path(self.preview.note_path)
        if path is None:
            self.notify("No active note", severity="information", timeout=3)
            return
        argv = integrity.resolve_editor_command(os.environ)
        try:
            with self.suspend():
                subprocess.run([*argv, str(path)])
        except Exception as error:
            self.notify(f"Editor failed: {error}", severity="error", timeout=5)
            return
        self.run_worker(self._after_editor(path), exclusive=False)

    async def _after_editor(self, path: Path) -> None:
        """
        Post-editor pipeline: integrity pass, reindex, preview refresh.

        Runs as a Textual worker so the integrity pass can await modal
        results via ``push_screen_wait``.

        :param path: The note that was just edited.
        """
        await self._run_integrity_pass(path)
        vault_root = self._active_vault_path()
        if vault_root.is_dir():
            self._index = Index.build(vault_root)
            self._index_root = vault_root
            await self.open_note(path, vault_root=vault_root)

    async def _prompt_unresolved(
        self, unresolved: integrity.UnresolvedWikilink
    ) -> integrity.Decision:
        """
        Push one §7.3 prompt and await the user's decision.

        Factored out so headless tests can stub the modal loop and
        drive :meth:`_run_integrity_pass` with canned decisions.

        :param unresolved: The unresolved occurrence to prompt about.
        :returns: The user's decision (a dismissed modal counts as
            skip).
        """
        result = await self.push_screen_wait(LinkIntegrityModal(unresolved))
        return result if result is not None else ("skip", None)

    async def _run_integrity_pass(self, path: Path) -> None:
        """
        Run the link-integrity pass on an edited note (spec §7.3, D1).

        Reads the file, rebuilds the index, and prompts sequentially —
        one modal per unresolved wikilink, in document order. Decisions
        are collected first and applied by
        :func:`hoppus.integrity.apply_integrity_decisions`, which
        rewrites corrections in reverse offset order (so every recorded
        span stays valid) and bootstraps created notes at the vault
        root with the prose untouched. The file is written back only
        when its text changed, and the index is rebuilt afterwards.

        :param path: The note to check.
        """
        if not self.config.get("link_integrity", {}).get(
            "enforce_no_dangling_wikilinks", True
        ):
            return
        vault_root = self._active_vault_path()
        if not vault_root.is_dir() or not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        index = Index.build(vault_root)
        self._index = index
        self._index_root = vault_root
        unresolved = integrity.find_unresolved_wikilinks(text, index, path)
        if not unresolved:
            return

        decisions: list[integrity.Decision] = []
        for occurrence in unresolved:
            decisions.append(await self._prompt_unresolved(occurrence))

        resolver = Resolver(
            list(index.notes_by_path.values()),
            vault_root,
            attachments=index._attachments,
        )
        try:
            new_text, created = integrity.apply_integrity_decisions(
                text, unresolved, decisions, vault_root, resolver
            )
        except (ValueError, FileExistsError) as error:
            self.notify(f"Link repair failed: {error}", severity="error", timeout=5)
            return
        if new_text != text:
            self._suppress_self_write(path)
            path.write_text(new_text, encoding="utf-8")
        if created:
            self._suppress_self_write(*created)
        if created or new_text != text:
            self._index = Index.build(vault_root)
            self._index_root = vault_root

    def action_open_system(self) -> None:
        """Open in system app (later story)."""
        self._not_implemented("Open in system app")

    def action_open_browser(self) -> None:
        """Open in browser (later story)."""
        self._not_implemented("Open in browser")

    def action_link_note(self) -> None:
        """
        Open the ``## Related`` link picker (spec §7.2, HOPPUS-41).

        Runs as a worker so the picker modal can be awaited via
        ``push_screen_wait``; the append/dedupe/bootstrap logic lives
        in :mod:`hoppus.related` and :meth:`_link_note_flow`.
        """
        if self.preview.note_path is None:
            self.notify("No active note", severity="information", timeout=3)
            return
        self.run_worker(self._link_note_flow(), exclusive=False)

    async def _pick_related_target(
        self, index: Index, vault_root: Path
    ) -> RelatedChoice | None:
        """
        Push the related picker and await the user's choice.

        Factored out so headless tests can stub the modal and drive
        :meth:`_link_note_flow` with canned choices.

        :param index: The active vault index.
        :param vault_root: The active vault root.
        :returns: The choice, or None on cancel.
        """
        return await self.push_screen_wait(RelatedPickerModal(index, vault_root))

    async def _link_note_flow(self) -> None:
        """
        The ``## Related`` authoring flow (spec §7.2, HOPPUS-41).

        Picks a target via the modal, bootstraps ``<name>.md`` at the
        vault root for a new name (so the section never gains a
        dangling link), appends ``- [[Target]]`` under the configured
        heading via :func:`hoppus.related.add_related_link`, and — when
        the text actually changed — suppresses the self-write,
        rewrites the note, reindexes, and refreshes the preview. A
        dedupe hit notifies and skips the write.
        """
        note_path = self.preview.note_path
        if note_path is None:
            self.notify("No active note", severity="information", timeout=3)
            return
        path = Path(note_path)
        vault_root = self._active_vault_path()
        if not vault_root.is_dir() or not path.is_file():
            self.notify("No open vault", severity="information", timeout=3)
            return
        choice = await self._pick_related_target(
            self._active_index(vault_root), vault_root
        )
        if choice is None:
            return
        heading = str(
            self.config.get("link_integrity", {}).get("related_heading", "## Related")
        )
        created: Path | None = None
        if choice.new_name is not None:
            target = choice.new_name.strip()
            try:
                self._suppress_self_write(vault_root / f"{target}.md")
                created = fileops.create_note(vault_root, target)
            except FileExistsError:
                # The note appeared since the picker was built; linking
                # to it is still safe, so just proceed.
                pass
            except ValueError as error:
                self.notify(f"Link to…: {error}", severity="error", timeout=5)
                return
        elif choice.note is not None:
            target = choice.note.title
        else:
            return
        try:
            text = path.read_text(encoding="utf-8")
            new_text = related.add_related_link(text, target, heading=heading)
            if new_text == text:
                self.notify(
                    f"Already linked: {target}", severity="information", timeout=3
                )
            else:
                self._suppress_self_write(path)
                path.write_text(new_text, encoding="utf-8")
        except OSError as error:
            self.notify(f"Link to…: {error}", severity="error", timeout=5)
            return
        if created is not None or new_text != text:
            self._index = Index.build(vault_root)
            self._index_root = vault_root
            await self.open_note(path, vault_root=vault_root)
        if new_text != text:
            self.notify(f"Linked to {target}", severity="information", timeout=3)

    def action_toggle_graph(self) -> None:
        """
        Toggle the local graph view for the active note (spec §9.7,
        HOPPUS-48).

        Pushes a :class:`GraphScreen` centered on the active note; when
        the screen is already open, ``g`` closes it. Choosing a node
        jumps to it in the preview.
        """
        if isinstance(self.screen, GraphScreen):
            self.screen.dismiss(None)
            return
        note_path = self.preview.note_path
        if note_path is None:
            self.notify("No active note", severity="information", timeout=3)
            return
        vault_root = self.preview.vault_root or self._active_vault_path()
        if vault_root is None or not vault_root.is_dir():
            self.notify("No open vault", severity="information", timeout=3)
            return
        graph_config = self.config.get("graph", {})
        index = self._active_index(vault_root)

        async def handle_result(path: Path | None) -> None:
            if path is None:
                return
            await self.open_note(path, vault_root=vault_root)

        self.push_screen(
            GraphScreen(
                index,
                Path(note_path),
                initial_radius=int(graph_config.get("default_degrees", 2)),
                max_degrees=int(graph_config.get("max_degrees", 5)),
                max_nodes=int(graph_config.get("max_nodes", 60)),
                exclude_hub_threshold=graph_config.get("exclude_hub_threshold"),
                vault_root=vault_root,
            ),
            handle_result,
        )

    def action_graph_radius_up(self) -> None:
        """
        Increase the graph hop radius (spec §9.16 ``+``).

        Delegates to the open :class:`GraphScreen`; a no-op notify when
        the graph view is not open (the ``+`` key does its real work as
        a screen binding while the graph is showing).
        """
        if isinstance(self.screen, GraphScreen):
            self.screen.action_radius_up()
            return
        self.notify("Graph radius: open the graph view first (g)", timeout=3)

    def action_graph_radius_down(self) -> None:
        """
        Decrease the graph hop radius (spec §9.16 ``-``).

        Delegates to the open :class:`GraphScreen`; a no-op notify when
        the graph view is not open.
        """
        if isinstance(self.screen, GraphScreen):
            self.screen.action_radius_down()
            return
        self.notify("Graph radius: open the graph view first (g)", timeout=3)

    def action_daily_note(self) -> None:
        """Daily note (later story)."""
        self._not_implemented("Daily note")

    def action_new_note(self) -> None:
        """
        Create a new note via the explorer's flow (spec §9.2, HOPPUS-34).

        Delegates to the File Explorer pane, which prompts for a name
        and creates the note at the vault root (GTD, spec §5.2).
        """
        self.query_one("#explorer-pane", ExplorerPane).action_new_note()

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
        """
        Rebuild the active vault's index (spec §9.16 ``r``, HOPPUS-37).

        This is the manual fallback of spec §8's graceful degradation:
        live watching via ``hoppus.index.watcher.VaultWatcher`` is
        deliberately NOT auto-started by the app in this story (a
        background observer thread risks flakiness in headless tests);
        the ``r`` keybind rebuilds on demand instead. Invalidates the
        cached index, rebuilds it, and refreshes the open note's
        backlinks pane.
        """
        vault_root = self._active_vault_path()
        if not vault_root.is_dir():
            self.notify("Reindex: no open vault", severity="information", timeout=3)
            return
        self._index = Index.build(vault_root)
        self._index_root = vault_root
        note_path = self.preview.note_path
        if note_path is not None:
            self.query_one("#backlinks-pane", BacklinksPane).show_backlinks(
                Path(note_path), self._index, vault_root
            )
        self._refresh_problems()
        self.notify("Reindexed", severity="information", timeout=3)

    # -- Vault switching -------------------------------------------------------

    def action_vault_switcher(self) -> None:
        """
        Open the vault switcher: pick the active vault (spec §9.15).

        Discovers vaults under the Vaults Root and pushes the modal; the
        chosen name (if any) routes through :meth:`switch_vault`.
        """
        try:
            vaults = discover_vaults(self.vaults_root)
        except (FileNotFoundError, NotADirectoryError):
            vaults = []
        if not vaults:
            self.notify(
                f"No vaults found under {self.vaults_root}",
                severity="information",
                timeout=3,
            )
            return

        async def handle_result(name: str | None) -> None:
            if name is None:
                return
            await self.switch_vault(name)

        names = [vault.name for vault in vaults]
        self.push_screen(
            VaultSwitcherModal(names, current=self.vault_name), handle_result
        )

    async def switch_vault(self, name: str) -> None:
        """
        Make the named vault active and refresh every pane (spec §9.15).

        Updates the status line via the ``vault_name`` reactive, re-roots
        the File Explorer tree, and resets the preview — dropping its
        cached index so link resolution lazily re-indexes the new vault.

        Args:
            name: Name of a vault under the Vaults Root.
        """
        vault_path = self.vaults_root / name
        if not vault_path.is_dir():
            self.notify(f"Vault not found: {name}", severity="warning", timeout=3)
            return
        self.vault_name = name

        explorer = self.query_one("#explorer-pane", ExplorerPane)
        explorer.path = vault_path
        await explorer.reload()

        preview = self.preview
        await preview.clear()
        preview.vault_root = vault_path
        self._index = None
        self._index_root = None
        self.query_one("#backlinks-pane", BacklinksPane).clear()
        self.note_title = None
        self.word_count = None
        self._refresh_problems()
