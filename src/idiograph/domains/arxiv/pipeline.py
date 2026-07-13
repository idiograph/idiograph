# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import math
import os
from datetime import date
from typing import Literal

import httpx
import networkx as nx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from idiograph.core.logging_config import get_logger
from idiograph.core.models import Graph, Node, Edge
from idiograph.domains.arxiv.models import (
    CitationEdge,
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
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    SeedResolutionFailure,
    SuppressedEdge,
    TruncatedSeed,
    make_node_id,
)
from idiograph.domains.arxiv.relationship_annotation import annotate_relationships

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
        raise EnvironmentError(
            "OPENALEX_API_KEY not set. Add it to .env or set it in the environment."
        )
    return key


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
    if "arxiv_id" in seed and seed["arxiv_id"]:
        return f"ids.arxiv:https://arxiv.org/abs/{seed['arxiv_id']}"
    if "doi" in seed and seed["doi"]:
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


async def backward_traverse(
    seeds: list[PaperRecord],
    api_key: str,
    n_backward: int,
    lambda_decay: float,
    sleep_ms: int = 150,
    *,
    client: httpx.AsyncClient,
) -> Node3Result:
    """Backward traversal from seed nodes up to depth 2.

    For each seed, fetches its direct references (depth=1) and the references
    of those references (depth=2). Deduplicates by ``node_id`` — when a paper
    appears via multiple paths, the lowest ``hop_depth`` wins and ``root_ids``
    is the union of every root reachable through any path. Seeds themselves
    are excluded from the output papers. The merged records are then scored
    by :func:`_node3_score`, sorted descending, and truncated to
    ``n_backward``.

    Returns a :class:`Node3Result` carrying the ranked papers, the citation
    edges discovered during traversal (seed→depth-1 and depth-1→depth-2),
    and any batch-level fetch failures recorded by ``_fetch_works_by_ids``.
    See AMD-020.
    """
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

    current_year = date.today().year
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

    return Node3Result(
        papers=papers,
        edges=filtered_edges,
        failed_batches=failed_batches,
    )


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


async def forward_traverse(
    seeds: list[PaperRecord],
    api_key: str,
    n_forward: int,
    alpha: float,
    beta: float,
    lambda_decay: float,
    *,
    client: httpx.AsyncClient,
    sort: ForwardSort,
    acceleration_method: str = "first_difference",
    current_year: int | None = None,
) -> Node4Result:
    """Forward traversal: fetch papers citing each seed, rank by α/β score.

    For each seed, issues an OpenAlex ``cites:<openalex_id>`` query and maps
    each returned work to a ``PaperRecord`` with ``hop_depth=1``. Papers
    cited by multiple seeds are deduplicated by ``node_id`` with ``root_ids``
    merged as a sorted union (AMD-017). Seeds themselves are excluded. The
    merged set is scored by :func:`_node4_score`, sorted descending, and
    truncated to ``n_forward``.

    ``sort`` is required (no default): OpenAlex's default sort order is not
    contractual and produces nondeterministic "first 200" sets across runs.
    See AMD-020.

    Returns a :class:`Node4Result` carrying the ranked papers, the citer→seed
    citation edges, per-seed call failures, and per-seed truncation events
    when OpenAlex reports a ``meta.count`` exceeding the returned-results
    length (currently capped at 200).

    ``counts_by_year`` is fetched here only — it is not available from Node 0
    or Node 3's ``select=`` fields.
    """
    if current_year is None:
        current_year = date.today().year

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

    return Node4Result(
        papers=papers,
        edges=filtered_edges,
        failed_seeds=failed_seeds,
        truncated_seeds=truncated_seeds,
    )


# ── Node 4.5 — Cycle Cleaning ───────────────────────────────────────────────


def clean_cycles(
    nodes: list[PaperRecord],
    edges: list[CitationEdge],
) -> CycleCleanResult:
    """Detect and resolve cycles in the citation graph via weakest-link suppression.

    Pure function — no I/O, no network, no mutation of inputs. See
    docs/specs/spec-node4.5-cycle-cleaning.md for the full contract, including
    the ordering of the weakest-link tiebreaker and the handling of missing-node
    citation lookups.
    """
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

    return CycleCleanResult(
        cleaned_edges=cleaned_edges,
        cycle_log=CycleLog(
            suppressed_edges=suppressed,
            cycles_detected_count=cycles_detected_count,
            iterations=iterations,
        ),
        input_node_ids=frozenset(n.node_id for n in nodes),
    )


