# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# The streamable-HTTP transport (goal c2449699): a second way to REACH the
# surface, not a second surface.
#
# tests/test_mcp_server.py calls the dispatch coroutines directly and so pins
# what is served. This module pins that a client reaching the same tools over
# HTTP is answered identically — which is the whole claim of the transport, and
# the one thing direct dispatch structurally cannot check.
#
# NO SOCKET, NO SUBPROCESS. The server is the very ASGI app `serve_http` hands
# uvicorn, driven through `httpx.ASGITransport` in this process. Binding a port
# in the suite would make it a port-availability test; spawning `uv run` would
# make it slow and machine-dependent. The real-socket check is the hand-run
# scripts/mcp_smoke.py, which is exactly why it must stay out of collection —
# see the tripwire at the foot of this module.

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from fnmatch import fnmatch
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from typer.testing import CliRunner

from idiograph import mcp_server
from idiograph.demo import frozen_crispr_address
from idiograph.domains.arxiv import pipeline_graph as pg
from idiograph.main import app as cli_app
from idiograph.mcp_server import RECORD_TOOL, call_tool

FIVE_TOOLS = {
    "get_node",
    "get_edges_from",
    "summarize_intent",
    "validate_graph",
    RECORD_TOOL,
}

# The mount's canonical URL, composed from the module's own bind constants
# rather than re-typed. The trailing slash is the canonical form — `Mount`
# answers `/mcp/` and 307s the bare `/mcp` onto it.
MOUNT_URL = (
    f"http://{mcp_server.DEFAULT_HTTP_HOST}:{mcp_server.DEFAULT_HTTP_PORT}"
    f"{mcp_server.HTTP_PATH}/"
)


# ── Driving the real transport in process ─────────────────────────────────────


