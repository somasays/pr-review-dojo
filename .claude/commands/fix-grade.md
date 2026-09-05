Grade the owner's fix for exercise `$ARGUMENTS` (N). Follow CLAUDE.md exactly.

1. Expect branch `fix/N` and an open PR from `fix/N` into `ex/N-<slug>`: `gh pr list --state open --head fix/N --json number,url,baseRefName`. If either is missing, say exactly what is missing ("branch fix/N not found" or "no open PR from fix/N into ex/N-<slug>") and stop.
2. Run the hidden tests against the fix. Use a temporary worktree so the current checkout is untouched:
   ```
   git fetch origin fix/N solutions/N
   tmp=$(mktemp -d); git worktree add --detach "$tmp/fix" origin/fix/N; git worktree add --detach "$tmp/sol" origin/solutions/N
   cp -R "$tmp/sol/solutions_tests" "$tmp/fix/solutions_tests"
   (cd "$tmp/fix" && ../../.venv/bin/python -m pytest -q -p no:cacheprovider solutions_tests tests; .venv ruff check .; mypy)
   ```
   Use the repo root `.venv/bin/python` with the absolute path. Record hidden passed/total, normal suite result, ruff and mypy results. Remove the worktrees afterward.
3. `git diff origin/solutions/N...origin/fix/N -- . ':!solutions_tests'` and `git diff origin/ex/N-<slug>...origin/fix/N` to see what the owner changed. Read the answer key issue (`gh issue list --label answer-key --search "Answer key N in:title" --json body`). Score with the fix rubric in CLAUDE.md: findings resolved (every planted item except the clean trap, including design and test findings, root cause gone, weighted by severity), hidden tests, no regressions, proportionality (compare the size and shape of the fix diff with the reference), PR description.
4. Post the grade comment on the fix PR via `gh pr comment` in the grade template shape (fix rubric sections, findings table with kind, resolved yes/no and how, hidden tests passed/total, coaching). Then post a second comment containing the reference fix as a collapsed block:
   ```
   <details><summary>Reference fix (solutions/N)</summary>

   ```diff
   <output of git diff origin/ex/N-<slug>...origin/solutions/N -- . ':!solutions_tests'>
   ```
   </details>
   ```
5. Update the fix score for N in `exercises/INDEX.md` on `main`, commit with "Grade fix for exercise N", push.
6. Print the score and the comment URL. Nothing else.
