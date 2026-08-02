# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# IDG-076 clause 3 — the params-marshalling coverage test.
#
# WHY THIS EXISTS. A converted stage's direct call site in `run_traversal`
# spells out its params keys BY HAND. If a field is later added to a parameters
# model and the call site is not updated, the handler silently takes the model's
# default while `content_address` — which hashes `PipelineParameters` WHOLE —
# MOVES. The result is a stored record keyed to a configuration that was never
# applied. For the three defaulted models (CoCitationParameters,
# PageRankParameters, CommunitiesParameters) that divergence is silent; for
# BackwardParameters, whose fields are all required, it raises. This test makes
# both loud at authoring time instead.
#
# WHAT IT ASSERTS. For each converted stage carrying a parameters model, with P
# the set of params keys the direct call site passes and F the model's field
# set:
#
#     F ⊆ P ⊆ F ∪ extras
#
# The LOWER bound catches a configured field the call site dropped. The UPPER
# bound keeps a typo from hiding in the extras gap — `extras` is named
# explicitly per stage, never inferred.
#
# HOW — STATIC PARSE, not runtime capture. Both were defensible; this route was
# chosen because the property under test is a property OF THE CALL SITE'S
# SOURCE, and reading the source tests it directly with no harness, no fixtures
# and no network fakes. The decisive point against runtime capture is Node 3
# itself: every existing harness that drives `run_traversal` end-to-end MOCKS
# `backward_traverse` (it is network-bound), so a recorder monkeypatched over it
# would observe the params of a stand-in — and un-mocking it to observe the real
# marshalling would mean rebuilding the OpenAlex fakes here purely to read back
# a dict of keys. The static parse reads what `run_traversal` actually writes.
#
# `compute_depth_metrics`, `clean_cycles` and `assemble_graph` carry no
# parameters model (they are called with `{}`) and are out of scope. So is the
# enrichment comprehension — not yet converted.
#
# THE HANDLER-LOCAL MIRROR (finding 6e77cbfb). A converted stage may also carry a
# handler-local params model (`_BackwardTraverseParams`, `_ForwardTraverseParams`)
# that HAND-MIRRORS its config model plus the stage's extras. Nothing in the type
# system ties the two together, so the mirror can silently drift: add a field to
# `ForwardParameters` and `_ForwardTraverseParams` keeps validating without it,
# at which point the call site passing the new key raises — or worse, under a
# non-strict model, does not. The fourth STAGES element carries the handler-local
# model explicitly (None for stages that validate directly against their config
# model), and `test_handler_local_model_mirrors_config_model` below asserts the
# mirror. It is named, never inferred by name-mangling from the handler name: a
# mangling rule that failed to resolve would skip the stage silently, which is
# the failure mode this guard exists to prevent.

import ast
from pathlib import Path

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CoCitationParameters,
    CommunitiesParameters,
    ForwardParameters,
    PageRankParameters,
)

#: (handler name as called in `run_traversal`, its parameters model, the params
#: keys that are legitimately NOT model fields, the handler-local params model or
#: None).
#:
#: Every extra is in `EXTRA_REASONS` below with a stated reason; nothing may sit
#: in an extras set without one. The two kinds, both present here:
#:
#:   - NOT output-determining, so it must stay OUT of the hashed model —
#:     `sleep_ms` (pacing), `acceleration_method` (one admissible method).
#:     Putting either on the config model would re-address every existing cached
#:     record for a value that changes nothing.
#:   - output-determining and IN the hashed model, but at a different level —
#:     `current_year` is a top-level `PipelineParameters` field, so it is hashed;
#:     it is an "extra" here only relative to the PER-STAGE config model, because
#:     both traversal stages score against one run-level year and two per-stage
#:     copies would have to agree with nothing enforcing agreement.
STAGES = [
    ("backward_traverse", BackwardParameters, {"sleep_ms", "current_year"},
     pipeline._BackwardTraverseParams),
    ("forward_traverse", ForwardParameters,
     {"acceleration_method", "current_year"}, pipeline._ForwardTraverseParams),
    ("compute_co_citations", CoCitationParameters, set(), None),
    ("compute_pagerank", PageRankParameters, set(), None),
    ("detect_communities", CommunitiesParameters, set(), None),
]

_STAGE_IDS = [name for name, _model, _extras, _local in STAGES]

