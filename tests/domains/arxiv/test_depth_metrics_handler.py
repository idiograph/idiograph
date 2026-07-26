# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the ComputeDepthMetrics stage is genuinely driven by the declarative
# Graph: the handler, invoked THROUGH core/executor.py::execute_graph on a
# minimal Graph, returns output equal to a direct handler call on the same
# inputs. The executor path is load-bearing in the assertion — the depth metrics
# are read off `results[<node_id>]`, which only exists if execute_graph actually
# dispatched the handler.
#
# The stage is BOUND: the depth node declares input ports, so the executor builds
# its `inputs` solely from the port-declared edges into it, keyed by `to_port`.
# The graphs here declare ports on both endpoints and carry from_port/to_port on
# every edge — which is also what makes them pass validate_integrity's dataflow
# check.
#
# The wiring test at the bottom consumes a port an earlier conversion created:
# `clean.cleaned_edges -> depth.cleaned_edges`. It is built to prove that
# specific port is the one carried, not merely to survive it — its fixture makes
# the suppressed edge change BOTH the hop distances and the direction labels, so
# wiring `depth` to `all_cites` instead would produce a different result and fail.

import asyncio

import pytest
from pydantic import ValidationError

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
    CLEAN_CYCLES_INPUT_PORTS,
    CLEAN_CYCLES_OUTPUT_PORTS,
    COMPUTE_DEPTH_METRICS_INPUT_PORTS,
    COMPUTE_DEPTH_METRICS_OUTPUT_PORTS,
    assemble_graph,
    clean_cycles,
    compute_depth_metrics,
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


def _shape(metrics: dict) -> dict[str, tuple[dict[str, int], str]]:
    """The whole depth result as plain comparable data, per node."""
    return {
        nid: (m.hop_depth_per_root, m.traversal_direction)
        for nid, m in metrics.items()
    }


def _fixture() -> tuple[list[PaperRecord], list[CitationEdge]]:
    """A chain seeded at S: S→A→B, so both declared metric fields vary.

    Hop distances span 0/1/2 and the directions are not all the same value, so
    an equality assertion against this result cannot be satisfied by a degenerate
    or empty output.
    """
    nodes = [_rec("S", root_ids=["S"], hop_depth=0), _rec("A", root_ids=["S"]),
             _rec("B", root_ids=["S"])]
    edges = [_edge("S", "A"), _edge("A", "B")]
    return nodes, edges


