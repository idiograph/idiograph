# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import math
import os
from typing import Literal

import httpx
import networkx as nx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from pydantic import BaseModel

from idiograph.core.executor import execute_graph
from idiograph.core.logging_config import get_logger
from idiograph.core.models import Edge, Graph, Node, PortDeclaration
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv.models import (
    CitationEdge,
    CoCitationParameters,
    CommunitiesParameters,
    CommunityResult,
    CycleCleanResult,
    CycleLog,
    DepthMetrics,
    EdgeMetadataMismatch,
    FailedBatch,
    FailedSeed,
    ForwardSort,
    Node3Result,
    Node4Result,
    Node5Result,
    PageRankParameters,
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    SeedResolutionFailure,
    SuppressedEdge,
    TruncatedSeed,
    make_node_id,
)

# Node 5.5's handler, RE-EXPORTED rather than called. The flip moved every stage
# dispatch onto the HANDLERS registry, so nothing in this module calls it any
# more — but `pipeline.annotate_relationships` is the module attribute every
# harness that stands in for Node 5.5 binds to (`monkeypatch.setattr` requires
# the attribute to exist), and dropping it would silently turn those stand-ins
# into AttributeErrors. Kept deliberately; the noqa is the record of that.
from idiograph.domains.arxiv.relationship_annotation import (  # noqa: F401
    annotate_relationships,
)

load_dotenv()

_log = get_logger("arxiv.pipeline")

OPENALEX_BASE = "https://api.openalex.org/works"
_WORK_SELECT = (
    "id,ids,title,publication_year,authorships,"
    "abstract_inverted_index,cited_by_count"
)
_TRAVERSAL_SELECT = _WORK_SELECT + ",referenced_works"


def _get_api_key() -> str:
    key = os.getenv("OPENALEX_API_KEY")
    if not key:
        raise OSError(
            "OPENALEX_API_KEY not set. Add it to .env or set it in the environment."
        )
    return key


def _untyped_port(name: str) -> PortDeclaration:
    """Declare a port by name alone.

    Ports are untyped at this stage: ``Graph.type_registry`` is unbuilt and
    nothing validates ``port_type``, so it carries a fixed inert marker rather
    than implying a contract no one enforces.

    Shared by every port-declaring stage in this module, which is why it lives
    in the preamble rather than beside any one of them.
    """
    return PortDeclaration(name=name, port_type="untyped")


def reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """Reconstruct plain-text abstract from OpenAlex's inverted-index format.

    The index maps each word to the list of positions where it occurs.
    """
    if not inverted_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions) or None


def _strip_openalex_id(url_or_id: str) -> str:
    """'https://openalex.org/W123' -> 'W123'; passthrough for bare IDs."""
    return url_or_id.rstrip("/").split("/")[-1]


def _work_to_record(
    work: dict, hop_depth: int, root_ids: list[str]
) -> PaperRecord:
    """Map an OpenAlex work JSON object to a PaperRecord."""
    ids = work.get("ids") or {}
    arxiv_url = ids.get("arxiv")
    arxiv_id = arxiv_url.rstrip("/").split("/")[-1] if arxiv_url else None
    doi = ids.get("doi")

    authorships = work.get("authorships") or []
    authors = [
        (a.get("author") or {}).get("display_name")
        for a in authorships
        if (a.get("author") or {}).get("display_name")
    ]

    return PaperRecord(
        node_id=make_node_id(work),
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=_strip_openalex_id(work["id"]),
        title=work.get("title") or "",
        year=work.get("publication_year"),
        authors=authors,
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        citation_count=work.get("cited_by_count") or 0,
        hop_depth=hop_depth,
        root_ids=list(root_ids),
    )


def _seed_filter(seed: dict) -> str | None:
    """Build the OpenAlex filter expression for a single seed entry."""
    if seed.get("arxiv_id"):
        return f"ids.arxiv:https://arxiv.org/abs/{seed['arxiv_id']}"
    if seed.get("doi"):
        # OpenAlex rejects `ids.doi:` with HTTP 400; `doi:` accepts both the bare
        # DOI and the https://doi.org/… prefixed form.
        return f"doi:{seed['doi']}"
    return None


async def fetch_seeds(
    seed_ids: list[dict],
    client: httpx.AsyncClient,
    api_key: str,
    sleep_ms: int = 150,
) -> tuple[list[PaperRecord], list[dict]]:
    """Resolve a list of seed identifiers against OpenAlex.

    Each entry in ``seed_ids`` is one of::

        {"arxiv_id": "1234.56789"}
        {"doi": "10.1234/example"}

    Returns a tuple ``(resolved, failures)``. ``resolved`` is a list of
    ``PaperRecord`` with ``hop_depth=0`` and ``root_ids=[node_id]``.
    ``failures`` is a list of ``{"seed": <original dict>, "reason": <str>}``.

    Raises ``ValueError`` if ``seed_ids`` is empty, or if every seed fails.
    """
    if not seed_ids:
        raise ValueError("fetch_seeds requires at least one seed identifier.")

    resolved: list[PaperRecord] = []
    failures: list[dict] = []
    sleep_s = sleep_ms / 1000.0

    for idx, seed in enumerate(seed_ids):
        if idx > 0:
            await asyncio.sleep(sleep_s)

        filt = _seed_filter(seed)
        if filt is None:
            failures.append({"seed": seed, "reason": "unrecognized seed shape"})
            _log.info("Seed %s failed: unrecognized shape", seed)
            continue

        params = {
            "filter": filt,
            "select": _WORK_SELECT,
            "api_key": api_key,
        }
        try:
            response = await client.get(OPENALEX_BASE, params=params)
            response.raise_for_status()
        except httpx.HTTPError as e:
            failures.append({"seed": seed, "reason": f"http error: {e}"})
            _log.info("Seed %s failed: http error: %s", seed, e)
            continue

        results = (response.json() or {}).get("results") or []
        if not results:
            failures.append({"seed": seed, "reason": "no results"})
            _log.info("Seed %s failed: no results", seed)
            continue

        work = results[0]
        record = _work_to_record(work, hop_depth=0, root_ids=[])
        record.root_ids = [record.node_id]
        resolved.append(record)
        _log.info("Seed resolved: %s", record.node_id)

    if not resolved:
        raise ValueError(
            f"All {len(seed_ids)} seeds failed to resolve. Failures: {failures}"
        )

    return resolved, failures


def _node3_score(
    record: PaperRecord, lambda_decay: float, current_year: int
) -> float:
    """Node 3 ranking: citations × log(hop_depth + 1) / recency_weight.

    ``recency_weight = exp(years_since_publication × lambda_decay)``.
    Missing ``year`` is treated as ``years_since_publication=0`` (no penalty).
    """
    if record.citation_count == 0:
        return 0.0
    years = 0 if record.year is None else max(0, current_year - record.year)
    recency_weight = math.exp(years * lambda_decay)
    return record.citation_count * math.log(record.hop_depth + 1) / recency_weight


async def _fetch_works_by_ids(
    openalex_ids: list[str],
    client: httpx.AsyncClient,
    api_key: str,
    sleep_ms: int,
    stage: Literal["seed_refetch", "depth_1", "depth_2"],
) -> tuple[list[dict], list[FailedBatch]]:
    """Batch-fetch OpenAlex works by ID (50 per call).

    Returns ``(works, failed_batches)``. Batches that raise ``httpx.HTTPError``
    are recorded as ``FailedBatch`` entries with the supplied ``stage`` label
    rather than dropped silently — see AMD-020.
    """
    if not openalex_ids:
        return [], []
    works: list[dict] = []
    failed_batches: list[FailedBatch] = []
    sleep_s = sleep_ms / 1000.0
    batch_size = 50
    for i in range(0, len(openalex_ids), batch_size):
        if i > 0:
            await asyncio.sleep(sleep_s)
        batch = openalex_ids[i : i + batch_size]
        filt = "openalex_id:" + "|".join(batch)
        params = {
            "filter": filt,
            "select": _TRAVERSAL_SELECT,
            "per-page": str(batch_size),
            "api_key": api_key,
        }
        try:
            response = await client.get(OPENALEX_BASE, params=params)
            response.raise_for_status()
        except httpx.HTTPError as e:
            _log.info("Batch fetch failed for %s (stage=%s): %s", batch, stage, e)
            failed_batches.append(
                FailedBatch(
                    requested_ids=list(batch),
                    stage=stage,
                    reason=f"http_error: {e}",
                )
            )
            continue
        results = (response.json() or {}).get("results") or []
        works.extend(results)
    return works, failed_batches


#: Production pacing between OpenAlex batch calls in this stage, in
#: milliseconds. THE ONE HOME for the value: ``_BackwardTraverseParams.sleep_ms``
#: is required with no default, so the number cannot also hide as a model
#: fallback, and ``run_traversal`` marshals THIS constant in explicitly rather
#: than relying on an implicit default — a declared graph should show what the
#: stage is configured with.
BACKWARD_SLEEP_MS = 150

#: Port declarations for the ``BackwardTraverse`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: EXACTLY ONE input port. ``seeds`` is name-identical to ``AssembleGraph``'s input
#: port of the same name (``ASSEMBLE_GRAPH_INPUT_PORTS``), so a future wiring feeds
#: both this stage and the merge from one producer with no renaming.
BACKWARD_TRAVERSE_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("seeds"),
]

#: TWO output ports.
#:
#: ``backward`` carries the whole ``Node3Result`` — the ``clean_cycles`` construct-
#: then-return shape. Not decomposed into ``papers``/``edges``: ``AssembleGraph``
#: already ships an input port literally named ``backward`` whose declared contract
#: IS a whole ``Node3Result`` (``_AssembleGraphInputs.backward``), so decomposing
#: here would force reopening a merged handler's input contract only to arrive back
#: at the same dataflow.
#:
#: ``failed_batches`` carries the same list also reachable inside the ``backward``
#: port's payload. The duplication is DELIBERATE: failure provenance rides a
#: declared output port rather than being dug out of another port's payload by the
#: consumer. The port is currently unconsumed, and that is legal —
#: ``validate_integrity`` checks referential integrity and port-declared edges, never
#: unconsumed outputs, and ``cycle_log``/``co_citation_warnings``/``mismatches``
#: already dangle green.
BACKWARD_TRAVERSE_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("backward"),
    _untyped_port("failed_batches"),
]


class _BackwardTraverseParams(BaseModel):
    """Declared param contract for the ``BackwardTraverse`` handler.

    The two ``BackwardParameters`` fields, mirrored here with their required-ness
    intact, PLUS ``sleep_ms`` and ``current_year``.

    ``sleep_ms`` lives HERE and deliberately NOT on ``BackwardParameters``.
    That model is nested inside ``PipelineParameters``, which ``content_address``
    hashes whole, so adding a field to it would re-address every existing cached
    record for a pacing knob that cannot affect output — the exact hazard the
    ``llm`` field documents. ``Node.params`` is not read by ``content_address``,
    so the key is free on this side of the fence.

    ``current_year`` is the OPPOSITE case and is here for the opposite reason.
    It IS output-determining — it orders the ``_node3_score`` sort that
    ``n_backward`` truncates — so it belongs in the address, and it is in the
    address: its home is ``PipelineParameters.current_year``, top-level, which
    ``content_address`` hashes. It appears on this model only as the marshalling
    channel that carries the run's value to the stage. It is NOT a
    ``BackwardParameters`` field because Node 4 scores against the same year and
    two per-stage fields would have to agree with nothing enforcing agreement.
    This stage no longer reads a clock; the value arrives stated.

    Required with no default, like its siblings: the sole home of the production
    pacing value is ``BACKWARD_SLEEP_MS`` and the sole home of the year is
    ``PipelineParameters``, and a model default would be a second one — silently
    applying whenever a wiring forgot to state its pacing or its year.
    """

    n_backward: int
    lambda_decay: float
    sleep_ms: int
    current_year: int


class _BackwardTraverseInputs(BaseModel):
    """Declared input contract for the ``BackwardTraverse`` handler.

    One field per declared input port (``BACKWARD_TRAVERSE_INPUT_PORTS``): the
    resolved seed records to traverse back from. The executor binds each
    port-declared incoming edge as ``inputs[to_port]``, so the ``inputs`` mapping
    validates directly against this model.
    """

    seeds: list[PaperRecord]


