# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import json
import os

import typer
from dotenv import load_dotenv
from pydantic import ValidationError

from idiograph.core import (
    SAMPLE_PIPELINE,
    load_config,
    load_graph,
    setup_logging,
    summarize,
)
from idiograph.core.executor import execute_graph
from idiograph.core.models import Graph
from idiograph.core.query import (
    find_cycles,
    get_downstream,
    get_upstream,
    summarize_intent,
    topological_sort,
    validate_integrity,
)

app = typer.Typer()
query_app = typer.Typer()
app.add_typer(query_app, name="query")


@app.callback()
def _startup():
    """Initialize logging and config before any command runs."""
    load_dotenv()
    config = load_config()
    setup_logging(config.get("log_level", "INFO"))


@app.command()
def stats():
    """Output pipeline statistics as JSON."""
    typer.echo(json.dumps(summarize(SAMPLE_PIPELINE), indent=2))


@app.command()
def workflows():
    """Output the full pipeline manifest as JSON."""
    typer.echo(SAMPLE_PIPELINE.model_dump_json(indent=2))


@app.command()
def validate(path: str):
    """Validate a graph JSON file against the idiograph schema."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        graph = load_graph(data)
        typer.echo(f"Valid — {len(graph.nodes)} nodes, {len(graph.edges)} edges.")
    except FileNotFoundError:
        typer.echo(f"Error: file not found: {path}")
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo("Validation failed:")
        typer.echo(e.json(indent=2))
        raise typer.Exit(1)


@app.command()
def check():
    """Run integrity and cycle checks on the default pipeline."""
    integrity = validate_integrity(SAMPLE_PIPELINE)
    cycles = find_cycles(SAMPLE_PIPELINE)
    result = {
        "integrity": integrity,
        "cycles_found": len(cycles) > 0,
        "cycles": cycles,
    }
    typer.echo(json.dumps(result, indent=2))


async def _execute_live(pipeline: Graph) -> dict:
    """Build the run's resources, own their lifecycle, execute the pipeline.

    This is the composition root, and the only place in the system that may
    construct a network client or read process environment. Handlers receive
    clients; they never build them, so what a handler can reach is exactly what
    its node declared.
    """
    import anthropic
    import httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set.")

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        return await execute_graph(
            pipeline,
            resources={
                "http_client": http_client,
                "anthropic_client": anthropic.AsyncAnthropic(api_key=api_key),
            },
        )


async def _execute_mock(pipeline: Graph) -> dict:
    """Execute with inert resource placeholders. Mocks construct nothing.

    The nodes declare, so the executor supplies whichever handlers are
    registered. `None` is the honest placeholder: present, so the pre-flight
    supply check passes, and unusable, so a stub that quietly reached for the
    network would crash rather than succeed.
    """
    return await execute_graph(
        pipeline,
        resources={"http_client": None, "anthropic_client": None},
    )


@app.command()
def run(
    paper_id: str = typer.Argument(..., help="arXiv paper ID, e.g. 2401.00001"),
    mock: bool = typer.Option(False, "--mock", help="Run with stub handlers. No API key or network access required."),
):
    """Execute the arXiv pipeline for a given paper ID."""
    from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE

    if mock:
        from idiograph.core.executor import register_handler
        from idiograph.domains.arxiv.mock_handlers import (
            mock_discard,
            mock_evaluator,
            mock_fetch_abstract,
            mock_llm_call,
            mock_llm_summarize,
        )
        register_handler("FetchAbstract", mock_fetch_abstract)
        register_handler("LLMCall",       mock_llm_call)
        register_handler("Evaluator",     mock_evaluator)
        register_handler("LLMSummarize",  mock_llm_summarize)
        register_handler("Discard",       mock_discard)
    else:
        from idiograph.domains.arxiv.handlers import register_arxiv_handlers
        register_arxiv_handlers()

    pipeline = ARXIV_PIPELINE.model_copy(deep=True)
    fetch_node = pipeline.get_node("fetch")
    if fetch_node:
        fetch_node.params["paper_id"] = paper_id
    results = asyncio.run(
        _execute_mock(pipeline) if mock else _execute_live(pipeline)
    )
    typer.echo(json.dumps(results, indent=2, default=str))


@query_app.command("downstream")
def query_downstream(node_id: str):
    """List all nodes reachable downstream from NODE_ID."""
    result = get_downstream(SAMPLE_PIPELINE, node_id)
    typer.echo(json.dumps({"node_id": node_id, "downstream": result}, indent=2))


@query_app.command("upstream")
def query_upstream(node_id: str):
    """List all nodes that are ancestors of NODE_ID."""
    result = get_upstream(SAMPLE_PIPELINE, node_id)
    typer.echo(json.dumps({"node_id": node_id, "upstream": result}, indent=2))


@query_app.command("topo")
def query_topo():
    """Output nodes in topological (execution) order."""
    try:
        result = topological_sort(SAMPLE_PIPELINE)
        typer.echo(json.dumps({"topological_order": result}, indent=2))
    except ValueError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(1)


@query_app.command("intent")
def query_intent():
    """Output a semantic intent summary of the default pipeline."""
    result = summarize_intent(SAMPLE_PIPELINE)
    typer.echo(json.dumps(result, indent=2))


@app.command()
def serve():
    """Start the Idiograph MCP server (stdio transport).

    The server takes no graph. Under IDG-109 the served surface is a read-only
    projection resolved per request from the repo, so there is nothing for the
    composition root to hand it and nothing for it to hold. Imported here rather
    than at module level so the other commands do not pay for the MCP stack and
    the arxiv pipeline import on every CLI invocation.
    """
    from idiograph.mcp_server import main as mcp_main

    mcp_main()

if __name__ == "__main__":
    app()
