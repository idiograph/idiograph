# CLAUDE.md — Idiograph
*Orientation entry point for every Claude Code session. Read before doing anything else.*

---

## Project Identity

**Name:** Idiograph (never NodeForge — that name is retired)
**Repo:** `idiograph/idiograph` on GitHub (user: `ryansmith3d-pixel`)
**Language:** Python 3.13 · Package manager: `uv`

**Thesis:** Deterministic, semantically grounded systems are what AI tooling in production environments actually requires. The LLM is a node in the graph, not the orchestrator. Every phase reinforces this argument.

---

## Before You Write Any Code

1. Read this file ✓
2. Read the relevant spec file in `docs/specs/`
3. Run `uv run pytest tests/ -v` — confirm baseline test count
4. Declare the session type and give the orientation paragraph
5. Only then proceed to implementation

---

## Session Workflow

**Declare the session type:**

| Type | Purpose | Produces |
|---|---|---|
| **Implementation** | Build a phase or part of a phase | Phase summary (frozen) |
| **Design** | Plan before building | Update to a living spec |
| **Reconciliation** | Align docs with code; resolve drift | Amendment entries only |

**Steps in order:**
1. Orientation — one paragraph: current phase, last session output, this session's goal
2. Spec review — read the relevant spec before any code
3. Implementation — micro-sessions, system runnable at every stopping point
4. Post-mortem — what completed, what deferred, amendments log updated
5. Session artifact — one artifact per session

**No phase ends with broken code.** If a session ends mid-phase, record the stopping point.

---

## Test Gate

All tests must pass before and after every change. The test count never regresses.

```bash
uv run pytest tests/ -v
```

Record the baseline test count at session start. Any failure or regression: stop and fix before continuing.

---

## Branch Protection

`main` is branch-protected. All changes go through a PR. Never commit directly to `main`.

Required status checks: `tests/test` and `codecov/patch`.

To recover from an accidental commit to `main`:

```bash
git checkout -b <branch-name>
git reset --soft origin/main
git status
```

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
        color_designer/     ← optional [qt] extra
    mcp_server.py       ← MCP interface layer, does not modify core/
    main.py             ← CLI: idiograph run, idiograph serve

tests/
scripts/                ← gen_diagrams.py, test_mcp_smoke.py, automation scripts
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

Non-negotiable. If a proposed change conflicts with one, stop and raise it before proceeding.

| Constraint | Source | Rationale |
|---|---|---|
| `summarize_intent()` must be purely algorithmic — no LLM calls | AMD-013 | LLM call at the query layer undercuts the determinism thesis |
| Edge `type` is an open string — never a closed enum | AMD-003 | Phase 10 requires causal edge types without modifying the Edge model |
| Node `domain` is metadata/label only — never a structural constraint | AMD-013 | Phase 10 rendering nodes must fit the same architecture without special-casing |
| Domain implementations live under `domains/<domain>/` — never siblings to `core/` | AMD-011 | Directory communicates the architecture |
| Generated files in `docs/generated/` are never hand-edited | AMD-012 | Fix the generator, not the output |
| Port type enforcement: after Phase 9, not Phase 10 | AMD-014 | Credibility requirement, not stretch goal |
| State management migration: only when a real forcing requirement exists | AMD-009 | Do not migrate speculatively |
| `open(path)` requires `encoding="utf-8"` explicitly | Windows compat | cp1252 default causes silent failures |

---

## Amendment System

All architectural decisions are AMD-numbered entries in `docs/decisions/amendments.md`.

**Status vocabulary:** `Accepted` · `Accepted — Not Yet Implemented` · `Superseded by AMD-NNN` · `Deferred` · `Rejected`

AMD-001 through AMD-018 complete. Next is AMD-019.

When a decision is made or changed during a session, append an AMD entry before closing.

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

**Windows / Git Bash notes:**
- Use `Select-Object -First 30` not `head` in PowerShell
- Write files via Python directly; PowerShell `>` produces empty files
- Always `encoding="utf-8"` on `open()` calls

---

## Key Libraries

| Library | Purpose |
|---|---|
| Pydantic V2 | Schema validation — Node, Edge, Graph, Port models |
| Typer | CLI (`idiograph run`, `idiograph serve`) |
| NetworkX | Graph topology, topological sort |
| httpx | API calls |
| anthropic | LLM call handler |
| python-dotenv | `.env` loading |
| PySide6 | Color Designer UI (`[qt]` extra) |
| ruff | Linting |
| pytest | Test runner |

---

## OpenAlex API

- Auth: `api_key=<value>` param loaded from `.env` via python-dotenv
- Rate limit: 10 rps hard limit; 150ms sleep is project standard
- Reference client: `scripts/spikes/openalex_crispr/openalex_client.py`

---

## "NodeForge" References

If you encounter "NodeForge" anywhere in the codebase, it is a rename artifact. Flag it — do not preserve it. Task 4.4 in the Phase 9 inventory is the full rename sweep.
