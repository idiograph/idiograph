# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import logging
from typing import Callable, Any

from idiograph.core.models import Graph, Node
from idiograph.core.query import topological_sort, find_cycles
from idiograph.core.logging_config import get_logger

_log = get_logger("executor")

# ── Handler Registry ─────────────────────────────────────────────────────────

HANDLERS: dict[str, Callable] = {}


def register_handler(node_type: str, fn: Callable) -> None:
    """Register an async handler function for a given node type."""
    HANDLERS[node_type] = fn
    _log.debug("Registered handler for node type '%s'.", node_type)


# ── Execution Engine ─────────────────────────────────────────────────────────

async def execute_graph(graph: Graph) -> dict[str, Any]:
    """
    Execute all nodes in topological order.
    Returns a results dict keyed by node ID.
    Each value is either the handler's output dict, or an error dict.
    Nodes whose upstream dependencies failed are skipped.
    """
    cycles = find_cycles(graph)
    if cycles:
        raise ValueError(f"Cannot execute graph with cycles: {cycles}")

    order = topological_sort(graph)
    results: dict[str, Any] = {}
    node_map = {n.id: n for n in graph.nodes}

    for node_id in order:
        node = node_map[node_id]
        upstream_edges = [e for e in graph.edges if e.target == node_id]

        # Check for failed or skipped upstream dependencies
        skip = False
        for edge in upstream_edges:
            upstream_result = results.get(edge.source, {})
            if upstream_result.get("status") in ("FAILED", "SKIPPED"):
                if edge.type == "CONTROL":
                    _log.warning(
                        "Skipping '%s' — CONTROL dependency '%s' did not succeed.",
                        node_id, edge.source,
                    )
                elif edge.type == "DATA":
                    _log.warning(
                        "Skipping '%s' — DATA dependency '%s' did not succeed.",
                        node_id, edge.source,
                    )
                skip = True
                break

        if skip:
            results[node_id] = {"status": "SKIPPED", "node_id": node_id}
            _update_node_status(node, "FAILED")
            continue

        # Collect inputs from all upstream nodes — edge type gates execution, not data flow
        inputs: dict[str, Any] = {}
        for edge in upstream_edges:
            upstream_output = results.get(edge.source, {})
            inputs[edge.source] = upstream_output

        results[node_id] = await _execute_node(node, inputs)

    return results


async def _execute_node(node: Node, inputs: dict[str, Any]) -> dict[str, Any]:
    """Look up and call the handler for a single node."""
    handler = HANDLERS.get(node.type)

    if handler is None:
        _log.error("No handler registered for node type '%s'.", node.type)
        _update_node_status(node, "FAILED")
        return {
            "status": "FAILED",
            "node_id": node.id,
            "error": f"No handler registered for node type '{node.type}'",
        }

    _log.info("Executing node '%s' (type: %s).", node.id, node.type)
    _update_node_status(node, "RUNNING")

    try:
        output = await handler(node.params, inputs)
        _update_node_status(node, "SUCCESS")
        _log.info("Node '%s' completed successfully.", node.id)
        return {**output, "status": "SUCCESS", "node_id": node.id}
    except Exception as e:
        _log.error("Node '%s' failed: %s", node.id, e)
        _update_node_status(node, "FAILED")
        return {
            "status": "FAILED",
            "node_id": node.id,
            "error": str(e),
        }


def _update_node_status(node: Node, status: str) -> None:
    """Mutate node status in place. The graph is the source of truth."""
    node.status = status
