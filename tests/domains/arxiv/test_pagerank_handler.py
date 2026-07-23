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
