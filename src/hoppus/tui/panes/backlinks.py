"""
Backlinks pane: linked and unlinked mentions for the active note
(spec §9.1, HOPPUS-32, HOPPUS-33, HOPPUS-96).

An ``OptionList`` in the right sidebar listing the notes that link TO the
active note, populated from the index's ``backlinks`` map and sorted
case-insensitively by source title. Each row carries a compact
"degrees away" suffix (``·1`` = direct mention); second-degree
backlink sources (notes linking to a direct backlink, ``·2``) are
appended below the direct set, with hop distances taken from the same
local-graph traversal that powers the graph view (HOPPUS-96). Pressing
``g`` in the pane opens that graph view centered on the active note.
On demand, an "Unlinked mentions" section is appended below the
backlinks (see ``hoppus.index.mentions``). Selecting an entry in any
section navigates to that source note through the app's ``open_note``
funnel.
"""

from pathlib import Path

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from hoppus.graph import build_local_graph
from hoppus.index.indexer import Index
from hoppus.index.mentions import UnlinkedMention

_EMPTY_LABEL = "No backlinks"
_UNLINKED_HEADER = "── Unlinked mentions ──"
_NO_UNLINKED_LABEL = "No unlinked mentions"

#: Node budget for the degree-annotation graph build (HOPPUS-96):
#: generous enough that second-degree sources of well-connected notes
#: keep their hop distance, without unbounded work on huge vaults.
_DEGREE_MAX_NODES = 200


