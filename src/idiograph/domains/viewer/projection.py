# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Depth/provenance projection — the headless producer of the renderer contract.

:func:`project_depth_provenance` reads a fully-attributed
:class:`~idiograph.domains.arxiv.models.PipelineResult` and emits the D3 data
contract for the *single* depth/provenance view (Slice 1). It is a pure function
of the artifact: no wall-clock, no RNG, no environment. The renderer is a dumb
consumer — every node arrives with its `(x, y)` already computed, so the same
artifact renders byte-identically every time. There is no force/physics layout.

Layout — bipolar depth bands (see ASSUMPTIONS in the run summary):

* The two seeds anchor a horizontal axis. Lexicographic order fixes which seed is
  "A" (left) and which is "B" (right), so seed-list order never perturbs output.
* Each node carries ``hop_depth_per_root`` — a BFS distance to *each* root. We
  resolve the two values into position with a **bipolar encoding**, keeping both
  rather than collapsing to a min:
    - ``lean = dB - dA``  → horizontal column. ``lean > 0`` is nearer root A
      (left); ``lean < 0`` nearer root B (right); ``lean == 0`` is *equidistant
      from both seeds* — the shared foundation, which forms the central column.
    - ``depth = dA + dB`` → vertical band. Seeds sit in the shallowest band at the
      top; the deep shared lineage sinks to the bottom.
* Within a ``(band, lean)`` cell, nodes are ordered by ``node_id`` and packed into
  a deterministic grid filling the cell's rectangle. Overplotting inside a dense
  cell is expected and accepted for this slice (LOD is deferred).

