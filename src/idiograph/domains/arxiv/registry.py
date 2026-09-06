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

THE REQUEST SIDECAR — WHAT THE RUN WAS ASKED FOR (IDG-113 clause 3)
-------------------------------------------------------------------
The address answers what a run was DERIVED FROM: resolved seed node_ids plus
parameters. It cannot answer what a run was ASKED for, because resolution stands
between the two — ``{"doi": "10.1126/science.1225829"}`` and the OpenAlex work id
it resolves to are the same record under different names, and only the second
reaches the address. A record therefore arrives at any later reader with its
request already discarded, and the only way anything downstream could reconstruct
the seeds Node 0 took was to import a literal from the module that happened to
freeze it. :data:`REQUEST_SIDECAR_SUFFIX` closes that: ``<address>.request.json``
carries the request dicts verbatim plus a free-text ``label``, beside the record
they produced.

PARAMETERS ARE NOT DUPLICATED INTO IT. The record's own ``parameters`` block is
authoritative for them, and a second copy that could disagree with the block the
address was computed over would be a drift hazard of exactly the kind
:func:`sole_record_address` exists to close.

ADDRESS-NEUTRAL BY DESIGN. Nothing in the sidecar enters :func:`content_address`.
Two requests differing only in label, or listing the same seeds in a different
order — which :func:`content_address` normalizes away — produce ONE record at ONE
address, and correctly so, because they derive the same artifact.
:meth:`PipelineRegistry.write_request`
is what makes that collision visible rather than silent: CREATE-OR-EQUAL, so a
second request claiming the same address with different content raises instead of
overwriting the first one's account of itself.

CANONICAL FORM, spelled once so byte-equality in that check is well-defined::

    json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\\n"

Sorted keys and a trailing newline for the same reason the derivation manifest's
sidecar carries them: the file is committed to a repository and read by people,
so it is written the way a reviewed artifact is read.
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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


#: Suffix marking a SIDECAR — a file that DESCRIBES a record rather than being
#: one. A record is ``<address>.json``; its derivation-manifest baseline is
#: ``<address>.manifest.json``, named from the same address so the pair cannot
#: drift apart. Declared here, in the module that owns what a registry root's
#: filenames mean, and read by :func:`is_record` and by
#: ``derivation_manifest.sidecar_path_for``.
#:
#: Named suffix rather than a non-``.json`` extension ON PURPOSE: the sidecar IS
#: JSON and should read as JSON to every tool that opens it, so the exclusion is
#: stated explicitly below rather than relying on an extension that happens to
#: fall outside a glob.
MANIFEST_SIDECAR_SUFFIX = ".manifest.json"

#: Suffix marking the REQUEST sidecar — ``<address>.request.json``, what the run
#: that produced the record was ASKED for. Same naming discipline as the manifest
#: above and for the same reason: derived from the record's own address, so the
#: pair cannot drift apart, and stated here in the module that owns what a
#: registry root's filenames mean. See the module docstring for why the request
#: is worth storing at all and why none of it enters the address.
REQUEST_SIDECAR_SUFFIX = ".request.json"

#: Every suffix that marks a file as DESCRIBING a record rather than being one.
#: :func:`is_record` reads this tuple rather than a chain of ``endswith`` tests,
#: so a third sidecar is a row here and not a second place to remember.
SIDECAR_SUFFIXES = (MANIFEST_SIDECAR_SUFFIX, REQUEST_SIDECAR_SUFFIX)


def is_record(path: Path) -> bool:
    """Whether ``path`` is a stored record, as opposed to a sidecar beside one.

    The predicate a registry root is enumerated by. A record is a ``*.json``
    named by its content address; anything carrying a known sidecar suffix
    describes a record and must never be counted as one, or a root holding one
    record plus its baseline manifest and its request would read as holding
    three records.
    """
    path = Path(path)
    return path.suffix == ".json" and not path.name.endswith(SIDECAR_SUFFIXES)


