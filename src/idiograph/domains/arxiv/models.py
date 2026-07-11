# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_serializer,
    model_validator,
)


RelationshipType = Literal[
    "methodological_precursor",
    "theoretical_foundation",
    "cross_domain_source",
    "downstream_application",
    "empirical_validation",
    "concurrent_work",
    "adjacent_work",
    "unclear",
]
"""Closed vocabulary for Node 5.5 semantic relationship annotation (IDG-034).

Shared alias reused by the typed-form model (``RelationshipAnnotation`` in
``relationship_annotation.py``) and any future renderer legend. ``None`` on a
``PaperRecord`` means *not classified* (a seed, or an LLM-free run); ``"unclear"``
means *classified as indeterminate*. These are distinct states.
"""


class DepthMetrics(BaseModel):
    """Per-node depth metrics produced by Node 6 compute_depth_metrics.

    Merged into PaperRecord at pipeline orchestrator layer via model_copy.
    """

    hop_depth_per_root: dict[str, int] = Field(
        description="Shortest-path distance from each reaching root, over the "
                    "undirected view of the cleaned citation graph. Key: root "
                    "node_id. Value: non-negative integer distance. A node's "
                    "own node_id appears with value 0 iff the node is a root."
    )
    traversal_direction: Literal["seed", "backward", "forward", "mixed"] = Field(
        description="Categorical position relative to the seed set. See AMD-019 "
                    "for vocabulary definitions."
    )


class CommunityResult(BaseModel):
    """Per-graph community partition produced by Node 7 detect_communities.

    community_assignments maps every input node_id to a community label
    (string-encoded module id). Merged into PaperRecord.community_id at
    the pipeline orchestrator layer via model_copy.
    """

    community_assignments: dict[str, str] = Field(
        description="Maps node_id -> community_id. Every input node appears "
                    "as a key. No node is omitted, including isolates."
    )
    algorithm_used: Literal["infomap", "leiden"] = Field(
        description="Which algorithm produced this partition. 'infomap' is "
                    "the primary; 'leiden' is the automatic fallback when "
                    "infomap is not installed."
    )
    community_count: int = Field(
        description="Number of distinct communities in the partition. Equal "
                    "to len(set(community_assignments.values()))."
    )
    validation_flags: list[str] = Field(
        default_factory=list,
        description="LOD validation warnings (e.g. "
                    "'community_count_below_minimum'). Empty list if "
                    "thresholds are satisfied. Never blocks execution."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Data-quality warnings from input validation. Each entry "
                    "names a node_id whose referencing edge was skipped due to "
                    "absence from nodes. Empty list if no unknown node_ids. "
                    "Never None. Distinct from validation_flags (those are "
                    "algorithm-configuration feedback; warnings is input-data "
                    "feedback)."
    )


class PaperRecord(BaseModel):
    # --- Identity ---
    node_id: str = Field(
        description="Canonical internal key. Format: 'arxiv:{id}', 'doi:{doi}', or 'openalex:{oa_id}'."
    )
    arxiv_id: str | None = Field(
        default=None,
        description="arXiv identifier. Null for papers predating arXiv or without arXiv presence.",
    )
    doi: str | None = Field(default=None, description="DOI. Null if unavailable.")
    openalex_id: str = Field(
        description="OpenAlex work ID (e.g. 'W2045435533'). Always present — OpenAlex is the data source."
    )

    # --- Metadata ---
    title: str = Field(description="Paper title.")
    year: int | None = Field(
        default=None, description="Publication year. Null if unavailable."
    )
    authors: list[str] = Field(
        default_factory=list, description="Author display names."
    )
    abstract: str | None = Field(
        default=None, description="Abstract text. Null if unavailable."
    )
    citation_count: int = Field(
        default=0, description="Total accumulated citations per OpenAlex."
    )

    # --- Traversal provenance ---
    hop_depth: int = Field(
        description="BFS distance from nearest seed at time of retrieval. 0 for seed nodes."
    )
    root_ids: list[str] = Field(
        default_factory=list,
        description="All root node_ids this node is reachable from. Required by AMD-017. Single-seed runs carry one entry.",
    )

    # --- Pipeline fields (populated by downstream nodes) ---
    community_id: str | None = Field(
        default=None,
        description="Assigned by Node 7 — Infomap community detection.",
    )
    pagerank: float | None = Field(
        default=None, description="Assigned by Node 6 — NetworkX PageRank."
    )
    hop_depth_per_root: dict[str, int] = Field(
        default_factory=dict,
        description="Assigned by Node 6 — shortest-path distance from each "
                    "reaching root over the undirected view of the cleaned "
                    "citation graph. Empty dict before Node 6 runs.",
    )
    traversal_direction: Literal["seed", "backward", "forward", "mixed"] | None = Field(
        default=None,
        description="Assigned by Node 6 — categorical position relative to the "
                    "seed set. See AMD-019.",
    )
    relationship_type: RelationshipType | None = Field(
        default=None,
        description="Semantic relationship to the seed set. Assigned by Node 5.5 "
                    "— closed vocabulary (IDG-034). None = not classified (seed or "
                    "LLM-free run); 'unclear' = classified as indeterminate.",
    )
    semantic_confidence: float | None = Field(
        default=None,
        description="Confidence score for relationship_type. Assigned by Node 5.5.",
    )

