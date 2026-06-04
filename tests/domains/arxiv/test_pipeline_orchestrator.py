# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock

import networkx as nx
import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CitationEdge,
    CoCitationParameters,
    EdgeMetadataMismatch,
    FailedBatch,
    FailedSeed,
    ForwardParameters,
    Node3Result,
    Node4Result,
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    TruncatedSeed,
)
from idiograph.domains.arxiv.pipeline import (
    PipelineError,
    assemble_graph,
    run_arxiv_pipeline,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


_CLIENT = object()  # sentinel — every network stage is mocked, so it is unused.


def _rec(
    node_id: str,
    root_ids: list[str] | None = None,
    hop_depth: int = 1,
    citation_count: int = 0,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=root_ids if root_ids is not None else [node_id],
        citation_count=citation_count,
    )


def _seed(node_id: str) -> PaperRecord:
    """A resolved seed: hop_depth=0, root_ids=[node_id]."""
    return _rec(node_id, root_ids=[node_id], hop_depth=0)


def _edge(
    source: str,
    target: str,
    type: str = "cites",
    citing_paper_year: int | None = None,
    strength: int | None = None,
) -> CitationEdge:
    return CitationEdge(
        source_id=source,
        target_id=target,
        type=type,
        citing_paper_year=citing_paper_year,
        strength=strength,
    )


def _params(
    min_strength: int = 2, max_edges: int | None = None
) -> PipelineParameters:
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        co_citation=CoCitationParameters(
            min_strength=min_strength, max_edges=max_edges
        ),
    )


def _install_stages(
    monkeypatch: pytest.MonkeyPatch,
    resolved: list[PaperRecord],
    failures: list[dict],
    n3: Node3Result,
    n4: Node4Result,
) -> None:
    """Mock the three network-bound stages (Node 0, Node 3, Node 4).

    The orchestrator is a composer; tests inject constructed node outputs rather
    than exercising OpenAlex. The pure whole-graph stages (4.5/5/6/7) run for
    real over the injected graph.
    """
    monkeypatch.setattr(
        pipeline, "fetch_seeds", AsyncMock(return_value=(resolved, failures))
    )
    monkeypatch.setattr(
        pipeline, "backward_traverse", AsyncMock(return_value=n3)
    )
    monkeypatch.setattr(
        pipeline, "forward_traverse", AsyncMock(return_value=n4)
    )


def _run(
    parameters: PipelineParameters | None = None,
    seeds: list[dict] | None = None,
) -> PipelineResult:
    return asyncio.run(
        run_arxiv_pipeline(
            seeds if seeds is not None else [{"arxiv_id": "x"}],
            parameters if parameters is not None else _params(),
            client=_CLIENT,
            api_key="k",
        )
    )


def _component_count(result: PipelineResult) -> int:
    g = nx.Graph()
    g.add_nodes_from(n.node_id for n in result.nodes)
    g.add_edges_from((e.source_id, e.target_id) for e in result.edges)
    return nx.number_connected_components(g)


# ── Happy path ──────────────────────────────────────────────────────────────


def test_single_seed_minimal_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """One seed; small backward/forward results; full pipeline; invariants hold."""
    s = _seed("S")
    b1 = _rec("B1", root_ids=["S"])
    f1 = _rec("F1", root_ids=["S"])
    n3 = Node3Result(papers=[b1], edges=[_edge("S", "B1")])
    n4 = Node4Result(papers=[f1], edges=[_edge("F1", "S")])
    _install_stages(monkeypatch, [s], [], n3, n4)

    params = _params()
    result = _run(params)

    assert len(result.nodes) >= len(result.seeds)
    assert result.seeds == ["S"]
    for node in result.nodes:
        assert node.node_id in result.depth_metrics
        assert node.node_id in result.pagerank
        assert node.node_id in result.communities.community_assignments
        assert node.community_id == result.communities.community_assignments[
            node.node_id
        ]
        assert node.pagerank == result.pagerank[node.node_id]
    assert result.co_citation_edges == [
        e for e in result.edges if e.type == "co_citation"
    ]
    assert result.cycle_clean.cleaned_edges == [
        e for e in result.edges if e.type == "cites"
    ]
    seed_node = next(n for n in result.nodes if n.node_id == "S")
    assert "S" in seed_node.root_ids
    assert result.parameters is params


