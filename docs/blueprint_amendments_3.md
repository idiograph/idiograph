# NodeForge – Blueprint Amendments & Decision Log (Addendum 3)

Continues from `blueprint_amendments_2.md`.
Last amendment in previous log: AMD-009.

---

## Amendments

---

### AMD-010 — Mock Execution Mode for Dependency-Free Demo
Affects: Phase 8 (or pre-Phase 8 cleanup)
Status: PENDING
Decided: 2026-03
Reason: The live `nodeforge run` command requires an Anthropic API key, creating a
barrier for anyone evaluating the repo — collaborators, interviewers, or potential
employers. The 44-test suite already runs without a key via stub handlers — this is
not incidental, it is the handler registry pattern doing exactly what it was designed
to do (AMD-007). The `--mock` flag extends that property to the CLI demo, making the
full execution pipeline demonstrable on any machine without credentials. The
architecture is the thesis artifact. A key requirement gates access to the architecture.
Change: Add `--mock` flag to `nodeforge run`. When set, registers lightweight stub
handlers that return plausible structured output without any API calls or network
access. The full pipeline executes: topological sort, node status progression, results
dict, failure handling — all demonstrable without a key. Add `.env.example` to the
repo root with the key name but no value. Add an explicit section to the README:
"Running tests requires no API key. Running the live demo requires one. Run with
`--mock` to see the full pipeline without credentials."
Done when: `nodeforge run 1706.03762 --mock` executes the full arXiv pipeline,
produces a complete and plausible results dict, and requires no API key or network
access. `.env.example` is committed. README section is present.

---

### AMD-011 — Domain-First Directory Structure
Affects: Pre-Phase 8 (blocking — import paths must be clean before MCP layer is added)
Status: COMPLETE — 2026-03
Decided: 2026-03
Reason: The current `handlers/` and `pipelines/` directories are organized by artifact
type but are entirely arXiv-specific. They have no architectural relationship to `core/`
yet sit as its siblings in the directory tree. A developer browsing the repo cannot tell
from the structure that the executor is domain-agnostic and that the arXiv handlers are
one domain implementation among many possible ones. This contradicts the thesis: if the
architecture is domain-agnostic, the directory structure should make that legible without
explanation. Additionally, when a second domain is added (VFX handlers in Phase 10), the
current structure provides no clear home for it and will require a retrofit. The fix is
trivial now and expensive after Phase 8 bakes in the import paths.
Change: Restructure `handlers/` and `pipelines/` into a `domains/` package with one
subdirectory per domain:

```
src/nodeforge/
    core/            ← domain-agnostic, unchanged
    domains/
        arxiv/
            __init__.py
            pipeline.py  ← moved from pipelines/arxiv.py
            handlers.py  ← moved from handlers/arxiv.py
```

`handlers/__init__.py` (which contains `register_all()`) moves into
`domains/arxiv/__init__.py` or becomes `domains/arxiv/register.py` — decide at
implementation time based on import clarity. All import paths in `main.py` and
`tests/` updated accordingly. Future domains (VFX, Phase 10 rendering) follow the
same pattern without any change to `core/`.

Done when: All imports updated, all 44 tests pass without modification, directory
structure reflects the domain-agnostic/domain-specific split at a glance.
`core/` is untouched. MCP server in Phase 8 imports from the new paths from the start.

---

### AMD-012 — Mermaid Diagrams in README and Documentation
Affects: Phase 9 (Documentation & Visibility) — or earlier, since repo is already public
Status: PENDING
Decided: 2026-03
Reason: GitHub natively renders Mermaid diagrams inside any Markdown file with no
setup required. The NodeForge thesis is about graph architecture. A visitor landing
on the repo currently reads about graphs — with Mermaid they see one immediately,
before running any code. For a credibility artifact aimed at AI company leadership,
the argument becoming visual before it becomes technical is significant. Three diagrams
carry the weight: the arXiv pipeline execution flow, the lookdev pipeline, and the
core architecture diagram showing `core/` as domain-agnostic with `domains/` plugging
in. The third diagram directly addresses the confusion new visitors have about the
directory structure and the domain-agnostic claim.
Change: Add three Mermaid diagrams to the README: (1) arXiv pipeline — nodes, edges,
edge types, execution flow; (2) lookdev pipeline — same treatment; (3) architecture
diagram — `core/` at center, `domains/arxiv/` and future domains as satellites, CLI
and MCP as operators on the graph. All diagrams use the fenced code block format
GitHub renders natively. No plugins, no external tools, no setup required.
Done when: A visitor landing on the GitHub repo sees rendered graph diagrams without
clicking anything or running any code. The architecture diagram makes the
domain-agnostic/domain-specific split legible at a glance.