class CitationEdge(BaseModel):
    """Edge in the citation graph. Produced by traversal (cites) or derivation (co_citation).
    Schema is the frozen renderer data contract from spec-arxiv-pipeline-final.md."""

    source_id: str = Field(
        description="node_id of the citing paper (for cites edges) or one member "
                    "of the co-citation pair. References PaperRecord.node_id."
    )
    target_id: str = Field(
        description="node_id of the cited paper (for cites edges) or the other "
                    "member of the co-citation pair. References PaperRecord.node_id."
    )
    type: str = Field(
        description="Edge type. 'cites' for direct citation (fact, from OpenAlex "
                    "reference lists). 'co_citation' for derived relationship "
                    "(inference, from Node 5). Open string — not a closed enum — "
                    "to preserve Phase 10 causal semantics compatibility."
    )
    citing_paper_year: int | None = Field(
        default=None,
        description="Publication year of the citing paper. Not a citation-event "
                    "timestamp. Null when year is unavailable from OpenAlex."
    )
    strength: int | None = Field(
        default=None,
        description="Shared citing paper count within the local traversal boundary. "
                    "Populated for co_citation edges only. Null for cites edges. "
                    "Field is always present in the schema, never absent."
    )


class SuppressedEdge(BaseModel):
    """Record of a single edge removed during cycle cleaning."""

    original: CitationEdge = Field(
        description="The full CitationEdge that was removed. All original fields preserved "
                    "(type, citing_paper_year, strength) for downstream reconstruction."
    )
    citation_sum: int = Field(
        description="Sum of citation_count for source and target at removal time. "
                    "The weakest-link heuristic selected the edge with the minimum of this value."
    )
    cycle_members: list[str] = Field(
        description="node_ids of all nodes in the cycle this edge was breaking, in traversal order."
    )


class CycleLog(BaseModel):
    """Audit trail of cycle cleaning. Flows to Node 8 provenance metadata."""

    suppressed_edges: list[SuppressedEdge] = Field(
        default_factory=list,
        description="Every edge removed during cleaning, in order of removal."
    )
    cycles_detected_count: int = Field(
        description="Total cycles found across all iterations. May exceed len(suppressed_edges) "
                    "when one removal breaks multiple cycles."
    )
    iterations: int = Field(
        description="Number of find_cycle -> remove passes executed before the graph was clean."
    )

    @property
    def affected_node_ids(self) -> set[str]:
        """node_ids whose original edges were suppressed during cycle cleaning.

        Retained for audit and provenance (Node 8). Under AMD-019, Node 6 does
        not require this handoff — suppressed-cycle nodes receive normal depth
        metrics computed over the cleaned DAG.
        """
        result: set[str] = set()
        for e in self.suppressed_edges:
            result.add(e.original.source_id)
            result.add(e.original.target_id)
        return result