def test_multi_seed_disjoint_neighborhoods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two seeds, no shared papers; both neighborhoods present; two components."""
    s1, s2 = _seed("S1"), _seed("S2")
    b1 = _rec("B1", root_ids=["S1"])
    b2 = _rec("B2", root_ids=["S2"])
    n3 = Node3Result(
        papers=[b1, b2], edges=[_edge("S1", "B1"), _edge("S2", "B2")]
    )
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s1, s2], [], n3, n4)

    result = _run()

    by_id = {n.node_id: n for n in result.nodes}
    assert by_id["B1"].root_ids == ["S1"]
    assert by_id["B2"].root_ids == ["S2"]
    assert _component_count(result) == 2


def test_multi_seed_shared_paper_root_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Node 3 paper carrying both seeds' root_ids is preserved with both."""
    s1, s2 = _seed("S1"), _seed("S2")
    p = _rec("P", root_ids=["S1", "S2"])
    n3 = Node3Result(papers=[p], edges=[_edge("S1", "P"), _edge("S2", "P")])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s1, s2], [], n3, n4)

    result = _run()

    p_node = next(n for n in result.nodes if n.node_id == "P")
    assert p_node.root_ids == ["S1", "S2"]


# ── Graph merge (assemble_graph) ────────────────────────────────────────────


def test_seeds_appear_in_nodes() -> None:
    """Every resolved seed appears in unified nodes with itself in root_ids."""
    seeds = [_seed("S1"), _seed("S2")]
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])

    nodes, _cites, _mismatches = assemble_graph(seeds, n3, n4)

    by_id = {n.node_id: n for n in nodes}
    assert {"S1", "S2"} <= set(by_id)
    assert by_id["S1"].root_ids == ["S1"]
    assert by_id["S2"].root_ids == ["S2"]


def test_merge_dedup_node_backward_and_forward() -> None:
    """Same paper in both n3.papers and n4.papers → one node, roots unioned."""
    seeds = [_seed("S1"), _seed("S2")]
    p_back = _rec("P", root_ids=["S1"])
    p_fwd = _rec("P", root_ids=["S2"])
    n3 = Node3Result(papers=[p_back], edges=[_edge("S1", "P")])
    n4 = Node4Result(papers=[p_fwd], edges=[_edge("P", "S2")])

    nodes, _cites, _mismatches = assemble_graph(seeds, n3, n4)

    p_nodes = [n for n in nodes if n.node_id == "P"]
    assert len(p_nodes) == 1
    assert p_nodes[0].root_ids == ["S1", "S2"]


def test_merge_dedup_edge_backward_and_forward() -> None:
    """Same (source, target, type) edge in both sources → one edge."""
    seeds = [_seed("S")]
    p = _rec("P", root_ids=["S"])
    n3 = Node3Result(papers=[p], edges=[_edge("P", "S")])
    n4 = Node4Result(papers=[p], edges=[_edge("P", "S")])

    _nodes, cites, mismatches = assemble_graph(seeds, n3, n4)

    matching = [
        e for e in cites if (e.source_id, e.target_id, e.type) == ("P", "S", "cites")
    ]
    assert len(matching) == 1
    assert mismatches == []


def test_merge_edge_metadata_consistency() -> None:
    """Same edge from both sources with identical metadata → no mismatch."""
    seeds = [_seed("S")]
    p = _rec("P", root_ids=["S"])
    edge = _edge("P", "S", citing_paper_year=2020)
    n3 = Node3Result(papers=[p], edges=[edge])
    n4 = Node4Result(papers=[p], edges=[_edge("P", "S", citing_paper_year=2020)])

    _nodes, _cites, mismatches = assemble_graph(seeds, n3, n4)

    assert mismatches == []


def test_merge_edge_metadata_mismatch() -> None:
    """Same edge key, differing metadata → backward kept, one mismatch recorded."""
    seeds = [_seed("S")]
    p = _rec("P", root_ids=["S"])
    n3 = Node3Result(
        papers=[p], edges=[_edge("P", "S", citing_paper_year=2020)]
    )
    n4 = Node4Result(
        papers=[p], edges=[_edge("P", "S", citing_paper_year=2021)]
    )

    _nodes, cites, mismatches = assemble_graph(seeds, n3, n4)

    kept = next(
        e for e in cites if (e.source_id, e.target_id, e.type) == ("P", "S", "cites")
    )
    assert kept.citing_paper_year == 2020  # first-seen (backward) wins
    assert len(mismatches) == 1
    assert isinstance(mismatches[0], EdgeMetadataMismatch)
    assert (mismatches[0].source_id, mismatches[0].target_id) == ("P", "S")


