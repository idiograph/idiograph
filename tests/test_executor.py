# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import pytest

from idiograph.core.executor import (
    HANDLERS,
    DuplicateNodeIdError,
    InjectedOutputError,
    UnregisteredNodeTypeError,
    UnsuppliedResourceError,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration


@pytest.fixture(autouse=True)
def clear_handlers():
    """Ensure handler registry is clean before each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


@pytest.fixture
def linear_graph() -> Graph:
    return Graph(
        name="linear_test",
        version="1.0",
        nodes=[
            Node(id="a", type="StubFetch",    params={"value": 1}),
            Node(id="b", type="StubProcess",  params={}),
            Node(id="c", type="StubOutput",   params={}),
        ],
        edges=[
            Edge(source="a", target="b", type="DATA"),
            Edge(source="b", target="c", type="DATA"),
        ],
    )


@pytest.fixture
def branching_graph() -> Graph:
    """Router with a CONTROL edge to one branch and a dead branch."""
    return Graph(
        name="branching_test",
        version="1.0",
        nodes=[
            Node(id="fetch",    type="StubFetch",   params={}),
            Node(id="gate",     type="StubGate",    params={"pass": True}),
            Node(id="summary",  type="StubSummary", params={}),
            Node(id="discard",  type="StubDiscard", params={}),
        ],
        edges=[
            Edge(source="fetch",   target="gate",    type="DATA"),
            Edge(source="gate",    target="summary", type="CONTROL"),
            Edge(source="gate",    target="discard", type="CONTROL"),
        ],
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHandlerRegistry:
    def test_register_and_dispatch(self):
        async def my_handler(params, inputs):
            return {"result": "ok"}

        register_handler("MyType", my_handler)
        assert "MyType" in HANDLERS

    def test_missing_handler_raises(self, linear_graph):
        """A registry miss is detectable before any handler runs, so it raises
        rather than becoming a FAILED result — the same side of the line as a
        cycle. The message names the node and the unregistered type."""
        import asyncio
        with pytest.raises(UnregisteredNodeTypeError) as excinfo:
            asyncio.run(execute_graph(linear_graph))

        message = str(excinfo.value)
        assert "'a'" in message
        assert "StubFetch" in message

    def test_missing_handler_error_escapes_execute_graph(self, linear_graph):
        """The no-handler branch sits outside the handler try/except: nothing
        converts it into `{"status": "FAILED"}`, and no partial results dict is
        returned in its place."""
        import asyncio

        async def stub(params, inputs):
            return {}

        # 'a' and 'c' are registered; 'b' is not. A partial registration is what
        # a per-node lookup would have carried into the loop; the preflight
        # refuses the graph on it, and nothing turns that into a results dict.
        register_handler("StubFetch", stub)
        register_handler("StubOutput", stub)

        with pytest.raises(UnregisteredNodeTypeError):
            asyncio.run(execute_graph(linear_graph))

    def test_unregistered_last_type_raises_before_anything_runs(self, linear_graph):
        """The promise is BEFORE ANY HANDLER RUNS, and the only graph that can
        tell a preflight from a per-node lookup is one whose unregistered type
        is topologically LAST. Resolved inside the loop, this graph executes 'a'
        and 'b' first and raises having already paid for work the run was always
        going to discard."""
        import asyncio

        executed: list[str] = []

        async def recording(params, inputs):
            executed.append(params["name"])
            return {}

        # Everything but the tail is registered, so a per-node lookup reaches
        # the raise only after both upstream handlers have run.
        register_handler("StubFetch", recording)
        register_handler("StubProcess", recording)
        linear_graph.get_node("a").params = {"name": "a"}
        linear_graph.get_node("b").params = {"name": "b"}

        with pytest.raises(UnregisteredNodeTypeError):
            asyncio.run(execute_graph(linear_graph))

        assert executed == []

    def test_every_unregistered_type_is_named_at_once(self, linear_graph):
        """The whole graph is in hand at the preflight, so one raise reports the
        whole registration gap — not the first type the order happens to reach."""
        import asyncio

        async def stub(params, inputs):
            return {}

        register_handler("StubProcess", stub)

        with pytest.raises(UnregisteredNodeTypeError) as excinfo:
            asyncio.run(execute_graph(linear_graph))

        message = str(excinfo.value)
        assert "StubFetch" in message
        assert "StubOutput" in message
        assert "StubProcess" not in message

    def test_disabled_node_needs_no_handler(self):
        """The config predicate is read FIRST, as it is in `_check_resource_supply`
        and in the loop: a node configured off is never dispatched, so a run that
        disables it needs no handler registered for its type."""
        import asyncio

        async def stub(_params, _inputs):
            return {}

        register_handler("StubTail", stub)

        graph = Graph(
            name="disabled_unregistered",
            version="1.0",
            nodes=[
                Node(
                    id="off",
                    type="NothingImplementsThis",
                    params={"on": False},
                    enabled_when="on",
                ),
                Node(id="tail", type="StubTail", params={}),
            ],
            edges=[],
        )

        results = asyncio.run(execute_graph(graph))

        assert results["off"]["status"] == "SKIPPED"
        assert results["tail"]["status"] == "SUCCESS"

    def test_injection_does_not_waive_handler_registration(self):
        """Injection is NOT an exemption, on the reading `_check_resource_supply`
        already takes of it: a node whose output the run supplies is still a
        declared node of its type, and a type nothing implements is a defect in
        the graph rather than a fact about one invocation."""
        import asyncio

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubTail", tail)

        with pytest.raises(UnregisteredNodeTypeError) as excinfo:
            asyncio.run(execute_graph(_head_graph(), None, {"head": {"value": 1}}))

        assert "StubHead" in str(excinfo.value)

    def test_missing_handler_leaves_node_status_untouched(self, linear_graph):
        """Node status is graph state, and graph state is the ran-a-handler
        side of the line. A type that was never dispatched is not marked
        FAILED."""
        import asyncio
        with pytest.raises(UnregisteredNodeTypeError):
            asyncio.run(execute_graph(linear_graph))

        assert {n.id: n.status for n in linear_graph.nodes}["a"] == "PENDING"


class TestLinearExecution:
    def test_data_flows_between_nodes(self, linear_graph):
        async def fetch(params, inputs):
            return {"value": params["value"]}

        async def process(params, inputs):
            upstream = next(iter(inputs.values()))
            return {"value": upstream["value"] * 2}

        async def output(params, inputs):
            upstream = next(iter(inputs.values()))
            return {"final": upstream["value"]}

        register_handler("StubFetch",   fetch)
        register_handler("StubProcess", process)
        register_handler("StubOutput",  output)

        import asyncio
        results = asyncio.run(execute_graph(linear_graph))

        assert results["a"]["status"] == "SUCCESS"
        assert results["b"]["status"] == "SUCCESS"
        assert results["b"]["value"] == 2
        assert results["c"]["final"] == 2

    def test_node_status_updated_in_graph(self, linear_graph):
        async def stub(params, inputs):
            return {}

        register_handler("StubFetch",   stub)
        register_handler("StubProcess", stub)
        register_handler("StubOutput",  stub)

        import asyncio
        asyncio.run(execute_graph(linear_graph))

        node_map = {n.id: n for n in linear_graph.nodes}
        assert node_map["a"].status == "SUCCESS"
        assert node_map["b"].status == "SUCCESS"
        assert node_map["c"].status == "SUCCESS"


class TestFailurePropagation:
    def test_failed_data_dependency_skips_downstream(self, linear_graph):
        async def failing(params, inputs):
            raise RuntimeError("Simulated failure")

        async def stub(params, inputs):
            return {}

        register_handler("StubFetch",   failing)
        register_handler("StubProcess", stub)
        register_handler("StubOutput",  stub)

        import asyncio
        results = asyncio.run(execute_graph(linear_graph))

        assert results["a"]["status"] == "FAILED"
        assert results["b"]["status"] == "SKIPPED"
        assert results["c"]["status"] == "SKIPPED"

    def test_failed_control_dependency_skips_downstream(self, branching_graph):
        async def stub(params, inputs):
            return {}

        async def failing_gate(params, inputs):
            raise RuntimeError("Gate failed")

        register_handler("StubFetch",   stub)
        register_handler("StubGate",    failing_gate)
        register_handler("StubSummary", stub)
        register_handler("StubDiscard", stub)

        import asyncio
        results = asyncio.run(execute_graph(branching_graph))

        assert results["gate"]["status"] == "FAILED"
        assert results["summary"]["status"] == "SKIPPED"
        assert results["discard"]["status"] == "SKIPPED"


class TestDuplicateNodeIds:
    """A reused id is refused by the same preflight class a cycle is: it is
    knowable without running anything, and every reader of the graph resolves it
    differently, so there is no correct run to attempt."""

    def _twins(self) -> Graph:
        return Graph(
            name="twins",
            version="1.0",
            nodes=[
                Node(id="a", type="StubFetch", params={"which": "first"}),
                Node(id="a", type="StubFetch", params={"which": "second"}),
                Node(id="b", type="StubProcess", params={}),
            ],
            edges=[Edge(source="a", target="b", type="DATA")],
        )

    def test_duplicate_ids_refuse_the_graph_before_any_handler_runs(self):
        import asyncio

        executed: list[str] = []

        async def recording(params, inputs):
            executed.append(params.get("which", "b"))
            return {}

        register_handler("StubFetch", recording)
        register_handler("StubProcess", recording)

        with pytest.raises(DuplicateNodeIdError) as excinfo:
            asyncio.run(execute_graph(self._twins()))

        assert "'a'" in str(excinfo.value)
        assert executed == []

    def test_duplicate_ids_outrank_the_other_preflights(self):
        """Identity is checked FIRST. With no handlers registered at all this
        graph is defective twice over, and the duplicate is what it raises on —
        the registration check reads a projection the duplicate has already
        collapsed."""
        import asyncio

        with pytest.raises(DuplicateNodeIdError):
            asyncio.run(execute_graph(self._twins()))


class TestCycleDetection:
    def test_cyclic_graph_raises(self, cyclic_graph):
        import asyncio
        with pytest.raises(ValueError, match="cycle"):
            asyncio.run(execute_graph(cyclic_graph))


# ── Per-node output injection ────────────────────────────────────────────────
#
# `execute_graph(graph, resources, outputs)` lets a run hand in an output it
# already computed for a head node instead of re-running it. The parameter is
# run-owned in the same sense `resources` is — the value belongs to the
# invocation, never to the graph, and never enters a content address.


def _port(name: str) -> PortDeclaration:
    return PortDeclaration(name=name, port_type="untyped")


def _head_graph(**head_kwargs) -> Graph:
    """A bound two-node graph whose head declares `input_ports=[]`.

    The empty list is a DECLARATION — the head reads nothing, which is what
    makes it injectable — and the tail binds its one declared port to the
    head's output, so an injected payload is observable downstream rather than
    only in the results dict.
    """
    return Graph(
        name="injection_test",
        version="1.0",
        nodes=[
            Node(
                id="head",
                type="StubHead",
                params={},
                input_ports=[],
                output_ports=[_port("value")],
                **head_kwargs,
            ),
            Node(
                id="tail",
                type="StubTail",
                params={},
                input_ports=[_port("value")],
                output_ports=[_port("seen")],
            ),
        ],
        edges=[
            Edge(source="head", target="tail", from_port="value", to_port="value"),
        ],
    )


class TestOutputInjection:
    def test_injected_node_is_not_dispatched(self):
        """The supplied output stands in for the handler: it is never called."""
        import asyncio

        async def must_not_run(_params, _inputs):
            raise AssertionError("injected node's handler was dispatched")

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", must_not_run)
        register_handler("StubTail", tail)

        results = asyncio.run(
            execute_graph(_head_graph(), None, {"head": {"value": 7}})
        )

        assert results["head"]["value"] == 7
        assert results["head"]["status"] == "SUCCESS"
        assert results["head"]["injected"] is True
        assert results["head"]["node_id"] == "head"

    def test_injected_output_flows_downstream(self):
        """A downstream bound node reads the injected payload off the declared
        port exactly as it would a computed one — injection is invisible to it."""
        import asyncio

        async def unused(_params, _inputs):
            raise AssertionError("should not run")

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", unused)
        register_handler("StubTail", tail)

        results = asyncio.run(
            execute_graph(_head_graph(), None, {"head": {"value": "x"}})
        )

        assert results["tail"]["seen"] == "x"
        assert results["tail"]["status"] == "SUCCESS"

    def test_injected_node_status_is_success(self):
        """Node status is graph state and the graph is the source of truth: an
        injected node's output is present, so it reads SUCCESS, not PENDING."""
        import asyncio

        async def unused(_params, _inputs):
            raise AssertionError("should not run")

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", unused)
        register_handler("StubTail", tail)

        graph = _head_graph()
        asyncio.run(execute_graph(graph, None, {"head": {"value": 1}}))

        assert {n.id: n.status for n in graph.nodes}["head"] == "SUCCESS"

    def test_bookkeeping_keys_win_over_supplied_ones(self):
        """The stamp is applied AFTER the splat. A supplied dict carrying a
        bookkeeping key cannot forge its own status — which is the collision
        `RESERVED_PORT_NAMES` rejects at declaration time."""
        import asyncio

        async def unused(_params, _inputs):
            raise AssertionError("should not run")

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", unused)
        register_handler("StubTail", tail)

        results = asyncio.run(
            execute_graph(
                _head_graph(),
                None,
                {"head": {"value": 1, "status": "FAILED", "node_id": "forged"}},
            )
        )

        assert results["head"]["status"] == "SUCCESS"
        assert results["head"]["node_id"] == "head"

    def test_unnamed_nodes_execute_normally(self):
        """Injection is per-node: a node the mapping does not name is dispatched."""
        import asyncio

        async def head(_params, _inputs):
            return {"value": "computed"}

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", head)
        register_handler("StubTail", tail)

        results = asyncio.run(execute_graph(_head_graph(), None, {}))

        assert results["head"]["value"] == "computed"
        assert "injected" not in results["head"]

    def test_default_none_keeps_existing_calls_exact(self):
        """The parameter is ADDITIVE: a caller supplying nothing keeps the exact
        call it had, and no result carries the injection marker."""
        import asyncio

        async def head(_params, _inputs):
            return {"value": 1}

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", head)
        register_handler("StubTail", tail)

        results = asyncio.run(execute_graph(_head_graph()))

        assert all("injected" not in r for r in results.values())


class TestInjectionRestriction:
    """Only `input_ports == []` is injectable — the fence fails closed."""

    def test_node_with_declared_inputs_raises(self):
        """A bound node with real input ports has upstream bindings that an
        injection would silently discard, so it raises rather than executing a
        graph whose dataflow is partly fiction."""
        import asyncio

        async def head(_params, _inputs):
            return {"value": 1}

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", head)
        register_handler("StubTail", tail)

        with pytest.raises(InjectedOutputError) as excinfo:
            asyncio.run(
                execute_graph(_head_graph(), None, {"tail": {"seen": "forged"}})
            )

        assert "'tail'" in str(excinfo.value)

    def test_legacy_none_input_ports_raises(self):
        """`None` is the LEGACY regime, not an empty declaration. It is not
        injectable: the node receives every upstream payload keyed by source id,
        and nothing declares that it reads none of them."""
        import asyncio

        async def stub(_params, _inputs):
            return {}

        register_handler("StubFetch", stub)
        register_handler("StubProcess", stub)
        register_handler("StubOutput", stub)

        graph = Graph(
            name="legacy_injection",
            version="1.0",
            nodes=[Node(id="a", type="StubFetch", params={})],
            edges=[],
        )

        with pytest.raises(InjectedOutputError) as excinfo:
            asyncio.run(execute_graph(graph, None, {"a": {"value": 1}}))

        assert "'a'" in str(excinfo.value)

    def test_disabled_by_config_outranks_injection(self):
        """A node both disabled and named in the mapping is SKIPPED and its
        supplied output DISCARDED — ruled precedence, not accident. The disable
        branch sits above the injection branch and returns first."""
        import asyncio

        async def unused(_params, _inputs):
            raise AssertionError("should not run")

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", unused)
        register_handler("StubTail", tail)

        graph = _head_graph(
            enabled_when="on",
            disabled_passthrough={},
        )
        graph.get_node("head").params = {"on": False}

        results = asyncio.run(
            execute_graph(graph, None, {"head": {"value": "discarded"}})
        )

        assert results["head"]["status"] == "SKIPPED"
        assert results["head"]["skip_reason"] == "disabled_by_config"
        assert "value" not in results["head"]
        assert "injected" not in results["head"]

    def test_injection_does_not_waive_resource_supply(self):
        """`_check_resource_supply` is DELIBERATELY unchanged: it runs over the
        whole graph before the loop, so an injected node still requires the
        resources it declares. Supply is a fact about the run, and injecting an
        output does not make the run capable."""
        import asyncio

        async def unused(_params, _inputs, *, resources):
            raise AssertionError("should not run")

        async def tail(_params, inputs):
            return {"seen": inputs["value"]}

        register_handler("StubHead", unused)
        register_handler("StubTail", tail)

        graph = _head_graph(resources=["http_client"])

        with pytest.raises(UnsuppliedResourceError):
            asyncio.run(execute_graph(graph, None, {"head": {"value": 1}}))


class TestFailedResultCarriesTheException:
    def test_exception_object_alongside_error_string(self):
        """IDG-063 clause 3, additive. The `error` string stays exactly what it
        was; the OBJECT rides alongside so a caller that halts on FAILED can
        re-raise with the original type and traceback intact."""
        import asyncio

        boom = RuntimeError("Simulated failure")

        async def failing(_params, _inputs):
            raise boom

        async def stub(_params, _inputs):
            return {}

        register_handler("StubFetch", failing)
        register_handler("StubProcess", stub)
        register_handler("StubOutput", stub)

        graph = Graph(
            name="one_node",
            version="1.0",
            nodes=[Node(id="a", type="StubFetch", params={})],
            edges=[],
        )
        results = asyncio.run(execute_graph(graph))

        assert results["a"]["error"] == "Simulated failure"
        assert results["a"]["exception"] is boom

    def test_successful_result_carries_no_exception(self):
        """Only the failure path carries it — a SUCCESS result is unchanged."""
        import asyncio

        async def stub(_params, _inputs):
            return {"ok": True}

        register_handler("StubFetch", stub)

        graph = Graph(
            name="one_node",
            version="1.0",
            nodes=[Node(id="a", type="StubFetch", params={})],
            edges=[],
        )
        results = asyncio.run(execute_graph(graph))

        assert "exception" not in results["a"]
