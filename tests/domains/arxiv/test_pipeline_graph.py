# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# IDG-075 clause 4d / IDG-079 clause 4 — the declared citation-traversal graph.
#
# WHY THIS EXISTS. `build_pipeline_graph` is a SECOND description of a pipeline
# that already has a first one: `run_traversal` performs the same dataflow by
# hand. Nothing in the type system relates the two, so the graph can drift from
# the orchestrator silently and still validate green — a graph that describes a
# different pipeline is not a broken graph, it is a wrong one, and
# `validate_integrity` cannot tell the difference. These tests are what holds
# the two together.
#
# THE INDEPENDENCE RULE. The expected topology below is WRITTEN OUT BY HAND, as
# literal strings, and is never derived from the module under test or from the
# port constants that module reads. A test that computes its expectation from
# its subject asserts nothing. This is what makes the node-set split at Node 5.5
# — the one edge relationship that cannot be read off stage names — an actual
# assertion rather than a restatement.
#
# THE 5.5 SPLIT, stated once here so a future editor changing EXPECTED_EDGES has
# to disagree with it deliberately. `run_traversal` rebinds `unified_nodes` to
# the ANNOTATED copies inside its Node 5.5 block. Stages whose call sits BEFORE
# that rebinding consume the pre-annotation set (`assemble.nodes`): clean, co,
# and annotate itself. Stages AFTER it consume `annotate.nodes`: depth,
# pagerank, communities, enrich. Both sides are green under
# `validate_integrity`, so nothing but this table catches getting it backwards.

import ast
import asyncio
import builtins
import inspect
import os
import socket
from pathlib import Path

import pytest

# The verified static-parse helper from the params-marshalling suite, reused
# rather than reimplemented: it already decides "what params keys does this
# stage's direct call site pass", and a second copy of that parser here could
# drift from it and quietly answer a different question. It is scoped to
# `run_traversal` by construction, which covers ten of the eleven stages —
# `resolve_seeds` is called from `run_arxiv_pipeline` and gets its own reader
# below. The import path is the one pytest's rootdir insertion gives this
# package (`tests/` has no `__init__.py`, `tests/domains/arxiv/` does).
from domains.arxiv.test_params_marshalling import _params_keys
from idiograph.core.executor import HANDLERS
from idiograph.core.models import Graph
from idiograph.core.query import validate_integrity
from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.handlers import register_arxiv_handlers
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    ForwardParameters,
    LLMConfig,
    PipelineParameters,
)
from idiograph.domains.arxiv.pipeline import (
    ASSEMBLE_GRAPH_INPUT_PORTS,
    ASSEMBLE_GRAPH_OUTPUT_PORTS,
    BACKWARD_TRAVERSE_INPUT_PORTS,
    BACKWARD_TRAVERSE_OUTPUT_PORTS,
    CLEAN_CYCLES_INPUT_PORTS,
    CLEAN_CYCLES_OUTPUT_PORTS,
    CO_CITATIONS_INPUT_PORTS,
    CO_CITATIONS_OUTPUT_PORTS,
    COMPUTE_DEPTH_METRICS_INPUT_PORTS,
    COMPUTE_DEPTH_METRICS_OUTPUT_PORTS,
    COMPUTE_PAGERANK_INPUT_PORTS,
    COMPUTE_PAGERANK_OUTPUT_PORTS,
    DETECT_COMMUNITIES_INPUT_PORTS,
    DETECT_COMMUNITIES_OUTPUT_PORTS,
    ENRICH_NODES_INPUT_PORTS,
    ENRICH_NODES_OUTPUT_PORTS,
    FORWARD_TRAVERSE_INPUT_PORTS,
    FORWARD_TRAVERSE_OUTPUT_PORTS,
    RESOLVE_SEEDS_INPUT_PORTS,
    RESOLVE_SEEDS_OUTPUT_PORTS,
)
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph
from idiograph.domains.arxiv.relationship_annotation import (
    ANNOTATE_RELATIONSHIPS_INPUT_PORTS,
    ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS,
    prompt_template_hash,
)