# ── Node 5 — Co-Citation ────────────────────────────────────────────────────


def compute_co_citations(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],
    min_strength: int = 2,
    max_edges: int | None = None,
) -> Node5Result:
    """Compute co-citation edges across the assembled citation graph.

    Two papers A and B are co-cited whenever any third paper C cites both;
    the number of shared citers is the edge ``strength``. See
    docs/specs/spec-node5-co-citation.md for the full contract, including
    the global cross-root semantics (AMD-017), canonical form, and sort
    ordering.

    Returns a :class:`Node5Result` carrying the co-citation edges and any
    data-quality warnings. Both endpoints of every edge are checked for
    unknown-ness unconditionally (Option A, IDG-023), so ``warnings`` lists
    every distinct unknown ``node_id`` in first-encounter order.

    Raises ``ValueError`` on invalid ``min_strength`` (< 1) or ``max_edges``
    (< 0). Pure function — no I/O, no mutation of inputs.
    """
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

    return Node5Result(edges=co_edges, warnings=warnings)


# ── Node 6 — Metric Computation ─────────────────────────────────────────────


def compute_depth_metrics(
    nodes: list[PaperRecord],
    cleaned_edges: list[CitationEdge],
) -> dict[str, DepthMetrics]:
    """Compute per-node depth metrics on the cleaned citation graph.

    For every input node, returns a ``DepthMetrics`` carrying
    ``hop_depth_per_root`` (BFS distance from each reaching root over the
    undirected view of the graph) and ``traversal_direction`` (categorical
    position relative to the seed set: seed/backward/forward/mixed). See
    docs/specs/spec-node6-metrics.md and AMD-019 for the full contract.

    Raises ``ValueError`` if no roots are present in ``nodes`` or if any
    node is unreachable from every root. Pure function — no I/O, no
    mutation of inputs.
    """
    if not nodes:
        return {}

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

    return result


def compute_pagerank(
    nodes: list[PaperRecord],
    cleaned_edges: list[CitationEdge],
    damping: float = 0.85,
) -> dict[str, float]:
    """Compute PageRank for every node in the cleaned citation graph.

    Returns ``{node_id: pagerank}``. Every input node receives a value,
    including isolates. Output values sum to 1.0 within NetworkX
    convergence tolerance. ``damping`` is passed through to
    ``nx.pagerank`` as ``alpha``; out-of-range values raise via NetworkX.
    Pure function — no I/O, no mutation of inputs.
    """
    if not nodes:
        return {}

    _log.info(
        "Node 6 pagerank: %d nodes, %d edges, alpha=%s",
        len(nodes),
        len(cleaned_edges),
        damping,
    )

    G: nx.DiGraph = nx.DiGraph()
    G.add_nodes_from(n.node_id for n in nodes)
    G.add_edges_from((e.source_id, e.target_id) for e in cleaned_edges)

    pr = nx.pagerank(G, alpha=damping)

    _log.info("Node 6 pagerank complete")

    return dict(pr)


# ── Node 7 — Community Detection ────────────────────────────────────────────


def detect_communities(
    nodes: list[PaperRecord],
    cites_edges: list[CitationEdge],
    infomap_seed: int = 42,
    infomap_trials: int = 10,
    infomap_teleportation: float = 0.15,
    leiden_seed: int = 42,
    community_count_min: int = 5,
    community_count_max: int = 40,
) -> CommunityResult:
    """Assign a community label to every node in the assembled citation graph.

    Runs Infomap (primary) over the directed citation graph, falling back to
    Leiden when ``infomap`` is not installed. Both algorithms produce a flat
    partition keyed by ``node_id``; isolates receive an assignment. The
    function does not modify graph structure or filter nodes.

    See docs/specs/spec-node7-community-detection.md for the full contract,
    including fallback policy, edge-input semantics (cleaned ∪ suppressed),
    and LOD validation thresholds.

    Raises ``RuntimeError`` if neither ``infomap`` nor ``leidenalg`` is
    installed. Pure function — no I/O, no mutation of inputs.
    """
    if not nodes:
        _log.debug("Node 7: empty input — no communities to detect")
        return CommunityResult(
            community_assignments={},
            algorithm_used="infomap",
            community_count=0,
            validation_flags=[],
            warnings=[],  # no edge validation runs on empty input
        )

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
            infomap_seed,
            infomap_trials,
            infomap_teleportation,
        )
    except ImportError:
        try:
            import igraph  # noqa: F401
            import leidenalg  # noqa: F401
            partial = _run_leiden(nodes, valid_edges, leiden_seed)
        except ImportError:
            raise RuntimeError(
                "Neither infomap nor leidenalg is installed. "
                "Install community detection dependencies: "
                "uv sync --extra community"
            ) from None

    flags: list[str] = []
    if partial.community_count < community_count_min:
        flags.append("community_count_below_minimum")
    if partial.community_count > community_count_max:
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

    return result


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


