# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Pass 3 — compute coverage statistics and assign verdict."""

import json
import statistics
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
INPUT_PATH = DATA_DIR / "pass_2_citing_sample.json"
OUTPUT_PATH = DATA_DIR / "pass_3_coverage_report.json"

REFERENCE_YEAR = 2026
BANDS = ("recent", "mid", "early")


def compute_velocity(citation_count: int | None, year: int | None) -> float | None:
    if citation_count is None or year is None:
        return None
    months = (REFERENCE_YEAR - year) * 12
    if months <= 0:
        return None
    return citation_count / months


def enrich(paper: dict) -> dict:
    velocity = compute_velocity(paper.get("citation_count"), paper.get("year"))
    has_min_3 = bool(paper.get("has_min_3_points"))
    enriched = dict(paper)
    enriched["velocity"] = velocity
    enriched["acceleration_viable"] = has_min_3 and velocity is not None
    return enriched


def stats_for(papers: list[dict]) -> dict:
    total = len(papers)
    lens = [p["counts_by_year_len"] for p in papers]
    ge3 = sum(1 for n in lens if n >= 3)
    ge1 = sum(1 for n in lens if n >= 1)
    eq0 = sum(1 for n in lens if n == 0)
    accel_viable = sum(1 for p in papers if p.get("acceleration_viable"))

    def pct(n: int) -> float:
        return round(100.0 * n / total, 2) if total else 0.0

    return {
        "total_papers": total,
        "counts_by_year_ge_3": ge3,
        "pct_counts_by_year_ge_3": pct(ge3),
        "counts_by_year_ge_1": ge1,
        "pct_counts_by_year_ge_1": pct(ge1),
        "counts_by_year_eq_0": eq0,
        "pct_counts_by_year_eq_0": pct(eq0),
        "median_counts_by_year_len": statistics.median(lens) if lens else 0,
        "min_counts_by_year_len": min(lens) if lens else 0,
        "max_counts_by_year_len": max(lens) if lens else 0,
        "acceleration_viable_count": accel_viable,
        "pct_acceleration_viable": pct(accel_viable),
    }


def assign_verdict(overall: dict, by_band: dict) -> tuple[str, str, dict | None]:
    overall_pct = overall["pct_counts_by_year_ge_3"]

    if overall_pct < 50.0:
        rationale = (
            f"{overall_pct}% of sampled citing papers have >=3 counts_by_year entries "
            f"(threshold: 50%). Beta term is not supported by available data. "
            f"Fallback to velocity-only ranking."
        )
        fallback = {"alpha": 1, "beta": 0, "age_filter_years": None}
        return "RED", rationale, fallback

    weak_bands = {
        name: by_band[name]["pct_counts_by_year_ge_3"]
        for name in BANDS
        if by_band[name]["pct_counts_by_year_ge_3"] < 30.0
    }

    if weak_bands:
        band_ages = {
            "recent": 1,
            "mid": 5,
            "early": 10,
        }
        healthy_ages = [
            band_ages[name]
            for name in BANDS
            if by_band[name]["pct_counts_by_year_ge_3"] >= 50.0
        ]
        age_filter = min(healthy_ages) if healthy_ages else max(band_ages.values())
        rationale = (
            f"{overall_pct}% of sampled citing papers have >=3 counts_by_year entries "
            f"(overall threshold met), but the following bands fall below 30%: "
            f"{weak_bands}. Beta term viable with age filter. Papers younger than "
            f"{age_filter} years from {REFERENCE_YEAR} fall back to velocity-only."
        )
        fallback = {"alpha": 1, "beta": 1, "age_filter_years": age_filter}
        return "YELLOW", rationale, fallback

    rationale = (
        f"{overall_pct}% of sampled citing papers have >=3 counts_by_year entries "
        f"(threshold: 50%). All bands exceed the 30% per-band floor. "
        f"Full alpha/beta ranking is viable."
    )
    return "GREEN", rationale, None


def main() -> None:
    with open(INPUT_PATH, encoding="utf-8") as f:
        pass_2 = json.load(f)

    all_papers: list[dict] = []
    by_band_papers: dict[str, list[dict]] = {name: [] for name in BANDS}
    by_seed_papers: dict[str, list[dict]] = {}

    for seed_id, bands in pass_2.items():
        by_seed_papers[seed_id] = []
        for band_name in BANDS:
            for paper in bands.get(band_name, []):
                enriched = enrich(paper)
                all_papers.append(enriched)
                by_band_papers[band_name].append(enriched)
                by_seed_papers[seed_id].append(enriched)

    overall = stats_for(all_papers)
    by_band = {name: stats_for(by_band_papers[name]) for name in BANDS}
    by_seed = {sid: stats_for(papers) for sid, papers in by_seed_papers.items()}

    verdict, rationale, fallback = assign_verdict(overall, by_band)

    report = {
        "statistics": {
            "overall": overall,
            "by_band": by_band,
            "by_seed": by_seed,
        },
        "verdict": verdict,
        "verdict_rationale": rationale,
        "fallback_parameters": fallback,
        "papers": all_papers,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUTPUT_PATH}")
    print()
    print(f"VERDICT: {verdict}")
    print(f"  {rationale}")
    if fallback is not None:
        print(f"  fallback_parameters: {fallback}")
    print()
    print("Overall statistics:")
    for k, v in overall.items():
        print(f"  {k}: {v}")
    print()
    print("Per-band statistics:")
    for band_name in BANDS:
        print(f"  {band_name}:")
        for k, v in by_band[band_name].items():
            print(f"    {k}: {v}")
    print()
    print("Per-seed statistics:")
    for sid, st in by_seed.items():
        print(f"  {sid}:")
        for k, v in st.items():
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