class BacklinksPane(OptionList):
    """
    Right-sidebar list of linked mentions for the active note.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        # ``g`` opens the graph view here instead of jumping to the
        # first row (HOPPUS-96): the old widget-level ``g`` shadowed
        # the app's graph toggle whenever this pane had focus, which
        # made the graph view undiscoverable from the backlinks list.
        Binding("g", "open_graph", "Graph", show=False),
        Binding("home", "first", "First", show=False),
        Binding("G", "last", "Last", show=False),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        """
        Args:
            id: Optional widget id.
        """
        super().__init__(id=id)
        self._vault_root: Path | None = None
        # Option id → source note path; titles can collide across folders,
        # so selection maps back through ids, never the visible title.
        self._paths: dict[str, Path] = {}
        # Option ids of the appended unlinked-mentions section (HOPPUS-33),
        # so re-running the action replaces the section instead of stacking.
        self._unlinked_ids: list[str] = []
        # True while a selection is being dispatched (HOPPUS-86): a
        # watcher-triggered refresh mid-selection must not clear the
        # options out from under Textual's OptionList machinery, so
        # refreshes arriving in that window are stashed in ``_pending``
        # and applied once the dispatch completes (last one wins).
        self._dispatching = False
        self._pending: tuple[Path, Index, Path] | None = None

    def on_mount(self) -> None:
        """
        Start in the empty (no active note) state.
        """
        if self.option_count == 0:
            self._show_empty()

    def show_backlinks(self, note_path: Path, index: Index, vault_root: Path) -> None:
        """
        Populate the pane with the notes linking to ``note_path``.

        Direct sources come from ``index.backlinks``; each row shows
        the source note's title (falling back to its file stem), sorted
        case-insensitively, with a ``·1`` hop-distance suffix
        (HOPPUS-96). Second-degree sources — notes that link to a
        direct backlink but are not themselves one hop from the active
        note — follow with a ``·2`` suffix. Hop distances are taken
        from :func:`hoppus.graph.build_local_graph` (the same traversal
        behind the graph view), so a candidate that is also a direct
        neighbor via an outbound link is correctly excluded from the
        second-degree section. Shows an empty state when there are no
        direct backlinks.

        While a selection is mid-dispatch the rebuild is deferred until
        the dispatch completes (HOPPUS-86): clearing the options under
        an in-flight ``OptionSelected`` crashes Textual's OptionList,
        and a watcher event can land exactly there.

        Args:
            note_path: Path of the active note.
            index: The active vault's index.
            vault_root: Vault root, stored for later navigation.
        """
        if self._dispatching:
            self._pending = (Path(note_path), index, Path(vault_root))
            return
        root = Path(note_path)
        self._vault_root = Path(vault_root)
        self._paths = {}
        self._unlinked_ids = []
        self.clear_options()

        def title_for(source: Path) -> str:
            note = index.notes_by_path.get(source)
            return note.title if note is not None else source.stem

        direct = sorted(
            index.backlinks.get(root, set()),
            key=lambda source: title_for(source).casefold(),
        )
        if not direct:
            self._show_empty()
            return
        degree_by_path: dict[Path, int] = {}
        try:
            graph = build_local_graph(
                index, root, degrees=2, max_nodes=_DEGREE_MAX_NODES
            )
            degree_by_path = {
                node.path: node.degree for node in graph.nodes if not node.truncated
            }
        except Exception:
            self.log.error(f"Degree annotation failed for {root}")
        seen: set[Path] = {root, *direct}
        second: set[Path] = set()
        for source in direct:
            for candidate in index.backlinks.get(source, set()):
                if candidate not in seen and degree_by_path.get(candidate) == 2:
                    seen.add(candidate)
                    second.add(candidate)
        entries = [(title_for(source), source, 1) for source in direct]
        entries.extend(
            (title_for(source), source, 2)
            for source in sorted(second, key=lambda s: title_for(s).casefold())
        )
        for position, (title, source, degree) in enumerate(entries):
            option_id = f"backlink-{position}"
            self._paths[option_id] = source
            self.add_option(Option(f"{title}  ·{degree}", id=option_id))

    def show_unlinked_mentions(
        self, mentions: list[UnlinkedMention], vault_root: Path
    ) -> None:
        """
        Append an "Unlinked mentions" section below the linked backlinks.

        The existing backlink rows are left untouched; a previously shown
        unlinked section is replaced. Each mention row shows the source
        title and its occurrence count, and maps to the source path in
        ``_paths`` so the shared selection handler navigates to it. With
        no mentions, a single disabled placeholder row is shown instead.

        Args:
            mentions: Unlinked mentions for the active note.
            vault_root: Vault root, stored for later navigation.
        """
        self._vault_root = Path(vault_root)
        for option_id in self._unlinked_ids:
            self.remove_option(option_id)
            self._paths.pop(option_id, None)
        self._unlinked_ids = []

        header_id = "unlinked-header"
        self.add_option(
            Option(f"[dim]{_UNLINKED_HEADER}[/dim]", id=header_id, disabled=True)
        )
        self._unlinked_ids.append(header_id)
        if not mentions:
            empty_id = "unlinked-empty"
            self.add_option(
                Option(f"[dim]{_NO_UNLINKED_LABEL}[/dim]", id=empty_id, disabled=True)
            )
            self._unlinked_ids.append(empty_id)
            return
        for position, mention in enumerate(mentions):
            option_id = f"unlinked-{position}"
            self._paths[option_id] = mention.source
            self._unlinked_ids.append(option_id)
            self.add_option(
                Option(f"{mention.title}  ·  ×{mention.count}", id=option_id)
            )

    def clear(self) -> None:
        """
        Reset to the empty state (vault switch / no active note).
        """
        self._vault_root = None
        self._paths = {}
        self._unlinked_ids = []
        self.clear_options()
        self._show_empty()

    def _show_empty(self) -> None:
        """
        Show the disabled 'No backlinks' placeholder row.
        """
        self.add_option(Option(f"[dim]{_EMPTY_LABEL}[/dim]", disabled=True))

    def action_open_graph(self) -> None:
        """
        Open the local graph view centered on the active note
        (HOPPUS-96, key ``g``).

        Delegates to the app's graph toggle so the view opens on the
        same note whose backlinks are showing; the app guards the
        no-active-note and no-vault cases.
        """
        toggle = getattr(self.app, "action_toggle_graph", None)
        if toggle is not None:
            toggle()

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """
        Navigate to the selected backlink source via the app's funnel.

        The pane is marked as dispatching for the duration so that a
        refresh arriving mid-navigation (e.g. from a live watcher
        event, HOPPUS-86) is deferred instead of clearing the options
        under this in-flight selection; the latest deferred refresh is
        applied afterwards.
        """
        event.stop()
        path = self._paths.get(event.option.id or "")
        if path is None:
            return
        open_note = getattr(self.app, "open_note", None)
        if open_note is None:
            return
        self._dispatching = True
        try:
            await open_note(path, vault_root=self._vault_root)
        finally:
            self._dispatching = False
            pending, self._pending = self._pending, None
            if pending is not None:
                self.show_backlinks(*pending)
