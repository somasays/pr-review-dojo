#!/usr/bin/env bash
# Post one inline review comment on a PR at a line that is part of the diff.
# Usage: scripts/pr_comment.sh <pr_number> <path> <line> [body]
# Body is read from stdin when omitted. Prints the comment URL.
set -euo pipefail
pr=$1
path=$2
line=$3
body=${4:-$(cat)}
sha=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
gh api "repos/{owner}/{repo}/pulls/$pr/comments" \
  -f body="$body" -f path="$path" -F line="$line" -f side=RIGHT -f commit_id="$sha" \
  --jq .html_url