class CycleCleanResult(BaseModel):
    """Return value of clean_cycles(). Separates cleaned graph from audit log.

    Carries a witness of the input node set against which cleaned_edges has
    been validated. The witness is required at construction; the validator
    fires on every construction path, so the invariant 'every cleaned_edges
    endpoint is a node_id in the witness' holds whenever a CycleCleanResult
    exists. Downstream consumers (Node 5, Node 6, Node 7, Node 8) trust
    this contract and run no per-consumer defensive checks.

    The witness is excluded from model_dump() output and from repr() — it
    is structural metadata, not part of the serialized graph payload. A
    consequence: model_validate(model_dump(result)) raises ValidationError
    because the witness is missing from the dump and required on reload.
    Persistence reload sites must re-supply input_node_ids from the loaded
    node list. This is the contract Node 8 will honor.
    """

    cleaned_edges: list[CitationEdge] = Field(
        description="Edge set with cycle-breaking edges removed. Safe for DAG algorithms."
    )
    cycle_log: CycleLog = Field(description="Audit trail of what was removed and why.")
    input_node_ids: frozenset[str] = Field(
        exclude=True,
        repr=False,
        description="Witness of the node_id set this result was validated "
                    "against. Required at construction. Excluded from "
                    "model_dump() and repr(). Persistence reload sites "
                    "must re-supply this from the loaded node list.",
    )

    @model_validator(mode="after")
    def _validate_edge_endpoints(self) -> "CycleCleanResult":
        """Every cleaned_edges endpoint must be a node_id in the witness."""
        for e in self.cleaned_edges:
            if e.source_id not in self.input_node_ids:
                raise ValueError(
                    f"cleaned_edges contains orphaned source_id "
                    f"{e.source_id!r} on edge {e!r} — not present in "
                    f"input_node_ids witness"
                )
            if e.target_id not in self.input_node_ids:
                raise ValueError(
                    f"cleaned_edges contains orphaned target_id "
                    f"{e.target_id!r} on edge {e!r} — not present in "
                    f"input_node_ids witness"
                )
        return self


ForwardSort = Literal[
    "cited_by_count:desc",
    "cited_by_count:asc",
    "publication_date:desc",
    "publication_date:asc",
]


class FailedBatch(BaseModel):
    """Batch-level fetch failure during Node 3 backward traversal.

    Recorded when a call to ``_fetch_works_by_ids`` raises an HTTP error.
    Per-ID granularity is not available — the OpenAlex batch endpoint is
    atomic at the call boundary, so all IDs in the batch are recorded
    together. Callers consuming Node3Result decide whether the partial
    result is usable.
    """

    requested_ids: list[str] = Field(
        description="OpenAlex IDs requested in the failed batch (up to "
                    "batch_size in length)."
    )
    stage: Literal["seed_refetch", "depth_1", "depth_2"] = Field(
        description="Which traversal stage the batch belonged to."
    )
    reason: str = Field(
        description="Failure description (e.g., 'http_error: 503', 'timeout')."
    )


class Node3Result(BaseModel):
    """Return value of Node 3 backward traversal.

    Carries the ranked, capped paper set together with the citation edges
    discovered during traversal and any batch-level fetch failures. Edges
    cover seed→depth-1 and depth-1→depth-2 citations; their endpoints are
    either papers in ``papers`` or input seeds (seeds are excluded from
    ``papers`` per existing behavior but remain valid edge endpoints).
    """

    papers: list[PaperRecord] = Field(
        description="Backward-traversal papers, ranked and capped."
    )
    edges: list[CitationEdge] = Field(
        description="Citation edges discovered during traversal. Source "
                    "cites target. Includes seed→depth-1 and depth-1→depth-2 "
                    "edges. Edges are emitted only when both endpoints have "
                    "full PaperRecord metadata; failures to fetch metadata "
                    "are recorded in failed_batches instead."
    )
    failed_batches: list[FailedBatch] = Field(
        default_factory=list,
        description="Batch-level fetch failures. Empty list when no batches "
                    "failed. Each entry records up to batch_size OpenAlex "
                    "IDs that were requested but not retrieved. Per-ID "
                    "granularity is not available."
    )


class FailedSeed(BaseModel):
    """Per-seed forward-traversal call failure for Node 4."""

    seed_id: str = Field(
        description="The seed whose forward-traversal call failed."
    )
    reason: str = Field(
        description="Failure description (e.g., 'http_error: 503')."
    )


