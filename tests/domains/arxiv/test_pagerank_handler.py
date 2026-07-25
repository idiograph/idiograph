# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the ComputePagerank stage is genuinely driven by the declarative Graph:
# the handler, invoked THROUGH core/executor.py::execute_graph on a minimal
# Graph, returns output equal to a direct handler call on the same inputs. The
# executor path is load-bearing in the assertion — the pagerank mapping is read
# off `results[<node_id>]`, which only exists if execute_graph actually
# dispatched the handler.
#
# The stage is now BOUND: the pagerank node declares input ports, so the
# executor builds its `inputs` solely from the port-declared edges into it,
# keyed by `to_port`. The graphs here declare ports on both endpoints and carry
# from_port/to_port on every edge — which is also what makes them pass
# validate_integrity's dataflow check.

import asyncio

import pytest

from idiograph.core.executor import (
    HANDLERS,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv.models import CitationEdge, PaperRecord
from idiograph.domains.arxiv.pipeline import (
    COMPUTE_PAGERANK_INPUT_PORTS,
    COMPUTE_PAGERANK_OUTPUT_PORTS,
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


def _pagerank_node(node_id: str = "pr", params: dict | None = None) -> Node:
    """The ComputePagerank node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="ComputePagerank",
        params=params or {},
        input_ports=COMPUTE_PAGERANK_INPUT_PORTS,
        output_ports=COMPUTE_PAGERANK_OUTPUT_PORTS,
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """ComputePagerank driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    One upstream provider declares both output ports and feeds them over TWO
    port-declared edges into the pagerank node's two distinct input ports — the
    same-source-two-ports shape, which is only expressible because bound inputs
    are keyed by `to_port` rather than by source node id.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C"), _rec("D")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D"), _edge("D", "A")]
    params = {"damping": 0.85}

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        compute_pagerank(params, {"nodes": nodes, "cleaned_edges": edges})
    )
    assert direct["pagerank"]  # non-empty — the graph has real structure

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "cleaned_edges": edges}

    register_handler("PagerankTestProvider", _provider)
    register_handler("ComputePagerank", compute_pagerank)

    graph = Graph(
        name="pagerank-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="PagerankTestProvider",
                params={},
                output_ports=[_port("nodes"), _port("cleaned_edges")],
            ),
            _pagerank_node(params=params),
        ],
        edges=[
            Edge(source="src", target="pr", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="pr", type="DATA",
                 from_port="cleaned_edges", to_port="cleaned_edges"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the two port-declared edges.
    assert results["pr"]["status"] == "SUCCESS"
    assert results["pr"]["pagerank"] == direct["pagerank"]


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires ComputePagerank to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["ComputePagerank"] is compute_pagerank


def test_omitting_damping_matches_explicit_default() -> None:
    """Omitting ``damping`` from params yields the same output as passing
    ``damping=0.85`` explicitly — pinning the default that now lives in
    ``PageRankParameters`` (it moved out of the old function signature, and no
    other test would catch it drifting).

    The fixture is a 3-node chain A -> B -> C, which is damping-sensitive: the
    guard assertion below confirms a different damping value produces a genuinely
    different result, so equality above is a real check, not a fixture that would
    match regardless of the value.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("A", "B"), _edge("B", "C")]
    payload = {"nodes": nodes, "cleaned_edges": edges}

    omitted = asyncio.run(compute_pagerank({}, payload))
    explicit = asyncio.run(compute_pagerank({"damping": 0.85}, payload))
    assert omitted["pagerank"] == explicit["pagerank"]

    # Fixture discriminates: a different damping must change the result, else the
    # equality above would hold for any default.
    other = asyncio.run(compute_pagerank({"damping": 0.5}, payload))
    assert other["pagerank"] != explicit["pagerank"]


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Replaces the old non-dict-payload test, which pinned the isinstance guard in
    the deleted `_gather_pagerank_inputs` scan. Under the bound contract the
    executor never hands the handler a foreign payload, but the handler still
    reads only its declared ports out of whatever mapping it is given.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    inputs = {
        "bogus": "not-a-declared-port",
        "nodes": nodes,
        "cleaned_edges": edges,
    }

    result = asyncio.run(compute_pagerank({"damping": 0.85}, inputs))
    assert result["pagerank"]
    assert set(result["pagerank"]) == {"A", "B", "C"}


def test_two_upstreams_disjoint_ports_via_execute_graph() -> None:
    """Stage-2 shape: ``nodes`` arrives from one upstream node and
    ``cleaned_edges`` from another, over two port-declared DATA edges.

    Driven THROUGH execute_graph so the executor's binding path is exercised
    across two upstreams — each edge binds its own declared port, and the result
    equals the single-payload direct call on the same data.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C"), _rec("D")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D"), _edge("D", "A")]
    params = {"damping": 0.85}

    direct = asyncio.run(
        compute_pagerank(params, {"nodes": nodes, "cleaned_edges": edges})
    )
    assert direct["pagerank"]

    async def _nodes_provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes}

    async def _edges_provider(_params: dict, _inputs: dict) -> dict:
        return {"cleaned_edges": edges}

    register_handler("NodesProvider", _nodes_provider)
    register_handler("EdgesProvider", _edges_provider)
    register_handler("ComputePagerank", compute_pagerank)

    graph = Graph(
        name="pagerank-two-upstreams-disjoint",
        version="1.0",
        nodes=[
            Node(id="nsrc", type="NodesProvider", params={},
                 output_ports=[_port("nodes")]),
            Node(id="esrc", type="EdgesProvider", params={},
                 output_ports=[_port("cleaned_edges")]),
            _pagerank_node(params=params),
        ],
        edges=[
            Edge(source="nsrc", target="pr", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="esrc", target="pr", type="DATA",
                 from_port="cleaned_edges", to_port="cleaned_edges"),
        ],
    )

    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph))

    assert results["pr"]["status"] == "SUCCESS"
    assert results["pr"]["pagerank"] == direct["pagerank"]


