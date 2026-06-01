#!/usr/bin/env bash
# scripts/preflight.sh — git-state preflight for agentic runs.
#
# Establishes that the working tree is a clean, current base before an agent
# acts on it. Run at the start of EVERY run that touches the tree — re-run per
# run, never carried forward from a prior step. A precondition is only valid
# immediately before the action it guards; the audit->implement gap is exactly
# where state drifts.
#
# Modes:
#   preflight.sh --verify        audit / read-only run: verify base, create nothing
#   preflight.sh <branch-name>   implementation run: verify base, then branch
#
# Halt contract: any non-zero exit means STOP. Report the failure to the user.
# Do NOT self-resolve — no stash, commit, discard, merge, rebase, or force.
# The halt is the feature; an agent that "helpfully" fixes state is how you get
# mangled history. This protects the BRANCH BASE and diff quality, not main's
# integrity — main is protected server-side by the repository ruleset.

set -euo pipefail

info() { printf 'preflight: %s\n' "$*" >&2; }
die()  { printf 'preflight: HALT — %s\n' "$*" >&2; exit 1; }

usage() {
  cat >&2 <<'EOF'
usage:
  scripts/preflight.sh --verify        # audit run: verify base only, no branch
  scripts/preflight.sh <branch-name>   # implementation run: verify, then branch
EOF
  exit 2
}

case "${1:-}" in
  -h|--help) usage ;;
esac
[ $# -eq 1 ] || usage
mode="$1"

# --- must be inside a work tree; operate from its root ---
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "not inside a git working tree. Run this from a clone of the repo."
cd "$(git rev-parse --show-toplevel)"

# --- 1. fetch ---
info "fetching origin"
git fetch origin || die "git fetch failed. Check network/remote, then report."

# --- 2. derive the default branch (not hardcoded) ---
default_branch() {
  local ref
  if ref=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null); then
    printf '%s\n' "${ref#origin/}"; return 0
  fi
  # local symref unset — ask origin authoritatively
  ref=$(git ls-remote --symref origin HEAD 2>/dev/null \
        | awk '/^ref:/ { sub("refs/heads/", "", $2); print $2; exit }') || true
  [ -n "$ref" ] && { printf '%s\n' "$ref"; return 0; }
  return 1
}
DEFAULT=$(default_branch) \
  || die "could not determine origin's default branch (origin/HEAD unset and remote query failed)."
info "default branch is '$DEFAULT'"

# --- 3. clean tree (BEFORE any checkout) ---
if [ -n "$(git status --porcelain)" ]; then
  git status >&2 || true
  die "working tree is not clean (see status above). Do NOT stash, commit, or discard. Report to the user and let them decide."
fi
info "working tree clean"

# --- 4. switch to default branch ---
info "checking out '$DEFAULT'"
git checkout "$DEFAULT" || die "could not check out '$DEFAULT'. Report to the user."

# --- 5. fast-forward only ---
info "fast-forward pull of origin/$DEFAULT"
git pull --ff-only origin "$DEFAULT" \
  || die "local '$DEFAULT' has diverged from origin/$DEFAULT; fast-forward refused. Do NOT merge, rebase, or force. Report to the user."

# --- 6. end-state assertion: HEAD == origin/DEFAULT ---
[ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$DEFAULT")" ] \
  || die "HEAD does not match origin/$DEFAULT after pull. The base is wrong. Do NOT proceed. Report to the user."
info "HEAD matches origin/$DEFAULT — base is current"

# --- verify mode stops here ---
if [ "$mode" = "--verify" ]; then
  info "verify-only: base confirmed clean and current. No branch created."
  exit 0
fi

# --- 7. implementation mode: create the feature branch ---
branch="$mode"
if git show-ref --verify --quiet "refs/heads/$branch"; then
  die "a local branch '$branch' already exists (leftover from a prior run?). Do NOT reuse or force it. Report to the user; pick a clean name or remove it deliberately."
fi
if git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
  die "branch '$branch' already exists on origin. Report to the user; choose another name or coordinate with the existing branch."
fi
info "creating branch '$branch' from $DEFAULT@origin"
git checkout -b "$branch" || die "could not create branch '$branch'. Report to the user."
info "preflight complete — on fresh branch '$branch', based on current origin/$DEFAULT."
