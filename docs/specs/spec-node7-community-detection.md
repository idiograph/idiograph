# Spec: Node 7 — Community Detection
**Status:** ACTIVE
**Branch:** `feature/node7-community-detection`
**Scope:** `src/idiograph/domains/arxiv/pipeline.py` — new function `detect_communities()`
         `src/idiograph/domains/arxiv/models.py` — new model `CommunityResult`
         `tests/domains/arxiv/test_pipeline_node7.py` — new test file
         `pyproject.toml` — new `[community]` optional extra

---

## Purpose

Node 7 assigns a community label to every node in the assembled citation graph. The label is a string encoding of the integer module ID produced by Infomap (primary) or Leiden (automatic fallback). Community membership is a node property in the renderer data contract — `community_id: str | None` — and is required for the LOD cluster view.

Community detection does not select, route, or modify graph structure. It produces one label per node and exits.

---

## Dependencies — `[community]` Optional Extra

Neither `infomap` nor `leidenalg`/`igraph` is currently in `pyproject.toml`. Both are optional because this is demo infrastructure, not core execution logic.

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
community = ["infomap", "leidenalg", "igraph"]
```

Install command for dev and CI:

```
uv sync --extra community
```

**Windows note:** verify that `igraph` has a `cp313-win_amd64` wheel available on PyPI before treating Leiden as a reliable fallback. Run `uv pip install igraph --dry-run` on the Windows dev machine to confirm. If no wheel exists, Leiden is not a valid fallback on the dev platform and the spec must be revised.

---

## New Model — `CommunityResult`

Add to `src/idiograph/domains/arxiv/models.py`, after the `DepthMetrics` model:

```python
class CommunityResult(BaseModel):
    community_assignments: dict[str, str]
    algorithm_used: Literal["infomap", "leiden"]
    community_count: int
    validation_flags: list[str]
```

Field contracts:

| Field | Type | Meaning |
|---|---|---|
| `community_assignments` | `dict[str, str]` | Maps `node_id` → `community_id`. Every input node appears as a key. No node is omitted. |
| `algorithm_used` | `Literal["infomap", "leiden"]` | Which algorithm ran. Recorded regardless of how the fallback was triggered. |
| `community_count` | `int` | Number of distinct communities in the partition. Equal to `len(set(community_assignments.values()))`. |
| `validation_flags` | `list[str]` | LOD validation warnings. Empty list if thresholds are satisfied. Never blocks execution. |

`Literal` is already imported in `models.py` — no new import line required.

---

## Function Signature

```python
def detect_communities(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],
    infomap_seed: int = 42,
    infomap_trials: int = 10,
    infomap_teleportation: float = 0.15,
    leiden_seed: int = 42,
    community_count_min: int = 5,
    community_count_max: int = 40,
) -> CommunityResult:
```

All parameters are explicit. No implicit defaults in production usage.

---

## Placement in `pipeline.py`

Insert after `compute_pagerank()` (currently ending at line 822), before the `ARXIV_PIPELINE` constant. Add the section header comment matching the existing style:

```python
# ── Node 7 — Community Detection ─────────────────────────────────────────────
```

---

## Edge Input

`cites_edges` receives the full citation topology: `cleaned_edges` ∪ suppressed originals. The merge happens at the call site — Node 7 is ignorant of `CycleCleanResult`.

Call-site merge (same pattern as Node 5):

```python
cycle_result = clean_cycles(nodes, raw_edges)
all_cites = cycle_result.cleaned_edges + [
    s.original for s in cycle_result.cycle_log.suppressed_edges
]
result = detect_communities(nodes, all_cites, ...)
```

**Why suppressed edges are included:** Infomap models a random walk and does not require a DAG. Cycle suppression was a structural accommodation for NetworkX's `dag_longest_path_length` — that constraint does not apply here. A suppressed edge is a real citation. Excluding it would give Infomap a distorted view of actual citation behavior with no algorithmic justification.

**Why co-citation edges are excluded:** The spec establishes a load-bearing distinction — direct citations are declarations, co-citation edges are inferences. Community structure derived from walk over declarations is a different and stronger claim than community structure derived from mixed signals. Co-citation edges also carry undirected semantics; including them alongside directed citation edges would mix semantic layers in the walk without a principled basis.

---

## Forest Semantics

Infomap/Leiden runs on the complete assembled graph — not per-root subtrees. For multi-seed (AMD-017) traversals, the overlap zone (nodes reachable from multiple roots) is precisely the analytically interesting structure. Within-root partitioning would destroy that signal by assigning shared ancestors to one root's community or the other's, arbitrarily.

---

## Graph Construction — Infomap Path

Infomap uses its own graph object, not NetworkX. Construct an `nx.DiGraph` first (matching the pattern of every other pipeline node), then pass it via `add_networkx_graph()`:

```python
G = nx.DiGraph()
G.add_nodes_from(node_id for n in nodes for node_id in [n.node_id])
G.add_edges_from((e.source_id, e.target_id) for e in cites_edges)

