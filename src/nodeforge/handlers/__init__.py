from nodeforge.core.executor import register_handler
from nodeforge.handlers.arxiv import (
    fetch_abstract,
    llm_call,
    evaluator,
    llm_summarize,
    discard,
)


def register_all() -> None:
    """Register all known handlers with the executor."""
    register_handler("FetchAbstract", fetch_abstract)
    register_handler("LLMCall",       llm_call)
    register_handler("Evaluator",     evaluator)
    register_handler("LLMSummarize",  llm_summarize)
    register_handler("Discard",       discard)