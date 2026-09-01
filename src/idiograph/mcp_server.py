# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""The served MCP surface — a READ-ONLY PROJECTION of durable artifacts (IDG-109).

WHAT IS SERVED. Two things, and they describe ONE run:

  - THE DECLARATION. ``build_pipeline_graph`` resolved over the packaged frozen
    CRISPR record's own arguments — its ``parameters`` block and the seed set it
    was triggered with. Eleven nodes, 21 edges: the citation-traversal pipeline
    as a declared ``Graph``.
  - THE RECORD. The packaged ``PipelineResult`` that run produced, read back
    through the address-verifying registry path.

The declaration is NOT a claim that this ``Graph`` was executed to produce the
record: ``run_traversal`` produced it, and the graph is the declaration that
transcribes that orchestrator (``pipeline_graph``'s own docstring says so). What
the pairing does claim is narrower and true — the declaration served here is the
declaration of the pipeline whose record this is, configured identically. That
is why the resolver reads the record's parameters rather than the viewer's inert
arguments: zeroed values would tell a client ``n_backward: 0`` and ``llm: None``
beside a record that says otherwise.

NO AUTHORITATIVE IN-PROCESS STATE. There is no module-level graph and no
initializer. Every request resolves its own ``Graph`` through the pure
constructor, so nothing a request can reach outlives it, and two requests never
share a mutable graph — which matters because the executor mutates
``Node.status`` in place. The record READ is memoized, keyed by content address:
a projection of a durable artifact under a key derived from its content is a
cache, not state. No tool call writes it, re-freezes anything, or touches the
network.

WHY THERE IS NO ``update_node`` AND NO ``execute_graph``. Both were deleted
under IDG-109. The repo is the authority on what the graph is, so a served
mutation would have nowhere durable to land; and execution lives at the CLI
composition root (``main._execute_live``), which is where the handlers are
registered. Serving ``execute_graph`` over a graph with no registered handler for
any of its node types is what finding f52487fa reported — the tool raised on the
only graph it was ever given. Removal is the discharge.

TRANSPORT. Two, and they are two ways to reach ONE surface. ``serve()`` mounts
stdio; ``serve_http()`` mounts the SDK's ``StreamableHTTPSessionManager`` at
``/mcp`` inside an ASGI app under uvicorn. Both mount the same module-level
``app`` — there is no second ``Server``, no duplicated registration, and no
transport-conditional tool set, so the five tools, their schemas and their
result shapes are identical whichever way a client connects (IDG-109 clause 3).
The promise the previous paragraph of this docstring made — that a second
transport could mount the same ``app`` without touching anything above — is
what the section at the foot of this module collects on: nothing between here
and there changed to admit it.

The HTTP mount is STATELESS. The SDK's stateful mode keeps an in-memory
session→transport registry, which would be the only mutable in-process state on
the surface, and clause 4 says there is none: every served thing is a projection
of a durable artifact, so there is no session for a client to have. A stateless
mount also survives a restart with nothing to lose. The cost — no
server-initiated notifications, no stream resumability — is not a cost here:
every tool answers one bounded question with one complete result.
"""

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from mcp import stdio_server, types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from idiograph.core import (
    get_edges_from,
    get_node,
    summarize_intent,
    validate_integrity,
)
from idiograph.core.logging_config import get_logger
from idiograph.core.models import Graph
from idiograph.demo import (
    FROZEN_CRISPR_SEEDS,
    REGISTRY_ROOT,
    frozen_crispr_address,
)
from idiograph.domains.arxiv.models import PipelineResult
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph
from idiograph.domains.arxiv.registry import PipelineRegistry

logger = get_logger("mcp_server")

RECORD_TOOL = "read_record"

#: A content address is a sha256 hex digest and nothing else. Matching the shape
#: before touching the filesystem is what keeps a client-supplied string out of
#: path construction: ``../`` and absolute paths cannot pass this, so an address
#: the registry does not hold is answered as a miss rather than as a read.
_ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Ceiling on one record response's serialized text. The record is ~9.3 MB with
#: 1,885 nodes and 14,852 edges; no single call may hand back the dump, so an
#: over-sized selection is refused with a structured error naming the two knobs
#: that narrow it. This is the mechanical form of the read-only-projection rule:
#: a client composes the record out of bounded windows or not at all.
_MAX_RESPONSE_BYTES = 131_072

#: Window defaults for a path read. Small enough that the default call is cheap,
#: large enough to be useful; the byte ceiling above is the real bound.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 200


class _RecordMiss(LookupError):
    """A record read that missed — an unknown address, path, node or edge.

    Carries the message the tool returns as ``{"error": ...}``. A miss is DATA,
    not a protocol fault: the client asked a well-formed question about
    something that is not there, and gets a structured answer. Malformed
    arguments still raise ``ValueError``, as they do on the four graph tools.
    """


# ── Resolution: the repo is the authority ─────────────────────────────────────


@lru_cache(maxsize=2)
def _read_record(address: str) -> PipelineResult:
    """Read one packaged record, memoized by its content address.

    Goes through :meth:`PipelineRegistry.read`, which re-supplies the excluded
    cycle witness and verifies that what came off disk addresses to what was
    asked for. Memoizing is sound precisely because the key is the content
    address: two calls with the same key cannot be answered differently by a
    content-addressed store, and ``PipelineResult`` is frozen.
    """
    return PipelineRegistry(REGISTRY_ROOT).read(address)


@lru_cache(maxsize=2)
def _record_json(address: str) -> dict[str, Any]:
    """The record's JSON projection, memoized alongside the record itself.

    ``model_dump(mode="json")`` over 9.3 MB is not something to repeat per
    request. Callers slice this mapping and never mutate it.
    """
    return _read_record(address).model_dump(mode="json")


def _resolve_address(address: str | None) -> str:
    """The address a record call names, defaulting to the packaged record's.

    Never hand-authored: the default is derived from the packaged registry's
    sole filename. A supplied address must both look like an address and be held
    by the registry, or the call is a miss.
    """
    if address is None:
        return frozen_crispr_address()
    if not _ADDRESS_PATTERN.match(address):
        raise _RecordMiss(
            f"Address '{address}' is not a content address "
            "(64 lowercase hex characters)."
        )
    if not PipelineRegistry(REGISTRY_ROOT).path_for(address).is_file():
        raise _RecordMiss(f"The registry holds no record at address '{address}'.")
    return address


def resolve_graph() -> Graph:
    """The served graph: the declaration of the pipeline whose record this is.

    A FRESH ``Graph`` every call, deliberately — ``build_pipeline_graph``'s own
    contract, and the reason there is no module-level graph here. The seed dicts
    are copied on the way in so that two resolutions share no mutable structure
    at all, not merely no shared ``Node``.
    """
    parameters = _read_record(frozen_crispr_address()).parameters
    return build_pipeline_graph(
        [dict(seed) for seed in FROZEN_CRISPR_SEEDS], parameters
    )


# ── The record read ───────────────────────────────────────────────────────────


def _window(arguments: dict) -> tuple[int, int]:
    """The (offset, limit) window a path read is bounded by."""
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", _DEFAULT_LIMIT)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"{RECORD_TOOL} 'offset' must be a non-negative integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"{RECORD_TOOL} 'limit' must be between 1 and {_MAX_LIMIT}")
    return offset, limit


def _descend(payload: dict[str, Any], path: list[str]) -> Any:
    """Walk ``path`` into the record's JSON projection.

    Segments are strings because the record's own keys are node_ids and DOIs —
    ``doi:10.1126/science.1225829`` contains both dots and slashes, so a dotted
    string path could not name them. Into a list, a segment is its index.
    """
    value: Any = payload
    for depth, segment in enumerate(path):
        if isinstance(value, dict):
            if segment not in value:
                raise _RecordMiss(
                    f"No key '{segment}' at path {path[:depth]!r} in the record."
                )
            value = value[segment]
        elif isinstance(value, list):
            try:
                index = int(segment)
            except ValueError:
                raise _RecordMiss(
                    f"Path segment '{segment}' indexes a list at {path[:depth]!r} "
                    "and must be an integer."
                ) from None
            if not -len(value) <= index < len(value):
                raise _RecordMiss(
                    f"Index {index} is out of range at path {path[:depth]!r} "
                    f"(length {len(value)})."
                )
            value = value[index]
        else:
            raise _RecordMiss(
                f"Path {path[: depth + 1]!r} descends into a scalar value."
            )
    return value


def _shape(address: str, payload: dict[str, Any]) -> dict[str, Any]:
    """What the record IS, without any of its bulk.

    The address it served, the resolved seed node_ids, the parameters block that
    (with those seeds) derives that address, and a count per top-level field. A
    client reads this first and then asks for the part it wants.
    """
    fields = {}
    for name, value in payload.items():
        if isinstance(value, list):
            fields[name] = {"kind": "list", "count": len(value)}
        elif isinstance(value, dict):
            fields[name] = {"kind": "object", "count": len(value)}
        else:
            fields[name] = {"kind": "value"}
    return {
        "address": address,
        "select": "shape",
        "seeds": payload["seeds"],
        "parameters": payload["parameters"],
        "fields": fields,
    }


def _path_read(
    address: str, payload: dict[str, Any], path: list[str], offset: int, limit: int
) -> dict[str, Any]:
    """One windowed slice of the record at ``path``."""
    value = _descend(payload, path)
    result = {"address": address, "select": "path", "path": path}
    if isinstance(value, list):
        return result | {
            "kind": "list",
            "count": len(value),
            "offset": offset,
            "limit": limit,
            "items": value[offset : offset + limit],
        }
    if isinstance(value, dict):
        keys = sorted(value)[offset : offset + limit]
        return result | {
            "kind": "object",
            "count": len(value),
            "offset": offset,
            "limit": limit,
            "items": {key: value[key] for key in keys},
        }
    return result | {"kind": "value", "value": value}


def _node_read(
    address: str, payload: dict[str, Any], node_id: str
) -> dict[str, Any]:
    """One ``PaperRecord`` by its node_id."""
    for node in payload["nodes"]:
        if node["node_id"] == node_id:
            return {
                "address": address,
                "select": "node",
                "node_id": node_id,
                "node": node,
            }
    raise _RecordMiss(f"The record holds no node '{node_id}'.")


def _edge_read(
    address: str, payload: dict[str, Any], source_id: str, target_id: str
) -> dict[str, Any]:
    """The edges between one ordered pair of node_ids.

    A ``CitationEdge`` carries no id of its own, so its identity is its
    endpoints. The match is DIRECTED and over the merged ``edges`` list, which
    holds both ``cites`` and ``co_citation`` types — a pair can therefore answer
    with more than one edge, and the list is returned rather than a first hit.
    """
    edges = [
        edge
        for edge in payload["edges"]
        if edge["source_id"] == source_id and edge["target_id"] == target_id
    ]
    if not edges:
        raise _RecordMiss(
            f"The record holds no edge from '{source_id}' to '{target_id}'."
        )
    return {
        "address": address,
        "select": "edge",
        "source_id": source_id,
        "target_id": target_id,
        "count": len(edges),
        "edges": edges,
    }


def _read_record_tool(arguments: dict) -> dict[str, Any] | list[Any]:
    """Dispatch one ``read_record`` call. Reads only; writes nothing."""
    select = arguments.get("select", "shape")
    address = _resolve_address(arguments.get("address"))
    payload = _record_json(address)

    if select == "shape":
        result = _shape(address, payload)
    elif select == "path":
        path = arguments.get("path")
        if not isinstance(path, list) or not path:
            raise ValueError(f"{RECORD_TOOL} select='path' requires a non-empty 'path'")
        offset, limit = _window(arguments)
        result = _path_read(address, payload, [str(p) for p in path], offset, limit)
    elif select == "node":
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError(f"{RECORD_TOOL} select='node' requires 'node_id'")
        result = _node_read(address, payload, node_id)
    elif select == "edge":
        source_id = arguments.get("source_id")
        target_id = arguments.get("target_id")
        if not source_id or not target_id:
            raise ValueError(
                f"{RECORD_TOOL} select='edge' requires 'source_id' and 'target_id'"
            )
        result = _edge_read(address, payload, source_id, target_id)
    else:
        raise ValueError(
            f"{RECORD_TOOL} 'select' must be one of "
            f"'shape', 'path', 'node', 'edge' — got {select!r}"
        )

    size = len(json.dumps(result, indent=2))
    if size > _MAX_RESPONSE_BYTES:
        raise _RecordMiss(
            f"Selection is {size} bytes, over the {_MAX_RESPONSE_BYTES}-byte "
            "response ceiling. Narrow it with a deeper 'path' or a smaller "
            "'limit'."
        )
    return result


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
            name=RECORD_TOOL,
            description=(
                "Read the packaged, content-addressed pipeline record the served "
                "graph declares — execution STATE, with no execution trigger. "
                "Read-only and offline. The record is far too large to return "
                "whole, so every call is a bounded selection: 'shape' (the "
                "default) gives the address, the resolved seeds, the parameters "
                "and a count per top-level field; 'path' walks into any field and "
                "returns one offset/limit window of it; 'node' returns one paper "
                "by node_id; 'edge' returns the citation edges between an ordered "
                "pair of node_ids. Every response names the address it served."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": (
                            "Content address of the record to read. Defaults to "
                            "the packaged frozen CRISPR record. An address the "
                            "registry does not hold returns an 'error' field."
                        ),
                    },
                    "select": {
                        "type": "string",
                        "enum": ["shape", "path", "node", "edge"],
                        "description": "What to select. Defaults to 'shape'.",
                    },
                    "path": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "select='path': keys from the record root, one per "
                            "segment — ['nodes'], ['cycle_clean', 'cleaned_edges'], "
                            "['pagerank', 'doi:10.1126/science.1225829']. A segment "
                            "indexing a list is its integer index as a string. "
                            "Segments are a list, not a dotted string, because "
                            "record keys contain dots and slashes."
                        ),
                    },
                    "node_id": {
                        "type": "string",
                        "description": "select='node': the paper's node_id.",
                    },
                    "source_id": {
                        "type": "string",
                        "description": "select='edge': node_id of the edge's source.",
                    },
                    "target_id": {
                        "type": "string",
                        "description": "select='edge': node_id of the edge's target.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "select='path': window start. Defaults to 0.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_LIMIT,
                        "description": (
                            f"select='path': window size, 1..{_MAX_LIMIT}. "
                            f"Defaults to {_DEFAULT_LIMIT}."
                        ),
                    },
                },
                "required": [],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # Resolved per request, never held: the repo is the authority and nothing
    # served outlives the call that resolved it.
    if name == "get_node":
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError("get_node requires 'node_id'")
        node = get_node(resolve_graph(), node_id)
        result = node.model_dump() if node else {"error": f"Node '{node_id}' not found."}

    elif name == "get_edges_from":
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError("get_edges_from requires 'node_id'")
        edges = get_edges_from(resolve_graph(), node_id)
        result = [e.model_dump() for e in edges]

    elif name == "summarize_intent":
        node_ids = arguments.get("node_ids") or None
        result = summarize_intent(resolve_graph(), node_ids)

    elif name == "validate_graph":
        result = validate_integrity(resolve_graph())

    elif name == RECORD_TOOL:
        try:
            result = _read_record_tool(arguments)
        except _RecordMiss as miss:
            result = {"error": str(miss)}

    else:
        raise ValueError(f"Unknown tool: {name}")

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ── Transports ────────────────────────────────────────────────────────────────

#: The two transport names, spelled once. The CLI's `--transport` value, the
#: switch in `main` below and the smoke script all source them from here rather
#: than retyping the strings.
TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "http"

#: Where the HTTP transport answers. ONE mount, one path. The ASGI app is a
#: router rather than the session manager bare, so that a second leg — the color
#: designer's SSE broadcast, which shares this host under goal 132a4c55 — can be
#: mounted beside this route later without moving it. That route is NOT built
#: here. Note the canonical URL carries a trailing slash: `Mount` answers
#: `/mcp/` and 307s the bare `/mcp` onto it.
HTTP_PATH = "/mcp"

#: Default bind for the HTTP transport: LOOPBACK, deliberately. A `serve` that
#: selects HTTP without naming a host must not be reachable from another
#: machine. Widening the bind is an explicit operator act, and it is that act —
#: not a flag of its own — that relaxes the Host allowlist below.
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765

#: Bind addresses that mean "this machine only". Compared against what the
#: operator asked to bind, which is why `::1` appears here unbracketed while the
#: Host-header spellings below bracket it — a bind address and a Host header are
#: different notations for the same interface.
_LOOPBACK_BINDS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Host-header spellings that all name the loopback interface. A client may
#: reach the default bind by any of them, so an allowlist derived from the bound
#: port carries all three; one that carried only `127.0.0.1` would refuse a
#: legitimate local client for spelling it `localhost`.
_LOOPBACK_HOST_HEADERS = ("127.0.0.1", "localhost", "[::1]")

#: Responses are complete JSON documents, not SSE frames. Nothing on this
#: surface streams: every tool answers one bounded question with one result
#: under `_MAX_RESPONSE_BYTES`, and a stateless mount has no server-initiated
#: notification to deliver anyway. SSE framing would wrap a single message in a
#: stream that never has a second one.
_JSON_RESPONSE = True


def _transport_security(host: str, port: int) -> TransportSecuritySettings:
    """DNS-rebinding protection that FOLLOWS THE BIND.

    On the default loopback bind the protection is ON, and the allowlist is
    derived from the port actually bound rather than hand-authored — the attack
    it answers is a browser on this machine being steered to this port by a
    rebound name, and the defense is to accept only the Host headers that spell
    the loopback interface.

    On a bind the operator WIDENED it is off, with a line in the log saying so.
    This is not a silent downgrade: a loopback-derived allowlist left in place
    over `0.0.0.0` would refuse every remote client by Host header — refusing
    exactly the hosts the operator just opened — and no allowlist can be derived
    from a wildcard bind, because the legitimate Host header is then whatever
    name the client used to reach this machine. Restricting a widened bind is
    the reverse proxy's job, not this process's.
    """
    if host in _LOOPBACK_BINDS:
        allowed_hosts = [f"{name}:{port}" for name in _LOOPBACK_HOST_HEADERS]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"http://{name}" for name in allowed_hosts],
        )
    logger.warning(
        "Bind %s is not loopback — DNS-rebinding Host/Origin checks are off, "
        "since the legitimate Host header cannot be derived from a widened "
        "bind. Restrict access ahead of this process.",
        host,
    )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def http_session_manager(
    host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT
) -> StreamableHTTPSessionManager:
    """The streamable-HTTP session manager for the module-level ``app``.

    NOT a server: it is a transport in front of the one ``app`` this module
    registers its tools on. Stateless for the reason the module docstring gives,
    which is also why it holds nothing worth reusing — a caller builds one per
    mount rather than sharing a module-level instance.
    """
    return StreamableHTTPSessionManager(
        app,
        stateless=True,
        json_response=_JSON_RESPONSE,
        security_settings=_transport_security(host, port),
    )


def build_http_app(manager: StreamableHTTPSessionManager) -> Starlette:
    """The ASGI app mounting ``manager`` at :data:`HTTP_PATH`.

    Takes the manager rather than building one so that both callers compose the
    same two pieces. ``serve_http`` hands the app to uvicorn, which runs the
    lifespan below; the in-process transport tests drive this same app through
    ``httpx.ASGITransport``, which does NOT run a lifespan, and so enter
    ``manager.run()`` themselves. Those are the only two ways in, and neither
    reaches a tool by a path the other does not.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(
        routes=[Mount(HTTP_PATH, app=manager.handle_request)], lifespan=lifespan
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def serve() -> None:
    logger.info("Idiograph MCP server starting (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def serve_http(host: str | None = None, port: int | None = None) -> None:
    """Serve the surface over streamable HTTP, blocking until the process stops.

    ``None`` for either bind component means the loopback default, so that the
    defaults live at :data:`DEFAULT_HTTP_HOST` / :data:`DEFAULT_HTTP_PORT` alone
    and the CLI does not carry a second copy of them.
    """
    # Imported here, not at module level, because uvicorn is needed only where a
    # socket is actually opened. The transport tests build the very same ASGI
    # app and drive it in-process, and nothing on that path binds anything.
    import uvicorn

    host = DEFAULT_HTTP_HOST if host is None else host
    port = DEFAULT_HTTP_PORT if port is None else port
    logger.info(
        "Idiograph MCP server starting (streamable HTTP, stateless) at http://%s:%d%s/",
        host,
        port,
        HTTP_PATH,
    )
    # `log_config=None`: uvicorn's default config would reconfigure the root
    # logger that the CLI's startup callback already set up.
    uvicorn.run(
        build_http_app(http_session_manager(host, port)),
        host=host,
        port=port,
        log_config=None,
    )


def main(
    transport: str = TRANSPORT_STDIO,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Run the served surface over one transport. THE DEFAULT IS STDIO.

    A no-argument call is exactly what it was before HTTP existed, which is the
    whole of the compatibility promise: a client that spawns this process and
    speaks over the pipe sees no change. HTTP is selected, never fallen into.
    """
    if transport == TRANSPORT_STDIO:
        asyncio.run(serve())
    elif transport == TRANSPORT_HTTP:
        serve_http(host, port)
    else:
        raise ValueError(
            f"Unknown transport: {transport!r} — "
            f"expected {TRANSPORT_STDIO!r} or {TRANSPORT_HTTP!r}"
        )