def _depth_node(node_id: str = "depth", params: dict | None = None) -> Node:
    """The ComputeDepthMetrics node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="ComputeDepthMetrics",
        params=params or {},
        input_ports=COMPUTE_DEPTH_METRICS_INPUT_PORTS,
        output_ports=COMPUTE_DEPTH_METRICS_OUTPUT_PORTS,
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """ComputeDepthMetrics driven through execute_graph on a minimal Graph
    returns output equal to the direct handler call on the same inputs.

    One upstream provider declares both output ports and feeds them over TWO
    port-declared edges into the depth node's two distinct input ports — the
    same-source-two-ports shape, which is only expressible because bound inputs
    are keyed by `to_port` rather than by source node id.
    """
    nodes, edges = _fixture()

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        compute_depth_metrics({}, {"nodes": nodes, "cleaned_edges": edges})
    )
    # The fixture is discriminating: every node carries metrics and the two
    # declared fields both vary, so equality below cannot be satisfied by an
    # empty or degenerate result.
    assert _shape(direct["depth_metrics"]) == {
        "S": ({"S": 0}, "seed"),
        "A": ({"S": 1}, "backward"),
        "B": ({"S": 2}, "backward"),
    }

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "cleaned_edges": edges}

    register_handler("DepthTestProvider", _provider)
    register_handler("ComputeDepthMetrics", compute_depth_metrics)

    graph = Graph(
        name="depth-metrics-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="DepthTestProvider",
                params={},
                output_ports=[_port("nodes"), _port("cleaned_edges")],
            ),
            _depth_node(),
        ],
        edges=[
            Edge(source="src", target="depth", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="depth", type="DATA",
                 from_port="cleaned_edges", to_port="cleaned_edges"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the two port-declared edges.
    assert results["depth"]["status"] == "SUCCESS"
    assert results["depth"]["depth_metrics"] == direct["depth_metrics"]


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires ComputeDepthMetrics to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["ComputeDepthMetrics"] is compute_depth_metrics


def test_declared_ports_are_the_returned_keys() -> None:
    """The returned mapping's keys are exactly COMPUTE_DEPTH_METRICS_OUTPUT_PORTS.

    Pins the declaration against the implementation: a port added to the list
    without a matching return key (or vice versa) fails here rather than at some
    downstream consumer. The output port name is deliberately the one
    ``PipelineResult`` consumes the payload by — this is what holds that choice
    in place.
    """
    nodes, edges = _fixture()

    result = asyncio.run(
        compute_depth_metrics({}, {"nodes": nodes, "cleaned_edges": edges})
    )

    assert set(result) == {p.name for p in COMPUTE_DEPTH_METRICS_OUTPUT_PORTS}
    assert {p.name for p in COMPUTE_DEPTH_METRICS_INPUT_PORTS} == {
        "nodes",
        "cleaned_edges",
    }
    # The empty-node early return still answers on the declared port rather than
    # collapsing to a bare `{}`.
    empty = asyncio.run(
        compute_depth_metrics({}, {"nodes": [], "cleaned_edges": []})
    )
    assert empty == {"depth_metrics": {}}


def test_params_are_unused() -> None:
    """This stage takes NO configuration — `params` is read for nothing.

    No parameters model exists for it, so there is no validation step that would
    reject a foreign mapping either. Passing junk must be indistinguishable from
    passing nothing; if a params read were ever introduced, this is what notices.
    """
    nodes, edges = _fixture()
    inputs = {"nodes": nodes, "cleaned_edges": edges}

    empty = asyncio.run(compute_depth_metrics({}, inputs))
    junk = asyncio.run(
        compute_depth_metrics(
            {"damping": 0.5, "min_strength": 99, "unknown": object()}, inputs
        )
    )

    assert junk["depth_metrics"] == empty["depth_metrics"]


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given. `all_cites` is the pointed choice: it is a real port on
    the upstream cycle-cleaning stage and the one this handler deliberately does
    NOT read, because depth needs the acyclic view.
    """
    nodes, edges = _fixture()

    inputs = {
        "bogus": "not-a-declared-port",
        # Would add S↔B if it were read, collapsing B's distance from 2 to 1.
        "all_cites": edges + [_edge("B", "S")],
        "nodes": nodes,
        "cleaned_edges": edges,
    }

    result = asyncio.run(compute_depth_metrics({}, inputs))

    assert result["depth_metrics"]["B"].hop_depth_per_root == {"S": 2}
    assert result["depth_metrics"]["B"].traversal_direction == "backward"


def test_missing_declared_port_fails_validation() -> None:
    """An absent declared input port is a validation failure, not a silent empty.

    The executor only omits a port when the graph left it unwired, so this is the
    unfed-port case surfacing at the handler boundary rather than turning into a
    plausible-looking empty result — which matters especially here, where an
    empty `nodes` is itself a legitimate input that returns an empty mapping.
    """
    nodes, _edges = _fixture()

    with pytest.raises(ValidationError, match="cleaned_edges"):
        asyncio.run(compute_depth_metrics({}, {"nodes": nodes}))

    with pytest.raises(ValidationError, match="nodes"):
        asyncio.run(compute_depth_metrics({}, {"cleaned_edges": []}))


def test_algorithm_valueerrors_are_preserved() -> None:
    """The two ValueError contracts survive the boundary conversion.

    No-roots-in-nodes and unreachable-from-every-root are the stage's only
    raises, and they fire from inside the handler rather than being swallowed by
    the input validation that now precedes them.
    """
    orphaned = [_rec("A", root_ids=["X"]), _rec("B", root_ids=["X"])]
    with pytest.raises(ValueError, match="No roots"):
        asyncio.run(
            compute_depth_metrics({}, {"nodes": orphaned, "cleaned_edges": []})
        )

    disconnected = [_rec("S", root_ids=["S"]), _rec("X", root_ids=["S"])]
    with pytest.raises(ValueError, match="X.*unreachable"):
        asyncio.run(
            compute_depth_metrics({}, {"nodes": disconnected, "cleaned_edges": []})
        )


