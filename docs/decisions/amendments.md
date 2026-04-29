# Idiograph – Blueprint Amendments & Decision Log

This document tracks amendments to the original Blueprint, and the reasoning behind each one.
The Blueprint itself is not modified — this file sits alongside it as the living layer.

Each entry records: what changes, which phase it affects, why it was decided, and what "done" looks like.

---

## Amendment Format

```
### AMD-[N] — [Short Title]
Affects: Phase X
Status: PENDING | ACTIVE | COMPLETE
Decided: [date or session]
Reason: [why]
Change: [what specifically is added or altered]
Done when: [concrete completion criterion]
```

---

## Amendments

---

### AMD-001 — Pydantic Field Descriptions as Agent Documentation
Affects: Phase 3 (Data Models & Typing)  
Status: COMPLETE  
Decided: 2026-03  
Reason: An agent calling a tool like `update_node()` needs to understand what each parameter
means without a human writing a wrapper to explain it. `Field(description="...")` on every
model field turns the schema itself into agent-readable documentation. Costs nothing in Phase 3,
pays dividends in Phase 8 when tool interfaces are generated from those schemas.  
Change: Every Pydantic field on `Node`, `Edge`, and `Graph` models must include a
`description=` argument written for an agent reader, not just a human one. Treat it like
docstrings for tools, not comments for developers.  
Done when: All Phase 3 model fields have `Field(description="...")` and the JSON Schema
output (via `model.model_json_schema()`) reads as a self-contained agent manual.

---

### AMD-002 — Semantic Intent Summary Tool
Affects: Phase 4.5 (Graph Query & Analysis)  
Status: PENDING  
Decided: 2026-03  
Reason: Phase 4.5 plans traversal, query, and cycle detection — all structural operations.
What's missing is a tool that answers a different kind of question: "what does this subgraph
*do*?" An LLM cannot reason effectively over a raw 1000-node graph. It needs a summarized,
intent-oriented view. This is distinct from `summarize()` (which is statistics) — it's a
semantic description oriented toward agent reasoning and decision-making.  
Change: Add a `summarize_intent(graph, subgraph_ids=None)` function to the query layer.
Output is a structured dict (JSON-serializable) describing: what the subgraph computes,
its domain (VFX / AI / mixed), its critical path, and any failure points or bottlenecks.
Expose via CLI as `idiograph query intent`.  
Done when: An agent can call `summarize_intent()` on a subgraph and receive a structured
response that meaningfully answers "what does this do and where might it fail?" without
needing to inspect individual nodes.

---

### AMD-003 — Edge Type Must Be Extensible, Not a Closed Enum
Affects: Phase 3 (Data Models & Typing)  
Status: COMPLETE  
Decided: 2026-03  
Reason: Phase 10 requires causal edge semantics beyond DATA and CONTROL — specifically
MODULATES, DRIVES, OCCLUDES, EMITS, and PROJECTS_TO. A closed `Literal` or `Enum` on
the `Edge` model would require a breaking change to accommodate these. The fix is trivial
in Phase 3 but expensive to retrofit later.  
Change: The `type` field on the `Edge` model must be defined as an open string (`str`),
not a closed `Literal["DATA", "CONTROL"]` or Python `Enum`. Validation of known types
can still be documented via `Field(description="...")` and enforced in a validator if
desired, but the field itself must accept arbitrary string values to remain extensible.  
Done when: The `Edge` model accepts "DATA", "CONTROL", and any future string type without
a code change to the model definition. Verified by a test that constructs an edge with
type "MODULATES" and confirms it passes validation.

---

### AMD-004 — MCP as Explicit Implementation Target for Phase 8
Affects: Phase 8 (Agent Integration)  
Status: PENDING  
Decided: 2026-03  
Reason: The Blueprint says "integrate with LLM frameworks" — MCP (Model Context Protocol)
is now the dominant open standard for exactly this. Naming it explicitly means Phase 8 has
a concrete delivery target rather than a vague integration goal. MCP means any agent
(Claude, GPT, local models) can connect to Idiograph without bespoke adapters. This also
strengthens the thesis demo: you're not showing a Claude-specific tool, you're showing a
standards-compliant agent-operable system.  
Change: Phase 8 delivery target is an MCP Server wrapping Idiograph's core tool interfaces.
Tools exposed via MCP: `get_node`, `get_edges_from`, `update_node`, `summarize_intent`,
`validate_graph`, `execute_graph` (once Phase 6 is complete). The MCP server is the
proof-of-concept artifact for the thesis — not just an integration detail.  
Done when: A Claude agent (or equivalent) can connect to the Idiograph MCP server,
inspect a graph, modify a node parameter, and re-validate — with no human-written adapter
code between the agent and the graph.

---

### AMD-005 — Graph-Level Referential Integrity Validation
Affects: Phase 4.5 (Graph Query & Analysis)  
Status: PENDING  
Decided: 2026-03  
Reason: Pydantic validates each model in isolation. The `Edge` model has no visibility into
the node list, so an edge referencing a non-existent node ID passes schema validation
silently. This is not a Phase 3 problem — the model layer is not the right place to enforce
cross-object constraints. But it becomes a real problem in Phase 4.5 when traversal follows
edges to nodes that don't exist, producing silent `None` returns or unexpected query behavior.  
Change: Add a graph-level integrity check function — `validate_integrity(graph)` — to the
query/analysis layer in Phase 4.5. It must verify that every `source` and `target` ID on
every edge corresponds to a node that exists in the graph. Returns a structured result
listing any dangling edge references. Expose via CLI as part of the `validate` command or
as a separate `idiograph check` command.  
Done when: A graph containing an edge that references a non-existent node ID is caught and
reported with the specific edge and missing node ID identified. Clean graphs pass silently.

---

### AMD-006 — arXiv Pipeline as Phase 6 Demo Domain
Affects: Phase 6 (Async & Orchestration)  
Status: ACTIVE  
Decided: 2026-03  
Reason: The Blueprint describes Phase 6 as making the graph executable, but does not
specify what executes. The original intent implied VFX handlers — `ShaderValidate`,
`RenderComparison`, etc. — operating on production assets. Two problems with that:

