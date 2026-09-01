# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# End-to-end smoke test of the served MCP surface over the REAL stdio transport:
# spawns `uv run idiograph serve` as a subprocess and drives it as a client.
#
# What tests/test_mcp_server.py cannot cover is exactly this — that the tools are
# reachable through a transport at all. It calls the dispatch functions directly;
# this launches the server the CLI actually starts and speaks MCP to it.
#
# The five-tool surface (IDG-109). `update_node` and `execute_graph` are not
# called here and are not called anywhere: mutation is gone because the repo is
# the authority, and execution lives at the CLI composition root.

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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


async def main():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "idiograph", "serve"],
    )

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

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

        r = await session.call_tool(
            "read_record", {"select": "path", "path": ["seeds"]}
        )
        print(f"\nread_record (path=['seeds']):\n{r.content[0].text}")

    print("\nSmoke test passed.")


asyncio.run(main())
