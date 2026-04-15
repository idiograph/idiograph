# Phase 4.5 Summary — Graph Query & Analysis

## What Was Built
The graph became interrogable. A dedicated query module adds traversal, dependency analysis, cycle detection, referential integrity validation, and a semantic intent summary. The CLI gained a `query` subcommand group and a standalone `check` command. Two open amendments (AMD-002, AMD-005) are now closed.

## Project Structure
```
E:\projects\nodeforge
│   pyproject.toml
│   .python-version
│   README.md
│
└───src
    └───idiograph
            __init__.py
            main.py          ← updated: query subcommands + check command
            core/
                __init__.py
                pipeline.py
                models.py
                graph.py
                query.py     ← new
```

## Key Decisions

**`networkx` as the graph math layer** — not hand-rolled BFS. `nx.descendants`, `nx.ancestors`, `nx.topological_sort`, and `nx.simple_cycles` are battle-tested. Writing equivalents from scratch would be noise at this stage and a maintenance liability later. More importantly, networkx is a production-standard library that any downstream agent or tool will recognize.

**`_build_nx_graph()` as an internal helper** — every query function converts the Idiograph `Graph` into a `nx.DiGraph` internally. This is deliberately not exposed in the public API. The networkx graph is a transient analysis artifact, not a second source of truth. The Idiograph `Graph` model remains the canonical representation.

**`downstream`/`upstream` return unordered lists** — `nx.descendants` and `nx.ancestors` return sets. The order of results from these functions is not meaningful. `topological_sort` is the only traversal where order matters, and it is deterministic. If ordered display becomes necessary later (e.g. sorted by topo position), that is a Phase 7 concern.

**`summarize_intent()` is purely algorithmic** — no LLM calls inside the query layer. The open question from AMD-002 is resolved: algorithmic for now, LLM-assisted as an optional Phase 9 layer on top. Using an LLM inside the foundation would undercut the determinism thesis. The function must produce identical output for identical input, always.

**`critical_path` uses longest-by-node-count path** — `nx.shortest_path` between each source/sink pair; the longest result wins. This is a heuristic, not a weighted critical path. It is good enough for intent summarization and agent reasoning. A weighted version (by estimated execution time) is a Phase 6/7 candidate if needed.

**`control_gates` derived from edge type** — any node that is the *source* of a CONTROL edge is a chokepoint: its failure halts downstream execution. This is read directly from edge metadata, requiring no additional annotation. It reinforces why the DATA/CONTROL distinction introduced in Phase 1 was meaningful from the start.

