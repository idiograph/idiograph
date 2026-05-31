# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import logging

import pytest

from idiograph.domains.arxiv.models import (
    CitationEdge,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import compute_co_citations


# ── Helpers ─────────────────────────────────────────────────────────────────


def _rec(node_id: str, citation_count: int = 0, hop_depth: int = 1) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=[node_id],
        citation_count=citation_count,
    )


def _edge(source: str, target: str) -> CitationEdge:
    return CitationEdge(source_id=source, target_id=target, type="cites")


def _triples(edges: list[CitationEdge]) -> list[tuple[str, str, int]]:
    return [(e.source_id, e.target_id, e.strength) for e in edges]


# ── Tests ───────────────────────────────────────────────────────────────────


def test_minimal_co_citation() -> None:
    """Three papers, C cites A and B: one edge A↔B with strength=1, min_strength=1."""
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert _triples(result.edges) == [("A", "B", 1)]


def test_strength_accumulates() -> None:
    """C and D both cite A and B: one edge with strength=2."""
    nodes = [_rec("A"), _rec("B"), _rec("C"), _rec("D")]
    edges = [
        _edge("C", "A"),
        _edge("C", "B"),
        _edge("D", "A"),
        _edge("D", "B"),
    ]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert _triples(result.edges) == [("A", "B", 2)]


def test_multiple_independent_pairs() -> None:
    """C cites A,B; D cites E,F: two edges, no cross-contamination."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E", "F")]
    edges = [
        _edge("C", "A"),
        _edge("C", "B"),
        _edge("D", "E"),
        _edge("D", "F"),
    ]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert _triples(result.edges) == [("A", "B", 1), ("E", "F", 1)]


def test_min_strength_filters_singletons() -> None:
    """Default min_strength=2 drops strength-1 edges."""
    # C→A, C→B produces a single shared-citer pair (strength=1) — must be filtered.
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    result = compute_co_citations(nodes, edges)  # default min_strength=2

    assert result.edges == []


def test_min_strength_one_includes_all() -> None:
    """min_strength=1 emits every shared-citer pair."""
    # C cites A, B, D. That produces three pairs each with strength 1.
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    edges = [_edge("C", "A"), _edge("C", "B"), _edge("C", "D")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert _triples(result.edges) == [
        ("A", "B", 1),
        ("A", "D", 1),
        ("B", "D", 1),
    ]


def test_max_edges_none_emits_all() -> None:
    """Default max_edges=None returns all qualifying edges."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    # E cites everyone; all pairs share citer E at strength 1.
    edges = [_edge("E", "A"), _edge("E", "B"), _edge("E", "C"), _edge("E", "D")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    # 4 choose 2 = 6 pairs.
    assert len(result.edges) == 6


def test_max_edges_enforces_hard_cap() -> None:
    """max_edges=N returns exactly N highest-strength edges."""
    # Build a graph with distinct strengths so ordering is unambiguous.
    # Strengths: (A,B)=3, (A,C)=2, (B,C)=2, (A,D)=1 — four edges, cap at 2.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "X", "Y", "Z")]
    edges = [
        # 3 shared citers of A and B: X, Y, Z
        _edge("X", "A"),
        _edge("X", "B"),
        _edge("Y", "A"),
        _edge("Y", "B"),
        _edge("Z", "A"),
        _edge("Z", "B"),
        # 2 shared citers of A and C: X, Y
        _edge("X", "C"),
        _edge("Y", "C"),
        # 2 shared citers of B and C already covered by X,Y (both cite B above
        # — and both cite C via the lines above). Good.
        # 1 shared citer of A and D: X
        _edge("X", "D"),
    ]

    result = compute_co_citations(nodes, edges, min_strength=1, max_edges=2)

    assert len(result.edges) == 2
    # Strongest pair first.
    assert (
        result.edges[0].source_id,
        result.edges[0].target_id,
        result.edges[0].strength,
    ) == (
        "A",
        "B",
        3,
    )
    assert result.edges[1].strength == 2


def test_output_sorted_by_contract() -> None:
    """Output order is (strength desc, source_id asc, target_id asc)."""
    # Construct pairs that exercise BOTH the -strength ordering AND the
    # source_id/target_id tiebreakers:
    #   (A,B)=2  — strongest
    #   (A,C)=1  — ties with (A,D) and (B,D): source_id tiebreak puts A before B
    #   (A,D)=1  — ties with (A,C) and (B,D): target_id breaks (C before D)
    #   (B,D)=1  — same-strength tail, source_id puts it last
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "X", "Y", "Z")]
    edges = [
        # A,B share two citers X and Y
        _edge("X", "A"),
        _edge("X", "B"),
        _edge("Y", "A"),
        _edge("Y", "B"),
        # A,C share one citer X
        _edge("X", "C"),
        # A,D share one citer Y — via Y→A above and Y→D below
        _edge("Y", "D"),
        # B,D share two citers Y and Z (Y via Y→B above and Y→D above;
        # Z via Z→B and Z→D below). Using a citer that cites only B and D
        # keeps the tiers distinct.
        _edge("Z", "B"),
        _edge("Z", "D"),
    ]
    # Resulting strengths:
    # A,B: shared citers = {X,Y}                   -> 2
    # A,C: shared citers = {X}                     -> 1
    # A,D: shared citers of A={X,Y}, D={Y,Z} = {Y} -> 1
    # B,C: shared citers of B={X,Y,Z}, C={X} = {X} -> 1
    # B,D: shared citers of B={X,Y,Z}, D={Y,Z} = {Y,Z} -> 2
    # C,D: shared citers of C={X}, D={Y,Z} = {}    -> 0 (filtered at min_strength=1)

    result = compute_co_citations(nodes, edges, min_strength=1)

    # Expected by contract (strength desc, source_id asc, target_id asc):
    #  (A,B,2), (B,D,2), (A,C,1), (A,D,1), (B,C,1)
    assert _triples(result.edges) == [
        ("A", "B", 2),
        ("B", "D", 2),
        ("A", "C", 1),
        ("A", "D", 1),
        ("B", "C", 1),
    ]


