# Idiograph — Doc Consolidation Migration Spec (v2)
**Status:** LIVING — anti-drift spec for Claude Code execution
**Branch:** `chore/doc-consolidation`
**Re-read this file at the top of every prompt.**
**Do not deviate from the sequence. Flag blockers; do not proceed past a blocker.**

---

## Context

This spec was written against a verified repo snapshot. The file inventory below
reflects what actually exists. Do not assume any file exists beyond what is listed.

---

## Anti-Drift Rules

- Re-read this spec at the top of every prompt.
- Pre-flight reads must complete and be reported before Phase 1 begins.
- Use `git mv` for every move and rename. Never `cp` then `rm`.
- Do not change any file's content except where explicitly instructed.
- Do not delete any file without explicit instruction in this spec.
- If a step is ambiguous, stop and ask. Do not resolve ambiguity silently.
- Frozen documents (marked `FROZEN` in header) must not have content edited.
  If a cross-reference update would require editing a frozen document, flag it.

---

## Verified File Inventory

**docs/ root (loose — all need action):**
```
docs/amd-016-llm-node-placement-2.md
docs/blueprint.md
docs/color_designer_spec-1.md
docs/nodeforge_architecture.md
docs/phase_0_summary.md
docs/phase_1_summary.md
docs/phase_2_summary.md
docs/phase_3_summary.md
docs/phase_4.5_summary.md
docs/phase_5_summary.md
docs/phase_6_summary.md
docs/phase_7_summary.md
docs/phase_8_9_task_inventory.md
docs/phase_8_summary.md
docs/rename_summary.md
docs/session_summary_2026_04_03.md
docs/session_summary_amd012.docx
docs/session_summary_amd014_amd010.docx
docs/session_workflow.md
docs/session-2026-04-08.md
docs/session-2026-04-09 (1).md
docs/session-2026-04-09.md
docs/session-2026-04-10.md
docs/spec-arxiv-pipeline-final.md
docs/state_management_migration.md
```

**docs/Claude_files/ (confirmed junk — browser export artifacts):**
```
docs/Claude_files/468742119653388
docs/Claude_files/analytics.min.js.download
docs/Claude_files/c6a992d55-BVbTAvz0.css
docs/Claude_files/c836d6dec-BqPxldXl.css
docs/Claude_files/commons.59560acdd69ed701c941.js.gz
docs/Claude_files/f12a4347e1080fb88155.js.download
docs/Claude_files/facebook-pixel.dynamic.js.gz
docs/Claude_files/fbevents.js.download
docs/Claude_files/index-BFYmYiIx.js.download
docs/Claude_files/isolated-segment.html
docs/Claude_files/lupk8zyo
docs/Claude_files/s.js.download
docs/Claude_files/saved_resource(1).html
docs/Claude_files/saved_resource(2).html
docs/Claude_files/saved_resource.html
```

**Already correctly located — no action required:**
```
docs/decisions/amendments.md
docs/sessions/session-2026-04-13.md
docs/sessions/session-2026-04-13-2.md
docs/sessions/session-2026-04-13-3.md
docs/sessions/session-2026-04-14.md
docs/specs/findings-citation-acceleration.md
docs/specs/findings-openalex-crispr.md
docs/specs/spec-citation-acceleration-spike.md
docs/specs/spec-color-designer-domain-impl.md
docs/specs/spec-color-designer-domain-refactor.md
docs/specs/spec-doc-consolidation.md        ← superseded by this file
docs/specs/spec-openalex-validation-spike.md
```

**Repo root (no action on these):**
```
CLAUDE.md, README.md, validation-prompt.md
pyproject.toml, idiograph.toml, uv.lock, .python-version
.env, .env.example, .gitignore
bad_graph.json, test_graph.json, file_header_template.py
```

---

## Pre-Flight — Read Only, Report Before Proceeding

**P1 — Date discovery for .docx files and session artifacts**

```bash
git log --follow --format="%ad" --date=short -- "docs/session_summary_amd012.docx" | head -1
git log --follow --format="%ad" --date=short -- "docs/session_summary_amd014_amd010.docx" | head -1
git log --follow --format="%ad" --date=short -- "docs/rename_summary.md" | head -1
git log --follow --format="%ad" --date=short -- "docs/state_management_migration.md" | head -1
```

Record all four dates. You will need them for file naming.

