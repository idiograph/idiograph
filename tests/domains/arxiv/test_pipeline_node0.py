# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from idiograph.domains.arxiv.pipeline import fetch_seeds, reconstruct_abstract


def _work(
    openalex_id: str = "W100",
    arxiv_id: str | None = "2301.07041",
    doi: str | None = None,
    title: str = "A paper",
    year: int | None = 2023,
    authors: list[str] | None = None,
    cited_by_count: int = 10,
    abstract_inverted_index: dict | None = None,
) -> dict:
    ids: dict = {"openalex": f"https://openalex.org/{openalex_id}"}
    if arxiv_id:
        ids["arxiv"] = f"https://arxiv.org/abs/{arxiv_id}"
    if doi:
        ids["doi"] = doi
    authorships = [
        {"author": {"display_name": a}} for a in (authors or ["Ada Lovelace"])
    ]
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "ids": ids,
        "title": title,
        "publication_year": year,
        "authorships": authorships,
        "abstract_inverted_index": abstract_inverted_index,
        "cited_by_count": cited_by_count,
    }


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


def _make_client(responses: list[MagicMock]) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=responses)
    return client


def test_single_arxiv_seed_resolves():
    client = _make_client([_ok_response({"results": [_work(arxiv_id="2301.07041")]})])
    resolved, failures = asyncio.run(
        fetch_seeds([{"arxiv_id": "2301.07041"}], client, api_key="k", sleep_ms=0)
    )
    assert len(resolved) == 1
    assert failures == []
    rec = resolved[0]
    assert rec.node_id == "arxiv:2301.07041"
    assert rec.hop_depth == 0
    assert rec.root_ids == ["arxiv:2301.07041"]
    assert rec.openalex_id == "W100"
    assert rec.citation_count == 10


def test_single_doi_seed_resolves():
    work = _work(openalex_id="W200", arxiv_id=None, doi="https://doi.org/10.1/x")
    client = _make_client([_ok_response({"results": [work]})])
    resolved, failures = asyncio.run(
        fetch_seeds([{"doi": "10.1/x"}], client, api_key="k", sleep_ms=0)
    )
    assert len(resolved) == 1
    assert failures == []
    assert resolved[0].node_id == "doi:https://doi.org/10.1/x"
    assert resolved[0].root_ids == ["doi:https://doi.org/10.1/x"]


def test_single_seed_not_found_raises():
    client = _make_client([_ok_response({"results": []})])
    with pytest.raises(ValueError):
        asyncio.run(
            fetch_seeds(
                [{"arxiv_id": "9999.99999"}], client, api_key="k", sleep_ms=0
            )
        )


def test_two_seeds_both_resolve():
    client = _make_client(
        [
            _ok_response({"results": [_work(openalex_id="W1", arxiv_id="1111.11111")]}),
            _ok_response({"results": [_work(openalex_id="W2", arxiv_id="2222.22222")]}),
        ]
    )
    resolved, failures = asyncio.run(
        fetch_seeds(
            [{"arxiv_id": "1111.11111"}, {"arxiv_id": "2222.22222"}],
            client,
            api_key="k",
            sleep_ms=0,
        )
    )
    assert failures == []
    assert [r.node_id for r in resolved] == ["arxiv:1111.11111", "arxiv:2222.22222"]
    assert resolved[0].root_ids == ["arxiv:1111.11111"]
    assert resolved[1].root_ids == ["arxiv:2222.22222"]


def test_two_seeds_one_fails():
    client = _make_client(
        [
            _ok_response({"results": [_work(openalex_id="W1", arxiv_id="1111.11111")]}),
            _ok_response({"results": []}),
        ]
    )
    resolved, failures = asyncio.run(
        fetch_seeds(
            [{"arxiv_id": "1111.11111"}, {"arxiv_id": "9999.99999"}],
            client,
            api_key="k",
            sleep_ms=0,
        )
    )
    assert len(resolved) == 1
    assert len(failures) == 1
    assert failures[0]["seed"] == {"arxiv_id": "9999.99999"}
    assert resolved[0].node_id == "arxiv:1111.11111"


def test_empty_seed_list_raises():
    client = _make_client([])
    with pytest.raises(ValueError):
        asyncio.run(fetch_seeds([], client, api_key="k", sleep_ms=0))


def test_unrecognized_seed_shape_recorded_as_failure():
    client = _make_client([])  # no HTTP calls expected
    resolved, failures = asyncio.run(
        fetch_seeds(
            [{"unknown": "x"}, {"arxiv_id": "1111.11111"}],
            _make_client(
                [_ok_response({"results": [_work(openalex_id="W1", arxiv_id="1111.11111")]})]
            ),
            api_key="k",
            sleep_ms=0,
        )
    )
    assert len(resolved) == 1
    assert len(failures) == 1
    assert failures[0]["seed"] == {"unknown": "x"}
    assert "unrecognized" in failures[0]["reason"]
    # unused local silences flake
    _ = client


def test_http_error_recorded_as_failure():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        side_effect=httpx.ConnectError("boom")
    )
    # Only the failing seed — resolved list will be empty, so ValueError fires.
    with pytest.raises(ValueError):
        asyncio.run(
            fetch_seeds(
                [{"arxiv_id": "1111.11111"}], client, api_key="k", sleep_ms=0
            )
        )

    # Now pair it with a successful seed so we can inspect failures.
    ok = _ok_response({"results": [_work(openalex_id="W1", arxiv_id="2222.22222")]})
    client2 = AsyncMock(spec=httpx.AsyncClient)
    client2.get = AsyncMock(side_effect=[httpx.ConnectError("boom"), ok])
    resolved, failures = asyncio.run(
        fetch_seeds(
            [{"arxiv_id": "1111.11111"}, {"arxiv_id": "2222.22222"}],
            client2,
            api_key="k",
            sleep_ms=0,
        )
    )
    assert len(resolved) == 1
    assert len(failures) == 1
    assert "http error" in failures[0]["reason"]


def test_reconstruct_abstract_roundtrip():
    inv = {"hello": [0, 2], "world": [1]}
    assert reconstruct_abstract(inv) == "hello world hello"


def test_reconstruct_abstract_none():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None
