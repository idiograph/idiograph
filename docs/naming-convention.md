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
