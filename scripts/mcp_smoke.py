# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# End-to-end smoke check of the served MCP surface over a REAL transport — a
# real subprocess and, for HTTP, a real loopback socket:
#
#     uv run python scripts/mcp_smoke.py            # stdio (the default)
#     uv run python scripts/mcp_smoke.py http       # streamable HTTP
#
# HAND-RUN, NEVER COLLECTED. This file was `scripts/test_mcp_smoke.py`, which
# matched pytest's default `test_*.py` glob; with no `testpaths` declared, every
# bare `uv run pytest` imported it at collection and ran its module body,
# spawning a subprocess even under `--collect-only` (finding 2a7572f2). That is
# question 6bee3e38, ruled by renaming this file off the glob. The suite fences
# the property in tests/test_mcp_http_transport.py, and the `__main__` guard at
# the foot means an import would still do nothing.
#
# What the suite cannot cover is exactly what this does: the suite drives the
# transports in process — direct dispatch, and the HTTP app through httpx's
# ASGITransport — so it never proves a client can reach the surface across a
# process boundary. This spawns the server the CLI actually starts and speaks
# MCP to it across one.
#
# The five-tool surface (IDG-109). `update_node` and `execute_graph` are not
# called here and are not called anywhere: mutation is gone because the repo is
# the authority, and execution lives at the CLI composition root.

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from idiograph.mcp_server import (
    DEFAULT_HTTP_HOST,
    HTTP_PATH,
    TRANSPORT_HTTP,
    TRANSPORT_STDIO,
)

FIVE_TOOLS = {
    "get_node",
    "get_edges_from",
    "summarize_intent",
    "validate_graph",
    "read_record",
}

# A real node id from the served declaration — Node 0 of the citation-traversal
# pipeline, the head whose seed set is configuration.
NODE_ID = "resolve"

# How long to wait for the spawned HTTP server to start listening. Generous:
# the server pays for the MCP stack and the arxiv pipeline import on the way up.
_STARTUP_TIMEOUT_SECONDS = 60


async def exercise(session: ClientSession) -> None:
    """The five-tool surface, over whichever transport ``session`` speaks.

    Shared by both transports deliberately: the claim under test is that the two
    are ways to reach ONE surface, so a check that ran different calls over each
    would not be checking it.
    """
    tools = await session.list_tools()
    tool_names = [t.name for t in tools.tools]
    print(f"Tools discovered: {tool_names}")
    assert set(tool_names) == FIVE_TOOLS
    assert len(tool_names) == len(FIVE_TOOLS)

    r = await session.call_tool("get_node", {"node_id": NODE_ID})
    print(f"\nget_node:\n{r.content[0].text}")

    r = await session.call_tool("get_edges_from", {"node_id": NODE_ID})
    print(f"\nget_edges_from:\n{r.content[0].text}")

    r = await session.call_tool("summarize_intent", {})
    print(f"\nsummarize_intent:\n{r.content[0].text}")

    r = await session.call_tool("validate_graph", {})
    print(f"\nvalidate_graph:\n{r.content[0].text}")
    assert json.loads(r.content[0].text) == {"valid": True, "errors": []}

    # Shape-only: the record's address, its resolved seeds, the parameters
    # that (with those seeds) derive that address, and a count per field.
    # Never the 9.3 MB dump.
    r = await session.call_tool("read_record", {})
    print(f"\nread_record (shape):\n{r.content[0].text}")

    r = await session.call_tool("read_record", {"select": "path", "path": ["seeds"]})
    print(f"\nread_record (path=['seeds']):\n{r.content[0].text}")


async def over_stdio() -> None:
    """Spawn `uv run idiograph serve` and speak MCP over its pipes."""
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "idiograph", "serve"],
    )
    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await exercise(session)


def free_loopback_port() -> int:
    """A loopback port nothing is listening on right now.

    Racy in principle — the port is released before the server claims it — and
    that is acceptable for a hand-run check on a seat where nothing else is
    binding ephemeral ports. The suite binds nothing at all.
    """
    with socket.socket() as probe:
        probe.bind((DEFAULT_HTTP_HOST, 0))
        return probe.getsockname()[1]


def wait_until_listening(port: int, server: subprocess.Popen) -> None:
    """Block until the spawned server accepts on ``port``, or fail loudly."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(
                f"Server exited with code {server.returncode} before listening."
            )
        try:
            with socket.create_connection((DEFAULT_HTTP_HOST, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(
        f"Server did not listen on {DEFAULT_HTTP_HOST}:{port} within "
        f"{_STARTUP_TIMEOUT_SECONDS}s."
    )


async def drive_http(url: str) -> None:
    """Speak MCP over HTTP to an already-listening server at ``url``."""
    async with (
        streamable_http_client(url) as (read, write, get_session_id),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        # Stateless mount: no session id is issued, so there is none to carry.
        # Printed rather than merely asserted — it is the visible difference
        # between the two mount modes.
        print(f"Session id: {get_session_id()}")
        await exercise(session)


def over_http() -> None:
    """Spawn the server on a loopback port, drive it over HTTP, stop it.

    Synchronous around the async conversation, not the other way round: the
    process lifecycle is blocking work (spawn, poll for the listen, terminate)
    and nesting it inside a coroutine would block the loop the client runs on.
    """
    port = free_loopback_port()
    url = f"http://{DEFAULT_HTTP_HOST}:{port}{HTTP_PATH}/"
    print(f"Starting server on {url}")
    server = subprocess.Popen(
        [
            "uv",
            "run",
            "idiograph",
            "serve",
            "--transport",
            TRANSPORT_HTTP,
            "--host",
            DEFAULT_HTTP_HOST,
            "--port",
            str(port),
        ]
    )
    try:
        wait_until_listening(port, server)
        asyncio.run(drive_http(url))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        print("\nServer stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "transport",
        nargs="?",
        default=TRANSPORT_STDIO,
        choices=[TRANSPORT_STDIO, TRANSPORT_HTTP],
        help="Transport to check. Defaults to stdio.",
    )
    transport = parser.parse_args().transport
    print(f"Smoke test: {transport} transport\n")
    if transport == TRANSPORT_STDIO:
        asyncio.run(over_stdio())
    else:
        over_http()
    print(f"\nSmoke test passed ({transport}).")


if __name__ == "__main__":
    sys.exit(main())
