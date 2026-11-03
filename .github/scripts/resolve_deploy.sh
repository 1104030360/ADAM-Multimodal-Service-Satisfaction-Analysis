#!/usr/bin/env bash
# First job of deploy-analyzer.yml: which commit to deploy, whether its images still have to be built, and
# whether to deploy at all. Every input arrives as an environment variable, never expanded into a script.
#   REF               github.ref; only refs/heads/main may deploy (the AWS role trusts main only)
#   EVENT_NAME        github.event_name
#   DEFAULT_COMMIT    github.sha, used when INPUT_COMMIT is empty
#   INPUT_COMMIT      workflow_dispatch input: a 40-character SHA on main, or empty
#   INPUT_BUILD_ONLY, INPUT_ALLOW_DESTRUCTIVE   workflow_dispatch inputs: true or false (empty on push)
#   REPOSITORY_OWNER  github.repository_owner (image names are lowercase)
#   GHCR_USER, GHCR_TOKEN  GITHUB_TOKEN with packages: read, to see the packages while they are private
# Writes the step outputs to $GITHUB_OUTPUT and a summary to $GITHUB_STEP_SUMMARY. Needs a full-history
# checkout (fetch-depth: 0), so that refs/remotes/origin/main exists.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
manifest=${GHCR_MANIFEST:-$here/ghcr_manifest.sh} # the tests replace it
main_ref=${MAIN_REF:-refs/remotes/origin/main}
output=${GITHUB_OUTPUT:-/dev/stdout}
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

fail() {
  echo "::error::$1"
  printf '\n**沒有部署**：%s\n' "$1" >>"$summary"
  exit 1
}

# 1. Only main: the role's trust policy accepts repo:<owner>/<repo>:ref:refs/heads/main, and only commits
#    from main are built and pushed.
if [[ ${REF:-} != refs/heads/main ]]; then
  fail "部署只能從 main 執行（這次是 ${REF:-不明}）：AWS 的部署角色只信任 main 上的 workflow。到 Actions → Deploy analyzer → Run workflow，Use workflow from 選 main，要部署的 commit 填在 commit 欄位。"
fi

# 2. The commit: 40 hex digits, present, on main, and new enough to have the deploy script.
commit=$(printf '%s' "${INPUT_COMMIT:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
if [[ -n $commit ]]; then
  origin=手動指定
elif [[ ${EVENT_NAME:-} == push ]]; then
  commit=${DEFAULT_COMMIT:-}
  origin="這次 push 到 main 的 commit"
else
  commit=${DEFAULT_COMMIT:-}
  origin="main 最新的 commit"
fi
if [[ ! $commit =~ ^[0-9a-f]{40}$ ]]; then
  fail "commit 要是 40 個字元的 SHA（0-9、a-f，例如 git rev-parse HEAD 的輸出），收到的有 ${#commit} 個字元。"
fi
if ! git cat-file -e "$commit^{commit}" 2>/dev/null; then
  fail "這個 repo 裡找不到 commit $commit。"
fi
if ! git rev-parse -q --verify "$main_ref" >/dev/null; then
  fail "找不到 $main_ref（checkout 要 fetch-depth: 0）。"
fi
if ! git merge-base --is-ancestor "$commit" "$main_ref"; then
  fail "commit $commit 不在 main 上：只能部署已經合併到 main 的 commit。"
fi
# A merged pull request's own commits are ancestors of main too, but the checks ran on the merge, not on
# them (a migration may still change later in the same pull request). Only main's first-parent line counts.
# No `| grep -q`: grep stops reading early, and with pipefail git's SIGPIPE would fail the check.
first_parent=$(git rev-list --first-parent "$main_ref")
if [[ $'\n'$first_parent$'\n' != *$'\n'$commit$'\n'* ]]; then
  fail "commit ${commit:0:12} 是某個已經合併的 PR 裡的 commit，不在 main 的主線上（first-parent）：部署合併進 main 的那個 commit。"
fi
if ! git cat-file -e "$commit:deploy/deploy.sh" 2>/dev/null; then
  fail "commit ${commit:0:12} 裡沒有 deploy/deploy.sh：自動部署之前的版本不能用這個 workflow 部署。"
fi

owner=$(printf '%s' "${REPOSITORY_OWNER:?}" | tr '[:upper:]' '[:lower:]')
analyzer_image=ghcr.io/$owner/adam-analyzer:$commit
migrations_image=ghcr.io/$owner/adam-db-migrations:$commit

# 3. Build only what GHCR does not have yet. A tag is pushed only after the image passed its tests and is
#    never rebuilt: redeploying or rolling back runs exactly the image that was tested (and ran) before.
# needs_build IMAGE DOCKERFILE: sets NEEDS_BUILD to true when the image has to be built.
needs_build() {
  local code=0
  "$manifest" "$1" || code=$?
  case $code in
    0) NEEDS_BUILD=false ;;
    3 | 4) NEEDS_BUILD=true ;; # 4: no package yet (or no access, and then the push fails and says so)
    *) fail "查不到 $1 在 GHCR 上的狀態（原因在上面）。" ;;
  esac
  if [[ $NEEDS_BUILD == true ]] && ! git cat-file -e "$commit:$2" 2>/dev/null; then
    fail "要建置 $1，但 commit ${commit:0:12} 裡沒有 $2。"
  fi
}
needs_build "$analyzer_image" services/analyzer/Dockerfile
build_analyzer=$NEEDS_BUILD
needs_build "$migrations_image" services/db/Dockerfile
build_migrations=$NEEDS_BUILD

