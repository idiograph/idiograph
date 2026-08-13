# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Node 8 read-through cache — the decision layer ABOVE ``run_arxiv_pipeline``.

The cache is an explicit decision layer above a pipeline that does not know it is
cached (IDG-030): the pipeline stays the pure computer; this wrapper decides
whether to compute. It owns exactly one short-circuit —
``resolve -> content_address -> hit/skip -> store`` — over the already-shipped
content-addressed registry (Node 8, IDG-029).

Resolution runs on EVERY call, hit or miss. It precedes and PRODUCES the key, so
it can never be short-circuited; only TRAVERSAL is skipped on a hit. A resolution
failure therefore never reaches the cache.

Hit-parity re-supply (IDG-030 constraint)
-----------------------------------------
The content address keys solely on the RESOLVED seed node_ids + ``PipelineParameters``
(``content_address``). Fields of a ``PipelineResult`` derived from the *requested*
seed set — which is NOT part of the key — can therefore differ between two requests
that share an address. A verbatim hit would return the cache-populating request's
values for those fields, diverging from what a fresh miss produces. So on a hit we
re-supply every request-derived field from the CURRENT request's resolve output,
making a hit provably equal to a fresh miss (this mirrors ``registry.read``'s
re-supply of the excluded ``input_node_ids`` witness).

Every ``PipelineResult`` field, classified:

============================  ==================  ============================
field                         classification      hit behaviour
============================  ==================  ============================
nodes                         keyed               use stored
edges                         keyed               use stored
seeds                         request-derived     RE-SUPPLY (order)
cycle_clean                   keyed               use stored
co_citation_edges             keyed               use stored
co_citation_warnings          keyed               use stored
depth_metrics                 keyed               use stored
pagerank                      keyed               use stored
communities                   keyed               use stored
parameters                    keyed (in key)      use stored
seed_failures                 request-derived     RE-SUPPLY
backward_failed_batches       keyed               use stored
forward_failed_seeds          keyed               use stored
truncated_seeds               keyed               use stored
data_integrity_warnings       keyed               use stored
============================  ==================  ============================

Two fields are request-derived and re-supplied:

- ``seed_failures`` — the known instance (IDG-030): failures come from the
  REQUESTED seeds that did not resolve; the requested set is not in the key, so
  ``[A, B, Xbad]`` and ``[A, B]`` (both resolving to ``{A, B}``) share an address
  yet carry different failures.
- ``seeds`` — the ordered resolved node_id list. The address normalizes seed ORDER
  away (``sorted(set(...))``), so two requests resolving to the same set in a
  different order (e.g. ``[A, B]`` vs ``[B, A]``, or an arxiv_id vs its DOI) share
  an address but a fresh miss would produce a differently-ordered ``seeds`` list.
  Re-supplying the current resolve order keeps a hit equal to a fresh miss.

Everything else is a deterministic function of the RESOLVED seed set + parameters
(the key), so a verbatim hit already equals a fresh miss on those fields — the
traversal outputs (``nodes``/``edges``/…), the whole-graph stage results, the
traversal-side failure provenance, and ``parameters`` (which is literally part of
the address). No re-supply, and none is possible for the traversal-side failure
lists without re-running traversal, which a hit exists to avoid.

Derivation-mismatch observation at the HIT gate (IDG-091 clause 3)
-----------------------------------------------------------------
The address keys on INPUTS. It says nothing about the CODE that turned them into
the stored artifact, so a hit stays a hit across a rewritten handler, an edited
scoring line or a bumped dependency — and serves an artifact derived by code the
tree no longer contains. ``derivation_manifest`` is the descriptor that witnesses
that span, and this module is where it is consulted: on every HIT, the record's
committed baseline manifest is compared against one computed live.

SERVE AND RECORD — never recompute. The ruling is that the observation is an
observation and nothing more:

- The stored artifact is ALWAYS served, mismatch or not. Nothing on this path
  triggers recomputation. A mismatch is a fact written down, not a cache
  invalidation, and treating it as one would silently convert an audit trail into
  an unbounded recompute bill.
- Every failure in the manifest machinery — an unresolvable ``uv.lock``, an
  unreadable module, an unparseable sidecar, a read-only filesystem under the
  ledger — degrades to serving the artifact with a logged warning. The
  observation channel must never break the thing it observes; a cache that stops
  answering because its audit trail could not be written has inverted the
  priority entirely.
- NO SIDECAR IS A NO-OP, NOT A MISMATCH. A record with no committed baseline has
  nothing to be measured against and is served exactly as it was before this
  channel existed, with no ledger write. Absence is not drift.
- On mismatch, ONE ledger entry per drift STATE, deduplicated by
  ``(record_address, manifest_diff_hash)``: re-observing an unchanged mismatch on
  a later HIT appends nothing, while a tree that has drifted further carries a new
  diff hash and so earns a new entry.

The entry IS the diff — no severity, no classification, no summarization. The
entry records; the operator interprets.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from anthropic import AsyncAnthropic

