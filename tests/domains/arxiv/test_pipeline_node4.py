# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from idiograph.domains.arxiv.models import (
    FailedSeed,
    Node4Result,
    PaperRecord,
    TruncatedSeed,
)
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
    """Fake httpx.AsyncClient. Dispatches by `cites:<oa_id>` filter.

    Optional ``meta_count_by_seed`` overrides the synthetic ``meta.count``
    in the response payload (defaults to ``len(works)``). Optional
    ``fail_seeds`` raises ``httpx.ConnectError`` for the listed seed
    OpenAlex IDs to exercise Node 4's per-seed failure path.
    """

    def __init__(
        self,
        citing_by_seed: dict[str, list[dict]],
        meta_count_by_seed: dict[str, int] | None = None,
        fail_seeds: set[str] | None = None,
    ):
        self.citing_by_seed = citing_by_seed
        self.meta_count_by_seed = meta_count_by_seed or {}
        self.fail_seeds = fail_seeds or set()
        self.calls: list[dict] = []
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, url: str, params: dict | None = None):
        self.calls.append(dict(params or {}))
        filt = (params or {}).get("filter", "")
        works: list[dict] = []
        oa_id = ""
        if filt.startswith("cites:"):
            oa_id = filt[len("cites:") :]
            if oa_id in self.fail_seeds:
                raise httpx.ConnectError(f"simulated failure for {oa_id}")
            works = self.citing_by_seed.get(oa_id, [])
        meta_count = self.meta_count_by_seed.get(oa_id, len(works))
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={"results": works, "meta": {"count": meta_count}}
        )
        return resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _run_forward(client: _CitesClient, **kwargs) -> Node4Result:
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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    ids = {r.node_id for r in result.papers}
    assert ids == {"arxiv:c1.1", "arxiv:c2.1"}
    for r in result.papers:
        assert r.hop_depth == 1
        assert r.root_ids == ["arxiv:seed.1"]


def test_sort_before_cap():
    """More citing papers than n_forward: top scorers are kept, not first-fetched."""
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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=2,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert [r.node_id for r in result.papers] == ["arxiv:d.1", "arxiv:c.1"]


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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1000.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.papers) == 1
    assert result.papers[0].node_id == "arxiv:p.1"


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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1000.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.papers) == 1
    assert result.papers[0].node_id == "arxiv:q.1"


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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.papers) == 1
    assert result.papers[0].node_id == "arxiv:shared.1"
    assert result.papers[0].root_ids == ["arxiv:s1.1", "arxiv:s2.1"]


def test_seed_exclusion():
    """A paper that is itself a seed does not appear in the citing results."""
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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    node_ids = {r.node_id for r in result.papers}
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
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=3,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.papers) == 3


def test_empty_citing_set():
    """Zero citing papers returns an empty list rather than raising."""
    client = _CitesClient({"W_SEED": []})
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=1.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert result.papers == []


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


# ── Node 4 wrapper, edges, and failure provenance (AMD-020) ────────────────


def test_node4_result_returns_wrapper():
    """Return type is Node4Result, not list[PaperRecord]."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    "W_C1",
                    arxiv_id="c1.1",
                    cited_by_count=10,
                    year=2022,
                ),
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert isinstance(result, Node4Result)
    assert isinstance(result.papers, list)
    assert isinstance(result.edges, list)
    assert isinstance(result.failed_seeds, list)
    assert isinstance(result.truncated_seeds, list)


def test_node4_emits_citer_to_seed_edges():
    """For each citing paper in result.papers, an edge (citer → seed) exists."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work("W_C1", arxiv_id="c1.1", cited_by_count=10, year=2022),
                _work("W_C2", arxiv_id="c2.1", cited_by_count=8, year=2022),
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    pairs = {(e.source_id, e.target_id, e.type) for e in result.edges}
    paper_ids = {p.node_id for p in result.papers}
    for pid in paper_ids:
        assert (pid, "arxiv:seed.1", "cites") in pairs


def test_node4_failed_seed_recorded():
    """When a per-seed call raises, failed_seeds contains FailedSeed."""
    client = _CitesClient(
        citing_by_seed={"W_SEED": []},
        fail_seeds={"W_SEED"},
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.failed_seeds) == 1
    fs = result.failed_seeds[0]
    assert isinstance(fs, FailedSeed)
    assert fs.seed_id == "arxiv:seed.1"
    assert fs.reason.startswith("http_error: ")


def test_node4_failed_seed_distinguishable_from_zero_citers():
    """Failed seed: in failed_seeds. Zero-citers seed: in neither bucket."""
    client = _CitesClient(
        citing_by_seed={
            "W_OK": [_work("W_C", arxiv_id="c.1", cited_by_count=5, year=2022)],
            "W_ZERO": [],
            "W_FAIL": [],
        },
        fail_seeds={"W_FAIL"},
    )
    seeds = [
        _seed_record("arxiv:ok.1", "W_OK"),
        _seed_record("arxiv:zero.1", "W_ZERO"),
        _seed_record("arxiv:fail.1", "W_FAIL"),
    ]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    failed_ids = {fs.seed_id for fs in result.failed_seeds}
    truncated_ids = {ts.seed_id for ts in result.truncated_seeds}
    assert failed_ids == {"arxiv:fail.1"}
    assert "arxiv:zero.1" not in failed_ids
    assert "arxiv:zero.1" not in truncated_ids
    assert "arxiv:ok.1" not in failed_ids
    assert "arxiv:ok.1" not in truncated_ids


