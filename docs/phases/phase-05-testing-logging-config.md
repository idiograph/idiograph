# Phase 5 Summary — Testing, Logging, Config

## What Was Built
The system gained a pytest test suite covering all three layers (models, graph functions, query functions), a structured logging setup scoped to the `idiograph` namespace, and a TOML config loader. The system is now stable enough to hand to an agent without silent failure modes.

## Project Structure
```
E:\projects\nodeforge
│   pyproject.toml
│   .python-version
│   README.md
│   idiograph.toml       ← new
│
├───tests/               ← new
│       conftest.py
│       test_models.py
│       test_graph.py
│       test_query.py
│
└───src
    └───idiograph
            __init__.py
            main.py               ← updated: startup callback
            core/
                __init__.py       ← updated: new exports
                pipeline.py
                models.py
                graph.py          ← updated: logging added
                query.py          ← updated: logging added
                config.py         ← new
                logging_config.py ← new
```

## Key Decisions

**`tests/` at repo root, no `__init__.py`** — standard pytest layout. Tests are not part of the package; they have no business inside `src/`. pytest discovers them without an `__init__.py`, and the absence of one avoids import path ambiguities.

**Fixtures in `conftest.py`, not inline** — `sample_graph` and `cyclic_graph` are shared across all three test files. Duplicating graph construction in every test would make the suite brittle: one schema change would require hunting down every inline dict. The fixture is the single place to update.

**Tests construct their own data, not `SAMPLE_PIPELINE`** — `SAMPLE_PIPELINE` is production sample data. Tests that depend on it are testing a specific content fixture, not the system's behavior. If `SAMPLE_PIPELINE` ever changes, those tests break for the wrong reason. The test fixtures define exactly the graph shape each test needs.

**`tomllib` (stdlib), no new dependency** — `tomllib` shipped in Python 3.11. Since the project targets 3.13, there is no reason to reach for `tomli` or `tomlkit`. Binary mode (`"rb"`) is required by the spec — `tomllib` will raise `TypeError` on text mode, not silently misread.

**Config falls back to defaults silently** — the config loader returns `_DEFAULTS` if `idiograph.toml` is absent. It never crashes. A missing config file is not an error; it means "use defaults." This matters for agents and CI environments where the file may not be present.

**Logger scoped to `idiograph` namespace** — all child loggers (`idiograph.graph`, `idiograph.query`, etc.) inherit from the root `idiograph` logger. A single `setup_logging()` call at startup controls the whole system. External code can set `logging.getLogger("idiograph").setLevel(logging.DEBUG)` to get full visibility without touching application code.

**`setup_logging()` guard on handler duplication** — `if not _LOGGER.handlers` prevents stacked handlers when `setup_logging()` is called multiple times (common in test runs). Without this, each test session would add another handler and duplicate every log line.

**`@app.callback()` for startup initialization** — Typer's callback fires before any subcommand runs. Config is loaded and logging is configured once, at entry, regardless of which command was invoked. This is the correct hook for application-level initialization — not inside individual command functions.

**Log level placement** — INFO for meaningful state changes (`load_graph` confirming a graph loaded), WARNING for structural problems (`validate_integrity` finding errors), DEBUG for routine checks that are only useful when diagnosing a specific problem. No log calls inside query traversal functions — those are pure computation with no side effects worth surfacing at runtime.

## Files

### `idiograph.toml`
```toml
[idiograph]
log_level = "INFO"
default_graph = ""
```

### `src/idiograph/core/config.py`
```python
import tomllib
from pathlib import Path

_DEFAULTS: dict = {
    "log_level": "INFO",
    "default_graph": "",
}


def load_config(path: Path | None = None) -> dict:
    """
    Load idiograph.toml from the given path (or the project root by default).
    Falls back to defaults silently if the file is absent — never crashes on missing config.
    """
    if path is None:
        path = Path("idiograph.toml")

    if not path.exists():
        return dict(_DEFAULTS)

    with open(path, "rb") as f:  # tomllib requires binary mode
        raw = tomllib.load(f)

    config = dict(_DEFAULTS)
    config.update(raw.get("idiograph", {}))
    return config
```

### `src/idiograph/core/logging_config.py`
```python
import logging

_LOGGER = logging.getLogger("idiograph")


def setup_logging(level: str = "INFO") -> None:
    """
    Configure the idiograph root logger.
    Safe to call multiple times — only adds a handler if none exists.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    _LOGGER.setLevel(numeric_level)

    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        ))
        _LOGGER.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the idiograph namespace."""
    return logging.getLogger(f"idiograph.{name}")
```

