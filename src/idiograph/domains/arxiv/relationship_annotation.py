# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Node 5.5 — Semantic Relationship Annotation (the pipeline's first LLM node).

For each non-seed paper in the assembled graph, classify its intellectual
relationship to the *seed set as a whole* into one bounded ``RelationshipType``
label, with a confidence. The node is a demonstration of restraint: the LLM is
invoked only where no deterministic method suffices, and everywhere adjacent the
graph decides deterministically —

- whether it runs at all: the IDG-036 text-presence guard (``text_route``);
- what labels it may emit: the IDG-034 ``Literal``, enforced on
  ``RelationshipAnnotation`` construction (NOT on the ``model_copy`` write, which
  does not re-run validators — finding 93486254);
- whether it re-runs: IDG-035 miss-gating by placement inside ``run_traversal``.

``relationship_type`` is a leaf annotation consumed by the renderer; it does not
feed Node 6 or Node 7 and does not depend on any enrichment field
(``pagerank``/``community_id``/``traversal_direction``), none of which are
populated at the Node 5→6 seam.
"""

import hashlib
import json
import logging
from enum import Enum

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from idiograph.domains.arxiv.models import LLMConfig, PaperRecord, RelationshipType

_log = logging.getLogger(__name__)


# ── Typed form (IDG-034 enforcement point) ──────────────────────────────────


class RelationshipAnnotation(BaseModel):
    """The typed form the model fills; Pydantic is the bound.

    Enforcement sits HERE, on construction — an off-vocabulary
    ``relationship_type`` or an out-of-range ``semantic_confidence`` raises
    ``ValidationError``. This is deliberate: the persistence idiom
    (``model_copy(update=...)``) does not re-run validators, so validating on
    this intermediate form is what keeps a bad label from silently reaching a
    ``PaperRecord`` field.
    """

    model_config = ConfigDict(frozen=True)

    relationship_type: RelationshipType = Field(
        ..., description="One of the eight labels — off-vocabulary REJECTED here."
    )
    semantic_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="0.0–1.0; out-of-range REJECTED here."
    )
    reasoning: str = Field(
        default="",
        description="Short rationale, for provenance only; not persisted on "
        "PaperRecord.",
    )


# ── Deterministic text-presence guard (IDG-036) ─────────────────────────────


class Route(Enum):
    """Deterministic routing decision for a single record."""

    NO_TEXT = "no_text"
    CLASSIFY = "classify"


def text_route(rec: PaperRecord) -> Route:
    """Route a record on a mechanical ``(title, abstract)`` presence check.

    The guard fires ONLY on the conjunction: ``title`` falsy AND ``abstract``
    null/empty → NO_TEXT (assign ``"unclear"``, no model call). A present title
    alone always routes to CLASSIFY — an academic title carries substantial
    classifiable signal, and discarding it would over-apply restraint. The LLM
    never decides whether it runs.
    """
    has_title = bool(rec.title and rec.title.strip())
    has_abstract = rec.abstract is not None and rec.abstract.strip() != ""
    if not has_title and not has_abstract:
        return Route.NO_TEXT
    return Route.CLASSIFY


# ── Prompt (its sha256 is the LLMConfig.prompt_template_hash) ────────────────

PROMPT_TEMPLATE = """\
You are classifying one candidate academic paper by its intellectual \
relationship to a fixed SEED SET of papers. Choose exactly ONE label from the \
closed vocabulary below — the single best fit for how the candidate relates to \
the seed set as a whole.

Labels (choose exactly one):
- methodological_precursor: introduces a method/technique the seed work builds on.
- theoretical_foundation: supplies theory/framework the seed work rests on.
- cross_domain_source: imports an idea from a different field into the seed's area.
- downstream_application: applies or extends the seed work to a new problem.
- empirical_validation: tests, replicates, or benchmarks the seed work's claims.
- concurrent_work: independent, roughly simultaneous work on the same problem.
- adjacent_work: related in topic but not in the direct lineage above.
- unclear: the relationship cannot be determined from the available text. Choose \
this ONLY when the text genuinely does not support a judgement — not as a \
catch-all to avoid deciding.

SEED SET:
{seed_context}

CANDIDATE PAPER:
Title: {title}
Abstract: {abstract}

