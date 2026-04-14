# Findings — Citation Acceleration Coverage Spike

**Status:** FROZEN
**Date:** 2026-04-14
**Spike spec:** `docs/specs/spec-citation-acceleration-spike.md`
**Branch:** `feat/citation-acceleration-spike`

---

## Verdict: GREEN

**100.0% of sampled citing papers have `counts_by_year_len >= 3`.** All three
year bands exceed the 30% per-band floor by a wide margin (all bands at 100%).
Full alpha/beta ranking is viable. No fallback required. `fallback_parameters: null`.

---

## Pass 1 — Seed cited_by_count

| Seed | OpenAlex ID | Year | cited_by_count | counts_by_year_len |
|---|---|---|---|---|
| Doudna/Charpentier — *A Programmable Dual-RNA–Guided DNA Endonuclease in Adaptive Bacterial Immunity* | W2045435533 | 2012 | 16,990 | 15 |
| Zhang — *Multiplex Genome Engineering Using CRISPR/Cas Systems* | W2064815984 | 2013 | 15,568 | 15 |

Both seeds resolved. Each has 15 years of `counts_by_year` data (2012–2026 and
2013–2026 respectively). Forward neighborhoods are on the order of 16k papers
per seed — confirming the stratified sampling decision over a full pull.

---

## Pass 2 — Stratified Citing Sample

Target: 50 citing papers per seed (100 total), stratified across three year bands.

| Seed | Band | Year range | Target | Actual |
|---|---|---|---|---|
| W2045435533 | recent | 2022–2025 | 20 | 20 |
| W2045435533 | mid    | 2017–2021 | 20 | 20 |
| W2045435533 | early  | 2013–2016 | 10 | 10 |
| W2064815984 | recent | 2022–2025 | 20 | 20 |
| W2064815984 | mid    | 2017–2021 | 20 | 20 |
| W2064815984 | early  | 2013–2016 | 10 | 10 |
| **Total** |  |  | **100** | **100** |

No shortfalls. Every seed × band call returned the full target count.

---

## Pass 3 — Coverage Statistics

### Overall (n = 100)

| Metric | Value |
|---|---|
| Total papers | 100 |
| counts_by_year_len >= 3 | 100 (100.0%) |
| counts_by_year_len >= 1 | 100 (100.0%) |
| counts_by_year_len == 0 | 0 (0.0%) |
| Median counts_by_year_len | 7.0 |
| Min counts_by_year_len | 3 |
| Max counts_by_year_len | 15 |
| acceleration_viable | 100 (100.0%) |

### Per-band breakdown

| Metric | recent (n=40) | mid (n=40) | early (n=20) |
|---|---|---|---|
| counts_by_year_len >= 3 | 40 (100.0%) | 40 (100.0%) | 20 (100.0%) |
| counts_by_year_len >= 1 | 40 (100.0%) | 40 (100.0%) | 20 (100.0%) |
| counts_by_year_len == 0 | 0 (0.0%)    | 0 (0.0%)    | 0 (0.0%)    |
| Median counts_by_year_len | 5.0 | 9.0 | 14.0 |
| Min counts_by_year_len | 3 | 7 | 12 |
| Max counts_by_year_len | 8 | 11 | 15 |
| acceleration_viable | 40 (100.0%) | 40 (100.0%) | 20 (100.0%) |

Median `counts_by_year_len` scales cleanly with band age (5 → 9 → 14), as
expected: older papers have had more years to accumulate year-over-year counts.

### Per-seed breakdown

| Metric | W2045435533 (n=50) | W2064815984 (n=50) |
|---|---|---|
| counts_by_year_len >= 3 | 50 (100.0%) | 50 (100.0%) |
| counts_by_year_len >= 1 | 50 (100.0%) | 50 (100.0%) |
| counts_by_year_len == 0 | 0 (0.0%)    | 0 (0.0%)    |
| Median counts_by_year_len | 7.5 | 7.0 |
| Min counts_by_year_len | 3 | 3 |
| Max counts_by_year_len | 15 | 15 |
| acceleration_viable | 50 (100.0%) | 50 (100.0%) |

Both seeds behave identically on coverage. No asymmetry between the two
foundational CRISPR papers.

---

## Fallback Parameters

None required. Verdict is GREEN.

`fallback_parameters: null` in `pass_3_coverage_report.json`.

For the record, the spec's fallback schema is preserved for future spikes or
corpus changes:
- RED fallback: `alpha=1, beta=0, age_filter_years=null`
- YELLOW fallback: `alpha=1, beta=1, age_filter_years=<n>`

---

## Data Anomalies

**One deviation from the spec, surfaced and corrected mid-spike.**

The spec originally showed the OpenAlex forward-traversal filter as
`cited_by:<openalex_id>`. OpenAlex has no `cited_by` filter — the correct filter
for "papers that cite work X" is `cites:<openalex_id>`. The Pass 2 implementation
used `cites:` (the correct semantic), all 100 papers returned successfully, and
the spec was corrected in commit `acf049e` before the spike froze.

No other anomalies:
- Every retrieved paper had a `counts_by_year` field present (none null, none
  missing, none empty).
- No unexpected structures in the OpenAlex response.
- `cited_by_count` present on every paper.
- `publication_year` present on every paper.

---

## Implications for Node 4 Implementation

The β term in the Node 4 ranking function
(`score = α(velocity) + β(acceleration) × recency_weight`) is fully supported
by OpenAlex data for the CRISPR corpus. Every sampled citing paper — across
recent, mid, and early bands — has enough `counts_by_year` data points to
compute citation acceleration. No age filter is required, no velocity-only
fallback is required, and no band-specific degradation was observed. Node 4
can be implemented with both α and β active from the outset, and the ranking
function does not need a runtime branch for coverage gaps. Parameter tuning
for α and β remains an open design question, but it is a tuning question, not
a data-availability question.

---

## Recommended Next Steps

1. Open PR for `feat/citation-acceleration-spike` with this findings file as the
   terminal artifact. Merge after review.
2. Freeze the spike spec (`spec-citation-acceleration-spike.md`) per the
   "Freezes when" clause — findings are committed on the branch.
3. Proceed to Node 3 / Node 4 implementation planning with α/β ranking as the
   baseline. Defer the YELLOW/RED fallback code paths; they are not needed for
   this corpus.
4. Document the reference year assumption (2026) in the eventual Node 4 spec,
   and decide whether it should be a runtime parameter or a build-time constant.
5. If the corpus is later extended beyond the CRISPR seeds, re-run this spike
   against the new seeds before assuming GREEN carries over. Coverage is a
   per-corpus question, not a per-tool question.
