# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Generate a blank session summary document in docs/sessions/.

Usage:
    uv run python scripts/new_session_summary.py

Creates docs/sessions/session-YYYY-MM-DD.md (today's date).
If that file already exists, creates session-YYYY-MM-DD-2.md,
session-YYYY-MM-DD-3.md, etc.

Prints the path of the created file on success.
"""

from datetime import date
from pathlib import Path

TEMPLATE = """\
# Session Summary — {date_display}

**Status:** FROZEN
**Type:** <!-- Implementation | Design | Reconciliation -->

---

## Context

<!-- One paragraph: current phase, last session output, this session's goal. -->

---

## What Was Built

<!-- Concrete artifacts produced. Be specific: file paths, function names, commands. -->

---

## Key Decisions

<!-- Architectural choices made during this session and the reasoning behind each.
     Append AMD entries to docs/decisions/amendments.md if any decisions were made. -->

---

## Files Modified

<!-- List every file created or meaningfully changed. -->

---

## Verified Working

<!-- Exact commands run and outputs confirmed (test gate, smoke test, CLI, etc.). -->

---

## Next

<!-- One paragraph: what the next session will do and why. -->
"""


def resolve_output_path(sessions_dir: Path, base_name: str) -> Path:
    candidate = sessions_dir / f"{base_name}.md"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = sessions_dir / f"{base_name}-{suffix}.md"
        if not candidate.exists():
            return candidate
        suffix += 1


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sessions_dir = repo_root / "docs" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    base_name = f"session-{today.isoformat()}"
    date_display = today.strftime("%Y-%m-%d")

    output_path = resolve_output_path(sessions_dir, base_name)
    output_path.write_text(
        TEMPLATE.format(date_display=date_display),
        encoding="utf-8",
    )

    print(output_path)


if __name__ == "__main__":
    main()
