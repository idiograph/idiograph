# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import xml.etree.ElementTree as ET

from idiograph.core.logging_config import get_logger

_log = get_logger("handlers.arxiv")

ARXIV_API = "https://export.arxiv.org/api/query"
_NS = "http://www.w3.org/2005/Atom"


async def fetch_abstract(params: dict, inputs: dict, *, resources: dict) -> dict:
    """Fetch paper metadata from the arXiv public API.

    The HTTP client arrives as a node-declared resource. This handler neither
    constructs one nor owns its lifecycle — including its timeout, which rides
    the client the composition root built. `resources` has no default: the node
    declares, so the executor always supplies, and a silent call without it
    would be a bug worth crashing on.
    """
    paper_id = params["paper_id"]
    client = resources["http_client"]
    r = await client.get(ARXIV_API, params={"id_list": paper_id})
    r.raise_for_status()
    root = ET.fromstring(r.text)
    entry = root.find(f"{{{_NS}}}entry")
    if entry is None:
        raise ValueError(f"Paper '{paper_id}' not found.")
    return {
        "paper_id": paper_id,
        "title": (entry.findtext(f"{{{_NS}}}title") or "").strip(),
        "abstract": (entry.findtext(f"{{{_NS}}}summary") or "").strip(),
        "authors": ", ".join(
            a.findtext(f"{{{_NS}}}name") or ""
            for a in entry.findall(f"{{{_NS}}}author")
        ),
    }


async def llm_call(params: dict, inputs: dict, *, resources: dict) -> dict:
    """Call Anthropic API. Prompt assembled from template + upstream inputs.

    The client arrives as a node-declared resource: no key is read from process
    environment here and no client is constructed here — both belong to the
    composition root.

    `model` and `max_tokens` are output-determining, so they are REQUIRED
    params read by subscript. A missing key raises. There is deliberately no
    `.get()` default: a silent fallback would let the graph claim one model
    while the run quietly used another.
    """
    upstream = next(iter(inputs.values()), {})
    prompt = params["prompt_template"].format(**{
        k: v for k, v in upstream.items() if isinstance(v, str)
    })
    client = resources["anthropic_client"]
    message = await client.messages.create(
        model=params["model"],
        max_tokens=params["max_tokens"],
        system=params.get("system", "You are a precise technical analyst."),
        messages=[{"role": "user", "content": prompt}],
    )
    return {"response": message.content[0].text, **upstream}


async def evaluator(params: dict, inputs: dict) -> dict:
    """Score upstream LLM response against keyword criteria defined in params."""
    upstream = next(iter(inputs.values()), {})
    response = upstream.get("response", "")
    keywords = params.get("keywords", [])
    threshold = params.get("threshold", 0.5)
    matched = [kw for kw in keywords if kw.lower() in response.lower()]
    score = len(matched) / len(keywords) if keywords else 0.0
    if score < threshold:
        raise ValueError(
            f"Score {score:.2f} below threshold {threshold}. Matched: {matched}"
        )
    return {"score": score, "matched_keywords": matched, **upstream}


async def llm_summarize(params: dict, inputs: dict, *, resources: dict) -> dict:
    """Generate a technical summary for papers that passed evaluation.

    Forwards to `llm_call`, so it declares the same resource and forwards it:
    a forwarder that does not declare would reach a callee that requires one.
    """
    return await llm_call(params, inputs, resources=resources)


async def discard(params: dict, inputs: dict) -> dict:
    """Terminal no-op. Records that this paper did not meet evaluation criteria."""
    upstream = next(iter(inputs.values()), {})
    paper_id = upstream.get("paper_id", "unknown")
    _log.info("Paper '%s' discarded — did not meet evaluation criteria.", paper_id)
    return {"discarded": True, "paper_id": paper_id}


def register_arxiv_handlers() -> None:
    """Explicit per-domain handler registration for the arXiv pipeline."""
    from idiograph.core.executor import register_handler
    # assemble_graph, backward_traverse, clean_cycles, compute_co_citations,
    # compute_depth_metrics, compute_pagerank, detect_communities and
    # forward_traverse live in pipeline.py (they are traversal stages, not
    # client-building handlers); imported here to keep registration explicit at
    # the domain boot site and to avoid a module-load import cycle.
    from idiograph.domains.arxiv.pipeline import (
        assemble_graph,
        backward_traverse,
        clean_cycles,
        compute_co_citations,
        compute_depth_metrics,
        compute_pagerank,
        detect_communities,
        forward_traverse,
    )
    # Node 5.5 lives in its own module rather than pipeline.py — it is the one
    # traversal stage with a model call in it — but registers the same way.
    from idiograph.domains.arxiv.relationship_annotation import (
        annotate_relationships,
    )
    register_handler("FetchAbstract",  fetch_abstract)
    register_handler("LLMCall",        llm_call)
    register_handler("Evaluator",      evaluator)
    register_handler("LLMSummarize",   llm_summarize)
    register_handler("Discard",        discard)
    register_handler("BackwardTraverse", backward_traverse)
    register_handler("ForwardTraverse", forward_traverse)
    register_handler("AssembleGraph",  assemble_graph)
    register_handler("CleanCycles",    clean_cycles)
    register_handler("ComputePagerank", compute_pagerank)
    register_handler("ComputeCoCitations", compute_co_citations)
    register_handler("ComputeDepthMetrics", compute_depth_metrics)
    register_handler("DetectCommunities", detect_communities)
    register_handler("AnnotateRelationships", annotate_relationships)
