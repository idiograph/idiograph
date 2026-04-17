# CLAUDE.md — Idiograph

**This file is the orientation entry point for every Claude Code session.**
Read it before doing anything else. Do not implement before completing Step 1 of the session workflow.

---

## Project Identity

**Name:** Idiograph (never NodeForge — that name is retired)
**Repo:** `idiograph/idiograph` on GitHub (user: `ryansmith3d-pixel`)
**Domain:** `theidiograph.com` (via Porkbun)
**Language:** Python 3.13
**Package manager:** `uv`

**Thesis:** Deterministic, semantically grounded systems are what AI tooling in production environments actually requires. The LLM is a node in the graph, not the orchestrator. Every phase should reinforce this argument, not just build features.

---

## Current State

**Phase 8 — COMPLETE** (MCP integration via stdio transport)
- Six tools exposed: `get_node`, `get_edges_from`, `update_node`, `summarize_intent`, `validate_graph`, `execute_graph`
- 44 tests passing
- GitHub Actions CI with test badge live
- Smoke test script: `scripts/test_mcp_smoke.py`

**Phase 9 — IN PROGRESS**
See `docs/specs/spec-phase-08-09-task-inventory.md` for the full task inventory.

Four tracks:
- **Track 1 — Demo** (blocking gate): richer arXiv pipeline, vector index, view functions, FastAPI, D3 renderer, self-description graph
- **Track 2 — Documentation scripts**: `scripts/new_session_summary.py`, `scripts/new_amendment.py`
- **Track 3 — Essay** (parallel, mobile-compatible)
- **Track 4 — Housekeeping**: CLAUDE.md audit, rename sweep, README polish, legal headers

---

## Session Workflow

**Declare the session type before doing anything:**

| Type | Purpose | Produces |
|---|---|---|
| **Implementation** | Build a phase or part of a phase | Phase summary (frozen) |
| **Design** | Plan before building | Update to a living spec |
| **Reconciliation** | Align docs with code; resolve drift | Amendment entries only |

**Every session runs these steps in order:**

1. **Orientation** — one paragraph: current phase, last session output, this session's goal
2. **Prior phase review** — depth scaled to time since last session
3. **Retention check** — opt-in, must be explicitly requested
4. **Phase overview** — Implementation sessions only: goal, thesis connection, key decisions before any code
5. **Implementation** — micro-sessions, system runnable at every stopping point
6. **Post-mortem** — what completed, what deferred, amendments log updated
7. **Session artifact** — one artifact per session, type determined by session type
8. **Retention check on current session** — opt-in

**No phase ends with broken code.** If a session ends mid-phase, record the stopping point in Step 6.

---

## Test Gate

**All tests must pass before and after every change. The test count never regresses — new work only adds tests.**

```bash
uv run pytest tests/ -v
```

Record the baseline test count at session start. Any failure or regression: stop and fix before continuing.
---

## Directory Structure

```
src/idiograph/
    core/               ← domain-agnostic executor, schemas, query layer
    domains/
        arxiv/
            __init__.py     ← contains register_all()
            handlers.py
            pipeline.py
    mcp_server.py       ← MCP interface layer, does not modify core/
    main.py             ← CLI: idiograph run, idiograph serve

tests/                  ← 44 tests, all must pass
scripts/                ← gen_diagrams.py, test_mcp_smoke.py, and Phase 9 automation scripts
docs/
    decisions/          ← amendments.md (single append-only file)
    phases/             ← frozen phase summaries
    sessions/           ← frozen session summaries
    specs/              ← living design specs
    vision/             ← stable thesis and principles
    generated/          ← Mermaid diagrams (CI-enforced, never hand-edit)
```

**The directory structure is the argument.** `core/` is domain-agnostic. `domains/arxiv/` is one domain implementation. Future domains plug in at `domains/<domain>/` without modifying `core/`.

---

## Architectural Constraints

These are non-negotiable. If a proposed change conflicts with one, stop and raise it explicitly before proceeding.

| Constraint | Source | Rationale |
|---|---|---|
| `summarize_intent()` must be purely algorithmic — no LLM calls | AMD-013 | An LLM call at the query layer undercuts the determinism thesis at its foundation |
| Edge `type` must be an open string, never a closed enum | AMD-003 | Phase 10 requires causal edge types (MODULATES, DRIVES, OCCLUDES) without modifying the Edge model |
| Node `domain` is metadata/label only, never a structural constraint | AMD-013 | Phase 10 rendering nodes must fit the same architecture without special-casing |
| Domain implementations live under `domains/<domain>/`, never as siblings to `core/` | AMD-011 | The directory communicates the architecture without a README |
| `open(path)` requires `encoding="utf-8"` explicitly | Windows compat | cp1252 default causes silent failures |
| Generated files in `docs/generated/` are never hand-edited | AMD-012 | Fix the generator, not the output |
| Port type enforcement: add after Phase 9, not Phase 10 | AMD-014 | Credibility requirement, not a stretch goal |
| State management (AMD-009): module-level graph → registry | AMD-009 | Forcing function is a real multi-user or persistence requirement; do not migrate speculatively |

