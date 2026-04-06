# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

# src/idiograph/domains/arxiv/__init__.py
from idiograph.core.executor import register_handler
from idiograph.domains.arxiv.handlers import (
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