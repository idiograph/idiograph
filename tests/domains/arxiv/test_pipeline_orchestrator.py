# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock

import networkx as nx
import pytest

from idiograph.core.executor import HANDLERS
from idiograph.domains.arxiv import pipeline, pipeline_graph
from idiograph.domains.arxiv.handlers import register_arxiv_handlers
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CitationEdge,
    CoCitationParameters,
    CycleCleanResult,
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
    PipelineHaltError,
    assemble_graph,
    run_arxiv_pipeline,
)
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph

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
        # Stated, never read from the clock: it enters the content address, so a
        # wall-clock value would move every address in this file on New Year.
        current_year=2026,
        co_citation=CoCitationParameters(
            min_strength=min_strength, max_edges=max_edges
        ),
    )


def _install_stage(
    monkeypatch: pytest.MonkeyPatch,
    attr: str,
    node_type: str,
    stand_in: object,
) -> object:
    """Install one stand-in in BOTH places, as the SAME object (IDG-089 rider 1).

    Post-flip, `run_traversal` dispatches every stage through the HANDLERS
    registry; the module attribute is what any surviving direct path — and the
    pre-flip reference implementation this suite differences against — still
    reads. A harness that patched only one of the two would silently drive the
    real handler down the other path, which for a network-bound stage means an
    OpenAlex call against a sentinel client.

    ONE object in both slots, never two equal ones: every spy assertion in this
    suite (`assert_not_called`, call counts) has to answer for the whole run
    regardless of which path invoked the stage.
    """
    register_arxiv_handlers()  # populate HANDLERS before overriding an entry
    monkeypatch.setattr(pipeline, attr, stand_in)
    monkeypatch.setitem(HANDLERS, node_type, stand_in)
    return stand_in


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

    Node 0 is patched at `fetch_seeds`, INSIDE the `resolve_seeds` handler,
    rather than at the handler itself: resolution runs above `run_traversal` on
    the direct path, and `run_traversal` injects its output into the graph rather
    than dispatching the node. Patching the network call underneath serves both.
    """
    monkeypatch.setattr(
        pipeline, "fetch_seeds", AsyncMock(return_value=(resolved, failures))
    )
    # Nodes 3 and 4 are port-declared handlers: they return their declared output
    # ports, not a bare Node3Result/Node4Result. The stand-ins return the same
    # mappings the real handlers do, so the port reads are exercised for real.
    _install_stage(
        monkeypatch,
        "backward_traverse",
        "BackwardTraverse",
        AsyncMock(return_value={"backward": n3, "failed_batches": n3.failed_batches}),
    )
    _install_stage(
        monkeypatch,
        "forward_traverse",
        "ForwardTraverse",
        AsyncMock(
            return_value={
                "forward": n4,
                "failed_seeds": n4.failed_seeds,
                "truncated_seeds": n4.truncated_seeds,
            }
        ),
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


def _merge(
    seeds: list[PaperRecord],
    n3: Node3Result,
    n4: Node4Result,
) -> dict:
    """Call the bound ``assemble_graph`` handler from a sync test.

    The stage is now an async port-bound handler, so these merge-semantics tests
    marshal into its declared contract — one key per declared input port, empty
    ``params`` — and read the declared output ports off the returned mapping.
    `asyncio.run` is the repo's async-from-sync convention (no async plugin).
    """
    return asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )


def test_seeds_appear_in_nodes() -> None:
    """Every resolved seed appears in unified nodes with itself in root_ids."""
    seeds = [_seed("S1"), _seed("S2")]
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])

    nodes = _merge(seeds, n3, n4)["nodes"]

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

    nodes = _merge(seeds, n3, n4)["nodes"]

    p_nodes = [n for n in nodes if n.node_id == "P"]
    assert len(p_nodes) == 1
    assert p_nodes[0].root_ids == ["S1", "S2"]


def test_merge_dedup_edge_backward_and_forward() -> None:
    """Same (source, target, type) edge in both sources → one edge."""
    seeds = [_seed("S")]
    p = _rec("P", root_ids=["S"])
    n3 = Node3Result(papers=[p], edges=[_edge("P", "S")])
    n4 = Node4Result(papers=[p], edges=[_edge("P", "S")])

    merged = _merge(seeds, n3, n4)
    cites, mismatches = merged["cites"], merged["mismatches"]

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

    mismatches = _merge(seeds, n3, n4)["mismatches"]

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

    merged = _merge(seeds, n3, n4)
    cites, mismatches = merged["cites"], merged["mismatches"]

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
    """Node 4.5 raises → orchestrator does not catch; no PipelineResult.

    Post-flip the executor SWALLOWS the raise and records the node FAILED; what
    keeps this assertion true is `run_traversal`'s halt scan, which re-raises as
    `PipelineHaltError` (a RuntimeError) `from` the original. The promise the
    test pins — a raising whole-graph stage yields no partial result — is
    unchanged; only who re-raises it moved.
    """
    s = _seed("S")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("cycle cleaning blew up")

    _install_stage(monkeypatch, "clean_cycles", "CleanCycles", _boom)

    with pytest.raises(RuntimeError):
        _run()


def test_node_4_5_failure_preserves_the_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The halt carries the failing node and the ORIGINAL exception as __cause__.

    `execute_graph` reports a raising handler as a FAILED result rather than
    letting it out, so without deliberate care the type and traceback would be
    flattened to `str(e)` on the way through. This is what says they are not:
    the raised `PipelineHaltError` names the node that failed and chains the
    original object.
    """
    s = _seed("S")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    boom = RuntimeError("cycle cleaning blew up")

    async def _raise(*_args, **_kwargs):
        raise boom

    _install_stage(monkeypatch, "clean_cycles", "CleanCycles", _raise)

    with pytest.raises(PipelineHaltError) as excinfo:
        _run()

    assert excinfo.value.node_id == "clean"
    assert "cycle cleaning blew up" in excinfo.value.error
    # The original object, not a re-creation: type and traceback survive.
    assert excinfo.value.__cause__ is boom
    # The whole results dict rides along, so a caller can see how far it got.
    assert excinfo.value.results["clean"]["status"] == "FAILED"


