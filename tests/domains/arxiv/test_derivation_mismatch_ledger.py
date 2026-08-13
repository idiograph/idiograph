# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""SERVE AND RECORD at the cache HIT gate (IDG-091 clause 3).

A content address keys on INPUTS, so a HIT stays a HIT across a rewritten
handler, an edited scoring line or a bumped dependency — and serves an artifact
derived by code the tree no longer contains. `cache.cached_run_arxiv_pipeline`
now compares the record's committed derivation baseline against a live one on
every HIT and appends the diff to a ledger.

The ruling these tests pin is that the observation is an OBSERVATION:

  SERVE, ALWAYS. The stored artifact is returned mismatch or not, byte-identical
  to what a no-mismatch HIT returns. Nothing on this path recomputes. That is the
  half a test suite most easily forgets to assert, because a mismatch test that
  only counts ledger entries passes just as happily against an implementation
  that invalidated the cache — so `test_a_mismatch_serves_the_stored_artifact_and_records_it`
  asserts BOTH halves in one body.

  RECORD ONCE PER DRIFT STATE. Deduplicated by
  `(record_address, manifest_diff_hash)`. Re-observing the same drift appends
  nothing; a tree that has drifted FURTHER carries a new hash and earns a new
  entry. Both directions matter, and the second is the dangerous one: a dedupe
  that swallowed a new state would leave the ledger reading as complete while
  missing the newest fact in it.

  ABSENCE IS NOT DRIFT. A record with no sidecar is served exactly as it was
  before this channel existed, with no ledger write at all. Every fixture
  registry in the pre-existing suite is in that position and must stay green.

  THE CHANNEL NEVER BREAKS THE SERVE. Any failure in the manifest machinery — a
  sidecar that will not parse, an unwritable ledger — degrades to serving the
  artifact with a logged warning. A cache that stops answering because its audit
  trail could not be written has inverted the priority entirely.

NO TRAVERSAL IS MOCKED HERE, on purpose. A HIT never calls traversal, so these
tests populate the registry by WRITING a hand-built `PipelineResult` and mock
only Node 0's `fetch_seeds` — the one call resolution genuinely makes. That
leaves `HANDLERS` pristine, so the manifest under test is the real production
manifest over the real handler modules rather than one anchored to
`unittest.mock`. `test_cache.py` mocks the traversal stages because it exercises
the MISS leg; this file does not, and should not acquire them.

Every ledger path here is tmp-rooted. The default resolves against the working
directory, and a test that appended to the developer's checkout would be writing
outside its own fixture.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
from idiograph.domains.arxiv.derivation_manifest import (
    DERIVATION_MANIFEST_VERSION,
    DerivationManifest,
    derive_manifest,
    sidecar_path_for,
    write_sidecar,
)
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CoCitationParameters,
    CommunityResult,
    CycleCleanResult,
    CycleLog,
    DepthMetrics,
    ForwardParameters,
    PaperRecord,
    PipelineParameters,
    PipelineResult,
)
from idiograph.domains.arxiv.registry import PipelineRegistry

_CLIENT = object()  # sentinel — resolution is mocked, so it is never used.
_SEED_ID = "arxiv:2101.00001"
_SEED_REQUEST = [{"arxiv_id": "2101.00001"}]

#: The module doctored to manufacture drift. A real anchored module, so the
#: fixture exercises the same row shape a genuine rewrite would produce.
_DRIFTED_MODULE = "idiograph.domains.arxiv.pipeline"


def _parameters() -> PipelineParameters:
    """Minimal valid parameters. ``current_year`` is stated, never clock-read: it
    enters the content address, and a clock would move every address here."""
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        current_year=2026,
        co_citation=CoCitationParameters(min_strength=1, max_edges=None),
    )


def _seed_record() -> PaperRecord:
    return PaperRecord(
        node_id=_SEED_ID,
        openalex_id="W1",
        title="seed",
        hop_depth=0,
        root_ids=[_SEED_ID],
        citation_count=0,
    )


