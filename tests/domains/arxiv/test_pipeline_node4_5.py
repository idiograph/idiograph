# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import logging

import networkx as nx
import pytest
from pydantic import ValidationError

from idiograph.domains.arxiv.models import (
    CitationEdge,
    CycleCleanResult,
    CycleLog,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import clean_cycles


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


def _pairs(edges: list[CitationEdge]) -> list[tuple[str, str]]:
    return [(e.source_id, e.target_id) for e in edges]


# ── Tests ───────────────────────────────────────────────────────────────────


def test_acyclic_passthrough() -> None:
    """Acyclic input: cleaned_edges equals input, zero suppressions, iterations=0."""
    nodes = [_rec("A", 5), _rec("B", 5), _rec("C", 5)]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = clean_cycles(nodes, edges)

    assert isinstance(result, CycleCleanResult)
    assert _pairs(result.cleaned_edges) == _pairs(edges)
    assert result.cycle_log.suppressed_edges == []
    assert result.cycle_log.iterations == 0
    assert result.cycle_log.cycles_detected_count == 0


def test_two_cycle_simple() -> None:
    """A→B, B→A: one edge removed (the weaker), cycle_members has both."""
    # Equal citations → tied sums → lex tiebreaker picks (A,B).
    nodes = [_rec("A", 10), _rec("B", 10)]
    edges = [_edge("A", "B"), _edge("B", "A")]

    result = clean_cycles(nodes, edges)

    assert len(result.cycle_log.suppressed_edges) == 1
    s = result.cycle_log.suppressed_edges[0]
    assert (s.original.source_id, s.original.target_id) == ("A", "B")
    assert set(s.cycle_members) == {"A", "B"}
    assert len(result.cleaned_edges) == 1
    assert (result.cleaned_edges[0].source_id, result.cleaned_edges[0].target_id) == (
        "B",
        "A",
    )


def test_three_cycle() -> None:
    """A→B, B→C, C→A: one edge removed, cycle_members has three."""
    nodes = [_rec("A", 10), _rec("B", 10), _rec("C", 10)]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")]

    result = clean_cycles(nodes, edges)

    assert len(result.cycle_log.suppressed_edges) == 1
    assert set(result.cycle_log.suppressed_edges[0].cycle_members) == {"A", "B", "C"}
    assert len(result.cleaned_edges) == 2


def test_weakest_link_selected() -> None:
    """Three-edge cycle with unequal citation counts: the minimum-sum edge is removed."""
    # Sums: A+B=15, B+C=6 (min), C+A=11 → B→C must be removed.
    nodes = [_rec("A", 10), _rec("B", 5), _rec("C", 1)]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")]

    result = clean_cycles(nodes, edges)

    assert len(result.cycle_log.suppressed_edges) == 1
    s = result.cycle_log.suppressed_edges[0]
    assert (s.original.source_id, s.original.target_id) == ("B", "C")
    assert s.citation_sum == 6


def test_lex_tiebreaker() -> None:
    """Tied citation sums: lexicographically smaller (source, target) wins removal."""
    nodes = [_rec("A", 5), _rec("B", 5), _rec("C", 5)]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")]

    result = clean_cycles(nodes, edges)

    s = result.cycle_log.suppressed_edges[0]
    assert (s.original.source_id, s.original.target_id) == ("A", "B")


def test_two_disjoint_cycles() -> None:
    """Two independent cycles cleaned in separate iterations; both logged."""
    nodes = [_rec("A", 5), _rec("B", 5), _rec("C", 5), _rec("D", 5)]
    edges = [
        _edge("A", "B"),
        _edge("B", "A"),
        _edge("C", "D"),
        _edge("D", "C"),
    ]

    result = clean_cycles(nodes, edges)

    assert result.cycle_log.iterations == 2
    assert result.cycle_log.cycles_detected_count == 2
    assert len(result.cycle_log.suppressed_edges) == 2
    assert len(result.cleaned_edges) == 2

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n.node_id)
    for e in result.cleaned_edges:
        G.add_edge(e.source_id, e.target_id)
    assert nx.is_directed_acyclic_graph(G)


