#!/usr/bin/env bash
# Verify exercise N: hidden tests under solutions_tests/ must fail on the
# exercise branch and pass on the solution branch, and the normal suite plus
# ruff and mypy must pass on the solution branch.
# Usage: scripts/verify_exercise.sh N
# Exit 0 on success. Uses temporary worktrees; the current checkout is untouched.
set -euo pipefail
n=$1
# Resolve the primary checkout even when run from a linked worktree, so .venv is found.
root=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
py="$root/.venv/bin/python"
git -C "$root" fetch -q origin "+refs/heads/ex/$n-*:refs/remotes/origin/ex/$n-*" "+refs/heads/solutions/$n:refs/remotes/origin/solutions/$n" 2>/dev/null || true
# Always verify what is on GitHub, never a stale local branch.
ex_ref=$(git -C "$root" for-each-ref --format='%(refname:short)' "refs/remotes/origin/ex/$n-*" | head -1)
sol_ref=$(git -C "$root" for-each-ref --format='%(refname:short)' "refs/remotes/origin/solutions/$n" | head -1)
[ -n "$ex_ref" ] || { echo "no ex/$n-* branch"; exit 2; }
[ -n "$sol_ref" ] || { echo "no solutions/$n branch"; exit 2; }

tmp=$(mktemp -d)
cleanup() {
  git -C "$root" worktree remove --force "$tmp/ex" 2>/dev/null || true
  git -C "$root" worktree remove --force "$tmp/sol" 2>/dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT

git -C "$root" worktree add -q --detach "$tmp/sol" "$sol_ref"
git -C "$root" worktree add -q --detach "$tmp/ex" "$ex_ref"
[ -d "$tmp/sol/solutions_tests" ] || { echo "solutions/$n has no solutions_tests/"; exit 2; }
cp -R "$tmp/sol/solutions_tests" "$tmp/ex/solutions_tests"

echo "== hidden tests on $ex_ref (expect failures)"
if (cd "$tmp/ex" && "$py" -m pytest -q -p no:cacheprovider solutions_tests 2>&1 | tail -3); then
  (cd "$tmp/ex" && "$py" -m pytest -q -p no:cacheprovider solutions_tests >/dev/null 2>&1) && { echo "FAIL: hidden tests pass on the exercise branch"; exit 1; }
fi

echo "== hidden tests on $sol_ref (expect pass)"
(cd "$tmp/sol" && "$py" -m pytest -q -p no:cacheprovider solutions_tests 2>&1 | tail -3) || { echo "FAIL: hidden tests fail on the solution branch"; exit 1; }

echo "== full suite, ruff, mypy on $sol_ref"
(cd "$tmp/sol" && "$py" -m pytest -q -p no:cacheprovider tests 2>&1 | tail -2) || { echo "FAIL: normal suite fails on the solution branch"; exit 1; }
(cd "$tmp/sol" && "$py" -m ruff check . && "$py" -m ruff format --check . >/dev/null && "$py" -m mypy >/dev/null) || { echo "FAIL: lint or mypy on the solution branch"; exit 1; }

echo "== normal suite on $ex_ref (expect pass, CI must stay green on the exercise PR)"
(cd "$tmp/ex" && rm -rf solutions_tests && "$py" -m pytest -q -p no:cacheprovider tests 2>&1 | tail -2) || { echo "FAIL: normal suite fails on the exercise branch"; exit 1; }
(cd "$tmp/ex" && "$py" -m ruff check . && "$py" -m ruff format --check . >/dev/null && "$py" -m mypy >/dev/null) || { echo "FAIL: lint or mypy on the exercise branch"; exit 1; }
echo "OK: exercise $n verified"