def _stored_result(parameters: PipelineParameters) -> PipelineResult:
    """A minimal but genuine ``PipelineResult`` — one seed, no traversal output.

    Written into the registry directly rather than produced by a MISS. The HIT
    leg reads a stored bundle and re-supplies two request-derived fields; what
    the bundle CONTAINS is irrelevant to the mismatch channel, and building it by
    hand is what lets these tests leave the handler registry alone.
    """
    return PipelineResult(
        nodes=[_seed_record()],
        edges=[],
        seeds=[_SEED_ID],
        cycle_clean=CycleCleanResult(
            cleaned_edges=[],
            cycle_log=CycleLog(cycles_detected_count=0, iterations=0),
            input_node_ids=frozenset({_SEED_ID}),
        ),
        co_citation_edges=[],
        depth_metrics={
            _SEED_ID: DepthMetrics(
                hop_depth_per_root={_SEED_ID: 0}, traversal_direction="seed"
            )
        },
        pagerank={_SEED_ID: 1.0},
        communities=CommunityResult(
            community_assignments={_SEED_ID: "c0"},
            algorithm_used="leiden",
            community_count=1,
        ),
        parameters=parameters,
    )


@pytest.fixture
def populated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A tmp registry holding one record, with Node 0 resolution mocked.

    Returns ``(registry, address, parameters, ledger_path)``. The ledger path is
    inside ``tmp_path`` and deliberately does NOT exist yet — "no file" is the
    starting state every assertion about appending is measured from.
    """
    monkeypatch.setattr(
        pipeline, "fetch_seeds", AsyncMock(return_value=([_seed_record()], []))
    )
    parameters = _parameters()
    registry = PipelineRegistry(tmp_path / "registry")
    address = registry.write(_stored_result(parameters))
    return registry, address, parameters, tmp_path / "mismatch_ledger.jsonl"


def _hit(
    registry: PipelineRegistry,
    parameters: PipelineParameters,
    ledger_path: Path,
) -> PipelineResult:
    """One call through the production cached entry point, landing on the HIT leg."""
    return asyncio.run(
        cached_run_arxiv_pipeline(
            _SEED_REQUEST,
            parameters,
            client=_CLIENT,
            api_key="k",
            registry=registry,
            mismatch_ledger_path=ledger_path,
        )
    )


def _entries(ledger_path: Path) -> list[dict]:
    """Every ledger entry, decoded. An absent ledger is zero entries, not an error."""
    if not ledger_path.is_file():
        return []
    return [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _drifted_baseline(
    parameters: PipelineParameters, fake_sha: str
) -> DerivationManifest:
    """The live manifest with ONE module's hash replaced — a manufactured baseline.

    Doctoring the BASELINE rather than the tree is what makes this test possible
    at all: the alternative is editing a real module mid-run, which no test may
    do. The diff the gate computes is identical either way — one row, naming
    ``_DRIFTED_MODULE``, with the fake hash on the baseline side and the module's
    true hash on the live side.
    """
    live = derive_manifest(_SEED_REQUEST, parameters)
    return live.model_copy(
        update={
            "modules": [
                anchor.model_copy(update={"sha256": fake_sha})
                if anchor.module == _DRIFTED_MODULE
                else anchor
                for anchor in live.modules
            ]
        }
    )


# ── Absence ──────────────────────────────────────────────────────────────────


def test_a_record_without_a_sidecar_serves_and_writes_nothing(populated) -> None:
    """Pins ABSENCE IS NOT DRIFT: no baseline means no comparison and no entry.

    This is the state every pre-existing fixture registry in the suite is in, so
    it is also the guarantee that this channel could be added without touching
    them. A missing baseline diffing against the live tree would append an entry
    on every HIT in the suite and write a ledger into whatever directory pytest
    happened to run from.
    """
    registry, _, parameters, ledger = populated

    result = _hit(registry, parameters, ledger)

    assert result.seeds == [_SEED_ID]
    assert not ledger.exists(), (
        f"a ledger was created at {ledger} for a record with no committed "
        f"baseline. Absent is a NO-OP, not a mismatch: there is nothing to "
        f"compare against, so there is nothing to record."
    )


# ── Serve AND record ─────────────────────────────────────────────────────────


def test_a_mismatch_serves_the_stored_artifact_and_records_it(populated) -> None:
    """Pins SERVE, ALWAYS and RECORD together — the whole ruling in one body.

    The control is a HIT taken BEFORE any baseline is attached, so "what a
    no-mismatch serve returns" is observed rather than assumed. The mismatching
    HIT must return exactly that, field for field, while appending exactly one
    ledger entry whose diff names exactly the doctored row.

    WHEN THIS TEST FAILS, READ THIS. Which half went red decides what broke. A
    changed RESULT means the observation channel altered or invalidated a serve —
    the gravest failure available here, because a mismatch would then silently
    trigger recomputation at live OpenAlex and Anthropic cost on a path whose
    entire purpose is to avoid it. A missing or wrong ENTRY means drift is going
    unrecorded, and the apparatus is decorative.
    """
    registry, address, parameters, ledger = populated
    fake_sha = "0" * 64

    # Control: the same HIT with no baseline attached — nothing to mismatch.
    served_clean = _hit(registry, parameters, ledger)
    assert not ledger.exists()

    baseline = _drifted_baseline(parameters, fake_sha)
    write_sidecar(sidecar_path_for(registry.root, address), baseline)

    served_mismatched = _hit(registry, parameters, ledger)

    # (a) SERVED — and identical to the no-mismatch serve.
    assert served_mismatched.model_dump() == served_clean.model_dump(), (
        "the artifact served under a derivation mismatch differs from the one "
        "served without it. The stored artifact is ALWAYS served, unchanged: the "
        "mismatch is recorded, never acted on. Anything else makes the "
        "observation channel a cache invalidator."
    )

    # (b) EXACTLY ONE entry appended.
    entries = _entries(ledger)
    assert len(entries) == 1, (
        f"expected exactly one ledger entry for one drift state, found "
        f"{len(entries)}: {entries}"
    )
    entry = entries[0]

    # (c) The diff names EXACTLY the altered row.
    assert entry["diff"] == [
        {
            "anchor": _DRIFTED_MODULE,
            "path": "idiograph/domains/arxiv/pipeline.py",
            "baseline": fake_sha,
            "live": next(
                anchor.sha256
                for anchor in derive_manifest(_SEED_REQUEST, parameters).modules
                if anchor.module == _DRIFTED_MODULE
            ),
        }
    ], (
        f"the ledger diff does not name exactly the doctored row. One module's "
        f"hash was altered, so one row should differ; the entry carries "
        f"{entry['diff']}. A diff reporting more than the change buries the fact "
        f"the operator needs, and one reporting less is not a witness."
    )

    # The entry is the diff plus the provenance needed to locate it — and carries
    # no severity, no classification and no summary. Pinned as a whole key set so
    # an interpretation field cannot be added without this test naming it.
    assert sorted(entry) == [
        "baseline_commit",
        "diff",
        "live_commit",
        "manifest_diff_hash",
        "record_address",
        "registry",
        "timestamp",
        "v",
    ]
    assert entry["v"] == DERIVATION_MANIFEST_VERSION
    assert entry["record_address"] == address
    assert entry["registry"] == str(registry.root)
    assert entry["baseline_commit"] == baseline.baseline_commit
    assert entry["timestamp"]


# ── Dedupe ───────────────────────────────────────────────────────────────────


def test_re_observing_one_drift_state_appends_nothing(populated) -> None:
    """Pins RECORD ONCE PER DRIFT STATE: an unchanged mismatch is written once.

    Without this, a warm cache read in a loop would append an identical entry per
    call and the ledger would grow without carrying a single new fact — the
    signal buried under its own repetition.
    """
    registry, address, parameters, ledger = populated
    write_sidecar(
        sidecar_path_for(registry.root, address),
        _drifted_baseline(parameters, "0" * 64),
    )

    _hit(registry, parameters, ledger)
    first = _entries(ledger)
    _hit(registry, parameters, ledger)
    second = _entries(ledger)

    assert len(first) == 1
    assert second == first, (
        f"a second HIT against the SAME drift state changed the ledger: "
        f"{first} -> {second}. Deduplication is keyed on "
        f"(record_address, manifest_diff_hash); an identical drift state observed "
        f"again is the same fact, already recorded."
    )


def test_a_new_drift_state_earns_its_own_entry(populated) -> None:
    """Pins the other half of dedupe: a DIFFERENT diff is not suppressed.

    The dangerous failure. A dedupe keyed too broadly — on the record address
    alone, say — would swallow this second entry, and the ledger would then read
    as a complete account while missing the most recent state of the tree. Both
    entries must be present, and they must carry different diff hashes.
    """
    registry, address, parameters, ledger = populated
    sidecar = sidecar_path_for(registry.root, address)

    write_sidecar(sidecar, _drifted_baseline(parameters, "0" * 64))
    _hit(registry, parameters, ledger)

    # A different baseline hash for the same module: a genuinely different diff.
    write_sidecar(sidecar, _drifted_baseline(parameters, "1" * 64))
    _hit(registry, parameters, ledger)

    entries = _entries(ledger)
    assert len(entries) == 2, (
        f"a second, DIFFERENT drift state produced {len(entries)} entries rather "
        f"than 2: {entries}. Deduplication must suppress a repeat, never a new "
        f"state — a ledger missing its newest fact still reads as complete."
    )
    assert entries[0]["manifest_diff_hash"] != entries[1]["manifest_diff_hash"]
    assert [entry["diff"][0]["baseline"] for entry in entries] == [
        "0" * 64,
        "1" * 64,
    ]


# ── The channel never breaks the serve ───────────────────────────────────────


def test_an_unreadable_sidecar_degrades_to_serving(populated) -> None:
    """Pins THE CHANNEL NEVER BREAKS THE SERVE, on the sidecar it most depends on.

    A sidecar that exists but does not parse is the sharpest version of the case:
    the machinery is engaged, gets as far as reading, and fails. The artifact is
    still served, nothing is recorded, and nothing propagates to the caller.

    WHEN THIS TEST FAILS, READ THIS. A malformed observation artifact is taking
    down a cache read. Every stored record's availability would then depend on a
    file that has no bearing on the record's contents.
    """
    registry, address, parameters, ledger = populated
    sidecar_path_for(registry.root, address).write_text(
        "{not json at all", encoding="utf-8"
    )

    result = _hit(registry, parameters, ledger)

    assert result.seeds == [_SEED_ID]
    assert _entries(ledger) == [], (
        "an unparseable sidecar produced a ledger entry. It cannot: no baseline "
        "was read, so no diff exists to record."
    )


def test_an_unwritable_ledger_degrades_to_serving(populated) -> None:
    """Pins the same rule on the WRITE side: a mismatch that cannot be recorded is
    still served.

    The ledger path is pointed at a location that cannot hold a file — a path
    under an existing FILE — which is the portable stand-in for the read-only
    filesystem the ruling names. The mismatch is real and the write fails; the
    artifact still comes back.
    """
    registry, address, parameters, ledger = populated
    write_sidecar(
        sidecar_path_for(registry.root, address),
        _drifted_baseline(parameters, "0" * 64),
    )
    blocker = ledger.parent / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    result = _hit(registry, parameters, blocker / "ledger.jsonl")

    assert result.seeds == [_SEED_ID], (
        "a mismatch whose ledger write failed did not serve the stored artifact. "
        "The observation channel must never break the thing it observes: an "
        "unwritable audit trail costs an observation, never a serve."
    )