async def backward_traverse(
    params: dict, inputs: dict, *, resources: dict
) -> dict:
    """Executor node handler (type ``BackwardTraverse``) — backward traversal
    from seed nodes up to depth 2 (Node 3).

    For each seed, fetches its direct references (depth=1) and the references
    of those references (depth=2). Deduplicates by ``node_id`` — when a paper
    appears via multiple paths, the lowest ``hop_depth`` wins and ``root_ids``
    is the union of every root reachable through any path. Seeds themselves
    are excluded from the output papers. The merged records are then scored
    by :func:`_node3_score`, sorted descending, and truncated to
    ``n_backward``.

    Contract (``core/executor.py`` handler convention):
      ``params``    — ``{"n_backward": int, "lambda_decay": float,
                      "sleep_ms": int, "current_year": int}``, validated as
                      ``_BackwardTraverseParams``. All four are REQUIRED.
                      ``n_backward``/``lambda_decay`` keep their home in
                      ``PipelineParameters.backward`` and ``current_year`` in
                      ``PipelineParameters`` itself, top-level;
                      ``run_traversal`` reads them there and marshals them in.
                      ``sleep_ms`` is pacing, not configuration that reaches
                      output — see that model for why it is not a
                      ``BackwardParameters`` field, and for why
                      ``current_year`` is not one either despite being fully
                      output-determining.
      ``inputs``    — BOUND. The node declares ``BACKWARD_TRAVERSE_INPUT_PORTS``,
                      so the executor builds ``inputs`` solely from the
                      port-declared edges into it, keyed by ``to_port``:
                      ``{"seeds": [...]}``, validated as
                      ``_BackwardTraverseInputs``. Undeclared keys are ignored. A
                      caller invoking the handler directly shapes ``inputs`` the
                      same way, so the direct and executor-driven paths share one
                      contract.
      ``resources`` — ``{"http_client": <httpx.AsyncClient>, "openalex_api_key":
                      <str>}``. Both belong to the run, not the graph, and
                      neither enters a content address — a credential in
                      particular is a RESOURCE and never a param. ``http_client``
                      is name-identical to the resource ``fetch_abstract``
                      declares; the key is qualified rather than bare ``api_key``
                      because the composition root already owns an Anthropic
                      credential. Keyword-only with no default: the node
                      declares, so the executor always supplies, and a silent
                      call without it would be a bug worth crashing on.
      returns       — ``{"backward": Node3Result, "failed_batches": [...]}`` —
                      the declared output ports
                      (``BACKWARD_TRAVERSE_OUTPUT_PORTS``).

    A :class:`Node3Result` — the ranked papers, the citation edges discovered
    during traversal (seed→depth-1 and depth-1→depth-2), and any batch-level
    fetch failures recorded by ``_fetch_works_by_ids`` (see AMD-020) — is
    CONSTRUCTED internally before the return mapping is built, and rides the
    ``backward`` port whole.

    THE ``failed_batches`` PORT IS CANONICAL. The same list is also reachable at
    ``backward.failed_batches``, and that duplication is deliberate: a consumer
    reads failure provenance off its own declared port, never by digging into
    another port's payload.
    """
    config = _BackwardTraverseParams.model_validate(params)
    data = _BackwardTraverseInputs.model_validate(inputs)
    seeds = data.seeds
    n_backward = config.n_backward
    lambda_decay = config.lambda_decay
    sleep_ms = config.sleep_ms
    current_year = config.current_year
    client = resources["http_client"]
    api_key = resources["openalex_api_key"]

    seed_ids = {s.node_id for s in seeds}
    failed_batches: list[FailedBatch] = []

    # Seeds must first be re-fetched to obtain ``referenced_works`` since
    # Node 0 doesn't store it. In the common case the caller is the pipeline
    # orchestrator and has the seed OpenAlex IDs already — we fetch via the
    # OpenAlex-ID batch endpoint.
    seed_oa_ids = [s.openalex_id for s in seeds]
    seed_works, seed_failed = await _fetch_works_by_ids(
        seed_oa_ids, client, api_key, sleep_ms, stage="seed_refetch"
    )
    failed_batches.extend(seed_failed)
    seed_works_by_oa: dict[str, dict] = {
        _strip_openalex_id(w["id"]): w for w in seed_works
    }

    # Map seed node_id -> list of depth-1 OpenAlex IDs (bare, e.g. "W123")
    seed_to_depth1: dict[str, list[str]] = {}
    all_depth1_ids: set[str] = set()
    for seed in seeds:
        work = seed_works_by_oa.get(seed.openalex_id)
        if work is None:
            seed_to_depth1[seed.node_id] = []
            continue
        refs = [_strip_openalex_id(r) for r in (work.get("referenced_works") or [])]
        seed_to_depth1[seed.node_id] = refs
        all_depth1_ids.update(refs)

    # Fetch all depth-1 works in one deduplicated batch run.
    depth1_works, depth1_failed = await _fetch_works_by_ids(
        sorted(all_depth1_ids), client, api_key, sleep_ms, stage="depth_1"
    )
    failed_batches.extend(depth1_failed)
    depth1_by_oa: dict[str, dict] = {
        _strip_openalex_id(w["id"]): w for w in depth1_works
    }

    # Map depth-1 OA id -> list of depth-2 OA ids.
    depth1_to_depth2: dict[str, list[str]] = {}
    all_depth2_ids: set[str] = set()
    for oa_id, work in depth1_by_oa.items():
        refs = [_strip_openalex_id(r) for r in (work.get("referenced_works") or [])]
        depth1_to_depth2[oa_id] = refs
        all_depth2_ids.update(refs)

    depth2_works, depth2_failed = await _fetch_works_by_ids(
        sorted(all_depth2_ids), client, api_key, sleep_ms, stage="depth_2"
    )
    failed_batches.extend(depth2_failed)
    depth2_by_oa: dict[str, dict] = {
        _strip_openalex_id(w["id"]): w for w in depth2_works
    }

    # Build merged records, keyed by node_id.
    merged: dict[str, PaperRecord] = {}

    def _merge(work: dict, hop_depth: int, roots: set[str]) -> None:
        node_id = make_node_id(work)
        if node_id in seed_ids:
            return
        existing = merged.get(node_id)
        if existing is None:
            rec = _work_to_record(work, hop_depth=hop_depth, root_ids=sorted(roots))
            merged[node_id] = rec
            return
        # All hop=1 merges happen before any hop=2 merge, so existing.hop_depth
        # is always ≤ hop_depth at this point. Only the root_ids union matters.
        existing.root_ids = sorted(set(existing.root_ids) | roots)

    # Walk depth=1 for each seed
    for seed in seeds:
        for oa_id in seed_to_depth1.get(seed.node_id, []):
            work = depth1_by_oa.get(oa_id)
            if work is None:
                continue
            _merge(work, hop_depth=1, roots={seed.node_id})

    # Walk depth=2 for each seed, via its depth=1 papers
    for seed in seeds:
        for oa1 in seed_to_depth1.get(seed.node_id, []):
            for oa2 in depth1_to_depth2.get(oa1, []):
                work = depth2_by_oa.get(oa2)
                if work is None:
                    continue
                _merge(work, hop_depth=2, roots={seed.node_id})

    # Edge emission. Edges are produced from the same maps the merge walk
    # consumes, then filtered post-rank/cap so endpoints are guaranteed to
    # be in `papers` ∪ seeds (see Node3Result invariants).
    edges: list[CitationEdge] = []

    # Depth-1 edges: seed -> depth-1 paper. Skipped when the depth-1 metadata
    # failed to fetch (recorded in failed_batches instead).
    for seed in seeds:
        for oa_id in seed_to_depth1.get(seed.node_id, []):
            work = depth1_by_oa.get(oa_id)
            if work is None:
                continue
            edges.append(
                CitationEdge(
                    source_id=seed.node_id,
                    target_id=make_node_id(work),
                    type="cites",
                    citing_paper_year=seed.year,
                    strength=None,
                )
            )

    # Depth-2 edges: depth-1 paper -> depth-2 paper. Skipped when the depth-1
    # paper is itself a seed (its outgoing edges already covered above) or
    # when depth-2 metadata failed to fetch.
    for oa1, work1 in depth1_by_oa.items():
        source_node_id = make_node_id(work1)
        if source_node_id in seed_ids:
            continue
        source_year = work1.get("publication_year")
        for oa2 in depth1_to_depth2.get(oa1, []):
            work2 = depth2_by_oa.get(oa2)
            if work2 is None:
                continue
            edges.append(
                CitationEdge(
                    source_id=source_node_id,
                    target_id=make_node_id(work2),
                    type="cites",
                    citing_paper_year=source_year,
                    strength=None,
                )
            )

    # `current_year` arrives on `params` (see `_BackwardTraverseParams`). It is
    # NOT read from the clock here: this sort is what `n_backward` truncates, so
    # the year selects the corpus, and a wall-clock read would make two runs with
    # identical seeds and identical parameters return different corpora under the
    # same content address across a New Year boundary — a false HIT.
    scored = sorted(
        merged.values(),
        key=lambda r: (-_node3_score(r, lambda_decay, current_year), r.node_id),
    )
    papers = scored[:n_backward]

    valid_endpoints = {p.node_id for p in papers} | seed_ids
    filtered_edges = [
        e for e in edges
        if e.source_id in valid_endpoints and e.target_id in valid_endpoints
    ]
    filtered_edges.sort(key=lambda e: (e.source_id, e.target_id))

    result = Node3Result(
        papers=papers,
        edges=filtered_edges,
        failed_batches=failed_batches,
    )
    return {"backward": result, "failed_batches": result.failed_batches}


# ── Node 4 — Forward Traversal ──────────────────────────────────────────────

_FORWARD_SELECT = (
    "id,ids,title,publication_year,authorships,"
    "abstract_inverted_index,cited_by_count,counts_by_year"
)


def _compute_velocity(
    cited_by_count: int,
    pub_year: int | None,
    current_year: int,
) -> float:
    """Citations per month since publication; 0.0 when pub_year is unknown."""
    if pub_year is None:
        return 0.0
    months = max(1, (current_year - pub_year) * 12)
    return cited_by_count / months


def _compute_acceleration(
    counts_by_year: list[dict],
    acceleration_method: str,
) -> float | None:
    """Mean year-over-year change in citation velocity.

    Returns ``None`` when fewer than 3 time points are available; callers
    should then fall back to β=0 scoring for that paper.
    """
    if acceleration_method == "regression":
        raise NotImplementedError("regression acceleration not yet implemented")
    if acceleration_method != "first_difference":
        raise ValueError(f"unknown acceleration_method: {acceleration_method}")
    sorted_counts = sorted(counts_by_year, key=lambda e: e["year"])
    if len(sorted_counts) < 3:
        return None
    velocities = [e["cited_by_count"] / 12 for e in sorted_counts]
    deltas = [velocities[i] - velocities[i - 1] for i in range(1, len(velocities))]
    return sum(deltas) / len(deltas)


def _node4_score(
    velocity: float,
    acceleration: float | None,
    pub_year: int | None,
    current_year: int,
    alpha: float,
    beta: float,
    lambda_decay: float,
) -> float:
    """Node 4 ranking: α·velocity + β·acceleration·recency_weight.

    Recency is *rewarded* here (multiplied), opposite to Node 3 where it is
    penalized. Papers lacking acceleration data score with β=0.
    """
    years = current_year - pub_year if pub_year else 0
    recency_weight = math.exp(years * lambda_decay)
    effective_beta = beta if acceleration is not None else 0.0
    accel = acceleration if acceleration is not None else 0.0
    return alpha * velocity + effective_beta * accel * recency_weight


#: Production acceleration method for this stage. THE ONE HOME for the value,
#: mirroring ``BACKWARD_SLEEP_MS``: ``_ForwardTraverseParams.acceleration_method``
#: is required with no default, so the choice cannot also hide as a model
#: fallback, and ``run_traversal`` marshals THIS constant in explicitly rather
#: than relying on an implicit default — a declared graph should show what the
#: stage is configured with.
#:
#: It is deliberately NOT a ``ForwardParameters`` field. ``_compute_acceleration``
#: admits exactly ONE non-raising method: ``"regression"`` raises
#: ``NotImplementedError`` and every other value raises ``ValueError``. A hash
#: over a value with one admissible member records nothing, so promoting it into
#: the model would re-address every cached record to record a constant. The
#: exemption is guarded by a test, not by this comment — see
#: ``test_forward_traverse_handler.py``'s ``_compute_acceleration`` tripwire,
#: which fails the day regression is implemented and is the instruction to move
#: the field into the hashed model deliberately and pay the rebaselining then.
FORWARD_ACCELERATION_METHOD = "first_difference"

#: Port declarations for the ``ForwardTraverse`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: EXACTLY ONE input port. ``seeds`` is name-identical to ``AssembleGraph``'s and
#: ``BackwardTraverse``'s input ports of the same name
#: (``ASSEMBLE_GRAPH_INPUT_PORTS``, ``BACKWARD_TRAVERSE_INPUT_PORTS``), so one
#: producer feeds all three stages with no renaming.
FORWARD_TRAVERSE_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("seeds"),
]