# ── Failure provenance (read off result objects, not exceptions) ────────────


def test_node_0_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_seeds returns one resolved + one failure → pipeline continues."""
    s = _seed("S")
    failures = [{"seed": {"arxiv_id": "bad"}, "reason": "no results"}]
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], failures, n3, n4)

    result = _run()

    assert len(result.seed_failures) == 1
    assert result.seed_failures[0].seed == {"arxiv_id": "bad"}
    assert result.seed_failures[0].reason == "no results"


def test_node_0_total_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_seeds raises ValueError (all seeds fail) → orchestrator propagates."""
    monkeypatch.setattr(
        pipeline,
        "fetch_seeds",
        AsyncMock(side_effect=ValueError("All seeds failed to resolve")),
    )

    with pytest.raises(ValueError):
        _run()


def test_node_3_failed_batches_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """n3 carries a FailedBatch → surfaced; affected seed remains a root."""
    s = _seed("S")
    batch = FailedBatch(
        requested_ids=["W1", "W2"], stage="depth_1", reason="http_error: 503"
    )
    n3 = Node3Result(papers=[], edges=[], failed_batches=[batch])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    result = _run()

    assert result.backward_failed_batches == [batch]
    seed_node = next(n for n in result.nodes if n.node_id == "S")
    assert "S" in seed_node.root_ids


def test_node_4_failed_seeds_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """n4 carries a FailedSeed → surfaced in forward_failed_seeds."""
    s = _seed("S")
    failed = FailedSeed(seed_id="S", reason="http_error: 503")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[], failed_seeds=[failed])
    _install_stages(monkeypatch, [s], [], n3, n4)

    result = _run()

    assert result.forward_failed_seeds == [failed]


def test_node_4_truncated_seeds_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """n4 carries a TruncatedSeed → surfaced; result otherwise complete."""
    s = _seed("S")
    trunc = TruncatedSeed(seed_id="S", returned_count=200, total_count=512)
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[], truncated_seeds=[trunc])
    _install_stages(monkeypatch, [s], [], n3, n4)

    result = _run()

    assert result.truncated_seeds == [trunc]
    assert result.nodes  # graph still produced
    assert "S" in result.depth_metrics


# ── Seeds-only / empty traversal ────────────────────────────────────────────


def test_seeds_only_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both backward and forward empty → valid seeds-only result, no halt."""
    s = _seed("S")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    result = _run()

    assert [n.node_id for n in result.nodes] == ["S"]
    assert result.cycle_clean.cleaned_edges == []
    assert "S" in result.depth_metrics
    assert "S" in result.communities.community_assignments


def test_empty_backward_nonempty_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward empty, forward non-empty → graph from forward + seeds."""
    s = _seed("S")
    f1 = _rec("F1", root_ids=["S"])
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[f1], edges=[_edge("F1", "S")])
    _install_stages(monkeypatch, [s], [], n3, n4)

    result = _run()

    ids = {n.node_id for n in result.nodes}
    assert ids == {"S", "F1"}


# ── End-of-pipeline enrichment ──────────────────────────────────────────────


def _enrichment_fixture(monkeypatch: pytest.MonkeyPatch) -> PipelineResult:
    s = _seed("S")
    b1 = _rec("B1", root_ids=["S"])
    f1 = _rec("F1", root_ids=["S"])
    n3 = Node3Result(papers=[b1], edges=[_edge("S", "B1")])
    n4 = Node4Result(papers=[f1], edges=[_edge("F1", "S")])
    _install_stages(monkeypatch, [s], [], n3, n4)
    return _run()


def test_enrichment_pagerank_matches_per_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _enrichment_fixture(monkeypatch)
    for node in result.nodes:
        assert node.pagerank == result.pagerank[node.node_id]


def test_enrichment_community_matches_per_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _enrichment_fixture(monkeypatch)
    for node in result.nodes:
        assert (
            node.community_id
            == result.communities.community_assignments[node.node_id]
        )


