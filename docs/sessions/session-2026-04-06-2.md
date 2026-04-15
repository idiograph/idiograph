# Idiograph – Architectural Debt Strategy: State Management Migration

## Document Purpose

This document formalizes a known architectural constraint introduced by the current
module-level graph state model, the conditions under which it becomes a liability,
and the sequenced migration strategy to resolve it if those conditions are met.

This is not a scheduled phase. It is a named liability with a defined remediation path,
triggered by a specific forcing function.

---

## The Constraint

### What Was Decided

Phases 0–6 use a module-level constant (`SAMPLE_PIPELINE`, `ARXIV_PIPELINE`) as the
graph source of truth. The CLI loads one graph per process invocation. The executor
receives a graph object as a parameter.

This was the correct decision for the current phase sequence. It eliminated premature
complexity around persistence, session management, and concurrent access before those
problems existed.

### Why It Works Now

The CLI is a single-process, single-command model. One invocation, one graph, one
result. There is no concurrency. There is no shared mutable state across requests.
The constraint is invisible because the execution model never stresses it.

### Why It Becomes a Liability

A stateless HTTP server (FastAPI) or a persistent MCP server handling concurrent
agent requests cannot share a module-level mutable graph object. The problems that
emerge are:

- **Concurrent mutation**: two agent requests modifying the same graph object
  simultaneously produce undefined behavior
- **No graph isolation**: every request operates on the same graph, with no way to
  load a different one by ID
- **No persistence**: graph state changes (node status updates after execution) are
  lost when the process exits
- **No session model**: an agent cannot maintain a working graph across multiple
  tool calls without re-sending the full graph each time

These are not hypothetical. They are the exact problems that appear the moment
Idiograph receives more than one concurrent request.

### Honest Scope of Remediation

"Adding FastAPI is an afternoon" is wrong. Adding FastAPI routes is an afternoon.
Making the system safe for FastAPI to sit in front of requires:

- Graph loading extracted from module-level state into a callable
- A graph registry mapping IDs to loader functions
- JSON-file persistence keyed by graph ID (minimum viable persistence layer)
- Request-scoped graph loading so each request operates on its own graph instance
- Verification that the executor's mutation model (`_update_node_status`) is
  safe under the new loading pattern

That is four to five focused micro-sessions of real work, not incidental cleanup.

---

## The Forcing Function

This migration is **not required** unless one of the following conditions is met:

1. **Phase 8 requires remote agent access** — if the MCP server must be reachable
   over a network (HTTP/SSE transport rather than stdio), concurrent request handling
   becomes real and the migration is necessary before Phase 8 begins.

2. **A web demo is required** — if the thesis demo requires a browser-accessible
   interface rather than a CLI, FastAPI becomes necessary and the migration is
   required first.

3. **Multiple graphs must be managed simultaneously** — if the system needs to hold,
   compare, or execute more than one graph in a single session, the module-level
   constant model breaks immediately.

If none of these conditions are met by the end of Phase 7, the migration is deferred
indefinitely and the constraint remains a documented liability rather than a scheduled
cost.

---

## Migration Strategy: Strangler Fig

The migration is executed as a strangler fig — the new architecture is introduced
alongside the old one, the system remains runnable at every intermediate state, and
the old path is removed only when the new one is proven.

No step expands its own scope. Anything discovered during a step that is out of scope
goes into the amendments log and gets its own step.

### Step 1 — Extract graph loading from module-level state

**What changes:** `SAMPLE_PIPELINE` and `ARXIV_PIPELINE` stop being imported directly
by command functions. Each is wrapped in a loader function:

```python
def load_default_pipeline() -> Graph:
    return SAMPLE_PIPELINE.model_copy(deep=True)

def load_arxiv_pipeline() -> Graph:
    return ARXIV_PIPELINE.model_copy(deep=True)
```

Command functions call the loader. External behavior is identical.

**Why `model_copy(deep=True)`:** the `run` command already does this before execution.
Making it universal means the module-level constant is never mutated by any caller —
it is always a clean template.

**Done when:** all command functions load graphs via a function call, not a direct
import. All existing tests pass without modification.

**Estimated scope:** one micro-session.

---

### Step 2 — Introduce a graph registry

**What changes:** a registry dict maps graph ID strings to loader functions.
Command functions look up graphs by ID rather than calling loaders directly.

```python
GRAPH_REGISTRY: dict[str, Callable[[], Graph]] = {}

def register_graph(graph_id: str, loader: Callable[[], Graph]) -> None:
    GRAPH_REGISTRY[graph_id] = loader

def load_graph_by_id(graph_id: str) -> Graph:
    loader = GRAPH_REGISTRY.get(graph_id)
    if loader is None:
        raise KeyError(f"No graph registered with ID '{graph_id}'")
    return loader()
```

