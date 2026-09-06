# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# `idiograph freeze` (goal 25ff3966): record a corpus named by a seed spec.
#
# The command takes ONE argument — a path — and nothing else that could ride into
# the content address. That is the property this module exists to hold: a run is
# reproducible from a file under review rather than from a set of flags somebody
# remembered to repeat, and a spec whose parameters do not round-trip is refused
# before it can name an address the run would not take.
#
# NOTHING HERE TOUCHES THE NETWORK OR THE OPERATOR'S REGISTRY. The freeze CLI
# builds live OpenAlex and Anthropic clients and writes to XDG; that wiring stops
# at `main.run_freeze`, which takes every client injected. Below that seam this
# module drives the REAL `cached_run_arxiv_pipeline` against a tmp_path registry
# with the three network-bound stages mocked (the established cache-test idiom)
# and an httpx client whose transport raises if anything reaches it. The command
# function itself is exercised only where it cannot reach a socket.

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import typer
from typer.testing import CliRunner

from idiograph import main as main_module
from idiograph.core.executor import HANDLERS
from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.handlers import register_arxiv_handlers
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CitationEdge,
    ForwardParameters,
    LLMConfig,
    Node3Result,
    Node4Result,
    PaperRecord,
    PipelineParameters,
)
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    RequestSidecarConflict,
)
from idiograph.domains.arxiv.relationship_annotation import prompt_template_hash
from idiograph.main import SeedSpec, load_seed_spec, run_freeze
from idiograph.main import app as cli_app

REPO_ROOT = Path(__file__).resolve().parents[1]
SPECS_DIR = REPO_ROOT / "specs"

# The two specs that ship with the repo. Named here so a spec added without a
# test is a visible omission rather than a silent one.
CRISPR_SPEC = SPECS_DIR / "crispr-april-2026.json"
IOANNIDIS_SPEC = SPECS_DIR / "ioannidis-2011.json"


# ── An httpx client that refuses to be used ───────────────────────────────────


def _refusing_client() -> httpx.AsyncClient:
    """A real ``httpx.AsyncClient`` whose transport fails any actual request.

    Every network-bound stage below is mocked, so nothing should reach this. It
    is a real client rather than a sentinel precisely so that "no HTTP was made"
    is enforced by the object under test rather than asserted about it.
    """

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the freeze seam issued a live request: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(refuse))


# ── A stubbed Anthropic client (mirrors tests/domains/arxiv/test_cache.py) ────


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, *, model, max_tokens, temperature, messages):
        self.calls.append({"model": model})
        return _FakeResponse(
            json.dumps(
                {
                    "relationship_type": "downstream_application",
                    "semantic_confidence": 0.8,
                    "reasoning": "because",
                }
            )
        )


class _FakeAnthropic:
    """Minimal stand-in for ``AsyncAnthropic`` — only ``.messages.create``."""

    def __init__(self) -> None:
        self.messages = _FakeMessages()

    @property
    def call_count(self) -> int:
        return len(self.messages.calls)


# ── Graph fixtures (the orchestrator/cache test idiom) ────────────────────────


def _rec(node_id: str, root_ids: list[str] | None = None, hop_depth: int = 1):
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=node_id,
        hop_depth=hop_depth,
        root_ids=root_ids if root_ids is not None else [node_id],
        citation_count=0,
    )


def _edge(source: str, target: str) -> CitationEdge:
    return CitationEdge(
        source_id=source, target_id=target, type="cites", strength=None
    )


def _install_stages(monkeypatch: pytest.MonkeyPatch, seed_id: str = "S") -> None:
    """Mock Node 0/3/4 in BOTH places the cache can reach them.

    Verbatim the discipline tests/domains/arxiv/test_cache.py states: post-flip
    ``run_traversal`` dispatches through ``HANDLERS`` while any surviving direct
    path reads the module attribute, so a stand-in installed in only one place
    would answer for only one path and pass vacuously.

    ``seed_id`` is what Node 0 resolves to, and the whole stubbed graph hangs off
    it — a resolved id the traversal stands-ins did not also use would leave Node
    5's cycle-clean witness with an orphaned edge endpoint. Two different values
    give two different content addresses, which is what the multi-record fixture
    below needs.
    """
    seed = _rec(seed_id, root_ids=[seed_id], hop_depth=0)
    fetch = AsyncMock(return_value=([seed], []))
    n3 = Node3Result(
        papers=[_rec("B1", root_ids=[seed_id])], edges=[_edge(seed_id, "B1")]
    )
    n4 = Node4Result(
        papers=[_rec("F1", root_ids=[seed_id])], edges=[_edge("F1", seed_id)]
    )
    backward = AsyncMock(
        return_value={"backward": n3, "failed_batches": n3.failed_batches}
    )
    forward = AsyncMock(
        return_value={
            "forward": n4,
            "failed_seeds": n4.failed_seeds,
            "truncated_seeds": n4.truncated_seeds,
        }
    )
    monkeypatch.setattr(pipeline, "fetch_seeds", fetch)
    register_arxiv_handlers()
    monkeypatch.setattr(pipeline, "backward_traverse", backward)
    monkeypatch.setitem(HANDLERS, "BackwardTraverse", backward)
    monkeypatch.setattr(pipeline, "forward_traverse", forward)
    monkeypatch.setitem(HANDLERS, "ForwardTraverse", forward)