**P2 — Extract .docx content**

```bash
pandoc "docs/session_summary_amd012.docx" -t markdown -o /tmp/amd012.md
pandoc "docs/session_summary_amd014_amd010.docx" -t markdown -o /tmp/amd014.md
cat /tmp/amd012.md
cat /tmp/amd014.md
```

Read both extracted files in full.

**P3 — Read phase summaries for topic names**

Read the first 5 lines of each:
```bash
for f in docs/phase_1_summary.md docs/phase_2_summary.md docs/phase_3_summary.md \
          docs/phase_4.5_summary.md docs/phase_5_summary.md docs/phase_6_summary.md \
          docs/phase_7_summary.md; do
  echo "=== $f ==="
  head -5 "$f"
done
```

Extract a 2–4 word kebab-case topic from each heading. Examples: `core-data-models`,
`graph-execution`, `handler-registry`. Do not guess — derive from the heading.

**P4 — Check nodeforge_architecture.md**

```bash
head -20 docs/nodeforge_architecture.md
wc -l docs/nodeforge_architecture.md
```

Determine: is this document superseded by current docs, or does it contain
unique content? Report your assessment.

**P5 — Confirm AMD-016 standalone content**

```bash
head -10 docs/amd-016-llm-node-placement-2.md
tail -5 docs/decisions/amendments.md
```

Confirm AMD-016 is not already in `amendments.md`. (AMD-018 is the last entry —
AMD-016 was recorded as a standalone file and never merged in.)

**Report all pre-flight findings before proceeding to Phase 0.**

---

## Phase 0 — Commit Foundational Docs

Two documents exist in project knowledge but were never committed to the repo.
They must exist in the repo before the migration runs, because other docs
reference them.

**Action 0.1 — Commit `docs/naming-convention.md`**

Create `docs/naming-convention.md` with the content below verbatim. This is the
taxonomy authority for all doc organization in this project.

```
# Idiograph — Documentation Naming Convention
**Status:** STABLE — update only when taxonomy changes
**Last revised:** 2026-04-04

---

## Document Taxonomy

Every document in this project has exactly one type. The type determines where it
lives, how it's named, and whether it can be edited after creation.

| Type | Can be edited? | Freeze trigger | Lives in |
|---|---|---|---|
| **Frozen** | Never after creation | Created frozen | `docs/phases/` or `docs/sessions/` |
| **Living** | Yes, until freeze trigger | Defined at creation | `docs/specs/` |
| **Stable** | Rarely — only if thesis changes | N/A | `docs/vision/` |
| **Generated** | Never by hand | Regenerated from code | `docs/generated/` |

---

## Directory Structure

docs/
  decisions/      ← amendments.md (single append-only file)
  phases/         ← one frozen summary per phase
  sessions/       ← one frozen summary per session
  specs/          ← living design and planning documents
  vision/         ← stable thesis, competitive analysis, principles
  generated/      ← diagrams and anything produced by scripts

---

## Naming Rules

### Phase Summaries — `docs/phases/`
phase-NN-short-topic.md

- NN is zero-padded: 01, 02 ... 10
- Short topic is 2–4 words in kebab-case
- No revision suffixes. Ever. Phase summaries are frozen on creation.

### Session Summaries — `docs/sessions/`
session-YYYY-MM-DD.md

- Date only. No topic, no AMD reference.
- Multiple sessions same date: session-2026-04-03-2.md
- Frozen on creation.

### Living Specs — `docs/specs/`
spec-short-topic.md

### Vision and Thesis Docs — `docs/vision/`
vision-short-topic.md

### Decisions Log — `docs/decisions/`
amendments.md — single file, append-only.

### Generated Documents — `docs/generated/`
Never hand-edited. CI enforces sync with source.

---

## Amendment Entry Status Vocabulary

| Status | Meaning |
|---|---|
| `Accepted` | In force. Implemented or actively constraining design. |
| `Accepted — Not Yet Implemented` | Decision made, code not yet written. |
| `Superseded by AMD-NNN` | No longer in force. |
| `Deferred` | Valid idea, not a current build target. |
| `Rejected` | Considered and explicitly ruled out. |
```

**Action 0.2 — Rename `docs/session_workflow.md` → `docs/workflow.md`**

```bash
git mv docs/session_workflow.md docs/workflow.md
```