#: THREE output ports.
#:
#: ``forward`` carries the whole ``Node4Result``. Not decomposed into
#: ``papers``/``edges``: ``AssembleGraph`` already ships an input port literally
#: named ``forward`` whose declared contract IS a whole ``Node4Result``
#: (``_AssembleGraphInputs.forward``), so decomposing here would force reopening
#: a merged handler's input contract only to arrive back at the same dataflow.
#: This is the ``backward`` port's ruling running one node later.
#:
#: ``failed_seeds`` and ``truncated_seeds`` carry the same list objects also
#: reachable inside the ``forward`` port's payload. The duplication is
#: DELIBERATE: failure provenance rides a declared output port of its own node
#: rather than being dug out of another port's payload by the consumer, and
#: truncation is failure provenance — it records that OpenAlex had more citing
#: papers than it returned, so the ranked set is a sample of an unknown larger
#: one. Both are currently unconsumed, and that is legal —
#: ``validate_integrity`` checks referential integrity and port-declared edges,
#: never unconsumed outputs, and ``failed_batches``/``cycle_log``/
#: ``co_citation_warnings``/``mismatches`` already dangle green.
FORWARD_TRAVERSE_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("forward"),
    _untyped_port("failed_seeds"),
    _untyped_port("truncated_seeds"),
]


class _ForwardTraverseParams(BaseModel):
    """Declared param contract for the ``ForwardTraverse`` handler.

    The five ``ForwardParameters`` fields, mirrored here with their required-ness
    intact, PLUS ``acceleration_method`` and ``current_year``.

    ``acceleration_method`` lives HERE and deliberately NOT on
    ``ForwardParameters``. That model is nested inside ``PipelineParameters``,
    which ``content_address`` hashes whole, and ``_compute_acceleration`` admits
    exactly one non-raising method — so hashing it would re-address every
    existing cached record in order to record a constant. ``Node.params`` is not
    read by ``content_address``, so the key is free on this side of the fence.
    See ``FORWARD_ACCELERATION_METHOD`` for the tripwire guarding the exemption.

    ``current_year`` is the OPPOSITE case and is here for the opposite reason.
    It IS output-determining — it feeds ``_compute_velocity`` and ``_node4_score``
    and so orders the sort that ``n_forward`` truncates — and it IS in the
    address: its home is ``PipelineParameters.current_year``, top-level, which
    ``content_address`` hashes. It appears on this model only as the marshalling
    channel that carries the run's value to the stage. It is NOT a
    ``ForwardParameters`` field because Node 3 scores against the same year and
    two per-stage fields would have to agree with nothing enforcing agreement.
    This stage no longer reads a clock; the value arrives stated.

    Required with no default, like its siblings: the production
    ``acceleration_method`` has one home (``FORWARD_ACCELERATION_METHOD``) and
    the year has one (``PipelineParameters``), and a model default would be a
    second — silently applying whenever a wiring forgot to state it. The
    pre-conversion function defaulted both; that is exactly the second home this
    conversion removes.
    """

    n_forward: int
    lambda_decay: float
    alpha: float
    beta: float
    sort: ForwardSort
    acceleration_method: str
    current_year: int


class _ForwardTraverseInputs(BaseModel):
    """Declared input contract for the ``ForwardTraverse`` handler.

    One field per declared input port (``FORWARD_TRAVERSE_INPUT_PORTS``): the
    resolved seed records to find citing papers for. The executor binds each
    port-declared incoming edge as ``inputs[to_port]``, so the ``inputs`` mapping
    validates directly against this model.
    """

    seeds: list[PaperRecord]


async def forward_traverse(
    params: dict, inputs: dict, *, resources: dict
) -> dict:
    """Executor node handler (type ``ForwardTraverse``) — forward traversal:
    fetch papers citing each seed, rank by α/β score (Node 4).

    For each seed, issues an OpenAlex ``cites:<openalex_id>`` query and maps
    each returned work to a ``PaperRecord`` with ``hop_depth=1``. Papers
    cited by multiple seeds are deduplicated by ``node_id`` with ``root_ids``
    merged as a sorted union (AMD-017). Seeds themselves are excluded. The
    merged set is scored by :func:`_node4_score`, sorted descending, and
    truncated to ``n_forward``.

    Contract (``core/executor.py`` handler convention):
      ``params``    — ``{"n_forward": int, "lambda_decay": float, "alpha": float,
                      "beta": float, "sort": ForwardSort,
                      "acceleration_method": str, "current_year": int}``,
                      validated as ``_ForwardTraverseParams``. All seven are
                      REQUIRED. The first five keep their home in
                      ``PipelineParameters.forward`` and ``current_year`` in
                      ``PipelineParameters`` itself, top-level;
                      ``run_traversal`` reads them there and marshals them in.
                      ``acceleration_method`` comes from
                      ``FORWARD_ACCELERATION_METHOD`` — see that constant and
                      ``_ForwardTraverseParams`` for why it is not a
                      ``ForwardParameters`` field, and for why ``current_year``
                      is not one either despite being fully output-determining.
                      ``sort`` stays required for the reason it always was:
                      OpenAlex's default sort order is not contractual and
                      produces nondeterministic "first 200" sets across runs
                      (AMD-020). Under this contract a missing key is a
                      ``ValidationError`` rather than a ``TypeError``, which is
                      the same refusal through the declared channel.
      ``inputs``    — BOUND. The node declares ``FORWARD_TRAVERSE_INPUT_PORTS``,
                      so the executor builds ``inputs`` solely from the
                      port-declared edges into it, keyed by ``to_port``:
                      ``{"seeds": [...]}``, validated as
                      ``_ForwardTraverseInputs``. Undeclared keys are ignored. A
                      caller invoking the handler directly shapes ``inputs`` the
                      same way, so the direct and executor-driven paths share one
                      contract.
      ``resources`` — ``{"http_client": <httpx.AsyncClient>, "openalex_api_key":
                      <str>}``, name-identical to Node 3's. Both belong to the
                      run, not the graph, and neither enters a content address —
                      a credential in particular is a RESOURCE and never a param.
                      Keyword-only with no default: the node declares, so the
                      executor always supplies, and a silent call without it
                      would be a bug worth crashing on.
      returns       — ``{"forward": Node4Result, "failed_seeds": [...],
                      "truncated_seeds": [...]}`` — the declared output ports
                      (``FORWARD_TRAVERSE_OUTPUT_PORTS``).

    A :class:`Node4Result` — the ranked papers, the citer→seed citation edges,
    per-seed call failures, and per-seed truncation events when OpenAlex reports
    a ``meta.count`` exceeding the returned-results length (currently capped at
    200) — is CONSTRUCTED internally before the return mapping is built, and
    rides the ``forward`` port whole.

    THE ``failed_seeds`` AND ``truncated_seeds`` PORTS ARE CANONICAL. The same
    lists are also reachable at ``forward.failed_seeds`` /
    ``forward.truncated_seeds``, and that duplication is deliberate: a consumer
    reads failure provenance off its own declared port, never by digging into
    another port's payload.

    ``counts_by_year`` is fetched here only — it is not available from Node 0
    or Node 3's ``select=`` fields.
    """
    config = _ForwardTraverseParams.model_validate(params)
    data = _ForwardTraverseInputs.model_validate(inputs)
    seeds = data.seeds
    n_forward = config.n_forward
    lambda_decay = config.lambda_decay
    alpha = config.alpha
    beta = config.beta
    sort = config.sort
    acceleration_method = config.acceleration_method
    # Arrives on `params`, never from the clock: this year feeds the sort that
    # `n_forward` truncates, so a wall-clock read would make two runs with
    # identical seeds and identical parameters return different corpora under the
    # same content address across a New Year boundary — a false HIT.
    current_year = config.current_year
    client = resources["http_client"]
    api_key = resources["openalex_api_key"]

    seed_ids = {s.node_id for s in seeds}
    merged: dict[str, PaperRecord] = {}
    counts_by_id: dict[str, list[dict]] = {}
    failed_seeds: list[FailedSeed] = []
    truncated_seeds: list[TruncatedSeed] = []
    edges: list[CitationEdge] = []

    sleep_s = 0.150
    for idx, seed in enumerate(seeds):
        if idx > 0:
            await asyncio.sleep(sleep_s)

        params = {
            "filter": f"cites:{seed.openalex_id}",
            "select": _FORWARD_SELECT,
            "per-page": "200",
            "sort": sort,
            "api_key": api_key,
        }
        try:
            response = await client.get(OPENALEX_BASE, params=params)
            response.raise_for_status()
        except httpx.HTTPError as e:
            _log.info("cites query failed for %s: %s", seed.node_id, e)
            failed_seeds.append(
                FailedSeed(seed_id=seed.node_id, reason=f"http_error: {e}")
            )
            continue

        payload = response.json() or {}
        results = payload.get("results") or []
        meta = payload.get("meta") or {}
        total_count = meta.get("count")
        if total_count is not None and total_count > len(results):
            _log.info(
                "Node 4: seed %s truncated — returned %d, total %d",
                seed.node_id,
                len(results),
                total_count,
            )
            truncated_seeds.append(
                TruncatedSeed(
                    seed_id=seed.node_id,
                    returned_count=len(results),
                    total_count=total_count,
                )
            )

        for work in results:
            node_id = make_node_id(work)
            if node_id in seed_ids:
                continue
            existing = merged.get(node_id)
            if existing is None:
                rec = _work_to_record(work, hop_depth=1, root_ids=[seed.node_id])
                merged[node_id] = rec
                counts_by_id[node_id] = work.get("counts_by_year") or []
            else:
                existing.root_ids = sorted(set(existing.root_ids) | {seed.node_id})
            edges.append(
                CitationEdge(
                    source_id=node_id,
                    target_id=seed.node_id,
                    type="cites",
                    citing_paper_year=work.get("publication_year"),
                    strength=None,
                )
            )

    def _score(record: PaperRecord) -> float:
        velocity = _compute_velocity(record.citation_count, record.year, current_year)
        acceleration = _compute_acceleration(
            counts_by_id.get(record.node_id, []), acceleration_method
        )
        if acceleration is None:
            _log.debug("acceleration unavailable for %s, using beta=0", record.node_id)
        return _node4_score(
            velocity,
            acceleration,
            record.year,
            current_year,
            alpha,
            beta,
            lambda_decay,
        )

    scored = sorted(merged.values(), key=lambda r: (-_score(r), r.node_id))
    papers = scored[:n_forward]

    paper_ids = {p.node_id for p in papers}
    filtered_edges = [
        e for e in edges
        if e.source_id in paper_ids and e.target_id in seed_ids
    ]
    filtered_edges.sort(key=lambda e: (e.source_id, e.target_id))

    result = Node4Result(
        papers=papers,
        edges=filtered_edges,
        failed_seeds=failed_seeds,
        truncated_seeds=truncated_seeds,
    )
    return {
        "forward": result,
        "failed_seeds": result.failed_seeds,
        "truncated_seeds": result.truncated_seeds,
    }


# ── Node 4.5 — Cycle Cleaning ───────────────────────────────────────────────


#: Port declarations for the ``CleanCycles`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: The input port names are deliberately name-identical to ``AssembleGraph``'s
#: output ports, so a wiring reads ``assemble.nodes -> clean.nodes`` and
#: ``assemble.cites -> clean.cites``.
CLEAN_CYCLES_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("cites"),
]

#: ``cleaned_edges`` and ``cycle_log`` are the two ``CycleCleanResult`` payload
#: fields; ``all_cites`` is the cleaned-plus-suppressed edge view that Node 5 and
#: Node 7 consume. The witness (``input_node_ids``) is NOT a port — it is
#: structural metadata reconstructed from the bound node set by each consumer.
CLEAN_CYCLES_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("cleaned_edges"),
    _untyped_port("cycle_log"),
    _untyped_port("all_cites"),
]


class _CleanCyclesInputs(BaseModel):
    """Declared input contract for the ``CleanCycles`` handler.

    One field per declared input port (``CLEAN_CYCLES_INPUT_PORTS``): the
    assembled node set and the raw (possibly cyclic) citation edges. The
    executor binds each port-declared incoming edge as ``inputs[to_port]``, so
    the ``inputs`` mapping validates directly against this model.
    """

    nodes: list[PaperRecord]
    cites: list[CitationEdge]


