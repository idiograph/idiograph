# Idiograph — Session Summary
**Date:** April 3, 2026
**Status:** FROZEN — historical record, do not revise

---

## Context

This session began as a conversation about monetization but evolved into the most important conceptual clarification the project has had. The arXiv pipeline was identified as failing to demonstrate its own thesis. A new demo architecture was designed from first principles. The project scope expanded significantly beyond 9 phases.

---

## Core Realization — The arXiv Pipeline Was Wrong

The existing arXiv pipeline fetches a paper and summarizes it. It demonstrates that the graph executes correctly. It does not demonstrate why the graph architecture is *necessary*.

**The thesis requires a domain where a wrong answer is visibly, immediately wrong.**

The arXiv pipeline showed a system working. It did not show what breaks without it. A technical evaluator — including Ryan's cousin, a physicist quant developer who reviewed it — could watch the demo and think "okay, a graph pipeline" without ever confronting the central argument.

**Feedback received:** "It looked incomplete. It wasn't really showing the value promises in the blueprint thesis."

This is accurate. The pipeline is a proof of wiring, not a proof of argument.

---

## Issue Resolution Log

| # | Issue | Resolution |
|---|---|---|
| 1 | Phase 8 / registry status | Labeled — forward design callouts added to architecture spec |
| 2 | Force-directed layout claim (Connected Papers) | Resolved — line deleted; untraceable edge critique stands alone |
| 3 | DBSCAN labeled "deterministic" | Resolved — algorithm deferred, principle stated; community view is a Phase 9 design task |
| 4 | 6-node graph is a blocker, not a gap | Labeled — upgraded to blocking prerequisite in demo design spec |
| 5 | "No interface owns state" wording | Resolved — principle rewritten; implementation gap noted in architecture spec |
| 6 | "Same D3 renderer" oversimplified | Resolved — "same renderer, different view functions" throughout |
| 7 | Cost vs. feasibility argument | Resolved — category error leads, cost is secondary |
| 8 | `create` capability gap | Dismissed — deferred post-Phase 9; structural mutation requires stable registry surface |

---

*Companion documents: demo_design_spec.md, vision_and_thesis.md, blueprint_amendments_3.md, phase_8_summary.md*
