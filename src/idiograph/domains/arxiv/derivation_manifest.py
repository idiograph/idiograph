# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""DERIVATION_MANIFEST — what CODE a stored artifact was derived by (IDG-091 clause 2).

The content address answers "what INPUTS produced this?" — resolved seeds plus
``PipelineParameters``, and nothing else. It deliberately says nothing about the
code that turned those inputs into an artifact. So a handler can be rewritten,
a scoring line edited, a dependency bumped, and every stored record keeps
addressing exactly as before: the cache keeps hitting, and the artifact it
serves was derived by code that no longer exists in the tree.

This module is the descriptor that witnesses that span. Given the DECLARED graph
(``pipeline_graph.build_pipeline_graph``) it resolves each node's ``type``
through the ``HANDLERS`` registry to the module that implements it, takes the
transitive closure of those modules' FIRST-PARTY imports, and anchors each one
by the sha256 of its bytes. A digest of ``uv.lock`` rides alongside for the
resolved dependency set.

A DESCRIPTOR, NOT A DIGEST. The manifest carries ROWS — one per module, each
with its dotted name, its path and its hash — and is deliberately not collapsed
to a single hash of the whole. A bare hash tells an operator that something
moved; it cannot tell them WHAT moved, and "something in your derivation code
changed" is not an observation anyone can act on. The rows are what make the
mismatch diff informative, so the rows are what get stored.

NOT IN THE CONTENT ADDRESS. The manifest is an OBSERVATION channel and enters no
address: ``content_address`` is untouched by this module, no field of it reaches
``PipelineParameters``, and adding a row here can never re-key a stored record.
That separation is the point — a descriptor that moved the address would force a
recompute of every artifact whenever the code that derives it changed, which is
the opposite of what IDG-091 clause 3 rules (serve, and record).

WHAT IT WITNESSES, AND WHAT IT DOES NOT. It witnesses CHANGE to first-party
derivation code and to the lock file: a module's bytes moved, a module entered
or left the closure, the lock resolved differently. It does NOT witness MEANING.
It cannot say whether a change altered derived output, whether the change was a
comment reflow or a rewritten score formula, or whether prose in a contract
constant still describes the code beneath it. A diff row is an invitation to
look, not a verdict.

FIRST-PARTY MEANS THIS TREE. The closure follows imports resolvable under the
``idiograph`` package and stops at every stdlib and third-party boundary: their
bytes are not this repository's to witness, they move on their own release
schedule, and ``uv.lock`` already carries the resolved third-party set as one
row. A handler whose module is NOT first-party — a test double, or an
implementation supplied by another distribution — still gets a row, with a NULL
hash: the manifest witnesses that the graph binds that type to something outside
this tree, without claiming to know its bytes.

NULL IS A VALUE. An unresolvable ``uv.lock`` (the installed-wheel case) is
recorded as a null digest rather than omitted, and null-versus-value IS a diff
row. Omission would make "no lock file here" and "the lock file is unchanged"
indistinguishable, which is exactly the ambiguity a witness exists to remove.

BASELINE_COMMIT IS PROVENANCE, NEVER LOAD-BEARING. It records ``git rev-parse
HEAD`` where a checkout is available and null where one is not, so an operator
reading a ledger entry can find the tree the baseline was taken at. It is not
compared and contributes nothing to the diff or its hash: two manifests with
identical rows and different commits are NOT a mismatch, because the rows are
the claim and the commit is only a pointer to where the rows came from.

SIDECAR SEMANTICS — BASELINE, NOT PROVENANCE. A manifest committed beside a
stored record (``<address>.manifest.json``) describes the derivation code AT THE
TREE WHERE IT WAS GENERATED, which for an already-frozen artifact is not the
tree the artifact was derived at. The epoch of the mismatch apparatus starts at
ATTACHMENT: the sidecar makes no claim whatsoever about the span between the
freeze and the attachment, and any drift inside that span is unwitnessed. That
span is known and bounded, and saying so here is cheaper than letting a later
reader infer a provenance claim the file cannot support.

A sidecar written at the MISS that produced the record is the DEGENERATE case of
those same semantics, not an exception to them: attachment and derivation
coincide, so the unwitnessed span is empty by construction. The file still makes
no stronger claim than any other sidecar — it says what the derivation code was
at the tree where it was generated, and that this happens to be the tree the
record was derived at is a fact about when it was written, not a claim the
manifest itself carries.