def test_node_7_missing_extra_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Node 7 raises RuntimeError (missing [community] extra) → propagates."""
    s = _seed("S")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    _install_stages(monkeypatch, [s], [], n3, n4)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("Neither infomap nor leidenalg is installed.")

    _install_stage(monkeypatch, "detect_communities", "DetectCommunities", _boom)

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


# ── The pre-execution integrity gate ─────────────────────────────────────────


def test_a_graph_that_does_not_validate_is_never_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dataflow defect halts BEFORE any handler runs.

    `validate_integrity` reads the graph and knows nothing about the run, so an
    input port fed by no edge is knowable without executing anything. Running the
    reachable prefix first and discovering it at the unfed node would mean having
    already spent the OpenAlex traversal calls, which are the expensive part of
    this pipeline — so the gate is placed before `execute_graph`, and this is
    what says so. The spy is the assertion: not merely that it raised, but that
    nothing was dispatched.
    """
    s = _seed("S")
    _install_stages(
        monkeypatch, [s], [], Node3Result(papers=[], edges=[]),
        Node4Result(papers=[], edges=[]),
    )

    # The real graph minus one edge — `depth`'s `cleaned_edges` port is left
    # unfed. Derived from the real builder rather than hand-built, so this stays
    # a test of the GATE and cannot drift into a test of a bespoke fixture.
    real = build_pipeline_graph([{"arxiv_id": "x"}], _params())
    broken = real.model_copy(
        update={
            "edges": [
                e
                for e in real.edges
                if not (e.target == "depth" and e.to_port == "cleaned_edges")
            ]
        }
    )
    monkeypatch.setattr(
        pipeline_graph, "build_pipeline_graph", lambda *_a, **_k: broken
    )

    with pytest.raises(PipelineError, match="does not validate"):
        _run()

    pipeline.backward_traverse.assert_not_called()
    pipeline.forward_traverse.assert_not_called()


def test_witness_rebuild_refuses_a_graph_with_no_declared_producer() -> None:
    """The witness helper raises rather than returning None into the result.

    Unreachable through `run_traversal` — the gate above rejects such a graph
    first — which is exactly why the guard is asserted here directly. A helper
    that silently returned None for a missing edge would put `None` where the
    node set belongs and fail much later, somewhere that does not name the cause.
    """
    real = build_pipeline_graph([{"arxiv_id": "x"}], _params())
    without_clean_nodes = real.model_copy(
        update={
            "edges": [
                e
                for e in real.edges
                if not (e.target == "clean" and e.to_port == "nodes")
            ]
        }
    )

    with pytest.raises(PipelineError, match="feeds no edge into"):
        pipeline._declared_producer_output(
            without_clean_nodes, {}, "clean", "nodes"
        )