Registration happens at startup alongside handler registration. The CLI `run` command
accepts an optional `--graph` argument defaulting to `"arxiv"`.

**Done when:** the registry exists, graphs are registered at startup, and the `run`
command loads the correct graph by ID. All existing tests pass.

**Estimated scope:** one micro-session.

---

### Step 3 — Verify executor safety under the new loading pattern

**What changes:** no code changes. This is a verification step.

The executor currently mutates node status in place via `_update_node_status`. Under
the new loading pattern, each `load_graph_by_id` call returns a fresh deep copy.
This means status mutations are isolated to the graph instance returned by the loader —
exactly the behavior needed for request isolation.

Write a test that:
1. Loads the same graph twice via the registry
2. Executes one instance
3. Confirms the other instance still has all nodes at `PENDING`

If the test passes, the mutation model is safe. If it fails, the executor needs
adjustment before proceeding.

**Done when:** isolation test passes. No regressions in existing suite.

**Estimated scope:** one micro-session, possibly zero code changes.

---

### Step 4 — Add JSON persistence

**What changes:** graphs can be saved to and loaded from JSON files on disk, keyed
by graph ID. The registry gains a second registration path: file-backed loaders
alongside the in-memory loaders from Step 2.

```python
def register_graph_from_file(graph_id: str, path: Path) -> None:
    def loader() -> Graph:
        with open(path, encoding="utf-8") as f:
            return load_graph(json.load(f))
    GRAPH_REGISTRY[graph_id] = loader
```

A `save` CLI command writes any graph to disk by ID:

```
idiograph save arxiv --output ./graphs/arxiv.json
```

**Done when:** a graph can be saved to disk, re-registered from that file, and
executed with identical results. Round-trip test passes.

**Estimated scope:** one micro-session.

---

### Step 5 — FastAPI layer (if forcing function is met)

**What changes:** a FastAPI application is added as a separate entry point
(`src/idiograph/api.py`). Routes call the same registry, loaders, and executor
that the CLI uses. The CLI is not modified.

```python
from fastapi import FastAPI
from idiograph.core.executor import execute_graph
from idiograph.core.registry import load_graph_by_id

api = FastAPI()

@api.post("/run/{graph_id}")
async def run_graph(graph_id: str):
    graph = load_graph_by_id(graph_id)
    results = await execute_graph(graph)
    return results
```

Because each request calls `load_graph_by_id` which returns a fresh deep copy,
concurrent requests are isolated by construction.

**Done when:** FastAPI server starts, handles concurrent requests without state
corruption, and returns identical results to the CLI for the same graph ID and inputs.

**Estimated scope:** one to two micro-sessions depending on auth and error handling
requirements.

---

## Scheduling

```
Phase 7 complete
        ↓
Is forcing function met?
   NO  → Document constraint, proceed to Phase 8 with stdio MCP
   YES → Execute Steps 1–4 in sessions before Phase 8 begins
              ↓
         Phase 8 begins with request-safe state model
              ↓
         Step 5 (FastAPI) added if HTTP transport required
```

The migration adds no risk to Phase 7 or Phase 8 if deferred. It adds known,
bounded cost if the forcing function arrives. Either outcome is manageable because
the constraint is named and the remediation is scoped.

---

## Amendment Log Entry

To be added to `blueprint_amendments.md` as AMD-009:

```
### AMD-009 — State Management Migration Strategy
Affects: Phase 7 / Pre-Phase 8 (conditional)
Status: PENDING — awaiting forcing function
Decided: 2026-03
Reason: Current module-level graph state model is incompatible with stateless HTTP
serving and concurrent agent requests. Remediation is non-trivial (4–5 micro-sessions)
and should not be confused with "adding FastAPI" (1 afternoon). The constraint is
acceptable for Phase 6 and Phase 7. It must be resolved before Phase 8 if MCP
requires HTTP/SSE transport or if a web demo is required.
Change: Execute 5-step strangler fig migration (see state_management_migration.md)
prior to Phase 8 if forcing function is met. No action required otherwise.
Done when: Forcing function evaluated at Phase 7 close. Migration executed if
triggered; constraint documented if not.
```

---

## Architectural Constraints Log Entry

| Decision | Affects | Rationale |
|---|---|---|
| Module-level graph state is incompatible with stateless HTTP or concurrent agent requests | Phase 7 onward | Covered in AMD-009. Remediation is a defined 5-step strangler fig migration, not an afternoon refactor. Cost is bounded and sequenced. Triggered only if HTTP transport or web demo is required. |

---

*Created: 2026-03*
*Owner: Idiograph project*
*Trigger: Phase 7 close — evaluate forcing function and schedule or defer accordingly*
