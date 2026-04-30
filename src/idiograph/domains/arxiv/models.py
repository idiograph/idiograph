# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    relationship_type: str | None = Field(
        default=None,
        description="Semantic relationship to seed. Assigned by Node 5.5 — closed vocabulary.",
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