# ── The expected graph, written out by hand ──────────────────────────────────

#: (node id, node type). Eleven stages. Types are the strings
#: `register_arxiv_handlers` registers, spelled here independently so a typo in
#: the module under test cannot be matched by the same typo here.
EXPECTED_NODES: list[tuple[str, str]] = [
    ("resolve", "ResolveSeeds"),
    ("backward", "BackwardTraverse"),
    ("forward", "ForwardTraverse"),
    ("assemble", "AssembleGraph"),
    ("clean", "CleanCycles"),
    ("co", "ComputeCoCitations"),
    ("annotate", "AnnotateRelationships"),
    ("depth", "ComputeDepthMetrics"),
    ("pagerank", "ComputePagerank"),
    ("communities", "DetectCommunities"),
    ("enrich", "EnrichNodes"),
]

#: (source, from_port, target, to_port). Twenty-one edges — one per declared
#: input port across the eleven stages, which is the floor AND the ceiling
#: `_dataflow_errors` enforces.
EXPECTED_EDGES: list[tuple[str, str, str, str]] = [
    # Node 0 → the three `seeds` consumers, plus the seed set 5.5 classifies
    # relative to.
    ("resolve", "seeds", "backward", "seeds"),
    ("resolve", "seeds", "forward", "seeds"),
    ("resolve", "seeds", "assemble", "seeds"),
    ("resolve", "seeds", "annotate", "resolved"),
    # Traversal results, each carried whole on one port.
    ("backward", "backward", "assemble", "backward"),
    ("forward", "forward", "assemble", "forward"),
    # PRE-5.5 consumers of the merged node set.
    ("assemble", "nodes", "clean", "nodes"),
    ("assemble", "cites", "clean", "cites"),
    ("assemble", "nodes", "co", "nodes"),
    ("assemble", "nodes", "annotate", "nodes"),
    # The all_cites / cleaned_edges split: co-occurrence and clustering keep
    # real-but-suppressed citations; depth and pagerank need the acyclic view.
    ("clean", "all_cites", "co", "all_cites"),
    ("clean", "all_cites", "communities", "all_cites"),
    ("clean", "cleaned_edges", "depth", "cleaned_edges"),
    ("clean", "cleaned_edges", "pagerank", "cleaned_edges"),
    # POST-5.5 consumers of the node set. Getting these four wrong is the
    # silent failure this file exists to catch.
    ("annotate", "nodes", "depth", "nodes"),
    ("annotate", "nodes", "pagerank", "nodes"),
    ("annotate", "nodes", "communities", "nodes"),
    ("annotate", "nodes", "enrich", "nodes"),
    # The four-input enrichment join.
    ("depth", "depth_metrics", "enrich", "depth_metrics"),
    ("pagerank", "pagerank", "enrich", "pagerank"),
    ("communities", "communities", "enrich", "communities"),
]

#: node id → (declared input ports, declared output ports), naming the CONSTANT
#: each node must carry. Referenced from `pipeline` / `relationship_annotation`
#: — the stages' own declarations — never from `pipeline_graph`, so this checks
#: agreement between the graph and the source of truth rather than the graph
#: with itself.
EXPECTED_PORTS = {
    "resolve": (RESOLVE_SEEDS_INPUT_PORTS, RESOLVE_SEEDS_OUTPUT_PORTS),
    "backward": (BACKWARD_TRAVERSE_INPUT_PORTS, BACKWARD_TRAVERSE_OUTPUT_PORTS),
    "forward": (FORWARD_TRAVERSE_INPUT_PORTS, FORWARD_TRAVERSE_OUTPUT_PORTS),
    "assemble": (ASSEMBLE_GRAPH_INPUT_PORTS, ASSEMBLE_GRAPH_OUTPUT_PORTS),
    "clean": (CLEAN_CYCLES_INPUT_PORTS, CLEAN_CYCLES_OUTPUT_PORTS),
    "co": (CO_CITATIONS_INPUT_PORTS, CO_CITATIONS_OUTPUT_PORTS),
    "annotate": (
        ANNOTATE_RELATIONSHIPS_INPUT_PORTS,
        ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS,
    ),
    "depth": (COMPUTE_DEPTH_METRICS_INPUT_PORTS, COMPUTE_DEPTH_METRICS_OUTPUT_PORTS),
    "pagerank": (COMPUTE_PAGERANK_INPUT_PORTS, COMPUTE_PAGERANK_OUTPUT_PORTS),
    "communities": (DETECT_COMMUNITIES_INPUT_PORTS, DETECT_COMMUNITIES_OUTPUT_PORTS),
    "enrich": (ENRICH_NODES_INPUT_PORTS, ENRICH_NODES_OUTPUT_PORTS),
}

