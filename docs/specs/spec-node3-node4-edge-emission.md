# Spec: Node 3 / Node 4 Edge Emission and Failure Provenance
**Status:** ACTIVE — design complete, awaiting Claude Code audit and revisions
**Branch:** TBD
**Scope:** Extend `backward_traverse()` and `forward_traverse()` return types via `Node3Result` and `Node4Result` wrapper models. Emit `CitationEdge` records during traversal. Surface failure modes as structured provenance. Add required `sort` parameter to forward traversal.

---

## Purpose

Nodes 3 and 4 currently fetch reference data from OpenAlex during traversal but discard the relational structure at return time. Both functions return `list[PaperRecord]`. Downstream stages (cycle cleaning, co-citation, depth metrics, communities) all consume edge lists. Nothing in the current pipeline produces them.

This is a pipeline-level gap, not just an orchestrator-level inconvenience. The orchestrator (currently in design) cannot run end-to-end against the existing per-stage functions because no upstream stage produces the edge input that downstream stages require.

This precursor closes the gap by extending Node 3 and Node 4 to return wrapper models carrying both papers and the citation edges discovered during traversal. It also surfaces three failure modes currently invisible at return time: Node 3's silent batch-fetch degradation, Node 4's per-seed call failures, and Node 4's silent truncation at 200 citers.

The precursor must land before the pipeline orchestrator implementation. The orchestrator spec will be revised after this lands to consume the new return types.

---

## Why this is a precursor, not a deferred follow-up

The pipeline orchestrator design session locked the principle that downstream stages read from explicit, first-class outputs of upstream stages — never from log records, never from reaches into provenance, never reconstructed from data the upstream stage discarded. Edge discard at Nodes 3/4 is exactly the failure mode that principle was named to prevent.

This precursor follows the established pattern from PR #11 (`SuppressedEdge` composing `CitationEdge` before Node 5) and PR #22 (AMD-019 docs sweep before Node 7 work): a precursor PR lands the contract change, then the consumer builds on the merged precursor.

---

## Models

All new models live in `src/idiograph/domains/arxiv/models.py`, alongside the existing `PaperRecord`, `CitationEdge`, `CycleCleanResult`, `CommunityResult`, `DepthMetrics`, etc.

### Node 3 result

```python
class FailedBatch(BaseModel):
    requested_ids: list[str] = Field(
        ...,
        description="OpenAlex IDs requested in the failed batch (up to batch_size in length)"
    )
    stage: Literal["seed_refetch", "depth_1", "depth_2"] = Field(
        ...,
        description="Which traversal stage the batch belonged to"
    )
    reason: str = Field(
        ...,
        description="Failure description (e.g., 'http_error: 503', 'timeout')"
    )


class Node3Result(BaseModel):
    papers: list[PaperRecord] = Field(
        ...,
        description="Backward-traversal papers, ranked and capped"
    )
    edges: list[CitationEdge] = Field(
        ...,
        description=(
            "Citation edges discovered during traversal. Source cites target. "
            "Includes seed→depth-1 and depth-1→depth-2 edges. Edges are emitted "
            "only when both endpoints have full PaperRecord metadata; failures "
            "to fetch metadata are recorded in failed_batches instead."
        )
    )
    failed_batches: list[FailedBatch] = Field(
        default_factory=list,
        description=(
            "Batch-level fetch failures. Empty list when no batches failed. "
            "Each entry records up to batch_size OpenAlex IDs that were requested "
            "but not retrieved. Per-ID granularity is not available."
        )
    )
```

`stage` is a closed `Literal` of the three batch sites in current `backward_traverse` (per the investigation report). Adding a new traversal site is a deliberate schema change, not a silent extension.

### Node 4 result