### `src/idiograph/core/graph.py`
```python
from idiograph.core.models import Graph, Node, Edge
from idiograph.core.logging_config import get_logger

_log = get_logger("graph")


def get_node(graph: Graph, node_id: str) -> Node | None:
    return graph.get_node(node_id)


def get_edges_from(graph: Graph, node_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.source == node_id]


def summarize(graph: Graph) -> dict:
    status_counts: dict[str, int] = {}
    for node in graph.nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1

    node_types: dict[str, int] = {}
    for node in graph.nodes:
        node_types[node.type] = node_types.get(node.type, 0) + 1

    return {
        "pipeline": graph.name,
        "version": graph.version,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "status_breakdown": status_counts,
        "node_types": node_types,
    }


def load_graph(data: dict) -> Graph:
    """Construct and validate a Graph from a raw dict (e.g. parsed JSON)."""
    graph = Graph.model_validate(data)
    _log.info("Loaded graph '%s' — %d nodes, %d edges.", graph.name, len(graph.nodes), len(graph.edges))
    return graph
```

### `src/idiograph/core/query.py` *(logging additions only — full file)*
```python
import networkx as nx
from idiograph.core.models import Graph, Node
from idiograph.core.logging_config import get_logger

_log = get_logger("query")


def _build_nx_graph(graph: Graph) -> nx.DiGraph:
    dg = nx.DiGraph()
    for node in graph.nodes:
        dg.add_node(node.id)
    for edge in graph.edges:
        dg.add_edge(edge.source, edge.target, type=edge.type)
    return dg


def get_downstream(graph: Graph, node_id: str) -> list[str]:
    dg = _build_nx_graph(graph)
    if node_id not in dg:
        return []
    return list(nx.descendants(dg, node_id))


def get_upstream(graph: Graph, node_id: str) -> list[str]:
    dg = _build_nx_graph(graph)
    if node_id not in dg:
        return []
    return list(nx.ancestors(dg, node_id))


def topological_sort(graph: Graph) -> list[str]:
    dg = _build_nx_graph(graph)
    try:
        return list(nx.topological_sort(dg))
    except nx.NetworkXUnfeasible:
        raise ValueError("Graph contains a cycle — topological sort is not possible.")


def find_cycles(graph: Graph) -> list[list[str]]:
    dg = _build_nx_graph(graph)
    return list(nx.simple_cycles(dg))


def validate_integrity(graph: Graph) -> dict:
    node_ids = {node.id for node in graph.nodes}
    errors = []

    for edge in graph.edges:
        if edge.source not in node_ids:
            errors.append(f"Edge {edge.source} → {edge.target}: source '{edge.source}' does not exist.")
        if edge.target not in node_ids:
            errors.append(f"Edge {edge.source} → {edge.target}: target '{edge.target}' does not exist.")

    if errors:
        _log.warning("Integrity check failed for '%s': %d error(s).", graph.name, len(errors))
    else:
        _log.debug("Integrity check passed for '%s'.", graph.name)

    return {"valid": len(errors) == 0, "errors": errors}


def summarize_intent(graph: Graph, node_ids: list[str] | None = None) -> dict:
    if node_ids is not None:
        nodes = [n for n in graph.nodes if n.id in node_ids]
        scoped_ids = {n.id for n in nodes}
        edges = [e for e in graph.edges if e.source in scoped_ids and e.target in scoped_ids]
    else:
        nodes = graph.nodes
        edges = graph.edges

    if not nodes:
        return {"error": "No nodes found in scope."}

    type_counts: dict[str, int] = {}
    for node in nodes:
        type_counts[node.type] = type_counts.get(node.type, 0) + 1

    status_counts: dict[str, int] = {}
    for node in nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1

    edge_type_counts: dict[str, int] = {}
    for edge in edges:
        edge_type_counts[edge.type] = edge_type_counts.get(edge.type, 0) + 1

    vfx_types = {"LoadAsset", "Render", "Simulate", "ApplyShader", "Cache",
                 "Composite", "ShaderValidate", "RenderComparison", "LookApproval", "MaterialAssign"}
    ai_types  = {"LLMCall", "VectorRetrieve", "ToolInvoke", "Evaluator",
                 "Router", "MemoryUpdate", "HumanInLoop"}

    node_type_set = set(type_counts.keys())
    has_vfx = bool(node_type_set & vfx_types)
    has_ai  = bool(node_type_set & ai_types)

    if has_vfx and has_ai:
        domain = "hybrid"
    elif has_vfx:
        domain = "vfx"
    elif has_ai:
        domain = "ai"
    else:
        domain = "unknown"

    dg = _build_nx_graph(Graph(name=graph.name, version=graph.version, nodes=nodes, edges=edges))
    sources = [n for n in dg.nodes if dg.in_degree(n) == 0]
    sinks   = [n for n in dg.nodes if dg.out_degree(n) == 0]

    critical_path: list[str] = []
    for source in sources:
        for sink in sinks:
            try:
                path = nx.shortest_path(dg, source, sink)
                if len(path) > len(critical_path):
                    critical_path = path
            except nx.NetworkXNoPath:
                continue

    control_gates = [e.source for e in edges if e.type == "CONTROL"]
    failed_nodes = [n.id for n in nodes if n.status == "FAILED"]

    return {
        "graph": graph.name,
        "scope": "full" if node_ids is None else "subgraph",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "domain": domain,
        "node_types": type_counts,
        "status": status_counts,
        "edge_types": edge_type_counts,
        "critical_path": critical_path,
        "control_gates": control_gates,
        "failed_nodes": failed_nodes,
    }
```