---

### AMD-013 — USD Composition Inversion Domain (Phase 10 Extension)
Affects: Phase 10 (planned extension) — architecture awareness required from Phase 8 onward
Status: PENDING — not a current build target; no phase gate
Decided: 2026-03
Reason: USD composition is one of the hardest conceptual surfaces in production VFX.
LIVRPS precedence ordering is a formal, deterministic rule set — but the inverse problem
("what composition strategy produces this opinion?") requires reasoning backward through
that rule set under constraints that are often stated in natural language by the person
asking. This is precisely the class of problem NodeForge's architecture is designed for,
and the domain makes the thesis unusually concrete: a probabilistic tool cannot enumerate
valid USD composition strategies reliably because USD semantics are formal, not
probabilistic. Correct reasoning here requires a deterministic rule graph, not inference
over uncertainty.

PGMs were considered and rejected. USD composition has no inherent probability
distribution — it is a formal system with exact evaluation rules. A PGM applied here
would be modeling uncertainty that doesn't exist in the domain and would produce
rankings that are not inspectable or reproducible from first principles. That is the
failure mode the thesis argues against. The correct approach is backward chaining over
a composition rule graph: enumerate valid strategies, prune by constraint, rank by
explicit and auditable criteria.

The LLM call earns its place at exactly one boundary: translating the user's natural
language intent into a validated ConstraintSet the graph can operate on. A pipeline
TD says "I want the skin shader overridable per-shot without touching the asset." That
sentence contains an opinion target, an ownership constraint, and a change-isolation
requirement — none expressable in a formal language without interpretation. The LLM
performs that translation once and exits. Every subsequent step — constraint satisfaction,
strategy enumeration, validity checking, tradeoff explanation — is deterministic graph
traversal. The LLM is a node, not the orchestrator. This is the thesis in its most
pointed form.

**Gate: Constraint Extraction Must Be Validated First**

The natural language translation step is not abstract. It must map to a possible
configuration — a valid, well-formed USD opinion specification with a prim path,
attribute name, value type, and layer context. The set of legal USD opinions is closed
and formally defined. Either the natural language maps to a validated member of that
set, or it does not. This is a binary gate, not a fuzzy translation.

This is the correct first milestone and must be assessed before any solver work begins.
If the gate fails at high rates on realistic production language, nothing downstream
matters. If it passes reliably, the rest is an engineering problem.

The gate works as follows:

```
Natural language intent
        ↓
LLMCall node → ConstraintSet (structured, typed)
        ↓
validate_integrity() on ConstraintSet
        ↓
PASS: hand to backward-chaining solver
FAIL: return specific, structured error to user
```

The validation step is deterministic and independent of how the ConstraintSet was
generated. Failure modes are explicit: prim path not found in stage, attribute type
mismatch, ambiguous layer context. Every failure surfaces a specific question, not a
probabilistic uncertainty. The LLM is not making a judgment call — it is filling out
a typed form. The form validates or it doesn't.

The constraint extraction and validation layer can be built and tested entirely
independently of the solver. No backward chaining logic is required. No LIVRPS rule
graph is required. The milestone is: given a sentence of realistic production language,
does the system produce a valid ConstraintSet? Run against a representative sample of
production intent sentences. Measure pass rate. Characterize failure modes. That
assessment determines whether Phase 10 proceeds to solver implementation.

This sequencing is deliberate. Building the solver before validating the gate would
invert the risk. The gate is the hard dependency. The solver is bounded and tractable
once the gate is proven.

**Solver Scope (contingent on gate passing)**

Once the gate is validated, the inverse solver operates over a closed search space.
LIVRPS has six arc types. The solution space for a single opinion on a single prim
is bounded by definition — backward chaining through a rule graph with six edge types
and fixed precedence. This is an enumeration problem with deterministic pruning, not
general constraint satisfaction.