class TruncatedSeed(BaseModel):
    """Record of a seed whose citer count exceeded Node 4's per-seed cap.

    OpenAlex returns at most 200 citers per request without pagination;
    when the seed's actual citer count exceeds 200, the additional citers
    are silently dropped at fetch time. ``returned_count`` and
    ``total_count`` make the truncation auditable so callers can decide
    whether to paginate (deferred follow-up) or accept the partial result.
    """

    seed_id: str = Field(
        description="The seed whose forward-traversal hit the per-seed cap."
    )
    returned_count: int = Field(
        description="Citers actually returned (currently capped at 200)."
    )
    total_count: int = Field(
        description="Total citers reported by OpenAlex's response metadata. "
                    "When returned_count < total_count, "
                    "(total_count - returned_count) citers were silently "
                    "truncated."
    )


class Node4Result(BaseModel):
    """Return value of Node 4 forward traversal.

    Carries the ranked, capped citing-paper set together with the citer→seed
    citation edges and provenance for failure modes (per-seed call failures
    and per-seed truncation events). All edges have a citing paper in
    ``papers`` as source and an input seed as target.
    """

    papers: list[PaperRecord] = Field(
        description="Forward-traversal papers (citing papers), ranked and "
                    "capped."
    )
    edges: list[CitationEdge] = Field(
        description="Citation edges discovered during traversal. Source "
                    "cites target. Direction is citer → seed. Edges are "
                    "emitted only for papers in the returned papers list."
    )
    failed_seeds: list[FailedSeed] = Field(
        default_factory=list,
        description="Seeds whose forward-traversal call raised. Empty list "
                    "when no seeds failed. Distinct from succeeded-but-zero-"
                    "citers seeds, which produce no entry."
    )
    truncated_seeds: list[TruncatedSeed] = Field(
        default_factory=list,
        description="Seeds whose citer count exceeded the per-seed cap. "
                    "Empty list when no seeds were truncated."
    )


class Node5Result(BaseModel):
    """Return value of Node 5 co-citation computation.

    Carries the co-citation edge set together with data-quality warnings
    from input validation. Each warning names a ``node_id`` whose
    referencing edge was skipped due to absence from ``nodes``; entries are
    de-duplicated to one per distinct unknown ``node_id``, in first-encounter
    order. Both endpoints of every edge are checked, so a node appearing only
    as the target of an unknown-source edge is still recorded (Option A,
    IDG-023). ``warnings`` is always a list, never None.
    """

    edges: list[CitationEdge] = Field(
        description="Co-citation edges, sorted and capped per the function "
                    "contract. Does not include the input cites_edges."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Data-quality warnings from input validation. One entry "
                    "per distinct unknown node_id, in first-encounter order. "
                    "Empty list if no unknown node_ids encountered."
    )


class BackwardParameters(BaseModel):
    """Node 3 backward-traversal tunables. Field names match the
    ``backward_traverse`` kwargs exactly so the orchestrator maps them at the
    call site without renaming. No defaults — λ and N are TBD-pending-validation
    per the frozen pipeline spec, so the caller must supply them.
    """

    model_config = ConfigDict(frozen=True)

    n_backward: int = Field(
        ...,
        description="Cap on backward traversal results (global top-N by score "
                    "across all seeds).",
    )
    lambda_decay: float = Field(
        ..., description="Recency decay rate in the Node 3 score."
    )


class ForwardParameters(BaseModel):
    """Node 4 forward-traversal tunables. Field names match the
    ``forward_traverse`` kwargs exactly. No defaults — α, β, λ, N are
    TBD-pending-validation per the frozen pipeline spec.
    """

    model_config = ConfigDict(frozen=True)

    n_forward: int = Field(
        ...,
        description="Cap on forward traversal results (global top-N by score "
                    "across all seeds).",
    )
    lambda_decay: float = Field(
        ..., description="Recency decay rate in the Node 4 score."
    )
    alpha: float = Field(
        ..., description="Weight on citation_velocity in forward ranking."
    )
    beta: float = Field(
        ..., description="Weight on citation_acceleration in forward ranking."
    )
    sort: ForwardSort = Field(
        ...,
        description="OpenAlex sort order for the per-seed citing-paper query. "
                    "Required: OpenAlex's default sort is not contractual and "
                    "produces nondeterministic results. See AMD-020.",
    )


class CoCitationParameters(BaseModel):
    """Node 5 co-citation tunables. Defaults inherited from the frozen Node 5
    contract.
    """

    model_config = ConfigDict(frozen=True)

    min_strength: int = Field(
        2, description="Minimum shared citing papers to emit a co-citation edge."
    )
    max_edges: int | None = Field(
        None, description="Hard cap on total co-citation edges; None = no cap."
    )


