# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from idiograph.core.models import Graph, Node, Edge
from idiograph.core.logging_config import get_logger

_log = get_logger("graph")


def get_node(graph: Graph, node_id: str) -> Node | None:
    return graph.get_node(node_id)


def get_edges_from(graph: Graph, node_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.source == node_id]


def summarize(graph: Graph) -> dict:
    status_counts: dict[str, int] = {}
    for node in graph.nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1

    node_types: dict[str, int] = {}
    for node in graph.nodes:
        node_types[node.type] = node_types.get(node.type, 0) + 1

    return {
        "pipeline": graph.name,
        "version": graph.version,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "status_breakdown": status_counts,
        "node_types": node_types,
    }


def load_graph(data: dict) -> Graph:
    """Construct and validate a Graph from a raw dict (e.g. parsed JSON)."""
    graph = Graph.model_validate(data)
    _log.info("Loaded graph '%s' — %d nodes, %d edges.", graph.name, len(graph.nodes), len(graph.edges))
    return graph
