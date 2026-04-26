# BRIEFING.md — Idiograph
*Live state. Updated when main changes, not at session end.*
*Last updated: 2026-04-26 (post PR #16 merge)*

---

## Current State

**Phase 9 — IN PROGRESS**
Main head: `dc2f6e4`
Test baseline: **120 passing**
Worktree: clean; no open branches pending merge.

---

## What's Built

### Citation graph pipeline — `src/idiograph/domains/arxiv/pipeline.py`

All functions individually callable and tested. No orchestrator chains them yet.

- **Node 0** — `fetch_seeds()` — direct seed entry, accepts list (AMD-017 multi-seed)
- **Node 3** — `backward_traverse()` — foundational lineage ranking
- **Node 4** — `forward_traverse()` — emerging-work ranking (α·velocity + β·acceleration)
- **Node 4.5** — `clean_cycles()` — weakest-link cycle suppression; returns `CycleCleanResult` with `cleaned_edges` and `cycle_log.suppressed_edges[].original` (full `CitationEdge`, no field loss). As of PR #16, `CycleCleanResult` carries a `Field(exclude=True)` witness `input_node_ids` and a `@model_validator(mode='after')` that fails construction on orphan-endpoint edges. Round-trip through `model_dump()` / `model_validate()` requires the witness to be re-supplied — persistence contract for Node 8.
- **Node 5** — `compute_co_citations()` — undirected co-citation edges, strength = shared-citer count, sorted `(-strength, source_id, target_id)`

### Adjacent systems

- Phase 6 arXiv abstract-processing pipeline in `handlers.py` (old executor-style, separate from the citation graph)
- Color Designer domain in `domains/color_designer/` — complete through AMD-018
- MCP server (`mcp_server.py`) — Phase 8 complete, six tools exposed over stdio
- 120 tests: 10 Node 0, 13 Node 3, 9 Node 4, 12 Node 4.5 + 7 validator, 20 Node 5, plus core/executor/query/graph/models

---

## Open Implementation Decisions

| Decision | Status |
|---|---|
| Next pipeline node | **Node 6 (metric computation)** — spec landing-ready (`spec-node6-metrics.md`), implementation session opens directly |
| Orchestrator placement | Deferred until Node 6 lands |

---

## What's Next

**Pipeline build-out (sequential):**
1. **Node 6 — metric computation** — `compute_depth_metrics()` (per-root BFS, traversal direction) + `compute_pagerank()` via NetworkX on the cleaned graph. Pure computation, deterministic, no new deps. Spec frozen-ready; implementation prompt drafted at `tmp/prompt-node6-implementation.md`. Lands the spec in the same PR per §Spec landing note. Target: 120 → 144 (+24 tests).
2. **Pipeline orchestrator** — first `run_arxiv_pipeline(seeds)` chaining Node 0 → (3, 4) → 4.5 → 5 → 6. Motivates the shape of Node 8's registry.
3. **Node 7 — community detection** — Infomap with Leiden fallback. Own design session (Infomap parameters, community-count emergence, LOD implications).
4. **Node 8 — registry** — content-addressed cache, JSON-serializable graphs on disk. Honors the round-trip-requires-witness contract from PR #16: every reload site reconstructs `input_node_ids` from the loaded node list before constructing `CycleCleanResult`.
5. **Demo surface** — vector index (ChromaDB), view functions, FastAPI, D3 renderer, self-description graph.
6. **Node 0.5 + Node 5.5 (AMD-016 LLM nodes)** — placement after the demo surface exists, not before.

**Post-Node-6 docs sweep (separate PR, deferred):**
- `spec-arxiv-pipeline-final.md` renderer data contract: remove `topological_depth` row, add `hop_depth_per_root` and `traversal_direction` rows. Node 6 section rewritten to match AMD-019.
- `spec-node4.5-cycle-cleaning.md` step-5 null-handling language: note that the behavior was superseded by AMD-019. (Note: PR #16 already superseded the "do not raise" graceful-degradation contract on this same spec — the step-5 null-handling edit is independent.)
- `amendments.md` AMD-017 "Downstream Metric Behavior in a Forest" table: AMD-019 cross-reference.

**Parallel tracks:**
- Essay editing pass — still queued.
- Seed pair validation spikes — once a complete pipeline exists to validate against.

---

## Active Specs

| Spec | Status |
|---|---|
| `docs/specs/spec-arxiv-pipeline-final.md` | Frozen — pipeline architecture (Node 6 section superseded by AMD-019; renderer data contract update deferred) |
| `docs/specs/spec-node4.5-cycle-cleaning.md` | Frozen — "do not raise" graceful-degradation language superseded by PR #16 (`Field(exclude=True)` validator); step-5 null-handling language pending AMD-019 update |
| `docs/specs/spec-node5-co-citation.md` | Frozen — landed with PR #13, §Boundaries correction in PR #14 |
| `docs/specs/spec-node6-metrics.md` | **LIVING — landing with Node 6 implementation PR** (drop-in version on disk pinned to `Field(exclude=True)` pattern) |

---

## Recent History

- **PR #16** (`dc2f6e4`, 2026-04-26) — `CycleCleanResult` validator, prerequisite to Node 6. `Field(exclude=True)` witness pattern; supersedes Node 4.5's "do not raise" graceful-degradation contract. 120 tests.
- **PR #14** (`8123a19`, 2026-04-23) — post-Node 5 housekeeping: `.gitignore`, CLAUDE.md branch protection note, Node 4.5 spec §Boundaries correction
- **PR #13** (`53a803b`, 2026-04-23) — Node 5 co-citation + spec freeze, 20 tests
- **PR #12** (`61b9218`, 2026-04-21) — Node 5 design sessions (primary + addendum)
- **PR #11** (`801f84b`, 2026-04-22) — SuppressedEdge refactor, composes CitationEdge
- **PR #10–#8** — post-Node 4.5 housekeeping, Node 4.5 implementation, Node 4 forward traversal

---

## Workflow Note

**Update cadence:** BRIEFING.md is updated **when main changes**, not at session end. Session summaries in `docs/sessions/` are frozen historical records describing a session's world at its close. Between a session's end and the next session's start, main can move forward through PR merges. The claim at the top of this file — "live state" — holds only when BRIEFING is refreshed at merge time, not conversation time.

**Two copies:** This file exists in the claude.ai project files and in the repo at `/BRIEFING.md`. The repo copy is the durable record; the project-files copy is what claude.ai reads at session start. Both must be kept in sync — by updating BRIEFING.md in the same PR (or follow-up commit) that moves main, then copying the updated file into project files as the last step before the next session.

**Reconciliation:** When in doubt about current state, `git clone` main and check directly. `git` is the authoritative source; everything else is a view.