```python
class FailedSeed(BaseModel):
    seed_id: str = Field(
        ...,
        description="The seed whose forward-traversal call failed"
    )
    reason: str = Field(
        ...,
        description="Failure description (e.g., 'http_error: 503')"
    )


class TruncatedSeed(BaseModel):
    seed_id: str = Field(
        ...,
        description="The seed whose forward-traversal hit the per-seed cap"
    )
    returned_count: int = Field(
        ...,
        description="Citers actually returned (currently capped at 200)"
    )
    total_count: int = Field(
        ...,
        description=(
            "Total citers reported by OpenAlex's response metadata. "
            "When returned_count < total_count, citers (total_count - returned_count) "
            "many were silently truncated."
        )
    )


class Node4Result(BaseModel):
    papers: list[PaperRecord] = Field(
        ...,
        description="Forward-traversal papers (citing papers), ranked and capped"
    )
    edges: list[CitationEdge] = Field(
        ...,
        description=(
            "Citation edges discovered during traversal. Source cites target. "
            "Direction is citer → seed. Edges are emitted only for papers in "
            "the returned `papers` list."
        )
    )
    failed_seeds: list[FailedSeed] = Field(
        default_factory=list,
        description=(
            "Seeds whose forward-traversal call raised. Empty list when no seeds failed. "
            "Distinct from succeeded-but-zero-citers seeds, which produce no entry."
        )
    )
    truncated_seeds: list[TruncatedSeed] = Field(
        default_factory=list,
        description=(
            "Seeds whose citer count exceeded the per-seed cap. "
            "Empty list when no seeds were truncated."
        )
    )
```

`Node4Result.failed_seeds` is structurally similar to the orchestrator's `StageFailure` but not identical. They live in different containers and record different aspects (one records what failed within a stage; the other records seeds that didn't survive Node 0). No shared base class — the parallel structure is intentional but the use cases are distinct.

### Forward parameters extension

Add a required `sort` field to existing `forward_traverse` parameter handling. The parameter does not currently exist on the function signature; this spec introduces it.

```python
ForwardSort = Literal[
    "cited_by_count:desc",
    "cited_by_count:asc",
    "publication_date:desc",
    "publication_date:asc",
]
```

The orchestrator's `ForwardParameters` model (defined in the orchestrator spec) gains a corresponding required `sort: ForwardSort` field with no default. Caller must specify.

---

## Behavior changes

### `backward_traverse()`

Existing signature continues to take seeds, client, kwargs as before — see Implementation Constraints. The function's body changes in three specific places:

**1. Edge construction at depth-1 walk site (currently `pipeline.py:259–276`).**

The `seed_to_depth1` mapping is already constructed during the walk. Replace the discard at function-return time with explicit edge emission. For each `(seed_id, depth_1_oa_id)` pair where both endpoints exist in the final returned papers (or are seeds), construct:

```python
CitationEdge(
    source_id=seed_node_id,
    target_id=depth_1_node_id,
    type="cites",
    citing_paper_year=seed.year,
    strength=None,
)
```

Edges where the depth-1 target failed to fetch (silent drop in current code) are not emitted. The failure is recorded in `failed_batches` instead.

**2. Edge construction at depth-2 walk site (currently `pipeline.py:279–291`).**

Symmetric to depth-1. The `depth1_to_depth2` mapping is already constructed. For each `(depth_1_id, depth_2_id)` pair where both endpoints exist in the final returned papers, construct:

```python
CitationEdge(
    source_id=depth_1_node_id,
    target_id=depth_2_node_id,
    type="cites",
    citing_paper_year=depth_1_paper.year,
    strength=None,
)
```

**3. Failure recording in `_fetch_works_by_ids` (currently `pipeline.py:218–223`).**

Replace the `_log.debug(...); continue` pattern with batch-level failure recording. The function gains a return that includes the failed-batch list, or accepts a mutable failed-batches accumulator. Implementation detail; the contract is that batch failures surface in the returned `Node3Result.failed_batches` rather than disappearing into DEBUG logs.

`stage` field is set to `"seed_refetch"`, `"depth_1"`, or `"depth_2"` depending on which call site triggered the batch.

### `forward_traverse()`

**1. Add `sort` parameter to OpenAlex query.**

Existing query construction at `pipeline.py:432–437`:

```python
params = {
    "filter": f"cites:{seed.openalex_id}",
    "select": _FORWARD_SELECT,
    "per-page": "200",
    "api_key": api_key,
}
```

Becomes:

```python
params = {
    "filter": f"cites:{seed.openalex_id}",
    "select": _FORWARD_SELECT,
    "per-page": "200",
    "sort": sort,
    "api_key": api_key,
}
```

