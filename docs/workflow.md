# Idiograph — Session Workflow

This document defines the standard structure for every working session on the Idiograph project.
It exists so that sessions can be short, interrupted, and resumed without losing momentum or context.

---

## Why This Structure Exists

Idiograph is being built part-time alongside a full-time production role.
Sessions may be days or weeks apart. The workflow below ensures that every session:

- starts with a shared understanding of current state
- validates retained knowledge before adding new knowledge
- frames new work in terms of the thesis, not just the feature
- produces a clean artifact at the end
- closes the loop on learning before the session ends

---

## Session Structure

### Step 1 — Orientation (5 minutes)

Before anything else: a one-paragraph summary of where the project stands.

This covers:
- which phases are complete
- what the last session produced
- what phase comes next and why

The goal is a 30-second re-entry for someone returning after a gap.
This step is always done first, even if the previous session ended yesterday.

---

### Step 2 — Prior Phase Review (as needed)

A structured review of all completed phases.

This covers:
- what was built in each phase
- key architectural decisions made and why they were made
- how each phase connects to the thesis

Length scales with how much time has passed since the last session.
If the previous session was recent, this can be brief. If weeks have passed, it goes deeper.

---

### Step 3 — Retention Check (optional but recommended)

A short quiz on prior phases before moving forward.

Format is contextual — shaped to whatever was covered in prior phases and where the gaps are most likely to matter going forward. A tooling phase and a data modeling phase should produce different quizzes.

The quiz is a diagnostic, not a gate. The goal is to surface gaps before they become confusion later.
Request this explicitly if you want it — it is not automatic.

---

### Step 4 — Current Phase Overview

Before any code: a clear framing of the current phase.

This covers:
- the goal of the phase in one sentence
- the thesis connection — why this phase matters beyond the feature it delivers
- key topics and concepts that will appear during implementation
- the reasoning behind major design choices before they are made

This is a discussion, not a lecture. Push back, ask questions, propose alternatives here —
not mid-implementation.

---

### Step 5 — Implementation

The actual build session.

Structure:
- work in micro-sessions as defined in the Blueprint
- each micro-session produces a runnable result before moving to the next
- decisions made during implementation are flagged and recorded
- if a decision conflicts with a prior architectural constraint, stop and resolve it explicitly

No phase ends with broken code. If a session ends mid-phase, the system must be in a
runnable state at the stopping point.

---

### Step 6 — Post-Mortem

After implementation: a review of what was actually built versus what was planned.

This covers:
- what was completed
- what was deferred and why
- any decisions made during implementation that differ from the plan
- amendments to the Blueprint or Architectural Constraints Log if needed

This is the moment to update `blueprint_amendments.md` if anything changed.

---

### Step 7 — Phase Summary Document

At the end of every completed phase: a clean summary document.

Format (consistent across all phases):
- **What Was Built** — the concrete artifacts produced
- **Key Decisions** — architectural choices made and the reasoning behind each
- **Files** — full content of every file created or modified
- **Verified Working** — the exact commands run and outputs confirmed
- **Next** — one paragraph on what Phase N+1 will do and why

This document is added to project files and becomes part of the project record.
It is the handoff artifact — written so that a new session (or a new collaborator) can
pick up exactly where this one ended.

---

### Step 8 — Current Phase Retention Check (optional but recommended)

A short quiz on the phase just completed.

Format is contextual — shaped to what was actually built, the decisions that were made, and what is most likely to matter in the next phase. No fixed structure.

Request this explicitly. It is most useful immediately after implementation while
the decisions are still fresh.

---

## Quick Reference — Session Checklist

```
[ ] Step 1  — Orientation: where does the project stand right now?
[ ] Step 2  — Prior phase review (depth scaled to time since last session)
[ ] Step 3  — Retention quiz on prior phases (optional)
[ ] Step 4  — Current phase overview: goal, thesis connection, key choices
[ ] Step 5  — Implementation: micro-sessions, runnable at every stop point
[ ] Step 6  — Post-mortem: what was built, what changed, what was deferred
[ ] Step 7  — Phase summary document produced and saved
[ ] Step 8  — Retention quiz on current phase (optional)
```

---

## Notes

- Steps 3 and 8 are opt-in. Request them explicitly each session.
- Step 5 can span multiple sessions. Steps 6, 7, and 8 only happen when a phase is complete.
- If a session ends mid-phase, record the stopping point in a brief note so Step 1 of the next session has something concrete to orient from.
- The thesis — deterministic, semantically grounded systems for AI-operable production pipelines — should be referenceable at every step. If a decision cannot be connected back to that argument, it should be questioned.

---

*Owner: Idiograph project*
*Last updated: 2026-03*
