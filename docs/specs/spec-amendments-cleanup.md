# Spec: amendments.md Structural Cleanup
**Status:** ACTIVE — design complete, ready for implementation
**Branch:** `chore/amendments-cleanup`
**Scope:** Structural reorganization of `docs/decisions/amendments.md`. Adds AMD-019 index entry. Moves cross-cutting indices to end of file. Includes a rewrite of root-level `workflow.md` (authored alongside this spec) and adds index-maintenance language to the new workflow structure.

---

## Purpose

`docs/decisions/amendments.md` has accumulated structural drift across multiple consolidation events. Specifically:

- AMD-016 was appended on 2026-04-15 from a standalone file as a level-1 heading (`# AMD-016`) rather than the level-3 convention (`### AMD-016`) used by all other entries.
- AMD-018 was authored on 2026-04-13 and inserted between AMD-015's tail and the file-level index sections — but AMD-016 and AMD-017 were appended *after* the indices on 2026-04-15. The result is a file ordered as: AMD-001 through AMD-015, then `## Architectural Constraints Log` and `## Open Questions` indices, then AMD-018, then AMD-016, then AMD-017. The numerical sequence is broken; the indices are stranded mid-file.
- AMD-019 was authored on 2026-04-26 alongside Node 6 implementation but its full text lives in `spec-node6-metrics.md` rather than in `amendments.md`. It is referenced in passing (one cross-reference note above the AMD-017 forest-metrics table) but has no canonical index entry. Per the project's `naming-convention.md` taxonomy, all amendments live in `docs/decisions/amendments.md`.
- The `## Architectural Constraints Log` and `## Open Questions` sections stop at AMD-014. Constraints and questions from AMD-015 through AMD-019 are not indexed.

This PR restores numerical ordering, indexes AMD-019, moves the cross-cutting sections to their structurally correct position, and lands a rewrite of `workflow.md` (root-level) that supersedes the prior 8-step phase-centric session structure with a spec-centric structure reflecting how Phase 9 sessions actually run. The new `workflow.md` includes index-maintenance as a named step in AMD-creating sessions, establishing the discipline that prevents the kind of drift this cleanup is correcting.

---

## Why this is its own PR

This is doc hygiene, not implementation. It does not affect any code path or test. The amendments.md cleanup and the workflow.md rewrite travel together because they're addressing related kinds of drift: the amendments file's structure has drifted from `naming-convention.md` conventions, and `workflow.md` had drifted from how the project actually runs sessions. Both are doc-hygiene work that benefits from a single careful pass and a coherent diff. Per the project's "one concern per PR" pattern (PR #20 SPDX sweep, PR #22 post-Node-6 docs sweep), structural cleanup gets its own dedicated landing.

---

## Changes

### 1. AMD-018 numerical reordering

**Current location:** lines 872 to ~903 in `amendments.md`, between the `## Open Questions` section (ending at line 870-ish) and the `<!-- AMD-016 appended ... -->` comment at line 902.

**New location:** after AMD-017's terminal lines (current end of file), as a level-3 heading. Numerical position 018, immediately following AMD-017.

**Content preserved verbatim.** No edits to AMD-018's body. Heading remains `### AMD-018 — Stable Node Identity for Color Designer Qt Nodes`.

### 2. AMD-016 heading reformat

**Current heading:** `# AMD-016 — LLM Node Placement in the arXiv Citation Pipeline` (level-1)

**New heading:** `### AMD-016 — LLM Node Placement in the arXiv Citation Pipeline` (level-3)

**Body content preserved verbatim.** No edits beyond the heading level.

The HTML comment from line 902 (`<!-- AMD-016 appended from standalone file during doc consolidation 2026-04-15 -->`) is preserved as-is. It documents the file's history.

### 3. AMD-019 index entry (new)

**Add after AMD-018's new position**, before the index sections. Standard amendment format matching AMD-001 through AMD-015:

