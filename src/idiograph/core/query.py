# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import networkx as nx
from idiograph.core.models import Graph, Node


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

def validate_integrity(graph: Graph) -> dict:
    """
    Check that every edge references node IDs that actually exist in the graph.
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

    if errors:
        _log.warning("Integrity check failed for '%s': %d error(s).", graph.name, len(errors))
    else:
        _log.debug("Integrity check passed for '%s'.", graph.name)
        
    return {"valid": len(errors) == 0, "errors": errors}


# ── Intent Summary ───────────────────────────────────────────────────────────

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

    # Critical path — longest chain by node count
    dg = _build_nx_graph(Graph(name=graph.name, version=graph.version, nodes=nodes, edges=edges))
    sources = [n for n in dg.nodes if dg.in_degree(n) == 0]
    sinks   = [n for n in dg.nodes if dg.out_degree(n) == 0]

    critical_path: list[str] = []
    for source in sources:
        for sink in sinks:
            try:
                path = nx.shortest_path(dg, source, sink)
                if len(path) > len(critical_path):
                    critical_path = path
            except nx.NetworkXNoPath:
                continue

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
