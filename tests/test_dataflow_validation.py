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

    def test_two_edges_binding_one_port_is_an_error(self):
        """A bound input port takes exactly one edge. Two claiming it leaves the
        port's value decided by edge-list order — which the graph does not
        declare — so the shape is rejected rather than resolved."""
        graph = _graph(
            [
                _declared_source(["alpha"]),
                Node(id="s2", type="Source", params={},
                     output_ports=[_port("beta")]),
                _bound_target(["left"]),
            ],
            [
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s2", target="t", type="DATA",
                     from_port="beta", to_port="left"),
            ],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "input port 'left' is bound by 2 edges" in errors[0]
        assert "s.alpha" in errors[0]
        assert "s2.beta" in errors[0]

    def test_two_edges_from_one_source_binding_one_port_is_an_error(self):
        """The collision is counted per edge, not per source: one upstream
        feeding two of its outputs into a single port is the same defect."""
        graph = _graph(
            [_declared_source(["alpha", "beta"]), _bound_target(["left"])],
            [
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s", target="t", type="DATA",
                     from_port="beta", to_port="left"),
            ],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "input port 'left' is bound by 2 edges" in errors[0]

    def test_collisions_on_two_ports_report_once_each(self):
        """One error per colliding (target, port) pair — not per edge."""
        graph = _graph(
            [_declared_source(["alpha", "beta"]), _bound_target(["left", "right"])],
            [
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s", target="t", type="DATA",
                     from_port="beta", to_port="left"),
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="right"),
                Edge(source="s", target="t", type="DATA",
                     from_port="beta", to_port="right"),
            ],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("'left'" in e for e in errors) == 1
        assert sum("'right'" in e for e in errors) == 1

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


class TestUnfedPorts:
    """The floor. The collision check says a bound input port takes at most one
    incoming edge; this says it takes at least one. Every declared input port is
    required — declaring it is what says the handler reads it — so there is no
    optional marker on `PortDeclaration` and none is wanted."""

    def test_unfed_declared_port_is_an_error(self):
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left", "right"])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "input port 'right' is bound by no incoming edge" in errors[0]
        assert "'t'" in errors[0]

    def test_bound_node_with_no_incoming_edges_at_all_is_an_error(self):
        """A bound node wired to nothing is not a source — it declared that it
        reads something, and nothing feeds it."""
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left"])],
            [],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "input port 'left' is bound by no incoming edge" in errors[0]

    def test_every_unfed_port_reports_once_each(self):
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left", "right"])],
            [],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("'left'" in e for e in errors) == 1
        assert sum("'right'" in e for e in errors) == 1

    def test_empty_input_ports_is_trivially_satisfied(self):
        """`input_ports=[]` declares 'accepts no inputs'. It is bound, but has
        no ports to feed."""
        graph = _graph(
            [Node(id="t", type="Sink", params={}, input_ports=[])],
            [],
        )
        assert _errors(graph) == []

    def test_legacy_node_with_no_incoming_edges_is_not_checked(self):
        """Declaring no `input_ports` keeps a node in the legacy regime, where
        the dataflow check says nothing about it."""
        graph = _graph([Node(id="t", type="Sink", params={})], [])
        assert _errors(graph) == []

    def test_port_fed_by_a_control_edge_counts_as_fed(self):
        """Claiming is about the port declarations, not the edge type — edge
        type gates execution, not dataflow."""
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left"])],
            [Edge(source="s", target="t", type="CONTROL",
                  from_port="alpha", to_port="left")],
        )
        assert _errors(graph) == []

    def test_collision_on_one_port_does_not_suppress_an_unfed_other(self):
        """Over-feeding 'left' says nothing about 'right'. The two are
        independent defects and both are reported."""
        graph = _graph(
            [_declared_source(["alpha", "beta"]), _bound_target(["left", "right"])],
            [
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s", target="t", type="DATA",
                     from_port="beta", to_port="left"),
            ],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("is bound by 2 edges" in e for e in errors) == 1
        assert sum("bound by no incoming edge" in e for e in errors) == 1


