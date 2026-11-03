#!/usr/bin/env bash
# Runs every test of an analysis service (unit, integration, and tests/queue/) inside an image that is
# already built, as the image's own non-root user and with the code baked into it, against a throwaway
# MySQL 8.4 loaded with the desktop test schema and the service's least-privilege grants. S3 and SQS are
# the tests' own moto. Pull requests use it for both services; deploy-analyzer.yml uses it on the exact
# image it then pushes.
#
#   .github/scripts/test_service_image.sh IMAGE SERVICE_DIR [RESULTS_DIR]
#
# Run from the repository root. SERVICE_DIR is services/analyzer or services/analysis_service. With
# RESULTS_DIR, pytest.xml and coverage.xml are written there. The containers share an internal network
# (no internet, no published ports) and are removed on exit.
#   NAME_PREFIX  prefix of the container and network names (default adam-ci)
set -euo pipefail

usage="用法：test_service_image.sh IMAGE SERVICE_DIR [RESULTS_DIR]"
image=${1:?$usage}
service_dir=${2:?$usage}
results=${3:-}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=disposable_mysql.sh
source "$here/disposable_mysql.sh"

schema=myapp/tests/fixtures/schema.sql
grants=$service_dir/tests/integration/grants.sql
for file in "$schema" "$grants"; do
  if [[ ! -f $file ]]; then
    echo "::error::找不到 $file（要在 repo 的根目錄執行）"
    exit 2
  fi
done

prefix=${NAME_PREFIX:-adam-ci}
network=$prefix-net-$$
db=$prefix-mysql-$$
tests=$prefix-tests-$$
cleanup() {
  docker rm -fv "$tests" "$db" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "映像檔：$image（$(docker image inspect -f '{{.Id}} {{.Os}}/{{.Architecture}} user={{.Config.User}}' "$image")）"

docker network create --internal "$network" >/dev/null
start_mysql "$db" "$network" "$schema" "$grants"
echo "等 MySQL 載入 $schema 和 $grants……"
wait_mysql "$db"

run=(--rm --name "$tests" --network "$network" -e DB_HOST="$db" -e AWS_EC2_METADATA_DISABLED=true)
pytest=(-m pytest --cov --cov-report=term-missing)
if [[ -n $results ]]; then
  mkdir -p "$results"
  chmod 0777 "$results" # the image's user (app, uid 10001) writes the reports here
  run+=(-v "$(cd "$results" && pwd):/results")
  pytest+=(--junitxml=/results/pytest.xml --cov-report=xml:/results/coverage.xml)
fi

# The image's WORKDIR (/app) holds the code and tests that were built into it, and its pyproject.toml
# keeps pytest and coverage from writing there (-p no:cacheprovider, data_file = /tmp/.coverage).
status=0
docker run "${run[@]}" "$image" python "${pytest[@]}" || status=$?
if ((status != 0)); then
  echo "::error::$service_dir 的測試沒有通過（pytest 結束碼 $status）"
  exit "$status"
fi
