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
