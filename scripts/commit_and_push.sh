#!/usr/bin/env bash
# GitHub Actions から、変わったファイルを main にコミットして push する。
# 使い方: scripts/commit_and_push.sh "コミットメッセージ" パス...
set -euo pipefail
message="$1"; shift
git config user.name "asuka-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
paths=()
for p in "$@"; do [ -e "$p" ] && paths+=("$p"); done
[ ${#paths[@]} -gt 0 ] && git add -A -- "${paths[@]}"
if git diff --cached --quiet; then
  echo "変更なし"
  exit 0
fi
git commit -m "$message"
for i in 1 2 3 4; do
  if git pull --rebase origin "${GITHUB_REF_NAME:-main}" && git push origin "HEAD:${GITHUB_REF_NAME:-main}"; then
    exit 0
  fi
  sleep $((2 ** i))
done
echo "push に失敗しました" >&2
exit 1
