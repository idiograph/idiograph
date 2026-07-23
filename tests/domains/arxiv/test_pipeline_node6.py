# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import pytest

from idiograph.domains.arxiv.models import (
    CitationEdge,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    clean_cycles,
    compute_depth_metrics,
    compute_pagerank,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _rec(
    node_id: str,
    root_ids: list[str] | None = None,
    hop_depth: int = 1,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=root_ids if root_ids is not None else [node_id],
    )


def _edge(source: str, target: str) -> CitationEdge:
    return CitationEdge(source_id=source, target_id=target, type="cites")


# ── compute_depth_metrics ───────────────────────────────────────────────────


def test_single_seed_backward_chain() -> None:
    """Seed S, S→A→B: A is "backward" d=1, B is "backward" d=2, S is "seed" with {S:0}."""
    nodes = [_rec("S"), _rec("A", root_ids=["S"]), _rec("B", root_ids=["S"])]
    edges = [_edge("S", "A"), _edge("A", "B")]

    result = compute_depth_metrics(nodes, edges)

    assert result["S"].traversal_direction == "seed"
    assert result["S"].hop_depth_per_root == {"S": 0}
    assert result["A"].traversal_direction == "backward"
    assert result["A"].hop_depth_per_root == {"S": 1}
    assert result["B"].traversal_direction == "backward"
    assert result["B"].hop_depth_per_root == {"S": 2}


def test_single_seed_forward_chain() -> None:
    """Seed S, A→B→S: A is "forward" d=2, B is "forward" d=1, S is "seed"."""
    nodes = [_rec("S"), _rec("A", root_ids=["S"]), _rec("B", root_ids=["S"])]
    edges = [_edge("A", "B"), _edge("B", "S")]

    result = compute_depth_metrics(nodes, edges)

    assert result["S"].traversal_direction == "seed"
    assert result["S"].hop_depth_per_root == {"S": 0}
    assert result["B"].traversal_direction == "forward"
    assert result["B"].hop_depth_per_root == {"S": 1}
    assert result["A"].traversal_direction == "forward"
    assert result["A"].hop_depth_per_root == {"S": 2}


def test_single_seed_mixed_graph() -> None:
    """Seed S with both ancestor and descendant neighbors: each labeled correctly."""
    # S→A (A is descendant of S, Node 3 lineage); B→S (B is ancestor of S, Node 4 side).
    nodes = [_rec("S"), _rec("A", root_ids=["S"]), _rec("B", root_ids=["S"])]
    edges = [_edge("S", "A"), _edge("B", "S")]

    result = compute_depth_metrics(nodes, edges)

    assert result["S"].traversal_direction == "seed"
    assert result["A"].traversal_direction == "backward"
    assert result["A"].hop_depth_per_root == {"S": 1}
    assert result["B"].traversal_direction == "forward"
    assert result["B"].hop_depth_per_root == {"S": 1}


def test_seed_self_entry_zero() -> None:
    """Every seed carries own node_id → 0 in its dict."""
    # Two disjoint seeds; each only reaches itself.
    nodes = [_rec("S1"), _rec("S2")]
    edges: list[CitationEdge] = []

    result = compute_depth_metrics(nodes, edges)

    assert result["S1"].hop_depth_per_root["S1"] == 0
    assert result["S1"].traversal_direction == "seed"
    assert result["S2"].hop_depth_per_root["S2"] == 0
    assert result["S2"].traversal_direction == "seed"


def test_two_seed_forest_no_overlap() -> None:
    """Two disjoint seed subtrees: all non-seed nodes reach only one seed each."""
    nodes = [
        _rec("S1"),
        _rec("S2"),
        _rec("A", root_ids=["S1"]),
        _rec("B", root_ids=["S2"]),
    ]
    edges = [_edge("S1", "A"), _edge("S2", "B")]

    result = compute_depth_metrics(nodes, edges)

    assert result["A"].hop_depth_per_root == {"S1": 1}
    assert result["A"].traversal_direction == "backward"
    assert result["B"].hop_depth_per_root == {"S2": 1}
    assert result["B"].traversal_direction == "backward"
    assert result["S1"].hop_depth_per_root == {"S1": 0}
    assert result["S2"].hop_depth_per_root == {"S2": 0}


def test_two_seed_shared_ancestor() -> None:
    """Both seeds cite X: X is "backward", dict has both seed entries."""
    nodes = [_rec("S1"), _rec("S2"), _rec("X", root_ids=["S1", "S2"])]
    edges = [_edge("S1", "X"), _edge("S2", "X")]

    result = compute_depth_metrics(nodes, edges)

    assert result["X"].traversal_direction == "backward"
    assert result["X"].hop_depth_per_root == {"S1": 1, "S2": 1}


def test_two_seed_mixed_between() -> None:
    """Seed S2 cites seed S1; X cites S1 and is cited by S2: X is "mixed"."""
    # Edges: S2→S1 (S2 cites S1), X→S1 (X cites S1), S2→X (S2 cites X).
    # X is a descendant of S2 (forward from S2) and an ancestor of S1
    # (backward from S1) — directions disagree → "mixed".
    nodes = [
        _rec("S1"),
        _rec("S2"),
        _rec("X", root_ids=["S1", "S2"]),
    ]
    edges = [_edge("S2", "S1"), _edge("X", "S1"), _edge("S2", "X")]

    result = compute_depth_metrics(nodes, edges)

    assert result["X"].traversal_direction == "mixed"
    assert result["X"].hop_depth_per_root == {"S1": 1, "S2": 1}


def test_two_seed_each_reaches_other() -> None:
    """S2 cites S1: S1's dict has {S1:0, S2:1}, S1's direction is "seed"."""
    nodes = [_rec("S1"), _rec("S2")]
    edges = [_edge("S2", "S1")]

    result = compute_depth_metrics(nodes, edges)

    assert result["S1"].traversal_direction == "seed"
    assert result["S1"].hop_depth_per_root == {"S1": 0, "S2": 1}
    assert result["S2"].traversal_direction == "seed"
    assert result["S2"].hop_depth_per_root == {"S2": 0, "S1": 1}


def test_three_seed_partial_reachability() -> None:
    """Node reachable from 2 of 3 seeds carries dict with exactly 2 entries."""
    # S1 and S2 both cite X; S3 has its own subtree (S3→Y) and never reaches X.
    nodes = [
        _rec("S1"),
        _rec("S2"),
        _rec("S3"),
        _rec("X", root_ids=["S1", "S2"]),
        _rec("Y", root_ids=["S3"]),
    ]
    edges = [_edge("S1", "X"), _edge("S2", "X"), _edge("S3", "Y")]

    result = compute_depth_metrics(nodes, edges)

    assert set(result["X"].hop_depth_per_root.keys()) == {"S1", "S2"}
    assert result["X"].hop_depth_per_root == {"S1": 1, "S2": 1}
    assert result["X"].traversal_direction == "backward"


def test_three_seed_partial_reach_mixed() -> None:
    """Node reachable forward from one seed, backward from another, unreachable from the third: labeled "mixed"."""
    # S1→X (X descendant of S1, forward from S1).
    # X→S2 (X ancestor of S2, backward from S2).
    # S3→Y (S3 has its own subtree; never reaches X).
    nodes = [
        _rec("S1"),
        _rec("S2"),
        _rec("S3"),
        _rec("X", root_ids=["S1", "S2"]),
        _rec("Y", root_ids=["S3"]),
    ]
    edges = [_edge("S1", "X"), _edge("X", "S2"), _edge("S3", "Y")]

    result = compute_depth_metrics(nodes, edges)

    assert result["X"].traversal_direction == "mixed"
    assert result["X"].hop_depth_per_root == {"S1": 1, "S2": 1}
    # S3 explicitly absent — partial reachability does not place a None key.
    assert "S3" not in result["X"].hop_depth_per_root


def test_suppressed_cycle_node_normal_values() -> None:
    """Nodes in cycle_log.affected_node_ids receive normal metrics, not null."""
    # 2-cycle S↔A: clean_cycles will suppress one direction; both endpoints
    # land in affected_node_ids. Under AMD-019, Node 6 still produces normal
    # metrics for them rather than special-casing nulls.
    nodes = [_rec("S"), _rec("A", root_ids=["S"])]
    edges = [_edge("S", "A"), _edge("A", "S")]

    result = clean_cycles(nodes, edges)

    assert result.cycle_log.affected_node_ids == {"S", "A"}

    metrics = compute_depth_metrics(nodes, result.cleaned_edges)

    # Both endpoints carry normal, non-empty metrics.
    assert metrics["S"].traversal_direction == "seed"
    assert metrics["S"].hop_depth_per_root == {"S": 0}
    assert metrics["A"].hop_depth_per_root  # non-empty dict
    assert metrics["A"].traversal_direction is not None


def test_unreachable_node_raises() -> None:
    """Node with no path from any root raises ValueError."""
    nodes = [_rec("S"), _rec("X", root_ids=["S"])]
    edges: list[CitationEdge] = []  # X disconnected from S

    with pytest.raises(ValueError, match="X.*unreachable"):
        compute_depth_metrics(nodes, edges)


def test_no_roots_raises() -> None:
    """nodes with no self-root entries raises ValueError."""
    nodes = [_rec("A", root_ids=["X"]), _rec("B", root_ids=["X"])]
    edges: list[CitationEdge] = []

    with pytest.raises(ValueError, match="No roots"):
        compute_depth_metrics(nodes, edges)


def test_empty_nodes_returns_empty() -> None:
    """nodes=[] returns {}."""
    assert compute_depth_metrics([], []) == {}


def test_input_not_mutated() -> None:
    """Original input lists unchanged after call."""
    nodes = [_rec("S"), _rec("A", root_ids=["S"])]
    edges = [_edge("S", "A")]

    nodes_snapshot = [n.model_copy(deep=True) for n in nodes]
    edges_snapshot = [e.model_copy(deep=True) for e in edges]

    compute_depth_metrics(nodes, edges)

    assert len(nodes) == 2
    assert len(edges) == 1
    for n, snap in zip(nodes, nodes_snapshot, strict=True):
        assert n == snap
    for e, snap in zip(edges, edges_snapshot, strict=True):
        assert e == snap


def test_deterministic_output() -> None:
    """Same input produces identical output across repeat calls."""
    nodes = [
        _rec("S1"),
        _rec("S2"),
        _rec("X", root_ids=["S1", "S2"]),
        _rec("Y", root_ids=["S1"]),
    ]
    edges = [_edge("S1", "X"), _edge("S2", "X"), _edge("Y", "S1")]

    r1 = compute_depth_metrics(nodes, edges)
    r2 = compute_depth_metrics(nodes, edges)

    assert r1 == r2


# ── compute_pagerank ────────────────────────────────────────────────────────


def test_pagerank_networkx_agreement() -> None:
    """Output matches nx.pagerank(G, alpha=damping) on hand-constructed fixture."""
    import asyncio
    import networkx as nx

    nodes = [_rec("A"), _rec("B"), _rec("C"), _rec("D")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D"), _edge("D", "A")]

    result = asyncio.run(compute_pagerank(
        {"damping": 0.85},
        {"upstream": {"nodes": nodes, "cleaned_edges": edges}},
    ))["pagerank"]

    G = nx.DiGraph()
    G.add_nodes_from(["A", "B", "C", "D"])
    G.add_edges_from([("A", "B"), ("B", "C"), ("C", "D"), ("D", "A")])
    expected = nx.pagerank(G, alpha=0.85)

    for node_id in ("A", "B", "C", "D"):
        assert result[node_id] == pytest.approx(expected[node_id])


def test_pagerank_sums_to_one() -> None:
    """Sum of output values is 1.0 within tolerance."""
    import asyncio

    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [
        _edge("A", "B"),
        _edge("B", "C"),
        _edge("C", "D"),
        _edge("D", "E"),
        _edge("E", "A"),
    ]

    result = asyncio.run(compute_pagerank(
        {},
        {"upstream": {"nodes": nodes, "cleaned_edges": edges}},
    ))["pagerank"]

    assert sum(result.values()) == pytest.approx(1.0)


def test_pagerank_every_node_assigned() -> None:
    """Every node_id in nodes appears in output, including isolates."""
    import asyncio

    # C is an isolate — no edges touch it. Must still appear in output.
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("A", "B")]

    result = asyncio.run(compute_pagerank(
        {},
        {"upstream": {"nodes": nodes, "cleaned_edges": edges}},
    ))["pagerank"]

    assert set(result.keys()) == {"A", "B", "C"}


def test_pagerank_damping_respected() -> None:
    """Different damping values produce different results on same graph."""
    import asyncio

    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    inputs = {"upstream": {"nodes": nodes, "cleaned_edges": edges}}
    r_low = asyncio.run(compute_pagerank({"damping": 0.5}, inputs))["pagerank"]
    r_high = asyncio.run(compute_pagerank({"damping": 0.95}, inputs))["pagerank"]

    assert r_low != r_high


def test_pagerank_deterministic() -> None:
    """Same input produces identical output across repeat calls."""
    import asyncio

    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D"), _edge("D", "A")]

    inputs = {"upstream": {"nodes": nodes, "cleaned_edges": edges}}
    r1 = asyncio.run(compute_pagerank({}, inputs))["pagerank"]
    r2 = asyncio.run(compute_pagerank({}, inputs))["pagerank"]

    assert r1 == r2


def test_pagerank_empty_nodes_returns_empty() -> None:
    """nodes=[] returns {}."""
    import asyncio

    result = asyncio.run(compute_pagerank(
        {},
        {"upstream": {"nodes": [], "cleaned_edges": []}},
    ))["pagerank"]
    assert result == {}


def test_pagerank_empty_edges_uniform() -> None:
    """No edges: each of N nodes gets value 1/N."""
    import asyncio

    nodes = [_rec(x) for x in ("A", "B", "C", "D")]

    result = asyncio.run(compute_pagerank(
        {},
        {"upstream": {"nodes": nodes, "cleaned_edges": []}},
    ))["pagerank"]

    for v in result.values():
        assert v == pytest.approx(0.25)


def test_pagerank_input_not_mutated() -> None:
    """Original inputs unchanged after call — restated against the `inputs`
    payload the handler actually receives under the node-handler contract."""
    import asyncio

    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    nodes_snapshot = [n.model_copy(deep=True) for n in nodes]
    edges_snapshot = [e.model_copy(deep=True) for e in edges]

    inputs = {"upstream": {"nodes": nodes, "cleaned_edges": edges}}
    asyncio.run(compute_pagerank({}, inputs))

    assert len(nodes) == 3
    assert len(edges) == 2
    for n, snap in zip(nodes, nodes_snapshot, strict=True):
        assert n == snap
    for e, snap in zip(edges, edges_snapshot, strict=True):
        assert e == snap
