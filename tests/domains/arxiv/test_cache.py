# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Node 8 read-through cache: resolve -> key -> hit/skip -> store, and the
hit-parity re-supply of request-derived fields (IDG-030).

Mirrors the orchestrator/registry test idiom: the three network-bound stages
(Node 0 ``fetch_seeds``, Node 3 ``backward_traverse``, Node 4 ``forward_traverse``)
are mocked so the pure whole-graph stages run for real over injected outputs, and
the cache composition is exercised end-to-end against a real on-disk registry.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CitationEdge,
    CoCitationParameters,
    ForwardParameters,
    Node3Result,
    Node4Result,
    PaperRecord,
    PipelineParameters,
    PipelineResult,
)
from idiograph.domains.arxiv.pipeline import run_arxiv_pipeline
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    address_of,
    content_address,
)

_CLIENT = object()  # sentinel — every network stage is mocked, so it is unused.


# ── Helpers (mirroring the orchestrator/registry idiom) ──────────────────────


def _rec(
    node_id: str,
    root_ids: list[str] | None = None,
    hop_depth: int = 1,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=root_ids if root_ids is not None else [node_id],
        citation_count=0,
    )


def _seed(node_id: str) -> PaperRecord:
    return _rec(node_id, root_ids=[node_id], hop_depth=0)


def _edge(source: str, target: str, type: str = "cites") -> CitationEdge:
    return CitationEdge(
        source_id=source, target_id=target, type=type, strength=None
    )


def _params(min_strength: int = 1) -> PipelineParameters:
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        co_citation=CoCitationParameters(min_strength=min_strength, max_edges=None),
    )


