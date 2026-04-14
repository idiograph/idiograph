# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Pass 1 — fetch CRISPR seeds from OpenAlex and record cited_by_count."""

import json
import time
from pathlib import Path

from scripts.spikes.citation_acceleration.openalex_client import get_work

SEEDS = [
    ("Doudna/Charpentier 2012", "W2045435533"),
    ("Zhang 2013", "W2064815984"),
]

OUTPUT_PATH = Path(__file__).parent / "data" / "pass_1_seed_citing_counts.json"

INTER_CALL_SLEEP_SECONDS = 0.150


def fetch_seed(openalex_id: str) -> dict:
    work = get_work(openalex_id)
    counts_by_year = work.get("counts_by_year") or []
    return {
        "openalex_id": openalex_id,
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "cited_by_count": work.get("cited_by_count"),
        "counts_by_year": counts_by_year,
        "counts_by_year_len": len(counts_by_year),
    }


def main() -> None:
    records = []
    for i, (label, openalex_id) in enumerate(SEEDS):
        if i > 0:
            time.sleep(INTER_CALL_SLEEP_SECONDS)
        record = fetch_seed(openalex_id)
        record["label"] = label
        records.append(record)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUTPUT_PATH}")
    print()
    for record in records:
        print(f"  {record['label']} ({record['openalex_id']})")
        print(f"    title: {record['title']}")
        print(f"    year: {record['year']}")
        print(f"    cited_by_count: {record['cited_by_count']}")
        print(f"    counts_by_year_len: {record['counts_by_year_len']}")
        print()


if __name__ == "__main__":
    main()