class TestUnfedSuppression:
    """The gate is per NODE, not per port: a node whose incoming wiring already
    has a reported defect is not additionally told its ports are unfed. That
    would be a consequence of the reported defect, not a second defect."""

    def test_bad_to_port_suppresses_the_unfed_report_for_the_same_node(self):
        """The edge names a port the target does not declare, so nothing says
        which port it meant to feed. One error, not two."""
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left"])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="typo")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "not a declared input port" in errors[0]

    def test_suppression_is_per_node_not_per_port(self):
        """One malformed edge silences the unfed report for EVERY port on that
        node — including ports the malformed edge plainly never addressed."""
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left", "right", "mid"])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="typo")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "not a declared input port" in errors[0]

    def test_portless_incoming_edge_suppresses_unfed_reports(self):
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left", "right"])],
            [Edge(source="s", target="t", type="DATA")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "declares input_ports" in errors[0]

    def test_half_declared_incoming_edge_suppresses_unfed_reports(self):
        graph = _graph(
            [_declared_source(["alpha"]), _bound_target(["left", "right"])],
            [Edge(source="s", target="t", type="DATA", from_port="alpha")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "declares from_port but not to_port" in errors[0]

    def test_dangling_incoming_edge_suppresses_unfed_reports(self):
        """The referential check already reported this node's wiring as broken;
        the dataflow check does not pile on."""
        graph = _graph(
            [_bound_target(["left", "right"])],
            [Edge(source="ghost", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_suppression_does_not_leak_to_a_different_bound_node(self):
        """The gate is scoped to the node with the reported defect. A second
        bound node with a genuinely unfed port still reports."""
        graph = _graph(
            [
                _declared_source(["alpha"]),
                Node(id="t", type="Sink", params={}, input_ports=[_port("left")]),
                Node(id="u", type="Sink", params={}, input_ports=[_port("solo")]),
            ],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="typo")],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("not a declared input port" in e for e in errors) == 1
        assert sum("'u'" in e and "'solo'" in e for e in errors) == 1


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

    def test_two_edges_binding_one_port_on_legacy_target_is_not_an_error(self):
        """The port-collision check turns on the TARGET's declaration too. A
        legacy consumer keys its inputs by source node id, so the `to_port`
        names on these edges are inert and collide with nothing."""
        graph = _graph(
            [
                _declared_source(["alpha"]),
                Node(id="s2", type="Source", params={},
                     output_ports=[_port("beta")]),
                Node(id="t", type="Sink", params={}),
            ],
            [
                Edge(source="s", target="t", type="DATA",
                     from_port="alpha", to_port="left"),
                Edge(source="s2", target="t", type="DATA",
                     from_port="beta", to_port="left"),
            ],
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


class TestConfigPredicateNames:
    """`enabled_when` must name a key PRESENT in the node's own params, with any
    value at all. This is a check on the NAME; the IDG-069 truthiness rider
    governs the VALUE and is untouched — a param holding None, 0, '', [], {} or
    False is a legal declaration that disables the node. The only defect is
    omitting the key, which is a reference to nothing: the executor's
    `params.get(name)` reads None for it, so the node is disabled for every run
    rather than gated by anything. The executor is unchanged — this says the
    graph carries a declaration defect, not what happens if you run it."""

    def test_predicate_naming_a_declared_param_is_valid(self):
        graph = _graph(
            [Node(id="n", type="Gated", params={"llm": True}, enabled_when="llm")],
            [],
        )
        assert _errors(graph) == []

    @pytest.mark.parametrize("value", [None, False, 0, 0.0, "", [], {}])
    def test_predicate_naming_a_param_holding_a_falsy_value_is_valid(self, value):
        """Present-with-any-value is the whole rule. Each of these disables the
        node at run time and none of them is a declaration defect."""
        graph = _graph(
            [Node(id="n", type="Gated", params={"llm": value}, enabled_when="llm")],
            [],
        )
        assert _errors(graph) == []

    def test_predicate_naming_an_absent_param_is_an_error(self):
        graph = _graph(
            [Node(id="n", type="Gated", params={"llm": True}, enabled_when="lm")],
            [],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "enabled_when names param 'lm'" in errors[0]
        assert "params: ['llm']" in errors[0]

    def test_predicate_check_applies_to_a_legacy_node(self):
        """The legacy exemption is pierced deliberately: it is about DATAFLOW,
        and a predicate name is not dataflow. A node declaring no ports at all
        is still checked."""
        graph = _graph(
            [Node(id="n", type="Gated", params={}, enabled_when="llm")],
            [],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "enabled_when names param 'llm'" in errors[0]

    def test_predicate_check_applies_to_a_bound_node(self):
        graph = _graph(
            [
                _declared_source(["alpha"]),
                Node(id="t", type="Gated", params={}, enabled_when="llm",
                     input_ports=[_port("left")]),
            ],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "enabled_when names param 'llm'" in errors[0]

    def test_dangling_edge_does_not_suppress_the_predicate_error(self):
        """No `faulted_targets` suppression: the check is node-internal and
        wholly independent of incoming wiring. A node with a dangling edge still
        has a wrong predicate name."""
        graph = _graph(
            [Node(id="n", type="Gated", params={"llm": True}, enabled_when="lm")],
            [Edge(source="ghost", target="n", type="DATA")],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("does not exist" in e for e in errors) == 1
        assert sum("enabled_when names param 'lm'" in e for e in errors) == 1

    def test_undeclared_predicate_is_not_checked(self):
        """`enabled_when is None` is the legacy regime — the node always runs
        and nothing is referenced, so there is no name to be wrong."""
        graph = _graph([Node(id="n", type="Gated", params={})], [])
        assert _errors(graph) == []


class TestDisabledPassthroughPorts:
    """Every `disabled_passthrough` KEY must name a declared output port of the
    node and every VALUE a declared input port of it — the mapping is what the
    node emits from what it received, so both halves are references into its own
    declarations. Gated on the mapping being declared, NOT on the ports fence: a
    node carrying the mapping with no port declarations at all is the worst
    case, not an exempt one."""

    def _passthrough_node(self, mapping: dict[str, str]) -> Node:
        return Node(
            id="t", type="Gated", params={},
            input_ports=[_port("left")], output_ports=[_port("out")],
            disabled_passthrough=mapping,
        )

    def _fed(self, node: Node) -> Graph:
        return _graph(
            [_declared_source(["alpha"]), node],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )

    def test_passthrough_naming_declared_ports_is_valid(self):
        assert _errors(self._fed(self._passthrough_node({"out": "left"}))) == []

    def test_empty_passthrough_is_trivially_satisfied(self):
        """`{}` is a declaration — the disabled node forwards nothing — and it
        has no entries to be wrong."""
        assert _errors(self._fed(self._passthrough_node({}))) == []

    def test_key_naming_an_undeclared_output_port_is_an_error(self):
        errors = _errors(self._fed(self._passthrough_node({"nope": "left"})))
        assert len(errors) == 1
        assert "disabled_passthrough emits output port 'nope'" in errors[0]
        assert "declared: ['out']" in errors[0]

    def test_value_naming_an_undeclared_input_port_is_an_error(self):
        errors = _errors(self._fed(self._passthrough_node({"out": "nope"})))
        assert len(errors) == 1
        assert "disabled_passthrough forwards input port 'nope'" in errors[0]
        assert "declared: ['left']" in errors[0]

    def test_both_halves_wrong_reports_both(self):
        errors = _errors(self._fed(self._passthrough_node({"bad": "worse"})))
        assert len(errors) == 2
        assert sum("emits output port 'bad'" in e for e in errors) == 1
        assert sum("forwards input port 'worse'" in e for e in errors) == 1

    def test_every_bad_entry_reports_once_each(self):
        node = Node(
            id="t", type="Gated", params={},
            input_ports=[_port("left")], output_ports=[_port("out")],
            disabled_passthrough={"out": "left", "nope": "left"},
        )
        errors = _errors(self._fed(node))
        assert len(errors) == 1
        assert "emits output port 'nope'" in errors[0]

    def test_passthrough_on_a_node_declaring_no_ports_reports_both(self):
        """The legacy node is the WORST case, not an exempt one: it declares a
        mapping between ports it does not have, so neither half can be
        satisfied."""
        graph = _graph(
            [Node(id="n", type="Gated", params={},
                  disabled_passthrough={"out": "left"})],
            [],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("emits output port 'out'" in e for e in errors) == 1
        assert sum("forwards input port 'left'" in e for e in errors) == 1
        assert all("declared: []" in e for e in errors)

    def test_passthrough_with_output_ports_none_is_still_checked(self):
        """Clause 5 in isolation: declaring inputs but not outputs does not
        exempt the key half."""
        graph = _graph(
            [
                _declared_source(["alpha"]),
                Node(id="t", type="Gated", params={},
                     input_ports=[_port("left")],
                     disabled_passthrough={"out": "left"}),
            ],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 1
        assert "emits output port 'out'" in errors[0]
        assert "declared: []" in errors[0]

    def test_dangling_edge_does_not_suppress_the_passthrough_error(self):
        """No `faulted_targets` suppression here either — the unfed report is
        suppressed by the dangling edge, the passthrough defect is not."""
        graph = _graph(
            [Node(id="t", type="Gated", params={},
                  input_ports=[_port("left")], output_ports=[_port("out")],
                  disabled_passthrough={"nope": "left"})],
            [Edge(source="ghost", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        errors = _errors(graph)
        assert len(errors) == 2
        assert sum("does not exist" in e for e in errors) == 1
        assert sum("emits output port 'nope'" in e for e in errors) == 1

    def test_undeclared_passthrough_is_not_checked(self):
        graph = _graph(
            [_declared_source(["alpha"]),
             Node(id="t", type="Gated", params={}, input_ports=[_port("left")])],
            [Edge(source="s", target="t", type="DATA",
                  from_port="alpha", to_port="left")],
        )
        assert _errors(graph) == []