First, VFX production handlers require DCC tool dependencies (Arnold, USD, Hydra) that
would make Phase 6 primarily about installation and integration rather than execution
architecture. That inverts the priority.

Second, and more importantly: Idiograph is not a VFX tool. It is a proof-of-concept
system for the thesis. Using VFX-specific handler implementations risks making the system
look domain-specific rather than domain-agnostic. The thesis claims the architecture
works for any production pipeline — the demo domain should demonstrate that, not
contradict it.

The selected demo domain is an **arXiv abstract processing pipeline** using the public
arXiv API (no key required). This domain was chosen because:

- Data is real and free — no invented fixtures
- Natural pipeline structure: fetch → extract → evaluate → route → summarize
- The LLM node adds genuine value, not decoration — claim extraction from academic
  abstracts is a task with real variance and real failure modes
- The content domain (AI and graphics research) is directly relevant to the thesis
  narrative — processing AI research papers using an AI pipeline demonstrates the
  argument without any rhetorical overhead. The demo is self-referential in a useful way.
- No new dependencies beyond what Phase 8 already requires (Anthropic API)

The pipeline structure:

```
FetchAbstract → LLMCall (extract claims) → Evaluator → Router
                                                          ├── LLMCall (technical summary)
                                                          └── Discard
```

Node responsibilities:
- `FetchAbstract` — hits arXiv API, returns structured abstract data. Real network I/O
  that fails realistically on bad IDs, network errors, or malformed responses.
- `LLMCall (claims)` — extracts concrete, falsifiable claims from the abstract. Output
  variance is intentional — the evaluator must handle both crisp and vague abstracts.
- `Evaluator` — scores claims against criteria defined in `params`, not hardcoded in
  logic. Threshold also lives in params. An agent can modify both without touching code.
- `Router` — activates one downstream branch via CONTROL edges based on evaluator output.
  The routing decision is preserved in graph state, not lost in an if/else.
- `LLMCall (summary)` — produces a technical summary for papers that pass evaluation.
- `Discard` — a no-op node that records the rejection. The graph preserves the decision
  trail even for dead ends.

The node type names carry VFX pipeline semantics where appropriate (e.g. `Evaluator`
maps conceptually to `ShaderValidate` — a quality gate with defined criteria). The
handler implementations are domain-specific, but the graph structure is identical.

Change: Phase 6 implementation uses the arXiv pipeline as its primary execution demo.
VFX node types remain in the schema and sample pipeline. The executor is not
VFX-specific. Handler implementations for the arXiv domain live in a separate module
and are registered via the handler registry (see AMD-007).

Done when: The arXiv pipeline graph executes end-to-end via the Phase 6 executor,
with at least one realistic failure demonstrated (bad paper ID, evaluator threshold
not met, LLM output that fails parsing) and that failure surfaced legibly in graph state.

---

### AMD-007 — Handler Registry Pattern for Executor
Affects: Phase 6 (Async & Orchestration)  
Status: ACTIVE  
Decided: 2026-03  
Reason: The execution engine must remain abstract. If the executor contains references
to specific handler implementations (arXiv, LLM calls, etc.), it becomes coupled to
those implementations and loses the property the thesis depends on: that the graph
architecture is domain-agnostic.

The standard solution for this class of problem is a handler registry — a mapping from
node type strings to async handler functions. The executor looks up a handler by
`node.type`, calls it with `(params, inputs)`, and returns the output dict. The executor
never imports handler modules directly.

```python
# Executor sees only this interface
HANDLERS: dict[str, Callable] = {}

def register_handler(node_type: str, fn: Callable) -> None:
    HANDLERS[node_type] = fn

async def execute_node(node: Node, inputs: dict) -> dict:
    handler = HANDLERS.get(node.type)
    if handler is None:
        raise ValueError(f"No handler registered for node type '{node.type}'")
    return await handler(node.params, inputs)
```

Handler implementations are registered at application startup, not imported into the
executor. This means:

- The executor can be tested with stub handlers independently of real implementations
- New node types require no changes to the executor — only a new registration
- The same executor runs VFX handlers, AI handlers, or any future domain without
  modification
- An agent operating on the graph never needs to know what the handler does — only
  what the node type expects as input and produces as output

This is a standard plugin/dispatch pattern. Apache Airflow uses it for operators.
Prefect uses it for tasks. The pattern is well-understood and carries no architectural
risk.

Change: The Phase 6 executor is implemented with a handler registry. Handler
implementations (arXiv domain) live in `src/idiograph/handlers/arxiv.py` and are
registered in `src/idiograph/handlers/__init__.py`. The executor lives in
`src/idiograph/core/executor.py` and imports nothing from the handlers module.

Done when: The executor can be instantiated and run with stub handlers registered
programmatically, independently of whether the arXiv handlers are present. Verified by
tests that register mock handlers and confirm execution order, data flow, and failure
handling without any real I/O.

---

### AMD-008 — Handler Scope Constraint (30-Line Business Logic Rule)
Affects: Phase 6 onward  
Status: ACTIVE  
Decided: 2026-03  
Reason: There is a real risk in Phase 6 that handler implementations grow into their
own subsystems. If a handler requires 200 lines to function correctly, one of two things
is true: either the handler is doing too much (the node should be decomposed into
multiple nodes), or the implementation detail has become the project.

Either outcome undermines the thesis. Idiograph is an architecture demonstration, not
a pipeline tool. Handler implementations are evidence that the architecture works under
real conditions — they are not the system.

The 30-line constraint applies to **business logic only** — the core computation a
handler performs. Error handling, logging, and type coercion are necessary for the system
to behave correctly at runtime and are not counted toward this limit. A handler that
requires significant error handling around a trivial core operation is not a scoping
violation; it is responsible production code.

