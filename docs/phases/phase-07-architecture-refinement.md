# Phase 7 Summary — Architecture Refinement

## What Was Built
Three targeted fixes to the existing system. No new features. The goal was a codebase
that is as clean to read as it is to run — which matters both for the thesis demo and
for the MCP integration in Phase 8.

## Changes Made

### 1. Fixed `node_id` trace corruption
The executor stamps `node_id` into every result dict. The bug: handler implementations
spread upstream results into their return value using `**upstream`, which carried the
upstream `node_id` forward. The executor's own `**output` spread then placed handler
output after its own fields, letting the upstream value overwrite the correct one.

Fix — swap the spread order in `_execute_node` so executor-set fields always win:

```python
# Before (broken)
return {"status": "SUCCESS", "node_id": node.id, **output}

# After (fixed)
return {**output, "status": "SUCCESS", "node_id": node.id}
```

The failure return was already safe (no `**output` spread). The SKIPPED return in
`execute_graph` was also already correct. One line changed.

### 2. Tightened `Node.status` to `Literal`
`Node.status` was an open `str`. Unlike `Edge.type` — which is open by design (AMD-003,
Phase 10 requires new edge semantics) — status has a fixed, known value set:
`PENDING`, `RUNNING`, `SUCCESS`, `FAILED`. There is no legitimate case for a value
outside that set.

```python
from typing import Literal

status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"] = Field(
    default="PENDING",
    description="Execution status. PENDING → RUNNING → SUCCESS or FAILED."
)
```

Pydantic now rejects invalid status values at construction time. The schema is
self-enforcing rather than documentation-dependent.

### 3. Declared the `core` public API
`core/__init__.py` previously exported only a subset of the core layer. The executor
and query functions were imported directly by `main.py`, bypassing the declared surface.
This works for a CLI but is a liability for Phase 8, where an MCP server will import
from `idiograph.core` and needs a stable, complete interface.

```python
from idiograph.core.pipeline import SAMPLE_PIPELINE
from idiograph.core.graph import summarize, get_node, get_edges_from, load_graph
from idiograph.core.config import load_config
from idiograph.core.logging_config import setup_logging, get_logger
from idiograph.core.executor import execute_graph, register_handler
from idiograph.core.query import (
    get_downstream,
    get_upstream,
    topological_sort,
    find_cycles,
    validate_integrity,
    summarize_intent,
)
```

`register_handler` is included explicitly — the MCP server will need to register
handlers at startup the same way the CLI does.

## Files Modified

- `src/idiograph/core/executor.py` — swapped spread order in success return
- `src/idiograph/core/models.py` — `Node.status` changed to `Literal`, added `Literal` import
- `src/idiograph/core/__init__.py` — full public API declared

## Verified Working
```
uv run pytest tests/ -v
→ 44 passed, no regressions
```

## Amendments Closed
The two known issues carried forward from Phase 6 are resolved:
- `node_id` trace corruption — fixed
- `Node.status` open string — tightened to `Literal`

## AMD-009 Forcing Function Evaluation
Per the state management migration strategy, Phase 7 close is the trigger point to
evaluate whether the migration is required before Phase 8.

**Evaluation:** Phase 8 targets MCP via stdio transport (the simpler local path,
established in AMD-004). No HTTP/SSE, no web demo, no multi-graph concurrent session
requirement. **Forcing function not met.** The module-level graph state constraint
remains a documented liability (AMD-009) and the migration is deferred.

## Next — Phase 8: MCP Integration
Idiograph gets exposed as a standards-compliant agent tool via the Model Context
Protocol. An MCP server will wrap the core tool interfaces — `get_node`,
`get_edges_from`, `summarize_intent`, `validate_graph`, `execute_graph` — so that any
MCP-compatible agent (Claude, local models, etc.) can connect, inspect a graph, modify
node parameters, and re-run execution without bespoke adapter code. This is the
primary external credibility artifact for the thesis: not a Claude-specific integration,
but a standards-compliant AI-operable system.

Transport target: stdio (local dev). HTTP/SSE deferred pending AMD-009 forcing function.
