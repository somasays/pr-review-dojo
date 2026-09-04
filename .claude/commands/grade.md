Grade the owner's review of exercise `$ARGUMENTS` (N). Follow CLAUDE.md exactly.

1. Find the exercise PR: `gh pr list --state open --head "ex/N-" --json number,url,headRefName` (match the branch prefix `ex/N-`). Fetch submitted reviews and review comments:
   - `gh api repos/{owner}/{repo}/pulls/<pr>/reviews` (the review summary and state)
   - `gh api repos/{owner}/{repo}/pulls/<pr>/comments` (inline comments with path, line, body)
   - `gh api repos/{owner}/{repo}/issues/<pr>/comments` (top-level comments)
   Only count reviews and comments authored by the repo owner. If there is no submitted review (state COMMENTED, APPROVED, or CHANGES_REQUESTED) and no inline comments from the owner, stop and say: "No submitted review found on PR <url>. Submit a review on GitHub, then run /grade N again."
2. Read the answer key: `gh issue list --label answer-key --search "Answer key N in:title" --json number,body` and take the body.
3. Score using the review rubric in CLAUDE.md. Match each owner comment to a planted defect by root cause and location, not wording. Record severity given (from the comment text; if absent, treat as Major for calibration purposes and note it). Count false positives, including any assertion on the clean-code trap. Assess prioritization from the review summary. Assess communication over all comments.
4. Post the grade comment on the exercise PR using the exact template in CLAUDE.md via `gh pr comment <pr> --body-file <tmpfile>`. Also post the model review as inline comments on the PR with `scripts/pr_comment.sh` for each planted defect and the clean-code trap (skip any line the owner already commented on with the correct finding; still post on missed ones).
5. Update the review score for N in `exercises/INDEX.md` on `main`, commit with "Grade review for exercise N", push.
6. Print the score and the grade comment URL. Nothing else.
