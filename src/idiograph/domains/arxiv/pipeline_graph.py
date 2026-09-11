# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""The citation-traversal pipeline as a declared ``Graph`` (IDG-075 clause 4d,
signature per IDG-079 clause 4).

Eleven stages in this domain are port-bound, and until now every one of those
port declarations was referenced only from the comment that justified it.
Nothing composed them. This module is that composition: it reads the eleven
declared port constants and returns the ``Graph`` they describe.

WHAT THIS IS A TRANSCRIPTION OF. ``pipeline.run_traversal`` (plus the
``resolve_seeds`` call at the head of ``run_arxiv_pipeline``) performs this
dataflow today by hand — locals threaded from one ``await`` to the next. That
hand-written orchestrator is the specification, and this graph is derived from
its call order, not from stage names. The two are expected to agree key for key;
``tests/domains/arxiv/test_pipeline_graph.py`` asserts that they do by parsing
the call sites.

WHAT THIS IS NOT. It does not execute anything and nothing in production
consumes it. ``run_traversal`` remains the production path; this module is read
by the declared-projection viewer, by ``validate_integrity``, and by its tests.
The flip onto ``execute_graph`` is a separate milestone (IDG-078 clause 5), so
obligation 9378c25e stands undischarged here and is not claimed otherwise.

PURITY. ``build_pipeline_graph`` is a function of its two arguments: no I/O, no
environment read, no event loop, no client, no network. That is what lets the
viewer and the validator read the declared pipeline without running it. Note the
one inherited exception, which this module cannot close without transcribing the
port declarations it is required to import: importing ``pipeline`` executes that
module's top-level ``load_dotenv()``, so the IMPORT touches the filesystem. The
CALL does not.

