#!/usr/bin/env bash
# Stage the PR from demo/pr42.patch on GitHub. Run from a clean main that already contains shopagent/.
# Pushes a branch and opens a real PR - review before running.
set -euo pipefail
BRANCH="${SHOPAGENT_PR_REF:-fix/sanitize-listing-input}"
git diff --quiet && git diff --cached --quiet || { echo "working tree not clean"; exit 1; }
git checkout -b "$BRANCH"
git apply demo/pr42.patch
git add shopagent/inbound.py
git -c user.name="remediation-bot" -c user.email="remediation-bot@users.noreply.github.com" \
  commit -m "fix(security): sanitize listing input" \
  -m "Listing text entered ShopAgent's context raw, so a listing could inject instructions. Sanitize listing text and route all inbound text through one handler."
git push -u origin "$BRANCH"
gh pr create --base main --head "$BRANCH" --title "fix(security): sanitize listing input" \
  --body "Automated remediation: sanitize listing text before it reaches ShopAgent's context, and route calls and seller messages through a unified inbound handler."
git checkout main
