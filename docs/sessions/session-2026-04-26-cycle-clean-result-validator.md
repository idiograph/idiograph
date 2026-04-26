# Idiograph — Session Summary
**Date:** 2026-04-26
**Status:** FROZEN — historical record, do not revise
**Session type:** Implementation
**Branch:** refactor/cycle-clean-result-validator (PR pending)

---

## Context

The Node 6 design session (2026-04-24, drafted in claude.ai) identified a
prerequisite: `CycleCleanResult` must guarantee that every endpoint in
`cleaned_edges` references a `node_id` in the input node set. Today the
contract is documented in `spec-node4.5-cycle-cleaning.md` but only
`clean_cycles()`'s own internal logic upholds it. A bug there would silently
produce orphaned-endpoint edges, and Node 6 would either need a defensive
check (which Nodes 7/8/9 would also need, multiplying paranoia) or trust a
contract nothing actively guarantees.

This session lands the prerequisite. Same architectural shape as the
SuppressedEdge → Node 5 prerequisite (PR #11 before PR #13): tighten the
upstream output contract before the consumer ships.

Going in: main at `f9b884b`, 113 tests passing.

---

## What Was Changed

### `CycleCleanResult` gains a witness field and a model validator

`src/idiograph/domains/arxiv/models.py`:

- New required field `input_node_ids: frozenset[str] = Field(exclude=True, repr=False, ...)`.
  No default. Required at every construction path.
- New `@model_validator(mode="after")` `_validate_edge_endpoints` that
  raises `ValueError` (surfaced as `ValidationError`) on the first
  `cleaned_edges` endpoint absent from the witness, naming both the
  offending `node_id` and the edge it appears on.
- Class docstring expanded to document the contract: the witness fires the
  validator on every construction path (`__init__`, `model_validate`,
  `model_validate_json`); `Field(exclude=True)` keeps the witness out of
  `model_dump()` and `repr()`; `model_validate(model_dump(result))` raises
  because the dump lacks the witness — persistence reload sites must
  re-supply `input_node_ids` from the loaded node list. This is the
  contract Node 8 will honor.

The `Field(exclude=True)` pattern was chosen specifically over `PrivateAttr`
and over a `construct_validated` factory because both leave direct
`CycleCleanResult(...)` construction unprotected — the invariant attaches to
a code path rather than to the type, and any caller using regular
construction silently skips the check. The Pydantic v2 trap of
leading-underscore field names being silently ignored (without an explicit
`PrivateAttr()` default) was avoided by naming the field
`input_node_ids` (no underscore).

### `clean_cycles()` populates the witness

`src/idiograph/domains/arxiv/pipeline.py` — single addition to the return
statement: `input_node_ids=frozenset(n.node_id for n in nodes)`. No change
to the algorithm; the witness is the input `nodes` parameter, not any
post-filtering subset.

### `spec-node4.5-cycle-cleaning.md` — two §Contracts edits

1. **Constructor invariant** appended to §Data models. Names
   `Field(exclude=True)` directly so the spec is unambiguous about the
   implementation pattern when read standalone. Documents the round-trip-
   requires-witness consequence and the persistence-reload contract.
2. **Missing-node bullet** in §Contracts and edge cases amended. The prior
   "Do not raise" graceful-degradation language is now explicitly
   superseded: citation-count lookup still treats unknown endpoints as
   `citation_count=0` and logs at WARNING (preserved), but result
   construction now raises `pydantic.ValidationError` when the surviving
   `cleaned_edges` retain an orphan. The two contracts were always
   incompatible — the validator surfaced it. The §Ordered step 9 invariant
   note and this §Contracts amendment land in the same PR by design.

### Tests

`tests/domains/arxiv/test_pipeline_node4_5.py`:

- Seven new tests covering the validator and the field semantics:
  `test_validator_passes_on_clean_cycles_output`,
  `test_validator_rejects_orphan_source`,
  `test_validator_rejects_orphan_target`,
  `test_witness_required_at_construction`,
  `test_model_dump_omits_witness`,
  `test_serialization_round_trip_requires_witness`,
  `test_clean_cycles_populates_witness`.
- One existing test renamed and inverted:
  `test_missing_citation_node_warns` → `test_missing_citation_node_raises`.
  The WARNING-then-raise sequence is asserted explicitly — the
  citation-count=0 WARNING still fires before the construction-time
  `ValidationError`, preserving the half of the prior behavior that survives
  the contract change.

---

## Mid-session Surface

The original prompt asserted "Existing 113 tests must stay green." Running
the test suite after the model edit revealed
`test_missing_citation_node_warns` failing — the test exercises a malformed
input (edge to an unknown `node_id`) that the prior spec said `clean_cycles()`
should tolerate, but that the new validator correctly rejects as an orphan
endpoint. Stopped and surfaced the conflict rather than guessing. User
direction: take the rename-and-invert path, treat the spec supersession as
in-scope for §Ordered step 9, accept that the prompt's "113 stay green"
claim was technically false. The prompt file
(`tmp/prompt-cycle-clean-result-validator.md`) is untracked but was updated
locally for record accuracy.

This is the kind of buried-incompatibility that the validator's purpose is
exactly to flush out: making illegal states unrepresentable forces
contradictions in older contracts to the surface where they can be resolved
deliberately rather than masked by graceful-degradation paths.

---

## Test Gate

| Metric | Before | After |
|---|---|---|
| Tests passing | 113 | 120 |
| New tests | — | 7 (validator coverage) |
| Renamed/inverted tests | — | 1 (`test_missing_citation_node_warns` → `_raises`) |
| ruff check (touched files) | clean | clean |

`uv run pytest tests/ -q` → `120 passed`.
`uv tool run ruff check <touched files>` → `All checks passed!`.

---

## Contracts that did NOT change

- `clean_cycles()` external signature
- `clean_cycles()` algorithm — only the return statement gained the
  `input_node_ids=` kwarg
- `CycleCleanResult.cleaned_edges` and `cycle_log` shape and semantics
- `CycleLog.affected_node_ids` behavior
- `SuppressedEdge` (untouched per §Out of scope)
- The step-5 null-handling language in `spec-node4.5-cycle-cleaning.md`
  (untouched per §Out of scope — that edit lands in the post-Node-6 doc PR)
- Node 6 spec (`spec-node6-metrics.md`) — read for context only, not landed
  in this PR per §Doc landing note
- No new dependencies; `model_validator` is already provided by Pydantic v2

---

## Workflow Observations

**Validator-first refactors expose buried legacy contracts.** The
`test_missing_citation_node_warns` failure was the cleanest possible signal
that two specifications had been quietly incompatible since Node 4.5
shipped. Surfacing the conflict at the test boundary, before any consumer
trusted the new contract, is exactly the value the validator pattern adds.
Worth flagging in the Node 7/Node 8 prerequisite stories: expect to find
similar buried contradictions when those nodes' validators land.

**Doc landing note worked as written.** Both ride-along design artifacts
(`docs/sessions/session-2026-04-24-node6-design.md` and
`docs/specs/spec-node6-metrics.md`) were untracked at session start. The
session summary lands with this PR per the precedent (PR #13's Node 5 spec
landing). The Node 6 spec is held back per its own §Freeze trigger — it
cannot freeze before any implementation has proven it correct.

**`uv tool run ruff` not `uv run ruff`.** `ruff` is not pinned as a dev
dependency in this project's environment; it must be invoked via
`uv tool run ruff check ...`. The Node 5 implementation session noted the
same gotcha. `ruff format` was deliberately not run, per §Out of scope —
file-scoped reformatting would touch pre-existing sites unrelated to this
refactor.

---

## Commits

```
<sha>  refactor(arxiv): CycleCleanResult validator — orphan endpoints unconstructible
```

---

## What's Next

1. **Open the PR** for `refactor/cycle-clean-result-validator` and merge
   into main. Node 6 branch waits for this.
2. **Node 6 implementation** — Claude Code session against the (frozen-on-
   merge) `spec-node6-metrics.md`. 24 new tests, baseline 137 expected.
3. **Cross-spec language updates** — follow-up doc PR after Node 6 lands.
   Includes the deferred step-5 null-handling supersession in
   `spec-node4.5-cycle-cleaning.md` and the AMD-017 "Downstream Metric
   Behavior" table touch-up.
4. **Node 7 design session** — communities. Will inherit the same
   `CycleCleanResult` trust pattern and is expected to surface its own
   prerequisite refactor (validator on the next-stage result type).

---

*Companion documents: `tmp/prompt-cycle-clean-result-validator.md` (session
brief, untracked), `docs/sessions/session-2026-04-24-node6-design.md`
(design rationale, ride-along to this PR), `docs/specs/spec-node6-metrics.md`
(consumer spec, NOT in this PR per §Doc landing note),
`session-2026-04-22-suppressed-edge-refactor.md` (precedent — same
"upstream contract tightening" pattern that preceded Node 5).*
