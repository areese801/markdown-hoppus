"""
Local graph view: box-drawing render of the N-hop neighborhood with live hop
filtering (spec §9.7, HOPPUS-48).

The module is split into a pure, unit-testable render layer and a thin
interactive screen:

- :func:`render_graph_rows` / :func:`render_graph_lines` turn a
  :class:`hoppus.graph.LocalGraph` into ``tree``-style Unicode lines
  (``├──`` / ``└──`` / ``│``), depth-first from the root, guarding cycles
  with a visit-set so a revisited node renders once as a ``↩``-annotated
  leaf. Edge direction is annotated with ``→`` / ``←`` / ``↔`` relative to
  the parent row. The synthetic "+N more" budget node renders as a dimmed
  final leaf. No Textual objects here — just strings.
- :class:`GraphScreen` is a modal that rebuilds the graph via
  :func:`hoppus.graph.build_local_graph` whenever the hop radius changes
  (``+`` / ``-``, clamped to ``[1, max_degrees]``) and dismisses with the
  chosen note's path (or ``None`` on Escape).
"""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from hoppus.graph import GraphEdge, LocalGraph, build_local_graph
from hoppus.index.indexer import Index

#: (rendered line, note path) — the path is None for unselectable rows
#: (the synthetic "+N more" placeholder).
GraphRow = tuple[str, Path | None]


def _direction_symbol(edge: GraphEdge, parent: Path) -> str:
    """
    Return the arrow annotating an edge, oriented from the parent row.

    :param edge: The edge between the parent and the child being rendered.
    :param parent: Path of the node the child hangs under in the tree.
    :returns: ``→`` when the parent links to the child, ``←`` when the
        child links to the parent, ``↔`` when both directions exist.
    """
    if edge.direction == "both":
        return "↔"
    if edge.source == parent:
        return "→" if edge.direction == "outbound" else "←"
    return "←" if edge.direction == "outbound" else "→"


def render_graph_rows(
    graph: LocalGraph, *, root_title: str | None = None
) -> list[GraphRow]:
    """
    Render a local graph as ``tree``-style rows with selectable paths.

    Walks depth-first from the root using ``graph.neighbors(...)`` for
    adjacency. A visit-set guards cycles: a node reached again through
    another route (or a back-edge) renders once more as a leaf annotated
    with ``↩`` and is never re-expanded, so rendering always terminates.
    Each child row carries a direction arrow (see
    :func:`_direction_symbol`); the synthetic "+N more" placeholder, if
    present, renders as a dimmed final leaf under the root.

    :param graph: The local graph to render.
    :param root_title: Optional title override for the root row; defaults
        to the root node's own title.
    :returns: Rows of (Rich-markup line, note path); the path is ``None``
        for the placeholder row.
    """
    nodes_by_path = {node.path: node for node in graph.nodes if not node.truncated}
    truncated = next((node for node in graph.nodes if node.truncated), None)
    edge_by_pair: dict[frozenset[Path], GraphEdge] = {
        frozenset((edge.source, edge.target)): edge for edge in graph.edges
    }

    root_node = nodes_by_path.get(graph.root)
    title = root_title or (root_node.title if root_node is not None else "(missing)")
    rows: list[GraphRow] = [(f"[b]{title}[/b]", graph.root)]
    visited: set[Path] = {graph.root}

    def walk(path: Path, prefix: str, parent: Path | None) -> None:
        """
        Emit rows for the children of ``path``, recursing into new nodes.

        :param path: The node whose neighbors are being rendered.
        :param prefix: Accumulated indentation for this depth.
        :param parent: The node ``path`` was reached from (skipped in the
            child list so the tree does not echo every parent back).
        """
        children = [
            neighbor
            for neighbor in graph.neighbors(path)
            if neighbor != parent and neighbor in nodes_by_path
        ]
        extras = 1 if path == graph.root and truncated is not None else 0
        total = len(children) + extras
        for position, child in enumerate(children):
            last = position == total - 1
            connector = "└── " if last else "├── "
            edge = edge_by_pair.get(frozenset((path, child)))
            arrow = f"{_direction_symbol(edge, path)} " if edge is not None else ""
            child_title = nodes_by_path[child].title
            if child in visited:
                rows.append((f"{prefix}{connector}{arrow}{child_title} ↩", child))
                continue
            visited.add(child)
            rows.append((f"{prefix}{connector}{arrow}{child_title}", child))
            walk(child, prefix + ("    " if last else "│   "), path)
        if extras and truncated is not None:
            rows.append((f"{prefix}└── [dim]{truncated.title}[/dim]", None))

    walk(graph.root, "", None)
    return rows


def render_graph_lines(
    graph: LocalGraph, *, root_title: str | None = None
) -> list[str]:
    """
    Render a local graph as ``tree``-style Unicode lines (spec §9.7).

    Pure string convenience over :func:`render_graph_rows` — the unit
    under test for the render layer.

    :param graph: The local graph to render.
    :param root_title: Optional title override for the root row.
    :returns: One Rich-markup string per rendered node.
    """
    return [line for line, _path in render_graph_rows(graph, root_title=root_title)]


