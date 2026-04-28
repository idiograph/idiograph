# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import logging

import pytest

from idiograph.domains.arxiv.models import (
    CitationEdge,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    clean_cycles,
    detect_communities,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _rec(node_id: str) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=1,
        root_ids=[node_id],
    )


def _edge(source: str, target: str) -> CitationEdge:
    return CitationEdge(source_id=source, target_id=target, type="cites")


# ── Tests ───────────────────────────────────────────────────────────────────


def test_all_nodes_assigned() -> None:
    """Every input node appears in community_assignments."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    result = detect_communities(nodes, edges)

    assert set(result.community_assignments.keys()) == {"A", "B", "C", "D", "E"}


def test_community_count_matches_assignments() -> None:
    """community_count == len(set(community_assignments.values()))."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E", "F")]
    edges = [
        _edge("A", "B"),
        _edge("B", "C"),
        _edge("D", "E"),
        _edge("E", "F"),
    ]

    result = detect_communities(nodes, edges)

    assert result.community_count == len(set(result.community_assignments.values()))


def test_isolate_receives_assignment() -> None:
    """Node with no edges is not omitted from output."""
    # X is an isolate — no edges touch it.
    nodes = [_rec(x) for x in ("A", "B", "C", "X")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = detect_communities(nodes, edges)

    assert "X" in result.community_assignments


def test_community_id_is_string() -> None:
    """All values in community_assignments are strings."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    edges = [_edge("A", "B"), _edge("C", "D")]

    result = detect_communities(nodes, edges)

    assert result.community_assignments  # precondition
    for cid in result.community_assignments.values():
        assert isinstance(cid, str)


def test_algorithm_used_set() -> None:
    """algorithm_used is "infomap" or "leiden" — never None."""
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = detect_communities(nodes, edges)

    assert result.algorithm_used in ("infomap", "leiden")


def test_validation_flags_empty_within_bounds() -> None:
    """No flags when community count is between min and max."""
    # Two clearly separate components → infomap finds ≥ 2 communities.
    # Bounds [1, 10] bracket that comfortably.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    result = detect_communities(
        nodes, edges, community_count_min=1, community_count_max=10
    )

    assert result.validation_flags == []


def test_validation_flag_below_minimum() -> None:
    """`community_count_below_minimum` flag when count < min."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    # community_count_min=100 forces below-minimum regardless of how many
    # communities infomap finds on this small graph.
    result = detect_communities(
        nodes, edges, community_count_min=100, community_count_max=200
    )

    assert "community_count_below_minimum" in result.validation_flags


def test_validation_flag_above_maximum() -> None:
    """`community_count_above_maximum` flag when count > max."""
    # Two disjoint components produce ≥ 2 communities; cap at 1 → flag fires.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    result = detect_communities(
        nodes, edges, community_count_min=1, community_count_max=1
    )

    assert "community_count_above_maximum" in result.validation_flags


def test_missing_edge_node_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown node_id in cites_edges logs WARNING; edge is skipped."""
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [
        _edge("A", "B"),
        _edge("B", "C"),
        _edge("Z", "A"),  # Z unknown — skip
        _edge("C", "Z"),  # Z unknown — skip
    ]

    with caplog.at_level(logging.WARNING, logger="idiograph.arxiv.pipeline"):
        result = detect_communities(nodes, edges)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("Z" in r.getMessage() for r in warnings)
    # All known nodes still receive assignments.
    assert set(result.community_assignments.keys()) == {"A", "B", "C"}


def test_empty_nodes() -> None:
    """Empty input returns empty community_assignments, community_count=0."""
    result = detect_communities([], [])

    assert result.community_assignments == {}
    assert result.community_count == 0


def test_single_node_no_edges() -> None:
    """Single node with no edges receives an assignment."""
    nodes = [_rec("A")]

    result = detect_communities(nodes, [])

    assert "A" in result.community_assignments
    assert result.community_count == 1


def test_disconnected_graph() -> None:
    """Multiple disconnected components all receive assignments."""
    # Three components: {A,B}, {C,D}, {E}.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("C", "D")]

    result = detect_communities(nodes, edges)

    assert set(result.community_assignments.keys()) == {"A", "B", "C", "D", "E"}
    # Disconnected components must land in distinct communities.
    assert result.community_count >= 2


def test_deterministic_same_input() -> None:
    """Two calls with same input produce identical output."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E", "F")]
    edges = [
        _edge("A", "B"),
        _edge("B", "C"),
        _edge("C", "A"),
        _edge("D", "E"),
        _edge("E", "F"),
        _edge("F", "D"),
    ]

    r1 = detect_communities(nodes, edges)
    r2 = detect_communities(nodes, edges)

    assert r1.community_assignments == r2.community_assignments
    assert r1.algorithm_used == r2.algorithm_used
    assert r1.community_count == r2.community_count


def test_suppressed_originals_merge() -> None:
    """Merge pattern (cleaned + [s.original ...]) produces correct input."""
    # Build a small graph with a 2-cycle so clean_cycles suppresses one edge.
    # Then assemble the Node 7 input the same way the pipeline orchestrator
    # will: cleaned ∪ suppressed originals — i.e. the full citation topology.
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    raw_edges = [
        _edge("A", "B"),
        _edge("B", "A"),  # cycle with A→B
        _edge("B", "C"),
        _edge("C", "D"),
    ]

    cycle_result = clean_cycles(nodes, raw_edges)
    assert cycle_result.cycle_log.suppressed_edges  # precondition: a cycle was suppressed

    all_cites = cycle_result.cleaned_edges + [
        s.original for s in cycle_result.cycle_log.suppressed_edges
    ]
    assert len(all_cites) == len(raw_edges)  # nothing dropped by the merge

    result = detect_communities(nodes, all_cites)

    # Every node still receives an assignment after the merge.
    assert set(result.community_assignments.keys()) == {"A", "B", "C", "D"}
    assert result.algorithm_used in ("infomap", "leiden")


def test_validation_flags_always_list() -> None:
    """validation_flags is a list — never None — on clean run."""
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = detect_communities(nodes, edges)

    assert isinstance(result.validation_flags, list)
