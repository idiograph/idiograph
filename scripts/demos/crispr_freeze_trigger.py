# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Freeze/Trigger — the IDG-032 record-replay thesis, on real data.

Runs the recorded CRISPR validation corpus twice through the PRODUCTION cached
entry point (:func:`cached_run_arxiv_pipeline`) against a registry this script
owns, and proves the determinism contract on the result:

1. FREEZE (cache MISS) — resolve the two CRISPR seed DOIs, run the full
   traversal, and let Node 5.5 classify the non-seed papers via LIVE Anthropic
   calls. The LLM-annotated ``PipelineResult`` is persisted to the registry.
   The derivation is recorded exactly once.

2. TRIGGER (cache HIT) — the same corpus and the SAME ``PipelineParameters``,
   with NO Anthropic client. The production cached path returns the persisted
   result byte-identically, without reaching traversal and without drawing a
   single token.

The HIT leg is a self-enforcing tripwire, not a claim on trust. It passes
``parameters.llm`` SET and ``anthropic_client=None`` — the exact combination
``run_traversal`` raises ``ValueError`` on (pipeline.py, the Node 5.5 guard).
So if the HIT had reached traversal, it would have CRASHED. That it instead
returns a fully LLM-annotated graph is proof the annotations were replayed, not
re-derived.

Nondeterminism in the answer is acceptable; nondeterminism in the
infrastructure is not. Note that ``temperature=0.0`` does NOT make the model
call reproducible — it never has. That is the point: soundness comes from
LLMConfig-in-the-content-address plus LLM-call-on-miss-only (IDG-032/IDG-035),
never from pinning a sampling parameter.

Instrumentation is observation-only. The Anthropic counter is a call-through
proxy over a real ``AsyncAnthropic``; the traversal counter is a call-through
wrapper over the real ``run_traversal``; OpenAlex requests are counted with an
httpx event hook. Nothing is stubbed, and no cache bypass is introduced — every
leg goes through the real ``cached_run_arxiv_pipeline``.

Run it::

    uv run python scripts/demos/crispr_freeze_trigger.py

