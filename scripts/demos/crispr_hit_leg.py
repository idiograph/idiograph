# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""HIT leg — cross-process replay of the frozen CRISPR artifact (IDG-032).

The companion :mod:`crispr_freeze_trigger` demo proves record-replay in ONE
process: it runs a MISS then a HIT against one durable registry it owns. This
script proves the strictly stronger claim the MISS leg left unproven — that a
SECOND, independent process can replay a FIRST process's artifact out of the
same DURABLE registry, addressed only by content:

1. FREEZE happened in an earlier process, via live Anthropic + OpenAlex calls at
   ~$2 and ~50 minutes. It persisted exactly one ``PipelineResult`` into the
   durable registry root under its address-derived filename. This script does NOT
   reproduce it and could not do so for free.

2. This process opens that same DURABLE registry root (XDG data home, outside
   /tmp so it survives a reboot), computes the content address the freeze would
   produce, and calls the production ``cached_run_arxiv_pipeline`` against it with
   the SAME ``PipelineParameters`` and ``anthropic_client=None``. It never writes
   to the registry — a HIT reads; only a MISS would write, and a MISS here is a
   reported finding, never a silent re-freeze. There is no file to shuttle: the
   artifact already sits at its address in the durable root.

The parameters and seeds are IMPORTED from :mod:`crispr_freeze_trigger` — the
module that produced the artifact — never re-typed. Any drift of one float or one
string would move the content address and turn the call into a MISS.

The proof is ``hit_traversals == 0``: the cache short-circuited traversal on a
name-match and replayed the stored, fully LLM-annotated graph. Zero Anthropic
calls is STRUCTURAL, not counted: ``parameters.llm`` is SET and
``anthropic_client=None`` is the exact combination ``run_traversal`` raises
``ValueError`` on — so a leg that reached traversal would have CRASHED, not
quietly re-derived. A returned annotated graph with zero traversal entries is the
whole proof.

Limits, stated honestly:

- A HIT is NOT hermetic. ``resolve_seeds`` runs on every call, above the hit/miss
  branch, because resolution PRODUCES the address — so the HIT still issues one
  OpenAlex GET per seed. That is expected and is documented here, not suppressed.
- Two fields (``seeds``, ``seed_failures``) are request-derived and re-supplied on
  every hit BY DESIGN (``cache._resupply_request_derived``). This script reports
  the field-level difference between the on-disk bundle and the returned result
  rather than asserting byte-identity.

This demo needs only ``OPENALEX_API_KEY``. A HIT draws no model, so requiring an
Anthropic key it cannot use would be a false claim about its own boundary.

Run it::

    uv run python scripts/demos/crispr_hit_leg.py
