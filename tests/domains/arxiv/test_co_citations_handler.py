# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Proves the ComputeCoCitations stage is genuinely driven by the declarative
# Graph: the handler, invoked THROUGH core/executor.py::execute_graph on a
# minimal Graph, returns output equal to a direct handler call on the same
# inputs. The executor path is load-bearing in the assertion — the co-citation
# edges and warnings are read off `results[<node_id>]`, which only exists if
# execute_graph actually dispatched the handler.
#
# The stage is BOUND: the co node declares input ports, so the executor builds
# its `inputs` solely from the port-declared edges into it, keyed by `to_port`.
# The graphs here declare ports on both endpoints and carry from_port/to_port on
# every edge — which is also what makes them pass validate_integrity's dataflow
# check.
#
# This is the first conversion to consume a port ANOTHER conversion created:
# `clean.all_cites -> co.all_cites`. The wiring test below is built to prove that
# specific port is the one carried, not merely to survive it — its fixture makes
# a suppressed edge participate in the co-citation result, so wiring `co` to
# `cleaned_edges` instead would produce a different (empty) output and fail.

import asyncio

import pytest
from pydantic import ValidationError

from idiograph.core.executor import (
    HANDLERS,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv.models import (
    CitationEdge,
    Node3Result,
    Node4Result,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    ASSEMBLE_GRAPH_INPUT_PORTS,
    ASSEMBLE_GRAPH_OUTPUT_PORTS,
    CLEAN_CYCLES_INPUT_PORTS,
    CLEAN_CYCLES_OUTPUT_PORTS,
    CO_CITATIONS_INPUT_PORTS,
    CO_CITATIONS_OUTPUT_PORTS,
    assemble_graph,
    clean_cycles,
    compute_co_citations,
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


def _rec(
    node_id: str,
    citation_count: int = 0,
    root_ids: list[str] | None = None,
    hop_depth: int = 1,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=root_ids if root_ids is not None else [node_id],
        citation_count=citation_count,
    )


def _edge(source: str, target: str) -> CitationEdge:
    return CitationEdge(source_id=source, target_id=target, type="cites")


def _triples(edges: list[CitationEdge]) -> list[tuple[str, str, int]]:
    return [(e.source_id, e.target_id, e.strength) for e in edges]


def _co_node(node_id: str = "co", params: dict | None = None) -> Node:
    """The ComputeCoCitations node, declaring the handler's port contract."""
    return Node(
        id=node_id,
        type="ComputeCoCitations",
        params=params or {},
        input_ports=CO_CITATIONS_INPUT_PORTS,
        output_ports=CO_CITATIONS_OUTPUT_PORTS,
    )


def test_handler_via_execute_graph_equals_direct_call() -> None:
    """ComputeCoCitations driven through execute_graph on a minimal Graph returns
    output equal to the direct handler call on the same inputs.

    One upstream provider declares both output ports and feeds them over TWO
    port-declared edges into the co node's two distinct input ports — the
    same-source-two-ports shape, which is only expressible because bound inputs
    are keyed by `to_port` rather than by source node id.
    """
    nodes = [_rec(x) for x in ("A", "B", "C", "D")]
    # C and D both cite A and B — one strength-2 pair. "Z" is unknown, so the
    # warnings port carries content too and equality below covers both ports.
    edges = [
        _edge("C", "A"),
        _edge("C", "B"),
        _edge("D", "A"),
        _edge("D", "B"),
        _edge("Z", "A"),
    ]
    params = {"min_strength": 1, "max_edges": None}

    # Direct handler call — the reference output. `inputs` is shaped as the
    # executor's bound mapping: one key per declared input port.
    direct = asyncio.run(
        compute_co_citations(params, {"nodes": nodes, "all_cites": edges})
    )
    # The fixture is discriminating: both declared output ports carry real
    # content, so equality below cannot be satisfied by empty results.
    assert direct["co_citation_edges"]
    assert direct["co_citation_warnings"] == ["Z"]

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"nodes": nodes, "all_cites": edges}

    register_handler("CoTestProvider", _provider)
    register_handler("ComputeCoCitations", compute_co_citations)

    graph = Graph(
        name="co-citations-minimal",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CoTestProvider",
                params={},
                output_ports=[_port("nodes"), _port("all_cites")],
            ),
            _co_node(params=params),
        ],
        edges=[
            Edge(source="src", target="co", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="src", target="co", type="DATA",
                 from_port="all_cites", to_port="all_cites"),
        ],
    )

    # The declarations alone make this graph checkable — no handler source read.
    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    # Load-bearing: this key only exists if execute_graph actually dispatched
    # the handler over the two port-declared edges.
    assert results["co"]["status"] == "SUCCESS"
    assert results["co"]["co_citation_edges"] == direct["co_citation_edges"]
    assert results["co"]["co_citation_warnings"] == direct["co_citation_warnings"]


