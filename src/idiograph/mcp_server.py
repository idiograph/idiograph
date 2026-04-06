# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import json
import logging

from mcp.server import Server
from mcp import stdio_server, types

from idiograph.core import (
    get_node,
    get_edges_from,
    validate_integrity,
    summarize_intent,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Graph
from idiograph.core.graph import load_graph
from idiograph.core.pipeline import SAMPLE_PIPELINE
from idiograph.core.logging_config import get_logger

logger = get_logger("mcp_server")


# ── Session-scoped graph state ────────────────────────────────────────────────
# stdio transport: one client, one process, one graph for the session lifetime.
# AMD-009: module-level state is a documented constraint; forcing function not
# met for stdio. Revisit if HTTP/SSE transport is added.

_graph: Graph | None = None


def _get_graph() -> Graph:
    if _graph is None:
        raise RuntimeError("Graph not initialized. Call init_graph() before serving.")
    return _graph


def init_graph(graph: Graph) -> None:
    global _graph
    _graph = graph


# ── Server ────────────────────────────────────────────────────────────────────

app = Server("idiograph")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_node",
            description="Return a single node by ID. Includes type, params, status, and port declarations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "The node ID to retrieve."}
                },
                "required": ["node_id"],
            },
        ),
        types.Tool(
            name="get_edges_from",
            description="Return all outgoing edges from a node.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "The source node ID."}
                },
                "required": ["node_id"],
            },
        ),
        types.Tool(
            name="update_node",
            description="Update the params dict of a node in-place. Merges supplied key/value pairs into existing params.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "The node ID to update."},
                    "params": {"type": "object", "description": "Key/value pairs to merge into the node's params."},
                },
                "required": ["node_id", "params"],
            },
        ),
        types.Tool(
            name="summarize_intent",
            description=(
                "Return a structured semantic summary of the graph or a subgraph. "
                "Purely algorithmic — no LLM calls. Answers: what does this do and where might it fail?"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of node IDs to scope the summary. Omit for the full graph.",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="validate_graph",
            description="Check referential integrity of the graph. Returns valid (bool) and a list of errors.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="execute_graph",
            description="Run the full pipeline in topological order. Returns per-node execution results.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    graph = _get_graph()

    if name == "get_node":
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError("get_node requires 'node_id'")
        node = get_node(graph, node_id)
        result = node.model_dump() if node else {"error": f"Node '{node_id}' not found."}

    elif name == "get_edges_from":
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError("get_edges_from requires 'node_id'")
        edges = get_edges_from(graph, node_id)
        result = [e.model_dump() for e in edges]

    elif name == "update_node":
        node_id = arguments.get("node_id")
        params = arguments.get("params", {})
        if not node_id:
            raise ValueError("update_node requires 'node_id'")
        node = get_node(graph, node_id)
        if node is None:
            result = {"error": f"Node '{node_id}' not found."}
        else:
            node.params.update(params)
            result = {"updated": node_id, "params": node.params}

    elif name == "summarize_intent":
        node_ids = arguments.get("node_ids") or None
        result = summarize_intent(graph, node_ids)

    elif name == "validate_graph":
        result = validate_integrity(graph)

    elif name == "execute_graph":
        result = await execute_graph(graph)

    else:
        raise ValueError(f"Unknown tool: {name}")

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ── Entry point ───────────────────────────────────────────────────────────────

async def serve(graph: Graph) -> None:
    init_graph(graph)
    logger.info("Idiograph MCP server starting (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main(graph: Graph) -> None:
    asyncio.run(serve(graph))