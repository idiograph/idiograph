# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Declared-graph projection — contract, layout, purity and determinism tests.

Two levels, mirroring the sibling depth/provenance suite: a small hand-built
``Graph`` exercises the layout invariants and the purity guarantee precisely,
and the real ``build_pipeline_graph`` output validates the projection against
the graph the renderer actually consumes.

THE PURITY LEVEL IS THE POINT OF THE SMALL GRAPH. It is constructed in-process
from ``core.models`` alone — no registry, no fixture on disk, no import of the
arxiv pipeline — so a test that passes there proves the projection needs nothing
but its argument.
"""

import builtins
import json
import os
import socket

import pytest

from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import _build_nx_graph, _longest_chain
from idiograph.domains.viewer import project_graph
from idiograph.domains.viewer.graph_projection import DECLARATION_CAVEAT


def _ports(*names: str) -> list[PortDeclaration]:
    return [PortDeclaration(name=n, port_type="any") for n in names]


def _toy_graph() -> Graph:
    """A diamond with a shortcut and a doubled node pair.

    Shapes every invariant the layout has to hold:

    * ``head -> tail`` is a SHORTCUT edge — under shortest-path ranking ``tail``
      would sit level with ``left``/``right``, above its own producer ``join``.
      Under longest-path ranking it cannot.
    * ``left -> join`` is declared TWICE, on two different port pairs. It is the
      ``assemble -> clean`` shape: one node pair, two edges, and a projection
      that collapses to node pairs would silently emit one.
    * ``gated`` carries ``enabled_when``/``disabled_passthrough`` — the only
      real conditionality shape in the subject pipeline.
    """
    return Graph(
        name="toy",
        version="0.1",
        nodes=[
            Node(
                id="head",
                type="Head",
                params={"seeds": [1]},
                input_ports=[],
                output_ports=_ports("a", "b"),
                resources=["http_client"],
            ),
            Node(
                id="left",
                type="Left",
                input_ports=_ports("a"),
                output_ports=_ports("x", "y"),
            ),
            Node(
                id="right",
                type="Right",
                input_ports=_ports("b"),
                output_ports=_ports("x"),
            ),
            Node(
                id="gated",
                type="Gated",
                params={"llm": "on"},
                input_ports=_ports("x"),
                output_ports=_ports("out"),
                resources=["anthropic_client"],
                enabled_when="llm",
                disabled_passthrough={"out": "x"},
            ),
            Node(
                id="join",
                type="Join",
                input_ports=_ports("p", "q", "r"),
                output_ports=_ports("z"),
            ),
            Node(
                id="tail",
                type="Tail",
                input_ports=_ports("z", "shortcut"),
                output_ports=_ports("done"),
            ),
        ],
        edges=[
            Edge(source="head", target="left", from_port="a", to_port="a"),
            Edge(source="head", target="right", from_port="b", to_port="b"),
            Edge(source="left", target="gated", from_port="x", to_port="x"),
            Edge(source="left", target="join", from_port="x", to_port="p"),
            Edge(source="left", target="join", from_port="y", to_port="q"),
            Edge(source="right", target="join", from_port="x", to_port="r"),
            Edge(source="join", target="tail", from_port="z", to_port="z"),
            # The shortcut: head reaches the last rank in one hop.
            Edge(source="head", target="tail", from_port="a", to_port="shortcut"),
        ],
    )


def _pipeline_graph() -> Graph:
    """The real subject — the declared citation-traversal pipeline.

    Imported inside the function: ``pipeline_graph`` pulls in ``pipeline``,
    whose module body runs ``load_dotenv()``, and the purity tests above must
    not have that import land inside their tripwire window.
    """
    from idiograph.domains.arxiv.models import (
        BackwardParameters,
        ForwardParameters,
        PipelineParameters,
    )
    from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph

    return build_pipeline_graph(
        [{"doi": "10.1000/xyz"}],
        PipelineParameters(
            backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
            forward=ForwardParameters(
                n_forward=10,
                lambda_decay=0.1,
                alpha=1.0,
                beta=1.0,
                sort="cited_by_count:desc",
            ),
            current_year=2026,
        ),
    )


# ── Contract ─────────────────────────────────────────────────────────────────

def test_emits_the_same_three_key_contract_as_the_sibling_projection():
    """One contract, so one renderer can consume both views."""
    data = project_graph(_toy_graph())
    assert set(data) == {"meta", "nodes", "edges"}
    assert data["meta"]["view"] == "declared_graph"
    assert data["meta"]["layout"] == "layered_dag"
    for record in data["nodes"]:
        assert "x" in record and "y" in record


def test_nodes_and_edges_are_sorted_and_complete():
    data = project_graph(_toy_graph())
    ids = [n["node_id"] for n in data["nodes"]]
    assert ids == sorted(ids)
    assert set(ids) == {"head", "left", "right", "gated", "join", "tail"}
    keys = [(e["source_id"], e["target_id"], e["from_port"], e["to_port"], e["type"])
            for e in data["edges"]]
    assert keys == sorted(keys)
    assert len(data["edges"]) == 8


def test_coordinates_stay_inside_the_unit_square():
    data = project_graph(_toy_graph())
    for record in data["nodes"]:
        assert 0.0 <= record["x"] <= 1.0
        assert 0.0 <= record["y"] <= 1.0
    for edge in data["edges"]:
        for key in ("x1", "y1", "x2", "y2"):
            assert 0.0 <= edge[key] <= 1.0


def test_run_state_is_not_emitted():
    """``Node.status`` is run state the executor mutates in place.

    This is a graph DEFINITION; reporting status here would describe whichever
    run last touched the object. It is the defect `summarize_intent` carries and
    the reason this projection does not build on it.
    """
    data = project_graph(_toy_graph())
    for record in data["nodes"]:
        assert "status" not in record
    assert "status" not in data["meta"]


# ── Layered layout ───────────────────────────────────────────────────────────

def test_every_edge_advances_at_least_one_rank():
    """The correctness bar for the layout: no wire runs level or backwards.

    The shortcut ``head -> tail`` is what makes this a real assertion — under a
    shortest-path rank ``tail`` would land beside its own producer.
    """
    data = project_graph(_toy_graph())
    assert all(e["rank_span"] >= 1 for e in data["edges"])
    shortcut = next(e for e in data["edges"] if e["to_port"] == "shortcut")
    assert shortcut["rank_span"] == 3


def test_rank_partitions_the_nodes_and_orders_them_left_to_right():
    data = project_graph(_toy_graph())
    ranks = data["meta"]["ranks"]
    assert sum(len(r) for r in ranks) == data["meta"]["node_count"]
    assert {n for rank in ranks for n in rank} == {n["node_id"] for n in data["nodes"]}

    x_of = {n["node_id"]: n["x"] for n in data["nodes"]}
    rank_of = {n["node_id"]: n["rank"] for n in data["nodes"]}
    for record in data["nodes"]:
        assert rank_of[record["node_id"]] == record["rank"]
    # X is a strictly increasing function of rank, and only of rank.
    by_rank: dict[int, set[float]] = {}
    for node_id, rank in rank_of.items():
        by_rank.setdefault(rank, set()).add(x_of[node_id])
    assert all(len(xs) == 1 for xs in by_rank.values())
    columns = [next(iter(by_rank[r])) for r in sorted(by_rank)]
    assert columns == sorted(columns) and len(set(columns)) == len(columns)


def test_longest_chain_agrees_with_core_query():
    """The rank recurrence is the one `core.query._longest_chain` runs.

    That helper is private and returns only the winning chain, so the projection
    re-states the recurrence to get a per-node rank out of it. This pins the two
    to the same answer instead of leaving the duplication to drift.
    """
    for graph in (_toy_graph(), _pipeline_graph()):
        data = project_graph(graph)
        assert data["meta"]["longest_chain"] == _longest_chain(_build_nx_graph(graph))
        assert data["meta"]["longest_chain_length"] == len(data["meta"]["longest_chain"])
        assert data["meta"]["rank_count"] == data["meta"]["longest_chain_length"]


def test_a_cyclic_graph_has_no_layered_layout():
    cyclic = Graph(
        name="cyclic",
        version="0.1",
        nodes=[Node(id="a", type="A"), Node(id="b", type="B")],
        edges=[Edge(source="a", target="b"), Edge(source="b", target="a")],
    )
    with pytest.raises(ValueError, match="cycle"):
        project_graph(cyclic)


def test_an_empty_graph_is_rejected_rather_than_projected():
    with pytest.raises(ValueError, match="no nodes"):
        project_graph(Graph(name="empty", version="0.1"))


# ── Port identity ────────────────────────────────────────────────────────────

def test_two_edges_between_one_node_pair_stay_two_edges():
    """`left -> join` is declared twice on different ports — the `assemble ->
    clean` shape. Collapsing to node pairs would under-report the wiring."""
    data = project_graph(_toy_graph())
    pair = [e for e in data["edges"] if (e["source_id"], e["target_id"]) == ("left", "join")]
    assert len(pair) == 2
    assert {e["from_port"] for e in pair} == {"x", "y"}
    assert {e["to_port"] for e in pair} == {"p", "q"}
    # Distinct on screen, not merely in the data.
    assert len({(e["x1"], e["y1"], e["x2"], e["y2"]) for e in pair}) == 2


def test_every_edge_has_a_unique_id_and_its_own_endpoints():
    for graph in (_toy_graph(), _pipeline_graph()):
        data = project_graph(graph)
        ids = [e["id"] for e in data["edges"]]
        assert len(set(ids)) == len(ids)
        geometry = {(e["x1"], e["y1"], e["x2"], e["y2"]) for e in data["edges"]}
        assert len(geometry) == len(ids)


def test_edge_endpoints_land_on_the_declared_port_anchors():
    data = project_graph(_toy_graph())
    anchors = {n["node_id"]: n["port_anchors"] for n in data["nodes"]}
    for edge in data["edges"]:
        source = anchors[edge["source_id"]]["outputs"][edge["from_port"]]
        target = anchors[edge["target_id"]]["inputs"][edge["to_port"]]
        assert (edge["x1"], edge["y1"]) == (source["x"], source["y"])
        assert (edge["x2"], edge["y2"]) == (target["x"], target["y"])


def test_a_bound_node_with_no_inputs_declares_no_input_anchors():
    """`input_ports=[]` is a declaration — the shape of a pipeline head."""
    data = project_graph(_toy_graph())
    head = next(n for n in data["nodes"] if n["node_id"] == "head")
    assert head["input_ports"] == []
    assert head["port_anchors"]["inputs"] == {}
    assert set(head["port_anchors"]["outputs"]) == {"a", "b"}


# ── The declaration, emitted ─────────────────────────────────────────────────

def test_conditionality_is_emitted_on_the_node_that_declares_it():
    data = project_graph(_toy_graph())
    gated = next(n for n in data["nodes"] if n["node_id"] == "gated")
    assert gated["enabled_when"] == "llm"
    assert gated["disabled_passthrough"] == {"out": "x"}
    assert data["meta"]["conditional_node_count"] == 1
    for record in data["nodes"]:
        if record["node_id"] != "gated":
            assert record["enabled_when"] is None


def test_undeclared_resources_stay_distinguishable_from_declared_empty():
    """`None` is not `[]` — the fence between the two regimes is load-bearing."""
    graph = _toy_graph()
    data = project_graph(graph)
    by_id = {n["node_id"]: n for n in data["nodes"]}
    assert by_id["head"]["resources"] == ["http_client"]
    assert by_id["left"]["resources"] is None
    assert by_id["left"]["input_ports"] == ["a"]
    assert data["meta"]["resource_names"] == ["anthropic_client", "http_client"]


def test_param_values_are_not_emitted_only_their_key_names():
    """Params carry live config objects; the view's subject is shape, not config."""
    data = project_graph(_toy_graph())
    head = next(n for n in data["nodes"] if n["node_id"] == "head")
    assert head["param_keys"] == ["seeds"]
    assert "params" not in head


