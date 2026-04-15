# Phase 2 Summary — Project Structure & Reusability

## What Was Built
The package was refactored into a proper module hierarchy. Logic was extracted from the CLI into a `core` subpackage with clean, importable functions. External behavior is unchanged — the refactor is verified by identical output before and after.

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
            main.py          ← CLI only, all logic removed
            pipeline.py      ← can be deleted (superseded by core/)
            core/
                __init__.py  ← exposes core symbols
                pipeline.py  ← sample data (moved here)
                graph.py     ← graph operations (new)
```

## Key Decisions

- **CLI is now dumb** — `main.py` only imports and formats. No loops, no logic. This is permanent: the CLI is a view on core, never the owner of behavior.
- **`core/` is the importable surface** — anything that needs to operate on a graph (tests, agents, other modules) imports from `idiograph.core`. The entry point is irrelevant to that.
- **`graph.py` functions take a `graph: dict` argument** — they are stateless and pure. They do not own or store the graph. This is intentional: the same functions will work on any valid graph dict, whether it came from a file, a fixture, or an agent mutation. Prepares cleanly for Phase 3 where the dict becomes a typed model.
- **`core/__init__.py` controls the public API** — only what is explicitly imported there is considered stable. Internal structure of `core/` can change without breaking callers.
- **`get_node` returns `dict | None`** — not an exception on miss. Agents querying for a node that doesn't exist should get a clean null, not a crash.

## Files

### `src/idiograph/core/__init__.py`
```python
from idiograph.core.pipeline import SAMPLE_PIPELINE
from idiograph.core.graph import summarize, get_node, get_edges_from
```

### `src/idiograph/core/pipeline.py`
```python
SAMPLE_PIPELINE: dict = {
    "name": "lookdev_approval_pipeline",
    "version": "1.0",
    "nodes": [
        {
            "id": "node_01",
            "type": "LoadAsset",
            "params": {"asset_path": "/assets/hero_character.usd"},
            "status": "PENDING",
        },
        {
            "id": "node_02",
            "type": "ApplyShader",
            "params": {"shader": "principled_bsdf", "material": "hero_skin"},
            "status": "PENDING",
        },
        {
            "id": "node_03",
            "type": "ShaderValidate",
            "params": {"rules": ["energy_conservation", "normal_range"]},
            "status": "PENDING",
        },
        {
            "id": "node_04",
            "type": "RenderComparison",
            "params": {"reference": "/refs/hero_approved.exr", "renderer": "arnold"},
            "status": "PENDING",
        },
        {
            "id": "node_05",
            "type": "LookApproval",
            "params": {"approver": "lead_lookdev", "threshold": 0.95},
            "status": "PENDING",
        },
    ],
    "edges": [
        {"from": "node_01", "to": "node_02", "type": "DATA"},
        {"from": "node_02", "to": "node_03", "type": "DATA"},
        {"from": "node_03", "to": "node_04", "type": "CONTROL"},
        {"from": "node_04", "to": "node_05", "type": "DATA"},
    ],
}
```

### `src/idiograph/core/graph.py`
```python
def get_node(graph: dict, node_id: str) -> dict | None:
    """Return a node by id, or None if not found."""
    for node in graph["nodes"]:
        if node["id"] == node_id:
            return node
    return None


def get_edges_from(graph: dict, node_id: str) -> list[dict]:
    """Return all edges where the source is node_id."""
    return [e for e in graph["edges"] if e["from"] == node_id]


def summarize(graph: dict) -> dict:
    """Return a statistics summary of the graph."""
    nodes = graph["nodes"]
    edges = graph["edges"]

    status_counts: dict[str, int] = {}
    for node in nodes:
        s = node["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    node_types: dict[str, int] = {}
    for node in nodes:
        t = node["type"]
        node_types[t] = node_types.get(t, 0) + 1

    return {
        "pipeline": graph["name"],
        "version": graph["version"],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "status_breakdown": status_counts,
        "node_types": node_types,
    }
```

### `src/idiograph/main.py`
```python
import json
import typer
from idiograph.core import SAMPLE_PIPELINE, summarize

app = typer.Typer()


@app.command()
def stats():
    """Output pipeline statistics as JSON."""
    typer.echo(json.dumps(summarize(SAMPLE_PIPELINE), indent=2))


@app.command()
def workflows():
    """Output the full pipeline manifest as JSON."""
    typer.echo(json.dumps(SAMPLE_PIPELINE, indent=2))


if __name__ == "__main__":
    app()
```

## Verified Working
```
uv run idiograph stats      → identical JSON output as Phase 1
uv run idiograph workflows  → identical JSON output as Phase 1
uv run python -c "from idiograph.core import get_node, SAMPLE_PIPELINE; print(get_node(SAMPLE_PIPELINE, 'node_03'))"
→ {'id': 'node_03', 'type': 'ShaderValidate', 'params': {'rules': ['energy_conservation', 'normal_range']}, 'status': 'PENDING'}
```

## Next: Phase 3
Replace the `dict`-based graph with validated Pydantic models: `Node`, `Edge`, `Graph`. This is where the system gets an enforceable schema — the foundation for safe agent read/write, CLI validation commands, and typed tool interfaces.
