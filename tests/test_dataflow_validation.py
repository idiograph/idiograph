# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# Dataflow checking in core/query.py::validate_integrity.
#
# The point of edge-declared binding is that the graph becomes self-sufficient:
# every port-declared edge is checkable against its endpoints' declarations
# WITHOUT reading handler source. These tests pin each ratchet-enforcement error
# and, just as importantly, pin that legacy graphs stay valid.

import pytest

from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity


def _port(name: str) -> PortDeclaration:
    """An untyped port. `port_type` is inert — nothing validates it."""
    return PortDeclaration(name=name, port_type="untyped")


def _graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    return Graph(name="dataflow-test", version="1.0", nodes=nodes, edges=edges)


def _bound_target(ports: list[str] | None = None) -> Node:
    return Node(
        id="t", type="Sink", params={},
        input_ports=[_port(p) for p in (ports if ports is not None else ["left"])],
    )


def _declared_source(ports: list[str] | None = None) -> Node:
    return Node(
        id="s", type="Source", params={},
        output_ports=[_port(p) for p in (ports if ports is not None else ["alpha"])],
    )


def _errors(graph: Graph) -> list[str]:
    result = validate_integrity(graph)
    assert result["valid"] is (len(result["errors"]) == 0)
    return result["errors"]


class TestValidBoundGraphs:
    def test_fully_declared_graph_is_valid(self):
        graph = _graph(
            [_declared_source(), _bound_target()],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        assert _errors(graph) == []

    def test_same_source_two_ports_is_valid(self):
        """Two edges from one source into two distinct ports — the shape binding
        exists to make expressible — validates cleanly."""
        graph = _graph(
            [_declared_source(["alpha", "beta"]), _bound_target(["left", "right"])],
            [
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s", target="t", type="DATA",
                     from_port="beta", to_port="right"),
            ],
        )
        assert _errors(graph) == []


class TestRatchetEnforcement:
    def test_portless_edge_into_bound_node_is_an_error(self):
        """The ratchet: once a node declares input_ports, EVERY incoming edge
        must declare ports — a portless one would silently contribute nothing."""
        graph = _graph(
            [_declared_source(), _bound_target()],
            [Edge(source="s", target="t", type="DATA")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "declares input_ports" in errors[0]
        assert "every incoming edge" in errors[0]

    @pytest.mark.parametrize(
        "from_port,to_port,present,absent",
        [
            ("alpha", None, "from_port", "to_port"),
            (None, "left", "to_port", "from_port"),
        ],
    )
    def test_half_declared_edge_is_an_error(self, from_port, to_port, present, absent):
        """No half-declared middle state: an edge is fully port-declared or not
        port-declared at all."""
        graph = _graph(
            [_declared_source(), _bound_target()],
            [Edge(source="s", target="t", type="DATA",
                  from_port=from_port, to_port=to_port)],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert f"declares {present} but not {absent}" in errors[0]

    def test_half_declared_edge_is_an_error_even_for_legacy_target(self):
        """Half-declaration is malformed regardless of which regime the target
        is in — it is a defect in the edge itself."""
        graph = _graph(
            [_declared_source(), Node(id="t", type="Sink", params={})],
            [Edge(source="s", target="t", type="DATA", from_port="alpha")],
        )
        assert len(_errors(graph)) == 1

    def test_undeclared_upstream_feeding_bound_node_is_an_error(self):
        """Declaring outputs is data, not code: the upstream handler already
        emits the key, so a bound consumer may demand the declaration."""
        graph = _graph(
            [Node(id="s", type="Source", params={}), _bound_target()],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "declares no output_ports" in errors[0]


class TestPortNameChecks:
    def test_from_port_absent_from_source_output_ports_is_an_error(self):
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left"])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="nope", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "from_port 'nope' is not a declared output port" in errors[0]

    def test_to_port_absent_from_target_input_ports_is_an_error(self):
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left"])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="nope")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "to_port 'nope' is not a declared input port" in errors[0]

    def test_both_port_names_wrong_reports_both(self):
        """Errors accumulate — validation reports the whole picture, not the
        first defect it trips over."""
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left"])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="nope", to_port="also-nope")],
        )
        assert len(_errors(graph)) == 2


class TestLegacyGraphsStayValid:
    def test_undeclared_graph_has_no_dataflow_errors(self):
        """A graph with no declarations anywhere is legacy and untouched by the
        dataflow check."""
        graph = _graph(
            [
                Node(id="a", type="A", params={}),
                Node(id="b", type="B", params={}),
            ],
            [Edge(source="a", target="b", type="DATA")],
        )
        assert _errors(graph) == []

    def test_arxiv_pipeline_is_valid(self):
        """The arXiv demo pipeline is legacy by ruling — including its
        data-carrying CONTROL edge — and must stay valid."""
        from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE

        assert _errors(ARXIV_PIPELINE) == []

    def test_sample_pipeline_is_valid(self):
        from idiograph.core.pipeline import SAMPLE_PIPELINE

        assert _errors(SAMPLE_PIPELINE) == []

    def test_port_declared_edge_into_legacy_target_is_not_an_error(self):
        """The ratchet turns on the TARGET's declaration. An upstream that has
        declared its outputs does not drag an undeclared consumer across the
        fence, so this edge is inert rather than invalid."""
        graph = _graph(
            [_declared_source(["alpha"]), Node(id="t", type="Sink", params={})],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        assert _errors(graph) == []


class TestReferentialChecksStillApply:
    def test_missing_endpoints_still_reported(self):
        graph = _graph(
            [Node(id="a", type="A", params={})],
            [Edge(source="ghost", target="a", type="DATA")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_dangling_edge_does_not_produce_dataflow_noise(self):
        """A port-declared edge to a nonexistent node reports the referential
        error only — the dataflow check cannot say anything useful about an
        endpoint that is not there."""
        graph = _graph(
            [_bound_target()],
            [Edge(source="ghost", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "does not exist" in errors[0]
