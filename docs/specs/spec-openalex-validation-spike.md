# Idiograph — OpenAlex CRISPR Validation Spike Spec
**Status:** LIVING — freezes when both passes complete and findings doc is written
**Created:** 2026-04-13
**Companion documents:** spec-arxiv-pipeline-final.md, amd-016-llm-node-placement-2.md, amd-017-multi-seed-boolean-ops.md
**Freeze trigger:** Pass 2 findings document committed under `docs/specs/findings-openalex-crispr.md` (or moved to `docs/sessions/` if a session summary supersedes it).

---

## Purpose

Validate, against real OpenAlex data, the demo premise established by AMD-017: that the CRISPR seed pair (Doudna 2012, Zhang 2013) produces a forest with a meaningful overlap zone when traversed backward.

This is a **read-only data inspection spike**. No pipeline code is being written. No cache is being built. No LLM nodes are being called. The deliverable is evidence — JSON dumps and a short findings document — sufficient to either green-light implementation of the demo pipeline or expose a gap that requires a different seed pair.

---

## Scope

### In scope
- Resolve both seeds in OpenAlex, capture canonical OpenAlex IDs.
- Pull the raw OpenAlex Work record for each seed.
- Inspect three fields per seed: `referenced_works` populated, `counts_by_year` populated with ≥3 points, abstract present and usable.
- Pull direct references (depth=1 backward) for both seeds.
- Compute the set intersection of those reference lists by OpenAlex Work ID.
- Report findings in a short markdown document with counts, examples, and a go/no-go recommendation.

### Out of scope
- Any code that lives under `src/idiograph/`. The spike is throwaway-shaped scratch work.
- Pipeline node implementations (Nodes 0/0.5/1–8).
- Caching, rate limiting beyond the `api_key=` param and a 150ms sleep between calls.
- LLM calls of any kind, including Node 5.5.
- arXiv API calls — OpenAlex only for this spike.
- Forward traversal (Node 4). Backward only.
- Cycle detection, metric computation, community detection.
- Modifications to the existing 44-test suite. The spike must not touch it.

### Explicitly deferred to later spikes
- Citation acceleration coverage analysis (Node 4 gate). Will need its own spike once Pass 1/2 land.
- Parameter tuning for λ, α, β, N_backward, N_forward, `co_citation_min_strength`.
- Weakest-link cycle suppression validation.
- LOD community count target range.

---

## Working Conventions

