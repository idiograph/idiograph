# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import pytest

from idiograph.core.models import Edge, Graph, Node
from idiograph.core.query import (
    find_cycles,
    get_downstream,
    get_upstream,
    summarize_intent,
    topological_sort,
    validate_integrity,
)


class TestDownstream:
    def test_all_descendants_reachable(self, sample_graph):
        result = get_downstream(sample_graph, "n1")
        assert set(result) == {"n2", "n3", "n4"}

    def test_sink_node_has_no_downstream(self, sample_graph):
        assert get_downstream(sample_graph, "n4") == []

    def test_missing_node_returns_empty(self, sample_graph):
        assert get_downstream(sample_graph, "ghost") == []


class TestUpstream:
    def test_all_ancestors_found(self, sample_graph):
        result = get_upstream(sample_graph, "n4")
        assert set(result) == {"n1", "n2", "n3"}

    def test_source_node_has_no_upstream(self, sample_graph):
        assert get_upstream(sample_graph, "n1") == []


class TestTopologicalSort:
    def test_order_is_valid(self, sample_graph):
        order = topological_sort(sample_graph)
        # Every node must appear before its dependents
        for edge in sample_graph.edges:
            assert order.index(edge.source) < order.index(edge.target)

    def test_all_nodes_included(self, sample_graph):
        order = topological_sort(sample_graph)
        assert set(order) == {n.id for n in sample_graph.nodes}

    def test_raises_on_cycle(self, cyclic_graph):
        with pytest.raises(ValueError, match="cycle"):
            topological_sort(cyclic_graph)


class TestFindCycles:
    def test_acyclic_graph_returns_empty(self, sample_graph):
        assert find_cycles(sample_graph) == []

    def test_cyclic_graph_detected(self, cyclic_graph):
        cycles = find_cycles(cyclic_graph)
        assert len(cycles) > 0


class TestValidateIntegrity:
    def test_valid_graph_passes(self, sample_graph):
        result = validate_integrity(sample_graph)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_dangling_target_caught(self, sample_graph):
        sample_graph.edges.append(
            Edge(source="n4", target="ghost_node", type="DATA")
        )
        result = validate_integrity(sample_graph)
        assert result["valid"] is False
        assert any("ghost_node" in e for e in result["errors"])

    def test_dangling_source_caught(self):
        g = Graph(
            name="bad", version="1.0",
            nodes=[Node(id="real", type="Render")],
            edges=[Edge(source="phantom", target="real", type="DATA")],
        )
        result = validate_integrity(g)
        assert result["valid"] is False


class TestSummarizeIntent:
    def test_domain_is_vfx(self, sample_graph):
        result = summarize_intent(sample_graph)
        assert result["domain"] == "vfx"

    def test_control_gates_identified(self, sample_graph):
        result = summarize_intent(sample_graph)
        # n3 → n4 is a CONTROL edge, so n3 is a gate
        assert "n3" in result["control_gates"]

    def test_subgraph_scope(self, sample_graph):
        result = summarize_intent(sample_graph, node_ids=["n1", "n2"])
        assert result["scope"] == "subgraph"
        assert result["node_count"] == 2

    def test_ai_domain_detected(self):
        g = Graph(
            name="ai_test", version="1.0",
            nodes=[
                Node(id="a", type="LLMCall", params={}),
                Node(id="b", type="Evaluator", params={}),
            ],
            edges=[Edge(source="a", target="b", type="DATA")],
        )
        result = summarize_intent(g)
        assert result["domain"] == "ai"


class TestSummarizeIntentCriticalPath:
    """
    `critical_path` is the LONGEST chain through the scope, by node count.

    These run on `branching_graph`, not `sample_graph`: on a straight line
    longest and shortest coincide, so a linear fixture cannot tell a correct
    implementation from one that takes shortest paths.
    """

    def test_reports_the_longest_chain(self, branching_graph):
        result = summarize_intent(branching_graph)
        assert result["critical_path"] == ["root", "alpha", "merge", "tail", "leaf"]

    def test_does_not_take_the_shortcut_edge(self, branching_graph):
        # root → merge skips alpha/beta. The longest shortest-path in this graph
        # is ['root', 'merge', 'tail', 'leaf'] — 4 nodes, one short, and it
        # reports the pipeline as shallower than it is.
        path = summarize_intent(branching_graph)["critical_path"]
        assert path[:2] != ["root", "merge"]
        assert len(path) == 5

    def test_returns_node_ids_forming_a_real_path(self, branching_graph):
        path = summarize_intent(branching_graph)["critical_path"]
        declared = {(e.source, e.target) for e in branching_graph.edges}
        hops = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
        assert all(hop in declared for hop in hops)

    def test_ties_resolve_lexicographically(self, branching_graph):
        # alpha and beta are interchangeable by length; the smaller id wins.
        path = summarize_intent(branching_graph)["critical_path"]
        assert "alpha" in path
        assert "beta" not in path

    def test_independent_of_declaration_order(self, branching_graph):
        shuffled = Graph(
            name=branching_graph.name,
            version=branching_graph.version,
            nodes=list(reversed(branching_graph.nodes)),
            edges=list(reversed(branching_graph.edges)),
        )
        assert (
            summarize_intent(shuffled)["critical_path"]
            == summarize_intent(branching_graph)["critical_path"]
        )

    def test_linear_graph_is_the_whole_chain(self, sample_graph):
        result = summarize_intent(sample_graph)
        assert result["critical_path"] == ["n1", "n2", "n3", "n4"]

    def test_subgraph_scope_is_respected(self, branching_graph):
        result = summarize_intent(branching_graph, node_ids=["root", "alpha", "merge"])
        assert result["critical_path"] == ["root", "alpha", "merge"]

    def test_cycle_yields_empty_path_without_raising(self, cyclic_graph):
        # A chain entering a cycle can go round it any number of times, so there
        # is no longest chain to report. summarize_intent still describes the
        # graph; find_cycles is what reports the cycle.
        result = summarize_intent(cyclic_graph)
        assert result["critical_path"] == []

    def test_cycle_does_not_disturb_the_rest_of_the_summary(self, cyclic_graph):
        result = summarize_intent(cyclic_graph)
        assert result["node_count"] == 2
        assert result["domain"] == "ai"

    def test_cycle_anywhere_suppresses_the_path(self):
        # An acyclic stretch alongside a cycle is still not reportable: the
        # chain through it is not bounded once the cycle is reachable.
        g = Graph(
            name="partially_cyclic", version="1.0",
            nodes=[Node(id=i, type="LLMCall", params={}) for i in ("s", "a", "b", "t")],
            edges=[
                Edge(source="s", target="a", type="DATA"),
                Edge(source="a", target="b", type="DATA"),
                Edge(source="b", target="a", type="CONTROL"),  # cycle
                Edge(source="b", target="t", type="DATA"),
            ],
        )
        assert summarize_intent(g)["critical_path"] == []