All coordinates are normalized to ``[0, 1]`` (renderer-agnostic) and rounded to a
fixed precision so the emitted JSON is byte-stable across platforms.
"""

import math
from collections import Counter, defaultdict

from idiograph.domains.arxiv.models import PipelineResult

# Fixed coordinate precision — rounding makes the emitted JSON byte-identical
# across platforms/interpreters (float formatting is otherwise not portable).
_COORD_PRECISION = 6

# Normalized drawing margins (fraction of the unit square kept clear of glyphs).
_MARGIN_X = 0.06
_MARGIN_Y = 0.05

# Load-bearing caveats, surfaced verbatim by the renderer (legend + panel +
# per-node tooltip). Sourced from the FROZEN spec-arxiv-pipeline-final.md.
CITATION_LAG_CAVEAT = (
    "Forward-traversal signal has a 12–18 month structural citation lag: a "
    "recently published paper may show few citations because the community has "
    "not yet responded, not because it is unimportant. The forward view is most "
    "meaningful for papers 12+ months old (Node 4). Applies to nodes whose "
    "traversal direction is forward or mixed."
)
CO_CITATION_LOCAL_CAVEAT = (
    "Co-citation strength is a LOCAL relative measure — the count of citing "
    "papers shared within this traversal boundary, not global co-citation "
    "prevalence (Node 5). A pair sharing 2 citers here may share hundreds across "
    "the full corpus."
)
CYCLE_SUPPRESSION_CAVEAT = (
    "Citation networks are not DAGs; bidirectional preprint citations and "
    "errata chains create cycles. Node 4.5 removes the weakest edge (lowest "
    "citation_count sum) per cycle — a declared heuristic, not a correct answer "
    "— and logs every removal to provenance."
)
EDGE_TYPE_CAVEAT = (
    "A direct citation (cites) is a declaration — a fact verifiable by opening "
    "the paper. A co-citation is an inference. They are rendered distinctly so "
    "the two are never conflated."
)


def _round(value: float) -> float:
    return round(value, _COORD_PRECISION)


def _resolve_roots(result: PipelineResult) -> tuple[str, str]:
    """Pick the two anchor roots and fix their left/right identity deterministically.

    Uses the resolved seed set, ordered lexicographically so seed-list order can
    never change the emitted layout. Slice 1 targets the two-seed CRISPR
    artifact; a single-seed artifact degenerates to one anchor (A == B), and a
    >2-seed artifact anchors on the two lexicographically-first seeds (a stated
    Slice-1 limitation — bipolar geometry encodes exactly two poles).
    """
    seeds = sorted(result.seeds)
    if not seeds:
        raise ValueError("cannot project a graph with no resolved seeds")
    root_a = seeds[0]
    root_b = seeds[-1] if len(seeds) > 1 else seeds[0]
    return root_a, root_b


def _depth_to(node, root: str, fallback: int) -> int:
    """BFS distance from ``node`` to ``root``, or ``fallback`` if unreachable.

    Every node in the CRISPR artifact is reachable from both roots, but the
    projection stays robust to a forest where a node reaches only one root.
    """
    hp = node.hop_depth_per_root or {}
    value = hp.get(root)
    return value if value is not None else fallback


def project_depth_provenance(result: PipelineResult) -> dict:
    """Emit the D3 depth/provenance data contract for ``result``.

    Pure and deterministic. Returns a dict with three keys:

    * ``meta``  — graph-level facts the renderer surfaces (seeds, counts, the
      cycle-suppression count, the local-co-citation and citation-lag caveats,
      the layout legend, and the traversal-direction distribution).
    * ``nodes`` — one record per node, sorted by ``node_id``, each carrying the
      contract fields plus its computed ``x``/``y`` and derived flags.
    * ``edges`` — one record per edge, sorted by ``(source_id, target_id, type)``,
      carrying the cites/co_citation ``type`` and the (local) ``strength``.
    """
    nodes = list(result.nodes)
    root_a, root_b = _resolve_roots(result)

    # An unreachable node sinks one band below the deepest real value; compute
    # that fallback from the observed maxima so it is data-derived, not a magic
    # constant.
    observed = [
        d
        for node in nodes
        for d in (node.hop_depth_per_root or {}).values()
    ]
    unreachable_depth = (max(observed) + 1) if observed else 1

    # --- 1. Per-node depth coordinates in the (lean, band) lattice ------------
    depths: dict[str, tuple[int, int, int]] = {}  # node_id -> (dA, dB, band)
    lean_values: set[int] = set()
    band_values: set[int] = set()
    for node in nodes:
        d_a = _depth_to(node, root_a, unreachable_depth)
        d_b = _depth_to(node, root_b, unreachable_depth)
        lean = d_b - d_a
        band = d_a + d_b
        depths[node.node_id] = (d_a, d_b, band)
        lean_values.add(lean)
        band_values.add(band)

    lean_min, lean_max = min(lean_values), max(lean_values)
    n_lean_cols = (lean_max - lean_min) + 1
    ordered_bands = sorted(band_values)  # shallow (seeds) first → top

    # --- 2. Bin nodes into (band, lean) cells, ordered by node_id ------------
    cells: dict[tuple[int, int], list] = defaultdict(list)
    for node in nodes:
        d_a, d_b, band = depths[node.node_id]
        lean = d_b - d_a
        cells[(band, lean)].append(node)
    for cell_nodes in cells.values():
        cell_nodes.sort(key=lambda n: n.node_id)

    # --- 3. Band heights: a band is as tall as its most-populated lean column
    # needs (so a cell's grid never overlaps its neighbours). Heights are
    # proportional to the row count, giving dense bands more vertical room.
    band_rows: dict[int, int] = {}
    cell_grid: dict[tuple[int, int], tuple[int, int]] = {}  # cell -> (cols, rows)
    for band in ordered_bands:
        max_rows = 1
        for lean in range(lean_min, lean_max + 1):
            cell = (band, lean)
            count = len(cells.get(cell, ()))
            if count == 0:
                continue
            # Square-ish grid inside the cell's column slot.
            cols = max(1, round(math.sqrt(count)))
            rows = math.ceil(count / cols)
            cell_grid[cell] = (cols, rows)
            max_rows = max(max_rows, rows)
        band_rows[band] = max_rows

    total_rows = sum(band_rows.values())
    draw_h = 1.0 - 2 * _MARGIN_Y
    draw_w = 1.0 - 2 * _MARGIN_X
    col_slot_w = draw_w / n_lean_cols

    # Vertical band offsets (cumulative, proportional to row counts).
    band_top: dict[int, float] = {}
    band_height: dict[int, float] = {}
    cursor = _MARGIN_Y
    for band in ordered_bands:
        height = draw_h * (band_rows[band] / total_rows) if total_rows else draw_h
        band_top[band] = cursor
        band_height[band] = height
        cursor += height

    # --- 4. Assign each node its (x, y) within its cell's rectangle ----------
    positions: dict[str, tuple[float, float]] = {}
    for (band, lean), cell_nodes in cells.items():
        cols, rows = cell_grid[(band, lean)]
        # Column slot: higher lean (nearer root A) sits to the LEFT.
        col_from_left = lean_max - lean
        x_left = _MARGIN_X + col_from_left * col_slot_w
        y_top = band_top[band]
        h = band_height[band]
        for i, node in enumerate(cell_nodes):
            c = i % cols
            r = i // cols
            x = x_left + (c + 0.5) / cols * col_slot_w
            y = y_top + (r + 0.5) / rows * h
            positions[node.node_id] = (_round(x), _round(y))

    # --- 5. Emit node records ------------------------------------------------
    dir_counts: Counter[str] = Counter()
    out_nodes = []
    for node in sorted(nodes, key=lambda n: n.node_id):
        d_a, d_b, band = depths[node.node_id]
        lean = d_b - d_a
        x, y = positions[node.node_id]
        direction = node.traversal_direction
        dir_counts[direction or "unknown"] += 1
        out_nodes.append(
            {
                "node_id": node.node_id,
                "arxiv_id": node.arxiv_id,
                "doi": node.doi,
                "title": node.title,
                "year": node.year,
                "citation_count": node.citation_count,
                "authors": node.authors,
                "community_id": node.community_id,
                "pagerank": node.pagerank,
                "hop_depth_per_root": dict(node.hop_depth_per_root),
                "traversal_direction": direction,
                "depth_to_a": d_a,
                "depth_to_b": d_b,
                "x": x,
                "y": y,
                "is_seed": direction == "seed",
                # Equidistant from both seeds → shared foundation (central column).
                "is_shared": lean == 0,
                # Node-4 citation-lag caveat applies to forward-facing signal.
                "lag_caveat": direction in ("forward", "mixed"),
            }
        )

    # --- 6. Emit edge records ------------------------------------------------
    out_edges = []
    cites_count = 0
    co_citation_count = 0
    strengths: list[int] = []
    for edge in sorted(
        result.edges, key=lambda e: (e.source_id, e.target_id, e.type)
    ):
        if edge.type == "co_citation":
            co_citation_count += 1
            if edge.strength is not None:
                strengths.append(edge.strength)
        elif edge.type == "cites":
            cites_count += 1
        out_edges.append(
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "type": edge.type,
                "strength": edge.strength,
                "citing_paper_year": edge.citing_paper_year,
            }
        )

    # --- 7. Graph-level metadata --------------------------------------------
    cycle_log = result.cycle_clean.cycle_log
    node_by_id = {n.node_id: n for n in nodes}

    def _seed_meta(root: str, side: str, x_hint: float) -> dict:
        rec = node_by_id.get(root)
        return {
            "node_id": root,
            "title": rec.title if rec else root,
            "year": rec.year if rec else None,
            "side": side,
            "x_hint": x_hint,
        }

    meta = {
        "view": "depth_provenance",
        "layout": "bipolar_depth_bands",
        "node_count": len(out_nodes),
        "edge_count": len(out_edges),
        "cites_count": cites_count,
        "co_citation_count": co_citation_count,
        "community_count": result.communities.community_count,
        "community_algorithm": result.communities.algorithm_used,
        "seeds": [
            _seed_meta(root_a, "A", 0.0),
            _seed_meta(root_b, "B", 1.0),
        ],
        "roots": {"a": root_a, "b": root_b},
        # Node 4.5 cycle-suppression — the user must see what was cleaned.
        "cycle_suppression_count": len(cycle_log.suppressed_edges),
        "cycles_detected_count": cycle_log.cycles_detected_count,
        "cycle_iterations": cycle_log.iterations,
        "co_citation_strength": {
            "min": min(strengths) if strengths else None,
            "max": max(strengths) if strengths else None,
            "label": "local relative measure (shared citers within traversal)",
        },
        "traversal_direction_counts": dict(sorted(dir_counts.items())),
        "shared_foundation_count": sum(1 for n in out_nodes if n["is_shared"]),
        "lag_caveat_count": sum(1 for n in out_nodes if n["lag_caveat"]),
        "depth_bands": ordered_bands,
        "lean_range": [lean_min, lean_max],
        "caveats": {
            "citation_lag": CITATION_LAG_CAVEAT,
            "co_citation_local": CO_CITATION_LOCAL_CAVEAT,
            "cycle_suppression": CYCLE_SUPPRESSION_CAVEAT,
            "edge_types": EDGE_TYPE_CAVEAT,
        },
    }

    return {"meta": meta, "nodes": out_nodes, "edges": out_edges}
