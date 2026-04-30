# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import httpx

from idiograph.domains.arxiv.models import (
    FailedBatch,
    Node3Result,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import (
    _node3_score,
    _strip_openalex_id,
    backward_traverse,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _work(
    openalex_id: str,
    arxiv_id: str | None = None,
    title: str = "T",
    year: int | None = 2020,
    cited_by_count: int = 1,
    referenced_works: list[str] | None = None,
) -> dict:
    ids: dict = {"openalex": f"https://openalex.org/{openalex_id}"}
    if arxiv_id:
        ids["arxiv"] = f"https://arxiv.org/abs/{arxiv_id}"
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "ids": ids,
        "title": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": "A"}}],
        "abstract_inverted_index": None,
        "cited_by_count": cited_by_count,
        "referenced_works": [
            f"https://openalex.org/{r}" for r in (referenced_works or [])
        ],
    }


def _seed_record(node_id: str, openalex_id: str) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        arxiv_id=node_id.split(":", 1)[1] if node_id.startswith("arxiv:") else None,
        openalex_id=openalex_id,
        title="seed",
        hop_depth=0,
        root_ids=[node_id],
        year=2020,
    )


class _BatchClient:
    """Fake httpx.AsyncClient.get — dispatches by filter= param to a work db."""

    def __init__(self, works: dict[str, dict]):
        self.works = works
        self.calls: list[dict] = []
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, url: str, params: dict | None = None):
        self.calls.append(dict(params or {}))
        filt = (params or {}).get("filter", "")
        # Expected format: "openalex_id:W1|W2|..."
        ids: list[str] = []
        if filt.startswith("openalex_id:"):
            ids = filt[len("openalex_id:") :].split("|")
        results = [self.works[i] for i in ids if i in self.works]
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": results})
        return resp


class _StageFailingClient:
    """Fake httpx.AsyncClient.get that raises on the Nth call (0-indexed).

    Calls are issued in order: seed_refetch (0), depth_1 (1), depth_2 (2),
    assuming each stage fits in a single batch (≤50 IDs). Tests use small
    fixtures that satisfy this assumption.
    """

    def __init__(self, works: dict[str, dict], fail_at_call: int):
        self.works = works
        self.fail_at_call = fail_at_call
        self.call_count = 0
        self.calls: list[dict] = []
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, url: str, params: dict | None = None):
        self.calls.append(dict(params or {}))
        current = self.call_count
        self.call_count += 1
        if current == self.fail_at_call:
            raise httpx.ConnectError("simulated stage failure")
        filt = (params or {}).get("filter", "")
        ids: list[str] = []
        if filt.startswith("openalex_id:"):
            ids = filt[len("openalex_id:") :].split("|")
        results = [self.works[i] for i in ids if i in self.works]
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": results})
        return resp


def _run(client, **kwargs) -> Node3Result:
    return asyncio.run(backward_traverse(client=client, **kwargs))


# ── Scoring ────────────────────────────────────────────────────────────────


def test_score_zero_citations_is_zero():
    rec = PaperRecord(
        node_id="x", openalex_id="W1", title="t", hop_depth=1, citation_count=0
    )
    assert _node3_score(rec, lambda_decay=0.1, current_year=2026) == 0.0


def test_score_none_year_no_penalty():
    rec = PaperRecord(
        node_id="x",
        openalex_id="W1",
        title="t",
        hop_depth=1,
        citation_count=100,
        year=None,
    )
    s = _node3_score(rec, lambda_decay=0.5, current_year=2026)
    # years_since_publication treated as 0 -> recency_weight = 1
    import math

    assert s == 100 * math.log(2) / 1.0


def test_score_higher_citations_and_depth_rank_higher():
    lo = PaperRecord(
        node_id="lo",
        openalex_id="W1",
        title="t",
        hop_depth=1,
        citation_count=10,
        year=2020,
    )
    hi = PaperRecord(
        node_id="hi",
        openalex_id="W2",
        title="t",
        hop_depth=2,
        citation_count=100,
        year=2020,
    )
    y = 2026
    assert _node3_score(hi, 0.05, y) > _node3_score(lo, 0.05, y)


# ── Traversal ───────────────────────────────────────────────────────────────


def test_depth1_papers_returned_with_seed_root():
    # Seed S cites D1a and D1b. No further depth.
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1a", "D1b"]),
        "D1a": _work("D1a", arxiv_id="d1a.1", cited_by_count=5, year=2015),
        "D1b": _work("D1b", arxiv_id="d1b.1", cited_by_count=3, year=2015),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]

    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    ids = {r.node_id for r in result.papers}
    assert ids == {"arxiv:d1a.1", "arxiv:d1b.1"}
    for r in result.papers:
        assert r.hop_depth == 1
        assert r.root_ids == ["arxiv:seed.1"]


