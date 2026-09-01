# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# The served MCP surface (IDG-109): a read-only projection of durable artifacts.
#
# There was no test module here before. The only coverage the surface had was a
# hand-run smoke script, which is why finding f52487fa — `execute_graph` raising
# on the only graph the server was ever given, because no handler is registered
# for any of its node types — survived in the tree unseen. This module is the
# standing form of that discharge: every tool on the surface answers, without
# raising, on the graph the server actually serves.
#
# OFFLINE BY CONSTRUCTION. Everything here reads the packaged frozen record off
# disk and resolves the declared graph through a pure constructor. No network, no
# credential, no execution.

import asyncio
import json

import pytest

from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address, load_frozen_crispr
from idiograph.domains.arxiv import pipeline_graph as pg
from idiograph.mcp_server import (
    _MAX_RESPONSE_BYTES,
    RECORD_TOOL,
    call_tool,
    list_tools,
    resolve_graph,
)

FIVE_TOOLS = {
    "get_node",
    "get_edges_from",
    "summarize_intent",
    "validate_graph",
    RECORD_TOOL,
}

# The eleven declared stages, taken from the constructor's own id constants
# rather than re-typed: a stage renamed there must move this list, not silently
# leave the surface untested on the node it renamed.
DECLARED_NODE_IDS = [
    pg.RESOLVE,
    pg.BACKWARD,
    pg.FORWARD,
    pg.ASSEMBLE,
    pg.CLEAN,
    pg.CO_CITATIONS,
    pg.ANNOTATE,
    pg.DEPTH,
    pg.PAGERANK,
    pg.COMMUNITIES,
    pg.ENRICH,
]


def call(name: str, arguments: dict | None = None):
    """Invoke one tool the way the transport does, and decode its text result.

    The lowlevel `Server` decorators return the undecorated function, so the
    dispatch under test here is the same coroutine stdio would call — no shim
    stands between this and the served surface.
    """
    contents = asyncio.run(call_tool(name, arguments or {}))
    assert len(contents) == 1
    assert contents[0].type == "text"
    return json.loads(contents[0].text)


@pytest.fixture(scope="module")
def record():
    return load_frozen_crispr()


# ── The surface is five tools ─────────────────────────────────────────────────


def test_exactly_five_tools_are_advertised() -> None:
    tools = asyncio.run(list_tools())
    assert {tool.name for tool in tools} == FIVE_TOOLS
    assert len(tools) == len(FIVE_TOOLS)


@pytest.mark.parametrize("removed", ["update_node", "execute_graph"])
def test_removed_tools_are_unknown(removed: str) -> None:
    """Mutation and remote execution are gone, not merely unadvertised.

    IDG-109 clauses 2 and 3: `update_node` had nowhere durable to land a write
    (the repo is the authority) and `execute_graph` belongs to the CLI
    composition root, where the handlers are registered. A client that knows the
    old names gets the unknown-tool error, not a silent no-op.
    """
    with pytest.raises(ValueError, match=f"Unknown tool: {removed}"):
        call(removed, {"node_id": pg.RESOLVE})


def test_no_module_level_graph_state() -> None:
    """Clause 4: the `_graph` global and its init/get pair are retired.

    Asserted by absence because that is the whole property — an attribute here
    that a request could write is authoritative in-process state whatever it is
    named.
    """
    import idiograph.mcp_server as server

    for retired in ("_graph", "init_graph", "_get_graph"):
        assert not hasattr(server, retired), retired


# ── Nothing on the surface raises on its only graph (f52487fa) ────────────────


@pytest.mark.parametrize("node_id", DECLARED_NODE_IDS)
def test_get_node_answers_for_every_declared_node(node_id: str) -> None:
    node = call("get_node", {"node_id": node_id})
    assert node["id"] == node_id
    assert node["type"]


def test_get_node_reports_a_miss_as_data() -> None:
    assert call("get_node", {"node_id": "no_such_node"}) == {
        "error": "Node 'no_such_node' not found."
    }


def test_get_node_requires_a_node_id() -> None:
    with pytest.raises(ValueError, match="get_node requires 'node_id'"):
        call("get_node", {})


