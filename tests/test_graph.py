"""
Tests for the local graph traversal engine (HOPPUS-47, spec §9.7).

All vaults are built as real ``.md`` files in temp dirs and indexed via
``Index.build``, so the tests exercise the real link/backlink resolution
rather than hand-built adjacency.
"""

from pathlib import Path

import pytest

from hoppus.graph import (
    HARD_CAP,
    TRUNCATED_SENTINEL,
    GraphNode,
    LocalGraph,
    build_local_graph,
)
from hoppus.index.indexer import Index


def _make_vault(tmp_path: Path, files: dict[str, str]) -> Index:
    """
    Write ``files`` (title -> body) as ``.md`` files and index the vault.

    :param tmp_path: Temp directory to use as the vault root.
    :param files: Mapping of note title to markdown body.
    :returns: The built index.
    """
    for title, body in files.items():
        (tmp_path / f"{title}.md").write_text(body, encoding="utf-8")
    return Index.build(tmp_path)


def _degrees(graph: LocalGraph) -> dict[str, int]:
    """
    Map real-node titles to their recorded degrees.

    :param graph: The graph under test.
    :returns: Title -> degree for non-placeholder nodes.
    """
    return {n.title: n.degree for n in graph.nodes if not n.truncated}


@pytest.fixture
def chain_index(tmp_path: Path) -> Index:
    """
    A linear chain vault: A -> B -> C -> D.
    """
    return _make_vault(
        tmp_path,
        {"A": "[[B]]", "B": "[[C]]", "C": "[[D]]", "D": "end"},
    )


class TestNHopCorrectness:
    """
    BFS reaches exactly the notes within N hops, with correct degrees.
    """

    def test_degrees_1(self, chain_index: Index) -> None:
        graph = build_local_graph(
            chain_index, chain_index.vault_root / "A.md", degrees=1
        )
        assert _degrees(graph) == {"A": 0, "B": 1}
        assert graph.radius == 1

    def test_degrees_2(self, chain_index: Index) -> None:
        graph = build_local_graph(
            chain_index, chain_index.vault_root / "A.md", degrees=2
        )
        assert _degrees(graph) == {"A": 0, "B": 1, "C": 2}

    def test_degrees_3(self, chain_index: Index) -> None:
        graph = build_local_graph(
            chain_index, chain_index.vault_root / "A.md", degrees=3
        )
        assert _degrees(graph) == {"A": 0, "B": 1, "C": 2, "D": 3}
        assert graph.radius == 3

    def test_backlinks_traversed(self, chain_index: Index) -> None:
        graph = build_local_graph(
            chain_index, chain_index.vault_root / "C.md", degrees=1
        )
        assert _degrees(graph) == {"C": 0, "B": 1, "D": 1}

    def test_degrees_zero_is_root_only(self, chain_index: Index) -> None:
        graph = build_local_graph(
            chain_index, chain_index.vault_root / "A.md", degrees=0
        )
        assert _degrees(graph) == {"A": 0}
        assert graph.edges == ()
        assert graph.radius == 0


class TestSignatureFixture:
    """
    The spec §9.7 recall workflow: person -> meeting -> other persons.
    """

    @pytest.fixture
    def meeting_index(self, tmp_path: Path) -> Index:
        return _make_vault(
            tmp_path,
            {
                "John Doe": "A person.",
                "Team Sync": "[[John Doe]], [[Jane Smith]], [[Bob Lee]]",
                "Jane Smith": "A person.",
                "Bob Lee": "A person.",
            },
        )

    def test_recover_forgotten_names(self, meeting_index: Index) -> None:
        root = meeting_index.vault_root / "John Doe.md"
        graph = build_local_graph(meeting_index, root, degrees=2)
        assert _degrees(graph) == {
            "John Doe": 0,
            "Team Sync": 1,
            "Jane Smith": 2,
            "Bob Lee": 2,
        }

    def test_edges_and_directions(self, meeting_index: Index) -> None:
        vault = meeting_index.vault_root
        root = vault / "John Doe.md"
        graph = build_local_graph(meeting_index, root, degrees=2)
        by_pair = {frozenset((e.source, e.target)): e.direction for e in graph.edges}
        sync = vault / "Team Sync.md"
        # John Doe reaches Team Sync via a backlink (the meeting links him).
        assert by_pair[frozenset((root, sync))] == "backlink"
        # The meeting's outbound links reach the other persons.
        assert by_pair[frozenset((sync, vault / "Jane Smith.md"))] == "outbound"
        assert by_pair[frozenset((sync, vault / "Bob Lee.md"))] == "outbound"
        assert len(graph.edges) == 3