The constraint is a **code review flag**, not a hard tooling gate. When a handler's
business logic approaches or exceeds 30 lines, the correct response is to stop and ask
whether the node is correctly scoped. The answer to that question is itself a
thesis-relevant finding: one of the architectural properties of a well-designed semantic
graph is that nodes are small, composable, and independently replaceable. A handler
whose logic cannot be expressed concisely is a signal that the node boundary is in the
wrong place.

Change: Handler business logic in Phase 6 (and subsequent phases) should not exceed
30 lines. If it does, decompose the node in the graph rather than extend the handler.
Reviewed at each phase post-mortem by convention, not automated tooling.

Done when: All Phase 6 handler implementations are reviewed at post-mortem. Any handler
whose business logic approached or exceeded the limit is noted, with an explanation of
whether the response was decomposition, justified exception, or a flag for later
refactoring.

---

### AMD-009 — State Management Migration Strategy
Affects: Phase 7 / Pre-Phase 8 (conditional)  
Status: PENDING — awaiting forcing function evaluation at Phase 7 close  
Decided: 2026-03  
Reason: The current module-level graph state model (`SAMPLE_PIPELINE`, `ARXIV_PIPELINE`
imported directly by command functions) is incompatible with stateless HTTP serving and
concurrent agent requests. This was the correct decision for Phases 0–6: the CLI is a
single-process, single-command model and the constraint is invisible under that execution
model. It becomes a real liability the moment Idiograph handles concurrent requests —
two agents mutating the same graph object simultaneously produces undefined behavior,
and there is no mechanism for graph isolation, persistence, or session state across calls.

Remediation is a defined 5-step strangler fig migration documented in
`docs/sessions/session-2026-04-06-2.md`. It is non-trivial (4–5 focused micro-sessions) and
should not be confused with "adding FastAPI" (a surface-level description of Step 5 only).

The forcing function: this migration is required before Phase 8 begins **only if** the
MCP server requires HTTP/SSE transport (rather than stdio), a web demo is required, or
multiple graphs must be managed simultaneously. If none of those conditions are met,
the constraint remains a documented liability and the migration is deferred indefinitely.  
Change: Evaluate forcing function at Phase 7 close. If triggered, execute the 5-step
migration (extract loaders → graph registry → executor isolation test → JSON persistence
→ FastAPI layer) before Phase 8 implementation begins. Full migration detail in
`docs/sessions/session-2026-04-06-2.md`.  
Done when: Forcing function evaluated at Phase 7 close. Migration executed if triggered;
constraint documented and deferred if not.

---

### AMD-010 — Mock Execution Mode for Dependency-Free Demo
Affects: Phase 8 (or pre-Phase 8 cleanup)
Status: PENDING
Decided: 2026-03
Reason: The live `idiograph run` command requires an Anthropic API key, creating a
barrier for anyone evaluating the repo — collaborators, interviewers, or potential
employers. The 44-test suite already runs without a key via stub handlers — this is
not incidental, it is the handler registry pattern doing exactly what it was designed
to do (AMD-007). The `--mock` flag extends that property to the CLI demo, making the
full execution pipeline demonstrable on any machine without credentials. The
architecture is the thesis artifact. A key requirement gates access to the architecture.
Change: Add `--mock` flag to `idiograph run`. When set, registers lightweight stub
handlers that return plausible structured output without any API calls or network
access. The full pipeline executes: topological sort, node status progression, results
dict, failure handling — all demonstrable without a key. Add `.env.example` to the
repo root with the key name but no value. Add an explicit section to the README:
"Running tests requires no API key. Running the live demo requires one. Run with
`--mock` to see the full pipeline without credentials."
Done when: `idiograph run 1706.03762 --mock` executes the full arXiv pipeline,
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
src/idiograph/
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
setup required. The Idiograph thesis is about graph architecture. A visitor landing
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
asking. This is precisely the class of problem Idiograph's architecture is designed for,
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

### AMD-014 — Port Typing and Type Registry

Affects: Schema (immediate) — enforcement (post-Phase-8 build target)
Status: PENDING
Decided: 2026-03

---

#### Reason

Idiograph's current schema connects nodes by ID. Edges carry no type information.
Ports are implicit: a node's inputs and outputs are inferred from params and
execution results, not declared. This is acceptable for simple demonstration graphs.
It is a credibility failure at production scale.

Production node graphs are not small. At Rhythm & Hues, the average Houdini scene
contained 250,000 nodes. At that scale, implicit data contracts are not a maintenance
inconvenience — they are a catastrophic failure mode. A type mismatch at node 180,000
is undebuggable without declared port contracts. The system produces wrong answers
silently. This is precisely the failure mode the Idiograph thesis argues against.

Two additional pressures make this non-deferrable:

**1. Reviewer credibility.** Any senior engineer or pipeline architect reviewing
Idiograph will immediately ask what happens at scale. Without port typing, the honest
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

#### Design model

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

#### Schema changes — immediate (optional fields, no enforcement yet)

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

#### Enforcement — post-Phase-8 build target

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

#### Subgraph implications

A SubgraphNode (not yet built) wraps an inner graph. Its external ports are the
re-exported ports of its internal boundary nodes. The internal graph is fully
inspectable — same schema, same type registry, same validation rules. From outside,
the subgraph looks like any other node: declared inputs, declared outputs, declared
types. The boundary is explicit and enforced.

This is the architectural answer to the scale objection: at 250,000 nodes, you
navigate by contract, not by inspection. The contract is the graph schema.

---

#### Architectural constraints added

| Decision | Affects | Rationale |
|---|---|---|
| Node schema must support optional `input_ports` / `output_ports` | Immediate | Required for port model without breaking existing graphs |
| Edge schema must support optional `from_port` / `to_port` | Immediate | Required for typed edge connections without breaking existing edges |
| Graph schema must support optional `type_registry` top-level key | Immediate | Type definitions must live in the graph document, not a separate store |
| Port type enforcement is a post-Phase-8 build target, not Phase 10 | Post-Phase-8 | This is a credibility requirement. Phase 10 is not the right gate. |
| `validate_integrity()` is the enforcement entry point | Post-Phase-8 | No new public API method required; extend existing validation surface |

