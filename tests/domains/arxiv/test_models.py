# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

import hashlib

from idiograph.domains.arxiv.models import (
    TRAVERSAL_CONTRACT,
    PaperRecord,
    make_node_id,
    traversal_contract_hash,
)


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


# ── The Node 3 traversal contract (IDG-091 clause 1 / IDG-043) ───────────────
#
# TRAVERSAL_CONTRACT declares what `backward_traverse`/`_node3_score` decide
# about derived output that no PipelineParameters field spells out: the score
# formula and its RULED depth term, the cap rule and its tie-break, the
# cap-then-edge-filter ordering, the orphan rule, and the declared ABSENCE of a
# selection predicate. The descriptor is the sha256 of that text, so these tests
# pin the one property the descriptor rests on — that it is DERIVED from the live
# constant rather than transcribed, which is what makes an amendment to the rule
# move the hash on its own (IDG-032).


class TestTraversalContractHash:
    def test_default_is_the_sha256_of_the_live_constant(self):
        """The deriver's default is computed, not a literal agreeing with nothing.

        The expectation is built here from `hashlib` directly rather than by
        calling the deriver a second way: a test that computes its expectation
        from its subject would pass against a hardcoded return.
        """
        expected = hashlib.sha256(TRAVERSAL_CONTRACT.encode("utf-8")).hexdigest()
        assert traversal_contract_hash() == expected
        assert traversal_contract_hash(TRAVERSAL_CONTRACT) == expected

    def test_amending_the_contract_moves_the_hash(self):
        """Editing the declared rule moves the descriptor — no version integer.

        This is the property that makes the DECLARED-ABSENT selection rule
        load-bearing: landing the IDG-042 `required_root_ids` predicate means
        editing this text, and editing the text moves the hash.
        """
        assert traversal_contract_hash(TRAVERSAL_CONTRACT + "\nEDIT") != (
            traversal_contract_hash(TRAVERSAL_CONTRACT)
        )
