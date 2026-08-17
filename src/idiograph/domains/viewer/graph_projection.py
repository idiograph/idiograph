# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Declared-graph projection — the pipeline itself, in the renderer's contract.

:func:`project_graph` reads a declared :class:`~idiograph.core.models.Graph` and
emits the SAME three-key ``{meta, nodes, edges}`` contract that
:func:`~idiograph.domains.viewer.projection.project_depth_provenance` emits for
the artifact. One renderer consumes both: Slice 1 draws what the pipeline
PRODUCED, this draws what the pipeline IS. The sameness of the contract is the
deliverable — a second renderer would satisfy the shape and lose the point.

Pure and deterministic: a function of the ``Graph`` and nothing else. No I/O, no
clock, no environment read, no registry, no handler lookup. Every node arrives
at the renderer with its ``(x, y)`` already computed, so equal Graphs emit
byte-identical JSON under ``json.dumps(..., sort_keys=True)``.

Layout — LAYERED DAG (see ASSUMPTIONS in the run summary):

* Each node's RANK is its longest-path depth from a source. Longest, not
  shortest: a shortest-path rank lets a node sit level with its own producer
  when a shortcut edge exists, and the dataflow then reads backwards. Under
  longest-path ranking every edge advances at least one rank, which is what
  makes the picture legible left to right.
* Ranks lay out in execution order along X; nodes within a rank stack along Y,
  the rank's group centred on the canvas so the trunk reads as a spine.
* Within-rank ORDER is one deterministic barycentre sweep in rank order: a node
  sits at the mean Y of its already-placed predecessors, ties broken by node id.
  This is Sugiyama's ordering pass with a single forward iteration — enough to
  untangle eleven nodes, and fixed by the graph alone rather than by declaration
  order.
* EDGES ANCHOR ON PORTS, not on node centres. Four edges leave ``resolve`` and
  four leave ``assemble``; two of the latter run to the same target (``nodes``
  and ``cites``, both ``assemble -> clean``). Collapsing an edge to its node
  pair would draw those two as one line and silently under-report the wiring, so
  every edge is emitted with its own endpoints, taken from the declared
  ``from_port``/``to_port`` anchors on the source's right and the target's left.

NODE SIZE LIVES IN ``meta``, ONE VALUE FOR EVERY NODE, and is deliberately not a
per-node field. The LLM node (``AnnotateRelationships``) is drawn at the same
weight and shape as every other node: the claim this view exists to make is that
the model is a bounded, auditable node inside a deterministic graph rather than
its orchestrator, and that claim is made by the node being unremarkable. A
per-node size field is the affordance that would let a later editor break it
without noticing, so the contract does not offer one.

DECLARATION VS EXECUTION — read this before extending the module. This view
renders what the Graph DECLARES: the ports a node says it binds, the resources
it says it needs, the predicate it says gates it. Whether the EXECUTOR honours
those declarations is a separate and currently UNRULED question — the executor
gathers data over all upstream edges regardless of edge type, so a declaration
drawn here is not by itself evidence of the run-time behaviour. Nothing in this
module resolves that question, and nothing emitted here should be read as taking
a side on it.