def test_nested_cycles() -> None:
    """One edge removal breaks multiple cycles: cycles_detected_count ≥ len(suppressed_edges)."""
    # Overlapping cycles share the edge B→A:
    #   2-cycle: A→B, B→A
    #   3-cycle: A→C, C→B, B→A
    # Removing B→A breaks both at once.
    nodes = [_rec("A", 10), _rec("B", 10), _rec("C", 10)]
    edges = [
        _edge("A", "B"),
        _edge("B", "A"),
        _edge("A", "C"),
        _edge("C", "B"),
    ]

    result = clean_cycles(nodes, edges)

    assert result.cycle_log.cycles_detected_count >= len(
        result.cycle_log.suppressed_edges
    )

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n.node_id)
    for e in result.cleaned_edges:
        G.add_edge(e.source_id, e.target_id)
    assert nx.is_directed_acyclic_graph(G)


def test_self_loop() -> None:
    """Self-loop removed; cycle_members is a single node_id."""
    nodes = [_rec("A", 5)]
    edges = [_edge("A", "A")]

    result = clean_cycles(nodes, edges)

    assert len(result.cycle_log.suppressed_edges) == 1
    s = result.cycle_log.suppressed_edges[0]
    assert s.cycle_members == ["A"]
    assert s.citation_sum == 10
    assert result.cleaned_edges == []


def test_affected_node_ids_property() -> None:
    """Derived property returns union of source and target node_ids across suppressed edges."""
    nodes = [_rec("A", 5), _rec("B", 5), _rec("C", 5), _rec("D", 5)]
    edges = [
        _edge("A", "B"),
        _edge("B", "A"),
        _edge("C", "D"),
        _edge("D", "C"),
    ]

    result = clean_cycles(nodes, edges)

    assert result.cycle_log.affected_node_ids == {"A", "B", "C", "D"}


def test_missing_citation_node_raises(caplog: pytest.LogCaptureFixture) -> None:
    """Edge with unknown node_id: WARNING logged, then ValidationError at construction.

    Supersedes the prior graceful-degradation contract. The citation-count
    lookup during cycle scoring still treats the unknown endpoint as 0 and
    emits a WARNING (preserved behavior), but the CycleCleanResult validator
    now raises when the surviving cleaned_edges retain an orphaned endpoint
    not in the input node set. See spec-node4.5-cycle-cleaning.md §Contracts
    "Missing node in citation lookup — supersedes the prior graceful-
    degradation contract".
    """
    # Node "C" is referenced by edges but not in nodes.
    nodes = [_rec("A", 10), _rec("B", 10)]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")]

    with caplog.at_level(logging.WARNING, logger="idiograph.arxiv.pipeline"):
        with pytest.raises(ValidationError) as exc_info:
            clean_cycles(nodes, edges)

    # Citation-count WARNING still fires before the construction-time raise.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("C" in r.getMessage() for r in warnings)
    # Validator names the orphan endpoint and the witness contract.
    assert "C" in str(exc_info.value)
    assert "input_node_ids" in str(exc_info.value)


def test_preserves_input_edge_order() -> None:
    """cleaned_edges retains input ordering for non-removed edges."""
    # Edges deliberately in non-sorted order. 2-cycle A↔B (equal citations
    # → lex tiebreaker removes A→B). C→B is standalone.
    nodes = [_rec("A", 10), _rec("B", 10), _rec("C", 10)]
    edges = [_edge("C", "B"), _edge("A", "B"), _edge("B", "A")]

    result = clean_cycles(nodes, edges)

    assert _pairs(result.cleaned_edges) == [("C", "B"), ("B", "A")]


def test_input_not_mutated() -> None:
    """Original input lists unchanged after call (pure function property)."""
    nodes = [_rec("A", 5), _rec("B", 5)]
    edges = [_edge("A", "B"), _edge("B", "A")]

    nodes_snapshot = list(nodes)
    edges_snapshot = list(edges)

    clean_cycles(nodes, edges)

    assert nodes == nodes_snapshot
    assert edges == edges_snapshot
    assert len(edges) == 2