class PageRankParameters(BaseModel):
    """Node 6 PageRank tunables. Defaults inherited from the frozen Node 6
    contract.
    """

    model_config = ConfigDict(frozen=True)

    damping: float = Field(
        0.85, description="PageRank damping factor (passed to nx.pagerank as alpha)."
    )


class CommunitiesParameters(BaseModel):
    """Node 7 community-detection tunables. Defaults inherited from the frozen
    Node 7 contract.
    """

    model_config = ConfigDict(frozen=True)

    infomap_seed: int = Field(42, description="Random seed for Infomap.")
    infomap_trials: int = Field(
        10, description="Number of Infomap optimization trials."
    )
    infomap_teleportation: float = Field(
        0.15, description="Teleportation probability for Infomap."
    )
    leiden_seed: int = Field(42, description="Random seed for Leiden fallback.")
    community_count_min: int = Field(
        5, description="Below-threshold flag for LOD validation."
    )
    community_count_max: int = Field(
        40, description="Above-threshold flag for LOD validation."
    )


class LLMConfig(BaseModel):
    """Node 5.5 model-configuration axis of the content address (IDG-032).

    This object IS the record-replay boundary's configuration: every decoding
    parameter that affects output participates in ``content_address`` via the
    nested ``model_dump``. ``prompt_template_hash`` is the sha256 of the module
    ``PROMPT_TEMPLATE`` constant — *derived, never hand-entered* — so editing the
    prompt moves the address automatically (no version integer to forget). Add a
    decoding param here only if it affects output; each one that does MUST be
    here. Frozen — an immutable config input.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(
        ..., description="Anthropic model id, e.g. 'claude-haiku-4-5-20251001'."
    )
    prompt_template_hash: str = Field(
        ...,
        description="sha256 of the module PROMPT_TEMPLATE constant. Derived via "
                    "relationship_annotation.prompt_template_hash(), never "
                    "hand-entered.",
    )
    temperature: float = Field(
        0.0, description="Decoding temperature (affects output → in the address)."
    )
    max_tokens: int = Field(
        512, description="Max output tokens (affects output → in the address)."
    )


class PipelineParameters(BaseModel):
    """Per-stage configuration for ``run_arxiv_pipeline``, as nested model
    objects. ``backward`` and ``forward`` are required; the rest default to the
    frozen per-node defaults. Frozen — an immutable config input.
    """

    model_config = ConfigDict(frozen=True)

    backward: BackwardParameters = Field(
        ..., description="Node 3 backward-traversal parameters."
    )
    forward: ForwardParameters = Field(
        ..., description="Node 4 forward-traversal parameters."
    )
    co_citation: CoCitationParameters = Field(
        default_factory=CoCitationParameters,
        description="Node 5 co-citation parameters.",
    )
    pagerank: PageRankParameters = Field(
        default_factory=PageRankParameters,
        description="Node 6 PageRank parameters.",
    )
    communities: CommunitiesParameters = Field(
        default_factory=CommunitiesParameters,
        description="Node 7 community-detection parameters.",
    )
    llm: LLMConfig | None = Field(
        default=None,
        description="Node 5.5 semantic-annotation config. Deliberately NOT a "
                    "default_factory (unlike the sibling per-stage params): a real "
                    "default would place non-null config into every run's "
                    "model_dump and change content_address for every existing "
                    "LLM-free run, violating IDG-032. None = LLM-free run (Node "
                    "5.5 skipped, address unchanged).",
    )

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """Omit ``llm`` from the dump when it is None so an LLM-free run's
        ``content_address`` is byte-identical to the pre-``llm``-field baseline
        (IDG-032 "LLMConfig=None → address-unchanged"; spec build-verification
        item 2, outcome (ii)).

        Narrow by design: a blanket ``exclude_none`` would also drop
        ``co_citation.max_edges`` (legitimately None) and re-baseline every
        existing address. A real ``llm`` config is always serialized in full —
        only the null case is dropped, so no LLM-run provenance is lost.
        """
        data = handler(self)
        if self.llm is None:
            data.pop("llm", None)
        return data


class SeedResolutionFailure(BaseModel):
    """Typed wrapper around Node 0's currently-untyped failure dicts, so the
    pipeline result is provenance-sufficient and round-trippable.
    """

    model_config = ConfigDict(frozen=True)

    seed: dict = Field(
        ..., description="The original seed identifier dict that failed to resolve."
    )
    reason: str = Field(
        ...,
        description="Failure description from Node 0 (e.g. 'http error: 503', "
                    "'no results', 'unrecognized seed shape').",
    )


class EdgeMetadataMismatch(BaseModel):
    """Record of a backward-vs-forward disagreement on the metadata of an edge
    sharing the same ``(source_id, target_id, type)`` key, caught during graph
    merge (OQ3). The first-seen (backward) edge is kept; this records the
    conflict rather than resolving it silently.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(
        ..., description="node_id of the citing paper on the conflicting edge."
    )
    target_id: str = Field(
        ..., description="node_id of the cited paper on the conflicting edge."
    )
    type: str = Field(..., description="Edge type at the conflicting key.")
    detail: str = Field(
        ...,
        description="Which fields differed between the backward and forward "
                    "views of this edge.",
    )


