"""
N-hop local subgraph traversal with node budget and cycle handling (spec §9.7).

Builds the *local* graph around a root note by breadth-first search over
the union of outbound resolved links and backlinks, out to a configurable
number of hops. The BFS is degree-agnostic: raising the MVP hop cap is a
one-constant change (``HARD_CAP`` — spec §17).

Four guardrails keep the view finite and useful:

1. **Degree cap** — the requested ``degrees`` is clamped to
   ``[0, HARD_CAP]``; degree 0 is the root alone.
2. **Cycle handling** — a visit-set keyed by path means a node discovered
   again via another route is never re-expanded and keeps its minimum
   (first-discovered) degree; edges to already-visited nodes are still
   recorded, so cycles render as edges rather than infinite nodes.
3. **Hub exclusion** — when ``exclude_hub_threshold`` is set, a node whose
   total vault neighbor count exceeds the threshold is included as a node
   but *not expanded* (its neighbors are not traversed). The root is
   always expanded even if it is a hub, because the user explicitly asked
   for that note's neighborhood.
4. **Node budget** — at most ``max_nodes`` real nodes are kept, added in
   BFS order (degree first, then deterministic neighbor order); the
   remainder are dropped, counted in ``truncated_count``, and represented
   by a single synthetic placeholder node (``truncated=True``) so the UI
   can render a "+N more…" affordance.

This module is pure: it only reads the passed index and performs no I/O.
"""

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from hoppus.index.indexer import Index

#: Hard cap on traversal depth for MVP (spec §9.7). The BFS itself is
#: degree-agnostic, so raising this constant is the only change needed to
#: allow deeper traversals (spec §17).
HARD_CAP = 5

#: Sentinel path used for the synthetic "+N more" placeholder node. It is
#: not a real vault path and must never be resolved against the filesystem.
TRUNCATED_SENTINEL = Path("__truncated__")


@dataclass(frozen=True)
class GraphNode:
    """
    A node in a local graph.

    :param path: Vault path of the note. For the synthetic "+N more"
        placeholder (``truncated=True``) this is ``TRUNCATED_SENTINEL``,
        not a real file.
    :param title: Note title, or ``"+N more"`` for the placeholder.
    :param degree: Hop distance from the root (root = 0). For the
        placeholder, the degree at which truncation first occurred.
    :param truncated: True only for the single synthetic placeholder node
        that stands in for nodes dropped by the node budget.
    :param link_count: Total distinct neighbors of this node in the whole
        vault (outbound resolved targets plus backlink sources), used for
        hub display and exclusion. Zero for the placeholder.
    """

    path: Path
    title: str
    degree: int
    truncated: bool
    link_count: int


@dataclass(frozen=True)
class GraphEdge:
    """
    A directed-annotated edge between two included nodes.

    ``direction`` is ``"outbound"`` when ``source`` links to ``target``
    via an outbound link, ``"backlink"`` when ``target`` links to
    ``source``, and ``"both"`` when links exist in both directions
    between the pair. ``"both"`` takes precedence over either single
    direction. Each unordered pair of nodes yields at most one edge,
    oriented from the node that was expanded first during BFS.

    :param source: Path of the node the edge was discovered from.
    :param target: Path of the neighbor.
    :param direction: One of ``"outbound"``, ``"backlink"``, ``"both"``.
    """

    source: Path
    target: Path
    direction: str


@dataclass(frozen=True)
class LocalGraph:
    """
    The N-hop local subgraph around a root note.

    :param root: Path of the root note (always included, degree 0).
    :param nodes: All included nodes in BFS order; if the node budget
        dropped any nodes, the last entry is the single synthetic
        placeholder (``truncated=True``).
    :param edges: Edges among included *real* nodes only.
    :param truncated_count: Number of real nodes dropped by the budget.
    :param radius: Maximum degree among included real nodes.
    """

    root: Path
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    truncated_count: int
    radius: int

    def nodes_by_degree(self) -> dict[int, list[GraphNode]]:
        """
        Group nodes by degree, preserving the stable BFS node order.

        The synthetic placeholder node, if present, is included in the
        bucket for the degree at which truncation occurred.

        :returns: Mapping of degree to nodes at that degree.
        """
        grouped: dict[int, list[GraphNode]] = {}
        for node in self.nodes:
            grouped.setdefault(node.degree, []).append(node)
        return grouped

    def neighbors(self, path: Path) -> list[Path]:
        """
        Return the adjacency of ``path`` within this graph.

        Adjacency is derived from ``edges`` regardless of direction, in
        edge order, de-duplicated.

        :param path: Path of an included node.
        :returns: Paths of nodes sharing an edge with ``path``.
        """
        seen: set[Path] = set()
        result: list[Path] = []
        for edge in self.edges:
            other: Path | None = None
            if edge.source == path:
                other = edge.target
            elif edge.target == path:
                other = edge.source
            if other is not None and other not in seen:
                seen.add(other)
                result.append(other)
        return result


