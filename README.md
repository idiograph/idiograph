# Idiograph
[![tests](https://github.com/idiograph/idiograph/actions/workflows/tests.yml/badge.svg)](https://github.com/idiograph/idiograph/actions/workflows/tests.yml) [![codecov](https://codecov.io/github/idiograph/idiograph/graph/badge.svg?token=1U3JZJ7V4E)](https://codecov.io/github/idiograph/idiograph)

**A semantic graph system for VFX and AI workflows — and a proof of concept for a thesis about how production AI tooling should actually work.**

---

## The Argument

Current AI tools fail in VFX production environments because they lack semantic context, deterministic behavior, and explicit state management. Probabilistic models are fundamentally in tension with the requirements of production pipelines, which demand reproducibility, auditability, and reliable communication of intent across technical and artistic teams.

The fix is not better prompts or bigger models. It is explicit structure.

VFX pipelines solved this problem decades ago: represent the work as a directed graph of typed, parameterized nodes with defined data and control flows. The graph is the source of truth. Every operator — human, tool, or agent — reads from and writes to that structure. The system is inspectable, serializable, and deterministic by design.

Idiograph applies that same architecture to AI agent workflows, and asks: what does AI tooling look like when it is built the way a production pipeline engineer would build it?

---

## What This Is

Idiograph is a Python-based semantic graph system. It represents VFX pipeline stages and AI agent operations as nodes in a unified, typed, JSON-serializable graph. The graph is the single source of truth. CLI, agents, and (optionally) UI are all operators on that graph — none of them own state.

The current implementation includes an arXiv research pipeline — nodes that fetch a paper, extract claims via LLM, evaluate against keyword criteria, and conditionally summarize. The architecture is domain-agnostic by design: VFX pipeline nodes (LoadAsset, ApplyShader, ShaderValidate) and additional AI agent node types are planned for Phase 10. Both would be represented identically to the existing nodes. There is no special-casing in the executor.

An MCP server wraps the core graph operations as six standards-compliant tools (`get_node`, `get_edges_from`, `update_node`, `summarize_intent`, `validate_graph`, `execute_graph`). Any MCP-compatible agent client can connect, inspect the graph, mutate node parameters, and trigger execution without bespoke adapter code.

---

## Current Status

| Phase | Description | Status |
|---|---|---|
| 0 | Environment & tooling | ✅ Complete |
| 1 | Structured JSON output | ✅ Complete |
| 2 | Package structure & reusability | ✅ Complete |
| 3 | Pydantic data models & validation | ✅ Complete |
| 4.5 | Graph query & analysis | ✅ Complete |
| 5 | Testing, logging, config | ✅ Complete |
| 6 | Async execution engine | ✅ Complete |
| 7 | Architecture refinement | ✅ Complete |
| 8 | Agent integration (MCP) | ✅ Complete |
| 9 | Documentation & visibility | 🔄 In progress |
| 10 | Projection-aware rendering domain | Planned |

---

## Architecture
```
src/idiograph/
├── core/
│   ├── models.py          # Node, Edge, Graph — Pydantic models with agent-readable field descriptions
│   ├── graph.py           # Core graph operations
│   ├── query.py           # Traversal, cycle detection, integrity validation, intent summary
│   ├── executor.py        # Async execution engine — topological order, failure containment
│   ├── config.py          # TOML config loader
│   └── logging_config.py
├── domains/
│   └── arxiv/             # Domain implementation — one of many possible domains
│       ├── pipeline.py
│       ├── handlers.py    # Live handlers (require API key)
│       └── mock_handlers.py  # Stub handlers for keyless execution
├── mcp_server.py          # MCP interface — six tools via stdio transport
└── main.py                # CLI entry point (Typer)
```

### Core design decisions

**The graph never executes code directly.** It describes execution. The execution engine interprets it. This separation is what makes the graph inspectable, serializable, and agent-safe.

**Edge types are open strings, not a closed enum.** `DATA` and `CONTROL` are the standard types. Phase 10 introduces causal semantics (`MODULATES`, `DRIVES`, `OCCLUDES`, `EMITS`, `PROJECTS_TO`). The schema was designed for this from Phase 3.

**Node domain is metadata, never a structural constraint.** VFX and AI nodes are typed with labels — they are not structurally different kinds of objects. The same executor handles both.

**Handlers are registered, not imported.** The execution engine looks up handlers by node type string. It never imports handler modules directly. New node types require no changes to the executor.

**Domain implementations are isolated from core.** `core/` is domain-agnostic. The arXiv pipeline lives under `domains/arxiv/`. A VFX rendering domain in Phase 10 gets its own subdirectory. Nothing in `core/` changes when a new domain is added.

**`summarize_intent()` is purely algorithmic.** The query layer can describe what a subgraph does and where it might fail without an LLM call. Deterministic output for deterministic input.

---

## Pipelines

<!-- GENERATED:arxiv-pipeline -->
```mermaid
flowchart LR
    fetch["fetch<br/>FetchAbstract"]
    claims["claims<br/>LLMCall"]
    evaluate["evaluate<br/>Evaluator"]
    summarize["summarize<br/>LLMSummarize"]
    fetch -->|DATA| claims
    claims -->|DATA| evaluate
    evaluate -->|CONTROL| summarize
```
<!-- END GENERATED -->

---

## What You Can Do With It Right Now

```bash
# Install
git clone https://github.com/idiograph/idiograph.git
cd idiograph
uv sync
uv pip install -e .

# Run the arXiv pipeline (requires ANTHROPIC_API_KEY in .env)
uv run idiograph run 1706.03762

# Run without an API key — mock handlers execute the full pipeline
uv run idiograph run 1706.03762 --mock

# Start the MCP server (stdio transport — connect any MCP-compatible client)
uv run idiograph serve

# Explore and inspect the graph
uv run idiograph stats                         # Pipeline statistics as JSON
uv run idiograph workflows                     # Full graph manifest
uv run idiograph validate path/to/graph.json   # Validate any graph file
uv run idiograph check                         # Integrity + cycle detection
uv run idiograph query downstream node_03      # Downstream traversal
uv run idiograph query upstream node_05        # Upstream traversal
uv run idiograph query topo                    # Topological execution order
uv run idiograph query intent                  # Semantic intent summary

# Test
uv run pytest tests/ -v
```

---

## The Thesis Connection

The goal of this system is to make a concrete argument about AI tooling in production environments.

A graph that can be serialized to JSON and reconstructed without loss is a graph an agent can safely read and write. A query layer that can answer "what does this subgraph do and where might it fail?" without an LLM call is a system that does not depend on probabilistic inference to reason about its own state. An execution engine that records failure in graph state rather than raising an exception is a system that remains inspectable after something goes wrong.

These are architectural properties, not features. They are what production pipeline engineers have required for decades, and what current AI tooling largely does not provide.

Idiograph is a proof of concept for what it looks like when you build AI-operable systems the way a pipeline engineer would build them.

---

## Stack

- Python 3.13
- [Pydantic](https://docs.pydantic.dev/) — typed models and validation
- [Typer](https://typer.tiangolo.com/) — CLI interface
- [NetworkX](https://networkx.org/) — graph traversal and analysis
- [uv](https://github.com/astral-sh/uv) — dependency and environment management
- [pytest](https://pytest.org/) — test suite

---

## Documentation

Phase summaries and architectural decision logs are in [`docs/`](docs/).

- [Blueprint](docs/vision/vision-blueprint-original.md) — full curriculum and system design
- [Amendments](docs/decisions/amendments.md) — architectural decisions and constraint log
- [Session Workflow](docs/workflow.md) — how development sessions are structured
- Phase summaries: [Phase 0](docs/phases/phase-00-foundation.md) · [Phase 1](docs/phases/phase-01-rapid-fluency-semantic-output.md) · [Phase 2](docs/phases/phase-02-project-structure-reusability.md) · [Phase 3](docs/phases/phase-03-data-models-typing.md) · [Phase 4.5](docs/phases/phase-04-05-graph-query-analysis.md) · [Phase 5](docs/phases/phase-05-testing-logging-config.md) · [Phase 6](docs/phases/phase-06-async-execution-orchestration.md) · [Phase 7](docs/phases/phase-07-architecture-refinement.md) · [Phase 8](docs/phases/phase-08-mcp-integration.md)