class TestNodeBudget:
    """
    The node budget truncates with a single "+N more" placeholder.
    """

    def test_truncation(self, tmp_path: Path) -> None:
        files = {f"Note {i:03d}": "leaf" for i in range(100)}
        files["Root"] = " ".join(f"[[Note {i:03d}]]" for i in range(100))
        index = _make_vault(tmp_path, files)
        graph = build_local_graph(index, tmp_path / "Root.md", degrees=2, max_nodes=10)
        real = [n for n in graph.nodes if not n.truncated]
        placeholders = [n for n in graph.nodes if n.truncated]
        assert len(real) == 10
        assert real[0].title == "Root"
        assert len(placeholders) == 1
        assert placeholders[0].title == "+91 more"
        assert placeholders[0].path == TRUNCATED_SENTINEL
        assert placeholders[0].degree == 1
        assert graph.truncated_count == 91
        # Edges only among included real nodes.
        included = {n.path for n in real}
        assert all(e.source in included and e.target in included for e in graph.edges)


class TestHubExclusion:
    """
    Hub nodes are included but not expanded; the root always expands.
    """

    def test_hub_not_expanded(self, tmp_path: Path) -> None:
        files = {f"Leaf {i:02d}": "leaf" for i in range(50)}
        files["Hub"] = " ".join(f"[[Leaf {i:02d}]]" for i in range(50))
        files["Root"] = "[[Hub]]"
        index = _make_vault(tmp_path, files)
        graph = build_local_graph(
            index,
            tmp_path / "Root.md",
            degrees=3,
            exclude_hub_threshold=10,
        )
        titles = set(_degrees(graph))
        assert titles == {"Root", "Hub"}
        hub = next(n for n in graph.nodes if n.title == "Hub")
        assert hub.link_count == 51  # 50 leaves + Root backlink

    def test_root_hub_always_expanded(self, tmp_path: Path) -> None:
        files = {f"Leaf {i:02d}": "leaf" for i in range(50)}
        files["Root"] = " ".join(f"[[Leaf {i:02d}]]" for i in range(50))
        index = _make_vault(tmp_path, files)
        graph = build_local_graph(
            index,
            tmp_path / "Root.md",
            degrees=1,
            exclude_hub_threshold=10,
        )
        assert len([n for n in graph.nodes if not n.truncated]) == 51


class TestCycles:
    """
    Cycles terminate and render as edges, not repeated nodes.
    """

    def test_two_node_cycle(self, tmp_path: Path) -> None:
        index = _make_vault(tmp_path, {"A": "[[B]]", "B": "[[A]]"})
        graph = build_local_graph(index, tmp_path / "A.md", degrees=3)
        assert _degrees(graph) == {"A": 0, "B": 1}
        assert len(graph.edges) == 1
        assert graph.edges[0].direction == "both"

    def test_neighbors_helper(self, tmp_path: Path) -> None:
        index = _make_vault(tmp_path, {"A": "[[B]]", "B": "[[A]]"})
        graph = build_local_graph(index, tmp_path / "A.md", degrees=3)
        assert graph.neighbors(tmp_path / "A.md") == [tmp_path / "B.md"]


class TestDeterminismAndClamping:
    """
    Repeated builds are byte-identical; degrees clamp to HARD_CAP.
    """

    def test_deterministic(self, tmp_path: Path) -> None:
        files = {f"N{i}": "" for i in range(20)}
        files["Root"] = " ".join(f"[[N{i}]]" for i in range(20))
        index = _make_vault(tmp_path, files)
        first = build_local_graph(index, tmp_path / "Root.md", degrees=2)
        second = build_local_graph(index, tmp_path / "Root.md", degrees=2)
        assert first == second
        assert first.nodes == second.nodes
        assert first.edges == second.edges

    def test_degrees_clamped_to_hard_cap(self, tmp_path: Path) -> None:
        titles = [chr(ord("A") + i) for i in range(8)]
        files = {
            t: f"[[{titles[i + 1]}]]" if i + 1 < len(titles) else ""
            for i, t in enumerate(titles)
        }
        index = _make_vault(tmp_path, files)
        graph = build_local_graph(index, tmp_path / "A.md", degrees=99)
        assert graph.radius == HARD_CAP == 5
        assert max(_degrees(graph).values()) == 5

    def test_nodes_by_degree(self, chain_index: Index) -> None:
        graph = build_local_graph(
            chain_index, chain_index.vault_root / "A.md", degrees=2
        )
        grouped = graph.nodes_by_degree()
        assert [n.title for n in grouped[0]] == ["A"]
        assert [n.title for n in grouped[1]] == ["B"]
        assert [n.title for n in grouped[2]] == ["C"]
        assert all(isinstance(n, GraphNode) for n in graph.nodes)
