# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Depth/provenance projection — output-contract, layout, and determinism tests.

Two levels: a small hand-built ``PipelineResult`` exercises the layout invariants
and byte-determinism precisely, and the real frozen CRISPR artifact (loaded
read-only through the registry) validates the projection against the shape the
renderer actually consumes.
"""

import json

import pytest

from idiograph.apps.viewer.generate import load_frozen_result
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CitationEdge,
    CommunityResult,
    CycleCleanResult,
    CycleLog,
    DepthMetrics,
    ForwardParameters,
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    SuppressedEdge,
)
from idiograph.domains.viewer import project_depth_provenance

ROOT_A = "arxiv:aaa"  # lexicographically first → pole A (left)
ROOT_B = "arxiv:bbb"


def _rec(node_id, d_a, d_b, direction, **kw):
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=kw.get("title", node_id),
        year=kw.get("year", 2020),
        citation_count=kw.get("citation_count", 5),
        hop_depth=min(d_a, d_b),
        root_ids=[ROOT_A, ROOT_B],
        hop_depth_per_root={ROOT_A: d_a, ROOT_B: d_b},
        traversal_direction=direction,
        pagerank=kw.get("pagerank", 0.001),
        community_id=kw.get("community_id", "c0"),
    )


def _toy_result() -> PipelineResult:
    """Two seeds + one node per lean class + one suppressed cycle edge."""
    nodes = [
        _rec(ROOT_A, 0, 1, "seed"),
        _rec(ROOT_B, 1, 0, "seed"),
        _rec("arxiv:leanA", 1, 2, "backward"),   # nearer A → left
        _rec("arxiv:shared", 2, 2, "mixed"),     # equidistant → centre
        _rec("arxiv:leanB", 2, 1, "forward"),    # nearer B → right
    ]
    node_ids = [n.node_id for n in nodes]
    cites = [
        CitationEdge(source_id="arxiv:leanA", target_id=ROOT_A, type="cites"),
        CitationEdge(source_id="arxiv:leanB", target_id=ROOT_B, type="cites"),
    ]
    cocite = [
        CitationEdge(
            source_id="arxiv:leanA", target_id="arxiv:shared",
            type="co_citation", strength=3,
        ),
    ]
    suppressed = SuppressedEdge(
        original=CitationEdge(
            source_id=ROOT_A, target_id=ROOT_B, type="cites"
        ),
        citation_sum=10,
        cycle_members=[ROOT_A, ROOT_B],
    )
    cycle_log = CycleLog(
        suppressed_edges=[suppressed], cycles_detected_count=1, iterations=1
    )
    cycle_clean = CycleCleanResult(
        cleaned_edges=cites,
        cycle_log=cycle_log,
        input_node_ids=frozenset(node_ids),
    )
    depth_metrics = {
        n.node_id: DepthMetrics(
            hop_depth_per_root=n.hop_depth_per_root,
            traversal_direction=n.traversal_direction,
        )
        for n in nodes
    }
    return PipelineResult(
        nodes=nodes,
        edges=cites + cocite,
        seeds=[ROOT_B, ROOT_A],  # deliberately unsorted — must not perturb output
        cycle_clean=cycle_clean,
        co_citation_edges=cocite,
        depth_metrics=depth_metrics,
        pagerank={n.node_id: n.pagerank for n in nodes},
        communities=CommunityResult(
            community_assignments={n.node_id: "c0" for n in nodes},
            algorithm_used="infomap",
            community_count=1,
        ),
        parameters=PipelineParameters(
            backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
            forward=ForwardParameters(
                n_forward=10, lambda_decay=0.1, alpha=1.0, beta=0.0,
                sort="cited_by_count:desc",
            ),
        ),
    )


# ── Synthetic: contract, layout, determinism ────────────────────────────────


def test_emits_three_top_level_keys():
    proj = project_depth_provenance(_toy_result())
    assert set(proj) == {"meta", "nodes", "edges"}


def test_node_records_carry_contract_fields():
    proj = project_depth_provenance(_toy_result())
    for n in proj["nodes"]:
        for field in (
            "node_id", "arxiv_id", "doi", "title", "year", "citation_count",
            "authors", "community_id", "pagerank", "hop_depth_per_root",
            "traversal_direction", "x", "y", "is_seed", "is_shared",
            "lag_caveat",
        ):
            assert field in n, f"missing {field}"
        assert 0.0 <= n["x"] <= 1.0 and 0.0 <= n["y"] <= 1.0


def test_edge_records_carry_type_and_strength():
    proj = project_depth_provenance(_toy_result())
    for e in proj["edges"]:
        assert set(e) >= {
            "source_id", "target_id", "type", "strength", "citing_paper_year"
        }
    cites = [e for e in proj["edges"] if e["type"] == "cites"]
    cocite = [e for e in proj["edges"] if e["type"] == "co_citation"]
    assert all(e["strength"] is None for e in cites)
    assert all(e["strength"] is not None for e in cocite)


def test_cites_co_citation_split_counts():
    proj = project_depth_provenance(_toy_result())
    assert proj["meta"]["cites_count"] == 2
    assert proj["meta"]["co_citation_count"] == 1
    assert proj["meta"]["edge_count"] == 3


def test_seeds_identified_and_distinct():
    proj = project_depth_provenance(_toy_result())
    seeds = [n for n in proj["nodes"] if n["is_seed"]]
    assert {n["node_id"] for n in seeds} == {ROOT_A, ROOT_B}
    assert all(n["traversal_direction"] == "seed" for n in seeds)
    # both roots surfaced in meta
    assert {s["node_id"] for s in proj["meta"]["seeds"]} == {ROOT_A, ROOT_B}
    assert proj["meta"]["roots"] == {"a": ROOT_A, "b": ROOT_B}


def test_both_roots_carried_in_hop_depth():
    proj = project_depth_provenance(_toy_result())
    for n in proj["nodes"]:
        assert set(n["hop_depth_per_root"]) == {ROOT_A, ROOT_B}


def test_layout_seeds_at_top_and_lean_columns():
    proj = project_depth_provenance(_toy_result())
    pos = {n["node_id"]: (n["x"], n["y"]) for n in proj["nodes"]}
    # Seeds (combined depth 1) sit in the shallowest band → smallest y (top).
    max_seed_y = max(pos[ROOT_A][1], pos[ROOT_B][1])
    assert pos["arxiv:shared"][1] > max_seed_y
    # Lean encodes the horizontal axis: nearer-A left of centre left of nearer-B.
    assert pos["arxiv:leanA"][0] < pos["arxiv:shared"][0] < pos["arxiv:leanB"][0]


def test_shared_foundation_flag_matches_equidistance():
    proj = project_depth_provenance(_toy_result())
    shared = {n["node_id"] for n in proj["nodes"] if n["is_shared"]}
    assert "arxiv:shared" in shared
    assert "arxiv:leanA" not in shared


def test_lag_caveat_flags_forward_and_mixed():
    proj = project_depth_provenance(_toy_result())
    flagged = {n["node_id"] for n in proj["nodes"] if n["lag_caveat"]}
    assert flagged == {"arxiv:shared", "arxiv:leanB"}  # mixed + forward
    assert proj["meta"]["lag_caveat_count"] == 2


def test_cycle_suppression_surfaced():
    proj = project_depth_provenance(_toy_result())
    assert proj["meta"]["cycle_suppression_count"] == 1
    assert proj["meta"]["cycles_detected_count"] == 1


def test_seed_order_does_not_perturb_output():
    """Byte-identical emission regardless of seed-list order (determinism)."""
    a = project_depth_provenance(_toy_result())
    b = project_depth_provenance(_toy_result())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_co_citation_strength_labeled_local():
    proj = project_depth_provenance(_toy_result())
    cs = proj["meta"]["co_citation_strength"]
    assert "local" in cs["label"]
    assert cs["min"] == 3 and cs["max"] == 3


def test_caveats_present():
    meta = project_depth_provenance(_toy_result())["meta"]
    for key in ("citation_lag", "co_citation_local", "cycle_suppression"):
        assert meta["caveats"][key]


# ── Real frozen CRISPR artifact: shape the renderer actually consumes ────────


@pytest.fixture(scope="module")
def frozen_projection():
    return project_depth_provenance(load_frozen_result())


def test_frozen_full_corpus_rendered(frozen_projection):
    # IDG-047: the FULL persisted artifact, not a curated subgraph.
    assert frozen_projection["meta"]["node_count"] == 1885
    assert frozen_projection["meta"]["edge_count"] == 14852
    assert len(frozen_projection["nodes"]) == 1885


def test_frozen_two_seeds(frozen_projection):
    seeds = [n for n in frozen_projection["nodes"] if n["is_seed"]]
    assert len(seeds) == 2
    assert len(frozen_projection["meta"]["seeds"]) == 2


def test_frozen_cites_co_citation_split(frozen_projection):
    m = frozen_projection["meta"]
    assert m["cites_count"] == 3479
    assert m["co_citation_count"] == 11373
    assert m["cites_count"] + m["co_citation_count"] == m["edge_count"]


def test_frozen_cycle_suppression_count(frozen_projection):
    assert frozen_projection["meta"]["cycle_suppression_count"] == 1


def test_frozen_all_nodes_positioned_and_in_unit_square(frozen_projection):
    for n in frozen_projection["nodes"]:
        assert 0.0 <= n["x"] <= 1.0
        assert 0.0 <= n["y"] <= 1.0
        assert set(n["hop_depth_per_root"]) == set(
            frozen_projection["meta"]["roots"].values()
        )


def test_frozen_determinism_byte_identical():
    a = project_depth_provenance(load_frozen_result())
    b = project_depth_provenance(load_frozen_result())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
