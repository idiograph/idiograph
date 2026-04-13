# Idiograph — OpenAlex CRISPR Validation Findings
**Status:** FROZEN — terminal artifact of the OpenAlex validation spike
**Created:** 2026-04-13
**Spike spec:** spec-openalex-validation-spike.md
**Branch:** feat/openalex-validation-spike

## Pass 1 — Seed Resolution

| Seed | OpenAlex ID | DOI | Year | referenced_works | counts_by_year | abstract |
|---|---|---|---|---|---|---|
| Doudna/Charpentier 2012 | W2045435533 | 10.1126/science.1225829 | 2012 | 48 | 15 | present |
| Zhang 2013 | W2064815984 | 10.1126/science.1231143 | 2013 | 32 | 15 | present |

All Pass 1 success criteria met for both seeds. No resolution fallback needed — both DOIs resolved cleanly on first attempt.

## Pass 2 — Overlap Zone

- Doudna reference count: 48
- Zhang reference count: 32
- Intersection: 10 (exactly at the green threshold)
- Doudna only: 38
- Zhang only: 22

**Recommendation: GREEN.** The demo premise survives contact with data. Multi-seed convergence narrative per AMD-017 is viable with this seed pair.

### Intersection papers

- (2007) Phage Response to CRISPR-Encoded Resistance in Streptococcus thermophilus — W2135598273
- (2008) Small CRISPR RNAs Guide Antiviral Defense in Prokaryotes — W2010986636
- (2009) Short motif sequences determine the targets of the prokaryotic CRISPR defence system — W2149751339
- (2010) A TALE nuclease architecture for efficient genome editing — W2025074364
- (2010) Targeting DNA Double-Strand Breaks with TAL Effector Nucleases — W2112006537
- (2010) The CRISPR/Cas bacterial immune system cleaves bacteriophage and plasmid DNA — W1978094899
- (2011) CRISPR RNA maturation by trans-encoded small RNA and host factor RNase III — W2120503144
- (2011) CRISPR-Cas Systems in Bacteria and Archaea: Versatile Small RNAs for Adaptive Defense and Regulation — W2131527797
- (2011) Evolution and classification of the CRISPR–Cas systems — W2163241233
- (2011) The Streptococcus thermophilus CRISPR/Cas system provides immunity in Escherichia coli — W2112295554

## Implications for AMD-017

The intersection is narrow but substantively correct. The shared references cluster in 2007–2010 and span two themes that map directly onto Node 5.5's closed vocabulary: early CRISPR mechanistic foundation (theoretical_foundation, methodological_precursor) and TALE-based genome editing precursors (cross_domain_source or methodological_precursor, depending on framing). This is the "shared intellectual foundation" AMD-017 predicted would surface — validated structurally rather than just claimed rhetorically.

Edge case flagged: the intersection meets the green threshold at exactly 10 papers. Had it come back at 9 we would be in yellow territory debating depth=2 expansion. The narrow margin is worth remembering when Node 3 parameters (N_backward, lambda) are tuned — parameter changes that shrink either seed's reference inclusion will also shrink the intersection, potentially below demo viability.

## Downstream Cleaning Tasks Exposed

- OpenAlex titles contain HTML tags (<i>, etc.). Renderer or persistence layer must strip or render these. Not a spike blocker.
- Windows stdout uses cp1252 and mojibakes non-ASCII characters. UTF-8 file writes are correct; stdout rendering is a terminal concern only. sys.stdout.reconfigure(encoding="utf-8") at script entry would fix if it matters later.

## Recommended Next Steps

- Merge feat/openalex-validation-spike to main.
- Next spike: citation acceleration coverage (Node 4 gate). Validate that forward traversal from these seeds produces the counts_by_year density needed for the alpha/beta ranking function. counts_by_year=15 on both seeds is promising but the gate is per-citing-paper, not per-seed.
- Begin Node 0 and Node 3 implementation once the above is green. LLM nodes (0.5, 5.5) remain downstream of pipeline core.