---

#### Open questions added

| Question | Raised | Notes |
|---|---|---|
| What are the compatibility rules for port types? | 2026-03 (AMD-014) | Minimum viable: exact match. Structural subtyping is a refinement. Decide at enforcement implementation, not now. |
| How does the type registry handle versioned types? | 2026-03 (AMD-014) | A `SurfaceProperties_v2` is a different type, not a version of `SurfaceProperties`. Versioning strategy is a Phase 8.5 design question. |
| Should MCP expose `get_type_registry` as a named tool? | 2026-03 (AMD-014) | Likely yes — an agent inspecting a graph needs the type registry to interpret port declarations. Decide at Phase 8 MCP tool surface design. |

---

### AMD-015 — Pre-Phase-8 Execution Sequence

Affects: AMD-010, AMD-011, AMD-014 (sequencing only — no schema or code changes)
Status: PENDING
Decided: 2026-03

---

#### Reason

AMD-014 inserted a new schema work item into a sequence that previously contained
only AMD-011 and AMD-010. The correct ordering is not obvious from reading the
individual amendments. This amendment records the rationale for the sequence so it
survives session boundaries and can be executed without reconstruction.

---

#### Sequence

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

Gate: `idiograph run 1706.03762 --mock` executes without API key. `.env.example`
committed. README section present.

**Phase 8 begins after all three gates are passed.**

---

#### Summary table

| Order | Amendment | Blocks | Can parallelize with |
|---|---|---|---|
| 1 | AMD-011 directory restructure | Phase 8, AMD-014 import paths | Nothing — must be first |
| 2 | AMD-014 schema additions | Phase 8 schema lock | AMD-010 |
| 3 | AMD-010 mock flag + README | Public repo presentation | AMD-014 |

---

<!-- AMD-016 appended from standalone file during doc consolidation 2026-04-15 -->

### AMD-016 — LLM Node Placement in the arXiv Citation Pipeline

**Affects:** spec-arxiv-pipeline-final.md — adds two new nodes
**Status:** Accepted — Not Yet Implemented (v2 — vocabulary and renderer contract corrected)
**Decided:** 2026-04-07
**Follows:** AMD-015

---

## Reason

The frozen arXiv pipeline spec (spec-arxiv-pipeline-final.md) contains no LLM calls. This is a critical omission: the thesis is not that LLMs should be excluded from production pipelines — it is that they should be used correctly. A demo with no LLM does not demonstrate correct LLM integration. It demonstrates a container without its most important occupant.

The thesis requires showing exactly two things about LLMs:

1. An LLM earns its place by doing one thing no deterministic method can do, receiving typed inputs, returning typed outputs, and exiting cleanly.
2. An LLM does not orchestrate, route, or make structural decisions.

Both must be visible in the demo. Two placements satisfy this requirement. They are at opposite ends of the pipeline, performing completely different tasks. The separation is intentional — it demonstrates the thesis twice, in two different contexts, with no ambiguity about where the LLM's responsibility begins and ends.

---

## Decision

Add two LLM nodes to the arXiv pipeline. Both are typed, auditable, and logged in provenance metadata. Neither makes structural decisions.

---

### Node 0.5 — Query Normalization *(new — on the Node 1 path only)*

**Position:** between user input and Node 1 (Structured Query). Bypassed entirely on the Node 0 path — a known arXiv ID or DOI does not require language normalization.

**Purpose:** translate sloppy, ambiguous, or malformed user input into valid structured query fields before Node 1 fires. Handles misspellings, overconstrained queries likely to return nothing, ambiguous author names, and category mismatches.

**Why this placement is correct:** Node 1 requires structured field inputs. The LLM sits at exactly the boundary between human intent and machine execution — the one boundary where language understanding is genuinely required and no simpler tool suffices. Once Node 0.5 exits, the pipeline is fully deterministic.

**Input:**
```
raw_query: dict   # whatever the user submitted
```

**Output:**
```
normalized_query: dict     # corrected, validated field values
warnings: list[string]     # one entry per issue found — empty if input was clean
confidence: float          # overall confidence in the normalization
```

**Behavior:**
- Always returns a `normalized_query` — even at low confidence
- Never blocks pipeline execution
- Warns, never gates
- Warnings surface in the renderer UI — not hidden in logs or provenance only
- If confidence falls below a declared threshold, the renderer displays a visible notice: "Query was modified — review changes before proceeding"

**Thesis connection:** the LLM is a translator at the human/machine boundary. It converts ambiguous natural language into typed structured data and exits. Every subsequent node receives clean, validated inputs. The LLM does not participate in any decision downstream of this point.

**Credibility rationale:** a technical evaluator who hits a null result because of a malformed query asks immediately why the system didn't catch it. In a project that claims to demonstrate correct AI integration in 2026, a missing input normalization layer is not a minor gap — it is a credibility failure before the demo starts. Node 0.5 is the answer to that question.

---

### Node 5.5 — Semantic Relationship Annotation *(new — runs after co-citation, before metrics)*

**Position:** after Node 5 (Co-citation Computation), before Node 6 (Metric Computation). Runs at pipeline build time, not demo runtime. Results stored in registry.

**Purpose:** for each node in the assembled graph, classify the intellectual relationship between that paper and the seed paper. The citation structure tells you *that* papers are related. Node 5.5 tells you *how*.

**Why this placement is correct:** by the time Node 5.5 runs, the graph structure is completely fixed. Traversal is done, cycle cleaning is done, co-citation edges are computed. Every node is in the graph because of deterministic structural criteria. The graph does not need the LLM to select, route, or modify structure. It needs the LLM to answer one question no deterministic method can answer: what is the intellectual relationship between these two abstracts? That is a language task. The LLM performs it once per node, returns a typed result, and exits. The result flows forward as a node property like any other field.