def test_transitive_reach_is_reported_per_node():
    data = project_graph(_toy_graph())
    by_id = {n["node_id"]: n for n in data["nodes"]}
    assert by_id["head"]["upstream_count"] == 0
    assert by_id["head"]["downstream_count"] == 5
    assert by_id["tail"]["downstream_count"] == 0
    assert by_id["gated"]["upstream_count"] == 2


def test_the_declaration_caveat_rides_in_meta_without_claiming_a_run():
    """The open question is stated; it is not answered in either direction."""
    data = project_graph(_toy_graph())
    assert data["meta"]["caveats"]["declaration_vs_execution"] == DECLARATION_CAVEAT
    assert "unruled" in DECLARATION_CAVEAT


# ── One weight for every node ────────────────────────────────────────────────

def test_node_size_is_a_graph_level_fact_with_no_per_node_override():
    """The LLM node is drawn exactly as its neighbours are.

    That claim is structural, not stylistic: the contract carries ONE size, in
    meta, and offers no per-node field a later editor could vary. The record key
    sets are asserted identical so a size (or a badge) cannot be smuggled onto
    one node alone.
    """
    data = project_graph(_pipeline_graph())
    assert set(data["meta"]["node_size"]) == {"w", "h"}
    key_sets = {frozenset(record) for record in data["nodes"]}
    assert len(key_sets) == 1
    for forbidden in ("w", "h", "size", "radius", "color", "colour", "badge"):
        assert forbidden not in next(iter(key_sets))


