import pytest
from nodeforge.core.models import Node, Edge, Graph
from nodeforge.core.executor import execute_graph, register_handler, HANDLERS


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

    def test_missing_handler_returns_failed_status(self, linear_graph):
        # No handlers registered — all nodes should fail
        import asyncio
        results = asyncio.run(execute_graph(linear_graph))
        assert results["a"]["status"] == "FAILED"
        assert "No handler registered" in results["a"]["error"]


class TestLinearExecution:
    def test_data_flows_between_nodes(self, linear_graph):
        async def fetch(params, inputs):
            return {"value": params["value"]}

        async def process(params, inputs):
            upstream = list(inputs.values())[0]
            return {"value": upstream["value"] * 2}

        async def output(params, inputs):
            upstream = list(inputs.values())[0]
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


class TestCycleDetection:
    def test_cyclic_graph_raises(self, cyclic_graph):
        import asyncio
        with pytest.raises(ValueError, match="cycle"):
            asyncio.run(execute_graph(cyclic_graph))