def test_two_edges_into_one_port_is_rejected_by_validation() -> None:
    """Two upstreams both bound to the SAME input port ``nodes`` is INVALID.

    Binding settled the old first-wins-over-``inputs.values()`` indeterminacy by
    keying inputs on ``to_port`` — which made this collision deterministic
    (assignment follows edge order, so the last such edge wins) and, in the same
    stroke, made it indefensible: edge-list order is not a thing the graph
    declares, so the winner is not readable off the declarations. The open
    question this test used to carry is closed. ``validate_integrity`` rejects
    the shape outright.

    The executor is deliberately unchanged: this is statically detectable, so it
    is validation's job, and an unvalidated graph still runs last-assign.
    """
    nodes_a = [_rec("A"), _rec("B")]
    nodes_x = [_rec("X"), _rec("Y")]

    async def _provider_a(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes_a, "cleaned_edges": []}

    async def _provider_x(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes_x}

    register_handler("ProviderA", _provider_a)
    register_handler("ProviderX", _provider_x)
    register_handler("ComputePagerank", compute_pagerank)

    graph = Graph(
        name="pagerank-two-edges-one-port",
        version="1.0",
        nodes=[
            Node(id="pa", type="ProviderA", params={},
                 output_ports=[_port("nodes"), _port("cleaned_edges")]),
            Node(id="px", type="ProviderX", params={},
                 output_ports=[_port("nodes")]),
            _pagerank_node(),
        ],
        edges=[
            Edge(source="pa", target="pr", type="DATA",
                 from_port="cleaned_edges", to_port="cleaned_edges"),
            Edge(source="pa", target="pr", type="DATA",
                 from_port="nodes", to_port="nodes"),
            # Second claimant on `nodes` — the collision validation now rejects.
            Edge(source="px", target="pr", type="DATA",
                 from_port="nodes", to_port="nodes"),
        ],
    )

    result = validate_integrity(graph)

    assert result["valid"] is False
    collisions = [e for e in result["errors"] if "takes exactly one incoming edge" in e]
    assert len(collisions) == 1
    assert "input port 'nodes'" in collisions[0]
    assert "pa.nodes" in collisions[0]
    assert "px.nodes" in collisions[0]
    # The `cleaned_edges` port, claimed once, is not implicated.
    assert "cleaned_edges" not in collisions[0]
