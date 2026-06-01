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

ruff is not a project dependency. Run it via:

```
uvx ruff check
```

Do not add ruff to pyproject.toml as part of implementation work.

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