**Input (per node):**
```
seed_abstract: string
candidate_abstract: string
seed_node_id: string
candidate_node_id: string
```

**Output (per node):**
```
relationship_type: string    # closed vocabulary — see below
semantic_confidence: float
```

**Closed vocabulary for `relationship_type`:**

| Value | Definition |
|---|---|
| `methodological_precursor` | Introduces or establishes a method the seed directly uses or extends |
| `theoretical_foundation` | Provides the conceptual or mathematical basis the seed builds on |
| `cross_domain_source` | Provides theory, method, or framing from another domain that the seed directly uses or adapts |
| `downstream_application` | Applies the seed's ideas, methods, or results to a new domain or problem space |
| `empirical_validation` | Provides experimental evidence that supports, challenges, or contextualizes the seed's claims |
| `concurrent_work` | Addresses the same problem at roughly the same time, via a different approach |
| `adjacent_work` | Operates in the same problem space without a direct intellectual dependency — lateral, not directional |
| `unclear` | Abstract does not contain sufficient information to classify the relationship confidently |

**Why `adjacent_work` is in the vocabulary:** academic citation graphs contain many papers that co-appear not because one builds on the other but because both address the same neighborhood of problems. `adjacent_work` is the semantic label for the same signal that co-citation edges carry structurally. When a strong co-citation edge and an `adjacent_work` classification agree on the same pair of nodes, confidence in the relationship is higher. When they disagree, that is informative — worth surfacing in the renderer.

**Renderer surface:** `relationship_type` is a node-level property and maps to node-level visuals only — node shape and filter controls in the influence view. Edge color is governed by edge type (`cites` vs `co_citation`), which already has a declared visual treatment in the spec. Node properties drive node visuals; edge properties drive edge visuals. The co-citation toggle already exists; a relationship type filter gives the evaluator the ability to isolate intellectual lineage (`methodological_precursor` + `theoretical_foundation`) from the competitive landscape (`concurrent_work` + `adjacent_work`). The LLM's contribution is directly visible and interrogable.

**Performance:** 150–300 Haiku calls at pipeline build time. Cost paid once. Results cached in registry under the content-addressed cache key (same seed + parameters → same annotations, no recomputation).

**Provenance:** Node 5.5 run recorded in graph provenance metadata — model used, call count, parameter values, any calls that returned `unclear` and why.

**Thesis connection:** the LLM measures a semantic property of content that is already structurally committed in the graph. It does not select nodes, modify edges, or affect execution order. Its output is a typed field. It is auditable. Removing Node 5.5 would leave the graph structurally correct and semantically opaque — you would know that papers are related but not how. That gap is exactly where the LLM belongs.

---

## Relationship Between the Two Nodes

The two nodes are independent. They run at different points in the pipeline, perform different tasks, and operate on different inputs. The separation is not incidental — it demonstrates that correct LLM integration is a design pattern, not a one-time decision.

Node 0.5: LLM as translator. Human intent → machine input. Runs at query time.
Node 5.5: LLM as classifier. Content → semantic property. Runs at build time.

Neither node orchestrates anything. Neither node's output determines pipeline structure. Both produce typed results that flow through the graph like any other field.

---

## Spec Amendment Required

`spec-arxiv-pipeline-final.md` must be updated to add:
- Node 0.5 to the pipeline node list, with full input/output contract
- Node 5.5 to the pipeline node list, with full input/output contract and relationship vocabulary
- `relationship_type` and `semantic_confidence` to the renderer data contract (node fields)
- Node 5.5 dependency added to the pipeline execution order (after Node 5, before Node 6)
- Both nodes added to the declared parameters table (model, confidence threshold)

The spec is currently marked FROZEN. This amendment supersedes the frozen state on the affected sections only. All other sections remain as declared.

---

## Architectural Constraints Added

| Decision | Affects | Rationale |
|---|---|---|
| Node 0.5 is on the Node 1 path only — Node 0 bypasses it entirely | arXiv pipeline | A known identifier does not require language normalization |
| Node 0.5 never blocks pipeline execution — warn, never gate | arXiv pipeline | The LLM is an assistant to the pipeline, not a gatekeeper |
| Node 5.5 relationship vocabulary is closed — new values require an amendment | arXiv pipeline | Open vocabulary produces unrenderable output; closed vocabulary is a schema contract |
| Both LLM nodes log to provenance metadata | arXiv pipeline | LLM contributions must be auditable, not inferred |
| Node 0 fallback to Node 1 does not trigger Node 0.5 | arXiv pipeline | A failed identifier lookup is a missing record problem, not a language normalization problem — no LLM call is warranted |
| `summarize_intent()` remains purely algorithmic — this decision does not affect it | Core | Unchanged from prior constraints |

---

*Amendment: AMD-016*
*Follows: AMD-015*
*Decided: 2026-04-07*

---

### AMD-017 — Multi-Seed Input and Boolean Graph Operations

**Affects:** spec-arxiv-pipeline-final.md — extends Node 0, adds renderer-layer boolean operations
**Status:** Accepted — Not Yet Implemented
**Decided:** 2026-04-10
**Follows:** AMD-016

---

#### Reason

The frozen pipeline spec assumes a single seed paper as the graph root. This is correct
for the common case but excludes a structurally distinct and analytically meaningful use
case: simultaneous traversal from multiple known seed papers.

The motivating case is the dual CRISPR breakthrough — Doudna/Charpentier (2012) and
Zhang (2013) — two papers addressing the same problem via partially independent
intellectual lineages, arriving at convergent results at roughly the same time. Neither
paper's neighborhood alone tells the complete story. The relationship between the two
neighborhoods is where the most analytically interesting structure lives.

Multi-seed traversal exposes that structure. Boolean operations on the resulting graphs
make it interrogable.

This amendment specifies two related extensions:

1. **Multi-seed input** — Node 0 accepts a list of identifiers. The pipeline runs each
   to a root node. The assembled graph is a forest.
