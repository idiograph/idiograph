# Spec — Node 4.5: Cycle Detection and Cleaning
**Status:** LIVING — governs implementation
**Freezes when:** all tests passing, branch merged to main
**Target file:** `src/idiograph/domains/arxiv/pipeline.py`
**Companion documents:** spec-arxiv-pipeline-final.md (frozen v3)

---

## Amendment status check

| Amendment | Impact on Node 4.5 | Action |
|---|---|---|
| AMD-016 (LLM node placement) | None. Node 4.5 runs before Node 5.5. No LLM involvement. | None |
| AMD-017 (multi-seed `root_ids`) | None. Cycle cleaning operates on edge set; node `root_ids` pass through untouched. | None |

---

## Purpose

Detect and resolve cycles in the assembled citation graph before Node 5 (co-citation) and Node 6 (metric computation). Metric computation requires a DAG; the citation graph is not one by default (arXiv preprint cross-citations, errata chains, self-citation loops).

**Algorithm authority:** `docs/specs/spec-arxiv-pipeline-final.md` §Node 4.5, verbatim:

1. Run `networkx.find_cycle()` on the assembled graph.
2. Log all detected cycles with member `node_id`s and edge directions.
3. For each cycle, remove the edge with the lowest `citation_count` sum between source and target (weakest link). Lex tiebreaker by `(source_id, target_id)`.
4. Repeat until no cycles remain.
5. Nodes involved in suppressed edges are marked for `topological_depth: null` downstream.

This is **not a silent fix.** The cycle log is part of the return value and flows to Node 8 provenance.

---

## Function signature

```python
def clean_cycles(
    nodes: list[PaperRecord],
    edges: list[CitationEdge],
) -> CycleCleanResult:
```

**Inputs:**
- `nodes` — all papers from Node 3 ∪ Node 4 (already deduplicated). Used for `citation_count` lookups.
- `edges` — all `cites` edges from Node 3 ∪ Node 4 (already deduplicated). Co-citation edges are **not** in scope — those are computed in Node 5 after this runs.

**Output:** `CycleCleanResult` — see Data Models below.

**Pure function.** No mutation of inputs. No I/O. No async. No network. This is a local graph computation.

---

## Data models

New Pydantic models, added to `src/idiograph/domains/arxiv/models.py` (same file as `PaperRecord`, `CitationEdge`):

```python
class SuppressedEdge(BaseModel):
    """Record of a single edge removed during cycle cleaning."""
    source_id: str = Field(description="node_id of edge source.")
    target_id: str = Field(description="node_id of edge target.")
    citation_sum: int = Field(
        description="Sum of citation_count for source and target at removal time. "
                    "The weakest-link heuristic selected the edge with the minimum of this value."
    )
    cycle_members: list[str] = Field(
        description="node_ids of all nodes in the cycle this edge was breaking, in traversal order."
    )


class CycleLog(BaseModel):
    """Audit trail of cycle cleaning. Flows to Node 8 provenance metadata."""
    suppressed_edges: list[SuppressedEdge] = Field(
        default_factory=list,
        description="Every edge removed during cleaning, in order of removal."
    )
    cycles_detected_count: int = Field(
        description="Total cycles found across all iterations. May exceed len(suppressed_edges) "
                    "when one removal breaks multiple cycles."
    )
    iterations: int = Field(
        description="Number of find_cycle -> remove passes executed before the graph was clean."
    )

    @property
    def affected_node_ids(self) -> set[str]:
        """node_ids whose topological_depth must be null downstream (Node 6 handoff)."""
        result: set[str] = set()
        for e in self.suppressed_edges:
            result.add(e.source_id)
            result.add(e.target_id)
        return result


class CycleCleanResult(BaseModel):
    """Return value of clean_cycles(). Separates cleaned graph from audit log."""
    cleaned_edges: list[CitationEdge] = Field(
        description="Edge set with cycle-breaking edges removed. Safe for DAG algorithms."
    )
    cycle_log: CycleLog = Field(description="Audit trail of what was removed and why.")
```

**Why a property, not a stored field:** `affected_node_ids` is derived from `suppressed_edges`. Storing it would open the possibility of the two drifting. Deriving on access keeps a single source of truth.

---

## Algorithm

```
Build a networkx.DiGraph from (nodes, edges).
Build a lookup: node_id -> citation_count.
iterations = 0
cycles_detected_count = 0
suppressed = []

loop:
    try: cycle = nx.find_cycle(G, orientation="original")
    except NetworkXNoCycle: break

    iterations += 1
    cycles_detected_count += 1

    cycle_edges = list of (u, v) pairs from `cycle`
    cycle_members = ordered list of node_ids in cycle

    For each (u, v) in cycle_edges:
        score[(u, v)] = citation_count[u] + citation_count[v]

    weakest = min(cycle_edges, key=lambda e: (score[e], e))
        # lex tiebreaker: (score_asc, source_id_asc, target_id_asc)

    Remove weakest from G.
    Remove the matching CitationEdge from the edge list.
    Append SuppressedEdge(
        source_id=weakest[0],
        target_id=weakest[1],
        citation_sum=score[weakest],
        cycle_members=cycle_members,
    )

Return CycleCleanResult(cleaned_edges=remaining_edges, cycle_log=CycleLog(...))
```

