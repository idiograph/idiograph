# src/idiograph/domains/arxiv/mock_handlers.py
"""
Stub handlers for --mock execution mode.
Return plausible structured output without API calls or network access.
The full pipeline executes: topological sort, node status progression,
results dict, failure handling — all demonstrable without credentials.
"""


async def mock_fetch_abstract(params: dict, inputs: dict) -> dict:
    return {
        "paper_id": params.get("paper_id", "1706.03762"),
        "title": "Attention Is All You Need",
        "authors": ["Vaswani", "Shazeer", "Parmar"],
        "abstract": (
            "We propose a new network architecture, the Transformer, based solely "
            "on attention mechanisms. The model achieves state-of-the-art results "
            "on machine translation tasks."
        ),
    }


async def mock_llm_call(params: dict, inputs: dict) -> dict:
    return {
        "claims": [
            "The Transformer outperforms recurrent architectures on WMT 2014 English-to-German translation.",
            "Attention mechanisms alone are sufficient to model sequence dependencies.",
            "The architecture trains faster than RNN-based models due to parallelism.",
        ]
    }


async def mock_evaluator(params: dict, inputs: dict) -> dict:
    threshold = params.get("threshold", 0.7)
    score = 0.85
    return {
        "score": score,
        "passed": score >= threshold,
        "threshold": threshold,
    }


async def mock_llm_summarize(params: dict, inputs: dict) -> dict:
    return {
        "summary": (
            "This paper introduces the Transformer architecture, replacing recurrence "
            "with self-attention. It demonstrates superior performance on translation "
            "benchmarks with reduced training time."
        )
    }


async def mock_discard(params: dict, inputs: dict) -> dict:
    return {"discarded": True, "reason": "score below threshold"}