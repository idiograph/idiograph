# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging

import pytest

from idiograph.domains.arxiv.models import (
    CitationEdge,
    CommunityResult,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    clean_cycles,
    detect_communities,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _clean(nodes: list[PaperRecord], edges: list[CitationEdge]) -> dict:
    """Call the bound ``clean_cycles`` handler from a sync test.

    Marshals into the stage's declared contract — one key per declared input
    port, empty ``params`` — and returns the declared output ports.
    `asyncio.run` is the repo's async-from-sync convention (no async plugin).
    """
    return asyncio.run(clean_cycles({}, {"nodes": nodes, "cites": edges}))


def _detect(
    nodes: list[PaperRecord],
    edges: list[CitationEdge],
    **params: object,
) -> CommunityResult:
    """Call the bound ``detect_communities`` handler from a sync test.

    Marshals into the stage's declared contract — one key per declared input
    port (``nodes`` / ``all_cites``), the tunables as ``params`` — and unwraps
    the single declared output port so these tests keep asserting against a
    ``CommunityResult``. Omitted tunables fall back to the
    ``CommunitiesParameters`` defaults, exactly as the executor path does.
    """
    return asyncio.run(
        detect_communities(params, {"nodes": nodes, "all_cites": edges})
    )["communities"]


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

    result = _detect(nodes, edges)

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

    result = _detect(nodes, edges)

    assert result.community_count == len(set(result.community_assignments.values()))


def test_isolate_receives_assignment() -> None:
    """Node with no edges is not omitted from output."""
    # X is an isolate — no edges touch it.
    nodes = [_rec(x) for x in ("A", "B", "C", "X")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = _detect(nodes, edges)

    assert "X" in result.community_assignments


def test_community_id_is_string() -> None:
    """All values in community_assignments are strings."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    edges = [_edge("A", "B"), _edge("C", "D")]

    result = _detect(nodes, edges)

    assert result.community_assignments  # precondition
    for cid in result.community_assignments.values():
        assert isinstance(cid, str)


def test_algorithm_used_set() -> None:
    """algorithm_used is "infomap" or "leiden" — never None."""
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = _detect(nodes, edges)

    assert result.algorithm_used in ("infomap", "leiden")


def test_validation_flags_empty_within_bounds() -> None:
    """No flags when community count is between min and max."""
    # Two clearly separate components → infomap finds ≥ 2 communities.
    # Bounds [1, 10] bracket that comfortably.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    result = _detect(
        nodes, edges, community_count_min=1, community_count_max=10
    )

    assert result.validation_flags == []


def test_validation_flag_below_minimum() -> None:
    """`community_count_below_minimum` flag when count < min."""
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    # community_count_min=100 forces below-minimum regardless of how many
    # communities infomap finds on this small graph.
    result = _detect(
        nodes, edges, community_count_min=100, community_count_max=200
    )

    assert "community_count_below_minimum" in result.validation_flags


def test_validation_flag_above_maximum() -> None:
    """`community_count_above_maximum` flag when count > max."""
    # Two disjoint components produce ≥ 2 communities; cap at 1 → flag fires.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    result = _detect(
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
        result = _detect(nodes, edges)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("Z" in r.getMessage() for r in warnings)
    # The unknown node_id surfaces as structured data, not just a log line.
    assert "Z" in result.warnings
    # All known nodes still receive assignments.
    assert set(result.community_assignments.keys()) == {"A", "B", "C"}


def test_empty_nodes() -> None:
    """Empty input returns empty community_assignments, community_count=0."""
    result = _detect([], [])

    assert result.community_assignments == {}
    assert result.community_count == 0


def test_single_node_no_edges() -> None:
    """Single node with no edges receives an assignment."""
    nodes = [_rec("A")]

    result = _detect(nodes, [])

    assert "A" in result.community_assignments
    assert result.community_count == 1


def test_disconnected_graph() -> None:
    """Multiple disconnected components all receive assignments."""
    # Three components: {A,B}, {C,D}, {E}.
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("C", "D")]

    result = _detect(nodes, edges)

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

    r1 = _detect(nodes, edges)
    r2 = _detect(nodes, edges)

    assert r1.community_assignments == r2.community_assignments
    assert r1.algorithm_used == r2.algorithm_used
    assert r1.community_count == r2.community_count


def test_suppressed_originals_merge() -> None:
    """The `all_cites` port produces correct Node 7 input.

    The cleaned ∪ suppressed-originals merge is no longer reproduced here: it is
    a declared output port of the cycle-cleaning stage, so this test reads the
    same edge set the pipeline orchestrator now reads — the full citation
    topology — off the returned mapping.
    """
    # Build a small graph with a 2-cycle so clean_cycles suppresses one edge.
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    raw_edges = [
        _edge("A", "B"),
        _edge("B", "A"),  # cycle with A→B
        _edge("B", "C"),
        _edge("C", "D"),
    ]

    cycle_result = _clean(nodes, raw_edges)
    # precondition: a cycle was suppressed
    assert cycle_result["cycle_log"].suppressed_edges

    all_cites = cycle_result["all_cites"]
    assert len(all_cites) == len(raw_edges)  # nothing dropped by the merge

    result = _detect(nodes, all_cites)

    # Every node still receives an assignment after the merge.
    assert set(result.community_assignments.keys()) == {"A", "B", "C", "D"}
    assert result.algorithm_used in ("infomap", "leiden")


def test_validation_flags_always_list() -> None:
    """validation_flags is a list — never None — on clean run."""
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = _detect(nodes, edges)

    assert isinstance(result.validation_flags, list)


def test_warnings_always_list() -> None:
    """warnings is a list — never None — on a clean run with no unknown ids."""
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    result = _detect(nodes, edges)

    assert isinstance(result.warnings, list)
    assert result.warnings == []


# Beyond the spec §Tests minimum set — these close coverage gaps for the
# fallback paths (Leiden activation, RuntimeError on no-libraries) that the
# spec's 15-test set does not exercise in normal runs.


def _patch_imports(monkeypatch: pytest.MonkeyPatch, fail: set[str]) -> None:
    """Make `from <name> import ...` (and `import <name>`) raise ImportError
    for any module name in ``fail``. Other imports pass through normally."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name in fail:
            raise ImportError(f"simulated: {name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_leiden_fallback_when_infomap_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Infomap ImportError routes to the Leiden path; result is valid."""
    _patch_imports(monkeypatch, fail={"infomap"})

    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    edges = [_edge("A", "B"), _edge("C", "D")]

    result = _detect(
        nodes, edges, community_count_min=1, community_count_max=10
    )

    assert result.algorithm_used == "leiden"
    assert set(result.community_assignments.keys()) == {"A", "B", "C", "D"}
    assert result.community_count == len(set(result.community_assignments.values()))


def test_raises_when_neither_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ImportErrors → RuntimeError with the install-extras message."""
    _patch_imports(monkeypatch, fail={"infomap", "leidenalg", "igraph"})

    nodes = [_rec("A"), _rec("B")]
    edges = [_edge("A", "B")]

    with pytest.raises(RuntimeError, match="uv sync --extra community"):
        _detect(nodes, edges)
