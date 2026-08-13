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

THE RESOLVERS ARE TESTED DIRECTLY, and the last sections of this file descend to
them: the small pure functions deciding what a module name resolves to, what a
relative import means, and what happens at each boundary the closure stops at.
They are reached directly because their failure mode is SILENCE. Every one of
them fails closed — into a `None`, an empty set, a skipped row — so a resolver
that quietly stopped resolving would subtract anchors from the manifest while
leaving it a well-formed manifest that parses, diffs, and reports agreement. The
determinism and first-party tests above would all stay green over a descriptor
that had gone half blind, because they assert over the rows that ARE there.

WHERE THESE TESTS MOCK, AND WHY — the discipline
`test_derivation_mismatch_ledger.py` states, restated here because these tests
reach further in. Exactly two things are patched: `subprocess.run`, for the
`_git_head` failures no fixture can induce (there is no way to un-install git),
and `sys.modules` membership, for the `find_spec` fallback that is unreachable
while a module is already imported. Nothing installs a handler into `HANDLERS`.
A mock handler left in that registry would anchor a later manifest to
`unittest.mock`, so the handler tests below build a `Graph` naming a type that
was never registered rather than registering a fake one.
"""

import ast
import json
import logging
import subprocess
import sys

import pytest

from idiograph.core.models import Graph, Node
from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address
from idiograph.domains.arxiv import derivation_manifest
from idiograph.domains.arxiv.derivation_manifest import (
    DERIVATION_MANIFEST_VERSION,
    FIRST_PARTY_ROOT,
    UV_LOCK_ANCHOR,
    DerivationManifest,
    ModuleAnchor,
    _git_head,
    _imported_names,
    _module_file,
    _package_of,
    _relative_path,
    _resolve_relative,
    derive_manifest,
    diff_hash,
    handler_modules,
    manifest_diff,
    module_anchors,
    package_parent,
    project_root,
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


# ── Tree location ────────────────────────────────────────────────────────────


def test_a_lone_lock_file_does_not_make_a_project_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins BOTH FILES, NOT EITHER — and ``None`` when neither is found.

    A stray `uv.lock` above an installed wheel belongs to whatever project owns
    that directory. Accepting it would digest a STRANGER's dependency set into
    this manifest, and every release of that unrelated project would then read as
    derivation drift in this one — a diff row pointing at a file the operator
    cannot connect to anything they did.

    The `None` half is the ordinary installed-wheel answer and not an error: it
    yields a null lock digest and a null baseline commit, both values the
    manifest carries deliberately. `package_parent` is redirected at a tmp
    directory because the real one sits inside this checkout, where a root always
    exists — the no-root case cannot be reached from here any other way.
    """
    monkeypatch.setattr(derivation_manifest, "package_parent", lambda: tmp_path)

    assert project_root() is None, (
        f"a project root was found above {tmp_path}, which holds neither "
        f"uv.lock nor pyproject.toml. No root is the installed-wheel answer and "
        f"must stay reachable; inventing one digests a directory nobody named."
    )

    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    assert project_root() is None, (
        "a lone uv.lock was accepted as a project root. BOTH files are required: "
        "a lock file without a pyproject.toml beside it belongs to some other "
        "project, and digesting it reports drift in a stranger's dependencies."
    )

    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert project_root() == tmp_path


def test_whatever_the_project_root_is_it_holds_both_project_files() -> None:
    """The un-redirected control: the real answer satisfies its own definition.

    Stated as an invariant rather than as a fixed path so it holds in a source
    checkout (where a root exists) and in an installed wheel (where it does not)
    alike — the same reason `ModuleAnchor.path` is relative to the package parent.
    """
    root = project_root()
    assert root is None or (
        (root / "uv.lock").is_file() and (root / "pyproject.toml").is_file()
    )


# ── Provenance ───────────────────────────────────────────────────────────────


def test_a_tree_git_cannot_answer_for_carries_a_null_provenance_stamp(
    tmp_path,
) -> None:
    """Pins BASELINE_COMMIT IS PROVENANCE on its failure side: no root, no stamp.

    Two of the ways git declines to answer, both un-mocked: there is no checkout
    to ask about, and there is a directory that is not one. The stamp is metadata
    the diff never reads, so neither may become an error — a manifest that
    refused to be computed off a checkout could not be derived from a wheel at
    all, and the HIT gate would degrade on every record in an installed
    deployment.
    """
    assert _git_head(None) is None, (
        "no checkout produced something other than None. There is nothing to ask "
        "git about, and the stamp is optional metadata: the answer is a null."
    )
    assert _git_head(tmp_path) is None, (
        f"{tmp_path} is not a git checkout, so `git rev-parse HEAD` exits "
        f"non-zero. A non-zero exit is an ABSENT stamp, never a raise — the "
        f"observation it decorates does not depend on it."
    )