from idiograph.core.logging_config import get_logger
from idiograph.domains.arxiv.derivation_manifest import (
    DERIVATION_MANIFEST_VERSION,
    derive_manifest,
    diff_hash,
    manifest_diff,
    read_sidecar,
    sidecar_path_for,
)
from idiograph.domains.arxiv.models import (
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    SeedResolutionFailure,
)
from idiograph.domains.arxiv.pipeline import resolve_seeds, run_traversal
from idiograph.domains.arxiv.registry import PipelineRegistry, content_address

_log = get_logger("arxiv.cache")

#: Default mismatch-ledger location, resolved against the WORKING DIRECTORY.
#: A relative default rather than an absolute one: the ledger belongs to whoever
#: is running the cache, and an absolute default would write into a directory the
#: caller never named. Overridable per call, like ``registry`` / ``client`` /
#: ``api_key`` — and deliberately NOT a ``PipelineParameters`` field, because the
#: ledger path is an observation-channel knob and not a derivation input. Keeping
#: it off that model is what keeps it out of the content address BY CONSTRUCTION
#: rather than by anyone remembering to exclude it.
DEFAULT_MISMATCH_LEDGER_PATH = Path("mismatch_ledger.jsonl")


def _resupply_request_derived(
    result: PipelineResult,
    resolved: list[PaperRecord],
    seed_failures: list[SeedResolutionFailure],
) -> PipelineResult:
    """Re-supply the request-derived fields from the CURRENT request's resolve
    output, so a cache hit provably equals a fresh miss (IDG-030 constraint).

    Overrides ``seeds`` (order is request-derived; the address normalizes it away)
    and ``seed_failures`` (derived from the requested, not the resolved, seed set).
    Every other field is keyed and left as the stored/computed value. Uses
    ``model_copy(update=...)`` — ``PipelineResult`` is frozen, so the stored
    instance is never mutated in place. See the module docstring for the full
    field enumeration.
    """
    return result.model_copy(
        update={
            "seeds": [record.node_id for record in resolved],
            "seed_failures": seed_failures,
        }
    )


def _recorded_drift_states(ledger_path: Path) -> set[tuple[str, str]]:
    """The ``(record_address, manifest_diff_hash)`` pairs the ledger already holds.

    The dedupe key is read back off the ledger rather than held in memory: the
    ledger is the durable record of what has been observed, and a per-process set
    would re-append the same drift state on every fresh interpreter.

    A line that does not decode is SKIPPED, not raised on. An append-only file
    can end in a torn write, and one unreadable line means one drift state that
    may be re-appended — a duplicate entry, which is a blemish. Refusing to read
    the ledger at all would instead lose the observation entirely, and refusing to
    SERVE over it would be far worse than either.
    """
    if not ledger_path.is_file():
        return set()
    states: set[tuple[str, str]] = set()
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            _log.warning(
                "Skipping an undecodable line in mismatch ledger %s — it "
                "deduplicates nothing.",
                ledger_path,
            )
            continue
        address = entry.get("record_address")
        digest = entry.get("manifest_diff_hash")
        if isinstance(address, str) and isinstance(digest, str):
            states.add((address, digest))
    return states