| Convention | Value |
|---|---|
| Branch | `feat/openalex-validation-spike` — never commit to `main` |
| Python | 3.13, managed by `uv` |
| Linting | `ruff` clean before every commit |
| HTTP client | `httpx` (sync is fine for this spike — it's a script, not pipeline code) |
| Authentication | `OPENALEX_API_KEY` loaded from `.env` via `python-dotenv`; passed as `api_key=` query param on every call. All OpenAlex calls now require a free API key — register at openalex.org. The client halts with a clear error if the key is missing. The retired `mailto=` polite-pool param is not sent. |
| Rate limiting | `time.sleep(0.150)` between calls; no other throttling |
| File encoding | `encoding="utf-8"` on every file open, no exceptions |
| Existing tests | The 44-test suite must pass before and after every change in this spike |
| Working directory | `scripts/spikes/openalex_crispr/` |
| Output directory | `scripts/spikes/openalex_crispr/output/` — committed; small JSON only |
| Shell | Windows PowerShell / Git Bash — use `Select-Object -First 30`, not `head` |

---

## File Layout

```
scripts/spikes/openalex_crispr/
  __init__.py
  openalex_client.py         ← thin httpx wrapper, api_key + sleep
  pass_1_resolve_seeds.py    ← Step 2: resolve both seeds, dump payloads
  pass_2_overlap.py          ← Step 4: pull references, compute intersection
  output/
    seed_doudna_2012.json    ← raw OpenAlex Work record
    seed_zhang_2013.json     ← raw OpenAlex Work record
    references_doudna.json   ← list of referenced Work records
    references_zhang.json    ← list of referenced Work records
    overlap_report.json      ← intersection set + counts
```

A findings document at `docs/specs/findings-openalex-crispr.md` is the spike's terminal artifact.

---

## The Two Seeds

| Lab | Year | Title (working reference) | OpenAlex ID |
|---|---|---|---|
| Doudna / Charpentier | 2012 | "A Programmable Dual-RNA–Guided DNA Endonuclease in Adaptive Bacterial Immunity" | **TBD — Pass 1 resolves** |
| Zhang | 2013 | "Multiplex Genome Engineering Using CRISPR/Cas Systems" | **TBD — Pass 1 resolves** |

Resolution strategy: search OpenAlex by DOI if known, otherwise by title + first author + year. Capture the canonical OpenAlex Work ID (`W...`) and the DOI as ground truth for Pass 2.

If either seed cannot be resolved cleanly, the spike halts and the findings document records the failure. Do not substitute a different paper without an explicit decision.

---

## Success Criteria

### Pass 1 — Resolve and inspect (per seed)

| Field | Required | Reason |
|---|---|---|
| `id` (OpenAlex Work ID) | yes | Canonical key for Pass 2 |
| `doi` | yes | External identifier, used for Node 0 input |
| `title`, `publication_year`, `authorships` | yes | Renderer contract fields |
| `referenced_works` populated, len ≥ 5 | yes | Node 3 backward traversal depends on this |
| `counts_by_year` with ≥ 3 entries | preferred | Node 4 citation acceleration gate — flag if absent but do not halt |
| `abstract_inverted_index` present | preferred | Node 5.5 input — flag if absent |

### Pass 2 — Overlap zone

| Outcome | Recommendation |
|---|---|
| Intersection ≥ 10 papers at depth=1 | Green-light. Demo premise survives data contact. |
| Intersection 3–9 papers at depth=1 | Yellow. Worth checking depth=2 before deciding. |
| Intersection 0–2 papers at depth=1 | Red. AMD-017 demo premise needs a different seed pair, or the convergence is structural rather than at the reference level (worth a separate analysis). |

The recommendation goes in the findings document with the raw counts and 3–5 example shared papers (titles + years), not just numbers.

---

## Anti-Drift Constraints

These exist because Claude Code will be doing the implementation and may try to be helpful in ways that exceed the spike:

| Do not | Reason |
|---|---|
| Add the spike's modules to the main test suite | Spike is throwaway scratch; the 44-test suite is production code |
| Build a `Node` subclass or wire into the executor | This is data inspection, not pipeline construction |
| Add a cache layer | The spike runs end-to-end in well under a minute; no cache needed |
| Use `requests` instead of `httpx` | Project convention is `httpx` |
| Skip the `api_key=` param or hardcode a key | The key must come from `.env` via `python-dotenv`; no unauthenticated calls and no keys in source |
| Pretty-print or filter the raw JSON dumps | Raw payload is the deliverable; downstream analysis depends on having it complete |
| Decide a thin field "looks fine" without reporting | The findings document records what's there, not what's interpreted |
| Substitute a different seed pair if Doudna or Zhang fails to resolve | Halt and surface the failure; substitution is a human decision |
| Touch any file under `src/idiograph/` | The spike is contained to `scripts/spikes/openalex_crispr/` |

---

## Decision Gates

After each Pass, work stops for human review before the next Pass begins.

- **After Pass 1:** Inspect both raw JSON dumps. Confirm seeds resolved correctly, fields are populated as expected. Decide whether to proceed to Pass 2 or address gaps first.
- **After Pass 2:** Read the overlap report. Apply the success criteria above. Decide whether to commit findings as-is, expand to depth=2, or escalate to a seed-selection conversation.

---

*Companion documents: spec-arxiv-pipeline-final.md, amd-016-llm-node-placement-2.md, amd-017-multi-seed-boolean-ops.md*