2. **Boolean graph operations** — renderer-layer set operations (union, intersection,
   difference) on two named graphs. No retraversal. Pipeline cost paid once per graph.

Both extensions are consistent with the thesis. Multi-seed traversal is fully
deterministic. Boolean operations are pure set logic on cached registry data. Neither
introduces probabilistic behavior or structural decision-making.

---

#### Decision

Extend Node 0 to accept a list of seed identifiers. Store `root_ids` per node at
traversal time. Define three boolean operations — union, intersection, difference — as
renderer-layer query parameters against two named graphs in the registry. Document
architectural constraints on root semantics and downstream metric behavior.

---

#### Extension 1 — Multi-Seed Input

**Node 0 — Direct Seed Entry (extended)**

Prior behavior: accepts a single `arxiv_id` or `doi`, resolves to one root node,
skips to Node 3.

Extended behavior: accepts a list of identifiers. Each identifier is resolved
independently using the existing single-seed resolution logic. All successfully resolved
papers are promoted to root nodes. The assembled graph is a **forest** — multiple root
nodes, no synthetic parent.

Input:
```
seed_ids: list[dict]   # each entry: {arxiv_id: string} or {doi: string}
```

Minimum list length: 1. Single-element list is identical to prior behavior. No
special case required.

Resolution: each identifier is resolved in sequence. Failed resolutions are logged
with a warning and excluded from the root set. If all identifiers fail to resolve, the
pipeline halts with an error. If at least one resolves, the pipeline continues with the
successful roots and logs the failures.

Gate: skip to Node 3 for all successfully resolved roots. Node 0.5 is bypassed for
all Node 0 paths — unchanged from prior behavior.

**Forest Architecture — Rationale**

A synthetic root connecting all seeds was considered and rejected. A synthetic root
is an artifact of the tooling, not a feature of the data. It would distort topological
depth calculations, produce misleading PageRank scores, and misrepresent the actual
intellectual relationship between the seeds. The forest is the truthful representation.

**Per-Node Root Provenance**

Fields stored on every node at traversal time:

```
root_ids: list[str]            # all root node_ids this node is reachable from — REQUIRED
hop_depth_per_root: dict[str, int]   # distance from each specific root — DEFERRED
```

`root_ids` is non-optional. For a single-seed run, `root_ids` contains one entry —
identical behavior, no special case required.

`hop_depth_per_root` is deferred. Cheap to add at traversal time, expensive to
reconstruct afterward. Add before Phase 10 if per-root depth queries are needed.

The overlap zone — nodes where `len(root_ids) > 1` — is the primary queryable surface
of the forest. This is where the shared intellectual foundation of two concurrent works
is structurally visible.

**Downstream Metric Behavior in a Forest**

*Note: Per AMD-019 (2026-04-26), the `topological_depth` and `hop_depth` rows below are superseded — those fields were removed from `PaperRecord`. `hop_depth_per_root` and `traversal_direction` are the AMD-019 successors. The table is preserved for historical context; AMD-019 governs current behavior.*

| Metric | Impact | Resolution |
|---|---|---|
| `topological_depth` | Multiple roots in forest | Defined as longest path from any root. NetworkX `dag_longest_path_length` supports this without modification. |
| `pagerank` | No root concept | Unchanged. |
| `community_id` | No root concept | Unchanged. |
| `hop_depth` | Per-root at traversal time | Minimum hop depth from nearest root recorded per node. |
| `root_ids` | New field | Stored at traversal time. Single-seed graphs carry one entry — no special case. |

---

#### Extension 2 — Boolean Graph Operations

Boolean operations are renderer-layer set operations on two named graphs already
present in the registry. They do not trigger retraversal. They do not modify stored
graph state. Pipeline cost is paid once per constituent graph.

**Three operations:**

- **Union** — all nodes and edges present in either graph. Functionally identical to
  multi-seed traversal with merged results. Prefer multi-seed traversal over union
  where possible — union on independently-cached graphs may carry parameter consistency risk.

- **Intersection** — nodes present in both graphs. Edges between intersection nodes from
  either graph are included. For the CRISPR case: a portrait of the shared intellectual
  foundation — papers both labs independently cited.

- **Difference** — nodes present in graph A but not graph B (asymmetric). The complement
  (B minus A) is a separate query. For the CRISPR case: a portrait of intellectual
  divergence — what each lab drew from that the other didn't.

**API contract:**

```
GET /graph/d3?op=intersection&graph_a=arxiv:2012-doudna&graph_b=arxiv:2013-zhang&view=influence
GET /graph/d3?op=difference&graph_a=arxiv:2012-doudna&graph_b=arxiv:2013-zhang&view=influence
GET /graph/d3?op=union&graph_a=arxiv:2012-doudna&graph_b=arxiv:2013-zhang&view=influence
```

Both graphs must be present in the registry before the operation is valid.
Missing graph returns a 404 with a logged error — no partial result.

**Provenance:** boolean operation results are not auto-persisted to the registry.
Persistence is an explicit act. Each response includes a provenance header recording
the operation, both constituent graphs and their parameters, node count, edge count,
and computation timestamp.

---

#### Architectural Constraints Added

| Decision | Affects | Rationale |
|---|---|---|
| Synthetic root is disqualified | arXiv pipeline | Distorts topological depth, PageRank, and misrepresents concurrent_work relationships structurally |
| `root_ids: list[str]` is required on every node | arXiv pipeline | Forest is only queryable if per-node root provenance is stored at traversal time |
| `hop_depth` in forest graphs reflects nearest root | arXiv pipeline | Unambiguous, computable, consistent with single-seed behavior |
| `hop_depth_per_root` is deferred | arXiv pipeline | Analytically useful but not required for current demo. Deferral is explicit and tracked. |
| Boolean operations are renderer-layer only — no retraversal | arXiv pipeline | Pipeline cost paid once per graph |
| Boolean operation results are not auto-persisted | arXiv pipeline | Persistence is an explicit act |
| Mixed-parameter boolean operations are valid but must record provenance | arXiv pipeline | Validity and auditability are separate concerns |
| Union prefers multi-seed traversal over post-hoc combination | arXiv pipeline | Multi-seed traversal produces a single coherent graph |

