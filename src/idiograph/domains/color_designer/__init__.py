# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Color Designer domain — handler registration and canonical pipeline.

Wires the Color Designer Qt app into the Idiograph executor. Handlers live
in `handlers.py`; the canonical pipeline graph lives in `pipeline.py`.
"""


def register_color_designer_handlers() -> None:
    """Explicit per-domain handler registration for the Color Designer pipeline."""
    from idiograph.core.executor import register_handler
    from idiograph.domains.color_designer.handlers import (
        array_assign,
        assign,
        color_array,
        color_swatch,
        schema,
        write_tokens,
    )
    register_handler("color_swatch", color_swatch)
    register_handler("color_array",  color_array)
    register_handler("schema",       schema)
    register_handler("assign",       assign)
    register_handler("array_assign", array_assign)
    register_handler("write_tokens", write_tokens)