# ── Purity and determinism ───────────────────────────────────────────────────

def test_projection_touches_no_file_socket_or_environment(monkeypatch):
    """Purity asserted by RECORDING every route out of the process.

    The recorders delegate to the real callables rather than raising, so the
    call completes either way and the assertion is about what was reached, not
    about whether an exception happened to escape. Undone before asserting:
    pytest's own failure reporting reads source files.
    """
    graph = _toy_graph()  # built before the recorders are installed
    reached: list[str] = []

    def _record(name: str, real):
        def _fn(*args, **kwargs):
            reached.append(name)
            return real(*args, **kwargs)

        return _fn

    class _RecordingEnviron(dict):
        def __getitem__(self, key):
            reached.append(f"os.environ[{key!r}]")
            return super().__getitem__(key)

        def get(self, key, default=None):
            reached.append(f"os.environ.get({key!r})")
            return super().get(key, default)

    monkeypatch.setattr(builtins, "open", _record("builtins.open", builtins.open))
    monkeypatch.setattr(socket, "socket", _record("socket.socket", socket.socket))
    monkeypatch.setattr(
        socket, "create_connection", _record("socket.create_connection", socket.create_connection)
    )
    monkeypatch.setattr(os, "environ", _RecordingEnviron(os.environ))

    data = project_graph(graph)
    monkeypatch.undo()

    assert reached == [], (
        f"project_graph is not pure — it reached {reached}. The projection must "
        f"be a function of the Graph and nothing else."
    )
    assert data["meta"]["node_count"] == 6