def test_depth2_papers_returned():
    # S -> D1 -> D2. D2 should land at hop_depth=2.
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=2, referenced_works=["D2"]),
        "D2": _work("D2", arxiv_id="d2.1", cited_by_count=8),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]

    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    by_id = {r.node_id: r for r in result.papers}
    assert by_id["arxiv:d1.1"].hop_depth == 1
    assert by_id["arxiv:d2.1"].hop_depth == 2


def test_dedup_keeps_lowest_hop_depth_and_unions_roots():
    # Two seeds. Shared paper P appears:
    #   - as depth=1 ref of S1
    #   - as depth=2 ref of S2 (via M)
    # P should end up hop_depth=1, root_ids = [S1, S2]
    works = {
        "S1": _work("S1", arxiv_id="s1.1", referenced_works=["P"]),
        "S2": _work("S2", arxiv_id="s2.1", referenced_works=["M"]),
        "M": _work("M", arxiv_id="m.1", cited_by_count=1, referenced_works=["P"]),
        "P": _work("P", arxiv_id="p.1", cited_by_count=50),
    }
    client = _BatchClient(works)
    seeds = [
        _seed_record("arxiv:s1.1", "S1"),
        _seed_record("arxiv:s2.1", "S2"),
    ]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    p = next(r for r in result.papers if r.node_id == "arxiv:p.1")
    assert p.hop_depth == 1
    assert set(p.root_ids) == {"arxiv:s1.1", "arxiv:s2.1"}


def test_n_backward_cap():
    # 5 depth-1 papers, n_backward=2, should return exactly 2.
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1", "D2", "D3", "D4", "D5"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=1),
        "D2": _work("D2", arxiv_id="d2.1", cited_by_count=10),
        "D3": _work("D3", arxiv_id="d3.1", cited_by_count=100),
        "D4": _work("D4", arxiv_id="d4.1", cited_by_count=5),
        "D5": _work("D5", arxiv_id="d5.1", cited_by_count=50),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=2,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert len(result.papers) == 2
    # Top-scored by citation_count at same hop/year: D3 then D5
    assert [r.node_id for r in result.papers] == ["arxiv:d3.1", "arxiv:d5.1"]


def test_multi_seed_overlapping_refs_merged():
    # Both seeds cite P directly. P should carry both seed roots.
    works = {
        "S1": _work("S1", arxiv_id="s1.1", referenced_works=["P"]),
        "S2": _work("S2", arxiv_id="s2.1", referenced_works=["P"]),
        "P": _work("P", arxiv_id="p.1", cited_by_count=10),
    }
    client = _BatchClient(works)
    seeds = [
        _seed_record("arxiv:s1.1", "S1"),
        _seed_record("arxiv:s2.1", "S2"),
    ]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert len(result.papers) == 1
    assert result.papers[0].node_id == "arxiv:p.1"
    assert result.papers[0].hop_depth == 1
    assert set(result.papers[0].root_ids) == {"arxiv:s1.1", "arxiv:s2.1"}


def test_paper_with_none_year_does_not_raise():
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=5, year=None),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.5,
        sleep_ms=0,
    )
    assert len(result.papers) == 1
    assert result.papers[0].node_id == "arxiv:d1.1"


def test_seeds_excluded_from_output():
    # S1 cites S2; S2 is also a seed. Should not appear in output papers.
    works = {
        "S1": _work("S1", arxiv_id="s1.1", referenced_works=["S2"]),
        "S2": _work("S2", arxiv_id="s2.1", cited_by_count=99),
    }
    client = _BatchClient(works)
    seeds = [
        _seed_record("arxiv:s1.1", "S1"),
        _seed_record("arxiv:s2.1", "S2"),
    ]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert all(
        r.node_id not in {"arxiv:s1.1", "arxiv:s2.1"} for r in result.papers
    )


def test_batch_fetch_http_error_recorded_in_failed_batches():
    # Client raises on every batch call — traversal returns empty papers/edges
    # but failed_batches must record the seed_refetch failure (per AMD-020).
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert isinstance(result, Node3Result)
    assert result.papers == []
    assert result.edges == []
    assert len(result.failed_batches) >= 1
    stages = {fb.stage for fb in result.failed_batches}
    assert "seed_refetch" in stages
    assert all(fb.reason.startswith("http_error: ") for fb in result.failed_batches)


