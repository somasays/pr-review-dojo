Open the reference solution for exercise `$ARGUMENTS` (N) for side-by-side comparison. Follow CLAUDE.md.

1. Read `exercises/INDEX.md`. Both the review score and the fix score for N must be filled in. If either is blank, say "Reference for N is available after both /grade N and /fix-grade N" and stop.
2. Find the exercise branch `ex/N-<slug>`. If a PR from `solutions/N` into it already exists, print its URL and stop.
3. `gh pr create --base ex/N-<slug> --head solutions/N --label reference --title "Reference: exercise N" --body "Reference solution for exercise N. Hidden tests are under solutions_tests/. Do not merge."` (create the `reference` label if missing with `gh label create reference --color 1D76DB`).
4. Print the PR URL. Never merge it.
