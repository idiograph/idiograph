# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the ForwardTraverse stage is genuinely driven by the declarative Graph:
# the handler, invoked THROUGH core/executor.py::execute_graph on a minimal
# Graph, returns output equal to a direct handler call on the same inputs. The
# executor path is load-bearing in the assertion — the Node4Result is read off
# `results[<node_id>]`, which only exists if execute_graph actually dispatched
# the handler.
#
# The stage is BOUND: the node declares one input port, `seeds`, name-identical
# to BackwardTraverse's and AssembleGraph's, so one producer feeds all three
# with no renaming. The graphs here declare ports on both endpoints and carry
# from_port/to_port on every edge — which is also what makes them pass
# validate_integrity's dataflow check.
#
# Node 4 declares THREE output ports. `forward` carries the whole Node4Result —
# AssembleGraph's input port of the same name already declares that contract, so
# decomposing it would mean reopening a merged handler's input contract to
# arrive back at the same dataflow. `failed_seeds` and `truncated_seeds`
# duplicate lists also reachable inside the `forward` payload, deliberately:
# failure provenance rides its own declared port. Both are currently unconsumed,
# which is legal — validate_integrity never checks for unconsumed outputs.
#
# It shares Node 3's two resources by name: the http client and the OpenAlex
# credential arrive through the resource channel, supplied by the run rather
# than by the graph. A credential is a resource and never a param — it does not
# enter a content address.
#
# TWO NON-PORT SUBJECTS also live here, because both are properties of this
# stage's params contract and have nowhere better to sit:
#
#   - the `_compute_acceleration` TRIPWIRE (IDG-080 clause 2), which guards the
#     exemption keeping `acceleration_method` out of the hashed config model;
#   - the `current_year` threading (IDG-080 clause 3), which is the reason
#     neither traversal stage reads a clock any more, and the sole cause of this
#     change's content-address rebaselining.
#
# The fake client and record builders below are IMPORTED from the Node 4
# behavioral test file rather than mirrored. They are the fixtures this stage's
# behavior is already pinned against; a private copy here would be a second
# definition free to drift from the one the behavioral tests use, and the point
# of this file is that the executor-driven path meets the SAME stage.

import ast
import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from idiograph.core.executor import (
    HANDLERS,
    UnsuppliedResourceError,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    ForwardParameters,
    PipelineParameters,
)
from idiograph.domains.arxiv.pipeline import (
    FORWARD_ACCELERATION_METHOD,
    FORWARD_TRAVERSE_INPUT_PORTS,
    FORWARD_TRAVERSE_OUTPUT_PORTS,
    _compute_acceleration,
    forward_traverse,
)
from idiograph.domains.arxiv.registry import content_address

from .test_pipeline_node4 import (
    _CitesClient,
    _seed_record,
    _work,
)


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


def _port(name: str) -> PortDeclaration:
    """An untyped port. `port_type` is inert — nothing validates it."""
    return PortDeclaration(name=name, port_type="untyped")


def _params(n_forward: int = 10, current_year: int = 2026) -> dict:
    """Params as the direct call site shapes them.

    `acceleration_method` is passed explicitly rather than defaulted, because
    the params model has no default: the production value's one home is
    `FORWARD_ACCELERATION_METHOD`. `current_year` is stated for the same reason
    — the stage reads no clock, so the year must arrive.
    """
    return {
        "n_forward": n_forward,
        "lambda_decay": 0.05,
        "alpha": 1.0,
        "beta": 0.0,
        "sort": "cited_by_count:desc",
        "acceleration_method": FORWARD_ACCELERATION_METHOD,
        "current_year": current_year,
    }


def _fixture_citers() -> dict[str, list[dict]]:
    """Seed W_SEED is cited by two papers of different vintage, so the ranking
    below is real work rather than a one-element passthrough.

    The two are chosen so their citations-per-month ordering FLIPS with the
    reference year — recent-and-modest (12 cites since 2024) beats
    old-and-substantial (100 cites since 1990) when the year is near 2026, and
    loses to it once the year is far from both. That flip is what
    `test_current_year_changes_the_ranking` reads.
    """
    return {
        "W_SEED": [
            _work("W_C1", arxiv_id="c1.1", cited_by_count=12, year=2024),
            _work("W_C2", arxiv_id="c2.1", cited_by_count=100, year=1990),
        ]
    }


