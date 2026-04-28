# BRIEFING.md — Idiograph
*Live state. Updated when main changes, not at session end.*
*Last updated: 2026-04-28 (post PR #25 merge)*

---

## Current State

**Phase 9 — IN PROGRESS**
Main head: `b5514af`
Test baseline: **161 passing**
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
- **Node 6** — `compute_depth_metrics()` (per-root BFS over directed and undirected views; `traversal_direction` ∈ {seed, backward, forward, mixed} per AMD-019) and `compute_pagerank()` (single `nx.pagerank` call, isolates included). Two pure functions, deterministic. PR #18 also removed `topological_depth` from `PaperRecord` and added `hop_depth_per_root` + `traversal_direction` per AMD-019; `CycleLog.affected_node_ids` becomes audit/provenance-only. PR #21 (post-implementation refactor) renamed `forward_from` / `backward_from` locals inside `compute_depth_metrics()` so each variable name matches the `traversal_direction` label assigned to nodes in that set — eliminates the graph-direction-vs-citation-semantic vocabulary trap. No behavior change.
- **Node 7** — `detect_communities()` — Infomap primary, Leiden automatic fallback on `ImportError`, `RuntimeError` if neither installed. Returns `CommunityResult`: `community_assignments: dict[str, str]` (every node_id → string module id, including isolates), `algorithm_used`, `community_count`, `validation_flags` (LOD threshold warnings, never blocks). Edge input is cleaned ∪ suppressed originals assembled at call site — Node 7 is ignorant of `CycleCleanResult`. Co-citation edges excluded by design. Adds `[community]` optional extra (`infomap`, `leidenalg`, `igraph`); `igraph cp313-win_amd64` wheel verified available. Infomap constructor: `--two-level --silent --seed N` (flat partition enforced). Leiden path uses explicit integer-index round-trip with `g.add_vertices()` before `g.add_edges()` so isolates are pre-registered.

### Adjacent systems

- Phase 6 arXiv abstract-processing pipeline in `handlers.py` (old executor-style, separate from the citation graph)
- Color Designer domain in `domains/color_designer/` — complete through AMD-018
- MCP server (`mcp_server.py`) — Phase 8 complete, six tools exposed over stdio
- 161 tests: 10 Node 0, 13 Node 3, 9 Node 4, 12 Node 4.5 + 7 validator, 20 Node 5, 16 Node 6 depth + 8 Node 6 pagerank, 17 Node 7 (15 spec + 2 coverage closures), plus core/executor/query/graph/models
- Repo-wide SPDX header coverage as of PR #20 (color_designer app/domain and `scripts/`); three docstring-only `__init__.py` stubs skipped per Track 4.2 inclusion rule

---

## Open Implementation Decisions

| Decision | Status |
|---|---|
| Pipeline orchestrator | Next — `run_arxiv_pipeline(seeds)` chaining Node 0 → (3, 4) → 4.5 → 5 → 6 → 7. Deferred to post-Node-7 per Node 6 design session: composing two producers is a thin wrapper; composing four is a real architecture decision with shape implications for Node 8's registry. |

---

## What's Next

**Pipeline build-out (sequential):**
1. **Pipeline orchestrator** — `run_arxiv_pipeline(seeds)` chaining Node 0 → (3, 4) → 4.5 → 5 → 6 → 7. Separate design session. Merge pattern for cleaned ∪ suppressed originals already established at call sites in Node 5 and Node 7 tests; orchestrator formalizes it. Merges `community_id` onto each `PaperRecord` via `model_copy` after Node 7 returns.
2. **Node 8 — registry** — content-addressed cache, JSON-serializable graphs on disk. Honors the round-trip-requires-witness contract from PR #16: every reload site reconstructs `input_node_ids` from the loaded node list before constructing `CycleCleanResult`.
3. **Demo surface** — vector index (ChromaDB), view functions, FastAPI, D3 renderer, self-description graph.
4. **Node 0.5 + Node 5.5 (AMD-016 LLM nodes)** — placement after the demo surface exists, not before.

**Post-freeze sweep (doc-only PR, separate from pipeline work):**
- Update `spec-arxiv-pipeline-final.md` Node 7 section to reflect the implemented function pair and `CommunityResult` shape
- Freeze `spec-node7-community-detection.md` status header (currently ACTIVE)

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
| `docs/specs/spec-node6-metrics.md` | Frozen — landed with PR #18, including in-PR §Implementation constraints clarification (numpy/scipy as substrate dependencies, not graph-library alternatives). |
| `docs/specs/spec-node7-community-detection.md` | Active — landed with PR #25. Freezes on post-freeze sweep PR. |

---

## Recent History

- **PR #25** (`b5514af`, 2026-04-28) — Node 7 community detection. Three sub-commits squashed: (1) implementation — `detect_communities()` + `_run_infomap()`/`_run_leiden()` helpers, `CommunityResult` model, `[community]` extra, 15 spec-required tests, spec, session summary; (2) CI fix — `uv sync --extra community` added to `.github/workflows/tests.yml` (14/15 Node 7 tests were raising `RuntimeError` on CI without it); (3) coverage closures — two tests beyond the spec minimum (`test_leiden_fallback_when_infomap_missing`, `test_raises_when_neither_installed`) using `monkeypatch`-based `_patch_imports()` helper to cover the Leiden fallback body and the `RuntimeError` branch unreachable when both libraries are installed locally. 8 files, 144 → 161 tests.
- **PR #22** (`c683eb5`, 2026-04-27) — post-Node-6 docs sweep: pipeline spec / Node 4.5 spec / AMD-017 language aligned with AMD-019. Three files, +20/-15. No code or test changes. `topological_depth` purged from both spec files (preserved in AMD-017's historical forest-metrics table with cross-reference). Node 4.5 spec status bumped LIVING → FROZEN.
- **PR #21** (`4e97444`, 2026-04-27) — Node 6 direction rename: `forward_from`/`backward_from` variable bindings swapped inside `compute_depth_metrics()` to match the `traversal_direction` labels they produce. One function, one file, no behavior change, no test changes. 144/144.
- **PR #20** (`d7196f8`, 2026-04-27) — SPDX header sweep: color_designer app/domain and top-level `scripts/`. 14 files modified, +84 lines. Three docstring-only `__init__.py` stubs skipped per Track 4.2 inclusion rule. 144/144.
- **PR #18** (`22a32b8`, 2026-04-26) — Node 6 metric computation: `compute_depth_metrics` + `compute_pagerank`. AMD-019 implemented. Spec freezes on merge. In-PR §Implementation constraints clarification for numpy/scipy substrate distinction. 120 → 144 tests.
- **PR #17** (`24d99fa`, 2026-04-26) — `BRIEFING.md` refresh post PR #16
- **PR #16** (`dc2f6e4`, 2026-04-26) — `CycleCleanResult` validator, prerequisite to Node 6. `Field(exclude=True)` witness pattern; supersedes Node 4.5's "do not raise" graceful-degradation contract. 113 → 120 tests.
- **PR #14** (`8123a19`, 2026-04-23) — post-Node 5 housekeeping: `.gitignore`, CLAUDE.md branch protection note, Node 4.5 spec §Boundaries correction
- **PR #13** (`53a803b`, 2026-04-23) — Node 5 co-citation + spec freeze, 20 tests
- **PR #12** (`61b9218`, 2026-04-21) — Node 5 design sessions (primary + addendum)
- **PR #11** (`801f84b`, 2026-04-22) — SuppressedEdge refactor, composes CitationEdge

---

## Workflow Note

**Update cadence:** BRIEFING.md is updated **when main changes**, not at session end. Session summaries in `docs/sessions/` are frozen historical records describing a session's world at its close. Between a session's end and the next session's start, main can move forward through PR merges. The claim at the top of this file — "live state" — holds only when BRIEFING is refreshed at merge time, not conversation time.

**Two copies:** This file exists in the claude.ai project files and in the repo at `/BRIEFING.md`. The repo copy is the durable record; the project-files copy is what claude.ai reads at session start. Both must be kept in sync — by updating BRIEFING.md in the same PR (or follow-up commit) that moves main, then copying the updated file into project files as the last step before the next session.

**Reconciliation:** When in doubt about current state, `git clone` main and check directly. `git` is the authoritative source; everything else is a view.
