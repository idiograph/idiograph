# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the BackwardTraverse stage is genuinely driven by the declarative Graph:
# the handler, invoked THROUGH core/executor.py::execute_graph on a minimal
# Graph, returns output equal to a direct handler call on the same inputs. The
# executor path is load-bearing in the assertion — the Node3Result is read off
# `results[<node_id>]`, which only exists if execute_graph actually dispatched
# the handler.
#
# The stage is BOUND: the node declares one input port, so the executor builds
# its `inputs` solely from the port-declared edges into it, keyed by `to_port`.
# The graphs here declare ports on both endpoints and carry from_port/to_port on
# every edge — which is also what makes them pass validate_integrity's dataflow
# check.
#
# It is also the first TRAVERSAL stage to DECLARE RESOURCES: the http client and
# the OpenAlex credential arrive through the resource channel, supplied by the
# run rather than by the graph. A credential is a resource and never a param —
# it does not enter a content address. Hence the two tests below that pin the
# resource fence: supply reaches the handler, and absent supply is refused
# PRE-FLIGHT by execute_graph rather than at the draw site.
#
# Node 3 declares TWO output ports. `backward` carries the whole Node3Result —
# AssembleGraph's input port of the same name already declares that contract.
# `failed_batches` duplicates a list also reachable inside the `backward`
# payload, deliberately: failure provenance rides its own declared port. It is
# currently unconsumed, which is legal — validate_integrity never checks for
# unconsumed outputs.
#
# The fake clients and record builders below are IMPORTED from the Node 3
# behavioral test file rather than mirrored. They are the fixtures this stage's
# behavior is already pinned against; a private copy here would be a second
# definition free to drift from the one the 25 behavioral tests use, and the
# point of this file is that the executor-driven path meets the SAME stage.

import asyncio

import pytest

from idiograph.core.executor import (
    HANDLERS,
    UnsuppliedResourceError,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv.pipeline import (
    BACKWARD_TRAVERSE_INPUT_PORTS,
    BACKWARD_TRAVERSE_OUTPUT_PORTS,
    backward_traverse,
)

from .test_pipeline_node3 import (
    _BatchClient,
    _seed_record,
    _StageFailingClient,
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


def _params(n_backward: int = 10) -> dict:
    """Params as the direct call site shapes them. `sleep_ms=0` because tests do
    not pace: the production value has exactly one home, and it is not here."""
    return {"n_backward": n_backward, "lambda_decay": 0.05, "sleep_ms": 0}


def _fixture_works() -> dict[str, dict]:
    """Seed S cites D1a and D1b; D1a cites D2. Real depth-2 structure, so the
    comparisons below are not empty-equals-empty."""
    return {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1a", "D1b"]),
        "D1a": _work(
            "D1a",
            arxiv_id="d1a.1",
            cited_by_count=5,
            year=2015,
            referenced_works=["D2"],
        ),
        "D1b": _work("D1b", arxiv_id="d1b.1", cited_by_count=3, year=2015),
        "D2": _work("D2", arxiv_id="d2.1", cited_by_count=9, year=2010),
    }


def _seeds() -> list:
    return [_seed_record("arxiv:seed.1", "S")]


def _backward_node(node_id: str = "n3", params: dict | None = None) -> Node:
    """The BackwardTraverse node, declaring the handler's port and resource
    contract. The resource NAMES are the declaration — the objects come from the
    run."""
    return Node(
        id=node_id,
        type="BackwardTraverse",
        params=params if params is not None else _params(),
        input_ports=BACKWARD_TRAVERSE_INPUT_PORTS,
        output_ports=BACKWARD_TRAVERSE_OUTPUT_PORTS,
        resources=["http_client", "openalex_api_key"],
    )


def _seed_provider_graph(seeds: list, name: str) -> Graph:
    """A minimal declared graph: one provider feeding the one input port."""

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds}

    register_handler("SeedsTestProvider", _provider)
    register_handler("BackwardTraverse", backward_traverse)

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
            _backward_node(),
        ],
        edges=[
            Edge(source="src", target="n3", type="DATA",
                 from_port="seeds", to_port="seeds"),
        ],
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """BackwardTraverse driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    The resources mapping is supplied to the RUN, and the executor narrows it to
    the names the node declared before dispatch. Two client instances are used —
    one per call — because the fakes record their calls; they are seeded from
    the same work db, so the traversal each sees is identical.
    """
    works = _fixture_works()
    seeds = _seeds()

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        backward_traverse(
            _params(),
            {"seeds": seeds},
            resources={
                "http_client": _BatchClient(works),
                "openalex_api_key": "k",
            },
        )
    )
    # Precondition: the fixture has real structure, so this is not a trivial
    # empty-equals-empty comparison.
    assert direct["backward"].papers
    assert direct["backward"].edges

    graph = _seed_provider_graph(seeds, "backward-minimal")

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph)["valid"]

    results = asyncio.run(
        execute_graph(
            graph,
            resources={
                "http_client": _BatchClient(works),
                "openalex_api_key": "k",
            },
        )
    )

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the port-declared edge.
    assert results["n3"]["status"] == "SUCCESS"
    assert results["n3"]["backward"] == direct["backward"]
    assert results["n3"]["failed_batches"] == direct["failed_batches"]