def test_handler_registered_by_register_arxiv_handlers() -> None:
    """The live registration path wires ComputeCoCitations to the handler."""
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers

    register_arxiv_handlers()
    assert HANDLERS["ComputeCoCitations"] is compute_co_citations


def test_declared_ports_are_the_returned_keys() -> None:
    """The returned mapping's keys are exactly CO_CITATIONS_OUTPUT_PORTS.

    Pins the declaration against the implementation: a port added to the list
    without a matching return key (or vice versa) fails here rather than at some
    downstream consumer. The output port names are deliberately NOT the bare
    `Node5Result` field names — this is what holds that choice in place.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    result = asyncio.run(
        compute_co_citations({"min_strength": 1}, {"nodes": nodes, "all_cites": edges})
    )

    assert set(result) == {p.name for p in CO_CITATIONS_OUTPUT_PORTS}
    assert {p.name for p in CO_CITATIONS_INPUT_PORTS} == {"nodes", "all_cites"}


def test_omitting_params_matches_explicit_defaults() -> None:
    """Empty params yields the same output as passing the frozen defaults
    explicitly — pinning the defaults that now live in ``CoCitationParameters``
    (they moved out of the old function signature, and no other test would catch
    them drifting).

    The fixture is discriminating on BOTH params: the guard assertions below
    confirm a different `min_strength` and a different `max_edges` each produce a
    genuinely different result, so the equality above is a real check rather than
    a fixture that would match regardless of the values.
    """
    nodes = [_rec(x) for x in ("A", "B", "D", "X", "Y")]
    # X and Y both cite A and B -> (A,B) strength 2; X also cites D -> (A,D) and
    # (B,D) at strength 1. So min_strength 2 vs 1 is visible in the output.
    edges = [
        _edge("X", "A"),
        _edge("X", "B"),
        _edge("Y", "A"),
        _edge("Y", "B"),
        _edge("X", "D"),
    ]
    payload = {"nodes": nodes, "all_cites": edges}

    omitted = asyncio.run(compute_co_citations({}, payload))
    explicit = asyncio.run(
        compute_co_citations({"min_strength": 2, "max_edges": None}, payload)
    )
    assert omitted["co_citation_edges"] == explicit["co_citation_edges"]
    assert _triples(explicit["co_citation_edges"]) == [("A", "B", 2)]

    # Fixture discriminates on min_strength: the default must not be 1.
    looser = asyncio.run(compute_co_citations({"min_strength": 1}, payload))
    assert len(looser["co_citation_edges"]) == 3

    # ...and on max_edges: the default must not be a cap, else this would match.
    capped = asyncio.run(
        compute_co_citations({"min_strength": 1, "max_edges": 1}, payload)
    )
    assert len(capped["co_citation_edges"]) == 1


def test_undeclared_input_keys_are_ignored() -> None:
    """Keys the handler does not declare as input ports are ignored.

    Under the bound contract the executor never hands the handler a foreign
    payload, but the handler still reads only its declared ports out of whatever
    mapping it is given. `cleaned_edges` is the pointed choice: it is a real port
    on the upstream stage and the one this handler deliberately does NOT read.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C")]
    edges = [_edge("C", "A"), _edge("C", "B")]

    inputs = {
        "bogus": "not-a-declared-port",
        "cleaned_edges": [_edge("C", "A")],
        "nodes": nodes,
        "all_cites": edges,
    }

    result = asyncio.run(compute_co_citations({"min_strength": 1}, inputs))
    assert _triples(result["co_citation_edges"]) == [("A", "B", 1)]


