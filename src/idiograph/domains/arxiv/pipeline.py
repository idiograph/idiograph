# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import math
import os
from datetime import date

import httpx
import networkx as nx
from dotenv import load_dotenv

from idiograph.core.logging_config import get_logger
from idiograph.core.models import Graph, Node, Edge
from idiograph.domains.arxiv.models import (
    CitationEdge,
    CycleCleanResult,
    CycleLog,
    PaperRecord,
    SuppressedEdge,
    make_node_id,
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
        return f"ids.doi:{seed['doi']}"
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
) -> list[dict]:
    """Batch-fetch OpenAlex works by ID (50 per call). Silently skips misses."""
    if not openalex_ids:
        return []
    works: list[dict] = []
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
            _log.debug("Batch fetch failed for %s: %s", batch, e)
            continue
        results = (response.json() or {}).get("results") or []
        works.extend(results)
    return works


async def backward_traverse(
    seeds: list[PaperRecord],
    client: httpx.AsyncClient,
    api_key: str,
    n_backward: int,
    lambda_decay: float,
    sleep_ms: int = 150,
) -> list[PaperRecord]:
    """Backward traversal from seed nodes up to depth 2.

    For each seed, fetches its direct references (depth=1) and the references
    of those references (depth=2). Deduplicates by ``node_id`` — when a paper
    appears via multiple paths, the lowest ``hop_depth`` wins and ``root_ids``
    is the union of every root reachable through any path. Seeds themselves
    are excluded from the output. The merged records are then scored by
    :func:`_node3_score`, sorted descending, and truncated to ``n_backward``.
    """
    seed_ids = {s.node_id for s in seeds}

    # Seeds must first be re-fetched to obtain ``referenced_works`` since
    # Node 0 doesn't store it. In the common case the caller is the pipeline
    # orchestrator and has the seed OpenAlex IDs already — we fetch via the
    # OpenAlex-ID batch endpoint.
    seed_oa_ids = [s.openalex_id for s in seeds]
    seed_works_by_oa: dict[str, dict] = {
        _strip_openalex_id(w["id"]): w
        for w in await _fetch_works_by_ids(seed_oa_ids, client, api_key, sleep_ms)
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
    depth1_works = await _fetch_works_by_ids(
        sorted(all_depth1_ids), client, api_key, sleep_ms
    )
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

    depth2_works = await _fetch_works_by_ids(
        sorted(all_depth2_ids), client, api_key, sleep_ms
    )
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

    current_year = date.today().year
    scored = sorted(
        merged.values(),
        key=lambda r: _node3_score(r, lambda_decay, current_year),
        reverse=True,
    )
    return scored[:n_backward]


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
    acceleration_method: str = "first_difference",
    current_year: int | None = None,
) -> list[PaperRecord]:
    """Forward traversal: fetch papers citing each seed, rank by α/β score.

    For each seed, issues an OpenAlex ``cites:<openalex_id>`` query and maps
    each returned work to a ``PaperRecord`` with ``hop_depth=1``. Papers
    cited by multiple seeds are deduplicated by ``node_id`` with ``root_ids``
    merged as a sorted union (AMD-017). Seeds themselves are excluded. The
    merged set is scored by :func:`_node4_score`, sorted descending, and
    truncated to ``n_forward``.

    ``counts_by_year`` is fetched here only — it is not available from Node 0
    or Node 3's ``select=`` fields.
    """
    if current_year is None:
        current_year = date.today().year

    seed_ids = {s.node_id for s in seeds}
    merged: dict[str, PaperRecord] = {}
    counts_by_id: dict[str, list[dict]] = {}

    sleep_s = 0.150
    async with httpx.AsyncClient() as client:
        for idx, seed in enumerate(seeds):
            if idx > 0:
                await asyncio.sleep(sleep_s)

            params = {
                "filter": f"cites:{seed.openalex_id}",
                "select": _FORWARD_SELECT,
                "per-page": "200",
                "api_key": api_key,
            }
            try:
                response = await client.get(OPENALEX_BASE, params=params)
                response.raise_for_status()
            except httpx.HTTPError as e:
                _log.debug("cites query failed for %s: %s", seed.node_id, e)
                continue

            results = (response.json() or {}).get("results") or []
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

    scored = sorted(merged.values(), key=_score, reverse=True)
    return scored[:n_forward]


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
) -> list[CitationEdge]:
    """Compute co-citation edges across the assembled citation graph.

    Two papers A and B are co-cited whenever any third paper C cites both;
    the number of shared citers is the edge ``strength``. See
    docs/specs/spec-node5-co-citation.md for the full contract, including
    the global cross-root semantics (AMD-017), canonical form, and sort
    ordering.

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
    warned_missing: set[str] = set()

    for e in cites_edges:
        if e.source_id == e.target_id:
            continue
        if e.source_id not in node_ids:
            if e.source_id not in warned_missing:
                warned_missing.add(e.source_id)
                _log.warning(
                    "Node 5: citation edge references unknown node_id %s; skipping",
                    e.source_id,
                )
            continue
        if e.target_id not in node_ids:
            if e.target_id not in warned_missing:
                warned_missing.add(e.target_id)
                _log.warning(
                    "Node 5: citation edge references unknown node_id %s; skipping",
                    e.target_id,
                )
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

    return co_edges


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
