# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import pytest
from pydantic import ValidationError
from idiograph.core.graph import get_node, get_edges_from, summarize, load_graph


class TestGetNode:
    def test_returns_correct_node(self, sample_graph):
        node = get_node(sample_graph, "n3")
        assert node is not None
        assert node.type == "ShaderValidate"

    def test_returns_none_for_missing(self, sample_graph):
        assert get_node(sample_graph, "nonexistent") is None


class TestGetEdgesFrom:
    def test_returns_outgoing_edges(self, sample_graph):
        edges = get_edges_from(sample_graph, "n2")
        assert len(edges) == 1
        assert edges[0].target == "n3"

    def test_returns_empty_for_sink_node(self, sample_graph):
        # n4 is the terminal node — it has no outgoing edges
        edges = get_edges_from(sample_graph, "n4")
        assert edges == []


class TestSummarize:
    def test_counts_are_correct(self, sample_graph):
        result = summarize(sample_graph)
        assert result["node_count"] == 4
        assert result["edge_count"] == 3
        assert result["status_breakdown"] == {"PENDING": 4}

    def test_pipeline_name_preserved(self, sample_graph):
        result = summarize(sample_graph)
        assert result["pipeline"] == "test_pipeline"


class TestLoadGraph:
    def test_round_trips_correctly(self, sample_graph):
        data = sample_graph.model_dump()
        restored = load_graph(data)
        assert restored.name == sample_graph.name
        assert len(restored.nodes) == len(sample_graph.nodes)
        assert len(restored.edges) == len(sample_graph.edges)

    def test_invalid_data_raises(self):
        with pytest.raises(ValidationError):
            load_graph({"version": "1.0"})  # name is missing
