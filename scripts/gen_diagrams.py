# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""
Generate Mermaid diagrams from live pipeline definitions and splice them into README.md.
Run before committing, or via CI to verify README is in sync with code.
"""

import re
import sys
from pathlib import Path

# Ensure src/ is on the path when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE


def generate_mermaid(pipeline) -> str:
    lines = ["```mermaid", "flowchart LR"]

    for node in pipeline.nodes:
        lines.append(f'    {node.id}["{node.id}<br/>{node.type}"]')

    for edge in pipeline.edges:
        lines.append(f"    {edge.source} -->|{edge.type}| {edge.target}")

    lines.append("```")
    return "\n".join(lines)


def splice(readme: str, marker: str, content: str) -> str:
    pattern = (
        rf"(<!-- GENERATED:{re.escape(marker)} -->)"
        rf".*?"
        rf"(<!-- END GENERATED -->)"
    )
    replacement = rf"\1\n{content}\n\2"
    result, count = re.subn(pattern, replacement, readme, flags=re.DOTALL)
    if count == 0:
        raise ValueError(f"Sentinel '<!-- GENERATED:{marker} -->' not found in README.")
    return result


def main():
    readme_path = Path(__file__).parent.parent / "README.md"
    readme = readme_path.read_text(encoding="utf-8")

    diagram = generate_mermaid(ARXIV_PIPELINE)
    updated = splice(readme, "arxiv-pipeline", diagram)

    readme_path.write_text(updated, encoding="utf-8")
    print("README.md updated.")


if __name__ == "__main__":
    main()