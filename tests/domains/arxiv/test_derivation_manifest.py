# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""The DERIVATION_MANIFEST descriptor: what it anchors, what it diffs, what it
must never touch (IDG-091 clause 2).

The manifest exists because the content address keys on INPUTS and says nothing
about the CODE that turned them into an artifact. These tests pin the properties
that make it a usable witness rather than a decorative one:

  DETERMINISM. Two computations against one tree are EQUAL. Without this the
  descriptor manufactures mismatches out of its own traversal order, and every
  ledger entry becomes noise an operator learns to ignore — the failure mode that
  kills an audit channel is not silence, it is false positives.

  THE ROWS ARE THE PRODUCT. The manifest is a descriptor carrying per-module
  rows, not a single digest. A bare hash says something moved; the rows say what
  moved, and the diff assertions below are written against rows for that reason.

  NULL IS A VALUE, NOT AN ABSENCE. An unresolvable `uv.lock` is recorded as a
  null digest and null-versus-value IS a diff row. Omitting it would make "no
  lock file here" and "the lock file is unchanged" indistinguishable.

  IT ENTERS NO ADDRESS. The manifest is an observation channel. If any part of it
  reached `PipelineParameters` it would re-key every stored record, including the
  frozen demo artifact, and convert an audit trail into a recompute bill.

