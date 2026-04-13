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

# DEPRECATED — test convenience only.
# Do NOT call from any production entry point. Production code must use
# `register_arxiv_handlers()` in `idiograph.domains.arxiv.handlers` so that
# each domain's registration is explicit at its boot site (spec:
# docs/specs/spec-color-designer-domain-refactor.md, "Handler Registration
# Pattern"). Retained here because integration tests register every domain
# at once via this shortcut.
def register_all() -> None:
    """Register all known handlers with the executor. Test convenience only."""
    register_handler("FetchAbstract", fetch_abstract)
    register_handler("LLMCall",       llm_call)
    register_handler("Evaluator",     evaluator)
    register_handler("LLMSummarize",  llm_summarize)
    register_handler("Discard",       discard)