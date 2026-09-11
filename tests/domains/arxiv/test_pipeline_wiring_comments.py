# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Binds `pipeline.py`'s `#:` wiring illustrations to the edges actually declared.

Every port-declaration constant in `pipeline.py` carries a `#:` comment that
shows the wiring its port names are designed for — ``assemble.nodes ->
clean.nodes``, ``annotate.nodes -> depth.nodes``, and so on. Those illustrations
are the reason the port names are what they are: they are cited in
`pipeline_graph._build_edges`' own docstring as what a reader arriving from the
declarations should find spelled out identically in the edge list.

Nothing held them together. The illustrations are COMMENTS, and
`build_pipeline_graph` is CODE, so a rewiring that moved an edge and left the
comment behind produced a declaration that reads as true, is cited downstream as
authoritative, and describes a pipeline that no longer exists. That is the same
defect shape `test_traversal_contract_binding.py` addresses for
`TRAVERSAL_CONTRACT`, in a cheaper register: these illustrations carry no content
address, so a stale one costs a reader rather than a re-freeze.

WHAT THIS PINS. Every wiring illustration in `pipeline.py` names a real edge in
`build_pipeline_graph()`. Nothing more. In particular it does NOT pin the
converse — an edge with no illustration is perfectly legal and most of the 21
have none — because the illustrations exist to justify PORT NAMES at the sites
that chose them, not to be a second copy of the edge list.

WHAT IT DELIBERATELY DOES NOT DO. It asserts nothing about MEANING. Whether an
illustration's prose correctly explains WHY two ports share a name is not
checkable here and is not attempted; question 0cc09383 — what binds a declared
absence in contract prose to the code — rides elsewhere and is not answered by
this file. The manifest apparatus this test ships beside witnesses CHANGE, not
meaning, and so does this.

THE EXTRACTION IS DERIVED, NEVER COUNTED. The illustrations are found by reading
`pipeline.py` and matching a shape, so a new port declaration that adds one is
covered the day it lands with no edit here. No count is hard-coded — a test
asserting "there are N illustrations" would go red on an honest addition and
would say nothing about correctness. `test_extractor_finds_the_known_wiring_sites`
is the one guard against the opposite failure: an extractor that silently matches
nothing would make every other assertion in this file vacuously true.

