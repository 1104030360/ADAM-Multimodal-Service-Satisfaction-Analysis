# shellcheck shell=bash
# Sourced by test_service_image.sh and test_migrations_image.sh: a throwaway MySQL 8.4 container on an
# internal Docker network (no published ports, no internet access), initialised from SQL files in order the
# way the services' READMEs do it (/docker-entrypoint-initdb.d). Only test-only passwords are used.
#   MYSQL_IMAGE     image to run (default mysql:8.4)
#   MYSQL_WAIT_SEC  how long to wait for the server, including loading the SQL files (default 240)

# The services' integration tests connect as root with this password (tests/integration/conftest.py).
MYSQL_TEST_ROOT_PASSWORD=adam-test-root

# start_mysql CONTAINER NETWORK SQL_FILE...
start_mysql() {
  local name=$1 network=$2 file number=1
  local mounts=()
  shift 2
  for file in "$@"; do
    # The entrypoint reads the files as the mysql user: they have to be world-readable.
    mounts+=(-v "$(realpath "$file"):/docker-entrypoint-initdb.d/$number-$(basename "$file"):ro")
    number=$((number + 1))
  done
  docker run -d --name "$name" --network "$network" -e MYSQL_ROOT_PASSWORD="$MYSQL_TEST_ROOT_PASSWORD" \
    "${mounts[@]}" "${MYSQL_IMAGE:-mysql:8.4}" >/dev/null
}

# wait_mysql CONTAINER: returns once the final server accepts TCP connections. While the entrypoint loads
# the SQL files it runs a temporary server without networking, so a TCP connection means loading finished.
wait_mysql() {
  local name=$1 limit=${MYSQL_WAIT_SEC:-240}
  local deadline=$((SECONDS + limit))
  until docker exec -e MYSQL_PWD="$MYSQL_TEST_ROOT_PASSWORD" "$name" \
    mysql --protocol=TCP -h 127.0.0.1 -u root -e 'SELECT 1' >/dev/null 2>&1; do
    if [[ $(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null) != true ]]; then
      echo "::error::MySQL 容器停了：載入的 SQL 檔可能有錯（下面是 MySQL 的 log）"
      docker logs --tail 60 "$name" 2>&1 | sed 's/^/  | /' || true
      return 1
    fi
    if ((SECONDS >= deadline)); then
      echo "::error::MySQL $limit 秒內沒有準備好（下面是 MySQL 的 log）"
      docker logs --tail 60 "$name" 2>&1 | sed 's/^/  | /' || true
      return 1
    fi
    sleep 3
  done
}
