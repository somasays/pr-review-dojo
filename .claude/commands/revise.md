Bring exercise `$ARGUMENTS` (N) up to the current finding mix defined in CLAUDE.md: defects plus design, refactor, and test findings, and a narration requirement. Follow CLAUDE.md throughout. Do not ask questions.

## Target mix

| Exercise | Defects (kept from the domain catalog, middle band only) | Design | Refactor | Test | Clean trap |
| --- | --- | --- | --- | --- | --- |
| teach easy | 3 (1 Blocker, 2 Major) | 2 | 1 | 1 | 1 |
| test medium | 4 (2 Blocker, 2 Major) | 2 | 1 | 1 | 1 |
| test hard | 5 (2 Blocker, 3 Major) plus the cross-cutting item | 3 | 1 | 1 | 1 |
| rewrite (teach or test) | 0 | 3 to 4 smells (unchanged) | folded into smells | 1 | 0 |

Blocker and major ids must be unique within the domain across its teach and test exercises. Nits: at most one, only if it misleads a reader; otherwise none.

## Steps

1. **Read.** `CLAUDE.md`, `exercises/catalog/design.md`, `exercises/catalog/tests.md`, the domain catalog (respect its `Do not plant` section), the answer-key issue for N (`gh issue list --label answer-key --search "Answer key N in:title" --json number,body`), and the answer keys of the other exercises in the same domain (for id uniqueness). Check out `ex/N-<slug>` in a worktree.
2. **Decide the new key.** Keep defects in the middle band; drop planted nits, trivia, and internals from the key (leave the code as is unless the item is an unused import, which you remove). If a kept blocker or major id is also planted in another exercise of the same domain, replace it with an unused middle-band id, which means editing the code to plant the new one and un-planting the old one. Pick design, refactor, and test findings that fit the feature naturally.
3. **Plant, as one follow-up commit on `ex/N-<slug>`.** The commit reads as the same mid-level author finishing the feature ("Add address export", "Wire refund notifications"). Prefer new functions or a new file so existing inline comments on teach PRs stay anchored. Test finding: edit the shipped test in `tests/` so it exhibits the chosen `TR-` pattern while still passing. Keep the PR within 150 to 300 changed lines; if you would exceed it, trim the follow-up. Normal suite, ruff, ruff format, mypy stay green. Push.
4. **Solution.** Rebase `solutions/N` onto the new `ex/N-<slug>` head (`git rebase --onto ex/N-<slug> <old-ex-head> solutions/N`). Extend the reference fix for the new findings, proportionally. Add structural hidden tests from the catalog entries (`ast`, `inspect`, import graph) under `solutions_tests/`, and the improved shipped test as a hidden test when it fails on the exercise branch. Run `scripts/verify_exercise.sh N` until it prints `OK`. Force-push `solutions/N` (it is protected from deletion, not from updates).
5. **Answer key.** Replace the issue body: `## Exercise`, `## Findings` table with columns `#`, `Kind`, `Catalog id`, `Severity`, `File:line`, `Description`; `## Model review` with one entry per finding in priority order (design and test entries use the wording a strong reviewer would use, naming the consequence); `## Narration` with the two to four sentences a strong reviewer would open with; `## Clean-code trap`; `## Reference fix rationale`; `## Hidden tests`. `gh issue edit <num> --body-file <tmp>`.
6. **Teach mode only.** Rebase `teach/N-rewrite` the same way, add one commit per new finding with a per-hunk comment on the rewrite PR, post new inline comments on the exercise PR for the new findings via `scripts/pr_comment.sh`, re-post any comment GitHub marks outdated, replace the summary review (`gh pr review --comment`) so it opens with the narration. Update `exercises/N/WALKTHROUGH.md` in the primary checkout with a "Design and tests" section: how a strong reviewer notices each design and test finding, and two interviewer questions about them.
7. **Sweeps.** `grep -rnP '\x{2014}'` on everything you touched; no defect language on `ex/*` or `teach/*` commits or PR bodies. Test-mode exercise PRs must still have zero comments.
8. **Report.** Print the exercise PR URL, the verify line, and the final finding counts by kind.
