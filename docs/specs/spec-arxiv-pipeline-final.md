# Idiograph — arXiv Citation Graph Pipeline Design Spec
**Status:** FROZEN — implementation ready
**Created:** 2026-04-06
**Revised:** 2026-04-07 (v3 — final review pass; hop_depth added to renderer contract; N_max_co_citation added to declared parameters)
**Companion documents:** demo_design_spec-1.md, session-2026-04-06.md

---

## Design Principle

This pipeline is designed output-backward. The renderer's data contract defines what every node must carry at pipeline exit. Every design decision is traceable to that contract or to a named thesis requirement.

---

## Renderer Data Contract

Every node exiting the pipeline must carry:

| Field | Type | Source | Notes |
|---|---|---|---|
| `node_id` | string | pipeline | Canonical internal registry key. See Node Identity note. |
| `arxiv_id` | string \| null | arXiv / OpenAlex | External identifier. Null for papers without arXiv presence. |
| `doi` | string \| null | OpenAlex | External identifier. |
| `title` | string | arXiv / OpenAlex | |
| `year` | integer | arXiv / OpenAlex | |
| `citation_count` | integer | OpenAlex | Total accumulated citations |
| `abstract` | string | arXiv / OpenAlex | |
| `authors` | list[string] | arXiv / OpenAlex | |
| `community_id` | string | Node 7 — Infomap | |
| `pagerank` | float | Node 6 — NetworkX | |
| `topological_depth` | integer \| null | Node 6 — NetworkX | Longest path from root in the cycle-cleaned DAG. Null if node is part of a suppressed cycle. |
| `hop_depth` | integer | Node 3 / Node 4 | BFS traversal distance from seed at time of retrieval. Available immediately; does not depend on Node 6. Distinct from `topological_depth` — see Node 3 ranking note. |

**Node Identity:** `arxiv_id` cannot serve as the universal registry key. Backward traversal surfaces papers that predate arXiv entirely — foundational ML works (backpropagation, Hopfield networks, original PageRank) have DOIs but no arXiv IDs. The pipeline uses a synthetic `node_id` as the canonical internal key:

```
node_id = f"arxiv:{arxiv_id}"   # for papers with arXiv presence
node_id = f"doi:{doi}"          # for papers without arXiv ID but with DOI
node_id = f"openalex:{oa_id}"   # fallback for papers with neither
```

`arxiv_id` and `doi` are retained as external identifiers for linking, rendering, and API calls. The internal graph always keys on `node_id`. This is not a compromise — it is the correct key strategy for a heterogeneous corpus. A separate `id` field is not required.

---

Every edge exiting the pipeline must carry:

| Field | Type | Source | Notes |
|---|---|---|---|
| `source_id` | string | traversal | References `node_id` |
| `target_id` | string | traversal | References `node_id` |
| `type` | string | pipeline | `cites` or `co_citation` |
| `citing_paper_year` | integer \| null | OpenAlex | Publication year of the citing paper. Not a citation-event timestamp. Null for papers where year is unavailable. |
| `strength` | integer \| null | Node 5 | Shared citing paper count. Populated for `co_citation` edges only. Null for `cites` edges. |

Edge type is an open string — not a closed enum. Required for Phase 10 causal semantics compatibility.

**Note on `strength`:** The co-citation toggle filters on this field at render time. It must be present in the edge schema at pipeline exit. `cites` edges carry `strength: null` — the field is always present, never absent.

---

## Target Graph Size

Two-tier architecture:

- **Curated subgraph:** 150–300 nodes, pre-traversed, persisted to registry, loads on demo open. Visually designed. Proves the multi-view projection argument.
- **Live neighborhood:** on-demand from real corpus, depth=1, ~20 nodes per hop. Loads as user navigates. Proves the infrastructure argument.

The curated subgraph is the stage set. The live neighborhood proves it is not just a stage set.

---

## Edge Types

**Direct citation (`cites`):** explicit reference from one paper to another. Primary edge type. Source: OpenAlex reference lists. Fact, not inference — verifiable by opening the paper.

**Co-citation (`co_citation`):** derived relationship — papers A and B are co-cited when a third paper C cites both. Strength proportional to number of shared citing papers **within the local traversal boundary** (see Co-citation Scope note in Node 5). Inference, not fact — rendered distinctly (dashed, lower opacity).

