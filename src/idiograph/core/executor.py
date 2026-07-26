# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from collections.abc import Mapping
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


class UnregisteredNodeTypeError(RuntimeError):
    """A node's `type` has no handler in `HANDLERS`.

    A registry miss is detectable without running anything — the graph names a
    type nothing was registered for — so it halts execution rather than being
    recorded as one node's failure. Propagates out of `execute_graph`.
    """


class UnsuppliedResourceError(RuntimeError):
    """A node declared a resource the run did not supply.

    Resources are declared on the node and handed in at execute time; the value
    belongs to the run, never to the graph, and never enters a content address.
    A missing one is detectable without running anything — the graph asks for a
    capability this run does not have — so it halts execution rather than being
    recorded as one node's failure. Propagates out of `execute_graph`.

    `validate_graph`/`validate_integrity` cannot catch this: supply is a fact
    about the run, not about the graph.
    """


# ── Handler Registry ─────────────────────────────────────────────────────────

HANDLERS: dict[str, Callable] = {}


def register_handler(node_type: str, fn: Callable) -> None:
    """Register an async handler function for a given node type."""
    HANDLERS[node_type] = fn
    _log.debug("Registered handler for node type '%s'.", node_type)


# ── Execution Engine ─────────────────────────────────────────────────────────

async def execute_graph(
    graph: Graph,
    resources: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute all nodes in topological order.
    Returns a results dict keyed by node ID.
    Each value is either the handler's output dict, or an error dict.
    Nodes whose upstream dependencies failed are skipped.

    `resources` carries the run's named capabilities — network clients,
    credentials, anything the run owns rather than the graph. It is additive:
    callers that supply nothing keep the exact single-argument call they had,
    and only nodes that declare `resources` ever see any of it.

    Where a defect surfaces decides how it is reported. A defect detectable
    BEFORE any handler runs RAISES: a cycle makes the order undefined, a node
    type with no registered handler names work that does not exist, and a node
    declaring a resource this run did not supply asks for a capability that is
    absent — none is a result the graph can carry. Anything that requires
    having run a handler becomes graph state instead: a raising handler, or an
    input binding that only fails once the upstream payload exists, becomes
    `{"status": "FAILED", ...}` and cascades to SKIPPED downstream.
    """
    cycles = find_cycles(graph)
    if cycles:
        raise ValueError(f"Cannot execute graph with cycles: {cycles}")

    supplied: Mapping[str, Any] = {} if resources is None else resources
    _check_resource_supply(graph, supplied)

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

        results[node_id] = await _execute_node(node, inputs, supplied)

    return results


def _declares_resources(node: Node) -> bool:
    """Whether `node` is on the declaring side of the resource fence.

    The deliberate twin of `_is_bound`, and the same one-way ratchet: a node
    that declares `resources` has its handler called with an additional
    keyword-only `resources` mapping. A node that declares nothing stays in the
    legacy regime and is called with two positional arguments. An empty list is
    a declaration — the node declares and receives an empty mapping.

    What differs from `_is_bound` is only where the value comes from: ports
    pull from upstream nodes, resources pull from the run.
    """
    return node.resources is not None


def _check_resource_supply(graph: Graph, supplied: Mapping[str, Any]) -> None:
    """Verify every declared resource was supplied, before anything executes.

    A node naming a resource the run did not supply is knowable without running
    a single handler, so it halts the whole execution rather than becoming one
    node's FAILED result — the same rule that sends an unregistered node type
    out of `execute_graph`. Checked once, over the whole graph, so a run that
    cannot finish never starts.

    This cannot live in `validate_graph`/`validate_integrity`: those read the
    graph, and supply is a fact about the run.
    """
    for node in graph.nodes:
        if not _declares_resources(node):
            continue
        missing = [name for name in node.resources if name not in supplied]
        if missing:
            raise UnsuppliedResourceError(
                f"Node '{node.id}' declares resource(s) "
                f"{', '.join(repr(name) for name in missing)} "
                f"that the run did not supply."
            )


def _collect_resources(node: Node, supplied: Mapping[str, Any]) -> dict[str, Any]:
    """Build the `resources` mapping a declaring node's handler receives.

    Narrowed to the names the node declared and nothing else — never the whole
    supplied mapping. The declaration on the node is therefore the complete
    truth about what that handler can reach, readable without opening the
    handler and without knowing what else the run happened to carry.

    Presence is already guaranteed: `_check_resource_supply` ran before the
    execution loop, so every declared name is a key here.
    """
    return {name: supplied[name] for name in node.resources}


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


async def _execute_node(
    node: Node,
    inputs: dict[str, Any],
    supplied: Mapping[str, Any],
) -> dict[str, Any]:
    """Look up and call the handler for a single node.

    Dispatch shape is decided by the node, not the handler: a declaring node's
    handler takes an extra keyword-only `resources`, a legacy node's handler is
    called with two positional arguments exactly as it always was.
    """
    handler = HANDLERS.get(node.type)

    if handler is None:
        # Outside the handler try/except below, and deliberately: this
        # propagates out of execute_graph rather than becoming a FAILED result.
        raise UnregisteredNodeTypeError(
            f"Node '{node.id}': no handler registered for node type "
            f"'{node.type}'."
        )

    _log.info("Executing node '%s' (type: %s).", node.id, node.type)
    _update_node_status(node, "RUNNING")

    try:
        if _declares_resources(node):
            output = await handler(
                node.params, inputs, resources=_collect_resources(node, supplied)
            )
        else:
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
