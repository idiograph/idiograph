# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Node 8 registry: content-addressed persistence + key derivation.

These tests build a real ``PipelineResult`` by running ``run_arxiv_pipeline``
with the three network-bound stages mocked (the established orchestrator-test
idiom), then exercise the registry's round-trip, address integrity,
order-independence, content-address soundness, and provenance retention.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CitationEdge,
    CoCitationParameters,
    FailedBatch,
    FailedSeed,
    ForwardParameters,
    Node3Result,
    Node4Result,
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    TruncatedSeed,
)
from idiograph.domains.arxiv.pipeline import run_arxiv_pipeline
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    address_of,
    content_address,
)

_CLIENT = object()  # sentinel — every network stage is mocked, so it is unused.


# ── Helpers (mirroring the orchestrator-test idiom) ──────────────────────────


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


def _edge(
    source: str,
    target: str,
    type: str = "cites",
    citing_paper_year: int | None = None,
) -> CitationEdge:
    return CitationEdge(
        source_id=source,
        target_id=target,
        type=type,
        citing_paper_year=citing_paper_year,
        strength=None,
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
) -> None:
    monkeypatch.setattr(
        pipeline, "fetch_seeds", AsyncMock(return_value=(resolved, failures))
    )
    monkeypatch.setattr(pipeline, "backward_traverse", AsyncMock(return_value=n3))
    monkeypatch.setattr(pipeline, "forward_traverse", AsyncMock(return_value=n4))


def _run(
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


def _build_result(
    monkeypatch: pytest.MonkeyPatch,
    *,
    params: PipelineParameters | None = None,
    with_failures: bool = True,
) -> PipelineResult:
    """A multi-node ``PipelineResult`` exercising provenance lists, cycle
    cleaning, and co-citation edges — the shape the round-trip must preserve."""
    s = _seed("S")
    a = _rec("A", root_ids=["S"])
    b = _rec("B", root_ids=["S"])
    c = _rec("C", root_ids=["S"])
    n3 = Node3Result(
        papers=[a, b, c],
        edges=[_edge("S", "C"), _edge("C", "A"), _edge("C", "B")],
        failed_batches=[
            FailedBatch(requested_ids=["W9"], stage="depth_2", reason="timeout")
        ],
    )
    n4 = Node4Result(
        papers=[],
        edges=[_edge("C", "A", citing_paper_year=1999)],
        failed_seeds=[FailedSeed(seed_id="S", reason="http_error: 503")],
        truncated_seeds=[
            TruncatedSeed(seed_id="S", returned_count=200, total_count=500)
        ],
    )
    failures = [{"seed": {"doi": "bad"}, "reason": "no results"}] if with_failures else []
    _install_stages(monkeypatch, [s], failures, n3, n4)
    return _run(params if params is not None else _params())


# ── Round-trip ───────────────────────────────────────────────────────────────


def test_round_trip_persist_reload_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """persist → reload yields an equal PipelineResult; the excluded witness is
    reconstructed (no RAISE) and all provenance lists survive."""
    result = _build_result(monkeypatch)
    # Sanity: the provenance surface under test is actually populated.
    assert result.seed_failures and result.co_citation_edges

    reg = PipelineRegistry(tmp_path)
    address = reg.write(result)
    restored = reg.read(address)

    assert restored.model_dump() == result.model_dump()
    assert restored.seed_failures == result.seed_failures
    assert restored.co_citation_edges == result.co_citation_edges
    assert restored.data_integrity_warnings == result.data_integrity_warnings


def test_written_file_is_content_addressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The on-disk file is named by the content address and is valid JSON."""
    result = _build_result(monkeypatch)
    reg = PipelineRegistry(tmp_path)
    address = reg.write(result)

    path = reg.path_for(address)
    assert path.exists()
    assert path.name == f"{address}.json"


# ── Address integrity ────────────────────────────────────────────────────────


def test_address_recomputed_from_reload_matches_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The address recomputed from the reloaded result equals the stored one."""
    result = _build_result(monkeypatch)
    reg = PipelineRegistry(tmp_path)
    address = reg.write(result)
    restored = reg.read(address)

    assert address_of(restored) == address


def test_read_rejects_tampered_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading a payload whose name disagrees with its content raises."""
    result = _build_result(monkeypatch)
    reg = PipelineRegistry(tmp_path)
    address = reg.write(result)

    # Copy the bytes under a bogus address; read must catch the disagreement.
    bogus = "0" * 64
    reg.path_for(bogus).write_text(
        reg.path_for(address).read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        reg.read(bogus)


# ── Order-independence ───────────────────────────────────────────────────────


def test_address_is_order_independent() -> None:
    """The same resolved seed set in a different order yields the same address."""
    params = _params()
    a = content_address(["S1", "S2", "S3"], params)
    b = content_address(["S3", "S1", "S2"], params)
    c = content_address(["S2", "S3", "S1", "S2"], params)  # duplicate normalized
    assert a == b == c


# ── Content-address soundness ────────────────────────────────────────────────


def test_same_resolved_set_and_params_same_address() -> None:
    """Equal resolved seed sets + equal parameters → equal address."""
    p1, p2 = _params(min_strength=2), _params(min_strength=2)
    assert content_address(["X", "Y"], p1) == content_address(["Y", "X"], p2)


def test_differing_params_differ_address() -> None:
    """Different parameters over the same seed set → different address."""
    seeds = ["X", "Y"]
    assert content_address(seeds, _params(min_strength=1)) != content_address(
        seeds, _params(min_strength=2)
    )


def test_differing_seed_set_differs_address() -> None:
    """Different resolved seed sets over the same parameters → different address."""
    params = _params()
    assert content_address(["X", "Y"], params) != content_address(["X", "Z"], params)


def test_two_results_same_inputs_same_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two independently-built PipelineResults with the same resolved seed set +
    parameters address identically."""
    r1 = _build_result(monkeypatch, params=_params(min_strength=1))
    r2 = _build_result(monkeypatch, params=_params(min_strength=1))
    assert address_of(r1) == address_of(r2)


# ── Provenance retention ─────────────────────────────────────────────────────


def test_seed_failures_survive_but_do_not_alter_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed_failures[].seed (requested-but-unresolved seeds) round-trips as
    provenance but is NOT part of the content address."""
    with_f = _build_result(monkeypatch, params=_params(), with_failures=True)
    without_f = _build_result(monkeypatch, params=_params(), with_failures=False)

    # Provenance differs...
    assert len(with_f.seed_failures) == 1
    assert without_f.seed_failures == []
    # ...but the resolved seed set + parameters are identical, so the address is.
    assert address_of(with_f) == address_of(without_f)

    reg = PipelineRegistry(tmp_path)
    address = reg.write(with_f)
    restored = reg.read(address)
    assert restored.seed_failures == with_f.seed_failures
    assert restored.seed_failures[0].seed == {"doi": "bad"}