`sort` is a new required keyword argument on `forward_traverse`, validated as one of the `ForwardSort` literal values. No default — caller must specify. The orchestrator passes it through from `ForwardParameters.sort`.

**2. Edge construction at per-seed result loop (currently `pipeline.py:445–456`).**

For each citing paper returned, construct:

```python
CitationEdge(
    source_id=citer_node_id,
    target_id=seed.node_id,
    type="cites",
    citing_paper_year=citer.year,
    strength=None,
)
```

**3. Failure recording at per-seed try/except (currently `pipeline.py:438–443`).**

Replace `_log.debug(...); continue` with `failed_seeds.append(FailedSeed(seed_id=seed.node_id, reason=str(e)))`. Continue iteration to remaining seeds.

**4. Truncation recording.**

OpenAlex's response includes `meta.count` indicating total citers available for the query. Compare against returned count; when returned < total, append `TruncatedSeed(seed_id=..., returned_count=..., total_count=...)` to the truncation list.

---

## Algorithm — Node 3

Existing algorithm structure preserved. Edge emission is additive at three sites; failure recording is additive at one site.

```
1. Re-fetch seeds for referenced_works (existing seed-refetch step)
   - On batch failure: append FailedBatch(stage="seed_refetch", ...) to failed_batches
2. Build seed_to_depth1 mapping (existing)
3. Fetch depth-1 papers in batches
   - On batch failure: append FailedBatch(stage="depth_1", ...) to failed_batches
4. Merge depth-1 papers into result (existing _merge call)
5. Emit edges for seed_to_depth1 pairs where target is in merged papers
6. Build depth1_to_depth2 mapping (existing)
7. Fetch depth-2 papers in batches
   - On batch failure: append FailedBatch(stage="depth_2", ...) to failed_batches
8. Merge depth-2 papers into result (existing _merge call)
9. Emit edges for depth1_to_depth2 pairs where target is in merged papers
10. Rank, cap, return Node3Result(papers, edges, failed_batches)
```

Edges to seeds-themselves (i.e., `target_id` is one of the input seeds) are emitted normally. The existing seed-skip in `_merge` (pipeline.py:298) prevents seeds from appearing in `papers`, but seeds are roots of the forest and their incoming/outgoing edges are still real.

---

## Algorithm — Node 4

Existing algorithm structure preserved. Sort parameter is additive; edge emission is additive; failure and truncation recording are additive.

```
1. Initialize failed_seeds, truncated_seeds, edges as empty lists
2. For each seed:
   a. Construct OpenAlex query with sort parameter
   b. Try to fetch:
      - On httpx.HTTPError: append FailedSeed, continue
   c. Compare meta.count to len(results):
      - If meta.count > len(results): append TruncatedSeed
   d. For each citing paper:
      - Skip if paper is itself a seed (existing behavior)
      - Add to merged papers (existing _merge logic)
      - Emit CitationEdge(citer → seed)
3. Rank, cap, return Node4Result(papers, edges, failed_seeds, truncated_seeds)
```

---

## Module location

- All new models (`Node3Result`, `Node4Result`, `FailedBatch`, `FailedSeed`, `TruncatedSeed`, `ForwardSort` literal): `src/idiograph/domains/arxiv/models.py`
- Behavior changes to `backward_traverse` and `forward_traverse`: `src/idiograph/domains/arxiv/pipeline.py`

No new modules.

---

## Logging

Existing log conventions preserved. Specifically:

- DEBUG-level logs that previously hid batch failures (Node 3) and per-seed failures (Node 4) become INFO-level acknowledgments that the failure occurred, with the structured record being the canonical surface. The structured record carries the detail; the log line is for live observability during a run.
- Truncation in Node 4 produces a new INFO log per truncated seed: `"Node 4: seed {seed_id} truncated — returned {returned}, total {total}"`.
- No changes to logger name (`idiograph.arxiv.pipeline`) or section header conventions.

---

## Contracts

### `Node3Result` invariants

- Every `edge.source_id` and `edge.target_id` corresponds to either a `paper.node_id` in `papers` or a seed's `node_id` (seeds are not in `papers` but are valid edge endpoints).
- `failed_batches` is empty when all batch fetches succeeded.
- `papers` and `edges` are independently rankable and cappable; the function returns the post-rank, post-cap state.

