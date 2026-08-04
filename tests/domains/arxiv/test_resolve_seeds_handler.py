# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the ResolveSeeds stage (Node 0) is genuinely driven by the declarative
# Graph: the handler, invoked THROUGH core/executor.py::execute_graph on a
# minimal Graph, returns output equal to a direct handler call on the same
# params. The executor path is load-bearing in the assertion — the resolved
# records are read off `results[<node_id>]`, which only exists if execute_graph
# actually dispatched the handler.
#
# This is the FIRST node in the tree to declare EMPTY input ports. `[]` is a
# declaration, not an omission: core/executor.py::_is_bound tests
# `input_ports is not None`, so the node is on the bound side of the fence and
# accepts no inputs. `None` would have left it in the legacy regime. Node 0 is
# the head of the pipeline — its seed set arrives as CONFIGURATION on `params`,
# never as dataflow — so having nothing to bind is the shape of the stage. Hence
# the graphs below: one where ResolveSeeds is the whole graph, and one where it
# feeds a downstream bound consumer, proving a head node with no input ports is
# still a real producer.
#
# The stage DECLARES RESOURCES, name-identical to what BackwardTraverse and
# ForwardTraverse declare: the http client and the OpenAlex credential arrive
# through the resource channel, supplied by the run rather than by the graph. A
# credential is a resource and never a param — it does not enter a content
# address. Hence the test that pins the resource fence: absent supply is refused
# PRE-FLIGHT by execute_graph rather than at the request site.
#
# Node 0 declares TWO output ports. `seeds` carries the resolved PaperRecord
# list; `seed_failures` carries the per-seed resolution failures on its own
# declared port rather than embedded in the `seeds` payload — the same ruling
# BACKWARD_TRAVERSE_OUTPUT_PORTS records for `failed_batches`. It is currently
# unconsumed by any graph, which is legal — validate_integrity never checks for
# unconsumed outputs.
#
# The fake client and work builders below are IMPORTED from the Node 0
# behavioral test file rather than mirrored. They are the fixtures this stage's
# resolution behavior is already pinned against; a private copy here would be a
# second definition free to drift from the one the behavioral tests use, and the
# point of this file is that the executor-driven path meets the SAME stage.

import asyncio
from unittest.mock import AsyncMock

import pytest

from idiograph.core.executor import (
    HANDLERS,
    UnsuppliedResourceError,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.pipeline import (
    RESOLVE_SEEDS_INPUT_PORTS,
    RESOLVE_SEEDS_OUTPUT_PORTS,
    PipelineError,
    resolve_seeds,
)

from .test_pipeline_node0 import _make_client, _ok_response, _work


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


def _port(name: str) -> PortDeclaration:
    """An untyped port. `port_type` is inert — nothing validates it."""
    return PortDeclaration(name=name, port_type="untyped")


ARXIV_SEED = {"arxiv_id": "2301.07041"}
#: A seed whose shape `_seed_filter` does not recognize. It fails resolution
#: WITHOUT issuing an HTTP call, so the failure provenance below is produced by
#: the stage itself rather than by a fake client's error scripting.
UNRESOLVABLE_SEED = {"isbn": "978-0"}


def _params(seeds: list[dict] | None = None) -> dict:
    """Params as the direct call site shapes them: the requested seed identifier
    dicts, and nothing else. The credential is NOT here — it is a resource."""
    return {"seeds": seeds if seeds is not None else [ARXIV_SEED]}


def _client(n_resolving: int = 1) -> AsyncMock:
    """A fake OpenAlex client that resolves `n_resolving` seeds in a row.

    One response per seed that reaches the network; `UNRESOLVABLE_SEED` reaches
    it not at all. Each call needs its own instance — the fake scripts its
    responses as a consumed side-effect list.
    """
    return _make_client(
        [_ok_response({"results": [_work(arxiv_id="2301.07041")]})] * n_resolving
    )


def _resources(client: AsyncMock | None = None) -> dict:
    return {
        "http_client": client if client is not None else _client(),
        "openalex_api_key": "k",
    }


def _resolve_node(node_id: str = "n0", params: dict | None = None) -> Node:
    """The ResolveSeeds node, declaring the handler's port and resource contract.

    `input_ports` is the EMPTY list the stage declares — passed through from
    RESOLVE_SEEDS_INPUT_PORTS rather than written as `[]` here, so this node
    asserts against the shipped declaration.
    """
    return Node(
        id=node_id,
        type="ResolveSeeds",
        params=params if params is not None else _params(),
        input_ports=RESOLVE_SEEDS_INPUT_PORTS,
        output_ports=RESOLVE_SEEDS_OUTPUT_PORTS,
        resources=["http_client", "openalex_api_key"],
    )


def _lone_graph(name: str, params: dict | None = None) -> Graph:
    """The head-node graph: ResolveSeeds alone, no edges to bind."""
    register_handler("ResolveSeeds", resolve_seeds)
    return Graph(
        name=name,
        version="1.0",
        nodes=[_resolve_node(params=params)],
        edges=[],
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """ResolveSeeds driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same params.

    THE SHARED-CONTRACT PROPERTY. The direct call shapes `params`, `inputs` and
    `resources` exactly as the executor does — including `inputs={}`, which is
    what a bound node with no declared input ports receives — so there is one
    contract and not two. Two client instances are used, one per call, because
    the fake consumes its scripted responses; both resolve the same work.
    """
    direct = asyncio.run(resolve_seeds(_params(), {}, resources=_resources()))
    # Precondition: a seed genuinely resolved, so this is not a trivial
    # empty-equals-empty comparison.
    assert direct["seeds"]

    graph = _lone_graph("resolve-seeds-minimal")

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph, resources=_resources()))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler.
    assert results["n0"]["status"] == "SUCCESS"
    assert results["n0"]["seeds"] == direct["seeds"]
    assert results["n0"]["seed_failures"] == direct["seed_failures"]


