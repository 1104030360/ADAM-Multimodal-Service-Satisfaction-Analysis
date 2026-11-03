#!/usr/bin/env bash
# Tells a Services CI job whether the files it checks changed. A required check has to report a result on
# every pull request, so the jobs always run; this lets them finish in seconds when there is nothing to check.
#
#   .github/scripts/changed_paths.sh REGEX...
#
# Each REGEX is an extended regular expression matched against repository paths, e.g. '^services/db/'.
# Writes run=true or run=false to $GITHUB_OUTPUT (standard output when that is unset):
#   pull_request   the files the pull request changes (the merge commit against its first parent)
#   push           the files changed since the previous tip of the branch (BEFORE); unknown history: run
#   anything else  run (a manual "Run workflow" runs every check)
# Environment: EVENT_NAME (github.event_name), BEFORE (github.event.before; push only).
# Needs a checkout with fetch-depth 2 or more.
set -euo pipefail

if (($# == 0)); then
  echo "用法：changed_paths.sh REGEX..." >&2
  exit 2
fi

output=${GITHUB_OUTPUT:-/dev/stdout}

# decide true|false REASON
decide() {
  echo "run=$1" >>"$output"
  echo "$2"
}

case ${EVENT_NAME:-} in
  pull_request)
    if ! git rev-parse -q --verify 'HEAD^2' >/dev/null; then
      decide true "HEAD 不是 pull request 的合併 commit：全部都檢查"
      exit 0
    fi
    files=$(git diff --name-only --no-renames 'HEAD^1' HEAD)
    ;;
  push)
    before=${BEFORE:-}
    if [[ ! $before =~ ^[0-9a-f]{40}$ || $before =~ ^0+$ ]]; then
      decide true "沒有上一個版本（新的分支）：全部都檢查"
      exit 0
    fi
    if ! git cat-file -e "$before^{commit}" 2>/dev/null \
      && ! git fetch -q --no-tags --depth=1 origin "$before" 2>/dev/null; then
      decide true "拿不到上一個版本 $before：全部都檢查"
      exit 0
    fi
    files=$(git diff --name-only --no-renames "$before" HEAD)
    ;;
  *)
    decide true "${EVENT_NAME:-手動執行}：全部都檢查"
    exit 0
    ;;
esac

patterns=()
for pattern in "$@"; do
  patterns+=(-e "$pattern")
done
matched=$(printf '%s\n' "$files" | grep -E "${patterns[@]}" || true)

if [[ -n $matched ]]; then
  decide true "有相關的變更："
  # The "  - " prefix keeps a file name from being read as a workflow command (lines starting with ::).
  printf '%s\n' "$matched" | sed 's/^/  - /'
else
  decide false "沒有改到這個檢查的檔案：跳過"
fi