Requires ``ANTHROPIC_API_KEY`` and ``OPENALEX_API_KEY`` (env or ``.env``).
"""

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from idiograph.core.logging_config import get_logger
from idiograph.domains.arxiv import cache as cache_module
from idiograph.domains.arxiv.cache import cached_run_arxiv_pipeline
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    ForwardParameters,
    LLMConfig,
    PipelineParameters,
    PipelineResult,
)
from idiograph.domains.arxiv.pipeline import resolve_seeds
from idiograph.domains.arxiv.registry import (
    PipelineRegistry,
    address_of,
    content_address,
)
from idiograph.domains.arxiv.relationship_annotation import prompt_template_hash

_log = get_logger("demos.crispr_freeze_trigger")

# The recorded CRISPR validation corpus, seeded as DOIs (the Node 0 path
# repaired in #42). Doudna/Charpentier 2012 -> W2045435533; Zhang 2013 ->
# W2064815984.
SEEDS = [
    {"doi": "10.1126/science.1225829"},
    {"doi": "10.1126/science.1231143"},
]

# 3200 is NOT a size choice and this run is NOT "small enough to watch" — the
# freeze takes ~50 minutes. 3200 is the ONE value at which the Node 3 backward
# cap is a no-op: nothing is truncated and nothing is stranded below the cap, so
# every retained node stays reachable from a root and Node 6 has no orphan to
# raise on. It is a workaround for an unfixed defect, not a tuning knob. Below it
# the cap strands nodes and compute_depth_metrics (pipeline.py:866) raises at
# pipeline.py:921 — `raise ValueError(f"Node {nid} unreachable from any root")`.
# Do NOT change the value: it is baked into the frozen artifact's content address.
N_BACKWARD = 3200
N_FORWARD = 4

# OpenAlex is slow enough on deep traversal that httpx's 5s default timeout can
# spuriously fail a call (finding 8a6e6be4). The production path owns no
# OpenAlex timeout, so the caller must set one.
OPENALEX_TIMEOUT_SECONDS = 30.0

_MODEL_ID = "claude-haiku-4-5-20251001"

# Node 5.5 draws serially ~1,100+ times on a cold MISS and the healthy path is
# otherwise SILENT — so is a hang. Heartbeat every this-many draws (roughly once
# a minute at the observed ~1 draw/2.5s) so a live run is visibly distinguishable
# from a stall. Demo-layer only: the heartbeat rides the counting proxy, nothing
# in src/ is touched.
_DRAW_PROGRESS_EVERY = 25


class _MessagesProxy:
    """Counts ``messages.create`` calls, then delegates to the real client.

    Also emits a heartbeat: every draw is serial and the healthy path prints
    nothing, so without this a 45-minute leg and a hung one look identical from
    the terminal (the 2026-07-17 operator watched a finished run for 40 minutes).
    """

    def __init__(self, inner: object, owner: "CountingAnthropicClient") -> None:
        self._inner = inner
        self._owner = owner

    async def create(self, **kwargs: object) -> object:
        self._owner.calls += 1
        if (
            self._owner.calls == 1
            or self._owner.calls % _DRAW_PROGRESS_EVERY == 0
        ):
            print(
                f"    … Node 5.5: {self._owner.calls} live draws so far "
                "(healthy — this leg is silent otherwise)",
                flush=True,
            )
        return await self._inner.create(**kwargs)


class CountingAnthropicClient:
    """Observation-only proxy over a real ``AsyncAnthropic``.

    Every draw still goes to the live API — this counts them, it does not stub
    them. Node 5.5 touches only ``.messages.create``; anything else falls
    through to the wrapped client.
    """

    def __init__(self, inner: AsyncAnthropic) -> None:
        self._inner = inner
        self.calls = 0
        self.messages = _MessagesProxy(inner.messages, self)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class TraversalSpy:
    """Call-through counter over ``cache.run_traversal``.

    Records how many times the cache ENTERED traversal. The real function still
    runs; this adds no behaviour and skips nothing.
    """

    def __init__(self) -> None:
        self.entries = 0
        self._real = cache_module.run_traversal

    async def __call__(self, *args: object, **kwargs: object) -> PipelineResult:
        self.entries += 1
        return await self._real(*args, **kwargs)


class RequestCounter:
    """httpx event hook counting outbound OpenAlex requests."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, request: httpx.Request) -> None:
        self.count += 1


