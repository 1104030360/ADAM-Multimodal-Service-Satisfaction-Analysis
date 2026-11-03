#!/usr/bin/env bash
# Tests ../changed_paths.sh in a throwaway git repository (isolated from any git configuration) that has a
# pull request merge commit like the one GitHub checks out. Run from anywhere.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script=$here/../changed_paths.sh
# shellcheck source=assert.sh
source "$here/assert.sh"

dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid \
  GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid

repo=$dir/repo
git init -q -b main "$repo"
change() { # MESSAGE FILE...: writes the files and commits them
  local message=$1
  shift
  for file in "$@"; do
    mkdir -p "$repo/$(dirname "$file")"
    echo "$message" >>"$repo/$file"
  done
  git -C "$repo" add -- "$@"
  git -C "$repo" commit -q -m "$message"
}
change base README.md services/analyzer/app.py services/db/migrations/001.sql
git -C "$repo" checkout -q -b pull
change "pull request" services/analyzer/app.py
git -C "$repo" checkout -q main
change "meanwhile on main" README.md
main_before=$(git -C "$repo" rev-parse HEAD)
git -C "$repo" merge -q --no-ff -m merge pull
git -C "$repo" mv services/db/migrations/001.sql archive.sql
git -C "$repo" commit -q -m "move a migration away"
moved=$(git -C "$repo" rev-parse HEAD)
git -C "$repo" checkout -q 'HEAD^' # the merge commit

# changed [VAR=VALUE...] -- REGEX...: sets $code; $dir/output holds run=..., $dir/out the log.
changed() {
  local settings=()
  while [[ $1 != -- ]]; do
    settings+=("$1")
    shift
  done
  shift
  rm -f "$dir/output"
  code=0
  (cd "$repo" && env GITHUB_OUTPUT="$dir/output" "${settings[@]}" "$script" "$@") >"$dir/out" 2>&1 || code=$?
  touch "$dir/output"
}
run_value() {
  sed -n 's/^run=//p' "$dir/output"
}

begin "pull request that changes the service"
changed EVENT_NAME=pull_request -- '^services/analyzer/' '^\.github/workflows/services\.yml$'
assert_eq "$code" 0 "exit code"
assert_eq "$(run_value)" true "run"
assert_contains "$dir/out" "  - services/analyzer/app.py"
assert_not_contains "$dir/out" "README.md"

begin "pull request that does not"
changed EVENT_NAME=pull_request -- '^services/db/'
assert_eq "$(run_value)" false "run"

begin "pull_request without a merge commit checks everything"
git -C "$repo" checkout -q pull
changed EVENT_NAME=pull_request -- '^services/db/'
assert_eq "$(run_value)" true "run"
git -C "$repo" checkout -q "$moved"

begin "push: files since the previous tip, renames count on both sides"
changed EVENT_NAME=push BEFORE="$main_before" -- '^services/db/'
assert_eq "$(run_value)" true "run"
assert_contains "$dir/out" "  - services/db/migrations/001.sql"

begin "push without changes to the pattern"
changed EVENT_NAME=push BEFORE="$main_before" -- '^deploy/'
assert_eq "$(run_value)" false "run"

begin "push that creates the branch"
changed EVENT_NAME=push BEFORE=0000000000000000000000000000000000000000 -- '^deploy/'
assert_eq "$(run_value)" true "run"

begin "push whose previous tip cannot be fetched"
changed EVENT_NAME=push BEFORE=1111111111111111111111111111111111111111 -- '^deploy/'
assert_eq "$(run_value)" true "run"

begin "manual run checks everything"
changed EVENT_NAME=workflow_dispatch -- '^deploy/'
assert_eq "$(run_value)" true "run"

begin "no pattern is a usage error"
changed EVENT_NAME=push --
assert_eq "$code" 2 "exit code"

end_tests "changed_paths.sh"
