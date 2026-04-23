# Idiograph — Session Summary
**Date:** 2026-04-21
**Status:** DRAFT — design session, not yet frozen
**Session type:** Design (Node 5 — co-citation)
**Branch:** n/a (no code changes this session)

---

## Context

Entering session: main at `11541ad`, 93 tests passing. Session 2026-04-17 closed the
post-Node-4.5 housekeeping batch via PR #9. Priority 1 per that summary was a
design-first session for Node 5 (co-citation). This session is that design pass. No
code was written.

---

## Decisions Locked

| Decision | Verdict | Rationale |
|---|---|---|
| **Input source for Node 5** | Full `cites` edge set (cleaned ∪ suppressed), merged at call site | Cycle suppression is a NetworkX-API accommodation for `dag_longest_path_length`, not a statement that the citations didn't happen. Co-citation is not directional — if C cites both A and B, that is a fact regardless of whether C→A got suppressed to break some other cycle. Using the cleaned graph would silently drop real co-citation signal for a downstream formatting concern. |
| **Merge location** | Pipeline orchestrator (call site), not inside Node 5 | Node 5 stays ignorant of Node 4.5. Its signature takes a node list and a citation edge list — whether those edges came from cleaning or not is not its concern. Domain-pure. |
| **Weighting function** | Raw integer count of shared citing papers | Spec-committed. Normalization (Jaccard etc.) would replace an audit-inspectable fact with a score. `strength=5` should mean "5 papers in this graph cite both endpoints," not a ratio. |
| **`co_citation_min_strength` default** | **2** | Strength 1 is a single shared citer — noise-adjacent. Strength 2 requires two independent confirmations. Revised empirically in seed pair validation spikes; 2 is a defensible starting floor, not arbitrary. |
| **`N_max_co_citation` default** | Tied to graph size — starting proposal `2 * (N_backward + N_forward)` | Co-citation graph is denser than citation graph but should not be pathologically so. Locks after seed pair validation. |
| **Edge directionality** | Symmetric — emit one edge per unordered pair with `source_id < target_id` lexicographic as canonical form | Co-citation is inherently undirected. Halves edge count, renderer already treats as undirected, ordering is deterministic. Document in the Node 5 spec. |
| **Forest semantics (AMD-017)** | Global across the whole assembled graph — ignore root boundaries | CRISPR use case: Doudna 2012 and Zhang 2013 seeds are in different root subtrees. A shared ancestor cited by both lineages is the structural overlap AMD-017 was designed to expose. Within-root-only would destroy that signal. |
| **Architectural placement** | Function in `src/idiograph/domains/arxiv/pipeline.py`, same pattern as `forward_traverse()` | Pipeline-stage node, not an executor handler. Pure function — no I/O — so tests use synthetic graph fixtures, no HTTP mocking. |
| **Co-citing paper identities on edges** | Deferred — store integer `strength` only | The list of supporting papers is reconstructable from the graph at query time by intersecting incoming citers. No need to denormalize into the edge. |
| **Node 4.5 output shape** | Already adequate — `CycleCleanResult` exposes `cleaned_edges` and `cycle_log.suppressed_edges` as structured data | Suppressed edges are first-class `SuppressedEdge` records, not just log lines. Node 5 can read them directly. |

---

## Prerequisite Refactor — `SuppressedEdge` Data Loss

Current `SuppressedEdge` shape:

```python
class SuppressedEdge:
    source_id: str
    target_id: str
    citation_sum: int       # forensic
    cycle_members: list[str]  # forensic
```

Compared to `CitationEdge`:

```python
class CitationEdge:
    source_id: str
    target_id: str
    type: str
    citing_paper_year: int | None
    strength: int | None
```

**Problem:** converting a `CitationEdge` → `SuppressedEdge` silently drops
`citing_paper_year`. Node 5 does not need that field (co-citation is pure topology),
but the loss is structural, not convenience-specific. Any future consumer that wants
to un-suppress, re-analyze, or audit the original edge cannot recover it.

**Proposed fix:** `SuppressedEdge` composes `CitationEdge` instead of duplicating its
fields:

```python
class SuppressedEdge(BaseModel):
    original: CitationEdge       # full edge, no data loss
    citation_sum: int            # forensic — why this edge, not another
    cycle_members: list[str]     # forensic — cycle this broke
```

**Scope:**

- `clean_cycles()` external signature unchanged
- Field-access in existing tests becomes `suppressed.original.source_id` rather than
  `suppressed.source_id` — mechanical
- Not a Node 5 concern per se — a Node 4.5 correctness concern that Node 5 is simply
  the first consumer to expose
- Free to fix now, painful to fix later

**Sequencing:** prerequisite to Node 5 implementation. Can ship as a separate PR
before the Node 5 branch opens — reads cleanly in history as "fix SuppressedEdge
data loss" rather than being bundled into a larger change.

---

## Node 5 Signature (Proposed)

Final signature pending anti-drift spec but this is the target shape:

```python
def compute_co_citations(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],   # full set — cleaned ∪ suppressed
    min_strength: int = 2,
    max_edges: int | None = None,
) -> list[CitationEdge]:               # only co_citation edges
```

Call-site assembly in the pipeline orchestrator:

```python
result = clean_cycles(nodes, edges)
all_cites = result.cleaned_edges + [
    s.original for s in result.cycle_log.suppressed_edges
]
co_edges = compute_co_citations(nodes, all_cites, min_strength=2)
```

Downstream routing from `CycleCleanResult`:

- Node 5: `result.cleaned_edges + suppressed originals` (the union)
- Node 6 (metrics): `result.cleaned_edges` only — needs a DAG
- Node 7 (communities): `result.cleaned_edges` only — needs a DAG
- Node 8 (provenance/audit): `result.cycle_log`

---

## Design Principle Reinforced

The user's framing during the session reset the architectural approach correctly.
Original sketch had Node 5 reaching into Node 4.5's provenance to reconstruct the
full edge set — implicit dataflow, back-channel access. The correct pattern — one
Ryan identified by analogy to DCC split-nodes (Nuke, Katana) — is explicit outputs
with downstream nodes connecting to the one they need. Node 4.5 already produces
this shape; the only gap was `SuppressedEdge`'s lossy field list.

This is the thesis in microcosm: the graph is the single source of truth, dataflow
is explicit, and nothing hides in a side channel. When a design accidentally routes
state through a log or a provenance write, the fix is to promote that data to a
first-class output — not to normalize reaching into logs.

---

## Open Items

| Item | Owner | When |
|---|---|---|
| `SuppressedEdge` refactor (prerequisite) | next implementation session | Before Node 5 spec |
| Node 5 anti-drift spec (`docs/specs/spec-node5-co-citation.md`) | next design-to-spec session | After refactor merged |
| `N_max_co_citation` default validation | Seed pair validation spikes | After Node 5 lands |
| `co_citation_min_strength` revisit | Seed pair validation spikes | After Node 5 lands |

---

## Test Gate

No code changes this session. Baseline 93 passing, unchanged.

---

## What's Next

1. **`SuppressedEdge` refactor PR** — small, mechanical, independent. First item.
2. **Node 5 anti-drift spec** — captures the decisions above plus signature,
   algorithm, deduplication rules, test plan. Written before any Node 5 code.
3. **Node 5 implementation** — Claude Code session against the frozen spec.
4. Essay editing pass — still queued behind Node 5.
5. Seed pair validation spikes — after Node 5 provides something to validate against.

---

*Companion documents: spec-arxiv-pipeline-final.md, spec-node4.5-cycle-cleaning.md,
session-2026-04-17.md, amendments.md (AMD-017 for forest semantics)*