async def clean_cycles(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``CleanCycles``) — detect and resolve cycles in
    the citation graph via weakest-link suppression.

    Contract (``core/executor.py`` handler convention):
      ``params``  — UNUSED. This stage takes no configuration; the weakest-link
                    tiebreaker is fixed and the iteration cap derives from
                    ``len(edges)``. Nothing is read off ``params`` and no
                    parameters model exists for it.
      ``inputs``  — BOUND. The node declares ``CLEAN_CYCLES_INPUT_PORTS``, so the
                    executor builds ``inputs`` solely from the port-declared
                    edges into it, keyed by ``to_port``: ``{"nodes": [...],
                    "cites": [...]}``, validated as ``_CleanCyclesInputs``.
                    Undeclared keys are ignored. A caller invoking the handler
                    directly shapes ``inputs`` the same way, so the direct and
                    executor-driven paths share one contract.
      returns     — ``{"cleaned_edges": [...], "cycle_log": CycleLog,
                    "all_cites": [...]}`` — the declared output ports
                    (``CLEAN_CYCLES_OUTPUT_PORTS``).

    A ``CycleCleanResult`` is CONSTRUCTED internally before the return mapping is
    built, so its ``_validate_edge_endpoints`` witness check still fires on every
    call — it is the pipeline's only orphan check, and Nodes 5-8 trust it and run
    no defensive checks of their own. The ports are then decomposed off that
    validated result rather than assembled from bare lists.

    The witness itself is not returned: it is ``exclude=True`` structural
    metadata, and every consumer that needs a ``CycleCleanResult`` back
    reconstructs it from the node set it bound to the ``nodes`` port.

    ``all_cites`` is cleaned edges followed by the suppressed originals, in that
    order. Node 5 (co-citation) and Node 7 (communities) keep real-but-suppressed
    citations for co-occurrence and clustering, while depth and pagerank take
    ``cleaned_edges`` alone because they need the acyclic graph. The split is
    deliberate. Both consumers are seeded but iteration-order-sensitive, so this
    concatenation order is load-bearing and must not be tidied.

    Pure — no I/O, no network, no mutation of inputs. See
    docs/specs/spec-node4.5-cycle-cleaning.md for the full contract, including
    the ordering of the weakest-link tiebreaker and the handling of missing-node
    citation lookups.
    """
    data = _CleanCyclesInputs.model_validate(inputs)
    nodes = data.nodes
    edges = data.cites

    _log.info(
        "Node 4.5: cycle cleaning on %d nodes, %d edges", len(nodes), len(edges)
    )

    citation_by_node: dict[str, int] = {n.node_id: n.citation_count for n in nodes}
    warned_missing: set[str] = set()

    def _citation(node_id: str) -> int:
        if node_id not in citation_by_node:
            if node_id not in warned_missing:
                warned_missing.add(node_id)
                _log.warning(
                    "Node 4.5: edge references unknown node_id %s; "
                    "treating citation_count as 0",
                    node_id,
                )
            return 0
        return citation_by_node[node_id]

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n.node_id)
    for e in edges:
        G.add_edge(e.source_id, e.target_id)

    edge_by_pair: dict[tuple[str, str], CitationEdge] = {
        (e.source_id, e.target_id): e for e in edges
    }

    suppressed: list[SuppressedEdge] = []
    suppressed_pairs: set[tuple[str, str]] = set()
    iterations = 0
    cycles_detected_count = 0
    iteration_cap = len(edges)

    while True:
        try:
            cycle = nx.find_cycle(G, orientation="original")
        except nx.NetworkXNoCycle:
            break

        if iterations >= iteration_cap:
            raise RuntimeError(
                f"Node 4.5: iteration cap ({iteration_cap}) exceeded — "
                "indicates a bug in the cycle cleaning loop, not malformed input."
            )

        iterations += 1
        cycles_detected_count += 1

        cycle_edges: list[tuple[str, str]] = [(edge[0], edge[1]) for edge in cycle]

        seen: set[str] = set()
        cycle_members: list[str] = []
        for u, _v in cycle_edges:
            if u not in seen:
                seen.add(u)
                cycle_members.append(u)

        def _score(pair: tuple[str, str]) -> int:
            u, v = pair
            return _citation(u) + _citation(v)

        weakest = min(cycle_edges, key=lambda e: (_score(e), e[0], e[1]))
        citation_sum = _score(weakest)

        _log.info(
            "Suppressed edge %s -> %s (citation_sum=%d) to break cycle of length %d",
            weakest[0],
            weakest[1],
            citation_sum,
            len(cycle_edges),
        )

        G.remove_edge(weakest[0], weakest[1])
        suppressed_pairs.add(weakest)
        suppressed.append(
            SuppressedEdge(
                original=edge_by_pair[weakest],
                citation_sum=citation_sum,
                cycle_members=cycle_members,
            )
        )

    if iterations == 0:
        _log.debug("Node 4.5: no cycles detected")

    cleaned_edges = [
        e for e in edges if (e.source_id, e.target_id) not in suppressed_pairs
    ]

    affected = {p[0] for p in suppressed_pairs} | {p[1] for p in suppressed_pairs}
    _log.info(
        "Node 4.5 complete: %d iterations, %d edges suppressed, %d affected node_ids",
        iterations,
        len(suppressed),
        len(affected),
    )

    # Constructed, not skipped: this is where the witness validator fires. The
    # ports below are decomposed off the validated result, so no return path
    # bypasses the pipeline's only orphan check.
    result = CycleCleanResult(
        cleaned_edges=cleaned_edges,
        cycle_log=CycleLog(
            suppressed_edges=suppressed,
            cycles_detected_count=cycles_detected_count,
            iterations=iterations,
        ),
        input_node_ids=frozenset(n.node_id for n in nodes),
    )

    return {
        "cleaned_edges": result.cleaned_edges,
        "cycle_log": result.cycle_log,
        # Cleaned edges first, then the suppressed originals appended — order is
        # load-bearing for the seeded-but-order-sensitive Node 5/7 consumers.
        "all_cites": result.cleaned_edges
        + [s.original for s in result.cycle_log.suppressed_edges],
    }


# ── Node 5 — Co-Citation ────────────────────────────────────────────────────


#: Port declarations for the ``ComputeCoCitations`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: The input port names are deliberately name-identical to the two upstream
#: stages' output ports, so a wiring reads ``assemble.nodes -> co.nodes`` and
#: ``clean.all_cites -> co.all_cites`` with no adapter node between them. The
#: ``all_cites`` name is load-bearing on its own: it is the cleaned-plus-suppressed
#: view, deliberately NOT ``cleaned_edges``, because co-occurrence keeps
#: real-but-suppressed citations.
CO_CITATIONS_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("all_cites"),
]

#: The two ``Node5Result`` payload fields, carried under the names
#: ``PipelineResult`` consumes them by. The bare model field names (``edges``,
#: ``warnings``) are too generic to read at a graph edge, where the port name is
#: all a wiring shows.
CO_CITATIONS_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("co_citation_edges"),
    _untyped_port("co_citation_warnings"),
]


class _CoCitationInputs(BaseModel):
    """Declared input contract for the ``ComputeCoCitations`` handler.

    One field per declared input port (``CO_CITATIONS_INPUT_PORTS``): the
    assembled node set and the cleaned-plus-suppressed citation edge view. The
    executor binds each port-declared incoming edge as ``inputs[to_port]``, so
    the ``inputs`` mapping validates directly against this model.
    """

    nodes: list[PaperRecord]
    all_cites: list[CitationEdge]


async def compute_co_citations(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``ComputeCoCitations``) — compute co-citation
    edges across the assembled citation graph.

    Two papers A and B are co-cited whenever any third paper C cites both;
    the number of shared citers is the edge ``strength``.

    Contract (``core/executor.py`` handler convention):
      ``params``  — ``{"min_strength": int, "max_edges": int | None}``, validated
                    as ``CoCitationParameters``; absent keys fall back to the
                    frozen Node 5 defaults. Config keeps its home in
                    ``PipelineParameters.co_citation``; ``run_traversal`` reads it
                    there and marshals it in.
      ``inputs``  — BOUND. The node declares ``CO_CITATIONS_INPUT_PORTS``, so the
                    executor builds ``inputs`` solely from the port-declared
                    edges into it, keyed by ``to_port``: ``{"nodes": [...],
                    "all_cites": [...]}``, validated as ``_CoCitationInputs``.
                    Undeclared keys are ignored. A caller invoking the handler
                    directly shapes ``inputs`` the same way, so the direct and
                    executor-driven paths share one contract.
      returns     — ``{"co_citation_edges": [...], "co_citation_warnings": [...]}``
                    — the declared output ports (``CO_CITATIONS_OUTPUT_PORTS``).

    ``CoCitationParameters`` carries no field constraints, so the range checks
    stay here rather than in the model: ``ValueError`` on ``min_strength`` (< 1)
    or ``max_edges`` (< 0), applied to the validated config.

    A ``Node5Result`` is CONSTRUCTED internally before the return mapping is
    built, and the ports are decomposed off that validated result rather than
    assembled from bare lists.

    Both endpoints of every edge are checked for unknown-ness unconditionally
    (Option A, IDG-023), so ``co_citation_warnings`` lists every distinct unknown
    ``node_id`` in first-encounter order. See
    docs/specs/spec-node5-co-citation.md for the full contract, including the
    global cross-root semantics (AMD-017), canonical form, and sort ordering.

    Pure — no I/O, no network, no mutation of inputs.
    """
    config = CoCitationParameters.model_validate(params)
    data = _CoCitationInputs.model_validate(inputs)
    nodes = data.nodes
    cites_edges = data.all_cites
    min_strength = config.min_strength
    max_edges = config.max_edges

    if min_strength < 1:
        raise ValueError(f"min_strength must be >= 1, got {min_strength}")
    if max_edges is not None and max_edges < 0:
        raise ValueError(f"max_edges must be >= 0 or None, got {max_edges}")

    _log.info(
        "Node 5: co-citation on %d nodes, %d citation edges, min_strength=%d",
        len(nodes),
        len(cites_edges),
        min_strength,
    )

    node_ids: set[str] = {n.node_id for n in nodes}
    citers: dict[str, set[str]] = {nid: set() for nid in node_ids}
    warned_missing: set[str] = set()  # dedup guard — membership test only
    warnings: list[str] = []  # ordered, first-encounter — the RETURNED field

    for e in cites_edges:
        # Missing-node provenance pass — examine BOTH endpoints, UNCONDITIONALLY,
        # before any index-construction skip. One entry per distinct unknown
        # node_id, in first-encounter order. The set guards dedup; the list
        # preserves order — never derive warnings from the set.
        for nid in (e.source_id, e.target_id):
            if nid not in node_ids and nid not in warned_missing:
                warned_missing.add(nid)
                warnings.append(nid)
                _log.warning(
                    "Node 5: citation edge references unknown node_id %s; skipping",
                    nid,
                )
        # Index-construction skips — Node-5-specific; they NEVER suppress a warning.
        if e.source_id == e.target_id:
            continue
        if e.source_id not in node_ids or e.target_id not in node_ids:
            continue
        citers[e.target_id].add(e.source_id)

    targets = sorted(citers.keys())
    co_edges: list[CitationEdge] = []
    for i in range(len(targets)):
        t1 = targets[i]
        citers_t1 = citers[t1]
        if not citers_t1:
            continue
        for j in range(i + 1, len(targets)):
            t2 = targets[j]
            citers_t2 = citers[t2]
            if not citers_t2:
                continue
            strength = len(citers_t1 & citers_t2)
            if strength >= min_strength:
                co_edges.append(
                    CitationEdge(
                        source_id=t1,
                        target_id=t2,
                        type="co_citation",
                        citing_paper_year=None,
                        strength=strength,
                    )
                )

    co_edges.sort(key=lambda e: (-e.strength, e.source_id, e.target_id))
    if max_edges is not None:
        co_edges = co_edges[:max_edges]

    if not co_edges:
        _log.debug("Node 5: no co-citation pairs met min_strength threshold")

    _log.info(
        "Node 5 complete: %d co-citation edges emitted (min_strength=%d, max_edges=%s)",
        len(co_edges),
        min_strength,
        max_edges,
    )

    # Constructed, not skipped: the ports below are decomposed off the validated
    # result rather than assembled from the bare lists above.
    result = Node5Result(edges=co_edges, warnings=warnings)

    return {
        "co_citation_edges": result.edges,
        "co_citation_warnings": result.warnings,
    }


# ── Node 6 — Metric Computation ─────────────────────────────────────────────