def test_both_declared_output_ports_are_returned() -> None:
    """The returned mapping is keyed by BOTH declared output ports, in the order
    the pre-binding 2-tuple carried them — and nothing else."""
    out = asyncio.run(resolve_seeds(_params(), {}, resources=_resources()))

    assert [p.name for p in RESOLVE_SEEDS_OUTPUT_PORTS] == [
        "seeds",
        "seed_failures",
    ]
    assert set(out) == {"seeds", "seed_failures"}
    assert [r.node_id for r in out["seeds"]] == ["arxiv:2301.07041"]
    assert out["seeds"][0].hop_depth == 0
    assert out["seed_failures"] == []


def test_seed_failures_port_carries_the_failure_provenance() -> None:
    """On a run where one seed fails to resolve, the `seed_failures` output port
    carries the typed per-seed failure while `seeds` carries the survivor.

    The separation is the point: a consumer reads failure provenance off its own
    declared port, never by digging into the `seeds` payload. Driven through
    execute_graph so the port is proven to survive the executor, and asserted
    non-empty so it is shown to be real rather than decorative.
    """
    graph = _lone_graph(
        "resolve-seeds-partial-failure",
        params=_params([ARXIV_SEED, UNRESOLVABLE_SEED]),
    )

    results = asyncio.run(execute_graph(graph, resources=_resources()))

    assert results["n0"]["status"] == "SUCCESS"
    assert [r.node_id for r in results["n0"]["seeds"]] == ["arxiv:2301.07041"]
    failures = results["n0"]["seed_failures"]
    # Non-empty: a seed genuinely failed, so this is not a vacuous assertion.
    assert len(failures) == 1
    assert failures[0].seed == UNRESOLVABLE_SEED
    assert failures[0].reason == "unrecognized seed shape"
    # The failure is NOT reachable through the `seeds` port's payload.
    assert not any(r.node_id == "isbn:978-0" for r in results["n0"]["seeds"])


