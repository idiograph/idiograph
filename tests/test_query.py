# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import pytest
from idiograph.core.query import (
    get_downstream,
    get_upstream,
    topological_sort,
    find_cycles,
    validate_integrity,
    summarize_intent,
)
from idiograph.core.models import Edge, Graph, Node


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