DETERMINISM. Two computations against the same tree produce identical manifests:
the rows are sorted by module name, every hash is over file bytes, and the only
process-external reads are the module files, ``uv.lock`` and ``git rev-parse``.
No network, no clock, no RNG. The manifest is also invariant to the SEEDS
argument — the declared graph's node TYPES are what select handlers, and seed
values only ride as Node 0 configuration — so the same tree yields one manifest
for every request shape.
"""

import ast
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from idiograph.core.executor import HANDLERS
from idiograph.core.logging_config import get_logger
from idiograph.core.models import Graph
from idiograph.domains.arxiv.handlers import register_arxiv_handlers
from idiograph.domains.arxiv.models import PipelineParameters
from idiograph.domains.arxiv.pipeline_graph import build_pipeline_graph
from idiograph.domains.arxiv.registry import MANIFEST_SIDECAR_SUFFIX

_log = get_logger("arxiv.derivation_manifest")

#: Schema version of the manifest and of the ledger entry that carries its diff.
#: Bumped when the ROW SHAPE changes, never when the rows themselves move — a
#: manifest whose modules changed is the normal case this exists to record.
DERIVATION_MANIFEST_VERSION = 1

#: The distribution whose code this manifest witnesses. "First-party" means
#: "importable under this name"; everything else is a boundary the closure stops
#: at. Named once here rather than spelled ``"idiograph"`` at each test site.
FIRST_PARTY_ROOT = "idiograph"

#: Diff-row anchor for the lock digest. Not a module name and cannot collide
#: with one: every module anchor is first-party (or a handler's own module), and
#: a first-party anchor always begins ``idiograph``.
UV_LOCK_ANCHOR = "uv.lock"

#: The anchor recorded for a handler whose implementing module cannot be
#: identified at all. Its row carries a null hash, like any non-first-party
#: handler module — the graph binds the type to SOMETHING, and the manifest says
#: so without pretending to know what.
UNRESOLVED_HANDLER_ANCHOR = "<unresolved-handler-module>"

#: How long ``git rev-parse`` is given before the provenance stamp is abandoned.
#: The stamp is optional metadata, so a hung git is answered with ``None`` rather
#: than by holding up the observation.
_GIT_TIMEOUT_SECONDS = 10


class ModuleAnchor(BaseModel):
    """One content-anchor row: a module, where it lives, and what its bytes hash to.

    ``path`` is relative to the directory CONTAINING the ``idiograph`` package —
    ``idiograph/domains/arxiv/pipeline.py`` — deliberately not an absolute path
    and not relative to a checkout root. That form is identical in a source
    checkout and in an installed wheel, so a manifest generated in one and
    compared in the other diffs on CONTENT rather than on where the tree happens
    to sit.

    ``path`` and ``sha256`` are both nullable, and null means one thing: this
    anchor names a module whose bytes are not this tree's to witness (a handler
    supplied from outside the ``idiograph`` package, or one whose module could
    not be identified). The row is kept rather than dropped so that a handler
    moving ACROSS that boundary shows up as a diff instead of as silence.
    """

    model_config = ConfigDict(frozen=True)

    module: str
    path: str | None = None
    sha256: str | None = None


class DerivationManifest(BaseModel):
    """The descriptor: every first-party module the declared pipeline derives by.

    Frozen, and ordered — ``modules`` is sorted by dotted name — so that equality
    between two manifests is a fact about the tree rather than about traversal
    order.

    What is NOT here is as deliberate as what is. There is no timestamp (the
    ledger entry carries the observation time; the manifest describes a tree, and
    a tree has no clock), no severity, no summary, and no single roll-up hash of
    the rows. The diff hash exists to DEDUPLICATE ledger entries and is computed
    over the diff, not over the manifest.
    """

    model_config = ConfigDict(frozen=True)

    v: int = DERIVATION_MANIFEST_VERSION
    baseline_commit: str | None = None
    uv_lock_sha256: str | None = None
    modules: list[ModuleAnchor] = Field(default_factory=list)


# ── Tree location ────────────────────────────────────────────────────────────


def package_parent() -> Path:
    """The directory CONTAINING the ``idiograph`` package.

    Resolved from this file's own location, never from the working directory, so
    it is correct from any invocation directory and from an installed wheel alike
    — the discipline ``idiograph.demo.REGISTRY_ROOT`` uses. Every
    :class:`ModuleAnchor` path is stated relative to this.
    """
    return Path(__file__).resolve().parents[3]


def project_root() -> Path | None:
    """The checkout root — the nearest ancestor holding BOTH ``uv.lock`` and
    ``pyproject.toml`` — or ``None`` when there is no such directory.

    BOTH files are required, not either. A lone ``uv.lock`` somewhere up the
    filesystem from an installed wheel belongs to whatever project happens to own
    that directory, not to this one, and digesting it would report drift in a
    stranger's dependencies. Requiring the pair means the directory found is a
    project root or nothing is.

    ``None`` is the ordinary installed-wheel answer, not an error: it yields a
    null lock digest and a null baseline commit, both of which are values this
    manifest carries deliberately.
    """
    for candidate in [package_parent(), *package_parent().parents]:
        if (candidate / "uv.lock").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    return None


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_head(root: Path | None) -> str | None:
    """``git rev-parse HEAD`` at ``root``, or ``None`` when unavailable.

    Every failure mode collapses to ``None`` — no root, no git binary, not a
    checkout, a hung invocation. The commit is provenance metadata the diff never
    reads, so there is no case in which failing to obtain it should stop, slow or
    alter the observation it decorates.
    """
    if root is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


# ── Module resolution and the first-party import closure ─────────────────────


def _is_first_party(module_name: str) -> bool:
    """Whether a dotted name is importable under the ``idiograph`` package."""
    return module_name == FIRST_PARTY_ROOT or module_name.startswith(
        f"{FIRST_PARTY_ROOT}."
    )


def _module_file(module_name: str) -> Path | None:
    """The ``.py`` file defining ``module_name``, or ``None``.

    Prefers an already-imported module's ``__file__`` and falls back to
    ``importlib.util.find_spec``, which resolves a name WITHOUT executing it —
    the closure must not import modules that the run itself did not, since
    importing ``pipeline`` alone executes a ``load_dotenv()``.

    ``None`` covers namespace packages, extension modules and builtins: things
    with no single source file to anchor.
    """
    imported = sys.modules.get(module_name)
    origin = getattr(imported, "__file__", None)
    if origin is None:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, AttributeError, ValueError):
            return None
        origin = None if spec is None else spec.origin
    if origin is None or not origin.endswith(".py"):
        return None
    path = Path(origin)
    return path if path.is_file() else None


def _relative_path(path: Path) -> str | None:
    """``path`` stated relative to :func:`package_parent`, or ``None`` if outside.

    A first-party module outside the package directory is not a shape this tree
    produces; the null keeps the row (whose HASH is the load-bearing half) rather
    than dropping an anchor over a cosmetic field.
    """
    try:
        return path.resolve().relative_to(package_parent()).as_posix()
    except ValueError:
        return None


def _imported_names(path: Path, package: str) -> set[str]:
    """Every module name ``path``'s source imports, relative names resolved.

    Read by AST rather than by importing: the closure must be computable without
    executing the modules it walks, and a static read is also what makes it
    deterministic — it sees the imports the file DECLARES, including
    function-local ones, rather than the ones a particular run happened to reach.

    ``from X import y`` contributes both ``X`` and ``X.y``: the name may be a
    submodule or an attribute, and only :func:`_module_file` can tell which, so
    both candidates are offered and the non-module one resolves to nothing.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative(node, package)
            if not base:
                continue
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _resolve_relative(node: ast.ImportFrom, package: str) -> str:
    """The absolute dotted name an ``ImportFrom`` refers to.

    ``node.level`` is the number of leading dots; level 0 is already absolute.
    A level that walks past the root of ``package`` is malformed and yields the
    empty string, which the caller skips.
    """
    if not node.level:
        return node.module or ""
    parts = package.split(".") if package else []
    if node.level > len(parts):
        return ""
    base = ".".join(parts[: len(parts) - node.level + 1])
    return f"{base}.{node.module}" if node.module else base