#: node id → the handler name its params are marshalled to at the direct call
#: site. Named, not mangled from the node type: a mangling rule that failed to
#: resolve would skip a stage silently, which is the failure this table exists
#: to prevent.
NODE_TO_HANDLER = {
    "resolve": "resolve_seeds",
    "backward": "backward_traverse",
    "forward": "forward_traverse",
    "assemble": "assemble_graph",
    "clean": "clean_cycles",
    "co": "compute_co_citations",
    "annotate": "annotate_relationships",
    "depth": "compute_depth_metrics",
    "pagerank": "compute_pagerank",
    "communities": "detect_communities",
    "enrich": "enrich_nodes",
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _seeds() -> list[dict]:
    """Seed request dicts — the shape Node 0 takes as CONFIGURATION."""
    return [{"arxiv_id": "2401.00001"}, {"doi": "10.1000/xyz123"}]


def _llm_config(model_id: str = "claude-haiku-4-5-20251001") -> LLMConfig:
    """A real LLMConfig, built the way the rest of the suite builds one.

    `prompt_template_hash` is DERIVED rather than hardcoded (IDG-032), so a
    prompt edit moves the content address automatically instead of leaving a
    stale literal here agreeing with nothing.
    """
    return LLMConfig(model_id=model_id, prompt_template_hash=prompt_template_hash())


def _parameters(llm: LLMConfig | None = None) -> PipelineParameters:
    """A fully-stated PipelineParameters.

    Every value is written down rather than defaulted where the model requires
    it, and `current_year` is stated rather than read from the clock: it enters
    the content address, so a wall-clock value would move this file's
    expectations on New Year.
    """
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        current_year=2026,
        llm=llm,
    )


@pytest.fixture
def graph() -> Graph:
    return build_pipeline_graph(_seeds(), _parameters())


# ── 1. The constraint's own trigger ──────────────────────────────────────────


def test_validate_integrity_reports_no_errors(graph: Graph) -> None:
    """`validate_integrity` returns zero errors on the built graph.

    The resolution trigger of constraint 18093c31. The ERROR LIST is asserted,
    not the boolean: a failure has to print which port was unfed or doubly
    bound, because "valid is False" names no defect and would send the reader
    back to the validator to find out what happened.
    """
    result = validate_integrity(graph)

    assert result["errors"] == [], (
        "the declared pipeline graph does not validate:\n  "
        + "\n  ".join(result["errors"])
    )
    assert result["valid"] is True


def test_every_declared_input_port_is_fed_exactly_once(graph: Graph) -> None:
    """The unfed-port floor and the one-edge ceiling, counted independently.

    `validate_integrity` already enforces both, so this does not add coverage of
    the validator — it adds a countable statement of the shape that makes the
    topology table above self-checking. The edge count and the declared-input-
    port count must be the same number, and if a future edit adds an edge
    without adding a port to feed, one of these two totals moves alone.
    """
    declared = [
        (node.id, port.name) for node in graph.nodes for port in node.input_ports
    ]
    fed = [(edge.target, edge.to_port) for edge in graph.edges]

    assert sorted(declared) == sorted(fed), (
        f"declared input ports and fed ports disagree. Declared but unfed: "
        f"{sorted(set(declared) - set(fed))}. Fed but undeclared: "
        f"{sorted(set(fed) - set(declared))}. An unfed port means the stage's "
        f"handler reads an input nothing produces."
    )
    assert len(fed) == len(set(fed)), (
        f"an input port is bound by more than one edge: "
        f"{sorted({p for p in fed if fed.count(p) > 1})}. The graph does not "
        f"say which value that port carries."
    )