class PipelineResult(BaseModel):
    """Terminal output bundle of ``run_arxiv_pipeline`` — the merged citation
    graph, every per-stage result, the input parameters, and structured
    provenance. This is the input contract for Node 8 (registry persistence):
    it must survive ``model_dump()`` → ``model_validate()`` (with Node 8
    re-supplying the embedded ``CycleCleanResult.input_node_ids`` witness from
    the loaded node list) and answer "what produced this graph?" from the
    result alone. Frozen — a value object, not mutable working state.
    """

    model_config = ConfigDict(frozen=True)

    # --- Primary surface — the merged graph ---
    nodes: list[PaperRecord] = Field(
        ..., description="Fully enriched node set (seeds ∪ backward ∪ forward)."
    )
    edges: list[CitationEdge] = Field(
        ...,
        description="All edges (cites + co_citation), post-cycle-cleaning.",
    )
    seeds: list[str] = Field(
        ..., description="Successfully resolved seed node_ids."
    )

    # --- Per-stage results — audit and replay ---
    cycle_clean: CycleCleanResult = Field(
        ..., description="Node 4.5 cycle-cleaning result and audit log."
    )
    co_citation_edges: list[CitationEdge] = Field(
        ...,
        description="Co-citation subset of `edges`, called out for direct query.",
    )
    co_citation_warnings: list[str] = Field(
        default_factory=list,
        description="Node5Result.warnings (data-quality warnings from "
                    "co-citation input validation).",
    )
    depth_metrics: dict[str, DepthMetrics] = Field(
        ..., description="Node 6 depth metrics, keyed by node_id."
    )
    pagerank: dict[str, float] = Field(
        ..., description="Node 6 PageRank, keyed by node_id."
    )
    communities: CommunityResult = Field(
        ..., description="Node 7 community partition."
    )

    # --- Provenance — node-native failure records, surfaced first-class ---
    parameters: PipelineParameters = Field(
        ..., description="The exact PipelineParameters instance passed in."
    )
    seed_failures: list[SeedResolutionFailure] = Field(
        default_factory=list,
        description="Node 0 per-seed resolution failures.",
    )
    backward_failed_batches: list[FailedBatch] = Field(
        default_factory=list,
        description="Node 3 batch-level fetch failures (not seed-attributable "
                    "by design).",
    )
    forward_failed_seeds: list[FailedSeed] = Field(
        default_factory=list,
        description="Node 4 per-seed forward-traversal call failures.",
    )
    truncated_seeds: list[TruncatedSeed] = Field(
        default_factory=list,
        description="Node 4 per-seed citer-count truncation events (IDG-020 "
                    "surface).",
    )
    data_integrity_warnings: list[EdgeMetadataMismatch] = Field(
        default_factory=list,
        description="Backward-vs-forward edge-metadata mismatches caught during "
                    "graph merge (OQ3).",
    )


def make_node_id(work: dict) -> str:
    """Derive the canonical node_id from an OpenAlex work record.

    Priority: arxiv_id > doi > openalex_id.
    """
    ids = work.get("ids") or {}
    arxiv_url = ids.get("arxiv")
    if arxiv_url:
        arxiv_id = arxiv_url.rstrip("/").split("/")[-1]
        return f"arxiv:{arxiv_id}"
    doi = ids.get("doi")
    if doi:
        return f"doi:{doi}"
    return f"openalex:{work['id'].split('/')[-1]}"