def _package_of(module_name: str, path: Path) -> str:
    """The package a module's relative imports resolve against.

    A package's ``__init__.py`` IS its package; any other module's package is its
    parent. Derived from the file name rather than from ``sys.modules`` so it is
    the same answer whether or not the module has been imported.
    """
    if path.name == "__init__.py":
        return module_name
    return module_name.rpartition(".")[0]


def handler_modules(graph: Graph) -> list[str]:
    """The modules implementing the handlers ``graph``'s node types name.

    Resolution is ``node.type`` → ``HANDLERS`` → the handler callable →
    ``inspect.getmodule``. Registration is invoked ONLY for types nothing has
    registered yet: re-running it unconditionally would overwrite whatever a
    caller has deliberately installed in ``HANDLERS``, and an observation channel
    that mutates the registry it is reading is not observing.

    A type with no handler at all is skipped rather than raised on. The manifest
    is not a validator — ``validate_integrity`` and the executor already fail on
    an unregistered type, and failing HERE would break a cache serve over a
    defect the serve does not depend on.
    """
    if any(node.type not in HANDLERS for node in graph.nodes):
        register_arxiv_handlers()

    modules: set[str] = set()
    for node in graph.nodes:
        handler = HANDLERS.get(node.type)
        if handler is None:
            _log.warning(
                "No handler registered for node type %r — it anchors nothing in "
                "the derivation manifest.",
                node.type,
            )
            continue
        module = inspect.getmodule(handler)
        name = getattr(module, "__name__", None) or getattr(
            handler, "__module__", None
        )
        modules.add(name or UNRESOLVED_HANDLER_ANCHOR)
    return sorted(modules)