Tractability holds for the MVP scope: one opinion, one prim, pipeline context supplied
as structured input. Complexity grows with nested compositions and multi-prim
interactions, but those are edge cases for a proof-of-concept, not gates on it.

Change: Add a USD composition inversion domain to Phase 10 scope. New edge types under
AMD-003's extensible model: REFERENCES, INHERITS, SPECIALIZES, VARIANTS, PAYLOAD —
USD composition arc semantics as first-class typed edges. New node types: OpinionTarget
(the desired end state), CompositionStrategy (one valid path to that state),
ConstraintSet (pruning rules derived from pipeline context). The LLMCall node in this
domain has a single responsibility: parse natural language intent into a structured
ConstraintSet. All reasoning downstream is graph traversal using existing Phase 4.5
query infrastructure. Output is a ranked set of valid strategies with tradeoffs stated
explicitly and derivable from the rule graph — not from a model's learned prior.

Architecture requirements (carry forward from Phase 8):
- Edge type extensibility (AMD-003) must remain open — USD arc types must be addable
  without modifying the Edge model
- `summarize_intent()` must remain purely algorithmic — it will be called on composition
  strategy subgraphs and must not introduce LLM calls at the query layer
- The handler registry (AMD-007) must remain domain-agnostic — USD composition handlers
  register exactly as arXiv handlers do, no special-casing in the executor

**Test Datasets for Gate Validation**

The gate test requires USD files with inspectable composition arcs and known opinions
to verify constraint extraction against. Geometry and render fidelity are irrelevant —
structure and inspectability are the selection criteria. The following datasets are
identified as candidates in ranked order of utility for this purpose.

*Tier 1 — Primary (use these)*

**Pixar OpenUSD end-to-end tutorial** (`extras/usd/tutorials/endToEnd/` in the OpenUSD
repo — `github.com/PixarAnimationStudios/OpenUSD`)
Small, controlled, fully inspectable usda, directly from the authors of LIVRPS. The
composition structure is minimal and completely known — sequence/shot organization,
department sublayers, shading variants. Use this first, before any production dataset.
It is the unit test before the integration test: a small set of manually written gate
test sentences with known correct answers, verified against files whose ground truth
is unambiguous.

**Animal Logic ALab — Asset Structure package** (ASWF Digital Production Example
Library — `github.com/DigitalProductionExampleLibrary/ALab`)
The primary production-scale candidate. The Asset Structure package is pure composition
— no geometry, shaders, or lights — exposing how 300+ real production assets relate to
each other through USD composition arcs including references, variants, assemblies, and
shot-based overrides. Derived from actual Animal Logic productions, not constructed as
a reference implementation. Free, openly licensed under the ASWF Digital Assets
Licence, and explicitly released for education, training, and demonstration. This is
the integration test: realistic production composition structure at scale.

*Tier 2 — Useful for specific coverage*

**NVIDIA da Vinci Workshop** (NVIDIA Omniverse sample datasets —
`docs.omniverse.nvidia.com/usd/latest/usd_content_samples/davinci_workshop.html`)
A full USD film-making production pipeline demo with documentation stating why specific
composition arc choices were made. The documentation is the primary asset for this
project — it provides composition decisions paired with stated intent, which is the
format needed to write realistic gate test sentences. The dataset itself (67GB) requires
an Omniverse Kit application and is not needed for gate testing. Use the documentation
to author test sentences; verify against ALab files.

**ASWF USD Working Group Assets** (listed in `github.com/matiascodesal/awesome-openusd`)
Designed specifically to test USD support across tools. Likely to cover deliberate edge
cases and stress tests of composition behavior. Useful for the hard end of the gate
test sample set once baseline pass rate is established.

*Tier 3 — Secondary*

**Pixar USD repo `extras/` folder** — synthetic, tiny, guaranteed correct. Use for
smoke-testing the ConstraintSet validation logic before running against real files.

**SideFX sample assets** (Mexican Still Life, Bar Scene, Market Scene — available via
SideFX) — Houdini-authored USD with Houdini-specific composition patterns. Useful if
coverage of LOPs/Solaris conventions is needed; secondary to ALab for the gate test.

Recommended sequence: Pixar tutorial assets → ALab Asset Structure package →
ASWF Working Group assets for edge case coverage. Da Vinci Workshop documentation
used throughout as a source of intent-to-composition sentence pairs.

**Forward-Looking Note: SemanticProjection as Image Generation Constraint**

