Create one exercise. Arguments: `$ARGUMENTS` in the form `<teach|test> <domain> [easy|medium|hard]` or `<teach|test> rewrite <domain>`. Difficulty defaults to medium. Domains: fastapi, services, logic, sqlalchemy, migrations, async, spark_batch, spark_streaming, concurrency. Follow CLAUDE.md throughout. Never ask questions; make the call and continue.

## Steps

1. **Number.** Read `exercises/INDEX.md`. N is the next unused number, zero-padded to two digits (01, 02, ...). If an `N` was supplied via `EXERCISE_N=<n>` in the arguments, use that instead. Slug: 3 to 5 lowercase words joined by dashes describing the feature (never the defects), for example `order-notes-endpoint`.

2. **Pick defects.** Read `exercises/catalog/<domain>.md`. Choose by difficulty:
   - easy: 1 Blocker, 2 Majors, 1 Minor, 2 Nits
   - medium: 2 Blockers, 2 Majors, 2 Minors, 2 Nits
   - hard: 2 Blockers, 3 Majors, 2 Minors, 2 Nits, plus one subtle cross-cutting issue that spans two files or two layers (record it as a Major with catalog id `<PREFIX>-XC`)
   Always add exactly one clean-code trap from the catalog's "Looks wrong but is fine" section. Prefer defects not yet used by earlier exercises in the same domain (check the answer-key issues' catalog ids with `gh issue list --label answer-key --json title,body`). For rewrite mode: read `exercises/catalog/rewrite.md`, pick 3 to 4 smells, plant zero defects, and the "domain" argument says which package the smelly feature lives in.

3. **Exercise branch.** `git checkout -b ex/N-<slug> main`. Write a feature PR of 150 to 300 changed lines into the existing app. The feature must be coherent and plausible for this codebase (a new endpoint, a new report, a new job option, a new service method). Plant the defects so they read as honest mistakes: no comments hinting at them, surrounding code idiomatic, names sensible. Include one plausible but incomplete test in `tests/` that passes and does not exercise the defects. The normal suite, ruff, ruff format, and mypy must stay green on this branch (run them). Commit with a message a mid-level engineer would write about the feature. Push.

4. **Solution branch.** `git checkout -b solutions/N ex/N-<slug>`. Apply the reference fix: minimal, proportional, no drive-by refactors. Add hidden tests under `solutions_tests/` (create `solutions_tests/__init__.py` plus one or more `test_*.py`), at least one test per Blocker and Major and ideally one per Minor, written so each fails on the exercise branch and passes here. Hidden tests may import from `conftest` for fixtures and constants. Run `scripts/verify_exercise.sh N`; it must print `OK`. If it does not, fix the tests or the solution and rerun until it does. Commit as "Reference solution for exercise N" and push. Rewrite mode: the solution is the good rewrite; hidden tests are characterization tests that pass on both branches plus, where the catalog suggests, a structural test (for example function length, nesting depth, or that pure functions exist and are importable) that fails on the exercise branch.

5. **Answer key.** Create issue "Answer key N" with label `answer-key` via `gh issue create`. Body sections: `## Exercise` (mode, domain, difficulty, PR link, branch names), `## Defects` as a table with columns `#`, `Catalog id`, `Severity`, `File:line` (line numbers on the exercise branch), `Description`; then `## Model review` with one entry per defect in priority order in the exact format CLAUDE.md's grade template uses (bold `path:line`, severity tag, catalog id, blockquoted comment); then `## Clean-code trap` (path:line, catalog id, why it is fine); then `## Reference fix rationale` (what was changed and why, what alternative was rejected); then `## Hidden tests` listing each test and the defect it covers. For rewrite mode the table lists smells instead of defects, severity column says `Smell`.

6. **Exercise PR.** `gh pr create --base main --head ex/N-<slug> --label <teach|test> --title "<feature title>" --body "<description>"`. The description is written as a mid-level engineer would: what the feature does, how it was tested, one open question or note. No mention of defects. Never merge it.

7. **Teach mode only.**
   a. Post the full model review on the exercise PR now: one inline comment per defect using `scripts/pr_comment.sh <pr> <path> <line> "<body>"` (line must be inside the diff; body starts with the severity tag in brackets, then the comment a strong reviewer would write, no catalog ids), one inline comment on the clean-code trap starting with `[Clean]` explaining why it is fine, and a summary comment via `gh pr review <pr> --comment --body "<summary>"` (GitHub refuses request-changes on your own PR, so state the merge decision in the text) with the merge decision and priority order.
   b. `git checkout -b teach/N-rewrite ex/N-<slug>`. Apply the same fix as `solutions/N` but in small steps: one defect per commit, commit message naming the defect (for example "Scope order lookup to the authenticated customer"). Do not include `solutions_tests/` on this branch. Push. `gh pr create --base ex/N-<slug> --head teach/N-rewrite --label teach --title "Rewrite: <feature title>" --body "<what this PR does, one paragraph>"`. Then leave an inline comment on every changed hunk via `scripts/pr_comment.sh`: which defect this fixes, why this fix, what alternative was considered and rejected, and what an over-engineered version would have looked like.
   c. Write `exercises/N/WALKTHROUGH.md` on `main`: the reading order a strong reviewer follows for this diff (what to open first, what to grep for, which questions to ask the author), the reasoning chain that surfaces each defect, and 5 questions an interviewer would ask about the rewrite. This file is committed to main in step 8.

8. **Index.** `git checkout main`. Append a row to `exercises/INDEX.md`: `| N | mode | domain | difficulty | YYYY-MM-DD | [PR](url) | [rewrite](url) or - | | |` (review score and fix score blank). Commit `exercises/INDEX.md` (and `exercises/N/WALKTHROUGH.md` for teach) with message "Add exercise N" and push main.

9. **Output.** Print only the PR URL(s) and a suggested timer: easy 20 min, medium 30, hard 40, rewrite 45. Teach exercises print no timer.

## Constraints

- Work in a fresh worktree if the current checkout is dirty: `git worktree add ../dojo-ex-N main`.
- Inline comment lines must be lines that appear in the PR diff, or the API returns 422. Pick lines from `git diff main...ex/N-<slug>`.
- Never write catalog ids, severities, or the words defect, bug, planted, trap into anything on `ex/*` or `teach/*` branches or PR bodies. Teach inline comments may use severity tags.
- Run `grep -rnP '\x{2014}' exercises .claude` (the em-dash code point) and fix any hit before committing.