---

*Amendment: AMD-017*
*Follows: AMD-016*
*Decided: 2026-04-10*

---

### AMD-018 — Stable Node Identity for Color Designer Qt Nodes
Affects: apps/color_designer — BaseNode and all subclasses
Status: Accepted — Not Yet Implemented
Decided: 2026-04-13
Reason: BaseNode instances currently have no stable identity. Two SwatchNode instances
with identical params are indistinguishable. build_graph() cannot construct a valid
Idiograph Graph without a stable Node.id for each Qt node.

Change: BaseNode.__init__ assigns self.node_id: str = str(uuid.uuid4()) at construction.
This ID is the bridge between the Qt UI layer and the Idiograph graph model — it becomes
Node.id when to_idiograph_node() is called on each Qt node.

Constraints:
- node_id is assigned once at construction and never reassigned
- node_id is not displayed in the UI
- node_id is not the same as the display title
- node_id is session-stable but not persisted — nodes get new IDs on each launch
- This is acceptable because color designer does not save or reload graph topology,
  only the token file output

Done when: BaseNode.__init__ assigns node_id, all subclasses inherit it, and the
field is verified present on a constructed node instance in a test or manual check.

---

### AMD-019 — Per-Root Hop Depth and Traversal Direction
Affects: `spec-arxiv-pipeline-final.md` (Node 6 section, renderer data contract), `PaperRecord` model, Node 4.5 `affected_node_ids` handoff semantics
Status: Accepted — lands with Node 6 implementation
Decided: 2026-04-24
Reason: The frozen spec's `topological_depth: int | None` on `PaperRecord` had three problems surfaced during Node 6 design. (1) Direction ambiguity — citation edges point `citer → cited`, so a single scalar "longest path from root" either leaves Node 4's forward-traversal output undefined or collapses direction into magnitude. (2) Forest semantics — under AMD-017, multiple roots make "longest path from root" ambiguous and lose per-root information that is analytically valuable for the multi-seed case. (3) NetworkX API mismatch — `dag_longest_path_length` returns a graph-level scalar, not a per-node depth; the spec's language implied an API call that does not exist.

Change: Replace `topological_depth: int | None` with two fields. `hop_depth_per_root: dict[str, int]` keys on root `node_id` and stores unsigned BFS distance over the undirected view of the cleaned citation graph (always non-negative; zero only when the node is that root). `traversal_direction: Literal["seed", "backward", "forward", "mixed"]` records categorical position relative to the seed set, computed from the directed cleaned graph. Renderer projections (Y-axis coordinate, signed scalar depth) are view-layer concerns computed on demand from these two fields. Node 6 under AMD-019 assigns normal depth to suppressed-cycle nodes — the cleaned DAG is traversable regardless of which cycle edges were suppressed. `CycleLog.affected_node_ids` is retained for audit/provenance value (Node 8) but is no longer a pipeline-critical handoff.

Done when: `PaperRecord` carries the two new fields and no `topological_depth`. `spec-arxiv-pipeline-final.md` renderer data contract updated in a follow-up PR. AMD-017's "Downstream Metric Behavior in a Forest" table updated with an AMD-019 cross-reference.

*Full text and rationale: `docs/specs/spec-node6-metrics.md`.*

---

## Architectural Constraints Log

*Coverage current through AMD-014. Entries from AMD-015 onward pending — see individual AMD bodies for constraints.*

| Decision | Affects | Rationale |
|---|---|---|
| Edge `type` must be an open/extensible string, not a closed enum | Phase 3 onward | Covered in AMD-003. Phase 10 requires causal edge types (MODULATES, DRIVES, OCCLUDES, EMITS, PROJECTS_TO). A closed enum would require breaking changes. |
| Node domain (VFX / AI / rendering) is metadata only, never a structural constraint | Phase 3 onward | Phase 10 rendering nodes must fit the same architecture without special-casing. Domain is a label for query filtering, not a type gate. |
| Content-addressed caching preferred over dirty flagging | Phase 6+ | Hash each node's params + input hashes as the cache key. Stateless, fits JSON-serializable graph, reinforces determinism thesis. Event sourcing is a secondary option for agent audit trails only. |
| Idiograph is not a DCC adapter | All phases | The system is an independent semantic graph — not a Houdini plugin or a wrapper for proprietary tools. Agent integration targets the Idiograph graph directly, not downstream software. |
| File I/O must specify UTF-8 encoding explicitly | Phase 3 onward | Windows default encoding is cp1252. Any open() call on a JSON file without encoding="utf-8" will silently misread non-ASCII characters. All file reads and writes in Idiograph must specify encoding explicitly. |
| Executor must not import handler modules directly | Phase 6 onward | Covered in AMD-007. The executor is domain-agnostic. Handler registration happens at startup, not at import time. Keeps the core layer independent of implementation details. |
| Handler business logic is capped at 30 lines | Phase 6 onward | Covered in AMD-008. Error handling and logging are excluded from the count. A handler whose core logic exceeds this limit is a signal that the node is incorrectly scoped, not that the limit should be raised. |
| Demo domain is arXiv pipeline, not VFX tool handlers | Phase 6 | Covered in AMD-006. The thesis claims domain-agnosticism. The demo domain should demonstrate that. VFX node types remain in the schema — VFX tool dependencies do not enter the codebase. |
| Module-level graph state is incompatible with stateless HTTP or concurrent agent requests | Phase 7 onward | Covered in AMD-009. Remediation is a defined 5-step strangler fig migration (`docs/sessions/session-2026-04-06-2.md`), not an afternoon refactor. Triggered only if HTTP/SSE MCP transport or web demo is required. |
| `idiograph run` must be demonstrable without an API key | Phase 8 onward | Covered in AMD-010. The `--mock` flag is the mechanism. The architecture already supports this via the handler registry — the flag makes it explicit at the CLI surface. |
| Domain implementations live under `domains/<domain>/`, never as siblings to `core/` | Phase 8 onward | Covered in AMD-011. `core/` is domain-agnostic. Domain-specific code belongs in its own namespace. The directory structure should communicate the architecture without a README. |
| Edge type extensibility must remain open for USD arc types | Phase 8 onward | Covered in AMD-013. USD composition arc semantics (REFERENCES, INHERITS, SPECIALIZES, VARIANTS, PAYLOAD) must be addable as typed edges without modifying the Edge model. AMD-003 already enforces this; AMD-013 names the concrete future use. |
| `summarize_intent()` must remain purely algorithmic — no LLM calls at the query layer | Phase 8 onward | Covered in AMD-013. Will be called on composition strategy subgraphs in Phase 10. An LLM call inside the query layer would undercut the determinism thesis at its foundation. |
| Node schema must support optional `input_ports` / `output_ports` | Immediate | Covered in AMD-014. Required for port model without breaking existing graphs. |
| Edge schema must support optional `from_port` / `to_port` | Immediate | Covered in AMD-014. Required for typed edge connections without breaking existing edges. |
| Graph schema must support optional `type_registry` top-level key | Immediate | Covered in AMD-014. Type definitions must live in the graph document, not a separate store. |
| Port type enforcement is a post-Phase-8 build target, not Phase 10 | Post-Phase-8 | Covered in AMD-014. This is a credibility requirement. Phase 10 is not the right gate. |
| `validate_integrity()` is the enforcement entry point for port types | Post-Phase-8 | Covered in AMD-014. No new public API method required; extend existing validation surface. |