Respond with a strict JSON object and NOTHING else — no prose, no markdown, no \
code fences. The object must have exactly these keys:
  "relationship_type": one of the labels above (string),
  "semantic_confidence": a number from 0.0 to 1.0,
  "reasoning": a brief (one sentence) justification (string).
"""


def prompt_template_hash(template: str = PROMPT_TEMPLATE) -> str:
    """sha256 of the prompt template — the ``LLMConfig.prompt_template_hash``.

    Derive, don't hardcode (IDG-032): the caller computes this over the module
    ``PROMPT_TEMPLATE`` when constructing ``LLMConfig``, so any prompt edit moves
    the content address automatically.
    """
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


# ── Provenance (IDG-016) ─────────────────────────────────────────────────────

_ABSTRACT_ABSENT = "(no abstract provided)"
_SEED_CONTEXT_CAP = 20  # keep seed-set token cost bounded
_RAW_LOG_TRUNCATE = 200


class RelationshipProvenance(BaseModel):
    """Auditable record of a Node 5.5 run (IDG-016).

    The three-way ``unclear`` split is the contract that lets an auditor
    distinguish "the graph never asked" (``no_classifiable_text``) from "the
    model was asked and shrugged" (``model_unclear``) from "the model answered
    off-contract" (``model_output_invalid``) — deterministic restraint vs. model
    uncertainty vs. bad output.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str
    prompt_template_hash: str
    temperature: float
    max_tokens: int
    papers_total: int = 0
    papers_classified: int = 0
    call_count: int = 0
    unclear_no_classifiable_text: int = 0
    unclear_model_output_invalid: int = 0
    unclear_model_unclear: int = 0

    @property
    def unclear_total(self) -> int:
        return (
            self.unclear_no_classifiable_text
            + self.unclear_model_output_invalid
            + self.unclear_model_unclear
        )


class RelationshipAnnotationResult(BaseModel):
    """Return value of :func:`annotate_relationships`: the annotated node list
    (via ``model_copy`` — input is not mutated) plus the provenance record."""

    model_config = ConfigDict(frozen=True)

    nodes: list[PaperRecord]
    provenance: RelationshipProvenance


class _ProvenanceAccumulator:
    """Mutable tally folded into a frozen ``RelationshipProvenance`` at the end."""

    def __init__(self, llm_config: LLMConfig, papers_total: int) -> None:
        self._config = llm_config
        self.papers_total = papers_total
        self.papers_classified = 0
        self.call_count = 0
        self.no_classifiable_text = 0
        self.model_output_invalid = 0
        self.model_unclear = 0

    def count_no_text(self) -> None:
        self.no_classifiable_text += 1

    def count_invalid(self) -> None:
        self.call_count += 1
        self.model_output_invalid += 1

    def count_call(self, ann: RelationshipAnnotation) -> None:
        self.call_count += 1
        self.papers_classified += 1
        if ann.relationship_type == "unclear":
            self.model_unclear += 1

    def finalize(self) -> RelationshipProvenance:
        return RelationshipProvenance(
            model_id=self._config.model_id,
            prompt_template_hash=self._config.prompt_template_hash,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            papers_total=self.papers_total,
            papers_classified=self.papers_classified,
            call_count=self.call_count,
            unclear_no_classifiable_text=self.no_classifiable_text,
            unclear_model_output_invalid=self.model_output_invalid,
            unclear_model_unclear=self.model_unclear,
        )


# ── Prompt rendering + model call ────────────────────────────────────────────


def _seed_context(resolved: list[PaperRecord]) -> str:
    """Compact seed-set framing: seed titles, capped for token cost."""
    titles = [r.title.strip() for r in resolved if r.title and r.title.strip()]
    shown = titles[:_SEED_CONTEXT_CAP]
    lines = [f"- {t}" for t in shown]
    if len(titles) > _SEED_CONTEXT_CAP:
        lines.append(f"- (+{len(titles) - _SEED_CONTEXT_CAP} more seed papers)")
    return "\n".join(lines) if lines else "(no seed titles available)"


def _render_prompt(rec: PaperRecord, seed_context: str) -> str:
    abstract = (
        rec.abstract if (rec.abstract and rec.abstract.strip()) else _ABSTRACT_ABSENT
    )
    return PROMPT_TEMPLATE.format(
        seed_context=seed_context,
        title=rec.title,
        abstract=abstract,
    )


