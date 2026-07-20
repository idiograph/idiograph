# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Headless viewer projections — the renderer's data contract, produced without
a browser.

This subpackage is the reusable producer half of the presentation surface: it
turns a persisted :class:`~idiograph.domains.arxiv.models.PipelineResult` into
the D3 data contract a renderer consumes, computing deterministic geometry in
Python so the same artifact always yields the same emitted JSON (the determinism
thesis, extended to the renderer). It is viewer-agnostic — a later FastAPI/SSE
layer wraps :func:`project_depth_provenance` directly; the static generator in
``idiograph.apps.viewer`` is just the first such wrapper.
"""

from idiograph.domains.viewer.projection import project_depth_provenance

__all__ = ["project_depth_provenance"]
