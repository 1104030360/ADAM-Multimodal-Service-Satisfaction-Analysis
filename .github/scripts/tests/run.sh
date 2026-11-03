#!/usr/bin/env bash
# Runs the tests of the workflow scripts in .github/scripts/ (no Docker, no network, no AWS):
#   .github/scripts/tests/run.sh
# Needs git and jq. Exit status 0 only when every test file passes.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
failed=0
for test in "$here"/test_*.sh; do
  echo "== $(basename "$test")"
  if ! bash "$test"; then
    failed=$((failed + 1))
  fi
done
if ((failed > 0)); then
  echo "::error::workflow 腳本的測試有 $failed 個檔案沒有通過"
  exit 1
fi
echo "workflow 腳本的測試全部通過"