def sole_record_address(root: Path) -> str:
    """The content address of the ONE record stored under ``root``.

    A registry names each record by its own content address (``<address>.json``),
    so a root holding exactly one record already states that record's address in
    the filename. Reading it back off disk is therefore strictly better than
    hand-authoring the hex beside the file: the two cannot drift, because there
    is only one of them.

    SIDECARS ARE NOT RECORDS. A root may also hold files that DESCRIBE a record —
    ``<address>.manifest.json`` derivation baselines and ``<address>.request.json``
    requests — and :func:`is_record` excludes both explicitly. Without that
    exclusion a record with a baseline attached would raise here as "two records",
    which is how an observation channel breaks the thing it observes.

    Deliberately generic. It knows nothing about which record it finds and
    resolves no root of its own — ``root`` is always the caller's parameter, so
    the registry layer never acquires knowledge of any particular artifact.

    Zero or several matches raise :class:`ValueError`. Both are checkout or
    packaging faults rather than caller errors, so the message names the root and
    the count: those two facts are what locate the fault.
    """
    root = Path(root)
    records = sorted(path for path in root.glob("*.json") if is_record(path))
    if len(records) != 1:
        raise ValueError(
            f"expected exactly one *.json record under {root}, found "
            f"{len(records)}"
        )
    return records[0].stem


def durable_registry_root() -> Path:
    """A registry root OUTSIDE /tmp that survives a reboot (XDG data home).

    /tmp is cleaned on reboot and a frozen artifact costs real money, so the
    registry an operator freezes into must outlive the session that wrote it and
    be findable by any later process. Falls back to ``~/.local/share`` when
    ``XDG_DATA_HOME`` is unset — the standard user-data location on this platform.

    It lives HERE rather than in the demo script that first needed it because
    three callers now name the same directory — the ``freeze`` CLI verb, the
    freeze/trigger demo and the HIT-leg demo — and a durable path spelled in three
    places is a path two of them can fall behind. The environment is read inside
    the body on every call, never captured at import, so a caller that redirects
    ``XDG_DATA_HOME`` gets the redirected root.

    NOT a default anywhere in this module. The registry layer resolves no root of
    its own (see :func:`sole_record_address`); this states where a DURABLE one
    conventionally sits and leaves the choosing to the caller.
    """
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(
        Path.home() / ".local" / "share"
    )
    return Path(base) / "idiograph" / "pipeline-registry"