### `Node4Result` invariants

- Every `edge.source_id` corresponds to a `paper.node_id` in `papers`.
- Every `edge.target_id` corresponds to one of the input seeds.
- `failed_seeds` is empty when all per-seed calls succeeded.
- `truncated_seeds` is empty when no seed exceeded the per-seed cap.
- A seed appearing in `failed_seeds` does not appear in `truncated_seeds` (a seed that failed entirely cannot also be reported as truncated).
- `papers` does not contain any of the input seeds (existing behavior preserved).

### Failure and truncation are non-fatal

- `Node3Result` returns successfully even when `failed_batches` is fully populated (every batch failed). The caller decides whether the partial result is usable.
- `Node4Result` returns successfully even when `failed_seeds` covers every input seed. Same caller-decides contract.
- Halting on total failure is an orchestrator-level decision, not a Node 3/4 decision.

---

## Tests

Tests live in existing files (`tests/domains/arxiv/test_pipeline_node3.py`, `tests/domains/arxiv/test_pipeline_node4.py`). Inline `_rec` and `_edge` helpers in each file follow per-file convention.

### Node 3 tests (additive to existing)

1. `test_node3_result_returns_wrapper` — return type is `Node3Result`, not `list[PaperRecord]`. Existing tests asserting on `list[PaperRecord]` are updated to use `result.papers`.
2. `test_node3_emits_seed_to_depth1_edges` — for a small known graph, every seed→depth-1 pair appears in `result.edges` with correct `source_id`, `target_id`, `type="cites"`.
3. `test_node3_emits_depth1_to_depth2_edges` — symmetric for depth-1→depth-2.
4. `test_node3_no_dangling_edges` — edges to depth-1/depth-2 papers whose metadata failed to fetch are not in `result.edges`.
5. `test_node3_seed_refetch_failure_recorded` — when seed re-fetch batch fails, `failed_batches` contains `FailedBatch(stage="seed_refetch", ...)`.
6. `test_node3_depth_1_failure_recorded` — symmetric for depth-1 batch failure.
7. `test_node3_depth_2_failure_recorded` — symmetric for depth-2 batch failure.
8. `test_node3_failed_batches_carries_requested_ids` — `FailedBatch.requested_ids` matches the batch list at the failure site.
9. `test_node3_full_success_empty_failed_batches` — clean run: `failed_batches == []`.
10. `test_node3_edge_citing_paper_year_set` — `edge.citing_paper_year` matches the source paper's year.

### Node 4 tests (additive to existing)

1. `test_node4_result_returns_wrapper` — return type is `Node4Result`, not `list[PaperRecord]`. Existing tests updated.
2. `test_node4_emits_citer_to_seed_edges` — for each citing paper in `result.papers`, an edge `(citer → seed)` exists in `result.edges`.
3. `test_node4_failed_seed_recorded` — when a per-seed call raises, `failed_seeds` contains `FailedSeed(seed_id=..., reason=...)`.
4. `test_node4_failed_seed_distinguishable_from_zero_citers` — failed seed: in `failed_seeds`. Zero-citers seed: in neither `failed_seeds` nor `truncated_seeds`. Different observable shape.
5. `test_node4_truncation_recorded` — when `meta.count > len(results)`, `truncated_seeds` contains `TruncatedSeed(seed_id, returned_count, total_count)`.
6. `test_node4_no_truncation_under_cap` — when `meta.count <= len(results)`, `truncated_seeds == []`.
7. `test_node4_sort_parameter_required` — calling `forward_traverse` without `sort` raises (TypeError or equivalent).
8. `test_node4_sort_passes_through_to_query` — value of `sort` parameter appears in OpenAlex query params.
9. `test_node4_full_success_empty_failure_lists` — clean run: `failed_seeds == [] and truncated_seeds == []`.
10. `test_node4_edge_citing_paper_year_set` — `edge.citing_paper_year` matches the citer's year.

### Determinism (both nodes)

11. `test_node3_deterministic_same_input` — identical inputs produce identical `Node3Result` (modulo any wall-clock fields).
12. `test_node4_deterministic_same_input` — identical inputs produce identical `Node4Result`. Sort parameter is the determinism mechanism.

