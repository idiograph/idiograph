# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""HIT leg: the measured warm boundary, through the GENERALIZED path.

``scripts/demos/crispr_hit_leg.py`` used to key its whole run on one corpus — the
seed dicts and the parameters came in through ``from crispr_freeze_trigger import
SEEDS, _parameters``, so the script could replay exactly the record that module
had frozen. Under IDG-113 clause 5 it takes ``--address`` and ``--registry-root``
and reads the run's own arguments off the record instead: parameters from the
record's ``parameters`` block, seeds from its ``<address>.request.json`` sidecar.

That generalization has to leave the demo's MEASURED boundary intact, and this
module is what holds it. The packaged CRISPR record is replayed through the
generalized argument path with the OpenAlex client mocked, and the two numbers
the demo prints are asserted:

  - ``traversal_entered == 0``. THE proof. A HIT short-circuits traversal on a
    name match and replays the stored, LLM-annotated graph; anything above zero
    means the cache re-derived and the demo proves nothing.
  - TWO OpenAlex GETs, one per seed. Node 0 resolution runs on every call, hit or
    miss, because it PRODUCES the address — so a HIT is not hermetic, and the
    honest boundary says two rather than zero.

``anthropic_client=None`` with ``parameters.llm`` SET is the demo's own tripwire
and is preserved here: that combination is the exact one ``run_traversal`` raises
on, so a leg that reached traversal would CRASH rather than quietly re-derive.

OFFLINE, AND CREDENTIAL-FREE ON ANY SEAT. The OpenAlex calls are answered by an
``httpx.MockTransport`` returning the two seed works, counted by the demo's own
``RequestCounter``, and the packaged registry is read but never written. The
tests that enter ``_main`` stub ``_openalex_key`` (the :func:`keyless` fixture),
because that helper runs ``load_dotenv()`` and raises ``SystemExit`` on a seat
with no key — so without the stub they would pass here and fail on a clean
checkout, which is a green suite resting on a credential no path in this file
ever spends.
"""

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv import cache as cache_module
from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
from idiograph.domains.arxiv.registry import PipelineRegistry

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMOS_DIR = _REPO_ROOT / "scripts" / "demos"
_DEMO_SCRIPT = _DEMOS_DIR / "crispr_hit_leg.py"


@contextmanager
def _demos_on_sys_path() -> Iterator[None]:
    """Put ``scripts/demos`` on ``sys.path`` for the duration of the load.

    Same shim, and same reasoning, as
    ``test_hit_leg_registry_selection.py::_demos_on_sys_path``: the script does
    ``from crispr_freeze_trigger import …`` relying on ``sys.path[0]`` being its
    own directory, and adding an ``__init__.py`` or a conftest hook would change
    the tree's shape to suit a test.
    """
    original = list(sys.path)
    sys.path.insert(0, str(_DEMOS_DIR))
    try:
        yield
    finally:
        sys.path[:] = original


def _load_demo_module():
    spec = importlib.util.spec_from_file_location(
        "idiograph_demo_crispr_hit_leg_replay", _DEMO_SCRIPT
    )
    assert spec is not None and spec.loader is not None, _DEMO_SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _demos_on_sys_path():
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load_demo_module()


@pytest.fixture(scope="module")
def packaged_arguments() -> tuple[list[dict], object]:
    """The run's own arguments, read the way the generalized script reads them.

    Deliberately NOT imported from ``crispr_freeze_trigger``: reading them off
    the record is the change under test, so a fixture that took the old path
    would exercise the code this module exists to replace.
    """
    address = frozen_crispr_address()
    registry = PipelineRegistry(REGISTRY_ROOT)
    request = registry.read_request(address)
    assert request is not None, "the packaged record carries no request sidecar"
    return request.seeds, registry.read(address).parameters


def _openalex_work(seed_node) -> dict:
    """One OpenAlex ``/works`` page holding the work a seed DOI names.

    Built FROM the record's own seed ``PaperRecord`` rather than hand-authored:
    ``make_node_id`` reads ``ids.doi``, and the node_id it produces has to be the
    one the packaged record's address was computed over or the replay lands
    beside the frozen record instead of on it. Deriving the payload from the
    record makes that agreement structural instead of a transcription anyone has
    to keep correct.
    """
    return {
        "results": [
            {
                "id": f"https://openalex.org/{seed_node.openalex_id}",
                "ids": {"doi": seed_node.doi},
                "title": seed_node.title,
                "publication_year": seed_node.year,
                "cited_by_count": seed_node.citation_count,
                "referenced_works": [],
            }
        ]
    }


def _resolving_transport(seed_nodes: list, counter) -> httpx.MockTransport:
    """Answer each seed's resolution GET, and refuse anything else.

    The refusal is the interesting half: a traversal that had NOT been
    short-circuited would issue works requests beyond these two, and this fails
    the test at the first one instead of letting a re-derivation pass quietly.
    """
    # Keyed by the bare DOI the seed filter carries — `10.1126/science.1225829`,
    # not the `https://doi.org/` URL the record stores.
    by_doi = {
        node.doi.rsplit("doi.org/", 1)[-1]: _openalex_work(node)
        for node in seed_nodes
    }

    def handle(request: httpx.Request) -> httpx.Response:
        counter.count += 1
        query = request.url.params.get("filter", "")
        for doi, payload in by_doi.items():
            if doi in query:
                return httpx.Response(200, json=payload)
        raise AssertionError(
            f"the warm leg issued an unexpected OpenAlex request: {request.url}"
        )

    return httpx.MockTransport(handle)


def _seed_nodes(record) -> list:
    """The record's own seed ``PaperRecord``s, in its seed order."""
    by_id = {node.node_id: node for node in record.nodes}
    return [by_id[node_id] for node_id in record.seeds]


