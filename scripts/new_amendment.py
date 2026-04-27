# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

"""Interactively create a new AMD entry and append it to docs/decisions/amendments.md.

Usage:
    uv run python scripts/new_amendment.py

Prompts for AMD number, status, decision summary, rationale, and done-when criteria.
Appends a formatted entry to docs/decisions/amendments.md (creates file if absent).
Prints confirmation with the AMD number when done.
"""

from datetime import date
from pathlib import Path

VALID_STATUSES = [
    "Accepted",
    "Accepted — Not Yet Implemented",
    "Superseded by AMD-NNN",
    "Deferred",
    "Rejected",
]

ENTRY_TEMPLATE = """\

---

### AMD-{number} — {summary}
Status: {status}
Decided: {decided}
Reason: {reason}
Done when: {done_when}
"""

FILE_HEADER = """\
# Idiograph — Architectural Decision Log

Append-only. One entry per AMD. Do not edit existing entries.

---
"""


def prompt(label: str, multiline: bool = False) -> str:
    """Prompt the user for input. Multiline ends on an empty line."""
    if multiline:
        print(f"{label} (press Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        # Strip the trailing blank line used as sentinel
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines).strip()
    else:
        return input(f"{label}: ").strip()


def prompt_status() -> str:
    print("\nStatus options:")
    for i, s in enumerate(VALID_STATUSES, 1):
        print(f"  {i}. {s}")
    while True:
        raw = input("Select status [1-5]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(VALID_STATUSES):
            return VALID_STATUSES[int(raw) - 1]
        print(f"  Enter a number between 1 and {len(VALID_STATUSES)}.")


def prompt_amd_number() -> str:
    while True:
        raw = input("AMD number (digits only, e.g. 16): ").strip()
        if raw.isdigit() and int(raw) > 0:
            return raw
        print("  Enter a positive integer.")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    decisions_dir = repo_root / "docs" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    amendments_path = decisions_dir / "amendments.md"

    print("=== New Amendment ===\n")

    number = prompt_amd_number()
    status = prompt_status()
    print()
    summary = prompt("Decision summary (one line)")
    print()
    reason = prompt("Rationale", multiline=True)
    print()
    done_when = prompt("Done when", multiline=True)

    decided = date.today().strftime("%Y-%m")

    entry = ENTRY_TEMPLATE.format(
        number=number,
        summary=summary,
        status=status,
        decided=decided,
        reason=reason,
        done_when=done_when,
    )

    if not amendments_path.exists():
        amendments_path.write_text(FILE_HEADER, encoding="utf-8")

    with amendments_path.open("a", encoding="utf-8") as f:
        f.write(entry)

    print(f"\nAMD-{number} appended to {amendments_path}")


if __name__ == "__main__":
    main()
