# BRIEFING.md — Idiograph
*Live state. Updated when main changes, not at session end.*
*Last updated: 2026-04-23 (post PR #14 merge)*

---

## Current State

**Phase 9 — IN PROGRESS**
Main head: `8123a19`
Test baseline: **113 passing**
Worktree: clean; no open branches pending merge.

---

## What's Built

### Citation graph pipeline — `src/idiograph/domains/arxiv/pipeline.py`

All functions individually callable and tested. No orchestrator chains them yet.

- **Node 0** — `fetch_seeds()` — direct seed entry, accepts list (AMD-017 multi-seed)
- **Node 3** — `backward_traverse()` — foundational lineage ranking
- **Node 4** — `forward_traverse()` — emerging-work ranking (α·velocity + β·acceleration)
- **Node 4.5** — `clean_cycles()` — weakest-link cycle suppression; returns `CycleCleanResult` with `cleaned_edges` and `cycle_log.suppressed_edges[].original` (full `CitationEdge`, no field loss)
- **Node 5** — `compute_co_citations()` — undirected co-citation edges, strength = shared-citer count, sorted `(-strength, source_id, target_id)`

### Adjacent systems

- Phase 6 arXiv abstract-processing pipeline in `handlers.py` (old executor-style, separate from the citation graph)
- Color Designer domain in `domains/color_designer/` — complete through AMD-018
- MCP server (`mcp_server.py`) — Phase 8 complete, six tools exposed over stdio
- 113 tests: 10 Node 0, 13 Node 3, 9 Node 4, 12 Node 4.5, 20 Node 5, plus core/executor/query/graph/models

---

## Open Implementation Decisions

| Decision | Status |
|---|---|
| Next pipeline node | **Node 6 (metric computation)** — recommended |
| Orchestrator placement | Deferred until Node 6 lands |
| Docs PR: CLAUDE.md revision + create BRIEFING.md in repo | **Queued** — revised CLAUDE.md and this BRIEFING live in claude.ai project files; both need to land in the repo in one PR. BRIEFING.md does not currently exist in the repo — creation is part of this PR. |

---

## What's Next

**Housekeeping (independent, small):**
1. **Docs PR — CLAUDE.md revision + BRIEFING.md creation.** Two files, one PR. Revised CLAUDE.md replaces the stale-state-bearing version on main (which still claims "Phase 8 complete / 44 tests"). BRIEFING.md is a new file at repo root — it does not currently exist in the repo. From this PR forward, BRIEFING.md rides along with every PR that moves main, which is what makes "update at merge time" enforceable rather than aspirational. Can run in parallel with Node 6 design.

**Pipeline build-out (sequential):**
2. **Node 6 — metric computation** — pagerank + topological_depth via NetworkX on the cleaned graph. Pure computation, deterministic, no new deps. Design spec first, then implementation.
3. **Pipeline orchestrator** — first `run_arxiv_pipeline(seeds)` chaining Node 0 → (3, 4) → 4.5 → 5 → 6. Motivates the shape of Node 8's registry.
4. **Node 7 — community detection** — Infomap with Leiden fallback. Own design session (Infomap parameters, community-count emergence, LOD implications).
5. **Node 8 — registry** — content-addressed cache, JSON-serializable graphs on disk.
6. **Demo surface** — vector index (ChromaDB), view functions, FastAPI, D3 renderer, self-description graph.
7. **Node 0.5 + Node 5.5 (AMD-016 LLM nodes)** — placement after the demo surface exists, not before.

**Parallel tracks:**
- Essay editing pass — still queued.
- Seed pair validation spikes — once a complete pipeline exists to validate against.

---

## Active Specs

| Spec | Status |
|---|---|
| `docs/specs/spec-arxiv-pipeline-final.md` | Frozen — pipeline architecture |
| `docs/specs/spec-node5-co-citation.md` | Frozen — landed with PR #13, §Boundaries correction in PR #14 |
| `docs/specs/spec-node6-metrics.md` | **To draft** |

---

## Recent History

- **PR #14** (`8123a19`, 2026-04-23) — post-Node 5 housekeeping: `.gitignore`, CLAUDE.md branch protection note, Node 4.5 spec §Boundaries correction
- **PR #13** (`53a803b`, 2026-04-23) — Node 5 co-citation + spec freeze, 20 tests
- **PR #12** (`61b9218`, 2026-04-21) — Node 5 design sessions (primary + addendum)
- **PR #11** (`801f84b`, 2026-04-22) — SuppressedEdge refactor, composes CitationEdge
- **PR #10–#8** — post-Node 4.5 housekeeping, Node 4.5 implementation, Node 4 forward traversal

---

## Workflow Note

**Update cadence:** BRIEFING.md is updated **when main changes**, not at session end. Session summaries in `docs/sessions/` are frozen historical records describing a session's world at its close. Between a session's end and the next session's start, main can (and did in 2026-04-23) move forward through PR merges. The claim at the top of this file — "authoritative source of project state" — holds only when BRIEFING is refreshed at merge time, not conversation time.

**Two copies (once the docs PR lands):** This file will exist in the claude.ai project files and in the repo at `/BRIEFING.md`. The repo copy is the durable record; the project-files copy is what claude.ai reads at session start. Both must be kept in sync — ideally by updating BRIEFING.md in the same PR that moves main, then copying the updated file into project files as the last step before the next session.

**Current status of this mechanism:** BRIEFING.md does not yet exist in the repo. It is created in the docs PR queued under What's Next. Until that PR merges, BRIEFING lives only in claude.ai project files and the "updated at merge time" cadence is aspirational rather than enforced.

**Reconciliation:** When in doubt about current state, `git clone` main and check directly. `git` is the authoritative source; everything else is a view.
