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
# parameters model (they are called with `{}`) and are out of scope. So are
# `forward_traverse` and the enrichment comprehension — not yet converted.

import ast
from pathlib import Path

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CoCitationParameters,
    CommunitiesParameters,
    PageRankParameters,
)

#: (handler name as called in `run_traversal`, its parameters model, the params
#: keys that are legitimately NOT model fields). `sleep_ms` is Node 3's one
#: extra: pacing rides `Node.params` precisely BECAUSE it must not join
#: `BackwardParameters`, which `content_address` hashes whole — adding it there
#: would re-address every existing cached record for a knob that cannot affect
#: output.
STAGES = [
    ("backward_traverse", BackwardParameters, {"sleep_ms"}),
    ("compute_co_citations", CoCitationParameters, set()),
    ("compute_pagerank", PageRankParameters, set()),
    ("detect_communities", CommunitiesParameters, set()),
]

_STAGE_IDS = [name for name, _model, _extras in STAGES]


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
    ("handler_name", "model", "extras"), STAGES, ids=_STAGE_IDS
)
def test_call_site_passes_every_model_field(handler_name, model, extras) -> None:
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
    ("handler_name", "model", "extras"), STAGES, ids=_STAGE_IDS
)
def test_call_site_passes_no_unaccounted_keys(handler_name, model, extras) -> None:
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


def test_backward_extras_is_exactly_sleep_ms() -> None:
    """Node 3 is the only stage with an extra, and it is `sleep_ms`.

    Pins the reason the gap exists at all. `sleep_ms` is a params key precisely
    because it is NOT a `BackwardParameters` field — the guard below is what
    makes that a checked fact rather than a comment. If pacing ever moves onto
    the model, this fails and the extras set must shrink with it.
    """
    (_name, model, extras) = STAGES[0]
    assert model is BackwardParameters
    assert extras == {"sleep_ms"}
    assert "sleep_ms" not in model.model_fields
    assert set(model.model_fields) == {"n_backward", "lambda_decay"}

    assert all(not extras for _n, _m, extras in STAGES[1:])