def module_anchors(seed_modules: list[str]) -> list[ModuleAnchor]:
    """Anchor rows for ``seed_modules`` and their transitive first-party imports.

    The seeds are anchored whatever they are — a handler module outside the
    ``idiograph`` package gets a null row, because the graph binding a type to
    code beyond this tree is itself worth witnessing. The CLOSURE, by contrast,
    is first-party only: it steps from a first-party module to a first-party
    import and stops at every other boundary.

    Sorted by dotted name, which is what makes two computations over one tree
    comparable by equality rather than by set arithmetic.
    """
    anchors: dict[str, ModuleAnchor] = {}
    pending = list(seed_modules)
    seeds = set(seed_modules)

    while pending:
        name = pending.pop()
        if name in anchors:
            continue
        path = _module_file(name)
        if path is None or not _is_first_party(name):
            # Outside the tree (or unidentifiable): witness the binding, not the
            # bytes. Only a SEED can land here — the closure never steps onto a
            # non-first-party name — so nothing downstream is silently pruned.
            if name in seeds:
                anchors[name] = ModuleAnchor(module=name)
            continue
        anchors[name] = ModuleAnchor(
            module=name, path=_relative_path(path), sha256=_sha256_file(path)
        )
        pending.extend(
            imported
            for imported in _imported_names(path, _package_of(name, path))
            if _is_first_party(imported) and imported not in anchors
        )

    return [anchors[name] for name in sorted(anchors)]


# ── The deriver ──────────────────────────────────────────────────────────────


def manifest_for_graph(graph: Graph) -> DerivationManifest:
    """The manifest for an already-built declared ``Graph``.

    The graph is read, never executed and never mutated: this walks its nodes'
    declared ``type`` fields and touches nothing else about it.
    """
    root = project_root()
    lock = None if root is None else root / "uv.lock"
    return DerivationManifest(
        v=DERIVATION_MANIFEST_VERSION,
        baseline_commit=_git_head(root),
        uv_lock_sha256=None if lock is None else _sha256_file(lock),
        modules=module_anchors(handler_modules(graph)),
    )


def derive_manifest(
    seeds: list[dict], parameters: PipelineParameters
) -> DerivationManifest:
    """The manifest for the pipeline ``build_pipeline_graph(seeds, parameters)``
    declares.

    ``seeds`` and ``parameters`` are the same pair the graph builder takes, so
    this reads at a call site exactly as the declared-graph builder does. Neither
    argument can move the RESULT: seeds ride as Node 0 configuration and
    parameters as per-node params, while the manifest reads only node TYPES. They
    are taken anyway so the deriver names the same pipeline the cache is about to
    serve, rather than a graph reconstructed from a second set of assumptions.
    """
    return manifest_for_graph(build_pipeline_graph(seeds, parameters))


