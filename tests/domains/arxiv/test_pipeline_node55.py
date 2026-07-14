# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Node 5.5 — semantic relationship annotation (the pipeline's first LLM node).

No live API: a fake ``AsyncAnthropic`` returns scripted payloads and records its
call count. Async is driven with ``asyncio.run`` per the module idiom (the suite
does not depend on pytest-asyncio). Synthetic ``PaperRecord`` fixtures.
"""

import asyncio
import hashlib
import json

import pytest

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    PARSE_CONTRACT,
    BackwardParameters,
    ForwardParameters,
    LLMConfig,
    Node3Result,
    Node4Result,
    PaperRecord,
    PipelineParameters,
    parse_contract_hash,
)
from idiograph.domains.arxiv.registry import content_address
from idiograph.domains.arxiv.relationship_annotation import (
    PROMPT_TEMPLATE,
    RelationshipAnnotation,
    Route,
    annotate_relationships,
    prompt_template_hash,
    text_route,
)


# ── Fake Anthropic client ────────────────────────────────────────────────────


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, payloads: list[str] | str) -> None:
        self._payloads = payloads
        self._i = 0
        self.calls: list[dict] = []

    async def create(self, *, model, max_tokens, temperature, messages):
        self.calls.append(
            {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
        )
        if isinstance(self._payloads, str):
            text = self._payloads
        else:
            text = self._payloads[min(self._i, len(self._payloads) - 1)]
            self._i += 1
        return _FakeResponse(text)


class _FakeClient:
    """Minimal stand-in for ``AsyncAnthropic`` — only ``.messages.create``."""

    def __init__(self, payloads: list[str] | str = "") -> None:
        self.messages = _FakeMessages(payloads)

    @property
    def call_count(self) -> int:
        return len(self.messages.calls)


# ── Fixtures / helpers ───────────────────────────────────────────────────────


def _rec(
    node_id: str,
    *,
    title: str = "A paper title",
    abstract: str | None = "An abstract.",
    hop_depth: int = 1,
) -> PaperRecord:
    return PaperRecord(
        node_id=node_id,
        openalex_id=node_id.replace(":", "_"),
        title=title,
        abstract=abstract,
        hop_depth=hop_depth,
        root_ids=[node_id],
    )


def _seed(node_id: str) -> PaperRecord:
    return _rec(node_id, title=f"Seed {node_id}", hop_depth=0)


def _valid_payload(
    label: str = "downstream_application", confidence: float = 0.8
) -> str:
    return json.dumps(
        {
            "relationship_type": label,
            "semantic_confidence": confidence,
            "reasoning": "because",
        }
    )


def _llm_config(model_id: str = "claude-haiku-4-5-20251001") -> LLMConfig:
    return LLMConfig(model_id=model_id, prompt_template_hash=prompt_template_hash())


def _annotate(
    unified_nodes: list[PaperRecord],
    resolved: list[PaperRecord],
    client: _FakeClient,
    *,
    config: LLMConfig | None = None,
):
    return asyncio.run(
        annotate_relationships(
            unified_nodes,
            resolved,
            config or _llm_config(),
            anthropic_client=client,
        )
    )


# ── Node-level behaviour ─────────────────────────────────────────────────────


def test_seed_papers_not_classified() -> None:
    """Seeds keep relationship_type=None; no model call for a seed."""
    seed = _seed("S")
    client = _FakeClient(_valid_payload())

    result = _annotate([seed], [seed], client)

    (out,) = result.nodes
    assert out.relationship_type is None
    assert out.semantic_confidence is None
    assert client.call_count == 0


def test_no_text_routes_to_unclear_no_call() -> None:
    """title '' + abstract None → 'unclear'/0.0, ZERO calls."""
    rec = _rec("P", title="", abstract=None)
    client = _FakeClient(_valid_payload())

    assert text_route(rec) is Route.NO_TEXT
    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert out.semantic_confidence == 0.0
    assert client.call_count == 0
    assert result.provenance.unclear_no_classifiable_text == 1


def test_title_only_classifies() -> None:
    """title present, abstract None → a call IS made; label applied."""
    rec = _rec(
        "P", title="A programmable dual-RNA-guided DNA endonuclease", abstract=None
    )
    client = _FakeClient(_valid_payload("methodological_precursor", 0.55))

    assert text_route(rec) is Route.CLASSIFY
    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert client.call_count == 1
    assert out.relationship_type == "methodological_precursor"
    assert out.semantic_confidence == 0.55


def test_valid_label_applied() -> None:
    """Scripted valid payload → fields written via model_copy."""
    rec = _rec("P")
    client = _FakeClient(_valid_payload("empirical_validation", 0.9))

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "empirical_validation"
    assert out.semantic_confidence == 0.9
    assert result.provenance.call_count == 1
    assert result.provenance.papers_classified == 1


def test_off_vocabulary_label_rejected() -> None:
    """Off-vocabulary label → construction raises → 'unclear'/model_output_invalid."""
    rec = _rec("P")
    client = _FakeClient(_valid_payload("totally_made_up_label", 0.9))

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert out.semantic_confidence == 0.0
    assert result.provenance.unclear_model_output_invalid == 1
    # the call WAS made (record-replay corpus records the bad draw)
    assert result.provenance.call_count == 1


def test_malformed_json_to_unclear() -> None:
    """Non-JSON model output → 'unclear'/model_output_invalid, no raise."""
    rec = _rec("P")
    client = _FakeClient("this is not json at all")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert result.provenance.unclear_model_output_invalid == 1


# ── Fence tolerance (parse contract v2) ──────────────────────────────────────
#
# The prompt forbids code fences; the model does not always comply. A fenced draw
# is a formatting slip, not a bad judgement, so it must reach its real label — and
# NOTHING beyond fence removal may become recoverable. Each "still invalid" case
# below pins an edge of that surface: the unwrap is not a find-the-JSON scrubber.


def test_fenced_json_draw_parses() -> None:
    """```json-fenced draw → its real label, not model_output_invalid."""
    rec = _rec("P")
    payload = _valid_payload("empirical_validation", 0.7)
    client = _FakeClient(f"```json\n{payload}\n```")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "empirical_validation"
    assert out.semantic_confidence == 0.7
    assert result.provenance.unclear_model_output_invalid == 0
    assert result.provenance.papers_classified == 1


def test_bare_fenced_draw_parses() -> None:
    """A fence with no info string (``` alone) unwraps too."""
    rec = _rec("P")
    client = _FakeClient(f"```\n{_valid_payload('concurrent_work', 0.6)}\n```")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "concurrent_work"
    assert result.provenance.unclear_model_output_invalid == 0


def test_single_line_fenced_draw_parses() -> None:
    """```{...}``` on one line: fence markers are still just fence markers."""
    rec = _rec("P")
    client = _FakeClient(f"```{_valid_payload('adjacent_work', 0.5)}```")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "adjacent_work"
    assert result.provenance.unclear_model_output_invalid == 0


def test_fenced_malformed_draw_still_invalid() -> None:
    """Fenced but not JSON → model_output_invalid, exactly as before the unwrap."""
    rec = _rec("P")
    client = _FakeClient("```json\nthis is not json at all\n```")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert result.provenance.unclear_model_output_invalid == 1


def test_fenced_off_vocabulary_label_still_rejected() -> None:
    """Unwrapping is strictly upstream of IDG-034 — it does not smuggle a label in."""
    rec = _rec("P")
    client = _FakeClient(f"```json\n{_valid_payload('totally_made_up_label')}\n```")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert result.provenance.unclear_model_output_invalid == 1


def test_unterminated_fence_still_invalid() -> None:
    """An opening fence with no closing fence is not a fenced block."""
    rec = _rec("P")
    client = _FakeClient(f"```json\n{_valid_payload('concurrent_work')}")

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert result.provenance.unclear_model_output_invalid == 1


def test_prose_wrapped_fence_still_invalid() -> None:
    """Prose around a fenced block → invalid. This is an unwrap, not a scrubber."""
    rec = _rec("P")
    client = _FakeClient(
        f"Sure! Here is my answer:\n```json\n{_valid_payload('concurrent_work')}\n```"
    )

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert result.provenance.unclear_model_output_invalid == 1


def test_prose_on_fence_opening_line_still_invalid() -> None:
    """The opening fence may carry an info string (```json), not a sentence."""
    rec = _rec("P")
    client = _FakeClient(
        f"```here is my answer\n{_valid_payload('concurrent_work')}\n```"
    )

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert result.provenance.unclear_model_output_invalid == 1


def test_confidence_out_of_range_rejected() -> None:
    """semantic_confidence=1.4 → invalid → model_output_invalid."""
    rec = _rec("P")
    client = _FakeClient(_valid_payload("concurrent_work", 1.4))

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert out.semantic_confidence == 0.0
    assert result.provenance.unclear_model_output_invalid == 1


def test_literal_enforced_not_bypassed_by_model_copy() -> None:
    """The bad label never reaches a PaperRecord field.

    Guards the model_copy-no-revalidate hole (finding 93486254): model_copy does
    NOT re-run validators, so a raw label written straight through would bypass
    the Literal. Enforcement therefore sits on RelationshipAnnotation.
    """
    bad_label = "not_a_real_relationship"
    rec = _rec("P")

    # The hole itself: model_copy accepts the bad label without validation.
    bypassed = rec.model_copy(update={"relationship_type": bad_label})
    assert bypassed.relationship_type == bad_label  # no revalidation happened

    # Enforcement on the typed form rejects it.
    with pytest.raises(Exception):
        RelationshipAnnotation.model_validate(
            {"relationship_type": bad_label, "semantic_confidence": 0.5}
        )

    # End to end: the bad label is mapped to 'unclear', never onto the record.
    client = _FakeClient(_valid_payload(bad_label, 0.9))
    result = _annotate([rec], [], client)
    (out,) = result.nodes
    assert out.relationship_type == "unclear"
    assert out.relationship_type != bad_label


def test_provenance_unclear_breakdown() -> None:
    """Provenance separates no_classifiable_text / model_output_invalid / model_unclear."""
    no_text = _rec("NT", title="", abstract=None)
    invalid = _rec("IV")
    modelunclear = _rec("MU")
    valid = _rec("OK")
    # order of CLASSIFY papers: IV, MU, OK (NT never calls)
    client = _FakeClient(
        [
            "not json",  # IV → model_output_invalid
            _valid_payload("unclear", 0.2),  # MU → model deliberately unclear
            _valid_payload("adjacent_work", 0.7),  # OK
        ]
    )

    result = _annotate([no_text, invalid, modelunclear, valid], [], client)
    prov = result.provenance

    assert prov.unclear_no_classifiable_text == 1
    assert prov.unclear_model_output_invalid == 1
    assert prov.unclear_model_unclear == 1
    assert prov.unclear_total == 3
    # three CLASSIFY papers made calls; NT made none.
    assert prov.call_count == 3
    assert client.call_count == 3


def test_call_count_excludes_no_text() -> None:
    """Call count == number of CLASSIFY papers, not total papers."""
    nodes = [
        _rec("A"),
        _rec("B", title="", abstract=None),  # NO_TEXT
        _rec("C"),
        _rec("D", title="", abstract="   "),  # NO_TEXT (whitespace-only)
    ]
    client = _FakeClient(_valid_payload())

    result = _annotate(nodes, [], client)

    assert result.provenance.papers_total == 4
    assert result.provenance.call_count == 2
    assert client.call_count == 2
    assert result.provenance.unclear_no_classifiable_text == 2


def test_input_not_mutated() -> None:
    """unified_nodes unchanged — annotation returns copies."""
    rec = _rec("P")
    nodes = [rec]
    client = _FakeClient(_valid_payload("cross_domain_source", 0.6))

    result = _annotate(nodes, [], client)

    # original untouched
    assert rec.relationship_type is None
    assert rec.semantic_confidence is None
    assert nodes[0] is rec
    # returned copy carries the annotation
    assert result.nodes[0] is not rec
    assert result.nodes[0].relationship_type == "cross_domain_source"


def test_enrichment_fields_not_required() -> None:
    """Classifier runs with pagerank/community_id/traversal_direction all None."""
    rec = _rec("P")
    assert rec.pagerank is None
    assert rec.community_id is None
    assert rec.traversal_direction is None
    client = _FakeClient(_valid_payload("theoretical_foundation", 0.75))

    result = _annotate([rec], [], client)

    (out,) = result.nodes
    assert out.relationship_type == "theoretical_foundation"


# ── Pipeline-level (llm-free skip) ───────────────────────────────────────────


def _params(
    llm: LLMConfig | None = None, *, parse_hash: str | None = None
) -> PipelineParameters:
    """Params under test; ``parse_hash=None`` lets the real default_factory fill it."""
    extra = {} if parse_hash is None else {"parse_contract_hash": parse_hash}
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        llm=llm,
        **extra,
    )


def _edge(source: str, target: str):
    from idiograph.domains.arxiv.models import CitationEdge

    return CitationEdge(source_id=source, target_id=target, type="cites", strength=None)


def test_llm_free_run_skips_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """parameters.llm is None → Node 5.5 not invoked; all records None."""
    resolved = [_seed("S")]
    n3 = Node3Result(papers=[_rec("B1")], edges=[_edge("S", "B1")])
    n4 = Node4Result(papers=[_rec("F1")], edges=[_edge("F1", "S")])

    async def _fake_backward(*a, **k):
        return n3

    async def _fake_forward(*a, **k):
        return n4

    called = {"annotate": False}

    async def _spy_annotate(*a, **k):
        called["annotate"] = True
        raise AssertionError(
            "annotate_relationships must not be called on an LLM-free run"
        )

    monkeypatch.setattr(pipeline, "backward_traverse", _fake_backward)
    monkeypatch.setattr(pipeline, "forward_traverse", _fake_forward)
    monkeypatch.setattr(pipeline, "annotate_relationships", _spy_annotate)

    result = asyncio.run(
        pipeline.run_traversal(
            resolved, _params(llm=None), client=object(), api_key="k"
        )
    )

    assert called["annotate"] is False
    assert all(n.relationship_type is None for n in result.nodes)


# ── Determinism / content-address contract ───────────────────────────────────


def _addr_from_dump(seeds: list[str], params_dump: dict) -> str:
    payload = {"seeds": sorted(set(seeds)), "parameters": params_dump}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_llmconfig_changes_content_address() -> None:
    """Different LLMConfig (model_id) → different address; identical → identical."""
    seeds = ["S"]
    a = _params(llm=_llm_config("model-a"))
    b = _params(llm=_llm_config("model-b"))
    a2 = _params(llm=_llm_config("model-a"))

    assert content_address(seeds, a) != content_address(seeds, b)
    assert content_address(seeds, a) == content_address(seeds, a2)


def test_llm_free_address_unchanged() -> None:
    """An llm=None run's address is governed by the non-llm fields alone.

    Build-verification item 2, outcome (ii): the model_serializer drops a null
    ``llm`` from the dump so the address is byte-identical to the pre-llm-field
    baseline (a dump with no ``llm`` key at all).
    """
    params = _params(llm=None)
    dump = params.model_dump(mode="json")
    assert "llm" not in dump  # serializer dropped the null field

    baseline_dump = {k: v for k, v in dump.items() if k != "llm"}
    assert content_address(["S"], params) == _addr_from_dump(["S"], baseline_dump)


def test_prompt_edit_moves_address() -> None:
    """Mutating PROMPT_TEMPLATE (hence its hash) → different address."""
    seeds = ["S"]
    base = LLMConfig(model_id="m", prompt_template_hash=prompt_template_hash())
    edited = LLMConfig(
        model_id="m",
        prompt_template_hash=prompt_template_hash(PROMPT_TEMPLATE + "\nEDIT"),
    )
    assert base.prompt_template_hash != edited.prompt_template_hash
    assert content_address(seeds, _params(llm=base)) != content_address(
        seeds, _params(llm=edited)
    )


# ── Parse contract in the content address (IDG-032) ──────────────────────────
#
# The parse rule decides derived output — fence tolerance turns a draw that used
# to land on `unclear`/model_output_invalid into a real label — but it does not
# touch PROMPT_TEMPLATE, so prompt_template_hash cannot carry it. Without its own
# address input, a pre-fix `unclear` HIT and a post-fix real-label MISS collide at
# one address: a record-replay soundness break. These tests pin the descriptor
# into the address, and pin it there on the LLM-free path too.


def test_parse_contract_hash_is_derived_not_hardcoded() -> None:
    """The default is the sha256 of the live PARSE_CONTRACT — amend it, it moves."""
    assert _params().parse_contract_hash == parse_contract_hash(PARSE_CONTRACT)
    assert parse_contract_hash(PARSE_CONTRACT + "\nEDIT") != parse_contract_hash(
        PARSE_CONTRACT
    )


def test_parse_contract_edit_moves_address() -> None:
    """Amending the parse contract → different address. The collision is closed."""
    seeds = ["S"]
    base = _params(llm=_llm_config())
    edited = _params(
        llm=_llm_config(),
        parse_hash=parse_contract_hash(PARSE_CONTRACT + "\nEDIT"),
    )

    assert content_address(seeds, base) != content_address(seeds, edited)


def test_llm_free_address_carries_parse_contract_hash() -> None:
    """The parse hash survives the LLM-free path, where LLMConfig pops to nothing.

    This is why the descriptor lives on PipelineParameters and NOT on LLMConfig:
    the ``_serialize`` wrap drops a null ``llm`` from the dump, so a parse hash
    nested there would vanish from the address exactly when no draw was made —
    while the parser still governs every derivation that parses one.
    """
    params = _params(llm=None)
    dump = params.model_dump(mode="json")

    assert "llm" not in dump  # LLMConfig popped …
    assert dump["parse_contract_hash"] == parse_contract_hash(PARSE_CONTRACT)  # … this did not

    # and it is load-bearing: drop it and the address moves.
    without = {k: v for k, v in dump.items() if k != "parse_contract_hash"}
    assert content_address(["S"], params) != _addr_from_dump(["S"], without)


def test_parse_contract_edit_moves_llm_free_address() -> None:
    """Same, end-to-end: an LLM-free derivation's address tracks the parse rule."""
    seeds = ["S"]
    base = _params(llm=None)
    edited = _params(
        llm=None, parse_hash=parse_contract_hash(PARSE_CONTRACT + "\nEDIT")
    )

    assert content_address(seeds, base) != content_address(seeds, edited)