Co-citation edges are a **view parameter, not a data parameter.** Pre-computed at pipeline time, stored in registry, toggled at render time via API parameter. Performance cost paid once. Toggle does not recompute — it filters on the `strength` field.

```
GET /graph/d3?source=arxiv&view=influence&co_citation=true
```

Visual distinction between direct and co-citation edges is load-bearing for the thesis. A direct citation is a declaration. A co-citation is an inference. Conflating them without distinction would undercut the transparency argument.

---

## Pipeline Nodes

### Node 0 — Direct Seed Entry *(bypass path)*
**Purpose:** accept a known paper identifier directly, bypassing Node 1 query and Node 2 selection

**Inputs:** `arxiv_id` (string) or `doi` (string) — caller supplies one
**Outputs:** full metadata for the seed paper, confirmed as graph root
**API:** OpenAlex lookup by identifier

**Rationale:** Node 1 + Node 2 are the discovery path — for an unknown corpus. When the seed is already known (demo preparation, automated pipeline runs, repeat traversals), the query/selection loop is unnecessary overhead. Node 0 provides a direct entry point. This is not a shortcut — it is a distinct and equally valid call path.

**Gate:** skip to Node 3 if seed resolves successfully. If the identifier does not resolve in OpenAlex, fall back to Node 1 with a logged warning.

---

### Node 1 — Structured Query
**Purpose:** retrieve candidate seed papers from arXiv corpus

**Inputs:**
- `title_fragment` (string, optional)
- `author` (string, optional)
- `date_range` (tuple[year, year], optional)
- `arxiv_category` (string, optional)

**Outputs:** ranked list of candidate papers with node_id, arxiv_id, title, year, authors

**API:** arXiv search API — discrete field parameters, not freetext. Deterministic retrieval: identical field inputs always produce identical candidate list.

**Design rationale:** keyword interfaces produce inconsistent query ordering and force implicit structure. Structured fields enforce identical query structure every time.

---

### Node 2 — Seed Selection
**Purpose:** establish the root node of the traversal

**Inputs:** candidate list from Node 1
**Outputs:** single seed paper with full metadata, confirmed as graph root
**Note:** the only human interaction point in the standard pipeline path. All downstream nodes are fully deterministic given this selection. (Node 0 is the bypass for automated/known-seed cases.)

---

### Node 3 — Backward Traversal (root path)
**Purpose:** find foundational works in the intellectual lineage of the seed

**Inputs:** seed `node_id`
**Outputs:** direct references and their references, with full metadata, ranked and capped
**API:** OpenAlex

**Node identity:** backward traversal will surface papers without arXiv IDs. These are assigned `node_id` per the Node Identity convention above. They are first-class nodes — not filtered out, not marked as degraded. The absence of an arXiv ID is a corpus fact, not a data quality failure.

**Ranking function:**
```
score = citation_count × log(hop_depth + 1) × (1 / recency_weight)
```

Where:
- `hop_depth` = BFS traversal depth from seed (1 = direct reference, 2 = reference of reference). Available at traversal time — does not depend on Node 6.
- `recency_weight = e^(years_since_publication × λ)`, λ declared explicitly

**Note on hop_depth scoring:** `log(hop_depth + 1)` is monotonically increasing — papers at hop_depth=2 score higher than equally-cited papers at hop_depth=1. This is intentional: deeper foundational works are what backward traversal is designed to surface. A paper cited by 5000 works across 30 years scores higher than a paper cited by 5000 works across 5 years, all else equal. If this behavior is not desired, replace with `log(1 / hop_depth)` to invert. The current formula must be treated as a declared design choice, not a default.

**Note on `hop_depth` vs `topological_depth`:** The full graph-structural `topological_depth` (longest path from root in the complete DAG) is computed in Node 6, after traversal is complete. Node 3's ranking uses `hop_depth` — the BFS traversal distance — which is available immediately and is a valid proxy for lineage distance at ranking time. These are related but distinct metrics; both are retained in the final node schema.

**Optimizes for:** foundational works, persistent citation across time, intellectual lineage. Older papers with high accumulated citation count score highest. Recency penalized — this is intentional.

**Cap:** top-N by score after ranking. N is a declared parameter (`N_backward`).

---

### Node 4 — Forward Traversal (branch path)
**Purpose:** find emerging work that builds on the seed

**Inputs:** seed `node_id`
**Outputs:** citing papers with full metadata, ranked and capped
**API:** OpenAlex

**Ranking function:**
```
score = α(citation_velocity) + β(citation_acceleration) × recency_weight
```

