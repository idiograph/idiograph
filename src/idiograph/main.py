# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import asyncio
import json
import os
from pathlib import Path

import typer
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError

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

# Imported at module level, unlike the arxiv PIPELINE and the MCP stack below:
# `SeedSpec` declares a field of this type, and a model whose annotation is a
# deferred import is a model that has to be rebuilt before it can validate
# anything. `models` is pure pydantic declarations — no I/O, no `load_dotenv`,
# and pydantic itself is already paid for by `idiograph.core` above.
from idiograph.domains.arxiv.models import (
    PipelineParameters,
    parse_contract_hash,
    traversal_contract_hash,
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
def serve(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="Transport to mount: 'stdio' (the default) or 'http'.",
    ),
    host: str = typer.Option(
        None,
        "--host",
        help="--transport http: bind address. Defaults to 127.0.0.1 (loopback).",
    ),
    port: int = typer.Option(
        None,
        "--port",
        help="--transport http: bind port. Defaults to 8765.",
    ),
    # `str`, not `Path`, matching `validate(path: str)` above — the paths this
    # CLI takes are strings at the boundary and become `Path` inside the body.
    registry_root: str = typer.Option(
        None,
        "--registry-root",
        help="Registry root to serve records from. Defaults to the packaged "
             "demo registry. Mirrors the viewer CLI's flag of the same name.",
    ),
):
    """Start the Idiograph MCP server over stdio (default) or streamable HTTP.

    The server takes no graph. Under IDG-109 the served surface is a read-only
    projection resolved per request from the repo, so there is nothing for the
    composition root to hand it and nothing for it to hold. Imported here rather
    than at module level so the other commands do not pay for the MCP stack and
    the arxiv pipeline import on every CLI invocation.

    The transports JOIN rather than replace: a bare `idiograph serve` is stdio,
    unchanged, and HTTP is reached only by asking for it. The HTTP mount is
    STATELESS — the surface has no session state to keep, because every served
    thing is a projection of a durable artifact — and binds loopback unless
    `--host` widens it, which is an explicit operator act.

    `--host`, `--port` and `--registry-root` default to None rather than to
    literals so that the defaults live in one place, `mcp_server`, which resolves
    them. `--registry-root` names WHICH records the surface serves — the packaged
    demo registry unless the operator points it at their own durable one — and is
    spelled exactly as the viewer CLI spells it, because it selects the same
    thing for the same reason.
    """
    from idiograph.mcp_server import TRANSPORT_HTTP, TRANSPORT_STDIO
    from idiograph.mcp_server import main as mcp_main

    # Checked here so a typo is a CLI usage error rather than a traceback, and
    # so it is caught before anything is mounted. `mcp_server.main` rejects the
    # same value on its own account, for callers that are not this CLI.
    if transport not in (TRANSPORT_STDIO, TRANSPORT_HTTP):
        raise typer.BadParameter(
            f"{transport!r} — expected {TRANSPORT_STDIO!r} or {TRANSPORT_HTTP!r}",
            param_hint="--transport",
        )
    mcp_main(
        transport=transport,
        host=host,
        port=port,
        registry_root=None if registry_root is None else Path(registry_root),
    )


# ── freeze: record a corpus from a seed spec ─────────────────────────────────
#
# A CLI VERB, NOT A SERVED ROUTE (IDG-109 clause 3). Freezing costs real OpenAlex
# and Anthropic calls and writes a durable artifact; the served surface is a
# read-only projection and gains no execution trigger from any of this. The
# operator triggers a freeze at their own terminal, with their own keys.


class SeedSpec(BaseModel):
    """A freeze request as a file: what to seed, what to call it, how to run it.

    THE WHOLE INPUT TO ``freeze``, and deliberately the only one — the command
    takes a path and nothing else that could ride into the content address. A run
    is then reproducible from a file under review rather than from a set of flags
    somebody remembered to repeat, and two operators handing each other a spec are
    handing each other the same address.

    ``seeds`` are request dicts exactly as Node 0 takes them; ``label`` is free
    text stored beside the record; ``parameters`` is a FULL ``PipelineParameters``
    dump. Frozen, like the models it carries.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    seeds: list[dict]
    parameters: PipelineParameters


def load_seed_spec(path: Path) -> SeedSpec:
    """Read and validate one seed spec. Every failure here is a USAGE error.

    Three checks. First, the file must validate — a spec omitting a field
    ``PipelineParameters`` requires is refused rather than silently defaulted,
    because a defaulted field is a value the operator did not state that
    nonetheless enters the content address.

    Second, the parameters block must ROUND-TRIP: the dump of what was validated
    must equal what the file said, key for key. That refuses a typo'd field name,
    which pydantic would otherwise ignore — leaving the real field at a default
    the operator never stated, and moving the address away from the one the spec
    describes.

    Third, the two DERIVED hashes must match the tree. ``parse_contract_hash``
    and ``traversal_contract_hash`` are ``default_factory`` fields computed from
    the code, and a spec pins whatever they were when it was written; a stale one
    round-trips perfectly and would freeze a record whose ``parameters`` block
    claims a contract this tree does not implement. Refusing costs an operator a
    loud failure at a filename; accepting costs them a record that lies about its
    own derivation.

    Every one of these is a lie the spec would otherwise tell about the run it
    names, and the whole point of a spec is that it does not.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"{path}: {exc}", param_hint="SPEC") from exc

    try:
        spec = SeedSpec.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(f"{path}:\n{exc}", param_hint="SPEC") from exc

    stated = payload.get("parameters")
    resolved = spec.parameters.model_dump(mode="json")
    if stated != resolved:
        differing = sorted(
            key
            for key in set(stated) | set(resolved)
            if stated.get(key) != resolved.get(key)
        )
        raise typer.BadParameter(
            f"{path}: 'parameters' is not a full PipelineParameters dump — "
            f"{differing} differ between what the file states and what the model "
            "resolves. A spec whose parameters do not round-trip names an "
            "address the run would not take.",
            param_hint="SPEC",
        )

    stale = {
        field: (resolved[field], live)
        for field, live in (
            ("parse_contract_hash", parse_contract_hash()),
            ("traversal_contract_hash", traversal_contract_hash()),
        )
        if resolved[field] != live
    }
    if stale:
        raise typer.BadParameter(
            f"{path}: derived contract hashes are stale — "
            + "; ".join(
                f"{field} pins {pinned} but this tree derives {live}"
                for field, (pinned, live) in sorted(stale.items())
            )
            + ". These are computed from the code, so a pinned value the tree no "
            "longer produces would freeze a record claiming a contract that does "
            "not exist. Re-derive the spec's parameters against this tree.",
            param_hint="SPEC",
        )
    return spec


