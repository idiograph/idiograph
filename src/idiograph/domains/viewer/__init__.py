# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Headless viewer projections — the renderer's data contract, produced without
a browser.

This subpackage is the reusable producer half of the presentation surface: it
turns a subject into the D3 data contract a renderer consumes, computing
deterministic geometry in Python so the same subject always yields the same
emitted JSON (the determinism thesis, extended to the renderer). It is
viewer-agnostic — a later FastAPI/SSE layer wraps a projection directly; the
static generator in ``idiograph.apps.viewer`` is just the first such wrapper.

TWO SUBJECTS, ONE CONTRACT. Both projections emit the same three keys —
``{meta, nodes, edges}``, every node carrying its own ``(x, y)`` — and the
renderer selects its draw path on ``meta["view"]``:

* :func:`project_depth_provenance` reads a
  :class:`~idiograph.domains.arxiv.models.PipelineResult` — what the pipeline
  PRODUCED (Slice 1).
* :func:`project_graph` reads a declared :class:`~idiograph.core.models.Graph` —
  what the pipeline IS (Slice 2).

The shared contract is the point rather than a convenience: it is what lets the
one unchanged instrument be turned on its own declaration.
"""

from idiograph.domains.viewer.graph_projection import project_graph
from idiograph.domains.viewer.projection import project_depth_provenance

__all__ = ["project_depth_provenance", "project_graph"]
