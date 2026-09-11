# AGENTS.md

## Running tests

```
uv run pytest -q
```

The suite must pass before and after any implementation change. Do not predict
the test count — run the suite and confirm the observed number. Never hardcode
a predicted count in a PR description (IDG-023 lesson: amendment delta ≠ node
total).

## Linting

ruff is pinned in the `dev` dependency-group at an exact version and locked in
`uv.lock`. Run it via:

```
uv run ruff check
```

Not `uvx ruff` — that path is retired. `uvx` resolves whatever ruff version is
current at the moment you invoke it, so two agents on the same commit can see
different violation sets. The pin is what makes a lint result reproducible.

The ruleset is declared in `[tool.ruff.lint]` in pyproject.toml: the pinned
version's default enabled set, transcribed verbatim, code by code (IDG-096).
It is not compressed to family prefixes — most families are only partially
enabled by default, so a bare prefix would silently widen the standard.

The working discipline for implementation agents is **zero new violations**.
Record your own base count before you edit:

```
uv run ruff check --output-format=concise | tail -1
```

then confirm the count has not risen after. Do not predict the base count and
do not carry one forward from an earlier run — measure it in the tree you are
about to change. Pre-existing violations are not yours to fix as a side effect
of unrelated work; they are retired under their own ruling.

Do not modify the ruff pin or the `[tool.ruff.lint]` table as part of
implementation work. Changes to the lint standard take their own ruling and
their own PR.

Inline suppressions are rulings, not annotations (IDG-098). No `# noqa` enters
the codebase without an explicit design-seat ruling on the record, cited in the
brief that carries it and naming the site and the reason. The zero-delta
discipline is therefore two-part: (1) zero new unsuppressed violations against
the base count you measured; (2) zero new inline suppressions absent a ruling
cited in your brief. A violation whose correct resolution appears to be a
suppression is a STOP-and-report, not a judgment call.

## Specs and prompt pairs

Specs live in the vault at `projects/idiograph/specs/`. A frozen spec
(`Status: FROZEN`) is the implementation contract — do not deviate without a
filed amendment. Audit prompts read the spec and write findings to `scratch/`
in the vault. Implementation prompts include or reference the relevant spec.

## Determinism thesis

The pipeline is deterministic given fixed inputs: the same seed papers, the
same OpenAlex API snapshot, and the same Leiden parameters produce the same
graph.

## Preflight: every run that touches the working tree starts here

Before doing anything to the repository — auditing it, editing it, branching —
establish that you are on a clean, current base. This applies to every run, in
every clone, and is **re-run per run**: never assume a check from an earlier
step (e.g. an audit) still holds. State drifts between runs, and in a
sequential-PR chain it drifts by design — the next branch must start from
post-merge `main`.

Run, from the repo root:

- **Audit / read-only run** (you are reasoning about the code, not changing it):
  ```
  scripts/preflight.sh --verify
  ```
- **Implementation run** (you will edit and open a PR):
  ```
  scripts/preflight.sh <feature-branch-name>
  ```

**If preflight exits non-zero, STOP.** Report the failure to the user verbatim
and wait. Do not stash, commit, discard, merge, rebase, or force-push to get
past it. The halt is intentional: it means the base is not what the work
assumes, and proceeding produces a wrong diff or a mangled history. Resolving
it is a decision for the user, not for you.

Preflight protects the *branch base and diff quality*. It does not protect
`main` — `main` is protected server-side by the repository ruleset, which you
cannot and need not bypass.