im = Infomap(f"--two-level --silent --seed {infomap_seed}")
im.add_networkx_graph(G)
im.num_trials = infomap_trials
im.teleportation_probability = infomap_teleportation
im.run()
```

**Flat modules (two-level):** `--two-level` is mandatory. Infomap defaults to hierarchical output; the `community_assignments: dict[str, str]` return type requires a flat partition. `get_modules()` returns the flat top-level assignment. If hierarchical levels are ever needed, that is a new field in a new amendment — not a change to `community_assignments`.

**Silent:** `--silent` suppresses Infomap's stdout progress output, which would pollute the pipeline log.

**Unweighted:** Every edge in the Node 7 input is a `cites` edge with `strength=None`. There is no meaningful weight to apply. The graph is unweighted. If a future version of Node 7 includes co-citation edges, weights become meaningful (use `strength`, default `1.0` for null) — record this dependency explicitly so that decision is revisited if the input-set changes.

**`community_id` format:** `str(node_module_id)` — direct integer-to-string conversion. No prefix. The prefix conventions (`arxiv:`, `doi:`, `openalex:`) exist to namespace external identifiers into the internal key space. `community_id` is an algorithm-assigned label, not a key. `"0"`, `"1"` etc. are unambiguous in the renderer context.

---

## Graph Construction — Leiden Fallback Path

`leidenalg` operates on `igraph.Graph`, not NetworkX. The integer-index round-trip is the load-bearing detail:

```python
node_ids = [n.node_id for n in nodes]
idx = {nid: i for i, nid in enumerate(node_ids)}

g = igraph.Graph(directed=True)
g.add_vertices(len(node_ids))
g.vs["name"] = node_ids  # preserve node_id on each vertex

valid_edges = [
    (idx[e.source_id], idx[e.target_id])
    for e in cites_edges
    if e.source_id in idx and e.target_id in idx
]
g.add_edges(valid_edges)

partition = leidenalg.find_partition(
    g,
    leidenalg.ModularityVertexPartition,
    seed=leiden_seed,
)
```

Map results back to `node_id`:

```python
assignments = {node_ids[i]: str(partition.membership[i]) for i in range(len(node_ids))}
```

**Isolates:** every vertex must exist before any edge referencing it. `g.add_vertices(len(node_ids))` runs before `g.add_edges(...)`, so nodes with no edges are pre-registered and will receive a community assignment. A test must verify this — see §Tests.

**Integer-index correctness:** the round-trip `node_ids[i]` → `partition.membership[i]` is correct only if `igraph` preserves vertex insertion order. It does. Document this assumption in the implementation with a comment.

---

## Fallback Policy

```python
def detect_communities(...) -> CommunityResult:
    try:
        from infomap import Infomap
        return _run_infomap(...)
    except ImportError:
        pass

    try:
        import leidenalg
        import igraph
        return _run_leiden(...)
    except ImportError:
        pass

    raise RuntimeError(
        "Neither infomap nor leidenalg is installed. "
        "Install community detection dependencies: uv sync --extra community"
    )
```

**Why `RuntimeError` and not a silent degradation:** `community_id` is in the renderer data contract. A pipeline that exits Node 7 with no community assignments produces a malformed graph — every node carries `community_id=None` and the LOD cluster view cannot render. Silent degradation here is a thesis violation: the failure is invisible and the output is wrong. A loud, specific failure with an actionable install message is the correct behavior.

**`_run_infomap()` and `_run_leiden()`** are private helpers defined immediately after `detect_communities()`. They are not part of the public API. Tests call `detect_communities()` — not the helpers directly.

---

## Input Validation

Before graph construction, validate that all `node_id`s referenced in `cites_edges` are present in the `nodes` list. Log a WARNING for each unknown `node_id` and skip the edge — same pattern as `compute_co_citations()`. Do not raise.

```python
node_id_set = {n.node_id for n in nodes}
warned_missing: set[str] = set()
valid_edges = []
for e in cites_edges:
    for nid in (e.source_id, e.target_id):
        if nid not in node_id_set and nid not in warned_missing:
            _log.warning("detect_communities: unknown node_id %s — edge skipped", nid)
            warned_missing.add(nid)
    if e.source_id in node_id_set and e.target_id in node_id_set:
        valid_edges.append(e)
```

---

## LOD Validation

After partition, compare `community_count` against the declared thresholds. Append string flags to `validation_flags`. Never block.

```python
flags = []
if community_count < community_count_min:
    flags.append("community_count_below_minimum")
if community_count > community_count_max:
    flags.append("community_count_above_maximum")