def _canonical(result: PipelineResult) -> bytes:
    """The result's model_dump as canonical bytes — the byte-identity witness."""
    return json.dumps(
        result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _parameters() -> PipelineParameters:
    """The SAME parameters both legs pass — so both legs key to one address.

    ``prompt_template_hash()`` is DERIVED from the Node 5.5 module template,
    never hand-entered (IDG-032): edit the prompt and the address moves on its
    own. The LLMConfig rides PipelineParameters, so the model id, the prompt
    content, and the decoding params all enter the content address.
    """
    return PipelineParameters(
        backward=BackwardParameters(n_backward=N_BACKWARD, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=N_FORWARD,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        llm=LLMConfig(
            model_id=_MODEL_ID,
            prompt_template_hash=prompt_template_hash(),
            temperature=0.0,
            max_tokens=512,
        ),
    )


def _durable_registry_root() -> Path:
    """A registry root OUTSIDE /tmp that survives a reboot (XDG data home).

    /tmp is cleaned on reboot and the frozen artifact cost real money, so the
    registry must outlive this session and be findable by any later process.
    Falls back to ``~/.local/share`` when XDG_DATA_HOME is unset — the standard
    user-data location on this platform.

    This lives HERE, not in :mod:`crispr_hit_leg`, because that module imports
    from this one (the module of record): the single definition belongs on the
    side the import direction already points at.
    """
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(
        Path.home() / ".local" / "share"
    )
    return Path(base) / "idiograph" / "pipeline-registry"


def _boundary_statement() -> list[str]:
    """The MEASURED freeze/replay boundary, stated in the demo's own voice.

    Five facts, measured twice: by source read at HEAD 6c5d975 and by live wire
    count on the 2026-07-17 HIT leg (2 OpenAlex requests observed). Shared with
    :mod:`crispr_hit_leg` so both scripts state the same boundary and neither can
    drift into the older, incomplete "no network beyond seed resolution" claim.
    """
    return [
        "  The measured boundary:",
        "",
        "    - The frozen and replayed region is Nodes 3 through 7 — the traversal",
        "      and the LLM draw — served from a content-addressed bundle on a HIT.",
        "    - Node 0 seed resolution is NOT frozen. It re-runs live against OpenAlex",
        "      on every call, hit or miss, at one HTTP GET per seed — unconditional",
        "      by construction, not oversight: resolution PRODUCES the content",
        "      address, so it cannot be skipped.",
        "    - A HIT therefore makes one OpenAlex GET per seed and ZERO Anthropic",
        "      calls.",
        "    - A HIT is NOT hermetic and cannot run offline: it fails outright if",
        "      OpenAlex is unreachable or the API key is invalid.",
        "    - The RECORD is portable across processes. The CORPUS it was drawn from",
        "      is live-sourced at capture and is NOT portable across time — the same",
        "      query froze 10 papers on one date and 11 on another, neither wrong.",
    ]


def _preconditions() -> tuple[str, str]:
    """Both keys or nothing — a stubbed LLM or a faked corpus voids the demo."""
    load_dotenv()

    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    openalex_key = (os.environ.get("OPENALEX_API_KEY") or "").strip()

    missing = [
        name
        for name, value in (
            ("ANTHROPIC_API_KEY", anthropic_key),
            ("OPENALEX_API_KEY", openalex_key),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"PRECONDITION FAILED: {', '.join(missing)} not set (env or .env).\n"
            "The MISS leg needs a real Anthropic key and OpenAlex needs its key. "
            "This demo does not stub the LLM or fabricate a corpus — without both "
            "keys there is nothing honest to prove."
        )
    return anthropic_key, openalex_key


def _describe_corpus(result: PipelineResult) -> None:
    """Print the corpus we actually got — real data, not expected data."""
    seed_ids = set(result.seeds)
    non_seeds = [n for n in result.nodes if n.node_id not in seed_ids]

    print(f"  nodes            : {len(result.nodes)} "
          f"({len(seed_ids)} seed, {len(non_seeds)} non-seed)")
    print(f"  edges            : {len(result.edges)}")
    print(f"  co-citation edges: {len(result.co_citation_edges)}")

    depths = Counter(n.hop_depth for n in result.nodes)
    print("  hop depths       : "
          + ", ".join(f"depth {d}: {c}" for d, c in sorted(depths.items())))

    labels = Counter(
        n.relationship_type or "(none)" for n in non_seeds
    )
    print("  Node 5.5 labels  : "
          + (", ".join(f"{k}: {v}" for k, v in sorted(labels.items())) or "(none)"))

    print("  papers:")
    for node in result.nodes:
        kind = "SEED " if node.node_id in seed_ids else "     "
        label = node.relationship_type or "-"
        conf = (
            f"{node.semantic_confidence:.2f}"
            if node.semantic_confidence is not None
            else " -  "
        )
        title = (node.title or "(untitled)")[:58]
        print(f"    {kind}[d{node.hop_depth}] {label:<24} {conf}  {title}")


async def _main() -> int:
    anthropic_key, openalex_key = _preconditions()

    parameters = _parameters()
    registry_root = _durable_registry_root()
    registry = PipelineRegistry(registry_root)

    # ---- Refuse to re-freeze (pre-check) ---------------------------------
    # Resolve the seeds and compute the content address the honest way — the
    # exact ``resolve_seeds -> content_address`` sequence the production cache
    # uses on its own hit branch — and look in the DURABLE registry BEFORE
    # emitting any MISS-framed output. If the artifact is already frozen, a
    # second freeze would only re-hit its own record: refuse, spend nothing, and
    # point the operator at the replay script. This is why item (1) is not a
    # one-liner — a durable root means a second invocation would otherwise HIT on
    # leg 1, make zero Anthropic calls, and fail its own MISS assertions. Guarding
    # here keeps those assertions honest by construction: the freeze only ever
    # runs against a registry with no artifact at this address.
    #
    # Cost: one OpenAlex GET per seed. These GETs belong to the pre-check and are
    # NOT folded into the per-leg OpenAlex counts printed below (separate client,
    # separate counter) — the demo's leg totals stay truthful about what they
    # include.
    precheck_openalex = RequestCounter()
    async with httpx.AsyncClient(
        timeout=OPENALEX_TIMEOUT_SECONDS,
        event_hooks={"request": [precheck_openalex]},
    ) as precheck_client:
        precheck_resolved, _ = await resolve_seeds(
            SEEDS, client=precheck_client, api_key=openalex_key
        )
    address = content_address(
        [record.node_id for record in precheck_resolved], parameters
    )
    if registry.path_for(address).exists():
        print()
        print("=" * 72)
        print("  ALREADY FROZEN — refusing to re-freeze.")
        print("=" * 72)
        print()
        print(f"  registry root : {registry_root}")
        print(f"  address       : {address}")
        print(f"  artifact      : {registry.path_for(address)}")
        print()
        print("  The CRISPR artifact is already recorded in the durable registry.")
        print("  Re-running the freeze would resolve, hit its own record, draw no")
        print("  tokens, and prove nothing new — so this leg does not run. The MISS")
        print("  assertions below only stay honest against an EMPTY registry, so the")
        print("  cold path deliberately executes at most once, ever.")
        print()
        print("  To watch the replay — seconds, no model — run the WARM path:")
        print()
        print("      uv run python scripts/demos/crispr_hit_leg.py")
        print()
        print(f"  pre-check cost: {precheck_openalex.count} OpenAlex GET(s), "
              "0 Anthropic calls, $0.")
        print("=" * 72)
        print()
        return 0

    traversal_spy = TraversalSpy()
    openalex_calls = RequestCounter()

    print()
    print("=" * 72)
    print("  IDIOGRAPH — FREEZE / TRIGGER")
    print("  IDG-032 record-replay determinism, on the CRISPR corpus,")
    print("  through the production cached entry point.")
    print("=" * 72)
    print()
    print("  entry point : cached_run_arxiv_pipeline  (the real cache.py)")
    print(f"  seeds       : {SEEDS[0]['doi']}  (Doudna/Charpentier 2012)")
    print(f"                {SEEDS[1]['doi']}  (Zhang 2013)")
    print(f"  n_backward  : {N_BACKWARD}      n_forward: {N_FORWARD}")
    print(f"  model       : {_MODEL_ID}")
    print(f"  prompt hash : {parameters.llm.prompt_template_hash[:16]}…  (derived)")
    print(f"  registry    : {registry_root}")
    print("                (durable; pre-check confirmed no artifact at this "
          "address — first leg MUST miss)")
    print(f"  pre-check   : {precheck_openalex.count} OpenAlex GET(s) to confirm no "
          "prior freeze")
    print("                (not folded into the per-leg counts below)")
    print()

    # Install the traversal counter on the symbol the cache actually calls. This
    # is a call-through spy: the production run_traversal still does the work.
    cache_module.run_traversal = traversal_spy
    try:
        async with httpx.AsyncClient(
            timeout=OPENALEX_TIMEOUT_SECONDS,
            event_hooks={"request": [openalex_calls]},
        ) as http_client:
            # ---- LEG 1: FREEZE (cache MISS, live LLM) --------------------
            raw_anthropic = AsyncAnthropic(api_key=anthropic_key)
            counting_anthropic = CountingAnthropicClient(raw_anthropic)

            print("-" * 72)
            print("  LEG 1 — FREEZE   (expect: cache MISS, traversal runs, LLM draws)")
            print("-" * 72)
            miss_openalex_before = openalex_calls.count

            miss = await cached_run_arxiv_pipeline(
                SEEDS,
                parameters,
                client=http_client,
                api_key=openalex_key,
                registry=registry,
                anthropic_client=counting_anthropic,
            )

            miss_traversals = traversal_spy.entries
            miss_anthropic = counting_anthropic.calls
            miss_openalex = openalex_calls.count - miss_openalex_before
            await raw_anthropic.close()

            print(f"  traversal entered : {miss_traversals}")
            print(f"  Anthropic calls   : {miss_anthropic}")
            print(f"  OpenAlex requests : {miss_openalex}")
            print()
            _describe_corpus(miss)
            print()

            # ---- LEG 2: TRIGGER (cache HIT, NO client) -------------------
            print("-" * 72)
            print("  LEG 2 — TRIGGER  (same params, anthropic_client=None)")
            print("  parameters.llm is SET and there is NO client: if this leg")
            print("  reached traversal, Node 5.5's guard would RAISE ValueError.")
            print("-" * 72)
            hit_traversal_before = traversal_spy.entries
            hit_openalex_before = openalex_calls.count

            hit = await cached_run_arxiv_pipeline(
                SEEDS,
                parameters,
                client=http_client,
                api_key=openalex_key,
                registry=registry,
                anthropic_client=None,
            )

            hit_traversals = traversal_spy.entries - hit_traversal_before
            hit_openalex = openalex_calls.count - hit_openalex_before

            print(f"  traversal entered : {hit_traversals}")
            print("  Anthropic calls   : 0  (structural — no client exists to draw)")
            print(f"  OpenAlex requests : {hit_openalex}  (resolution only — "
                  "resolution runs on every call, hit or miss)")
            print()
    finally:
        cache_module.run_traversal = traversal_spy._real

    # ---- EVIDENCE ---------------------------------------------------------
    miss_address = address_of(miss)
    hit_address = address_of(hit)
    miss_bytes = _canonical(miss)
    hit_bytes = _canonical(hit)
    artifacts = sorted(p.name for p in registry_root.glob("*.json"))

    print("=" * 72)
    print("  EVIDENCE")
    print("=" * 72)
    print()
    print(f"  content address (MISS) : {miss_address}")
    print(f"  content address (HIT)  : {hit_address}")
    print(f"  registry artifacts     : {artifacts}")
    print()
    print(f"  MISS model_dump : {len(miss_bytes)} bytes")
    print(f"  HIT  model_dump : {len(hit_bytes)} bytes")
    print()

    checks: list[tuple[str, bool, str]] = [
        (
            "both legs share ONE content address",
            miss_address == hit_address,
            f"{miss_address} vs {hit_address}",
        ),
        (
            "hit.model_dump() == miss.model_dump() (byte-identical)",
            hit_bytes == miss_bytes and hit.model_dump() == miss.model_dump(),
            "canonical dumps differ",
        ),
        (
            "MISS made N>0 live Anthropic calls",
            miss_anthropic > 0,
            f"got {miss_anthropic}",
        ),
        (
            "MISS ran traversal exactly once",
            miss_traversals == 1,
            f"got {miss_traversals}",
        ),
        (
            "HIT performed NO traversal",
            hit_traversals == 0,
            f"got {hit_traversals}",
        ),
        (
            "HIT replayed the LLM annotations it never derived",
            any(n.relationship_type is not None for n in hit.nodes),
            "no relationship_type survived the replay",
        ),
        (
            "registry holds exactly one artifact, named by the address",
            artifacts == [f"{miss_address}.json"],
            f"got {artifacts}",
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
        print(f"  THESIS NOT DEMONSTRATED — {failures} check(s) failed.")
        print("=" * 72)
        return 1

    print("  THESIS DEMONSTRATED.")
    print()
    print("  The derivation was recorded once, against a content address that")
    print("  includes the model id, the prompt content, and the decoding params.")
    print("  The HIT leg returned the same bytes with no model and no traversal.")
    print()
    for line in _boundary_statement():
        print(line)
    print()
    print("  The answer came from a nondeterministic model.")
    print("  The infrastructure around it did not.")
    print("=" * 72)
    print()
    print(f"  Frozen artifact: {registry_root / (miss_address + '.json')}")
    print()
    print("  Recorded once. Replay it with the warm path — seconds, no model:")
    print("      uv run python scripts/demos/crispr_hit_leg.py")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
