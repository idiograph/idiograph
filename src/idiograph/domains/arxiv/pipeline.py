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
from dotenv import load_dotenv

from idiograph.core.logging_config import get_logger
from idiograph.core.models import Graph, Node, Edge
from idiograph.domains.arxiv.models import PaperRecord, make_node_id

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
        Edge(source="fetch",    target="claims",   type="DATA"),
        Edge(source="claims",   target="evaluate", type="DATA"),
        Edge(source="evaluate", target="summarize", type="CONTROL"),
    ],
)
