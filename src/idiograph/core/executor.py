# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from typing import Callable, Any

from idiograph.core.models import Edge, Graph, Node
from idiograph.core.query import topological_sort, find_cycles
from idiograph.core.logging_config import get_logger

_log = get_logger("executor")


class PortBindingError(RuntimeError):
    """A port-declared edge could not be bound at execution time.

    Raised when the upstream node did not emit the `from_port` key the edge
    reads. Binding is explicit: a missing port is an error, never a silent skip
    and never a fallback to the legacy whole-payload gather.
    """


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

        try:
            inputs = _collect_inputs(node, upstream_edges, results)
        except PortBindingError as exc:
            _log.error("Node '%s' input binding failed: %s", node_id, exc)
            _update_node_status(node, "FAILED")
            results[node_id] = {
                "status": "FAILED",
                "node_id": node_id,
                "error": str(exc),
            }
            continue

        results[node_id] = await _execute_node(node, inputs)

    return results


def _is_bound(node: Node) -> bool:
    """Whether `node` is on the bound side of the migration fence.

    The fence is a one-way ratchet: a node that declares `input_ports` gets its
    `inputs` built solely from port-declared incoming edges. A node that
    declares nothing stays in the legacy regime. An empty list is a
    declaration — the node is bound and accepts no inputs.
    """
    return node.input_ports is not None


def _collect_inputs(
    node: Node,
    upstream_edges: list[Edge],
    results: dict[str, Any],
) -> dict[str, Any]:
    """Build the `inputs` mapping a handler receives.

    Legacy nodes get every upstream payload keyed by source node id — edge type
    gates execution, not data flow, so CONTROL edges carry data here as they
    always have.

    Bound nodes get `inputs[to_port] = upstream_output[from_port]` for each
    port-declared incoming edge, and nothing else: keying by `to_port` is what
    makes two edges from one source into distinct ports expressible. Edges that
    declare no ports contribute no data to a bound node — `validate_integrity`
    reports them as dataflow errors rather than the executor guessing.
    """
    if not _is_bound(node):
        inputs: dict[str, Any] = {}
        for edge in upstream_edges:
            upstream_output = results.get(edge.source, {})
            inputs[edge.source] = upstream_output
        return inputs

    inputs = {}
    for edge in upstream_edges:
        if edge.from_port is None or edge.to_port is None:
            continue
        upstream_output = results.get(edge.source, {})
        if edge.from_port not in upstream_output:
            raise PortBindingError(
                f"Node '{node.id}': upstream '{edge.source}' did not emit "
                f"declared output port '{edge.from_port}' "
                f"(bound to input port '{edge.to_port}')."
            )
        inputs[edge.to_port] = upstream_output[edge.from_port]
    return inputs


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