FRESH GRAPH PER CALL, deliberately not a module-level singleton. The executor
mutates ``Node.status`` in place (``_update_node_status``), so a shared instance
would carry one run's statuses into the next reader's view. Two calls with equal
arguments return equal-but-distinct graphs.
"""

from idiograph.core.models import Edge, Graph, Node
from idiograph.domains.arxiv.models import PipelineParameters
from idiograph.domains.arxiv.pipeline import (
    ASSEMBLE_GRAPH_INPUT_PORTS,
    ASSEMBLE_GRAPH_OUTPUT_PORTS,
    BACKWARD_SLEEP_MS,
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
    FORWARD_ACCELERATION_METHOD,
    FORWARD_TRAVERSE_INPUT_PORTS,
    FORWARD_TRAVERSE_OUTPUT_PORTS,
    RESOLVE_SEEDS_INPUT_PORTS,
    RESOLVE_SEEDS_OUTPUT_PORTS,
)
from idiograph.domains.arxiv.relationship_annotation import (
    ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH,
    ANNOTATE_RELATIONSHIPS_ENABLED_WHEN,
    ANNOTATE_RELATIONSHIPS_INPUT_PORTS,
    ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS,
)

#: The graph's name. Deliberately NOT ``arxiv_abstract_pipeline`` — that name
#: belongs to ``pipeline.ARXIV_PIPELINE``, the unrelated four-node
#: abstract-summarizer demo. Two graphs answering to one name would make every
#: by-name lookup ambiguous, and these two describe different work.
PIPELINE_GRAPH_NAME = "arxiv_citation_traversal"

#: First declared composition of these stages, so 1.0. The version tracks THIS
#: declaration, not the stages: a stage changing its ports is a breaking change
#: to the wiring and moves this number; a handler changing its internals is not.
PIPELINE_GRAPH_VERSION = "1.0"

#: The run-supplied capabilities the two OpenAlex traversal stages and Node 0
#: name. Held in one constant because all three declare the same pair and a
#: divergence between them would be a defect, not a variation — the credential
#: and the client are one run's, threaded to every stage that calls OpenAlex.
_OPENALEX_RESOURCES = ["http_client", "openalex_api_key"]

# Node ids. Short stage names, matching the ones the port-declaration comments
# already use when they show the intended wiring (``assemble.nodes ->
# clean.nodes``, ``clean.all_cites -> co.all_cites``, ``depth.depth_metrics ->
# enrich.depth_metrics``). Reusing them means a reader who arrives from those
# comments finds the edges spelled exactly as predicted; inventing a second
# naming scheme would make the declarations and the wiring read as two
# vocabularies for one pipeline.
RESOLVE = "resolve"
BACKWARD = "backward"
FORWARD = "forward"
ASSEMBLE = "assemble"
CLEAN = "clean"
CO_CITATIONS = "co"
ANNOTATE = "annotate"
DEPTH = "depth"
PAGERANK = "pagerank"
COMMUNITIES = "communities"
ENRICH = "enrich"


def build_pipeline_graph(
    seeds: list[dict],
    parameters: PipelineParameters,
) -> Graph:
    """Build the declared ``Graph`` for the citation-traversal pipeline.

    ``seeds`` is the list of seed identifier dicts (``{"arxiv_id": ...}`` /
    ``{"doi": ...}``) — Node 0's seed set arrives as CONFIGURATION on its params
    rather than as dataflow, because it is the head of the pipeline and has no
    producer to bind from. It is a separate argument rather than a
    ``PipelineParameters`` field because that model does not carry the request
    dicts; the argument order mirrors ``run_arxiv_pipeline(seeds, parameters,
    ...)``.

    Every node's ports ARE the stage's own declared constants, imported. They
    are never re-declared here and never hand-copied into ``PortDeclaration``
    literals: a transcribed port can drift from its handler silently, which is
    the failure the port-declaration exercise exists to prevent.
    """
    return Graph(
        name=PIPELINE_GRAPH_NAME,
        version=PIPELINE_GRAPH_VERSION,
        nodes=_build_nodes(seeds, parameters),
        edges=_build_edges(),
    )


def _build_nodes(
    seeds: list[dict],
    parameters: PipelineParameters,
) -> list[Node]:
    """The eleven stages, with the params each one's direct call site passes.

    PARAMS ARE MIRRORED, NOT INVENTED. Each mapping below is the same mapping
    the hand-written call site marshals in ``run_traversal`` (and, for Node 0,
    at the head of ``run_arxiv_pipeline``). This module is therefore the SECOND
    place each stage's params are spelled out, and a divergence would mean the
    graph describes a configuration production does not run — so the test module
    parses the call sites and asserts key-for-key agreement rather than trusting
    this comment.

    Both non-config constants come in the same way the call sites take them:
    ``BACKWARD_SLEEP_MS`` (pacing) and ``FORWARD_ACCELERATION_METHOD`` (one
    admissible method) are imported from their one home rather than restated, so
    the graph shows what the stage is configured with and cannot disagree with
    the direct path.

    RESOURCES: ``None`` IS NOT ``[]``. Exactly four of the eleven handlers take
    a keyword-only ``resources`` mapping — the two OpenAlex traversal stages,
    Node 0, and Node 5.5 — and only those four declare. The other seven are
    ``(params, inputs)`` and carry ``None``, which keeps them in the legacy
    two-positional-argument dispatch. Declaring ``[]`` on one of them would put
    it on the bound side of the resource fence and the executor would call it
    with a ``resources=`` keyword it does not accept.
    """
    return [
        # Node 0. `input_ports=[]` is a DECLARATION, not an omission: the empty
        # list puts the node on the bound side of the fence with nothing to
        # bind, which is the shape of a pipeline head whose seed set is config.
        # `None` would drop it into the legacy regime — the opposite.
        Node(
            id=RESOLVE,
            type="ResolveSeeds",
            params={"seeds": seeds},
            input_ports=RESOLVE_SEEDS_INPUT_PORTS,
            output_ports=RESOLVE_SEEDS_OUTPUT_PORTS,
            resources=_OPENALEX_RESOURCES,
        ),
        Node(
            id=BACKWARD,
            type="BackwardTraverse",
            params={
                "n_backward": parameters.backward.n_backward,
                "lambda_decay": parameters.backward.lambda_decay,
                "sleep_ms": BACKWARD_SLEEP_MS,
                "current_year": parameters.current_year,
            },
            input_ports=BACKWARD_TRAVERSE_INPUT_PORTS,
            output_ports=BACKWARD_TRAVERSE_OUTPUT_PORTS,
            resources=_OPENALEX_RESOURCES,
        ),
        Node(
            id=FORWARD,
            type="ForwardTraverse",
            params={
                "n_forward": parameters.forward.n_forward,
                "lambda_decay": parameters.forward.lambda_decay,
                "alpha": parameters.forward.alpha,
                "beta": parameters.forward.beta,
                "sort": parameters.forward.sort,
                "acceleration_method": FORWARD_ACCELERATION_METHOD,
                "current_year": parameters.current_year,
            },
            input_ports=FORWARD_TRAVERSE_INPUT_PORTS,
            output_ports=FORWARD_TRAVERSE_OUTPUT_PORTS,
            resources=_OPENALEX_RESOURCES,
        ),
        Node(
            id=ASSEMBLE,
            type="AssembleGraph",
            params={},
            input_ports=ASSEMBLE_GRAPH_INPUT_PORTS,
            output_ports=ASSEMBLE_GRAPH_OUTPUT_PORTS,
        ),
        Node(
            id=CLEAN,
            type="CleanCycles",
            params={},
            input_ports=CLEAN_CYCLES_INPUT_PORTS,
            output_ports=CLEAN_CYCLES_OUTPUT_PORTS,
        ),
        Node(
            id=CO_CITATIONS,
            type="ComputeCoCitations",
            params={
                "min_strength": parameters.co_citation.min_strength,
                "max_edges": parameters.co_citation.max_edges,
            },
            input_ports=CO_CITATIONS_INPUT_PORTS,
            output_ports=CO_CITATIONS_OUTPUT_PORTS,
        ),
        # Node 5.5. UNCONDITIONALLY PRESENT, whatever `parameters.llm` holds.
        # `run_traversal` guards its call with `if parameters.llm is not None`;
        # the node-graph equivalent of that guard is the config predicate, not
        # an absent node. `enabled_when="llm"` makes the executor test the same
        # param's truthiness before dispatch, and the passthrough forwards the
        # pre-annotation node set on its `nodes` output port so the four
        # downstream consumers stay bound. Omitting the node on an LLM-free run
        # would strand those consumers' `nodes` ports and fail
        # `validate_integrity` — the graph's TOPOLOGY would depend on config,
        # which is exactly what the predicate exists to prevent.
        #
        # `llm` carries the live `LLMConfig` OBJECT, not a `model_dump()`: the
        # direct call site passes the object, and passing it keeps the two
        # paths byte-identical through `content_address`. It also makes the
        # predicate read correctly with no special case — an LLM-free run puts
        # `None` here, which is falsy, exactly as the direct path's `is not
        # None` test decides. Serializability is NOT the cost this looks
        # like: pydantic walks the nested model through `dict[str, Any]`, so
        # the graph round-trips `model_dump(mode="json")` -> `json.dumps` ->
        # `Graph.model_validate` with `validate_integrity` still green, the
        # round-tripped dict still truthy for the predicate, and
        # `_AnnotateRelationshipsParams` still accepting it and rebuilding an
        # equal `LLMConfig`. IDG-079's serializability objection was to
        # resolved `PaperRecord`s — network-fetched domain data — and does not
        # reach a frozen config model.
        Node(
            id=ANNOTATE,
            type="AnnotateRelationships",
            params={"llm": parameters.llm},
            input_ports=ANNOTATE_RELATIONSHIPS_INPUT_PORTS,
            output_ports=ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS,
            resources=["anthropic_client"],
            enabled_when=ANNOTATE_RELATIONSHIPS_ENABLED_WHEN,
            disabled_passthrough=ANNOTATE_RELATIONSHIPS_DISABLED_PASSTHROUGH,
        ),
        Node(
            id=DEPTH,
            type="ComputeDepthMetrics",
            params={},
            input_ports=COMPUTE_DEPTH_METRICS_INPUT_PORTS,
            output_ports=COMPUTE_DEPTH_METRICS_OUTPUT_PORTS,
        ),
        Node(
            id=PAGERANK,
            type="ComputePagerank",
            params={"damping": parameters.pagerank.damping},
            input_ports=COMPUTE_PAGERANK_INPUT_PORTS,
            output_ports=COMPUTE_PAGERANK_OUTPUT_PORTS,
        ),
        Node(
            id=COMMUNITIES,
            type="DetectCommunities",
            params={
                "infomap_seed": parameters.communities.infomap_seed,
                "infomap_trials": parameters.communities.infomap_trials,
                "infomap_teleportation": parameters.communities.infomap_teleportation,
                "leiden_seed": parameters.communities.leiden_seed,
                "community_count_min": parameters.communities.community_count_min,
                "community_count_max": parameters.communities.community_count_max,
            },
            input_ports=DETECT_COMMUNITIES_INPUT_PORTS,
            output_ports=DETECT_COMMUNITIES_OUTPUT_PORTS,
        ),
        Node(
            id=ENRICH,
            type="EnrichNodes",
            params={},
            input_ports=ENRICH_NODES_INPUT_PORTS,
            output_ports=ENRICH_NODES_OUTPUT_PORTS,
        ),
    ]


def _build_edges() -> list[Edge]:
    """The 21 edges, one per declared input port of a bound node.

    The count is not a coincidence and is the cheapest check on this list: the
    eleven stages declare 21 input ports between them, ``_dataflow_errors``
    requires every one to be fed by EXACTLY one edge — floor and ceiling — and
    no edge here feeds anything else. Unconsumed OUTPUT ports are a different
    matter and are legal: `seed_failures`, `failed_batches`, `failed_seeds`,
    `truncated_seeds`, `mismatches`, `cycle_log`, `provenance`, both co-citation
    ports and `enriched_nodes` all dangle green, and no consumer is invented for
    them.

    THE NODE 5.5 SPLIT is the one thing in this list that cannot be read off
    stage names, and it is where a wrong graph would still validate green while
    describing a different pipeline. ``run_traversal`` rebinds its
    ``unified_nodes`` local to the ANNOTATED copies at the Node 5.5 block, so
    the `nodes` port of every stage divides on which side of that rebinding its
    call sits:

      - BEFORE the rebinding, consuming ``assemble.nodes``: `clean`, `co`, and
        `annotate` itself. `co`'s call is explicitly documented as not moving
        past 5.5.
      - AFTER the rebinding, consuming ``annotate.nodes``: `depth`, `pagerank`,
        `communities`, `enrich`.

    Port-NAME identity is what makes that split invisible to a reader of names
    alone, and is the point ``COMPUTE_DEPTH_METRICS_INPUT_PORTS``' comment makes
    with ``annotate.nodes -> depth.nodes``: a consumer's input port carries the
    same name as its producer's output port, so the wiring needs no adapter node
    between them. But `assemble` and `annotate` BOTH publish a `nodes` port, so
    the name alone never says which side of the rebinding a consumer binds —
    only the call order does. ``ANNOTATE_RELATIONSHIPS_OUTPUT_PORTS`` reuses the
    name deliberately, on its own statement that "every downstream ``nodes``
    consumer binds HERE on the LLM path"; the disabled passthrough is what makes
    that true on the LLM-free path too, with no second topology.
    """
    return [
        # Node 0 feeds the three stages whose `seeds` port is name-identical to
        # its own output port, plus the seed set 5.5 classifies RELATIVE TO —
        # `annotate.resolved` is the frame of reference, not a second node set.
        Edge(source=RESOLVE, target=BACKWARD, from_port="seeds", to_port="seeds"),
        Edge(source=RESOLVE, target=FORWARD, from_port="seeds", to_port="seeds"),
        Edge(source=RESOLVE, target=ASSEMBLE, from_port="seeds", to_port="seeds"),
        Edge(source=RESOLVE, target=ANNOTATE, from_port="seeds", to_port="resolved"),
        # The two traversals carry their whole result on one port each; the
        # merge's input ports are named for the results they take whole.
        Edge(source=BACKWARD, target=ASSEMBLE, from_port="backward", to_port="backward"),
        Edge(source=FORWARD, target=ASSEMBLE, from_port="forward", to_port="forward"),
        # Pre-5.5 consumers of the merged node set.
        Edge(source=ASSEMBLE, target=CLEAN, from_port="nodes", to_port="nodes"),
        Edge(source=ASSEMBLE, target=CLEAN, from_port="cites", to_port="cites"),
        Edge(source=ASSEMBLE, target=CO_CITATIONS, from_port="nodes", to_port="nodes"),
        Edge(source=ASSEMBLE, target=ANNOTATE, from_port="nodes", to_port="nodes"),
        # The cleaned/all split, deliberate and documented on `clean_cycles`:
        # co-occurrence and clustering keep real-but-suppressed citations, so
        # they take `all_cites`; depth and pagerank need the ACYCLIC view, so
        # they take `cleaned_edges`. A suppressed edge would shorten hop
        # distances and blur the direction categories.
        Edge(source=CLEAN, target=CO_CITATIONS, from_port="all_cites", to_port="all_cites"),
        Edge(source=CLEAN, target=COMMUNITIES, from_port="all_cites", to_port="all_cites"),
        Edge(source=CLEAN, target=DEPTH, from_port="cleaned_edges", to_port="cleaned_edges"),
        Edge(source=CLEAN, target=PAGERANK, from_port="cleaned_edges", to_port="cleaned_edges"),
        # Post-5.5 consumers of the node set. All four bind `annotate.nodes`,
        # which is the annotated copies on the LLM path and the pre-annotation
        # set forwarded by the disabled passthrough otherwise.
        Edge(source=ANNOTATE, target=DEPTH, from_port="nodes", to_port="nodes"),
        Edge(source=ANNOTATE, target=PAGERANK, from_port="nodes", to_port="nodes"),
        Edge(source=ANNOTATE, target=COMMUNITIES, from_port="nodes", to_port="nodes"),
        Edge(source=ANNOTATE, target=ENRICH, from_port="nodes", to_port="nodes"),
        # The four-input join. The three metric ports and the node set must
        # describe one graph, which is why enrichment binds the same `nodes`
        # the metrics were computed over rather than reaching back to `assemble`.
        Edge(source=DEPTH, target=ENRICH, from_port="depth_metrics", to_port="depth_metrics"),
        Edge(source=PAGERANK, target=ENRICH, from_port="pagerank", to_port="pagerank"),
        Edge(source=COMMUNITIES, target=ENRICH, from_port="communities", to_port="communities"),
    ]