# ── Pipeline Orchestrator ───────────────────────────────────────────────────


class PipelineError(Exception):
    """Defensive-guard error for ``run_arxiv_pipeline``.

    Raised only for the should-not-happen case where ``fetch_seeds`` returns an
    empty ``resolved`` list *without* raising — a Node 0 contract violation.
    Normal total failure (every seed fails resolution) is ``fetch_seeds``' own
    ``ValueError``, which the orchestrator lets propagate unwrapped; it is not
    wrapped in ``PipelineError``.
    """


def assemble_graph(
    seeds: list[PaperRecord],
    backward: Node3Result,
    forward: Node4Result,
) -> tuple[list[PaperRecord], list[CitationEdge], list[EdgeMetadataMismatch]]:
    """Reconcile seeds, Node 3, and Node 4 into one node set and one cites edge set.

    Pure function. Does **not** do cross-seed dedup — Nodes 3 and 4 already did
    that internally (the global top-N cap is applied across the cross-seed union
    inside each node). Its only job is the backward ∪ forward ∪ seed union, where
    a node or edge can legitimately appear in more than one of the three sources.

    Bucket-then-reduce: one ``model_copy`` per unique node, hash-based dedup, no
    O(N²) existence checks. Mirrors ``clean_cycles``' ``edge_by_pair`` lookup.

    Returns ``(unified_nodes, unified_cites, mismatches)``. ``mismatches`` records
    each ``(source_id, target_id, type)`` edge whose backward and forward views
    disagree on metadata; the first-seen (backward) edge is kept (OQ3).
    """
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
    return unified_nodes, unified_cites, mismatches


async def resolve_seeds(
    seeds: list[dict],
    *,
    client: httpx.AsyncClient,
    api_key: str,
) -> tuple[list[PaperRecord], list[SeedResolutionFailure]]:
    """Node 0 resolution phase, extracted so the uncached orchestrator and the
    read-through cache share ONE resolution per invocation.

    ``seeds`` is a list of seed identifier dicts (``{"arxiv_id": ...}`` /
    ``{"doi": ...}``) — the exact shape ``fetch_seeds`` accepts. Returns
    ``(resolved, seed_failures)``: the resolved ``PaperRecord`` list that feeds
    BOTH the content-address key and traversal, and the typed per-seed
    resolution failures.

    Resolution runs on every pipeline call, hit or miss — the cache short-circuits
    TRAVERSAL, never resolution — so this phase is deliberately independent of the
    cache. ``seed_failures`` is request-derived (a function of the requested seed
    set, which is not part of the content address); the composition layer
    re-supplies it onto the result, so this helper returns it separately rather
    than embedding it.

    Input validation: ``seeds == []`` raises ``ValueError`` here, before any work
    (a pre-check, not a reliance on ``fetch_seeds``' own empty-input guard — see
    Halt conditions in the spec). ``fetch_seeds``' own ``ValueError`` on total
    resolution failure propagates; the empty-resolved-without-raising Node 0
    contract violation raises ``PipelineError``.
    """
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
    return resolved, seed_failures


