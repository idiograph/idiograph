# Idiograph — Citation Acceleration Coverage Spike Spec
**Status:** LIVING — re-read at the top of every prompt before executing any step
**Created:** 2026-04-14
**Spike branch:** feat/citation-acceleration-spike
**Companion documents:** spec-arxiv-pipeline-final.md, findings-openalex-crispr.md
**Freezes when:** findings-citation-acceleration.md is committed to the branch

---

## Purpose

Validate whether OpenAlex `counts_by_year` coverage on the *citing neighborhood* of
the CRISPR seeds is sufficient to support the Node 4 α/β ranking function before any
pipeline implementation begins.

The prior spike validated backward reference overlap (AMD-017, GREEN). This spike
validates forward traversal data quality. These are independent questions. A GREEN on
AMD-017 does not imply a GREEN here.

---

## The Gate

Node 4's ranking function:
```
score = α(citation_velocity) + β(citation_acceleration) × recency_weight
```

`citation_acceleration` = rate of change of `citation_velocity`. Requires **≥3 time
points** in `counts_by_year` per paper.

**The gate is per-citing-paper, not per-seed.** The seeds themselves have 15
`counts_by_year` entries each — that tells us nothing about their citing neighborhoods.
We need to know what fraction of papers *in the forward traversal* have enough data
points to compute acceleration.

**Threshold (to be determined from data):** if fewer than 50% of sampled citing papers
have ≥3 `counts_by_year` entries, the β term is not viable for this corpus. Fallback:
`α=1, β=0` (velocity only). The fallback must be declared explicitly — not silent.

---

## Sampling Strategy

The CRISPR seeds are landmark papers. Their full citing neighborhoods are enormous —
tens of thousands of papers. A full pull is unnecessary and expensive. Structured
sampling is required.

**Sample size:** 50 citing papers per seed (100 total). This is sufficient to assess
coverage distribution without exhausting API budget.

**Sampling structure — stratified by publication year:**
Pull citing papers in three year bands to detect whether counts_by_year coverage
degrades for older or newer papers:

| Band | Year range | Papers per seed |
|---|---|---|
| Recent | 2022–2025 | 20 |
| Mid | 2017–2021 | 20 |
| Early | 2013–2016 | 10 |

Rationale: coverage may be systematically thinner for recently published papers
(not enough time for year-over-year accumulation) or for older papers (pre-OpenAlex
corpus coverage gaps). Stratified sampling surfaces this pattern; a random sample
would average it away.

**OpenAlex filter for forward traversal:**
```
cited_by: <openalex_id>
publication_year: <range>
sort: cited_by_count:desc
per-page: 20 (or 10 for early band)
```

Taking the most-cited papers per band is a conservative choice — if high-citation
papers have thin coverage, the β term is in serious trouble.

---

## What to Measure Per Paper

For each sampled citing paper, record:

| Field | Source | Notes |
|---|---|---|
| `openalex_id` | OpenAlex | |
| `title` | OpenAlex | |
| `year` | OpenAlex | |
| `citation_count` | OpenAlex | `cited_by_count` field |
| `counts_by_year_len` | OpenAlex | Length of `counts_by_year` list |
| `counts_by_year_raw` | OpenAlex | Full list — needed to compute velocity/acceleration |
| `has_min_3_points` | computed | `counts_by_year_len >= 3` |
| `velocity` | computed | `citation_count / months_since_publication` |
| `acceleration_viable` | computed | True if has_min_3_points and velocity computable |

---

## Passes

### Pass 1 — Seed cited_by_count
Fetch both seeds from OpenAlex by their known IDs and record `cited_by_count`. This
establishes the scale of the forward neighborhood before sampling.

Seeds:
- Doudna/Charpentier 2012: W2045435533
- Zhang 2013: W2064815984

Success criteria: both IDs resolve, `cited_by_count` recorded for each.

Output: `pass_1_seed_citing_counts.json`

---

### Pass 2 — Stratified citing paper sample
For each seed × year band, pull the top papers by citation count and record the fields
listed above.

Success criteria:
- 50 papers retrieved per seed (100 total), distributed per band targets
- `counts_by_year` field present on all records (may be empty list — that is data)
- All records written to `pass_2_citing_sample.json`