#: Port declarations for the ``ComputeDepthMetrics`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: The input port names are deliberately name-identical to the two upstream
#: stages' output ports, so a wiring reads ``annotate.nodes -> depth.nodes`` and
#: ``clean.cleaned_edges -> depth.cleaned_edges`` with no adapter node between
#: them. The ``cleaned_edges`` name is load-bearing on its own: depth takes the
#: ACYCLIC view, deliberately NOT ``all_cites``, because a suppressed edge would
#: shorten hop distances and blur the direction categories. That split is
#: documented on ``clean_cycles`` and is the same one ``ComputePagerank`` takes.
COMPUTE_DEPTH_METRICS_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("cleaned_edges"),
]

#: The single ``{node_id: DepthMetrics}`` payload, carried under the name
#: ``PipelineResult`` consumes it by. A bare ``metrics`` would be too generic to
#: read at a graph edge, where the port name is all a wiring shows.
COMPUTE_DEPTH_METRICS_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("depth_metrics"),
]


class _DepthMetricsInputs(BaseModel):
    """Declared input contract for the ``ComputeDepthMetrics`` handler.

    One field per declared input port (``COMPUTE_DEPTH_METRICS_INPUT_PORTS``):
    the assembled node set and the cleaned (acyclic) citation edges. The executor
    binds each port-declared incoming edge as ``inputs[to_port]``, so the
    ``inputs`` mapping validates directly against this model.
    """

    nodes: list[PaperRecord]
    cleaned_edges: list[CitationEdge]


async def compute_depth_metrics(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``ComputeDepthMetrics``) — per-node depth
    metrics on the cleaned citation graph.

    For every input node, emits a ``DepthMetrics`` carrying
    ``hop_depth_per_root`` (BFS distance from each reaching root over the
    undirected view of the graph) and ``traversal_direction`` (categorical
    position relative to the seed set: seed/backward/forward/mixed). See
    docs/specs/spec-node6-metrics.md and AMD-019 for the full contract.

    Contract (``core/executor.py`` handler convention):
      ``params``  — UNUSED. This stage takes no configuration; the BFS is over
                    the whole bound graph and the four direction categories are
                    fixed. Nothing is read off ``params`` and no parameters model
                    exists for it.
      ``inputs``  — BOUND. The node declares ``COMPUTE_DEPTH_METRICS_INPUT_PORTS``,
                    so the executor builds ``inputs`` solely from the
                    port-declared edges into it, keyed by ``to_port``:
                    ``{"nodes": [...], "cleaned_edges": [...]}``, validated as
                    ``_DepthMetricsInputs``. Undeclared keys are ignored. A caller
                    invoking the handler directly shapes ``inputs`` the same way,
                    so the direct and executor-driven paths share one contract.
      returns     — ``{"depth_metrics": {node_id: DepthMetrics}}`` — the declared
                    output port (``COMPUTE_DEPTH_METRICS_OUTPUT_PORTS``).

    Raises ``ValueError`` if no roots are present in ``nodes`` or if any
    node is unreachable from every root. Pure — no I/O, no mutation of inputs.
    """
    data = _DepthMetricsInputs.model_validate(inputs)
    nodes = data.nodes
    cleaned_edges = data.cleaned_edges

    if not nodes:
        return {"depth_metrics": {}}

    roots = [n.node_id for n in nodes if n.node_id in n.root_ids]
    if not roots:
        raise ValueError("No roots found in nodes")

    G_directed: nx.DiGraph = nx.DiGraph()
    G_directed.add_nodes_from(n.node_id for n in nodes)
    G_directed.add_edges_from((e.source_id, e.target_id) for e in cleaned_edges)
    G_undirected = G_directed.to_undirected()

    _log.info(
        "Node 6 depth: %d nodes, %d edges, %d roots",
        len(nodes),
        len(cleaned_edges),
        len(roots),
    )

    undirected_distance: dict[str, dict[str, int]] = {}
    forward_from: dict[str, set[str]] = {}
    backward_from: dict[str, set[str]] = {}
    for r in roots:
        undirected_distance[r] = nx.single_source_shortest_path_length(
            G_undirected, r
        )
        backward_from[r] = nx.descendants(G_directed, r)  # papers the seed cites
        forward_from[r] = nx.ancestors(G_directed, r)     # papers citing the seed

    roots_set = set(roots)
    counts = {"seed": 0, "backward": 0, "forward": 0, "mixed": 0}
    result: dict[str, DepthMetrics] = {}

    for n in nodes:
        nid = n.node_id
        reaching_roots = [r for r in roots if nid in undirected_distance[r]]

        if not reaching_roots:
            _log.error("Node %s unreachable from any root", nid)
            raise ValueError(f"Node {nid} unreachable from any root")

        hop_depth_per_root = {r: undirected_distance[r][nid] for r in reaching_roots}

        if nid in roots_set:
            direction: Literal["seed", "backward", "forward", "mixed"] = "seed"
        else:
            backward_hits = [r for r in reaching_roots if nid in backward_from[r]]
            forward_hits = [r for r in reaching_roots if nid in forward_from[r]]
            if backward_hits == reaching_roots and not forward_hits:
                direction = "backward"
            elif forward_hits == reaching_roots and not backward_hits:
                direction = "forward"
            else:
                direction = "mixed"

        counts[direction] += 1
        result[nid] = DepthMetrics(
            hop_depth_per_root=hop_depth_per_root,
            traversal_direction=direction,
        )

    _log.info(
        "Node 6 depth complete: seed=%d, backward=%d, forward=%d, mixed=%d",
        counts["seed"],
        counts["backward"],
        counts["forward"],
        counts["mixed"],
    )

    return {"depth_metrics": result}


#: Port declarations for the ``ComputePagerank`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
COMPUTE_PAGERANK_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("cleaned_edges"),
]

COMPUTE_PAGERANK_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("pagerank"),
]


class _PageRankInputs(BaseModel):
    """Declared input contract for the ``ComputePagerank`` handler.

    One field per declared input port (``COMPUTE_PAGERANK_INPUT_PORTS``): the
    assembled node set and the cleaned (acyclic) citation edges. The executor
    binds each port-declared incoming edge as ``inputs[to_port]``, so the
    ``inputs`` mapping validates directly against this model.
    """

    nodes: list[PaperRecord]
    cleaned_edges: list[CitationEdge]


async def compute_pagerank(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``ComputePagerank``) — PageRank over the
    cleaned citation graph.

    Contract (``core/executor.py`` handler convention):
      ``params``  — ``{"damping": float}``, validated as ``PageRankParameters``;
                    an absent ``damping`` falls back to the frozen Node 6 default.
                    Config keeps its home in ``PipelineParameters.pagerank``;
                    ``run_traversal`` reads it there and marshals it in.
      ``inputs``  — BOUND. The node declares ``COMPUTE_PAGERANK_INPUT_PORTS``, so
                    the executor builds ``inputs`` solely from the port-declared
                    edges into it, keyed by ``to_port``: ``{"nodes": [...],
                    "cleaned_edges": [...]}``, validated as ``_PageRankInputs``.
                    Undeclared keys are ignored. A caller invoking the handler
                    directly shapes ``inputs`` the same way, so the direct and
                    executor-driven paths share one contract.
      returns     — ``{"pagerank": {node_id: pagerank}}`` — the declared output
                    port ``pagerank`` (``COMPUTE_PAGERANK_OUTPUT_PORTS``).

    Every input node receives a value, including isolates. Output values sum to
    1.0 within NetworkX convergence tolerance. ``damping`` is passed to
    ``nx.pagerank`` as ``alpha``; out-of-range values raise via NetworkX. Pure —
    no I/O, no mutation of inputs.
    """
    config = PageRankParameters.model_validate(params)
    data = _PageRankInputs.model_validate(inputs)
    nodes = data.nodes
    cleaned_edges = data.cleaned_edges

    if not nodes:
        return {"pagerank": {}}

    _log.info(
        "Node 6 pagerank: %d nodes, %d edges, alpha=%s",
        len(nodes),
        len(cleaned_edges),
        config.damping,
    )

    G: nx.DiGraph = nx.DiGraph()
    G.add_nodes_from(n.node_id for n in nodes)
    G.add_edges_from((e.source_id, e.target_id) for e in cleaned_edges)

    pr = nx.pagerank(G, alpha=config.damping)

    _log.info("Node 6 pagerank complete")

    return {"pagerank": dict(pr)}


# ── Node 7 — Community Detection ────────────────────────────────────────────


#: Port declarations for the ``DetectCommunities`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: ``all_cites`` is name-identical to ``CleanCycles``' output port of the same name
#: (``CLEAN_CYCLES_OUTPUT_PORTS``) and to ``ComputeCoCitations``' input port, so a
#: wiring reads ``clean.all_cites -> communities.all_cites`` with no renaming. This
#: stage takes the cleaned-plus-suppressed view, not ``cleaned_edges``: suppressed
#: citations are real co-occurrence signal for clustering.
DETECT_COMMUNITIES_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("all_cites"),
]

#: EXACTLY ONE output port, carrying the whole ``CommunityResult``. Ports are named
#: for what the CONSUMER consumes them by, and ``PipelineResult.communities`` takes
#: the result whole — this is the ``compute_depth_metrics`` shape, not the
#: decomposed ``clean_cycles`` / ``compute_co_citations`` one, which decomposed only
#: because ``PipelineResult`` has a separate field per payload field. Decomposing
#: here would force every consumer to rebuild a ``CommunityResult`` from loose
#: ports.
DETECT_COMMUNITIES_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("communities"),
]


class _DetectCommunitiesInputs(BaseModel):
    """Declared input contract for the ``DetectCommunities`` handler.

    One field per declared input port (``DETECT_COMMUNITIES_INPUT_PORTS``): the
    assembled node set and the cleaned-plus-suppressed citation edge view. The
    executor binds each port-declared incoming edge as ``inputs[to_port]``, so the
    ``inputs`` mapping validates directly against this model.
    """

    nodes: list[PaperRecord]
    all_cites: list[CitationEdge]