def _neighbor_set(index: Index, path: Path) -> set[Path]:
    """
    Compute a note's full vault neighbor set.

    Neighbors are the union of the note's outbound resolved link targets
    and its backlink sources, with self-loops dropped.

    :param index: The vault index.
    :param path: Path of the note.
    :returns: Distinct neighbor paths.
    """
    outbound = {
        link.resolved for link in index.links.get(path, []) if link.resolved is not None
    }
    neighbors = outbound | index.backlinks.get(path, set())
    neighbors.discard(path)
    return neighbors


def _title_for(index: Index, path: Path) -> str:
    """
    Return the title of a note, falling back to the filename stem.

    :param index: The vault index.
    :param path: Path of the note.
    :returns: The note title.
    """
    note = index.notes_by_path.get(path)
    return note.title if note is not None else path.stem


def _sorted_neighbors(index: Index, path: Path) -> list[Path]:
    """
    Return a note's neighbors in deterministic (title, path) order.

    :param index: The vault index.
    :param path: Path of the note.
    :returns: Sorted neighbor paths.
    """
    return sorted(
        _neighbor_set(index, path),
        key=lambda p: (_title_for(index, p), str(p)),
    )


def _edge_direction(index: Index, source: Path, target: Path) -> str:
    """
    Classify the link direction between two neighboring notes.

    :param index: The vault index.
    :param source: The node the edge is oriented from.
    :param target: The neighbor.
    :returns: ``"both"`` if links exist in both directions (precedence),
        else ``"outbound"`` if source links to target, else ``"backlink"``.
    """
    outbound = any(link.resolved == target for link in index.links.get(source, []))
    back = target in index.backlinks.get(source, set())
    if outbound and back:
        return "both"
    if outbound:
        return "outbound"
    return "backlink"


def build_local_graph(
    index: Index,
    root: Path,
    *,
    degrees: int = 2,
    max_nodes: int = 60,
    exclude_hub_threshold: int | None = None,
) -> LocalGraph:
    """
    Build the N-hop local graph around ``root`` (spec §9.7).

    Performs a breadth-first search from ``root`` over the union of
    outbound resolved links and backlinks, applying the four guardrails
    documented in the module docstring: degree clamping to ``HARD_CAP``,
    cycle handling via a visit-set, optional hub exclusion (the root is
    always expanded), and a node budget with a "+N more" placeholder.

    :param index: The vault index to traverse (read-only).
    :param root: Path of the root note; always included at degree 0.
    :param degrees: Requested hop radius; clamped to ``[0, HARD_CAP]``.
    :param max_nodes: Maximum number of real nodes to include (>= 1; the
        root is always included).
    :param exclude_hub_threshold: If set, nodes with more than this many
        vault neighbors are included but not expanded.
    :returns: The resulting ``LocalGraph``.
    """
    degrees = max(0, min(degrees, HARD_CAP))

    visited: dict[Path, int] = {root: 0}
    order: list[Path] = [root]
    edges: list[GraphEdge] = []
    edge_pairs: set[frozenset[Path]] = set()
    queue: deque[Path] = deque([root])

    while queue:
        current = queue.popleft()
        current_degree = visited[current]
        if current_degree >= degrees:
            continue
        neighbor_count = len(_neighbor_set(index, current))
        is_hub = (
            exclude_hub_threshold is not None and neighbor_count > exclude_hub_threshold
        )
        if is_hub and current != root:
            continue
        for neighbor in _sorted_neighbors(index, current):
            if neighbor not in visited:
                visited[neighbor] = current_degree + 1
                order.append(neighbor)
                queue.append(neighbor)
            pair = frozenset((current, neighbor))
            if pair not in edge_pairs:
                edge_pairs.add(pair)
                edges.append(
                    GraphEdge(
                        source=current,
                        target=neighbor,
                        direction=_edge_direction(index, current, neighbor),
                    )
                )

    included_paths = order[:max_nodes]
    dropped_paths = order[max_nodes:]
    truncated_count = len(dropped_paths)
    included_set = set(included_paths)

    nodes = [
        GraphNode(
            path=path,
            title=_title_for(index, path),
            degree=visited[path],
            truncated=False,
            link_count=len(_neighbor_set(index, path)),
        )
        for path in included_paths
    ]
    radius = max(node.degree for node in nodes)

    if truncated_count:
        nodes.append(
            GraphNode(
                path=TRUNCATED_SENTINEL,
                title=f"+{truncated_count} more",
                degree=visited[dropped_paths[0]],
                truncated=True,
                link_count=0,
            )
        )

    kept_edges = tuple(
        edge
        for edge in edges
        if edge.source in included_set and edge.target in included_set
    )
    return LocalGraph(
        root=root,
        nodes=tuple(nodes),
        edges=kept_edges,
        truncated_count=truncated_count,
        radius=radius,
    )