---

## Contracts and edge cases

**Missing node in citation lookup.** If a `CitationEdge` references a `node_id` not present in `nodes`, the edge is treated as having `citation_count=0` for that endpoint. Log at WARNING with the missing `node_id`. Do not raise — the spec requires Node 4.5 to handle the graph it is given, including malformed data. Node 8 provenance will show these warnings.

**Empty cycle set.** If `find_cycle` raises on the first call, return `CycleCleanResult(cleaned_edges=list(edges), cycle_log=CycleLog(suppressed_edges=[], cycles_detected_count=0, iterations=0))`. The input edge list is copied, not aliased.

**Self-loops.** A self-loop (`source_id == target_id`) is a cycle of length 1. `find_cycle` detects these. The edge is removed; `cycle_members` contains a single `node_id`; `citation_sum` is `2 * citation_count` (the same node counted as both endpoints).

**Parallel edges to same target.** Out of scope — the edge schema does not permit duplicate (source, target) pairs, and Node 3/4 dedup guarantees it. If this assumption is violated, the lex tiebreaker still produces a deterministic result.

**Infinite loop protection.** Cap iterations at `len(edges)` as a safety bound. Removing one edge per iteration cannot require more passes than there are edges. If the cap is hit, raise `RuntimeError` — this indicates a bug in the loop logic, not a malformed input.

**Order preservation.** `cleaned_edges` preserves the input order of non-removed edges. Downstream consumers (Node 5) do not depend on order, but determinism across runs matters. Do not reorder via set operations.

---

## Logging

- Node 4.5 start: INFO, `"Node 4.5: cycle cleaning on N nodes, M edges"`
- Each removal: INFO, `"Suppressed edge {source_id} -> {target_id} (citation_sum={sum}) to break cycle of length {k}"`
- Missing-node citation lookup: WARNING with the `node_id`
- Completion: INFO, `"Node 4.5 complete: {iterations} iterations, {count} edges suppressed, {affected} affected node_ids"`
- Clean graph on first check: DEBUG, `"Node 4.5: no cycles detected"`

Standard project logger — `logging.getLogger(__name__)`.

---

## Tests — minimum set

File: `tests/domains/arxiv/test_pipeline_node4_5.py`

Each test has a one-line docstring. No pytest-asyncio (this is a synchronous function). No mocked HTTP (no I/O).

| Test | What it proves |
|---|---|
| `test_acyclic_passthrough` | Acyclic input: cleaned_edges equals input, zero suppressions, iterations=0 |
| `test_two_cycle_simple` | A→B, B→A: one edge removed (the weaker), cycle_members has both |
| `test_three_cycle` | A→B, B→C, C→A: one edge removed, cycle_members has three |
| `test_weakest_link_selected` | Three-edge cycle with unequal citation counts: the minimum-sum edge is the one removed |
| `test_lex_tiebreaker` | Tied citation sums: lexicographically smaller (source, target) wins removal |
| `test_two_disjoint_cycles` | Two independent cycles cleaned in separate iterations; both logged |
| `test_nested_cycles` | One edge removal breaks multiple cycles: cycles_detected_count ≥ len(suppressed_edges) |
| `test_self_loop` | Self-loop removed; cycle_members is a single node_id |
| `test_affected_node_ids_property` | Derived property returns union of source and target node_ids across suppressed edges |
| `test_missing_citation_node_warns` | Edge referencing unknown node_id: treated as citation_count=0, WARNING logged, no raise |
| `test_preserves_input_edge_order` | cleaned_edges retains input ordering for non-removed edges |
| `test_input_not_mutated` | Original input lists unchanged after call (pure function property) |

---

## Boundaries — what Node 4.5 does not do

- Does not compute `topological_depth`. That is Node 6. Node 4.5 only flags *which* nodes will receive null via `cycle_log.affected_node_ids`.
- Does not handle co-citation edges. Node 5 runs on the cleaned graph.
- Does not attempt minimum feedback arc set. The weakest-link heuristic is declared, not claimed optimal. This is stated in the spec and must not be overstated in docstrings.
- Does not touch `PaperRecord.topological_depth` fields. That field is populated by Node 6 using the `affected_node_ids` handoff.
- Does not persist anything. Node 8 writes the `CycleLog` to provenance metadata later.

---

## Implementation constraints

- Pure function. No I/O, no network, no async, no mutation of inputs.
- `encoding="utf-8"` is irrelevant here — no file I/O. (Project standard for all `.py` opens, but this function opens nothing.)
- NetworkX is already a project dependency (used in core query layer). Import as `import networkx as nx`.
- No new top-level dependencies.
- ruff: format new code only; do not reformat pre-existing code in `pipeline.py` or `models.py`.

---

## Freeze trigger

All tests in `test_pipeline_node4_5.py` passing, merged to main.

Post-freeze deferred items:
- Weakest-link heuristic validation against real arXiv cycles (spec open question §Open Questions).
- Integration into the top-level pipeline runner (no runner exists yet — nodes are still called individually).