**`validate_integrity()` lives in `query.py`, not `models.py`** — Pydantic validates models in isolation. Cross-object constraints (does this edge's target actually exist?) require graph-level visibility. That belongs in the analysis layer, not the schema layer. This was the design rationale behind AMD-005.

**`query` is a Typer sub-app** — `app.add_typer(query_app, name="query")` gives a clean `idiograph query <subcommand>` namespace without polluting the top-level command list. Scales naturally as more query types are added.

## Amendments Closed

**AMD-002** — `summarize_intent(graph, node_ids=None)` implemented in `query.py`. Exposed via `idiograph query intent`. Returns structured JSON describing domain, critical path, control gates, and failure state. Purely algorithmic.

**AMD-005** — `validate_integrity(graph)` implemented in `query.py`. Catches dangling edge references (source or target ID not present in node list). Returns structured result with specific errors identified. Exposed via `idiograph check`.

## Files

### `src/idiograph/core/query.py` *(new)*
```python
import networkx as nx
from idiograph.core.models import Graph, Node


# ── Internal helper ──────────────────────────────────────────────────────────

def _build_nx_graph(graph: Graph) -> nx.DiGraph:
    """Convert a Idiograph Graph into a networkx DiGraph for analysis."""
    dg = nx.DiGraph()
    for node in graph.nodes:
        dg.add_node(node.id)
    for edge in graph.edges:
        dg.add_edge(edge.source, edge.target, type=edge.type)
    return dg


# ── Traversal ────────────────────────────────────────────────────────────────

def get_downstream(graph: Graph, node_id: str) -> list[str]:
    """Return all node IDs reachable downstream from node_id (excludes node_id itself)."""
    dg = _build_nx_graph(graph)
    if node_id not in dg:
        return []
    return list(nx.descendants(dg, node_id))


def get_upstream(graph: Graph, node_id: str) -> list[str]:
    """Return all node IDs that are ancestors of node_id (excludes node_id itself)."""
    dg = _build_nx_graph(graph)
    if node_id not in dg:
        return []
    return list(nx.ancestors(dg, node_id))


def topological_sort(graph: Graph) -> list[str]:
    """
    Return node IDs in topological order (safe execution order).
    Raises ValueError if the graph contains a cycle.
    """
    dg = _build_nx_graph(graph)
    try:
        return list(nx.topological_sort(dg))
    except nx.NetworkXUnfeasible:
        raise ValueError("Graph contains a cycle — topological sort is not possible.")


def find_cycles(graph: Graph) -> list[list[str]]:
    """
    Return a list of cycles found in the graph.
    Each cycle is a list of node IDs. Returns an empty list if the graph is acyclic.
    """
    dg = _build_nx_graph(graph)
    return list(nx.simple_cycles(dg))


# ── Integrity ────────────────────────────────────────────────────────────────

def validate_integrity(graph: Graph) -> dict:
    """
    Check that every edge references node IDs that actually exist in the graph.
    Returns a dict with 'valid' (bool) and 'errors' (list of problem descriptions).
    """
    node_ids = {node.id for node in graph.nodes}
    errors = []

    for edge in graph.edges:
        if edge.source not in node_ids:
            errors.append(f"Edge {edge.source} → {edge.target}: source '{edge.source}' does not exist.")
        if edge.target not in node_ids:
            errors.append(f"Edge {edge.source} → {edge.target}: target '{edge.target}' does not exist.")

    return {"valid": len(errors) == 0, "errors": errors}


# ── Intent Summary ───────────────────────────────────────────────────────────

def summarize_intent(graph: Graph, node_ids: list[str] | None = None) -> dict:
    """
    Return a structured semantic description of the graph or a subgraph.
    Intended for agent consumption — answers 'what does this do and where might it fail?'
    Purely algorithmic: no LLM calls. Deterministic output for a given graph state.
    """
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

### `src/idiograph/main.py` *(updated)*
```python
import json
import typer
from idiograph.core import SAMPLE_PIPELINE, summarize, load_graph
from idiograph.core.query import (
    get_downstream,
    get_upstream,
    topological_sort,
    find_cycles,
    validate_integrity,
    summarize_intent,
)
from pydantic import ValidationError

app = typer.Typer()
query_app = typer.Typer()
app.add_typer(query_app, name="query")


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

## Verified Working
```
uv run idiograph check
→ integrity valid, no cycles

uv run idiograph query downstream node_01
→ ['node_05', 'node_04', 'node_02', 'node_03'] (unordered — correct)

uv run idiograph query upstream node_05
→ ['node_03', 'node_02', 'node_01', 'node_04'] (unordered — correct)

uv run idiograph query topo
→ ['node_01', 'node_02', 'node_03', 'node_04', 'node_05'] (deterministic)

uv run idiograph query intent
→ domain: vfx, critical_path: [node_01..node_05], control_gates: [node_03]

uv run idiograph --help
→ stats, workflows, validate, check, query all listed
```

## Thesis Connection
A graph that can't be questioned is just a data format. This phase is what separates Idiograph from a structured JSON file. `summarize_intent` in particular demonstrates the core claim: explicit semantic structure enables reasoning that no amount of probabilistic inference over raw node lists could replicate. An agent can call this function and receive a meaningful answer to "what does this subgraph do and where might it fail?" — without inspecting a single node individually.

## Next — Phase 5: Testing, Logging, Config
The system now has enough structure to be worth protecting. Phase 5 adds `pytest` coverage for the core and query layers, structured logging, and config loading. These are not glamorous — but they are what makes the system trustworthy enough to hand to an agent in Phase 8. An untested system is not a safe tool interface.