Where:
- `citation_velocity` = citations accumulated / months since publication
- `citation_acceleration` = rate of change of citation_velocity (requires ≥3 time points; see Data Availability note)
- `recency_weight = e^(years_since_publication × λ)`, λ declared explicitly
- α, β are declared weighting parameters

**Optimizes for:** emerging consensus, early signal, where the field is heading. Papers with positive citation acceleration score highest — these are the early indicators of a field consensus forming.

**Cap:** top-N by score after ranking. N is a declared parameter (`N_forward`).

**⚠ Citation Lag — Known Limitation of the Forward Traversal Premise**

The framing of Node 4 ("where the field is heading," "early indicators of forming consensus") depends on citation velocity being a valid early signal. It is not reliably valid for recently published papers. Citation accumulation has a 12–18 month structural lag — a paper published 6 months ago may have 3 citations not because it is unimportant but because the community has not had time to respond. This is not a data availability problem; it is a property of how citations work.

This does not invalidate the Node 4 approach. It means the forward traversal is most meaningful for papers 12+ months old, and weakest for papers in the most recent publication window. This limitation must be stated clearly in the renderer (tooltip, legend, or metadata panel) — not hidden. "Emerging signal" framed honestly is still valuable. The same claim made without the caveat is misleading.

**⚠ Data Availability — Validate Before Implementation**

Citation acceleration requires per-year citation counts. OpenAlex exposes this via the `counts_by_year` field, but population is inconsistent — older papers and papers outside the OpenAlex core corpus may have only a single data point or none. The minimum of 3 time points required for acceleration computation must be validated against real OpenAlex data on the target corpus before this ranking function is implemented.

If `counts_by_year` coverage falls below an acceptable threshold (to be defined after validation), the fallback ranking is citation_velocity alone (α=1, β=0). This fallback must be documented and declared — not silent. The acceleration claim is the differentiator; if it cannot be supported by available data, that must be stated explicitly rather than obscured.

---

### Node 3 / Node 4 — Independence Note

The two ranking functions are fully independent. They answer different questions and share no parameters. Recency appears in both but operates in opposite directions — penalized in Node 3, rewarded in Node 4. This asymmetry is meaningful and must be preserved. Shared parameters would conflate two distinct retrieval goals.

---

### Node 4.5 — Cycle Detection and Cleaning
**Purpose:** detect and resolve cycles in the assembled citation graph before metric computation

**Inputs:** complete node + direct citation edge set from Nodes 3 + 4
**Outputs:** cleaned graph with cycles resolved; cycle log

**Why cycles exist:** citation networks are not DAGs by definition. arXiv preprints routinely cite each other bidirectionally — Paper A cites Paper B, Paper B cites Paper A, both posted as preprints before either is formally published. OpenAlex will surface these. Cycles also appear through updated versions, errata chains, and self-citation loops. The DAG assumption is false; it must not be inherited silently from NetworkX's `dag_longest_path_length` function, which will throw an exception or produce a wrong answer if the graph contains a cycle.

**Resolution strategy:**
1. Run `networkx.find_cycle()` on the assembled graph.
2. Log all detected cycles with their member `node_id`s and edge directions.
3. For each cycle, remove the edge with the lowest `citation_count` sum between source and target (weakest link heuristic). If tied, remove the lexicographically later edge by `node_id` pair — deterministic tiebreaker.
4. Repeat until no cycles remain.
5. Nodes involved in suppressed edges retain `topological_depth: null` in their schema to mark that their depth is not computable without the suppressed edge.

**This is not a silent fix.** Cycle suppression is logged to provenance metadata (Node 8). The renderer surfaces a count of suppressed edges in the graph metadata panel. The user must be able to audit what was cleaned and why.

**Rationale for weakest-link removal over other strategies:** minimum feedback arc set is NP-hard for the general case. The weakest-link heuristic is polynomial, deterministic, and produces a defensible result: if the cycle must be broken, the least-cited relationship is the most reasonable candidate for suppression. This must be declared as a heuristic, not presented as a correct answer.

---

### Node 5 — Co-citation Computation
**Purpose:** derive co-citation edges from the assembled node set

**Inputs:** complete node set from Nodes 3 + 4 (post-cycle cleaning)
**Outputs:** co-citation edges with `strength` score (count of shared citing papers)
**Note:** pure graph computation — no API call. Runs entirely over the local node set.

