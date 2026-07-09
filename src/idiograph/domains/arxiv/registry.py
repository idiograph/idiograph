# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Node 8 — the registry: content-addressed persistence for ``PipelineResult``.

A completed :class:`PipelineResult` is persisted as ONE content-addressed
artifact (IDG-029): a single JSON bundle, not separately-addressable
sub-artifacts. The on-disk format is the faithful Pydantic dump
(``model_dump(mode="json")`` → JSON → ``model_validate``); the explicit-outputs
duplication in that dump is the audit record, preserved verbatim.

The content address (cache key) is a pure, deterministic function of what
*produced* the graph: the RESOLVED seed set and the pipeline parameters —
``(frozenset(PipelineResult.seeds), PipelineParameters)``. It is derivable from
those inputs directly, without a whole ``PipelineResult`` in hand, so a future
read-through cache can compute the key from resolved seeds BEFORE running the
pipeline. Keying over the resolved set (not the originally-requested set) is the
honest content address: a cache hit provably equals a fresh miss.
``seed_failures[].seed`` (requested seeds that failed to resolve) stays in the
artifact as provenance but is NOT part of the address.

Reload re-supplies the one excluded witness. ``CycleCleanResult.input_node_ids``
is ``Field(exclude=True)``, so ``model_dump()`` omits it and a naive
``model_validate(model_dump(result))`` raises. :func:`read_result` reconstructs
``input_node_ids`` from the loaded node list before validating. This is the sole
reload subtlety; it is not generalized to any other field.
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from idiograph.domains.arxiv.models import PipelineParameters, PipelineResult


def content_address(
    seeds: Iterable[str], parameters: PipelineParameters
) -> str:
    """Derive the content address for a pipeline run from its direct inputs.

    Pure and deterministic: a function of the RESOLVED seed node_ids and the
    parameters alone — no wall-clock, no RNG, no environment, no iteration-order
    leakage. The seed set is order-normalized (deduplicated and sorted) so the
    same resolved set in any order yields the same address; the parameters are
    dumped canonically (JSON mode, sorted keys). Callable BEFORE a pipeline runs
    — it needs only the resolved seeds and parameters, not a ``PipelineResult``.
    """
    normalized_seeds = sorted(set(seeds))
    payload = {
        "seeds": normalized_seeds,
        "parameters": parameters.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def address_of(result: PipelineResult) -> str:
    """The content address a ``PipelineResult`` addresses to.

    Convenience over :func:`content_address` using the result's own resolved
    ``seeds`` and ``parameters``. By construction this equals the address the
    same run's resolved seeds + parameters would produce before the run.
    """
    return content_address(result.seeds, result.parameters)


class PipelineRegistry:
    """Content-addressed on-disk store for ``PipelineResult`` bundles.

    Rooted at a directory; each result is one ``<address>.json`` file named by
    its content address. Takes no OpenAlex client and constructs none — the
    persistence path performs no network I/O (IDG-024). The store is the
    substrate a later read-through cache sits on; it does not itself short-circuit
    ``run_arxiv_pipeline``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, address: str) -> Path:
        """The on-disk path a given address maps to."""
        return self.root / f"{address}.json"

    def write(self, result: PipelineResult) -> str:
        """Persist ``result`` as its content-addressed JSON bundle; return the
        address.

        Stores the faithful ``model_dump(mode="json")`` payload — including the
        explicit-outputs duplication, which is the audit record. The write is
        atomic: the JSON goes to a uniquely-named temp file in ``self.root``,
        then :func:`os.replace` atomically swaps it into place, so no reader
        ever observes a partial ``<address>.json``. The content address is
        verified on the read path (:meth:`read`), not here.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        address = address_of(result)
        payload = result.model_dump(mode="json")

        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        target = self.path_for(address)
        # Temp file in the SAME directory as the target so os.replace is a true
        # atomic rename (single-writer contract; this just closes the
        # torn-write window, not a concurrent-writer lock).
        fd, tmp_name = tempfile.mkstemp(dir=self.root, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(text)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return address

    def read(self, address: str) -> PipelineResult:
        """Load the ``PipelineResult`` stored at ``address``.

        Reconstructs the excluded ``CycleCleanResult.input_node_ids`` witness
        from the loaded node list before validating, then asserts the address
        recomputed from the loaded result equals the requested ``address`` — a
        content-addressed store must return exactly what its key names.
        """
        payload = json.loads(self.path_for(address).read_text(encoding="utf-8"))

        # Witness re-supply — the ONLY reload subtlety. model_dump() omits the
        # excluded input_node_ids; reconstruct it from the loaded nodes before
        # constructing/validating the embedded CycleCleanResult.
        payload["cycle_clean"]["input_node_ids"] = [
            node["node_id"] for node in payload["nodes"]
        ]
        result = PipelineResult.model_validate(payload)

        loaded_address = address_of(result)
        if loaded_address != address:
            raise ValueError(
                f"content address mismatch on load: stored under {address!r} "
                f"but the loaded result addresses to {loaded_address!r}"
            )
        return result