Not a Phase 10 build target. Recorded here as a downstream application the architecture
makes possible without requiring it to be built.

The SemanticProjection node produces deterministic, labeled masks derived directly from
graph structure — object IDs, material regions, surface boundaries — with explicit
provenance back to specific nodes. These masks are structurally equivalent to
cryptomattes and are suitable as hard constraints on image generation systems.

The inversion matters: masks are projected from the graph, not extracted from generated
images. The probabilistic generator operates inside semantically defined regions whose
boundaries come from ground truth. Every constraint is traceable to a node. Deviations
between the semantic projection and generated output are measurable as structured errors,
not visual impressions.

This is meaningfully different from ControlNet-style conditioning, which conditions on
learned or observed data. Conditioning on a formal semantic graph with explicit
provenance is derived, not learned. That distinction is thesis-relevant.

The argument does not require this to be built. Phase 10's proof — semantic graph →
fields → projections — is complete without a probabilistic system involved. Adding
image generation to Phase 10 would introduce a second major system, invite comparison
with generative art pipelines, and dilute the architectural claim. The note stands; the
build target does not.

If pursued, this is a post-Phase 10 application layer, not a core phase.

Done when: Phase 10 proceeds in two sequential milestones:
1. **Gate milestone**: The constraint extraction layer is implemented and validated
   against a representative sample of production intent sentences. Pass rate and failure
   modes are documented. Decision to proceed to solver is made explicitly based on this
   evidence — not assumed.
2. **Solver milestone** (contingent on gate): A user can state a desired USD opinion
   in natural language, receive a ranked set of valid composition strategies with
   explicit tradeoffs, and trace any strategy back to the specific rule graph edges
   that validate it — without any probabilistic inference in the reasoning chain.

---


## AMD-014 — Port Typing and Type Registry

Affects: Schema (immediate) — enforcement (post-Phase-8 build target)
Status: PENDING
Decided: 2026-03

---

### Reason

NodeForge's current schema connects nodes by ID. Edges carry no type information.
Ports are implicit: a node's inputs and outputs are inferred from params and
execution results, not declared. This is acceptable for simple demonstration graphs.
It is a credibility failure at production scale.

Production node graphs are not small. At Rhythm & Hues, the average Houdini scene
contained 250,000 nodes. At that scale, implicit data contracts are not a maintenance
inconvenience — they are a catastrophic failure mode. A type mismatch at node 180,000
is undebuggable without declared port contracts. The system produces wrong answers
silently. This is precisely the failure mode the NodeForge thesis argues against.

Two additional pressures make this non-deferrable:

**1. Reviewer credibility.** Any senior engineer or pipeline architect reviewing
NodeForge will immediately ask what happens at scale. Without port typing, the honest
answer is: "the graph becomes opaque and failures surface at runtime." That concedes
the thesis in the review. Port typing is the architectural answer to that question —
it must exist in the design, even if full enforcement lands post-Phase-8.

**2. AMD-013 collision.** The USD composition inversion domain (AMD-013) requires
graph composition — overlaying partial graphs, chaining constraint satisfaction steps,
composing pipeline stages. Graph composition across boundaries without declared port
contracts is implicit. What flows across the boundary is unknown at schema validation
time. This is the probabilistic model smuggled into the architecture through the
composition seam. Typed ports with a type registry are the structural solution.

---

### Design model

The Houdini/Katana port model is the correct reference architecture:

- Nodes declare typed **input ports** and **output ports** — named slots with an
  explicit type reference.
- Edges connect a named output port on one node to a named input port on another.
- Port types are defined as named schemas in a **type registry** — a top-level key
  in the graph JSON document.
- At graph validation time, port types on both ends of an edge are checked for
  compatibility. This is a schema check, not an execution check — deterministic
  by definition.
- Subgraph nodes re-export their internal boundary nodes' ports as their own
  external interface. The internal graph remains fully inspectable. The external
  interface is the declared contract only.

The struct-as-protocol pattern is the key property: the port type (a named schema)
defines the full data contract at the boundary. A node that passes a struct through
and modifies only one field does not need to know anything about the other fields.
It operates on the declared contract, not on assumptions about what is upstream.
This is auditability enforced structurally, not by convention.

