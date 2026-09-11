# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the DetectCommunities stage is genuinely driven by the declarative Graph:
# the handler, invoked THROUGH core/executor.py::execute_graph on a minimal
# Graph, returns output equal to a direct handler call on the same inputs. The
# executor path is load-bearing in the assertion — the CommunityResult is read
# off `results[<node_id>]`, which only exists if execute_graph actually
# dispatched the handler.
#
# The stage is BOUND: the communities node declares input ports, so the executor
# builds its `inputs` solely from the port-declared edges into it, keyed by
# `to_port`. The graphs here declare ports on both endpoints and carry
# from_port/to_port on every edge — which is also what makes them pass
# validate_integrity's dataflow check.
#
# Node 7 declares EXACTLY ONE output port, `communities`, carrying the whole
# CommunityResult. That is the compute_depth_metrics shape, not the decomposed
# clean_cycles one: PipelineResult.communities takes the result whole, so
# decomposing would only force consumers to rebuild it from loose ports.

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
    DETECT_COMMUNITIES_INPUT_PORTS,
    DETECT_COMMUNITIES_OUTPUT_PORTS,
    detect_communities,
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


def _communities_node(node_id: str = "comm", params: dict | None = None) -> Node:
    """The DetectCommunities node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="DetectCommunities",
        params=params or {},
        input_ports=DETECT_COMMUNITIES_INPUT_PORTS,
        output_ports=DETECT_COMMUNITIES_OUTPUT_PORTS,
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """DetectCommunities driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    One upstream provider declares both output ports and feeds them over TWO
    port-declared edges into the communities node's two distinct input ports —
    the same-source-two-ports shape, which is only expressible because bound
    inputs are keyed by `to_port` rather than by source node id.
    """
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]
    params = {"community_count_min": 1, "community_count_max": 10}

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        detect_communities(params, {"nodes": nodes, "all_cites": edges})
    )
    # Precondition: the fixture has real structure, so this is not a trivial
    # empty-equals-empty comparison.
    assert direct["communities"].community_assignments

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "all_cites": edges}

    register_handler("CommunitiesTestProvider", _provider)
    register_handler("DetectCommunities", detect_communities)

    graph = Graph(
        name="communities-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CommunitiesTestProvider",
                params={},
                output_ports=[_port("nodes"), _port("all_cites")],
            ),
            _communities_node(params=params),
        ],
        edges=[
            Edge(source="src", target="comm", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="comm", type="DATA",
                 from_port="all_cites", to_port="all_cites"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the two port-declared edges.
    assert results["comm"]["status"] == "SUCCESS"
    assert results["comm"]["communities"] == direct["communities"]


def test_single_output_port_carries_whole_result() -> None:
    """The one declared output port carries the entire ``CommunityResult`` —
    the result is not decomposed into per-field ports.

    Pins the decomposition ruling: a consumer reading ``communities`` off the
    port gets an object it can hand straight to ``PipelineResult``, with no
    reconstruction from loose ports.
    """
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    out = asyncio.run(
        detect_communities({}, {"nodes": nodes, "all_cites": edges})
    )

    assert [p.name for p in DETECT_COMMUNITIES_OUTPUT_PORTS] == ["communities"]
    assert set(out) == {"communities"}
    result = out["communities"]
    assert result.community_assignments
    assert result.algorithm_used in ("infomap", "leiden")
    assert result.community_count == len(set(result.community_assignments.values()))


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires DetectCommunities to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["DetectCommunities"] is detect_communities


def test_omitting_community_count_min_matches_explicit_default() -> None:
    """Omitting ``community_count_min`` from params yields the same output as
    passing ``community_count_min=5`` explicitly — pinning the default that now
    lives in ``CommunitiesParameters`` (it moved out of the old function
    signature, and no other test would catch it drifting).

    The fixture is two components plus an isolate, which partitions into fewer
    than 5 communities: the guard assertion below confirms a different
    ``community_count_min`` produces a genuinely different result (the
    below-minimum flag fires under the default and not under 1), so the equality
    above is a real check, not a fixture that would match regardless of value.
    """
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]
    payload = {"nodes": nodes, "all_cites": edges}

    omitted = asyncio.run(detect_communities({}, payload))
    explicit = asyncio.run(
        detect_communities({"community_count_min": 5}, payload)
    )
    assert omitted["communities"] == explicit["communities"]

    # Fixture discriminates: a different community_count_min must change the
    # result, else the equality above would hold for any default.
    other = asyncio.run(
        detect_communities({"community_count_min": 1}, payload)
    )
    assert other["communities"] != explicit["communities"]
    assert "community_count_below_minimum" in explicit[
        "communities"
    ].validation_flags
    assert other["communities"].validation_flags == []


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given — including a ``cleaned_edges`` key, which is a real port
    name on the sibling stages and specifically NOT one Node 7 consumes.
    """
    nodes = [_rec(x) for x in ("A", "B", "C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    inputs = {
        "bogus": "not-a-declared-port",
        "cleaned_edges": [],
        "nodes": nodes,
        "all_cites": edges,
    }

    result = asyncio.run(detect_communities({}, inputs))["communities"]
    assert set(result.community_assignments) == {"A", "B", "C"}


def test_two_upstreams_disjoint_ports_via_execute_graph() -> None:
    """``nodes`` arrives from one upstream node and ``all_cites`` from another,
    over two port-declared DATA edges.

    This is the shape the real wiring will take — ``all_cites`` is a declared
    output port of ``CleanCycles``, so a pipeline graph reads
    ``clean.all_cites -> communities.all_cites`` with no renaming. Driven THROUGH
    execute_graph so the executor's binding path is exercised across two
    upstreams, and the result equals the single-payload direct call.
    """
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    edges = [_edge("A", "B"), _edge("C", "D")]
    params = {"community_count_min": 1, "community_count_max": 10}

    direct = asyncio.run(
        detect_communities(params, {"nodes": nodes, "all_cites": edges})
    )
    assert direct["communities"].community_assignments

    async def _nodes_provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes}

    async def _edges_provider(_params: dict, _inputs: dict) -> dict:
        return {"all_cites": edges}

    register_handler("NodesProvider", _nodes_provider)
    register_handler("AllCitesProvider", _edges_provider)
    register_handler("DetectCommunities", detect_communities)

    graph = Graph(
        name="communities-two-upstreams-disjoint",
        version="1.0",
        nodes=[
            Node(id="nsrc", type="NodesProvider", params={},
                 output_ports=[_port("nodes")]),
            Node(id="esrc", type="AllCitesProvider", params={},
                 output_ports=[_port("all_cites")]),
            _communities_node(params=params),
        ],
        edges=[
            Edge(source="nsrc", target="comm", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="esrc", target="comm", type="DATA",
                 from_port="all_cites", to_port="all_cites"),
        ],
    )

    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph))

    assert results["comm"]["status"] == "SUCCESS"
    assert results["comm"]["communities"] == direct["communities"]