def _seeds() -> list:
    return [_seed_record("arxiv:seed.1", "W_SEED")]


def _resources(client) -> dict:
    return {"http_client": client, "openalex_api_key": "k"}


def _forward_node(node_id: str = "n4", params: dict | None = None) -> Node:
    """The ForwardTraverse node, declaring the handler's port and resource
    contract. The resource NAMES are the declaration — the objects come from the
    run."""
    return Node(
        id=node_id,
        type="ForwardTraverse",
        params=params if params is not None else _params(),
        input_ports=FORWARD_TRAVERSE_INPUT_PORTS,
        output_ports=FORWARD_TRAVERSE_OUTPUT_PORTS,
        resources=["http_client", "openalex_api_key"],
    )


def _seed_provider_graph(seeds: list, name: str, params: dict | None = None) -> Graph:
    """A minimal declared graph: one provider feeding the one input port."""

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds}

    register_handler("SeedsTestProvider", _provider)
    register_handler("ForwardTraverse", forward_traverse)

    return Graph(
        name=name,
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="SeedsTestProvider",
                params={},
                output_ports=[_port("seeds")],
            ),
            _forward_node(params=params),
        ],
        edges=[
            Edge(source="src", target="n4", type="DATA",
                 from_port="seeds", to_port="seeds"),
        ],
    )


# ── Port binding ────────────────────────────────────────────────────────────


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """ForwardTraverse driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    The resources mapping is supplied to the RUN, and the executor narrows it to
    the names the node declared before dispatch. Two client instances are used —
    one per call — because the fakes record their calls; they are seeded from
    the same citer db, so the traversal each sees is identical.
    """
    citers = _fixture_citers()
    seeds = _seeds()

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        forward_traverse(
            _params(),
            {"seeds": seeds},
            resources=_resources(_CitesClient(citers)),
        )
    )
    # Precondition: the fixture has real structure, so this is not a trivial
    # empty-equals-empty comparison.
    assert direct["forward"].papers
    assert direct["forward"].edges

    graph = _seed_provider_graph(seeds, "forward-minimal")

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph)["valid"]

    results = asyncio.run(
        execute_graph(graph, resources=_resources(_CitesClient(citers)))
    )

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the port-declared edge.
    assert results["n4"]["status"] == "SUCCESS"
    assert results["n4"]["forward"] == direct["forward"]
    assert results["n4"]["failed_seeds"] == direct["failed_seeds"]
    assert results["n4"]["truncated_seeds"] == direct["truncated_seeds"]


def test_forward_port_carries_whole_node4_result() -> None:
    """The `forward` port carries the entire ``Node4Result`` — the result is not
    decomposed into per-field ports.

    Pins the decomposition ruling: ``AssembleGraph`` already declares an input
    port named ``forward`` whose contract IS a whole ``Node4Result``, so a
    consumer reading this port hands it straight on with no reconstruction.
    """
    out = asyncio.run(
        forward_traverse(
            _params(),
            {"seeds": _seeds()},
            resources=_resources(_CitesClient(_fixture_citers())),
        )
    )

    assert [p.name for p in FORWARD_TRAVERSE_OUTPUT_PORTS] == [
        "forward",
        "failed_seeds",
        "truncated_seeds",
    ]
    assert set(out) == {"forward", "failed_seeds", "truncated_seeds"}
    result = out["forward"]
    assert {p.node_id for p in result.papers} == {"arxiv:c1.1", "arxiv:c2.1"}
    assert result.edges
    # Seeds are excluded from papers; edges reference them as targets.
    assert "arxiv:seed.1" not in {p.node_id for p in result.papers}


def test_input_port_name_matches_the_other_two_seed_consumers() -> None:
    """The one input port is `seeds`, spelled identically to
    ``BACKWARD_TRAVERSE_INPUT_PORTS``' and ``ASSEMBLE_GRAPH_INPUT_PORTS``'.

    The reason the name is forced: at the flip one producer feeds all three
    stages, and a rename here would mean an adapter edge whose only job is to
    spell a name differently.
    """
    assert [p.name for p in FORWARD_TRAVERSE_INPUT_PORTS] == ["seeds"]
    assert "seeds" in {p.name for p in pipeline.BACKWARD_TRAVERSE_INPUT_PORTS}
    assert "seeds" in {p.name for p in pipeline.ASSEMBLE_GRAPH_INPUT_PORTS}


