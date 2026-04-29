# Idiograph — Session Workflow

*How Idiograph design and implementation sessions are structured. This is project-internal process documentation — the live record of how the work actually runs. Update when the pattern changes, not aspirationally.*

*Last updated: 2026-04-29*

---

## Why this document exists

Idiograph is built part-time alongside production work. Sessions are days or weeks apart. The workflow below ensures every session can start cold without losing momentum, and that the work each session produces is durable enough to survive the next gap.

The pattern documented here evolved through Phase 8 and Phase 9. Several pieces — the spec-and-prompt pair, the Claude Code audit step against a draft spec, end-of-session read-only validation, the two-PR pattern for in-session scope creep — emerged because earlier session shapes produced specific failures and these steps fixed them. Recording the workflow matters for the same reason the project records architectural decisions in amendments: a process that works because of properties not visible in the artifact is fragile. Future-me, or any contributor, can recover the *what* by reading session summaries. Recovering the *why* without the explanation requires re-running the failure modes that motivated each step. This document is the explanation.

This document supersedes the earlier 8-step phase-centric workflow. That structure described the project when phases were the unit of work. Phase 9 has been spec-centric and node-centric; the workflow followed.

---

## Two session types

Sessions fall into two categories with different shapes. The full cycle from "we should design X" to "X is merged" runs through both, in sequence.

**Design sessions** happen in claude.ai with full project context loaded. Architectural decisions get made. No code is written. Output is a session summary in `docs/sessions/` and (eventually) a draft spec in `docs/specs/`.

**Implementation sessions** happen in Claude Code against a frozen spec. Code gets written, tests get added, the spec is the contract. Output is a PR, a session summary, and a `BRIEFING.md` update at merge.

The audit step bridges the two — Claude Code reads a draft spec against the actual codebase and produces a structured report before the spec freezes.

---

## The full cycle

The steps below run in order. Skipping a step is a deliberate choice, not a default — each step is doing specific work the next step depends on.

### Step 0 — Orientation

Every session starts with a read of `BRIEFING.md` and `CONTEXT.md`. These are the authoritative source of project state — current phase, test baseline, what's built, open decisions, architectural constraints. Memory alone is not sufficient; both files exist precisely so a session can start cold without reconstructing context from scratch.

If the session has been preceded by recent merges, the BRIEFING reflects them. If the worktree is dirty or branches are open, the BRIEFING says so. The 30-second re-entry comes from these two files.

### Step 1 — Design session

Conducted in claude.ai. The architectural calls happen here — what the function does, what its inputs and outputs are, where it sits in the pipeline, what failure modes are first-class, what gets deferred. This is the only step where claude.ai is the right venue, because it's the only step that benefits from broad context across the whole project.

The session is a structured discussion, not a lecture. The pattern that has held: the project author drives the high-level architectural decisions and design intent; claude.ai holds the detailed architectural logic and surfaces consequences. When a decision is made, claude.ai records it; when a consequence is named, claude.ai checks it against existing constraints and amendments. This division of labor is real and worth naming — design sessions work because each side is doing what they do well.

**Open question discipline.** When a design session surfaces a decision the prior context didn't name, that decision becomes an explicit option in the spec rather than being silently incorporated. The 2026-04-27 docs sweep is the canonical example: the BRIEFING described the scope as `topological_depth` updates, but the actual AMD-019 surface was wider. Surfacing the additional `hop_depth` row as an explicit Option A/B/C choice — rather than silently extending scope — meant the choice was reviewable and named in the diff. The principle: prior framing under-described, the choice gets surfaced explicitly.

**AMD index discipline.** If the design session produces a new AMD, the AMD's index updates are authored alongside the AMD body in the same PR — never deferred. New constraints introduced by the AMD become rows in `## Architectural Constraints Log` in `docs/decisions/amendments.md`. New open questions raised by the AMD become rows in `## Open Questions`. The discipline exists because deferral has historically produced drift: between AMD-014 and AMD-019 the indices stopped tracking new entries, and reconstructing them after the fact is costly authorial work. Authoring inline keeps `amendments.md` in alignment with `naming-convention.md` and keeps the indices honest about coverage.

The session ends with a session summary in `docs/sessions/session-YYYY-MM-DD.md`. Status `DRAFT` until the design is complete, then `FROZEN` (historical record, not revised).

