# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Entry point for the static depth/provenance viewer.

    uv run python -m idiograph.apps.viewer [--out PATH] [--registry-root DIR] [--address HASH]

Renders the frozen CRISPR artifact to a single self-contained HTML file. This is
the viewer subtree's OWN entry point — Slice 1 deliberately does not wire the
viewer into the top-level typer CLI (out of scope). Argument parsing uses stdlib
``argparse`` to honour the no-new-dependency constraint.
"""

import argparse
from pathlib import Path

from idiograph.apps.viewer.generate import (
    DEFAULT_REGISTRY_ROOT,
    FROZEN_CRISPR_ADDRESS,
    render_viewer,
)

_DEFAULT_OUT = (
    Path(__file__).resolve().parents[4]
    / "build"
    / "viewer"
    / "depth-provenance.html"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m idiograph.apps.viewer",
        description="Render the frozen CRISPR graph to a self-contained "
                    "depth/provenance viewer (Slice 1).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Output HTML path (default: {_DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=DEFAULT_REGISTRY_ROOT,
        help=f"Registry root to read from (default: {DEFAULT_REGISTRY_ROOT}).",
    )
    parser.add_argument(
        "--address",
        default=FROZEN_CRISPR_ADDRESS,
        help="Content address of the artifact to render "
             "(default: the frozen CRISPR artifact).",
    )
    args = parser.parse_args(argv)

    written = render_viewer(args.out, args.registry_root, args.address)
    size_kb = written.stat().st_size / 1024
    print(f"wrote {written} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