def test_the_packaged_record_replays_with_zero_traversal(
    demo, packaged_arguments, tmp_path: Path
) -> None:
    """THE proof, through the generalized path: a HIT enters traversal zero times.

    The arguments are the ones the script now reads off the record, so this
    exercises the same values ``_main`` would pass. The counter is the demo's own
    ``TraversalSpy``, installed on the symbol the cache actually calls, so what is
    counted is what the production cache did.
    """
    seeds, parameters = packaged_arguments
    record = PipelineRegistry(REGISTRY_ROOT).read(frozen_crispr_address())
    traversal_spy = demo.TraversalSpy()
    openalex = demo.RequestCounter()
    transport = _resolving_transport(_seed_nodes(record), openalex)

    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await cached_run_arxiv_pipeline(
                seeds,
                parameters,
                client=client,
                api_key="k",
                registry=PipelineRegistry(REGISTRY_ROOT),
                # The HIT gate compares the packaged record's committed baseline
                # manifest against a live one, and any edit to the derivation
                # closure makes that a real mismatch — which is an APPEND. Rooted
                # in tmp_path because the default resolves against the working
                # directory, and appending there would drop a ledger into the
                # repository root every time the suite ran.
                mismatch_ledger_path=tmp_path / "mismatch_ledger.jsonl",
                # The demo's tripwire, preserved: llm is SET and there is no
                # client, the exact pair run_traversal raises on. A leg that
                # reached traversal would crash rather than re-derive quietly.
                anthropic_client=None,
            )

    original = cache_module.run_traversal
    cache_module.run_traversal = traversal_spy
    try:
        hit = asyncio.run(_go())
    finally:
        cache_module.run_traversal = original

    assert traversal_spy.entries == 0, (
        "the warm leg entered traversal — the generalized argument path is no "
        "longer addressing the packaged record, so the demo would re-derive at "
        "live OpenAlex and Anthropic cost instead of replaying"
    )
    assert openalex.count == 2, (
        "the measured boundary is ONE OpenAlex GET per seed — resolution runs on "
        f"every call because it produces the address — but {openalex.count} were "
        "made"
    )
    assert parameters.llm is not None, "the tripwire needs llm SET to be a tripwire"
    assert any(node.relationship_type is not None for node in hit.nodes), (
        "no relationship_type survived the replay, so nothing was actually "
        "replayed"
    )


def test_the_replay_writes_nothing_to_the_packaged_registry(
    demo, packaged_arguments, tmp_path: Path
) -> None:
    """A HIT reads. The packaged record and both its sidecars are untouched.

    Asserted on mtime as well as on bytes: the record is committed to the
    repository, and a warm leg that rewrote it — even identically — would put a
    9.3 MB diff in front of a reviewer for a run that derived nothing.
    """
    seeds, parameters = packaged_arguments
    record = PipelineRegistry(REGISTRY_ROOT).read(frozen_crispr_address())
    openalex = demo.RequestCounter()
    transport = _resolving_transport(_seed_nodes(record), openalex)
    before = {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(REGISTRY_ROOT.glob("*.json"))
    }

    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            await cached_run_arxiv_pipeline(
                seeds,
                parameters,
                client=client,
                api_key="k",
                registry=PipelineRegistry(REGISTRY_ROOT),
                mismatch_ledger_path=tmp_path / "mismatch_ledger.jsonl",
                anthropic_client=None,
            )

    asyncio.run(_go())

    assert {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(REGISTRY_ROOT.glob("*.json"))
    } == before