def test_backward_port_carries_whole_node3_result() -> None:
    """The `backward` port carries the entire ``Node3Result`` — the result is
    not decomposed into per-field ports.

    Pins the decomposition ruling: ``AssembleGraph`` already declares an input
    port named ``backward`` whose contract IS a whole ``Node3Result``, so a
    consumer reading this port hands it straight on with no reconstruction.
    """
    out = asyncio.run(
        backward_traverse(
            _params(),
            {"seeds": _seeds()},
            resources={
                "http_client": _BatchClient(_fixture_works()),
                "openalex_api_key": "k",
            },
        )
    )

    assert [p.name for p in BACKWARD_TRAVERSE_OUTPUT_PORTS] == [
        "backward",
        "failed_batches",
    ]
    assert set(out) == {"backward", "failed_batches"}
    result = out["backward"]
    assert {p.node_id for p in result.papers} == {"arxiv:d1a.1", "arxiv:d1b.1",
                                                 "arxiv:d2.1"}
    assert result.edges
    # Seeds are excluded from papers; edges reference them as sources.
    assert "arxiv:seed.1" not in {p.node_id for p in result.papers}


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires BackwardTraverse to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["BackwardTraverse"] is backward_traverse


def test_failed_batches_port_carries_the_failure_provenance() -> None:
    """On a run where a batch fails, the `failed_batches` output port carries
    the same records as ``backward.failed_batches``.

    The duplication is the point: the port is the CANONICAL carrier of failure
    provenance, so a consumer never has to dig into the `backward` payload to
    find out what did not fetch. Driven through execute_graph so the port is
    proven to survive the executor, and asserted non-empty so the port is shown
    to be real rather than decorative.
    """
    works = _fixture_works()
    seeds = _seeds()
    graph = _seed_provider_graph(seeds, "backward-failing-batch")

    results = asyncio.run(
        execute_graph(
            graph,
            resources={
                # Fails the depth_1 fetch (call index 1).
                "http_client": _StageFailingClient(works, fail_at_call=1),
                "openalex_api_key": "k",
            },
        )
    )

    assert results["n3"]["status"] == "SUCCESS"
    port = results["n3"]["failed_batches"]
    # Non-empty: the failure genuinely happened, so this is not a vacuous
    # equality between two empty lists.
    assert port
    assert [b.stage for b in port] == ["depth_1"]
    assert port == results["n3"]["backward"].failed_batches


def test_missing_resource_is_refused_pre_flight() -> None:
    """Omitting a declared resource from the run's mapping raises out of
    execute_graph BEFORE any handler runs — it is not a FAILED node result and
    it does not surface at the draw site.

    A node naming a resource the run did not supply is knowable without running
    anything, so it halts the whole execution. Here `openalex_api_key` is
    withheld while `http_client` is supplied, so the failure is specifically the
    credential and not the client.
    """
    graph = _seed_provider_graph(_seeds(), "backward-missing-resource")

    with pytest.raises(UnsuppliedResourceError) as exc:
        asyncio.run(
            execute_graph(
                graph,
                resources={"http_client": _BatchClient(_fixture_works())},
            )
        )

    assert "'n3'" in str(exc.value)
    assert "openalex_api_key" in str(exc.value)


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given — including a `backward` key, which is a real port name
    on this very stage's OUTPUT side and specifically not one it consumes.
    """
    works = _fixture_works()
    seeds = _seeds()

    reference = asyncio.run(
        backward_traverse(
            _params(),
            {"seeds": seeds},
            resources={
                "http_client": _BatchClient(works),
                "openalex_api_key": "k",
            },
        )
    )

    out = asyncio.run(
        backward_traverse(
            _params(),
            {
                "bogus": "not-a-declared-port",
                "backward": "not-consumed-here",
                "nodes": [],
                "seeds": seeds,
            },
            resources={
                "http_client": _BatchClient(works),
                "openalex_api_key": "k",
            },
        )
    )

    assert out["backward"] == reference["backward"]


def test_api_key_reaches_the_openalex_query_params() -> None:
    """Routing the credential through the resource channel changes only where
    the handler READS it: it still lands in every OpenAlex request's params.

    The stage must issue byte-identical requests to the pre-conversion function,
    and the API key is the part of that the resource move could plausibly have
    broken. Asserted on the recorded calls of the fake client.
    """
    client = _BatchClient(_fixture_works())

    asyncio.run(
        backward_traverse(
            _params(),
            {"seeds": _seeds()},
            resources={"http_client": client, "openalex_api_key": "secret-key"},
        )
    )

    assert client.calls  # the traversal actually issued requests
    assert all(c["api_key"] == "secret-key" for c in client.calls)
