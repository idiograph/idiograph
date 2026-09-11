# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Static-viewer generator — projection → self-contained HTML.

:func:`render_viewer` is the whole render path: load a persisted
:class:`~idiograph.domains.arxiv.models.PipelineResult` through the registry read
path, run the headless depth/provenance projection, and inline the emitted data
contract together with the vendored D3 v7 bundle, the stylesheet, and the
renderer script into ONE HTML file. The output has no external references — it
opens offline in a browser with no serving layer (Slice 1 has none by design).

The generator is deliberately thin: all geometry and all contract shaping live in
:mod:`idiograph.domains.viewer`. This module only reads bytes, fills a template,
and writes a file.

THE PROJECTION IS A PARAMETER, NOT A HARDWIRING. :func:`render_projection_html`
is the viewer-agnostic core — it takes an already-projected ``{meta, nodes,
edges}`` dict and knows nothing about where it came from. Everything above it is
a named entry for one subject: :func:`generate_viewer_html` for the artifact
(Slice 1, still defaulting to :func:`project_depth_provenance`, now overridable),
:func:`generate_graph_viewer_html` for a declared ``Graph`` (Slice 2). The
renderer is one renderer; it selects its draw path on ``meta["view"]``.
"""

import json
from collections.abc import Callable
from pathlib import Path

from idiograph.core.models import Graph
from idiograph.demo import REGISTRY_ROOT
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    ForwardParameters,
    PipelineParameters,
    PipelineResult,
)
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph
from idiograph.domains.arxiv.registry import PipelineRegistry, sole_record_address
from idiograph.domains.viewer import project_depth_provenance, project_graph

# The arguments `build_pipeline_graph` requires and the declared-graph view
# cannot use. EVERY VALUE HERE IS DELIBERATELY DEGENERATE — zero seeds, zeroed
# counts, zeroed decay, no LLM config — precisely so that nobody reads a real
# pipeline configuration into a picture that does not carry one. They are not
# defaults, not a suggested configuration, and not the frozen CRISPR run's
# values; they are the shape the builder demands with the content removed.
# `test_the_projection_is_invariant_to_seeds_and_parameters` is the enforcement.
_INERT_ARGUMENTS = (
    [],
    PipelineParameters(
        backward=BackwardParameters(n_backward=0, lambda_decay=0.0),
        forward=ForwardParameters(
            n_forward=0,
            lambda_decay=0.0,
            alpha=0.0,
            beta=0.0,
            sort="cited_by_count:desc",
        ),
        current_year=0,
    ),
)

_ASSETS = Path(__file__).resolve().parent / "assets"
_TEMPLATE = _ASSETS / "template.html"
_CSS = _ASSETS / "viewer.css"
_JS = _ASSETS / "viewer.js"
_D3 = _ASSETS / "vendor" / "d3.v7.min.js"

# Placeholders in template.html. Plain markers (not str.format) so the CSS/JS —
# which are full of ``{`` and ``}`` — pass through untouched.
_MARK_TITLE = "/*__TITLE__*/"
_MARK_CSS = "/*__CSS__*/"
_MARK_D3 = "/*__D3__*/"
_MARK_DATA = "/*__DATA__*/"
_MARK_JS = "/*__JS__*/"


def render_projection_html(data: dict, title: str | None = None) -> str:
    """Inline an already-projected ``{meta, nodes, edges}`` contract into the template.

    THE VIEWER-AGNOSTIC SEAM. This function knows nothing about what produced
    ``data`` — only that it satisfies the contract every projection in
    :mod:`idiograph.domains.viewer` emits. It inlines the payload, the vendored
    D3 bundle, the CSS and the renderer JS into one self-contained HTML string.

    ``title`` defaults to whatever the projection put in ``meta["title"]``, and
    falls back to the graph name. A caller that wants a different heading passes
    one rather than teaching this function about views.
    """
    # sort_keys → byte-stable payload; the projections are already deterministic.
    data_json = json.dumps(data, sort_keys=True, ensure_ascii=False)

    template = _TEMPLATE.read_text(encoding="utf-8")
    if title is None:
        title = data["meta"].get("title") or data["meta"].get("view", "Idiograph")

    # Order matters only in that each marker is replaced exactly once; the D3 and
    # JS bodies may themselves contain braces but never our sentinel markers.
    html = template.replace(_MARK_TITLE, _escape_text(title))
    html = html.replace(_MARK_CSS, _CSS.read_text(encoding="utf-8"))
    html = html.replace(_MARK_D3, _D3.read_text(encoding="utf-8"))
    html = html.replace(
        _MARK_DATA, "const GRAPH = " + _inline_json(data_json) + ";"
    )
    html = html.replace(_MARK_JS, _JS.read_text(encoding="utf-8"))
    return html


def generate_viewer_html(
    result: PipelineResult,
    projection: Callable[[PipelineResult], dict] = project_depth_provenance,
) -> str:
    """Render the self-contained viewer HTML string for ``result``.

    Runs the headless projection and inlines it, the vendored D3 bundle, the CSS,
    and the renderer JS into the HTML template. Pure over ``result`` — no I/O
    beyond reading the static assets that ship with the package.

    ``projection`` is the seam: any callable taking a ``PipelineResult`` and
    returning the ``{meta, nodes, edges}`` contract. It defaults to
    :func:`~idiograph.domains.viewer.project_depth_provenance`, so every existing
    call site renders exactly the bytes it did before this parameter existed.
    """
    data = projection(result)
    return render_projection_html(data, _depth_provenance_title(data))


def generate_graph_viewer_html(graph: Graph) -> str:
    """Render the self-contained viewer HTML string for a declared ``Graph``.

    The Slice 2 entry, and the whole of it: project the graph, hand the contract
    to the same :func:`render_projection_html` the artifact view uses. There is
    no second renderer and no second template — the instrument is unchanged and
    is simply pointed at the declaration instead of the result.
    """
    return render_projection_html(project_graph(graph))


def _depth_provenance_title(data: dict) -> str:
    """The Slice 1 heading — the two seed titles, unchanged.

    Kept here rather than in the projection because ``project_depth_provenance``
    is Slice 1's frozen contract and does not emit a title; a projection that
    does (Slice 2) is picked up by :func:`render_projection_html`'s default. The
    seed lookup is guarded so this cannot become the reason a differently-shaped
    contract fails to render.
    """
    seeds = data["meta"].get("seeds")
    if not seeds or len(seeds) < 2:
        return data["meta"].get("title") or "Idiograph — depth/provenance"
    return "Idiograph — depth/provenance ({a} × {b})".format(
        a=(seeds[0]["title"] or "seed A")[:40],
        b=(seeds[1]["title"] or "seed B")[:40],
    )


def render_viewer(
    output_path: Path,
    registry_root: Path | None = None,
    address: str | None = None,
) -> Path:
    """Load a persisted artifact, render the viewer, and write it to ``output_path``.

    Both selectors default: ``registry_root`` to the packaged demo registry, and
    ``address`` to whatever sole record the chosen root holds — so pointing this
    at another single-record root needs no second argument, and the frozen CRISPR
    address is never restated here. Defaults resolve at CALL time, not import
    time, so merely importing this module touches no registry.

    Returns the written path. Creates parent directories as needed. This is the
    generator's top-level entry, used by ``python -m idiograph.apps.viewer``.
    """
    root = REGISTRY_ROOT if registry_root is None else Path(registry_root)
    result = PipelineRegistry(root).read(
        sole_record_address(root) if address is None else address
    )
    html = generate_viewer_html(result)
    return _write(output_path, html)


def declared_pipeline_graph() -> Graph:
    """Build the citation-traversal pipeline's declared ``Graph``.

    THE SUBJECT OF THIS VIEW IS A SHAPE, NOT A RUN. ``build_pipeline_graph``
    takes a seed set and ``PipelineParameters`` because a graph built to be
    EXECUTED needs them — seeds ride as Node 0 configuration, parameters as
    per-node params. The projection emits neither: param KEY NAMES only, no
    values, and no seed identifiers. Every emitted byte is therefore a function
    of the pipeline's declared topology alone.

    So this passes ``_INERT_ARGUMENTS`` rather than reading a persisted artifact
    to recover a configuration that cannot reach the output. An artifact read
    here would make the declared-graph view require a stored run in order to
    draw a picture that does not depend on one, and would let a claim about
    provenance ride on a value nothing consumes.

    ``test_the_projection_is_invariant_to_seeds_and_parameters`` is what holds
    this: it projects the graph under deliberately divergent configurations —
    including ``llm`` set and unset — and asserts one payload. If that
    invariance ever breaks, this function is wrong and the test says so, rather
    than a docstring being quietly outrun.
    """
    return build_pipeline_graph(*_INERT_ARGUMENTS)


def render_graph_viewer(output_path: Path) -> Path:
    """Render the DECLARED pipeline graph to a self-contained HTML file.

    Takes no registry selectors, because it reads no artifact: the declaration
    is knowable without a run, which is the claim the whole view exists to make.
    See :func:`declared_pipeline_graph`.

    Returns the written path. Creates parent directories as needed.
    """
    return _write(output_path, generate_graph_viewer_html(declared_pipeline_graph()))


def _write(output_path: Path, html: str) -> Path:
    """Write ``html`` to ``output_path``, creating parent directories."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _escape_text(text: str) -> str:
    """Minimal HTML-text escaping for the interpolated <title>/heading."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline_json(data_json: str) -> str:
    """Make a JSON string safe to embed inside a <script> element.

    Escapes ``<`` (defeats a ``</script>`` breakout) and the JS-only line
    separators U+2028/U+2029 — valid inside JSON strings, but illegal bare in a
    JavaScript source token.
    """
    return (
        data_json.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
