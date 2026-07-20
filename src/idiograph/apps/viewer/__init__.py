# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Static viewer app — the first renderer over the headless projection.

``idiograph.apps.viewer`` is presentation: it loads a persisted artifact through
the registry read path, runs the headless
:func:`idiograph.domains.viewer.project_depth_provenance` producer, and inlines
the result into a single self-contained HTML file (vendored D3 v7 + CSS + JS, no
network, no serving layer). Invoke it via ``python -m idiograph.apps.viewer``.
"""

from idiograph.apps.viewer.generate import generate_viewer_html, render_viewer

__all__ = ["generate_viewer_html", "render_viewer"]