---

## Open Questions

*Coverage current through AMD-014. Entries from AMD-015 onward pending — see individual AMD bodies for open questions.*

| Question | Raised | Notes |
|---|---|---|
| Should `summarize_intent()` use an LLM call internally, or be purely algorithmic? | 2026-03 | Algorithmic is safer for determinism thesis. LLM-assisted version could be a Phase 9 extension. Don't conflate the two. |
| MCP server: stdio transport or HTTP/SSE? | Not yet raised | stdio is simpler for local dev; HTTP/SSE supports remote agents. Decide at Phase 8 start based on demo requirements. |
| Should the handler registry support async-only handlers, or mixed sync/async? | 2026-03 | Async-only is cleaner and consistent with the execution model. Sync handlers can be wrapped with `asyncio.to_thread` if needed. Decide at Phase 6 implementation start. |
| How should the results dict handle failed nodes — omit the key, or store the error? | 2026-03 | Storing a structured error dict under the node ID is preferable. Downstream nodes can inspect it; the executor can distinguish "not run yet" from "ran and failed." Decide at Phase 6 implementation start. |
| Should `Discard` be a real node type or a terminal status on `Router`? | 2026-03 | Separate node is preferable — preserves the decision trail in graph state and keeps Router's responsibility narrow. But worth confirming once the routing logic is implemented. |
| How does the discrete node/edge model handle continuous field evaluation in Phase 10 without losing the clean input/output contract the executor depends on? | 2026-03 | Field nodes in Phase 10 (ScalarField, VectorField, OrientationField) are continuous mathematical objects — they evaluate over a surface or volume rather than consuming and producing discrete data dicts. This is structurally different from every other node type in the system. Three candidate approaches: (1) handlers return a callable (the field function itself) as a value in the output dict — downstream projection nodes invoke it; (2) fields are evaluated at a fixed sample set defined in params and returned as a structured array; (3) field evaluation is treated as a dedicated pre-execution pass, separate from the main executor loop. Option 1 has two compounding problems: it breaks JSON serializability, and it breaks agent-readability — an agent inspecting graph state post-execution would find an opaque callable object where it expects structured data. That is a thesis violation, not merely a technical inconvenience. Option 2 preserves serializability but discretizes what is inherently continuous — the fidelity loss may be acceptable for a demo but is architecturally dishonest. Option 3 introduces a second execution model, which sounds like a compromise but may be the most defensible: a well-defined field evaluation pass with explicit structure and clear semantics is fully consistent with the thesis. The thesis does not require one execution model for everything — it requires explicit structure at every level. A principled second pass is not a contradiction; a collapsed workaround is. Resolve at Phase 10 design stage, not before — but the resolution should be framed as an architectural decision, not a technical patch. |
| What are the compatibility rules for port types? | 2026-03 (AMD-014) | Minimum viable: exact match. Structural subtyping is a refinement. Decide at enforcement implementation, not now. |
| How does the type registry handle versioned types? | 2026-03 (AMD-014) | A `SurfaceProperties_v2` is a different type, not a version of `SurfaceProperties`. Versioning strategy is a Phase 8.5 design question. |
| Should MCP expose `get_type_registry` as a named tool? | 2026-03 (AMD-014) | Likely yes — an agent inspecting a graph needs the type registry to interpret port declarations. Decide at Phase 8 MCP tool surface design. |
| What is the ConstraintSet schema? | 2026-03 (AMD-013) | Must be defined before gate milestone work begins. Minimum fields: prim path, attribute name, value type, layer context, ownership constraints. Exact schema is a Phase 10 design task, not a current gate. |
| How does the backward-chaining solver handle contradictory constraints? | 2026-03 (AMD-013) | Contradiction is a first-class output, not an error. The solver should return a structured result explaining which constraints are in conflict and why — traceable to specific LIVRPS rules. Phase 10 design task. |
| Should `register_all()` live in `domains/arxiv/__init__.py` or a dedicated `domains/arxiv/register.py`? | 2026-03 | Decide at AMD-011 implementation. `__init__.py` is simpler; a dedicated file is more explicit about what it does. Either is acceptable — consistency with the existing handler registration pattern matters more than the specific choice. |

---

*Last updated: 2026-04-29*
*Owner: Idiograph project*
