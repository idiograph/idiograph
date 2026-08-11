# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""Freeze/Trigger demo: the parameters it builds still address the frozen record.

The demo in ``scripts/demos/crispr_freeze_trigger.py`` is only meaningful if the
parameters it constructs LIVE key to the artifact that was already frozen at real
OpenAlex and Anthropic cost. If they drift, the HIT leg misses the committed
record and the demo silently re-derives instead of replaying — a failure the
suite could not otherwise see, because nothing imports the script.

These tests bind the script's live parameter construction to the record packaged
under ``idiograph.demo``:

1. The address computed from the record's RESOLVED SEEDS plus the script's OWN
   ``_parameters()`` equals the record's filename. Deliberately NOT
   ``address_of(read_result(...))`` — that hashes the record's parameters against
   themselves and would pass no matter how far the script had drifted.
2. The script's parameter dump equals the record's ``parameters`` block field for
   field — the same binding, stated finely enough to name which field moved.
3. ``frozen_crispr_address()`` — which production code derives by globbing the
   packaged registry — equals the literal authored below.

All three are offline: the record is read from disk, the address is computed by
``registry.content_address``, and no network call or credential is involved.

When a test here goes red on a SEMANTIC edit — one that changes what the pipeline
would produce, such as the Node 5.5 prompt template or the parse contract (both
ride into the address via ``prompt_template_hash`` / ``parse_contract_hash``) —
the remedy is a RE-FREEZE of the demo artifact (~50 minutes, ~1,100 live LLM
draws, real OpenAlex and Anthropic cost), never an update to ``FROZEN_ADDRESS``,
which would silently convert this guard into a tautology and destroy the
record-replay contract the demo exists to demonstrate.

THE ONE EXCEPTION — IDG-094 clause 3, DECLARATION-ONLY address rebaselining. A
new field entering the address while the derivation handlers are provably
unchanged moves every address without changing a single derived byte, so a
re-freeze would burn ~50 minutes and ~1,100 live draws to reproduce the artifact
already on disk. That case is HAND RE-ADDRESSED under IDG-094's three
conditions: (i) the handlers are byte-identical, asserted mechanically rather
than by eye; (ii) the record's ``parameters`` block is updated to the new dump
so the field-for-field test below keeps biting; (iii) the carve-out is named
here, at the guard, so the next reader finds it. Applied once so far, when
``traversal_contract_hash`` landed on ``PipelineParameters``:
``27429df2…f064d4`` → ``1f1a583a…c2855e``, with ``_node3_score`` and
``backward_traverse`` unchanged.

The distinction is the whole carve-out: SEMANTIC change re-freezes, DECLARATION
change re-addresses. If you cannot show condition (i) mechanically, you have a
semantic change and you owe a re-freeze.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv.models import PipelineParameters
from idiograph.domains.arxiv.registry import content_address

# The frozen artifact this demo replays. The address IS the filename — that
# identity is what these tests exist to hold.
#
# This literal is the ONLY hand-authored copy of the address left in the tree
# (IDG-087 deleted the rest in favour of deriving it from the packaged record's
# filename). It stays authored ON PURPOSE: a test that derived the address the
# same way production does would compare a value against itself and pass for any
# record whatsoever. An independent second opinion has to be independently
# written down.
FROZEN_ADDRESS = (
    "1f1a583ac55e7d5b60c593292dd0da1ec471d134b649875a91f382428cc2855e"
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FROZEN_RECORD = REGISTRY_ROOT / f"{FROZEN_ADDRESS}.json"
_DEMO_SCRIPT = _REPO_ROOT / "scripts" / "demos" / "crispr_freeze_trigger.py"


def _load_demo_module():
    """Load the demo script by file path.

    ``scripts/demos/`` is not a package and there is no pytest path
    configuration, so the module is loaded from its path rather than imported.
    The alternative — adding an ``__init__.py`` or a path hook — would change the
    tree's shape to suit one test. Safe to execute: the script's module level is
    imports, constants and definitions only, with no ``load_dotenv()``, no I/O
    and no network.
    """
    spec = importlib.util.spec_from_file_location(
        "idiograph_demo_crispr_freeze_trigger", _DEMO_SCRIPT
    )
    assert spec is not None and spec.loader is not None, _DEMO_SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load_demo_module()


@pytest.fixture(scope="module")
def frozen_record() -> dict:
    return json.loads(_FROZEN_RECORD.read_text(encoding="utf-8"))


def test_frozen_record_is_present() -> None:
    """The fixture these tests assert against is the packaged artifact."""
    assert _FROZEN_RECORD.is_file(), f"frozen artifact missing: {_FROZEN_RECORD}"


def test_packaged_registry_resolves_to_the_authored_address() -> None:
    """What production DERIVES equals what this file AUTHORS.

    ``frozen_crispr_address()`` reads the address off the packaged registry's
    sole filename; ``FROZEN_ADDRESS`` above is written out by hand. Since
    IDG-087 that pair is the only place the derived and the authored address
    meet, so this is the seam where a re-freeze — a new record under a new
    filename — becomes visible to the suite.
    """
    assert frozen_crispr_address() == FROZEN_ADDRESS, (
        "the packaged registry no longer holds the record this file names. If "
        "the pipeline's SEMANTICS moved — anything changing what a run would "
        "derive — the remedy is a RE-FREEZE of the demo artifact (~50 minutes, "
        "~1,100 live LLM draws) and repackaging the record it produces, NOT an "
        "edit to FROZEN_ADDRESS, which would make this test agree with whatever "
        "happens to be on disk and destroy the guard entirely. THE ONE "
        "EXCEPTION is IDG-094 clause 3, declaration-only rebaselining: a new "
        "field entering the content address while the derivation handlers are "
        "provably byte-identical is hand re-addressed under IDG-094's three "
        "conditions — see this module's docstring before concluding the guard "
        "has rotted."
    )


def test_script_parameters_address_the_frozen_record(demo, frozen_record) -> None:
    """The script's own parameters + the record's seeds address to its filename.

    This is the guard that would have caught ``current_year`` becoming required:
    the parameters come from the live ``_parameters()`` call, so any field the
    script stops supplying, or supplies differently, moves the address here.
    """
    parameters = demo._parameters()
    assert isinstance(parameters, PipelineParameters)

    address = content_address(frozen_record["seeds"], parameters)

    assert address == FROZEN_ADDRESS, (
        "the demo's parameters no longer address the frozen record — the HIT leg "
        "would miss and re-derive at live cost"
    )


def test_script_parameters_match_the_record_field_for_field(
    demo, frozen_record
) -> None:
    """Same binding as above, reported per field rather than as one hash."""
    assert demo._parameters().model_dump(mode="json") == frozen_record["parameters"]