#: The reason each extra key exists, keyed by `(handler_name, key)`. This table
#: is the replacement for the old `test_backward_extras_is_exactly_sleep_ms`,
#: which pinned "Node 3 is the only stage with an extra and it is `sleep_ms`" —
#: true when Node 3 was the only converted traversal stage, false the moment
#: Node 4 joined and `current_year` moved onto `PipelineParameters`.
#:
#: The PROPERTY that test pinned is the one that had to survive: an extras set is
#: a hole in the upper bound `P ⊆ F ∪ extras`, so every key sitting in it must be
#: there for a STATED reason, and a key appearing in the gap unexplained must be
#: caught. That property is now enforced generically — for every stage rather
#: than only Node 3 — by requiring each extra to carry a reason HERE, in a second
#: place, deliberately separate from STAGES. Adding an extra to STAGES alone
#: fails `test_every_extra_has_a_stated_reason`; the author must come here and
#: write down why.
EXTRA_REASONS: dict[tuple[str, str], str] = {
    ("backward_traverse", "sleep_ms"): (
        "Pacing between OpenAlex batch calls. Cannot affect output, so it must "
        "not join BackwardParameters, which content_address hashes whole — that "
        "would re-address every cached record for a knob that changes nothing. "
        "One home: pipeline.BACKWARD_SLEEP_MS."
    ),
    ("backward_traverse", "current_year"): (
        "Output-determining and hashed, but at the RUN level: its home is the "
        "top-level PipelineParameters.current_year, not BackwardParameters. Node "
        "4 scores against the same year, and two per-stage fields would have to "
        "agree with nothing enforcing agreement. An extra relative to the "
        "per-stage config model only."
    ),
    ("forward_traverse", "acceleration_method"): (
        "pipeline._compute_acceleration admits exactly ONE non-raising method, "
        "so hashing it would re-address every cached record to record a "
        "constant. Guarded by the tripwire in "
        "test_forward_traverse_handler.py::test_compute_acceleration_admits_"
        "exactly_one_method, which fails the day regression is implemented. One "
        "home: pipeline.FORWARD_ACCELERATION_METHOD."
    ),
    ("forward_traverse", "current_year"): (
        "Same run-level field as Node 3's, marshalled to both stages from one "
        "top-level PipelineParameters.current_year. See that entry."
    ),
}


def _run_traversal_ast() -> ast.AsyncFunctionDef:
    """The parsed `run_traversal` body — the single site every converted stage
    is called from on the direct path."""
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_traversal":
            return node
    raise AssertionError(
        "run_traversal not found in pipeline.py — this test is pointed at the "
        "wrong function and would otherwise pass vacuously."
    )


def _params_keys(handler_name: str) -> set[str]:
    """The params keys the direct call site passes to `handler_name`.

    The params mapping is the call's FIRST POSITIONAL argument, per the handler
    convention `(params, inputs, ...)`. Every key must be a literal string:
    a computed key would mean this property is no longer statically decidable,
    and silently returning fewer keys would weaken the assertion rather than
    fail it, so it raises.
    """
    calls = [
        node
        for node in ast.walk(_run_traversal_ast())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == handler_name
    ]
    assert len(calls) == 1, (
        f"expected exactly one call to {handler_name}() in run_traversal, "
        f"found {len(calls)} — the marshalling this test checks is no longer "
        f"at a single site."
    )
    (call,) = calls

    assert call.args, f"{handler_name}() called with no positional params mapping"
    params_arg = call.args[0]
    assert isinstance(params_arg, ast.Dict), (
        f"{handler_name}()'s params argument is not a dict literal, so its keys "
        f"cannot be read statically."
    )

    keys: set[str] = set()
    for key in params_arg.keys:
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
            f"{handler_name}() passes a non-literal params key; this test can "
            f"only decide the property over literal keys."
        )
        keys.add(key.value)
    return keys


@pytest.mark.parametrize(
    ("handler_name", "model", "extras", "handler_params_model"),
    STAGES,
    ids=_STAGE_IDS,
)
def test_call_site_passes_every_model_field(
    handler_name, model, extras, handler_params_model
) -> None:
    """F ⊆ P — the call site passes every field its parameters model declares.

    The lower bound. A field added to the model but not to the call site is the
    silent-default hazard: the handler runs on the model default while
    `content_address` moves, storing a record keyed to a configuration that was
    never applied.
    """
    passed = _params_keys(handler_name)
    fields = set(model.model_fields)

    missing = fields - passed
    assert not missing, (
        f"{handler_name}() does not pass {sorted(missing)} from "
        f"{model.__name__}. The handler would take the model default while "
        f"content_address hashes the configured value — a stored record keyed "
        f"to a configuration that was never applied. Add the key to the "
        f"run_traversal call site."
    )


@pytest.mark.parametrize(
    ("handler_name", "model", "extras", "handler_params_model"),
    STAGES,
    ids=_STAGE_IDS,
)
def test_call_site_passes_no_unaccounted_keys(
    handler_name, model, extras, handler_params_model
) -> None:
    """P ⊆ F ∪ extras — every key the call site passes is either a model field
    or a per-stage extra named in this file.

    The upper bound, and the reason `extras` is enumerated rather than inferred:
    without it a typo'd key (`n_backwards`) would satisfy the lower bound only
    by accident of the real key also being present, and would otherwise hide in
    the gap. A new extra is a deliberate edit to STAGES above, with a reason.
    """
    passed = _params_keys(handler_name)
    allowed = set(model.model_fields) | extras

    unaccounted = passed - allowed
    assert not unaccounted, (
        f"{handler_name}() passes {sorted(unaccounted)}, which are neither "
        f"{model.__name__} fields nor declared extras. Either it is a typo, or "
        f"the key is a deliberate non-address param and belongs in this "
        f"stage's `extras` set with a reason."
    )