**Co-citation Scope:** strength scores are computed relative to the local traversal boundary, not the full OpenAlex corpus. A paper cited by 800 papers in the full corpus may share only 2 citing papers with another node in this local set. Co-citation strength in the registry reflects shared citation density within the traversal window, not global co-citation prevalence. This must be labeled clearly in the renderer — strength is a local relative measure.

A minimum strength threshold (shared citation count below which co-citation edges are suppressed) is a declared parameter (`co_citation_min_strength`). Default to be determined after validating against real traversal data.

**`strength` field:** populated on all `co_citation` edges. Carried through to pipeline exit in the edge schema. The renderer's co-citation toggle filters on this field — it must be present and non-null on all co-citation edges.

---

### Node 6 — Metric Computation
**Purpose:** compute graph-structural metrics per node

**Inputs:** complete node + direct citation edge set (post-cycle cleaning from Node 4.5)
**Outputs:** `pagerank`, `topological_depth` added to each node
**Implementation:** NetworkX, deterministic

**Prerequisite:** Node 4.5 must complete before Node 6 runs. Metric computation assumes a cycle-free graph. This dependency is explicit in the pipeline execution order.

**`pagerank`:** computed with declared damping factor (default 0.85). All parameters explicit.

**`topological_depth`:** longest path from root node in the cycle-cleaned citation DAG. Computed via NetworkX `dag_longest_path_length` from root. Nodes with `topological_depth: null` (from suppressed cycle edges) are excluded from this computation and carry null in their schema.

---

### Node 7 — Community Detection
**Purpose:** assign community membership to each node

**Inputs:** complete graph (post-cycle cleaning)
**Outputs:** `community_id` added to each node

**Algorithm:** Infomap. Fixed random seed, all parameters declared explicitly.

**Tuning parameters (Infomap-specific):**

| Parameter | Description | Default |
|---|---|---|
| `infomap_seed` | Random seed for reproducibility | 42 |
| `infomap_trials` | Number of optimization trials | 10 |
| `infomap_teleportation` | Teleportation probability (handles dangling nodes) | 0.15 |

**Note:** Infomap does not use a resolution parameter. Resolution parameters belong to modularity-based methods (Louvain, Leiden). Infomap tunes via teleportation probability and trial count.

**Rationale:** Infomap optimizes for compression of a random walk — communities are defined as regions where a random walker tends to stay before escaping. For citation networks specifically, this is a closer model to how knowledge propagates than Louvain/Leiden's modularity optimization. A citation trail is a walk, not a random edge sample. Infomap community boundaries mean something about how knowledge flows, not just how edges are distributed.

**Fallback:** Leiden with fixed random seed (`leiden_seed`, default 42), documented. Used if the `infomap` package creates installation friction. Leiden is deterministic with a fixed random seed and has stronger community connectivity guarantees than Louvain.

**Important:** Infomap and Leiden produce different community assignments for the same graph. If the fallback is used, community_ids in the registry reflect the Leiden partition, not the Infomap partition. This must be recorded in the graph's provenance metadata so the partition algorithm is always auditable.

**Disqualified:** Label Propagation — non-deterministic. Violates the pipeline determinism guarantee.

---

### Node 8 — Persist to Registry
**Purpose:** store the fully attributed graph as named, queryable infrastructure

**Inputs:** complete attributed graph (nodes + all edge types)
**Outputs:** named graph object in registry

**Cache key:** content-addressed hash of seed `node_id` + declared traversal parameters. Same seed, same parameters → same graph, no recomputation. The cache is not a convenience — it is an acceleration structure. The thesis argument and the performance optimization are the same mechanism.

**Provenance metadata:** each persisted graph records:
- Algorithm used for community detection (Infomap or Leiden fallback)
- All declared parameter values at time of run
- Pipeline version
- Cycle log: count of cycles detected, edges suppressed, affected `node_id`s
- Citation acceleration fallback status (whether β=0 was applied)
- Node identity distribution: count of `arxiv:`, `doi:`, and `openalex:` prefixed nodes

Graph state is always auditable. Provenance metadata is not optional decoration — it is what makes the graph's outputs trustworthy.

**Note:** the registry is the target surface for both MCP and FastAPI interfaces. Graph state does not live in the interface layer.

---

## Declared Parameters

All parameters are named, documented, and passed explicitly. No implicit defaults in production.

