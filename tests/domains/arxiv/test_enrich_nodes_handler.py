# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the EnrichNodes stage is genuinely driven by the declarative Graph: the
# handler, invoked THROUGH core/executor.py::execute_graph on a minimal Graph,
# returns output equal to a direct handler call on the same inputs. The executor
# path is load-bearing in the assertion — the enriched node set is read off
# `results[<node_id>]`, which only exists if execute_graph actually dispatched
# the handler.
#
# The stage is BOUND: the enrich node declares input ports, so the executor
# builds its `inputs` solely from the port-declared edges into it, keyed by
# `to_port`. The graphs here declare ports on both endpoints and carry
# from_port/to_port on every edge — which is also what makes them pass
# validate_integrity's dataflow check.
#
# This is the four-input join at the end of the pipeline, and its three metric
# input ports are name-identical to the output ports of the three stages that
# produce them, so a real wiring reads `depth.depth_metrics ->
# enrich.depth_metrics` with no renaming. It declares EXACTLY ONE output port,
# `enriched_nodes`, carrying the whole list — the compute_depth_metrics /
# detect_communities shape, since PipelineResult.nodes takes the list whole.

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
    CommunityResult,
    DepthMetrics,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    ENRICH_NODES_INPUT_PORTS,
    ENRICH_NODES_OUTPUT_PORTS,
    enrich_nodes,
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


def _fixture() -> tuple[
    list[PaperRecord], dict[str, DepthMetrics], dict[str, float], CommunityResult
]:
    """Three nodes whose four merged values all DIFFER per node.

    Every field the merge writes varies across the node set, so an equality
    assertion against this result cannot be satisfied by a degenerate output that
    wrote the same value everywhere, or by one that dropped a field.
    """
    nodes = [_rec("S"), _rec("A"), _rec("B")]
    depth = {
        "S": DepthMetrics(hop_depth_per_root={"S": 0}, traversal_direction="seed"),
        "A": DepthMetrics(
            hop_depth_per_root={"S": 1}, traversal_direction="backward"
        ),
        "B": DepthMetrics(hop_depth_per_root={"S": 2}, traversal_direction="forward"),
    }
    prank = {"S": 0.5, "A": 0.3, "B": 0.2}
    communities = CommunityResult(
        community_assignments={"S": "0", "A": "0", "B": "1"},
        algorithm_used="infomap",
        community_count=2,
    )
    return nodes, depth, prank, communities


def _payload() -> dict:
    """The fixture shaped as the executor's bound mapping — one key per port."""
    nodes, depth, prank, communities = _fixture()
    return {
        "nodes": nodes,
        "depth_metrics": depth,
        "pagerank": prank,
        "communities": communities,
    }


def _shape(records: list[PaperRecord]) -> dict[str, tuple]:
    """The four merged fields as plain comparable data, per node."""
    return {
        r.node_id: (
            r.traversal_direction,
            r.hop_depth_per_root,
            r.pagerank,
            r.community_id,
        )
        for r in records
    }


