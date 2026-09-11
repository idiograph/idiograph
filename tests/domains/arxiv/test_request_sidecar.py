# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""The request sidecar: what a run was ASKED for, beside the record it produced.

The content address answers what a record was DERIVED FROM — resolved seed
node_ids plus parameters. Resolution stands between that and what a caller
actually typed, so a stored record arrives at every later reader with its request
already discarded. ``<address>.request.json`` is the file that keeps it, and this
module pins the three things that make it trustworthy:

1. THE CANONICAL FORM. The sidecar is written in exactly one serialization, and
   byte-equality in that form is the whole mechanism of the write policy below.
   Asserted against the expression itself, not against a re-description of it.
2. CREATE-OR-EQUAL. No sidecar writes one; an identical request is a genuine
   no-op (nothing is opened for writing, so even the mtime is untouched); a
   DIFFERENT request raises, naming the address and both payloads. The sidecar is
   address-neutral by design, so two requests CAN legitimately reach one address
   — raising is what turns that from one account of a run silently erasing
   another into a decision the operator makes.
3. SIDECARS ARE NOT RECORDS. A root holding one record plus its baseline manifest
   plus its request still holds ONE record, or every consumer of
   ``sole_record_address`` would break the moment a record acquired an account of
   itself.

Plus the packaged CRISPR record's own committed sidecar, checked against the
authored literal in ``idiograph.demo`` — the "independent second opinion" idiom
``test_freeze_trigger_address.py`` uses for the address, applied to the request.

