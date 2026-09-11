# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# IDG-076 clause 3 — the params-marshalling coverage test.
#
# WHY THIS EXISTS. A converted stage's params keys are spelled out BY HAND. If a
# field is later added to a parameters model and that spelling is not updated,
# the handler silently takes the model's default while `content_address` — which
# hashes `PipelineParameters` WHOLE — MOVES. The result is a stored record keyed
# to a configuration that was never applied. For the three defaulted models
# (CoCitationParameters, PageRankParameters, CommunitiesParameters) that
# divergence is silent; for BackwardParameters, whose fields are all required, it
# raises. This test makes both loud at authoring time instead. THE HAZARD IS
# UNCHANGED BY THE FLIP — address/behavior desync, ending in a false HIT — only
# the address of the hand-spelling moved.
#
# WHAT IT ASSERTS. The hand-spelling used to live at each stage's direct call
# site inside `run_traversal`. The executor flip (IDG-075 clause 4e) deleted
# those call sites: `run_traversal` now builds the declared graph and executes
# it, so the ONE place each stage's params are spelled out is its `Node` in
# `build_pipeline_graph`. That is what this file reads now (IDG-076 clause 4 as
# implemented by IDG-089). For each converted stage carrying a parameters model,
# with P the key set of `build_pipeline_graph(...).get_node(node_id).params` and
# F the model's field set:
#
#     F ⊆ P ⊆ F ∪ extras
#
# The LOWER bound catches a configured field the graph dropped. The UPPER bound
# keeps a typo from hiding in the extras gap — `extras` is named explicitly per
# stage, never inferred.
#
# WHY THE TRIANGULATION IS STILL REAL. The bounds are only worth asserting if the
# two sides are independent, and they are: `models.py` imports nothing from
# `pipeline_graph`, so the config models cannot be deriving their fields from the
# graph or the graph its params from them. A field added to one genuinely does
# not reach the other, which is the whole hazard.
#
# HOW — a BUILT GRAPH, not a static parse. The predecessor read `run_traversal`'s
# source with `ast`, because the property was a property of that function's
# SOURCE and the stage it most needed to cover (Node 3) is network-bound, so
# every harness mocks it and a runtime recorder would have observed a stand-in.
# Neither objection survives the flip: `build_pipeline_graph` is a pure function
# of its two arguments — no I/O, no network, no event loop, no client — so its
# params can be read by CALLING it, with no harness and nothing mocked. Reading
# the real object is strictly better than parsing a rendering of it: a params
# value that is computed rather than literal is now in scope, where the parser
# had to require literal keys and would have raised rather than decide.
#
# `compute_depth_metrics`, `clean_cycles`, `assemble_graph` and `enrich_nodes`
# carry no parameters model (their nodes declare `params={}`) and are out of
# scope. So is `resolve_seeds`, whose seed set is request data rather than
# configuration; Node 0 keeps a second spelling at its own direct call site in
# `run_arxiv_pipeline` and is checked there, in test_pipeline_graph.py.
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

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CoCitationParameters,
    CommunitiesParameters,
    ForwardParameters,
    PageRankParameters,
    PipelineParameters,
)
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph

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


#: Which node in the declared graph carries each stage's params. Written out as
#: literal strings rather than imported from `pipeline_graph`: this file has to
#: be able to disagree with the module it reads, and a node id imported from the
#: subject would agree with a rename by construction. A rename that does not
#: reach this table makes `get_node` return None, which fails loudly below.
STAGE_NODE_IDS: dict[str, str] = {
    "backward_traverse": "backward",
    "forward_traverse": "forward",
    "compute_co_citations": "co",
    "compute_pagerank": "pagerank",
    "detect_communities": "communities",
}


