# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from pydantic import BaseModel, Field


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
    topological_depth: int | None = Field(
        default=None,
        description="Longest path from root in cycle-cleaned DAG. Assigned by Node 6. Null for nodes involved in suppressed cycles.",
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

    source_id: str = Field(description="node_id of edge source.")
    target_id: str = Field(description="node_id of edge target.")
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
        """node_ids whose topological_depth must be null downstream (Node 6 handoff)."""
        result: set[str] = set()
        for e in self.suppressed_edges:
            result.add(e.source_id)
            result.add(e.target_id)
        return result


class CycleCleanResult(BaseModel):
    """Return value of clean_cycles(). Separates cleaned graph from audit log."""

    cleaned_edges: list[CitationEdge] = Field(
        description="Edge set with cycle-breaking edges removed. Safe for DAG algorithms."
    )
    cycle_log: CycleLog = Field(description="Audit trail of what was removed and why.")


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
