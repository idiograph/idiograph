# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from openalex_client import get_work

OUTPUT_DIR = Path(__file__).parent / "output"
DOUDNA_SEED_PATH = OUTPUT_DIR / "seed_doudna_2012.json"
ZHANG_SEED_PATH = OUTPUT_DIR / "seed_zhang_2013.json"
DOUDNA_REFS_PATH = OUTPUT_DIR / "references_doudna.json"
ZHANG_REFS_PATH = OUTPUT_DIR / "references_zhang.json"
OVERLAP_PATH = OUTPUT_DIR / "overlap_report.json"


def load_seed(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def short_id(work_url: str) -> str:
    return work_url.rsplit("/", 1)[-1]


def recommendation(intersection_count: int) -> str:
    if intersection_count >= 10:
        return "GREEN — demo premise survives data contact"
    if intersection_count >= 3:
        return "YELLOW — worth checking depth=2 before deciding"
    return "RED — seed pair or convergence model needs rethinking"


def main() -> int:
    doudna = load_seed(DOUDNA_SEED_PATH)
    zhang = load_seed(ZHANG_SEED_PATH)

    doudna_ref_ids = [short_id(u) for u in doudna.get("referenced_works") or []]
    zhang_ref_ids = [short_id(u) for u in zhang.get("referenced_works") or []]

    doudna_set = set(doudna_ref_ids)
    zhang_set = set(zhang_ref_ids)
    intersection_ids = doudna_set & zhang_set
    doudna_only = doudna_set - zhang_set
    zhang_only = zhang_set - doudna_set

    union_ids = doudna_set | zhang_set
    print(
        f"Fetching {len(union_ids)} unique referenced works "
        f"(doudna={len(doudna_set)}, zhang={len(zhang_set)}, "
        f"intersection={len(intersection_ids)})..."
    )

    cache: dict[str, dict] = {}
    for i, wid in enumerate(sorted(union_ids), start=1):
        try:
            cache[wid] = get_work(wid)
        except Exception as exc:
            print(f"  [{i}/{len(union_ids)}] {wid}: FAILED ({exc!r})")
            cache[wid] = {"id": wid, "_fetch_error": repr(exc)}
        if i % 10 == 0:
            print(f"  {i}/{len(union_ids)}")

    doudna_refs = [cache[wid] for wid in doudna_ref_ids if wid in cache]
    zhang_refs = [cache[wid] for wid in zhang_ref_ids if wid in cache]

    with open(DOUDNA_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(doudna_refs, f, ensure_ascii=False)
    with open(ZHANG_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(zhang_refs, f, ensure_ascii=False)

    intersection_records = []
    for wid in sorted(intersection_ids):
        work = cache.get(wid, {})
        intersection_records.append(
            {
                "id": work.get("id") or f"https://openalex.org/{wid}",
                "doi": work.get("doi"),
                "title": work.get("title"),
                "year": work.get("publication_year"),
            }
        )

    intersection_records.sort(
        key=lambda r: (r.get("year") or 0, r.get("title") or ""),
    )

    report = {
        "doudna_seed_id": doudna.get("id"),
        "zhang_seed_id": zhang.get("id"),
        "doudna_reference_count": len(doudna_set),
        "zhang_reference_count": len(zhang_set),
        "intersection_count": len(intersection_ids),
        "doudna_only_count": len(doudna_only),
        "zhang_only_count": len(zhang_only),
        "intersection": intersection_records,
    }

    with open(OVERLAP_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print()
    print("=== Overlap Report ===")
    print(f"  doudna_reference_count: {report['doudna_reference_count']}")
    print(f"  zhang_reference_count:  {report['zhang_reference_count']}")
    print(f"  intersection_count:     {report['intersection_count']}")
    print(
        f"  doudna_only: {report['doudna_only_count']}   "
        f"zhang_only: {report['zhang_only_count']}"
    )
    print()
    print("First 5 intersection papers:")
    for rec in intersection_records[:5]:
        print(f"  - ({rec.get('year')}) {rec.get('title')}")
    print()
    print(f"Recommendation: {recommendation(report['intersection_count'])}")
    print()
    print(f"Wrote {DOUDNA_REFS_PATH}")
    print(f"Wrote {ZHANG_REFS_PATH}")
    print(f"Wrote {OVERLAP_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
