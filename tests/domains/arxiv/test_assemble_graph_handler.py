# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the AssembleGraph stage is genuinely driven by the declarative Graph:
# the handler, invoked THROUGH core/executor.py::execute_graph on a minimal
# Graph, returns output equal to a direct handler call on the same inputs. The
# executor path is load-bearing in the assertion — the merged node/edge/mismatch
# sets are read off `results[<node_id>]`, which only exists if execute_graph
# actually dispatched the handler.
#
# The stage is BOUND: the merge node declares input ports, so the executor
# builds its `inputs` solely from the port-declared edges into it, keyed by
# `to_port`. The graphs here declare ports on both endpoints and carry
# from_port/to_port on every edge — which is also what makes them pass
# validate_integrity's dataflow check.
#
# AssembleGraph is tested alone in its own minimal graph. It is NOT wired to the
# ComputePagerank node here: pagerank declares two input ports and feeding only
# one of them lands in a separate, open design question about unfed declared
# ports.

import asyncio

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
    Node3Result,
    Node4Result,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    ASSEMBLE_GRAPH_INPUT_PORTS,
    ASSEMBLE_GRAPH_OUTPUT_PORTS,
    assemble_graph,
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


def _seed(node_id: str) -> PaperRecord:
    """A resolved seed: hop_depth=0, root_ids=[node_id]."""
    return _rec(node_id, root_ids=[node_id], hop_depth=0)


def _edge(
    source: str,
    target: str,
    citing_paper_year: int | None = None,
) -> CitationEdge:
    return CitationEdge(
        source_id=source,
        target_id=target,
        type="cites",
        citing_paper_year=citing_paper_year,
    )


def _fixture() -> tuple[list[PaperRecord], Node3Result, Node4Result]:
    """A merge fixture that exercises all three outputs non-trivially.

    Paper P is reached from both directions under different roots (so `nodes`
    carries a real root_ids union), edge P→S1 appears in both sources with
    DIFFERING metadata (so `mismatches` is non-empty), and the forward source
    contributes an edge and a paper the backward source does not.
    """
    seeds = [_seed("S1"), _seed("S2")]
    n3 = Node3Result(
        papers=[_rec("P", root_ids=["S1"]), _rec("B", root_ids=["S1"])],
        edges=[_edge("P", "S1", citing_paper_year=2020), _edge("S1", "B")],
    )
    n4 = Node4Result(
        papers=[_rec("P", root_ids=["S2"]), _rec("F", root_ids=["S2"])],
        edges=[_edge("P", "S1", citing_paper_year=2021), _edge("F", "S2")],
    )
    return seeds, n3, n4


def _merge_node(node_id: str = "merge") -> Node:
    """The AssembleGraph node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="AssembleGraph",
        params={},
        input_ports=ASSEMBLE_GRAPH_INPUT_PORTS,
        output_ports=ASSEMBLE_GRAPH_OUTPUT_PORTS,
    )


def _assert_matches_direct(result: dict, direct: dict) -> None:
    """All three declared output ports agree with the direct call."""
    assert result["nodes"] == direct["nodes"]
    assert result["cites"] == direct["cites"]
    assert result["mismatches"] == direct["mismatches"]


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """AssembleGraph driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    One upstream provider declares all three output ports and feeds them over
    THREE port-declared edges into the merge node's three distinct input ports —
    the same-source-many-ports shape, which is only expressible because bound
    inputs are keyed by `to_port` rather than by source node id.
    """
    seeds, n3, n4 = _fixture()

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )
    # The fixture is discriminating: all three outputs carry real content, so
    # equality below cannot be satisfied by empty results on either path.
    assert {n.node_id for n in direct["nodes"]} == {"S1", "S2", "P", "B", "F"}
    assert direct["cites"]
    assert len(direct["mismatches"]) == 1

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds, "backward": n3, "forward": n4}

    register_handler("AssembleTestProvider", _provider)
    register_handler("AssembleGraph", assemble_graph)

    graph = Graph(
        name="assemble-graph-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="AssembleTestProvider",
                params={},
                output_ports=[
                    _port("seeds"),
                    _port("backward"),
                    _port("forward"),
                ],
            ),
            _merge_node(),
        ],
        edges=[
            Edge(source="src", target="merge", type="DATA",
                 from_port="seeds", to_port="seeds"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="backward", to_port="backward"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="forward", to_port="forward"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the three port-declared edges.
    assert results["merge"]["status"] == "SUCCESS"
    _assert_matches_direct(results["merge"], direct)


def test_three_upstreams_disjoint_ports_via_execute_graph() -> None:
    """Each declared input port arrives from a DIFFERENT upstream node, over its
    own port-declared DATA edge.

    This is the shape the traversal pipeline would take if the surrounding
    stages were bound too — seeds, backward, and forward are produced by three
    separate nodes. Driven THROUGH execute_graph so the executor's binding path
    is exercised across three upstreams; the result equals the single-payload
    direct call on the same data.
    """
    seeds, n3, n4 = _fixture()

    direct = asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )

    async def _seeds_provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds}

    async def _backward_provider(_params: dict, _inputs: dict) -> dict:
        return {"backward": n3}

    async def _forward_provider(_params: dict, _inputs: dict) -> dict:
        return {"forward": n4}

    register_handler("SeedsProvider", _seeds_provider)
    register_handler("BackwardProvider", _backward_provider)
    register_handler("ForwardProvider", _forward_provider)
    register_handler("AssembleGraph", assemble_graph)

    graph = Graph(
        name="assemble-graph-three-upstreams",
        version="1.0",
        nodes=[
            Node(id="ssrc", type="SeedsProvider", params={},
                 output_ports=[_port("seeds")]),
            Node(id="bsrc", type="BackwardProvider", params={},
                 output_ports=[_port("backward")]),
            Node(id="fsrc", type="ForwardProvider", params={},
                 output_ports=[_port("forward")]),
            _merge_node(),
        ],
        edges=[
            Edge(source="ssrc", target="merge", type="DATA",
                 from_port="seeds", to_port="seeds"),
            Edge(source="bsrc", target="merge", type="DATA",
                 from_port="backward", to_port="backward"),
            Edge(source="fsrc", target="merge", type="DATA",
                 from_port="forward", to_port="forward"),
        ],
    )

    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    assert results["merge"]["status"] == "SUCCESS"
    _assert_matches_direct(results["merge"], direct)


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires AssembleGraph to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["AssembleGraph"] is assemble_graph


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given.
    """
    seeds, n3, n4 = _fixture()

    inputs = {
        "bogus": "not-a-declared-port",
        "seeds": seeds,
        "backward": n3,
        "forward": n4,
    }

    result = asyncio.run(assemble_graph({}, inputs))

    assert {n.node_id for n in result["nodes"]} == {"S1", "S2", "P", "B", "F"}


def test_params_are_ignored() -> None:
    """The stage takes no configuration — a populated `params` changes nothing.

    Pins the deliberate absence of a parameters model for this handler: whatever
    is handed in as `params` is inert, unlike ComputePagerank's `damping`.
    """
    seeds, n3, n4 = _fixture()
    payload = {"seeds": seeds, "backward": n3, "forward": n4}

    empty = asyncio.run(assemble_graph({}, payload))
    populated = asyncio.run(assemble_graph({"damping": 0.5, "bogus": 1}, payload))

    _assert_matches_direct(populated, empty)