---

## Implementation Constraints

- Spec compliance is the contract. Deviations require explicit naming in the session summary.
- `ruff check`, not `ruff format`. Existing pipeline.py contents must not be reformatted.
- All new model fields use `Field(description="...")` per AMD-001.
- File reads/writes specifying `encoding="utf-8"` per existing project standard.
- Existing per-stage function signatures gain new return types but preserve all existing kwargs (`n_backward`, `lambda_decay`, etc.). The `sort` parameter on `forward_traverse` is the only new kwarg.
- Existing tests asserting on `list[PaperRecord]` return types are mechanically updated to use `result.papers`. Test count change is expected and counted.
- Section headers in `pipeline.py` match existing 78-char convention.

---

## Boundaries

**In scope:**
- New models in `models.py`
- Edge emission in `backward_traverse` and `forward_traverse`
- Failure and truncation provenance fields on result wrappers
- New `sort` parameter on `forward_traverse`
- Test additions and updates
- Spec file (this document) lands with the implementation PR
- AMD entry recording the gap and resolution

**Out of scope:**
- Pagination of Node 4 to overcome the 200-citer cap (deferred follow-up — see Deferred items)
- Transport-level retry/backoff in `_fetch_works_by_ids` and OpenAlex-call sites (audit's open question 5 — separate concern)
- Extracting OpenAlex transport into a separate `openalex_client.py` module (also separate concern)
- Pre-fetching `referenced_works` in Node 0 to avoid Node 3's seed re-fetch (efficiency optimization, separate AMD)
- Multi-key sort for `forward_traverse` (current `sort: Literal[...]` is single-key only)
- Changes to Node 0, Node 4.5, Node 5, Node 6, Node 7

---

## Open questions

1. **Failure handling in `_fetch_works_by_ids`'s call signature.** The function currently doesn't return failure information. Three options for surfacing it: (a) extend the return type to `tuple[list[dict], list[FailedBatch]]`, (b) accept a mutable accumulator parameter, (c) make the function class-based with state. Option (a) is cleanest but changes a multi-call-site function. Audit will surface implications.

2. **`stage` field on `FailedBatch`.** Closed Literal of three values matches current code structure. If `_fetch_works_by_ids` is called from new sites in the future (e.g., Node 4 batching, Phase 10 stages), the Literal needs extension. Acceptable — extension is a deliberate schema change.

3. **Edge emission ordering.** Should `Node3Result.edges` be sorted, or returned in discovery order? Discovery order is cheaper but non-deterministic across OpenAlex response variations (which the `sort` parameter prevents at fetch time but doesn't enforce at edge-emission time). My lean: sort by `(source_id, target_id)` before return — same pattern as `compute_co_citations`'s output sort.

---

## Freeze trigger

Spec freezes when:
1. Audit findings have been folded back as spec revisions
2. Open questions 1 and 3 have been resolved
3. The implementation PR opens

Per Node 5/6/7 pattern, the spec lands with the implementation PR rather than as a separate docs PR.

---

## Deferred items

These are tracked here so they're not lost when the precursor lands:

- **Node 4 pagination.** Once `sort` lands and truncation is honestly recorded, pagination is the coverage improvement that eliminates the cap entirely. Will need its own AMD, spec, and PR. The `truncated_seeds` field on `Node4Result` is the artifact that makes this future work observable.
- **Transport-level retry/backoff.** Audit's open question 5. Currently no retry on any OpenAlex call site. Separate from edge emission; tangled with the code-organization concern that OpenAlex transport lives directly in `pipeline.py` rather than a separate client module.
- **AMD-019 migration to `amendments.md`.** AMD-019 currently lives in `spec-node6-metrics.md`. Doc-org cleanup. Not blocking.
- **Pre-fetching `referenced_works` in Node 0.** Currently `_WORK_SELECT` excludes it, forcing Node 3 to re-fetch seeds at entry. ~1 extra OpenAlex call per seed; small efficiency win, separate AMD.

---

*Companion: `spec-arxiv-pipeline-final.md` (frozen pipeline architecture), `spec-pipeline-orchestrator.md` (consumer of these wrapper types — currently in design), `amendments.md` (architectural decisions).*