def test_get_edges_from_resolve() -> None:
    """Node 0 feeds four consumers — three `seeds` ports plus `annotate.resolved`."""
    edges = call("get_edges_from", {"node_id": pg.RESOLVE})
    assert [edge["target"] for edge in edges] == [
        pg.BACKWARD,
        pg.FORWARD,
        pg.ASSEMBLE,
        pg.ANNOTATE,
    ]
    assert all(edge["source"] == pg.RESOLVE for edge in edges)


def test_summarize_intent_unscoped() -> None:
    summary = call("summarize_intent")
    assert summary["graph"] == pg.PIPELINE_GRAPH_NAME
    assert summary["scope"] == "full"
    assert summary["node_count"] == 11
    assert summary["edge_count"] == 21


def test_validate_graph_is_green() -> None:
    assert call("validate_graph") == {"valid": True, "errors": []}


# ── The declaration describes the record, not a zeroed picture ────────────────


def test_annotate_carries_the_records_llm_config(record) -> None:
    """The LLM node is configured, not switched off.

    Serving the viewer's inert arguments would put `llm: None` here — the node
    would read as config-disabled beside a record holding ~1,100 live LLM draws.
    """
    node = call("get_node", {"node_id": pg.ANNOTATE})
    assert node["params"]["llm"]
    assert node["params"]["llm"]["model_id"] == record.parameters.llm.model_id


def test_backward_carries_the_records_traversal_cap(record) -> None:
    node = call("get_node", {"node_id": pg.BACKWARD})
    assert node["params"]["n_backward"] == record.parameters.backward.n_backward
    assert node["params"]["n_backward"] > 0


def test_resolve_carries_the_records_own_seeds(record) -> None:
    """The served seed set is the one that produced the served record.

    Node 0's seeds are REQUEST dicts and the record's `seeds` are what they
    resolved to, so the two are compared by containment of the DOI in the
    resolved node_id rather than by equality.
    """
    seeds = call("get_node", {"node_id": pg.RESOLVE})["params"]["seeds"]
    assert len(seeds) == len(record.seeds)
    for seed, resolved in zip(seeds, record.seeds, strict=True):
        assert seed["doi"] in resolved


# ── No shared mutable graph between requests ─────────────────────────────────


def test_consecutive_resolutions_are_equal_but_distinct() -> None:
    """Clause 1/4: each request gets its own graph.

    The executor mutates `Node.status` in place, so a shared instance would leak
    one reader's view into the next. Distinctness is asserted down to the node
    objects and the seed dicts, which are the mutable parts.
    """
    first, second = resolve_graph(), resolve_graph()
    assert first == second
    assert first is not second
    assert first.nodes[0] is not second.nodes[0]
    assert (
        first.nodes[0].params["seeds"] is not second.nodes[0].params["seeds"]
    )


# ── The record is served ─────────────────────────────────────────────────────


def test_record_default_call_names_the_packaged_address() -> None:
    shape = call(RECORD_TOOL)
    assert shape["address"] == frozen_crispr_address()
    assert shape["select"] == "shape"


def test_record_shape_counts_every_top_level_field(record) -> None:
    shape = call(RECORD_TOOL)
    assert set(shape["fields"]) == set(record.model_dump(mode="json"))
    assert shape["fields"]["nodes"] == {"kind": "list", "count": len(record.nodes)}
    assert shape["fields"]["edges"] == {"kind": "list", "count": len(record.edges)}
    assert shape["seeds"] == record.seeds
    assert shape["parameters"] == record.parameters.model_dump(mode="json")


def test_record_path_read_returns_that_field(record) -> None:
    page = call(RECORD_TOOL, {"select": "path", "path": ["edges"], "limit": 3})
    assert page["address"] == frozen_crispr_address()
    assert page["kind"] == "list"
    assert page["count"] == len(record.edges)
    assert page["items"] == record.model_dump(mode="json")["edges"][:3]


