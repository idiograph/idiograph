# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from collections.abc import Callable, Mapping
from typing import Any

from idiograph.core.logging_config import get_logger
from idiograph.core.models import Edge, Graph, Node
from idiograph.core.query import duplicate_node_ids, find_cycles, topological_sort

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


class InjectedOutputError(RuntimeError):
    """A run supplied an output for a node that is not injectable.

    Only a node declaring `input_ports == []` may have its output injected: an
    empty list is a declaration that the node reads nothing from upstream, so
    handing it a precomputed output replaces work that depended on nothing in
    the graph. A node with declared inputs (or one in the legacy `None` regime)
    has upstream bindings that injection would silently discard, so this fails
    closed rather than executing a graph whose dataflow is partly fiction.

    Detectable the moment the node is reached and before its handler runs, so it
    propagates out of `execute_graph` rather than becoming one node's FAILED
    result — the same side of the line as an unregistered type.
    """


class DuplicateNodeIdError(RuntimeError):
    """The graph declares one node id more than once.

    An id is the graph's only identity, and its readers resolve a reused one
    differently: `Graph.get_node` returns the FIRST node carrying it,
    `execute_graph`'s `node_map` keeps the LAST, and the networkx projection the
    traversal helpers build collapses both into a single node holding the union
    of their edges. Execution against that graph is not wrong in one identifiable
    place — every reader answers consistently with itself, and no two agree.

    Detectable without running anything, so it halts execution rather than being
    recorded as one node's failure. Propagates out of `execute_graph`.

    A named class rather than the bare `ValueError` the cycle check raises: the
    cycle raise predates this module's error family, and a caller that wants to
    tell a malformed graph from a malformed argument should not have to read the
    message to do it.
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
    outputs: Mapping[str, Mapping[str, Any]] | None = None,
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

    `outputs` maps node id -> that node's output dict and is run-owned in the
    same sense: a value this run already computed, handed in so the node is
    recorded rather than re-run. Only a node declaring `input_ports == []` is
    injectable, because only such a node reads nothing an injection could
    discard; anything else raises `InjectedOutputError`. Config-disable
    OUTRANKS injection — a node both disabled and named here is SKIPPED with
    `disabled_by_config` and its supplied output is dropped, since the graph
    says that node produces nothing on this run. Supply is NOT waived:
    `_check_resource_supply` runs over the whole graph before the loop, so an
    injected node still requires the resources it declares.

    Where a defect surfaces decides how it is reported. A defect detectable
    BEFORE any handler runs RAISES: a duplicate node id leaves the graph's own
    readers disagreeing about which node an id names, a cycle makes the order
    undefined, a node type with no registered handler names work that does not
    exist, and a node declaring a resource this run did not supply asks for a
    capability that is absent — none is a result the graph can carry. All four
    are checked over the WHOLE graph before the loop starts, never per-node as it
    reaches them: a defect that halts the run is reported before the run does
    any work, not after everything upstream of it has already been paid for.
    Anything that requires having run a handler becomes graph state instead: a
    raising handler, or an input binding that only fails once the upstream
    payload exists, becomes `{"status": "FAILED", ...}` and cascades to SKIPPED
    downstream.

    A node declaring `enabled_when` is neither: it is not a defect at all. The
    predicate is read before dispatch, and a node configured off is recorded as
    `{"status": "SKIPPED", "skip_reason": "disabled_by_config", ...}` while
    staying in the declared graph — an honest self-portrait rather than an
    absence. Because it never ran, its `Node.status` stays PENDING, it never
    asks for its declared resources, and it does NOT cascade: it forwards its
    `disabled_passthrough` ports and the downstream tail runs on.
    """
    # Identity first: every check below it reads the graph through a projection
    # or a map that a duplicate id has already collapsed, so they would be
    # answering about whichever node their own lookup happened to keep.
    duplicates = duplicate_node_ids(graph)
    if duplicates:
        raise DuplicateNodeIdError(
            f"Cannot execute graph with duplicate node id(s): "
            f"{', '.join(repr(node_id) for node_id in duplicates)}. An id is the "
            f"graph's only identity and its readers resolve a reused one "
            f"differently, so the run is refused rather than executed against "
            f"whichever node each reader finds."
        )

    cycles = find_cycles(graph)
    if cycles:
        raise ValueError(f"Cannot execute graph with cycles: {cycles}")

    _check_handler_registration(graph)

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
            if upstream_result.get("skip_reason") == DISABLED_BY_CONFIG:
                # Config-skip does not cascade. The upstream node is SKIPPED but
                # it is a declared node that was configured off, not one that
                # could not run: it forwarded its declared passthrough ports and
                # this node reads them like any other. Without this exemption the
                # whole tail dies behind a disabled node.
                continue
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

        if _is_disabled_by_config(node):
            # Before dispatch is ever considered — the node is not run, so it
            # never asks for its resources and never enters the PENDING →
            # RUNNING ladder. `Node.status` is therefore left exactly as it was:
            # a config-disabled node stays PENDING. The whole record of the
            # decision lives here in the results dict, distinguished from an
            # upstream-cascade skip by `skip_reason`, so the viewer can render a
            # declared node that was configured off. Inputs were collected above
            # because a disabled node must still forward them.
            _log.info(
                "Node '%s' disabled by config — param '%s' is falsy.",
                node_id, node.enabled_when,
            )
            results[node_id] = {
                **_forwarded_ports(node, inputs),
                "status": "SKIPPED",
                "node_id": node_id,
                "skip_reason": DISABLED_BY_CONFIG,
                "disabled_by": node.enabled_when,
            }
            continue

        if outputs is not None and node_id in outputs:
            # Below the config gate, deliberately: a disabled node is already
            # SKIPPED above, so config OUTRANKS injection and the supplied
            # output is discarded. Reaching here means the node would have been
            # dispatched, and the run has its output already.
            if node.input_ports != []:
                raise InjectedOutputError(
                    f"Node '{node_id}' was supplied an output but declares "
                    f"input_ports={node.input_ports!r}; only a node declaring "
                    f"an empty list is injectable."
                )
            results[node_id] = {
                **outputs[node_id],
                "status": "SUCCESS",
                "node_id": node_id,
                "injected": True,
            }
            _update_node_status(node, "SUCCESS")
            continue

        results[node_id] = await _execute_node(node, inputs, supplied)

    return results