def test_params_omitted_entirely_via_execute_graph() -> None:
    """A node declaring no ``params`` runs on the ``CommunitiesParameters``
    defaults through the executor.

    The executor passes ``node.params`` straight through, so an empty mapping is
    the graph-level expression of "use the frozen Node 7 defaults" — the same
    fallback the direct call gets.
    """
    nodes = [_rec(x) for x in ("A", "B", "C", "D", "E")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("D", "E")]

    direct = asyncio.run(
        detect_communities({}, {"nodes": nodes, "all_cites": edges})
    )

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "all_cites": edges}

    register_handler("CommunitiesTestProvider", _provider)
    register_handler("DetectCommunities", detect_communities)

    graph = Graph(
        name="communities-default-params",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CommunitiesTestProvider",
                params={},
                output_ports=[_port("nodes"), _port("all_cites")],
            ),
            _communities_node(),  # params={} — defaults apply
        ],
        edges=[
            Edge(source="src", target="comm", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="comm", type="DATA",
                 from_port="all_cites", to_port="all_cites"),
        ],
    )

    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph))

    assert results["comm"]["status"] == "SUCCESS"
    assert results["comm"]["communities"] == direct["communities"]
    # The default min is 5 and this fixture partitions into fewer — the flag
    # proves the defaults were genuinely applied, not silently skipped.
    assert "community_count_below_minimum" in results["comm"][
        "communities"
    ].validation_flags