def test_enrichment_depth_matches_per_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _enrichment_fixture(monkeypatch)
    for node in result.nodes:
        dm = result.depth_metrics[node.node_id]
        assert node.traversal_direction == dm.traversal_direction
        assert node.hop_depth_per_root == dm.hop_depth_per_root


# ── Whole-graph stage failures ──────────────────────────────────────────────


def test_node_4_5_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Node 4.5 raises → orchestrator does not catch; no PipelineResult."""
    s = _seed("S")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("cycle cleaning blew up")

    monkeypatch.setattr(pipeline, "clean_cycles", _boom)

    with pytest.raises(RuntimeError):
        _run()


def test_node_7_missing_extra_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Node 7 raises RuntimeError (missing [community] extra) → propagates."""
    s = _seed("S")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("Neither infomap nor leidenalg is installed.")

    monkeypatch.setattr(pipeline, "detect_communities", _boom)

    with pytest.raises(RuntimeError):
        _run()


# ── Round-trip / validation / determinism ───────────────────────────────────


def test_pipeline_result_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """PipelineResult survives model_dump → model_validate with Node 8's
    input_node_ids reconstruction; all fields (including provenance lists)
    preserved."""
    s = _seed("S")
    a = _rec("A", root_ids=["S"])
    b = _rec("B", root_ids=["S"])
    c = _rec("C", root_ids=["S"])
    n3 = Node3Result(
        papers=[a, b, c],
        edges=[_edge("S", "C"), _edge("C", "A"), _edge("C", "B")],
        failed_batches=[
            FailedBatch(requested_ids=["W9"], stage="depth_2", reason="timeout")
        ],
    )
    n4 = Node4Result(
        papers=[],
        # Same (C, A, cites) key as backward but different metadata → mismatch.
        edges=[_edge("C", "A", citing_paper_year=1999)],
        failed_seeds=[FailedSeed(seed_id="S", reason="http_error: 503")],
        truncated_seeds=[
            TruncatedSeed(seed_id="S", returned_count=200, total_count=500)
        ],
    )
    failures = [{"seed": {"doi": "bad"}, "reason": "no results"}]
    _install_stages(monkeypatch, [s], failures, n3, n4)

    result = _run(_params(min_strength=1))

    # Provenance lists actually exercised.
    assert len(result.seed_failures) == 1
    assert len(result.backward_failed_batches) == 1
    assert len(result.forward_failed_seeds) == 1
    assert len(result.truncated_seeds) == 1
    assert len(result.data_integrity_warnings) == 1
    assert len(result.co_citation_edges) >= 1

    dumped = result.model_dump()
    # Node 8 reload path: re-supply the excluded input_node_ids witness from the
    # loaded node list before reconstructing the embedded CycleCleanResult.
    dumped["cycle_clean"]["input_node_ids"] = [
        n["node_id"] for n in dumped["nodes"]
    ]
    restored = PipelineResult.model_validate(dumped)

    assert restored.model_dump() == result.model_dump()
    assert restored.seed_failures == result.seed_failures
    assert restored.data_integrity_warnings == result.data_integrity_warnings
    assert restored.co_citation_edges == result.co_citation_edges


def test_empty_seeds_raises() -> None:
    """seeds=[] raises ValueError before any work (pre-check)."""
    with pytest.raises(ValueError):
        _run(seeds=[])


@pytest.mark.repeat(3)
def test_deterministic_same_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same (seeds, parameters) twice → identical PipelineResult."""
    s = _seed("S")
    b1 = _rec("B1", root_ids=["S"])
    c = _rec("C", root_ids=["S"])
    n3 = Node3Result(
        papers=[b1, c], edges=[_edge("S", "C"), _edge("C", "B1")]
    )
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    params = _params(min_strength=1)
    first = _run(params)
    second = _run(params)

    assert first.model_dump() == second.model_dump()


def test_run_arxiv_pipeline_is_pure_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PipelineError guards the should-not-happen empty-resolved-without-raising
    case (a Node 0 contract violation), distinct from normal total failure."""
    monkeypatch.setattr(
        pipeline, "fetch_seeds", AsyncMock(return_value=([], []))
    )

    with pytest.raises(PipelineError):
        _run()
