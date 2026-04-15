# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import httpx

from idiograph.domains.arxiv.models import PaperRecord
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

    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    ids = {r.node_id for r in out}
    assert ids == {"arxiv:d1a.1", "arxiv:d1b.1"}
    for r in out:
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

    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    by_id = {r.node_id: r for r in out}
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
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    p = next(r for r in out if r.node_id == "arxiv:p.1")
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
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=2, lambda_decay=0.05, sleep_ms=0
        )
    )
    assert len(out) == 2
    # Top-scored by citation_count at same hop/year: D3 then D5
    assert [r.node_id for r in out] == ["arxiv:d3.1", "arxiv:d5.1"]


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
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    assert len(out) == 1
    assert out[0].node_id == "arxiv:p.1"
    assert out[0].hop_depth == 1
    assert set(out[0].root_ids) == {"arxiv:s1.1", "arxiv:s2.1"}


def test_paper_with_none_year_does_not_raise():
    works = {
        "S": _work("S", arxiv_id="seed.1", referenced_works=["D1"]),
        "D1": _work("D1", arxiv_id="d1.1", cited_by_count=5, year=None),
    }
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.5, sleep_ms=0
        )
    )
    assert len(out) == 1
    assert out[0].node_id == "arxiv:d1.1"


def test_seeds_excluded_from_output():
    # S1 cites S2; S2 is also a seed. Should not appear in output.
    works = {
        "S1": _work("S1", arxiv_id="s1.1", referenced_works=["S2"]),
        "S2": _work("S2", arxiv_id="s2.1", cited_by_count=99),
    }
    client = _BatchClient(works)
    seeds = [
        _seed_record("arxiv:s1.1", "S1"),
        _seed_record("arxiv:s2.1", "S2"),
    ]
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    assert all(r.node_id not in {"arxiv:s1.1", "arxiv:s2.1"} for r in out)


def test_batch_fetch_http_error_silently_skipped():
    # Client raises on every batch call — traversal should return empty, not crash.
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    seeds = [_seed_record("arxiv:seed.1", "S")]
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    assert out == []


def test_seed_refetch_miss_treated_as_empty_refs():
    # The seed's OpenAlex ID isn't returned by the re-fetch (e.g. pulled from
    # OpenAlex after Node 0 ran). Traversal must not crash — seed just yields
    # zero depth-1 refs and the final output is empty.
    works: dict[str, dict] = {}  # re-fetch returns nothing
    client = _BatchClient(works)
    seeds = [_seed_record("arxiv:seed.1", "S")]
    out = asyncio.run(
        backward_traverse(
            seeds, client, api_key="k", n_backward=10, lambda_decay=0.05, sleep_ms=0
        )
    )
    assert out == []


def test_strip_openalex_id_helper():
    assert _strip_openalex_id("https://openalex.org/W123") == "W123"
    assert _strip_openalex_id("W123") == "W123"
    # silence unused-import warning in minimal date usage
    assert date.today().year >= 2026