# ── 2. The topology, spelled out ─────────────────────────────────────────────


def test_nodes_are_exactly_the_expected_set(graph: Graph) -> None:
    """Every node's (id, type), against the hand-written table.

    Order-insensitive: the graph's node order is not contractual — the executor
    runs a topological sort — so pinning it would fail on a harmless
    reordering while catching nothing.
    """
    actual = sorted((node.id, node.type) for node in graph.nodes)
    expected = sorted(EXPECTED_NODES)

    assert actual == expected, (
        f"the graph's stages are not the pipeline's stages. Missing: "
        f"{sorted(set(expected) - set(actual))}. Unexpected: "
        f"{sorted(set(actual) - set(expected))}. A missing stage means the "
        f"declared graph describes work the orchestrator does not do, or omits "
        f"work it does."
    )


def test_edges_are_exactly_the_expected_set(graph: Graph) -> None:
    """Every edge as (source, from_port, target, to_port), against the table.

    THE TRAP-1 ASSERTION. A graph that hands `depth`/`pagerank`/`communities`/
    `enrich` the PRE-annotation `assemble.nodes` instead of `annotate.nodes`
    passes `validate_integrity` green while describing a pipeline in which Node
    5.5's annotations reach nothing — the relationship types would be computed
    and dropped. Only this comparison against an independently written table
    catches it, which is why the table is literal strings and not a derivation.
    """
    actual = sorted(
        (edge.source, edge.from_port, edge.target, edge.to_port)
        for edge in graph.edges
    )
    expected = sorted(EXPECTED_EDGES)

    assert actual == expected, (
        f"the graph's dataflow is not `run_traversal`'s dataflow. Missing "
        f"edges: {sorted(set(expected) - set(actual))}. Unexpected edges: "
        f"{sorted(set(actual) - set(expected))}. Check the Node 5.5 rebinding "
        f"first: stages called before it consume `assemble.nodes`, stages "
        f"called after it consume `annotate.nodes`, and both wirings validate "
        f"green."
    )


def test_every_edge_is_fully_port_declared(graph: Graph) -> None:
    """No edge carries a half-declaration or falls back to the legacy regime.

    Every node here is bound, so an edge with no ports feeds nothing: the
    executor's `_collect_inputs` skips it and the target's port goes unbound. An
    edge declaring exactly one of the two is a validation error outright.
    """
    partial = [
        (e.source, e.target)
        for e in graph.edges
        if (e.from_port is None) != (e.to_port is None)
    ]
    assert not partial, (
        f"{partial} declare one port and not the other — such an edge is "
        f"neither port-declared nor legacy, and `_dataflow_errors` rejects it."
    )

    undeclared = [
        (e.source, e.target) for e in graph.edges if e.from_port is None
    ]
    assert not undeclared, (
        f"{undeclared} carry no ports. Every node in this graph is bound, so "
        f"the executor would build no input from these edges and the target's "
        f"declared port would never be fed."
    )


# ── 3. Type-registry agreement ───────────────────────────────────────────────


def test_every_node_type_has_a_registered_handler(graph: Graph) -> None:
    """Every node's `type` is a type `register_arxiv_handlers()` registers.

    A typo in a type string is invisible to `validate_integrity` — it checks
    dataflow, not dispatch — and would otherwise surface only when something
    executes this graph, which is a later milestone. `UnregisteredNodeTypeError`
    at that point would name the defect long after the PR that introduced it.
    """
    register_arxiv_handlers()

    unregistered = sorted(
        {node.type for node in graph.nodes if node.type not in HANDLERS}
    )
    assert not unregistered, (
        f"{unregistered} name no registered handler. Execution would raise "
        f"UnregisteredNodeTypeError before running anything. Registered types: "
        f"{sorted(HANDLERS)}."
    )


