# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Edge-declared input binding in core/executor.py.
#
# The migration fence is a one-way ratchet: a node that declares `input_ports`
# is BOUND and the executor builds its `inputs` solely from port-declared
# incoming edges, keyed by `to_port`. A node that declares nothing stays in the
# legacy regime and sees exactly what it always saw — every upstream payload
# keyed by source node id, CONTROL edges included. Both regimes execute in one
# graph.

import asyncio
from collections.abc import Callable

import pytest

from idiograph.core.executor import HANDLERS, execute_graph, register_handler
from idiograph.core.models import Edge, Graph, Node, PortDeclaration


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


def _port(name: str) -> PortDeclaration:
    """An untyped port. `port_type` is inert — nothing validates it."""
    return PortDeclaration(name=name, port_type="untyped")


def _recorder() -> tuple[list[dict], Callable]:
    """A handler that records the `inputs` mapping it was handed."""
    seen: list[dict] = []

    async def handler(_params: dict, inputs: dict) -> dict:
        seen.append(inputs)
        return {"ok": True}

    return seen, handler


class TestBoundBinding:
    def test_inputs_keyed_by_to_port(self):
        """A bound node receives `inputs[to_port] = upstream_output[from_port]` —
        keyed by the declared port name, not by source node id."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"alpha": 1, "beta": 2}

        register_handler("Source", source)
        register_handler("Sink", sink)

        graph = Graph(
            name="bound-basic", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={},
                     output_ports=[_port("alpha"), _port("beta")]),
                Node(id="t", type="Sink", params={},
                     input_ports=[_port("left"), _port("right")]),
            ],
            edges=[
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s", target="t", type="DATA",
                     from_port="beta", to_port="right"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert seen[0] == {"left": 1, "right": 2}
        # The source node id never appears as a key on the bound path.
        assert "s" not in seen[0]

    def test_same_source_two_ports(self):
        """Two edges from ONE source into two distinct ports — the shape that is
        only expressible because bound inputs are keyed by `to_port`. Under the
        legacy regime both edges collapse onto a single `inputs["s"]` entry."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"nodes": ["n"], "edges": ["e"]}

        register_handler("Source", source)
        register_handler("Sink", sink)

        graph = Graph(
            name="same-source-two-ports", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={},
                     output_ports=[_port("nodes"), _port("edges")]),
                Node(id="t", type="Sink", params={},
                     input_ports=[_port("nodes"), _port("edges")]),
            ],
            edges=[
                Edge(source="s", target="t", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="s", target="t", type="DATA",
                     from_port="edges", to_port="edges"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert seen[0] == {"nodes": ["n"], "edges": ["e"]}

    def test_inputs_are_exactly_the_declared_edges(self):
        """A bound node's inputs come SOLELY from port-declared edges. A portless
        incoming edge contributes no data (validate_integrity reports it as an
        error) and does not leak a source-id key into the bound mapping."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"alpha": 1}

        async def other(_params, _inputs):
            return {"noise": 99}

        register_handler("Source", source)
        register_handler("Other", other)
        register_handler("Sink", sink)

        graph = Graph(
            name="bound-portless-edge", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={}, output_ports=[_port("alpha")]),
                Node(id="o", type="Other", params={}),
                Node(id="t", type="Sink", params={}, input_ports=[_port("left")]),
            ],
            edges=[
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="o", target="t", type="DATA"),  # portless
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert seen[0] == {"left": 1}

    def test_empty_input_ports_is_a_declaration_of_no_inputs(self):
        """`input_ports=[]` is a declaration, not an absence — the node is bound
        and receives an empty mapping, never the legacy source-keyed payloads."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"alpha": 1}

        register_handler("Source", source)
        register_handler("Sink", sink)

        graph = Graph(
            name="bound-empty-ports", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={}, output_ports=[_port("alpha")]),
                Node(id="t", type="Sink", params={}, input_ports=[]),
            ],
            edges=[Edge(source="s", target="t", type="DATA")],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert seen[0] == {}

    def test_bound_edge_binds_over_control_edge_too(self):
        """CONTROL-edge semantics are untouched by binding: a CONTROL edge
        carries data on the bound path exactly as it does on the legacy one."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"alpha": 7}

        register_handler("Source", source)
        register_handler("Sink", sink)

        graph = Graph(
            name="bound-control-edge", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={}, output_ports=[_port("alpha")]),
                Node(id="t", type="Sink", params={}, input_ports=[_port("left")]),
            ],
            edges=[
                Edge(source="s", target="t", type="CONTROL",
                     from_port="alpha", to_port="left"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert seen[0] == {"left": 7}


class TestMissingPortIsExplicit:
    def test_missing_from_port_key_fails_the_node_explicitly(self):
        """A bound edge whose `from_port` the upstream did not emit is an
        explicit error — never a silent skip, never a fallback to the legacy
        gather. The node fails with a message naming the missing port, and
        downstream nodes skip as they would for any failure."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"something_else": 1}  # never emits "alpha"

        async def downstream(_params, _inputs):
            return {}

        register_handler("Source", source)
        register_handler("Sink", sink)
        register_handler("Downstream", downstream)

        graph = Graph(
            name="missing-from-port", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={}, output_ports=[_port("alpha")]),
                Node(id="t", type="Sink", params={}, input_ports=[_port("left")]),
                Node(id="d", type="Downstream", params={}),
            ],
            edges=[
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="t", target="d", type="DATA"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "FAILED"
        assert "alpha" in results["t"]["error"]
        assert "did not emit" in results["t"]["error"]
        # The handler was never reached — no silent fallback ran it on partial data.
        assert seen == []
        assert results["d"]["status"] == "SKIPPED"


class TestLegacyRegimeUnchanged:
    def test_unbound_node_sees_payloads_keyed_by_source_id(self):
        """A node declaring no ports is legacy: every upstream payload arrives
        keyed by source node id, CONTROL edges carrying data, exactly as before."""
        seen, sink = _recorder()

        async def a(_params, _inputs):
            return {"v": 1}

        async def b(_params, _inputs):
            return {"v": 2}

        register_handler("A", a)
        register_handler("B", b)
        register_handler("Sink", sink)

        graph = Graph(
            name="legacy", version="1.0",
            nodes=[
                Node(id="a", type="A", params={}),
                Node(id="b", type="B", params={}),
                Node(id="t", type="Sink", params={}),
            ],
            edges=[
                Edge(source="a", target="t", type="DATA"),
                Edge(source="b", target="t", type="CONTROL"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert set(seen[0]) == {"a", "b"}
        assert seen[0]["a"]["v"] == 1
        assert seen[0]["b"]["v"] == 2  # CONTROL edge still carries data

    def test_arxiv_pipeline_summarize_still_receives_payload_over_control_edge(self):
        """The arXiv demo pipeline is legacy with respect to `input_ports` — the
        property this test is about. Its nodes DO declare `resources` per
        IDG-068, but that is the other fence: the resource channel decides what
        a handler is handed from the run, never how its `inputs` are gathered.
        The two ratchets turn independently, and this test pins the input one.

        Its evaluate -> summarize edge is CONTROL and is summarize's ONLY
        upstream, so that CONTROL edge must keep carrying data —
        `llm_summarize` reads title/abstract off `next(iter(inputs.values()))`
        and would get nothing if binding had changed CONTROL semantics or the
        legacy gather.

        Driven on the real ARXIV_PIPELINE graph with stubbed handlers and inert
        resources, so no API key or network is involved.
        """
        from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE

        graph = ARXIV_PIPELINE.model_copy(deep=True)
        assert all(n.input_ports is None for n in graph.nodes)  # all legacy

        control_edges = [
            e for e in graph.edges
            if e.source == "evaluate" and e.target == "summarize"
        ]
        assert len(control_edges) == 1
        assert control_edges[0].type == "CONTROL"

        seen: dict[str, dict] = {}

        # fetch/claims/summarize sit on resource-declaring nodes, so the
        # executor hands them a keyword-only `resources`. They accept and
        # ignore it — the input gather, not the resource channel, is what this
        # test measures. `evaluate`'s node declares nothing, so its stub keeps
        # the bare two-positional legacy signature.
        async def fetch(_params, inputs, *, resources):
            seen["fetch"] = inputs
            return {"title": "T", "abstract": "A", "paper_id": "p1"}

        async def claims(_params, inputs, *, resources):
            seen["claims"] = inputs
            upstream = next(iter(inputs.values()), {})
            return {"response": "method model result", **upstream}

        async def evaluate(_params, inputs):
            seen["evaluate"] = inputs
            upstream = next(iter(inputs.values()), {})
            return {"score": 1.0, **upstream}

        async def summarize(_params, inputs, *, resources):
            seen["summarize"] = inputs
            upstream = next(iter(inputs.values()), {})
            return {"summary": f"{upstream['title']}/{upstream['abstract']}"}

        register_handler("FetchAbstract", fetch)
        register_handler("LLMCall", claims)
        register_handler("Evaluator", evaluate)
        register_handler("LLMSummarize", summarize)

        results = asyncio.run(execute_graph(
            graph, resources={"http_client": None, "anthropic_client": None},
        ))

        assert results["summarize"]["status"] == "SUCCESS"
        # The CONTROL edge is the only thing feeding summarize, and it carried
        # the evaluator's full payload through.
        assert set(seen["summarize"]) == {"evaluate"}
        assert seen["summarize"]["evaluate"]["title"] == "T"
        assert seen["summarize"]["evaluate"]["abstract"] == "A"
        assert seen["summarize"]["evaluate"]["score"] == 1.0
        assert results["summarize"]["summary"] == "T/A"

    def test_port_declarations_on_edges_are_inert_for_legacy_targets(self):
        """Ports on an edge do not bind a target that declares no `input_ports`.
        The ratchet only turns when the TARGET declares — otherwise an upstream
        gaining declarations would silently change its consumers' input shape."""
        seen, sink = _recorder()

        async def source(_params, _inputs):
            return {"alpha": 1}

        register_handler("Source", source)
        register_handler("Sink", sink)

        graph = Graph(
            name="legacy-target-declared-edge", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={}, output_ports=[_port("alpha")]),
                Node(id="t", type="Sink", params={}),  # undeclared -> legacy
            ],
            edges=[
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["t"]["status"] == "SUCCESS"
        assert set(seen[0]) == {"s"}
        assert seen[0]["s"]["alpha"] == 1


class TestMixedGraph:
    def test_bound_and_unbound_nodes_coexist(self):
        """A graph holding both regimes executes: the bound node binds by port,
        the legacy node downstream of it still gets source-keyed payloads."""
        bound_seen: list[dict] = []
        legacy_seen, legacy_sink = _recorder()

        async def source(_params, _inputs):
            return {"alpha": 1, "beta": 2}

        async def bound(_params, inputs):
            bound_seen.append(inputs)
            return {"out": inputs["left"] + 10}

        register_handler("Source", source)
        register_handler("Bound", bound)
        register_handler("Legacy", legacy_sink)

        graph = Graph(
            name="mixed", version="1.0",
            nodes=[
                Node(id="s", type="Source", params={},
                     output_ports=[_port("alpha"), _port("beta")]),
                Node(id="b", type="Bound", params={},
                     input_ports=[_port("left")], output_ports=[_port("out")]),
                Node(id="l", type="Legacy", params={}),
            ],
            edges=[
                Edge(source="s", target="b", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s", target="l", type="DATA"),
                Edge(source="b", target="l", type="DATA"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["s"]["status"] == "SUCCESS"
        assert results["b"]["status"] == "SUCCESS"
        assert results["l"]["status"] == "SUCCESS"

        assert bound_seen[0] == {"left": 1}
        assert results["b"]["out"] == 11
        # The legacy consumer sees both upstreams keyed by source id.
        assert set(legacy_seen[0]) == {"s", "b"}
        assert legacy_seen[0]["b"]["out"] == 11

    def test_sample_pipeline_still_executes_unbound(self):
        """The core SAMPLE_PIPELINE declares no ports anywhere — it must run
        entirely on the legacy path, with every handler seeing source-keyed
        inputs."""
        from idiograph.core.pipeline import SAMPLE_PIPELINE

        assert all(n.input_ports is None for n in SAMPLE_PIPELINE.nodes)
        assert all(n.output_ports is None for n in SAMPLE_PIPELINE.nodes)
        assert all(
            e.from_port is None and e.to_port is None for e in SAMPLE_PIPELINE.edges
        )

        seen: dict[str, dict] = {}

        def make(node_id: str):
            async def handler(_params, inputs):
                seen[node_id] = inputs
                return {"v": node_id}
            return handler

        for node in SAMPLE_PIPELINE.nodes:
            register_handler(node.type, make(node.id))

        graph = SAMPLE_PIPELINE.model_copy(deep=True)
        results = asyncio.run(execute_graph(graph))

        assert all(r["status"] == "SUCCESS" for r in results.values())
        for node in graph.nodes:
            upstreams = {e.source for e in graph.edges if e.target == node.id}
            assert set(seen[node.id]) == upstreams