Rate limiting: 150ms sleep between API calls. Do not batch — respect the 10 rps cap.

Output: `pass_2_citing_sample.json`

---

### Pass 3 — Coverage analysis
Compute aggregate statistics over the 100-paper sample:

- % of papers with `counts_by_year_len >= 3` (overall and per band)
- % of papers with `counts_by_year_len >= 1` (velocity viable)
- % of papers with `counts_by_year_len == 0` (no data)
- Median `counts_by_year_len` overall and per band
- % of papers where `acceleration_viable == True`

Verdict logic:
- **GREEN:** ≥50% of sampled papers have `counts_by_year_len >= 3` across all bands.
  β term is viable. Implement full α/β ranking.
- **YELLOW:** ≥50% overall but coverage degrades significantly in one band (e.g.,
  <30% in Recent). β term viable with a declared age filter — papers below a minimum
  age threshold fall back to velocity-only. Age threshold becomes a declared parameter.
- **RED:** <50% overall. β term is not supported by available data. Implement
  velocity-only ranking, α=1, β=0. Document explicitly in pipeline and renderer.

Output: `pass_3_coverage_report.json`

---

## Terminal Artifact

`docs/specs/findings-citation-acceleration.md` — records:
- Pass 1 seed citing counts
- Pass 3 coverage statistics (full table)
- Verdict (GREEN / YELLOW / RED) with rationale
- If YELLOW: declared age filter threshold
- If RED or YELLOW: confirmed fallback parameters (α=1, β=0 or mixed)
- Any data anomalies observed (missing fields, unexpected structures)
- Recommended next steps

This file freezes the spec. Do not modify the spec after the findings are committed.

---

## File Layout

```
scripts/spikes/citation_acceleration/
    __init__.py
    openalex_client.py          # copy from openalex_crispr spike, no changes
    pass_1_seed_citing_counts.py
    pass_2_citing_sample.py
    pass_3_coverage_analysis.py
    data/
        pass_1_seed_citing_counts.json
        pass_2_citing_sample.json
        pass_3_coverage_report.json

docs/specs/
    spec-citation-acceleration-spike.md   (this file, committed to branch)
    findings-citation-acceleration.md     (terminal artifact, written at end)
```

---

## Anti-Drift Constraints

These constraints must be respected in every prompt, in every step:

1. **Read this spec before executing any step.** If code contradicts the spec, stop
   and flag the contradiction — do not invent a resolution.

2. **No pipeline code.** This is a data validation spike, not an implementation spike.
   No Node 3, Node 4, or pipeline scaffolding belongs in this branch.

3. **44-test gate.** All 44 existing tests must pass before and after any commit.
   Run `uv run pytest` before committing. If tests fail, stop and report — do not
   push failing tests.

4. **No silent fallbacks.** If `counts_by_year` is missing or empty on a paper,
   record it as-is. Do not substitute a default. The absence is data.

5. **No modifications to `openalex_client.py`.** Copy it from the CRISPR spike
   directory. If a change is required, flag it explicitly — do not modify silently.

6. **150ms sleep between API calls.** Hard requirement. Do not reduce.

7. **The spike spec is the authority.** If the spec and the session summary conflict,
   the spec wins. Flag the conflict rather than resolving it silently.

8. **Branch only.** All work on `feat/citation-acceleration-spike`. No commits to
   `main` until PR is approved and merged.

---

## Environment

- Python 3.13, `uv` package manager
- `.env` in repo root with `OPENALEX_API_KEY` — loaded via `python-dotenv`
- Missing key raises immediately — no silent fallback to anonymous tier
- `httpx` for HTTP (sync), `python-dotenv` for env loading
- `ruff check` and `ruff format` before every commit

---

## Success Criteria Summary

| Pass | Criterion | Status |
|---|---|---|
| Pass 1 | Both seeds resolve, cited_by_count recorded | — |
| Pass 2 | 100 citing papers retrieved, counts_by_year present on all | — |
| Pass 3 | Coverage statistics computed, verdict assigned | — |
| Terminal | findings-citation-acceleration.md committed | — |

Spike is complete when the terminal artifact is committed and all 44 tests pass.