Then open `docs/workflow.md` and replace the header line:
```
# NodeForge – Session Workflow Protocol
```
with:
```
# Idiograph — Session Workflow
```

This is the only content change permitted in this file.

**Commit:**
```
chore: add naming-convention.md, rename session_workflow to workflow
```

---

## Phase 1 — Create Missing Directories

```bash
mkdir -p docs/phases
mkdir -p docs/vision
mkdir -p docs/generated
```

`docs/sessions/`, `docs/specs/`, and `docs/decisions/` already exist.

**Commit:**
```
chore: create docs/phases/, docs/vision/, docs/generated/
```

---

## Phase 2 — Session Summaries → `docs/sessions/`

**Moves and renames (use `git mv` for all):**

```bash
git mv "docs/session_summary_2026_04_03.md" "docs/sessions/session-2026-04-03.md"
git mv "docs/session-2026-04-08.md"         "docs/sessions/session-2026-04-08.md"
git mv "docs/session-2026-04-09.md"         "docs/sessions/session-2026-04-09.md"
git mv "docs/session-2026-04-09 (1).md"     "docs/sessions/session-2026-04-09-2.md"
git mv "docs/session-2026-04-10.md"         "docs/sessions/session-2026-04-10.md"
```

**Session artifacts — rename using dates from P1:**

```bash
git mv "docs/rename_summary.md"           "docs/sessions/session-[DATE-P1-rename].md"
git mv "docs/state_management_migration.md" "docs/sessions/session-[DATE-P1-state].md"
```

Fill in dates from pre-flight P1 findings.

**Commit:**
```
chore: move session summaries to docs/sessions/
```

---

## Phase 3 — Convert .docx → .md, Move to `docs/sessions/`

For each file, create a new `.md` at `docs/sessions/session-[DATE].md` using
dates from pre-flight P1. Use the standard session summary format:

```
# Idiograph — Session Summary
**Date:** YYYY-MM-DD
**Status:** FROZEN — historical record, do not revise
**Session type:** [from document content]

---

## Context
[from document]

## What Was Built
[from document]

## Key Decisions
[from document — use table format where content supports it]

## What's Next
[from document]

---

*Companion documents: [from document if present]*
```

Map extracted content faithfully. Do not add content. Do not drop content.
Fix the project name in headers only: "NodeForge" → "Idiograph" where it appears
in the title line (`# Idiograph — Session Summary`). Do not do a global find/replace
on NodeForge throughout the body — these are historical records.

After creating both `.md` files:

```bash
git rm "docs/session_summary_amd012.docx"
git rm "docs/session_summary_amd014_amd010.docx"
```

**Commit:**
```
chore: convert .docx session summaries to markdown
```

---

## Phase 4 — Phase Summaries → `docs/phases/`

Use `git mv`. Topic names derived from pre-flight P3 findings — fill in `[topic]`
from what you read, not from this spec.

```bash
git mv "docs/phase_0_summary.md"   "docs/phases/phase-00-foundation.md"
git mv "docs/phase_1_summary.md"   "docs/phases/phase-01-[topic].md"
git mv "docs/phase_2_summary.md"   "docs/phases/phase-02-[topic].md"
git mv "docs/phase_3_summary.md"   "docs/phases/phase-03-[topic].md"
git mv "docs/phase_4.5_summary.md" "docs/phases/phase-04-05-[topic].md"
git mv "docs/phase_5_summary.md"   "docs/phases/phase-05-[topic].md"
git mv "docs/phase_6_summary.md"   "docs/phases/phase-06-[topic].md"
git mv "docs/phase_7_summary.md"   "docs/phases/phase-07-[topic].md"
git mv "docs/phase_8_summary.md"   "docs/phases/phase-08-mcp-integration.md"
```

**Commit:**
```
chore: move phase summaries to docs/phases/ with canonical names
```

---

## Phase 5 — Vision Docs → `docs/vision/`

```bash
git mv "docs/blueprint.md" "docs/vision/vision-blueprint-original.md"
```

**Commit:**
```
chore: move blueprint to docs/vision/
```

---

## Phase 6 — Loose Specs → `docs/specs/`

```bash
git mv "docs/spec-arxiv-pipeline-final.md" "docs/specs/spec-arxiv-pipeline-final.md"
git mv "docs/color_designer_spec-1.md"     "docs/specs/spec-color-designer.md"
git mv "docs/phase_8_9_task_inventory.md"  "docs/specs/spec-phase-08-09-task-inventory.md"
```