async def run_traversal(
    resolved: list[PaperRecord],
    parameters: PipelineParameters,
    *,
    client: httpx.AsyncClient,
    api_key: str,
    anthropic_client: AsyncAnthropic | None = None,
) -> PipelineResult:
    """Traversal + whole-graph assembly over an already-resolved seed set — the
    pure compute core of the pipeline, extracted so the read-through cache can
    short-circuit exactly this and nothing before it.

    Takes the resolved seeds (never the request dicts): it performs no Node 0
    resolution and every OpenAlex call it issues is a traversal call, so a cache
    hit that skips this function issues none. It delegates every domain operation
    to an existing per-stage function and is responsible only for composition:
    graph merge, dataflow, failure-provenance aggregation, end-of-pipeline
    enrichment, and result assembly.

    Returns a complete ``PipelineResult`` EXCEPT the request-derived
    ``seed_failures``, which it leaves empty for the composition layer
    (``run_arxiv_pipeline`` or the cache) to re-supply from the current resolve
    output — so a cache hit provably equals a fresh miss on that field.

    Halts (raises, no partial result) when any whole-graph stage (Node
    4.5/5/6/7) raises. Otherwise proceeds — partial Node 3/4 failures and empty
    backward/forward results still produce a valid (possibly seeds-only) graph.
    """
    # 2. Traversal (one batch call each, over the full resolved-seed list).
    #    Failures are read off the results, never caught per seed.
    _log.info("Pipeline: starting backward traversal")
    n3 = await backward_traverse(
        resolved,
        api_key,
        n_backward=parameters.backward.n_backward,
        lambda_decay=parameters.backward.lambda_decay,
        client=client,
    )
    _log.info("Pipeline: backward traversal complete")
    if n3.failed_batches:
        _log.warning(
            "Pipeline: Node 3 recorded %d failed batch(es)", len(n3.failed_batches)
        )

    _log.info("Pipeline: starting forward traversal")
    n4 = await forward_traverse(
        resolved,
        api_key,
        n_forward=parameters.forward.n_forward,
        alpha=parameters.forward.alpha,
        beta=parameters.forward.beta,
        lambda_decay=parameters.forward.lambda_decay,
        client=client,
        sort=parameters.forward.sort,
    )
    _log.info("Pipeline: forward traversal complete")
    if n4.failed_seeds or n4.truncated_seeds:
        _log.warning(
            "Pipeline: Node 4 recorded %d failed seed(s), %d truncated",
            len(n4.failed_seeds),
            len(n4.truncated_seeds),
        )

    # 3. Graph merge.
    _log.info("Pipeline: starting graph merge")
    unified_nodes, unified_cites, mismatches = assemble_graph(resolved, n3, n4)
    _log.info(
        "Pipeline: graph merge complete — %d nodes, %d cites edges, %d mismatch(es)",
        len(unified_nodes),
        len(unified_cites),
        len(mismatches),
    )

    # 4. Whole-graph stages (exceptions propagate; orchestrator does not catch).
    _log.info("Pipeline: starting cycle cleaning")
    cycle = clean_cycles(unified_nodes, unified_cites)
    # all_cites (cleaned + suppressed) -> co-citation + communities: co-occurrence
    # and clustering keep real-but-suppressed citations. depth + pagerank ->
    # cleaned_edges only (they need the acyclic graph). The split is deliberate.
    all_cites = cycle.cleaned_edges + [
        s.original for s in cycle.cycle_log.suppressed_edges
    ]
    _log.info("Pipeline: cycle cleaning complete")

    _log.info("Pipeline: starting co-citation")
    co = compute_co_citations(
        unified_nodes,
        all_cites,
        min_strength=parameters.co_citation.min_strength,
        max_edges=parameters.co_citation.max_edges,
    )
    _log.info("Pipeline: co-citation complete")

    # --- Node 5.5 insertion (spec-node5.5-semantic-relationship) ---
    # Build-time, miss-gated (this function runs only on a cache MISS): classify
    # each non-seed paper's relationship to the seed set. LLM-free runs
    # (parameters.llm is None) skip it entirely — a pure no-op, address unchanged.
    # relationship_type is a leaf; the downstream stages below do not read it, so
    # reassigning unified_nodes to the annotated copies is safe.
    if parameters.llm is not None:
        if anthropic_client is None:
            raise ValueError(
                "run_traversal: parameters.llm is set but anthropic_client is "
                "None — Node 5.5 requires an injected AsyncAnthropic client "
                "(IDG-024 keyword-only injection)."
            )
        ann = await annotate_relationships(
            unified_nodes,
            resolved,
            parameters.llm,
            anthropic_client=anthropic_client,
        )
        unified_nodes = ann.nodes
    else:
        _log.debug("Node 5.5: skipped (llm config None)")
    # --- end Node 5.5 ---

    _log.info("Pipeline: starting depth metrics")
    depth = compute_depth_metrics(unified_nodes, cycle.cleaned_edges)
    _log.info("Pipeline: depth metrics complete")

    _log.info("Pipeline: starting pagerank")
    prank = compute_pagerank(
        unified_nodes, cycle.cleaned_edges, damping=parameters.pagerank.damping
    )
    _log.info("Pipeline: pagerank complete")

    _log.info("Pipeline: starting community detection")
    communities = detect_communities(
        unified_nodes,
        all_cites,
        infomap_seed=parameters.communities.infomap_seed,
        infomap_trials=parameters.communities.infomap_trials,
        infomap_teleportation=parameters.communities.infomap_teleportation,
        leiden_seed=parameters.communities.leiden_seed,
        community_count_min=parameters.communities.community_count_min,
        community_count_max=parameters.communities.community_count_max,
    )
    _log.info("Pipeline: community detection complete")

    # 5. End-of-pipeline enrichment (immutable write path — Node 6 owns the
    #    canonical hop_depth_per_root / traversal_direction).
    enriched_nodes = [
        node.model_copy(
            update={
                "traversal_direction": depth[node.node_id].traversal_direction,
                "hop_depth_per_root": depth[node.node_id].hop_depth_per_root,
                "pagerank": prank[node.node_id],
                "community_id": communities.community_assignments[node.node_id],
            }
        )
        for node in unified_nodes
    ]

    # 6. Merged edge view. Suppressed originals are NOT in `edges` — they live in
    #    cycle.cycle_log.suppressed_edges for audit.
    merged_edges = cycle.cleaned_edges + co.edges

    # 7. Construct and return. ``seed_failures`` is request-derived (a function
    #    of the requested seed set, not the resolved set that keys the cache);
    #    the composition layer re-supplies it from the current resolve output,
    #    so it is left empty here (see run_arxiv_pipeline / cache re-supply).
    result = PipelineResult(
        nodes=enriched_nodes,
        edges=merged_edges,
        seeds=[s.node_id for s in resolved],
        cycle_clean=cycle,
        co_citation_edges=co.edges,
        co_citation_warnings=co.warnings,
        depth_metrics=depth,
        pagerank=prank,
        communities=communities,
        parameters=parameters,
        seed_failures=[],
        backward_failed_batches=n3.failed_batches,
        forward_failed_seeds=n4.failed_seeds,
        truncated_seeds=n4.truncated_seeds,
        data_integrity_warnings=mismatches,
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
    core). The single request-derived field, ``seed_failures``, is re-supplied
    from the resolve output onto the traversal result — the same re-supply the
    read-through cache applies on a hit, so this orchestrator and a cache hit
    produce byte-identical results. This function is deliberately cache-unaware;
    the caching decision layer lives above it in ``cache.py``.

    Halts (raises, no partial result) when ``seeds`` is empty, when every seed
    fails Node 0 resolution, or when any whole-graph stage (Node 4.5/5/6/7)
    raises. Otherwise records provenance on the result and proceeds — partial
    Node 0/3/4 failures and empty backward/forward results still produce a valid
    (possibly seeds-only) graph.
    """
    resolved, seed_failures = await resolve_seeds(
        seeds, client=client, api_key=api_key
    )
    result = await run_traversal(
        resolved,
        parameters,
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
        ),
        Node(
            id="claims",
            type="LLMCall",
            params={
                "system": "You are a precise scientific analyst.",
                "prompt_template": (
                    "List the key concrete claims from this abstract as bullet points.\n\n"
                    "Title: {title}\n\nAbstract: {abstract}"
                ),
            },
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
                "prompt_template": (
                    "Write a 2-sentence technical summary of this paper for an AI engineer.\n\n"
                    "Title: {title}\n\nAbstract: {abstract}"
                ),
            },
        ),
    ],
    edges=[
        Edge(source="fetch", target="claims", type="DATA"),
        Edge(source="claims", target="evaluate", type="DATA"),
        Edge(source="evaluate", target="summarize", type="CONTROL"),
    ],
)
