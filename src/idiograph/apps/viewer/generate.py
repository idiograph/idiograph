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
:mod:`idiograph.domains.viewer.projection`. This module only reads bytes, fills a
template, and writes a file.
"""

import json
from pathlib import Path

from idiograph.domains.arxiv.models import PipelineResult
from idiograph.domains.arxiv.registry import PipelineRegistry
from idiograph.domains.viewer import project_depth_provenance

# The committed frozen CRISPR artifact and its known content address. Resolved
# from this file's location (never CWD) so the generator runs from anywhere:
#   apps/viewer/generate.py -> parents[4] == repo root -> demo/registry.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REGISTRY_ROOT = REPO_ROOT / "demo" / "registry"
FROZEN_CRISPR_ADDRESS = (
    "4e368a767b8778a9b5487abc449c6dbdf37815da60783110eead60ee1d9b7200"
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


def load_frozen_result(
    registry_root: Path = DEFAULT_REGISTRY_ROOT,
    address: str = FROZEN_CRISPR_ADDRESS,
) -> PipelineResult:
    """Load a persisted ``PipelineResult`` through the registry read path.

    Read-only: goes through :meth:`PipelineRegistry.read`, which re-supplies the
    excluded cycle witness and verifies the content address. Never writes, never
    re-freezes.
    """
    return PipelineRegistry(Path(registry_root)).read(address)


def generate_viewer_html(result: PipelineResult) -> str:
    """Render the self-contained viewer HTML string for ``result``.

    Runs the headless projection and inlines it, the vendored D3 bundle, the CSS,
    and the renderer JS into the HTML template. Pure over ``result`` — no I/O
    beyond reading the static assets that ship with the package.
    """
    data = project_depth_provenance(result)
    # sort_keys → byte-stable payload; the projection is already deterministic.
    data_json = json.dumps(data, sort_keys=True, ensure_ascii=False)

    template = _TEMPLATE.read_text(encoding="utf-8")
    seeds = data["meta"]["seeds"]
    title = "Idiograph — depth/provenance ({a} × {b})".format(
        a=(seeds[0]["title"] or "seed A")[:40],
        b=(seeds[1]["title"] or "seed B")[:40],
    )

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


def render_viewer(
    output_path: Path,
    registry_root: Path = DEFAULT_REGISTRY_ROOT,
    address: str = FROZEN_CRISPR_ADDRESS,
) -> Path:
    """Load the frozen artifact, render the viewer, and write it to ``output_path``.

    Returns the written path. Creates parent directories as needed. This is the
    generator's top-level entry, used by ``python -m idiograph.apps.viewer``.
    """
    result = load_frozen_result(registry_root, address)
    html = generate_viewer_html(result)
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