The type registry lives as a top-level key in the graph JSON — not a separate file,
not a system-level store. Everything needed to validate and inspect the graph is in
one serializable data artifact. An agent querying the graph via MCP should be able
to retrieve the type registry as a first-class graph object.

---

### Schema changes — immediate (optional fields, no enforcement yet)

Add to **Node schema**:

```json
"input_ports": [
  { "name": "surface_props", "port_type": "SurfaceProperties" }
],
"output_ports": [
  { "name": "surface_props", "port_type": "SurfaceProperties" }
]
```

Both fields are **optional**. Nodes without declared ports are valid — they behave
as they do today. No existing tests break. No current behavior changes.

Add to **Edge schema**:

```json
"from_port": "surface_props",
"to_port": "surface_props"
```

Both fields are **optional**. Edges without port references are valid and behave
as today.

Add to **Graph schema** (top level):

```json
"type_registry": {
  "SurfaceProperties": {
    "albedo": "vec3",
    "roughness": "float",
    "metallic": "float",
    "normal": "vec3"
  }
}
```

Optional at graph level. An empty or absent registry is valid. No validation logic
is attached yet.

---

### Enforcement — post-Phase-8 build target

Full port validation is a discrete build target after Phase 8 (MCP integration)
is complete. It is not a Phase 10 stretch goal. It is a credibility requirement
that must land before the essay publishes or the repo is presented as a primary
portfolio artifact.

Enforcement scope:

- At graph load time, if a node declares ports, validate that all named port types
  exist in the type registry.
- At graph validation time, if an edge declares `from_port` and `to_port`, validate
  that the port types on both ends are compatible.
- Incompatible port types are a **validation error**, not a runtime error. The graph
  must not execute with a schema-invalid edge. This is the determinism guarantee.
- `validate_integrity()` (already in the public API) is extended to cover port
  type consistency. No new public API entry point required.

Compatibility rules are a Phase 8.5 design task. Minimum viable rule: exact type
match. Structural subtyping (a superset struct is compatible with a subset port)
is a refinement, not a gate.

---

### Subgraph implications

A SubgraphNode (not yet built) wraps an inner graph. Its external ports are the
re-exported ports of its internal boundary nodes. The internal graph is fully
inspectable — same schema, same type registry, same validation rules. From outside,
the subgraph looks like any other node: declared inputs, declared outputs, declared
types. The boundary is explicit and enforced.

This is the architectural answer to the scale objection: at 250,000 nodes, you
navigate by contract, not by inspection. The contract is the graph schema.

---

### Architectural constraints added

| Decision | Affects | Rationale |
|---|---|---|
| Node schema must support optional `input_ports` / `output_ports` | Immediate | Required for port model without breaking existing graphs |
| Edge schema must support optional `from_port` / `to_port` | Immediate | Required for typed edge connections without breaking existing edges |
| Graph schema must support optional `type_registry` top-level key | Immediate | Type definitions must live in the graph document, not a separate store |
| Port type enforcement is a post-Phase-8 build target, not Phase 10 | Post-Phase-8 | This is a credibility requirement. Phase 10 is not the right gate. |
| `validate_integrity()` is the enforcement entry point | Post-Phase-8 | No new public API method required; extend existing validation surface |

---

### Open questions added

| Question | Raised | Notes |
|---|---|---|
| What are the compatibility rules for port types? | 2026-03 (AMD-014) | Minimum viable: exact match. Structural subtyping is a refinement. Decide at enforcement implementation, not now. |
| How does the type registry handle versioned types? | 2026-03 (AMD-014) | A `SurfaceProperties_v2` is a different type, not a version of `SurfaceProperties`. Versioning strategy is a Phase 8.5 design question. |
| Should MCP expose `get_type_registry` as a named tool? | 2026-03 (AMD-014) | Likely yes — an agent inspecting a graph needs the type registry to interpret port declarations. Decide at Phase 8 MCP tool surface design. |

---

*Amendment: AMD-014*
*Follows: AMD-013*
*Last updated: 2026-03*


## AMD-015 — Pre-Phase-8 Execution Sequence

Affects: AMD-010, AMD-011, AMD-014 (sequencing only — no schema or code changes)
Status: PENDING
Decided: 2026-03

---

### Reason

AMD-014 inserted a new schema work item into a sequence that previously contained
only AMD-011 and AMD-010. The correct ordering is not obvious from reading the
individual amendments. This amendment records the rationale for the sequence so it
survives session boundaries and can be executed without reconstruction.