"""

import asyncio
import os
import sys
from collections import Counter

import httpx
from dotenv import load_dotenv

# Import the STANDARD and — per its own docstring — the thing to reuse. Deriving
# the parameters and seeds from the module that produced the artifact is the
# whole experiment: a re-typed literal that hashes differently fails as a MISS.
# The durable-root helper and the measured-boundary text also live there (the
# module of record) so this script cannot drift from them.
# crispr_freeze_trigger guards _main() behind __main__, so importing it is inert.
from crispr_freeze_trigger import (  # noqa: E402  (scripts/demos is on sys.path[0])
    OPENALEX_TIMEOUT_SECONDS,
    SEEDS,
    RequestCounter,
    TraversalSpy,
    _boundary_statement,
    _durable_registry_root,
    _parameters,
)

from idiograph.core.logging_config import get_logger
from idiograph.domains.arxiv import cache as cache_module
from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
from idiograph.domains.arxiv.models import PipelineResult
from idiograph.domains.arxiv.pipeline import resolve_seeds
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    address_of,
    content_address,
)

_log = get_logger("demos.crispr_hit_leg")

# The re-supplied (request-derived) fields — see cache._resupply_request_derived.
# A field-level diff between the on-disk bundle and the returned result should
# name only these; anything else is a finding.
_RESUPPLIED_FIELDS = {"seeds", "seed_failures"}


def _openalex_key() -> str:
    """OpenAlex key only. A HIT needs no model, so this deliberately does NOT
    require ANTHROPIC_API_KEY — unlike crispr_freeze_trigger's _preconditions(),
    whose MISS leg genuinely needs both.
    """
    load_dotenv()
    key = (os.environ.get("OPENALEX_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "PRECONDITION FAILED: OPENALEX_API_KEY not set (env or .env).\n"
            "Seed resolution runs on every call — hit or miss — so the HIT leg "
            "still needs the OpenAlex key. It needs NO Anthropic key."
        )
    return key


def _field_diff(
    stored: PipelineResult, returned: PipelineResult
) -> tuple[list[str], dict[str, bool]]:
    """Field-level diff between the on-disk bundle and the returned result.

    Returns (differing_field_names, resupplied_content_equal). The second maps
    each re-supplied field to whether stored and returned agree in CONTENT — for
    ``seeds``, order-independently (the address normalizes seed order away).
    """
    stored_dump = stored.model_dump(mode="json")
    returned_dump = returned.model_dump(mode="json")
    differing = [k for k in stored_dump if stored_dump[k] != returned_dump[k]]

    content_equal = {
        "seeds": sorted(stored.seeds) == sorted(returned.seeds),
        "seed_failures": (
            [f.model_dump() for f in stored.seed_failures]
            == [f.model_dump() for f in returned.seed_failures]
        ),
    }
    return differing, content_equal


async def _diagnose_miss(openalex_key: str) -> str:
    """A miss means no on-disk artifact addresses to the live-computed address.
    Recompute the address the honest way — resolve, then content_address — for the
    STOP report, so the finding names the address that actually moved.
    """
    async with httpx.AsyncClient(timeout=OPENALEX_TIMEOUT_SECONDS) as http_client:
        resolved, _ = await resolve_seeds(
            SEEDS, client=http_client, api_key=openalex_key
        )
    return content_address([r.node_id for r in resolved], _parameters())


async def _main() -> int:
    openalex_key = _openalex_key()
    parameters = _parameters()
    registry_root = _durable_registry_root()
    registry = PipelineRegistry(registry_root)

    print()
    print("=" * 72)
    print("  IDIOGRAPH — HIT LEG  (cross-process replay of the frozen artifact)")
    print("  A SECOND process reads a FIRST process's artifact from a DURABLE")
    print("  registry outside /tmp. Traversal must never be entered.")
    print("=" * 72)
    print()
    print(f"  entry point   : cached_run_arxiv_pipeline  (the real cache.py)")
    print(f"  seeds         : {SEEDS[0]['doi']}  (Doudna/Charpentier 2012)")
    print(f"                  {SEEDS[1]['doi']}  (Zhang 2013)")
    print(f"  parameters    : imported from crispr_freeze_trigger._parameters()")
    print(f"  prompt hash   : {parameters.llm.prompt_template_hash[:16]}…  (derived)")
    print(f"  durable root  : {registry_root}")
    print()

    # ---- Fast fail: an empty registry means nothing was ever frozen -------
    # A stranger cloning the repo has no artifact. Detect that HERE, straight from
    # disk — no resolve, no pipeline. Otherwise the cache would resolve, run a full
    # n_backward=3200 traversal (many minutes, pipeline.py:1308–1409), and only
    # THEN raise the Node 5.5 guard. The whole selling point is "replays in
    # seconds"; a no-artifact clone must fail in one, not after a MISS traversal.
    present = (
        sorted(p.name for p in registry_root.glob("*.json"))
        if registry_root.exists()
        else []
    )
    if not present:
        print("-" * 72)
        print("  NO ARTIFACT — nothing to replay.")
        print("-" * 72)
        print(f"  durable root : {registry_root}")
        print("  The durable registry holds no frozen artifact, so there is nothing")
        print("  to hit. This script REPLAYS a record; it does not create one, and")
        print("  it will not enter the pipeline just to discover the record is absent.")
        print()
        print("  Record it first with the COLD path — ~$2 and ~50 minutes of live")
        print("  Anthropic + OpenAlex calls, and it only needs to run ONCE, ever:")
        print()
        print("      uv run python scripts/demos/crispr_freeze_trigger.py")
        print()
        print("  Then re-run this script; the replay takes seconds.")
        print("=" * 72)
        print()
        return 3

    print(f"  registry holds: {present}")
    print()

    # ---- HIT leg: same params, NO anthropic client ------------------------
    traversal_spy = TraversalSpy()
    openalex_calls = RequestCounter()

    print("-" * 72)
    print("  HIT LEG  (same params, anthropic_client=None)")
    print("  parameters.llm is SET and there is NO client: had this leg reached")
    print("  traversal, Node 5.5's guard would have RAISED ValueError.")
    print("-" * 72)

    # Install the call-through counter on the symbol the cache actually calls.
    cache_module.run_traversal = traversal_spy
    guard_raised = False
    try:
        async with httpx.AsyncClient(
            timeout=OPENALEX_TIMEOUT_SECONDS,
            event_hooks={"request": [openalex_calls]},
        ) as http_client:
            try:
                hit = await cached_run_arxiv_pipeline(
                    SEEDS,
                    parameters,
                    client=http_client,
                    api_key=openalex_key,
                    registry=registry,
                    anthropic_client=None,
                )
            except ValueError as exc:
                # The ONLY way this path raises ValueError is the Node 5.5 guard,
                # reached only on a MISS (traversal entered with llm-set/no-client).
                guard_raised = True
                guard_error = exc
    finally:
        cache_module.run_traversal = traversal_spy._real

    hit_traversals = traversal_spy.entries
    hit_openalex = openalex_calls.count

    # ---- MISS abort path: an artifact is present but the address MOVED -----
    # We only reach the cached call when the registry is non-empty, so a miss here
    # is the genuinely interesting case: a record exists, but the live-computed
    # address does not name it (params drifted, or OpenAlex resolved a seed to a
    # different id than at capture). Report expected-vs-computed and STOP; never
    # re-freeze — that would cost $2 and destroy the evidence.
    if guard_raised or hit_traversals > 0:
        computed = await _diagnose_miss(openalex_key)
        print(f"  traversal entered : {hit_traversals}")
        print()
        print("=" * 72)
        print("  MISS — STOPPING. The call did not hit any frozen artifact.")
        print("=" * 72)
        print(f"  computed address : {computed}")
        print(f"  registry holds   : {present}")
        print(f"  guard raised     : {guard_raised}"
              + (f" ({guard_error})" if guard_raised else ""))
        print()
        print("  An artifact IS present but the live address does not name it — the")
        print("  address moved. Either the parameters drifted or OpenAlex resolved a")
        print("  seed to a different id than at capture. Both are FINDINGS for the")
        print("  design seat. Not retrying, not regenerating — that would cost $2 and")
        print("  destroy the evidence. The artifact was NOT modified.")
        print("=" * 72)
        print()
        return 2

    # ---- HIT confirmed: read the on-disk bundle it replayed ---------------
    hit_address = address_of(hit)
    # registry.read validates that the on-disk file addresses to its own filename
    # — the content-addressed store returning exactly what its key names. A
    # mismatch here is a FINDING (corrupt/renamed artifact), so let it propagate.
    stored = registry.read(hit_address)

    print(f"  traversal entered : {hit_traversals}")
    print(f"  Anthropic calls   : 0  (structural — no client exists to draw)")
    print(f"  OpenAlex requests : {hit_openalex}  (seed resolution — runs on "
          "every call, hit or miss; a HIT is not hermetic)")
    print()

    # ---- EVIDENCE ---------------------------------------------------------
    differing_fields, resupplied_equal = _field_diff(stored, hit)
    labels = Counter(n.relationship_type or "null" for n in hit.nodes)
    non_seed_labeled = sum(
        1 for n in hit.nodes if n.relationship_type is not None
    )

    print("=" * 72)
    print("  EVIDENCE")
    print("=" * 72)
    print()
    print(f"  content address (HIT)    : {hit_address}")
    print(f"  registry file            : {registry.path_for(hit_address).name}")
    print(f"  on-disk bundle addresses : {address_of(stored)}")
    print()
    print(f"  returned nodes           : {len(hit.nodes)} "
          f"({non_seed_labeled} carry a replayed relationship_type)")
    print(f"  relationship_type labels : ")
    for label, count in sorted(labels.items()):
        print(f"      {label:<26} {count}")
    print()
    print("  field-level diff  (on-disk bundle  vs  returned result)")
    print(f"    fields that differ     : {differing_fields or '(none)'}")
    print(f"    seeds        (stored)  : {stored.seeds}")
    print(f"    seeds        (returned): {hit.seeds}")
    print(f"    seeds  equal-in-content: {resupplied_equal['seeds']}")
    print(f"    seed_failures (stored) : {[f.model_dump() for f in stored.seed_failures]}")
    print(f"    seed_failures (return) : {[f.model_dump() for f in hit.seed_failures]}")
    print(f"    seed_failures equal    : {resupplied_equal['seed_failures']}")
    print()

    checks: list[tuple[str, bool, str]] = [
        (
            "HIT entered traversal ZERO times (THE proof)",
            hit_traversals == 0,
            f"got {hit_traversals}",
        ),
        (
            "returned result's content address names a file already on disk",
            f"{hit_address}.json" in present,
            f"{hit_address}.json not in {present}",
        ),
        (
            "returned nodes carry replayed relationship_type (never derived here)",
            non_seed_labeled > 0,
            "no relationship_type survived the replay",
        ),
        (
            "field-diff names only the re-supplied fields (subset of "
            "{seeds, seed_failures})",
            set(differing_fields) <= _RESUPPLIED_FIELDS,
            f"unexpected differing fields: "
            f"{sorted(set(differing_fields) - _RESUPPLIED_FIELDS)}",
        ),
        (
            "re-supplied seeds are equal in content (order-normalized)",
            resupplied_equal["seeds"],
            "resolved seed sets differ",
        ),
        (
            "re-supplied seed_failures are equal in content",
            resupplied_equal["seed_failures"],
            "seed_failures differ",
        ),
        (
            "HIT issued OpenAlex resolution requests (a HIT is not hermetic)",
            hit_openalex > 0,
            f"got {hit_openalex}",
        ),
        (
            "registry holds the artifact under its address-named file",
            registry.path_for(hit_address).exists(),
            "address-named file missing",
        ),
    ]

    failures = 0
    for label, ok, detail in checks:
        if ok:
            print(f"  [PASS]  {label}")
        else:
            failures += 1
            print(f"  [FAIL]  {label} — {detail}")

    print()
    print("=" * 72)
    if failures:
        print(f"  HIT LEG NOT DEMONSTRATED — {failures} check(s) failed.")
        print("=" * 72)
        return 1

    print("  HIT LEG DEMONSTRATED — cross-process replay proven.")
    print()
    print("  A second process, from a durable registry outside /tmp, replayed a")
    print("  first process's LLM-annotated graph by content address: no traversal,")
    print("  no model, no Anthropic client — only seed resolution touched the")
    print("  network. The frozen artifact was read, not rewritten.")
    print()
    for line in _boundary_statement():
        print(line)
    print("=" * 72)
    print()
    print(f"  Durable registry root: {registry_root}")
    print(f"  Frozen artifact      : {registry.path_for(hit_address)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