class RecordRequest(BaseModel):
    """What a run was ASKED for: the request seeds Node 0 took, and a label.

    ``seeds`` are the un-resolved request dicts exactly as
    ``cached_run_arxiv_pipeline`` receives them — ``[{"doi": "..."}, ...]`` — as
    distinct from a ``PipelineResult.seeds``, which holds the node_ids Node 0
    resolved them to. ``label`` is free text for a human: it names the corpus, and
    nothing reads it but a person.

    NO PARAMETERS FIELD, deliberately. The record's own ``parameters`` block is
    authoritative for them and is what the address was computed over; a copy here
    could disagree with it and there would be nothing to say which was the run.

    Frozen, like every configuration model in this domain.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    seeds: list[dict]


class RequestSidecarConflict(ValueError):
    """A second request claims an address a different request already holds.

    Raised by :meth:`PipelineRegistry.write_request` under its create-or-equal
    policy. Not a defect in the address — two requests CAN legitimately derive one
    record (the sidecar is address-neutral by design). It is the point at which
    that collision becomes visible instead of one account of the run silently
    replacing another, so the message carries both payloads: which two requests
    collided is the whole of what an operator needs.
    """


def canonical_sidecar_json(payload: dict) -> str:
    """The one canonical serialization a request sidecar is written and compared in.

    Spelled once, here, because byte-equality is the create-or-equal check's
    entire mechanism: a second serialization with different separators would make
    an identical request read as a conflicting one. See the module docstring.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def request_sidecar_path_for(root: Path, address: str) -> Path:
    """Where a record's request sits: ``<root>/<address>.request.json``.

    Named FROM the record's own content address so the pairing cannot drift, and
    suffixed so :func:`is_record` excludes it from the record enumeration — a
    sidecar describes a record, it is never one. Mirrors
    ``derivation_manifest.sidecar_path_for`` exactly.
    """
    return Path(root) / f"{address}{REQUEST_SIDECAR_SUFFIX}"


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

    def request_path_for(self, address: str) -> Path:
        """The on-disk path a given address's request sidecar maps to."""
        return request_sidecar_path_for(self.root, address)

    def read_request(self, address: str) -> RecordRequest | None:
        """Load what the run at ``address`` was asked for, or ``None`` if unstated.

        ABSENT IS NOT EMPTY — the same distinction ``derivation_manifest.read_sidecar``
        keeps, and for the same reason. A record frozen before this channel existed,
        or one written by a caller that named no request, has nothing to say about
        its own request; returning ``None`` leaves that fact in the type rather than
        laundering it into a request with no seeds, which every consumer would then
        have to re-detect. A sidecar that exists and does not parse RAISES: the file
        is there and says something this code cannot read, which is a different fact
        from its absence.
        """
        path = self.request_path_for(address)
        if not path.is_file():
            return None
        return RecordRequest.model_validate_json(path.read_text(encoding="utf-8"))

    def write_request(self, address: str, request: RecordRequest) -> Path:
        """Record what the run at ``address`` was asked for. CREATE-OR-EQUAL.

        Three cases, and the third is the reason this is a method rather than a
        write: no sidecar, so write it; a sidecar holding byte-identical canonical
        content, so do nothing at all; a sidecar holding DIFFERENT content, so
        raise :class:`RequestSidecarConflict` naming the address and both payloads.

        A RECORD IS NEVER REWRITTEN AND NEITHER IS ITS REQUEST. Because the
        sidecar is address-neutral (module docstring), two different requests can
        arrive at one address legitimately — a relabelled corpus, one seed
        respelled — and silently overwriting would let the second one erase the
        first one's account of a run neither of them re-derived. Raising is what
        turns that from a lost fact into a decision the operator makes.

        The no-op leg is a genuine no-op: nothing is opened for writing, so a
        second run against an unchanged request leaves the file's bytes AND its
        mtime exactly as they were.
        """
        payload = canonical_sidecar_json(request.model_dump(mode="json"))
        path = self.request_path_for(address)
        if path.is_file():
            stored = path.read_text(encoding="utf-8")
            if stored == payload:
                return path
            raise RequestSidecarConflict(
                f"a different request is already recorded for {address!r} at "
                f"{path} — a record is never rewritten and neither is its "
                f"request.\nstored:\n{stored}\nincoming:\n{payload}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return path

    def write(
        self, result: PipelineResult, *, request: RecordRequest | None = None
    ) -> str:
        """Persist ``result`` as its content-addressed JSON bundle; return the
        address.

        Stores the faithful ``model_dump(mode="json")`` payload — including the
        explicit-outputs duplication, which is the audit record. The write is
        atomic: the JSON goes to a uniquely-named temp file in ``self.root``,
        then :func:`os.replace` atomically swaps it into place, so no reader
        ever observes a partial ``<address>.json``. The content address is
        verified on the read path (:meth:`read`), not here.

        ``request`` is what the run was ASKED for, and naming it here is what
        keeps the record and its request written by ONE code path — the store —
        rather than by whichever script happened to trigger the run. Defaulted to
        ``None`` so every existing caller persists exactly the bytes it always
        did; a caller that supplies one gets :meth:`write_request`'s
        create-or-equal policy, including its raise.

        The sidecar is written AFTER the record, deliberately: a sidecar describes
        a record, so there must be a record for it to describe. Unlike the
        derivation baseline the cache attaches, this write is NOT fenced — a
        conflicting request is a fact about what was asked for, and swallowing it
        would defeat the only mechanism that makes an address collision visible.
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
        if request is not None:
            self.write_request(address, request)
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
