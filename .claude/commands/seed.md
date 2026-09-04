Build the full curriculum. Follow CLAUDE.md throughout. Do not ask questions.

1. Read `exercises/CURRICULUM.md` (columns: order, mode, domain, difficulty, theme) and `exercises/INDEX.md` and `exercises/SEED_LOG.md` (create the log if missing). The exercise number N for a row is its `order` value, zero-padded to two digits. A row is done if N appears in INDEX.md or SEED_LOG.md.
2. Total is the number of rows; done is the number already present. Print `done/total` before starting.
3. For each remaining row in order, run every step of `.claude/commands/exercise.md` with arguments `<mode> <domain> <difficulty>` (or `<mode> rewrite <domain>` when domain is `rewrite:<package>`) and `EXERCISE_N=<N>`, using the row's theme as the feature to build. Independent rows may be built in parallel by subagents, each in its own worktree (`git worktree add`), each given the full text of `exercise.md`, its N, mode, domain, difficulty, theme, and the rule that only the coordinator appends to INDEX.md. Verification is `scripts/verify_exercise.sh N` and must print `OK`.
4. If verification fails, fix and retry once. If it fails a second time: delete branches `ex/N-*` and `teach/N-*`, reset `solutions/N` to `main` (it is protected from deletion), close the answer-key issue, close any PRs, and append a row to `exercises/SEED_LOG.md` (`| N | domain | date | reason |`). Continue with the next row.
5. After each exercise, or each parallel batch, append the INDEX.md rows, commit to main with "Add exercise N" (or "Add exercises N1 to N2"), and push. Print `done/total`.
6. When every row is done, confirm: every CURRICULUM row is in INDEX.md or SEED_LOG.md; every teach PR has inline comments and a rewrite PR with per-hunk comments; every test PR has zero comments; `scripts/verify_exercise.sh N` passes for every N in INDEX.md. Report any gap.
7. Print the first teach PR URL and stop. Do not grade anything.

Safe to rerun: it skips rows already in INDEX.md or SEED_LOG.md. If context runs low, commit progress, push, and tell the owner to run `/seed` again.
