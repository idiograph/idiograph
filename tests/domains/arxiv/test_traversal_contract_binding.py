# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Binds `TRAVERSAL_CONTRACT`'s prose to the code it declares (IDG-091 / IDG-043).

`models.TRAVERSAL_CONTRACT` is PROSE that DECLARES what
`pipeline.backward_traverse` and `pipeline._node3_score` decide about derived
output; `models.traversal_contract_hash` is the sha256 of that prose. Until this
file existed, nothing held the two together — an edit to the scoring block that
left the text alone made the declaration quietly wrong AND left the hash
unmoved, which is the descriptor's own purpose inverted.

Each clause is pinned in BOTH halves, and the pairing is the point:

  BEHAVIORAL half — the code does what the clause says. Every expectation here
  is computed INDEPENDENTLY, from `math` and hand-written fixtures, never by
  calling `_node3_score` or `backward_traverse` a second way. A test that
  derived its expectation from the subject would pass against any formula.

  TEXT half — a substring tripwire against the live `TRAVERSAL_CONTRACT`
  constant, pinning a clause's load-bearing TOKENS rather than whole sentences,
  so it survives a typo fix or a re-wrap and does not survive a change of rule.
  Be honest about what this does: it does NOT prove the prose is correct. It
  does not even prove the prose is well-formed English. It makes an
  amendment to a pinned clause impossible to land without a test naming the
  behavioral pin the author must re-examine, and it stops a behavioral pin from
  being satisfied by moving the very declaration it is measured against.

THIS FILE ADDS TESTS AND NOTHING ELSE. `TRAVERSAL_CONTRACT`, `_node3_score` and
`backward_traverse` are untouched: the contract's text is a content address, and
amending it is a separately gated operator action with a real bill attached.

WHEN A BEHAVIORAL PIN HERE FAILS, READ THIS. It means the declaration and the
implementation have diverged — the most valuable thing this file can tell you,
and NOT a local repair. Editing the prose to match the code moves a content
address and backdates a rule change that never happened; editing the code to
match the prose changes derived output for every run. Report the divergence by
clause name, with the declared and the observed behavior; the operator routes
the amendment.

The `SELECTION RULE — DECLARED ABSENT` clause is deliberately NOT pinned here.
A negative fact has no code to pin against, and how to guard a declared absence
is an open question routed elsewhere. Its absence from this file is a decision,
not an oversight.