def test_no_git_failure_escapes_the_provenance_stamp(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the fence around the stamp: every failure mode collapses to ``None``.

    A missing git binary raises `OSError`; an invocation that outlives its
    timeout raises `TimeoutExpired`. Both are caught, because the alternative is
    that an optional provenance field takes down a cache serve — the manifest is
    derived on the HIT path, and an exception here propagates into a read that
    has nothing to do with git.

    MOCKING NOTE. `subprocess.run` is patched here and nowhere else in this file.
    Neither failure can be produced by a fixture — a test cannot un-install git
    or hang it — and the whole claim of the function is that neither escapes, so
    the failure has to be injected to be observed at all.
    """

    def no_git_binary(*args, **kwargs):
        raise OSError("no git binary on PATH")

    def hung_invocation(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git", "rev-parse", "HEAD"], timeout=1)

    monkeypatch.setattr(subprocess, "run", no_git_binary)
    assert _git_head(tmp_path) is None, (
        "an OSError from `git` escaped the provenance stamp. A machine without "
        "git installed must still derive a manifest; the commit is a pointer to "
        "where the rows came from, and the rows do not need it."
    )

    monkeypatch.setattr(subprocess, "run", hung_invocation)
    assert _git_head(tmp_path) is None, (
        "a timed-out `git` escaped the provenance stamp. The timeout exists so a "
        "hung invocation is answered with None rather than by holding up the "
        "observation — catching it is the other half of that decision."
    )


# ── Module resolution ────────────────────────────────────────────────────────


def test_a_module_with_no_python_source_anchors_no_file() -> None:
    """Modules with nothing to hash resolve to ``None`` instead of to a guess.

    Builtins, extension modules and namespace packages have no single `.py` file
    to anchor, and a name the package does not contain resolves to nothing at
    all. Both must answer `None` rather than raise: `module_anchors` turns a
    `None` here into a null ROW, which is how the manifest witnesses "the graph
    binds this to something outside the bytes I can see" without pretending to
    know what.
    """
    assert _module_file("sys") is None, (
        "a builtin resolved to a file. `sys` has no __file__ and its spec origin "
        "is 'built-in', not a source path — hashing whatever came back would "
        "anchor a row to bytes this tree does not own."
    )
    assert _module_file(f"{FIRST_PARTY_ROOT}.no_such_module") is None, (
        "a module that does not exist resolved to a file. find_spec answers None "
        "for a name its parent package does not contain, and None is a null row, "
        "not an error."
    )


def test_a_module_is_resolved_without_being_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the ``find_spec`` fallback, and that it stays a fallback.

    The closure walks modules the RUN never imported, so resolution must not
    import them: importing `pipeline` alone executes a `load_dotenv()`, and an
    observation channel that runs a module's side effects to describe it has
    stopped observing. `find_spec` resolves a name without executing it.

    MOCKING NOTE. `sys.modules` membership is the one thing patched here. The
    fallback runs only for a module not already imported, and by the time this
    file's tests run the whole arxiv package is loaded — so the entry is removed
    for the duration to reach the branch at all. `monkeypatch` restores it.
    """
    name = f"{FIRST_PARTY_ROOT}.domains.arxiv.cache"
    monkeypatch.delitem(sys.modules, name, raising=False)

    resolved = _module_file(name)

    assert resolved is not None and resolved.name == "cache.py", (
        f"a module absent from sys.modules did not resolve through find_spec; "
        f"got {resolved}. Every module in the closure that the run did not "
        f"happen to import would then anchor nothing."
    )
    assert name not in sys.modules, (
        "resolving a module IMPORTED it. The closure must be computable without "
        "executing what it walks — module-level side effects (load_dotenv, "
        "registry mutation) would otherwise fire from inside a cache read."
    )


def test_a_module_outside_the_package_directory_has_no_stated_path(tmp_path) -> None:
    """A path that cannot be stated relative to the package parent is null.

    `ModuleAnchor.path` is relative so a manifest generated in a source checkout
    and compared in an installed wheel diffs on CONTENT rather than on where the
    tree sits. A path outside that directory has no such form; the row keeps its
    HASH — the load-bearing half — and nulls only the cosmetic field, rather than
    dropping an anchor and silently shrinking the closure.
    """
    inside = package_parent() / FIRST_PARTY_ROOT / "core" / "models.py"
    assert _relative_path(inside) == f"{FIRST_PARTY_ROOT}/core/models.py"

    outside = tmp_path / "stranger.py"
    outside.write_text("", encoding="utf-8")
    assert _relative_path(outside) is None, (
        f"{outside} is not under {package_parent()} and cannot be stated "
        f"relative to it. The answer is a null path, not a raise and not an "
        f"absolute path — an absolute one would diff on the checkout location "
        f"and report drift for every machine the manifest is read on."
    )


# ── The import closure ───────────────────────────────────────────────────────


def test_source_that_cannot_be_read_or_parsed_contributes_no_imports(
    tmp_path,
) -> None:
    """An unreadable module subtracts itself from the closure, not the manifest.

    The closure is computed by AST over files on disk, and a file can be
    unparseable (a syntax error mid-edit) or absent (a stale `.pyc`, a path that
    moved). Answering with the empty set costs the imports of ONE module; raising
    would cost the whole manifest, and the fence at the HIT gate would turn that
    into every record on this tree serving with a warning and no observation.
    """
    broken = tmp_path / "broken.py"
    broken.write_text("import idiograph\ndef (\n", encoding="utf-8")
    assert _imported_names(broken, FIRST_PARTY_ROOT) == set(), (
        "a file that does not parse yielded something other than an empty set. "
        "A SyntaxError inside one module must not propagate out of the closure "
        "walk — it would take the entire manifest down with it."
    )

    assert _imported_names(tmp_path / "absent.py", FIRST_PARTY_ROOT) == set(), (
        "a file that does not exist yielded something other than an empty set. "
        "The OSError is answered the same way, and for the same reason."
    )


def test_a_relative_import_reaching_past_the_package_root_is_skipped(
    tmp_path,
) -> None:
    """A malformed relative import is dropped; the rest of the file still counts.

    The skip is what keeps one bad import statement from costing a module its
    OTHER imports. The control in the same body is the point: an absolute import
    beside the malformed one must still land, and a `from X import y` must still
    offer both `X` and `X.y` — only `_module_file` can tell which of the two is a
    module, so both are offered and the non-module one resolves to nothing.
    """
    source = tmp_path / "module.py"
    source.write_text(
        "import idiograph.core.models\nfrom ... import runaway\n", encoding="utf-8"
    )

    assert _imported_names(source, FIRST_PARTY_ROOT) == {"idiograph.core.models"}, (
        "a relative import walking past the root of the package either raised or "
        "contributed a name. It resolves to nothing and is skipped, and the "
        "absolute import beside it must survive that skip."
    )

    both = tmp_path / "both.py"
    both.write_text("from idiograph.core import models\n", encoding="utf-8")
    assert _imported_names(both, FIRST_PARTY_ROOT) == {
        "idiograph.core",
        "idiograph.core.models",
    }


def _import_from(source: str) -> ast.ImportFrom:
    """The single ``ImportFrom`` statement in ``source``, as the walker sees it.

    Parsed rather than hand-constructed so ``level`` and ``module`` carry exactly
    what Python's own parser reads out of the dots — a hand-built node could
    encode a level the language never produces.
    """
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.ImportFrom)
    return node


