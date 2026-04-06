# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "idiograph", "serve"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"Tools discovered: {tool_names}")
            assert set(tool_names) == {
                "get_node", "get_edges_from", "update_node",
                "summarize_intent", "validate_graph", "execute_graph"
            }

            r = await session.call_tool("get_node", {"node_id": "node_01"})
            print(f"\nget_node:\n{r.content[0].text}")

            r = await session.call_tool("get_edges_from", {"node_id": "node_01"})
            print(f"\nget_edges_from:\n{r.content[0].text}")

            r = await session.call_tool("update_node", {"node_id": "node_01", "params": {"asset_path": "/assets/updated.usd"}})
            print(f"\nupdate_node:\n{r.content[0].text}")

            r = await session.call_tool("summarize_intent", {})
            print(f"\nsummarize_intent:\n{r.content[0].text}")

            r = await session.call_tool("validate_graph", {})
            print(f"\nvalidate_graph:\n{r.content[0].text}")

            r = await session.call_tool("execute_graph", {})
            print(f"\nexecute_graph:\n{r.content[0].text}")

    print("\nSmoke test passed.")


asyncio.run(main())