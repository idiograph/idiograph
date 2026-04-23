# Idiograph — Session Summary Addendum
**Date:** 2026-04-21
**Status:** DRAFT — design addendum to session-2026-04-21-node5-design.md
**Session type:** Design (Node 5 — co-citation, continued)
**Branch:** n/a (no code changes)

---

## Context

Extension of the 2026-04-21 Node 5 design session. The primary session summary
closed the major architectural calls (input source, merge location, weighting,
forest semantics, placement, `SuppressedEdge` refactor). Four smaller items were
flagged as worth pressing before the anti-drift spec freezes. This addendum
captures the verdicts on those four, and promotes the Node 4.5 output contract
from implicit-in-the-signature to a named architectural principle.

---

## Decisions Locked

| Decision | Verdict | Rationale |
|---|---|---|
| **`max_edges` default** | `None` — no cap | `min_strength` is the semantic filter ("this isn't noise"). `max_edges` is a display/performance cap ("even among signal, cap volume"). Different jobs. Collapsing them into one parameter with a formula default blurs a distinction the thesis cares about. The original `2 * (N_backward + N_forward)` multiplier is linear in node count, but co-citation edges are quadratic worst case and heavy-tailed in practice — the multiplier doesn't track the shape of the distribution it's trying to cut. Whatever number it lands on is coincidence. Honest default: emit every edge that clears `min_strength`. If a caller needs a cap, they set one; the function default stays neutral. |
| **Output ordering** | `(strength desc, source_id asc, target_id asc)` — part of function contract | Strength-first matches natural consumption patterns (top-K rendering, audit inspection). `source_id, target_id` ascending as tiebreaker is determinism hygiene — canonical form is already `source_id < target_id` (locked in primary session), so within-tier ordering is unambiguous. NetworkX iteration order is not guaranteed stable across versions and dict insertion ordering is a CPython implementation detail not worth loading a determinism thesis onto. Explicit sort removes the entire category of dep-upgrade regressions. |
| **Truncation strategy** | Hard cap via post-sort slice; ties at boundary resolved by secondary sort | Because output is already sorted, truncation is `co_edges[:max_edges]`. No second sort, no separate logic, no ambiguity at the boundary. Strength ties that straddle the cutoff are "deterministic but arbitrary" — same input produces same cut every time, but edges of equal strength may be split across the line. This is the honest contract: `max_edges` is a hard cap, not a soft target. Callers who need ties-inclusive behavior pass `max_edges=None` and truncate themselves. |
| **Edge type string** | `"co_citation"` — lowercase snake_case | Matches existing `"cites"` convention in `CitationEdge.type`. Edge type is an open string by design (required for Phase 10 causal semantics), so open-vocabulary discipline requires explicit convention or it rots. Within-domain consistency is the goal; cross-domain consistency (e.g., vs. Phase 10's uppercase `MODULATES`, `DRIVES`) is not — different domains express different kinds of relationships and may choose different conventions. Naming this tension in the spec preempts a later "fix" that imposes false uniformity. |

---

## Updated Signature

Same as primary session. Spec language for the defaults and contract:

```python
def compute_co_citations(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],   # full set — cleaned ∪ suppressed
    min_strength: int = 2,
    max_edges: int | None = None,       # no cap by default
) -> list[CitationEdge]:                # only co_citation edges
```

Contract additions to capture in `spec-node5-co-citation.md`:

- Output is sorted by `(strength descending, source_id ascending, target_id ascending)`.
  This ordering is part of the function contract — consumers may rely on it.
- When `max_edges` is set, the function returns the first `max_edges` entries after
  sorting. Hard cap. Ties in `strength` at the cutoff boundary are resolved by the
  secondary sort keys — deterministic, but arbitrary from a semantic standpoint.
- Output edges have `type="co_citation"`. Convention within the arxiv domain is
  lowercase snake_case for edge types. Other domains may use different conventions
  internally; cross-domain consistency is not a goal — within-domain consistency is.

---

## Python Implementation Note

Sort is a single stable sort with a tuple key, not chained sorts:

```python
co_edges.sort(
    key=lambda e: (-e.strength, e.source_id, e.target_id)
)
if max_edges is not None:
    co_edges = co_edges[:max_edges]
```

`-e.strength` flips descending for the numeric field without `reverse=True`
(which would flip the string tiebreakers too). Standard idiom for mixed-direction
sorts on a single tuple key. Python's `sorted()` and `list.sort()` are both stable,
so earlier ordering is preserved within strength tiers before the secondary keys
break the tie — which matters if the upstream edge list has any meaningful order
(it doesn't here, but the guarantee is cheap).

---

## Design Principle Reinforced

`min_strength` vs. `max_edges` as separate jobs mirrors the thesis pattern that
recurred throughout the primary session: **explicit, single-purpose outputs beat
clever compound parameters.** A "reasonable default cap" that mixes semantic
filtering with volume control is exactly the kind of hidden coupling the graph-
first architecture is built to avoid. The function returns all the facts; the
caller decides how many to display. The renderer, not the data layer, owns
presentation concerns.

Same pattern at the edge-type naming level: resisting the urge to impose false
uniformity across domains. Each domain's vocabulary serves its own semantics;
forcing a global convention would be optimizing for consistency over meaning.
The open-string edge type design already anticipates this — the spec just needs
to say so explicitly.

---

## Node 4.5 Output Contract (Promoted to Named Principle)

The primary session touched this in two places — once in the "output shape" row
of the decisions table ("already adequate"), and once in a downstream-routing
note tucked after the signature section. Both understated the principle. It
belongs here, first-class:

> **Node 4.5 is responsible for producing every shape of the edge set that any
> downstream consumer needs. No downstream node reconstructs data from another
> consumer's outputs or from log records.**

This is the thesis pattern applied to one specific seam: explicit, separate
outputs for each consumer's needs, no implicit dataflow, no back-channel reaches
through logs or provenance. The split is the whole point — not an incidental
property of the current shape.

**Concrete contract Node 4.5 owes:**

| Output field | Shape | Consumer |
|---|---|---|
| `cleaned_edges` | `list[CitationEdge]` | Nodes 6, 7 (metrics, communities — need DAG) |
| `suppressed_edges` (via `.original`) | `list[CitationEdge]` wrapped in `SuppressedEdge` | Node 5 (co-citation — needs full topology) |
| `cycle_log` | structured metadata | Node 8 (audit, provenance) |

Any consumer that needs "all citations" composes the union at the call site in
one line:

```python
all_cites = result.cleaned_edges + [s.original for s in result.cycle_log.suppressed_edges]
```

No parsing log records. No reconstruction. No inference. The data is already
there in first-class structured form; the caller just concatenates.

**Why `SuppressedEdge` composing `CitationEdge` is load-bearing for this contract:**

Currently `SuppressedEdge` duplicates a subset of `CitationEdge`'s fields
(`source_id`, `target_id`) and silently drops the rest (`citing_paper_year`,
`strength`). That's not just a data-loss bug — it's a *contract violation*. A
downstream consumer that needs the full topology of all citations (cleaned +
suppressed) cannot get it without field-by-field reconstruction, which means
either (a) the consumer reaches into provenance to rebuild — violating the
explicit-outputs principle — or (b) the data is permanently lost. Neither is
acceptable.

The refactor (`SuppressedEdge` composes `CitationEdge` as `.original`) is the
mechanical fix that makes the contract actually hold. It's not a convenience
cleanup — it's the concrete change without which the architectural principle
stated above is aspirational rather than enforced.

**Sequencing consequence:** this is why the `SuppressedEdge` refactor is
prerequisite to Node 5, not bundled with it. The refactor is "fix Node 4.5's
output contract"; Node 5 is "first consumer that relies on the contract being
correct." Two separate stories, two separate PRs, clean history.

---

## Open Items (unchanged from primary session, re-listed for continuity)

| Item | Owner | When |
|---|---|---|
| `SuppressedEdge` refactor (prerequisite) | next implementation session | Before Node 5 spec |
| Node 5 anti-drift spec (`docs/specs/spec-node5-co-citation.md`) | next design-to-spec session | After refactor merged |
| Node 5 test plan | design-to-spec session | Into the spec |
| `N_max_co_citation` default validation | Deferred — no default cap, caller concern | n/a |
| `co_citation_min_strength` revisit | Seed pair validation spikes | After Node 5 lands |

Note: the original "`N_max_co_citation` default validation" open item is closed
by the `max_edges=None` decision. If a cap is needed at the demo/renderer layer,
validation happens there, not here.

---

## Test Gate

No code changes. Baseline 93 passing, unchanged.

---

## What's Next

1. **`SuppressedEdge` refactor PR** — unchanged, still first.
2. **Node 5 anti-drift spec** — now includes the four locks from this addendum
   plus a test plan (next design-to-spec pass).
3. **Node 5 implementation** — Claude Code against the frozen spec.

---

*Companion: session-2026-04-21-node5-design.md (primary session summary).*