# ── The differential: flipped run_traversal vs. the hand-written reference ────
#
# THE LOAD-BEARING CHECK on the executor flip (IDG-075 clause 4e). `run_traversal`
# used to call the eleven stages by hand; it now builds the declared
# `build_pipeline_graph` and drives it through `execute_graph`. Nothing in the
# type system says the two produce the same pipeline — the graph could wire a
# port to the wrong producer, marshal a params value onto the wrong stage, or
# read a provenance field off the wrong node, and every one of those still
# validates green and still returns a well-formed `PipelineResult`. Only running
# both and comparing the results catches it.
#
# So the PRE-FLIP orchestrator is kept alive here as a reference implementation
# and the two are run over the same fixtures. This is a scaffold with a stated
# expiry, not a permanent second orchestrator: delete
# `_reference_run_traversal` and `test_flip_matches_the_reference_implementation`
# once the flip has held.


async def _reference_run_traversal(
    resolved: list[PaperRecord],
    parameters: PipelineParameters,
    *,
    client: object,
    api_key: str,
    anthropic_client: object | None = None,
) -> PipelineResult:
    """The PRE-FLIP `run_traversal`, copied from
    `src/idiograph/domains/arxiv/pipeline.py::run_traversal` at 5294945.

    DELETE WHEN THE FLIP HAS HELD. This exists only to be differenced against
    the post-flip implementation; it is not a second production path and nothing
    but the differential test below may call it.

    The executable body is the original verbatim. Two deliberate differences,
    both mechanical:

      - Every stage is reached as `pipeline.<name>` rather than as a bare name,
        so a `monkeypatch.setattr(pipeline, ...)` stand-in is picked up here. The
        flipped path reads the HANDLERS registry instead, which is why every
        harness in this suite installs the same mock object in both places — that
        is what lets one fixture drive both legs of the differential.
      - The `_log` calls, and the three provenance locals read only to log them
        (`n3_failed_batches`, `n4_failed_seeds`, `n4_truncated_seeds`), are
        dropped. Logging is not output-determining, and the differential compares
        results. The long per-call comments justifying each hand-marshalled
        params/inputs mapping go with them: they argued that the direct call
        agreed with the node's declared contract, and post-flip that agreement is
        not argued but executed. Every comment carrying a LOAD-BEARING ordering
        or binding fact — the Node 5.5 rebinding, the witness — is kept.
    """
    n3_ports = await pipeline.backward_traverse(
        {
            "n_backward": parameters.backward.n_backward,
            "lambda_decay": parameters.backward.lambda_decay,
            "sleep_ms": pipeline.BACKWARD_SLEEP_MS,
            "current_year": parameters.current_year,
        },
        {"seeds": resolved},
        resources={"http_client": client, "openalex_api_key": api_key},
    )
    n3 = n3_ports["backward"]

    n4_ports = await pipeline.forward_traverse(
        {
            "n_forward": parameters.forward.n_forward,
            "lambda_decay": parameters.forward.lambda_decay,
            "alpha": parameters.forward.alpha,
            "beta": parameters.forward.beta,
            "sort": parameters.forward.sort,
            "acceleration_method": pipeline.FORWARD_ACCELERATION_METHOD,
            "current_year": parameters.current_year,
        },
        {"seeds": resolved},
        resources={"http_client": client, "openalex_api_key": api_key},
    )
    n4 = n4_ports["forward"]

    merged = await pipeline.assemble_graph(
        {},
        {"seeds": resolved, "backward": n3, "forward": n4},
    )
    unified_nodes = merged["nodes"]
    unified_cites = merged["cites"]
    mismatches = merged["mismatches"]

    # The bound mapping is held in a local because the `nodes` port binding is
    # also what the result-assembly witness is built from. Node 5.5 below rebinds
    # `unified_nodes` to annotated copies, so the witness must come from what was
    # bound HERE, not from that free variable later.
    clean_inputs = {"nodes": unified_nodes, "cites": unified_cites}
    cleaned = await pipeline.clean_cycles({}, clean_inputs)
    cleaned_edges = cleaned["cleaned_edges"]
    cycle_log = cleaned["cycle_log"]
    all_cites = cleaned["all_cites"]

    # The `nodes` port binds `unified_nodes` HERE, before the Node 5.5 block
    # below may rebind that free variable. The stage order is load-bearing: this
    # call does not move past 5.5.
    co = await pipeline.compute_co_citations(
        {
            "min_strength": parameters.co_citation.min_strength,
            "max_edges": parameters.co_citation.max_edges,
        },
        {"nodes": unified_nodes, "all_cites": all_cites},
    )
    co_citation_edges = co["co_citation_edges"]
    co_citation_warnings = co["co_citation_warnings"]

    if parameters.llm is not None:
        if anthropic_client is None:
            raise ValueError(
                "run_traversal: parameters.llm is set but anthropic_client is "
                "None — Node 5.5 requires an injected AsyncAnthropic client "
                "(IDG-024 keyword-only injection)."
            )
        ann = await pipeline.annotate_relationships(
            {"llm": parameters.llm},
            {"nodes": unified_nodes, "resolved": resolved},
            resources={"anthropic_client": anthropic_client},
        )
        unified_nodes = ann["nodes"]

    # `cleaned_edges`, not `all_cites`: depth needs the acyclic view. `nodes`
    # binds whichever `unified_nodes` is in scope HERE — 5.5 above may have
    # rebound it, and depth is a consumer of that rebinding.
    depth = (
        await pipeline.compute_depth_metrics(
            {},
            {"nodes": unified_nodes, "cleaned_edges": cleaned_edges},
        )
    )["depth_metrics"]

    prank = (
        await pipeline.compute_pagerank(
            {"damping": parameters.pagerank.damping},
            {"nodes": unified_nodes, "cleaned_edges": cleaned_edges},
        )
    )["pagerank"]

    # `all_cites`, not `cleaned_edges`: clustering keeps the real-but-suppressed
    # citations, the same view Node 5 takes.
    communities = (
        await pipeline.detect_communities(
            {
                "infomap_seed": parameters.communities.infomap_seed,
                "infomap_trials": parameters.communities.infomap_trials,
                "infomap_teleportation": parameters.communities.infomap_teleportation,
                "leiden_seed": parameters.communities.leiden_seed,
                "community_count_min": parameters.communities.community_count_min,
                "community_count_max": parameters.communities.community_count_max,
            },
            {"nodes": unified_nodes, "all_cites": all_cites},
        )
    )["communities"]

    enriched_nodes = (
        await pipeline.enrich_nodes(
            {},
            {
                "nodes": unified_nodes,
                "depth_metrics": depth,
                "pagerank": prank,
                "communities": communities,
            },
        )
    )["enriched_nodes"]

    # Suppressed originals are NOT in `edges` — they live in
    # cycle_log.suppressed_edges for audit.
    merged_edges = cleaned_edges + co_citation_edges

    # The witness is NOT a port, so it is rebuilt from the node set bound to the
    # `nodes` port — the `clean_inputs` binding above, deliberately not the
    # `unified_nodes` free variable, which 5.5 may since have rebound.
    cycle_clean = CycleCleanResult(
        cleaned_edges=cleaned_edges,
        cycle_log=cycle_log,
        input_node_ids=frozenset(n.node_id for n in clean_inputs["nodes"]),
    )

    return PipelineResult(
        nodes=enriched_nodes,
        edges=merged_edges,
        seeds=[s.node_id for s in resolved],
        cycle_clean=cycle_clean,
        co_citation_edges=co_citation_edges,
        co_citation_warnings=co_citation_warnings,
        depth_metrics=depth,
        pagerank=prank,
        communities=communities,
        parameters=parameters,
        seed_failures=[],
        backward_failed_batches=n3.failed_batches,
        forward_failed_seeds=n4.failed_seeds,
        truncated_seeds=n4.truncated_seeds,
        data_integrity_warnings=mismatches,
    )