def _enrich_node(node_id: str = "enrich", params: dict | None = None) -> Node:
    """The EnrichNodes node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="EnrichNodes",
        params=params or {},
        input_ports=ENRICH_NODES_INPUT_PORTS,
        output_ports=ENRICH_NODES_OUTPUT_PORTS,
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """EnrichNodes driven through execute_graph on a minimal Graph returns output
    equal to the direct handler call on the same inputs.

    One upstream provider declares all four output ports and feeds them over FOUR
    port-declared edges into the enrich node's four distinct input ports — the
    same-source-many-ports shape, which is only expressible because bound inputs
    are keyed by `to_port` rather than by source node id.
    """
    payload = _payload()

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(enrich_nodes({}, payload))
    # The fixture is discriminating: every node carries all four merged values
    # and each of them varies, so equality below cannot be satisfied by an empty
    # or degenerate result.
    assert _shape(direct["enriched_nodes"]) == {
        "S": ("seed", {"S": 0}, 0.5, "0"),
        "A": ("backward", {"S": 1}, 0.3, "0"),
        "B": ("forward", {"S": 2}, 0.2, "1"),
    }

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return payload

    register_handler("EnrichTestProvider", _provider)
    register_handler("EnrichNodes", enrich_nodes)

    graph = Graph(
        name="enrich-nodes-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="EnrichTestProvider",
                params={},
                output_ports=[
                    _port("nodes"),
                    _port("depth_metrics"),
                    _port("pagerank"),
                    _port("communities"),
                ],
            ),
            _enrich_node(),
        ],
        edges=[
            Edge(source="src", target="enrich", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="enrich", type="DATA",
                 from_port="depth_metrics", to_port="depth_metrics"),
            Edge(source="src", target="enrich", type="DATA",
                 from_port="pagerank", to_port="pagerank"),
            Edge(source="src", target="enrich", type="DATA",
                 from_port="communities", to_port="communities"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the four port-declared edges.
    assert results["enrich"]["status"] == "SUCCESS"
    assert results["enrich"]["enriched_nodes"] == direct["enriched_nodes"]


def test_single_output_port_carries_whole_enriched_list() -> None:
    """The one declared output port carries the entire enriched
    ``list[PaperRecord]`` — the node set is not decomposed into per-field ports.

    Pins the decomposition ruling: a consumer reading ``enriched_nodes`` off the
    port gets a list it can hand straight to ``PipelineResult.nodes``, with no
    reconstruction from loose ports.
    """
    payload = _payload()

    out = asyncio.run(enrich_nodes({}, payload))

    assert [p.name for p in ENRICH_NODES_OUTPUT_PORTS] == ["enriched_nodes"]
    assert set(out) == {"enriched_nodes"}
    assert {p.name for p in ENRICH_NODES_INPUT_PORTS} == {
        "nodes",
        "depth_metrics",
        "pagerank",
        "communities",
    }
    enriched = out["enriched_nodes"]
    assert all(isinstance(r, PaperRecord) for r in enriched)
    assert [r.node_id for r in enriched] == [r.node_id for r in payload["nodes"]]


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires EnrichNodes to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["EnrichNodes"] is enrich_nodes


def test_enrichment_does_not_mutate_input_records() -> None:
    """The merge is an immutable write path — ``model_copy`` returns new records
    and leaves the originals as they were.

    The stage is the one place four upstream payloads are written onto the node
    set, and `run_traversal` keeps the pre-enrichment `unified_nodes` binding
    alive after the call. If the merge mutated in place, that binding would
    silently change identity underneath every later reader.
    """
    payload = _payload()
    originals = payload["nodes"]

    enriched = asyncio.run(enrich_nodes({}, payload))["enriched_nodes"]

    # The originals keep their pre-enrichment defaults on all four merged fields.
    for r in originals:
        assert r.community_id is None
        assert r.pagerank is None
        assert r.traversal_direction is None
        assert r.hop_depth_per_root == {}

    # ...and the returned records are genuinely different objects that DID get
    # the values, so the check above is not passing on an inert no-op.
    assert all(e is not o for e, o in zip(enriched, originals, strict=True))
    assert all(
        e.community_id is not None
        and e.pagerank is not None
        and e.traversal_direction is not None
        and e.hop_depth_per_root
        for e in enriched
    )


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given. `cleaned_edges` and `all_cites` are the pointed choices:
    both are real port names on the upstream stages, and specifically NOT ones
    this stage consumes — the edge views were spent upstream, and only their
    computed metrics reach here.
    """
    payload = _payload()
    reference = asyncio.run(enrich_nodes({}, payload))

    inputs = {
        "bogus": "not-a-declared-port",
        "cleaned_edges": [],
        "all_cites": [],
        **payload,
    }

    result = asyncio.run(enrich_nodes({}, inputs))
    assert result["enriched_nodes"] == reference["enriched_nodes"]


def test_params_omitted_entirely_via_execute_graph() -> None:
    """A node declaring no ``params`` runs identically through the executor.

    This stage takes NO configuration and no parameters model exists for it, so
    an empty mapping is not a defaults fallback — there is nothing to fall back
    to. The executor passes ``node.params`` straight through, and the result must
    equal the direct call that also passed nothing.
    """
    payload = _payload()

    direct = asyncio.run(enrich_nodes({}, payload))

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return payload

    register_handler("EnrichTestProvider", _provider)
    register_handler("EnrichNodes", enrich_nodes)

    graph = Graph(
        name="enrich-nodes-default-params",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="EnrichTestProvider",
                params={},
                output_ports=[
                    _port("nodes"),
                    _port("depth_metrics"),
                    _port("pagerank"),
                    _port("communities"),
                ],
            ),
            _enrich_node(),  # params={} — nothing to configure
        ],
        edges=[
            Edge(source="src", target="enrich", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="enrich", type="DATA",
                 from_port="depth_metrics", to_port="depth_metrics"),
            Edge(source="src", target="enrich", type="DATA",
                 from_port="pagerank", to_port="pagerank"),
            Edge(source="src", target="enrich", type="DATA",
                 from_port="communities", to_port="communities"),
        ],
    )

    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    assert results["enrich"]["status"] == "SUCCESS"
    assert results["enrich"]["enriched_nodes"] == direct["enriched_nodes"]