# ── 4. Port identity ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("node_id", sorted(EXPECTED_PORTS), ids=sorted(EXPECTED_PORTS))
def test_node_ports_are_the_declared_constants(graph: Graph, node_id: str) -> None:
    """Each node's ports ARE the stage's declared `PortDeclaration` objects.

    Identity, element by element, not equality. A graph that hand-copied
    `PortDeclaration(name="seeds", port_type="untyped")` would compare EQUAL to
    the constant while being free to drift from the handler the day the stage
    renames a port — which is the exact failure the port-declaration exercise
    exists to prevent. `is` is what distinguishes the two.

    Element-wise rather than whole-list because pydantic rebuilds the list
    container when it validates the field, so the container is a copy by
    construction and its identity carries no information. The PortDeclaration
    instances inside pass through untouched, and they are what a transcription
    would have had to recreate.
    """
    node = graph.get_node(node_id)
    assert node is not None, f"no node '{node_id}' in the graph"
    expected_in, expected_out = EXPECTED_PORTS[node_id]

    for label, actual, expected in (
        ("input_ports", node.input_ports, expected_in),
        ("output_ports", node.output_ports, expected_out),
    ):
        assert actual is not None, (
            f"'{node_id}'.{label} is None, which is the LEGACY regime — the "
            f"node would receive every upstream payload keyed by source id "
            f"instead of by port. It must declare the stage's constant."
        )
        assert len(actual) == len(expected), (
            f"'{node_id}'.{label} has {len(actual)} port(s), the stage "
            f"declares {len(expected)}."
        )
        for i, (got, want) in enumerate(zip(actual, expected)):
            assert got is want, (
                f"'{node_id}'.{label}[{i}] is a COPY of '{want.name}', not the "
                f"declaration itself. The graph transcribed the port instead of "
                f"importing it, so it can drift from the handler silently the "
                f"day the stage renames it."
            )


# ── 5. Params agreement with the direct call sites ───────────────────────────


def _resolve_seeds_params_keys() -> set[str]:
    """The params keys `run_arxiv_pipeline` passes to `resolve_seeds`.

    Node 0's direct call site is the ONE that does not live in `run_traversal`
    — resolution is held outside the traversal core so the read-through cache
    can short-circuit traversal alone — so the imported `_params_keys`, which is
    scoped to `run_traversal`, cannot read it. Same static-parse property, same
    literal-keys-only requirement, one different enclosing function.
    """
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_arxiv_pipeline":
            enclosing = node
            break
    else:
        raise AssertionError(
            "run_arxiv_pipeline not found in pipeline.py — this helper is "
            "pointed at the wrong function and would otherwise pass vacuously."
        )

    calls = [
        n
        for n in ast.walk(enclosing)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "resolve_seeds"
    ]
    assert len(calls) == 1, (
        f"expected exactly one call to resolve_seeds() in run_arxiv_pipeline, "
        f"found {len(calls)} — Node 0's marshalling is no longer at a single "
        f"site."
    )
    (call,) = calls
    params_arg = call.args[0]
    assert isinstance(params_arg, ast.Dict), (
        "resolve_seeds()'s params argument is not a dict literal, so its keys "
        "cannot be read statically."
    )
    keys = set()
    for key in params_arg.keys:
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
            "resolve_seeds() passes a non-literal params key; this helper can "
            "only decide the property over literal keys."
        )
        keys.add(key.value)
    return keys


