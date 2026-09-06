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
    RecordRequest,
    RequestSidecarConflict,
    address_of,
    content_address,
    durable_registry_root,
    sole_record_address,
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
        # Stated, never read from the clock: it enters the content address, so a
        # wall-clock value would move every address in this file on New Year.
        current_year=2026,
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
    # Nodes 3 and 4 are port-declared handlers — their stand-ins return the
    # declared output ports, not a bare Node3Result/Node4Result.
    monkeypatch.setattr(
        pipeline,
        "backward_traverse",
        AsyncMock(return_value={"backward": n3, "failed_batches": n3.failed_batches}),
    )
    monkeypatch.setattr(
        pipeline,
        "forward_traverse",
        AsyncMock(
            return_value={
                "forward": n4,
                "failed_seeds": n4.failed_seeds,
                "truncated_seeds": n4.truncated_seeds,
            }
        ),
    )


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


# ── Sole-record address ──────────────────────────────────────────────────────


def test_sole_record_address_returns_the_stem(tmp_path: Path) -> None:
    """One record under the root → its filename stem, which IS its address.

    The point of the helper: a single-record root already states its record's
    address, so nothing downstream has to hand-author the hex beside the file.
    """
    address = "a" * 64
    (tmp_path / f"{address}.json").write_text("{}", encoding="utf-8")

    assert sole_record_address(tmp_path) == address


def test_sole_record_address_ignores_non_json_neighbours(tmp_path: Path) -> None:
    """Only ``*.json`` counts — a stray README or a torn ``.json.tmp`` from the
    write path's atomic swap must not make a valid root look ambiguous."""
    address = "b" * 64
    (tmp_path / f"{address}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("notes", encoding="utf-8")
    (tmp_path / "leftover.json.tmp").write_text("{}", encoding="utf-8")

    assert sole_record_address(tmp_path) == address


def test_sole_record_address_rejects_an_empty_root(tmp_path: Path) -> None:
    """Zero records raises, naming the root and the count — the two facts that
    locate a broken checkout or a packaging fault."""
    with pytest.raises(ValueError) as excinfo:
        sole_record_address(tmp_path)

    message = str(excinfo.value)
    assert str(tmp_path) in message
    assert "0" in message


def test_sole_record_address_rejects_multiple_records(tmp_path: Path) -> None:
    """Two records raises rather than picking one: "the sole record" is a claim
    about the directory, and silently choosing a winner would launder a fault
    into a plausible-looking address."""
    (tmp_path / f"{'c' * 64}.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"{'d' * 64}.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        sole_record_address(tmp_path)

    message = str(excinfo.value)
    assert str(tmp_path) in message
    assert "2" in message


# ── The store writes the record's request beside it ──────────────────────────
#
# The policy itself (create-or-equal, the canonical form, the exclusion from the
# record enumeration) is pinned in tests/domains/arxiv/test_request_sidecar.py.
# What belongs HERE is the store's own contract: that `write` is the one code
# path persisting both halves, and that naming a request changes nothing about
# the record it accompanies.


def _request(label: str = "a corpus") -> RecordRequest:
    return RecordRequest(label=label, seeds=[{"arxiv_id": "x"}])


def test_write_without_a_request_writes_no_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is the behaviour every caller had before requests existed."""
    reg = PipelineRegistry(tmp_path)
    address = reg.write(_build_result(monkeypatch))

    assert reg.read_request(address) is None
    assert not reg.request_path_for(address).exists()


def test_write_with_a_request_attaches_it_to_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One code path persists both halves — the store, never a script."""
    reg = PipelineRegistry(tmp_path)
    address = reg.write(_build_result(monkeypatch), request=_request())

    assert reg.read_request(address) == _request()
    assert reg.request_path_for(address).name == f"{address}.request.json"


def test_a_request_does_not_change_the_record_or_its_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADDRESS-NEUTRAL BY DESIGN (IDG-113 clause 3).

    The same result written with and without a request — and with two DIFFERENT
    requests — lands at one address under byte-identical record bytes. The
    sidecar is provenance about what was asked for; nothing in it may reach
    ``content_address``, or two spellings of one corpus would derive two copies
    of one artifact.
    """
    result = _build_result(monkeypatch)
    bare = PipelineRegistry(tmp_path / "bare")
    labelled = PipelineRegistry(tmp_path / "labelled")
    relabelled = PipelineRegistry(tmp_path / "relabelled")

    plain = bare.write(result)
    named = labelled.write(result, request=_request())
    renamed = relabelled.write(result, request=_request(label="another name"))

    assert plain == named == renamed
    assert (
        bare.path_for(plain).read_bytes()
        == labelled.path_for(named).read_bytes()
        == relabelled.path_for(renamed).read_bytes()
    )
    # And the root still reports ONE record, sidecar and all.
    assert sole_record_address(tmp_path / "labelled") == plain


def test_writing_a_second_record_under_a_conflicting_request_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is persisted; the conflicting account of it is refused.

    Deliberately NOT fenced the way the derivation baseline is: a baseline is an
    observation and must never break what it observes, but a request is a CLAIM,
    and two different claims on one address is the exact condition create-or-equal
    exists to surface.
    """
    result = _build_result(monkeypatch)
    reg = PipelineRegistry(tmp_path)
    address = reg.write(result, request=_request())

    with pytest.raises(RequestSidecarConflict):
        reg.write(result, request=_request(label="a different account"))

    assert reg.read(address).model_dump() == result.model_dump()
    assert reg.read_request(address) == _request()


# ── The durable root ─────────────────────────────────────────────────────────


def test_durable_registry_root_follows_xdg_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read from the environment on every call, never captured at import.

    The demo scripts and their tests redirect ``XDG_DATA_HOME`` to keep an
    operator's real registry out of the suite, which only works because the read
    happens inside the body.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert durable_registry_root() == tmp_path / "idiograph" / "pipeline-registry"


def test_durable_registry_root_falls_back_to_local_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No XDG_DATA_HOME — and an empty one — mean the platform default.

    ``~/.local/share`` and never ``/tmp``: a frozen artifact costs real money and
    must survive a reboot.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".local" / "share" / "idiograph" / "pipeline-registry"

    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert durable_registry_root() == expected

    monkeypatch.setenv("XDG_DATA_HOME", "   ")
    assert durable_registry_root() == expected