### Step 2 — Draft spec

Written from the locked design decisions. Lives in `docs/specs/spec-short-topic.md` with status `ACTIVE` (not yet frozen). The spec is the contract Claude Code will execute against — function signatures, contracts, test names, logging conventions, file locations.

The spec author writes against the design session's decisions, not against the codebase directly. This is intentional — at this stage the spec describes the target, not the path to it. Codebase reality enters in step 3.

### Step 3 — Claude Code audit

The draft spec is handed to a Claude Code session whose only job is to audit the spec against the actual current codebase and return a structured report. **The audit produces findings, not edits.**

The audit catches a class of error that the design session and the spec draft cannot see: divergence between the spec's assumptions about the codebase and the codebase's actual state. Field shapes, logger names, import patterns, section header conventions, file organization, helper availability — all of these can drift between sessions, and a spec written in claude.ai operates from snapshots that may be stale.

Findings come back categorized:

- **Conflicts** — the spec contradicts existing code. Field type wrong, function signature wrong, file path wrong.
- **Gaps** — the spec assumes something that doesn't exist yet. Helper not present, model field not defined, dependency not installed.
- **Stylistic divergences** — the spec asks for a pattern inconsistent with the codebase's existing pattern. Logger name format, section header length, naming conventions.
- **Non-issues** — the audit checked, no problem found, but worth recording that the check happened.

Three properties make the audit work, in order of importance:

**The audit happens against a draft, not a frozen spec.** This is the load-bearing detail. If the spec is frozen when the audit runs, the audit's only options are "accept the divergence as a deviation" or "block on a re-spec." Running against a draft means the spec author can fold findings back in *before* freezing. The spec ends up describing something the codebase can actually receive.

**The audit produces a report, not edits.** Claude Code returns findings; the spec author decides which become spec changes, which become deferred items, which are non-issues. The spec author retains the architectural call; the auditor surfaces facts. Auditor-as-author would collapse roles that need to stay separate.

**The audit precedes the implementation prompt, not the design session.** Earlier than that and there's no spec to audit against. Later than that and the conflicts surface during execution.

### Step 4 — Spec revisions

The spec author reads the audit report and decides which findings become spec changes, which become deferred items, and which are non-issues.

**Existing code wins on style.** When a stylistic divergence is named, the spec is revised to match the codebase, not the other way around. The Node 7 implementation session log records this happening twice (log-message prefix, empty-input early return), and both divergences were caught at the read-pass stage rather than after-the-fact because the audit had named them. The principle: the codebase's existing patterns are the ground truth. New code conforms to them; specs that ask otherwise get revised.

The spec's status remains `ACTIVE` through this step.

### Step 5 — Freeze

The revised spec is committed (or the freeze trigger is met — convention varies; see existing specs for current pattern). From this point forward, the spec is the contract. Changes to the spec require a new amendment cycle, not edits to the frozen document.

### Step 6 — Implementation prompt

A short execution brief is written, typically in `tmp/`. The prompt does not duplicate the spec — it points at the spec and gives Claude Code the operational instructions: which branch, what tests to run, what the commit message is, what the out-of-scope list looks like.

The spec is durable project documentation. The prompt is ephemeral session scaffolding. Conflating them creates a class of failure where session-specific decisions leak into project documentation, or where the durable contract becomes ambiguous because it's mixed with session-specific scaffolding. The pattern of separation has held cleanly across multiple PRs and is recorded as a permanent workflow element.

`tmp/` and `*-prompt.md` are gitignored — prompts are session scaffolding, not artifacts.

### Step 7 — Claude Code implementation

Claude Code executes against the frozen spec following the implementation prompt. Spec compliance is the contract — deviations require explicit naming in the session summary, not silent omission.

A few practical patterns that emerged during Phase 9 and are worth keeping:

**Anti-drift: spec re-read at every prompt.** The implementation prompt instructs Claude Code to read the spec before doing anything else. The spec is the contract; reading it at session open prevents drift between what the prompt says and what the spec actually requires.

**Long prompts via file handoff.** When the prompt is substantial, claude.ai writes the prompt to a `.md` file, the project author places it in the repo, and Claude Code reads it. Avoids losing structure to copy-paste round-trips.

