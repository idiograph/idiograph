import os
import xml.etree.ElementTree as ET

import httpx
import anthropic

from idiograph.core.logging_config import get_logger

_log = get_logger("handlers.arxiv")

ARXIV_API = "https://export.arxiv.org/api/query"
_NS = "http://www.w3.org/2005/Atom"


async def fetch_abstract(params: dict, inputs: dict) -> dict:
    """Fetch paper metadata from the arXiv public API."""
    paper_id = params["paper_id"]
    async with httpx.AsyncClient(timeout=10.0) as client:
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


async def llm_call(params: dict, inputs: dict) -> dict:
    """Call Anthropic API. Prompt assembled from template + upstream inputs."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set.")
    upstream = next(iter(inputs.values()), {})
    prompt = params["prompt_template"].format(**{
        k: v for k, v in upstream.items() if isinstance(v, str)
    })
    client = anthropic.AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
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


async def llm_summarize(params: dict, inputs: dict) -> dict:
    """Generate a technical summary for papers that passed evaluation."""
    return await llm_call(params, inputs)


async def discard(params: dict, inputs: dict) -> dict:
    """Terminal no-op. Records that this paper did not meet evaluation criteria."""
    upstream = next(iter(inputs.values()), {})
    paper_id = upstream.get("paper_id", "unknown")
    _log.info("Paper '%s' discarded — did not meet evaluation criteria.", paper_id)
    return {"discarded": True, "paper_id": paper_id}
