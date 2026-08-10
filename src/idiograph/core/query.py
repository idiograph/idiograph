# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import networkx as nx
from idiograph.core.models import Graph


# ── Internal helper ──────────────────────────────────────────────────────────

def _build_nx_graph(graph: Graph) -> nx.DiGraph:
    """Convert a idiograph Graph into a networkx DiGraph for analysis."""
    dg = nx.DiGraph()
    for node in graph.nodes:
        dg.add_node(node.id)
    for edge in graph.edges:
        dg.add_edge(edge.source, edge.target, type=edge.type)
    return dg


# ── Traversal ────────────────────────────────────────────────────────────────

def get_downstream(graph: Graph, node_id: str) -> list[str]:
    """Return all node IDs reachable downstream from node_id (excludes node_id itself)."""
    dg = _build_nx_graph(graph)
    if node_id not in dg:
        return []
    return list(nx.descendants(dg, node_id))


def get_upstream(graph: Graph, node_id: str) -> list[str]:
    """Return all node IDs that are ancestors of node_id (excludes node_id itself)."""
    dg = _build_nx_graph(graph)
    if node_id not in dg:
        return []
    return list(nx.ancestors(dg, node_id))


def topological_sort(graph: Graph) -> list[str]:
    """
    Return node IDs in topological order (safe execution order).
    Raises ValueError if the graph contains a cycle.
    """
    dg = _build_nx_graph(graph)
    try:
        return list(nx.topological_sort(dg))
    except nx.NetworkXUnfeasible:
        raise ValueError("Graph contains a cycle — topological sort is not possible.")


def find_cycles(graph: Graph) -> list[list[str]]:
    """
    Return a list of cycles found in the graph.
    Each cycle is a list of node IDs. Returns an empty list if the graph is acyclic.
    """
    dg = _build_nx_graph(graph)
    return list(nx.simple_cycles(dg))

# ── Integrity ────────────────────────────────────────────────────────────────

#: The bookkeeping keys `execute_graph` stamps onto every result dict, AFTER
#: splatting the handler's output into it. A port sharing one of these names is
#: therefore unreadable: the stamp wins, and the consumer bound to that port
#: silently reads the executor's bookkeeping value instead of the payload. Not a
#: run-time failure — a wrong value, which is worse — so it is rejected at
#: declaration time, where the name is visible without executing anything.
RESERVED_PORT_NAMES = frozenset({"status", "node_id", "injected"})