async def _call_model(
    rec: PaperRecord,
    seed_context: str,
    llm_config: LLMConfig,
    anthropic_client: AsyncAnthropic,
) -> str:
    """One model call for one CLASSIFY paper; returns the raw text draw.

    Baseline is one call per CLASSIFY paper (IDG-016), matching the provenance
    call-count granularity and keeping the record-replay corpus one-draw-per-paper.
    """
    prompt = _render_prompt(rec, seed_context)
    response = await anthropic_client.messages.create(
        model=llm_config.model_id,
        max_tokens=llm_config.max_tokens,
        temperature=llm_config.temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(response)


def _extract_text(response: object) -> str:
    """Concatenate text blocks from an Anthropic Message response."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "".join(parts)


# ── Node body ────────────────────────────────────────────────────────────────


async def annotate_relationships(
    unified_nodes: list[PaperRecord],
    resolved: list[PaperRecord],
    llm_config: LLMConfig,
    *,
    anthropic_client: AsyncAnthropic,
) -> RelationshipAnnotationResult:
    """Classify each non-seed paper's relationship to the seed set (Node 5.5).

    Seeds pass through with ``relationship_type=None`` (never classified).
    Non-seeds route through the deterministic ``text_route`` guard: NO_TEXT →
    ``"unclear"``/``0.0`` with zero model calls; CLASSIFY → one model call, its
    raw draw validated on ``RelationshipAnnotation`` construction (the
    enforcement point). A malformed or off-vocabulary draw maps to
    ``"unclear"``/``0.0`` (``model_output_invalid``) — no retry, no raise. The
    input list is never mutated; annotated copies are returned via ``model_copy``.
    """
    seed_ids = {r.node_id for r in resolved}
    non_seed_count = sum(1 for rec in unified_nodes if rec.node_id not in seed_ids)
    no_text_count = sum(
        1
        for rec in unified_nodes
        if rec.node_id not in seed_ids and text_route(rec) is Route.NO_TEXT
    )
    _log.info(
        "Node 5.5: annotating %d non-seed papers (model=%s, %d no-text skips)",
        non_seed_count,
        llm_config.model_id,
        no_text_count,
    )

    seed_context = _seed_context(resolved)
    prov = _ProvenanceAccumulator(llm_config, papers_total=non_seed_count)
    annotated: list[PaperRecord] = []

    for rec in unified_nodes:
        if rec.node_id in seed_ids:
            annotated.append(rec)  # seed: relationship_type stays None
            continue

        if text_route(rec) is Route.NO_TEXT:
            annotated.append(
                rec.model_copy(
                    update={
                        "relationship_type": "unclear",
                        "semantic_confidence": 0.0,
                    }
                )
            )
            prov.count_no_text()
            continue

        raw = await _call_model(rec, seed_context, llm_config, anthropic_client)
        try:
            ann = _parse_annotation(raw)  # ENFORCEMENT (Literal + range)
        except (ValidationError, ValueError):
            _log.warning(
                "Node 5.5: invalid model output for %s — raw=%r",
                rec.node_id,
                raw[:_RAW_LOG_TRUNCATE],
            )
            annotated.append(
                rec.model_copy(
                    update={
                        "relationship_type": "unclear",
                        "semantic_confidence": 0.0,
                    }
                )
            )
            prov.count_invalid()
            continue

        annotated.append(
            rec.model_copy(
                update={
                    "relationship_type": ann.relationship_type,
                    "semantic_confidence": ann.semantic_confidence,
                }
            )
        )
        prov.count_call(ann)

    provenance = prov.finalize()
    _log.info(
        "Node 5.5 complete: %d calls, %d unclear (%d no-text / %d invalid / %d model)",
        provenance.call_count,
        provenance.unclear_total,
        provenance.unclear_no_classifiable_text,
        provenance.unclear_model_output_invalid,
        provenance.unclear_model_unclear,
    )
    return RelationshipAnnotationResult(nodes=annotated, provenance=provenance)


def _parse_annotation(raw: str) -> RelationshipAnnotation:
    """Parse and validate a raw model draw into a ``RelationshipAnnotation``.

    ``model_validate_json`` would accept a bare top-level string; we require a
    JSON object, so a non-object draw raises ``ValueError`` → the
    ``model_output_invalid`` path. Enforcement of the label vocabulary and the
    confidence range happens on ``RelationshipAnnotation`` construction.
    """
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return RelationshipAnnotation.model_validate(parsed)
