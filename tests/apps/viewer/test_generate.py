# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Static-viewer generator — smoke tests over the frozen CRISPR artifact.

The generator is read-only over the committed artifact; these assert it produces
a non-empty, self-contained (no external references) HTML file that embeds the
projection data and the vendored D3 bundle.

Since Slice 2 the generator serves TWO views through one seam, so this file also
carries the Slice 1 REGRESSION assertions: the projection the artifact view
embeds must still be exactly ``project_depth_provenance(result)``, byte for
byte, and the no-argument entry point must still render it.
"""

import json

from idiograph.apps.viewer.__main__ import _DEFAULT_OUT, _DEFAULT_VIEW, main
from idiograph.apps.viewer.generate import (
    generate_graph_viewer_html,
    generate_viewer_html,
    render_graph_viewer,
    render_projection_html,
    render_viewer,
)
from idiograph.demo import load_frozen_crispr
from idiograph.domains.viewer import project_depth_provenance


def test_generate_html_non_empty_and_self_contained():
    html = generate_viewer_html(load_frozen_crispr())
    assert html.startswith("<!DOCTYPE html>")
    assert len(html) > 100_000  # inlined D3 + ~1,885 nodes of data
    # Self-contained: nothing is fetched over the network at load time.
    assert "<script src" not in html
    assert "<link " not in html
    assert "cdn.jsdelivr" not in html and "unpkg.com" not in html
    # Vendored D3 v7 is inlined.
    assert "d3js.org v7" in html
    # Data payload is inlined.
    assert "const GRAPH =" in html


def test_generated_html_carries_load_bearing_signals():
    html = generate_viewer_html(load_frozen_crispr())
    # cites vs co-citation distinction, cycle count, local + lag caveats.
    assert "cites" in html and "co_citation" in html
    assert "suppressed" in html
    assert "local relative measure" in html
    assert "citation lag" in html.lower()


def test_render_viewer_writes_file(tmp_path):
    out = tmp_path / "nested" / "viewer.html"
    written = render_viewer(out)
    assert written == out
    assert out.exists()
    assert out.stat().st_size > 100_000


def test_no_unreplaced_markers(tmp_path):
    html = generate_viewer_html(load_frozen_crispr())
    for marker in ("/*__TITLE__*/", "/*__CSS__*/", "/*__D3__*/",
                   "/*__DATA__*/", "/*__JS__*/"):
        assert marker not in html, f"unreplaced marker {marker}"


# ── Slice 1 regression — the seam most at risk from parameterization ─────────

def test_artifact_view_still_embeds_exactly_the_depth_provenance_projection():
    """The payload is byte-identical to ``project_depth_provenance(result)``.

    Parameterizing the generator moved WHERE the projection is chosen. If it
    also changed WHAT is embedded, Slice 1 is broken — so the embedded payload
    is compared against the projection called directly, not merely spot-checked.
    """
    from idiograph.apps.viewer.generate import _inline_json

    result = load_frozen_crispr()
    expected = _inline_json(
        json.dumps(project_depth_provenance(result), sort_keys=True, ensure_ascii=False)
    )
    html = generate_viewer_html(result)
    assert "const GRAPH = " + expected + ";" in html


def test_artifact_view_keeps_its_seed_derived_title_and_view_name():
    html = generate_viewer_html(load_frozen_crispr())
    assert "Idiograph — depth/provenance (" in html
    # The DATA selects the view. Both views' draw code ships in the one shared
    # renderer, so the marker of which one runs is `meta.view`, not the JS body.
    assert '"view": "depth_provenance"' in html
    assert '"view": "declared_graph"' not in html


def test_the_default_projection_is_the_slice_one_projection():
    """The parameter defaults so every pre-existing call site is unchanged."""
    result = load_frozen_crispr()
    assert generate_viewer_html(result) == generate_viewer_html(
        result, projection=project_depth_provenance
    )


def test_the_projection_is_a_parameter_not_a_hardwiring():
    """A caller can supply its own producer of the contract."""
    result = load_frozen_crispr()

    def _tiny(_result):
        return {
            "meta": {"view": "depth_provenance", "title": "custom heading"},
            "nodes": [],
            "edges": [],
        }

    html = generate_viewer_html(result, projection=_tiny)
    assert "custom heading" in html
    assert '"nodes": []' in html


def test_no_argument_entry_point_still_renders_the_artifact_view(tmp_path):
    """`python -m idiograph.apps.viewer` with no arguments is unchanged.

    The default VIEW and the default OUTPUT PATH are asserted separately from
    the render, so the render can be redirected into tmp_path without the
    assertion quietly becoming vacuous about where the real default writes.
    """
    assert _DEFAULT_VIEW == "depth-provenance"
    assert _DEFAULT_OUT.name == "depth-provenance.html"

    out = tmp_path / "default.html"
    assert main(["--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert html == generate_viewer_html(load_frozen_crispr())


# ── Slice 2 — the declared-graph view through the same seam ─────────────────

def _declared_graph_html(tmp_path):
    out = tmp_path / "declared.html"
    assert main(["--view", "declared-graph", "--out", str(out)]) == 0
    return out.read_text(encoding="utf-8")


def test_declared_graph_view_is_self_contained_and_uses_the_same_chrome(tmp_path):
    html = _declared_graph_html(tmp_path)
    assert html.startswith("<!DOCTYPE html>")
    # Same vendored D3, same inlining discipline, no external references.
    assert "d3js.org v7" in html
    assert "<script src" not in html and "<link " not in html
    assert "const GRAPH =" in html
    for marker in ("/*__TITLE__*/", "/*__CSS__*/", "/*__D3__*/",
                   "/*__DATA__*/", "/*__JS__*/"):
        assert marker not in html, f"unreplaced marker {marker}"


def test_declared_graph_view_carries_the_pipeline_and_its_caveat(tmp_path):
    html = _declared_graph_html(tmp_path)
    assert '"view": "declared_graph"' in html
    assert '"layout": "layered_dag"' in html
    assert "arxiv_citation_traversal" in html
    assert "AnnotateRelationships" in html
    assert "unruled" in html  # the declaration-vs-execution caveat


def test_one_renderer_serves_both_views(tmp_path):
    """The JS and CSS bodies are the same bytes in both outputs.

    This is the deliverable, asserted rather than asserted-about: the artifact
    view and the declared-graph view differ only in their embedded data.
    """
    from idiograph.apps.viewer.generate import _CSS, _D3, _JS

    graph_html = _declared_graph_html(tmp_path)
    artifact_html = generate_viewer_html(load_frozen_crispr())
    for asset in (_JS, _CSS, _D3):
        body = asset.read_text(encoding="utf-8")
        assert body in graph_html
        assert body in artifact_html


def test_render_graph_viewer_writes_a_file(tmp_path):
    out = tmp_path / "nested" / "declared.html"
    written = render_graph_viewer(out)
    assert written == out
    assert out.exists() and out.stat().st_size > 100_000


def test_render_projection_html_is_view_agnostic():
    """The core takes a contract dict and knows nothing about its producer."""
    html = render_projection_html(
        {"meta": {"view": "anything", "title": "a title"}, "nodes": [], "edges": []}
    )
    assert "a title" in html
    assert "const GRAPH =" in html


def test_the_two_views_default_to_different_output_paths(tmp_path, monkeypatch):
    """A `--view` switch must not silently overwrite the other view's file."""
    import idiograph.apps.viewer.__main__ as entry

    monkeypatch.setattr(entry, "_BUILD_DIR", tmp_path)
    assert entry.main([]) == 0
    assert entry.main(["--view", "declared-graph"]) == 0
    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == ["declared-graph.html", "depth-provenance.html"]


def test_generate_graph_viewer_html_takes_a_graph_directly(tmp_path):
    """The Slice 2 generator is a function of a Graph — no registry involved."""
    from idiograph.core.models import Edge, Graph, Node

    html = generate_graph_viewer_html(
        Graph(
            name="tiny",
            version="9.9",
            nodes=[Node(id="a", type="A"), Node(id="b", type="B")],
            edges=[Edge(source="a", target="b")],
        )
    )
    assert "tiny" in html and "v9.9" in html
    assert '"view": "declared_graph"' in html