All offline: tmp_path roots and one packaged file read. No network, no credential.
"""

import json
from pathlib import Path

import pytest

from idiograph.demo import FROZEN_CRISPR_SEEDS, REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv.registry import (
    REQUEST_SIDECAR_SUFFIX,
    PipelineRegistry,
    RecordRequest,
    RequestSidecarConflict,
    canonical_sidecar_json,
    is_record,
    request_sidecar_path_for,
    sole_record_address,
)

# A well-formed address that names nothing — the policy under test never reads a
# record, so these tests need a key, not an artifact.
_ADDRESS = "a" * 64
_OTHER_ADDRESS = "b" * 64

# The label the packaged CRISPR request carries, AUTHORED here rather than read
# from the file it is checked against. Same discipline as `FROZEN_ADDRESS` in
# test_freeze_trigger_address.py and for the same reason: a test that derived the
# expected value from the artifact would compare it with itself and pass for any
# contents whatsoever.
PACKAGED_CRISPR_LABEL = "CRISPR April 2026 exhibit"


def _request(label: str = "a corpus", seeds: list[dict] | None = None) -> RecordRequest:
    return RecordRequest(
        label=label,
        seeds=[{"doi": "10.1/x"}, {"doi": "10.1/y"}] if seeds is None else seeds,
    )


# ── The canonical form ────────────────────────────────────────────────────────


def test_the_sidecar_is_written_in_the_canonical_form(tmp_path: Path) -> None:
    """Sorted keys, no ASCII escaping, two-space indent, trailing newline.

    Asserted against the expression the module docstring names rather than
    against a transcribed blob: the create-or-equal check is byte-equality, so a
    second serialization anywhere would make an identical request read as a
    conflicting one.
    """
    request = _request(label="a label with a non-ASCII em dash — here")
    path = PipelineRegistry(tmp_path).write_request(_ADDRESS, request)

    assert path.read_text(encoding="utf-8") == json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    assert canonical_sidecar_json(request.model_dump(mode="json")) == path.read_text(
        encoding="utf-8"
    )
    assert "—" in path.read_text(encoding="utf-8")


def test_the_sidecar_is_named_from_the_records_own_address(tmp_path: Path) -> None:
    """The pairing is the filename, so the two cannot drift apart."""
    registry = PipelineRegistry(tmp_path)
    path = registry.write_request(_ADDRESS, _request())

    assert path == tmp_path / f"{_ADDRESS}{REQUEST_SIDECAR_SUFFIX}"
    assert path == request_sidecar_path_for(tmp_path, _ADDRESS)
    assert path == registry.request_path_for(_ADDRESS)


# ── Create-or-equal ───────────────────────────────────────────────────────────


def test_a_first_write_creates_the_sidecar(tmp_path: Path) -> None:
    registry = PipelineRegistry(tmp_path)
    assert registry.read_request(_ADDRESS) is None

    registry.write_request(_ADDRESS, _request(label="first"))

    stored = registry.read_request(_ADDRESS)
    assert stored == _request(label="first")


def test_an_identical_rewrite_touches_nothing(tmp_path: Path) -> None:
    """A no-op means the file is not opened for writing, not merely rewritten alike.

    Asserted on mtime as well as on bytes, because "writes nothing" is the claim
    ``freeze``'s second invocation makes about itself: a rewrite producing equal
    bytes would satisfy an equality check while still touching a durable artifact.
    """
    registry = PipelineRegistry(tmp_path)
    path = registry.write_request(_ADDRESS, _request())
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    returned = registry.write_request(_ADDRESS, _request())

    assert returned == path
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize(
    ("label", "seeds"),
    [
        ("a different label", None),
        ("a corpus", [{"doi": "10.1/x"}]),
        ("a corpus", [{"arxiv_id": "2401.00001"}, {"doi": "10.1/y"}]),
    ],
    ids=["label", "fewer-seeds", "respelled-seed"],
)
def test_a_different_request_for_one_address_raises(
    tmp_path: Path, label: str, seeds: list[dict] | None
) -> None:
    """The collision the address cannot see, made visible.

    None of these move the content address — label is not in it at all, and a
    seed respelled to the same work resolves identically — which is exactly why
    the sidecar has to refuse rather than overwrite. All three shapes are covered
    because "different" must mean different in ANY field, not only in the one a
    relabelling changes.
    """
    registry = PipelineRegistry(tmp_path)
    registry.write_request(_ADDRESS, _request())

    with pytest.raises(RequestSidecarConflict) as excinfo:
        registry.write_request(_ADDRESS, _request(label=label, seeds=seeds))

    message = str(excinfo.value)
    assert _ADDRESS in message, "the conflict must name the address it is about"
    assert "a corpus" in message, "the STORED payload must be in the message"
    assert label in message, "the INCOMING payload must be in the message"
    # The stored request is the one that survives — refusing means refusing.
    assert registry.read_request(_ADDRESS) == _request()


def test_two_addresses_hold_their_own_requests(tmp_path: Path) -> None:
    """The policy is per address, not per root: a second record is not a conflict."""
    registry = PipelineRegistry(tmp_path)
    registry.write_request(_ADDRESS, _request(label="first"))
    registry.write_request(_OTHER_ADDRESS, _request(label="second"))

    assert registry.read_request(_ADDRESS).label == "first"
    assert registry.read_request(_OTHER_ADDRESS).label == "second"


# ── Reading ───────────────────────────────────────────────────────────────────


def test_an_absent_request_reads_as_none_not_as_empty(tmp_path: Path) -> None:
    """ABSENT IS NOT EMPTY. A record that never recorded its request has nothing
    to say about it, and returning a request with no seeds would make "unstated"
    indistinguishable from "asked for nothing" at every call site downstream."""
    assert PipelineRegistry(tmp_path).read_request(_ADDRESS) is None


def test_an_unparseable_request_raises_rather_than_reading_as_absent(
    tmp_path: Path,
) -> None:
    """A file that is there and says something unreadable is a different fact
    from a file that is not there, and must not be laundered into one."""
    request_sidecar_path_for(tmp_path, _ADDRESS).write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(ValueError):
        PipelineRegistry(tmp_path).read_request(_ADDRESS)


# ── Sidecars are not records ──────────────────────────────────────────────────


def test_is_record_excludes_the_request_suffix(tmp_path: Path) -> None:
    assert is_record(tmp_path / f"{_ADDRESS}.json")
    assert not is_record(tmp_path / f"{_ADDRESS}{REQUEST_SIDECAR_SUFFIX}")
    assert not is_record(tmp_path / f"{_ADDRESS}.manifest.json")


def test_a_record_with_both_sidecars_is_still_one_record(tmp_path: Path) -> None:
    """The whole reason the exclusion is explicit: a record that acquired an
    account of itself must not read as a root holding three records, or every
    caller of ``sole_record_address`` breaks on a well-formed registry."""
    (tmp_path / f"{_ADDRESS}.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"{_ADDRESS}.manifest.json").write_text("{}", encoding="utf-8")
    PipelineRegistry(tmp_path).write_request(_ADDRESS, _request())

    assert sole_record_address(tmp_path) == _ADDRESS


# ── The packaged CRISPR request, as an independent second opinion ─────────────


@pytest.fixture(scope="module")
def packaged_request() -> RecordRequest:
    return PipelineRegistry(REGISTRY_ROOT).read_request(frozen_crispr_address())


def test_the_packaged_record_carries_a_request(packaged_request) -> None:
    """It ships in the wheel beside the record, so an installed idiograph can
    resolve what the exhibit was asked for with no checkout present."""
    assert packaged_request is not None, (
        "the packaged CRISPR record has no request sidecar — the served surface "
        "resolves a run's seeds from that file and has no literal to fall back on"
    )


def test_the_packaged_request_equals_the_authored_seed_literal(
    packaged_request,
) -> None:
    """What the committed file says equals what ``idiograph.demo`` authors.

    ``FROZEN_CRISPR_SEEDS`` stays the hand-written literal and this is the seam
    where it meets the file production reads. Deriving the expectation from the
    sidecar instead would agree with whatever bytes happen to be on disk, which
    is the same tautology ``test_freeze_trigger_address`` declines for the
    address.
    """
    assert packaged_request.seeds == FROZEN_CRISPR_SEEDS
    assert packaged_request.label == PACKAGED_CRISPR_LABEL


def test_the_packaged_request_is_in_the_canonical_form() -> None:
    """The committed bytes are what the writer would produce, not a hand edit.

    If they were not, a ``freeze`` re-run over this exhibit would read its own
    identical request as a CONFLICT and refuse — the create-or-equal check is
    byte-equality, so a stray space in a committed file is a latent failure.
    """
    path = request_sidecar_path_for(REGISTRY_ROOT, frozen_crispr_address())
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.read_text(encoding="utf-8") == canonical_sidecar_json(payload)


def test_the_packaged_registry_still_holds_exactly_one_record() -> None:
    """The sidecar did not make the packaged root ambiguous.

    ``frozen_crispr_address()`` globs that root and raises on anything but one
    record, so this is the standing check that adding a second sidecar beside the
    exhibit left the address derivation intact.
    """
    assert sole_record_address(REGISTRY_ROOT) == frozen_crispr_address()