@pytest.mark.parametrize(
    "node_id", sorted(NODE_TO_HANDLER), ids=sorted(NODE_TO_HANDLER)
)
def test_node_params_match_the_direct_call_site(graph: Graph, node_id: str) -> None:
    """Each node's params keys are exactly the keys its direct call site passes.

    This PR introduces a SECOND place every stage's params are spelled out. The
    graph literal and the hand-written call site in `run_traversal` must agree,
    and nothing else checks that they do: a key the graph passes and the call
    site does not means the declared graph describes a configuration production
    never runs, and a key the call site passes and the graph does not means the
    graph would run the stage on a model default while `content_address` hashes
    the configured value.

    Keys, not values — the values are `PipelineParameters` reads on both sides
    and comparing them would require executing the call site. The key set is
    what a static parse can decide, and it is where the drift shows up: a field
    added to a parameters model reaches one site and not the other.
    """
    handler = NODE_TO_HANDLER[node_id]
    call_site = (
        _resolve_seeds_params_keys()
        if handler == "resolve_seeds"
        else _params_keys(handler)
    )
    node = graph.get_node(node_id)
    assert node is not None, f"no node '{node_id}' in the graph"
    declared = set(node.params)

    assert declared == call_site, (
        f"node '{node_id}' declares params {sorted(declared)} but the "
        f"{handler}() call site passes {sorted(call_site)}. Only in the graph: "
        f"{sorted(declared - call_site)} — a configuration production does not "
        f"run. Only at the call site: {sorted(call_site - declared)} — the "
        f"graph would take a model default while content_address hashes the "
        f"configured value."
    )


def test_annotate_params_carry_the_live_llm_config() -> None:
    """Node 5.5's `llm` param is the LLMConfig object the call site passes.

    Not a `model_dump()`. Two things ride on it. The config predicate reads
    ordinary truthiness of `params["llm"]`, so an LLM-free run must put None
    here to disable the node — which is what makes the graph's gate the same
    decision as `run_traversal`'s `if parameters.llm is not None`. And
    `content_address` hashes `PipelineParameters` whole, so handing the object
    through keeps the declared and direct paths byte-identical.
    """
    configured = _llm_config()
    with_llm = build_pipeline_graph(_seeds(), _parameters(llm=configured))
    without_llm = build_pipeline_graph(_seeds(), _parameters(llm=None))

    assert with_llm.get_node("annotate").params["llm"] is configured, (
        "the graph re-serialized the LLMConfig instead of carrying it. The "
        "direct call site passes the object; a dump would make the two paths "
        "take different content addresses for one configuration."
    )
    assert without_llm.get_node("annotate").params["llm"] is None, (
        "an LLM-free run must put None on the `llm` param — that falsy value "
        "IS the config predicate's decision to skip the node. Anything else "
        "would dispatch Node 5.5 on a run that has no model."
    )


def test_annotate_node_is_present_and_gated_on_every_run() -> None:
    """Node 5.5 is in the graph whatever `parameters.llm` holds.

    THE TRAP-3 ASSERTION. `run_traversal` guards its call with an `if`; the
    node-graph equivalent is the config predicate, NOT an absent node. Omitting
    the node on an LLM-free run would leave `depth`/`pagerank`/`communities`/
    `enrich` with an unfed `nodes` port — `validate_integrity` would fail — so
    the graph's TOPOLOGY would depend on config. The predicate and the
    passthrough are what keep one topology across both runs.
    """
    for label, llm in (("with llm", _llm_config()), ("without llm", None)):
        graph = build_pipeline_graph(_seeds(), _parameters(llm=llm))
        node = graph.get_node("annotate")

        assert node is not None, (
            f"{label}: Node 5.5 is absent. Its four downstream consumers would "
            f"have an unfed `nodes` port and the graph would not validate."
        )
        assert node.enabled_when == "llm", (
            f"{label}: the node does not declare its config predicate, so the "
            f"executor would dispatch it on a run with no model."
        )
        assert node.disabled_passthrough == {"nodes": "nodes"}, (
            f"{label}: without the passthrough a disabled Node 5.5 emits no "
            f"`nodes` port at all, and all four downstream consumers fail with "
            f"PortBindingError."
        )
        assert validate_integrity(graph)["errors"] == [], (
            f"{label}: the graph does not validate — topology must not depend "
            f"on configuration."
        )


# ── Trap 2 — the resource fence ──────────────────────────────────────────────


