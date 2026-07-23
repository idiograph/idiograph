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

import asyncio

import pytest

from idiograph.core.executor import (
    HANDLERS,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node
from idiograph.domains.arxiv.models import CitationEdge, PaperRecord
from idiograph.domains.arxiv.pipeline import compute_pagerank


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


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


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """ComputePagerank driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    The graph has an upstream provider node feeding `nodes`/`cleaned_edges` to
    the pagerank node over a DATA edge, so the value the handler consumes arrives
    through the executor's input-collection path, not from a hand-built dict.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C"), _rec("D")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D"), _edge("D", "A")]
    params = {"damping": 0.85}

    # Direct handler call — the reference output. `inputs` is shaped as a single
    # upstream payload, exactly the shape execute_graph builds per upstream node.
    direct = asyncio.run(
        compute_pagerank(
            params,
            {"traversal": {"nodes": nodes, "cleaned_edges": edges}},
        )
    )
    assert direct["pagerank"]  # non-empty — the graph has real structure

    # Graph-driven path: a provider node emits the traversal payload; the
    # pagerank node consumes it via the DATA edge.
    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "cleaned_edges": edges}

    register_handler("PagerankTestProvider", _provider)
    register_handler("ComputePagerank", compute_pagerank)

    graph = Graph(
        name="pagerank-minimal",
        version="1.0",
        nodes=[
            Node(id="src", type="PagerankTestProvider", params={}),
            Node(id="pr", type="ComputePagerank", params=params),
        ],
        edges=[Edge(source="src", target="pr", type="DATA")],
    )

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler through the provider -> pagerank DATA edge.
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
    payload = {"traversal": {"nodes": nodes, "cleaned_edges": edges}}

    omitted = asyncio.run(compute_pagerank({}, payload))
    explicit = asyncio.run(compute_pagerank({"damping": 0.85}, payload))
    assert omitted["pagerank"] == explicit["pagerank"]

    # Fixture discriminates: a different damping must change the result, else the
    # equality above would hold for any default.
    other = asyncio.run(compute_pagerank({"damping": 0.5}, payload))
    assert other["pagerank"] != explicit["pagerank"]


def test_non_dict_upstream_payload_is_ignored() -> None:
    """A non-dict upstream payload is skipped (pipeline.py:981) and the handler
    still succeeds on the valid dict payload alongside it.

    The executor keys ``inputs`` by upstream node id and passes each upstream's
    output verbatim; nothing guarantees every value is a dict, so the gather step
    guards with ``isinstance`` and ``continue``s past non-dicts.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("A", "B"), _edge("B", "C")]

    inputs = {
        "bogus": "not-a-dict-payload",
        "traversal": {"nodes": nodes, "cleaned_edges": edges},
    }

    result = asyncio.run(compute_pagerank({"damping": 0.85}, inputs))
    assert result["pagerank"]
    assert set(result["pagerank"]) == {"A", "B", "C"}


def test_two_upstreams_disjoint_keys_via_execute_graph() -> None:
    """Stage-2 shape: ``nodes`` arrives from one upstream node and
    ``cleaned_edges`` from another, over two DATA edges into the pagerank node.

    Driven THROUGH execute_graph so the executor's multi-upstream collection path
    is exercised — the handler must gather the two declared keys across the two
    upstream payloads and return output equal to the single-upstream direct call
    on the same data.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C"), _rec("D")]
    edges = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D"), _edge("D", "A")]
    params = {"damping": 0.85}

    direct = asyncio.run(
        compute_pagerank(
            params,
            {"traversal": {"nodes": nodes, "cleaned_edges": edges}},
        )
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
            Node(id="nsrc", type="NodesProvider", params={}),
            Node(id="esrc", type="EdgesProvider", params={}),
            Node(id="pr", type="ComputePagerank", params=params),
        ],
        edges=[
            Edge(source="nsrc", target="pr", type="DATA"),
            Edge(source="esrc", target="pr", type="DATA"),
        ],
    )

    results = asyncio.run(execute_graph(graph))

    assert results["pr"]["status"] == "SUCCESS"
    assert results["pr"]["pagerank"] == direct["pagerank"]


def test_two_upstreams_same_key_is_first_wins_and_undetermined() -> None:
    """Two upstreams both supply ``nodes`` with different values.

    This DOCUMENTS current behavior rather than endorsing it. The handler
    completes and returns exactly one of the two node sets — first-wins over
    ``inputs.values()``. Which one wins is NOT determined by the graph in any
    principled sense: the executor ignores ``from_port``/``to_port`` entirely
    (``core/executor.py`` collects inputs keyed only by source node id), so the
    winner is merely whichever upstream payload the gather step happens to reach
    first. That is why this behavior is pinned here as observed, not specified —
    the resolution mechanism is an open design question a later stage must settle
    before it can depend on this shape.
    """
    nodes_a = [_rec("A"), _rec("B")]
    nodes_x = [_rec("X"), _rec("Y")]

    async def _provider_a(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes_a, "cleaned_edges": []}

    async def _provider_x(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes_x, "cleaned_edges": []}

    register_handler("ProviderA", _provider_a)
    register_handler("ProviderX", _provider_x)
    register_handler("ComputePagerank", compute_pagerank)

    graph = Graph(
        name="pagerank-two-upstreams-collision",
        version="1.0",
        nodes=[
            Node(id="pa", type="ProviderA", params={}),
            Node(id="px", type="ProviderX", params={}),
            Node(id="pr", type="ComputePagerank", params={}),
        ],
        edges=[
            Edge(source="pa", target="pr", type="DATA"),
            Edge(source="px", target="pr", type="DATA"),
        ],
    )

    results = asyncio.run(execute_graph(graph))

    assert results["pr"]["status"] == "SUCCESS"
    won = set(results["pr"]["pagerank"])
    # Exactly one upstream's node set is reflected — never a merge of both.
    assert won in ({"A", "B"}, {"X", "Y"})
