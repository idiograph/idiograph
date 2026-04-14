# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Pass 2 — stratified sample of citing papers for each CRISPR seed."""

import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.openalex.org"
SLEEP_SECONDS = 0.150

SEEDS = ["W2045435533", "W2064815984"]

BANDS = [
    ("recent", 2022, 2025, 20),
    ("mid", 2017, 2021, 20),
    ("early", 2013, 2016, 10),
]

OUTPUT_PATH = Path(__file__).parent / "data" / "pass_2_citing_sample.json"


def _api_key() -> str:
    key = os.environ.get("OPENALEX_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENALEX_API_KEY is not set. Add it to .env (see .env.example). "
            "All OpenAlex calls in this spike require a free API key."
        )
    return key


def fetch_citing_band(
    seed_id: str, year_start: int, year_end: int, per_page: int
) -> list[dict]:
    params = {
        "filter": f"cites:{seed_id},publication_year:{year_start}-{year_end}",
        "sort": "cited_by_count:desc",
        "per-page": per_page,
        "api_key": _api_key(),
    }
    response = httpx.get(f"{BASE_URL}/works", params=params, timeout=30.0)
    response.raise_for_status()
    data = response.json()
    time.sleep(SLEEP_SECONDS)
    return data.get("results", [])


def record_from_work(work: dict, seed_id: str, band: str) -> dict:
    counts_by_year = work.get("counts_by_year")
    if counts_by_year is None:
        counts_by_year = []
    openalex_url = work.get("id", "")
    openalex_id = openalex_url.rsplit("/", 1)[-1] if openalex_url else ""
    return {
        "openalex_id": openalex_id,
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "citation_count": work.get("cited_by_count"),
        "counts_by_year_raw": counts_by_year,
        "counts_by_year_len": len(counts_by_year),
        "has_min_3_points": len(counts_by_year) >= 3,
        "seed_id": seed_id,
        "band": band,
    }


def main() -> None:
    results: dict[str, dict[str, list[dict]]] = {}

    for seed_id in SEEDS:
        results[seed_id] = {}
        for band_name, year_start, year_end, target in BANDS:
            works = fetch_citing_band(seed_id, year_start, year_end, target)
            records = [record_from_work(w, seed_id, band_name) for w in works]
            results[seed_id][band_name] = records

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUTPUT_PATH}")
    print()

    targets = {name: target for name, _, _, target in BANDS}
    grand_total = 0
    for seed_id in SEEDS:
        print(f"Seed {seed_id}:")
        for band_name, _, _, _ in BANDS:
            actual = len(results[seed_id][band_name])
            target = targets[band_name]
            flag = "" if actual >= target else f"  (shortfall: {target - actual})"
            print(f"  {band_name:<7} {actual:>3} / {target:<3}{flag}")
            grand_total += actual
        print()

    print(f"Grand total papers retrieved: {grand_total} / 100")


if __name__ == "__main__":
    main()