def test_resource_declarations_match_the_handler_signatures() -> None:
    """A node declares `resources` exactly when its handler takes them.

    `resources=None` is not `resources=[]`. An empty list is a DECLARATION: it
    puts the node on the bound side of the fence and the executor calls the
    handler with a `resources=` keyword. Of the eleven handlers exactly four
    accept that keyword; giving one of the other seven `[]` would call it with
    an argument it does not take, and dropping it from one of the four would
    call a handler that needs a client with none.

    Read off the registered handlers' real signatures rather than a table, so
    this cannot agree with a wrong graph by being wrong in the same way.
    """
    register_arxiv_handlers()
    graph = build_pipeline_graph(_seeds(), _parameters())

    for node in graph.nodes:
        handler = HANDLERS[node.type]
        params = inspect.signature(handler).parameters
        takes_resources = (
            "resources" in params
            and params["resources"].kind is inspect.Parameter.KEYWORD_ONLY
        )

        if takes_resources:
            assert node.resources is not None, (
                f"'{node.id}' declares no resources, but {handler.__name__}() "
                f"takes a keyword-only `resources` and reads from it. The "
                f"executor would call it with two positional arguments."
            )
        else:
            assert node.resources is None, (
                f"'{node.id}' declares resources {node.resources}, but "
                f"{handler.__name__}() takes only (params, inputs). Declaring "
                f"puts the node on the bound side of the fence and the "
                f"executor would call it with a `resources=` keyword it does "
                f"not accept. Use None, not []."
            )


def test_declared_resource_names_are_the_ones_handlers_read() -> None:
    """The four declaring nodes name the resources their handlers look up.

    `_collect_resources` narrows the run's supplied mapping to exactly the
    declared names, so a name the handler reads but the node does not declare is
    a KeyError inside the handler, and `_check_resource_supply` would not have
    caught it — it verifies supply against the declaration, not against the
    handler.
    """
    graph = build_pipeline_graph(_seeds(), _parameters())
    expected = {
        "resolve": ["http_client", "openalex_api_key"],
        "backward": ["http_client", "openalex_api_key"],
        "forward": ["http_client", "openalex_api_key"],
        "annotate": ["anthropic_client"],
    }

    declaring = {n.id: n.resources for n in graph.nodes if n.resources is not None}
    assert declaring == expected, (
        f"declared resources {declaring} are not the ones the handlers read "
        f"({expected}). A name the handler reads but the node does not declare "
        f"raises KeyError inside the handler, after the run has already started."
    )


# ── 6. Purity ────────────────────────────────────────────────────────────────


def test_two_calls_produce_equal_but_distinct_graphs() -> None:
    """Equal arguments give equal graphs — and never the same object.

    Equality is the determinism half. Distinctness is the half that matters
    operationally: `execute_graph` mutates `Node.status` in place, so a shared
    instance would leak one run's statuses into every later reader's view of the
    declared pipeline. A module-level singleton would satisfy equality
    trivially and fail this.
    """
    seeds, parameters = _seeds(), _parameters()
    first = build_pipeline_graph(seeds, parameters)
    second = build_pipeline_graph(seeds, parameters)

    assert first == second, (
        "two calls with equal arguments produced different graphs — the "
        "builder is reading something other than its arguments."
    )
    assert first is not second, (
        "the builder returned the same object twice. The executor mutates "
        "Node.status in place, so one run's statuses would show up in the next "
        "caller's declared graph."
    )
    assert first.nodes[0] is not second.nodes[0], (
        "the two graphs share node objects, so mutating one mutates the other."
    )


def test_build_is_synchronous_and_opens_no_event_loop() -> None:
    """The builder is an ordinary function returning an ordinary Graph.

    The whole point of a declared graph is that the viewer and the validator can
    read it WITHOUT executing it. A coroutine function would mean the caller
    needs a loop to find out what the pipeline is.
    """
    assert not inspect.iscoroutinefunction(build_pipeline_graph)
    result = build_pipeline_graph(_seeds(), _parameters())
    assert isinstance(result, Graph)
    assert not inspect.isawaitable(result)