def _install_stages(
    monkeypatch: pytest.MonkeyPatch,
    resolved: list[PaperRecord],
    failures: list[dict],
    n3: Node3Result,
    n4: Node4Result,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Mock Node 0/3/4 and return the (fetch, backward, forward) spies so tests
    can assert which stages ran on a hit vs a miss."""
    fetch = AsyncMock(return_value=(resolved, failures))
    backward = AsyncMock(return_value=n3)
    forward = AsyncMock(return_value=n4)
    monkeypatch.setattr(pipeline, "fetch_seeds", fetch)
    monkeypatch.setattr(pipeline, "backward_traverse", backward)
    monkeypatch.setattr(pipeline, "forward_traverse", forward)
    return fetch, backward, forward


def _small_graph() -> tuple[Node3Result, Node4Result]:
    """A one-seed graph with a backward and a forward neighbour."""
    n3 = Node3Result(papers=[_rec("B1", root_ids=["S"])], edges=[_edge("S", "B1")])
    n4 = Node4Result(papers=[_rec("F1", root_ids=["S"])], edges=[_edge("F1", "S")])
    return n3, n4


def _cached_run(
    registry: PipelineRegistry,
    parameters: PipelineParameters,
    seeds: list[dict] | None = None,
) -> PipelineResult:
    return asyncio.run(
        cached_run_arxiv_pipeline(
            seeds if seeds is not None else [{"arxiv_id": "x"}],
            parameters,
            client=_CLIENT,
            api_key="k",
            registry=registry,
        )
    )


def _uncached_run(
    parameters: PipelineParameters,
    seeds: list[dict] | None = None,
) -> PipelineResult:
    return asyncio.run(
        run_arxiv_pipeline(
            seeds if seeds is not None else [{"arxiv_id": "x"}],
            parameters,
            client=_CLIENT,
            api_key="k",
        )
    )


# ── Miss: populate + parity with the uncached pipeline ───────────────────────


def test_miss_populates_registry_and_equals_uncached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache MISS runs traversal, persists the result, and returns exactly what
    the uncached run_arxiv_pipeline produces for the same inputs."""
    s = _seed("S")
    n3, n4 = _small_graph()
    _install_stages(monkeypatch, [s], [], n3, n4)
    params = _params()

    reg = PipelineRegistry(tmp_path)
    missed = _cached_run(reg, params)

    # The uncached pipeline over identical mocked stages is the oracle.
    uncached = _uncached_run(params)
    assert missed.model_dump() == uncached.model_dump()

    # The registry is populated at the address the inputs key to.
    address = content_address([s.node_id], params)
    assert reg.path_for(address).exists()
    assert reg.read(address).model_dump() == missed.model_dump()


# ── Hit: stored result returned WITHOUT traversal ────────────────────────────


def test_hit_returns_stored_without_running_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a HIT, neither traversal function is invoked; resolution still runs."""
    s = _seed("S")
    n3, n4 = _small_graph()
    fetch, backward, forward = _install_stages(monkeypatch, [s], [], n3, n4)
    params = _params()
    reg = PipelineRegistry(tmp_path)

    # First call: MISS — populates the registry and runs both traversals.
    first = _cached_run(reg, params)
    assert backward.call_count == 1
    assert forward.call_count == 1

    # Second call, same inputs: HIT — traversal must not run again.
    backward.reset_mock()
    forward.reset_mock()
    fetch.reset_mock()
    second = _cached_run(reg, params)

    backward.assert_not_called()
    forward.assert_not_called()
    # Resolution MAY (and does) run on a hit — it precedes the key.
    fetch.assert_called_once()
    assert second.model_dump() == first.model_dump()


# ── Hit-parity: request-derived seed_failures ────────────────────────────────


def test_hit_parity_seed_failures_are_current_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests resolving to the SAME set but differing in requested seeds
    return DIFFERENT seed_failures — the current request's, not the cache
    populator's. Fails if the seed_failures re-supply is removed (IDG-030)."""
    s = _seed("S")
    n3, n4 = _small_graph()
    params = _params()
    reg = PipelineRegistry(tmp_path)

    # Request 1: [S, bad] → resolves to {S}, one failure. Populates the cache.
    _install_stages(
        monkeypatch,
        [s],
        [{"seed": {"arxiv_id": "bad"}, "reason": "no results"}],
        n3,
        n4,
    )
    populator = _cached_run(
        reg, params, seeds=[{"arxiv_id": "S"}, {"arxiv_id": "bad"}]
    )
    assert len(populator.seed_failures) == 1

    # Request 2: [S] → resolves to the SAME {S} (same address), zero failures.
    _install_stages(monkeypatch, [s], [], n3, n4)
    hit = _cached_run(reg, params, seeds=[{"arxiv_id": "S"}])

    # The hit carries the CURRENT request's (empty) failures, not the stored one.
    assert hit.seed_failures == []
    # Both requests share an address (same resolved set) — so it was truly a hit.
    assert address_of(populator) == address_of(hit)


# ── Hit-parity: request-derived seeds ORDER ──────────────────────────────────


def test_hit_parity_seeds_order_is_current_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests resolving to the same SET in a different ORDER share an
    address (order is normalized in the key) but return the current request's
    seeds ordering. Fails if the seeds re-supply is removed."""
    s1, s2 = _seed("S1"), _seed("S2")
    n3 = Node3Result(papers=[], edges=[])
    n4 = Node4Result(papers=[], edges=[])
    params = _params()
    reg = PipelineRegistry(tmp_path)

    # Populate with resolve order [S1, S2].
    _install_stages(monkeypatch, [s1, s2], [], n3, n4)
    populator = _cached_run(
        reg, params, seeds=[{"arxiv_id": "S1"}, {"arxiv_id": "S2"}]
    )
    assert populator.seeds == ["S1", "S2"]

    # Hit with resolve order [S2, S1] — same set, same address.
    _install_stages(monkeypatch, [s2, s1], [], n3, n4)
    hit = _cached_run(
        reg, params, seeds=[{"arxiv_id": "S2"}, {"arxiv_id": "S1"}]
    )

    assert hit.seeds == ["S2", "S1"]  # current order, not the stored ["S1","S2"]


# ── Determinism / full hit==miss==uncached parity ────────────────────────────


@pytest.mark.repeat(3)
def test_hit_equals_miss_equals_uncached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MISS, subsequent HIT, and the uncached pipeline all produce byte-identical
    results for the same inputs — the hit-provably-equals-miss invariant, run
    repeatedly to catch nondeterminism."""
    s = _seed("S")
    n3, n4 = _small_graph()
    failures = [{"seed": {"doi": "bad"}, "reason": "no results"}]
    _install_stages(monkeypatch, [s], failures, n3, n4)
    params = _params()

    reg = PipelineRegistry(tmp_path)
    missed = _cached_run(reg, params)  # MISS
    hit = _cached_run(reg, params)     # HIT
    uncached = _uncached_run(params)

    assert missed.model_dump() == uncached.model_dump()
    assert hit.model_dump() == uncached.model_dump()
    # The re-supplied request-derived provenance survived the round-trip.
    assert hit.seed_failures == uncached.seed_failures
    assert len(hit.seed_failures) == 1