def _dataflow_errors(graph: Graph) -> list[str]:
    """
    Check that every port-declared edge names ports its endpoints actually declare.

    This is what makes a graph self-sufficient: dataflow is verifiable from the
    declarations alone, without reading handler source.

    The migration fence is a one-way ratchet. A node that declares `input_ports`
    is BOUND — the executor builds its inputs solely from port-declared incoming
    edges — so every incoming edge must carry ports, and every upstream feeding
    it must declare the output port being read. Nodes that declare nothing stay
    in the legacy regime and are not checked here.

    A bound input port takes exactly one incoming edge — floor and ceiling. Two
    edges binding the same `to_port` have no declared precedence between them,
    so the graph does not say which value the port carries. A declared port that
    no edge binds is never fed at all: every declared input port is required,
    because declaring it is what says the handler reads it. Both are defects in
    the wiring, reported here rather than silently resolved at run time.

    Ports are untyped: `port_type` and `Graph.type_registry` are not consulted.

    A port named for one of the executor's bookkeeping keys
    (`RESERVED_PORT_NAMES`) is rejected outright, independently of any edge —
    the collision is a property of the DECLARATION, so a port that no edge
    binds yet is still reported.
    """
    node_map = {node.id: node for node in graph.nodes}
    errors: list[str] = []

    for node in graph.nodes:
        for side, declared in (
            ("input", node.input_ports),
            ("output", node.output_ports),
        ):
            for port in declared or ():
                if port.name in RESERVED_PORT_NAMES:
                    errors.append(
                        f"Node '{node.id}': {side} port '{port.name}' uses the "
                        f"reserved word '{port.name}' — the executor stamps it "
                        f"onto every result dict, so the port's value would be "
                        f"silently overwritten. Reserved: "
                        f"{sorted(RESERVED_PORT_NAMES)}."
                    )

    # (target id, to_port) → the `source.from_port` of every edge claiming it.
    port_claims: dict[tuple[str, str], list[str]] = {}
    # Targets with an already-reported defect on an incoming edge. Such a node
    # is not additionally told its ports are unfed: a malformed or dangling edge
    # says nothing about which port it meant to feed, so the unfed report would
    # be a consequence of the reported defect rather than a second defect.
    faulted_targets: set[str] = set()

    for edge in graph.edges:
        source = node_map.get(edge.source)
        target = node_map.get(edge.target)
        if source is None or target is None:
            faulted_targets.add(edge.target)
            continue  # already reported by the referential check

        label = f"Edge {edge.source} → {edge.target}"
        has_from = edge.from_port is not None
        has_to = edge.to_port is not None

        if has_from != has_to:
            present, absent = ("from_port", "to_port") if has_from else ("to_port", "from_port")
            errors.append(
                f"{label}: declares {present} but not {absent} — an edge is either "
                f"fully port-declared or not port-declared at all."
            )
            faulted_targets.add(edge.target)
            continue

        target_bound = target.input_ports is not None

        if not has_from:
            if target_bound:
                errors.append(
                    f"{label}: target '{edge.target}' declares input_ports, so every "
                    f"incoming edge must declare from_port and to_port."
                )
                faulted_targets.add(edge.target)
            continue

        if target_bound:
            declared_inputs = {p.name for p in target.input_ports}
            if edge.to_port not in declared_inputs:
                errors.append(
                    f"{label}: to_port '{edge.to_port}' is not a declared input port "
                    f"of '{edge.target}' (declared: {sorted(declared_inputs)})."
                )
                faulted_targets.add(edge.target)
            else:
                claim = f"{edge.source}.{edge.from_port}"
                port_claims.setdefault((edge.target, edge.to_port), []).append(claim)

        if source.output_ports is None:
            if target_bound:
                errors.append(
                    f"{label}: source '{edge.source}' declares no output_ports, but "
                    f"'{edge.target}' is bound and reads from_port '{edge.from_port}'."
                )
                faulted_targets.add(edge.target)
        else:
            declared_outputs = {p.name for p in source.output_ports}
            if edge.from_port not in declared_outputs:
                errors.append(
                    f"{label}: from_port '{edge.from_port}' is not a declared output "
                    f"port of '{edge.source}' (declared: {sorted(declared_outputs)})."
                )
                faulted_targets.add(edge.target)

    for (target_id, to_port), claims in port_claims.items():
        if len(claims) > 1:
            errors.append(
                f"Node '{target_id}': input port '{to_port}' is bound by "
                f"{len(claims)} edges (competing: {sorted(claims)}) — a bound "
                f"input port takes exactly one incoming edge."
            )

    # The floor, the complement of `port_claims`: a declared input port no edge
    # binds. This needs a pass over NODES — the edge loop above cannot see a
    # port that has no edge. An empty `input_ports` declares "accepts no
    # inputs" and is trivially satisfied; legacy nodes stay out of the regime.
    for node in graph.nodes:
        if node.input_ports is None or node.id in faulted_targets:
            continue
        for port in node.input_ports:
            if (node.id, port.name) not in port_claims:
                errors.append(
                    f"Node '{node.id}': input port '{port.name}' is bound by no "
                    f"incoming edge — every declared input port of a bound node "
                    f"must be fed."
                )

    return errors


def validate_integrity(graph: Graph) -> dict:
    """
    Check referential integrity (every edge references node IDs that exist) and
    dataflow integrity (every port-declared edge names ports its endpoints declare).
    Returns a dict with 'valid' (bool) and 'errors' (list of problem descriptions).
    """
    from idiograph.core.logging_config import get_logger
    _log = get_logger("query")

    node_ids = {node.id for node in graph.nodes}
    errors = []

    for edge in graph.edges:
        if edge.source not in node_ids:
            errors.append(f"Edge {edge.source} → {edge.target}: source '{edge.source}' does not exist.")
        if edge.target not in node_ids:
            errors.append(f"Edge {edge.source} → {edge.target}: target '{edge.target}' does not exist.")

    errors.extend(_dataflow_errors(graph))

    if errors:
        _log.warning("Integrity check failed for '%s': %d error(s).", graph.name, len(errors))
    else:
        _log.debug("Integrity check passed for '%s'.", graph.name)
        
    return {"valid": len(errors) == 0, "errors": errors}


# ── Intent Summary ───────────────────────────────────────────────────────────

def _outranks(chain: list[str], incumbent: list[str]) -> bool:
    """
    True if `chain` beats `incumbent`: longer, or the same length and
    lexicographically smaller. This is the whole tie-break rule, in one place
    because `_longest_chain` applies it twice.
    """
    if len(chain) != len(incumbent):
        return len(chain) > len(incumbent)
    return chain < incumbent


