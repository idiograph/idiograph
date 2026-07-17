# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""HIT leg — cross-process replay of the frozen CRISPR artifact (IDG-032).

The companion :mod:`crispr_freeze_trigger` demo proves record-replay in ONE
process: it runs a MISS then a HIT against a registry it just created in /tmp.
This script proves the strictly stronger claim the MISS leg left unproven — that
a SECOND, independent process can replay a FIRST process's artifact out of a
DIFFERENT, durable directory:

1. FREEZE happened earlier (09:40–10:33 today, another process). It persisted
   exactly one ``PipelineResult`` — the first this project ever wrote — via live
   Anthropic + OpenAlex calls, at ~$2 and ~53 minutes. This script does NOT
   reproduce it and could not do so for free.

2. This process stands up a DURABLE registry root outside /tmp, places that
   existing artifact into it under its address-derived filename (a COPY; the
   original is never touched), and calls the production ``cached_run_arxiv_pipeline``
   against it with the SAME ``PipelineParameters`` and ``anthropic_client=None``.

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
import shutil
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Import the STANDARD and — per its own docstring — the thing to reuse. Deriving
# the parameters and seeds from the module that produced the artifact is the
# whole experiment: a re-typed literal that hashes differently fails as a MISS.
# crispr_freeze_trigger guards _main() behind __main__, so importing it is inert.
from crispr_freeze_trigger import (  # noqa: E402  (scripts/demos is on sys.path[0])
    OPENALEX_TIMEOUT_SECONDS,
    SEEDS,
    RequestCounter,
    TraversalSpy,
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

# The one artifact this project has ever persisted (FREEZE leg, 10:33 today).
# Read-only to this script: we COPY out of it and never write into its directory.
ARTIFACT = Path(
    "/tmp/idiograph-crispr-freeze-trigger-ui5it4i7/"
    "4e368a767b8778a9b5487abc449c6dbdf37815da60783110eead60ee1d9b7200.json"
)
EXPECTED_ADDRESS = ARTIFACT.stem

# The re-supplied (request-derived) fields — see cache._resupply_request_derived.
# A field-level diff between the on-disk bundle and the returned result should
# name only these; anything else is a finding.
_RESUPPLIED_FIELDS = {"seeds", "seed_failures"}


def _durable_registry_root() -> Path:
    """A registry root OUTSIDE /tmp that survives a reboot (XDG data home).

    /tmp is cleaned on reboot and the artifact cost real money; the registry must
    outlive this session. Falls back to ``~/.local/share`` when XDG_DATA_HOME is
    unset — the standard user-data location on this platform.
    """
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(
        Path.home() / ".local" / "share"
    )
    return Path(base) / "idiograph" / "pipeline-registry"


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


def _hash_file(path: Path) -> str:
    """sha256 of a file, read in chunks (the artifact is ~9 MB)."""
    h = sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _place_artifact(registry: PipelineRegistry) -> Path:
    """Copy the existing artifact into the durable registry under its
    address-derived filename (``root/<address>.json``), leaving the original
    untouched. A renamed/arbitrary-path copy would be preserved but unreadable —
    ``PipelineRegistry.path_for`` makes the filename the key.
    """
    registry.root.mkdir(parents=True, exist_ok=True)
    target = registry.path_for(EXPECTED_ADDRESS)
    shutil.copyfile(ARTIFACT, target)  # COPY out; original is never modified.
    return target


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
    """A miss means the placed file's name != the computed address. Recompute the
    address the honest way — resolve, then content_address — for the STOP report.
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
    print(f"  source artifact : {ARTIFACT}")
    print(f"  durable root    : {registry_root}")
    print()

    # ---- Freeze the evidence: artifact hash BEFORE anything ----------------
    sha_before = _hash_file(ARTIFACT)
    artifact_bytes = ARTIFACT.stat().st_size
    print("-" * 72)
    print("  PLACEMENT  (copy the existing artifact into the durable registry)")
    print("-" * 72)
    print(f"  artifact size        : {artifact_bytes} bytes")
    print(f"  artifact sha256 (pre): {sha_before}")

    target = _place_artifact(registry)
    print(f"  placed under         : {target}")
    print(f"  registry path_for    : {registry.path_for(EXPECTED_ADDRESS)}")

    # registry.read validates that the placed file addresses to its own filename
    # — the content-addressed store returning exactly what its key names. A
    # mismatch here is a FINDING (corrupt/renamed artifact), so let it propagate.
    stored = registry.read(EXPECTED_ADDRESS)
    print(f"  registry.read OK     : addresses to {address_of(stored)}")
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

    # ---- MISS abort path: report addresses and STOP -----------------------
    if guard_raised or hit_traversals > 0:
        computed = await _diagnose_miss(openalex_key)
        print(f"  traversal entered : {hit_traversals}")
        print()
        print("=" * 72)
        print("  MISS — STOPPING. The call did not hit the frozen artifact.")
        print("=" * 72)
        print(f"  expected address : {EXPECTED_ADDRESS}")
        print(f"  computed address : {computed}")
        print(f"  guard raised     : {guard_raised}"
              + (f" ({guard_error})" if guard_raised else ""))
        print()
        print("  Either the parameters drifted or OpenAlex resolved a seed to a")
        print("  different id since 10:33. Both are FINDINGS for the design seat.")
        print("  Not retrying, not regenerating — that would cost $2 and destroy")
        print("  the evidence. The artifact was NOT modified.")
        print("=" * 72)
        sha_after = _hash_file(ARTIFACT)
        print(f"  artifact sha256 (post): {sha_after}")
        print(f"  artifact unchanged    : {sha_before == sha_after}")
        return 2

    print(f"  traversal entered : {hit_traversals}")
    print(f"  Anthropic calls   : 0  (structural — no client exists to draw)")
    print(f"  OpenAlex requests : {hit_openalex}  (seed resolution — runs on "
          "every call, hit or miss; a HIT is not hermetic)")
    print()

    # ---- EVIDENCE ---------------------------------------------------------
    hit_address = address_of(hit)
    differing_fields, resupplied_equal = _field_diff(stored, hit)
    labels = Counter(n.relationship_type or "null" for n in hit.nodes)
    non_seed_labeled = sum(
        1 for n in hit.nodes if n.relationship_type is not None
    )
    sha_after = _hash_file(ARTIFACT)

    print("=" * 72)
    print("  EVIDENCE")
    print("=" * 72)
    print()
    print(f"  computed address (HIT)   : {hit_address}")
    print(f"  expected address         : {EXPECTED_ADDRESS}")
    print(f"  artifact filename stem   : {ARTIFACT.stem}")
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
    print(f"  artifact sha256 (pre)    : {sha_before}")
    print(f"  artifact sha256 (post)   : {sha_after}")
    print()

    checks: list[tuple[str, bool, str]] = [
        (
            "HIT entered traversal ZERO times (THE proof)",
            hit_traversals == 0,
            f"got {hit_traversals}",
        ),
        (
            "computed address == expected == artifact filename stem",
            hit_address == EXPECTED_ADDRESS == ARTIFACT.stem,
            f"{hit_address} vs {EXPECTED_ADDRESS} vs {ARTIFACT.stem}",
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
            registry.path_for(EXPECTED_ADDRESS).exists(),
            "address-named file missing",
        ),
        (
            "artifact sha256 unchanged (before == after)",
            sha_before == sha_after,
            f"{sha_before} != {sha_after}",
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
    print("=" * 72)
    print()
    print(f"  Durable registry root: {registry_root}")
    print(f"  Frozen artifact      : {registry.path_for(EXPECTED_ADDRESS)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