### `src/idiograph/core/__init__.py`
```python
from idiograph.core.pipeline import SAMPLE_PIPELINE
from idiograph.core.graph import summarize, get_node, get_edges_from, load_graph
from idiograph.core.config import load_config
from idiograph.core.logging_config import setup_logging, get_logger
```

### `src/idiograph/main.py`
```python
import json
import typer
from idiograph.core import SAMPLE_PIPELINE, summarize, load_graph, load_config, setup_logging
from idiograph.core.query import (
    get_downstream, get_upstream, topological_sort,
    find_cycles, validate_integrity, summarize_intent,
)
from pydantic import ValidationError

app = typer.Typer()
query_app = typer.Typer()
app.add_typer(query_app, name="query")


@app.callback()
def _startup():
    """Initialize logging and config before any command runs."""
    config = load_config()
    setup_logging(config.get("log_level", "INFO"))


@app.command()
def stats():
    """Output pipeline statistics as JSON."""
    typer.echo(json.dumps(summarize(SAMPLE_PIPELINE), indent=2))


@app.command()
def workflows():
    """Output the full pipeline manifest as JSON."""
    typer.echo(SAMPLE_PIPELINE.model_dump_json(indent=2))


@app.command()
def validate(path: str):
    """Validate a graph JSON file against the Idiograph schema."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        graph = load_graph(data)
        typer.echo(f"Valid — {len(graph.nodes)} nodes, {len(graph.edges)} edges.")
    except FileNotFoundError:
        typer.echo(f"Error: file not found: {path}")
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo("Validation failed:")
        typer.echo(e.json(indent=2))
        raise typer.Exit(1)


@app.command()
def check():
    """Run integrity and cycle checks on the default pipeline."""
    integrity = validate_integrity(SAMPLE_PIPELINE)
    cycles = find_cycles(SAMPLE_PIPELINE)
    result = {
        "integrity": integrity,
        "cycles_found": len(cycles) > 0,
        "cycles": cycles,
    }
    typer.echo(json.dumps(result, indent=2))


@query_app.command("downstream")
def query_downstream(node_id: str):
    """List all nodes reachable downstream from NODE_ID."""
    result = get_downstream(SAMPLE_PIPELINE, node_id)
    typer.echo(json.dumps({"node_id": node_id, "downstream": result}, indent=2))


@query_app.command("upstream")
def query_upstream(node_id: str):
    """List all nodes that are ancestors of NODE_ID."""
    result = get_upstream(SAMPLE_PIPELINE, node_id)
    typer.echo(json.dumps({"node_id": node_id, "upstream": result}, indent=2))


@query_app.command("topo")
def query_topo():
    """Output nodes in topological (execution) order."""
    try:
        result = topological_sort(SAMPLE_PIPELINE)
        typer.echo(json.dumps({"topological_order": result}, indent=2))
    except ValueError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(1)


@query_app.command("intent")
def query_intent():
    """Output a semantic intent summary of the default pipeline."""
    result = summarize_intent(SAMPLE_PIPELINE)
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
```

