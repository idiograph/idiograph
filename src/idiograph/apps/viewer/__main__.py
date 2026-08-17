# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Entry point for the static viewer.

    uv run python -m idiograph.apps.viewer [--view VIEW] [--out PATH]
        [--registry-root DIR] [--address HASH]

Renders one of two views to a single self-contained HTML file. This is the
viewer subtree's OWN entry point — the viewer is deliberately not wired into the
top-level typer CLI (out of scope). Argument parsing uses stdlib ``argparse`` to
honour the no-new-dependency constraint.

``--view depth-provenance`` (the DEFAULT, and the no-argument behaviour, which is
unchanged) renders the frozen CRISPR artifact — what the pipeline produced.
``--view declared-graph`` renders the ``Graph`` that same pipeline declares —
what the pipeline is. The default output path follows the view, so the two never
overwrite each other.
"""

import argparse
from pathlib import Path

from idiograph.apps.viewer.generate import render_graph_viewer, render_viewer
from idiograph.demo import REGISTRY_ROOT

_BUILD_DIR = Path(__file__).resolve().parents[4] / "build" / "viewer"

#: view name → (renderer, default output filename). One table so a third view is
#: a row rather than a branch, and so `--out` genuinely defaults per view instead
#: of one view silently writing over the other's file.
_VIEWS = {
    "depth-provenance": (render_viewer, "depth-provenance.html"),
    "declared-graph": (render_graph_viewer, "declared-graph.html"),
}
_DEFAULT_VIEW = "depth-provenance"

# Slice 1's path, named so the unchanged default stays legible at a glance.
_DEFAULT_OUT = _BUILD_DIR / _VIEWS[_DEFAULT_VIEW][1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m idiograph.apps.viewer",
        description="Render the frozen CRISPR pipeline to a self-contained "
                    "viewer: its artifact (depth-provenance) or its own "
                    "declared graph (declared-graph).",
    )
    parser.add_argument(
        "--view",
        choices=sorted(_VIEWS),
        default=_DEFAULT_VIEW,
        help=f"Which projection to render (default: {_DEFAULT_VIEW} — the "
             f"artifact the pipeline produced). 'declared-graph' renders the "
             f"pipeline's own declared Graph.",
    )
    # None, not the Slice 1 path: the default follows --view and is resolved
    # after parsing, so `--view declared-graph` does not write over the
    # depth/provenance file.
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output HTML path (default: {_BUILD_DIR}/<view>.html).",
    )
    # Both selectors pass None through so render_viewer stays the single place
    # that decides what "default" means; the help text only DESCRIBES the rule.
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=None,
        help=f"Registry root to read from (default: the packaged demo "
             f"registry, {REGISTRY_ROOT}).",
    )
    parser.add_argument(
        "--address",
        default=None,
        help="Content address of the artifact to render (default: the sole "
             "record in the registry root — the frozen CRISPR artifact).",
    )
    args = parser.parse_args(argv)

    render, default_name = _VIEWS[args.view]
    out = _BUILD_DIR / default_name if args.out is None else args.out
    written = render(out, args.registry_root, args.address)
    size_kb = written.stat().st_size / 1024
    print(f"wrote {written} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
