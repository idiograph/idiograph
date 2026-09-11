# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""The Node 3 traversal contract rides into the content address (IDG-032).

``TestTraversalContractHash`` in ``test_models.py`` pins the DERIVER — that the
hash is computed over the live ``TRAVERSAL_CONTRACT`` and moves when the text is
amended. That is a property of a function nothing was obliged to call. These
tests pin the other half: the hash is a ``PipelineParameters`` field, so the
declared contract reaches the ADDRESS, and amending it re-addresses the run.

The gap that leaves matters. Node 3's score formula, cap rule and edge-filter
ordering decide which papers survive and which edges are emitted; ``n_backward``
and ``lambda_decay`` feed that formula but do not state it. Before the field
landed, a silently amended score returned a different corpus under an unchanged
address — a record-replay soundness break of exactly the shape the parse
contract closed for Node 5.5. These are the tests that hold it closed.

Offline and synthetic throughout: ``content_address`` is pure, so no record, no
network and no credential is involved.
"""

import hashlib
import json

from idiograph.domains.arxiv.models import (
    TRAVERSAL_CONTRACT,
    BackwardParameters,
    ForwardParameters,
    LLMConfig,
    PipelineParameters,
    traversal_contract_hash,
)
from idiograph.domains.arxiv.registry import content_address


def _llm_config(model_id: str = "m") -> LLMConfig:
    return LLMConfig(model_id=model_id, prompt_template_hash="ph")


def _params(
    llm: LLMConfig | None = None, *, traversal_hash: str | None = None
) -> PipelineParameters:
    """Params under test; ``traversal_hash=None`` lets the real default_factory fill it."""
    extra = {} if traversal_hash is None else {"traversal_contract_hash": traversal_hash}
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        # Stated, never read from the clock: it enters the content address, so a
        # wall-clock value would move every address in this file on New Year.
        current_year=2026,
        llm=llm,
        **extra,
    )


def _addr_from_dump(seeds: list[str], params_dump: dict) -> str:
    payload = {"seeds": sorted(set(seeds)), "parameters": params_dump}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_field_default_is_derived_from_the_live_constant() -> None:
    """The field is populated by the deriver, not by whatever a caller passed.

    The expectation is built from ``hashlib`` directly rather than by calling the
    deriver again: a test that computes its expectation from its subject would
    pass against a hardcoded return.
    """
    expected = hashlib.sha256(TRAVERSAL_CONTRACT.encode("utf-8")).hexdigest()
    assert _params().traversal_contract_hash == expected


def test_traversal_contract_edit_moves_address() -> None:
    """A different traversal_contract_hash → a different content address.

    THE pin of this module. Amending the declared Node 3 rule moves the hash
    (``test_models.py``), and this is where that movement reaches the address:
    an amended contract can no longer return a different corpus under the
    address the old contract earned.
    """
    seeds = ["S"]
    base = _params(llm=_llm_config())
    edited = _params(
        llm=_llm_config(),
        traversal_hash=traversal_contract_hash(TRAVERSAL_CONTRACT + "\nEDIT"),
    )

    assert base.traversal_contract_hash != edited.traversal_contract_hash
    assert content_address(seeds, base) != content_address(seeds, edited)


def test_identical_contract_holds_the_address_still() -> None:
    """The other direction: same hash → same address, so the pin is not vacuous.

    Without this, a ``content_address`` that hashed the wall clock would satisfy
    the inequality above and prove nothing about the contract.
    """
    seeds = ["S"]
    assert content_address(seeds, _params()) == content_address(seeds, _params())


def test_whitespace_only_reflow_moves_the_address() -> None:
    """Even a re-wrap with no word changed re-addresses the run.

    Stated explicitly because it is the property the wiring ACQUIRED, not one it
    assumed: before the field landed, reflowing a contract line moved nothing and
    no test fired. The hash is over the bytes, so the address is too — there is
    no "cosmetic" edit to this constant.
    """
    seeds = ["S"]
    reflowed = TRAVERSAL_CONTRACT.replace(
        "How Node 3 turns its fetched population into the papers and edges every later\nstage derives from:",
        "How Node 3 turns its fetched population into the papers and edges\nevery later stage derives from:",
    )
    assert reflowed != TRAVERSAL_CONTRACT  # the substitution actually landed
    assert reflowed.split() == TRAVERSAL_CONTRACT.split()  # and changed no word

    edited = _params(traversal_hash=traversal_contract_hash(reflowed))
    assert content_address(seeds, _params()) != content_address(seeds, edited)


def test_llm_free_address_carries_traversal_contract_hash() -> None:
    """The hash survives the LLM-free path, where LLMConfig pops to nothing.

    This is why the descriptor lives on PipelineParameters and not nested: the
    ``_serialize`` wrap drops a null ``llm`` from the dump, and Node 3 runs on
    every derivation whether or not a draw is ever made. A traversal hash that
    vanished from LLM-free addresses would leave the exact runs the demo replays
    unprotected.
    """
    params = _params(llm=None)
    dump = params.model_dump(mode="json")

    assert "llm" not in dump  # LLMConfig popped …
    assert dump["traversal_contract_hash"] == traversal_contract_hash(
        TRAVERSAL_CONTRACT
    )  # … this did not

    # and it is load-bearing: drop it and the address moves.
    without = {k: v for k, v in dump.items() if k != "traversal_contract_hash"}
    assert content_address(["S"], params) != _addr_from_dump(["S"], without)


def test_traversal_contract_edit_moves_llm_free_address() -> None:
    """Same, end-to-end: an LLM-free derivation's address tracks the Node 3 rule."""
    seeds = ["S"]
    base = _params(llm=None)
    edited = _params(
        llm=None, traversal_hash=traversal_contract_hash(TRAVERSAL_CONTRACT + "\nEDIT")
    )

    assert content_address(seeds, base) != content_address(seeds, edited)