``Node.status`` is NOT emitted, on purpose. It is run state that the executor
mutates in place, and this is a graph DEFINITION — reporting it would describe
whichever run last touched the object rather than the pipeline.
"""

from idiograph.core.models import Graph
from idiograph.core.query import get_downstream, get_upstream, topological_sort

# Fixed coordinate precision — rounding makes the emitted JSON byte-identical
# across platforms/interpreters (float formatting is otherwise not portable).
# Matches the sibling depth/provenance projection's precision so one renderer
# never sees two coordinate resolutions.
_COORD_PRECISION = 6

# Normalized drawing margins (fraction of the unit square kept clear of glyphs).
_MARGIN_X = 0.06
_MARGIN_Y = 0.05

# Fraction of a rank's column width / row height the node box occupies. The
# remainder is the gutter edges are routed through, so these are the ratio of
# ink to routing space, not arbitrary padding. The width is set by the longest
# label a stage type produces (`AnnotateRelationships`, 21 characters) rather
# than by taste: a box narrower than its own name is unreadable, and the
# renderer's fallback for that case is to shrink the type down.
_NODE_W_FRACTION = 0.68
_NODE_H_FRACTION = 0.46

# Fraction of the node box height across which its ports are spread. Ports sit
# inside the box rather than on its corners, so an edge always lands on ink.
_PORT_SPREAD = 0.74

#: Stated in the rendered output so a reader is not left to infer that the
#: picture is a claim about run-time behaviour. It reports that the question is
#: open; it does not answer it.
DECLARATION_CAVEAT = (
    "This view renders what the graph DECLARES — the ports each node binds, the "
    "run-supplied resources it names, and the config predicate that gates it. "
    "Whether the executor honours those declarations is a separate question and "
    "is currently unruled: the executor gathers data over all upstream edges "
    "regardless of edge type. Read this as the declaration, not as a trace of a "
    "run."
)

#: Why every edge is drawn from a port rather than from a node centre.
PORT_IDENTITY_CAVEAT = (
    "An edge is identified by (source, from_port, target, to_port), not by its "
    "node pair: two distinct edges can run between the same two nodes on "
    "different ports. Each edge is drawn between its own port anchors so the "
    "count on screen matches the count in the declaration."
)


def _round(value: float) -> float:
    return round(value, _COORD_PRECISION)


def _predecessors(graph: Graph) -> dict[str, list[str]]:
    """Node id → its distinct source ids, sorted.

    Distinct: ``assemble -> clean`` is declared twice (on ``nodes`` and on
    ``cites``), and for ranking that is one dependency, not two. Sorted so the
    ranking below is a fact about the graph rather than about edge order.
    """
    preds: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        if edge.target in preds and edge.source in preds:
            preds[edge.target].add(edge.source)
    return {node_id: sorted(sources) for node_id, sources in preds.items()}


def _outranks(chain: list[str], incumbent: list[str]) -> bool:
    """True if ``chain`` beats ``incumbent``: longer, else lexicographically smaller.

    The same rule ``core.query._longest_chain`` applies, so the chain reported
    here and the one that function reports are the same chain.
    """
    if len(chain) != len(incumbent):
        return len(chain) > len(incumbent)
    return chain < incumbent


def _longest_chains(graph: Graph) -> dict[str, list[str]]:
    """Node id → the longest chain of node ids ENDING at that node.

    This is the recurrence ``core.query._longest_chain`` runs, kept here because
    that function is private and returns only the winning chain, while the
    layout needs the per-node length as its rank. The two are pinned to
    agreement by ``tests/domains/viewer/test_graph_projection.py`` rather than
    left to drift.

    Topological order is what makes one pass sufficient: every predecessor is
    final before the node that reads it. A cyclic graph has no such order and no
    longest chain, so ``topological_sort`` raises and this propagates — a cycle
    has no layered layout to draw, and inventing one would misdescribe it.
    """
    preds = _predecessors(graph)
    best: dict[str, list[str]] = {}
    for node_id in topological_sort(graph):
        prefix: list[str] = []
        for pred in preds.get(node_id, ()):
            if _outranks(best[pred], prefix):
                prefix = best[pred]
        best[node_id] = [*prefix, node_id]
    return best


def _order_within_ranks(
    ranks: dict[int, list[str]],
    preds: dict[str, list[str]],
    y_of: dict[str, float],
    row_h: float,
) -> None:
    """Order each rank by predecessor barycentre and fill ``y_of`` in place.

    One forward sweep, ranks visited shallowest first, so every predecessor
    already has a Y when its consumer is placed. A node with no placed
    predecessor takes the centre line, which leaves ties to the node id — the
    ordering is therefore a function of the graph, never of declaration order.

    Each rank's nodes are centred as a GROUP on the canvas mid-line rather than
    spread to fill it, so the single-node ranks line up into a spine and the
    wide rank fans symmetrically around it.
    """
    for rank in sorted(ranks):
        members = ranks[rank]
        placed = {
            node_id: [y_of[p] for p in preds.get(node_id, ()) if p in y_of]
            for node_id in members
        }
        members.sort(
            key=lambda node_id: (
                sum(placed[node_id]) / len(placed[node_id]) if placed[node_id] else 0.5,
                node_id,
            )
        )
        offset = (len(members) - 1) / 2
        for slot, node_id in enumerate(members):
            y_of[node_id] = 0.5 + (slot - offset) * row_h


def _port_anchors(
    ports: list | None,
    edge_x: float,
    centre_y: float,
    node_h: float,
) -> dict[str, dict[str, float]]:
    """Anchor points for one side of a node box, keyed by port name.

    Ports keep DECLARATION ORDER top to bottom — the order the stage's own port
    constant lists them — so the picture and the port declaration read the same
    way down. ``None`` (a legacy, unbound node) and ``[]`` (a bound node with
    nothing to bind, such as the pipeline head) both yield no anchors; edges
    touching them fall back to the box edge midpoint.
    """
    if not ports:
        return {}
    span = node_h * _PORT_SPREAD
    offset = (len(ports) - 1) / 2
    step = span / len(ports)
    return {
        port.name: {
            "x": _round(edge_x),
            "y": _round(centre_y + (index - offset) * step),
        }
        for index, port in enumerate(ports)
    }


def project_graph(graph: Graph) -> dict:
    """Emit the D3 declared-graph data contract for ``graph``.

    Pure and deterministic — a function of ``graph`` alone. Returns a dict with
    the same three keys the depth/provenance projection returns:

    * ``meta``  — graph-level facts the renderer surfaces: the graph's name and
      version, node/edge counts, the edge-type distribution, the rank structure
      (how many execution layers and which nodes sit in each), the longest
      declared chain, the uniform node box size, the run-supplied resource names
      the graph asks for, the count of config-gated nodes, and the caveats.
    * ``nodes`` — one record per node, sorted by ``node_id``, carrying the
      declaration (``type``, port names, ``resources``, ``enabled_when``,
      ``disabled_passthrough``, param key names), the layout result (``rank``,
      ``slot``, ``x``, ``y``, per-port anchors) and the transitive
      upstream/downstream counts. ``Node.status`` is deliberately absent: it is
      run state, and this is a definition.
    * ``edges`` — one record per DECLARED edge, sorted by
      ``(source_id, target_id, from_port, to_port, type)``, each with its own
      ``id`` and its own precomputed endpoints. Two edges between the same node
      pair stay two records with two lines.

    Raises ``ValueError`` (from ``topological_sort``) if ``graph`` is cyclic: a
    layered layout is defined over a DAG, and there is nothing honest to draw
    for a cycle.
    """
    nodes = list(graph.nodes)
    if not nodes:
        raise ValueError("cannot project a graph with no nodes")

    # --- 1. Rank by longest-path depth ---------------------------------------
    chains = _longest_chains(graph)
    preds = _predecessors(graph)
    rank_of = {node_id: len(chain) - 1 for node_id, chain in chains.items()}

    ranks: dict[int, list[str]] = {}
    for node_id, rank in rank_of.items():
        ranks.setdefault(rank, []).append(node_id)
    ordered_ranks = sorted(ranks)
    n_ranks = len(ordered_ranks)
    # Data-derived: the widest rank is what sets the row pitch, so no rank can
    # overlap its neighbour however lopsided the graph is.
    max_rank_size = max(len(members) for members in ranks.values())

    # --- 2. Geometry ---------------------------------------------------------
    draw_w = 1.0 - 2 * _MARGIN_X
    draw_h = 1.0 - 2 * _MARGIN_Y
    col_w = draw_w / n_ranks
    row_h = draw_h / max_rank_size
    node_w = col_w * _NODE_W_FRACTION
    node_h = row_h * _NODE_H_FRACTION

    y_of: dict[str, float] = {}
    _order_within_ranks(ranks, preds, y_of, row_h)

    x_of: dict[str, float] = {}
    slot_of: dict[str, int] = {}
    for column, rank in enumerate(ordered_ranks):
        centre_x = _MARGIN_X + (column + 0.5) * col_w
        for slot, node_id in enumerate(ranks[rank]):
            x_of[node_id] = centre_x
            slot_of[node_id] = slot

    # --- 3. Port anchors -----------------------------------------------------
    # Inputs on the left face, outputs on the right, so an edge always leaves a
    # right face and lands on a left face and the direction needs no arrowhead
    # to be readable.
    anchors: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for node in nodes:
        centre_x = x_of[node.id]
        centre_y = y_of[node.id]
        anchors[node.id] = {
            "inputs": _port_anchors(
                node.input_ports, centre_x - node_w / 2, centre_y, node_h
            ),
            "outputs": _port_anchors(
                node.output_ports, centre_x + node_w / 2, centre_y, node_h
            ),
        }

    def _anchor(node_id: str, side: str, port: str | None, edge_x: float) -> tuple[float, float]:
        """Endpoint for one end of an edge, falling back to the box edge midpoint.

        The fallback covers an edge with no declared port — legal on the model,
        absent from this pipeline, and not a reason to drop the edge from the
        picture.
        """
        found = anchors[node_id][side].get(port) if port is not None else None
        if found is None:
            return _round(x_of[node_id] + edge_x), _round(y_of[node_id])
        return found["x"], found["y"]

    # --- 4. Node records -----------------------------------------------------
    out_nodes = []
    conditional_count = 0
    resource_names: set[str] = set()
    for node in sorted(nodes, key=lambda n: n.id):
        if node.enabled_when is not None:
            conditional_count += 1
        resource_names.update(node.resources or ())
        out_nodes.append(
            {
                "node_id": node.id,
                "type": node.type,
                "rank": rank_of[node.id],
                "slot": slot_of[node.id],
                "x": _round(x_of[node.id]),
                "y": _round(y_of[node.id]),
                # Names only. Port TYPES are declared but unenforced in this
                # codebase, so rendering them would imply a guarantee the graph
                # does not currently make.
                "input_ports": (
                    None if node.input_ports is None else [p.name for p in node.input_ports]
                ),
                "output_ports": (
                    None if node.output_ports is None else [p.name for p in node.output_ports]
                ),
                "port_anchors": anchors[node.id],
                # KEY NAMES, not values: params carry live config objects (Node
                # 5.5's `llm` is an `LLMConfig`), which are not JSON and whose
                # contents are a run's configuration rather than the pipeline's
                # shape. What the node is configured BY is structure; what it is
                # configured WITH is not this view's subject.
                "param_keys": sorted(node.params),
                # `None` is not `[]` — an undeclared node and a node declaring
                # it needs nothing are different states, and the fence between
                # them is load-bearing in the executor.
                "resources": node.resources,
                "enabled_when": node.enabled_when,
                "disabled_passthrough": node.disabled_passthrough,
                "upstream_count": len(get_upstream(graph, node.id)),
                "downstream_count": len(get_downstream(graph, node.id)),
            }
        )

    # --- 5. Edge records -----------------------------------------------------
    out_edges = []
    edge_type_counts: dict[str, int] = {}
    for edge in sorted(
        graph.edges,
        key=lambda e: (e.source, e.target, e.from_port or "", e.to_port or "", e.type),
    ):
        edge_type_counts[edge.type] = edge_type_counts.get(edge.type, 0) + 1
        x1, y1 = _anchor(edge.source, "outputs", edge.from_port, node_w / 2)
        x2, y2 = _anchor(edge.target, "inputs", edge.to_port, -node_w / 2)
        out_edges.append(
            {
                # Identity is the port quadruple, not the node pair.
                "id": f"{edge.source}.{edge.from_port}->{edge.target}.{edge.to_port}",
                "source_id": edge.source,
                "target_id": edge.target,
                # Contract field. Every edge in this graph is DATA, so there is
                # no DATA/CONTROL distinction here to encode visually; the field
                # is emitted because it is contract, not because it varies.
                "type": edge.type,
                "from_port": edge.from_port,
                "to_port": edge.to_port,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "rank_span": rank_of[edge.target] - rank_of[edge.source],
            }
        )

    # --- 6. Graph-level metadata --------------------------------------------
    longest_chain: list[str] = []
    for chain in chains.values():
        if _outranks(chain, longest_chain):
            longest_chain = chain

    meta = {
        "view": "declared_graph",
        "layout": "layered_dag",
        "title": f"Idiograph — declared graph ({graph.name} v{graph.version})",
        "graph_name": graph.name,
        "graph_version": graph.version,
        "node_count": len(out_nodes),
        "edge_count": len(out_edges),
        "edge_type_counts": dict(sorted(edge_type_counts.items())),
        "rank_count": n_ranks,
        "ranks": [ranks[rank] for rank in ordered_ranks],
        "max_rank_size": max_rank_size,
        "longest_chain": longest_chain,
        "longest_chain_length": len(longest_chain),
        # One size for every node — see the module docstring.
        "node_size": {"w": _round(node_w), "h": _round(node_h)},
        "resource_names": sorted(resource_names),
        "conditional_node_count": conditional_count,
        "caveats": {
            "declaration_vs_execution": DECLARATION_CAVEAT,
            "port_identity": PORT_IDENTITY_CAVEAT,
        },
    }

    return {"meta": meta, "nodes": out_nodes, "edges": out_edges}