def test_missing_declared_port_fails_validation() -> None:
    """An absent declared input port is a validation failure, not a silent empty.

    The executor only omits a port when the graph left it unwired, so this is the
    unfed-port case surfacing at the handler boundary rather than turning into a
    plausible-looking empty result.
    """
    nodes = [_rec("A"), _rec("B"), _rec("C")]

    with pytest.raises(ValidationError, match="all_cites"):
        asyncio.run(compute_co_citations({"min_strength": 1}, {"nodes": nodes}))

    with pytest.raises(ValidationError, match="nodes"):
        asyncio.run(compute_co_citations({"min_strength": 1}, {"all_cites": []}))


def test_range_check_valueerrors_are_preserved() -> None:
    """``CoCitationParameters`` carries no field constraints, so the handler's own
    range checks are still the ones that fire — with the same messages.

    Pydantic would accept `min_strength=0` and `max_edges=-1` on the model, so
    losing these raises would silently change behavior rather than fail loudly.
    """
    payload = {"nodes": [_rec("A"), _rec("B")], "all_cites": []}

    with pytest.raises(ValueError, match="min_strength must be >= 1, got 0"):
        asyncio.run(compute_co_citations({"min_strength": 0}, payload))

    with pytest.raises(ValueError, match="min_strength must be >= 1, got -1"):
        asyncio.run(compute_co_citations({"min_strength": -1}, payload))

    with pytest.raises(ValueError, match="max_edges must be >= 0 or None, got -1"):
        asyncio.run(compute_co_citations({"max_edges": -1}, payload))

    # max_edges=0 remains valid — it is a cap of zero, not an invalid value.
    zero = asyncio.run(compute_co_citations({"max_edges": 0}, payload))
    assert zero["co_citation_edges"] == []