def test_seeds_port_feeds_a_bound_downstream_consumer() -> None:
    """A head node with EMPTY input ports is still a real producer: a downstream
    bound node reads its `seeds` output port over a port-declared DATA edge.

    `seeds` is name-identical to the input port BackwardTraverse, ForwardTraverse
    and AssembleGraph already declare, so this is the shape a later wiring uses
    verbatim. The consumer here is a test stub — production graph wiring is not
    this change's job.
    """

    async def _sink(_params: dict, inputs: dict) -> dict:
        return {"seen": [r.node_id for r in inputs["seeds"]]}

    register_handler("ResolveSeeds", resolve_seeds)
    register_handler("SeedsTestSink", _sink)

    graph = Graph(
        name="resolve-seeds-downstream",
        version="1.0",
        nodes=[
            _resolve_node(),
            Node(
                id="sink",
                type="SeedsTestSink",
                params={},
                input_ports=[_port("seeds")],
                output_ports=[_port("seen")],
            ),
        ],
        edges=[
            Edge(source="n0", target="sink", type="DATA",
                 from_port="seeds", to_port="seeds"),
        ],
    )

    assert validate_integrity(graph)["valid"]

    results = asyncio.run(execute_graph(graph, resources=_resources()))

    assert results["n0"]["status"] == "SUCCESS"
    assert results["sink"]["status"] == "SUCCESS"
    assert results["sink"]["seen"] == ["arxiv:2301.07041"]


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires ResolveSeeds to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["ResolveSeeds"] is resolve_seeds


def test_missing_resource_is_refused_pre_flight() -> None:
    """Omitting a declared resource from the run's mapping raises out of
    execute_graph BEFORE any handler runs — it is not a FAILED node result and it
    does not surface at the request site.

    A node naming a resource the run did not supply is knowable without running
    anything, so it halts the whole execution. Here `openalex_api_key` is
    withheld while `http_client` is supplied, so the failure is specifically the
    credential and not the client.
    """
    graph = _lone_graph("resolve-seeds-missing-resource")

    with pytest.raises(UnsuppliedResourceError) as exc:
        asyncio.run(execute_graph(graph, resources={"http_client": _client()}))

    assert "'n0'" in str(exc.value)
    assert "openalex_api_key" in str(exc.value)


def test_undeclared_input_keys_are_ignored() -> None:
    """The node declares NO input ports, so nothing in `inputs` is read.

    Under the bound contract the executor hands this handler an empty mapping,
    but the handler reads its seeds off `params` regardless of what mapping it is
    given — including a `seeds` key, which is a real port name on this very
    stage's OUTPUT side and specifically not one it consumes.
    """
    reference = asyncio.run(resolve_seeds(_params(), {}, resources=_resources()))

    out = asyncio.run(
        resolve_seeds(
            _params(),
            {"seeds": ["not-consumed-here"], "bogus": "not-a-declared-port"},
            resources=_resources(),
        )
    )

    assert out["seeds"] == reference["seeds"]


def test_empty_seeds_param_raises_value_error() -> None:
    """The empty-input halt still fires, now off the validated param.

    A pre-check, not a reliance on `fetch_seeds`' own empty-input guard: it
    raises before any work and before any client call.
    """
    client = _client(n_resolving=0)

    with pytest.raises(ValueError, match="seeds must be non-empty"):
        asyncio.run(resolve_seeds(_params([]), {}, resources=_resources(client)))

    assert client.get.await_count == 0


def test_missing_seeds_param_is_refused_by_the_param_model() -> None:
    """`seeds` is REQUIRED with no default — a params mapping that omits it does
    not silently resolve nothing."""
    with pytest.raises(Exception, match="seeds"):
        asyncio.run(resolve_seeds({}, {}, resources=_resources()))


def test_contract_violation_raises_pipeline_error(monkeypatch) -> None:
    """The Node 0 contract violation halt still fires: `fetch_seeds` returning an
    empty resolved list WITHOUT raising is a should-not-happen state, and it
    raises `PipelineError` rather than emitting an empty `seeds` port.

    Stubbed, because the real `fetch_seeds` raises `ValueError` when every seed
    fails — this branch is unreachable without forcing it.
    """
    monkeypatch.setattr(
        pipeline, "fetch_seeds", AsyncMock(return_value=([], []))
    )

    with pytest.raises(PipelineError, match="no seeds resolved"):
        asyncio.run(resolve_seeds(_params(), {}, resources=_resources()))


def test_contract_violation_surfaces_as_a_failed_node_result() -> None:
    """Driven through execute_graph the same halt is a FAILED node result rather
    than an exception out of the run — the executor's handler-failure contract,
    unchanged by this conversion. No `seeds` port is emitted."""
    graph = _lone_graph(
        "resolve-seeds-total-failure",
        params=_params([UNRESOLVABLE_SEED]),
    )

    results = asyncio.run(
        execute_graph(graph, resources=_resources(_client(n_resolving=0)))
    )

    assert results["n0"]["status"] == "FAILED"
    assert "seeds" not in results["n0"]
