# Idiograph — Phase 8→9 Task Inventory
**Created:** April 5, 2026
**Status:** LIVING — update as tasks complete or scope changes

---

## Track 1 — Demo (Blocking Gate)

**1.0 — Survey and hands-on review of existing arXiv tools**
Try the relevant apps directly — Connected Papers, Semantic Scholar, ResearchRabbit, Litmaps, others as found. Document what each does, what projection decisions they make (explicit vs. implicit), and where they fail the thesis test. Design input for 1.1, not competitive analysis for its own sake. Must happen before the pipeline design session.

**1.1 — Design the richer arXiv pipeline**
Design session only — no code. Current 6-node pipeline is a proof of wiring, not a proof of argument. Pipeline needs real citation traversal, not just abstract fetching. Node structure decided from the output backward before any implementation begins.

**1.2 — Build the vector index**
ChromaDB over arXiv metadata. Prerequisite for the navigation model at scale.

**1.3 — Community view algorithm selection**
Explicitly deferred in demo spec. Algorithm must be named and documented before any view code is written.

**1.4 — Named, versioned view functions**
One function per declared projection: influence, temporal, community. Written to core before FastAPI is touched.

**1.5 — Graph registry**
`src/idiograph/core/registry.py` — current module-level state is a known liability (AMD-009). Registry is the surface the FastAPI and demo code targets.

**1.6 — FastAPI interface**
`src/idiograph/api.py` with `source` + `view` parameters and neighborhood endpoint. View functions must exist first.

**1.7 — D3 renderer with view-switching UI**
Same renderer, different view functions. Self-description moment depends on this working.

**1.8 — Self-description graph**
Idiograph's own execution graph as a demo input. Must have enough node density to be visually meaningful. This is a gate on the demo, not a refinement.

**1.9 — Demo packaging**
Define what someone actually runs to see the demo. Install instructions, entry point, sample data included. Distinct from building the demo — this is the handoff artifact.

---

## Track 2 — Documentation Infrastructure

**2.1 — `scripts/new_session_summary.py`**
Generates a pre-filled `docs/sessions/session-YYYY-MM-DD.md` with FROZEN status header and correct section stubs.

**2.2 — `scripts/new_amendment.py`**
Prompts for AMD number, status, decision, and rationale. Appends formatted entry to `docs/decisions/amendments.md`.

---

## Track 3 — Essay (Parallel, Mobile-Compatible)

**3.1 — Stories 2–5 editing pass**
Status: drafted. Verify which sections are locked vs. still soft before any editing begins.

**3.2 — Idiograph section**
Currently two sentences. Needs enough substance to convert argument into artifact. This is the section a technical reader will look for after the stories land.

**3.3 — Pending fix notes**
Two flagged issues requiring an editing pass:
- "the people building you don't seem to know it exists" — too contemptuous before standing is established; soften or move after stories land
- "AI tools have regressed on all three" — reframe as tradeoff, not regression

**3.4 — Raw notes placement**
Three captured note blocks not yet placed. Decision needed on each: usable, needs reframing, or discard.

---

## Track 4 — Tooling & Housekeeping

**4.1 — Migrate dev workflow to Claude Code**
All future implementation sessions run in Claude Code. Establish working pattern — CLAUDE.md is the orientation entry point each session.

**4.2 — Legal boilerplate on all `.py` files**
Apply header from `file_header_template.py` to every `.py` file in `src/`, `tests/`, `scripts/`, `domains/`. Update copyright year from 2025 to 2026. Exclude generated files and empty `__init__.py` stubs. One commit.

**4.3 — Audit and update CLAUDE.md**
First task of the first Claude Code session. Verify it reflects current project name (Idiograph, not NodeForge), current phase state, document taxonomy, session types, and architectural constraints.

**4.4 — Full NodeForge → Idiograph rename audit**
Sweep entire codebase — source, tests, docs, inline comments, docstrings. Any surviving "NodeForge" is a credibility problem. One commit.

**4.5 — README polish**
Current state unknown. Must reflect Phase 8 completion, MCP integration, correct project name, and thesis framing. Gate: readable by a technical evaluator who has never seen the project.

---

## Deferred — Not Phase 9

- Port type enforcement (referential integrity)
- AMD-009 state management migration (forcing function not yet met)
- Phase 10 — USD composition inversion domain

---

*Companion documents: demo_design_spec.md, phase_8_summary.md, blueprint_amendments_3.md, essay_blueprint.md*
