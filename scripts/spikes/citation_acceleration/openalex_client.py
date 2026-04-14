# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.openalex.org"
SLEEP_SECONDS = 0.150


def _api_key() -> str:
    key = os.environ.get("OPENALEX_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENALEX_API_KEY is not set. Add it to .env (see .env.example). "
            "All OpenAlex calls in this spike require a free API key."
        )
    return key


def _params(extra: dict | None = None) -> dict:
    params = {"api_key": _api_key()}
    if extra:
        params.update(extra)
    return params


def get_work(openalex_id_or_doi: str) -> dict:
    if openalex_id_or_doi.startswith("10."):
        path = f"/works/https://doi.org/{openalex_id_or_doi}"
    elif openalex_id_or_doi.startswith("https://doi.org/"):
        path = f"/works/{openalex_id_or_doi}"
    else:
        path = f"/works/{openalex_id_or_doi}"

    url = f"{BASE_URL}{path}"
    response = httpx.get(url, params=_params(), timeout=30.0)
    response.raise_for_status()
    data = response.json()
    time.sleep(SLEEP_SECONDS)
    return data


def search_works(query: str, per_page: int = 5) -> list[dict]:
    url = f"{BASE_URL}/works"
    response = httpx.get(
        url,
        params=_params({"search": query, "per-page": per_page}),
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    time.sleep(SLEEP_SECONDS)
    return data.get("results", [])