def test_every_extra_has_a_stated_reason() -> None:
    """Every key in every stage's `extras` set is there for a written reason.

    THE REPLACEMENT for `test_backward_extras_is_exactly_sleep_ms`, which pinned
    "Node 3 is the only stage with an extra, and it is `sleep_ms`". That
    statement stopped being true when Node 4 converted and `current_year` moved
    onto `PipelineParameters` — but the property underneath it did not, so it is
    re-pinned here in a form that does not have to be rewritten every time a
    stage converts.

    The property: `extras` is a deliberate hole in the upper bound
    `P ⊆ F ∪ extras`, so a key that lands in it stops being checked against any
    model. Enumerating the extras alone is not enough — that only makes the hole
    explicit, not justified. This requires the hole to be ARGUED, in a second
    table the author has to edit separately.

    Adding an arbitrary new extra to any stage fails HERE, which is the
    strength the old test had and this one had to keep: it is not satisfiable by
    editing STAGES.
    """
    declared = {
        (name, key) for name, _model, extras, _local in STAGES for key in extras
    }

    unexplained = declared - set(EXTRA_REASONS)
    assert not unexplained, (
        f"{sorted(unexplained)} sit in an extras set with no entry in "
        f"EXTRA_REASONS. An extra is a hole in the `P ⊆ F ∪ extras` upper "
        f"bound: the key stops being checked against any model, so it must be "
        f"argued, not merely declared. Add a reason saying why the key is NOT a "
        f"field on the stage's config model — either it cannot affect output "
        f"(and so must stay out of the hashed model), or it is hashed at "
        f"another level."
    )

    stale = set(EXTRA_REASONS) - declared
    assert not stale, (
        f"EXTRA_REASONS explains {sorted(stale)}, which no stage declares as an "
        f"extra any more. The reason outlived the key — delete it, or restore "
        f"the extra to STAGES."
    )

    blank = sorted(k for k, why in EXTRA_REASONS.items() if not why.strip())
    assert not blank, (
        f"{blank} carry an empty reason. An empty string satisfies the key "
        f"check without stating anything; write the argument."
    )


def test_extras_are_disjoint_from_their_config_model() -> None:
    """No stage lists an extra that is ALSO a field on its config model.

    The other half of what the old test's `"sleep_ms" not in model.model_fields`
    line pinned, generalized off Node 3. An extra naming a real config field
    would widen the upper bound to allow a key that the lower bound already
    requires, so the key would pass both bounds while nothing checked it came
    from the model. It also silently un-guards the field: if pacing ever moves
    onto `BackwardParameters`, or `current_year` onto the per-stage models, the
    extras set must shrink with it and this is what says so.
    """
    for name, model, extras, _local in STAGES:
        overlap = extras & set(model.model_fields)
        assert not overlap, (
            f"{name} lists {sorted(overlap)} as extras, but they are "
            f"{model.__name__} fields. The key is now configuration in the "
            f"hashed model — drop it from `extras` (and from EXTRA_REASONS)."
        )


@pytest.mark.parametrize(
    ("handler_name", "model", "extras", "handler_params_model"),
    [s for s in STAGES if s[3] is not None],
    ids=[name for name, _m, _e, local in STAGES if local is not None],
)
def test_handler_local_model_mirrors_config_model(
    handler_name, model, extras, handler_params_model
) -> None:
    """The handler-local params model is exactly its config model plus extras.

    Finding 6e77cbfb. `_BackwardTraverseParams` and `_ForwardTraverseParams`
    HAND-MIRROR `BackwardParameters` / `ForwardParameters`; nothing in the type
    system relates them, so the mirror can drift in either direction and both
    directions are live hazards:

      - a field added to the CONFIG model and not to the handler-local one: the
        call site (which `test_call_site_passes_every_model_field` forces to
        pass it) hands the handler a key its params model does not declare.
      - a field added to the HANDLER-LOCAL model and not to `extras`: a
        configuration input that never reaches `content_address`, which is the
        false-HIT shape this whole file exists to prevent.

    Stages validating directly against their config model carry no handler-local
    model and are skipped by the parametrization — they cannot drift, having
    nothing to drift from.
    """
    mirrored = set(handler_params_model.model_fields)
    expected = set(model.model_fields) | extras

    assert mirrored == expected, (
        f"{handler_params_model.__name__} declares {sorted(mirrored)}, but "
        f"{model.__name__} plus this stage's extras is {sorted(expected)}. "
        f"Missing from the handler-local model: "
        f"{sorted(expected - mirrored)}. Present only there: "
        f"{sorted(mirrored - expected)}. The two are hand-mirrored — nothing "
        f"but this test holds them together."
    )
