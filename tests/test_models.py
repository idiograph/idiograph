# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import pytest
from pydantic import ValidationError
from idiograph.core.models import Node, Edge, Graph


class TestNode:
    def test_defaults(self):
        node = Node(id="n1", type="Render")
        assert node.status == "PENDING"
        assert node.params == {}

    def test_explicit_status(self):
        node = Node(id="n1", type="Render", status="SUCCESS")
        assert node.status == "SUCCESS"

    def test_params_are_independent(self):
        # Two nodes with default params must not share the same dict object.
        # This would silently corrupt state if default_factory=dict were missing.
        a = Node(id="a", type="Render")
        b = Node(id="b", type="Render")
        a.params["key"] = "value"
        assert "key" not in b.params

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            Node(id="n1")  # type is required


class TestEdge:
    def test_defaults_to_data(self):
        edge = Edge(source="a", target="b")
        assert edge.type == "DATA"

    def test_accepts_extensible_type(self):
        # AMD-003: Edge type must accept arbitrary strings, not just DATA/CONTROL.
        edge = Edge(source="a", target="b", type="MODULATES")
        assert edge.type == "MODULATES"

    def test_control_type(self):
        edge = Edge(source="a", target="b", type="CONTROL")
        assert edge.type == "CONTROL"


class TestGraph:
    def test_construction(self, sample_graph):
        assert sample_graph.name == "test_pipeline"
        assert len(sample_graph.nodes) == 4
        assert len(sample_graph.edges) == 3

    def test_get_node_found(self, sample_graph):
        node = sample_graph.get_node("n2")
        assert node is not None
        assert node.type == "ApplyShader"

    def test_get_node_missing_returns_none(self, sample_graph):
        # Agents querying non-existent nodes must get None, not an exception.
        assert sample_graph.get_node("ghost") is None

    def test_empty_graph_is_valid(self):
        g = Graph(name="empty", version="0.1")
        assert g.nodes == []
        assert g.edges == []

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            Graph(version="1.0")