def test_clean_cycles_all_cites_wires_into_co_citations_via_execute_graph() -> None:
    """assemble.nodes -> co.nodes and clean.all_cites -> co.all_cites.

    The first port one conversion created feeding the next. Driven THROUGH
    execute_graph over the full assemble -> clean -> co chain, so the executor
    carries real Node 4.5 output across a real port-declared edge rather than a
    stub.

    The fixture is built so the `all_cites` port is DISTINGUISHED from
    `cleaned_edges`, not merely tolerated. Cycle cleaning suppresses A->X (the
    weakest-link lex tiebreaker picks it out of the A<->X 2-cycle), and that
    suppressed edge is itself a citation that carries the only co-citation pair:
    A cites both X and Y, so (X,Y) emits at strength 1. Feed `co` the
    `cleaned_edges` port instead and A->X is gone, A cites only Y, and the output
    is empty. The assertions below pin all three facts — the suppression happened,
    the two candidate ports genuinely disagree, and the executor carried the one
    the wiring declares.
    """
    seeds = [_rec("A", citation_count=10, root_ids=["A"], hop_depth=0)]
    # X->A together with A->X forms the 2-cycle; A->Y is the second citation from
    # A that makes the co-citation pair possible.
    n3 = Node3Result(
        papers=[_rec("X", citation_count=10, root_ids=["A"])],
        edges=[_edge("X", "A")],
    )
    n4 = Node4Result(
        papers=[_rec("Y", citation_count=10, root_ids=["A"])],
        edges=[_edge("A", "X"), _edge("A", "Y")],
    )
    params = {"min_strength": 1, "max_edges": None}

    merged = asyncio.run(
        assemble_graph({}, {"seeds": seeds, "backward": n3, "forward": n4})
    )
    cleaned = asyncio.run(
        clean_cycles({}, {"nodes": merged["nodes"], "cites": merged["cites"]})
    )

    # Non-vacuity, part 1: a cycle was actually suppressed, and the suppressed
    # edge is the citation the co-citation pair depends on.
    suppressed = cleaned["cycle_log"].suppressed_edges
    assert len(suppressed) == 1
    assert (suppressed[0].original.source_id, suppressed[0].original.target_id) == (
        "A",
        "X",
    )

    direct = asyncio.run(
        compute_co_citations(
            params, {"nodes": merged["nodes"], "all_cites": cleaned["all_cites"]}
        )
    )
    assert _triples(direct["co_citation_edges"]) == [("X", "Y", 1)]

    # Non-vacuity, part 2: the two candidate upstream ports DISAGREE. Without
    # this, the equality at the end would pass under a `cleaned_edges` miswiring.
    via_cleaned_edges = asyncio.run(
        compute_co_citations(
            params,
            {"nodes": merged["nodes"], "all_cites": cleaned["cleaned_edges"]},
        )
    )
    assert via_cleaned_edges["co_citation_edges"] == []
    assert via_cleaned_edges["co_citation_edges"] != direct["co_citation_edges"]

    async def _provider(_params: dict, _inputs: dict) -> dict:
        return {"seeds": seeds, "backward": n3, "forward": n4}

    register_handler("CoTestProvider", _provider)
    register_handler("AssembleGraph", assemble_graph)
    register_handler("CleanCycles", clean_cycles)
    register_handler("ComputeCoCitations", compute_co_citations)

    graph = Graph(
        name="clean-into-co-citations",
        version="1.0",
        nodes=[
            Node(
                id="src",
                type="CoTestProvider",
                params={},
                output_ports=[
                    _port("seeds"),
                    _port("backward"),
                    _port("forward"),
                ],
            ),
            Node(
                id="merge",
                type="AssembleGraph",
                params={},
                input_ports=ASSEMBLE_GRAPH_INPUT_PORTS,
                output_ports=ASSEMBLE_GRAPH_OUTPUT_PORTS,
            ),
            Node(
                id="clean",
                type="CleanCycles",
                params={},
                input_ports=CLEAN_CYCLES_INPUT_PORTS,
                output_ports=CLEAN_CYCLES_OUTPUT_PORTS,
            ),
            _co_node(params=params),
        ],
        edges=[
            Edge(source="src", target="merge", type="DATA",
                 from_port="seeds", to_port="seeds"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="backward", to_port="backward"),
            Edge(source="src", target="merge", type="DATA",
                 from_port="forward", to_port="forward"),
            Edge(source="merge", target="clean", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="merge", target="clean", type="DATA",
                 from_port="cites", to_port="cites"),
            # The point of the name-identical port choice: no adapter on either
            # hop. `nodes` comes straight from the merge stage; `all_cites` is the
            # port the cycle-cleaning conversion created for exactly this consumer.
            Edge(source="merge", target="co", type="DATA",
                 from_port="nodes", to_port="nodes"),
            Edge(source="clean", target="co", type="DATA",
                 from_port="all_cites", to_port="all_cites"),
        ],
    )

    assert validate_integrity(graph) == {"valid": True, "errors": []}

    results = asyncio.run(execute_graph(graph))

    assert results["merge"]["status"] == "SUCCESS"
    assert results["clean"]["status"] == "SUCCESS"
    assert results["co"]["status"] == "SUCCESS"
    assert results["co"]["co_citation_edges"] == direct["co_citation_edges"]
    assert results["co"]["co_citation_warnings"] == direct["co_citation_warnings"]
