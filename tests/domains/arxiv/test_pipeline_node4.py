# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from idiograph.domains.arxiv.models import PaperRecord
from idiograph.domains.arxiv.pipeline import (
    _compute_acceleration,
    forward_traverse,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _work(
    openalex_id: str,
    arxiv_id: str | None = None,
    title: str = "T",
    year: int | None = 2022,
    cited_by_count: int = 10,
    counts_by_year: list[dict] | None = None,
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
        "counts_by_year": list(counts_by_year or []),
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


class _CitesClient:
    """Fake httpx.AsyncClient. Dispatches by `cites:<oa_id>` filter."""

    def __init__(self, citing_by_seed: dict[str, list[dict]]):
        self.citing_by_seed = citing_by_seed
        self.calls: list[dict] = []
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, url: str, params: dict | None = None):
        self.calls.append(dict(params or {}))
        filt = (params or {}).get("filter", "")
        works: list[dict] = []
        if filt.startswith("cites:"):
            oa_id = filt[len("cites:") :]
            works = self.citing_by_seed.get(oa_id, [])
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": works})
        return resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _run_forward(client: _CitesClient, **kwargs):
    with patch(
        "idiograph.domains.arxiv.pipeline.httpx.AsyncClient",
        return_value=client,
    ):
        return asyncio.run(forward_traverse(**kwargs))


# ── Tests ───────────────────────────────────────────────────────────────────


def test_happy_path():
    """Valid seed + two citing papers yield two scored, capped results."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    "W_C1",
                    arxiv_id="c1.1",
                    cited_by_count=48,
                    year=2022,
                    counts_by_year=[
                        {"year": 2023, "cited_by_count": 12},
                        {"year": 2024, "cited_by_count": 18},
                        {"year": 2025, "cited_by_count": 18},
                    ],
                ),
                _work(
                    "W_C2",
                    arxiv_id="c2.1",
                    cited_by_count=24,
                    year=2022,
                    counts_by_year=[
                        {"year": 2023, "cited_by_count": 6},
                        {"year": 2024, "cited_by_count": 9},
                        {"year": 2025, "cited_by_count": 9},
                    ],
                ),
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    ids = {r.node_id for r in out}
    assert ids == {"arxiv:c1.1", "arxiv:c2.1"}
    for r in out:
        assert r.hop_depth == 1
        assert r.root_ids == ["arxiv:seed.1"]


def test_sort_before_cap():
    """More citing papers than n_forward: top scorers are kept, not first-fetched."""
    # First-fetched papers have the LOWEST citation counts; highest are last.
    counts = [
        {"year": 2024, "cited_by_count": 12},
        {"year": 2025, "cited_by_count": 12},
        {"year": 2026, "cited_by_count": 12},
    ]
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    "W_A",
                    arxiv_id="a.1",
                    cited_by_count=1,
                    year=2022,
                    counts_by_year=counts,
                ),
                _work(
                    "W_B",
                    arxiv_id="b.1",
                    cited_by_count=5,
                    year=2022,
                    counts_by_year=counts,
                ),
                _work(
                    "W_C",
                    arxiv_id="c.1",
                    cited_by_count=500,
                    year=2022,
                    counts_by_year=counts,
                ),
                _work(
                    "W_D",
                    arxiv_id="d.1",
                    cited_by_count=1000,
                    year=2022,
                    counts_by_year=counts,
                ),
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=2,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    assert [r.node_id for r in out] == ["arxiv:d.1", "arxiv:c.1"]


def test_beta_zero_fallback_few_points():
    """Paper with <3 counts_by_year entries is included, scored with β=0."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    "W_P",
                    arxiv_id="p.1",
                    cited_by_count=60,
                    year=2020,
                    counts_by_year=[
                        {"year": 2024, "cited_by_count": 10},
                        {"year": 2025, "cited_by_count": 20},
                    ],
                )
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    # Large β would dominate if the fallback were skipped — function must not
    # raise and the paper must still appear in results.
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1000.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    assert len(out) == 1
    assert out[0].node_id == "arxiv:p.1"


def test_beta_zero_fallback_no_points():
    """Paper with empty counts_by_year is included, scored with β=0."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    "W_Q",
                    arxiv_id="q.1",
                    cited_by_count=30,
                    year=2021,
                    counts_by_year=[],
                )
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1000.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    assert len(out) == 1
    assert out[0].node_id == "arxiv:q.1"


def test_multi_seed_dedup():
    """A citing paper shared by two seeds is returned once with sorted root_ids."""
    shared = _work(
        "W_SHARED",
        arxiv_id="shared.1",
        cited_by_count=20,
        year=2022,
        counts_by_year=[
            {"year": 2023, "cited_by_count": 5},
            {"year": 2024, "cited_by_count": 7},
            {"year": 2025, "cited_by_count": 8},
        ],
    )
    client = _CitesClient(
        {
            "W_S1": [shared],
            "W_S2": [shared],
        }
    )
    seeds = [
        _seed_record("arxiv:s2.1", "W_S2"),  # deliberately reversed to test sort
        _seed_record("arxiv:s1.1", "W_S1"),
    ]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    assert len(out) == 1
    assert out[0].node_id == "arxiv:shared.1"
    assert out[0].root_ids == ["arxiv:s1.1", "arxiv:s2.1"]


def test_seed_exclusion():
    """A paper that is itself a seed does not appear in the citing results."""
    # S1's cites-query returns S2 as if S2 cited S1. S2 is also a seed → excluded.
    s2_as_work = _work("W_S2", arxiv_id="s2.1", cited_by_count=99, year=2022)
    other = _work(
        "W_OTHER",
        arxiv_id="other.1",
        cited_by_count=5,
        year=2022,
        counts_by_year=[
            {"year": 2023, "cited_by_count": 1},
            {"year": 2024, "cited_by_count": 2},
            {"year": 2025, "cited_by_count": 2},
        ],
    )
    client = _CitesClient({"W_S1": [s2_as_work, other], "W_S2": []})
    seeds = [
        _seed_record("arxiv:s1.1", "W_S1"),
        _seed_record("arxiv:s2.1", "W_S2"),
    ]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    node_ids = {r.node_id for r in out}
    assert "arxiv:s2.1" not in node_ids
    assert node_ids == {"arxiv:other.1"}


def test_n_forward_cap():
    """Result length never exceeds n_forward."""
    counts = [
        {"year": 2024, "cited_by_count": 1},
        {"year": 2025, "cited_by_count": 2},
        {"year": 2026, "cited_by_count": 3},
    ]
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    f"W_{i}",
                    arxiv_id=f"p.{i}",
                    cited_by_count=i,
                    year=2022,
                    counts_by_year=counts,
                )
                for i in range(1, 11)
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=3,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    assert len(out) == 3


def test_empty_citing_set():
    """Zero citing papers returns an empty list rather than raising."""
    client = _CitesClient({"W_SEED": []})
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    out = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        current_year=2026,
    )
    assert out == []


def test_regression_not_implemented():
    """acceleration_method='regression' raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        _compute_acceleration(
            [
                {"year": 2023, "cited_by_count": 1},
                {"year": 2024, "cited_by_count": 2},
                {"year": 2025, "cited_by_count": 3},
            ],
            acceleration_method="regression",
        )