| Parameter | Node | Description | Default |
|---|---|---|---|
| `λ` | 3, 4 | Recency decay rate in recency_weight | TBD |
| `α` | 4 | Weight on citation_velocity in forward ranking | TBD |
| `β` | 4 | Weight on citation_acceleration in forward ranking | TBD |
| `N_backward` | 3 | Cap on backward traversal results | TBD |
| `N_forward` | 4 | Cap on forward traversal results | TBD |
| `pagerank_damping` | 6 | PageRank damping factor | 0.85 |
| `infomap_seed` | 7 | Random seed for Infomap | 42 |
| `infomap_trials` | 7 | Number of Infomap optimization trials | 10 |
| `infomap_teleportation` | 7 | Teleportation probability for Infomap | 0.15 |
| `leiden_seed` | 7 | Random seed for Leiden fallback | 42 |
| `co_citation_min_strength` | 5 | Minimum shared citations to emit a co-citation edge | TBD |
| `N_max_co_citation` | 5 | Global cap on total co-citation edges emitted, regardless of strength. Prevents edge proliferation in dense subgraphs. Choice of which edges to surface is an explicit design decision, not an emergent artifact. | TBD |

TBD values require validation against real OpenAlex data before being locked.

---

## JIT Traversal Architecture

The pipeline's traversal and caching behavior follows a JIT compilation model, not a passive cache model.

**Interpreted tier:** cold API fetch from OpenAlex. Full network cost. Happens once per node per parameter set.

**Compiled tier:** local persistent registry. Sub-millisecond. No network. Grows denser as the user explores.

**The profiler:** the traversal engine itself. High-citation nodes appear repeatedly across unrelated neighborhoods — they are hot paths. These should be speculatively pre-fetched the moment they appear as references, before the user navigates to them. Texture streaming: load the tile before the camera gets there.

**Note:** speculative pre-fetch is a design intent, not a currently scoped implementation. Designate as deferred until the registry and traversal engine are stable.

The materialized graph is an explicit record of every traversal that was worth doing — auditable, deterministic, not probabilistic. This is the thesis made concrete in the data layer.

---

## LOD (Level of Detail) System

Community detection (Node 7) runs in the pipeline, not the renderer. Community membership is a node property like `year` or `citation_count`. This is required because the LOD system depends on cluster objects at the far view — those cluster objects are pre-computed aggregates of community members, not emergent from the renderer.

**Far LOD:** cluster objects. Centroid position, member count, community label. Renders on initial load.
**Mid LOD:** individual nodes with labels. Loads as user zooms into a region.
**Near LOD:** full metadata, edges, abstract. Loads on node selection.

**⚠ Community Count is Not a Parameter**

Infomap's community count emerges from the algorithm — it is not configurable. For a 150–300 node citation graph, Infomap may return anywhere from 5 to 60+ communities depending on graph structure. The Far LOD's cluster count is therefore unknown until the pipeline runs against real data.

A post-processing step must be specified before the LOD system is implemented:

- If community count falls below a minimum (e.g., < 5), the graph is too coarsely partitioned for a meaningful Far LOD — flag and investigate parameters.
- If community count exceeds a maximum (e.g., > 40), consider merging small communities below a member-count threshold into a catch-all "Other" cluster for rendering purposes. This merge is a rendering decision only — the underlying `community_id` assignments are preserved in the registry unchanged.
- Target render range and min/max thresholds are **TBD, pending validation against real traversal data.** This is a gate before LOD implementation begins.

Interaction model matches the data available at each level. The dart-throwing problem (Paperscape) is a LOD mismatch — this architecture prevents it structurally.

---

## Open Questions — Deferred

- Exact formula for `recency_weight` normalization across different corpus sizes
- Citation acceleration data availability — minimum viable coverage threshold. **Must be validated against real OpenAlex data before Node 4 implementation begins. This is a gate, not a footnote.**
- `co_citation_min_strength` threshold — validate against real traversal data
- Parameter values for λ, α, β, N_backward, N_forward — all TBD pending data validation
- LOD community count target range and merge threshold — validate against real Infomap output before LOD implementation begins
- Weakest-link cycle suppression heuristic — validate that it produces defensible results on real arXiv citation cycles before committing

---

*Companion documents: demo_design_spec-1.md, session-2026-04-06.md, phase_8_9_task_inventory.md*
*Next session: Task 1.2 — vector index design, or Task 1.3 formalized as closed. Ranking functions need parameter values validated against real OpenAlex data before implementation begins.*