### `tests/conftest.py`
```python
import pytest
from idiograph.core.models import Node, Edge, Graph


@pytest.fixture
def sample_graph() -> Graph:
    return Graph(
        name="test_pipeline",
        version="1.0",
        nodes=[
            Node(id="n1", type="LoadAsset",      params={"path": "/test.usd"}),
            Node(id="n2", type="ApplyShader",    params={"shader": "pbr"}),
            Node(id="n3", type="ShaderValidate", params={}),
            Node(id="n4", type="LookApproval",   params={"threshold": 0.9}),
        ],
        edges=[
            Edge(source="n1", target="n2", type="DATA"),
            Edge(source="n2", target="n3", type="DATA"),
            Edge(source="n3", target="n4", type="CONTROL"),
        ],
    )


@pytest.fixture
def cyclic_graph() -> Graph:
    return Graph(
        name="cyclic_test",
        version="1.0",
        nodes=[
            Node(id="a", type="LLMCall",   params={}),
            Node(id="b", type="Evaluator", params={}),
        ],
        edges=[
            Edge(source="a", target="b", type="DATA"),
            Edge(source="b", target="a", type="CONTROL"),
        ],
    )
```

### `tests/test_models.py`
```python
import pytest
from pydantic import ValidationError
from idiograph.core.models import Node, Edge, Graph


class TestNode:
    def test_defaults(self):
        node = Node(id="n1", type="Render")
        assert node.status == "PENDING"
        assert node.params == {}

    def test_explicit_status(self):
        node = Node(id="n1", type="Render", status="SUCCESS")
        assert node.status == "SUCCESS"

    def test_params_are_independent(self):
        a = Node(id="a", type="Render")
        b = Node(id="b", type="Render")
        a.params["key"] = "value"
        assert "key" not in b.params

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            Node(id="n1")


class TestEdge:
    def test_defaults_to_data(self):
        edge = Edge(source="a", target="b")
        assert edge.type == "DATA"

    def test_accepts_extensible_type(self):
        edge = Edge(source="a", target="b", type="MODULATES")
        assert edge.type == "MODULATES"

    def test_control_type(self):
        edge = Edge(source="a", target="b", type="CONTROL")
        assert edge.type == "CONTROL"


class TestGraph:
    def test_construction(self, sample_graph):
        assert sample_graph.name == "test_pipeline"
        assert len(sample_graph.nodes) == 4
        assert len(sample_graph.edges) == 3

    def test_get_node_found(self, sample_graph):
        node = sample_graph.get_node("n2")
        assert node is not None
        assert node.type == "ApplyShader"

    def test_get_node_missing_returns_none(self, sample_graph):
        assert sample_graph.get_node("ghost") is None

    def test_empty_graph_is_valid(self):
        g = Graph(name="empty", version="0.1")
        assert g.nodes == []
        assert g.edges == []

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            Graph(version="1.0")
```

### `tests/test_graph.py`
```python
import pytest
from pydantic import ValidationError
from idiograph.core.graph import get_node, get_edges_from, summarize, load_graph


class TestGetNode:
    def test_returns_correct_node(self, sample_graph):
        node = get_node(sample_graph, "n3")
        assert node is not None
        assert node.type == "ShaderValidate"

    def test_returns_none_for_missing(self, sample_graph):
        assert get_node(sample_graph, "nonexistent") is None


class TestGetEdgesFrom:
    def test_returns_outgoing_edges(self, sample_graph):
        edges = get_edges_from(sample_graph, "n2")
        assert len(edges) == 1
        assert edges[0].target == "n3"

    def test_returns_empty_for_sink_node(self, sample_graph):
        edges = get_edges_from(sample_graph, "n4")
        assert edges == []


class TestSummarize:
    def test_counts_are_correct(self, sample_graph):
        result = summarize(sample_graph)
        assert result["node_count"] == 4
        assert result["edge_count"] == 3
        assert result["status_breakdown"] == {"PENDING": 4}

    def test_pipeline_name_preserved(self, sample_graph):
        result = summarize(sample_graph)
        assert result["pipeline"] == "test_pipeline"


class TestLoadGraph:
    def test_round_trips_correctly(self, sample_graph):
        data = sample_graph.model_dump()
        restored = load_graph(data)
        assert restored.name == sample_graph.name
        assert len(restored.nodes) == len(sample_graph.nodes)
        assert len(restored.edges) == len(sample_graph.edges)

    def test_invalid_data_raises(self):
        with pytest.raises(ValidationError):
            load_graph({"version": "1.0"})
```