# ── CycleCleanResult validator tests (Node 6 prerequisite) ──────────────────


def test_validator_passes_on_clean_cycles_output() -> None:
    """Happy path: results produced by clean_cycles() always satisfy the validator."""
    nodes = [_rec("A", 10), _rec("B", 10), _rec("C", 10)]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")]

    result = clean_cycles(nodes, edges)

    assert result.input_node_ids == frozenset({"A", "B", "C"})
    for e in result.cleaned_edges:
        assert e.source_id in result.input_node_ids
        assert e.target_id in result.input_node_ids


def test_validator_rejects_orphan_source() -> None:
    """Direct construction with an edge whose source_id is absent from the witness raises."""
    edge = _edge("X", "A")
    with pytest.raises(ValidationError) as exc_info:
        CycleCleanResult(
            cleaned_edges=[edge],
            cycle_log=CycleLog(cycles_detected_count=0, iterations=0),
            input_node_ids=frozenset({"A", "B"}),
        )
    msg = str(exc_info.value)
    assert "orphaned source_id" in msg
    assert "'X'" in msg


def test_validator_rejects_orphan_target() -> None:
    """Direct construction with an edge whose target_id is absent from the witness raises."""
    edge = _edge("A", "Y")
    with pytest.raises(ValidationError) as exc_info:
        CycleCleanResult(
            cleaned_edges=[edge],
            cycle_log=CycleLog(cycles_detected_count=0, iterations=0),
            input_node_ids=frozenset({"A", "B"}),
        )
    msg = str(exc_info.value)
    assert "orphaned target_id" in msg
    assert "'Y'" in msg


def test_witness_required_at_construction() -> None:
    """Constructing without input_node_ids raises ValidationError for the missing field."""
    with pytest.raises(ValidationError) as exc_info:
        CycleCleanResult(
            cleaned_edges=[],
            cycle_log=CycleLog(cycles_detected_count=0, iterations=0),
        )
    # Pydantic v2 surfaces missing required fields with type=missing and the
    # field name in the error report.
    assert "input_node_ids" in str(exc_info.value)


def test_model_dump_omits_witness() -> None:
    """Field(exclude=True) keeps the witness out of model_dump() JSON output."""
    nodes = [_rec("A", 5), _rec("B", 5)]
    edges = [_edge("A", "B")]

    result = clean_cycles(nodes, edges)
    dumped = result.model_dump()

    assert "input_node_ids" not in dumped
    assert "cleaned_edges" in dumped
    assert "cycle_log" in dumped


def test_serialization_round_trip_requires_witness() -> None:
    """model_validate(model_dump(result)) raises because the witness was excluded.

    Distinguishes Field(exclude=True) from PrivateAttr (which would round-trip
    silently as the default empty value) and from a factory pattern (which
    would not enforce on plain construction at all). Persistence reload sites
    must re-supply input_node_ids from the loaded node list — the contract
    Node 8 will honor.
    """
    nodes = [_rec("A", 5), _rec("B", 5)]
    edges = [_edge("A", "B")]

    result = clean_cycles(nodes, edges)
    dumped = result.model_dump()

    with pytest.raises(ValidationError) as exc_info:
        CycleCleanResult.model_validate(dumped)
    assert "input_node_ids" in str(exc_info.value)


def test_clean_cycles_populates_witness() -> None:
    """clean_cycles() builds input_node_ids from its nodes parameter."""
    nodes = [_rec("A", 5), _rec("B", 5), _rec("C", 5), _rec("D", 5)]
    edges = [_edge("A", "B"), _edge("C", "D")]

    result = clean_cycles(nodes, edges)

    assert result.input_node_ids == frozenset(n.node_id for n in nodes)
    assert result.input_node_ids == frozenset({"A", "B", "C", "D"})