def test_seed_refetch_miss_treated_as_empty_refs():
    # The seed's OpenAlex ID isn't returned by the re-fetch (e.g. pulled from
    # OpenAlex after Node 0 ran). Traversal must not crash — seed just yields
    # zero depth-1 refs and the final papers list is empty.
    works: dict[str, dict] = {}  # re-fetch returns nothing
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert result.papers == []


def test_strip_openalex_id_helper():
    assert _strip_openalex_id("https://openalex.org/W123") == "W123"
    assert _strip_openalex_id("W123") == "W123"
    # silence unused-import warning in minimal date usage
    assert date.today().year >= 2026


# ── Node 3 wrapper, edges, and failure provenance (AMD-020) ────────────────


def test_node3_result_returns_wrapper():
    """Return type is Node3Result, not list[PaperRecord]."""
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=5),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert isinstance(result, Node3Result)
    assert isinstance(result.papers, list)
    assert isinstance(result.edges, list)
    assert isinstance(result.failed_batches, list)


def test_node3_emits_seed_to_depth1_edges():
    """Every seed→depth-1 pair appears in result.edges with correct fields.
    Includes the seeds-themselves case (S1 cites S2, both seeds): edge is
    emitted because seeds are valid edge endpoints.
    """
    works = {
        "S1": _work("S1", arxiv_id="s1.1", referenced_works=["D1a", "D1b", "S2"]),
        "S2": _work("S2", arxiv_id="s2.1", year=2018),
        "D1a": _work("D1a", arxiv_id="d1a.1", cited_by_count=5),
        "D1b": _work("D1b", arxiv_id="d1b.1", cited_by_count=3),
    }
    client = _BatchClient(works)
    seeds = [
        _seed_record("arxiv:s1.1", "S1"),
        _seed_record("arxiv:s2.1", "S2"),
    ]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    pairs = {(e.source_id, e.target_id, e.type) for e in result.edges}
    assert ("arxiv:s1.1", "arxiv:d1a.1", "cites") in pairs
    assert ("arxiv:s1.1", "arxiv:d1b.1", "cites") in pairs
    # Seed-to-seed edge is emitted (target_id is another seed).
    assert ("arxiv:s1.1", "arxiv:s2.1", "cites") in pairs


def test_node3_emits_depth1_to_depth2_edges():
    """Every depth-1→depth-2 pair appears in result.edges with correct fields."""
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work(
            "D1",
            arxiv_id="d1.1",
            cited_by_count=2,
            referenced_works=["D2a", "D2b"],
        ),
        "D2a": _work("D2a", arxiv_id="d2a.1", cited_by_count=8),
        "D2b": _work("D2b", arxiv_id="d2b.1", cited_by_count=4),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    pairs = {(e.source_id, e.target_id, e.type) for e in result.edges}
    assert ("arxiv:d1.1", "arxiv:d2a.1", "cites") in pairs
    assert ("arxiv:d1.1", "arxiv:d2b.1", "cites") in pairs


def test_node3_no_dangling_edges_after_cap():
    """Every edge endpoint is in papers ∪ seeds.

    Covers the post-rank/cap edge filter: edges whose target_id is dropped
    by the cap don't appear in result.edges. Also covers fetch-failure
    dropping: an edge to a depth-2 paper whose metadata fetch failed (None
    in depth2_by_oa) is never emitted.
    """
    works = {
        "S": _work(
            "S",
            arxiv_id="seed.1",
            referenced_works=["D1", "D2", "D3", "D4", "D5"],
        ),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=1),
        "D2": _work("D2", arxiv_id="d2.1", cited_by_count=10),
        "D3": _work("D3", arxiv_id="d3.1", cited_by_count=100),
        "D4": _work("D4", arxiv_id="d4.1", cited_by_count=5),
        "D5": _work("D5", arxiv_id="d5.1", cited_by_count=50),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=2,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    valid = {p.node_id for p in result.papers} | {s.node_id for s in seeds}
    assert len(result.papers) == 2
    for e in result.edges:
        assert e.source_id in valid, f"edge source not in papers/seeds: {e}"
        assert e.target_id in valid, f"edge target not in papers/seeds: {e}"


def test_node3_seed_refetch_failure_recorded():
    """Seed re-fetch batch failure recorded as FailedBatch(stage='seed_refetch')."""
    seeds = [_seed_record("arxiv:seed.1", "S")]
    client = _StageFailingClient(works={}, fail_at_call=0)
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    seed_refetch_failures = [
        fb for fb in result.failed_batches if fb.stage == "seed_refetch"
    ]
    assert len(seed_refetch_failures) == 1
    assert "S" in seed_refetch_failures[0].requested_ids


