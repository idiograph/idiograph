# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Packaged demo data — the frozen CRISPR artifact and its read path.

``registry/`` holds ONE record: the ``PipelineResult`` frozen at real OpenAlex
and Anthropic cost (~50 minutes, ~1,100 live LLM draws) that both CRISPR demos
and the static viewer replay. It lives inside the package rather than at the repo
root so it ships in the wheel — an installed idiograph can render the viewer and
run the HIT leg with no checkout present.

The record was re-addressed once, by the IDG-080 ``current_year`` rebaselining:
the year both traversal stages score against became a top-level
``PipelineParameters`` field and so entered the content address. Its payload is
byte-identical apart from that one field, retro-stamped with the year it was
actually derived under (frozen 2026-07-18). The superseded address is not
restated here — git holds it, and a dead hex in the tree is one more thing that
can be mistaken for a live one.

This module is the ONE place that knows where that record sits and what it is
addressed by. Nothing else in the tree hand-authors the address: it is read back
off the filename by :func:`frozen_crispr_address`, so the record and its address
cannot drift apart. The single deliberate exception is the authored literal in
``tests/domains/arxiv/test_freeze_trigger_address.py``, which exists precisely to
be an independent second opinion — a test that derived the address from here
would only be agreeing with itself.

It is also the one place that knows what the run was ASKED for.
:data:`FROZEN_CRISPR_SEEDS` holds the two request dicts the freeze was triggered
with — the un-resolved input, as distinct from the record's ``seeds`` field,
which holds the node_ids Node 0 resolved them to. They live here, not in the
demo script that used to own them, because the package now has two importers of
the demo run's own arguments (the freeze script and the served MCP surface) and
a second literal of a value that rides into a content address is a drift hazard
of exactly the kind :func:`frozen_crispr_address` exists to close.
"""

from pathlib import Path

from idiograph.domains.arxiv.models import PipelineResult
from idiograph.domains.arxiv.registry import PipelineRegistry, sole_record_address

# The packaged registry root, resolved from THIS file's location (never CWD) so
# it is correct from any invocation directory and from an installed wheel alike
# — the same discipline ``_ASSETS`` uses in idiograph.apps.viewer.generate.
REGISTRY_ROOT = Path(__file__).resolve().parent / "registry"

#: The recorded CRISPR validation corpus as REQUESTED, seeded as DOIs (the Node
#: 0 path repaired in #42). Doudna/Charpentier 2012 -> W2045435533; Zhang 2013
#: -> W2064815984. These are request dicts, not resolved node_ids: they are what
#: Node 0 takes as configuration, and the record's own ``seeds`` field is what it
#: turned them into.
FROZEN_CRISPR_SEEDS = [
    {"doi": "10.1126/science.1225829"},
    {"doi": "10.1126/science.1231143"},
]

__all__ = [
    "FROZEN_CRISPR_SEEDS",
    "REGISTRY_ROOT",
    "frozen_crispr_address",
    "load_frozen_crispr",
]


def frozen_crispr_address() -> str:
    """The content address of the packaged frozen CRISPR record.

    Derived from the packaged registry's sole filename, not authored here.
    Raises if that registry does not hold exactly one record — which post-install
    means a broken wheel or an incomplete checkout, not a caller error.
    """
    return sole_record_address(REGISTRY_ROOT)


def load_frozen_crispr() -> PipelineResult:
    """Load the packaged frozen CRISPR ``PipelineResult``.

    Read-only: goes through :meth:`PipelineRegistry.read`, which re-supplies the
    excluded cycle witness and verifies the content address. Never writes, never
    re-freezes.
    """
    return PipelineRegistry(REGISTRY_ROOT).read(frozen_crispr_address())