async def detect_communities(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``DetectCommunities``) — assign a community
    label to every node in the assembled citation graph.

    Contract (``core/executor.py`` handler convention):
      ``params``  — ``{"infomap_seed": int, "infomap_trials": int,
                    "infomap_teleportation": float, "leiden_seed": int,
                    "community_count_min": int, "community_count_max": int}``,
                    validated as ``CommunitiesParameters``; absent keys fall back
                    to the frozen Node 7 defaults, which live in that model rather
                    than in this signature. Config keeps its home in
                    ``PipelineParameters.communities``; ``run_traversal`` reads it
                    there and marshals it in.
      ``inputs``  — BOUND. The node declares ``DETECT_COMMUNITIES_INPUT_PORTS``, so
                    the executor builds ``inputs`` solely from the port-declared
                    edges into it, keyed by ``to_port``: ``{"nodes": [...],
                    "all_cites": [...]}``, validated as
                    ``_DetectCommunitiesInputs``. Undeclared keys are ignored. A
                    caller invoking the handler directly shapes ``inputs`` the same
                    way, so the direct and executor-driven paths share one contract.
      returns     — ``{"communities": CommunityResult}`` — the single declared
                    output port (``DETECT_COMMUNITIES_OUTPUT_PORTS``), carrying the
                    result whole.

    Runs Infomap (primary) over the directed citation graph, falling back to
    Leiden when ``infomap`` is not installed. Both algorithms produce a flat
    partition keyed by ``node_id``; isolates receive an assignment. The
    handler does not modify graph structure or filter nodes.

    See docs/specs/spec-node7-community-detection.md for the full contract,
    including fallback policy, edge-input semantics (cleaned ∪ suppressed),
    and LOD validation thresholds.

    Raises ``RuntimeError`` if neither ``infomap`` nor ``leidenalg`` is
    installed. Pure — no I/O, no mutation of inputs.
    """
    config = CommunitiesParameters.model_validate(params)
    data = _DetectCommunitiesInputs.model_validate(inputs)
    nodes = data.nodes
    cites_edges = data.all_cites

    if not nodes:
        _log.debug("Node 7: empty input — no communities to detect")
        return {
            "communities": CommunityResult(
                community_assignments={},
                algorithm_used="infomap",
                community_count=0,
                validation_flags=[],
                warnings=[],  # no edge validation runs on empty input
            )
        }

    _log.info("Node 7: %d nodes, %d edges", len(nodes), len(cites_edges))

    node_id_set = {n.node_id for n in nodes}
    warned_missing: set[str] = set()  # dedup guard — membership test only
    warnings: list[str] = []  # ordered, first-encounter — the RETURNED field
    valid_edges: list[CitationEdge] = []
    for e in cites_edges:
        for nid in (e.source_id, e.target_id):
            if nid not in node_id_set and nid not in warned_missing:
                warned_missing.add(nid)
                warnings.append(nid)  # preserves order — never derive from set
                _log.warning(
                    "Node 7: edge references unknown node_id %s; skipping",
                    nid,
                )
        if e.source_id in node_id_set and e.target_id in node_id_set:
            valid_edges.append(e)

    try:
        from infomap import Infomap  # noqa: F401
        partial = _run_infomap(
            nodes,
            valid_edges,
            config.infomap_seed,
            config.infomap_trials,
            config.infomap_teleportation,
        )
    except ImportError:
        try:
            import igraph  # noqa: F401
            import leidenalg  # noqa: F401
            partial = _run_leiden(nodes, valid_edges, config.leiden_seed)
        except ImportError:
            raise RuntimeError(
                "Neither infomap nor leidenalg is installed. "
                "Install community detection dependencies: "
                "uv sync --extra community"
            ) from None

    flags: list[str] = []
    if partial.community_count < config.community_count_min:
        flags.append("community_count_below_minimum")
    if partial.community_count > config.community_count_max:
        flags.append("community_count_above_maximum")

    result = CommunityResult(
        community_assignments=partial.community_assignments,
        algorithm_used=partial.algorithm_used,
        community_count=partial.community_count,
        validation_flags=flags,
        warnings=warnings,
    )

    _log.info(
        "Node 7 complete: %d communities via %s — flags: %s",
        result.community_count,
        result.algorithm_used,
        result.validation_flags or "none",
    )

    return {"communities": result}


def _run_infomap(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],
    seed: int,
    trials: int,
    teleportation: float,
) -> CommunityResult:
    """Infomap path. Builds nx.DiGraph then hands it to Infomap via
    add_networkx_graph(). --two-level forces a flat partition; the graph
    is unweighted (every input edge is a 'cites' edge with strength=None).
    """
    from infomap import Infomap

    G: nx.DiGraph = nx.DiGraph()
    G.add_nodes_from(n.node_id for n in nodes)
    G.add_edges_from((e.source_id, e.target_id) for e in cites_edges)

    im = Infomap(f"--two-level --silent --seed {seed}")
    internal_to_name: dict[int, str] = im.add_networkx_graph(G)
    im.num_trials = trials
    im.teleportation_probability = teleportation
    im.run()

    modules = im.get_modules()
    assignments = {
        internal_to_name[i]: str(mid) for i, mid in modules.items()
    }

    return CommunityResult(
        community_assignments=assignments,
        algorithm_used="infomap",
        community_count=len(set(assignments.values())),
        validation_flags=[],
    )


def _run_leiden(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],
    seed: int,
) -> CommunityResult:
    """Leiden fallback. Round-trips node_ids through integer indices: igraph
    preserves vertex insertion order, so partition.membership[i] is the
    community for node_ids[i]. add_vertices() runs before add_edges() so
    isolates are pre-registered and receive an assignment.
    """
    import igraph
    import leidenalg

    node_ids = [n.node_id for n in nodes]
    idx = {nid: i for i, nid in enumerate(node_ids)}

    g = igraph.Graph(directed=True)
    g.add_vertices(len(node_ids))
    g.vs["name"] = node_ids
    g.add_edges([(idx[e.source_id], idx[e.target_id]) for e in cites_edges])

    partition = leidenalg.find_partition(
        g,
        leidenalg.ModularityVertexPartition,
        seed=seed,
    )

    assignments = {
        node_ids[i]: str(partition.membership[i]) for i in range(len(node_ids))
    }

    return CommunityResult(
        community_assignments=assignments,
        algorithm_used="leiden",
        community_count=len(set(assignments.values())),
        validation_flags=[],
    )


# ── Node Enrichment — End-of-Pipeline Merge ─────────────────────────────────


#: Port declarations for the ``EnrichNodes`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: ``depth_metrics``, ``pagerank`` and ``communities`` are deliberately
#: name-identical to their producers' declared output ports
#: (``COMPUTE_DEPTH_METRICS_OUTPUT_PORTS``, ``COMPUTE_PAGERANK_OUTPUT_PORTS``,
#: ``DETECT_COMMUNITIES_OUTPUT_PORTS``), so a wiring reads
#: ``depth.depth_metrics -> enrich.depth_metrics``,
#: ``pagerank.pagerank -> enrich.pagerank`` and
#: ``communities.communities -> enrich.communities`` with no adapter node between
#: them. ``nodes`` is the same node-set port every metric stage takes, and binds
#: the same node set they were computed over — this is a four-input join, so the
#: three metric ports and the node set must describe one graph.
ENRICH_NODES_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("depth_metrics"),
    _untyped_port("pagerank"),
    _untyped_port("communities"),
]

#: EXACTLY ONE output port, carrying the whole enriched ``list[PaperRecord]``.
#: Ports are named for what the CONSUMER consumes them by, and a bare ``nodes``
#: would be too generic to read at a graph edge, where the port name is all a
#: wiring shows — the ``COMPUTE_DEPTH_METRICS_OUTPUT_PORTS`` rationale. Here it
#: would also collide in the reader's eye with this stage's own ``nodes`` INPUT
#: port, which carries the UNenriched set.
ENRICH_NODES_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("enriched_nodes"),
]


class _EnrichNodesInputs(BaseModel):
    """Declared input contract for the ``EnrichNodes`` handler.

    One field per declared input port (``ENRICH_NODES_INPUT_PORTS``): the node
    set to enrich, and the three metric payloads merged onto it. The executor
    binds each port-declared incoming edge as ``inputs[to_port]``, so the
    ``inputs`` mapping validates directly against this model.
    """

    nodes: list[PaperRecord]
    depth_metrics: dict[str, DepthMetrics]
    pagerank: dict[str, float]
    communities: CommunityResult


async def enrich_nodes(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``EnrichNodes``) — merge the computed metrics
    onto the assembled node set.

    For every input node, emits a copy carrying ``traversal_direction`` and
    ``hop_depth_per_root`` from the depth metrics, ``pagerank`` from the pagerank
    mapping, and ``community_id`` from the community assignments. The write path
    is immutable — ``model_copy(update=...)`` — and Node 6 owns the canonical
    ``hop_depth_per_root`` / ``traversal_direction``, so the input records are
    left as they were.

    Contract (``core/executor.py`` handler convention):
      ``params``  — UNUSED. This stage takes no configuration; it is a four-input
                    join and nothing about the merge is tunable. Nothing is read
                    off ``params`` and no parameters model exists for it.
      ``inputs``  — BOUND. The node declares ``ENRICH_NODES_INPUT_PORTS``, so the
                    executor builds ``inputs`` solely from the port-declared
                    edges into it, keyed by ``to_port``: ``{"nodes": [...],
                    "depth_metrics": {...}, "pagerank": {...},
                    "communities": CommunityResult}``, validated as
                    ``_EnrichNodesInputs``. Undeclared keys are ignored. A caller
                    invoking the handler directly shapes ``inputs`` the same way,
                    so the direct and executor-driven paths share one contract.
      returns     — ``{"enriched_nodes": [PaperRecord, ...]}`` — the single
                    declared output port (``ENRICH_NODES_OUTPUT_PORTS``),
                    carrying the whole enriched node set.

    ``_EnrichNodesInputs`` validates SHAPE, not COVERAGE: a ``node_id`` present
    in ``nodes`` but absent from any of the three mappings raises ``KeyError``
    out of the merge, which is exactly what it did before this stage was bound.
    Pure — no I/O, no mutation of inputs.
    """
    data = _EnrichNodesInputs.model_validate(inputs)
    depth = data.depth_metrics
    prank = data.pagerank
    communities = data.communities

    enriched = [
        node.model_copy(
            update={
                "traversal_direction": depth[node.node_id].traversal_direction,
                "hop_depth_per_root": depth[node.node_id].hop_depth_per_root,
                "pagerank": prank[node.node_id],
                "community_id": communities.community_assignments[node.node_id],
            }
        )
        for node in data.nodes
    ]

    return {"enriched_nodes": enriched}


# ── Pipeline Orchestrator ───────────────────────────────────────────────────


class PipelineError(Exception):
    """Defensive-guard error for ``run_arxiv_pipeline``.

    Raised only for the should-not-happen case where ``fetch_seeds`` returns an
    empty ``resolved`` list *without* raising — a Node 0 contract violation.
    Normal total failure (every seed fails resolution) is ``fetch_seeds``' own
    ``ValueError``, which the orchestrator lets propagate unwrapped; it is not
    wrapped in ``PipelineError``.
    """


#: Port declarations for the ``AssembleGraph`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
ASSEMBLE_GRAPH_INPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("seeds"),
    _untyped_port("backward"),
    _untyped_port("forward"),
]

ASSEMBLE_GRAPH_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("nodes"),
    _untyped_port("cites"),
    _untyped_port("mismatches"),
]


class _AssembleGraphInputs(BaseModel):
    """Declared input contract for the ``AssembleGraph`` handler.

    One field per declared input port (``ASSEMBLE_GRAPH_INPUT_PORTS``): the
    resolved seed records, the Node 3 backward result, and the Node 4 forward
    result. The executor binds each port-declared incoming edge as
    ``inputs[to_port]``, so the ``inputs`` mapping validates directly against
    this model.
    """

    seeds: list[PaperRecord]
    backward: Node3Result
    forward: Node4Result


async def assemble_graph(params: dict, inputs: dict) -> dict:
    """Executor node handler (type ``AssembleGraph``) — reconcile seeds, Node 3,
    and Node 4 into one node set and one cites edge set.

    Contract (``core/executor.py`` handler convention):
      ``params``  — UNUSED. This stage takes no configuration; nothing is read
                    off ``params`` and no parameters model exists for it.
      ``inputs``  — BOUND. The node declares ``ASSEMBLE_GRAPH_INPUT_PORTS``, so
                    the executor builds ``inputs`` solely from the port-declared
                    edges into it, keyed by ``to_port``: ``{"seeds": [...],
                    "backward": Node3Result, "forward": Node4Result}``, validated
                    as ``_AssembleGraphInputs``. Undeclared keys are ignored. A
                    caller invoking the handler directly shapes ``inputs`` the
                    same way, so the direct and executor-driven paths share one
                    contract.
      returns     — ``{"nodes": [...], "cites": [...], "mismatches": [...]}`` —
                    the declared output ports (``ASSEMBLE_GRAPH_OUTPUT_PORTS``),
                    in the order the pre-binding 3-tuple carried them.

    Pure. Does **not** do cross-seed dedup — Nodes 3 and 4 already did that
    internally (the global top-N cap is applied across the cross-seed union
    inside each node). Its only job is the backward ∪ forward ∪ seed union, where
    a node or edge can legitimately appear in more than one of the three sources.

    Bucket-then-reduce: one ``model_copy`` per unique node, hash-based dedup, no
    O(N²) existence checks. Mirrors ``clean_cycles``' ``edge_by_pair`` lookup.

    ``mismatches`` records each ``(source_id, target_id, type)`` edge whose
    backward and forward views disagree on metadata; the first-seen (backward)
    edge is kept (OQ3).
    """
    data = _AssembleGraphInputs.model_validate(inputs)
    seeds = data.seeds
    backward = data.backward
    forward = data.forward

    node_buckets: dict[str, tuple[PaperRecord, set[str]]] = {}
    edge_buckets: dict[tuple[str, str, str], CitationEdge] = {}
    mismatches: list[EdgeMetadataMismatch] = []

    def _add_node(rec: PaperRecord) -> None:
        existing = node_buckets.get(rec.node_id)
        if existing is None:
            # First-seen record wins; seed its root_ids set.
            node_buckets[rec.node_id] = (rec, set(rec.root_ids))
        else:
            # Union this source's root_ids into the accumulating set. This is
            # the only cross-source union the orchestrator performs.
            existing[1].update(rec.root_ids)

    # Seeds are the roots of the graph (root_ids == [node_id]).
    for seed in seeds:
        _add_node(seed)
    for paper in backward.papers:
        _add_node(paper)
    for paper in forward.papers:
        _add_node(paper)

    def _add_edge(edge: CitationEdge) -> None:
        key = (edge.source_id, edge.target_id, edge.type)
        existing = edge_buckets.get(key)
        if existing is None:
            edge_buckets[key] = edge
            return
        if (
            existing.citing_paper_year != edge.citing_paper_year
            or existing.strength != edge.strength
        ):
            mismatches.append(
                EdgeMetadataMismatch(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    type=edge.type,
                    detail=(
                        f"citing_paper_year {existing.citing_paper_year!r} vs "
                        f"{edge.citing_paper_year!r}; strength "
                        f"{existing.strength!r} vs {edge.strength!r}"
                    ),
                )
            )
        # First-seen (backward before forward) is kept regardless.

    for edge in backward.edges:
        _add_edge(edge)
    for edge in forward.edges:
        _add_edge(edge)

    unified_nodes = [
        rec.model_copy(update={"root_ids": sorted(roots)})
        for rec, roots in node_buckets.values()
    ]
    unified_cites = list(edge_buckets.values())
    return {
        "nodes": unified_nodes,
        "cites": unified_cites,
        "mismatches": mismatches,
    }


