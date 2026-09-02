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
from collections.abc import AsyncIterator, Callable
from functools import lru_cache

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from idiograph import mcp_server
from idiograph.apps.viewer.generate import declared_pipeline_graph
from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv.registry import PipelineRegistry
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


def fetch(path: str, method: str = "GET") -> httpx.Response:
    """One request to ``path`` against a freshly built app."""

    async def work(asgi):
        async with client_for(asgi) as client:
            return await client.request(method, path)

    return over_http(work)


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


# ── The two projections, byte for byte ────────────────────────────────────────


def test_the_graph_route_serves_the_declared_graph_projection_byte_for_byte() -> None:
    response = fetch(mcp_server.PROJECTION_GRAPH_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == expected_graph_body()


def test_the_record_route_serves_the_depth_provenance_projection_byte_for_byte() -> None:
    response = fetch(mcp_server.PROJECTION_RECORD_PATH)
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
