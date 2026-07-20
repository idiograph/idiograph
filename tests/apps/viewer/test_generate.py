# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Static-viewer generator — smoke tests over the frozen CRISPR artifact.

The generator is read-only over the committed artifact; these assert it produces
a non-empty, self-contained (no external references) HTML file that embeds the
projection data and the vendored D3 bundle.
"""

from idiograph.apps.viewer.generate import (
    generate_viewer_html,
    load_frozen_result,
    render_viewer,
)


def test_generate_html_non_empty_and_self_contained():
    html = generate_viewer_html(load_frozen_result())
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
    html = generate_viewer_html(load_frozen_result())
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
    html = generate_viewer_html(load_frozen_result())
    for marker in ("/*__TITLE__*/", "/*__CSS__*/", "/*__D3__*/",
                   "/*__DATA__*/", "/*__JS__*/"):
        assert marker not in html, f"unreplaced marker {marker}"
