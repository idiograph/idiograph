# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# The projection routes (goal eadb33e8): the surface a RENDERER consumes, served
# beside the tool surface an LLM consumes.
#
# The claim under test is byte identity, not shape. `render_projection_html`
# inlines `json.dumps(projection, sort_keys=True, ensure_ascii=False)` into the
# static HTML; a client fetching these routes must receive those same bytes, or
# the "live source the viewer would consume" is a second contract wearing the
# first one's name. Every body assertion here is therefore equality against that
# expression computed in process, never a re-description of the payload.
#
# NO SOCKET, NO SUBPROCESS (IDG-111). The server is the very ASGI app
# `serve_http` hands uvicorn, driven through `httpx.ASGITransport` in this
# process, so no test reaches a route by a path uvicorn would not.

import asyncio
import contextlib
import json
import shutil
from collections.abc import AsyncIterator, Callable, Iterator
from functools import lru_cache
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from idiograph import mcp_server
from idiograph.apps.viewer.generate import declared_pipeline_graph
from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    RecordRequest,
    request_sidecar_path_for,
)
from idiograph.domains.viewer import project_depth_provenance, project_graph
from idiograph.mcp_server import call_tool

# Composed from the module's own bind constants rather than re-typed, so a
# changed default moves these with it. The mount's canonical URL carries a
# trailing slash; the projection routes are exact paths and carry none.
BASE_URL = f"http://{mcp_server.DEFAULT_HTTP_HOST}:{mcp_server.DEFAULT_HTTP_PORT}"
MOUNT_URL = f"{BASE_URL}{mcp_server.HTTP_PATH}/"

#: The tool whose answer is compared across the mount, chosen because it takes
#: no arguments and its result is a total function of the served graph.
MOUNT_PROBE_TOOL = "validate_graph"


# ── Driving the real app in process ───────────────────────────────────────────


def over_http(work):
    """Run ``work(asgi_app)`` against the app ``serve_http`` builds.

    A local copy of the helper in tests/test_mcp_http_transport.py rather than an
    import of it: that module pins the transport and this one pins the routes
    beside it, and a shared helper would couple two files that must be able to
    fail independently. `ASGITransport` runs no lifespan, so the session
    manager's `run()` is entered here — the same context uvicorn enters through
    the app's lifespan.
    """

    async def _run():
        manager = mcp_server.http_session_manager()
        async with manager.run():
            return await work(mcp_server.build_http_app(manager))

    return asyncio.run(_run())


@contextlib.asynccontextmanager
async def client_for(asgi) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client whose transport is ``asgi`` instead of a socket."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi),
        base_url=BASE_URL,
        follow_redirects=True,
    ) as client:
        yield client


def fetch(
    path: str, method: str = "GET", params: dict | None = None
) -> httpx.Response:
    """One request to ``path`` against a freshly built app."""

    async def work(asgi):
        async with client_for(asgi) as client:
            return await client.request(method, path, params=params)

    return over_http(work)


def record_url(address: str | None = None) -> dict:
    """:func:`fetch` keyword arguments naming one record projection.

    Composed from the module's own route and parameter constants so the
    selector's spelling lives in exactly one place; ``None`` is the no-selector
    URL, which must stay the bare path it always was.
    """
    if address is None:
        return {"path": mcp_server.PROJECTION_RECORD_PATH}
    return {
        "path": mcp_server.PROJECTION_RECORD_PATH,
        "params": {mcp_server.PROJECTION_ADDRESS_PARAM: address},
    }


# ── What the routes must return, computed in process ──────────────────────────
#
# Both helpers are memoized only to avoid re-running a projection over 1,885
# nodes once per assertion; each returns exactly the expression the contract
# names, and neither shares a cache with the server's.


