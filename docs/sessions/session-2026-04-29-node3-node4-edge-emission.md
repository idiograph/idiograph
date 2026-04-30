# Idiograph — Session Summary
**Date:** 2026-04-29
**Status:** FROZEN — historical record, do not revise
**Session type:** Implementation
**Branch:** feature/node3-node4-edge-emission (PR pending)

---

## Context

Node 3 / Node 4 edge emission and failure provenance precursor against the
living spec at `docs/specs/spec-node3-node4-edge-emission.md` and the
implementation prompt at `tmp/prompt-node3-node4-edge-implementation.md`.
Spec was untracked in the working tree at session start and is included
in this PR per the same pattern as Nodes 5, 6, 7.

Prerequisites verified at session start:
- Baseline 161 tests passing.
- `CitationEdge` model already present in `models.py` — no edge-schema
  changes needed.
- `_fetch_works_by_ids` helper already present in `pipeline.py` — its
  return type is the lever AMD-020 turns to surface batch failures.
- AMD-019 already merged via the cleanup PR (#27); the next AMD number is
  AMD-020.

Scope: extend Node 3 and Node 4 return types via `Node3Result` and
`Node4Result` wrappers, emit citation edges at traversal time, surface
failure modes (`failed_batches`, `failed_seeds`, `truncated_seeds`), add
required `sort` parameter to `forward_traverse`. AMD-020 lands as the
first AMD under the new index-maintenance discipline introduced by the
cleanup PR's `workflow.md` rewrite.

---

## What Was Built

### `models.py`

Five new BaseModel classes and one Literal alias inserted between
`CycleCleanResult` and `make_node_id` (top of the helper section,
alongside the other pipeline-result wrapper models):

- `ForwardSort` — `Literal["cited_by_count:desc", "cited_by_count:asc",
  "publication_date:desc", "publication_date:asc"]`. Closed set per the
  spec; no defaults, caller must specify.
- `FailedBatch(requested_ids, stage, reason)` — Node 3 batch-fetch
  failure record. `stage` is a closed `Literal` of `seed_refetch`,
  `depth_1`, `depth_2`.
- `Node3Result(papers, edges, failed_batches)` — Node 3 return wrapper.
  `failed_batches` defaults to empty list.
- `FailedSeed(seed_id, reason)` — Node 4 per-seed call failure.
- `TruncatedSeed(seed_id, returned_count, total_count)` — Node 4 cap-event
  record. Surfaces `meta.count - len(results)` silent truncation.
- `Node4Result(papers, edges, failed_seeds, truncated_seeds)` — Node 4
  return wrapper. Both failure-list fields default to empty list.

All fields use `Field(description=...)` per AMD-001. Class docstrings
explain the contract for downstream consumers.

### `pipeline.py`

**`_fetch_works_by_ids` signature change.** Return type goes from
`list[dict]` to `tuple[list[dict], list[FailedBatch]]`. New required
`stage` kwarg threads the call site label through to the `FailedBatch`
record. `httpx.HTTPError` no longer drops batches into DEBUG logs —
batches surface as `FailedBatch(stage=..., requested_ids=batch,
reason=f"http_error: {e}")`. Resolves spec open question 1, option (a).

**`backward_traverse`.**
- Return type: `list[PaperRecord]` → `Node3Result`.
- Three call sites of `_fetch_works_by_ids` now thread `stage="seed_refetch"`,
  `stage="depth_1"`, `stage="depth_2"` and accumulate `FailedBatch` records
  into `failed_batches`.
- Edge emission added at two sites after the merge walk:
  - Depth-1: for each `(seed, depth-1 paper)` pair where the depth-1
    metadata was fetched, emit `CitationEdge(source_id=seed.node_id,
    target_id=make_node_id(work), type="cites",
    citing_paper_year=seed.year)`.
  - Depth-2: for each `(depth-1 paper, depth-2 paper)` pair where both
    metadata sets were fetched and the depth-1 source is not itself a
    seed (avoids duplicates with the depth-1 emission of that seed),
    emit a similar edge with the depth-1 paper's `publication_year`.
- Post-rank/cap edge filter: edges whose endpoints aren't in `papers ∪
  seeds` are dropped. Edges sorted by `(source_id, target_id)` for
  determinism (resolves spec open question 3 per the spec author's lean).
- Returns `Node3Result(papers=..., edges=filtered_edges,
  failed_batches=...)`.

**`forward_traverse`.**
- Return type: `list[PaperRecord]` → `Node4Result`.
- New required keyword-only `sort: ForwardSort` parameter, threaded into
  the OpenAlex query params alongside `filter`, `select`, `per-page`.
  Caller must specify; `TypeError` raised otherwise.
- Per-seed `httpx.HTTPError` paths now append `FailedSeed(seed_id=...,
  reason=f"http_error: {e}")` and continue.
- After successful per-seed fetch, compares `meta.count` to
  `len(results)`; appends `TruncatedSeed` when `meta.count > len(results)`.
- For each citing paper added to the merged set, emits
  `CitationEdge(source_id=node_id, target_id=seed.node_id, type="cites",
  citing_paper_year=work.publication_year)`.
- Post-rank/cap edge filter: source must be in `papers`, target must be
  in seeds. Edges sorted by `(source_id, target_id)`.
- Returns `Node4Result(papers, edges, failed_seeds, truncated_seeds)`.

DEBUG-level "silently skipped" logs upgraded to INFO at both sites where
they previously hid the failure modes.

### `tests/domains/arxiv/test_pipeline_node3.py`

- 8 existing traversal tests mechanically updated to bind `result =`
  instead of `out =` and assert against `result.papers`.
- 1 rename: `test_batch_fetch_http_error_silently_skipped` →
  `test_batch_fetch_http_error_recorded_in_failed_batches`. Assertion
  shape restructured to verify `Node3Result` shape, empty papers/edges,
  and the `seed_refetch` stage on the recorded `FailedBatch`.
- 11 new tests added: `test_node3_result_returns_wrapper`,
  `test_node3_emits_seed_to_depth1_edges` (includes the seeds-themselves
  edge case), `test_node3_emits_depth1_to_depth2_edges`,
  `test_node3_no_dangling_edges_after_cap`,
  `test_node3_seed_refetch_failure_recorded`,
  `test_node3_depth_1_failure_recorded`,
  `test_node3_depth_2_failure_recorded`,
  `test_node3_failed_batches_carries_requested_ids`,
  `test_node3_full_success_empty_failed_batches`,
  `test_node3_edge_citing_paper_year_set`,
  `test_node3_deterministic_same_input`.
- New fixture `_StageFailingClient` exercises stage-specific failures by
  failing on the Nth `client.get` call (0=seed_refetch, 1=depth_1,
  2=depth_2 under the small-fixture call-order assumption).
- File total: 24 tests (was 13).

### `tests/domains/arxiv/test_pipeline_node4.py`

- 8 existing traversal tests mechanically updated: `result.papers` and
  added `sort="cited_by_count:desc"` kwarg.
- `_CitesClient` fixture extended with optional `meta_count_by_seed`
  (overrides synthetic `meta.count` for truncation tests) and
  `fail_seeds` (raises `httpx.ConnectError` for listed OA IDs).
- 13 new tests added: `test_node4_result_returns_wrapper`,
  `test_node4_emits_citer_to_seed_edges`,
  `test_node4_failed_seed_recorded`,
  `test_node4_failed_seed_distinguishable_from_zero_citers`,
  `test_node4_truncation_recorded`,
  `test_node4_no_truncation_under_cap`,
  `test_node4_sort_parameter_required`,
  `test_node4_sort_passes_through_to_query`,
  `test_node4_full_success_empty_failure_lists`,
  `test_node4_edge_citing_paper_year_set`,
  `test_node4_no_dangling_edges_after_cap`,
  `test_node4_failed_seed_not_in_truncated_seeds`,
  `test_node4_deterministic_same_input`.
- File total: 22 tests (was 9).

### `docs/specs/spec-node3-node4-edge-emission.md`

Spec (untracked in the working tree before this session) lands as-is with
this PR per the Node 5/6/7 pattern.

### `docs/specs/spec-pipeline-orchestrator.md`

Cross-spec edit. The `ForwardParameters` model gains a required `sort:
ForwardSort` field with no default. The spec was untracked in the working
tree before this session and lands with this PR with the AMD-020 edit
already applied. The orchestrator spec remains in design — only the
single field addition is normative for AMD-020.

### `docs/decisions/amendments.md`

- New AMD-020 entry inserted after AMD-019, before
  `## Architectural Constraints Log`.
- Three new rows in the Constraints Log table for the three constraints
  AMD-020 introduces (post-rank/cap emission, required `sort`, per-batch
  failure granularity).
- Two new rows in the Open Questions table (Node 4 pagination, OpenAlex
  transport retry/extraction).
- Drift notes at top of both tables updated from "Coverage current
  through AMD-014" to "Coverage current through AMD-014 and AMD-020.
  Entries from AMD-015 through AMD-019 pending."

---

## Test Gate

**Baseline:** 161 passing on main.

**Final:** 185 passing on the feature branch.

**Net new: 24** (matches the prompt's expected count exactly).
- Node 3 file: +11 net new (10 spec tests + 1 determinism). The
  rename-and-restructure of `test_batch_fetch_http_error_silently_skipped`
  keeps the count stable.
- Node 4 file: +13 net new (10 spec tests + 1 dangling-after-cap +
  1 failed-not-in-truncated + 1 determinism).

`uv run ruff check src/ tests/` clean. `ruff format` was not run, per
spec.

---

## Spec compliance self-check

Every contract bullet in the spec maps to at least one test:

**Node3Result invariants.**
- "Every edge endpoint corresponds to a paper in papers or a seed" →
  `test_node3_no_dangling_edges_after_cap`.
- "failed_batches is empty when all batch fetches succeeded" →
  `test_node3_full_success_empty_failed_batches`.
- "papers and edges are independently rankable and cappable" →
  `test_node3_no_dangling_edges_after_cap` (asserts post-cap state
  satisfies the invariant).

**Node4Result invariants.**
- "Every edge.source_id corresponds to a paper.node_id in papers" →
  `test_node4_no_dangling_edges_after_cap`.
- "Every edge.target_id corresponds to one of the input seeds" →
  `test_node4_emits_citer_to_seed_edges`.
- "failed_seeds is empty when all per-seed calls succeeded" →
  `test_node4_full_success_empty_failure_lists`.
- "truncated_seeds is empty when no seed exceeded the per-seed cap" →
  `test_node4_no_truncation_under_cap` and
  `test_node4_full_success_empty_failure_lists`.
- "A seed in failed_seeds does not appear in truncated_seeds" →
  `test_node4_failed_seed_not_in_truncated_seeds`.
- "papers does not contain any of the input seeds" → existing
  `test_seed_exclusion`.

**Other named contract bullets.**
- "Edges to seeds-themselves are emitted normally" → covered by
  `test_node3_emits_seed_to_depth1_edges` (includes the S1→S2 case where
  both are seeds).
- Sort/truncation coupling: `test_node4_truncation_recorded` and
  `test_node4_sort_passes_through_to_query` together demonstrate that
  `sort` and `truncated_seeds` work as a coupled pair (the same seed call
  surfaces both the deterministic ordering and the cap event).

---

## AMD-020 index discipline self-check

Per `workflow.md` Step 9 verification list ("If the PR introduces an AMD,
the index updates in `amendments.md` are present in the diff"):

- ✓ AMD-020 entry present in `amendments.md` after AMD-019.
- ✓ Three new rows in the Constraints Log table covering AMD-020.
- ✓ Two new rows in the Open Questions table covering AMD-020.
- ✓ Drift notes updated to acknowledge AMD-020 coverage.

---

## Deviations from the spec

None substantive. Two clarifications:

1. **Spec test #4 named test_node3_no_dangling_edges_after_cap.** The
   spec lists this test as `test_node3_no_dangling_edges` with a fetch-
   failure focus, but the implementation prompt's validation-step-4
   explicit name check requires `test_node3_no_dangling_edges_after_cap`
   (post-rank/cap focus). Implementation merges both concerns into the
   stronger invariant test under the prompt's name: "every edge endpoint
   is in papers ∪ seeds." Fetch-failure dropping is implicitly covered
   (the dropped-batch test verifies the depth-2 paper is absent from
   both papers and edges, which would have produced a dangling edge if
   emission ignored the fetch failure).

2. **Resolved spec open question 3.** Edges sorted by `(source_id,
   target_id)` before return per the spec author's lean. Same pattern
   as `compute_co_citations`'s output sort. The `test_node3_deterministic_same_input`
   and `test_node4_deterministic_same_input` tests rely on this ordering.

---

## Workflow Observations

- The `_StageFailingClient` fixture (call-count-based failure injection)
  was the cleanest way to exercise stage-specific failures without
  threading internal call-site state through the test boundary. The
  small-fixture assumption (each stage = 1 batch) is documented in the
  fixture's docstring.
- The `_CitesClient` fixture extension (optional `meta_count_by_seed`
  and `fail_seeds`) preserved backward compatibility with all 8 existing
  Node 4 tests — only the `sort=` kwarg addition was a mechanical
  surface change.
- BRIEFING.md modifications carried over from a prior session were
  stashed at session start (`stash@{0}: briefing-prior-session`). Per
  `workflow.md`, BRIEFING is updated at merge time, not session end.

---

## What's Next

Per the BRIEFING update queued for merge time:

1. Pipeline orchestrator implementation. AMD-020 closes the remaining
   pipeline-level gap; the orchestrator spec (currently in active
   design, lands in this PR with only the `sort` field finalized) can
   now resume design with all upstream contracts in place.
2. Pagination of Node 4 — the deferred follow-up flagged in this PR's
   Open Questions table. `truncated_seeds` makes this tractable.
3. Transport-level retry/backoff and OpenAlex client extraction —
   second deferred Open Question, scope is a dedicated transport
   refactor PR.

---

*Companion files: `docs/specs/spec-node3-node4-edge-emission.md`,
`docs/decisions/amendments.md` (AMD-020).*
