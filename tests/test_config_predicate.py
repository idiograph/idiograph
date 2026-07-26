# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# The node-declared config-predicate channel in core/executor.py.
#
# A node declares `enabled_when` — the NAME of one of its own params — and the
# executor tests that param's truthiness before dispatch. The gate is
# configuration: it lives in params, so it enters the content address, and the
# address never claims bytes a disabled node did not produce. The third instance
# of the declare-on-the-node fence, after input_ports and resources, and the same
# one-way ratchet: `enabled_when=None` is the legacy regime, always runs.
#
# A disabled node is not a failure and not an absence. It stays in the declared
# graph, records SKIPPED with a distinguished `skip_reason`, keeps `Node.status`
# PENDING because it never entered the RUNNING ladder, and forwards its declared
# `disabled_passthrough` ports so a config-skip does not cascade.

import asyncio

import pytest

from idiograph.core.executor import (
    HANDLERS,
    UnsuppliedResourceError,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


def _one_node(node: Node) -> Graph:
    """A single-node graph, so a test isolates the predicate from dataflow."""
    return Graph(name="config-predicate", version="1.0", nodes=[node], edges=[])


def _port(name: str) -> PortDeclaration:
    return PortDeclaration(name=name, port_type="untyped")


class TestDisabledRecord:
    def test_disabled_node_records_skipped_with_its_own_reason(self):
        """Clause 5 — the record shows it. The node is SKIPPED, but with a
        reason that separates "configured off" from "upstream did not
        succeed", and its handler was never called."""
        ran: list[str] = []

        async def handler(_params, _inputs):
            ran.append("handler")
            return {"v": 1}

        register_handler("Gated", handler)
        node = Node(id="n", type="Gated", params={"llm": None}, enabled_when="llm")

        results = asyncio.run(execute_graph(_one_node(node)))

        assert results["n"]["status"] == "SKIPPED"
        assert results["n"]["skip_reason"] == "disabled_by_config"
        assert results["n"]["node_id"] == "n"
        assert ran == []  # never dispatched

    def test_the_record_names_the_predicate(self):
        """The reason says the node was configured off; the record also says
        WHICH param decided it, so the graph is readable without opening the
        handler."""

        async def handler(_params, _inputs):
            return {"v": 1}

        register_handler("Gated", handler)
        node = Node(
            id="n", type="Gated", params={"annotate": False}, enabled_when="annotate"
        )

        results = asyncio.run(execute_graph(_one_node(node)))

        assert results["n"]["disabled_by"] == "annotate"

    def test_node_status_stays_pending(self):
        """IDG-072 — the disabled path mutates Node.status not at all. A
        disabled node never enters the PENDING → RUNNING ladder because it was
        never dispatched, so PENDING is the truthful reading. NOT FAILED: the
        cascade path's FAILED is a different situation and stays where it is."""

        async def handler(_params, _inputs):
            return {"v": 1}

        register_handler("Gated", handler)
        node = Node(id="n", type="Gated", params={"llm": None}, enabled_when="llm")
        assert node.status == "PENDING"

        asyncio.run(execute_graph(_one_node(node)))

        assert node.status == "PENDING"

    def test_the_node_stays_in_the_declared_graph(self):
        """A disabled node is not removed and not absent from the results — the
        self-portrait shows a declared node that was configured off."""

        async def handler(_params, _inputs):
            return {"v": 1}

        register_handler("Gated", handler)
        graph = _one_node(
            Node(id="n", type="Gated", params={"llm": None}, enabled_when="llm")
        )

        results = asyncio.run(execute_graph(graph))

        assert [n.id for n in graph.nodes] == ["n"]
        assert "n" in results


class TestPassthrough:
    def test_declared_mapping_forwards_to_a_bound_consumer(self):
        """Clause 6 — config-skip does not cascade. The disabled node forwards
        its declared mapping onto its own output port, the downstream node RUNS,
        and it reads the pre-annotation value."""
        downstream_inputs: list[dict] = []

        async def source(_params, _inputs):
            return {"nodes": ["a", "b"]}

        async def gated(_params, _inputs):
            raise AssertionError("disabled node must not be dispatched")

        async def sink(_params, inputs):
            downstream_inputs.append(inputs)
            return {"seen": inputs["nodes"]}

        register_handler("Source", source)
        register_handler("Gated", gated)
        register_handler("Sink", sink)

        graph = Graph(
            name="passthrough", version="1.0",
            nodes=[
                Node(
                    id="src", type="Source",
                    input_ports=[], output_ports=[_port("nodes")],
                ),
                Node(
                    id="ann", type="Gated",
                    params={"llm": None},
                    enabled_when="llm",
                    disabled_passthrough={"nodes": "nodes"},
                    input_ports=[_port("nodes")],
                    output_ports=[_port("nodes")],
                ),
                Node(
                    id="depth", type="Sink",
                    input_ports=[_port("nodes")], output_ports=[_port("seen")],
                ),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="ann", target="depth", type="DATA",
                     from_port="nodes", to_port="nodes"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["status"] == "SKIPPED"
        assert results["ann"]["nodes"] == ["a", "b"]  # forwarded on the port
        assert results["depth"]["status"] == "SUCCESS"  # the tail RAN
        assert downstream_inputs[0] == {"nodes": ["a", "b"]}

    def test_inputs_not_named_in_the_mapping_are_not_forwarded(self):
        """The mapping is exhaustive by declaration: an input the node
        collected but did not map is simply not forwarded."""

        async def source(_params, _inputs):
            return {"nodes": [1], "resolved": [2]}

        async def gated(_params, _inputs):
            raise AssertionError("disabled node must not be dispatched")

        register_handler("Source", source)
        register_handler("Gated", gated)

        graph = Graph(
            name="partial-passthrough", version="1.0",
            nodes=[
                Node(id="src", type="Source", input_ports=[],
                     output_ports=[_port("nodes"), _port("resolved")]),
                Node(
                    id="ann", type="Gated",
                    params={"llm": None},
                    enabled_when="llm",
                    disabled_passthrough={"nodes": "nodes"},
                    input_ports=[_port("nodes"), _port("resolved")],
                    output_ports=[_port("nodes"), _port("provenance")],
                ),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="src", target="ann", type="DATA",
                     from_port="resolved", to_port="resolved"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["nodes"] == [1]
        assert "resolved" not in results["ann"]

    def test_unmapped_output_port_fails_its_consumer_explicitly(self):
        """A disabled node emits ONLY its mapped ports. A consumer bound to an
        unmapped one raises PortBindingError at its own binding step — explicit,
        never a silent None — which the executor surfaces as that consumer's
        FAILED result carrying the binding message, exactly as it does for any
        other unemitted port."""

        async def source(_params, _inputs):
            return {"nodes": [1]}

        async def gated(_params, _inputs):
            raise AssertionError("disabled node must not be dispatched")

        async def sink(_params, _inputs):
            return {"ok": True}

        register_handler("Source", source)
        register_handler("Gated", gated)
        register_handler("Sink", sink)

        graph = Graph(
            name="unmapped-port", version="1.0",
            nodes=[
                Node(id="src", type="Source", input_ports=[],
                     output_ports=[_port("nodes")]),
                Node(
                    id="ann", type="Gated",
                    params={"llm": None},
                    enabled_when="llm",
                    disabled_passthrough={"nodes": "nodes"},
                    input_ports=[_port("nodes")],
                    output_ports=[_port("nodes"), _port("provenance")],
                ),
                # Bound to `provenance`, which the mapping does not carry.
                Node(id="audit", type="Sink",
                     input_ports=[_port("provenance")], output_ports=[]),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="ann", target="audit", type="DATA",
                     from_port="provenance", to_port="provenance"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["status"] == "SKIPPED"
        assert results["audit"]["status"] == "FAILED"
        assert "provenance" in results["audit"]["error"]
        assert "did not emit" in results["audit"]["error"]

    def test_no_mapping_emits_no_ports_at_all(self):
        """`disabled_passthrough=None` forwards nothing. Documented, not
        softened: a consumer then fails at its own binding step."""

        async def source(_params, _inputs):
            return {"nodes": [1]}

        async def gated(_params, _inputs):
            raise AssertionError("disabled node must not be dispatched")

        async def sink(_params, _inputs):
            return {"ok": True}

        register_handler("Source", source)
        register_handler("Gated", gated)
        register_handler("Sink", sink)

        graph = Graph(
            name="no-mapping", version="1.0",
            nodes=[
                Node(id="src", type="Source", input_ports=[],
                     output_ports=[_port("nodes")]),
                Node(id="ann", type="Gated", params={"llm": None},
                     enabled_when="llm",
                     input_ports=[_port("nodes")], output_ports=[_port("nodes")]),
                Node(id="depth", type="Sink",
                     input_ports=[_port("nodes")], output_ports=[]),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="ann", target="depth", type="DATA",
                     from_port="nodes", to_port="nodes"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["status"] == "SKIPPED"
        assert "nodes" not in results["ann"]
        assert results["depth"]["status"] == "FAILED"

    def test_a_disabled_node_still_collects_its_inputs(self):
        """It must, to forward them — so the port-binding machinery applies
        unchanged, including its failure. An upstream that did not emit the
        bound port fails the disabled node itself, as FAILED, not SKIPPED."""

        async def source(_params, _inputs):
            return {"something_else": 1}

        async def gated(_params, _inputs):
            raise AssertionError("disabled node must not be dispatched")

        register_handler("Source", source)
        register_handler("Gated", gated)

        graph = Graph(
            name="binding-applies", version="1.0",
            nodes=[
                Node(id="src", type="Source", input_ports=[],
                     output_ports=[_port("nodes")]),
                Node(id="ann", type="Gated", params={"llm": None},
                     enabled_when="llm",
                     disabled_passthrough={"nodes": "nodes"},
                     input_ports=[_port("nodes")], output_ports=[_port("nodes")]),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA",
                     from_port="nodes", to_port="nodes"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["status"] == "FAILED"
        assert "did not emit" in results["ann"]["error"]


class TestEnabled:
    def test_truthy_param_dispatches_exactly_as_an_undeclared_node(self):
        """Clause 4's other side: a truthy predicate param is not a special
        regime. The node is dispatched byte-for-byte as if it declared no
        predicate at all — same params, same inputs, same result shape."""
        gated_calls: list[tuple] = []
        plain_calls: list[tuple] = []

        async def gated(params, inputs):
            gated_calls.append((params, inputs))
            return {"v": 1}

        async def plain(params, inputs):
            plain_calls.append((params, inputs))
            return {"v": 1}

        register_handler("Gated", gated)
        register_handler("Plain", plain)

        params = {"llm": {"model_id": "m"}}
        graph = Graph(
            name="enabled", version="1.0",
            nodes=[
                Node(id="a", type="Gated", params=params, enabled_when="llm"),
                Node(id="b", type="Plain", params=params),
            ],
            edges=[],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["a"] == results["b"] | {"node_id": "a"}
        assert results["a"]["status"] == "SUCCESS"
        assert "skip_reason" not in results["a"]
        assert gated_calls == plain_calls
        assert graph.get_node("a").status == "SUCCESS"

    def test_enabled_when_none_is_the_legacy_regime(self):
        """The fence's legacy side: no predicate declared → the node always
        runs, exactly as before this field existed, even with a param whose
        value is falsy."""
        ran: list[str] = []

        async def handler(_params, _inputs):
            ran.append("handler")
            return {"v": 1}

        register_handler("Plain", handler)
        node = Node(id="n", type="Plain", params={"llm": None})
        assert node.enabled_when is None

        results = asyncio.run(execute_graph(_one_node(node)))

        assert results["n"]["status"] == "SUCCESS"
        assert ran == ["handler"]
        assert node.status == "SUCCESS"


class TestResourceSequencing:
    def test_disabled_node_never_asks_for_its_resource(self):
        """Clause 2 — the raise/skip tension dissolves by SEQUENCING. The
        config predicate is read first, so a node configured off is never
        dispatched, never asks for its resource, and the pre-loop supply check
        passes over it. No UnsuppliedResourceError."""
        ran: list[str] = []

        async def handler(_params, _inputs, *, resources):
            ran.append("handler")
            return {"v": 1}

        register_handler("Gated", handler)
        node = Node(
            id="n", type="Gated",
            params={"llm": None},
            enabled_when="llm",
            resources=["anthropic_client"],
        )

        results = asyncio.run(execute_graph(_one_node(node)))  # supplies nothing

        assert results["n"]["status"] == "SKIPPED"
        assert results["n"]["skip_reason"] == "disabled_by_config"
        assert ran == []

    def test_enabled_node_with_no_supply_still_raises(self):
        """The half of clause 2 that MUST survive: configured ON and unsupplied
        raises, with zero exceptions. The address includes the config, so a
        silent skip here would mint an artifact whose address claims annotated
        bytes that do not exist. The raise protects the content address."""

        async def handler(_params, _inputs, *, resources):
            return {"v": 1}

        register_handler("Gated", handler)
        node = Node(
            id="n", type="Gated",
            params={"llm": {"model_id": "m"}},
            enabled_when="llm",
            resources=["anthropic_client"],
        )

        with pytest.raises(UnsuppliedResourceError) as exc:
            asyncio.run(execute_graph(_one_node(node)))

        assert "'n'" in str(exc.value)
        assert "anthropic_client" in str(exc.value)

    def test_a_disabled_node_does_not_excuse_an_enabled_one(self):
        """The pass-over is per node, on that node's own predicate — it does
        not weaken the pre-loop check for anyone else in the graph."""

        async def handler(_params, _inputs, *, resources):
            return {"v": 1}

        register_handler("Gated", handler)

        graph = Graph(
            name="mixed-supply", version="1.0",
            nodes=[
                Node(id="off", type="Gated", params={"llm": None},
                     enabled_when="llm", resources=["anthropic_client"]),
                Node(id="on", type="Gated", params={"llm": {"model_id": "m"}},
                     enabled_when="llm", resources=["anthropic_client"]),
            ],
            edges=[],
        )

        with pytest.raises(UnsuppliedResourceError) as exc:
            asyncio.run(execute_graph(graph))

        assert "'on'" in str(exc.value)

    def test_enabled_node_receives_its_resource_normally(self):
        """Nothing about the predicate changes the resource channel for a node
        that runs: same narrowed mapping, same keyword-only delivery."""
        seen: list[dict] = []

        async def handler(_params, _inputs, *, resources):
            seen.append(resources)
            return {"v": 1}

        register_handler("Gated", handler)
        client = object()
        node = Node(
            id="n", type="Gated",
            params={"llm": {"model_id": "m"}},
            enabled_when="llm",
            resources=["anthropic_client"],
        )

        results = asyncio.run(
            execute_graph(_one_node(node), resources={"anthropic_client": client})
        )

        assert results["n"]["status"] == "SUCCESS"
        assert seen == [{"anthropic_client": client}]


class TestTruthiness:
    """These tests DOCUMENT the truthiness behaviour; they do not rule it.

    `enabled_when` names a param and the executor tests ordinary Python
    truthiness of `params.get(name)`. That is the reading a plain
    `if node.params[name]` would give, chosen so the field needs no lookup
    table to predict — and it means an ABSENT key is None and therefore
    disabled, alongside 0, '', [], {} and False. If a future node needs "0 is a
    real value", that is the second predicate shape clause 4 defers to, and it
    arrives as a declared shape, not as a special case here.
    """

    def _ran(self, node: Node) -> bool:
        ran: list[str] = []

        async def handler(_params, _inputs):
            ran.append("handler")
            return {"v": 1}

        register_handler("Gated", handler)
        asyncio.run(execute_graph(_one_node(node)))
        return ran == ["handler"]

    def test_absent_key_is_disabled(self):
        """The documented absent-key case: no such param → None → falsy →
        disabled. The node does not run and does not error."""
        node = Node(id="n", type="Gated", params={}, enabled_when="llm")

        assert self._ran(node) is False

    @pytest.mark.parametrize("value", [None, False, 0, 0.0, "", [], {}])
    def test_falsy_values_disable(self, value):
        node = Node(id="n", type="Gated", params={"flag": value}, enabled_when="flag")

        assert self._ran(node) is False

    @pytest.mark.parametrize("value", [True, 1, 0.5, "x", [0], {"k": "v"}])
    def test_truthy_values_enable(self, value):
        node = Node(id="n", type="Gated", params={"flag": value}, enabled_when="flag")

        assert self._ran(node) is True


class TestCascadeUnchanged:
    def test_upstream_failure_still_cascades_through_a_disabled_node(self):
        """The exemption is for `disabled_by_config` ONLY. A real upstream
        FAILED still cascades — including to a disabled node's neighbours, and
        including through the disabled node itself, which is cascade-SKIPPED
        before its predicate is ever consulted."""

        async def boom(_params, _inputs):
            raise RuntimeError("upstream broke")

        async def gated(_params, _inputs):
            raise AssertionError("must not be dispatched")

        async def sink(_params, _inputs):
            raise AssertionError("must not be dispatched")

        register_handler("Boom", boom)
        register_handler("Gated", gated)
        register_handler("Sink", sink)

        graph = Graph(
            name="cascade", version="1.0",
            nodes=[
                Node(id="src", type="Boom"),
                Node(id="ann", type="Gated", params={"llm": None},
                     enabled_when="llm", disabled_passthrough={"nodes": "nodes"}),
                Node(id="depth", type="Sink"),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA"),
                Edge(source="ann", target="depth", type="DATA"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["src"]["status"] == "FAILED"
        assert results["ann"]["status"] == "SKIPPED"
        # Cascade-skipped, NOT configured off — the reasons stay separable.
        assert "skip_reason" not in results["ann"]
        assert results["depth"]["status"] == "SKIPPED"

    def test_upstream_cascade_skip_still_cascades_past_a_disabled_neighbour(self):
        """A plain upstream SKIPPED is not exempt either: only the
        distinguished config-skip forwards."""

        async def boom(_params, _inputs):
            raise RuntimeError("upstream broke")

        async def plain(_params, _inputs):
            return {"v": 1}

        async def gated(_params, _inputs):
            raise AssertionError("must not be dispatched")

        register_handler("Boom", boom)
        register_handler("Plain", plain)
        register_handler("Gated", gated)

        graph = Graph(
            name="cascade-chain", version="1.0",
            nodes=[
                Node(id="a", type="Boom"),
                Node(id="b", type="Plain"),
                Node(id="c", type="Gated", params={"llm": None}, enabled_when="llm"),
            ],
            edges=[
                Edge(source="a", target="b", type="DATA"),
                Edge(source="b", target="c", type="DATA"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["b"]["status"] == "SKIPPED"
        assert results["c"]["status"] == "SKIPPED"
        assert "skip_reason" not in results["c"]  # cascade reached it first

    def test_a_disabled_node_between_two_failures_does_not_launder_them(self):
        """Forwarding is not immunity: the disabled node forwards, and a
        downstream node that then fails on its own cascades normally."""

        async def source(_params, _inputs):
            return {"nodes": [1]}

        async def gated(_params, _inputs):
            raise AssertionError("must not be dispatched")

        async def boom(_params, _inputs):
            raise RuntimeError("downstream broke")

        async def sink(_params, _inputs):
            raise AssertionError("must not be dispatched")

        register_handler("Source", source)
        register_handler("Gated", gated)
        register_handler("Boom", boom)
        register_handler("Sink", sink)

        graph = Graph(
            name="forward-then-fail", version="1.0",
            nodes=[
                Node(id="src", type="Source", input_ports=[],
                     output_ports=[_port("nodes")]),
                Node(id="ann", type="Gated", params={"llm": None},
                     enabled_when="llm", disabled_passthrough={"nodes": "nodes"},
                     input_ports=[_port("nodes")], output_ports=[_port("nodes")]),
                Node(id="depth", type="Boom",
                     input_ports=[_port("nodes")], output_ports=[_port("out")]),
                Node(id="tail", type="Sink",
                     input_ports=[_port("out")], output_ports=[]),
            ],
            edges=[
                Edge(source="src", target="ann", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="ann", target="depth", type="DATA",
                     from_port="nodes", to_port="nodes"),
                Edge(source="depth", target="tail", type="DATA",
                     from_port="out", to_port="out"),
            ],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["status"] == "SKIPPED"
        assert results["depth"]["status"] == "FAILED"  # it RAN, and it broke
        assert results["tail"]["status"] == "SKIPPED"


class TestNode55Declarations:
    """The channel's first real subject — Node 5.5, the pipeline's LLM node.

    The clauses are stated in terms of this node, so pin what it declares.
    """

    def test_the_predicate_names_the_config_not_the_resource(self):
        """Clause 1 — the gate reads CONFIG PRESENCE ONLY. `llm` is the frozen
        LLMConfig in params, which enters the content address; the live client
        is a resource and never carries the enable/disable decision."""
        from idiograph.domains.arxiv.relationship_annotation import (
            ANNOTATE_RELATIONSHIPS_ENABLED_WHEN,
        )

        assert ANNOTATE_RELATIONSHIPS_ENABLED_WHEN == "llm"

    def test_the_passthrough_maps_nodes_to_nodes(self):
        """Clause 6 — 5.5 rebinds the node set, so downstream `nodes` consumers
        bind to its output on the LLM path. Disabled, the same port carries the
        pre-annotation records forward. `provenance` is unmapped: there was no
        run to have provenance of."""
        from idiograph.domains.arxiv.relationship_annotation import (
            ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH,
            ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS,
        )

        assert ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH == {"nodes": "nodes"}
        out_names = {p.name for p in ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS}
        assert out_names == {"nodes", "provenance"}
        # Every mapped output port is a declared one.
        assert set(ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH) <= out_names

    def test_every_mapped_input_is_a_declared_input_port(self):
        """The mapping's values name input ports, so the forward is readable
        off the declaration without opening the handler."""
        from idiograph.domains.arxiv.relationship_annotation import (
            ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH,
            ANNOTATE_RELATIONSHIPS_INPUT_PORTS,
        )

        in_names = {p.name for p in ANNOTATE_RELATIONSHIPS_INPUT_PORTS}
        assert in_names == {"nodes", "resolved"}
        assert set(ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH.values()) <= in_names

    def test_dispatching_a_node_the_predicate_would_gate_off_raises(self):
        """The predicate's other side. The handler REQUIRES a non-null llm: a
        run with none never reaches it, so arriving with `llm` absent or None
        means a caller dispatched a node the predicate would have gated off.
        That is a caller defect and it raises rather than quietly annotating
        nothing — the silent skip clause 2 forbids."""
        from pydantic import ValidationError

        from idiograph.domains.arxiv.relationship_annotation import (
            annotate_relationships,
        )

        inputs = {"nodes": [], "resolved": []}
        resources = {"anthropic_client": object()}

        with pytest.raises(ValidationError):
            asyncio.run(annotate_relationships({}, inputs, resources=resources))

        with pytest.raises(ValidationError):
            asyncio.run(
                annotate_relationships({"llm": None}, inputs, resources=resources)
            )


class TestControlEdges:
    def test_config_skip_does_not_gate_a_control_dependent(self):
        """A CONTROL edge out of a disabled node does not gate its target:
        config-skip is not a failure to propagate, on either edge type."""
        ran: list[str] = []

        async def gated(_params, _inputs):
            raise AssertionError("must not be dispatched")

        async def sink(_params, _inputs):
            ran.append("sink")
            return {"ok": True}

        register_handler("Gated", gated)
        register_handler("Sink", sink)

        graph = Graph(
            name="control", version="1.0",
            nodes=[
                Node(id="ann", type="Gated", params={"llm": None},
                     enabled_when="llm"),
                Node(id="after", type="Sink"),
            ],
            edges=[Edge(source="ann", target="after", type="CONTROL")],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["ann"]["status"] == "SKIPPED"
        assert results["after"]["status"] == "SUCCESS"
        assert ran == ["sink"]
