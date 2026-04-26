# Idiograph — Session Summary
**Date:** 2026-04-24
**Status:** DRAFT — design session, not yet frozen
**Session type:** Design (Node 6 — metric computation)
**Branch:** n/a (no code changes this session)

---

## Context

Entering session: main at `f9b884b`, 113 tests passing. PR #15 merged 2026-04-23
landed the revised CLAUDE.md and created BRIEFING.md as live-state doc. Per BRIEFING
"What's Next" section, the queued sequence is housekeeping (done, PR #15) →
Node 6 design → Node 6 implementation. This session is the Node 6 design pass.
No code was written.

The Node 5 design pattern is the model: claude.ai produces design conversation
+ session summary + spec; Claude Code consumes the spec for implementation.
Companion to the spec drafted alongside this summary at
`docs/specs/spec-node6-metrics.md`.

---

## Decisions Locked

| Decision | Verdict | Rationale |
|---|---|---|
| **`topological_depth` field** | Removed from `PaperRecord`. Replaced with two new fields. AMD-019 created. | The frozen spec's "longest path from root" definition has three problems: direction ambiguity (Node 4 traversal output has no forward path from seed), forest ambiguity (multiple roots, no canonical referent), and `nx.dag_longest_path_length` returns a graph-level scalar rather than a per-node depth. None of the four readings (forward-only, undirected, max-of-both, signed) survive scrutiny without compromise. |
| **Depth representation** | Per-root dict + categorical direction. `hop_depth_per_root: dict[str, int]` and `traversal_direction: Literal["seed", "backward", "forward", "mixed"]`. | Stores honest multi-dimensional truth. No invented sign convention, no arbitrary tiebreaker. Renderer projections (Y-axis coordinate, signed scalar, etc.) are view-layer concerns derived from these two fields on demand. The "mixed" category gives the between-seeds zone a first-class label rather than burying it in a sign collision. |
| **`"mixed"` direction precision** | Reaching roots disagree on direction; non-reaching roots don't vote. Truth table in spec algorithm section. | Pressed during design: three-seed graphs with partial reachability (Case C — node reachable forward from one root, backward from another, unreachable from a third) need a clean answer. The algorithm produces "mixed" correctly under the "reaching roots disagree" rule; spec language tightened from "different roots in different directions" to remove ambiguity. |
| **Function decomposition** | Two pure functions: `compute_depth_metrics` and `compute_pagerank`. Thin orchestrator at call site composes both. | Independent computations with no shared state. Each small, independently testable. Matches thesis pattern of explicit composition. PageRank is a single NetworkX call; depth metrics is per-root BFS over directed and undirected views. |
| **Output shape** | Each function returns `dict[node_id, X]`. Orchestrator merges into `PaperRecord` via `model_copy`. | Pure functions don't mutate inputs. Symmetric shape across producers (Node 6 today, Node 7 communities tomorrow) lets the orchestrator merge uniformly. Pydantic culture leans immutable. |
| **`DepthMetrics` as Pydantic model** | Yes — full Pydantic `BaseModel`, not `TypedDict` or plain dict. | (1) Validation at construction — Literal vocabulary enforced at boundary, not later. (2) Consistency with `models.py` (every other arxiv-domain structured value is a Pydantic model). (3) Forward composability with future per-node metric producers. The "transient type" objection is the same critique that could be made of `CycleCleanResult` and is overruled by the same reasoning. |
| **Suppressed-cycle nodes** | Receive normal metrics. No null special-casing. `cycle_log.affected_node_ids` becomes audit-only. | Under AMD-019, no node carries `topological_depth: null` because there is no `topological_depth` field. The cleaned DAG is BFS-traversable regardless of which cycle edges were suppressed. The Node 4.5 → Node 6 handoff that previously gated null assignment is no longer load-bearing; the property survives for provenance value to Node 8. |
| **Defensive checks for unknown-endpoint edges** | Solved upstream. `CycleCleanResult` gains a Pydantic `@model_validator` enforcing edge-endpoint membership in the input node set. Node 6 trusts the contract. | Per-node defensive checks would multiply paranoia across Nodes 7/8/9. The thesis-aligned pattern is "make illegal states unrepresentable" — Python's idiomatic version of this is a model validator that fails construction at the boundary, not a defensive check at every consumption site. Validator is a prerequisite to Node 6, not deferred. |
| **PageRank** | Single `nx.pagerank()` call. No bespoke logic. Damping default 0.85. | Spec already declared damping=0.85. NetworkX is deterministic given fixed input + fixed alpha. The function adds nodes before edges so isolates appear in the result. |
| **AMD-019 scope** | Supersedes `topological_depth` in `spec-arxiv-pipeline-final.md` §Node 6 + renderer data contract. AMD-017's "Downstream Metric Behavior" table needs a follow-up update. | Same pattern as PR #14 (Node 4.5 §Boundaries correction): the spec-amendment lives in the new node's spec, with cross-spec language updates landing as a follow-up doc PR after the implementation merges. |

---

## Prerequisite — `CycleCleanResult` Contract Enforcement

Mirrors the SuppressedEdge → Node 5 prerequisite story (PR #11 before PR #13).
Node 6 trusts the contract that every endpoint in `cleaned_edges` references
a node in the input node set. Today nothing actively guarantees this — the
contract is documented in `spec-node4.5-cycle-cleaning.md` but only Node 4.5's
own internal logic upholds it. A bug in `clean_cycles()` could silently produce
orphaned-endpoint edges, and Node 6 would either need a defensive check
(which Nodes 7/8/9 would also need, multiplying paranoia) or trust a contract
nothing enforces.

The fix: a Pydantic `@model_validator(mode='after')` on `CycleCleanResult`
that fails construction if any edge endpoint is absent from a stored witness
of the input node set. Once a `CycleCleanResult` exists, its invariant holds.
Every downstream consumer trusts the type and runs no per-consumer defensive
check. Python-idiomatic version of "make illegal states unrepresentable":
the type system (such as Python's is) does the work once at the boundary.

**Implementation shape (refined at prerequisite PR drafting):**
- `CycleCleanResult` gains private witness — `_input_node_ids: frozenset[str]`
  is the lightest sufficient form.
- `clean_cycles()` populates the witness from its `nodes` parameter.
- Validator iterates `cleaned_edges`, raises `ValidationError` on first
  orphan, naming it.
- Existing tests stay green (Node 4.5 already produces correct output).
- New tests verify the validator fires on direct construction with bad input.

**Sequencing:** lands as its own PR ("tighten CycleCleanResult contract:
enforce edge endpoint membership") before the Node 6 branch opens. Same
pattern as the SuppressedEdge refactor (PR #11) that preceded Node 5.
The Node 6 spec is written against the post-validator world.

**Architectural payoff beyond Node 6.** The validator pattern is reusable.
Node 7's communities will be computed from a `CycleCleanResult` (or a
successor type that adds Node 6's metrics) and Node 7 will trust the witness.
Node 8's registry will persist graphs whose constructed shape carries the
validator. The "stop the defensive checks from multiplying" architectural
goal solved at the type-boundary level, not per-node.

---

## Function Signatures (Final)

```python
def compute_depth_metrics(
    nodes: list[PaperRecord],
    cleaned_edges: list[CitationEdge],
) -> dict[str, DepthMetrics]:
    ...

def compute_pagerank(
    nodes: list[PaperRecord],
    cleaned_edges: list[CitationEdge],
    damping: float = 0.85,
) -> dict[str, float]:
    ...
```

Call-site assembly in the pipeline orchestrator (future work):

```python
result = clean_cycles(nodes, edges)
# (Node 5 runs separately on result.cleaned_edges + suppressed originals)
depth_metrics = compute_depth_metrics(nodes, result.cleaned_edges)
pagerank_scores = compute_pagerank(nodes, result.cleaned_edges, damping=0.85)

nodes_with_metrics = [
    n.model_copy(update={
        "hop_depth_per_root": depth_metrics[n.node_id].hop_depth_per_root,
        "traversal_direction": depth_metrics[n.node_id].traversal_direction,
        "pagerank": pagerank_scores[n.node_id],
    })
    for n in nodes
]
```

Downstream routing of Node 4.5 output:
- Node 5: `result.cleaned_edges + [s.original for s in result.cycle_log.suppressed_edges]`
- Node 6 (this spec): `result.cleaned_edges` only — needs the DAG
- Node 7 (future): `result.cleaned_edges` only — needs the DAG
- Node 8 (audit): `result.cycle_log` (and `affected_node_ids` for provenance)

---

## Design Principle Reinforced

Three thesis patterns recurred throughout this session.

**Graph is authority, renderer projects.** The signed-depth proposal almost
slipped a renderer concern (single Y-axis coordinate) into the data layer
(stored signed scalar). The fix was to recognize that "how to project depth
onto a one-dimensional axis" is a view question, not a data question, and
to store the richer truth (per-root dict + categorical direction) so the
renderer can project as it wishes. Same pattern as Node 5's `min_strength`
vs. `max_edges` separation in the design addendum — semantic filter
(data layer) vs. display cap (view layer) deserve separate parameters
even when the temptation is to collapse them.

**Make illegal states unrepresentable.** Question 2's defensive-check
problem (Node 6 doesn't trust upstream — but Nodes 7/8/9 inherit the
same problem) was solved by moving validation to the type boundary
rather than to every consumer. The Rust pattern translated to Python
via Pydantic model validators. Node 6 trusts `CycleCleanResult` because
the type system enforces the contract at construction. The pattern
scales to every downstream node.

**Explicit outputs over side channels.** Each Node 6 function produces
its own structured output. The orchestrator composes them via `model_copy`.
No mutation of `PaperRecord` in place, no shared state, no implicit
dataflow. Same pattern as Node 5 returning `list[CitationEdge]` and
Node 4.5 returning `CycleCleanResult` — every consumer gets a first-class
output from the producer that owns it.

---

## Open Items

| Item | Owner | When |
|---|---|---|
| `CycleCleanResult` validator (prerequisite) | next implementation session | Before Node 6 branch opens |
| Node 6 implementation | Claude Code session against frozen spec | After prerequisite merged |
| Cross-spec language updates (arxiv-pipeline-final renderer contract, node4.5 step-5 language, AMD-017 table) | follow-up doc PR | After Node 6 implementation merged — same pattern as PR #14 post-Node-5 |
| Orchestrator wiring (`run_arxiv_pipeline()`) | separate design session | After Node 7 lands at earliest — needs more producers to compose |
| Additional centrality metrics (betweenness, closeness) | future amendment | If renderer surfaces a need |

---

## Test Gate

No code changes this session. Baseline 113 passing, unchanged.

The Node 6 spec commits to 24 new tests (16 depth + 8 pagerank). Implementation
PR will land at expected baseline 137. Prerequisite PR adds tests for the
`CycleCleanResult` validator (small set — likely 3-5 tests verifying validator
fires on direct construction with bad input, passes on legitimate
`clean_cycles()` output).

---

## What's Next

1. **`CycleCleanResult` validator prerequisite PR** — small, mechanical, independent.
   First item. Lands before Node 6 branch opens.
2. **Node 6 implementation** — Claude Code session against the frozen spec.
   Two functions in `pipeline.py`, one new `DepthMetrics` model, two field
   changes on `PaperRecord`, 24 tests.
3. **Cross-spec language updates** — follow-up doc PR after Node 6 lands.
4. **Node 7 design session** — communities, Infomap with Leiden fallback.
   Independent of Node 6 in algorithm, but builds on the same `CycleCleanResult`
   trust pattern.

---

*Companion documents: `docs/specs/spec-node6-metrics.md` (drafted alongside this
summary), `docs/decisions/amendments.md` (AMD-019, defined in the spec).*