```markdown
### AMD-019 — Per-Root Hop Depth and Traversal Direction
Affects: Phase 9 — Node 6 (Metric Computation), arXiv pipeline schema
Status: Accepted
Decided: 2026-04-26
Reason: The original schema used a scalar `topological_depth: int | None` on `PaperRecord`, which collapsed multi-root forest structure into a single number and produced null values for nodes in suppressed cycles. Both behaviors are dishonest: forests have multiple meaningful distances, and cycle suppression at the structural level is unrelated to whether a node is reachable from a root in the cleaned graph.
Change: Replace `topological_depth` with two fields — `hop_depth_per_root: dict[str, int]` (BFS distance from each root over the undirected cleaned graph, never null) and `traversal_direction: Literal["seed", "backward", "forward", "mixed"]` (which traversal surfaced the node, computed from the directed cleaned graph). Node 6 owns the canonical computation post-cleaning. `CycleLog.affected_node_ids` becomes audit-only; depth is fully defined for every node in the cleaned graph.
Done when: PR #18 merged with `compute_depth_metrics()` returning `dict[str, DepthMetrics]` carrying both new fields per node, `topological_depth` removed from `PaperRecord`, and `spec-arxiv-pipeline-final.md`'s renderer data contract updated to reflect the new fields.

*Full text and rationale: `docs/specs/spec-node6-metrics.md`.*
```

The dual-location pattern is intentional: the canonical authoring location remains the spec where the decision was made and acted upon. The index entry in `amendments.md` provides the standard navigability all amendments should have.

**Note for implementer:** the `Reason`, `Change`, and `Done when` fields above are reconstructed from references in BRIEFING, session summaries, and spec-node6-metrics.md. Before committing the entry, read the actual AMD-019 text in `spec-node6-metrics.md` and verify the reconstruction faithfully represents the original decision. If the actual text says something materially different, use the actual text — paraphrased into the standard amendment format if needed.

### 4. `## Architectural Constraints Log` and `## Open Questions` move to end of file

Both sections currently appear at lines 828 and 853 — between AMD-015's tail and AMD-018's inserted position. They are file-level appendices: cross-cutting indices that aggregate information across all AMDs.

**New position:** at the end of the file, after the last AMD entry. Order: `## Architectural Constraints Log` first, then `## Open Questions`, then the trailing `*Last updated: ...*` and `*Owner: ...*` lines (preserved from current file).

**Content currently preserved verbatim.** Both sections stop at AMD-014; the content drift from AMD-015 through AMD-019 is acknowledged in the next section but not corrected in this PR.

### 5. Drift acknowledgment notes

Add a single italicized line at the top of each index section, immediately after the section heading:

For `## Architectural Constraints Log`:

```markdown
## Architectural Constraints Log

*Coverage current through AMD-014. Entries from AMD-015 onward pending — see individual AMD bodies for constraints.*

| Decision | Affects | Rationale |
|---|---|---|
... (existing table preserved verbatim)
```

For `## Open Questions`:

```markdown
## Open Questions

*Coverage current through AMD-014. Entries from AMD-015 onward pending — see individual AMD bodies for open questions.*

| Question | Raised | Notes |
|---|---|---|
... (existing table preserved verbatim)
```

These notes preserve honesty about the indices' state while deferring the content-update work to a future dedicated PR (or to AMD-020 onward as part of routine authoring; see workflow.md changes below).

### 6. `workflow.md` rewrite and index-maintenance language

The root-level `workflow.md` is rewritten in this PR. The previous version described an 8-step phase-centric session structure that no longer reflects how the project runs (Phase 9 has been spec-centric and node-centric, not phase-centric). The new version describes the actual current pattern: design sessions, draft specs, Claude Code audits, freezes, implementation prompts, implementation sessions, session summaries, and PR/merge — captured under the headings §Two session types and §The full cycle.

The rewrite is authored alongside this spec and is included in the PR's diff. It is not separately versioned or AMD'd; the new file *is* the documented process going forward.

**Index-maintenance language added on top of the rewrite.** Within the new `workflow.md` structure:

- Add a sub-step or note under §The full cycle / Step 1 (Design session) stating: "If the design session produces a new AMD, the AMD's index updates (architectural constraints, open questions) are authored alongside the AMD body. Constraints introduced by the AMD are added as new rows to the `## Architectural Constraints Log` table in `amendments.md`. Open questions raised by the AMD are added to the `## Open Questions` table. This happens in the same PR that introduces the AMD, never deferred."
- Add an entry to §The full cycle / Step 9 (PR and merge) verification list: "If the PR introduces an AMD, the index updates in `amendments.md` are present in the diff."

Exact wording and placement are at the implementer's discretion — the requirement is that the discipline is named in `workflow.md` such that future implementation prompts referring to the workflow document will surface it.

---

## Final structural shape

After this PR, `docs/decisions/amendments.md` reads top to bottom:

```
# Idiograph – Blueprint Amendments & Decision Log
## Amendment Format
## Amendments
### AMD-001 — ...
### AMD-002 — ...
...
### AMD-015 — ...
### AMD-016 — LLM Node Placement in the arXiv Citation Pipeline
    (level-3 heading; HTML comment preserving history retained)
### AMD-017 — Multi-Seed Input and Boolean Graph Operations
### AMD-018 — Stable Node Identity for Color Designer Qt Nodes
### AMD-019 — Per-Root Hop Depth and Traversal Direction
    (index entry; full text in spec-node6-metrics.md)
## Architectural Constraints Log
    (drift note + existing table)
## Open Questions
    (drift note + existing table)
*Last updated: 2026-04-29*
*Owner: Idiograph project*
```

Numerical sequence restored. Indices at the end where they belong. AMD-019 indexed.

`workflow.md` reads as the rewritten spec-centric structure with the index-maintenance language threaded through Steps 1 and 9.

---

## Out of scope

- **Updating the Constraints Log and Open Questions content** to cover AMDs 015 through 019. This is real authorial work (judgment calls about what counts as a constraint, what counts as an open question) and is deferred. The "Coverage current through AMD-014" notes acknowledge this honestly.
- **Editing AMD-019's text in `spec-node6-metrics.md`.** The full canonical text stays where it was authored. The index entry references it.
- **Any other amendments file edits.** No content changes to AMD-001 through AMD-018 bodies. No reformatting of working tables. No prose edits.
- **Changes to `naming-convention.md`.** The taxonomy is correct as written; this cleanup brings the file back into alignment with it.
- **Changes to other specs that reference AMD-019.** Specs that reference AMD-019 by location ("AMD-019's text lives in `spec-node6-metrics.md`") remain valid because the dual-location pattern means the text is still there.

---

## Verification

```
uv run pytest tests/ -q
```

161 passing, 0 failed. Doc-only PR; no code changes; test count must remain at the current baseline.

```
uv run ruff check src/ tests/
```

Clean. Do not run `ruff format`.

**Manual checks before opening PR:**

1. `grep -nE "^### AMD-[0-9]+" docs/decisions/amendments.md` returns exactly 19 entries, in numerical order: AMD-001 through AMD-019.
2. `grep -nE "^# AMD-" docs/decisions/amendments.md` returns zero entries (no level-1 AMD headings remain).
3. `## Architectural Constraints Log` and `## Open Questions` sections appear after the last AMD entry.
4. Both index sections begin with the "Coverage current through AMD-014" italicized note immediately after the heading.
5. AMD-016's HTML comment (`<!-- AMD-016 appended from standalone file during doc consolidation 2026-04-15 -->`) is preserved.
6. AMD-019 index entry's closing line points to `docs/specs/spec-node6-metrics.md`.
7. `workflow.md` has the new spec-centric structure (§Two session types, §The full cycle / Step 1 through Step 9) and the index-maintenance language is present in the appropriate sections.

---

## Commit

```
chore(docs): restore amendments.md ordering, rewrite workflow.md, index AMD-019

- Move AMD-018 to numerical position after AMD-017
- Reformat AMD-016 heading from level-1 to level-3
- Add AMD-019 index entry; full text remains in spec-node6-metrics.md
- Move Architectural Constraints Log and Open Questions to end of file
- Add coverage drift notes to both index sections
- Rewrite workflow.md from 8-step phase-centric structure to spec-centric structure reflecting Phase 9 session pattern
- Add index-maintenance step to workflow.md AMD-creating sessions
```

Single commit. The diff contains structural moves and additions in `amendments.md`, plus the `workflow.md` rewrite. No content changes to existing AMD bodies in `amendments.md`.

---

## Why no AMD entry for this PR

This cleanup is doc hygiene, not an architectural decision. It restores file structure to match conventions established in `naming-convention.md` and updates `workflow.md` to reflect actual current process. No new architectural commitment is being made; nothing is being decided that wasn't already decided.

The workflow.md rewrite captures process discipline that already existed in spirit; documenting it doesn't change the process. The index-maintenance language operationalizes a discipline that prevents future drift of the kind this cleanup is correcting; it's a process refinement, not a new commitment.

If the workflow.md rewrite were standalone, it could arguably warrant an AMD entry. Bundled into this cleanup PR, the workflow change is part of the cleanup itself — making the discipline that the cleanup is correcting concrete and visible. Future AMDs that emerge from this point onward will follow the discipline; the workflow update is the mechanism, not a separately-recordable decision.

---

*Companion: `naming-convention.md` (the taxonomy this cleanup restores compliance with), `workflow.md` (rewritten by this PR), `spec-node6-metrics.md` (canonical home of AMD-019's full text).*
