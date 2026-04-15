# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

from idiograph.domains.arxiv.models import PaperRecord, make_node_id


class TestMakeNodeId:
    def test_prefers_arxiv_id(self):
        work = {
            "id": "https://openalex.org/W2045435533",
            "ids": {
                "arxiv": "https://arxiv.org/abs/2301.07041",
                "doi": "https://doi.org/10.1234/example",
                "openalex": "https://openalex.org/W2045435533",
            },
        }
        assert make_node_id(work) == "arxiv:2301.07041"

    def test_falls_back_to_doi_when_no_arxiv(self):
        work = {
            "id": "https://openalex.org/W2045435533",
            "ids": {
                "doi": "https://doi.org/10.1234/example",
                "openalex": "https://openalex.org/W2045435533",
            },
        }
        assert make_node_id(work) == "doi:https://doi.org/10.1234/example"

    def test_falls_back_to_openalex_when_no_arxiv_no_doi(self):
        work = {
            "id": "https://openalex.org/W2045435533",
            "ids": {"openalex": "https://openalex.org/W2045435533"},
        }
        assert make_node_id(work) == "openalex:W2045435533"

    def test_handles_missing_ids_dict(self):
        work = {"id": "https://openalex.org/W999"}
        assert make_node_id(work) == "openalex:W999"


class TestPaperRecord:
    def test_minimal_construction(self):
        rec = PaperRecord(
            node_id="arxiv:2301.07041",
            openalex_id="W2045435533",
            title="A paper",
            hop_depth=0,
        )
        assert rec.node_id == "arxiv:2301.07041"
        assert rec.hop_depth == 0
        assert rec.authors == []
        assert rec.root_ids == []
        assert rec.citation_count == 0
        assert rec.community_id is None
        assert rec.pagerank is None