deploy=true
if [[ ${INPUT_BUILD_ONLY:-false} == true ]]; then
  deploy=false
fi
allow_destructive=false
if [[ ${INPUT_ALLOW_DESTRUCTIVE:-false} == true ]]; then
  allow_destructive=true
fi

{
  echo "commit=$commit"
  echo "analyzer-image=$analyzer_image"
  echo "migrations-image=$migrations_image"
  echo "build-analyzer=$build_analyzer"
  echo "build-migrations=$build_migrations"
  echo "deploy=$deploy"
  echo "allow-destructive=$allow_destructive"
} >>"$output"

describe() { # IMAGE BUILD
  if [[ $2 == true ]]; then
    printf '%s（建置、測試之後推上去）' "\`$1\`"
  else
    printf '%s（GHCR 上已經有，不重新建置）' "\`$1\`"
  fi
}
# The subject goes to the summary only: printed to the log, a line starting with :: would be a command.
subject=$(git log -1 --format=%s "$commit" | sed 's/|/\\|/g; s/`/'"'"'/g')
{
  echo "### 要部署的版本"
  echo
  echo "| 項目 | 值 |"
  echo "|---|---|"
  echo "| commit | \`$commit\`（$origin） |"
  echo "| 說明 | $subject |"
  echo "| 分析服務 | $(describe "$analyzer_image" "$build_analyzer") |"
  echo "| migration | $(describe "$migrations_image" "$build_migrations") |"
  if [[ $deploy == true ]]; then
    echo "| 部署 | 是 |"
  else
    echo "| 部署 | 否：只建置（build_only） |"
  fi
  if [[ $allow_destructive == true ]]; then
    echo "| 破壞性的 migration | **允許**（先在 RDS 建好 snapshot） |"
  else
    echo "| 破壞性的 migration | 不允許：有的話部署會停下、舊版照常跑 |"
  fi
} >>"$summary"

echo "commit：$commit（$origin）"
echo "分析服務的映像檔：$analyzer_image（要建置：$build_analyzer）"
echo "migration 的映像檔：$migrations_image（要建置：$build_migrations）"
echo "部署：$deploy；允許破壞性的 migration：$allow_destructive"
