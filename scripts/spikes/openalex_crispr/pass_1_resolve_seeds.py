# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from openalex_client import get_work, search_works

OUTPUT_DIR = Path(__file__).parent / "output"

DOUDNA_DOI = "10.1126/science.1225829"
DOUDNA_TITLE = (
    "A Programmable Dual-RNA-Guided DNA Endonuclease in Adaptive Bacterial Immunity"
)
DOUDNA_FIRST_AUTHOR = "Jinek"
DOUDNA_YEAR = 2012

ZHANG_DOI = "10.1126/science.1231143"
ZHANG_TITLE = "Multiplex Genome Engineering Using CRISPR/Cas Systems"
ZHANG_FIRST_AUTHOR = "Cong"
ZHANG_YEAR = 2013


def resolve_seed(
    label: str,
    doi: str,
    title: str,
    first_author: str,
    year: int,
) -> dict:
    work: dict | None = None
    try:
        work = get_work(doi)
    except httpx.HTTPStatusError as exc:
        print(
            f"[{label}] DOI lookup failed ({exc.response.status_code}); falling back to search."
        )
    except httpx.HTTPError as exc:
        print(f"[{label}] DOI lookup errored ({exc!r}); falling back to search.")

    if work is None:
        results = search_works(f"{title} {first_author}", per_page=5)
        candidates = [
            r
            for r in results
            if r.get("publication_year") == year
            and first_author.lower()
            in " ".join(
                (a.get("author", {}) or {}).get("display_name", "") or ""
                for a in (r.get("authorships") or [])
            ).lower()
        ]
        if not candidates:
            raise RuntimeError(
                f"[{label}] Could not resolve seed. DOI {doi} failed and search "
                f"for '{title}' + '{first_author}' ({year}) returned no matching candidates."
            )
        work = candidates[0]

    missing_required: list[str] = []
    for field in ("id", "doi", "title", "publication_year", "authorships"):
        if not work.get(field):
            missing_required.append(field)
    referenced_works = work.get("referenced_works") or []
    if len(referenced_works) < 5:
        missing_required.append(
            f"referenced_works (len={len(referenced_works)}, need >=5)"
        )

    if missing_required:
        raise RuntimeError(
            f"[{label}] Seed resolved but required fields are missing or thin: "
            f"{', '.join(missing_required)}"
        )

    return work


def inspect(label: str, work: dict) -> None:
    counts_by_year = work.get("counts_by_year") or []
    abstract = work.get("abstract_inverted_index")
    print(f"--- {label} ---")
    print(f"  id:                 {work.get('id')}")
    print(f"  doi:                {work.get('doi')}")
    print(f"  title:              {work.get('title')}")
    print(f"  publication_year:   {work.get('publication_year')}")
    print(f"  authorships (n):    {len(work.get('authorships') or [])}")
    print(f"  referenced_works:   {len(work.get('referenced_works') or [])}")
    print(
        f"  counts_by_year:     {len(counts_by_year)}"
        + ("" if len(counts_by_year) >= 3 else "  [FLAG: <3 entries]")
    )
    print(f"  abstract_inverted_index: {'present' if abstract else 'ABSENT  [FLAG]'}")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        doudna = resolve_seed(
            "doudna_2012", DOUDNA_DOI, DOUDNA_TITLE, DOUDNA_FIRST_AUTHOR, DOUDNA_YEAR
        )
    except RuntimeError as exc:
        print(f"HALT: {exc}")
        return 1

    doudna_path = OUTPUT_DIR / "seed_doudna_2012.json"
    with open(doudna_path, "w", encoding="utf-8") as f:
        json.dump(doudna, f, ensure_ascii=False)

    try:
        zhang = resolve_seed(
            "zhang_2013", ZHANG_DOI, ZHANG_TITLE, ZHANG_FIRST_AUTHOR, ZHANG_YEAR
        )
    except RuntimeError as exc:
        print(f"HALT: {exc}")
        return 1

    zhang_path = OUTPUT_DIR / "seed_zhang_2013.json"
    with open(zhang_path, "w", encoding="utf-8") as f:
        json.dump(zhang, f, ensure_ascii=False)

    print()
    inspect("doudna_2012", doudna)
    print()
    inspect("zhang_2013", zhang)
    print()
    print(f"Wrote {doudna_path}")
    print(f"Wrote {zhang_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