#: The `skip_reason` a config-disabled node's result carries. Contract surface:
#: the cascade exemption below reads it, and so does the viewer, which renders a
#: declared node that was configured off rather than a node that failed.
DISABLED_BY_CONFIG = "disabled_by_config"


def _is_disabled_by_config(node: Node) -> bool:
    """Whether `node`'s declared config predicate gates it off for this run.

    The third instance of the declare-on-the-node fence, after `input_ports`
    (`_is_bound`) and `resources` (`_declares_resources`), and read the same
    way: the node declares, the executor obeys. `enabled_when` names one of the
    node's own params — a NAME, not an expression language — and this returns
    True when that param is falsy.

    Truthiness is ordinary Python truthiness of `params.get(name)`, so an absent
    key is None and therefore disabled, alongside 0, '', [], {} and False. The
    gate is configuration and lives in params, so it enters the content address:
    a disabled node's address already says the bytes it did not produce are not
    there.

    `enabled_when is None` is the legacy regime — the node always runs.
    """
    if node.enabled_when is None:
        return False
    return not node.params.get(node.enabled_when)


def _forwarded_ports(node: Node, inputs: dict[str, Any]) -> dict[str, Any]:
    """Build the ports a disabled node passes through, per `disabled_passthrough`.

    Config-skip does not cascade: a disabled node stays in the declared graph
    and forwards inputs onto its own output ports, so downstream wiring is
    untouched and one-edge-per-port still holds. This is the disable semantics
    of every node-graph tool — a disabled comp node passes B through.

    Absences are silent here and loud downstream, deliberately. A node with no
    mapping forwards nothing, and a mapping naming an input this run did not
    bind forwards nothing for that entry; either way the output port is simply
    not emitted, and a consumer bound to it fails with `PortBindingError` at its
    own binding step. The error names the real problem — this port was never
    emitted — rather than a None quietly flowing on.
    """
    if node.disabled_passthrough is None:
        return {}
    return {
        out_port: inputs[in_port]
        for out_port, in_port in node.disabled_passthrough.items()
        if in_port in inputs
    }


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