#: (label, resolved seeds, Node 0 failures, n3, n4, parameters) — the fixtures
#: both legs of the differential are run over. Deliberately NOT just the minimal
#: one: a wrong graph is most likely to still agree on a single seed with an
#: empty traversal, and to diverge exactly where the pipeline has structure —
#: several seeds, real cycles to clean, co-citation edges that pass the strength
#: filter, and every provenance list non-empty.
def _differential_cases() -> list[tuple]:
    s = _seed("S")
    s1, s2 = _seed("S1"), _seed("S2")
    a, b, c = _rec("A", root_ids=["S"]), _rec("B", root_ids=["S"]), _rec("C", root_ids=["S"])

    return [
        (
            "single-seed-minimal",
            [s],
            [],
            Node3Result(papers=[_rec("B1", root_ids=["S"])], edges=[_edge("S", "B1")]),
            Node4Result(papers=[_rec("F1", root_ids=["S"])], edges=[_edge("F1", "S")]),
            _params(),
        ),
        (
            "multi-seed-disjoint",
            [s1, s2],
            [],
            Node3Result(
                papers=[_rec("B1", root_ids=["S1"]), _rec("B2", root_ids=["S2"])],
                edges=[_edge("S1", "B1"), _edge("S2", "B2")],
            ),
            Node4Result(papers=[], edges=[]),
            _params(),
        ),
        (
            "multi-seed-shared-paper",
            [s1, s2],
            [],
            Node3Result(
                papers=[_rec("P", root_ids=["S1", "S2"])],
                edges=[_edge("S1", "P"), _edge("S2", "P")],
            ),
            Node4Result(papers=[], edges=[]),
            _params(min_strength=1),
        ),
        (
            # Every provenance carrier non-empty at once, plus a cycle to clean
            # and a metadata mismatch — the case where reading a field off the
            # wrong node's output port shows up.
            "partial-failure-and-cycles",
            [s],
            [{"seed": {"doi": "bad"}, "reason": "no results"}],
            Node3Result(
                papers=[a, b, c],
                edges=[
                    _edge("S", "C"),
                    _edge("C", "A"),
                    _edge("C", "B"),
                    # A ↔ C 2-cycle, so cycle cleaning does real work.
                    _edge("A", "C"),
                ],
                failed_batches=[
                    FailedBatch(requested_ids=["W9"], stage="depth_2", reason="timeout")
                ],
            ),
            Node4Result(
                papers=[],
                # Same (C, A, cites) key as backward, different metadata → mismatch.
                edges=[_edge("C", "A", citing_paper_year=1999)],
                failed_seeds=[FailedSeed(seed_id="S", reason="http_error: 503")],
                truncated_seeds=[
                    TruncatedSeed(seed_id="S", returned_count=200, total_count=500)
                ],
            ),
            _params(min_strength=1),
        ),
        (
            # A DIRECTED 3-cycle S→X→Y→S, which is the case that makes the
            # cleaned/all_cites split OBSERVABLE downstream. The split is the one
            # piece of this wiring that a wrong graph gets wrong while still
            # validating green — `cleaned_edges` and `all_cites` are both real
            # declared ports of `clean`, so feeding depth or pagerank the wrong
            # one is a legal graph describing a different pipeline.
            #
            # It has to be a directed cycle of length ≥ 3. A reciprocal pair
            # (A→C plus C→A) suppresses an edge too, but `hop_depth_per_root` is
            # computed over the UNDIRECTED view, where removing one direction of
            # a reciprocal pair changes nothing — so a fixture built on one would
            # leave depth's binding untested and this differential would pass
            # over a mis-wired graph.
            "directed-cycle-splits-cleaned-from-all",
            [s],
            [],
            Node3Result(
                papers=[_rec("X", root_ids=["S"]), _rec("Y", root_ids=["S"])],
                edges=[_edge("S", "X"), _edge("X", "Y"), _edge("Y", "S")],
            ),
            Node4Result(papers=[], edges=[]),
            _params(min_strength=1),
        ),
        (
            "seeds-only-empty-traversal",
            [s],
            [],
            Node3Result(papers=[], edges=[]),
            Node4Result(papers=[], edges=[]),
            _params(),
        ),
    ]