**`ruff check`, not `ruff format`.** Multiple sessions hit the same trap: `ruff format` is file-scoped, not line-scoped. For prescribed "touched-lines-only" refactors, the formatter reformats unrelated pre-existing sites and the changes have to be reverted. `ruff check` is the right invocation; skip the formatter unless the pre-existing baseline is known ruff-clean. Named in implementation prompts going forward.

**Test count as gate.** Implementation prompts name an expected test count and instruct Claude Code to stop if the count differs. Catches missing tests and accidental test deletion before the PR opens.

**Spec files dropped untracked.** Spec files have landed in the working tree at the start of several implementation sessions (Node 5, Node 6, Node 7). Pattern is consistent: the spec lands with the implementation PR, not in a separate docs PR. Avoids the "spec describes code that doesn't exist on main yet" review state.

**End-of-session read-only validation.** Before the PR opens, the implementation session runs a structured spec-compliance self-check: every contract bullet from the spec is mapped to an enforcing test, the test gate is verified, the diff is reviewed against the prompt's scope statement. Gaps are either closed by adding a test or surfaced as flagged deviations. This catches incomplete coverage and scope creep before review.

**Two-PR pattern for in-session scope creep.** When a session surfaces a precursor change that needs to land before the main work — `SuppressedEdge` composing `CitationEdge` before Node 5, the AMD-019 docs sweep before Node 7 work — split into a precursor PR and a follow-up rather than bundle. The precursor reviews cleanly as one mechanical change; the main work then builds on the merged precursor without history noise.

**Merge commits, not squash.** Merge commits preserve the spike narrative — the sequence of decisions and their contexts. Squashing collapses that into one commit and loses the audit trail.

### Step 8 — Session summary

Written to `docs/sessions/` capturing what was built, what tests were added, the test gate (before/after counts), spec-compliance self-check results, deviations from the spec, workflow observations, and what's next. Status `FROZEN` — historical record, not revised.

The session summary is the durable account of what happened in that session. It is the artifact that lets future-me reconstruct *why* a decision was made when the spec text alone is ambiguous. **Deviations from the spec are named explicitly** — not silently absorbed. The session summary's "Deviations from the spec" section is the audit surface for whether the spec was followed.

### Step 9 — PR and merge

Standard PR review against main. Branch protection on main requires PRs even for single-file doc updates — direct push to main is blocked.

**AMD index check at PR review.** If the PR introduces an AMD, the index updates in `amendments.md` must be present in the diff: new rows in `## Architectural Constraints Log` for any constraints the AMD introduces, new rows in `## Open Questions` for any questions it raises. Per Step 1's AMD index discipline, these are authored alongside the AMD body, not deferred. Missing index updates block the merge.

After merge, `BRIEFING.md` is updated to reflect the new state — test baseline, what's built, recent history. BRIEFING updates land in the same PR or in an immediate follow-up. **The update cadence is at merge, not at session end.** Between a session's end and the next session's start, main can move forward; BRIEFING's claim to be "live state" only holds when it's refreshed at merge time.

---

## Document taxonomy reference

This workflow operates within the documentation taxonomy defined in `docs/naming-convention.md`. The relevant slots:

- **`docs/sessions/`** — frozen session summaries, one per session, dated.
- **`docs/specs/`** — living specs (`ACTIVE`) and frozen specs.
- **`docs/decisions/amendments.md`** — append-only architectural decision log (AMD-numbered).
- **`BRIEFING.md`** at root — live state, updated at merge.
- **`CONTEXT.md`** at root — static project identity and architectural constraints.
- **`workflow.md`** at root — this document.

---

## When to revise this document

When the pattern changes. Not aspirationally, not when a new step seems like it might help — when an actual session has run a new step successfully and it has earned its place by fixing a specific failure mode the prior workflow didn't catch.

Like the project's amendment system, this document records what's been decided, with rationale. Process changes get folded into the relevant section with the date in the header bumped. Substantial revisions warrant a session summary entry naming the change and why it was made.

---

*Companion: `CONTEXT.md` (project identity and architectural constraints), `BRIEFING.md` (live state), `docs/decisions/amendments.md` (architectural decision log), `docs/naming-convention.md` (document taxonomy).*
