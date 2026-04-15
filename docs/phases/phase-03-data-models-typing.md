# Phase 3 Summary — Data Models & Typing

## What Was Built
The dict-based graph was replaced with validated Pydantic models. The system now has an enforceable schema — invalid graphs are rejected with structured error output. The CLI gained a `validate` command that round-trips any JSON file through the schema.

## Key Decisions

**`dict[str, Any]` for `params`** — deliberately loose for now. The Blueprint anticipates discriminated unions per node type in Phase 7+. Solving that in Phase 3 would have made the phase significantly larger without advancing the thesis. The field description documents the intent clearly enough for agents to reason about it.

**`inputs`/`outputs` omitted from `Node`** — these fields have no consumer until the execution engine in Phase 6. Optional fields with no current role are noise in the schema, not structure. They get added when they have a concrete job.

**`source`/`target` instead of `from`/`to` on `Edge`** — `from` is a Python reserved word. Aliasing around it is unnecessary friction. `source` and `target` are standard graph terminology and cleaner in every context.

**`get_node()` lives on `Graph` as a method** — pure lookup with no side effects belongs on the model. The standalone function in `graph.py` delegates to it, preserving the existing public API.

**`model_dump_json()` instead of `json.dumps()`** — Pydantic's own serializer handles the full model structure correctly. `json.dumps` doesn't know how to traverse a model object.

**`load_graph()` as the single entry point for reconstruction** — anything deserializing a graph from JSON goes through `Graph.model_validate()` via this function. One place to add migration logic, schema versioning, or pre-validation later.

## Files

### `src/idiograph/core/models.py` *(new)*
```python
from typing import Any
from pydantic import BaseModel, Field


class Node(BaseModel):
    id: str = Field(description="Unique identifier for this node within the graph.")
    type: str = Field(description="Node type determining its role. Examples: LoadAsset, Render, LLMCall, ShaderValidate.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters for this node. Keys and value types vary by node type."
    )
    status: str = Field(
        default="PENDING",
        description="Execution status. One of: PENDING, RUNNING, SUCCESS, FAILED."
    )


class Edge(BaseModel):
    source: str = Field(description="ID of the source node.")
    target: str = Field(description="ID of the target node.")
    type: str = Field(
        default="DATA",
        description="Edge type defining the relationship. Known types: DATA (passes values), CONTROL (gates execution). Extensible — additional semantic types such as MODULATES or DRIVES are valid."
    )


class Graph(BaseModel):
    name: str = Field(description="Human-readable name for this graph.")
    version: str = Field(description="Version string for this graph definition.")
    nodes: list[Node] = Field(default_factory=list, description="All nodes in the graph.")
    edges: list[Edge] = Field(default_factory=list, description="All edges in the graph.")

    def get_node(self, node_id: str) -> Node | None:
        """Return a node by id, or None if not found."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None
```

### `src/idiograph/core/pipeline.py` *(updated)*
```python
from idiograph.core.models import Node, Edge, Graph

SAMPLE_PIPELINE: Graph = Graph(
    name="lookdev_approval_pipeline",
    version="1.0",
    nodes=[
        Node(id="node_01", type="LoadAsset", params={"asset_path": "/assets/hero_character.usd"}),
        Node(id="node_02", type="ApplyShader", params={"shader": "principled_bsdf", "material": "hero_skin"}),
        Node(id="node_03", type="ShaderValidate", params={"rules": ["energy_conservation", "normal_range"]}),
        Node(id="node_04", type="RenderComparison", params={"reference": "/refs/hero_approved.exr", "renderer": "arnold"}),
        Node(id="node_05", type="LookApproval", params={"approver": "lead_lookdev", "threshold": 0.95}),
    ],
    edges=[
        Edge(source="node_01", target="node_02", type="DATA"),
        Edge(source="node_02", target="node_03", type="DATA"),
        Edge(source="node_03", target="node_04", type="CONTROL"),
        Edge(source="node_04", target="node_05", type="DATA"),
    ],
)
```

### `src/idiograph/core/graph.py` *(updated)*
```python
from idiograph.core.models import Graph, Node, Edge


def get_node(graph: Graph, node_id: str) -> Node | None:
    """Return a node by id, or None if not found."""
    return graph.get_node(node_id)


def get_edges_from(graph: Graph, node_id: str) -> list[Edge]:
    """Return all edges where the source is node_id."""
    return [e for e in graph.edges if e.source == node_id]


def summarize(graph: Graph) -> dict:
    """Return a statistics summary of the graph."""
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
    return Graph.model_validate(data)
```

### `src/idiograph/core/__init__.py` *(updated)*
```python
from idiograph.core.pipeline import SAMPLE_PIPELINE
from idiograph.core.graph import summarize, get_node, get_edges_from, load_graph
```

### `src/idiograph/main.py` *(updated)*
```python
import json
import typer
from idiograph.core import SAMPLE_PIPELINE, summarize, load_graph
from pydantic import ValidationError

app = typer.Typer()


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
        with open(path) as f:
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


if __name__ == "__main__":
    app()
```

## Verified Working
```
uv run idiograph stats                      → pipeline statistics as JSON, identical to Phase 2
uv run idiograph workflows                  → full graph as JSON with typed model fields
uv run idiograph validate test_graph.json   → Valid — 5 nodes, 4 edges.
uv run idiograph validate bad_graph.json    → Validation failed: version field required
```

## Amendments Closed
AMD-001 (Pydantic field descriptions) and AMD-003 (extensible edge type) are both satisfied. All model fields carry `Field(description=...)`. Edge `type` is an open `str`.

## Next — Phase 4.5
Phase 4 (UI) is explicitly deprioritized. Phase 4.5 — Graph Query & Analysis — comes next. This is where the graph becomes interrogable: traversal, dependency queries, cycle detection, and the foundation for the `summarize_intent()` tool from AMD-002. That's the layer that lets an agent reason over the graph before touching it.
