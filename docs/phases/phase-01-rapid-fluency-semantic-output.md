# Phase 1 Summary — Rapid Fluency & Semantic Output

## What Was Built
A pipeline data module and two CLI commands that produce structured JSON output. The system now speaks in a format that both humans and agents can consume.

## Environment
- No new dependencies added
- `uv pip install -e .` required after adding new source files to a live editable install — expected behavior, worth remembering

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
            main.py       ← updated
            pipeline.py   ← new
```

## Key Decisions

- **Data lives in `pipeline.py`, interface lives in `main.py`** — the CLI imports from the data module, never owns the data itself. This separation will hold as the system grows.
- **JSON output via `typer.echo(json.dumps(...))`** — not `print()`. Correct output channel for CLI tools; handles stream routing and piping cleanly.
- **Stats are computed from the graph, not stored** — derived views are always calculated from the source of truth, never cached as parallel state.
- **Look dev domain used for sample data** — `ShaderValidate`, `RenderComparison`, `LookApproval` are real first-class node types, not placeholders. The pipeline reflects actual production logic.
- **DATA vs CONTROL edges already present** — the `ShaderValidate → RenderComparison` edge is typed `CONTROL` because a validation failure should gate execution, not just pass data. The distinction is meaningful now, not just future scaffolding.
- **`dict[str, int]` type hints** — using Python 3.10+ built-in generic syntax throughout. No imports from `typing` needed.

## Files

### `src/idiograph/pipeline.py`
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

### `src/idiograph/main.py`
```python
import json
import typer
from idiograph.pipeline import SAMPLE_PIPELINE

app = typer.Typer()


@app.command()
def stats():
    """Output pipeline statistics as JSON."""
    nodes = SAMPLE_PIPELINE["nodes"]
    edges = SAMPLE_PIPELINE["edges"]

    status_counts: dict[str, int] = {}
    for node in nodes:
        s = node["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    node_types: dict[str, int] = {}
    for node in nodes:
        t = node["type"]
        node_types[t] = node_types.get(t, 0) + 1

    output = {
        "pipeline": SAMPLE_PIPELINE["name"],
        "version": SAMPLE_PIPELINE["version"],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "status_breakdown": status_counts,
        "node_types": node_types,
    }

    typer.echo(json.dumps(output, indent=2))


@app.command()
def workflows():
    """Output the full pipeline manifest as JSON."""
    typer.echo(json.dumps(SAMPLE_PIPELINE, indent=2))


if __name__ == "__main__":
    app()
```

## Verified Working
```
uv run idiograph stats       → pipeline statistics as JSON
uv run idiograph workflows   → full manifest as JSON
uv run idiograph --help      → both subcommands listed
```

## Next: Phase 2
Convert the package into a properly structured module with `idiograph.core`. Move pipeline data and logic into submodules. The goal is a system where functions are importable cleanly — by humans, by tests, and eventually by agents.
