# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""The settings-equality tripwire for the lint standard (IDG-097).

`[tool.ruff.lint].select` in pyproject.toml is a verbatim transcription of the
rule set ruff enables by default at the pinned version (IDG-096 clause 2). A
transcription can drift — from a ruff bump that adds or retires a rule, or from
a hand edit to the select table. This module fails the moment it does, and says
which codes moved in which direction.

It compares RULE SETS, never lint OUTPUT. Output comparison under `--isolated`
is a documented false-divergence surface: isolation drops first-party module
inference, which manufactures a phantom I001 that has nothing to do with the
rule set.

The instrument is the PINNED ruff — the binary uv.lock resolves into this
project's venv — never whatever `ruff` a developer happens to have on PATH.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The pinned binary. The suite runs under `uv run pytest`, so sys.executable is
# the venv interpreter uv.lock provisioned and the pinned ruff sits beside it.
# Resolving this way — rather than by name — is what keeps a system ruff out.
PINNED_RUFF = Path(sys.executable).parent / "ruff"

_ENABLED_HEADER = "linter.rules.enabled = ["
_BLOCK_END = "]"

# Entries render as `sys-version-slice3 (YTT101),` — the parenthesized code is
# the token we want; the human-readable rule name is not stable enough to key on.
_CODE_RE = re.compile(r"\(([A-Z]+[0-9]+)\)")


def _read_pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _pinned_ruff() -> Path:
    assert PINNED_RUFF.exists(), (
        f"pinned ruff not found at {PINNED_RUFF}. This test must run against the "
        f"binary uv.lock resolves (`uv run pytest`), never a system ruff — a "
        f"different build reports a different default rule set and the "
        f"comparison becomes meaningless."
    )
    return PINNED_RUFF


def _default_enabled_codes() -> set[str]:
    """The rule codes the pinned ruff enables by default.

    `--isolated` is what makes this the DEFAULT set: it makes ruff ignore the
    project's own pyproject.toml, so the settings dump reflects ruff's built-in
    configuration rather than the select table we are checking it against.
    """
    proc = subprocess.run(
        [str(_pinned_ruff()), "check", "--show-settings", "--isolated", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = proc.stdout.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == _ENABLED_HEADER
        )
    except StopIteration:
        raise AssertionError(
            f"`{_ENABLED_HEADER}` block not found in --show-settings output. The "
            f"settings dump format changed; this extractor needs updating before "
            f"the tripwire means anything."
        ) from None

    codes: set[str] = set()
    for line in lines[start + 1 :]:
        if line.strip() == _BLOCK_END:
            break
        match = _CODE_RE.search(line)
        assert match is not None, (
            f"unparseable entry in the enabled-rules block: {line!r}. Expected a "
            f"trailing parenthesized rule code."
        )
        codes.add(match.group(1))
    else:
        raise AssertionError(
            "enabled-rules block was never terminated; --show-settings output "
            "is truncated or its format changed."
        )

    assert codes, "extracted an empty default rule set — the extractor is broken."
    return codes


def _selected_codes() -> set[str]:
    """The rule codes `[tool.ruff.lint].select` transcribes."""
    select = _read_pyproject()["tool"]["ruff"]["lint"]["select"]
    codes = set(select)
    assert len(codes) == len(select), (
        f"`select` contains duplicate codes: "
        f"{sorted({c for c in select if select.count(c) > 1})}"
    )
    return codes


def test_pinned_ruff_matches_the_declared_pin() -> None:
    """The instrument is the version pyproject pins, not some other build."""
    dev_deps = _read_pyproject()["dependency-groups"]["dev"]
    pins = [d for d in dev_deps if d.startswith("ruff==")]
    assert len(pins) == 1, f"expected exactly one ruff== pin in dev deps, got {pins}"
    pinned_version = pins[0].split("==", 1)[1]

    proc = subprocess.run(
        [str(_pinned_ruff()), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    # `ruff --version` prints e.g. "ruff 0.16.2".
    reported = proc.stdout.strip().split()[-1]

    assert reported == pinned_version, (
        f"the ruff on the test path reports {reported}, but pyproject pins "
        f"{pinned_version}. The default rule set is version-specific, so the "
        f"transcription can only be checked against the pinned build. Re-run "
        f"under `uv run pytest` after `uv sync`."
    )


def test_select_equals_pinned_ruff_default_enabled_set() -> None:
    """`select` is set-equal to the pinned ruff's default enabled rules.

    Both directions. A one-sided difference in either direction is drift.
    """
    default_enabled = _default_enabled_codes()
    selected = _selected_codes()

    missing = default_enabled - selected
    extra = selected - default_enabled

    assert not missing and not extra, (
        "[tool.ruff.lint].select has drifted from the pinned ruff's default "
        "enabled rule set (IDG-097).\n"
        f"  enabled by ruff but ABSENT from select ({len(missing)}): "
        f"{sorted(missing) or '—'}\n"
        f"  present in select but NOT enabled by ruff ({len(extra)}): "
        f"{sorted(extra) or '—'}\n"
        f"  (select={len(selected)} codes, ruff default={len(default_enabled)} "
        f"codes)\n"
        "Changes to the lint standard take their own ruling and their own PR — "
        "do not amend select to make this pass."
    )
