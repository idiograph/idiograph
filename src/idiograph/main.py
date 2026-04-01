import json
import asyncio
import typer
from dotenv import load_dotenv
from idiograph.core import SAMPLE_PIPELINE, summarize, load_graph, load_config, setup_logging
from idiograph.core.executor import execute_graph
from idiograph.core.query import (
    get_downstream, get_upstream, topological_sort,
    find_cycles, validate_integrity, summarize_intent,
)
from pydantic import ValidationError
from idiograph.mcp_server import main as mcp_main

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


@app.command()
def run(
    paper_id: str = typer.Argument(..., help="arXiv paper ID, e.g. 2401.00001"),
    mock: bool = typer.Option(False, "--mock", help="Run with stub handlers. No API key or network access required."),
):
    """Execute the arXiv pipeline for a given paper ID."""
    from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE

    if mock:
        from idiograph.domains.arxiv.mock_handlers import (
            mock_fetch_abstract, mock_llm_call, mock_evaluator,
            mock_llm_summarize, mock_discard,
        )
        from idiograph.core.executor import register_handler
        register_handler("FetchAbstract", mock_fetch_abstract)
        register_handler("LLMCall",       mock_llm_call)
        register_handler("Evaluator",     mock_evaluator)
        register_handler("LLMSummarize",  mock_llm_summarize)
        register_handler("Discard",       mock_discard)
    else:
        from idiograph.domains.arxiv import register_all
        register_all()

    pipeline = ARXIV_PIPELINE.model_copy(deep=True)
    fetch_node = pipeline.get_node("fetch")
    if fetch_node:
        fetch_node.params["paper_id"] = paper_id
    results = asyncio.run(execute_graph(pipeline))
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
    """Start the Idiograph MCP server (stdio transport)."""
    from idiograph.core.pipeline import SAMPLE_PIPELINE
    from idiograph.core.graph import load_graph
    graph = load_graph(SAMPLE_PIPELINE.model_dump())
    mcp_main(graph)

if __name__ == "__main__":
    app()