```

**`community_count_above_maximum` does not trigger community merging.** Merging small communities for the Far LOD cluster view is a renderer decision. Node 7 emits the full partition unchanged. The flag tells the renderer "you may want to consolidate." The underlying `community_id` assignments in the registry are never altered by Node 7.

Default thresholds (5, 40) are informed by the spec's suggested range and will be revised after the first real traversal runs against the arXiv corpus.

---

## Logging

```python
_log.info("detect_communities: %d nodes, %d edges", len(nodes), len(cites_edges))
# ... after partition:
_log.info(
    "detect_communities: %d communities via %s — flags: %s",
    community_count, algorithm_used, validation_flags or "none",
)
```

DEBUG log on empty input. WARNING on unknown node_id (once per id). INFO on start and completion.

---

## Contracts

- Every node in `nodes` appears as a key in `community_assignments`. No node is omitted.
- `community_count == len(set(community_assignments.values()))`.
- Nodes with no edges (isolates) receive a community assignment — they are not omitted.
- `algorithm_used` reflects which algorithm actually ran — always set, never None.
- `validation_flags` is always a list — empty list if no flags, never None.
- Unknown `node_id`s in `cites_edges` are warned and skipped — never raise.
- `min_strength < 1` is not applicable here (no strength parameter) — no ValueError needed.
- If both imports fail, raises `RuntimeError` with install-extras message. Never returns silently.

---

## Tests

File: `tests/domains/arxiv/test_pipeline_node7.py`

Helpers (match the Node 5 / Node 6 pattern):
- `_rec(node_id, ...)` — builds a minimal `PaperRecord`
- `_edge(source_id, target_id)` — builds a `CitationEdge` with `type="cites"`, `strength=None`, `citing_paper_year=None`

**Minimum test set:**

| # | Test name | What it verifies |
|---|---|---|
| 1 | `test_all_nodes_assigned` | Every input node appears in `community_assignments` |
| 2 | `test_community_count_matches_assignments` | `community_count == len(set(assignments.values()))` |
| 3 | `test_isolate_receives_assignment` | Node with no edges is not omitted from output |
| 4 | `test_community_id_is_string` | All values in `community_assignments` are strings |
| 5 | `test_algorithm_used_set` | `algorithm_used` is `"infomap"` or `"leiden"` — never None |
| 6 | `test_validation_flags_empty_within_bounds` | No flags when community count is between min and max |
| 7 | `test_validation_flag_below_minimum` | `"community_count_below_minimum"` flag when count < min |
| 8 | `test_validation_flag_above_maximum` | `"community_count_above_maximum"` flag when count > max |
| 9 | `test_missing_edge_node_warns` | Unknown node_id in cites_edges logs WARNING; edge is skipped |
| 10 | `test_empty_nodes` | Empty input returns empty `community_assignments`, `community_count=0` |
| 11 | `test_single_node_no_edges` | Single node with no edges receives an assignment |
| 12 | `test_disconnected_graph` | Multiple disconnected components all receive assignments |
| 13 | `test_deterministic_same_input` | Two calls with same input produce identical output |
| 14 | `test_suppressed_originals_merge` | Merge pattern (`cleaned + [s.original ...]`) produces correct input — integration-style, not a unit test of the merge itself |
| 15 | `test_validation_flags_always_list` | `validation_flags` is a list — never None — on clean run |

The determinism test (13) is load-bearing for the thesis. Two identical calls must return identical `community_assignments`. Infomap's seed parameter is the mechanism.

---

## Implementation Constraints

1. Do not run `ruff format` on pre-existing code. Run `ruff check` only.
2. Do not refactor the existing inline `nx.DiGraph` builders in `pipeline.py` into a shared helper — that is a separate cleanup PR.
3. `_run_infomap()` and `_run_leiden()` are private. Tests do not call them directly.
4. `CommunityResult` is added to `models.py`. It is not defined inline in `pipeline.py`.
5. The `[community]` extra must be added to `pyproject.toml` in this PR — not deferred.
6. Section header comment is mandatory, matching the style at lines 478, 602, 700 of `pipeline.py`.

---

## Verification

```
uv sync --extra community
uv run pytest tests/domains/arxiv/test_pipeline_node7.py -v
uv run pytest tests/ -q
uv run ruff check src/idiograph/domains/arxiv/pipeline.py src/idiograph/domains/arxiv/models.py tests/domains/arxiv/test_pipeline_node7.py
```

Expected test count after merge: **144 + 15 = 159 passing**.

---

## Boundaries

**Node 7 receives from:**
- Node 5 call site (indirectly): `cleaned_edges` ∪ suppressed originals as `cites_edges`
- Node 0–4.5 (indirectly): `list[PaperRecord]` with `pagerank`, `hop_depth_per_root`, and `traversal_direction` already populated from Node 6

**Node 7 produces:**
- `CommunityResult` — consumed by the future orchestrator to write `community_id` onto each `PaperRecord`

**Node 7 does not:**
- Merge communities for rendering
- Make structural decisions based on community count
- Call any external API
- Read or write from the registry (Node 8's concern)

---

## Freeze Trigger

Freeze this spec to FROZEN on merge of the Node 7 PR into main. After freeze, changes require an AMD entry.

Post-freeze list:
- Update `spec-arxiv-pipeline-final.md` Node 7 section to reflect the implemented function pair and `CommunityResult` shape (same pattern as the post-Node-6 docs sweep)
- Update `BRIEFING.md` — Node 7 entry in What's Built, test baseline to 159