---

### Sequence

**Step 1 — AMD-011: Domain-first directory restructure**

Must be first. MCP import paths are determined by the directory structure. If AMD-011
runs after Phase 8 begins, the MCP server is built against paths that will change —
a guaranteed breaking refactor mid-phase. All subsequent work assumes the
`domains/arxiv/` layout is in place.

Gate: All 44 tests pass against the new paths. `core/` is untouched.

**Step 2 — AMD-014: Optional port fields on Node, Edge, and Graph schemas**

Must run before Phase 8 locks the schema surface. The Pydantic model additions are
non-breaking — all optional fields, no existing tests affected. If this runs after
Phase 8, the MCP server's tool surface (graph inspection, `validate_integrity`) is
built against a schema that will need a breaking change when port enforcement lands.
Adding the fields now costs almost nothing. Adding them after MCP is built costs
a refactor of the public API.

Gate: `input_ports`, `output_ports` optional on Node. `from_port`, `to_port`
optional on Edge. `type_registry` optional on Graph. All 44 tests pass unchanged.

**Step 3 — AMD-010: Mock flag, .env.example, README**

No dependency on AMD-011 or AMD-014. Can run in parallel with Step 2 if two
workstreams are open, or sequentially after Step 2. Placed last because it has
no downstream blocking effect — Phase 8 can begin without it, though it should
be complete before the repo is presented publicly.

Gate: `nodeforge run 1706.03762 --mock` executes without API key. `.env.example`
committed. README section present.

**Phase 8 begins after all three gates are passed.**

---

### Summary table

| Order | Amendment | Blocks | Can parallelize with |
|---|---|---|---|
| 1 | AMD-011 directory restructure | Phase 8, AMD-014 import paths | Nothing — must be first |
| 2 | AMD-014 schema additions | Phase 8 schema lock | AMD-010 |
| 3 | AMD-010 mock flag + README | Public repo presentation | AMD-014 |

---

*Amendment: AMD-015*
*Follows: AMD-014*
*Last updated: 2026-03*

## Architectural Constraints Log — Additions

New rows to append to the constraints table in `blueprint_amendments-1.md`:

| Decision | Affects | Rationale |
|---|---|---|
| `nodeforge run` must be demonstrable without an API key | Phase 8 onward | Covered in AMD-010. The `--mock` flag is the mechanism. The architecture already supports this via the handler registry — the flag makes it explicit at the CLI surface. |
| Domain implementations live under `domains/<domain>/`, never as siblings to `core/` | Phase 8 onward | Covered in AMD-011. `core/` is domain-agnostic. Domain-specific code belongs in its own namespace. The directory structure should communicate the architecture without a README. |
| Edge type extensibility must remain open for USD arc types | Phase 8 onward | Covered in AMD-013. USD composition arc semantics (REFERENCES, INHERITS, SPECIALIZES, VARIANTS, PAYLOAD) must be addable as typed edges without modifying the Edge model. AMD-003 already enforces this; AMD-013 names the concrete future use. |
| `summarize_intent()` must remain purely algorithmic — no LLM calls at the query layer | Phase 8 onward | Covered in AMD-013. Will be called on composition strategy subgraphs in Phase 10. An LLM call inside the query layer would undercut the determinism thesis at its foundation. |

---

## Open Questions — Additions

| Question | Raised | Notes |
|---|---|---|
| Should `register_all()` live in `domains/arxiv/__init__.py` or a dedicated `domains/arxiv/register.py`? | 2026-03 | Decide at AMD-011 implementation. `__init__.py` is simpler; a dedicated file is more explicit about what it does. Either is acceptable — consistency with the existing handler registration pattern matters more than the specific choice. |
| What is the ConstraintSet schema? | 2026-03 (AMD-013) | Must be defined before gate milestone work begins. Minimum fields: prim path, attribute name, value type, layer context, ownership constraints. Exact schema is a Phase 10 design task, not a current gate. |
| How does the backward-chaining solver handle contradictory constraints? | 2026-03 (AMD-013) | Contradiction is a first-class output, not an error. The solver should return a structured result explaining which constraints are in conflict and why — traceable to specific LIVRPS rules. Phase 10 design task. |

---

*Last updated: 2026-03*
*Owner: NodeForge project*
