# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Static viewer app — the one renderer over the headless projections.

``idiograph.apps.viewer`` is presentation: it loads a persisted artifact through
the registry read path, runs a headless producer from
:mod:`idiograph.domains.viewer`, and inlines the result into a single
self-contained HTML file (vendored D3 v7 + CSS + JS, no network, no serving
layer). Invoke it via ``python -m idiograph.apps.viewer [--view ...]``.

TWO VIEWS, ONE RENDERER. ``--view depth-provenance`` (the default) draws the
artifact the pipeline produced; ``--view declared-graph`` draws the pipeline's
own declared ``Graph``. Both go through :func:`render_projection_html` and the
same vendored D3 canvas, tooltip and panel chrome — the instrument is unchanged
and is simply turned on itself.
"""

from idiograph.apps.viewer.generate import (
    generate_graph_viewer_html,
    generate_viewer_html,
    render_graph_viewer,
    render_projection_html,
    render_viewer,
)

__all__ = [
    "generate_graph_viewer_html",
    "generate_viewer_html",
    "render_graph_viewer",
    "render_projection_html",
    "render_viewer",
]