def test_forward_output_port_is_the_assemble_graph_input_contract() -> None:
    """``AssembleGraph`` declares an input port named `forward` whose contract is
    a whole ``Node4Result`` — which is WHY this stage's primary port is named
    `forward` and carries the whole result.

    Asserted against the merged handler's own input model so the claim is read
    off the tree rather than restated in a comment.
    """
    assert "forward" in {p.name for p in pipeline.ASSEMBLE_GRAPH_INPUT_PORTS}
    annotation = pipeline._AssembleGraphInputs.model_fields["forward"].annotation
    assert annotation is pipeline.Node4Result

    out = asyncio.run(
        forward_traverse(
            _params(),
            {"seeds": _seeds()},
            resources=_resources(_CitesClient(_fixture_citers())),
        )
    )
    # The port payload is handed straight to the merge with no reconstruction.
    assert isinstance(out["forward"], pipeline.Node4Result)


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires ForwardTraverse to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["ForwardTraverse"] is forward_traverse


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given — including a `forward` key, which is a real port name on
    this very stage's OUTPUT side and specifically not one it consumes.
    """
    citers = _fixture_citers()
    seeds = _seeds()

    reference = asyncio.run(
        forward_traverse(
            _params(), {"seeds": seeds}, resources=_resources(_CitesClient(citers))
        )
    )

    out = asyncio.run(
        forward_traverse(
            _params(),
            {
                "bogus": "not-a-declared-port",
                "forward": "not-consumed-here",
                "backward": None,
                "seeds": seeds,
            },
            resources=_resources(_CitesClient(citers)),
        )
    )

    assert out["forward"] == reference["forward"]


# ── Failure provenance on its own ports ─────────────────────────────────────


def test_failed_seeds_port_carries_the_failure_provenance() -> None:
    """On a run where a seed's cites query fails, the `failed_seeds` output port
    carries the same records as ``forward.failed_seeds``.

    The duplication is the point: the port is the CANONICAL carrier of failure
    provenance, so a consumer never has to dig into the `forward` payload to
    find out what did not fetch. Driven through execute_graph so the port is
    proven to survive the executor, and asserted non-empty so the port is shown
    to be real rather than decorative.
    """
    seeds = _seeds()
    graph = _seed_provider_graph(seeds, "forward-failing-seed")
    client = _CitesClient(_fixture_citers(), fail_seeds={"W_SEED"})

    results = asyncio.run(execute_graph(graph, resources=_resources(client)))

    assert results["n4"]["status"] == "SUCCESS"
    port = results["n4"]["failed_seeds"]
    # Non-empty: the failure genuinely happened, so this is not a vacuous
    # equality between two empty lists.
    assert port
    assert [f.seed_id for f in port] == ["arxiv:seed.1"]
    assert port == results["n4"]["forward"].failed_seeds


def test_truncated_seeds_port_carries_the_truncation_provenance() -> None:
    """On a run where OpenAlex reports more citing papers than it returned, the
    `truncated_seeds` output port carries the same records as
    ``forward.truncated_seeds``.

    Truncation IS failure provenance: it records that the ranked set is a sample
    of an unknown larger one. Same canonical-port rule as `failed_seeds`, and
    the same non-vacuity guard.
    """
    seeds = _seeds()
    graph = _seed_provider_graph(seeds, "forward-truncated-seed")
    client = _CitesClient(
        _fixture_citers(), meta_count_by_seed={"W_SEED": 5000}
    )

    results = asyncio.run(execute_graph(graph, resources=_resources(client)))

    assert results["n4"]["status"] == "SUCCESS"
    port = results["n4"]["truncated_seeds"]
    assert port
    assert [t.seed_id for t in port] == ["arxiv:seed.1"]
    assert [t.total_count for t in port] == [5000]
    assert port == results["n4"]["forward"].truncated_seeds


# ── The resource fence ──────────────────────────────────────────────────────


def test_missing_resource_is_refused_pre_flight() -> None:
    """Omitting a declared resource from the run's mapping raises out of
    execute_graph BEFORE any handler runs — it is not a FAILED node result and
    it does not surface at the draw site.

    A node naming a resource the run did not supply is knowable without running
    anything, so it halts the whole execution. Here `openalex_api_key` is
    withheld while `http_client` is supplied, so the failure is specifically the
    credential and not the client.
    """
    graph = _seed_provider_graph(_seeds(), "forward-missing-resource")

    with pytest.raises(UnsuppliedResourceError) as exc:
        asyncio.run(
            execute_graph(
                graph,
                resources={"http_client": _CitesClient(_fixture_citers())},
            )
        )

    assert "'n4'" in str(exc.value)
    assert "openalex_api_key" in str(exc.value)


def test_api_key_reaches_the_openalex_query_params() -> None:
    """Routing the credential through the resource channel changes only where
    the handler READS it: it still lands in every OpenAlex request's params.

    The stage must issue byte-identical requests to the pre-conversion function,
    and the API key is the part of that the resource move could plausibly have
    broken. Asserted on the recorded calls of the fake client.
    """
    client = _CitesClient(_fixture_citers())

    asyncio.run(
        forward_traverse(
            _params(),
            {"seeds": _seeds()},
            resources={"http_client": client, "openalex_api_key": "secret-key"},
        )
    )

    assert client.calls  # the traversal actually issued requests
    assert all(c["api_key"] == "secret-key" for c in client.calls)


def test_credential_is_not_a_param() -> None:
    """No params key carries the credential, on either traversal stage.

    IDG-075 clause 3: a credential converts as a RESOURCE and never a param,
    because `content_address` would otherwise hash it. Asserted over the declared
    params models rather than over one call site, so a future key cannot smuggle
    it in.
    """
    for model in (
        pipeline._ForwardTraverseParams,
        pipeline._BackwardTraverseParams,
    ):
        fields = set(model.model_fields)
        assert not {f for f in fields if "key" in f or "credential" in f}, (
            f"{model.__name__} declares a credential-shaped param field; "
            f"credentials ride the resource channel and must never enter a "
            f"content address."
        )


# ── The `acceleration_method` tripwire (IDG-080 clause 2) ───────────────────


#: Probed methods: the production value, the one reserved-but-unimplemented
#: alternative, and arbitrary non-members. Enumerating every string is
#: impossible; what this set has to establish is that of the values anyone might
#: plausibly pass, exactly one is admitted.
_ACCELERATION_CANDIDATES = [
    "first_difference",
    "regression",
    "",
    "linear",
    "ols",
    "First_Difference",
    "second_difference",
]

#: Three time points — the minimum `_compute_acceleration` needs to return a
#: number rather than None. A method that raises raises regardless of the data,
#: so this only has to be enough for the NON-raising branch to complete.
_ACCELERATION_DATA = [
    {"year": 2022, "cited_by_count": 12},
    {"year": 2023, "cited_by_count": 24},
    {"year": 2024, "cited_by_count": 48},
]


def test_compute_acceleration_admits_exactly_one_method() -> None:
    """TRIPWIRE. `_compute_acceleration` admits exactly ONE non-raising method.

    THIS TEST IS THE GUARD ON AN ADDRESS EXEMPTION, not a behavioral check.
    `acceleration_method` is deliberately NOT a `ForwardParameters` field and so
    never reaches `content_address` (see `pipeline.FORWARD_ACCELERATION_METHOD`).
    The entire justification for that exemption is the assertion below: a hash
    over a value with exactly one admissible member records nothing, so hashing
    it would re-address every cached record in order to store a constant.

    WHEN THIS TEST FAILS, READ THIS. It fails when a second method becomes
    admissible — in practice, when `regression` is implemented. That failure is
    not a broken test to repair; it is the INSTRUCTION that the exemption has
    expired. `acceleration_method` then genuinely selects between corpora, must
    become a `ForwardParameters` field so `content_address` hashes it, and the
    address rebaselining that entails must be paid DELIBERATELY, at that moment,
    with the rest of this test file updated to match — rather than discovered
    later as cached records keyed to a scoring method that was never applied.
    """
    admitted = []
    for method in _ACCELERATION_CANDIDATES:
        try:
            _compute_acceleration(_ACCELERATION_DATA, method)
        except (NotImplementedError, ValueError):
            continue
        admitted.append(method)

    assert admitted == [FORWARD_ACCELERATION_METHOD], (
        f"_compute_acceleration now admits {admitted}, not exactly "
        f"[{FORWARD_ACCELERATION_METHOD!r}]. THE ADDRESS EXEMPTION FOR "
        f"`acceleration_method` HAS EXPIRED. It is kept out of "
        f"ForwardParameters — and so out of content_address — solely because a "
        f"hash over a one-member domain records nothing. With a second method "
        f"admissible it now selects between corpora, so it must move onto "
        f"ForwardParameters (removing it from this stage's `extras` in "
        f"test_params_marshalling.py and from FORWARD_ACCELERATION_METHOD as a "
        f"sole home), and the content-address rebaselining must be paid "
        f"deliberately now rather than discovered later."
    )


def test_regression_is_the_reserved_unimplemented_method() -> None:
    """`regression` raises NotImplementedError; anything else raises ValueError.

    Pins WHICH of the two refusals each value gets, so the tripwire above cannot
    be quietly satisfied by turning the `regression` branch into a generic
    rejection — that would erase the marker for the one method actually queued
    to arrive.
    """
    with pytest.raises(NotImplementedError):
        _compute_acceleration(_ACCELERATION_DATA, "regression")

    with pytest.raises(ValueError):
        _compute_acceleration(_ACCELERATION_DATA, "no_such_method")


def test_acceleration_method_is_not_in_the_hashed_model() -> None:
    """`acceleration_method` is a params key and NOT a `ForwardParameters` field.

    The static half of the tripwire: the exemption the test above guards is only
    real while the field is genuinely absent from the model `content_address`
    hashes.
    """
    assert "acceleration_method" not in ForwardParameters.model_fields
    assert "acceleration_method" in pipeline._ForwardTraverseParams.model_fields
    assert FORWARD_ACCELERATION_METHOD == "first_difference"


def test_acceleration_method_has_no_default_on_the_params_model() -> None:
    """The params model requires `acceleration_method` — the production value's
    sole home is `FORWARD_ACCELERATION_METHOD`.

    A model default would be a second home, silently applying whenever a wiring
    forgot to state the method. The pre-conversion function had exactly that
    default; removing it is part of the conversion.
    """
    params = _params()
    del params["acceleration_method"]
    with pytest.raises(ValidationError):
        pipeline._ForwardTraverseParams.model_validate(params)


# ── `current_year` threading (IDG-080 clauses 3–5) ──────────────────────────


def _traversal_handler_asts() -> dict[str, ast.AsyncFunctionDef]:
    """The two traversal handlers' parsed bodies, read off pipeline.py's source.

    Static, because the property under test is "no reachable clock read", which
    a runtime probe could only ever sample: a `date.today()` on a branch the
    fixtures do not take would go unobserved.
    """
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"backward_traverse", "forward_traverse"}
    }
    assert set(found) == {"backward_traverse", "forward_traverse"}, (
        f"expected both traversal handlers in pipeline.py, found "
        f"{sorted(found)} — this test is pointed at the wrong module and would "
        f"otherwise pass vacuously."
    )
    return found


def test_neither_traversal_handler_reads_a_clock() -> None:
    """No `date.today()` / `datetime.now()` anywhere in either traversal stage.

    IDG-080 clause 3, and the shipped defect (finding 4480c117) this change
    closes. Node 3's `current_year = date.today().year` fed `_node3_score`, which
    ordered the sort that `n_backward` truncated, while `BackwardParameters` did
    not carry the year and `content_address` did not hash it: identical seeds and
    identical parameters on either side of a New Year boundary selected different
    corpora under the SAME address — under-inclusion, the false-HIT direction.
    Node 4 had the second read in its `current_year is None` default path.

    Both are gone; the year arrives on `params` from
    `PipelineParameters.current_year`, which IS hashed. Clause 5 is why this
    asserts over BOTH stages rather than only the one being converted: fixing
    forward alone would leave a split convention and a known false-HIT path live.
    """
    clock_calls = {"today", "now", "utcnow", "fromtimestamp", "time"}
    for name, fn in _traversal_handler_asts().items():
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in clock_calls, (
                    f"{name} calls .{node.func.attr}() — a wall-clock read. The "
                    f"traversal stages score against "
                    f"PipelineParameters.current_year, which content_address "
                    f"hashes; a clock read here re-creates the false-HIT path "
                    f"across a New Year boundary that IDG-080 closed."
                )


def test_pipeline_module_imports_no_clock() -> None:
    """`pipeline.py` no longer imports `date`/`datetime` at all.

    Stronger and cheaper than the AST scan it complements: with no clock name
    bound in the module namespace, no function in it can read one, including
    ones added later.
    """
    assert not hasattr(pipeline, "date")
    assert not hasattr(pipeline, "datetime")


def test_current_year_is_required_on_pipeline_parameters() -> None:
    """`PipelineParameters.current_year` has no default and no default_factory.

    The design ruling this brief settled. A `default_factory=lambda:
    date.today().year` would relocate the wall-clock read one layer up rather
    than out of the library, and would let two runs on either side of a New Year
    boundary take different content addresses without the caller having stated
    anything different. The read belongs to whoever constructs the model.
    """
    field = PipelineParameters.model_fields["current_year"]
    assert field.is_required()
    assert field.default_factory is None

    with pytest.raises(ValidationError):
        PipelineParameters(
            backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
            forward=ForwardParameters(
                n_forward=10, lambda_decay=0.1, alpha=1.0, beta=0.0,
                sort="cited_by_count:desc",
            ),
        )


def test_current_year_is_top_level_not_per_stage() -> None:
    """`current_year` is a `PipelineParameters` field and is on NEITHER
    per-stage traversal model.

    IDG-080 clause 4: two fields that must agree, with nothing enforcing
    agreement, is the defect rather than the fix. One run-level field is
    marshalled to both stages.
    """
    assert "current_year" in PipelineParameters.model_fields
    assert "current_year" not in BackwardParameters.model_fields
    assert "current_year" not in ForwardParameters.model_fields
    # ...and it reaches both handlers, through their params models.
    assert "current_year" in pipeline._BackwardTraverseParams.model_fields
    assert "current_year" in pipeline._ForwardTraverseParams.model_fields


def test_current_year_has_no_default_on_the_params_model() -> None:
    """The handler refuses params that do not state a year.

    The stage reads no clock, so an unstated year has no fallback to take — and
    must not acquire one. A default here would be a second home for a value
    whose whole point is that it comes from the hashed model.
    """
    params = _params()
    del params["current_year"]
    with pytest.raises(ValidationError):
        pipeline._ForwardTraverseParams.model_validate(params)


def test_current_year_changes_the_ranking() -> None:
    """The year is LOAD-BEARING on output: two runs differing only in
    `current_year` rank the same citing papers in a different order.

    Without this the threading tests above would all pass over a parameter the
    stage accepted and ignored. The fixture is built so the flip is a real one:
    a 2024 paper with few citations out-ranks a 1990 paper with many on
    citations-per-month when the reference year is close to 2024, and loses to
    it once the reference year is far from both.
    """
    citers = _fixture_citers()

    near = asyncio.run(
        forward_traverse(
            _params(current_year=2026),
            {"seeds": _seeds()},
            resources=_resources(_CitesClient(citers)),
        )
    )
    far = asyncio.run(
        forward_traverse(
            _params(current_year=2200),
            {"seeds": _seeds()},
            resources=_resources(_CitesClient(citers)),
        )
    )

    near_order = [p.node_id for p in near["forward"].papers]
    far_order = [p.node_id for p in far["forward"].papers]

    assert sorted(near_order) == sorted(far_order)  # same corpus...
    assert near_order != far_order  # ...different ranking
    assert near_order[0] == "arxiv:c1.1"
    assert far_order[0] == "arxiv:c2.1"


def test_current_year_changes_the_content_address() -> None:
    """Two `PipelineParameters` differing ONLY in `current_year` address
    differently.

    The rebaselining, asserted rather than assumed. `content_address` hashes
    `PipelineParameters` whole, so promoting the year to a top-level field puts
    it in the address — which is the entire point: identical seeds and identical
    parameters can no longer denote different corpora. Existing cached records
    becoming unreachable at their old addresses is the intended, ruled cost.
    """

    def _at(year: int) -> PipelineParameters:
        return PipelineParameters(
            backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
            forward=ForwardParameters(
                n_forward=10, lambda_decay=0.1, alpha=1.0, beta=0.0,
                sort="cited_by_count:desc",
            ),
            current_year=year,
        )

    seeds = ["arxiv:seed.1"]
    assert content_address(seeds, _at(2025)) != content_address(seeds, _at(2026))
    # Deterministic in the year, not merely sensitive to it.
    assert content_address(seeds, _at(2026)) == content_address(seeds, _at(2026))