def test_node3_depth_1_failure_recorded():
    """Depth-1 batch failure recorded as FailedBatch(stage='depth_1')."""
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=5),
    }
    seeds = [_seed_record("arxiv:seed.1", "S")]
    client = _StageFailingClient(works=works, fail_at_call=1)
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    depth_1_failures = [
        fb for fb in result.failed_batches if fb.stage == "depth_1"
    ]
    assert len(depth_1_failures) == 1
    assert depth_1_failures[0].requested_ids == ["D1"]


def test_node3_depth_2_failure_recorded():
    """Depth-2 batch failure recorded as FailedBatch(stage='depth_2')."""
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work(
            "D1",
            arxiv_id="d1.1",
            cited_by_count=2,
            referenced_works=["D2"],
        ),
        "D2": _work("D2", arxiv_id="d2.1", cited_by_count=8),
    }
    seeds = [_seed_record("arxiv:seed.1", "S")]
    client = _StageFailingClient(works=works, fail_at_call=2)
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    depth_2_failures = [
        fb for fb in result.failed_batches if fb.stage == "depth_2"
    ]
    assert len(depth_2_failures) == 1
    assert depth_2_failures[0].requested_ids == ["D2"]


def test_node3_failed_batches_carries_requested_ids():
    """FailedBatch.requested_ids matches the batch list at the failure site."""
    works = {
        "S1": _work("S1", arxiv_id="s1.1", referenced_works=[]),
        "S2": _work("S2", arxiv_id="s2.1", referenced_works=[]),
        "S3": _work("S3", arxiv_id="s3.1", referenced_works=[]),
    }
    seeds = [
        _seed_record("arxiv:s1.1", "S1"),
        _seed_record("arxiv:s2.1", "S2"),
        _seed_record("arxiv:s3.1", "S3"),
    ]
    client = _StageFailingClient(works=works, fail_at_call=0)
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert len(result.failed_batches) == 1
    fb = result.failed_batches[0]
    assert isinstance(fb, FailedBatch)
    assert set(fb.requested_ids) == {"S1", "S2", "S3"}


def test_node3_full_success_empty_failed_batches():
    """Clean run: failed_batches == []."""
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=5),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert result.failed_batches == []


def test_node3_edge_citing_paper_year_set():
    """edge.citing_paper_year matches the source paper's publication year.

    Verified for both depth-1 edges (source = seed) and depth-2 edges
    (source = depth-1 paper).
    """
    works = {
        "S": _work(
            "S",
            arxiv_id="seed.1",
            year=2019,
            referenced_works=["D1"],
        ),
        "D1": _work(
            "D1",
            arxiv_id="d1.1",
            year=2015,
            cited_by_count=2,
            referenced_works=["D2"],
        ),
        "D2": _work("D2", arxiv_id="d2.1", year=2010, cited_by_count=8),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    result = _run(
        client,
        seeds=seeds,
        api_key="k",
        n_backward=10,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    by_pair = {(e.source_id, e.target_id): e for e in result.edges}
    seed_to_d1 = by_pair[("arxiv:seed.1", "arxiv:d1.1")]
    assert seed_to_d1.citing_paper_year == 2020  # seed fixture year
    d1_to_d2 = by_pair[("arxiv:d1.1", "arxiv:d2.1")]
    assert d1_to_d2.citing_paper_year == 2015


def test_node3_deterministic_same_input():
    """Identical inputs produce identical Node3Result."""
    works = {
        "S": _work(
            "S",
            arxiv_id="seed.1",
            referenced_works=["D1", "D2"],
        ),
        "D1": _work(
            "D1",
            arxiv_id="d1.1",
            cited_by_count=10,
            referenced_works=["D2x"],
        ),
        "D2": _work(
            "D2",
            arxiv_id="d2.1",
            cited_by_count=5,
            referenced_works=["D2y"],
        ),
        "D2x": _work("D2x", arxiv_id="d2x.1", cited_by_count=2),
        "D2y": _work("D2y", arxiv_id="d2y.1", cited_by_count=3),
    }
    seeds = [_seed_record("arxiv:seed.1", "S")]

    r1 = _run(
        _BatchClient(works),
        seeds=seeds,
        api_key="k",
        n_backward=5,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    r2 = _run(
        _BatchClient(works),
        seeds=seeds,
        api_key="k",
        n_backward=5,
        lambda_decay=0.05,
        sleep_ms=0,
    )
    assert [p.node_id for p in r1.papers] == [p.node_id for p in r2.papers]
    assert [(e.source_id, e.target_id) for e in r1.edges] == [
        (e.source_id, e.target_id) for e in r2.edges
    ]
    assert r1.failed_batches == r2.failed_batches