def _parameters() -> PipelineParameters:
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        # Stated, never read from the clock — it enters the content address.
        current_year=2026,
        llm=LLMConfig(
            model_id="claude-haiku-4-5-20251001",
            prompt_template_hash=prompt_template_hash(),
        ),
    )


def _spec_payload(label: str = "a corpus", seeds: list[dict] | None = None) -> dict:
    return {
        "label": label,
        "seeds": [{"doi": "10.1/seed"}] if seeds is None else seeds,
        "parameters": _parameters().model_dump(mode="json"),
    }


def _write_spec(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _freeze(spec: SeedSpec, registry: PipelineRegistry) -> tuple[str, bool]:
    async def _go():
        async with _refusing_client() as client:
            return await run_freeze(
                spec,
                registry=registry,
                client=client,
                api_key="k",
                anthropic_client=_FakeAnthropic(),
            )

    return asyncio.run(_go())


# ── The spec is the whole input, and it must not lie ──────────────────────────


def test_a_well_formed_spec_loads(tmp_path: Path) -> None:
    spec = load_seed_spec(_write_spec(tmp_path / "s.json", _spec_payload()))

    assert spec.label == "a corpus"
    assert spec.seeds == [{"doi": "10.1/seed"}]
    assert spec.parameters == _parameters()


def test_a_spec_omitting_a_required_parameter_is_a_usage_error(
    tmp_path: Path,
) -> None:
    """Not a defaulted run (IDG-113): ``current_year`` has no default ON PURPOSE,
    and supplying one here would let a spec take an address it never stated."""
    payload = _spec_payload()
    del payload["parameters"]["current_year"]

    with pytest.raises(typer.BadParameter) as excinfo:
        load_seed_spec(_write_spec(tmp_path / "s.json", payload))

    assert "current_year" in str(excinfo.value)


def test_a_spec_missing_a_top_level_field_is_a_usage_error(tmp_path: Path) -> None:
    payload = _spec_payload()
    del payload["label"]

    with pytest.raises(typer.BadParameter) as excinfo:
        load_seed_spec(_write_spec(tmp_path / "s.json", payload))

    assert "label" in str(excinfo.value)


def test_a_spec_whose_parameters_do_not_round_trip_is_refused(
    tmp_path: Path,
) -> None:
    """A typo'd field name would otherwise be IGNORED and silently defaulted.

    Pydantic drops unknown keys, so ``pagernak`` would leave the real
    ``pagerank`` at its default and the run would take an address the spec does
    not describe. The round-trip check is what turns that into a refusal naming
    the field.
    """
    payload = _spec_payload()
    payload["parameters"]["pagernak"] = {"damping": 0.5}

    with pytest.raises(typer.BadParameter) as excinfo:
        load_seed_spec(_write_spec(tmp_path / "s.json", payload))

    assert "pagernak" in str(excinfo.value)


def test_a_spec_pinning_a_stale_contract_hash_is_refused(tmp_path: Path) -> None:
    """``parse_contract_hash``/``traversal_contract_hash`` are DERIVED from the
    code. A spec pinning a value the tree no longer produces would address a
    record derived under a contract that no longer exists, so it is refused
    rather than run."""
    payload = _spec_payload()
    payload["parameters"]["traversal_contract_hash"] = "0" * 64

    with pytest.raises(typer.BadParameter) as excinfo:
        load_seed_spec(_write_spec(tmp_path / "s.json", payload))

    assert "traversal_contract_hash" in str(excinfo.value)


def test_an_unreadable_spec_is_a_usage_error(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(typer.BadParameter):
        load_seed_spec(tmp_path / "bad.json")

    with pytest.raises(typer.BadParameter):
        load_seed_spec(tmp_path / "absent.json")


# ── The freeze seam: spec in, record + request out ────────────────────────────


def test_a_miss_writes_the_record_and_its_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One run of the production cached path leaves a record that can say what
    it was asked for — through the registry store, never through this command."""
    _install_stages(monkeypatch)
    registry = PipelineRegistry(tmp_path)
    spec = load_seed_spec(_write_spec(tmp_path / "s.json", _spec_payload()))

    address, hit = _freeze(spec, registry)

    assert hit is False
    assert registry.path_for(address).is_file()
    request = registry.read_request(address)
    assert request.label == "a corpus"
    assert request.seeds == spec.seeds


def test_a_second_run_on_the_same_spec_hits_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache's own contract, and the reason ``freeze`` is safe to re-run.

    "Writes nothing" is asserted on mtime as well as on bytes: create-or-equal
    no-ops without opening the file, so an unchanged request leaves the durable
    artifact and its account of itself both untouched.
    """
    _install_stages(monkeypatch)
    registry = PipelineRegistry(tmp_path)
    spec = load_seed_spec(_write_spec(tmp_path / "s.json", _spec_payload()))

    first_address, first_hit = _freeze(spec, registry)
    stamps = {
        path: path.stat().st_mtime_ns for path in sorted(tmp_path.glob("*.json"))
    }

    second_address, second_hit = _freeze(spec, registry)

    assert first_hit is False
    assert second_hit is True
    assert second_address == first_address
    assert {
        path: path.stat().st_mtime_ns for path in sorted(tmp_path.glob("*.json"))
    } == stamps


def test_the_same_corpus_under_a_different_label_raises_on_the_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE COLLISION THE ADDRESS CANNOT SEE (IDG-113 clause 3).

    The label is not in the content address, so a relabelled spec hits the record
    the first one froze. Create-or-equal is what makes that visible instead of
    letting the second spec quietly overwrite the first one's account of a run
    neither of them re-derived. The record and the stored request both survive.
    """
    _install_stages(monkeypatch)
    registry = PipelineRegistry(tmp_path)
    first = load_seed_spec(_write_spec(tmp_path / "a.json", _spec_payload()))
    relabelled = load_seed_spec(
        _write_spec(tmp_path / "b.json", _spec_payload(label="a different name"))
    )

    address, _ = _freeze(first, registry)

    with pytest.raises(RequestSidecarConflict) as excinfo:
        _freeze(relabelled, registry)

    assert address in str(excinfo.value)
    assert registry.read_request(address).label == "a corpus"
    assert registry.path_for(address).is_file()


def test_a_hit_on_a_record_with_no_request_records_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record frozen before requests existed acquires one on the next freeze.

    The HIT leg attaches too, and this is why: the address is a function of the
    RESOLVED seeds, so a hit reached the record through a request the record
    itself cannot state. Create-or-equal means attaching is safe.
    """
    _install_stages(monkeypatch)
    registry = PipelineRegistry(tmp_path)
    spec = load_seed_spec(_write_spec(tmp_path / "s.json", _spec_payload()))

    address, _ = _freeze(spec, registry)
    registry.request_path_for(address).unlink()
    assert registry.read_request(address) is None

    again_address, hit = _freeze(spec, registry)

    assert hit is True
    assert again_address == address
    assert registry.read_request(address).seeds == spec.seeds


def test_the_freeze_seam_makes_no_http_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusing transport is the assertion — it raises on any live request.

    Stated as its own test because every other test in this file depends on it
    silently, and a transport that had quietly stopped refusing would make all of
    them pass for the wrong reason.
    """
    _install_stages(monkeypatch)

    async def _go():
        async with _refusing_client() as client:
            with pytest.raises(AssertionError, match="live request"):
                await client.get("https://api.openalex.org/works")

    asyncio.run(_go())


# ── Two records, one root, and the surface that must tell them apart ──────────
#
# THE POINT OF THE WHOLE GOAL, and it lives here because this is the module that
# knows how to MAKE a record: two freezes of two specs into one tmp_path root
# produce two genuine, readable artifacts, each with its own request sidecar.
# tests/test_projection_routes.py pins the route-level behaviour on a root it can
# build cheaply; what needs two real records is asserted below.


@pytest.fixture
def two_corpus_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Freeze two different specs into ONE root. Returns {label: address}.

    The two differ ONLY in their seeds, so they are two records of one
    configuration — the shape an operator's registry actually takes once they
    have frozen a second corpus beside the exhibit.
    """
    root = tmp_path / "registry"
    registry = PipelineRegistry(root)
    addresses = {}
    for label, seed in (("first corpus", "10.1/a"), ("second corpus", "10.1/b")):
        # Each spec resolves to its OWN seed node_id, so the two take different
        # addresses — which is the point: one root, two records, and a surface
        # that must be told which.
        _install_stages(monkeypatch, seed_id=seed)
        spec = load_seed_spec(
            _write_spec(
                tmp_path / f"{label.replace(' ', '-')}.json",
                _spec_payload(label=label, seeds=[{"doi": seed}]),
            )
        )
        addresses[label] = _freeze(spec, registry)[0]
    return {"root": root, "addresses": addresses}


@pytest.fixture
def served(two_corpus_root: dict):
    """Point the served surface at that root for the duration of one test."""
    from idiograph import mcp_server

    mcp_server.set_registry_root(two_corpus_root["root"])
    try:
        yield two_corpus_root
    finally:
        mcp_server.set_registry_root(None)


def test_two_specs_freeze_to_two_addresses_under_one_root(
    two_corpus_root: dict,
) -> None:
    """Different seeds, one configuration, one root — two records and two requests."""
    root, addresses = two_corpus_root["root"], two_corpus_root["addresses"]
    registry = PipelineRegistry(root)
    first, second = addresses["first corpus"], addresses["second corpus"]

    assert first != second
    assert registry.read_request(first).label == "first corpus"
    assert registry.read_request(second).label == "second corpus"
    assert registry.read_request(first).seeds == [{"doi": "10.1/a"}]
    assert registry.read_request(second).seeds == [{"doi": "10.1/b"}]


def test_a_multi_record_root_has_no_default_record(served: dict) -> None:
    """The served surface reports it structurally rather than choosing a winner."""
    from idiograph.mcp_server import RECORD_TOOL, call_tool

    answer = json.loads(asyncio.run(call_tool(RECORD_TOOL, {}))[0].text)

    assert "error" in answer
    assert str(served["root"]) in answer["error"]


def test_each_address_reads_its_own_record(served: dict) -> None:
    """A named address is answered by the record it names, never its neighbour."""
    from idiograph.mcp_server import RECORD_TOOL, call_tool

    for label, address in served["addresses"].items():
        shape = json.loads(
            asyncio.run(call_tool(RECORD_TOOL, {"address": address}))[0].text
        )
        assert shape["address"] == address, label
        expected = "10.1/a" if label == "first corpus" else "10.1/b"
        assert shape["seeds"] == [expected], label


def test_the_declared_graph_is_built_from_that_records_own_request(
    served: dict,
) -> None:
    """THE SIDECAR IS WHAT CONFIGURES THE DECLARATION (IDG-113 clause 5).

    Node 0's ``seeds`` param carries the REQUEST dicts, and they must come off
    the record being served — not off a packaged literal, which on a root the
    package never wrote would describe a run that did not happen. Asserted per
    address, because a fallback would give both records the same seeds and pass
    every test that only looked at one.
    """
    from idiograph.domains.arxiv import pipeline_graph as pg
    from idiograph.mcp_server import resolve_graph

    seeds_by_label = {
        label: resolve_graph(address).get_node(pg.RESOLVE).params["seeds"]
        for label, address in served["addresses"].items()
    }

    assert seeds_by_label == {
        "first corpus": [{"doi": "10.1/a"}],
        "second corpus": [{"doi": "10.1/b"}],
    }


def test_a_record_with_no_request_is_a_structured_miss_naming_the_sidecar(
    served: dict,
) -> None:
    """No fallback. A record that cannot say what it was asked for says so.

    Substituting some other run's seeds would be invisible in the response and
    wrong in exactly the case the address selector exists for, so the miss names
    the file it wanted instead.
    """
    from idiograph.mcp_server import call_tool

    address = served["addresses"]["first corpus"]
    registry = PipelineRegistry(served["root"])
    sidecar = registry.request_path_for(address)
    sidecar.unlink()

    answer = json.loads(
        asyncio.run(call_tool("validate_graph", {"address": address}))[0].text
    )

    assert "error" in answer
    assert str(sidecar) in answer["error"]


# ── The shipped specs ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [CRISPR_SPEC, IOANNIDIS_SPEC], ids=lambda p: p.name)
def test_every_shipped_spec_loads(path: Path) -> None:
    """A spec in the tree that will not load is a freeze the operator cannot run.

    This is the standing guard on the derived contract hashes: they are
    ``default_factory`` fields computed from the code, so a semantic edit to the
    Node 5.5 prompt or the Node 3 traversal contract makes every shipped spec
    stale — and it fails HERE, at a name, rather than at an operator's terminal
    after a spend.
    """
    spec = load_seed_spec(path)
    assert spec.label
    assert spec.seeds


def test_the_crispr_spec_reproduces_the_exhibit_it_names() -> None:
    """The spec and the packaged record must agree, or the spec is a lie.

    Parameters field for field against the record's own block, and the request
    against the sidecar committed beside it. Together those are exactly the two
    halves ``freeze`` would feed the pipeline, so agreement here is what makes
    the exhibit expressible as a spec rather than merely described by one.
    """
    spec = load_seed_spec(CRISPR_SPEC)
    address = frozen_crispr_address()
    record = json.loads(
        (REGISTRY_ROOT / f"{address}.json").read_text(encoding="utf-8")
    )
    request = PipelineRegistry(REGISTRY_ROOT).read_request(address)

    assert spec.parameters.model_dump(mode="json") == record["parameters"]
    assert spec.seeds == request.seeds
    assert spec.label == request.label


def test_the_ioannidis_spec_names_the_second_corpus() -> None:
    """The corpus goal 25ff3966 exists to freeze, stated as a reviewable file.

    Two DOIs and the exhibit's own parameters. It is NOT run here and must not be
    — the freeze costs real OpenAlex and Anthropic calls and is the operator's to
    trigger — so what the suite can hold is that the file says what it should.
    """
    spec = load_seed_spec(IOANNIDIS_SPEC)

    assert spec.seeds == [
        {"doi": "10.1371/journal.pmed.0020124"},
        {"doi": "10.1177/0956797611417632"},
    ]
    assert "Ioannidis" in spec.label
    # Same run configuration as the exhibit, so the two corpora are comparable
    # rather than merely both present — except that nothing about the SEEDS is
    # shared, which is what makes it a second corpus and a second address.
    assert (
        spec.parameters.model_dump(mode="json")
        == load_seed_spec(CRISPR_SPEC).parameters.model_dump(mode="json")
    )
    assert spec.parameters.current_year == 2026
    assert spec.seeds != load_seed_spec(CRISPR_SPEC).seeds


def test_the_two_shipped_specs_address_differently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different seeds, same parameters — so the addresses must differ.

    Asserted over the REQUEST seeds rather than resolved ones (resolution needs
    the network), which is sound here because the two seed sets are disjoint: no
    resolution could map them onto one another.
    """
    from idiograph.domains.arxiv.registry import content_address

    crispr = load_seed_spec(CRISPR_SPEC)
    ioannidis = load_seed_spec(IOANNIDIS_SPEC)

    def as_ids(spec: SeedSpec) -> list[str]:
        return [seed["doi"] for seed in spec.seeds]

    assert content_address(as_ids(crispr), crispr.parameters) != content_address(
        as_ids(ioannidis), ioannidis.parameters
    )


# ── The CLI wiring above the seam ─────────────────────────────────────────────


def test_freeze_refuses_to_start_without_both_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both keys, checked BEFORE anything resolves.

    A HIT draws no model, but which leg a run takes is not knowable until the
    seeds resolve — so discovering a missing Anthropic key after a MISS has begun
    traversing would waste the OpenAlex leg of a run that cannot finish.

    The variables are set EMPTY rather than deleted: the app callback runs
    ``load_dotenv()``, which does not override a variable already present, so an
    operator's real ``.env`` cannot leak a live key into this test.
    """
    monkeypatch.setenv("OPENALEX_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    def refuse(*args, **kwargs):
        raise AssertionError("freeze reached the run seam without credentials")

    monkeypatch.setattr(main_module, "run_freeze", refuse)
    spec = _write_spec(tmp_path / "s.json", _spec_payload())

    result = CliRunner().invoke(cli_app, ["freeze", str(spec)])

    assert result.exit_code != 0
    assert "OPENALEX_API_KEY" in result.output
    assert "ANTHROPIC_API_KEY" in result.output


def test_freeze_reports_the_address_the_root_and_the_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the operator reads off the terminal, pinned as JSON.

    The run seam is replaced, so no client is ever used and no registry is ever
    written — this is a test of the report, which is the whole of what the
    command adds above ``run_freeze``.
    """
    monkeypatch.setenv("OPENALEX_API_KEY", "openalex")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    address = "c" * 64

    async def stub(spec, **kwargs):
        assert spec.label == "a corpus"
        return address, True

    monkeypatch.setattr(main_module, "run_freeze", stub)
    spec = _write_spec(tmp_path / "s.json", _spec_payload())

    result = CliRunner().invoke(cli_app, ["freeze", str(spec)])

    assert result.exit_code == 0, result.output
    reported = json.loads(result.output)
    root = tmp_path / "idiograph" / "pipeline-registry"
    assert reported == {
        "address": address,
        "registry_root": str(root),
        "result": "HIT",
        "record": str(root / f"{address}.json"),
        "request": str(root / f"{address}.request.json"),
    }