def test_equal_graphs_emit_byte_identical_json():
    """`Graph` is built fresh per call by design, so this is a real property.

    Two separately-constructed, equal Graphs — not one object projected twice —
    have to serialize to the same bytes under ``sort_keys=True``.
    """
    a = json.dumps(project_graph(_toy_graph()), sort_keys=True)
    b = json.dumps(project_graph(_toy_graph()), sort_keys=True)
    assert a == b

    c = json.dumps(project_graph(_pipeline_graph()), sort_keys=True)
    d = json.dumps(project_graph(_pipeline_graph()), sort_keys=True)
    assert c == d


def test_declaration_order_does_not_move_the_layout():
    """The layout is a fact about the graph, not about how it was written down."""
    graph = _toy_graph()
    shuffled = Graph(
        name=graph.name,
        version=graph.version,
        nodes=list(reversed(graph.nodes)),
        edges=list(reversed(graph.edges)),
    )
    assert json.dumps(project_graph(graph), sort_keys=True) == json.dumps(
        project_graph(shuffled), sort_keys=True
    )


# ── The real subject ─────────────────────────────────────────────────────────

def test_the_declared_pipeline_projects_to_eleven_nodes_and_twenty_one_edges():
    data = project_graph(_pipeline_graph())
    assert data["meta"]["node_count"] == 11
    assert data["meta"]["edge_count"] == 21
    assert {n["node_id"] for n in data["nodes"]} == {
        "resolve", "backward", "forward", "assemble", "clean", "co",
        "annotate", "depth", "pagerank", "communities", "enrich",
    }


def test_every_declared_edge_is_data_so_no_distinction_is_available_to_encode():
    """All 21 edges take the model default. A uniform field is not a signal."""
    data = project_graph(_pipeline_graph())
    assert data["meta"]["edge_type_counts"] == {"DATA": 21}


def test_the_assemble_clean_pair_survives_as_two_edges():
    data = project_graph(_pipeline_graph())
    pair = [e for e in data["edges"]
            if (e["source_id"], e["target_id"]) == ("assemble", "clean")]
    assert len(pair) == 2
    assert {e["from_port"] for e in pair} == {"nodes", "cites"}


def test_the_llm_node_is_the_only_conditional_one_and_declares_its_passthrough():
    data = project_graph(_pipeline_graph())
    annotate = next(n for n in data["nodes"] if n["node_id"] == "annotate")
    assert annotate["type"] == "AnnotateRelationships"
    assert annotate["enabled_when"] == "llm"
    assert annotate["disabled_passthrough"] == {"nodes": "nodes"}
    assert annotate["resources"] == ["anthropic_client"]
    assert data["meta"]["conditional_node_count"] == 1


def test_the_projection_synthesizes_no_node_the_declaration_does_not_contain():
    """The cache boundary is OUT OF SCOPE and stays out.

    `build_pipeline_graph` transcribes `run_traversal` plus `resolve_seeds`;
    `cache.py` is outside its frame, so the declared Graph carries no node for
    the registry write and none for the cache read. Rendering structure the
    declaration does not contain is the exact failure this project indicts, so
    the node and edge sets are asserted to be exactly the graph's own.
    """
    graph = _pipeline_graph()
    data = project_graph(graph)
    assert {n["node_id"] for n in data["nodes"]} == {n.id for n in graph.nodes}
    assert len(data["edges"]) == len(graph.edges)
    declared = sorted(
        (e.source, e.target, e.from_port, e.to_port, e.type) for e in graph.edges
    )
    projected = sorted(
        (e["source_id"], e["target_id"], e["from_port"], e["to_port"], e["type"])
        for e in data["edges"]
    )
    assert projected == declared