def test_node4_truncation_recorded():
    """meta.count > len(results): TruncatedSeed appended."""
    client = _CitesClient(
        citing_by_seed={
            "W_SEED": [
                _work(f"W_C{i}", arxiv_id=f"c{i}.1", cited_by_count=i, year=2022)
                for i in range(1, 4)
            ]
        },
        meta_count_by_seed={"W_SEED": 350},
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.truncated_seeds) == 1
    ts = result.truncated_seeds[0]
    assert isinstance(ts, TruncatedSeed)
    assert ts.seed_id == "arxiv:seed.1"
    assert ts.returned_count == 3
    assert ts.total_count == 350


def test_node4_no_truncation_under_cap():
    """meta.count == len(results): truncated_seeds is empty."""
    client = _CitesClient(
        citing_by_seed={
            "W_SEED": [
                _work("W_C1", arxiv_id="c1.1", cited_by_count=10, year=2022),
            ]
        },
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert result.truncated_seeds == []


def test_node4_sort_parameter_required():
    """Calling forward_traverse without sort raises TypeError."""
    client = _CitesClient({"W_SEED": []})
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    with pytest.raises(TypeError):
        with patch(
            "idiograph.domains.arxiv.pipeline.httpx.AsyncClient",
            return_value=client,
        ):
            asyncio.run(
                forward_traverse(
                    seeds=seeds,
                    api_key="k",
                    n_forward=10,
                    alpha=1.0,
                    beta=0.0,
                    lambda_decay=0.05,
                    current_year=2026,
                )
            )


def test_node4_sort_passes_through_to_query():
    """sort kwarg is forwarded into the OpenAlex query params."""
    client = _CitesClient({"W_SEED": []})
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="publication_date:desc",
        current_year=2026,
    )
    assert client.calls, "client.get was not called"
    assert client.calls[0]["sort"] == "publication_date:desc"


def test_node4_full_success_empty_failure_lists():
    """Clean run: failed_seeds == [] and truncated_seeds == []."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work("W_C1", arxiv_id="c1.1", cited_by_count=10, year=2022),
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert result.failed_seeds == []
    assert result.truncated_seeds == []


def test_node4_edge_citing_paper_year_set():
    """edge.citing_paper_year matches the citer's publication year."""
    client = _CitesClient(
        {
            "W_SEED": [
                _work("W_C1", arxiv_id="c1.1", cited_by_count=10, year=2023),
                _work("W_C2", arxiv_id="c2.1", cited_by_count=8, year=2024),
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    by_source = {e.source_id: e for e in result.edges}
    assert by_source["arxiv:c1.1"].citing_paper_year == 2023
    assert by_source["arxiv:c2.1"].citing_paper_year == 2024


def test_node4_no_dangling_edges_after_cap():
    """Every edge endpoint is in papers ∪ seeds (post-cap filter)."""
    counts = [
        {"year": 2024, "cited_by_count": 1},
        {"year": 2025, "cited_by_count": 2},
        {"year": 2026, "cited_by_count": 3},
    ]
    client = _CitesClient(
        {
            "W_SEED": [
                _work(
                    f"W_C{i}",
                    arxiv_id=f"c{i}.1",
                    cited_by_count=i * 10,
                    year=2022,
                    counts_by_year=counts,
                )
                for i in range(1, 6)
            ]
        }
    )
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=2,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert len(result.papers) == 2
    paper_ids = {p.node_id for p in result.papers}
    seed_ids = {s.node_id for s in seeds}
    for e in result.edges:
        assert e.source_id in paper_ids, f"edge source not in papers: {e}"
        assert e.target_id in seed_ids, f"edge target not in seeds: {e}"


def test_node4_failed_seed_not_in_truncated_seeds():
    """A seed in failed_seeds does not also appear in truncated_seeds."""
    client = _CitesClient(
        citing_by_seed={"W_FAIL": []},
        meta_count_by_seed={"W_FAIL": 999},
        fail_seeds={"W_FAIL"},
    )
    seeds = [_seed_record("arxiv:fail.1", "W_FAIL")]
    result = _run_forward(
        client,
        seeds=seeds,
        api_key="k",
        n_forward=10,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    failed_ids = {fs.seed_id for fs in result.failed_seeds}
    truncated_ids = {ts.seed_id for ts in result.truncated_seeds}
    assert failed_ids == {"arxiv:fail.1"}
    assert truncated_ids == set()


def test_node4_deterministic_same_input():
    """Identical inputs produce identical Node4Result. Sort is the
    determinism mechanism — the same cited_by_count:desc sort yields the
    same returned-citer set across runs.
    """
    counts = [
        {"year": 2023, "cited_by_count": 5},
        {"year": 2024, "cited_by_count": 7},
        {"year": 2025, "cited_by_count": 8},
    ]
    citers = [
        _work(f"W_C{i}", arxiv_id=f"c{i}.1", cited_by_count=i * 5, year=2022,
              counts_by_year=counts)
        for i in range(1, 6)
    ]
    seeds = [_seed_record("arxiv:seed.1", "W_SEED")]

    r1 = _run_forward(
        _CitesClient({"W_SEED": list(citers)}),
        seeds=seeds,
        api_key="k",
        n_forward=3,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    r2 = _run_forward(
        _CitesClient({"W_SEED": list(citers)}),
        seeds=seeds,
        api_key="k",
        n_forward=3,
        alpha=1.0,
        beta=0.0,
        lambda_decay=0.05,
        sort="cited_by_count:desc",
        current_year=2026,
    )
    assert [p.node_id for p in r1.papers] == [p.node_id for p in r2.papers]
    assert [(e.source_id, e.target_id) for e in r1.edges] == [
        (e.source_id, e.target_id) for e in r2.edges
    ]
    assert r1.failed_seeds == r2.failed_seeds
    assert r1.truncated_seeds == r2.truncated_seeds
