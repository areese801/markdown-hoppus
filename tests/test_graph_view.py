"""
Tests for the pure graph render layer (spec §9.7, HOPPUS-48).

Exercises :func:`hoppus.tui.graph_view.render_graph_lines` (and its row
variant) on hand-built ``LocalGraph`` values: root-first ordering,
box-drawing connectors, direction arrows, the dimmed "+N more" truncated
leaf, and cycle termination via the visit-set.
"""

from pathlib import Path

from hoppus.graph import TRUNCATED_SENTINEL, GraphEdge, GraphNode, LocalGraph
from hoppus.tui.graph_view import render_graph_lines, render_graph_rows

A = Path("A.md")
B = Path("B.md")
C = Path("C.md")
D = Path("D.md")


def _node(path: Path, title: str, degree: int) -> GraphNode:
    """
    Build a real (non-truncated) graph node.

    :param path: Vault path of the node.
    :param title: Note title.
    :param degree: Hop distance from the root.
    :returns: The node.
    """
    return GraphNode(
        path=path, title=title, degree=degree, truncated=False, link_count=1
    )


def _graph(*, truncated_count: int = 0, cycle: bool = False) -> LocalGraph:
    """
    Build a small hand-made local graph: A → B, C ← A, B ↔ D.

    :param truncated_count: When > 0, append the synthetic "+N more" node.
    :param cycle: When True, add a back-edge D → A (a cycle to the root).
    :returns: The local graph.
    """
    nodes: list[GraphNode] = [
        _node(A, "Alpha", 0),
        _node(B, "Beta", 1),
        _node(C, "Gamma", 1),
        _node(D, "Delta", 2),
    ]
    edges: list[GraphEdge] = [
        GraphEdge(source=A, target=B, direction="outbound"),
        GraphEdge(source=A, target=C, direction="backlink"),
        GraphEdge(source=B, target=D, direction="both"),
    ]
    if cycle:
        edges.append(GraphEdge(source=D, target=A, direction="outbound"))
    if truncated_count:
        nodes.append(
            GraphNode(
                path=TRUNCATED_SENTINEL,
                title=f"+{truncated_count} more",
                degree=2,
                truncated=True,
                link_count=0,
            )
        )
    return LocalGraph(
        root=A,
        nodes=tuple(nodes),
        edges=tuple(edges),
        truncated_count=truncated_count,
        radius=2,
    )


def test_root_renders_first_with_children_indented() -> None:
    """
    The root is line one; neighbors use box-drawing connectors.
    """
    lines = render_graph_lines(_graph())
    assert "Alpha" in lines[0]
    assert len(lines) == 4
    assert lines[1].startswith("├── ")
    assert any(line.startswith("│   └── ") for line in lines)
    assert lines[-1].startswith("└── ")


def test_direction_arrows_annotate_edges() -> None:
    """
    Outbound, backlink, and both edges get →, ←, and ↔ arrows.
    """
    lines = render_graph_lines(_graph())
    beta = next(line for line in lines if "Beta" in line)
    gamma = next(line for line in lines if "Gamma" in line)
    delta = next(line for line in lines if "Delta" in line)
    assert "→" in beta
    assert "←" in gamma
    assert "↔" in delta


def test_truncated_node_renders_dimmed_and_last() -> None:
    """
    The "+N more" placeholder is a dimmed final leaf with no path.
    """
    rows = render_graph_rows(_graph(truncated_count=3))
    line, path = rows[-1]
    assert "+3 more" in line
    assert "[dim]" in line
    assert path is None
    assert line.lstrip().startswith("└── ") or line.startswith("└── ")


def test_cycle_terminates_and_annotates_revisit() -> None:
    """
    A back-edge to the root renders once as a ↩ leaf, never looping.
    """
    # The back-edge also makes D adjacent to A, so both the root (via
    # D's children) and D (as A's third child) render as revisit leaves.
    lines = render_graph_lines(_graph(cycle=True))
    assert len(lines) == 6
    revisits = [line for line in lines if "↩" in line]
    assert len(revisits) == 2
    assert any("Alpha" in line for line in revisits)


def test_root_title_override() -> None:
    """
    ``root_title`` replaces the root node's own title.
    """
    lines = render_graph_lines(_graph(), root_title="Custom Root")
    assert "Custom Root" in lines[0]
    assert "Alpha" not in lines[0]


def test_rows_map_real_nodes_to_paths() -> None:
    """
    Every real rendered row carries its note path for selection.
    """
    rows = render_graph_rows(_graph())
    paths = [path for _line, path in rows]
    assert paths == [A, B, D, C]
