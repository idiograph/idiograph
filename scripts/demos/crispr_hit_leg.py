# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""HIT leg — cross-process replay of a frozen artifact (IDG-032).

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

THE PARAMETERS AND SEEDS COME OFF THE RECORD, not out of a Python import
(IDG-113 clause 5). The parameters are the record's own ``parameters`` block —
the block its address was computed over — and the seeds are the request dicts on
its ``<address>.request.json`` sidecar. That is what makes this script replay ANY
record rather than the one corpus a `from crispr_freeze_trigger import SEEDS`
could name: ``--address`` and ``--registry-root`` select which, and the run's own
arguments are then read from the artifact itself. Nothing is re-typed, so nothing
can drift — a re-typed float or string would move the content address and turn
the call into a MISS.

The CRISPR record stays the default for both selectors, so a bare invocation is
exactly the demo it always was.

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
    uv run python scripts/demos/crispr_hit_leg.py --address <hex> --registry-root DIR
"""

import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

import httpx

# The instrumentation and the measured-boundary text live in the module of
# record, so this script cannot drift from either. The SEEDS and _parameters()
# imports are gone (IDG-113): the run's own arguments come off the record now,
# not out of the module that happened to freeze one particular record.
# crispr_freeze_trigger guards _main() behind __main__, so importing it is inert.
from crispr_freeze_trigger import (
    OPENALEX_TIMEOUT_SECONDS,
    RequestCounter,
    TraversalSpy,
    _boundary_statement,
    _durable_registry_root,
)
from dotenv import load_dotenv

from idiograph.core.logging_config import get_logger
from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv import cache as cache_module
from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
from idiograph.domains.arxiv.models import PipelineParameters, PipelineResult
from idiograph.domains.arxiv.pipeline import resolve_seeds
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    address_of,
    content_address,
    is_record,
    sole_record_address,
)

_log = get_logger("demos.crispr_hit_leg")

# The re-supplied (request-derived) fields — see cache._resupply_request_derived.
# A field-level diff between the on-disk bundle and the returned result should
# name only these; anything else is a finding.
_RESUPPLIED_FIELDS = {"seeds", "seed_failures"}

def _warm_registry_root(
    address: str | None = None, registry_root: Path | None = None
) -> tuple[Path, str]:
    """Select the registry root the warm leg reads, and a label for which source won.

    An operator-supplied ``registry_root`` wins outright — they named a root, so
    there is nothing to select and no fallback to apply; selecting something else
    would be the false affordance a flag that is quietly ignored always is.

    Otherwise: XDG-FIRST, packaged registry as FALLBACK. When the operator's XDG
    durable root already holds the artifact (their own cold->warm loop), use XDG
    unchanged so they replay THEIR freeze. Only when XDG lacks it — the stranger
    who just cloned — fall through to the record packaged under
    :data:`idiograph.demo.REGISTRY_ROOT`.

    Presence is keyed on ONE content address, not a blunt non-empty glob: an
    operator whose XDG holds a DIFFERENT artifact is, for THIS replay, a stranger
    and must fall through to the packaged record — a glob would instead pin them
    to XDG and MISS. ``address`` defaults to the packaged record's, read off its
    own filename via ``frozen_crispr_address()`` rather than restated here, so it
    can no longer fall behind a re-freeze the way a hand-authored copy did.

    Both checks are bare local filesystem reads — a glob of the packaged registry
    and one file-existence test. No resolve, no network, ZERO OpenAlex GETs, so
    the warm leg's measured 2-GET boundary is never inflated by root selection
    (cf. residual fb93ee61).

    The redirect lives HERE, at the warm construction site, and NEVER in the shared
    ``durable_registry_root`` — that helper is shared with the COLD freeze, whose
    write target must stay XDG and must never land in the package tree.
    """
    if registry_root is not None:
        return Path(registry_root), "operator-supplied --registry-root"
    address = frozen_crispr_address() if address is None else address
    xdg_root = _durable_registry_root()
    if (xdg_root / f"{address}.json").exists():
        return xdg_root, "XDG durable root"
    return REGISTRY_ROOT, "packaged idiograph.demo registry (clone fallback)"


def _run_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """WHICH record to replay: an address and a root, both defaulting to CRISPR.

    Two selectors and nothing else. Everything the run itself needs — the seeds
    and the parameters — is read off the record they select rather than passed
    here, which is what keeps a re-typed argument from ever moving the address.
    Stdlib ``argparse``, matching the viewer's own entry point and honouring the
    no-new-dependency constraint.
    """
    parser = argparse.ArgumentParser(
        prog="python scripts/demos/crispr_hit_leg.py",
        description="Replay a frozen, content-addressed pipeline record out of a "
                    "durable registry. Reads; never writes, never re-freezes.",
    )
    parser.add_argument(
        "--address",
        default=None,
        help="Content address of the record to replay (default: the packaged "
             "frozen CRISPR record's).",
    )
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=None,
        help="Registry root to replay from (default: the XDG durable root when "
             "it holds this address, otherwise the packaged demo registry).",
    )
    return parser.parse_args(argv)


def _replay_address(address: str | None, registry_root: Path | None, root: Path) -> str:
    """WHICH record in ``root`` to replay, when the operator named no address.

    An explicit ``--address`` is used as given. Otherwise the default follows
    what the operator DID name: a bare invocation replays the packaged CRISPR
    record, which is the demo this script has always been; an invocation that
    named a ``--registry-root`` replays that root's sole record, because someone
    who pointed this at their own registry meant the record in it, and looking
    for the CRISPR address there would report "absent" about a root that holds
    exactly one perfectly good artifact.

    ``sole_record_address`` raises on a root holding several — correctly: a
    multi-record root has no default, and the operator has to say which.
    """
    if address is not None:
        return address
    if registry_root is None:
        return frozen_crispr_address()
    return sole_record_address(root)


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


async def _diagnose_miss(
    openalex_key: str, seeds: list[dict], parameters: PipelineParameters
) -> str:
    """A miss means no on-disk artifact addresses to the live-computed address.
    Recompute the address the honest way — resolve, then content_address — for the
    STOP report, so the finding names the address that actually moved.
    """
    async with httpx.AsyncClient(timeout=OPENALEX_TIMEOUT_SECONDS) as http_client:
        node0 = await resolve_seeds(
            {"seeds": seeds},
            {},
            resources={
                "http_client": http_client,
                "openalex_api_key": openalex_key,
            },
        )
    resolved = node0["seeds"]
    return content_address([r.node_id for r in resolved], parameters)


async def _main(argv: list[str] | None = None) -> int:
    args = _run_arguments(argv)
    openalex_key = _openalex_key()
    # XDG-first, packaged-registry fallback, unless the operator named a root.
    # The COLD path's shared durable_registry_root() is left untouched (still
    # XDG-writing); this is the ONLY redirect, and it happens at construction.
    registry_root, registry_source = _warm_registry_root(
        args.address, args.registry_root
    )
    registry = PipelineRegistry(registry_root)

    print()
    print("=" * 72)
    print("  IDIOGRAPH — HIT LEG  (cross-process replay of a frozen artifact)")
    print("  A SECOND process reads a FIRST process's artifact from a DURABLE")
    print("  registry outside /tmp. Traversal must never be entered.")
    print("=" * 72)
    print()
    print("  entry point   : cached_run_arxiv_pipeline  (the real cache.py)")
    print(f"  registry root : {registry_root}")
    print(f"                  ({registry_source})")
    print()

    # ---- Fast fail: an empty registry means nothing was ever frozen -------
    # Detect that HERE, straight from disk — no resolve, no pipeline. Otherwise the
    # cache would resolve, run a full n_backward=3200 traversal (many minutes,
    # pipeline.py:1308–1409), and only THEN raise the Node 5.5 guard. The whole
    # selling point is "replays in seconds"; a no-artifact root must fail in one,
    # not after a MISS traversal. Because the root was chosen XDG-first with the
    # packaged registry as fallback, an empty root here means NEITHER source holds
    # the artifact — which should not happen, since the record ships inside the
    # package (a stranger clone and an installed wheel both carry it), so this now
    # flags a broken checkout or install, not a never-frozen operator.
    #
    # RECORDS, not files: `is_record` excludes the derivation-baseline and request
    # sidecars sitting beside each record, which a bare `*.json` glob would print
    # as artifacts and count as replayable.
    present = (
        sorted(p.name for p in registry_root.glob("*.json") if is_record(p))
        if registry_root.exists()
        else []
    )
    if not present:
        print("-" * 72)
        print("  NO ARTIFACT — nothing to replay.")
        print("-" * 72)
        print(f"  registry root : {registry_root}")
        print(f"                  ({registry_source})")
        print("  Neither the XDG durable root nor the packaged idiograph.demo registry")
        print("  held a frozen artifact, so there is nothing to hit. This script")
        print("  REPLAYS a record; it does not create one, and it will not enter the")
        print("  pipeline just to discover the record is absent.")
        print()
        print("  The record ships inside the package, so it should be present already;")
        print("  if it is missing, the checkout or install is incomplete. Otherwise")
        print("  record it with the COLD path — ~$2 and ~50 minutes of live calls,")
        print("  run ONCE, ever:")
        print()
        print("      uv run python scripts/demos/crispr_freeze_trigger.py")
        print()
        print("  Then re-run this script; the replay takes seconds.")
        print("=" * 72)
        print()
        return 3

    print(f"  registry holds: {present}")
    print()

    # ---- The run's own arguments, read off the record it will replay -------
    # NOT imported from the module that froze one particular corpus. The
    # parameters are the block the record's address was computed over, and the
    # seeds are the request dicts on its sidecar — so this script's arguments are
    # BY CONSTRUCTION the arguments that produced the artifact, for any record,
    # and there is no literal anywhere that could drift and turn the call into a
    # MISS. All of it is a local file read: no resolve, no network.
    address = _replay_address(args.address, args.registry_root, registry_root)
    if f"{address}.json" not in present:
        print("-" * 72)
        print("  NO SUCH RECORD — nothing to replay at that address.")
        print("-" * 72)
        print(f"  address        : {address}")
        print(f"  registry root  : {registry_root}")
        print(f"  registry holds : {present}")
        print()
        print("  This script REPLAYS a record; it does not create one, and it will")
        print("  not enter the pipeline just to discover the record is absent.")
        print("=" * 72)
        print()
        return 3

    stored = registry.read(address)
    parameters = stored.parameters
    request = registry.read_request(address)
    if request is None:
        print("-" * 72)
        print("  NO REQUEST — the record does not say what it was asked for.")
        print("-" * 72)
        print(f"  address       : {address}")
        print(f"  expected file : {registry.request_path_for(address)}")
        print()
        print("  The seeds a replay must pass are the REQUEST dicts Node 0 took, and")
        print("  the record holds only the node_ids they resolved to. Without the")
        print("  request sidecar there is no honest way to reconstruct them, and")
        print("  substituting some other run's seeds would silently replay the wrong")
        print("  corpus. Records frozen before IDG-113 carry no request; re-run the")
        print("  freeze that produced this one through `idiograph freeze` to attach")
        print("  one, or pass --address for a record that has it.")
        print("=" * 72)
        print()
        return 4

    seeds = request.seeds
    print(f"  address       : {address}")
    print(f"  label         : {request.label}")
    print(f"  seeds         : {seeds}")
    print("                  (from the record's own request sidecar)")
    print("  parameters    : from the record's own parameters block")
    if parameters.llm is not None:
        print(f"  prompt hash   : "
              f"{parameters.llm.prompt_template_hash[:16]}…  (derived)")
    else:
        # An LLM-free record replays too — the tripwire below simply has nothing
        # to trip on, since there is no Node 5.5 guard to reach.
        print("  llm           : none (this record was frozen LLM-free)")
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
                    seeds,
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
        computed = await _diagnose_miss(openalex_key, seeds, parameters)
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

    # ---- HIT confirmed: the on-disk bundle it replayed --------------------
    hit_address = address_of(hit)
    # `stored` is the bundle already read above, at `address`, through
    # registry.read — which validates that the on-disk file addresses to its own
    # filename, the content-addressed store returning exactly what its key names.
    # A hit can only have landed on the address the live resolution computed, so
    # a disagreement here is a FINDING (corrupt or renamed artifact) rather than
    # an expected branch, and it is asserted rather than papered over with a
    # second read of 9.3 MB.
    if hit_address != address:
        raise AssertionError(
            f"the HIT returned a result addressing to {hit_address} but the "
            f"record read from disk was {address} — the store returned "
            "something its key does not name"
        )

    print(f"  traversal entered : {hit_traversals}")
    print("  Anthropic calls   : 0  (structural — no client exists to draw)")
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
    print("  relationship_type labels : ")
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
            (
                "field-diff names only the re-supplied fields (subset of "
                "{seeds, seed_failures})"
            ),
            set(differing_fields) <= _RESUPPLIED_FIELDS,
            (
                "unexpected differing fields: "
                f"{sorted(set(differing_fields) - _RESUPPLIED_FIELDS)}"
            ),
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
    print(f"  Registry root        : {registry_root}")
    print(f"                         ({registry_source})")
    print(f"  Frozen artifact      : {registry.path_for(hit_address)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