async def run_freeze(
    spec: SeedSpec,
    *,
    registry,
    client,
    api_key: str,
    anthropic_client,
) -> tuple[str, bool]:
    """Run ``spec`` through the production cached path. Returns (address, hit).

    THE TESTABLE SEAM. Every network client is injected, so the whole of what
    ``freeze`` does — spec to record to request sidecar, and the HIT/MISS the
    operator reads off the terminal — is exercisable against a tmp_path registry
    with mocked clients. What is left above this function is the wiring that
    builds real clients from real credentials, which is the part no test should
    pretend to cover.

    ``registry``, ``client`` and ``anthropic_client`` are deliberately unannotated:
    naming their types would pull ``httpx``, ``anthropic`` and the arxiv registry
    to module level, which is the import cost every other command in this file is
    written to avoid. What they must be is stated by the one call they are handed
    to, ``cached_run_arxiv_pipeline``, which does declare them.

    HIT is observed, not asked for. The address is not knowable before
    resolution, so the records already under the root are enumerated first and
    the answer is whether the address the run produced was among them. That costs
    one local glob and, unlike a pre-check resolve, no OpenAlex call the run does
    not already make.

    The request sidecar is stamped by the registry store, from ``spec.label`` and
    the same ``spec.seeds`` passed to the pipeline — on either leg, so a HIT on a
    record frozen without one records it, and a HIT under a DIFFERENT label
    raises rather than overwriting what the record already claims.
    """
    from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
    from idiograph.domains.arxiv.registry import address_of, is_record

    root = registry.root
    already = (
        {path.stem for path in root.glob("*.json") if is_record(path)}
        if root.exists()
        else set()
    )
    result = await cached_run_arxiv_pipeline(
        spec.seeds,
        spec.parameters,
        client=client,
        api_key=api_key,
        registry=registry,
        anthropic_client=anthropic_client,
        request_label=spec.label,
    )
    address = address_of(result)
    return address, address in already


@app.command()
def freeze(
    spec_path: str = typer.Argument(
        ...,
        help="Path to a seed-spec JSON file: {label, seeds, parameters}.",
    ),
):
    """Record a corpus named by a seed spec into the durable registry.

    ONE argument, and nothing else that rides into the content address. The spec
    states the seeds and the full parameters block, so the address this produces
    is a function of a file under review — re-run it and you get the same address
    or a loud failure, never a quietly different corpus.

    A second invocation on the same spec HITs: the cache's own contract, so it
    resolves the seeds, finds the record already at its address, draws nothing and
    writes nothing. That is why this is safe to re-run and why it is not a
    re-freeze command.

    Costs real money on a MISS — live OpenAlex traversal and, with `llm` set, live
    Anthropic draws. Requires ANTHROPIC_API_KEY and OPENALEX_API_KEY (env or the
    `.env` the app callback already loads).
    """
    import httpx
    from anthropic import AsyncAnthropic

    from idiograph.domains.arxiv.registry import (
        PipelineRegistry,
        durable_registry_root,
    )

    loaded = load_seed_spec(Path(spec_path))

    openalex_key = (os.environ.get("OPENALEX_API_KEY") or "").strip()
    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    missing = [
        name
        for name, value in (
            ("OPENALEX_API_KEY", openalex_key),
            ("ANTHROPIC_API_KEY", anthropic_key),
        )
        if not value
    ]
    # Both, even though a HIT draws no model: which leg this run takes is not
    # knowable until the seeds resolve, and discovering a missing Anthropic key
    # AFTER a MISS has begun traversing wastes the OpenAlex leg of a run that
    # cannot finish.
    if missing:
        raise typer.BadParameter(
            f"{', '.join(missing)} not set (env or .env).",
            param_hint="environment",
        )

    root = durable_registry_root()
    registry = PipelineRegistry(root)
    anthropic_client = AsyncAnthropic(api_key=anthropic_key)

    async def _go() -> tuple[str, bool]:
        # OpenAlex is slow enough on deep traversal that httpx's 5s default can
        # spuriously fail a call (finding 8a6e6be4); the production path owns no
        # timeout of its own, so the composition root sets one.
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            try:
                return await run_freeze(
                    loaded,
                    registry=registry,
                    client=http_client,
                    api_key=openalex_key,
                    anthropic_client=anthropic_client,
                )
            finally:
                await anthropic_client.close()

    address, hit = asyncio.run(_go())
    typer.echo(
        json.dumps(
            {
                "address": address,
                "registry_root": str(root),
                "result": "HIT" if hit else "MISS",
                "record": str(registry.path_for(address)),
                "request": str(registry.request_path_for(address)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