def _record_derivation_mismatch(
    *,
    registry: PipelineRegistry,
    address: str,
    seeds: list[dict],
    parameters: PipelineParameters,
    ledger_path: Path,
) -> None:
    """Compare the record's committed baseline manifest against a live one and,
    on a NEW drift state, append one ledger entry.

    Returns without writing in the two cases that are not drift: no sidecar (no
    baseline to measure against) and an empty diff (the derivation code is
    unmoved). Raises on anything it cannot do; the fence in
    :func:`_observe_derivation_mismatch` is what turns that into a served
    artifact and a warning.

    Reads only. It never touches the stored record, never writes to the registry
    root, and never returns a value the serve path consults — the artifact this
    is observing has already been loaded and is served whatever happens here.
    """
    baseline = read_sidecar(sidecar_path_for(registry.root, address))
    if baseline is None:
        return

    live = derive_manifest(seeds, parameters)
    diff = manifest_diff(baseline, live)
    if not diff:
        return

    digest = diff_hash(diff)
    if (address, digest) in _recorded_drift_states(ledger_path):
        _log.info(
            "Derivation mismatch for %s already recorded under diff %s — "
            "serving, appending nothing.",
            address,
            digest,
        )
        return

    entry = {
        "v": DERIVATION_MANIFEST_VERSION,
        # The FIRST observation of this diff state. Later HITs on the same state
        # dedupe above and never restamp it, so the timestamp answers "when did
        # this drift first become visible", not "when was the cache last read".
        "timestamp": datetime.now(UTC).isoformat(),
        "registry": str(registry.root),
        "record_address": address,
        "baseline_commit": baseline.baseline_commit,
        "live_commit": live.baseline_commit,
        "diff": diff,
        "manifest_diff_hash": digest,
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    with ledger_path.open("a", encoding="utf-8") as ledger:
        ledger.write(f"{line}\n")
    _log.warning(
        "Derivation mismatch for %s — %d row(s) differ from the committed "
        "baseline; recorded in %s under diff %s. The stored artifact was "
        "SERVED; nothing was recomputed.",
        address,
        len(diff),
        ledger_path,
        digest,
    )


def _observe_derivation_mismatch(
    *,
    registry: PipelineRegistry,
    address: str,
    seeds: list[dict],
    parameters: PipelineParameters,
    ledger_path: Path,
) -> None:
    """The fence around the observation channel: it may fail, the serve may not.

    Deliberately blind. Every failure the manifest machinery can reach —
    unresolvable ``uv.lock``, unreadable module file, malformed sidecar,
    read-only filesystem under the ledger, a registry root that has since gone
    away — is one this HIT must survive, because none of them is a fact about the
    artifact being served. Enumerating the exception types would be a promise to
    keep the list current, and the day the list falls behind is the day an audit
    channel takes down a cache read.
    """
    try:
        _record_derivation_mismatch(
            registry=registry,
            address=address,
            seeds=seeds,
            parameters=parameters,
            ledger_path=ledger_path,
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring; the serve wins.
        _log.warning(
            "Derivation-mismatch observation failed for %s (%s: %s) — serving "
            "the stored artifact unchanged.",
            address,
            type(exc).__name__,
            exc,
        )


async def cached_run_arxiv_pipeline(
    seeds: list[dict],
    parameters: PipelineParameters,
    *,
    client: httpx.AsyncClient,
    api_key: str,
    registry: PipelineRegistry,
    anthropic_client: AsyncAnthropic | None = None,
    mismatch_ledger_path: Path = DEFAULT_MISMATCH_LEDGER_PATH,
) -> PipelineResult:
    """Read-through cache over ``run_arxiv_pipeline``'s traversal core.

    ``resolve -> key -> hit/skip -> store``. Resolution (:func:`resolve_seeds`)
    runs on every call — it precedes and produces the content address — and only
    TRAVERSAL (:func:`run_traversal`) is short-circuited on a hit. On a HIT the
    stored ``PipelineResult`` is loaded from ``registry`` and its request-derived
    fields re-supplied from the current resolve output (a hit equals a fresh miss);
    on a MISS the traversal core runs, its result is persisted, and it is returned.
    Both legs read the SAME resolve output — ``resolve_seeds`` is a port-declared
    executor handler, so this direct call shapes its ``params`` / ``inputs`` /
    ``resources`` as the executor would and reads ``seeds`` and ``seed_failures``
    off its declared output ports.

    The traversal call NEVER runs on a hit, so a hit issues no OpenAlex call for
    traversal (including the seed-refetch) — the whole point of the cache. The key
    is derived solely by :func:`content_address` over the resolved seed node_ids +
    ``parameters``; no competing key is computed, and the path is deterministic (no
    wall-clock, RNG, or env reads).

    ``run_arxiv_pipeline`` itself is cache-unaware; this wrapper is the explicit
    decision layer that owns whether to compute. ``registry`` is injected (the
    caller owns its lifecycle), matching the client/api_key injection convention.

    ``anthropic_client`` is threaded through to :func:`run_traversal` on the MISS
    path only — it is what Node 5.5 draws against when ``parameters.llm`` is set
    (IDG-024 keyword-only injection; defaults None, guarded in ``run_traversal``
    exactly where a draw is attempted). A HIT never calls traversal and so never
    draws, so replay requires no client: a hit with ``parameters.llm`` set and
    ``anthropic_client=None`` succeeds and replays the stored ``PipelineResult``.
    This wrapper mirrors ``run_arxiv_pipeline``'s forwarding — it adds no guard of
    its own, since the sole draw site is inside ``run_traversal``.

    ``mismatch_ledger_path`` is where the HIT leg's derivation-mismatch
    observations are appended (IDG-091 clause 3; see the module docstring).
    Injected on the same convention as ``registry`` / ``client`` / ``api_key`` and
    defaulted relative to the working directory — it is a knob on the OBSERVATION
    channel, never a derivation input, so it is not a ``PipelineParameters`` field
    and enters no content address. The MISS path does not read it: a record being
    written now was derived by the code running now, so there is nothing to
    compare and nothing to record.
    """
    node0 = await resolve_seeds(
        {"seeds": seeds},
        {},
        resources={"http_client": client, "openalex_api_key": api_key},
    )
    resolved = node0["seeds"]
    seed_failures = node0["seed_failures"]
    address = content_address(
        [record.node_id for record in resolved], parameters
    )

    if registry.path_for(address).exists():
        _log.info("Cache HIT for %s — skipping traversal", address)
        stored = registry.read(address)
        # Observation only, and AFTER the artifact is in hand: the serve does not
        # depend on it, is not gated by it, and is not altered by it.
        _observe_derivation_mismatch(
            registry=registry,
            address=address,
            seeds=seeds,
            parameters=parameters,
            ledger_path=Path(mismatch_ledger_path),
        )
        return _resupply_request_derived(stored, resolved, seed_failures)

    _log.info("Cache MISS for %s — running traversal", address)
    result = await run_traversal(
        resolved,
        parameters,
        seed_requests=seeds,
        client=client,
        api_key=api_key,
        anthropic_client=anthropic_client,
    )
    result = _resupply_request_derived(result, resolved, seed_failures)
    registry.write(result)
    return result
