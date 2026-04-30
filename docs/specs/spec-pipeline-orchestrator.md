# Spec: Pipeline Orchestrator
**Status:** ACTIVE — design complete, awaiting Claude Code audit and revisions
**Branch:** TBD
**Scope:** New function `run_arxiv_pipeline()` composing Node 0 → (3, 4) → 4.5 → 5 → 6 → 7. New models in `models.py`. Forest assembly helper. No changes to existing per-stage function signatures.

---

## Purpose

Compose the per-stage pipeline functions into a single end-to-end call that produces a fully attributed citation graph from a list of seed identifiers. The orchestrator is the input boundary for Node 8 (registry persistence) — its return type is what Node 8 will store and reload.

The orchestrator does not perform domain computation. Every domain operation is delegated to an existing per-stage function. The orchestrator's responsibility is composition: forest assembly, dataflow between stages, per-seed failure handling, end-of-pipeline merge, and provenance assembly.

---

## Function signature

```python
def run_arxiv_pipeline(
    seeds: list[str],
    parameters: PipelineParameters,
) -> PipelineResult: ...
```

`seeds` is a list of seed identifiers (arXiv IDs or DOIs, accepted as strings — Node 0 handles resolution). Single-seed runs pass a one-element list; no separate single-seed entry point.

`parameters` carries per-stage configuration as nested model objects. See §Parameters.

Return is a `PipelineResult` carrying the merged graph, all per-stage results, the input parameters, and structured provenance. See §Return shape.

---

## Constraints from Node 8

The orchestrator's return shape is the input contract for Node 8. Two constraints follow:

**Round-trippability.** `PipelineResult` must survive `model_dump()` followed by `model_validate()` without information loss. The `CycleCleanResult` embedded in `PipelineResult` already requires `input_node_ids` to be re-supplied on reload (the witness pattern from PR #16); the orchestrator inherits this contract. Node 8's load path is responsible for reconstructing `input_node_ids` from the loaded node list before constructing `CycleCleanResult`.

**Provenance sufficiency.** A reviewer reading a stored `PipelineResult` should be able to answer "what produced this graph?" from the result alone, without consulting external logs. All per-stage failures, missing-node warnings, parameter values, and validation flags are first-class fields on the result.

How Node 8 persists, what the cache key looks like, what the lookup API is — out of scope for this spec.

---

## Models

All new models live in `src/idiograph/domains/arxiv/models.py`, alongside the existing `DepthMetrics`, `CycleCleanResult`, `CommunityResult`, `PaperRecord`, `CitationEdge`, etc.

### Per-stage parameter models

```python
class BackwardParameters(BaseModel):
    n: int = Field(..., description="Cap on backward traversal results (top-N by score)")
    lambda_recency: float = Field(..., description="Recency decay rate in recency_weight")

class ForwardParameters(BaseModel):
    n: int = Field(..., description="Cap on forward traversal results (top-N by score)")
    lambda_recency: float = Field(..., description="Recency decay rate in recency_weight")
    alpha: float = Field(..., description="Weight on citation_velocity in forward ranking")
    beta: float = Field(..., description="Weight on citation_acceleration in forward ranking")
    sort: ForwardSort = Field(..., description="OpenAlex sort order for the per-seed citing-paper query. Required: OpenAlex's default sort is not contractual and produces nondeterministic results. See AMD-020.")

class CoCitationParameters(BaseModel):
    min_strength: int = Field(2, description="Minimum shared citing papers to emit a co-citation edge")
    max_edges: int | None = Field(None, description="Hard cap on total co-citation edges; None = no cap")

class PageRankParameters(BaseModel):
    damping: float = Field(0.85, description="PageRank damping factor")

class CommunitiesParameters(BaseModel):
    infomap_seed: int = Field(42, description="Random seed for Infomap")
    infomap_trials: int = Field(10, description="Number of Infomap optimization trials")
    infomap_teleportation: float = Field(0.15, description="Teleportation probability for Infomap")
    leiden_seed: int = Field(42, description="Random seed for Leiden fallback")
    community_count_min: int = Field(5, description="Below-threshold flag for LOD validation")
    community_count_max: int = Field(40, description="Above-threshold flag for LOD validation")
```

`BackwardParameters` and `ForwardParameters` have no defaults — λ, α, β, N are explicitly TBD-pending-validation per the frozen pipeline spec. Caller must supply.

`CoCitationParameters`, `PageRankParameters`, and `CommunitiesParameters` carry sensible defaults from the frozen per-node specs.

Cycle cleaning and depth metrics have no parameters currently — no model needed.

### Container model

```python
class PipelineParameters(BaseModel):
    backward: BackwardParameters
    forward: ForwardParameters
    co_citation: CoCitationParameters = CoCitationParameters()
    pagerank: PageRankParameters = PageRankParameters()
    communities: CommunitiesParameters = CommunitiesParameters()
```

`backward` and `forward` are required; the rest default. Frozen Pydantic config (immutable after construction).

### Failure provenance

```python
class StageFailure(BaseModel):
    seed_id: str = Field(..., description="The seed whose per-seed call raised")
    stage: Literal["node_0_resolve", "node_3_backward", "node_4_forward"]
    exception_type: str = Field(..., description="The exception class name")
    exception_message: str = Field(..., description="The exception's str() representation")
```

Whole-graph stages (Node 4.5, 5, 6, 7) do not produce `StageFailure` records — they either succeed or halt the pipeline.

### Result

```python
class PipelineResult(BaseModel):
    # Primary surface — the merged graph
    nodes: list[PaperRecord] = Field(..., description="Fully enriched node set")
    edges: list[CitationEdge] = Field(..., description="All edges (cites + co_citation), post-cycle-cleaning")
    seeds: list[str] = Field(..., description="Successfully resolved seed node_ids")

    # Per-stage results — audit and replay
    cycle_clean: CycleCleanResult
    co_citation_edges: list[CitationEdge] = Field(..., description="Co-citation subset of `edges`, called out for clarity")
    depth_metrics: dict[str, DepthMetrics] = Field(..., description="Keyed by node_id")
    pagerank: dict[str, float] = Field(..., description="Keyed by node_id")
    communities: CommunityResult

    # Provenance
    parameters: PipelineParameters
    stage_failures: list[StageFailure] = Field(default_factory=list)
```

The merged-view fields (`nodes`, `edges`) and the per-stage result fields carry duplicate information by design — this is the cost of the explicit-outputs principle (per Node 5 design addendum, "explicit, separate outputs for each consumer's needs, no implicit dataflow"). Frozen-model enforcement prevents drift between the views.

`co_citation_edges` is a subset of `edges`. Surfacing it as a first-class field lets consumers query co-citation directly without filtering. The duplication is intentional and named.

---

## Forest assembly

`assemble_forest()` is a private helper in `pipeline.py`. Constructs the unified node and edge sets from per-seed Node 3 and Node 4 results.

### Algorithm

1. Initialize `node_buckets: dict[str, tuple[PaperRecord, set[str]]]` keyed by `node_id`. Value is `(first-seen PaperRecord, set of root_ids that reached it)`.
2. Initialize `edge_buckets: dict[tuple[str, str, str], CitationEdge]` keyed by `(source_id, target_id, type)`.
3. For each successfully resolved seed: insert it into `node_buckets` with its own node_id as the only root. Seeds are roots of the forest.
4. For each `(root_id, papers)` from `backward_per_root`: for each paper, insert into `node_buckets` (creating bucket if absent) and add `root_id` to its set.
5. Same for `forward_per_root`.
6. For edges from Node 3 and Node 4: for each edge, insert into `edge_buckets` if absent. If present, verify metadata equality with the existing entry; on mismatch, emit a structured warning (see §Open question 3 — auditability extensions).
7. Materialize: produce `unified_nodes: list[PaperRecord]` by iterating buckets, calling `model_copy(update={"root_ids": sorted(roots)})` once per unique node.
8. Materialize: produce `unified_cites: list[CitationEdge]` by iterating `edge_buckets.values()`.

### Performance contract

Bucket-then-reduce. One `model_copy` per unique node, never per encounter. Hash-based dedup, never O(N²) existence checks. Pattern mirrors `clean_cycles()`'s existing `edge_by_pair` lookup.

### Hop depth

Forest assembly does not track or carry hop depth. Node 3's internal hop tracking is for ranking purposes only and does not surface as a `PaperRecord` field. The canonical `hop_depth_per_root` is set by Node 6's `compute_depth_metrics()` over the cleaned graph.

---

## Orchestrator algorithm

```
1. Resolve seeds
   - Call Node 0 once per seed (or in batch if Node 0 supports it — check)
   - Failed resolutions become StageFailure(stage="node_0_resolve") records
   - If zero seeds resolve: raise PipelineError("no seeds resolved")

2. Per-seed traversal (Node 3 and Node 4)
   - For each resolved seed:
     - Call backward_traverse(seed, **parameters.backward.model_dump())
       - On raise: append StageFailure(seed_id=..., stage="node_3_backward", ...)
       - On success: store in backward_per_root[seed.node_id]
     - Call forward_traverse(seed, **parameters.forward.model_dump())
       - On raise: append StageFailure(seed_id=..., stage="node_4_forward", ...)
       - On success: store in forward_per_root[seed.node_id]
   - If every seed has both backward and forward failure: raise PipelineError("no graph assembled")

3. Forest assembly
   - unified_nodes, unified_cites = assemble_forest(seeds, backward_per_root, forward_per_root)

4. Whole-graph stages (each raises propagate; orchestrator does not catch)
   - cycle_clean = clean_cycles(unified_nodes, unified_cites)
   - all_cites = cycle_clean.cleaned_edges + [s.original for s in cycle_clean.cycle_log.suppressed_edges]
   - co_cit = compute_co_citations(unified_nodes, all_cites, **parameters.co_citation.model_dump())
   - depth = compute_depth_metrics(unified_nodes, cycle_clean.cleaned_edges)
   - prank = compute_pagerank(unified_nodes, cycle_clean.cleaned_edges, **parameters.pagerank.model_dump())
   - communities = detect_communities(unified_nodes, all_cites, **parameters.communities.model_dump())

5. End-of-pipeline merge
   - For each node in unified_nodes:
     enriched = node.model_copy(update={
         "traversal_direction": depth[node.node_id].traversal_direction,
         "hop_depth_per_root": depth[node.node_id].hop_depth_per_root,
         "pagerank": prank[node.node_id],
         "community_id": communities.community_assignments[node.node_id],
     })

6. Edge assembly for the merged view
   - merged_edges = cycle_clean.cleaned_edges + co_cit
   - (Suppressed originals are NOT included in the merged `edges` field — they live in cycle_clean.cycle_log.suppressed_edges for audit)

7. Construct and return PipelineResult
```

### Halt conditions

The orchestrator raises (does not return a partial `PipelineResult`) when:
- All Node 0 resolutions fail (no roots)
- All per-seed Node 3 calls fail *and* all per-seed Node 4 calls fail (no graph assembled)
- Any whole-graph stage raises (Node 4.5, 5, 6, 7)
- Node 7 raises `RuntimeError` due to missing `[community]` extra (configuration gap, propagate)

### Continue conditions

The orchestrator continues (records `StageFailure` and proceeds) when:
- Some seeds fail Node 0 resolution but at least one succeeds
- Some per-seed Node 3 or Node 4 calls fail but enough succeed to assemble a forest
- Per-seed empty results (not failures — the seed remains a root, the branch is empty)

### Retry policy

The orchestrator does not retry. Retry-with-backoff for transient OpenAlex errors is the responsibility of the OpenAlex client below the orchestrator boundary. See §Open question 5 — confirm the client implements this.

---

## Module location

- `PipelineResult`, `PipelineParameters`, all per-stage parameter models, `StageFailure`: `src/idiograph/domains/arxiv/models.py`
- `run_arxiv_pipeline()`, `assemble_forest()`, `PipelineError`: `src/idiograph/domains/arxiv/pipeline.py`

Consistent with existing organization (`CycleCleanResult`, `CommunityResult`, `DepthMetrics` already in `models.py`; per-stage functions already in `pipeline.py`).

If `pipeline.py` grows uncomfortable later, the natural split is per-stage functions stay, orchestrator and forest assembly move to `orchestrator.py`. Refactor for then, not a structure decision for now.

---

## Logging

Per Node 4.5/5/6/7 convention (`Node N: ...` / `Node N <subsystem>: ...`):

- INFO at orchestrator start: `"Pipeline: starting run with {len(seeds)} seeds"`
- INFO at each stage start: `"Pipeline: starting {stage_name}"`
- INFO at each stage completion: `"Pipeline: {stage_name} complete"`
- WARNING on per-seed Node 0/3/4 failure: `"Pipeline: {stage_name} failed for seed {seed_id}: {exception}"` (also recorded as `StageFailure`)
- INFO at orchestrator completion: `"Pipeline: complete — {len(nodes)} nodes, {len(edges)} edges, {len(stage_failures)} per-seed failures"`
- ERROR on halt: appropriate message before raising

Logger name: `idiograph.arxiv.pipeline` (existing convention).

---

## Contracts

**Return invariants (when orchestrator succeeds):**
- `len(nodes) >= len(seeds)` — every successfully resolved seed is in `nodes`
- Every `node.node_id` in `nodes` appears as a key in `depth_metrics`, `pagerank`, and `communities.community_assignments`
- Every `node.community_id` matches `communities.community_assignments[node.node_id]`
- Every `node.pagerank` matches `pagerank[node.node_id]`
- `co_citation_edges` is a subset of `edges` (specifically: `[e for e in edges if e.type == "co_citation"]`)
- `cycle_clean.cleaned_edges` is a subset of `edges` (specifically: `[e for e in edges if e.type == "cites"]`)
- `parameters` is exactly the `PipelineParameters` instance passed in
- `stage_failures` is empty when no per-seed failures occurred

**Failure contract:**
- Per-seed failures recorded in `stage_failures`, never silently absorbed
- Whole-graph stage exceptions propagate; orchestrator does not catch
- Orchestrator never returns an empty `nodes` list (halts with `PipelineError` instead)

**Input validation:**
- `seeds` empty: raise `ValueError("seeds must be non-empty")` before any work
- `parameters` is a `PipelineParameters` instance (Pydantic validates this at the type boundary)

---

## Tests

Tests live in `tests/domains/arxiv/test_pipeline_orchestrator.py`. Helpers `_rec` and `_edge` follow the Node 5/6/7 convention.

### Happy path

1. `test_single_seed_minimal_graph` — one seed, both Node 3 and Node 4 return small results, full pipeline runs to completion. Verify return invariants.
2. `test_multi_seed_disjoint_neighborhoods` — two seeds with no shared papers. Verify each node has exactly one root_id; forest is two disconnected components.
3. `test_multi_seed_overlapping_neighborhoods` — two seeds whose backward neighborhoods share at least one paper. Verify the shared paper has `root_ids` containing both seed ids.
4. `test_seeds_appear_in_nodes` — every successfully resolved seed appears in `nodes` with itself in its `root_ids`.

### Forest assembly

5. `test_assemble_forest_dedup_node` — same paper appears in multiple per-seed lists, ends up as one node with merged `root_ids`.
6. `test_assemble_forest_dedup_edge` — same edge appears in multiple per-seed lists, ends up as one edge.
7. `test_assemble_forest_edge_metadata_consistency` — same edge from two seeds has identical metadata, no warning raised.
8. `test_assemble_forest_edge_metadata_mismatch` — same edge from two seeds has different metadata; behavior depends on §Open question 3 resolution.

### Per-seed failure handling

9. `test_node_0_partial_failure` — two seeds, one resolves and one raises. Pipeline continues with the resolved seed; `stage_failures` contains one `StageFailure(stage="node_0_resolve")`.
10. `test_node_0_total_failure` — all seeds fail to resolve. Pipeline raises `PipelineError`.
11. `test_node_3_partial_failure` — Node 3 raises for one seed but succeeds for another. Pipeline continues; `stage_failures` contains one `StageFailure(stage="node_3_backward")`. Failed seed is still a root in the assembled forest.
12. `test_node_4_partial_failure` — symmetric to test 11 for Node 4.
13. `test_node_3_and_4_total_failure` — all per-seed Node 3 and Node 4 calls fail. Pipeline raises `PipelineError`.
14. `test_partial_node_3_does_not_halt_when_node_4_succeeds` — every seed's Node 3 fails, but Node 4 succeeds for at least one. Pipeline continues.

### Empty results vs failures

15. `test_node_3_empty_result_not_failure` — Node 3 returns `[]` for a seed. Not a failure; no `StageFailure` recorded; seed remains a root.
16. `test_node_4_empty_result_not_failure` — symmetric for Node 4.

### End-of-pipeline merge

17. `test_enrichment_pagerank_matches_per_stage` — for each node, `node.pagerank == pagerank[node.node_id]`.
18. `test_enrichment_community_matches_per_stage` — symmetric for `community_id`.
19. `test_enrichment_depth_matches_per_stage` — symmetric for `traversal_direction` and `hop_depth_per_root`.

### Whole-graph stage failures

20. `test_node_4_5_failure_propagates` — Node 4.5 raises; orchestrator does not catch; no `PipelineResult` returned.
21. `test_node_7_missing_extra_propagates` — Node 7 raises `RuntimeError` due to missing `[community]`; orchestrator does not catch.

### Round-trip

22. `test_pipeline_result_round_trip` — `PipelineResult` survives `model_dump()` → `model_validate()` (with Node 8's `input_node_ids` reconstruction logic applied to `cycle_clean`). All fields preserved.

### Input validation

23. `test_empty_seeds_raises` — `seeds=[]` raises `ValueError` before any work.

### Determinism

24. `test_deterministic_same_input` — running the same `(seeds, parameters)` twice produces identical `PipelineResult` (modulo any wall-clock fields if added). Run via `pytest --count=3`.

---

## Implementation Constraints

- Spec compliance is the contract. Deviations require explicit naming in the session summary.
- `ruff check`, not `ruff format`. Existing pipeline.py contents must not be reformatted.
- All new model fields use `Field(description="...")` per AMD-001.
- File reads/writes specifying `encoding="utf-8"` per existing project standard. (No new file I/O expected, but flagging.)
- Existing per-stage function signatures are NOT modified by this PR. The orchestrator unpacks parameter objects to kwargs at call sites.
- Frozen-model config on all new models (`model_config = ConfigDict(frozen=True)` or equivalent — match existing convention in `models.py`).
- Section header in `pipeline.py` matches existing 78-char convention.

---

## Boundaries

**In scope:**
- New models in `models.py`
- `run_arxiv_pipeline()` and `assemble_forest()` in `pipeline.py`
- `PipelineError` exception class (location TBD — `pipeline.py` is fine)
- Test file `test_pipeline_orchestrator.py`
- Spec file (this document) lands with the implementation PR per Node 5/6/7 pattern

**Out of scope:**
- Migration of existing per-stage function signatures to take parameter objects (open question 4 — separate cleanup)
- Node 5 / Node 7 return-type extensions for missing-node warnings (open question 2 — separate precursor or follow-up)
- Node 8 persistence implementation
- MCP tool surface for the orchestrator
- CLI integration of the orchestrator
- Changes to the frozen pipeline spec or per-node specs

---

## Open questions

1. **AMD-019 verification.** Inference that Node 6 owns canonical `hop_depth_per_root` (computed post-cleaning) and that `traversal_direction` is the AMD-019-introduced field needs verification against the actual amendment text before spec freezes.

2. **Node 5/Node 7 auditability extensions.** The orchestrator's `stage_failures` field promotes Node 3/4 raised exceptions to structured provenance. By the same contract, Node 5's missing-node warnings and Node 7's missing-node warnings (currently log-only) should be structured fields on their respective result objects. If accepted, this requires return-type extensions to `compute_co_citations` (returns `Node5Result(edges, warnings)` instead of bare `list[CitationEdge]`) and `CommunityResult` (adds `warnings: list[str]` field, distinct from existing `validation_flags`). Both are frozen-spec changes. Sequencing options: precursor PRs / bundled / deferred. Decide before orchestrator implementation begins.

3. **`assemble_forest` edge metadata mismatch behavior.** Three options: (a) silent last-write-wins, (b) silent first-write-wins, (c) structured warning recorded on `PipelineResult`. Option (c) requires a new field on `PipelineResult` (e.g., `data_integrity_warnings: list[...]`) and is the auditability-consistent choice. Resolution depends on open question 2 — same shape, same sequencing.

4. **Stage function signature migration.** Existing per-stage functions take individual kwargs; eventual migration to take parameter objects directly is consistency cleanup, not a contract fix. Schedule TBD; not blocking this PR.

5. **OpenAlex client retry policy.** The orchestrator assumes transient retry happens below its boundary. Worth confirming against existing client code at audit time.

6. **Node 8 persistence-split.** `PipelineResult`'s in-memory duplication may split into separately-addressable artifacts at persistence time. Deferred to Node 8 design.

---

## Freeze trigger

Spec freezes when:
1. Audit findings have been folded back as spec revisions
2. Open questions 1, 2, 3, and 5 have been resolved (at least to the extent of "yes, these are spec changes" or "no, deferred")
3. The implementation PR opens

Per Node 5/6/7 pattern, the spec lands with the implementation PR rather than as a separate docs PR.

---

*Companion: `spec-arxiv-pipeline-final.md` (frozen pipeline architecture), `spec-node4.5-cycle-cleaning.md` through `spec-node7-community-detection.md` (per-node contracts), `amendments.md` (architectural decisions).*
