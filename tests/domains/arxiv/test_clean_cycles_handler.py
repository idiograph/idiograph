# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the CleanCycles stage is genuinely driven by the declarative Graph:
# the handler, invoked THROUGH core/executor.py::execute_graph on a minimal
# Graph, returns output equal to a direct handler call on the same inputs. The
# executor path is load-bearing in the assertion — the cleaned edge set, the
# cycle log, and the all_cites view are read off `results[<node_id>]`, which
# only exists if execute_graph actually dispatched the handler.
#
# The stage is BOUND: the clean node declares input ports, so the executor
# builds its `inputs` solely from the port-declared edges into it, keyed by
# `to_port`. The graphs here declare ports on both endpoints and carry
# from_port/to_port on every edge — which is also what makes them pass
# validate_integrity's dataflow check.
#
# CleanCycles' input port names are name-identical to AssembleGraph's output
# ports, so the two-node graph below wires assemble.nodes -> clean.nodes and
# assemble.cites -> clean.cites with no adapter. That wiring is exercised here
# because it is the shape the traversal pipeline would take; the executor flip
# itself remains out of scope and run_traversal still calls stages directly.

import asyncio
from unittest.mock import AsyncMock

import pytest

from idiograph.core.executor import (
    HANDLERS,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv.models import (
    CitationEdge,
    CycleCleanResult,
    Node3Result,
    Node4Result,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    ASSEMBLE_GRAPH_INPUT_PORTS,
    ASSEMBLE_GRAPH_OUTPUT_PORTS,
    CLEAN_CYCLES_INPUT_PORTS,
    CLEAN_CYCLES_OUTPUT_PORTS,
    COMPUTE_PAGERANK_INPUT_PORTS,
    COMPUTE_PAGERANK_OUTPUT_PORTS,
    assemble_graph,
    clean_cycles,
    compute_pagerank,
)


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


def _port(name: str) -> PortDeclaration:
    """An untyped port. `port_type` is inert — nothing validates it."""
    return PortDeclaration(name=name, port_type="untyped")


def _rec(
    node_id: str,
    citation_count: int = 0,
    root_ids: list[str] | None = None,
    hop_depth: int = 1,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=root_ids if root_ids is not None else [node_id],
        citation_count=citation_count,
    )


def _edge(source: str, target: str) -> CitationEdge:
    return CitationEdge(source_id=source, target_id=target, type="cites")


def _fixture() -> tuple[list[PaperRecord], list[CitationEdge]]:
    """A fixture that exercises all three outputs non-trivially.

    A→B→A is a 2-cycle (so `cycle_log` records a real suppression and
    `all_cites` differs from `cleaned_edges`), while B→C and C→D survive
    untouched (so `cleaned_edges` carries real content rather than being empty).
    """
    nodes = [_rec(x, citation_count=10) for x in ("A", "B", "C", "D")]
    edges = [_edge("A", "B"), _edge("B", "A"), _edge("B", "C"), _edge("C", "D")]
    return nodes, edges


def _clean_node(node_id: str = "clean") -> Node:
    """The CleanCycles node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="CleanCycles",
        params={},
        input_ports=CLEAN_CYCLES_INPUT_PORTS,
        output_ports=CLEAN_CYCLES_OUTPUT_PORTS,
    )


def _assert_matches_direct(result: dict, direct: dict) -> None:
    """All three declared output ports agree with the direct call."""
    assert result["cleaned_edges"] == direct["cleaned_edges"]
    assert result["cycle_log"] == direct["cycle_log"]
    assert result["all_cites"] == direct["all_cites"]


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """CleanCycles driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    One upstream provider declares both output ports and feeds them over TWO
    port-declared edges into the clean node's two distinct input ports — the
    same-source-many-ports shape, which is only expressible because bound
    inputs are keyed by `to_port` rather than by source node id.
    """
    nodes, edges = _fixture()

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(clean_cycles({}, {"nodes": nodes, "cites": edges}))
    # The fixture is discriminating: all three outputs carry real content, so
    # equality below cannot be satisfied by empty results on either path.
    assert direct["cleaned_edges"]
    assert len(direct["cycle_log"].suppressed_edges) == 1
    assert len(direct["all_cites"]) == len(edges)

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "cites": edges}

    register_handler("CleanTestProvider", _provider)
    register_handler("CleanCycles", clean_cycles)

    graph = Graph(
        name="clean-cycles-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CleanTestProvider",
                params={},
                output_ports=[_port("nodes"), _port("cites")],
            ),
            _clean_node(),
        ],
        edges=[
            Edge(source="src", target="clean", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="clean", type="DATA",
                 from_port="cites", to_port="cites"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the two port-declared edges.
    assert results["clean"]["status"] == "SUCCESS"
    _assert_matches_direct(results["clean"], direct)


def test_assemble_graph_wires_into_clean_cycles_via_execute_graph() -> None:
    """assemble.nodes -> clean.nodes and assemble.cites -> clean.cites.

    The input port names were chosen name-identical to AssembleGraph's outputs
    so the two converted stages compose with no adapter node. Driven THROUGH
    execute_graph, so the executor carries real Node 4.5 input across a real
    port-declared edge pair from the upstream stage rather than from a stub.
    """
    seeds = [_rec("S1", citation_count=10, root_ids=["S1"], hop_depth=0)]
    # P→S1 and S1→P form a 2-cycle once merged, so the downstream stage has
    # something to actually clean.
    n3 = Node3Result(
        papers=[_rec("P", citation_count=10, root_ids=["S1"])],
        edges=[_edge("P", "S1")],
    )
    n4 = Node4Result(
        papers=[_rec("Q", citation_count=10, root_ids=["S1"])],
        edges=[_edge("S1", "P"), _edge("P", "Q")],
    )

    merged = asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )
    direct = asyncio.run(
        clean_cycles({}, {"nodes": merged["nodes"], "cites": merged["cites"]})
    )
    assert len(direct["cycle_log"].suppressed_edges) == 1

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds, "backward": n3, "forward": n4}

    register_handler("CleanTestProvider", _provider)
    register_handler("AssembleGraph", assemble_graph)
    register_handler("CleanCycles", clean_cycles)

    graph = Graph(
        name="assemble-into-clean",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CleanTestProvider",
                params={},
                output_ports=[
                    _port("seeds"),
                    _port("backward"),
                    _port("forward"),
                ],
            ),
            Node(
                id="merge",
                type="AssembleGraph",
                params={},
                input_ports=ASSEMBLE_GRAPH_INPUT_PORTS,
                output_ports=ASSEMBLE_GRAPH_OUTPUT_PORTS,
            ),
            _clean_node(),
        ],
        edges=[
            Edge(source="src", target="merge", type="DATA",
                 from_port="seeds", to_port="seeds"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="backward", to_port="backward"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="forward", to_port="forward"),
            # The point of the name-identical port choice: no adapter here.
            Edge(source="merge", target="clean", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="merge", target="clean", type="DATA",
                 from_port="cites", to_port="cites"),
        ],
    )

    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    assert results["merge"]["status"] == "SUCCESS"
    assert results["clean"]["status"] == "SUCCESS"
    _assert_matches_direct(results["clean"], direct)


def test_three_stage_assemble_clean_pagerank_via_execute_graph() -> None:
    """assemble -> clean -> pagerank, all three stages, one execute_graph run.

    The two-node test above shows one converted stage feeding the next. This
    extends that shape to THREE converted stages in one graph, which is what the
    standing design constraint asked for a demonstration of: the pagerank node
    takes `nodes` from the merge stage and `cleaned_edges` from the cycle-cleaning
    stage, over port-declared edges, with no adapter node anywhere. The port names
    already compose — that composition is the point.

    Non-vacuity is kept in two parts: a cycle is actually suppressed (so the
    cleaning stage did real work), and pagerank over the CLEANED edges genuinely
    differs from pagerank over the raw merged edges (so the `cleaned_edges` port
    is load-bearing in the comparison rather than incidental).
    """
    seeds = [_rec("S1", citation_count=10, root_ids=["S1"], hop_depth=0)]
    # P→S1 and S1→P form a 2-cycle once merged, so the cleaning stage has
    # something to actually clean; P→Q survives to give the chain real structure.
    n3 = Node3Result(
        papers=[_rec("P", citation_count=10, root_ids=["S1"])],
        edges=[_edge("P", "S1")],
    )
    n4 = Node4Result(
        papers=[_rec("Q", citation_count=10, root_ids=["S1"])],
        edges=[_edge("S1", "P"), _edge("P", "Q")],
    )
    params = {"damping": 0.85}

    merged = asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )
    cleaned = asyncio.run(
        clean_cycles({}, {"nodes": merged["nodes"], "cites": merged["cites"]})
    )
    # Non-vacuity, part 1: cleaning actually suppressed a cycle edge.
    assert len(cleaned["cycle_log"].suppressed_edges) == 1

    direct = asyncio.run(
        compute_pagerank(
            params,
            {"nodes": merged["nodes"], "cleaned_edges": cleaned["cleaned_edges"]},
        )
    )
    assert direct["pagerank"]

    # Non-vacuity, part 2: the cleaned edge set and the raw merged edge set give
    # different pagerank, so the equality at the end pins which port was carried.
    on_raw_cites = asyncio.run(
        compute_pagerank(
            params, {"nodes": merged["nodes"], "cleaned_edges": merged["cites"]}
        )
    )
    assert on_raw_cites["pagerank"] != direct["pagerank"]

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds, "backward": n3, "forward": n4}

    register_handler("CleanTestProvider", _provider)
    register_handler("AssembleGraph", assemble_graph)
    register_handler("CleanCycles", clean_cycles)
    register_handler("ComputePagerank", compute_pagerank)

    graph = Graph(
        name="assemble-clean-pagerank",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CleanTestProvider",
                params={},
                output_ports=[
                    _port("seeds"),
                    _port("backward"),
                    _port("forward"),
                ],
            ),
            Node(
                id="merge",
                type="AssembleGraph",
                params={},
                input_ports=ASSEMBLE_GRAPH_INPUT_PORTS,
                output_ports=ASSEMBLE_GRAPH_OUTPUT_PORTS,
            ),
            _clean_node(),
            Node(
                id="pr",
                type="ComputePagerank",
                params=params,
                input_ports=COMPUTE_PAGERANK_INPUT_PORTS,
                output_ports=COMPUTE_PAGERANK_OUTPUT_PORTS,
            ),
        ],
        edges=[
            Edge(source="src", target="merge", type="DATA",
                 from_port="seeds", to_port="seeds"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="backward", to_port="backward"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="forward", to_port="forward"),
            Edge(source="merge", target="clean", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="merge", target="clean", type="DATA",
                 from_port="cites", to_port="cites"),
            # No adapter on either hop into pagerank: `nodes` comes straight from
            # the merge stage, `cleaned_edges` from the cycle-cleaning stage.
            Edge(source="merge", target="pr", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="clean", target="pr", type="DATA",
                 from_port="cleaned_edges", to_port="cleaned_edges"),
        ],
    )

    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    assert results["merge"]["status"] == "SUCCESS"
    assert results["clean"]["status"] == "SUCCESS"
    assert results["pr"]["status"] == "SUCCESS"
    # The three-stage chain reproduces the direct-call result exactly.
    _assert_matches_direct(results["clean"], cleaned)
    assert results["pr"]["pagerank"] == direct["pagerank"]


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires CleanCycles to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["CleanCycles"] is clean_cycles


def test_declared_ports_are_the_returned_keys() -> None:
    """The returned mapping's keys are exactly CLEAN_CYCLES_OUTPUT_PORTS.

    Pins the declaration against the implementation: a port added to the list
    without a matching return key (or vice versa) fails here rather than at
    some downstream consumer.
    """
    nodes, edges = _fixture()

    result = asyncio.run(clean_cycles({}, {"nodes": nodes, "cites": edges}))

    assert set(result) == {p.name for p in CLEAN_CYCLES_OUTPUT_PORTS}
    assert {p.name for p in CLEAN_CYCLES_INPUT_PORTS} == {"nodes", "cites"}


def test_witness_is_not_a_port_but_result_reassembles() -> None:
    """The witness is absent from the ports and rebuilt from the bound nodes.

    `input_node_ids` is exclude=True structural metadata, not a declared output
    port. Consumers that need a CycleCleanResult back — `run_traversal` at
    result assembly, the registry on reload — rebuild it from the node set they
    bound to the `nodes` port. This is that reassembly, and it must validate.
    """
    nodes, edges = _fixture()

    result = asyncio.run(clean_cycles({}, {"nodes": nodes, "cites": edges}))
    assert "input_node_ids" not in result

    reassembled = CycleCleanResult(
        cleaned_edges=result["cleaned_edges"],
        cycle_log=result["cycle_log"],
        input_node_ids=frozenset(n.node_id for n in nodes),
    )

    assert reassembled.input_node_ids == frozenset({"A", "B", "C", "D"})
    assert reassembled.cleaned_edges == result["cleaned_edges"]
    assert reassembled.cycle_log == result["cycle_log"]


# ── The run_traversal consumer of those ports ───────────────────────────────
#
# One orchestrator-level test lives here rather than in the orchestrator suite
# because what it guards is a property of how this stage's ports are consumed:
# `run_traversal` rebuilds the non-port witness from the node set it bound to
# the `nodes` port. Today that binding and `unified_nodes` are the same object,
# so nothing else in the suite would notice the difference — which is exactly
# why the guard is worth its weight.


def test_run_traversal_witness_binds_to_nodes_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reassembled witness comes from the bound `nodes` port, not from the
    `unified_nodes` free variable that Node 5.5 rebinds.

    Node 5.5 reassigns `unified_nodes` to its annotated output. Here that output
    DROPS a node, so the two diverge: a witness rebuilt from the rebound list
    would omit "A" while cleaned_edges still references it, and the
    CycleCleanResult reconstruction would raise "orphaned ... A". Passing proves
    the witness tracked the port binding instead.
    """
    from idiograph.domains.arxiv import pipeline
    from idiograph.domains.arxiv.models import (
        BackwardParameters,
        CoCitationParameters,
        ForwardParameters,
        LLMConfig,
        PipelineParameters,
    )
    from idiograph.domains.arxiv.relationship_annotation import (
        RelationshipAnnotationResult,
        RelationshipProvenance,
    )

    seeds = [_rec("S", citation_count=50, root_ids=["S"], hop_depth=0)]
    # 2-cycle S↔A, so cycle cleaning does real work and cleaned_edges retains
    # an edge touching "A" whichever direction is suppressed.
    n3 = Node3Result(
        papers=[_rec("A", citation_count=10, root_ids=["S"])],
        edges=[_edge("A", "S"), _edge("S", "A")],
    )
    n4 = Node4Result(papers=[], edges=[])

    # Node 3 is a port-declared handler — its stand-in returns the declared
    # output ports, not a bare Node3Result.
    monkeypatch.setattr(
        pipeline,
        "backward_traverse",
        AsyncMock(return_value={"backward": n3, "failed_batches": n3.failed_batches}),
    )
    monkeypatch.setattr(pipeline, "forward_traverse", AsyncMock(return_value=n4))

    async def _annotate_dropping_a(params, inputs, *, resources):
        """Stands in for the converted Node 5.5 handler, in its own contract:
        `(params, inputs, *, resources)` in, the declared output ports out."""
        llm = params["llm"]
        result = RelationshipAnnotationResult(
            nodes=[n for n in inputs["nodes"] if n.node_id != "A"],
            provenance=RelationshipProvenance(
                model_id=llm.model_id,
                prompt_template_hash=llm.prompt_template_hash,
                temperature=llm.temperature,
                max_tokens=llm.max_tokens,
            ),
        )
        return {"nodes": result.nodes, "provenance": result.provenance}

    monkeypatch.setattr(
        pipeline, "annotate_relationships", _annotate_dropping_a
    )

    params = PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10, lambda_decay=0.1, alpha=1.0, beta=1.0,
            sort="cited_by_count:desc",
        ),
        co_citation=CoCitationParameters(min_strength=1, max_edges=None),
        llm=LLMConfig(
            model_id="claude-haiku-4-5-20251001",
            prompt_template_hash="x" * 64,
        ),
    )

    result = asyncio.run(
        pipeline.run_traversal(
            seeds, params,
            client=object(), api_key="k", anthropic_client=object(),
        )
    )

    # The pre-5.5 bound node set — NOT the annotated list, which dropped "A".
    assert result.cycle_clean.input_node_ids == frozenset({"S", "A"})
    for e in result.cycle_clean.cleaned_edges:
        assert e.source_id in result.cycle_clean.input_node_ids
        assert e.target_id in result.cycle_clean.input_node_ids