def _graph_params_keys(handler_name: str) -> set[str]:
    """The params keys the declared graph puts on `handler_name`'s node.

    P in `F ⊆ P ⊆ F ∪ extras`. Read off a BUILT graph rather than parsed out of
    source: `build_pipeline_graph` is pure, so calling it is cheap, needs no
    harness, and yields the actual mapping the executor will hand the handler
    rather than a rendering of it.

    The parameters passed in are fully populated — every optional field
    explicitly set — so that a key cannot be missing from P merely because this
    fixture left its value unset. The bound must fail on a graph that DROPS a
    field, not on a fixture that never asked for it.
    """
    node_id = STAGE_NODE_IDS[handler_name]
    graph = build_pipeline_graph(_seeds(), _parameters())
    node = graph.get_node(node_id)
    assert node is not None, (
        f"the declared graph has no node '{node_id}' for {handler_name}(). "
        f"Either the node was renamed and STAGE_NODE_IDS was not updated, or "
        f"the stage left the graph — in which case its params are no longer "
        f"spelled out anywhere this file checks, and the bounds below would "
        f"pass vacuously."
    )
    return set(node.params)


def _seeds() -> list[dict]:
    """Seed request dicts. Node 0's params only; no stage read here takes them."""
    return [{"arxiv_id": "2401.00001"}, {"doi": "10.1000/xyz123"}]


def _parameters() -> PipelineParameters:
    """A FULLY POPULATED `PipelineParameters`.

    Every nested config model is constructed explicitly, including the three that
    would otherwise default (`co_citation`, `pagerank`, `communities`). The point
    is the lower bound `F ⊆ P`: if this fixture omitted a sub-model, the graph
    would still read its fields off the default instance and P would be complete,
    so the omission would not be detectable. Populating them means P is the graph
    marshalling a real configuration, which is the case the hazard lives in.
    """
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        # Stated, never read from the clock: it enters the content address, so a
        # wall-clock value would move every address in this file on New Year.
        current_year=2026,
        co_citation=CoCitationParameters(min_strength=2, max_edges=None),
        pagerank=PageRankParameters(damping=0.85),
        communities=CommunitiesParameters(),
    )


@pytest.mark.parametrize(
    ("handler_name", "model", "extras", "handler_params_model"),
    STAGES,
    ids=_STAGE_IDS,
)
def test_call_site_passes_every_model_field(
    handler_name, model, extras, handler_params_model
) -> None:
    """F ⊆ P — the graph puts every field its parameters model declares on the node.

    The lower bound. A field added to the model but not to the node is the
    silent-default hazard: the handler runs on the model default while
    `content_address` moves, storing a record keyed to a configuration that was
    never applied.
    """
    passed = _graph_params_keys(handler_name)
    fields = set(model.model_fields)

    missing = fields - passed
    assert not missing, (
        f"{handler_name}()'s node does not carry {sorted(missing)} from "
        f"{model.__name__}. The handler would take the model default while "
        f"content_address hashes the configured value — a stored record keyed "
        f"to a configuration that was never applied. Add the key to the node's "
        f"params in build_pipeline_graph."
    )


@pytest.mark.parametrize(
    ("handler_name", "model", "extras", "handler_params_model"),
    STAGES,
    ids=_STAGE_IDS,
)
def test_call_site_passes_no_unaccounted_keys(
    handler_name, model, extras, handler_params_model
) -> None:
    """P ⊆ F ∪ extras — every key on the node is either a model field or a
    per-stage extra named in this file.

    The upper bound, and the reason `extras` is enumerated rather than inferred:
    without it a typo'd key (`n_backwards`) would satisfy the lower bound only
    by accident of the real key also being present, and would otherwise hide in
    the gap. A new extra is a deliberate edit to STAGES above, with a reason.
    """
    passed = _graph_params_keys(handler_name)
    allowed = set(model.model_fields) | extras

    unaccounted = passed - allowed
    assert not unaccounted, (
        f"{handler_name}()'s node carries {sorted(unaccounted)}, which are "
        f"neither {model.__name__} fields nor declared extras. Either it is a "
        f"typo, or the key is a deliberate non-address param and belongs in "
        f"this stage's `extras` set with a reason."
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