def _longest_chain(dg: nx.DiGraph) -> list[str]:
    """
    Return the longest chain of nodes in `dg`, measured in node count.

    Longest, not shortest. A branching graph can carry a shortcut edge that
    reaches a sink in a couple of hops while the work the pipeline exists to do
    runs the long way round; the shortest route between the same endpoints
    understates how deep the pipeline is, and is what a per-(source, sink)
    `nx.shortest_path` scan selects.

    A CYCLIC graph has no longest chain — a chain that enters a cycle can go
    round it any number of times, so there is no maximum to report. This returns
    [] there rather than raising: `summarize_intent` describes a graph, and has
    to stay callable on a malformed one to describe it. `find_cycles` is what
    reports the cycle itself. (`nx.dag_longest_path` raises `NetworkXUnfeasible`
    here, which is why it is not called directly.)

    Ties are broken toward the lexicographically smallest node-id sequence, so
    the answer is fixed by the graph alone — not by node insertion order, dict
    iteration order, or the networkx version.
    """
    if not nx.is_directed_acyclic_graph(dg):
        return []

    # Longest chain ending at each node. Topological order is what makes the
    # single pass sufficient: every predecessor is final before the node that
    # reads it.
    best: dict[str, list[str]] = {}
    for node in nx.topological_sort(dg):
        prefix: list[str] = []
        for pred in dg.predecessors(node):
            if _outranks(best[pred], prefix):
                prefix = best[pred]
        best[node] = [*prefix, node]

    longest: list[str] = []
    for chain in best.values():
        if _outranks(chain, longest):
            longest = chain
    return longest


def summarize_intent(graph: Graph, node_ids: list[str] | None = None) -> dict:
    """
    Return a structured semantic description of the graph or a subgraph.
    Intended for agent consumption — answers 'what does this do and where might it fail?'
    Purely algorithmic: no LLM calls. Deterministic output for a given graph state.
    """
    # Scope to subgraph if node_ids provided, otherwise use full graph
    if node_ids is not None:
        nodes = [n for n in graph.nodes if n.id in node_ids]
        scoped_ids = {n.id for n in nodes}
        edges = [e for e in graph.edges if e.source in scoped_ids and e.target in scoped_ids]
    else:
        nodes = graph.nodes
        edges = graph.edges

    if not nodes:
        return {"error": "No nodes found in scope."}

    # Node type inventory
    type_counts: dict[str, int] = {}
    for node in nodes:
        type_counts[node.type] = type_counts.get(node.type, 0) + 1

    # Status inventory
    status_counts: dict[str, int] = {}
    for node in nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1

    # Edge type breakdown
    edge_type_counts: dict[str, int] = {}
    for edge in edges:
        edge_type_counts[edge.type] = edge_type_counts.get(edge.type, 0) + 1

    # Domain inference — what kind of work is this graph doing?
    vfx_types = {"LoadAsset", "Render", "Simulate", "ApplyShader", "Cache",
                 "Composite", "ShaderValidate", "RenderComparison", "LookApproval", "MaterialAssign"}
    ai_types  = {"LLMCall", "VectorRetrieve", "ToolInvoke", "Evaluator",
                 "Router", "MemoryUpdate", "HumanInLoop"}

    node_type_set = set(type_counts.keys())
    has_vfx = bool(node_type_set & vfx_types)
    has_ai  = bool(node_type_set & ai_types)

    if has_vfx and has_ai:
        domain = "hybrid"
    elif has_vfx:
        domain = "vfx"
    elif has_ai:
        domain = "ai"
    else:
        domain = "unknown"

    # Critical path — longest chain by node count; [] if the scope is cyclic
    dg = _build_nx_graph(Graph(name=graph.name, version=graph.version, nodes=nodes, edges=edges))
    critical_path = _longest_chain(dg)

    # Failure points — CONTROL edges are gates; their source nodes are chokepoints
    control_gates = [e.source for e in edges if e.type == "CONTROL"]

    # Blocked nodes — anything currently FAILED
    failed_nodes = [n.id for n in nodes if n.status == "FAILED"]

    return {
        "graph": graph.name,
        "scope": "full" if node_ids is None else "subgraph",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "domain": domain,
        "node_types": type_counts,
        "status": status_counts,
        "edge_types": edge_type_counts,
        "critical_path": critical_path,
        "control_gates": control_gates,
        "failed_nodes": failed_nodes,
    }