def test_record_path_reaches_a_nested_field_and_a_scalar(record) -> None:
    """Every top-level field is reachable, and so is anything under one.

    Segments are a list rather than a dotted string because the record's own
    keys — node_ids and DOIs — contain dots and slashes.
    """
    log = call(RECORD_TOOL, {"select": "path", "path": ["cycle_clean", "cycle_log"]})
    assert log["kind"] == "object"

    year = call(RECORD_TOOL, {"select": "path", "path": ["parameters", "current_year"]})
    assert year == {
        "address": frozen_crispr_address(),
        "select": "path",
        "path": ["parameters", "current_year"],
        "kind": "value",
        "value": record.parameters.current_year,
    }


def test_record_path_windows_a_keyed_field(record) -> None:
    first = call(RECORD_TOOL, {"select": "path", "path": ["pagerank"], "limit": 2})
    second = call(
        RECORD_TOOL,
        {"select": "path", "path": ["pagerank"], "offset": 2, "limit": 2},
    )
    assert first["count"] == len(record.pagerank)
    assert len(first["items"]) == 2
    assert set(first["items"]).isdisjoint(second["items"])


def test_record_node_read_returns_one_paper(record) -> None:
    node_id = record.seeds[0]
    result = call(RECORD_TOOL, {"select": "node", "node_id": node_id})
    assert result["node"]["node_id"] == node_id
    assert result["node"]["title"]
    assert result["address"] == frozen_crispr_address()


def test_record_edge_read_returns_one_edge_by_endpoints(record) -> None:
    edge = record.edges[0]
    result = call(
        RECORD_TOOL,
        {
            "select": "edge",
            "source_id": edge.source_id,
            "target_id": edge.target_id,
        },
    )
    assert result["count"] >= 1
    assert all(
        found["source_id"] == edge.source_id and found["target_id"] == edge.target_id
        for found in result["edges"]
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"address": "0" * 64},
        {"address": "not-an-address"},
        {"select": "node", "node_id": "doi:nothing"},
        {"select": "edge", "source_id": "doi:a", "target_id": "doi:b"},
        {"select": "path", "path": ["no_such_field"]},
    ],
)
def test_record_misses_are_structured_errors_not_exceptions(arguments: dict) -> None:
    """A well-formed question about something absent is answered, not raised.

    The `{"error": ...}` convention `get_node` already uses. Malformed ARGUMENTS
    still raise — that distinction is the point.
    """
    result = call(RECORD_TOOL, arguments)
    assert set(result) == {"error"}
    assert result["error"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"select": "path"},
        {"select": "node"},
        {"select": "edge", "source_id": "doi:a"},
        {"select": "path", "path": ["nodes"], "limit": 0},
        {"select": "path", "path": ["nodes"], "offset": -1},
        {"select": "sideways"},
    ],
)
def test_record_malformed_arguments_raise(arguments: dict) -> None:
    with pytest.raises(ValueError):
        call(RECORD_TOOL, arguments)


def test_no_record_response_is_the_whole_record() -> None:
    """The 9.3 MB dump is never handed back — not by any selector, not at all.

    Asserted over the widest calls a client can make: the shape summary, a
    maximum-window read of each of the two big lists, and a whole-field read of
    the largest nested field, which is refused outright.
    """
    on_disk = (REGISTRY_ROOT / f"{frozen_crispr_address()}.json").stat().st_size
    widest = [
        {},
        {"select": "path", "path": ["nodes"], "limit": 200},
        {"select": "path", "path": ["edges"], "limit": 200},
        {"select": "path", "path": ["communities"]},
        {"select": "path", "path": ["cycle_clean"]},
    ]
    for arguments in widest:
        text = json.dumps(call(RECORD_TOOL, arguments), indent=2)
        assert len(text) <= _MAX_RESPONSE_BYTES, arguments
        assert len(text) < on_disk // 20, arguments

    # The one field too big to hand back whole is refused rather than truncated
    # silently, and the refusal names the knobs that narrow it.
    refused = call(RECORD_TOOL, {"select": "path", "path": ["cycle_clean"]})
    assert "ceiling" in refused["error"]
    assert "'limit'" in refused["error"]
