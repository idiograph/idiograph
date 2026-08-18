# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import subprocess
import sys
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


def test_arxiv_id_seed_is_refused():
    """arXiv-ID seed resolution is UNSUPPORTED by ruling (IDG-105), not by
    transport accident.

    The refusal is stated locally: it names the seed as received and the forms
    the path actually accepts, rather than building `ids.arxiv:<abs url>` and
    letting OpenAlex answer HTTP 400 — a response that got recorded as a network
    failure and invited a retry that could never succeed.
    """
    client = _make_client([])  # no HTTP call is expected to be reached

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            fetch_seeds([{"arxiv_id": "2301.07041"}], client, api_key="k", sleep_ms=0)
        )

    message = str(excinfo.value)
    # The offending seed, as received.
    assert "{'arxiv_id': '2301.07041'}" in message
    # The forms the path does accept.
    assert '{"doi": ...}' in message
    client.get.assert_not_awaited()


def test_arxiv_id_seed_refused_before_any_seed_is_fetched():
    """The halt is whole-batch and fires BEFORE the fetch loop. A resolvable seed
    ahead of the offending one must not have been spent on the network — the run
    was never going to resolve the set it was asked for."""
    client = _make_client([_ok_response({"results": [_work(openalex_id="W1")]})])

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            fetch_seeds(
                [{"doi": "10.1/x"}, {"arxiv_id": "2301.07041"}],
                client,
                api_key="k",
                sleep_ms=0,
            )
        )

    assert "seed 1 {'arxiv_id': '2301.07041'}" in str(excinfo.value)
    client.get.assert_not_awaited()


def test_every_refused_seed_is_named():
    client = _make_client([])
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            fetch_seeds(
                [{"arxiv_id": "1111.11111"}, {"arxiv_id": "2222.22222"}],
                client,
                api_key="k",
                sleep_ms=0,
            )
        )

    message = str(excinfo.value)
    assert "1111.11111" in message
    assert "2222.22222" in message


def test_single_doi_seed_resolves_with_full_record():
    """The DOI path is unchanged — this is the resolution the refused form used
    to stand in for in this file."""
    work = _work(openalex_id="W100", arxiv_id="2301.07041")
    client = _make_client([_ok_response({"results": [work]})])
    resolved, failures = asyncio.run(
        fetch_seeds([{"doi": "10.1/x"}], client, api_key="k", sleep_ms=0)
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


def test_doi_seed_emits_doi_filter_on_the_wire():
    """DOI seeds must filter on ``doi:``.

    OpenAlex rejects ``ids.doi:`` with HTTP 400 ("ids.doi is not a valid field").
    Asserting the emitted ``filter=`` expression — not the parsed response — is
    what makes this a regression test: a filter the API rejects is otherwise
    indistinguishable from one it accepts under a mocked transport.
    """
    work = _work(openalex_id="W200", arxiv_id=None, doi="https://doi.org/10.1/x")
    client = _make_client([_ok_response({"results": [work]})])
    asyncio.run(fetch_seeds([{"doi": "10.1/x"}], client, api_key="k", sleep_ms=0))

    params = client.get.call_args.kwargs["params"]
    assert params["filter"] == "doi:10.1/x"
    assert not params["filter"].startswith("ids.doi:")


def test_doi_seed_url_form_emits_doi_filter_on_the_wire():
    """The https://doi.org/… form is accepted upstream too — pass it through as-is."""
    work = _work(openalex_id="W200", arxiv_id=None, doi="https://doi.org/10.1/x")
    client = _make_client([_ok_response({"results": [work]})])
    asyncio.run(
        fetch_seeds(
            [{"doi": "https://doi.org/10.1/x"}], client, api_key="k", sleep_ms=0
        )
    )

    params = client.get.call_args.kwargs["params"]
    assert params["filter"] == "doi:https://doi.org/10.1/x"


def test_single_seed_not_found_raises():
    client = _make_client([_ok_response({"results": []})])
    with pytest.raises(ValueError):
        asyncio.run(
            fetch_seeds([{"doi": "10.1/missing"}], client, api_key="k", sleep_ms=0)
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
            [{"doi": "10.1/one"}, {"doi": "10.1/two"}],
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
            [{"doi": "10.1/one"}, {"doi": "10.1/missing"}],
            client,
            api_key="k",
            sleep_ms=0,
        )
    )
    assert len(resolved) == 1
    assert len(failures) == 1
    assert failures[0]["seed"] == {"doi": "10.1/missing"}
    assert resolved[0].node_id == "arxiv:1111.11111"


def test_empty_seed_list_raises():
    client = _make_client([])
    with pytest.raises(ValueError):
        asyncio.run(fetch_seeds([], client, api_key="k", sleep_ms=0))


def test_unrecognized_seed_shape_recorded_as_failure():
    client = _make_client([])  # no HTTP calls expected
    resolved, failures = asyncio.run(
        fetch_seeds(
            [{"unknown": "x"}, {"doi": "10.1/one"}],
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
            fetch_seeds([{"doi": "10.1/one"}], client, api_key="k", sleep_ms=0)
        )

    # Now pair it with a successful seed so we can inspect failures.
    ok = _ok_response({"results": [_work(openalex_id="W1", arxiv_id="2222.22222")]})
    client2 = AsyncMock(spec=httpx.AsyncClient)
    client2.get = AsyncMock(side_effect=[httpx.ConnectError("boom"), ok])
    resolved, failures = asyncio.run(
        fetch_seeds(
            [{"doi": "10.1/one"}, {"doi": "10.1/two"}],
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


def test_importing_the_pipeline_module_does_not_load_the_environment(tmp_path):
    """Environment loading is not an import side effect (finding 069febda).

    `pipeline.py` used to call `load_dotenv()` in its module body, so importing
    it — or anything that transitively pulled it in, which is most of the domain
    — read `.env` off the filesystem before a line of caller code ran.
    `apps/viewer/generate.py` imported `pipeline_graph` at CALL time purely to
    dodge that.

    Run in a SUBPROCESS: `idiograph.domains.arxiv.pipeline` is already in
    `sys.modules` by the time this file executes, so an in-process re-import
    would be a no-op and would pass vacuously.

    The probe counts calls rather than watching the filesystem: `dotenv.load_dotenv`
    is replaced before the module is imported, and `pipeline`'s own
    `from dotenv import load_dotenv` then binds the replacement. The second half
    is what keeps the assertion honest — `_get_api_key`, the module's one reader
    of process environment, must still load the environment before reading it,
    so a fix that merely deleted the call would fail here rather than pass.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import dotenv\n"
        "calls = []\n"
        "dotenv.load_dotenv = lambda *a, **k: calls.append(1)\n"
        "\n"
        "import idiograph.domains.arxiv.pipeline as pipeline\n"
        "at_import = len(calls)\n"
        "\n"
        "pipeline._get_api_key()\n"
        "at_read = len(calls)\n"
        "\n"
        "print(at_import, at_read)\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "OPENALEX_API_KEY": "sentinel"},
        check=True,
    )

    at_import, at_read = completed.stdout.split()
    assert at_import == "0", "importing the pipeline module loaded the environment"
    assert at_read == "1", "_get_api_key read the environment without loading it"
