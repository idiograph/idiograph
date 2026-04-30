# BRIEFING.md — Idiograph
*Live state. Updated when main changes, not at session end.*
*Last updated: 2026-04-29 (post PR #27 and PR #28 merges)*

---

## Current State

**Phase 9 — IN PROGRESS**
Main head: `16ef88a`
Test baseline: **185 passing**
Worktree: clean; no open branches.

---

## What's Built

### Citation graph pipeline — `src/idiograph/domains/arxiv/pipeline.py`

All functions individually callable and tested. **Edge production now flows through the pipeline.** Nodes 3 and 4 emit `CitationEdge` records during traversal; downstream stages (cycle cleaning, co-citation, depth metrics, communities) consume the edges directly. The orchestrator (next major build target) can run end-to-end against the existing per-stage functions.

- **Node 0** — `fetch_seeds()` — direct seed entry, accepts list (AMD-017 multi-seed)
- **Node 3** — `backward_traverse()` — foundational lineage ranking. As of PR #28 (AMD-020), returns `Node3Result(papers, edges, failed_batches)`. Citation edges emitted at depth-1 and depth-2 merge sites; post-rank/cap edge filter enforces the contract that all endpoints are in returned papers (or are seeds). Batch-level fetch failures in `_fetch_works_by_ids` surface as `FailedBatch` records on the result, with stage-tagging for the three call sites (seed_refetch, depth_1, depth_2).
- **Node 4** — `forward_traverse()` — emerging-work ranking (α·velocity + β·acceleration). As of PR #28 (AMD-020), returns `Node4Result(papers, edges, failed_seeds, truncated_seeds)`. Required `sort` parameter (`Literal` of OpenAlex sort options) — eliminates the prior nondeterminism in the 200-citer cap. Per-seed call failures surface as `FailedSeed`. Cap-overflow events surface as `TruncatedSeed` (uses OpenAlex's `meta.count`). Pagination of the cap remains deferred.
- **Node 4.5** — `clean_cycles()` — weakest-link cycle suppression; returns `CycleCleanResult` with `cleaned_edges` and `cycle_log.suppressed_edges[].original` (full `CitationEdge`, no field loss). As of PR #16, `CycleCleanResult` carries a `Field(exclude=True)` witness `input_node_ids` and a `@model_validator(mode='after')` that fails construction on orphan-endpoint edges. Round-trip through `model_dump()` / `model_validate()` requires the witness to be re-supplied — persistence contract for Node 8.
- **Node 5** — `compute_co_citations()` — undirected co-citation edges, strength = shared-citer count, sorted `(-strength, source_id, target_id)`
- **Node 6** — `compute_depth_metrics()` (per-root BFS over directed and undirected views; `traversal_direction` ∈ {seed, backward, forward, mixed} per AMD-019) and `compute_pagerank()` (single `nx.pagerank` call, isolates included). Two pure functions, deterministic. PR #18 also removed `topological_depth` from `PaperRecord` and added `hop_depth_per_root` + `traversal_direction` per AMD-019; `CycleLog.affected_node_ids` becomes audit/provenance-only. PR #21 (post-implementation refactor) renamed `forward_from` / `backward_from` locals inside `compute_depth_metrics()` so each variable name matches the `traversal_direction` label assigned to nodes in that set — eliminates the graph-direction-vs-citation-semantic vocabulary trap. No behavior change.
- **Node 7** — `detect_communities()` — Infomap primary, Leiden automatic fallback on `ImportError`, `RuntimeError` if neither installed. Returns `CommunityResult`: `community_assignments: dict[str, str]` (every node_id → string module id, including isolates), `algorithm_used`, `community_count`, `validation_flags` (LOD threshold warnings, never blocks). Edge input is cleaned ∪ suppressed originals assembled at call site — Node 7 is ignorant of `CycleCleanResult`. Co-citation edges excluded by design. Adds `[community]` optional extra (`infomap`, `leidenalg`, `igraph`); `igraph cp313-win_amd64` wheel verified available. Infomap constructor: `--two-level --silent --seed N` (flat partition enforced). Leiden path uses explicit integer-index round-trip with `g.add_vertices()` before `g.add_edges()` so isolates are pre-registered.

### Adjacent systems

- Phase 6 arXiv abstract-processing pipeline in `handlers.py` (old executor-style, separate from the citation graph)
- Color Designer domain in `domains/color_designer/` — complete through AMD-018
- MCP server (`mcp_server.py`) — Phase 8 complete, six tools exposed over stdio
- 185 tests: 10 Node 0, 24 Node 3 (13 prior + 11 net new), 21 Node 4 (9 prior + 12 net new), 12 Node 4.5 + 7 validator, 20 Node 5, 16 Node 6 depth + 8 Node 6 pagerank, 17 Node 7, 2 cross-node determinism, plus core/executor/query/graph/models
- Repo-wide SPDX header coverage as of PR #20 (color_designer app/domain and `scripts/`); three docstring-only `__init__.py` stubs skipped per Track 4.2 inclusion rule

---

## Open Implementation Decisions

| Decision | Status |
|---|---|
| Pipeline orchestrator | In design — co-design with Node 8 input contract underway. Spec drafted (status `ACTIVE`) but needs revision to incorporate AMD-020 consumption changes (consumes `Node3Result` / `Node4Result` instead of `list[PaperRecord]`, `StageFailure` simplified to Node 0 only since Nodes 3/4 carry their own per-seed failure provenance, forest assembly section shrinks because Node 3's existing `_merge` helper handles intra-Node-3 dedup). Remaining unsettled design item: **async surface and OpenAlex client lifecycle** — Nodes 0/3 take a passed-in client, Node 4 creates its own; whether `run_arxiv_pipeline` is sync or async, and how it owns the client, is a real design call. |

---

## What's Next

**Pipeline build-out (sequential):**

1. **Pipeline orchestrator** — `run_arxiv_pipeline(seeds)` chaining Node 0 → (3, 4) → 4.5 → 5 → 6 → 7. Design session resumes; the async-surface decision is the major remaining design-discussion item before the orchestrator spec can revise and freeze. Spec revisions then incorporate AMD-020 consumption changes alongside the async decision. Audit cycle, then implementation. Merges `community_id`, `traversal_direction`, `hop_depth_per_root`, `pagerank` onto each `PaperRecord` via `model_copy` after Node 7 returns (Shape C: merged graph + per-stage results, both first-class).
2. **Node 8 — registry** — content-addressed cache, JSON-serializable graphs on disk. Honors the round-trip-requires-witness contract from PR #16: every reload site reconstructs `input_node_ids` from the loaded node list before constructing `CycleCleanResult`.
3. **Demo surface** — vector index (ChromaDB), view functions, FastAPI, D3 renderer, self-description graph.
4. **Node 0.5 + Node 5.5 (AMD-016 LLM nodes)** — placement after the demo surface exists, not before.

**Post-freeze sweep (doc-only PR, separate from pipeline work):**
- Update `spec-arxiv-pipeline-final.md` Node 7 section to reflect the implemented function pair and `CommunityResult` shape
- Freeze `spec-node7-community-detection.md` status header (currently ACTIVE)

**Tracked doc-org follow-ups:**
- `amendments.md:1158` — AMD-017's cross-reference carries the pre-correction AMD-019 date (2026-04-26 — should be 2026-04-24). Out-of-scope for any PR that doesn't have license to touch AMD-017's body. Fold into the next docs sweep.
- Constraints Log and Open Questions content updates to cover AMDs 015 through 019 — drift notes acknowledge this honestly; the actual content catchup is its own work, deferred.

**Parallel tracks:**
- Essay editing pass — still queued.
- Seed pair validation spikes — once a complete pipeline exists to validate against.

---

## Active Specs

| Spec | Status |
|---|---|
| `docs/specs/spec-arxiv-pipeline-final.md` | Frozen — pipeline architecture. Renderer data contract and Node 6 section aligned with AMD-019 in PR #22 (post-Node-6 docs sweep). Node 7 section pending post-freeze sweep. |
| `docs/specs/spec-node4.5-cycle-cleaning.md` | Frozen — status header bumped FROZEN in PR #22; `topological_depth` references rewritten in algorithm step 5, property docstring, and §Boundaries bullets per AMD-019. Constructor invariant landed in PR #16 (`Field(exclude=True)` validator). |
| `docs/specs/spec-node5-co-citation.md` | Frozen — landed with PR #13, §Boundaries correction in PR #14. |
| `docs/specs/spec-node6-metrics.md` | Frozen — landed with PR #18, including in-PR §Implementation constraints clarification. Canonical home of AMD-019's full text; indexed in `amendments.md` as of PR #27. |
| `docs/specs/spec-node7-community-detection.md` | Active — landed with PR #25. Freezes on post-freeze sweep PR. |
| `docs/specs/spec-node3-node4-edge-emission.md` | Frozen — landed with PR #28. AMD-020 implementation contract. |
| `docs/specs/spec-pipeline-orchestrator.md` | Active — drafted; cross-spec edit landed in PR #28 (`ForwardParameters` gained `sort` field). Needs further revision to incorporate AMD-020 consumption changes and resolve async-surface decision before freeze. |

---

## Recent History

- **PR #28** (`16ef88a` merge of `2db6a43`, 2026-04-29) — Node 3/Node 4 edge emission and failure provenance. Five new models in `models.py` (`Node3Result`, `Node4Result`, `FailedBatch`, `FailedSeed`, `TruncatedSeed`, plus `ForwardSort` literal). `_fetch_works_by_ids` return type changed to `tuple[list[dict], list[FailedBatch]]`. `backward_traverse` now emits citation edges at depth-1 and depth-2 merge sites with post-rank/cap edge filter. `forward_traverse` gained required `sort` parameter and emits citer→seed edges with per-seed-failure and truncation provenance. AMD-020 entry added with three Constraints Log rows and two Open Questions rows (first AMD landing under the index-maintenance discipline introduced by PR #27's workflow.md rewrite). Cross-spec edit threaded `sort` field through orchestrator spec's `ForwardParameters`. Test gate: 161 + 24 net new = 185 (exact match to expected). One existing test renamed and restructured: `test_batch_fetch_http_error_silently_skipped` → `test_batch_fetch_http_error_recorded_in_failed_batches`. Closes the pipeline-level gap that prevented the orchestrator from running end-to-end.
- **PR #27** (`2ba6916`, 2026-04-29) — amendments.md cleanup and workflow.md rewrite. Three pieces in one PR: (1) `amendments.md` structural restoration — AMD-018 moved to numerical position after AMD-017, AMD-016 heading reformatted from level-1 to level-3 (HTML history-comment preserved), AMD-019 indexed in standard format with closing line pointing to `spec-node6-metrics.md` for full text, `## Architectural Constraints Log` and `## Open Questions` sections moved to end of file with "Coverage current through AMD-014" drift notes added; (2) `workflow.md` rewritten from 8-step phase-centric structure to spec-centric two-session-types structure (§Two session types, §The full cycle / Step 0 through Step 9) reflecting Phase 9 reality; (3) AMD-index-maintenance discipline named as a step in AMD-creating sessions (Step 1 sub-step) and verified in PR review (Step 9 verification list). AMD-019 entry verified against canonical text in `spec-node6-metrics.md` during implementation — four divergences caught and resolved. Doc-only PR; 161/161.
- **PR #25** (`b5514af`, 2026-04-28) — Node 7 community detection. Three sub-commits squashed: (1) implementation — `detect_communities()` + `_run_infomap()`/`_run_leiden()` helpers, `CommunityResult` model, `[community]` extra, 15 spec-required tests, spec, session summary; (2) CI fix — `uv sync --extra community` added to `.github/workflows/tests.yml`; (3) coverage closures — two tests beyond the spec minimum using `monkeypatch`-based `_patch_imports()` helper. 8 files, 144 → 161 tests.
- **PR #22** (`c683eb5`, 2026-04-27) — post-Node-6 docs sweep: pipeline spec / Node 4.5 spec / AMD-017 language aligned with AMD-019. Three files, +20/-15. No code or test changes. Node 4.5 spec status bumped LIVING → FROZEN.
- **PR #21** (`4e97444`, 2026-04-27) — Node 6 direction rename: `forward_from`/`backward_from` variable bindings swapped inside `compute_depth_metrics()` to match the `traversal_direction` labels they produce. One function, one file, no behavior change, no test changes. 144/144.
- **PR #20** (`d7196f8`, 2026-04-27) — SPDX header sweep: color_designer app/domain and top-level `scripts/`. 14 files modified, +84 lines. 144/144.
- **PR #18** (`22a32b8`, 2026-04-26) — Node 6 metric computation: `compute_depth_metrics` + `compute_pagerank`. AMD-019 implemented. 120 → 144 tests.
- **PR #17** (`24d99fa`, 2026-04-26) — `BRIEFING.md` refresh post PR #16
- **PR #16** (`dc2f6e4`, 2026-04-26) — `CycleCleanResult` validator, prerequisite to Node 6. `Field(exclude=True)` witness pattern. 113 → 120 tests.
- **PR #14** (`8123a19`, 2026-04-23) — post-Node 5 housekeeping
- **PR #13** (`53a803b`, 2026-04-23) — Node 5 co-citation + spec freeze, 20 tests

---

## Workflow Note

**Update cadence:** BRIEFING.md is updated **when main changes**, not at session end. Session summaries in `docs/sessions/` are frozen historical records describing a session's world at its close. Between a session's end and the next session's start, main can move forward through PR merges. The claim at the top of this file — "live state" — holds only when BRIEFING is refreshed at merge time, not conversation time. This cadence is named in `workflow.md` Step 9.

**Two copies:** This file exists in the claude.ai project files and in the repo at `/BRIEFING.md`. The repo copy is the durable record; the project-files copy is what claude.ai reads at session start. Both must be kept in sync — by updating BRIEFING.md in the same PR (or follow-up commit) that moves main, then copying the updated file into project files as the last step before the next session.

**Reconciliation:** When in doubt about current state, `git clone` main and check directly. `git` is the authoritative source; everything else is a view.