# ── Sidecars ─────────────────────────────────────────────────────────────────


def sidecar_path_for(root: Path, address: str) -> Path:
    """Where a record's baseline manifest sits: ``<root>/<address>.manifest.json``.

    Named FROM the record's own content address so the pairing cannot drift, and
    suffixed so ``registry.sole_record_address`` excludes it from the record
    enumeration — a sidecar describes a record, it is never one.
    """
    return Path(root) / f"{address}{MANIFEST_SIDECAR_SUFFIX}"


def read_sidecar(path: Path) -> DerivationManifest | None:
    """Load a stored baseline manifest, or ``None`` if there is no file there.

    ABSENT IS NOT MISMATCHED. A record with no sidecar has no baseline to be
    measured against, and the caller's correct response is to serve exactly as it
    would have before this module existed. Returning ``None`` — rather than an
    empty manifest, which would diff against everything — is what keeps that
    distinction in the type.

    A sidecar that exists but does not parse RAISES: the file is there and says
    something this code cannot read, which is a different fact from its absence
    and must not be laundered into one. The HIT gate degrades on it; that
    decision belongs at the gate, not here.
    """
    path = Path(path)
    if not path.is_file():
        return None
    return DerivationManifest.model_validate_json(path.read_text(encoding="utf-8"))


def write_sidecar(path: Path, manifest: DerivationManifest) -> Path:
    """Write ``manifest`` to ``path`` as canonical JSON; return the path.

    Sorted keys and a trailing newline: the file is committed to the repository,
    so it is written the way a reviewed artifact is read rather than the way a
    dump is emitted.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    )
    path.write_text(f"{payload}\n", encoding="utf-8")
    return path


# ── The diff ─────────────────────────────────────────────────────────────────


def manifest_diff(
    baseline: DerivationManifest, live: DerivationManifest
) -> list[dict]:
    """The rows on which ``baseline`` and ``live`` disagree — and only those.

    One row per disagreement, each ``{anchor, path, baseline, live}``, sorted by
    anchor. A module present on one side only carries ``None`` on the other, and
    the lock digest participates under the :data:`UV_LOCK_ANCHOR` anchor on the
    same terms — including the null-versus-value case, which is a real
    disagreement and not an absence to be skipped.

    ``baseline_commit`` is NOT compared. It says where a manifest came from, not
    what it claims; two manifests with identical rows taken at different commits
    describe the same derivation code and are not a mismatch.

    An empty list means agreement. It is the whole verdict this module offers:
    no severity, no classification, no summary. The entry records; the operator
    interprets.
    """
    baseline_rows = {anchor.module: anchor for anchor in baseline.modules}
    live_rows = {anchor.module: anchor for anchor in live.modules}

    diff: list[dict] = []
    for name in sorted(set(baseline_rows) | set(live_rows)):
        was, now = baseline_rows.get(name), live_rows.get(name)
        before = None if was is None else was.sha256
        after = None if now is None else now.sha256
        if was is not None and now is not None and before == after:
            continue
        present = now if now is not None else was
        diff.append(
            {
                "anchor": name,
                "path": present.path,
                "baseline": before,
                "live": after,
            }
        )

    if baseline.uv_lock_sha256 != live.uv_lock_sha256:
        diff.append(
            {
                "anchor": UV_LOCK_ANCHOR,
                "path": UV_LOCK_ANCHOR,
                "baseline": baseline.uv_lock_sha256,
                "live": live.uv_lock_sha256,
            }
        )
    return diff


def diff_hash(diff: list[dict]) -> str:
    """The dedupe key: sha256 over the canonical JSON of the diff rows.

    Identifies a DRIFT STATE, not an observation. Two HITs against the same
    unchanged mismatch produce the same hash and so one ledger entry; a tree that
    drifts further produces a different hash and so a second entry. Canonical
    encoding (sorted keys, no incidental whitespace) mirrors ``content_address``,
    for the same reason: the key must be a function of the content and of nothing
    about how it was serialized.
    """
    encoded = json.dumps(
        diff, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256_bytes(encoded)