The diff tests build their manifests BY HAND rather than by calling the deriver
twice. A test that derived both sides from the subject would agree with whatever
the subject does — including doing nothing — so the expected rows are written out
independently, the way `test_traversal_contract_binding.py` transcribes a formula
rather than calling the function it is measuring.
"""

import json

from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv.derivation_manifest import (
    DERIVATION_MANIFEST_VERSION,
    FIRST_PARTY_ROOT,
    UV_LOCK_ANCHOR,
    DerivationManifest,
    ModuleAnchor,
    derive_manifest,
    diff_hash,
    manifest_diff,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)
from idiograph.domains.arxiv.models import (
    BackwardParameters,
    CoCitationParameters,
    ForwardParameters,
    PipelineParameters,
)
from idiograph.domains.arxiv.registry import (
    MANIFEST_SIDECAR_SUFFIX,
    sole_record_address,
)

_SEEDS = [{"arxiv_id": "2101.00001"}]


def _parameters() -> PipelineParameters:
    """Minimal valid parameters. ``current_year`` is stated, never clock-read."""
    return PipelineParameters(
        backward=BackwardParameters(n_backward=10, lambda_decay=0.1),
        forward=ForwardParameters(
            n_forward=10,
            lambda_decay=0.1,
            alpha=1.0,
            beta=1.0,
            sort="cited_by_count:desc",
        ),
        current_year=2026,
        co_citation=CoCitationParameters(min_strength=1, max_edges=None),
    )


def _anchor(module: str, sha256: str | None = "a" * 64) -> ModuleAnchor:
    return ModuleAnchor(
        module=module, path=f"{module.replace('.', '/')}.py", sha256=sha256
    )


def _manifest(*anchors: ModuleAnchor, **overrides) -> DerivationManifest:
    """A hand-built manifest. Fields not named take deliberately fixed values, so
    a diff assertion below turns on the rows and on nothing incidental."""
    return DerivationManifest(
        v=DERIVATION_MANIFEST_VERSION,
        baseline_commit=overrides.get("baseline_commit", "0" * 40),
        uv_lock_sha256=overrides.get("uv_lock_sha256", "b" * 64),
        modules=list(anchors),
    )


# ── Determinism ──────────────────────────────────────────────────────────────


def test_two_computations_over_one_tree_are_equal() -> None:
    """Pins DETERMINISM: the deriver is a function of the tree, of nothing else.

    Both halves are computed against the same unchanged checkout, so any
    inequality comes from the deriver itself — set iteration leaking into row
    order, a clock or a random value reaching a field, a path resolved against
    the working directory.

    WHEN THIS TEST FAILS, READ THIS. Every HIT would then diff against its own
    baseline and the ledger would fill with entries describing no real change.
    An audit channel that cries wolf is worse than none, because the operator
    stops reading it and the real drift arrives unread.
    """
    first = derive_manifest(_SEEDS, _parameters())
    second = derive_manifest(_SEEDS, _parameters())
    assert first == second, (
        "two derivations over one unchanged tree disagreed. The manifest must be "
        "a function of the tree alone — check row ordering, and check that no "
        "clock, RNG or working-directory-relative path has entered a field."
    )


def test_the_manifest_does_not_depend_on_the_seeds() -> None:
    """The descriptor anchors CODE, so a different request shape is the same tree.

    ``seeds`` ride as Node 0 configuration and select no handler; the manifest
    reads node TYPES. If this ever fails, one baseline sidecar could no longer
    serve every request that hits one record — a per-request-shape baseline is
    not a thing the HIT gate can store or compare.
    """
    by_arxiv = derive_manifest([{"arxiv_id": "2101.00001"}], _parameters())
    by_doi = derive_manifest([{"doi": "10.1126/science.1225829"}], _parameters())
    assert by_arxiv == by_doi


def test_rows_anchor_first_party_modules_including_the_handler_modules() -> None:
    """Pins WHAT is anchored: the handler modules plus their first-party closure.

    Two claims at once. Every anchored module with a hash is first-party — the
    closure stops at the stdlib and third-party boundary, whose bytes are not
    this repository's to witness and whose drift `uv.lock` already carries. And
    the two modules that actually implement this pipeline's handlers are present,
    so the closure is not merely first-party but non-vacuous.
    """
    manifest = derive_manifest(_SEEDS, _parameters())
    anchored = {anchor.module for anchor in manifest.modules}

    for handler_module in [
        "idiograph.domains.arxiv.pipeline",
        "idiograph.domains.arxiv.relationship_annotation",
    ]:
        assert handler_module in anchored, (
            f"{handler_module} implements node handlers this pipeline declares "
            f"but is not anchored. The manifest found {sorted(anchored)}. A "
            f"handler module that anchors nothing can be rewritten without "
            f"moving a single row, which is precisely the blind spot this "
            f"descriptor exists to close."
        )

    for anchor in manifest.modules:
        if anchor.sha256 is None:
            continue
        assert anchor.module.startswith(f"{FIRST_PARTY_ROOT}."), (
            f"{anchor.module!r} is anchored with a hash but is not first-party. "
            f"Hashing a module outside this distribution makes every dependency "
            f"release read as derivation drift; the resolved third-party set "
            f"rides as the single {UV_LOCK_ANCHOR} row instead."
        )
        assert anchor.path is not None and anchor.path.endswith(".py")


def test_the_manifest_reaches_no_content_address_input() -> None:
    """Pins IT ENTERS NO ADDRESS: no manifest or ledger field on the address model.

    `content_address` hashes `PipelineParameters` whole, so any field landing
    there enters the address by construction. This is the tripwire against that:
    a manifest digest or a ledger path on this model would re-key every stored
    record — the frozen CRISPR artifact included — and turn an observation
    channel into a recompute trigger for the entire registry.

    WHEN THIS TEST FAILS, READ THIS. Do not update this test. A new
    `PipelineParameters` field is an address move and an operator-gated action;
    if the new field genuinely belongs in the address, it is not part of the
    mismatch apparatus and must not be named for it.
    """
    forbidden = [
        name
        for name in PipelineParameters.model_fields
        if "manifest" in name or "ledger" in name
    ]
    assert not forbidden, (
        f"PipelineParameters has acquired {forbidden}. The derivation manifest "
        f"and the mismatch ledger are the OBSERVATION channel: they witness what "
        f"code derived an artifact and must never help key it. A field here "
        f"enters content_address, re-addresses every stored record, and makes "
        f"the cache miss on artifacts it already holds."
    )


# ── The diff ─────────────────────────────────────────────────────────────────


def test_identical_manifests_diff_to_nothing() -> None:
    """Agreement is the empty list — the no-op the HIT gate leans on.

    The gate writes a ledger entry only on a non-empty diff, so a diff that
    reported spurious rows for equal manifests would append an entry on every
    single HIT.
    """
    manifest = _manifest(_anchor("idiograph.core.models"))
    assert manifest_diff(manifest, manifest) == []


def test_a_changed_module_produces_exactly_that_row() -> None:
    """Pins THE ROWS ARE THE PRODUCT: a moved module names itself, old and new.

    Two modules, one of them moved. The unchanged one must not appear — a diff
    that reported every row would bury the one fact the operator needs under the
    rest of the closure, which is the descriptor's purpose inverted.
    """
    unchanged = _anchor("idiograph.core.models", "1" * 64)
    baseline = _manifest(unchanged, _anchor("idiograph.domains.arxiv.pipeline", "2" * 64))
    live = _manifest(unchanged, _anchor("idiograph.domains.arxiv.pipeline", "3" * 64))

    assert manifest_diff(baseline, live) == [
        {
            "anchor": "idiograph.domains.arxiv.pipeline",
            "path": "idiograph/domains/arxiv/pipeline.py",
            "baseline": "2" * 64,
            "live": "3" * 64,
        }
    ]


def test_a_module_present_on_one_side_only_is_a_row() -> None:
    """A module entering or leaving the closure is drift, reported as a null side.

    Both directions, because they are different events: a module the baseline
    knew and the live tree does not means derivation code was deleted or is no
    longer reached, while the reverse means new code entered the derivation path.
    Either is a change to what derives an artifact.
    """
    shared = _anchor("idiograph.core.models", "1" * 64)
    departed = _anchor("idiograph.core.query", "2" * 64)
    arrived = _anchor("idiograph.core.executor", "3" * 64)

    assert manifest_diff(_manifest(shared, departed), _manifest(shared)) == [
        {
            "anchor": "idiograph.core.query",
            "path": "idiograph/core/query.py",
            "baseline": "2" * 64,
            "live": None,
        }
    ]
    assert manifest_diff(_manifest(shared), _manifest(shared, arrived)) == [
        {
            "anchor": "idiograph.core.executor",
            "path": "idiograph/core/executor.py",
            "baseline": None,
            "live": "3" * 64,
        }
    ]


def test_a_null_lock_digest_against_a_value_is_a_diff_row() -> None:
    """Pins NULL IS A VALUE: an absent `uv.lock` diffs against a present one.

    The installed-wheel case. Recording the absence as null — and diffing it —
    is what keeps "there is no lock file here" distinguishable from "the lock
    file has not moved". Silence would make the two identical, and the second is
    the one an operator would wrongly assume.
    """
    shared = _anchor("idiograph.core.models", "1" * 64)
    with_lock = _manifest(shared, uv_lock_sha256="c" * 64)
    without_lock = _manifest(shared, uv_lock_sha256=None)

    assert manifest_diff(with_lock, without_lock) == [
        {
            "anchor": UV_LOCK_ANCHOR,
            "path": UV_LOCK_ANCHOR,
            "baseline": "c" * 64,
            "live": None,
        }
    ]
    assert manifest_diff(without_lock, with_lock) == [
        {
            "anchor": UV_LOCK_ANCHOR,
            "path": UV_LOCK_ANCHOR,
            "baseline": None,
            "live": "c" * 64,
        }
    ]


def test_baseline_commit_alone_is_not_a_mismatch() -> None:
    """Pins BASELINE_COMMIT IS PROVENANCE: it is not compared and not diffed.

    Identical rows taken at two different commits describe the SAME derivation
    code — a merge that did not touch the closure, a rebase, a shallow clone with
    a different HEAD. Treating the commit as load-bearing would append a ledger
    entry on every unrelated commit, which is drift reported where none exists.
    """
    rows = [_anchor("idiograph.core.models", "1" * 64)]
    baseline = _manifest(*rows, baseline_commit="a" * 40)
    live = _manifest(*rows, baseline_commit="f" * 40)
    assert manifest_diff(baseline, live) == []
    assert baseline.baseline_commit != live.baseline_commit


# ── The dedupe key ───────────────────────────────────────────────────────────


def test_the_diff_hash_identifies_a_drift_state_not_an_observation() -> None:
    """Pins the dedupe key: equal diffs hash equal, different diffs hash apart.

    This is what makes "one entry per drift state" work at the HIT gate. Were the
    hash unstable across equal diffs, every HIT would append; were it equal
    across different diffs, a tree that drifted FURTHER would be silently
    swallowed by the earlier entry — the worse of the two failures, because the
    ledger would then read as complete while missing the newest state.
    """
    shared = _anchor("idiograph.core.models", "1" * 64)
    first = manifest_diff(
        _manifest(shared, _anchor("idiograph.core.query", "2" * 64)),
        _manifest(shared, _anchor("idiograph.core.query", "3" * 64)),
    )
    same = manifest_diff(
        _manifest(shared, _anchor("idiograph.core.query", "2" * 64)),
        _manifest(shared, _anchor("idiograph.core.query", "3" * 64)),
    )
    further = manifest_diff(
        _manifest(shared, _anchor("idiograph.core.query", "2" * 64)),
        _manifest(shared, _anchor("idiograph.core.query", "4" * 64)),
    )

    assert diff_hash(first) == diff_hash(same)
    assert diff_hash(first) != diff_hash(further)


# ── Sidecars ─────────────────────────────────────────────────────────────────


def test_a_sidecar_round_trips_and_an_absent_one_reads_as_none(tmp_path) -> None:
    """Written then read is the same manifest; a missing file is ``None``.

    The ``None`` half is the load-bearing one: the HIT gate treats it as "no
    baseline, serve exactly as before", so it must be distinguishable from an
    empty manifest, which would diff against every row in the tree and append a
    ledger entry for a record that never had a baseline at all.
    """
    manifest = derive_manifest(_SEEDS, _parameters())
    path = sidecar_path_for(tmp_path, "f" * 64)

    assert read_sidecar(path) is None
    assert write_sidecar(path, manifest) == path
    assert read_sidecar(path) == manifest


def test_the_committed_sidecar_does_not_count_as_a_record() -> None:
    """Pins the packaged-registry invariant: one record, sidecar present.

    `sole_record_address` is what `idiograph.demo.frozen_crispr_address` and the
    viewer generator both derive the frozen address from, and it raises unless
    exactly one record is found. The baseline manifest now sits in that same
    directory, so this is the seam where an observation channel could break the
    thing it observes: a sidecar counted as a record makes the packaged registry
    read as holding two, and every caller of that function starts raising.

    WHEN THIS TEST FAILS, READ THIS. The record-enumeration predicate
    (`registry.is_record`) has stopped excluding sidecars, or a sidecar was
    committed under a name it does not recognize. The viewer, the HIT-leg demo's
    root selection and the freeze-address guard all go down with it.
    """
    address = frozen_crispr_address()
    sidecar = sidecar_path_for(REGISTRY_ROOT, address)

    assert sidecar.is_file(), (
        f"the committed baseline manifest is missing from {REGISTRY_ROOT}. The "
        f"frozen record's derivation baseline ships beside it; without the file "
        f"the HIT gate has nothing to compare against and silently observes "
        f"nothing."
    )
    assert sidecar.name.endswith(MANIFEST_SIDECAR_SUFFIX)
    assert sole_record_address(REGISTRY_ROOT) == address, (
        "sole_record_address no longer returns the one record's address with the "
        "sidecar present. A sidecar DESCRIBES a record and is never one; "
        "registry.is_record is what states that, and everything deriving the "
        "frozen address depends on it holding."
    )


def test_the_committed_sidecar_parses_as_a_manifest() -> None:
    """The packaged baseline is a manifest this code can still read.

    A sidecar that exists but does not parse is worse than an absent one: the HIT
    gate degrades to serving with a warning, so the drift it was committed to
    witness would go unrecorded while everything looked healthy.
    """
    address = frozen_crispr_address()
    manifest = read_sidecar(sidecar_path_for(REGISTRY_ROOT, address))

    assert manifest is not None
    assert manifest.v == DERIVATION_MANIFEST_VERSION
    assert manifest.modules, "the committed baseline anchors no modules at all"
    # It is a BASELINE, not a claim about the freeze: it describes the tree it was
    # generated at, and is expected to diverge from the live tree over time. So
    # nothing here asserts it equals a fresh derivation — such a test would go red
    # on the first honest edit to any anchored module, which is the event the
    # ledger exists to RECORD rather than to forbid.
    payload = json.loads(
        sidecar_path_for(REGISTRY_ROOT, address).read_text(encoding="utf-8")
    )
    assert sorted(payload) == ["baseline_commit", "modules", "uv_lock_sha256", "v"]