@pytest.mark.parametrize(
    ("resolved", "failures", "n3", "n4", "parameters"),
    [case[1:] for case in _differential_cases()],
    ids=[case[0] for case in _differential_cases()],
)
def test_flip_matches_the_reference_implementation(
    monkeypatch: pytest.MonkeyPatch,
    resolved: list[PaperRecord],
    failures: list[dict],
    n3: Node3Result,
    n4: Node4Result,
    parameters: PipelineParameters,
) -> None:
    """The executor-driven `run_traversal` equals the hand-written original.

    Field-level equality on the DUMPED models, not identity: the two legs build
    separate object graphs from the same inputs, so identity would fail on
    equal-and-correct output. `model_dump()` is also what `content_address`
    consumes, so agreement here is agreement on what gets stored.

    Both legs run over the SAME `_install_stages` fixture. That is only possible
    because the harness installs each stand-in on the module attribute (which the
    reference reads) AND in the HANDLERS registry (which the flipped path reads),
    as the same object — so neither leg is running against a different Node 3/4
    than the other.
    """
    _install_stages(monkeypatch, resolved, failures, n3, n4)

    flipped = asyncio.run(
        pipeline.run_traversal(
            resolved,
            parameters,
            seed_requests=[{"arxiv_id": "x"}],
            client=_CLIENT,
            api_key="k",
        )
    )
    reference = asyncio.run(
        _reference_run_traversal(
            resolved, parameters, client=_CLIENT, api_key="k"
        )
    )

    assert flipped.model_dump() == reference.model_dump(), (
        "the executor-driven run_traversal and the pre-flip reference disagree. "
        "The declared graph describes a different pipeline than the one "
        "run_traversal used to run by hand — check the port wiring, the params "
        "marshalled onto each node, and which node each provenance field is "
        "read off."
    )