#: Port declarations for the ``ResolveSeeds`` node. These are the contract:
#: a graph wiring this node declares them on the ``Node``, and ``validate_integrity``
#: checks the edges against them without reading this handler's source.
#:
#: AN EMPTY LIST, NOT ``None``. Per ``core/executor.py::_is_bound`` an empty list
#: IS a declaration: the node is bound and accepts no inputs, so the executor
#: builds its ``inputs`` from port-declared incoming edges — of which there are
#: none — rather than handing it every upstream payload keyed by source id.
#: ``None`` would leave the node in the legacy regime, which is the opposite of
#: this conversion. Node 0 is the head of the pipeline: its seed set arrives as
#: CONFIGURATION on ``params``, not as dataflow from a producer, so having
#: nothing to bind is the shape of the stage rather than an omission.
RESOLVE_SEEDS_INPUT_PORTS: list[PortDeclaration] = []

#: TWO output ports.
#:
#: ``seeds`` carries the resolved ``list[PaperRecord]`` that feeds BOTH the
#: content-address key and traversal. It is name-identical to the ``seeds`` input
#: port ``BACKWARD_TRAVERSE_INPUT_PORTS``, ``FORWARD_TRAVERSE_INPUT_PORTS`` and
#: ``ASSEMBLE_GRAPH_INPUT_PORTS`` already declare, so a future wiring feeds all
#: three consumers from this one producer with no renaming.
#:
#: ``seed_failures`` carries the typed per-seed resolution failures. Not folded
#: into the ``seeds`` port as one struct: that shape was considered and rejected,
#: on the grounds already written into ``BACKWARD_TRAVERSE_OUTPUT_PORTS``' comment
#: — failure provenance rides its own declared output port rather than being dug
#: out of another port's payload by the consumer. The port is currently
#: unconsumed by any graph, and that is legal — ``validate_integrity`` checks
#: referential integrity and port-declared edges, never unconsumed outputs.
RESOLVE_SEEDS_OUTPUT_PORTS: list[PortDeclaration] = [
    _untyped_port("seeds"),
    _untyped_port("seed_failures"),
]


class _ResolveSeedsParams(BaseModel):
    """Declared param contract for the ``ResolveSeeds`` handler.

    One field: the requested seed identifier dicts. It is CONFIGURATION and it
    belongs on ``params`` — plain JSON, serializable, no network types — because
    Node 0 is the head of the pipeline and has no producer to bind a seed set
    from. The credential and the HTTP client that resolve it are RESOURCES,
    declared on the node and supplied by the run; neither is a param, and a
    credential never becomes one.

    Required with no default. An empty seed list is not a configuration state to
    fall back to — it is the halt this stage raises ``ValueError`` on — so a
    model default would only turn a wiring that forgot to state its seeds into a
    run that resolves nothing.
    """

    seeds: list[dict]


async def resolve_seeds(params: dict, inputs: dict, *, resources: dict) -> dict:
    """Executor node handler (type ``ResolveSeeds``) — Node 0 seed resolution.

    The resolution phase, held apart from traversal so the uncached orchestrator
    and the read-through cache share ONE resolution per invocation.

    Contract (``core/executor.py`` handler convention):
      ``params``    — ``{"seeds": [request dicts]}``, validated as
                      ``_ResolveSeedsParams``. Seed identifier dicts
                      (``{"arxiv_id": ...}`` / ``{"doi": ...}``) — the exact
                      shape ``fetch_seeds`` accepts; shape classification is
                      Node 0's own job. REQUIRED.
      ``inputs``    — BOUND AND EMPTY. The node declares
                      ``RESOLVE_SEEDS_INPUT_PORTS``, which is ``[]``: an empty
                      list is a declaration, so the node is on the bound side of
                      the fence and accepts no inputs. Nothing is read off
                      ``inputs`` and no inputs model exists for it. A caller
                      invoking the handler directly passes ``{}``, so the direct
                      and executor-driven paths share one contract.
      ``resources`` — ``{"http_client": <httpx.AsyncClient>, "openalex_api_key":
                      <str>}``. Name-identical to what ``backward_traverse`` and
                      ``forward_traverse`` declare. Both belong to the run, not
                      the graph, and neither enters a content address — a
                      credential in particular is a RESOURCE and never a param.
                      Keyword-only with no default: the node declares, so the
                      executor always supplies, and a silent call without it
                      would be a bug worth crashing on.
      returns       — ``{"seeds": [PaperRecord], "seed_failures":
                      [SeedResolutionFailure]}`` — the declared output ports
                      (``RESOLVE_SEEDS_OUTPUT_PORTS``), in the order the
                      pre-binding 2-tuple carried them.

    Resolution runs on every pipeline call, hit or miss — the cache short-circuits
    TRAVERSAL, never resolution — so this phase is deliberately independent of the
    cache. ``seed_failures`` is request-derived (a function of the requested seed
    set, which is not part of the content address); the composition layer
    re-supplies it onto the result, so this stage emits it on its own port rather
    than embedding it in the ``seeds`` payload.

    Input validation: an empty ``seeds`` param raises ``ValueError`` here, before
    any work (a pre-check, not a reliance on ``fetch_seeds``' own empty-input
    guard — see Halt conditions in the spec). ``fetch_seeds``' own ``ValueError``
    on total resolution failure propagates; the empty-resolved-without-raising
    Node 0 contract violation raises ``PipelineError``.
    """
    config = _ResolveSeedsParams.model_validate(params)
    seeds = config.seeds
    client = resources["http_client"]
    api_key = resources["openalex_api_key"]

    if not seeds:
        raise ValueError("seeds must be non-empty")

    _log.info("Pipeline: starting run with %d seeds", len(seeds))

    # Resolve seeds (single batch call). fetch_seeds raises ValueError on empty
    # input OR when every seed fails — that is the "no roots" halt; let it
    # propagate.
    resolved, raw_failures = await fetch_seeds(seeds, client, api_key)
    if not resolved:
        # Defensive guard: a Node 0 contract violation, not normal total
        # failure (that path raises ValueError above).
        _log.error("Pipeline: fetch_seeds returned no resolved seeds without raising")
        raise PipelineError("no seeds resolved")
    seed_failures = [
        SeedResolutionFailure(seed=f["seed"], reason=f["reason"])
        for f in raw_failures
    ]
    if seed_failures:
        _log.warning(
            "Pipeline: Node 0 failed to resolve %d seed(s)", len(seed_failures)
        )
    return {"seeds": resolved, "seed_failures": seed_failures}