def test_the_relative_import_resolver_walks_one_package_per_leading_dot() -> None:
    """Pins the resolver the closure's reach depends on, level by level.

    Off-by-one here is invisible and expensive. A resolver one level too shallow
    anchors a module that does not exist — the name resolves to nothing, the row
    never appears, and the real module goes unwitnessed while the manifest still
    looks complete. One level too deep does the same thing from the other side.
    Nothing else in this file would catch either: the closure is walked from
    absolute imports today, so this resolver's output is compared against no
    expectation at all until a first-party module acquires a relative import.

    The malformed case closes it out. A level that walks past the root yields the
    empty string, which is the caller's signal to skip rather than a name to
    resolve.
    """
    package = f"{FIRST_PARTY_ROOT}.domains.arxiv"

    # Level 0 is already absolute — the package is not consulted at all.
    assert (
        _resolve_relative(_import_from("from idiograph.core import models"), package)
        == "idiograph.core"
    )

    # One dot IS the package; two its parent; three its grandparent.
    assert _resolve_relative(_import_from("from . import cache"), package) == package
    assert (
        _resolve_relative(_import_from("from .cache import x"), package)
        == "idiograph.domains.arxiv.cache"
    )
    assert (
        _resolve_relative(_import_from("from ..models import y"), package)
        == "idiograph.domains.models"
    )
    assert (
        _resolve_relative(_import_from("from ... import z"), package)
        == FIRST_PARTY_ROOT
    )

    # Past the root, and from no package at all: malformed, and skipped.
    assert _resolve_relative(_import_from("from .... import w"), package) == "", (
        "a relative import four levels up from a three-package root resolved to "
        "a name. It refers to nothing; the empty string is what the caller skips "
        "on, and any name here would be fabricated."
    )
    assert _resolve_relative(_import_from("from . import w"), "") == ""