# ── The generalized selectors ────────────────────────────────────────────────


def test_the_selectors_default_to_the_packaged_crispr_replay(demo) -> None:
    """A bare invocation is the demo it always was.

    Both selectors default, and ``_replay_address`` with neither supplied is the
    packaged address — so the operator who types the command in the README gets
    the CRISPR replay and nothing about it has moved.
    """
    args = demo._run_arguments([])

    assert args.address is None
    assert args.registry_root is None
    assert (
        demo._replay_address(args.address, args.registry_root, REGISTRY_ROOT)
        == frozen_crispr_address()
    )


def test_an_operator_supplied_root_wins_outright(demo, tmp_path: Path) -> None:
    """A named root is selected, not weighed against XDG.

    A flag that is quietly overridden is the same false affordance as a flag that
    is quietly ignored, so ``--registry-root`` short-circuits the XDG-first rule
    entirely — and the label says so, since that line is what the operator reads
    to know which source won.
    """
    args = demo._run_arguments(["--registry-root", str(tmp_path)])
    root, label = demo._warm_registry_root(args.address, args.registry_root)

    assert root == tmp_path
    assert "operator" in label.lower()


def test_a_named_root_defaults_its_address_to_that_roots_sole_record(
    demo, tmp_path: Path
) -> None:
    """Someone who names a root means the record in it.

    Looking for the CRISPR address there would report "absent" about a root
    holding exactly one perfectly good artifact, which is the wrong answer to
    give an operator replaying their own freeze.
    """
    address = "e" * 64
    (tmp_path / f"{address}.json").write_text("{}", encoding="utf-8")

    assert demo._replay_address(None, tmp_path, tmp_path) == address


def test_an_explicit_address_is_used_as_given(demo, tmp_path: Path) -> None:
    address = "f" * 64
    assert demo._replay_address(address, None, REGISTRY_ROOT) == address
    assert demo._replay_address(address, tmp_path, tmp_path) == address


def test_the_selectors_are_the_only_arguments(demo) -> None:
    """Two selectors and nothing else that could ride into the address.

    Everything the run needs is read off the record, so a third argument here
    would be a value a caller could type that the artifact does not agree with.
    """
    args = demo._run_arguments([])

    assert set(vars(args)) == {"address", "registry_root"}


@pytest.fixture
def keyless(demo, monkeypatch: pytest.MonkeyPatch):
    """Stub the OpenAlex credential check for the ``_main`` reports below.

    ``_main`` calls ``_openalex_key()`` before it looks at a single file, and that
    helper runs ``load_dotenv()`` and raises ``SystemExit`` when no key is found.
    So without this stub these tests pass ONLY on a seat whose ``.env`` happens to
    hold a real key, and fail at the precondition on any seat that does not — a
    green suite that depends on a credential none of these paths ever spends.

    The stub is honest about what it removes. Every test using it asserts a report
    reached from LOCAL DISK ALONE — a missing record, an address the root does not
    hold, a record with no request sidecar — all of which ``_main`` decides before
    it resolves anything. The key is never used, so a fake one cannot make a
    passing test lie; it only stops the precondition from standing in front of the
    behaviour under test.
    """
    monkeypatch.setattr(demo, "_openalex_key", lambda: "test-key")
    return demo


def test_the_demo_reports_a_record_with_no_request(keyless, tmp_path: Path) -> None:
    """A record that cannot say what it was asked for stops the replay.

    Not a fallback to some other run's seeds: that substitution would be
    invisible on screen and wrong in exactly the case ``--address`` exists for.
    The exit code is distinct from the no-artifact one so the two failures are
    told apart by a script, and the report names the file it wanted.
    """
    address = frozen_crispr_address()
    (tmp_path / f"{address}.json").write_bytes(
        (REGISTRY_ROOT / f"{address}.json").read_bytes()
    )

    exit_code = asyncio.run(
        keyless._main(["--registry-root", str(tmp_path), "--address", address])
    )

    assert exit_code == 4


def test_the_demo_reports_an_address_the_root_does_not_hold(
    keyless, tmp_path: Path
) -> None:
    """Named but absent: reported from disk, without entering the pipeline."""
    address = frozen_crispr_address()
    (tmp_path / f"{address}.json").write_text("{}", encoding="utf-8")

    exit_code = asyncio.run(
        keyless._main(["--registry-root", str(tmp_path), "--address", "d" * 64])
    )

    assert exit_code == 3


def test_the_demo_reports_an_empty_root(keyless, tmp_path: Path) -> None:
    exit_code = asyncio.run(keyless._main(["--registry-root", str(tmp_path)]))

    assert exit_code == 3
