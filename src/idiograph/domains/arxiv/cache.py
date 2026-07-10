# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Node 8 read-through cache — the decision layer ABOVE ``run_arxiv_pipeline``.

The cache is an explicit decision layer above a pipeline that does not know it is
cached (IDG-030): the pipeline stays the pure computer; this wrapper decides
whether to compute. It owns exactly one short-circuit —
``resolve -> content_address -> hit/skip -> store`` — over the already-shipped
content-addressed registry (Node 8, IDG-029).

Resolution runs on EVERY call, hit or miss. It precedes and PRODUCES the key, so
it can never be short-circuited; only TRAVERSAL is skipped on a hit. A resolution
failure therefore never reaches the cache.

Hit-parity re-supply (IDG-030 constraint)
-----------------------------------------
The content address keys solely on the RESOLVED seed node_ids + ``PipelineParameters``
(``content_address``). Fields of a ``PipelineResult`` derived from the *requested*
seed set — which is NOT part of the key — can therefore differ between two requests
that share an address. A verbatim hit would return the cache-populating request's
values for those fields, diverging from what a fresh miss produces. So on a hit we
re-supply every request-derived field from the CURRENT request's resolve output,
making a hit provably equal to a fresh miss (this mirrors ``registry.read``'s
re-supply of the excluded ``input_node_ids`` witness).

Every ``PipelineResult`` field, classified:

============================  ==================  ============================
field                         classification      hit behaviour
============================  ==================  ============================
nodes                         keyed               use stored
edges                         keyed               use stored
seeds                         request-derived     RE-SUPPLY (order)
cycle_clean                   keyed               use stored
co_citation_edges             keyed               use stored
co_citation_warnings          keyed               use stored
depth_metrics                 keyed               use stored
pagerank                      keyed               use stored
communities                   keyed               use stored
parameters                    keyed (in key)      use stored
seed_failures                 request-derived     RE-SUPPLY
backward_failed_batches       keyed               use stored
forward_failed_seeds          keyed               use stored
truncated_seeds               keyed               use stored
data_integrity_warnings       keyed               use stored
============================  ==================  ============================

Two fields are request-derived and re-supplied:

- ``seed_failures`` — the known instance (IDG-030): failures come from the
  REQUESTED seeds that did not resolve; the requested set is not in the key, so
  ``[A, B, Xbad]`` and ``[A, B]`` (both resolving to ``{A, B}``) share an address
  yet carry different failures.
- ``seeds`` — the ordered resolved node_id list. The address normalizes seed ORDER
  away (``sorted(set(...))``), so two requests resolving to the same set in a
  different order (e.g. ``[A, B]`` vs ``[B, A]``, or an arxiv_id vs its DOI) share
  an address but a fresh miss would produce a differently-ordered ``seeds`` list.
  Re-supplying the current resolve order keeps a hit equal to a fresh miss.

Everything else is a deterministic function of the RESOLVED seed set + parameters
(the key), so a verbatim hit already equals a fresh miss on those fields — the
traversal outputs (``nodes``/``edges``/…), the whole-graph stage results, the
traversal-side failure provenance, and ``parameters`` (which is literally part of
the address). No re-supply, and none is possible for the traversal-side failure
lists without re-running traversal, which a hit exists to avoid.
"""

import httpx

from idiograph.core.logging_config import get_logger
from idiograph.domains.arxiv.models import (
    PaperRecord,
    PipelineParameters,
    PipelineResult,
    SeedResolutionFailure,
)
from idiograph.domains.arxiv.pipeline import resolve_seeds, run_traversal
from idiograph.domains.arxiv.registry import PipelineRegistry, content_address

_log = get_logger("arxiv.cache")


def _resupply_request_derived(
    result: PipelineResult,
    resolved: list[PaperRecord],
    seed_failures: list[SeedResolutionFailure],
) -> PipelineResult:
    """Re-supply the request-derived fields from the CURRENT request's resolve
    output, so a cache hit provably equals a fresh miss (IDG-030 constraint).

    Overrides ``seeds`` (order is request-derived; the address normalizes it away)
    and ``seed_failures`` (derived from the requested, not the resolved, seed set).
    Every other field is keyed and left as the stored/computed value. Uses
    ``model_copy(update=...)`` — ``PipelineResult`` is frozen, so the stored
    instance is never mutated in place. See the module docstring for the full
    field enumeration.
    """
    return result.model_copy(
        update={
            "seeds": [record.node_id for record in resolved],
            "seed_failures": seed_failures,
        }
    )


async def cached_run_arxiv_pipeline(
    seeds: list[dict],
    parameters: PipelineParameters,
    *,
    client: httpx.AsyncClient,
    api_key: str,
    registry: PipelineRegistry,
) -> PipelineResult:
    """Read-through cache over ``run_arxiv_pipeline``'s traversal core.

    ``resolve -> key -> hit/skip -> store``. Resolution (:func:`resolve_seeds`)
    runs on every call — it precedes and produces the content address — and only
    TRAVERSAL (:func:`run_traversal`) is short-circuited on a hit. On a HIT the
    stored ``PipelineResult`` is loaded from ``registry`` and its request-derived
    fields re-supplied from the current resolve output (a hit equals a fresh miss);
    on a MISS the traversal core runs, its result is persisted, and it is returned.

    The traversal call NEVER runs on a hit, so a hit issues no OpenAlex call for
    traversal (including the seed-refetch) — the whole point of the cache. The key
    is derived solely by :func:`content_address` over the resolved seed node_ids +
    ``parameters``; no competing key is computed, and the path is deterministic (no
    wall-clock, RNG, or env reads).

    ``run_arxiv_pipeline`` itself is cache-unaware; this wrapper is the explicit
    decision layer that owns whether to compute. ``registry`` is injected (the
    caller owns its lifecycle), matching the client/api_key injection convention.
    """
    resolved, seed_failures = await resolve_seeds(
        seeds, client=client, api_key=api_key
    )
    address = content_address(
        [record.node_id for record in resolved], parameters
    )

    if registry.path_for(address).exists():
        _log.info("Cache HIT for %s — skipping traversal", address)
        stored = registry.read(address)
        return _resupply_request_derived(stored, resolved, seed_failures)

    _log.info("Cache MISS for %s — running traversal", address)
    result = await run_traversal(
        resolved, parameters, client=client, api_key=api_key
    )
    result = _resupply_request_derived(result, resolved, seed_failures)
    registry.write(result)
    return result