Overlap with `test_pipeline_node3.py` is INTENDED and is not duplication to
clean up. Those tests check behavior and fail when behavior breaks; these bind
behavior to a declaration and fail when the two drift apart. They fail for
different reasons and instruct differently.
"""

import asyncio
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from idiograph.domains.arxiv import models
from idiograph.domains.arxiv.models import (
    TRAVERSAL_CONTRACT,
    CycleCleanResult,
    Node3Result,
    PaperRecord,
)
from idiograph.domains.arxiv.pipeline import _node3_score, backward_traverse

# ── The declaration, as the tripwires read it ───────────────────────────────

#: `TRAVERSAL_CONTRACT` with every run of whitespace collapsed to one space.
#: EVERY text tripwire below asserts against this view rather than the raw
#: constant, for one reason: the constant is hard-wrapped prose, so a token long
#: enough to be load-bearing usually straddles a line break. Matching the
#: collapsed view means a re-wrap of a paragraph — the shape a typo fix or a
#: reflow takes — does not fire a tripwire whose message would then wrongly
#: announce that a rule was amended, while any change to the rule's own words
#: still does. Whitespace-only edits are not unguarded: they move the sha256,
#: which is `test_models.py`'s subject, not this file's.
_CONTRACT_ONE_LINE = " ".join(TRAVERSAL_CONTRACT.split())

#: What the author of a red text tripwire has to be told. Every text-half
#: failure message ends with this, because the remedy is never "update the
#: string in the test".
_AMENDMENT_INSTRUCTION = (
    "This is a TEXT TRIPWIRE, not a behavioral check. Do NOT satisfy it by "
    "editing this test. Either the clause was amended — in which case the "
    "content address has moved, that move is an operator-gated action, and the "
    "behavioral pin named in this test's docstring must be re-examined and "
    "updated in the same change — or the clause was deleted, in which case "
    "Node 3 now has an undeclared determinant of derived output, which is the "
    "defect IDG-043 exists to prevent."
)


def _declared_score(
    citation_count: int,
    hop_depth: int,
    year: int | None,
    lambda_decay: float,
    current_year: int,
) -> float:
    """The SCORE FORMULA clause transcribed from the prose into arithmetic.

    Deliberately NOT a call to `_node3_score`: this is the independent second
    opinion the behavioral pins are measured against, so it is written from the
    contract text and `math` alone. The two declared special cases are here
    because the contract states them — `citation_count == 0` scores 0.0, and a
    missing `year` means `years_since_publication = 0`.

    The contract declares no behavior for a `year` in the future, so no clamp is
    transcribed and no fixture in this file uses one.
    """
    if citation_count == 0:
        return 0.0
    years = 0 if year is None else current_year - year
    return citation_count * math.log(hop_depth + 1) / math.exp(years * lambda_decay)


# ── Fixtures ────────────────────────────────────────────────────────────────
#
# Re-implemented here rather than imported from `test_pipeline_node3.py`: that
# file is under a do-not-modify fence, so it cannot be made to export them, and
# a local copy keeps this file's fixtures free to serve the pins below without
# perturbing the behavioral suite. No network: the client is a fake, and no
# credential of any kind is read.


def _work(
    openalex_id: str,
    arxiv_id: str,
    year: int | None = 2015,
    cited_by_count: int = 1,
    referenced_works: list[str] | None = None,
) -> dict:
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "ids": {
            "openalex": f"https://openalex.org/{openalex_id}",
            "arxiv": f"https://arxiv.org/abs/{arxiv_id}",
        },
        "title": "T",
        "publication_year": year,
        "authorships": [{"author": {"display_name": "A"}}],
        "abstract_inverted_index": None,
        "cited_by_count": cited_by_count,
        "referenced_works": [
            f"https://openalex.org/{r}" for r in (referenced_works or [])
        ],
    }


def _seed_record(node_id: str, openalex_id: str, year: int = 2020) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        arxiv_id=node_id.split(":", 1)[1],
        openalex_id=openalex_id,
        title="seed",
        hop_depth=0,
        root_ids=[node_id],
        year=year,
    )


def _record(
    node_id: str,
    hop_depth: int,
    citation_count: int,
    year: int | None,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id="W1",
        title="t",
        hop_depth=hop_depth,
        citation_count=citation_count,
        year=year,
    )


class _BatchClient:
    """Fake `httpx.AsyncClient.get` — dispatches by `filter=` param to a work db."""

    def __init__(self, works: dict[str, dict]):
        self.works = works
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, url: str, params: dict | None = None):
        filt = (params or {}).get("filter", "")
        ids: list[str] = []
        if filt.startswith("openalex_id:"):
            ids = filt[len("openalex_id:") :].split("|")
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={"results": [self.works[i] for i in ids if i in self.works]}
        )
        return resp


def _run(
    works: dict[str, dict],
    seeds: list[PaperRecord],
    *,
    n_backward: int,
    lambda_decay: float = 0.05,
    current_year: int = 2026,
) -> Node3Result:
    """Drive the `BackwardTraverse` handler through its declared contract.

    The marshalling `run_traversal` performs — one key per declared param, one
    key per declared input port, client and credential through the resource
    channel. `current_year` is fixed, never clock-derived: these pins turn on
    ranking and truncation, and a clock read would re-rank them on New Year.
    """
    out = asyncio.run(
        backward_traverse(
            {
                "n_backward": n_backward,
                "lambda_decay": lambda_decay,
                "sleep_ms": 0,
                "current_year": current_year,
            },
            {"seeds": seeds},
            resources={
                "http_client": _BatchClient(works),
                "openalex_api_key": "not-a-real-key",
            },
        )
    )
    return out["backward"]


# ── Clause: SCORE FORMULA ───────────────────────────────────────────────────


#: `(citation_count, hop_depth, year, lambda_decay, current_year)`. Spans both
#: production hop depths, a zero-age record, and a 28-year-old one, so a decay
#: term that was dropped, inverted, or moved out of the exponent shows up.
_SCORE_CASES = [
    (10, 1, 2020, 0.05, 2026),
    (100, 2, 2015, 0.10, 2026),
    (3, 1, 2026, 0.50, 2026),
    (57, 2, 1998, 0.02, 2026),
]


def test_score_formula_matches_the_declared_expression() -> None:
    """Pins SCORE FORMULA: `_node3_score` computes what the clause declares.

    The expectation comes from `_declared_score`, transcribed from the contract
    prose and `math` — never from `_node3_score`, which would compare the
    subject against itself and pass for any formula whatsoever.

    WHEN THIS TEST FAILS, READ THIS. It does not mean the arithmetic is wrong.
    It means the score formula in `pipeline._node3_score` no longer computes
    what `TRAVERSAL_CONTRACT` says it computes, so Node 3's ranking — and
    therefore which papers survive the cap, and therefore every corpus derived
    downstream — has changed while the declaration and its hash stood still.
    """
    for citation_count, hop_depth, year, lam, current_year in _SCORE_CASES:
        rec = _record("x", hop_depth=hop_depth, citation_count=citation_count, year=year)
        expected = _declared_score(citation_count, hop_depth, year, lam, current_year)
        observed = _node3_score(rec, lambda_decay=lam, current_year=current_year)
        assert observed == expected, (
            f"_node3_score returned {observed!r} for "
            f"(citation_count={citation_count}, hop_depth={hop_depth}, "
            f"year={year}, lambda_decay={lam}, current_year={current_year}); the "
            f"SCORE FORMULA clause of TRAVERSAL_CONTRACT declares "
            f"citation_count × log(hop_depth + 1) / "
            f"exp(years_since_publication × lambda_decay) = {expected!r}. The "
            f"code and the declaration have DIVERGED. Do not repair this by "
            f"editing either one: amending the contract text moves a content "
            f"address (an operator-gated action) and changing the formula "
            f"re-ranks every run. Report the divergence, naming this clause."
        )


def test_score_zero_citation_count_is_exactly_zero() -> None:
    """Pins SCORE FORMULA's first declared special case: zero citations → 0.0.

    Asserted as an exact `0.0` across hop depths and ages, because the clause
    states a flat value and not "approximately nothing".

    WHEN THIS TEST FAILS, READ THIS. An uncited paper has acquired a score, so
    uncited papers can now displace cited ones under the cap. Either the
    short-circuit went away or the formula grew an additive term — either way
    the declaration no longer describes the code.
    """
    for hop_depth, year in [(1, 2020), (2, 1998), (1, None)]:
        rec = _record("x", hop_depth=hop_depth, citation_count=0, year=year)
        observed = _node3_score(rec, lambda_decay=0.1, current_year=2026)
        assert observed == 0.0, (
            f"_node3_score returned {observed!r} for a record with "
            f"citation_count == 0 (hop_depth={hop_depth}, year={year}). The "
            f"SCORE FORMULA clause declares `citation_count == 0` scores 0.0. "
            f"Uncited papers can now consume cap slots. Report this divergence "
            f"against the SCORE FORMULA clause; do not edit the contract text."
        )


def test_score_missing_year_carries_no_recency_penalty() -> None:
    """Pins SCORE FORMULA's second declared special case: `year is None`.

    The clause says a missing year is treated as `years_since_publication = 0`
    — no penalty. `lambda_decay` is deliberately large here: if the code fell
    back to any nonzero age instead, the observed score would be a fraction of
    the expectation rather than a rounding error away from it.

    WHEN THIS TEST FAILS, READ THIS. Papers with unknown publication years are
    now being ranked as though they had one. Which year the code substituted
    determines whether they are systematically promoted or buried, and neither
    is what the declaration says happens.
    """
    rec = _record("x", hop_depth=2, citation_count=100, year=None)
    expected = _declared_score(100, 2, None, 0.5, 2026)
    assert expected == 100 * math.log(3)  # no penalty means the divisor is exactly 1
    observed = _node3_score(rec, lambda_decay=0.5, current_year=2026)
    assert observed == expected, (
        f"_node3_score returned {observed!r} for a record with year=None; the "
        f"SCORE FORMULA clause declares a missing year is treated as "
        f"`years_since_publication = 0` — no recency penalty — which gives "
        f"{expected!r}. The code now applies some other age to year-less "
        f"papers. Report this divergence against the SCORE FORMULA clause."
    )


def test_contract_still_declares_the_score_formula() -> None:
    """TEXT TRIPWIRE for SCORE FORMULA, paired with the three pins above.

    Pins the clause's load-bearing tokens: the expression, the recency weight it
    divides by, and the two special cases. Together these are exactly what
    `test_score_formula_matches_the_declared_expression`,
    `test_score_zero_citation_count_is_exactly_zero` and
    `test_score_missing_year_carries_no_recency_penalty` measure the code
    against — so the formula cannot be re-declared out from under them.
    """
    for token in [
        "`citation_count × log(hop_depth + 1) / recency_weight`",
        "`recency_weight = exp(years_since_publication × lambda_decay)`",
        "`citation_count == 0` scores 0.0",
        "`years_since_publication = 0`",
    ]:
        assert token in _CONTRACT_ONE_LINE, (
            f"TRAVERSAL_CONTRACT no longer contains the SCORE FORMULA token "
            f"{token!r}. {_AMENDMENT_INSTRUCTION} The behavioral pins to "
            f"re-examine are test_score_formula_matches_the_declared_expression, "
            f"test_score_zero_citation_count_is_exactly_zero and "
            f"test_score_missing_year_carries_no_recency_penalty in this file."
        )


# ── Clause: THE DEPTH TERM IS RULED (IDG-044) ───────────────────────────────


def test_score_strictly_increases_with_hop_depth_alone() -> None:
    """Pins THE DEPTH TERM IS RULED: depth RAISES a score, holding all else equal.

    Isolating, which is the whole reason this pin exists. The records below are
    identical in `citation_count` and `year` and differ ONLY in `hop_depth`, so
    nothing but the depth term can move the result.
    `test_score_higher_citations_and_depth_rank_higher` in
    `test_pipeline_node3.py` varies citations and depth together and so passes
    just as happily under an inverted depth term; this one does not.

    WHEN THIS TEST FAILS, READ THIS. The most likely cause is that the depth
    term was inverted to `log(1 / hop_depth)` — the alternative the contract
    itself names — or flattened out. That silently reverses what backward
    traversal is for: deep foundational works stop surfacing and the cap fills
    with papers one hop from the seeds. It is a ruled design choice (IDG-044),
    not a default, so flipping it is an amendment to the declaration and a
    content-address move, never a quiet edit to the scoring line.
    """
    depths = [1, 2, 3, 4]
    scores = [
        _node3_score(
            _record(f"d{d}", hop_depth=d, citation_count=50, year=2015),
            lambda_decay=0.05,
            current_year=2026,
        )
        for d in depths
    ]
    for (shallow_depth, shallow), (deep_depth, deep) in zip(
        list(zip(depths, scores))[:-1], list(zip(depths, scores))[1:], strict=True
    ):
        assert deep > shallow, (
            f"hop_depth={deep_depth} scored {deep!r}, which is NOT strictly "
            f"greater than hop_depth={shallow_depth} at {shallow!r}, with "
            f"citation_count and year held identical. The THE DEPTH TERM IS "
            f"RULED clause (IDG-044) declares `log(hop_depth + 1)` "
            f"monotonically INCREASING in hop depth BY DESIGN, because deeper "
            f"foundational works are what backward traversal exists to "
            f"surface. If the inversion `log(1 / hop_depth)` was wanted, it is "
            f"an amendment to TRAVERSAL_CONTRACT and a content-address move "
            f"that an operator gates — not an edit to _node3_score. Report "
            f"this divergence against THE DEPTH TERM IS RULED."
        )


def test_contract_still_rules_the_depth_term_with_its_reason() -> None:
    """TEXT TRIPWIRE for THE DEPTH TERM IS RULED, paired with the pin above.

    Pins the direction, the ruling, the named inversion, AND the rationale
    carried beside the formula. The rationale is load-bearing here rather than
    decorative: the clause says in as many words that a formula stated without
    its reason is exactly the defect IDG-044 repaired, so a tripwire that let
    the reason be deleted while the formula stayed would not be pinning this
    clause at all — it would be re-opening the defect.
    """
    for token in [
        "THE DEPTH TERM IS RULED (IDG-044), not defaulted",
        "monotonically INCREASING in hop depth BY DESIGN",
        "the inversion is `log(1 / hop_depth)`",
    ]:
        assert token in _CONTRACT_ONE_LINE, (
            f"TRAVERSAL_CONTRACT no longer contains the depth-term token "
            f"{token!r}. {_AMENDMENT_INSTRUCTION} The behavioral pin to "
            f"re-examine is test_score_strictly_increases_with_hop_depth_alone "
            f"in this file."
        )
    for reason in [
        "distance from the seeds raises a paper's score rather than lowering it",
        "a formula stated without its reason is exactly the defect IDG-044 repaired",
    ]:
        assert reason in _CONTRACT_ONE_LINE, (
            f"TRAVERSAL_CONTRACT no longer carries the IDG-044 REASON beside "
            f"the depth term: {reason!r} is gone. The formula surviving without "
            f"its reason is precisely the defect IDG-044 repaired — the current "
            f"shape is a declared design choice, and a reader who cannot see "
            f"why cannot tell it from a default nobody chose. "
            f"{_AMENDMENT_INSTRUCTION}"
        )


# ── Clause: CAP RULE ────────────────────────────────────────────────────────


def test_cap_sort_key_is_negative_score_then_ascending_node_id() -> None:
    """Pins CAP RULE: retained papers are ordered by `(-score, node_id)`.

    The fixture makes both halves of the key observable at once. `a.1` and `b.1`
    are identical in citations, year and hop depth, so they tie EXACTLY on
    score and only the `node_id` tiebreak can separate them; the seed lists its
    references as `[C, B, A, D]`, so insertion order would put `b.1` ahead of
    `a.1` and a score-only sort would keep it there. The expected ranking is
    built independently, by sorting the fixture's own numbers through
    `_declared_score`.

    WHEN THIS TEST FAILS, READ THIS. Two different things break here and the
    observed order tells you which. A tie resolved toward `b.1` means the
    secondary key is gone, and Node 3's truncation is once again a function of
    OpenAlex's response order rather than of the seed set — the determinism
    thesis fails and identical runs return different corpora under one content
    address. A non-tied pair out of order means the score sort itself, or the
    formula under it, no longer matches the declaration.
    """
    fixture = [
        # (openalex_id, arxiv_id, cited_by_count)
        ("C", "c.1", 100),
        ("B", "b.1", 10),
        ("A", "a.1", 10),
        ("D", "d.1", 1),
    ]
    works = {
        "S": _work("S", "seed.1", referenced_works=[oa for oa, _, _ in fixture]),
        **{oa: _work(oa, ax, cited_by_count=n) for oa, ax, n in fixture},
    }
    expected = [
        f"arxiv:{ax}"
        for _, ax, _ in sorted(
            fixture,
            key=lambda f: (-_declared_score(f[2], 1, 2015, 0.05, 2026), f"arxiv:{f[1]}"),
        )
    ]
    assert expected == ["arxiv:c.1", "arxiv:a.1", "arxiv:b.1", "arxiv:d.1"]

    result = _run(works, [_seed_record("arxiv:seed.1", "S")], n_backward=4)

    observed = [p.node_id for p in result.papers]
    assert observed == expected, (
        f"Node 3 ranked {observed}, not {expected}. The CAP RULE clause "
        f"declares the retained papers are the top `n_backward` by score "
        f"DESCENDING with ties breaking toward ASCENDING `node_id` — the sort "
        f"key is `(-score, node_id)`. arxiv:a.1 and arxiv:b.1 tie exactly on "
        f"score, so their relative order is the tiebreak and nothing else. "
        f"Report this divergence against the CAP RULE clause; do not edit "
        f"TRAVERSAL_CONTRACT to match."
    )


def test_cap_does_not_spend_a_slot_on_a_seed() -> None:
    """Pins CAP RULE: seeds are EXCLUDED from the scored population.

    `arxiv:s2.1` is a seed AND a reference of the other seed, and it carries
    more citations than every non-seed paper combined — so if seeds entered the
    scored population it would take the first cap slot and only two of the three
    depth-1 papers would come back. Exactly `n_backward` non-seed papers is the
    assertion.

    WHEN THIS TEST FAILS, READ THIS. Seeds are re-entering the ranked
    population, so `n_backward` no longer means "this many non-seed papers".
    Every run's corpus silently shrinks by however many seeds happen to cite
    each other, which is data-dependent and therefore invisible until it changes
    a result. The declaration says the cap counts non-seed papers only.
    """
    works = {
        "S1": _work("S1", "s1.1", referenced_works=["S2", "D1", "D2", "D3"]),
        "S2": _work("S2", "s2.1", cited_by_count=99_999),
        "D1": _work("D1", "d1.1", cited_by_count=30),
        "D2": _work("D2", "d2.1", cited_by_count=20),
        "D3": _work("D3", "d3.1", cited_by_count=10),
    }
    seeds = [_seed_record("arxiv:s1.1", "S1"), _seed_record("arxiv:s2.1", "S2")]

    result = _run(works, seeds, n_backward=3)

    observed = [p.node_id for p in result.papers]
    assert observed == ["arxiv:d1.1", "arxiv:d2.1", "arxiv:d3.1"], (
        f"Node 3 returned {observed} for n_backward=3. The CAP RULE clause "
        f"declares seeds are excluded from the scored population, so the cap "
        f"counts non-seed papers only — all three depth-1 papers should be "
        f"here. 'arxiv:s2.1' among them means the seed was scored and spent a "
        f"cap slot despite already being in the graph. Report this divergence "
        f"against the CAP RULE clause."
    )


def test_contract_still_declares_the_cap_sort_key_and_seed_exclusion() -> None:
    """TEXT TRIPWIRE for CAP RULE, paired with the two pins above.

    Pins the sort-key parenthetical verbatim — it is the token that makes the
    tiebreak checkable rather than a matter of taste — plus the descending
    direction and the seed exclusion the second pin measures.
    """
    for token in [
        "top `n_backward` by score DESCENDING",
        "ties break toward ASCENDING `node_id`",
        "(the sort key is `(-score, node_id)`)",
        "Seeds are excluded from the scored population",
    ]:
        assert token in _CONTRACT_ONE_LINE, (
            f"TRAVERSAL_CONTRACT no longer contains the CAP RULE token "
            f"{token!r}. {_AMENDMENT_INSTRUCTION} The behavioral pins to "
            f"re-examine are "
            f"test_cap_sort_key_is_negative_score_then_ascending_node_id and "
            f"test_cap_does_not_spend_a_slot_on_a_seed in this file."
        )


# ── Clause: ORDERING ────────────────────────────────────────────────────────


def test_the_cap_runs_before_the_edge_filter_not_after() -> None:
    """Pins ORDERING: cap-then-edge-filter, and NOT filter-then-cap.

    This is the case that distinguishes the two orders. `arxiv:d2.1` is a
    depth-1 paper the cap cuts, and the seed cites it — so an edge into it
    exists at emission time and both of its endpoints are in the PRE-cap
    population. Under filter-then-cap that edge passes the endpoint test and
    survives into a result whose node set no longer contains its target. Under
    the declared order the cap runs first, the endpoint set is the POST-cap one,
    and the edge is dropped. The surviving edge into `arxiv:d1.1` is asserted
    too, so this cannot pass by emitting no edges at all.

    WHEN THIS TEST FAILS, READ THIS. Node 3 is emitting edges into papers it did
    not return. That breaks the ORPHAN RULE below in the same stroke, and the
    breakage does not surface until `CycleCleanResult` refuses the edge set much
    further downstream — by which point the stage that produced it is several
    nodes back.
    """
    works = {
        "S": _work("S", "seed.1", referenced_works=["D1", "D2"]),
        "D1": _work("D1", "d1.1", cited_by_count=100),
        "D2": _work("D2", "d2.1", cited_by_count=1),
    }

    result = _run(works, [_seed_record("arxiv:seed.1", "S")], n_backward=1)

    assert [p.node_id for p in result.papers] == ["arxiv:d1.1"]
    pairs = [(e.source_id, e.target_id) for e in result.edges]
    assert ("arxiv:seed.1", "arxiv:d1.1") in pairs, (
        f"The edge into the RETAINED paper is missing from {pairs}. The "
        f"ORDERING clause declares the edge filter keeps edges whose endpoints "
        f"both lie in {{retained papers}} ∪ {{seeds}}; this one qualifies."
    )
    assert ("arxiv:seed.1", "arxiv:d2.1") not in pairs, (
        f"An edge into 'arxiv:d2.1' survived, but the cap cut that paper — "
        f"result.edges is {pairs}. The ORDERING clause declares score-sort → "
        f"truncate to `n_backward` → THEN filter edges, so the endpoint set is "
        f"the POST-cap one. An edge into a cut paper is the signature of "
        f"filter-then-cap: the filter ran against the pre-cap population. "
        f"Report this divergence against the ORDERING clause."
    )


def test_emitted_edges_are_sorted_by_source_then_target() -> None:
    """Pins ORDERING: the surviving edge list is sorted by `(source_id, target_id)`.

    The fixture's emission order is deliberately not its sorted order — the seed
    lists `z.1` before `a.1`, and the depth-2 edge out of `a.1` is emitted last
    of all but sorts first — so an unsorted list is a visibly different list
    rather than an accidental match. Nothing is capped here; sorting is the only
    thing under test.

    WHEN THIS TEST FAILS, READ THIS. Edge order is part of Node 3's derived
    output, and the determinism thesis says identical inputs produce an
    identical graph. Unsorted, the order tracks OpenAlex's response order and
    Python's dict insertion order, so two runs with identical seeds and
    parameters can return byte-different edge lists under one content address.
    """
    works = {
        "S": _work("S", "seed.1", referenced_works=["Z", "A"]),
        "Z": _work("Z", "z.1", cited_by_count=5),
        "A": _work("A", "a.1", cited_by_count=5, referenced_works=["M"]),
        "M": _work("M", "m.1", cited_by_count=5),
    }

    result = _run(works, [_seed_record("arxiv:seed.1", "S")], n_backward=10)

    pairs = [(e.source_id, e.target_id) for e in result.edges]
    expected = [
        ("arxiv:a.1", "arxiv:m.1"),
        ("arxiv:seed.1", "arxiv:a.1"),
        ("arxiv:seed.1", "arxiv:z.1"),
    ]
    assert pairs == expected, (
        f"result.edges came back as {pairs}, not {expected}. The ORDERING "
        f"clause declares the surviving edge list sorted by "
        f"`(source_id, target_id)`. Unsorted, Node 3's edge output depends on "
        f"fetch and insertion order, so the same seeds and parameters can "
        f"produce different graphs under the same content address. Report this "
        f"divergence against the ORDERING clause."
    )


def test_contract_still_declares_the_edge_filter_runs_after_the_cap() -> None:
    """TEXT TRIPWIRE for ORDERING, paired with the two pins above.

    Pins the pipeline arrow, the sort key of the surviving list, and the
    sentence that states BOTH halves of what makes the order distinguishable:
    the filter runs AFTER the cap, and it filters EDGES, not papers. Drop
    either half and the two candidate orders stop being tellable apart from the
    prose alone.
    """
    for token in [
        "Score-sort → truncate to `n_backward` → THEN filter edges to those",
        "edge list sorted by `(source_id, target_id)`",
    ]:
        assert token in _CONTRACT_ONE_LINE, (
            f"TRAVERSAL_CONTRACT no longer contains the ORDERING token "
            f"{token!r}. {_AMENDMENT_INSTRUCTION} The behavioral pins to "
            f"re-examine are test_the_cap_runs_before_the_edge_filter_not_after "
            f"and test_emitted_edges_are_sorted_by_source_then_target in this "
            f"file."
        )
    rule = "The edge filter runs AFTER the cap, and it filters EDGES, not papers."
    assert rule in _CONTRACT_ONE_LINE, (
        f"TRAVERSAL_CONTRACT no longer states {rule!r}. This is the sentence "
        f"that makes cap-then-filter distinguishable from filter-then-cap at "
        f"all, and the one test_the_cap_runs_before_the_edge_filter_not_after "
        f"is written against. {_AMENDMENT_INSTRUCTION}"
    )


# ── Clause: ORPHAN RULE ─────────────────────────────────────────────────────


def test_node3_emits_no_orphan_edges() -> None:
    """Pins ORPHAN RULE: every emitted edge's endpoints are in the nodes ∪ seeds.

    Two seeds, both hop depths populated, and a cap tight enough that most of
    the population is cut — so the endpoint filter has real work to do. The
    assertions that edges remain and that papers were actually dropped keep this
    from passing vacuously on an empty or uncapped result.

    WHEN THIS TEST FAILS, READ THIS. Node 3 is the stage that guarantees this
    invariant, and no stage between here and the cycle cleaner re-checks it —
    the contract says Nodes 5-8 run no defensive checks of their own. So an
    orphan emitted here travels intact until `CycleCleanResult` raises on it,
    and the traceback names the cleaner rather than the producer. What is broken
    is the endpoint filter in `backward_traverse`, not the downstream check.
    """
    works = {
        "S1": _work("S1", "s1.1", referenced_works=["P1", "P2"]),
        "S2": _work("S2", "s2.1", referenced_works=["P3"]),
        "P1": _work("P1", "p1.1", cited_by_count=100, referenced_works=["Q1"]),
        "P2": _work("P2", "p2.1", cited_by_count=2, referenced_works=["Q2"]),
        "P3": _work("P3", "p3.1", cited_by_count=50, referenced_works=["Q3"]),
        "Q1": _work("Q1", "q1.1", cited_by_count=80),
        "Q2": _work("Q2", "q2.1", cited_by_count=1),
        "Q3": _work("Q3", "q3.1", cited_by_count=40),
    }
    seeds = [_seed_record("arxiv:s1.1", "S1"), _seed_record("arxiv:s2.1", "S2")]

    result = _run(works, seeds, n_backward=3)

    assert len(result.papers) == 3, "fixture must exercise the cap, not slip under it"
    assert result.edges, "fixture must emit edges for the endpoint filter to act on"

    emitted = {p.node_id for p in result.papers} | {s.node_id for s in seeds}
    for e in result.edges:
        orphans = [
            f"{role}={node_id!r}"
            for role, node_id in (("source_id", e.source_id), ("target_id", e.target_id))
            if node_id not in emitted
        ]
        assert not orphans, (
            f"Node 3 emitted an ORPHAN edge {(e.source_id, e.target_id)} — "
            f"{', '.join(orphans)} is in neither the emitted node set nor the "
            f"seeds ({sorted(emitted)}). The ORPHAN RULE clause declares Node 3 "
            f"itself emits no orphan edges, and the pipeline's only orphan "
            f"CHECK is downstream in "
            f"`CycleCleanResult._validate_edge_endpoints` — so nothing between "
            f"here and there will catch this, and when it finally raises it "
            f"will name the cleaner and not this stage. Fix the endpoint filter "
            f"in `backward_traverse`; report this divergence against the "
            f"ORPHAN RULE clause."
        )


def test_contract_orphan_check_symbol_still_exists_downstream() -> None:
    """STATIC TRIPWIRE for ORPHAN RULE, paired with the pin above.

    Two halves of one claim. The text half pins the contract's declaration that
    Node 3 emits no orphans and that the only CHECK is downstream. The symbol
    half asserts the downstream check the clause NAMES is still there: the
    contract points at `CycleCleanResult._validate_edge_endpoints` by name, and
    a named symbol that has been renamed or deleted makes the prose a dangling
    reference — the declaration would still read as true while pointing at
    nothing.

    WHEN THIS TEST FAILS on the symbol half, READ THIS. Do not repoint the
    contract text at the new name; that is an amendment and a content-address
    move. Report which symbol the check now lives under.
    """
    for token in [
        "Node 3 itself emits no orphan edges",
        "The pipeline's only orphan CHECK is downstream",
        "`CycleCleanResult._validate_edge_endpoints`",
    ]:
        assert token in _CONTRACT_ONE_LINE, (
            f"TRAVERSAL_CONTRACT no longer contains the ORPHAN RULE token "
            f"{token!r}. {_AMENDMENT_INSTRUCTION} The behavioral pin to "
            f"re-examine is test_node3_emits_no_orphan_edges in this file."
        )

    assert hasattr(CycleCleanResult, "_validate_edge_endpoints"), (
        "TRAVERSAL_CONTRACT's ORPHAN RULE clause names "
        "`CycleCleanResult._validate_edge_endpoints` as the pipeline's ONLY "
        "orphan check, and that attribute is gone. Either the check was renamed "
        "— leaving the contract pointing at nothing — or it was removed, in "
        "which case an orphan edge emitted anywhere now reaches persistence "
        "unchallenged. Report which; do not repoint the contract text."
    )
    source = Path(models.__file__).read_text(encoding="utf-8")
    assert "def _validate_edge_endpoints" in source, (
        "`_validate_edge_endpoints` is no longer defined in "
        f"{Path(models.__file__).name}, where TRAVERSAL_CONTRACT's ORPHAN RULE "
        "clause locates it. An inherited or re-exported stand-in satisfies "
        "hasattr() while the check the contract describes has moved. Report the "
        "relocation; do not repoint the contract text."
    )
