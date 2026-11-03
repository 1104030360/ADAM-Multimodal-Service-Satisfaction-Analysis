#!/usr/bin/env bash
# Smoke test of a built adam-db-migrations image: on pull requests that change services/db/, and in
# deploy-analyzer.yml on the exact image before it is pushed. It checks the interface the EC2 deploy script
# relies on (docs/rebuild/cd/CONTRACTS.md section 2) rather than the runner's logic, which
# services/db/tests/run.sh tests:
#   1. no DB_HOST                         -> exit code 2
#   2. a database that does not answer    -> exit code 7
#   3. "status" against MySQL 8.4 loaded with services/db/schema.sql, as adam_migrator with the production
#      privileges (PLAN section 8, step 8) over TLS -> exit code 0
# Every run has to end with "migrate: result=<word> code=<its exit code>" and must not print the password.
#
#   .github/scripts/test_migrations_image.sh IMAGE
#
# Run from the repository root.
#   NAME_PREFIX  prefix of the container and network names (default adam-ci)
set -euo pipefail

image=${1:?用法：test_migrations_image.sh IMAGE}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=disposable_mysql.sh
source "$here/disposable_mysql.sh"

schema=services/db/schema.sql
if [[ ! -f $schema ]]; then
  echo "::error::找不到 $schema（要在 repo 的根目錄執行）"
  exit 2
fi

prefix=${NAME_PREFIX:-adam-ci}
network=$prefix-net-$$
db=$prefix-mysql-$$
runner=$prefix-migrate-$$
work=$(mktemp -d)
cleanup() {
  docker rm -fv "$runner" "$db" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# Test-only password. The runner takes it from the environment only (docker run -e DB_PASSWORD).
export DB_PASSWORD=adam-test-only-migrator
cat >"$work/users.sql" <<SQL
-- The production accounts (PLAN section 8, step 8), with test-only passwords.
CREATE USER 'adam_migrator'@'%' IDENTIFIED BY '$DB_PASSWORD' REQUIRE SSL;
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, CREATE ROUTINE, ALTER ROUTINE, EXECUTE
  ON adam.* TO 'adam_migrator'@'%' WITH GRANT OPTION;
CREATE USER 'adam_app'@'%' IDENTIFIED BY 'adam-test-only-app' REQUIRE SSL;
CREATE USER 'adam_analysis'@'%' IDENTIFIED BY 'adam-test-only-analysis' REQUIRE SSL;
SQL
chmod 0755 "$work"
chmod 0644 "$work/users.sql" # read by the mysql user inside the container

failures=0

# A runner that hangs must not hold the job until its timeout (timeout(1) is missing on a stock Mac).
limited() {
  if command -v timeout >/dev/null; then
    timeout 300 "$@"
  else
    "$@"
  fi
}

# expect CODE DESCRIPTION DOCKER_RUN_ARGUMENT...: runs the image and checks the exit code, the last line,
# and that the password stays out of the output.
expect() {
  local want=$1 what=$2 code=0 last
  shift 2
  echo "== $what（應該是結束碼 $want）"
  limited docker run --rm --name "$runner" "$@" >"$work/out" 2>"$work/err" || code=$?
  docker rm -f "$runner" >/dev/null 2>&1 || true # still there only when timeout(1) stopped the client
  sed 's/^/  | /' "$work/out" "$work/err"
  last=$(grep -v '^[[:space:]]*$' "$work/out" | tail -n 1 || true)
  if ((code != want)); then
    echo "::error::$what：結束碼是 $code，應該是 $want"
    failures=$((failures + 1))
  fi
  if [[ ! $last =~ ^migrate:\ result=[^[:space:]]+\ code=([0-9]+)$ || ${BASH_REMATCH[1]} != "$code" ]]; then
    echo "::error::$what：stdout 的最後一行應該是「migrate: result=… code=$code」"
    failures=$((failures + 1))
  fi
  if grep -qF -- "$DB_PASSWORD" "$work/out" "$work/err"; then
    echo "::error::$what：輸出裡出現了密碼"
    failures=$((failures + 1))
  fi
}

expect 2 "少了 DB_HOST" --network none -e DB_PASSWORD "$image" status
expect 7 "連不上資料庫" --network none -e DB_HOST=127.0.0.1 -e DB_PORT=9 -e DB_PASSWORD -e DB_SSL_MODE=DISABLED \
  "$image" status

docker network create --internal "$network" >/dev/null
start_mysql "$db" "$network" "$schema" "$work/users.sql"
echo "等 MySQL 載入 $schema……"
wait_mysql "$db"
expect 0 "status（adam_migrator、TLS）" --network "$network" -e DB_HOST="$db" -e DB_PASSWORD -e DB_SSL_MODE=REQUIRED \
  "$image" status

if ((failures > 0)); then
  echo "::error::migration 的映像檔有 $failures 項不符合約定"
  exit 1
fi
echo "migration 的映像檔：3 項都符合約定"