---

## Amendment System

All architectural decisions are formalized as AMD-numbered entries in `docs/decisions/amendments.md`.

**Status vocabulary:**
- `Accepted` — in force, implemented
- `Accepted — Not Yet Implemented` — decision made, code not written
- `Superseded by AMD-NNN` — replaced; keep the record
- `Deferred` — valid, not current build target
- `Rejected` — considered and ruled out; keep the record

**AMD sequence so far:** AMD-001 through AMD-018. Next is AMD-019.

When a decision is made or changed during a session, append an AMD entry before closing. Do not defer amendment logging to a later session.

---

## Document Taxonomy

| Type | Editable? | Lives in |
|---|---|---|
| Frozen | Never after creation | `docs/phases/` or `docs/sessions/` |
| Living | Yes, until freeze trigger | `docs/specs/` |
| Stable | Rarely | `docs/vision/` |
| Generated | Never by hand | `docs/generated/` |

**Naming rules:**
- Phase summaries: `phase-NN-short-topic.md` (zero-padded)
- Session summaries: `session-YYYY-MM-DD.md`
- Living specs: `spec-short-topic.md`
- Amendments: single file `amendments.md`, append-only

---

## Environment

```bash
# Run tests
uv run pytest tests/ -v

# Run CLI
uv run idiograph run <arxiv_id>
uv run idiograph run <arxiv_id> --mock   # no API key required
uv run idiograph serve                   # start MCP server on stdio

# Run MCP smoke test
uv run python scripts/test_mcp_smoke.py

# Regenerate Mermaid diagrams
uv run python scripts/gen_diagrams.py
```

**Windows / PowerShell notes:**
- Use `Select-Object -First 30` not `head`
- Write files via Python directly; PowerShell `>` produces empty files
- Always `encoding="utf-8"` on `open()` calls

---

## Key Libraries

| Library | Purpose |
|---|---|
| Pydantic V2 | Schema validation — Node, Edge, Graph, Port models |
| Typer | CLI (`idiograph run`, `idiograph serve`) |
| NetworkX | Graph topology, topological sort |
| httpx | arXiv API calls |
| anthropic | LLM call handler (Haiku in pipeline; Sonnet in dev) |
| python-dotenv | `.env` loading |
| ruff | Linting |
| pytest | Test runner |

---

## What "NodeForge" Means

If you encounter "NodeForge" anywhere in the codebase, it is a rename artifact. The project is Idiograph. Flag it — do not preserve it. Task 4.4 in the Phase 9 inventory is a full rename sweep.

---

## Before You Write Any Code

1. Read this file ✓
2. Run `uv run pytest tests/ -v` — confirm 44 tests pass
3. Declare the session type
4. Give the orientation paragraph (Step 1 of the workflow)
5. Only then proceed to implementation

---

---

## Color Designer Tool

**Location:** `tools/color-designer/`
**Environment:** Separate `uv` environment — do NOT use the root environment
**Spec:** `tools/color-designer/SPEC.md` — read before implementing anything

### Environment commands

```bash
cd tools/color-designer
uv run python src/main.py        # launch the app
uv run python test_token_store.py  # verify token store
```

### Architecture constraints

| Constraint | Rationale |
|---|---|
| No Idiograph-specific logic inside tool core | Tool is built to extract as standalone |
| Token file is open registry — never hardcode role names in UI code | Roles are data, not code |
| `token_store.py` is pure data layer — no UI imports | Survives any UI rewrite |
| All file operations use `encoding="utf-8"` explicitly | Windows compat |
| Node view state is per-instance, not global | Each node remembers its own view |

### Test gate

No pytest suite yet for the color designer. Before and after every change:

```bash
cd tools/color-designer
uv run python test_token_store.py
```

Must exit cleanly with "Round-trip passed" before proceeding.

### Current phase

**Phase G — COMPLETE**
**Verify first:** Schema Compact view collapse — may have residual height bug after
port display mode strip removal. Run the app and switch Schema to Compact before
starting any new work.

**Phase H and beyond are deferred.** Next work is color design iteration using the
tool as built.

*Last updated: 2026-04-06*
*Owner: Idiograph project — Ryan Smith*