def test_clean_cycles_cleaned_edges_wires_into_depth_via_execute_graph() -> None:
    """assemble.nodes -> depth.nodes and clean.cleaned_edges -> depth.cleaned_edges.

    A port one conversion created feeding the next. Driven THROUGH execute_graph
    over the full assemble -> clean -> depth chain, so the executor carries real
    Node 4.5 output across a real port-declared edge rather than a stub.

    The fixture is built so the `cleaned_edges` port is DISTINGUISHED from
    `all_cites`, not merely tolerated. The merged graph is the 3-cycle
    A→B→C→A rooted at A; the weakest-link tiebreaker suppresses C→A (citation
    sums 100, 101, 1). That suppressed edge is a chord of the undirected view, so
    dropping it changes BOTH declared metric fields: C sits 2 hops from A instead
    of 1, and B and C read "backward" instead of "mixed" (with C→A present, A
    reaches them and they reach A). Feed `depth` the `all_cites` port instead and
    every one of those values changes. The assertions below pin all three facts —
    the suppression happened, the two candidate ports genuinely disagree, and the
    executor carried the one the wiring declares.
    """
    seeds = [_rec("A", citation_count=0, root_ids=["A"], hop_depth=0)]
    # Citation counts pick the suppression: min by (citation_sum, source, target)
    # over {A→B: 100, B→C: 101, C→A: 1} is C→A.
    n3 = Node3Result(
        papers=[_rec("B", citation_count=100, root_ids=["A"])],
        edges=[_edge("A", "B"), _edge("B", "C")],
    )
    n4 = Node4Result(
        papers=[_rec("C", citation_count=1, root_ids=["A"])],
        edges=[_edge("C", "A")],
    )

    merged = asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )
    cleaned = asyncio.run(
        clean_cycles({}, {"nodes": merged["nodes"], "cites": merged["cites"]})
    )

    # Non-vacuity, part 1: a cycle was actually suppressed, and the suppressed
    # edge is the chord the metrics below depend on.
    suppressed = cleaned["cycle_log"].suppressed_edges
    assert len(suppressed) == 1
    assert (suppressed[0].original.source_id, suppressed[0].original.target_id) == (
        "C",
        "A",
    )

    direct = asyncio.run(
        compute_depth_metrics(
            {},
            {"nodes": merged["nodes"], "cleaned_edges": cleaned["cleaned_edges"]},
        )
    )
    assert _shape(direct["depth_metrics"]) == {
        "A": ({"A": 0}, "seed"),
        "B": ({"A": 1}, "backward"),
        "C": ({"A": 2}, "backward"),
    }

    # Non-vacuity, part 2: the two candidate upstream ports DISAGREE, on both the
    # hop distances and the direction labels. Without this, the equality at the
    # end would pass under an `all_cites` miswiring.
    via_all_cites = asyncio.run(
        compute_depth_metrics(
            {}, {"nodes": merged["nodes"], "cleaned_edges": cleaned["all_cites"]}
        )
    )
    assert _shape(via_all_cites["depth_metrics"]) == {
        "A": ({"A": 0}, "seed"),
        "B": ({"A": 1}, "mixed"),
        "C": ({"A": 1}, "mixed"),
    }
    assert via_all_cites["depth_metrics"] != direct["depth_metrics"]

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds, "backward": n3, "forward": n4}

    register_handler("DepthTestProvider", _provider)
    register_handler("AssembleGraph", assemble_graph)
    register_handler("CleanCycles", clean_cycles)
    register_handler("ComputeDepthMetrics", compute_depth_metrics)

    graph = Graph(
        name="clean-into-depth-metrics",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="DepthTestProvider",
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
            Node(
                id="clean",
                type="CleanCycles",
                params={},
                input_ports=CLEAN_CYCLES_INPUT_PORTS,
                output_ports=CLEAN_CYCLES_OUTPUT_PORTS,
            ),
            _depth_node(),
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
            # The point of the name-identical port choice: no adapter on either
            # hop. `nodes` comes straight from the merge stage; `cleaned_edges` is
            # the acyclic view the cycle-cleaning conversion already publishes.
            Edge(source="merge", target="depth", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="clean", target="depth", type="DATA",
                 from_port="cleaned_edges", to_port="cleaned_edges"),
        ],
    )

    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    assert results["merge"]["status"] == "SUCCESS"
    assert results["clean"]["status"] == "SUCCESS"
    assert results["depth"]["status"] == "SUCCESS"
    assert results["depth"]["depth_metrics"] == direct["depth_metrics"]