@contextlib.asynccontextmanager
async def connect(asgi) -> AsyncIterator[tuple[ClientSession, Callable[[], str | None]]]:
    """One initialized MCP client session speaking HTTP to ``asgi``.

    The SDK's own `streamable_http_client` — the same client code a remote
    consumer runs — handed an httpx client whose transport is the app in this
    process instead of a socket. Everything above that substitution is the real
    request path: real HTTP semantics, real streamable-HTTP framing.
    """
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi), follow_redirects=True
    )
    async with (
        http_client,
        streamable_http_client(MOUNT_URL, http_client=http_client) as (
            read,
            write,
            get_session_id,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session, get_session_id


def over_http(work):
    """Run ``work(asgi_app)`` against a live streamable-HTTP mount.

    `ASGITransport` does not run a lifespan, so the session manager's `run()` is
    entered here — the same context uvicorn enters through the app's lifespan in
    `serve_http`. That is the reason `build_http_app` takes a manager rather
    than making one: both callers compose the identical two pieces.
    """

    async def _run():
        manager = mcp_server.http_session_manager()
        async with manager.run():
            return await work(mcp_server.build_http_app(manager))

    return asyncio.run(_run())


def http_call(name: str, arguments: dict | None = None):
    """Invoke one tool over HTTP and decode its text result."""

    async def work(asgi):
        async with connect(asgi) as (session, _):
            result = await session.call_tool(name, arguments or {})
            assert not result.isError, result.content
            assert len(result.content) == 1
            assert result.content[0].type == "text"
            return json.loads(result.content[0].text)

    return over_http(work)


def direct_call(name: str, arguments: dict | None = None):
    """The same tool by direct dispatch — the answer HTTP has to reproduce."""
    contents = asyncio.run(call_tool(name, arguments or {}))
    return json.loads(contents[0].text)


# The five calls, one per tool, that the direct-dispatch module already pins.
FIVE_CALLS = [
    ("get_node", {"node_id": pg.RESOLVE}),
    ("get_edges_from", {"node_id": pg.RESOLVE}),
    ("summarize_intent", {}),
    ("validate_graph", {}),
    (RECORD_TOOL, {}),
]


# ── The same surface, reached over HTTP ───────────────────────────────────────


def test_http_advertises_exactly_the_five_tools() -> None:
    async def work(asgi):
        async with connect(asgi) as (session, _):
            return [tool.name for tool in (await session.list_tools()).tools]

    names = over_http(work)
    assert set(names) == FIVE_TOOLS
    assert len(names) == len(FIVE_TOOLS)


@pytest.mark.parametrize(("name", "arguments"), FIVE_CALLS, ids=lambda a: str(a)[:24])
def test_every_tool_answers_identically_over_both_transports(
    name: str, arguments: dict
) -> None:
    """One surface, two doors: the transport is not allowed to shape the answer.

    Asserted as equality against direct dispatch rather than by re-describing
    each result, because the claim is not that HTTP returns something of the
    right shape — it is that it returns the same thing.
    """
    assert http_call(name, arguments) == direct_call(name, arguments)


def test_get_node_over_http_returns_the_declared_head() -> None:
    node = http_call("get_node", {"node_id": pg.RESOLVE})
    assert node["id"] == pg.RESOLVE
    assert node["type"]


def test_get_edges_from_over_http_returns_resolves_four_consumers() -> None:
    edges = http_call("get_edges_from", {"node_id": pg.RESOLVE})
    assert [edge["target"] for edge in edges] == [
        pg.BACKWARD,
        pg.FORWARD,
        pg.ASSEMBLE,
        pg.ANNOTATE,
    ]


def test_summarize_intent_over_http_is_unscoped() -> None:
    summary = http_call("summarize_intent")
    assert summary["graph"] == pg.PIPELINE_GRAPH_NAME
    assert summary["scope"] == "full"
    assert summary["node_count"] == 11
    assert summary["edge_count"] == 21


def test_validate_graph_over_http_is_green() -> None:
    assert http_call("validate_graph") == {"valid": True, "errors": []}


def test_read_record_over_http_is_the_shape_selection() -> None:
    shape = http_call(RECORD_TOOL)
    assert shape["address"] == frozen_crispr_address()
    assert shape["select"] == "shape"


@pytest.mark.parametrize("removed", ["update_node", "execute_graph"])
def test_removed_tools_are_refused_over_http_too(removed: str) -> None:
    """Mutation and remote execution are gone on every transport (IDG-109 §3).

    In process the unknown name raises `ValueError`; over a transport the
    protocol turns that into an error RESULT rather than a crash. Both are the
    same refusal, and neither is a silent no-op.
    """

    async def work(asgi):
        async with connect(asgi) as (session, _):
            return await session.call_tool(removed, {"node_id": pg.RESOLVE})

    result = over_http(work)
    assert result.isError
    assert f"Unknown tool: {removed}" in result.content[0].text


# ── Two clients at once ───────────────────────────────────────────────────────


def test_two_concurrent_clients_never_see_each_others_answers() -> None:
    """Concurrency safety, asserted where it is claimed rather than assumed.

    Two client sessions against one mount, each round issued together, over
    three rounds. Every response is the one that client asked for — which holds
    because there is no shared mutable state to race, not because anything
    locks: `resolve_graph()` builds a fresh `Graph` per request and the record
    read is memoized under its own content address.
    """

    async def work(asgi):
        async with (
            connect(asgi) as (first, first_session_id),
            connect(asgi) as (second, second_session_id),
        ):
            rounds = []
            for mine, theirs in (
                (pg.RESOLVE, pg.ANNOTATE),
                (pg.BACKWARD, pg.PAGERANK),
                (pg.ENRICH, pg.CLEAN),
            ):
                answers = await asyncio.gather(
                    first.call_tool("get_node", {"node_id": mine}),
                    second.call_tool("get_node", {"node_id": theirs}),
                )
                rounds.append(
                    (mine, theirs, [json.loads(a.content[0].text) for a in answers])
                )
            return rounds, first_session_id(), second_session_id()

    rounds, first_id, second_id = over_http(work)

    for mine, theirs, (first_answer, second_answer) in rounds:
        assert first_answer["id"] == mine
        assert second_answer["id"] == theirs

    # The stateless mount issues no session id to anyone, so there is no session
    # for either client to observe. That is the property itself, not a proxy for
    # it: an in-memory session -> transport registry is the mutable state
    # IDG-109 clause 4 says the surface does not have.
    assert first_id is None
    assert second_id is None


# ── The transport switch defaults to stdio ────────────────────────────────────


def select_transport(argv: list[str]) -> tuple[dict, int]:
    """Run `idiograph serve` with `argv`, recording the transport it selected.

    `mcp_server.main` is replaced, so the selection is observed and nothing is
    mounted — the test is of the switch, not of a socket. Patching it by
    attribute works because `main.serve` imports it inside the command body.
    """
    selected: dict = {}

    def record(transport: str, host: str | None, port: int | None) -> None:
        selected.update(transport=transport, host=host, port=port)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "main", record)
        result = CliRunner().invoke(cli_app, ["serve", *argv])
    return selected, result.exit_code


def test_serve_with_no_arguments_selects_stdio() -> None:
    """The compatibility promise: a bare `serve` is what it always was.

    Any client that spawns this process and speaks over the pipe must see no
    change at all from HTTP existing, so the default is asserted here rather
    than left to the option declaration.
    """
    selected, exit_code = select_transport([])
    assert exit_code == 0
    assert selected == {
        "transport": mcp_server.TRANSPORT_STDIO,
        "host": None,
        "port": None,
    }


def test_serve_selects_http_and_passes_the_bind_through() -> None:
    selected, exit_code = select_transport(
        ["--transport", "http", "--host", "127.0.0.1", "--port", "9999"]
    )
    assert exit_code == 0
    assert selected == {
        "transport": mcp_server.TRANSPORT_HTTP,
        "host": "127.0.0.1",
        "port": 9999,
    }


def test_serve_http_without_a_bind_leaves_the_defaults_to_mcp_server() -> None:
    """The CLI carries no second copy of the bind defaults."""
    selected, exit_code = select_transport(["--transport", "http"])
    assert exit_code == 0
    assert selected == {
        "transport": mcp_server.TRANSPORT_HTTP,
        "host": None,
        "port": None,
    }


def test_serve_rejects_an_unknown_transport() -> None:
    selected, exit_code = select_transport(["--transport", "carrier-pigeon"])
    assert exit_code != 0
    assert selected == {}


# ── The default HTTP bind is loopback ─────────────────────────────────────────


def test_the_default_bind_is_loopback() -> None:
    """A `serve` that selects HTTP without naming a host is not remotely reachable."""
    assert mcp_server.DEFAULT_HTTP_HOST in mcp_server._LOOPBACK_BINDS


def test_dns_rebinding_protection_follows_the_bind() -> None:
    """On loopback the allowlist is derived from the bound port; widened, it lifts.

    The failure this guards is the quiet one: a loopback-derived Host allowlist
    left in place over a bind the operator widened would refuse every remote
    client with a 421, i.e. refuse exactly the hosts they just opened.
    """
    narrow = mcp_server._transport_security(mcp_server.DEFAULT_HTTP_HOST, 8765)
    assert narrow.enable_dns_rebinding_protection
    assert narrow.allowed_hosts == [
        "127.0.0.1:8765",
        "localhost:8765",
        "[::1]:8765",
    ]
    assert all(origin.startswith("http://") for origin in narrow.allowed_origins)

    widened = mcp_server._transport_security("0.0.0.0", 8765)
    assert not widened.enable_dns_rebinding_protection


# ── Collection tripwire (question 6bee3e38) ───────────────────────────────────

#: pytest's default `python_files` discovery, verbatim. `pyproject.toml`
#: declares no `[tool.pytest.ini_options]` and so no `testpaths`, which is why
#: `scripts/` is collected from at all: a bare `uv run pytest` walks the rootdir
#: and imports every file matching one of these.
PYTEST_FILE_GLOBS = ("test_*.py", "*_test.py")

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_script_matches_pytest_file_discovery() -> None:
    """Nothing under `scripts/` may re-enter collection.

    `scripts/test_mcp_smoke.py` matched `test_*.py`, so every bare `uv run
    pytest` imported it and ran its module body — spawning a subprocess and
    printing "Smoke test passed." even under `--collect-only` (finding 2a7572f2,
    question 6bee3e38). Renaming that one file fixes today. This asserts the
    property, so the next hand-run script named into the glob fails here instead
    of quietly rejoining the suite.
    """
    collected = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "scripts").rglob("*.py"))
        if any(fnmatch(path.name, glob) for glob in PYTEST_FILE_GLOBS)
    ]
    assert collected == []