def test_a_package_init_is_its_own_package() -> None:
    """What relative imports resolve AGAINST, decided by file name.

    An `__init__.py` IS its package, while every other module's package is its
    parent — get this wrong for `__init__.py` and every relative import in every
    package initializer resolves one level too high, silently pulling the wrong
    modules into the closure or none at all.

    Read off the file name rather than out of `sys.modules`, so the answer is the
    same whether or not the module has been imported — the same discipline that
    lets the closure walk modules the run never loaded.
    """
    arxiv = package_parent() / FIRST_PARTY_ROOT / "domains" / "arxiv"
    package = f"{FIRST_PARTY_ROOT}.domains.arxiv"

    assert _package_of(package, arxiv / "__init__.py") == package, (
        "a package's __init__.py was assigned its PARENT as its package. Every "
        "relative import in an initializer would then resolve one level too "
        "high."
    )
    assert _package_of(f"{package}.cache", arxiv / "cache.py") == package


# ── Handler resolution ───────────────────────────────────────────────────────


def test_a_node_type_no_handler_implements_anchors_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pins THE MANIFEST IS NOT A VALIDATOR: an unregistered type warns and skips.

    `validate_integrity` and the executor already fail on a type nothing
    implements, and they are the right places for it. Failing HERE would fail on
    the HIT path — a cache serve broken by a defect the serve does not depend on,
    over an artifact already loaded and already correct.

    Stated as an equality against the same graph WITHOUT the unregistered node,
    so it pins "anchors nothing" exactly: not a null row, not an
    `<unresolved-handler-module>` row, nothing. That anchor is for a handler that
    EXISTS and cannot be located; a type with no handler at all is a different
    fact and gets no row.

    No handler is registered to reach this. Installing a fake into `HANDLERS`
    would anchor whatever manifest a later test derives to `unittest.mock`.
    """
    registered = Node(id="0", type="ResolveSeeds")
    unregistered = Node(id="1", type="NoSuchNodeTypeExists")

    with caplog.at_level(
        logging.WARNING, logger="idiograph.arxiv.derivation_manifest"
    ):
        with_unknown = handler_modules(
            Graph(name="probe", version="1", nodes=[registered, unregistered])
        )

    assert with_unknown == handler_modules(
        Graph(name="probe", version="1", nodes=[registered])
    ), (
        f"a node type nothing implements changed what the manifest anchors: "
        f"{with_unknown}. It resolves to no handler and so to no module, and the "
        f"registered node beside it must still anchor exactly what it did alone."
    )

    assert any(
        "NoSuchNodeTypeExists" in record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING"
    ), (
        "the skipped node type was not named in a warning. Skipping silently "
        "makes a graph the executor would reject look, to anyone reading the "
        "manifest, like a graph whose every type is implemented."
    )


def test_a_seed_module_outside_this_tree_is_witnessed_with_a_null_row() -> None:
    """Pins FIRST-PARTY MEANS THIS TREE, on the row that crosses the boundary.

    A handler supplied from outside the `idiograph` package still gets a ROW,
    with a null hash — the graph binds that node type to SOMETHING, and the
    manifest says so without claiming to know its bytes. Dropping the row instead
    would make a handler MOVING across that boundary — production code replaced
    by a test double, or by an implementation from another distribution — read as
    silence, which is the single change this descriptor most needs to report.

    Both null shapes are the same row: a module whose file exists but is not this
    tree's to hash, and one with no source file at all.
    """
    assert module_anchors(["json"]) == [ModuleAnchor(module="json")], (
        "a seed module outside the idiograph package was not witnessed as a null "
        "row. It has a file, but not one this repository owns: hashing it makes "
        "every stdlib release read as derivation drift, and dropping it makes a "
        "handler leaving this tree read as no change at all."
    )
    assert module_anchors(["sys"]) == [ModuleAnchor(module="sys")]

    for row in module_anchors(["json"]):
        assert row.path is None and row.sha256 is None
