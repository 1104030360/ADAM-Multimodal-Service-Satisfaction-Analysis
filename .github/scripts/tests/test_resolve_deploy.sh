#!/usr/bin/env bash
# Tests ../resolve_deploy.sh in a throwaway git repository (isolated from any git configuration), with a
# fake GHCR lookup. Run from anywhere.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script=$here/../resolve_deploy.sh
# shellcheck source=assert.sh
source "$here/assert.sh"

dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid \
  GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid

repo=$dir/repo
git init -q "$repo"
commit_files() { # MESSAGE FILE...: adds the files and prints the new commit
  local message=$1
  shift
  for file in "$@"; do
    mkdir -p "$repo/$(dirname "$file")"
    echo "$file" >"$repo/$file"
  done
  git -C "$repo" add -- "$@"
  git -C "$repo" commit -q -m "$message"
  git -C "$repo" rev-parse HEAD
}
before_cd=$(commit_files "before CD" README.md)
with_cd=$(commit_files "add CD" deploy/deploy.sh services/analyzer/Dockerfile services/db/Dockerfile)
special=$(commit_files "fix | pipes and \`ticks\`" other.txt)
git -C "$repo" update-ref refs/remotes/origin/main "$special"
git -C "$repo" checkout -q -b side
off_main=$(commit_files "side branch" side.txt)
git -C "$repo" checkout -q "$with_cd"

# The fake lookup answers by image name: FAKE_ANALYZER and FAKE_MIGRATIONS are its exit codes.
cat >"$dir/fake_manifest" <<'FAKE'
#!/usr/bin/env bash
echo "$1" >>"$FAKE_LOG"
case $1 in
  *adam-analyzer:*) exit "${FAKE_ANALYZER:-3}" ;;
  *adam-db-migrations:*) exit "${FAKE_MIGRATIONS:-3}" ;;
esac
exit 1
FAKE
chmod +x "$dir/fake_manifest"

# resolve [VAR=VALUE...]: runs the script in the repository; sets $code; writes $dir/out, $dir/output, and
# $dir/summary.
resolve() {
  rm -f "$dir/out" "$dir/output" "$dir/summary" "$dir/lookups"
  code=0
  (cd "$repo" && env GHCR_MANIFEST="$dir/fake_manifest" FAKE_LOG="$dir/lookups" GITHUB_OUTPUT="$dir/output" \
    GITHUB_STEP_SUMMARY="$dir/summary" REF=refs/heads/main EVENT_NAME=workflow_dispatch DEFAULT_COMMIT="$with_cd" \
    INPUT_COMMIT= INPUT_BUILD_ONLY=false INPUT_ALLOW_DESTRUCTIVE=false REPOSITORY_OWNER=SomeOwner "$@" \
    "$script") >"$dir/out" 2>&1 || code=$?
  touch "$dir/output" "$dir/summary" "$dir/lookups"
}
output() { # NAME: a step output
  sed -n "s/^$1=//p" "$dir/output"
}

begin "push: the pushed commit, both images missing"
resolve EVENT_NAME=push INPUT_BUILD_ONLY= INPUT_ALLOW_DESTRUCTIVE=
assert_eq "$code" 0 "exit code"
assert_eq "$(output commit)" "$with_cd" "commit"
assert_eq "$(output analyzer-image)" "ghcr.io/someowner/adam-analyzer:$with_cd" "analyzer image"
assert_eq "$(output migrations-image)" "ghcr.io/someowner/adam-db-migrations:$with_cd" "migration image"
assert_eq "$(output build-analyzer)" true "build analyzer"
assert_eq "$(output build-migrations)" true "build migrations"
assert_eq "$(output deploy)" true "deploy"
assert_eq "$(output allow-destructive)" false "allow destructive"
assert_contains "$dir/summary" "這次 push 到 main 的 commit"

begin "manual commit, upper case with spaces; both images exist"
resolve INPUT_COMMIT=" $(printf '%s' "$with_cd" | tr '[:lower:]' '[:upper:]') " FAKE_ANALYZER=0 FAKE_MIGRATIONS=0
assert_eq "$code" 0 "exit code"
assert_eq "$(output commit)" "$with_cd" "commit"
assert_eq "$(output build-analyzer)" false "build analyzer"
assert_eq "$(output build-migrations)" false "build migrations"
assert_contains "$dir/summary" "GHCR 上已經有，不重新建置"
assert_contains "$dir/summary" "手動指定"

begin "only the migration image is missing (private packages count as missing)"
resolve FAKE_ANALYZER=0 FAKE_MIGRATIONS=4
assert_eq "$code" 0 "exit code"
assert_eq "$(output build-analyzer)" false "build analyzer"
assert_eq "$(output build-migrations)" true "build migrations"

begin "build only, destructive allowed"
resolve INPUT_BUILD_ONLY=true INPUT_ALLOW_DESTRUCTIVE=true
assert_eq "$code" 0 "exit code"
assert_eq "$(output deploy)" false "deploy"
assert_eq "$(output allow-destructive)" true "allow destructive"
assert_contains "$dir/summary" "只建置"

begin "the commit subject cannot break the summary table"
resolve INPUT_COMMIT="$special"
assert_eq "$code" 0 "exit code"
assert_contains "$dir/summary" "| 說明 | fix \\| pipes and 'ticks' |"

begin "not from main"
resolve REF=refs/heads/side
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "::error::部署只能從 main 執行（這次是 refs/heads/side）"
assert_eq "$(wc -l <"$dir/lookups" | tr -d ' ')" 0 "registry lookups"
assert_eq "$(wc -c <"$dir/output" | tr -d ' ')" 0 "step outputs"

begin "not a 40-character SHA"
resolve INPUT_COMMIT="${with_cd:0:12}"
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "收到的有 12 個字元"

begin "an input that tries to inject a workflow command is not echoed"
resolve INPUT_COMMIT=$'abc\n::warning::injected'
assert_eq "$code" 1 "exit code"
assert_not_contains "$dir/out" "::warning::injected"

begin "unknown commit"
resolve INPUT_COMMIT=ffffffffffffffffffffffffffffffffffffffff
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "找不到 commit"

begin "commit not on main"
resolve INPUT_COMMIT="$off_main"
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "不在 main 上"

begin "commit from before CD"
resolve INPUT_COMMIT="$before_cd"
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "沒有 deploy/deploy.sh"

begin "registry lookup error stops the run"
resolve FAKE_ANALYZER=1
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "查不到 ghcr.io/someowner/adam-analyzer:$with_cd 在 GHCR 上的狀態"
assert_eq "$(wc -c <"$dir/output" | tr -d ' ')" 0 "step outputs"

end_tests "resolve_deploy.sh"