**Commit:**
```
chore: move loose specs to docs/specs/
```

---

## Phase 7 — AMD-016 → `docs/decisions/amendments.md`

AMD-016 exists as a standalone file but was never appended to `amendments.md`.
AMD-018 is currently the last entry (AMD-016 and AMD-017 were skipped in the file).

**Action:** Append the full content of `docs/amd-016-llm-node-placement-2.md`
to the end of `docs/decisions/amendments.md` with this separator:

```markdown

---

<!-- AMD-016 appended from standalone file during doc consolidation 2026-04-15 -->
```

Then paste the full AMD-016 content after it.

Then:
```bash
git rm "docs/amd-016-llm-node-placement-2.md"
```

**Flag for human:** AMD-017 (multi-seed boolean ops) does not exist as a standalone
file in the repo. Content exists in project knowledge only. After this migration,
AMD-017 needs to be written and appended to `amendments.md` in a separate session.
Note this in the commit message.

**Commit:**
```
chore: merge AMD-016 into amendments.md; note AMD-017 missing from repo
```

---

## Phase 8 — Deletions

**`docs/Claude_files/`** — confirmed browser export dump, no unique content:
```bash
git rm -r "docs/Claude_files/"
```

**`docs/nodeforge_architecture.md`** — execute based on P4 finding:
- If superseded and content exists elsewhere: `git rm "docs/nodeforge_architecture.md"`
- If unique content found: flag for human, do not delete.

**`docs/specs/spec-doc-consolidation.md`** — the first (stale) version of this
spec, superseded by this file:
```bash
git rm "docs/specs/spec-doc-consolidation.md"
```

**Commit:**
```
chore: delete Claude_files/, stale nodeforge_architecture.md, superseded spec
```

---

## Phase 9 — Cross-Reference Sweep

Search all `.md` files for stale paths created by the moves above:

```bash
grep -rn "phase_[0-9]" docs/ --include="*.md"
grep -rn "session_summary_" docs/ --include="*.md"
grep -rn "session_workflow" docs/ --include="*.md"
grep -rn "rename_summary" docs/ --include="*.md"
grep -rn "state_management_migration" docs/ --include="*.md"
grep -rn "demo_design_spec" docs/ --include="*.md"
grep -rn "amd-016-llm-node-placement" docs/ --include="*.md"
grep -rn "blueprint\.md" docs/ --include="*.md"
grep -rn "color_designer_spec" docs/ --include="*.md"
grep -rn "spec-doc-consolidation" docs/ --include="*.md"
```

For each hit:
- If in a non-frozen doc: update the path to the new location
- If in a frozen doc (`FROZEN` in header): flag the file and line — do not edit

**Commit:**
```
chore: update cross-references after doc consolidation
```

---

## Done When

`docs/` root contains only:
```
decisions/
generated/
naming-convention.md
phases/
sessions/
specs/
vision/
workflow.md
```

All session summaries in `docs/sessions/` with `session-YYYY-MM-DD.md` naming.
All phase summaries in `docs/phases/` with `phase-NN-topic.md` naming.
`docs/vision/` contains blueprint.
No `.docx` files anywhere in the repo.
AMD-016 in `docs/decisions/amendments.md`.
AMD-017 flagged as outstanding.
CI green. 44 tests passing.

---

## Commit Summary

| Phase | Commit message |
|---|---|
| 0 | `chore: add naming-convention.md, rename session_workflow to workflow` |
| 1 | `chore: create docs/phases/, docs/vision/, docs/generated/` |
| 2 | `chore: move session summaries to docs/sessions/` |
| 3 | `chore: convert .docx session summaries to markdown` |
| 4 | `chore: move phase summaries to docs/phases/ with canonical names` |
| 5 | `chore: move blueprint to docs/vision/` |
| 6 | `chore: move loose specs to docs/specs/` |
| 7 | `chore: merge AMD-016 into amendments.md; note AMD-017 missing from repo` |
| 8 | `chore: delete Claude_files/, stale nodeforge_architecture.md, superseded spec` |
| 9 | `chore: update cross-references after doc consolidation` |

Merge via PR, merge commit (not squash). Branch: `chore/doc-consolidation`.