class GraphScreen(ModalScreen[Path | None]):
    """
    Interactive local graph view (spec §9.7): live hop radius + jump.

    Rebuilds the graph on every radius change (``+`` / ``-``, clamped to
    ``[1, max_degrees]``), renders it as a selectable Unicode tree, and
    dismisses with the chosen note's path — or ``None`` on Escape. All
    graph logic lives in :mod:`hoppus.graph`; the screen only builds,
    renders, and maps selections back to paths.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        # The modal blocks app-level bindings, so `g` toggles the view
        # closed via a screen binding (spec §9.16).
        Binding("g", "cancel", "Close", show=False),
        Binding("plus,+", "radius_up", "Hops +"),
        Binding("minus,-", "radius_down", "Hops -"),
    ]

    CSS = """
    GraphScreen {
        align: center middle;
    }
    #graph-panel {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 85%;
        border: round $primary;
        background: $surface;
    }
    #graph-header {
        padding: 0 1;
        text-style: bold;
    }
    #graph-list {
        height: auto;
        max-height: 30;
    }
    """

    def __init__(
        self,
        index: Index,
        root: Path,
        *,
        initial_radius: int = 2,
        max_degrees: int = 5,
        max_nodes: int = 60,
        exclude_hub_threshold: int | None = None,
        vault_root: Path | None = None,
    ) -> None:
        """
        Args:
            index: A built vault index to traverse.
            root: Path of the active note the graph is centered on.
            initial_radius: Starting hop radius (clamped to
                ``[1, max_degrees]``).
            max_degrees: Upper bound for the live radius (config
                ``graph.max_degrees``).
            max_nodes: Node budget forwarded to the traversal.
            exclude_hub_threshold: Hub-exclusion threshold forwarded to
                the traversal, or ``None`` to disable.
            vault_root: The active vault root (kept for callers; the
                screen itself performs no I/O).
        """
        super().__init__()
        self._index = index
        self._root = root
        self._max_degrees = max(1, max_degrees)
        self._radius = max(1, min(initial_radius, self._max_degrees))
        self._max_nodes = max_nodes
        self._exclude_hub_threshold = exclude_hub_threshold
        self._vault_root = vault_root
        self._row_paths: list[Path | None] = []

    @property
    def radius(self) -> int:
        """The current hop radius."""
        return self._radius

    def compose(self) -> ComposeResult:
        """
        Build the header line and the selectable tree list.
        """
        with Vertical(id="graph-panel"):
            yield Static(id="graph-header")
            yield OptionList(id="graph-list")

    def on_mount(self) -> None:
        """
        Render the initial graph and focus the tree list.
        """
        self._rebuild()
        self.query_one("#graph-list", OptionList).focus()

    # -- Build + render --------------------------------------------------------

    def _rebuild(self) -> None:
        """
        Rebuild the graph at the current radius and re-render the tree.

        Exception-guarded: a failed build or render clears the list and
        notifies instead of crashing the screen.
        """
        rows: list[GraphRow] = []
        root_title = self._root.stem
        try:
            graph = build_local_graph(
                self._index,
                self._root,
                degrees=self._radius,
                max_nodes=self._max_nodes,
                exclude_hub_threshold=self._exclude_hub_threshold,
            )
            rows = render_graph_rows(graph)
            root_node = next(
                (node for node in graph.nodes if node.path == graph.root), None
            )
            if root_node is not None:
                root_title = root_node.title
        except Exception as error:
            self.notify(f"Graph view failed: {error}", severity="error", timeout=5)
            rows = []
        self._row_paths = [path for _line, path in rows]
        options = self.query_one("#graph-list", OptionList)
        options.clear_options()
        options.add_options(Option(line, disabled=path is None) for line, path in rows)
        if rows:
            options.highlighted = 0
        self.query_one("#graph-header", Static).update(
            f"Graph: {root_title} · radius {self._radius}/{self._max_degrees}"
        )

    # -- Radius ----------------------------------------------------------------

    def action_radius_up(self) -> None:
        """
        Increase the hop radius (clamped to ``max_degrees``) and re-render.
        """
        if self._radius >= self._max_degrees:
            return
        self._radius += 1
        self._rebuild()

    def action_radius_down(self) -> None:
        """
        Decrease the hop radius (minimum 1) and re-render.
        """
        if self._radius <= 1:
            return
        self._radius -= 1
        self._rebuild()

    # -- Selection ---------------------------------------------------------------

    def select_row(self, row_index: int) -> None:
        """
        Dismiss with the note at a rendered row, skipping the placeholder.

        :param row_index: Index into the rendered rows.
        """
        if not 0 <= row_index < len(self._row_paths):
            return
        path = self._row_paths[row_index]
        if path is None:
            return
        self.dismiss(path)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Enter on (or a click of) a row jumps to that note.
        """
        event.stop()
        self.select_row(event.option_index)

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)


__all__ = [
    "GraphRow",
    "GraphScreen",
    "render_graph_lines",
    "render_graph_rows",
]