@lru_cache(maxsize=1)
def expected_graph_body() -> bytes:
    return json.dumps(
        project_graph(declared_pipeline_graph()), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


@lru_cache(maxsize=1)
def expected_record_body() -> bytes:
    record = PipelineRegistry(REGISTRY_ROOT).read(frozen_crispr_address())
    return json.dumps(
        project_depth_provenance(record), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def clear_record_caches() -> None:
    """Drop every memo on the record read path.

    Called where an assertion is about what a ROUTE does rather than about what
    a cache remembers — a warm cache would answer a request that never reached
    the registry and the test would pass for the wrong reason.
    """
    mcp_server._read_record.cache_clear()
    mcp_server._record_json.cache_clear()
    mcp_server._record_projection_body.cache_clear()


# ── Pointing the surface at another root ──────────────────────────────────────


@contextlib.contextmanager
def serving_root(root: Path) -> Iterator[Path]:
    """Serve ``root`` for the duration, then put the packaged default back.

    `set_registry_root` is the process setting `serve_http`/`main` write once at
    startup, and it drops the address-keyed memos on the way through — which is
    exactly what makes swapping roots inside one interpreter sound here, since a
    content address names a record WITHIN a root and not across roots.
    """
    mcp_server.set_registry_root(root)
    try:
        yield root
    finally:
        mcp_server.set_registry_root(None)


@pytest.fixture
def two_record_root(tmp_path: Path) -> Path:
    """A root holding the packaged record plus a SECOND, differently-addressed one.

    The second record's bytes are the first's, copied under a decoy address. That
    makes it unreadable through `PipelineRegistry.read` — which verifies that what
    came off disk addresses to its own filename — and that is deliberate: every
    assertion this fixture serves is about RESOLUTION (which address does this URL
    name, and does the root have a default), never about a projection of the decoy.
    A second genuine record would cost a second 9.3 MB artifact to prove nothing
    more.
    """
    address = frozen_crispr_address()
    root = tmp_path / "two"
    root.mkdir()
    shutil.copy(REGISTRY_ROOT / f"{address}.json", root / f"{address}.json")
    shutil.copy(
        request_sidecar_path_for(REGISTRY_ROOT, address),
        request_sidecar_path_for(root, address),
    )
    (root / f"{DECOY_ADDRESS}.json").write_text("{}", encoding="utf-8")
    PipelineRegistry(root).write_request(
        DECOY_ADDRESS, RecordRequest(label="decoy", seeds=[{"doi": "10.1/decoy"}])
    )
    return root


#: A well-formed content address that names nothing in the packaged registry.
#: Well-formed on purpose: a junk string would be refused by the shape check
#: before the registry was ever consulted, and the misses under test here are
#: about what the ROOT holds, not about what the pattern admits.
DECOY_ADDRESS = "9f2c4a7e1b6d8035c5e0a91d4f7b23680d8e6b13a4c95f27be14803da6f2c79e"


# ── The two projections, byte for byte ────────────────────────────────────────


def test_the_graph_route_serves_the_declared_graph_projection_byte_for_byte() -> None:
    response = fetch(mcp_server.PROJECTION_GRAPH_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == expected_graph_body()


@pytest.mark.parametrize(
    "address", [None, frozen_crispr_address()], ids=["no-selector", "explicit"]
)
def test_the_record_route_serves_the_depth_provenance_projection_byte_for_byte(
    address: str | None,
) -> None:
    """The same bytes with the selector absent and with it naming that record.

    The no-selector leg is the byte pin this route shipped with, unmoved by the
    selector arriving — the URL a client already has must answer exactly what it
    answered before. The explicit leg is the new claim: naming the address the
    default resolves to selects the same record, not a second code path with its
    own serialization.
    """
    response = fetch(**record_url(address))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == expected_record_body()


def test_the_graph_route_reads_no_record() -> None:
    """The declared-graph subject is a SHAPE, and a shape needs no run.

    Mechanical rather than documentary: the registry read is made to raise, so a
    route that resolved its graph from the packaged record's parameters — the
    inert-artifact-read defect cut at 9137725 — cannot answer 200 here. The
    caches are cleared first so the 200 is the route's and not a memo's.
    """
    expected = expected_graph_body()

    def refuse(self, address: str):
        raise AssertionError(f"the graph route read the registry at {address!r}")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PipelineRegistry, "read", refuse)
        clear_record_caches()
        response = fetch(mcp_server.PROJECTION_GRAPH_PATH)

    assert response.status_code == 200
    assert response.content == expected


def test_each_route_carries_the_view_key_its_projection_emits() -> None:
    """``meta["view"]`` is what the renderer dispatches on, so it must survive.

    Asserted against the value each projection emits in process rather than
    against a literal, so that a projection renaming its own view moves this
    test with it instead of pinning a string the renderer no longer looks for.
    """
    graph_view = json.loads(fetch(mcp_server.PROJECTION_GRAPH_PATH).content)["meta"]
    record_view = json.loads(fetch(mcp_server.PROJECTION_RECORD_PATH).content)["meta"]

    assert graph_view["view"] == project_graph(declared_pipeline_graph())["meta"]["view"]
    assert record_view["view"] == json.loads(expected_record_body())["meta"]["view"]
    assert graph_view["view"] != record_view["view"]


# ── Read-only, and beside the mount rather than over it ───────────────────────


def test_a_projection_route_refuses_a_write_verb() -> None:
    """GET-only, declared on the route: there is no body any of them can read."""
    response = fetch(mcp_server.PROJECTION_GRAPH_PATH, method="POST")
    assert response.status_code == 405


def test_a_projection_route_answers_head_without_a_body() -> None:
    """`Route` admits HEAD wherever it admits GET, which is what `curl -I` sends."""
    response = fetch(mcp_server.PROJECTION_RECORD_PATH, method="HEAD")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == b""


def test_the_mcp_mount_still_answers_beside_the_new_routes() -> None:
    """A route added beside the `Mount` neither shadows nor reorders it.

    Both surfaces are exercised against ONE app instance, in one process, so the
    assertion is about this router's resolution and not about two apps that
    happen to agree. The tool answer is compared to direct dispatch, which is the
    same equality tests/test_mcp_http_transport.py makes of the transport.
    """

    async def work(asgi):
        async with client_for(asgi) as client:
            projection = await client.get(mcp_server.PROJECTION_GRAPH_PATH)
            async with (
                streamable_http_client(MOUNT_URL, http_client=client) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                answer = await session.call_tool(MOUNT_PROBE_TOOL, {})
            assert not answer.isError, answer.content
            return projection, json.loads(answer.content[0].text)

    projection, over_mount = over_http(work)
    direct = json.loads(asyncio.run(call_tool(MOUNT_PROBE_TOOL, {}))[0].text)

    assert projection.status_code == 200
    assert projection.content == expected_graph_body()
    assert over_mount == direct


# ── The record projection is memoized, not re-read ────────────────────────────


def test_the_record_route_reads_the_registry_once_across_two_requests() -> None:
    """The memo is keyed by content address, so the second request reads nothing.

    Counted at `PipelineRegistry.read` rather than timed, because the property is
    "the durable artifact is read once", not "the second call was fast". Two
    separately built apps share the module-level cache, which is the point: the
    cache belongs to the content address, not to a server instance.
    """
    reads: list[str] = []
    original: Callable = PipelineRegistry.read

    def counting(self, address: str):
        reads.append(address)
        return original(self, address)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PipelineRegistry, "read", counting)
        clear_record_caches()
        first = fetch(mcp_server.PROJECTION_RECORD_PATH)
        second = fetch(mcp_server.PROJECTION_RECORD_PATH)

    assert first.content == second.content == expected_record_body()
    assert reads == [frozen_crispr_address()]


# ── Which record: the selector, and the roots it resolves against ─────────────


def test_the_record_route_refuses_a_malformed_address_without_touching_disk() -> None:
    """The property the route has always had, restated with a selector present.

    No client-supplied string reaches the registry except a validated content
    address: `../` and an absolute path cannot pass the shape check, so the
    registry is never consulted at all. Enforced mechanically — the read is made
    to raise — rather than asserted about.
    """

    def refuse(self, address: str):
        raise AssertionError(f"a malformed selector reached the registry: {address!r}")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PipelineRegistry, "read", refuse)
        clear_record_caches()
        traversal = fetch(**record_url("../../etc/passwd"))
        absolute = fetch(**record_url("/etc/passwd"))
        shouty = fetch(**record_url(frozen_crispr_address().upper()))

    for response in (traversal, absolute, shouty):
        assert response.status_code == 404, response.text
        assert "error" in json.loads(response.content)


def test_the_record_route_reports_an_unheld_address_as_a_structured_miss() -> None:
    """A well-formed address the root does not hold is DATA, not a 500."""
    response = fetch(**record_url(DECOY_ADDRESS))

    assert response.status_code == 404
    assert DECOY_ADDRESS in json.loads(response.content)["error"]


def test_a_multi_record_root_has_no_default_and_says_so(
    two_record_root: Path,
) -> None:
    """The no-selector request FAILS STRUCTURALLY rather than picking a winner.

    "The sole record" is a claim about a directory, and a root holding two makes
    it false. Choosing one would hand a client a plausible-looking projection of
    a record they never named, so the route answers with the fault instead.
    """
    with serving_root(two_record_root):
        response = fetch(**record_url())

    assert response.status_code == 404
    error = json.loads(response.content)["error"]
    assert str(two_record_root) in error
    assert "2" in error


def test_on_a_multi_record_root_an_explicit_address_still_resolves(
    two_record_root: Path,
) -> None:
    """No default available, but a named address serves — out of another root.

    The packaged address returns the packaged projection byte for byte from a
    root that is NOT the packaged one, which is the whole of what "resolve by
    registry root + content address" buys. And each address resolves to ITSELF:
    neither of the two records is ever substituted for the other, which is the
    failure a root-wide default would produce silently.

    That the projections themselves differ per address needs two READABLE
    records, so it is asserted in tests/test_freeze_command.py, where the module
    that knows how to produce records makes two.
    """
    with serving_root(two_record_root):
        packaged = fetch(**record_url(frozen_crispr_address()))
        resolved = [
            mcp_server._resolve_address(frozen_crispr_address()),
            mcp_server._resolve_address(DECOY_ADDRESS),
        ]

    assert packaged.status_code == 200
    assert packaged.content == expected_record_body()
    assert resolved == [frozen_crispr_address(), DECOY_ADDRESS]


def test_the_graph_route_refuses_an_address_rather_than_ignoring_it() -> None:
    """A selector that silently does nothing is a false affordance.

    The declared-graph projection emits param key NAMES and no values, so it is
    invariant to which record configured it — an accepted `address` would return
    identical bytes for every value and let a caller believe they had selected a
    subject they had not. Same call the viewer CLI makes when it refuses
    `--address` on this same view.
    """
    response = fetch(
        mcp_server.PROJECTION_GRAPH_PATH,
        params={mcp_server.PROJECTION_ADDRESS_PARAM: frozen_crispr_address()},
    )

    assert response.status_code == 400
    error = json.loads(response.content)["error"]
    assert mcp_server.PROJECTION_RECORD_PATH in error


def test_the_default_serving_root_is_the_packaged_registry() -> None:
    """A surface started with no argument serves what it always served.

    Asserted about the module's resolved value rather than about a response, so
    that the compatibility promise is pinned at its source and not inferred from
    one route agreeing with one fixture.
    """
    assert mcp_server.registry_root() == REGISTRY_ROOT


def test_setting_the_root_drops_the_address_keyed_memos(
    two_record_root: Path,
) -> None:
    """The memos are keyed by address alone, which is sound only per root.

    So the setter clears them. Without that, a projection cached under an address
    in one root would be served for the same address in another — the one shape
    in which a content-addressed memo can lie.
    """
    fetch(**record_url())  # warm the memo under the packaged root
    assert mcp_server._record_projection_body.cache_info().currsize == 1

    with serving_root(two_record_root):
        assert mcp_server._record_projection_body.cache_info().currsize == 0

    assert mcp_server._record_projection_body.cache_info().currsize == 0