WHEN A TEST HERE FAILS, READ THIS. An illustration naming an edge that does not
exist means the comment and the wiring have diverged. Which one is wrong is not
this test's call: either the pipeline was rewired and the declaration was left
stale, or the comment describes an intended wiring that was never built. Repair
the one that is actually wrong — do not delete the illustration to silence this.
"""

import re
from pathlib import Path

from idiograph.domains.arxiv import pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CoCitationParameters,
    ForwardParameters,
    PipelineParameters,
)
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph

#: A wiring illustration: ``producer.port -> consumer.port`` inside the double
#: backticks the module's comments use for code. The backticks are part of the
#: pattern ON PURPOSE — they are what distinguishes an illustration from an arrow
#: in ordinary prose (a return annotation, a "->" in a sentence), so prose can be
#: written freely around these comments without feeding this test noise.
_WIRING = re.compile(
    r"``([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)``"
)

#: Three illustrations known to be in the tree, used ONLY to prove the extractor
#: is alive. Not a census: the extractor finds every illustration in the file,
#: and the number it finds is deliberately not asserted anywhere.
_KNOWN_SITES = [
    "assemble.nodes -> clean.nodes",
    "assemble.nodes -> co.nodes",
    "annotate.nodes -> depth.nodes",
]


def _parameters() -> PipelineParameters:
    """Parameters sufficient to build the declared graph.

    Values are irrelevant to this file — the EDGES are a function of the
    declarations alone, not of any parameter — so these are the minimum a
    ``PipelineParameters`` will validate with. ``current_year`` is stated rather
    than read from the clock, matching the discipline everywhere else in the
    suite: nothing here should acquire a dependency on the date.
    """
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        current_year=2026,
        co_citation=CoCitationParameters(min_strength=1, max_edges=None),
    )


def _comment_blocks() -> list[tuple[int, str]]:
    """Contiguous runs of ``#:`` comment lines, as ``(first line number, text)``.

    Joined per BLOCK rather than per line, with whitespace collapsed, because the
    comments are hard-wrapped prose: an illustration long enough to be worth
    writing frequently straddles a line break, and a per-line matcher would miss
    exactly the longest ones. The block's first line number is carried so a
    failure can point at the declaration rather than at the file.
    """
    blocks: list[tuple[int, str]] = []
    start: int | None = None
    parts: list[str] = []
    for number, raw in enumerate(
        Path(pipeline.__file__).read_text(encoding="utf-8").splitlines(), 1
    ):
        stripped = raw.strip()
        if stripped.startswith("#:"):
            if start is None:
                start = number
            parts.append(stripped[2:].strip())
            continue
        if start is not None:
            blocks.append((start, " ".join(" ".join(parts).split())))
            start, parts = None, []
    if start is not None:
        blocks.append((start, " ".join(" ".join(parts).split())))
    return blocks


def _illustrations() -> list[tuple[int, str, tuple[str, str, str, str]]]:
    """Every wiring illustration in ``pipeline.py``: ``(line, text, wiring)``."""
    found: list[tuple[int, str, tuple[str, str, str, str]]] = []
    for line, text in _comment_blocks():
        for match in _WIRING.finditer(text):
            source, from_port, target, to_port = match.groups()
            found.append(
                (
                    line,
                    f"{source}.{from_port} -> {target}.{to_port}",
                    (source, from_port, target, to_port),
                )
            )
    return found


def test_extractor_finds_the_known_wiring_sites() -> None:
    """The extractor is alive — it finds illustrations known to be in the tree.

    Every other assertion in this file iterates over what the extractor returns,
    so an extractor matching nothing would turn them all green while checking
    nothing at all. This is the guard against that, and it is the ONLY place any
    specific illustration is written down.

    WHEN THIS TEST FAILS, READ THIS. Either the comment shape moved — in which
    case fix `_WIRING`/`_comment_blocks` so the illustrations are found again,
    never by deleting this guard — or one of these declarations genuinely lost
    its illustration, which is a change to how the port names justify themselves
    and deserves a look rather than a test edit.
    """
    extracted = {text for _, text, _ in _illustrations()}
    assert extracted, (
        "no wiring illustrations were extracted from pipeline.py at all. Every "
        "other test in this file iterates over this set, so they are now "
        "vacuous. The comment shape this file matches — a "
        "``producer.port -> consumer.port`` inside double backticks on a `#:` "
        "line — has moved. Repair the extractor; do not relax the assertions."
    )
    missing = [site for site in _KNOWN_SITES if site not in extracted]
    assert not missing, (
        f"the extractor no longer finds {missing}, which were present when this "
        f"file was written. It found {sorted(extracted)}. This is a liveness "
        f"guard, not a census: if a declaration legitimately lost its wiring "
        f"illustration, say so deliberately here — but first rule out that the "
        f"extractor simply stopped matching the comment shape."
    )


def test_every_wiring_illustration_names_a_declared_edge() -> None:
    """Pins the binding: each `#:` illustration corresponds to a real declared edge.

    The edge set comes from `build_pipeline_graph()` — the live declaration, not a
    transcription — so this compares the comments against what the graph actually
    says today.

    WHEN THIS TEST FAILS, READ THIS. A port-declaration comment claims a wiring
    the graph does not declare. The likely cause is a rewiring in
    `pipeline_graph._build_edges` that left the illustration behind: that
    docstring cites these very comments as what a reader should find spelled
    identically in the edge list, so a stale one makes the two read as two
    vocabularies for one pipeline — the exact confusion the shared naming exists
    to prevent. Fix whichever is wrong. Deleting the comment silences this test
    and destroys the binding.
    """
    graph = build_pipeline_graph([{"arxiv_id": "x"}], _parameters())
    declared = {
        (edge.source, edge.from_port, edge.target, edge.to_port)
        for edge in graph.edges
    }

    for line, text, wiring in _illustrations():
        assert wiring in declared, (
            f"pipeline.py:{line} illustrates the wiring `{text}`, but "
            f"build_pipeline_graph() declares no such edge. The declared edges "
            f"are {sorted(declared)}. The comment and the wiring have DIVERGED: "
            f"either the pipeline was rewired and this illustration was left "
            f"stale, or the illustration describes a wiring that was never "
            f"built. `pipeline_graph._build_edges` cites these comments as what "
            f"a reader should find in the edge list, so a stale one misleads by "
            f"design. Repair the wrong half — do not delete the comment."
        )