def _check_handler_registration(graph: Graph) -> None:
    """Verify every node type this run will dispatch has a handler, before
    anything executes.

    A registry miss is knowable without running a single handler — the graph
    names a type nothing was registered for — so it halts the whole execution
    rather than becoming one node's failure, the same rule that sends an
    unsupplied resource out of `execute_graph`. Checked once, over the whole
    graph, so a run that cannot finish never starts. Resolved per-node inside
    the loop instead, the identical defect would be reported only when the loop
    reached the node carrying it, so a graph whose LAST type is unregistered
    would run everything upstream first and pay for work the run was always
    going to discard.

    Every unregistered type is named at once rather than the first one reached.
    The whole graph is in hand here, so a caller with a registration gap learns
    its full size from one raise instead of one run per missing type.

    The config predicate is evaluated FIRST, here as in `_check_resource_supply`
    and in the loop. A node configured off is never dispatched, so it never asks
    for a handler and a run that disables it needs none registered for its type.
    Injection is NOT an exemption, on the same reading `_check_resource_supply`
    takes of it: a node whose output this run supplies is still a declared node
    of that type, and a type nothing implements is a defect in the graph rather
    than a fact about one invocation.
    """
    offenders: dict[str, list[str]] = {}
    for node in graph.nodes:
        if _is_disabled_by_config(node) or node.type in HANDLERS:
            continue
        offenders.setdefault(node.type, []).append(node.id)

    if offenders:
        named = ", ".join(
            f"{node_type!r} (nodes {', '.join(repr(i) for i in ids)})"
            for node_type, ids in sorted(offenders.items())
        )
        raise UnregisteredNodeTypeError(
            f"No handler registered for node type(s): {named}."
        )


def _check_resource_supply(graph: Graph, supplied: Mapping[str, Any]) -> None:
    """Verify every declared resource was supplied, before anything executes.

    A node naming a resource the run did not supply is knowable without running
    a single handler, so it halts the whole execution rather than becoming one
    node's FAILED result — the same rule that sends an unregistered node type
    out of `execute_graph`. Checked once, over the whole graph, so a run that
    cannot finish never starts.

    This cannot live in `validate_graph`/`validate_integrity`: those read the
    graph, and supply is a fact about the run.

    The config predicate is evaluated FIRST, here as in the loop. A node
    configured off is never dispatched, so it never asks for its resource and a
    run that disables it needs no supply for it. The raise/skip tension
    dissolves by sequencing rather than by exception: what survives is the case
    that matters — configured ON with nothing supplied still raises, without a
    single exemption, because that address claims bytes the run cannot produce.
    """
    for node in graph.nodes:
        if _is_disabled_by_config(node):
            continue
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
        # Not reachable from `execute_graph`'s loop: `_check_handler_registration`
        # ran over the whole graph before it, so a missing handler has already
        # raised. Kept anyway, because `HANDLERS` is a live global that the
        # preflight can only read once — a handler that mutates the registry
        # mid-run can unregister a type the preflight saw — and because this
        # function is callable on its own. Outside the handler try/except below,
        # and deliberately: this propagates out of execute_graph rather than
        # becoming a FAILED result.
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
    # The executor fence: converts ANY handler failure into a FAILED result.
    # Catching anything narrower reintroduces crash-through (ruled, IDG-098).
    except Exception as e:  # noqa: BLE001
        _log.error("Node '%s' failed: %s", node.id, e)
        _update_node_status(node, "FAILED")
        return {
            "status": "FAILED",
            "node_id": node.id,
            "error": str(e),
            # Additive alongside the `error` string, which stays the reported
            # surface every existing reader uses. The OBJECT is carried so a
            # caller that halts on a FAILED result can re-raise with
            # `raise ... from`, preserving the original type and traceback that
            # `str(e)` throws away.
            "exception": e,
        }


def _update_node_status(node: Node, status: str) -> None:
    """Mutate node status in place. The graph is the source of truth."""
    node.status = status
