# Phase 8 Summary — MCP Integration

**stdio Transport · 44 Tests Passing**

---

## Overview

Phase 8 exposes Idiograph as a standards-compliant agent tool via the Model Context Protocol (MCP). An MCP server wraps the core tool interfaces so that any MCP-compatible agent client — Claude Desktop, a local model, or any future client — can connect, inspect a graph, modify node parameters, and trigger execution without bespoke adapter code.

This is the primary external credibility artifact for the thesis. Not a Claude-specific integration — a standards-compliant AI-operable system.

---

## Pre-Phase 8 Sequence — Closed

| Amendment | Status |
|---|---|
| AMD-011 | Domain-first directory restructure — COMPLETE |
| AMD-014 | Optional port fields (Node, Edge, Graph schemas) — COMPLETE |
| AMD-010 | Mock flag, .env.example, README — COMPLETE |
| Phase 8 | MCP integration via stdio transport — COMPLETE |

---

## What Was Built

### `src/idiograph/mcp_server.py`

New interface layer sitting alongside `main.py`. Imports from `core`, does not modify it.

```
Agent Client (stdio / JSON-RPC)
        ↓
src/idiograph/mcp_server.py    ← new
        ↓
src/idiograph/core/            ← unchanged
```

The server holds one module-level `Graph` instance initialized at startup. stdio transport means one client, one process — scoped and documented correctly per AMD-009. The instance is held for the session lifetime and mutated in-place by `update_node` calls.

### `idiograph serve`

New CLI command added to `main.py`. Loads `SAMPLE_PIPELINE`, initializes the graph, and starts the MCP server on stdio.

```
uv run idiograph serve
```

### Six Tools Exposed via MCP

| Tool | What it does |
|---|---|
| `get_node` | Return a node by ID. Includes type, params, status, and port declarations (AMD-014 fields present as null until enforcement). |
| `get_edges_from` | Return all outgoing edges from a node. Includes `from_port` / `to_port` (AMD-014). |
| `update_node` | Merge supplied key/value pairs into a node's params dict in-place. Agent-driven graph mutation without human-written adapter code. |
| `summarize_intent` | Structured semantic summary of the full graph or a scoped subgraph. Purely algorithmic — no LLM calls. Deterministic output. |
| `validate_graph` | Referential integrity check. Returns `valid` (bool) and `errors` list. Clean gate before execution. |
| `execute_graph` | Run the full pipeline in topological order. Returns per-node status and results. |

---

## Key Decisions

**State model: module-level graph for stdio.** `_graph` is a module-level variable set once at startup. One client, one process, one graph. AMD-009 documents the liability — no concurrent session support. The forcing function for migration (HTTP/SSE with multiple concurrent clients) is not met. Documented, not ignored.

**`update_node` is a merge, not a replace.** `dict.update()` merges supplied params into existing ones. A full replace would silently discard keys the agent didn't touch. Merge is the safer default — the agent can always overwrite a key explicitly.

**`execute_graph` behavior on SAMPLE_PIPELINE is correct.** SAMPLE_PIPELINE uses VFX node types with no registered handlers. The executor fails cleanly at `node_01` and marks all dependents SKIPPED. That is blast radius containment working as designed. The arXiv pipeline with mock handlers registered would run clean. Both behaviors are expected.

**`summarize_intent` has no LLM calls.** The tool manifest description makes this explicit. This is an architectural constraint logged in AMD-013 — an LLM call inside the query layer would undercut the determinism thesis at its foundation. The tool description is also the thesis statement for any agent that reads the manifest.

**`core/__init__.py` was incomplete.** The Phase 7 summary documented the full public API export but the actual file had not been updated. The missing exports (`validate_integrity`, `summarize_intent`, `execute_graph`, `register_handler`, query functions) were added as Phase 8 unblocking work. All 44 tests passed before the MCP server imported from core.

---

## Files Produced or Modified

| File | Change |
|---|---|
| `src/idiograph/mcp_server.py` | New — MCP server, session state, six tool handlers |
| `src/idiograph/main.py` | Added `serve()` command and `mcp_main` import |
| `src/idiograph/core/__init__.py` | Added missing exports: executor, query functions |
| `scripts/test_mcp_smoke.py` | New — client-side smoke test exercising all six tools |

---

## Verified Working

```
uv run pytest tests/ -v
→ 44 passed, no regressions

uv run python scripts/test_mcp_smoke.py
→ Tools discovered: ['get_node', 'get_edges_from', 'update_node',
                     'summarize_intent', 'validate_graph', 'execute_graph']
→ All six tools called, correct responses returned
→ Smoke test passed.
```

---

## Thesis Connection

The MCP integration makes one thing concrete that was previously only argued: any standards-compliant agent can now operate this graph without knowing it exists. The agent calls `list_tools`, gets a manifest, and starts working. It doesn't need to know the graph is Pydantic-validated, or that `summarize_intent` has no LLM inside it, or that `execute_graph` respects topological order. Those properties are in the system — not in instructions to the agent.

That is the thesis expressed as a running artifact. The architecture makes the correctness guarantees, not the agent's behavior.

---

## Next

Phase 9 — documentation, demo packaging, and visibility work.

The essay does not need to wait for Phase 9 to begin. It is the highest-leverage action for the career pivot and should run in parallel.

Port type enforcement (AMD-014 gate) and HTTP/SSE transport (AMD-009 forcing function evaluation) are post-Phase 9 concerns.