class PipelineHaltError(RuntimeError):
    """A node of the declared graph FAILED, so the run has no result to return.

    ``run_traversal`` drives the pipeline through ``execute_graph``, which
    SWALLOWS a raising handler and reports it as ``{"status": "FAILED", ...}``
    rather than letting it out. That swallow-and-report policy belongs to the
    executor and is deliberately kept there (IDG-063 clauses 2-3): a graph
    runner reports on the graph. It is NOT this function's contract —
    ``run_traversal`` promises a complete ``PipelineResult`` or nothing at all,
    and every caller above it is written against that promise. This exception is
    the adapter turning the executor's reported failure back into the halt the
    caller was always given.

    THE PREDICATE IS EXACTLY "ANY FAILED", with no per-node exemption. Scanning
    for FAILED alone is sufficient because a cascade-SKIPPED result always traces
    to a FAILED root, which is itself in the same results dict. And it is
    necessary that SKIPPED not be matched: a node the graph gates off with
    ``enabled_when`` is also SKIPPED, and that is legitimate declared state — an
    LLM-free run halting because Node 5.5 is configured off would be absurd.

    Carries the whole failure: ``node_id`` (the FIRST node to fail in topological
    order, which is the root rather than a downstream casualty), ``error`` (the
    string the executor reported) and ``results`` (the entire results dict, so a
    caller can see exactly how far the run got and which nodes cascaded). The
    ORIGINAL exception is the ``__cause__`` — this is raised ``from`` the
    exception OBJECT the executor carried on the FAILED result, so the type and
    the traceback that ``str(e)`` throws away both survive the round trip.

    Deliberately a ``RuntimeError`` subclass, matching the core executor's own
    error family (``PortBindingError``, ``UnregisteredNodeTypeError``,
    ``UnsuppliedResourceError``): a halt is a failure of the run, not of the
    caller's arguments. And deliberately defined HERE rather than in ``core/``:
    swallow-and-report is the executor's policy, and the decision to halt on it
    is this domain's (IDG-075 clause 2). Pre-handler defects — cycles,
    unregistered node types, unsupplied resources — already raise out of
    ``execute_graph`` on their own and are NOT re-wrapped in this.
    """

    def __init__(
        self,
        message: str,
        *,
        node_id: str,
        error: str,
        results: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.node_id = node_id
        self.error = error
        self.results = results


def _halt_if_any_node_failed(results: dict) -> None:
    """Raise ``PipelineHaltError`` on the FIRST failed node, or return.

    ``results`` is populated in topological order, so the first FAILED entry is
    the EARLIEST failure — the root cause, not one of the nodes that cascaded
    behind it. Reporting the root is the whole reason to scan in order rather
    than collect and pick arbitrarily.

    The re-raise is ``from`` the exception OBJECT the executor recorded, which is
    what makes the original type and traceback survive: a caller catching this
    still reaches the ``RuntimeError`` (or whatever the handler actually raised)
    through ``__cause__``, where ``str(e)`` alone would have flattened it to a
    message. A FAILED result with no ``exception`` — a port binding that failed
    once the upstream payload existed — carries None, and the halt simply has no
    cause.
    """
    for node_id, node_result in results.items():
        if node_result.get("status") != "FAILED":
            continue
        error = node_result.get("error", "")
        raise PipelineHaltError(
            f"pipeline halted: node '{node_id}' failed — {error}",
            node_id=node_id,
            error=error,
            results=results,
        ) from node_result.get("exception")


def _declared_producer_output(
    graph: Graph,
    results: dict,
    target: str,
    to_port: str,
) -> object:
    """The value the GRAPH declares as feeding ``target``'s ``to_port``.

    Reads the wiring rather than restating it: find the edge the graph declares
    into that input port, then read the producer's output at the ``from_port``
    that edge names. The alternative — naming the producer here — would put a
    second copy of the topology in this module, free to disagree with
    ``_build_edges`` silently while both still validate green.

    Used for the ``cycle_clean`` witness, which is the one piece of the result
    that is NOT carried on a port (``input_node_ids`` is ``exclude=True``
    structural metadata) and so must be rebuilt from the node set that fed
    ``CleanCycles``. Getting it from any free variable is the live defect: Node
    5.5 rebinds the node set downstream, so a witness built from "the nodes" and
    a witness built from "the nodes CleanCycles was given" differ exactly when
    5.5 changes the node set — and only the second one is right.

    ``validate_integrity`` ran before execution and requires exactly one edge per
    declared input port, so the loop finds one; the raise is the unreachable
    guard that says so rather than returning None into the result.
    """
    for edge in graph.edges:
        if edge.target == target and edge.to_port == to_port:
            return results[edge.source][edge.from_port]
    raise PipelineError(
        f"the declared graph feeds no edge into '{target}'.'{to_port}', so the "
        f"value cannot be read off its producer. validate_integrity should have "
        f"caught this before execution."
    )


async def run_traversal(
    resolved: list[PaperRecord],
    parameters: PipelineParameters,
    *,
    seed_requests: list[dict],
    client: httpx.AsyncClient,
    api_key: str,
    anthropic_client: AsyncAnthropic | None = None,
) -> PipelineResult:
    """Traversal + whole-graph assembly over an already-resolved seed set — the
    pure compute core of the pipeline, extracted so the read-through cache can
    short-circuit exactly this and nothing before it.

    AN ADAPTER, NOT AN ORCHESTRATOR (IDG-075 clause 4e). This function no longer
    calls the eleven stages by hand. It builds the declared ``Graph`` from
    ``build_pipeline_graph`` and hands it to ``execute_graph``; the dataflow that
    used to be locals threaded from one ``await`` to the next is now the graph's
    edges, executed. What is left here is exactly what is NOT dataflow: the
    argument guard, the integrity gate, the halt decision, and reassembling a
    ``PipelineResult`` from the results dict. ``pipeline_graph`` was a second
    description of this pipeline that nothing ran; it is now the only one.

    Takes the resolved seeds (never as the graph's dataflow head): it performs no
    Node 0 resolution and every OpenAlex call it issues is a traversal call, so a
    cache hit that skips this function issues none. Resolution already happened
    above, so the ResolveSeeds node's output is INJECTED rather than recomputed —
    the node declares ``input_ports == []``, which is what makes it injectable,
    and the executor records it SUCCESS without dispatching the handler.

    ``seed_requests`` is the request identifier dicts (``{"arxiv_id": ...}`` /
    ``{"doi": ...}``) that produced ``resolved``, and it is REQUIRED with no
    default (IDG-088; IDG-089 rider 3). It is the seed set Node 0 carries as
    CONFIGURATION on its params, so it enters the declared graph. A default would
    let a caller build a graph whose RESOLVE params silently disagreed with the
    request set that produced the injected records — a graph describing one run
    while executing another.

    Returns a complete ``PipelineResult`` EXCEPT the request-derived
    ``seed_failures``, which it leaves empty for the composition layer
    (``run_arxiv_pipeline`` or the cache) to re-supply from the current resolve
    output — so a cache hit provably equals a fresh miss on that field. The
    injected ResolveSeeds output carries an empty ``seed_failures`` for the same
    reason: the port exists, nothing in the graph consumes it, and the value is
    not this function's to state.

    Halts (raises, no partial result) when any node FAILS — see
    ``PipelineHaltError``, which also explains why the predicate is FAILED alone
    and never SKIPPED. Otherwise proceeds: partial Node 3/4 failures ride
    provenance ports rather than failing their node, and empty backward/forward
    results still produce a valid (possibly seeds-only) graph.
    """
    # THE GUARD, FIRST — before the graph is built, before it is validated,
    # before anything executes. Node 5.5 declares `anthropic_client` as a
    # resource, so an LLM run that supplied none would otherwise be caught by the
    # executor's `_check_resource_supply` and surface as an
    # `UnsuppliedResourceError` — a graph-level defect, which is not what
    # happened. What happened is that the CALLER did not supply a client it was
    # contracted to supply, and callers are written against this ValueError.
    # Letting the executor report it instead would be a silent contract change,
    # and letting it become a FAILED node would degrade it into a halt.
    if parameters.llm is not None and anthropic_client is None:
        raise ValueError(
            "run_traversal: parameters.llm is set but anthropic_client is "
            "None — Node 5.5 requires an injected AsyncAnthropic client "
            "(IDG-024 keyword-only injection)."
        )

    # FUNCTION-LOCAL, both of them, and for one reason: this module is what they
    # import. `pipeline_graph` imports the port constants declared here (it must
    # never transcribe them), and `register_arxiv_handlers` imports the ten stage
    # functions defined here. Either import at module level would close the cycle
    # at load time. `handlers.py` already uses this idiom for the same reason.
    from idiograph.domains.arxiv.handlers import register_arxiv_handlers
    from idiograph.domains.arxiv.pipeline_graph import (
        ASSEMBLE,
        BACKWARD,
        CLEAN,
        CO_CITATIONS,
        COMMUNITIES,
        DEPTH,
        ENRICH,
        FORWARD,
        PAGERANK,
        RESOLVE,
        build_pipeline_graph,
    )

    # Registration is INVOKED, never reimplemented: the domain has exactly one
    # boot site naming the eleven node types, and a second mapping here could
    # bind a different handler under the same type. Called per run rather than
    # once, so this function is self-sufficient instead of depending on some
    # earlier caller having booted the domain.
    #
    # WHAT THIS MEANS FOR STAND-INS, because it is not obvious and the test suite
    # rests on it: `register_arxiv_handlers` imports the stage functions FROM
    # THIS MODULE at call time, so it re-reads `pipeline.<stage>` on every run.
    # A harness that rebinds the module attribute therefore has its stand-in
    # picked up here automatically. The one stage that does NOT work that way is
    # Node 5.5 — the registrar reads it from `relationship_annotation`, not from
    # here — so a harness standing in for it must rebind the attribute on THAT
    # module, which is the one place this re-registration reads it from.
    register_arxiv_handlers()
    graph = build_pipeline_graph(seed_requests, parameters)

    # BEFORE execution, deliberately. A dataflow defect — an input port fed by no
    # edge, or by two — is knowable from the graph alone and without running
    # anything, and discovering it halfway through means having already spent the
    # OpenAlex traversal calls that are the expensive part of this pipeline.
    # `validate_integrity` reads only the graph and knows nothing about the run,
    # so it is a pure gate: it passes, or the run never starts.
    integrity = validate_integrity(graph)
    if not integrity["valid"]:
        raise PipelineError(
            "the declared citation-traversal graph does not validate, so it was "
            "not executed: " + "; ".join(integrity["errors"])
        )

    _log.info("Pipeline: executing the declared graph (%d nodes)", len(graph.nodes))
    results = await execute_graph(
        graph,
        resources={
            "http_client": client,
            "openalex_api_key": api_key,
            # Supplied unconditionally, including as None. On an LLM run the
            # guard above has already established it is not None; on an LLM-free
            # run Node 5.5 is disabled by its config predicate, so
            # `_check_resource_supply` skips it and nothing is ever dispatched
            # that could draw against it.
            "anthropic_client": anthropic_client,
        },
        # Node 0 already ran, ABOVE this function — that separation is what lets
        # the read-through cache short-circuit traversal alone. Injecting its
        # output is what keeps that true here: `resolved` is threaded in as the
        # RESOLVE node's result rather than the handler being dispatched to fetch
        # the same seeds a second time. The node declares `input_ports == []`,
        # which is what makes it injectable at all. `seed_failures` rides the
        # port EMPTY because it is request-derived and the composition layer
        # re-supplies it — the same reason this function returns it empty.
        outputs={RESOLVE: {"seeds": resolved, "seed_failures": []}},
    )
    _halt_if_any_node_failed(results)

    # Everything below reads the results dict AT DECLARED OUTPUT PORTS. Failure
    # provenance especially: `failed_batches` is read off the port that carries
    # it, never dug out of the `backward` payload that also happens to hold the
    # same list. BACKWARD_TRAVERSE_OUTPUT_PORTS calls that port canonical, and
    # reading it here is what makes the claim true of production rather than only
    # of the handler.
    cleaned_edges = results[CLEAN]["cleaned_edges"]
    co_citation_edges = results[CO_CITATIONS]["co_citation_edges"]

    # The merged edge view, which stays HERE rather than becoming a stage:
    # suppressed originals are deliberately NOT in `edges` (they live in
    # cycle_log.suppressed_edges for audit), and no node declares this
    # concatenation because it is result assembly, not dataflow.
    merged_edges = cleaned_edges + co_citation_edges

    cycle_clean = CycleCleanResult(
        cleaned_edges=cleaned_edges,
        cycle_log=results[CLEAN]["cycle_log"],
        # The witness is not a port (it is exclude=True structural metadata), so
        # it is rebuilt — from the node set the GRAPH DECLARES as feeding
        # CleanCycles' `nodes` port, read off that producer's own output port.
        # Not from a local: Node 5.5 rebinds the node set downstream, so "the
        # nodes" and "the nodes CleanCycles was given" are different lists on any
        # run where 5.5 changes the set, and only the second is the witness.
        # Re-running the validator over the same edges and the same node set it
        # already passed inside the handler is a no-op by construction.
        input_node_ids=frozenset(
            n.node_id
            for n in _declared_producer_output(graph, results, CLEAN, "nodes")
        ),
    )

    # `seed_failures` is request-derived (a function of the requested seed set,
    # not the resolved set that keys the cache); the composition layer re-supplies
    # it from the current resolve output, so it is left empty here.
    result = PipelineResult(
        nodes=results[ENRICH]["enriched_nodes"],
        edges=merged_edges,
        seeds=[s.node_id for s in results[RESOLVE]["seeds"]],
        cycle_clean=cycle_clean,
        co_citation_edges=co_citation_edges,
        co_citation_warnings=results[CO_CITATIONS]["co_citation_warnings"],
        depth_metrics=results[DEPTH]["depth_metrics"],
        pagerank=results[PAGERANK]["pagerank"],
        communities=results[COMMUNITIES]["communities"],
        parameters=parameters,
        seed_failures=[],
        backward_failed_batches=results[BACKWARD]["failed_batches"],
        forward_failed_seeds=results[FORWARD]["failed_seeds"],
        truncated_seeds=results[FORWARD]["truncated_seeds"],
        data_integrity_warnings=results[ASSEMBLE]["mismatches"],
    )
    _log.info(
        "Pipeline: traversal complete — %d nodes, %d edges",
        len(result.nodes),
        len(result.edges),
    )
    return result


async def run_arxiv_pipeline(
    seeds: list[dict],
    parameters: PipelineParameters,
    *,
    client: httpx.AsyncClient,
    api_key: str,
    anthropic_client: AsyncAnthropic | None = None,
) -> PipelineResult:
    """Compose the per-stage pipeline into one end-to-end run (UNCACHED).

    ``seeds`` is a list of seed identifier dicts (``{"arxiv_id": ...}`` /
    ``{"doi": ...}``) — the exact shape Node 0's ``fetch_seeds`` accepts; shape
    classification is Node 0's job. ``client`` and ``api_key`` are owned at the
    true top of the call graph and threaded to every async stage (IDG-022); the
    orchestrator constructs neither.

    Body is the composition of the two extracted halves: :func:`resolve_seeds`
    (the Node 0 phase, which also raises the empty-input, total-failure, and
    contract-violation halts) then :func:`run_traversal` (the traversal + assembly
    core). ``resolve_seeds`` is a port-declared executor handler, so this direct
    call shapes its ``params`` / ``inputs`` / ``resources`` exactly as the executor
    would and reads its results off the declared output ports
    (``RESOLVE_SEEDS_OUTPUT_PORTS``) — one contract, whichever path invoked it.
    The single request-derived field, ``seed_failures``, is re-supplied from the
    resolve output onto the traversal result — the same re-supply the
    read-through cache applies on a hit, so this orchestrator and a cache hit
    produce byte-identical results. This function is deliberately cache-unaware;
    the caching decision layer lives above it in ``cache.py``.

    Halts (raises, no partial result) when ``seeds`` is empty, when every seed
    fails Node 0 resolution, or when any whole-graph stage (Node 4.5/5/6/7)
    raises. Otherwise records provenance on the result and proceeds — partial
    Node 0/3/4 failures and empty backward/forward results still produce a valid
    (possibly seeds-only) graph.
    """
    node0 = await resolve_seeds(
        {"seeds": seeds},
        {},
        resources={"http_client": client, "openalex_api_key": api_key},
    )
    resolved = node0["seeds"]
    seed_failures = node0["seed_failures"]
    result = await run_traversal(
        resolved,
        parameters,
        seed_requests=seeds,
        client=client,
        api_key=api_key,
        anthropic_client=anthropic_client,
    )
    result = result.model_copy(update={"seed_failures": seed_failures})
    _log.info(
        "Pipeline: complete — %d nodes, %d edges, %d failure records",
        len(result.nodes),
        len(result.edges),
        len(result.seed_failures)
        + len(result.backward_failed_batches)
        + len(result.forward_failed_seeds),
    )
    return result


ARXIV_PIPELINE: Graph = Graph(
    name="arxiv_abstract_pipeline",
    version="1.0",
    nodes=[
        Node(
            id="fetch",
            type="FetchAbstract",
            params={"paper_id": ""},  # patched at runtime via CLI
            resources=["http_client"],
        ),
        Node(
            id="claims",
            type="LLMCall",
            params={
                "system": "You are a precise scientific analyst.",
                # Output-determining, so declared on the node rather than
                # hardcoded in the handler.
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "prompt_template": (
                    "List the key concrete claims from this abstract as bullet points.\n\n"
                    "Title: {title}\n\nAbstract: {abstract}"
                ),
            },
            resources=["anthropic_client"],
        ),
        Node(
            id="evaluate",
            type="Evaluator",
            params={
                "keywords": ["method", "model", "result", "performance", "dataset"],
                "threshold": 0.4,
            },
        ),
        Node(
            id="summarize",
            type="LLMSummarize",
            params={
                "system": "You are a technical research communicator.",
                # Output-determining, so declared on the node rather than
                # hardcoded in the handler.
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "prompt_template": (
                    "Write a 2-sentence technical summary of this paper for an AI engineer.\n\n"
                    "Title: {title}\n\nAbstract: {abstract}"
                ),
            },
            resources=["anthropic_client"],
        ),
    ],
    edges=[
        Edge(source="fetch", target="claims", type="DATA"),
        Edge(source="claims", target="evaluate", type="DATA"),
        Edge(source="evaluate", target="summarize", type="CONTROL"),
    ],
)