### `tests/test_query.py`
```python
import pytest
from idiograph.core.query import (
    get_downstream, get_upstream, topological_sort,
    find_cycles, validate_integrity, summarize_intent,
)
from idiograph.core.models import Edge, Graph, Node


class TestDownstream:
    def test_all_descendants_reachable(self, sample_graph):
        result = get_downstream(sample_graph, "n1")
        assert set(result) == {"n2", "n3", "n4"}

    def test_sink_node_has_no_downstream(self, sample_graph):
        assert get_downstream(sample_graph, "n4") == []

    def test_missing_node_returns_empty(self, sample_graph):
        assert get_downstream(sample_graph, "ghost") == []


class TestUpstream:
    def test_all_ancestors_found(self, sample_graph):
        result = get_upstream(sample_graph, "n4")
        assert set(result) == {"n1", "n2", "n3"}

    def test_source_node_has_no_upstream(self, sample_graph):
        assert get_upstream(sample_graph, "n1") == []


class TestTopologicalSort:
    def test_order_is_valid(self, sample_graph):
        order = topological_sort(sample_graph)
        for edge in sample_graph.edges:
            assert order.index(edge.source) < order.index(edge.target)

    def test_all_nodes_included(self, sample_graph):
        order = topological_sort(sample_graph)
        assert set(order) == {n.id for n in sample_graph.nodes}

    def test_raises_on_cycle(self, cyclic_graph):
        with pytest.raises(ValueError, match="cycle"):
            topological_sort(cyclic_graph)


class TestFindCycles:
    def test_acyclic_graph_returns_empty(self, sample_graph):
        assert find_cycles(sample_graph) == []

    def test_cyclic_graph_detected(self, cyclic_graph):
        cycles = find_cycles(cyclic_graph)
        assert len(cycles) > 0


class TestValidateIntegrity:
    def test_valid_graph_passes(self, sample_graph):
        result = validate_integrity(sample_graph)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_dangling_target_caught(self, sample_graph):
        sample_graph.edges.append(
            Edge(source="n4", target="ghost_node", type="DATA")
        )
        result = validate_integrity(sample_graph)
        assert result["valid"] is False
        assert any("ghost_node" in e for e in result["errors"])

    def test_dangling_source_caught(self):
        g = Graph(
            name="bad", version="1.0",
            nodes=[Node(id="real", type="Render")],
            edges=[Edge(source="phantom", target="real", type="DATA")],
        )
        result = validate_integrity(g)
        assert result["valid"] is False


class TestSummarizeIntent:
    def test_domain_is_vfx(self, sample_graph):
        result = summarize_intent(sample_graph)
        assert result["domain"] == "vfx"

    def test_control_gates_identified(self, sample_graph):
        result = summarize_intent(sample_graph)
        assert "n3" in result["control_gates"]

    def test_subgraph_scope(self, sample_graph):
        result = summarize_intent(sample_graph, node_ids=["n1", "n2"])
        assert result["scope"] == "subgraph"
        assert result["node_count"] == 2

    def test_ai_domain_detected(self):
        g = Graph(
            name="ai_test", version="1.0",
            nodes=[
                Node(id="a", type="LLMCall",   params={}),
                Node(id="b", type="Evaluator", params={}),
            ],
            edges=[Edge(source="a", target="b", type="DATA")],
        )
        result = summarize_intent(g)
        assert result["domain"] == "ai"
```

## Verified Working
```
uv run pytest tests/ -v
→ All tests pass

uv run idiograph validate test_graph.json
10:33:00  INFO      idiograph.graph  Loaded graph 'lookdev_approval_pipeline' — 5 nodes, 4 edges.
Valid — 5 nodes, 4 edges.

uv run idiograph stats       → pipeline statistics as JSON (no logging output — correct)
uv run idiograph check       → integrity valid, no cycles
uv run idiograph query intent → domain: vfx, critical path and control gates present
```

## Thesis Connection
Tests are not incidental here — they are the enforcement mechanism for determinism. The thesis claims Idiograph behaves predictably under all inputs, including adversarial ones. Without a test suite, that is an assertion. With one, it is a verifiable property. The `test_params_are_independent` test in particular captures something subtle: a mutable default would silently corrupt node state, which is exactly the class of bug that undermines the reliability guarantees the thesis depends on.

## Next — Phase 6: Async & Orchestration
The graph becomes executable. An async execution engine will run nodes in topological order, respecting DATA and CONTROL edges, handling partial failures, and updating node status as execution proceeds. This is where the declarative graph description becomes a live system — and where the distinction between "the graph describes execution" and "the graph performs execution" gets tested in code for the first time.