def test_build_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call reads no file, no socket, and no environment variable.

    IDG-075 clause 4d's purity requirement, asserted by removing the capability
    rather than by inspecting for it: every route out of the process is replaced
    with a recorder for the duration of the ONE call, so a read of any of them
    is recorded whether or not the caller swallows the resulting exception.

    Scope, stated honestly: this covers the CALL. It does not cover this
    module's IMPORT, which cannot be pure — importing the port declarations from
    `pipeline` (which the graph is required to do rather than transcribe them)
    executes that module's top-level `load_dotenv()`, and that reads `.env` off
    disk. That is pre-existing behaviour of `pipeline`, inherited by every test
    in this suite, and closing it is not in this PR's scope.
    """
    seeds, parameters = _seeds(), _parameters()  # built before the tripwires
    tripped: list[str] = []

    def _trip(name: str):
        def _fn(*args, **kwargs):
            tripped.append(name)
            raise AssertionError(f"build_pipeline_graph reached {name}")

        return _fn

    class _TrippingEnviron(dict):
        def __getitem__(self, key):
            tripped.append(f"os.environ[{key!r}]")
            raise AssertionError(f"build_pipeline_graph read os.environ[{key!r}]")

        def get(self, key, default=None):
            tripped.append(f"os.environ.get({key!r})")
            raise AssertionError(f"build_pipeline_graph read os.environ[{key!r}]")

    monkeypatch.setattr(builtins, "open", _trip("builtins.open"))
    monkeypatch.setattr(socket, "socket", _trip("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", _trip("socket.create_connection"))
    monkeypatch.setattr(asyncio, "new_event_loop", _trip("asyncio.new_event_loop"))
    monkeypatch.setattr(os, "environ", _TrippingEnviron())

    failure: BaseException | None = None
    graph: Graph | None = None
    try:
        graph = build_pipeline_graph(seeds, parameters)
    except BaseException as exc:  # noqa: BLE001 — re-raised as an assertion below
        failure = exc
    finally:
        # Undone BEFORE asserting: pytest's own failure reporting reads source
        # files, and it cannot do that through the tripwire above.
        monkeypatch.undo()

    assert tripped == [], (
        f"build_pipeline_graph is not pure — it reached {tripped}. The viewer "
        f"and validate_integrity must be able to read the declared pipeline "
        f"without the process owning a network, a filesystem or a credential."
    )
    assert failure is None, f"build_pipeline_graph raised under tripwires: {failure!r}"
    assert isinstance(graph, Graph)


def test_build_does_not_alias_its_seed_argument_into_shared_state() -> None:
    """Different seed sets give different graphs; the builder holds no cache.

    The cheap check that `seeds` reaches Node 0's params as CONFIGURATION on
    every call rather than being captured once at import. A memoized builder
    would return the first call's seeds forever, and every later run would be
    addressed to a seed set it did not request.
    """
    first = build_pipeline_graph([{"arxiv_id": "1111.11111"}], _parameters())
    second = build_pipeline_graph([{"arxiv_id": "2222.22222"}], _parameters())

    assert first.get_node("resolve").params["seeds"] == [{"arxiv_id": "1111.11111"}]
    assert second.get_node("resolve").params["seeds"] == [{"arxiv_id": "2222.22222"}]


# ── Non-collision with the unrelated demo graph ──────────────────────────────


def test_graph_name_does_not_collide_with_the_abstract_demo() -> None:
    """This graph is not `pipeline.ARXIV_PIPELINE`.

    `ARXIV_PIPELINE` is the four-node abstract-summarizer demo (`fetch` →
    `claims` → `evaluate` → `summarize`) and has never been the citation
    traversal. Two graphs answering to one name would make any by-name lookup —
    the viewer's included — ambiguous between a demo and the production
    pipeline.
    """
    graph = build_pipeline_graph(_seeds(), _parameters())

    assert graph.name != pipeline.ARXIV_PIPELINE.name
    assert graph.name, "the graph must carry a name"
    assert graph.version, "the graph must carry a version"