def test_canonical_form_dedup() -> None:
    """Pairs emit once with source_id < target_id, not twice."""
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    # Exactly one edge; canonical form.
    assert len(result.edges) == 1
    e = result.edges[0]
    assert e.source_id < e.target_id
    assert (e.source_id, e.target_id) == ("A", "B")


def test_no_self_co_citation() -> None:
    """Self-citation edges in input do not produce self co-citation edges."""
    # A self-cites; B has no citers. No pair should emit.
    nodes = [_rec("A"), _rec("B")]
    edges = [_edge("A", "A"), _edge("B", "B")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert result.edges == []
    # Also: ensure we never emit an edge with source==target.
    for e in result.edges:
        assert e.source_id != e.target_id


def test_cross_root_co_citation() -> None:
    """AMD-017: papers in different root_ids co-cite when they share citers."""
    # A and B have distinct root_ids (helper sets root_ids=[node_id] per node).
    # Citer C cites both — a co-citation edge must emit regardless of root_ids.
    a = _rec("A")
    b = _rec("B")
    c = _rec("C")
    # Sanity check: A and B are in different root subtrees.
    assert set(a.root_ids) != set(b.root_ids)

    edges = [_edge("C", "A"), _edge("C", "B")]

    result = compute_co_citations([a, b, c], edges, min_strength=1)

    assert _triples(result.edges) == [("A", "B", 1)]


def test_truncation_boundary_deterministic() -> None:
    """Ties straddling max_edges cutoff resolve by secondary sort, same on repeat."""
    # Build many strength-1 pairs so the boundary sits inside a tie tier.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E", "F", "X")]
    # X cites A, B, C, D, E, F → all 15 pairs at strength 1.
    edges = [_edge("X", t) for t in ("A", "B", "C", "D", "E", "F")]

    r1 = compute_co_citations(nodes, edges, min_strength=1, max_edges=3)
    r2 = compute_co_citations(nodes, edges, min_strength=1, max_edges=3)

    # Deterministic across calls.
    assert _triples(r1.edges) == _triples(r2.edges)
    # And the first three by secondary sort are (A,B), (A,C), (A,D).
    assert _triples(r1.edges) == [("A", "B", 1), ("A", "C", 1), ("A", "D", 1)]


def test_edge_type_is_co_citation() -> None:
    """All emitted edges have type="co_citation"."""
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert result.edges  # precondition for the assertion below
    for e in result.edges:
        assert e.type == "co_citation"
        assert e.citing_paper_year is None
        assert isinstance(e.strength, int) and e.strength > 0


def test_input_not_mutated() -> None:
    """Original input lists unchanged after call (pure function property)."""
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    nodes_snapshot = list(nodes)
    edges_snapshot = list(edges)

    compute_co_citations(nodes, edges, min_strength=1)

    assert nodes == nodes_snapshot
    assert edges == edges_snapshot
    assert len(nodes) == 3
    assert len(edges) == 2


def test_routing_independence() -> None:
    """Same logical citations via cleaned/suppressed/union route produce identical output."""
    # The function is agnostic to upstream cleaning provenance — the caller
    # hands it a single flat list of citation edges assembled however it sees
    # fit. Construct three equivalent routings of the same logical citation
    # set and assert identical output.
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    logical_edges = [
        _edge("C", "A"),
        _edge("C", "B"),
        _edge("D", "A"),
        _edge("D", "B"),
    ]

    # Route 1: all "cleaned" (whole list as if no suppression happened).
    route_all_cleaned = list(logical_edges)
    # Route 2: all edges arriving via the ".original" of suppressed records.
    #          Node 5 receives identical CitationEdge objects either way.
    route_all_suppressed = [
        CitationEdge(
            source_id=e.source_id,
            target_id=e.target_id,
            type=e.type,
            citing_paper_year=e.citing_paper_year,
            strength=e.strength,
        )
        for e in logical_edges
    ]
    # Route 3: mixed union of a partitioned split.
    route_mixed = logical_edges[:2] + [
        CitationEdge(
            source_id=e.source_id,
            target_id=e.target_id,
            type=e.type,
            citing_paper_year=e.citing_paper_year,
            strength=e.strength,
        )
        for e in logical_edges[2:]
    ]

    r1 = compute_co_citations(nodes, route_all_cleaned, min_strength=1)
    r2 = compute_co_citations(nodes, route_all_suppressed, min_strength=1)
    r3 = compute_co_citations(nodes, route_mixed, min_strength=1)

    assert _triples(r1.edges) == _triples(r2.edges) == _triples(r3.edges)
    assert _triples(r1.edges) == [("A", "B", 2)]


def test_missing_citation_node_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Edge referencing unknown node_id: skipped, WARNING logged, id in result.warnings, no raise."""
    # Node "Z" appears only in an edge, not in nodes — must trigger a warning
    # and be skipped, not raise.
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [
        _edge("C", "A"),
        _edge("C", "B"),
        _edge("Z", "A"),  # Z unknown — skip
        _edge("C", "Z"),  # Z unknown — skip
    ]

    with caplog.at_level(logging.WARNING, logger="idiograph.arxiv.pipeline"):
        result = compute_co_citations(nodes, edges, min_strength=1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("Z" in r.getMessage() for r in warnings)
    # The unknown node_id is surfaced in the returned warnings (deduped once).
    assert result.warnings == ["Z"]
    # Output still reflects the valid portion: C co-cites A and B.
    assert _triples(result.edges) == [("A", "B", 1)]


def test_missing_citation_node_both_endpoints_unknown() -> None:
    """Both endpoints of one edge unknown: BOTH ids in result.warnings (Option A completeness)."""
    # Edge P→Q where neither P nor Q is in nodes. The check is unconditional on
    # both endpoints, so a node appearing only as the target of an unknown-source
    # edge (Q) is still recorded — not source-only. First-encounter order: P, Q.
    nodes = [_rec("A"), _rec("B")]
    edges = [_edge("P", "Q")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert result.warnings == ["P", "Q"]
    assert result.edges == []


def test_min_strength_zero_raises() -> None:
    """min_strength < 1 raises ValueError."""
    nodes = [_rec("A"), _rec("B")]
    edges: list[CitationEdge] = []

    with pytest.raises(ValueError, match="min_strength"):
        compute_co_citations(nodes, edges, min_strength=0)

    with pytest.raises(ValueError, match="min_strength"):
        compute_co_citations(nodes, edges, min_strength=-1)


def test_max_edges_negative_raises() -> None:
    """max_edges < 0 raises ValueError."""
    nodes = [_rec("A"), _rec("B")]
    edges: list[CitationEdge] = []

    with pytest.raises(ValueError, match="max_edges"):
        compute_co_citations(nodes, edges, max_edges=-1)

    # max_edges=0 is valid (returns []) per spec §Contracts.
    assert compute_co_citations(nodes, edges, max_edges=0).edges == []


# Beyond the spec §Tests minimum set — these close gaps in spec §Contracts
# (empty inputs, single node) that the named 18 tests do not enforce directly.


def test_empty_inputs() -> None:
    """Empty nodes or empty edges returns [] without error (spec §Contracts)."""
    assert compute_co_citations([], [], min_strength=1).edges == []
    assert compute_co_citations([_rec("A")], [], min_strength=1).edges == []


def test_single_node() -> None:
    """A single node produces no pairs, returns [] (spec §Contracts)."""
    nodes = [_rec("A")]
    edges = [_edge("A", "A")]  # self-citation defensively filtered

    assert compute_co_citations(nodes, edges, min_strength=1).edges == []


def test_warnings_always_list() -> None:
    """warnings is a list, never None, on a clean run with no unknown node_ids."""
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    result = compute_co_citations(nodes, edges, min_strength=1)

    assert result.warnings == []
    assert isinstance(result.warnings, list)
