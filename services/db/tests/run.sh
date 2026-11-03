#!/usr/bin/env bash
# =============================================================================
# services/db/tests/run.sh：用真的 mysql:8.4 容器測 migration 的 runner 和 tools/verify.sh。
#   bash services/db/tests/run.sh
# 需要 docker、git。runner 一律用這次建好的映像檔跑（docker build services/db），也就是正式環境用的那個。
# MySQL 容器同一時間最多一個：runner 的測試共用一個，測完刪掉；verify.sh 的每個情境自己開一個、用完刪掉。
# 容器、網路、映像檔的名稱前綴是 ADAM_DB_TEST_PREFIX（預設 adam-db-test-）；不對外開 port；結束時全部刪掉。
# 密碼都是測試專用的值（tools/test_accounts.sql）；最後檢查所有輸出裡都沒有 adam_migrator 的密碼。
# 要用 bash 3.2 也跑得動（macOS 的 /bin/bash）。
# =============================================================================
set -u
set -o pipefail
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
db_dir=$(cd "$here/.." && pwd)
prefix=${ADAM_DB_TEST_PREFIX:-adam-db-test-}
mysql_image=${MYSQL_IMAGE:-mysql:8.4}
id=$$
server=${prefix}mysql-$id
net=${prefix}net-$id
runner_name=${prefix}runner-$id
image=$(printf '%s' "${prefix}migrations:test-$id" | tr '[:upper:]' '[:lower:]')
root_pw=adam-test-only-root
admin_pw=adam-test-only-admin
mig_pw=adam-test-only-migrator          # 和 tools/test_accounts.sql 一樣
lock_name='adam_schema_migration:adam'
work=$(mktemp -d "${TMPDIR:-/tmp}/adam-db-tests.XXXXXX") || exit 1
chmod 755 "$work"
outputs=$work/all-outputs.log
: >"$outputs"
passed=0
failed=0
current=
rc=0
image_built=0

say() { printf '%s\n' "$*"; }

cleanup() {
    docker rm -f -v "$runner_name" "$server" >/dev/null 2>&1
    docker network rm "$net" >/dev/null 2>&1
    if [ "$image_built" = 1 ]; then docker rmi -f "$image" >/dev/null 2>&1; fi
    rm -rf "$work"
}
trap cleanup EXIT
trap 'say "被中斷"; exit 1' INT TERM

# ---- 斷言 ----------------------------------------------------------------------------
t() { current=$1; say "== $1"; }
ok() { passed=$((passed + 1)); say "   ok   $1"; }
bad() {
    failed=$((failed + 1))
    say "   FAIL $current：$1"
    if [ -n "${2:-}" ] && [ -s "$2" ]; then sed 's/^/        | /' "$2" | head -60; fi
}
expect_rc() {   # expect_rc <預期的結束碼> [說明]
    if [ "$rc" -eq "$1" ]; then ok "${2:-結束碼 $1}"; else bad "結束碼 $rc（預期 $1）${2:+：$2}" "$work/out"; fi
}
expect_has() {   # expect_has <字串> [說明]：上一次的輸出有這個字串
    if grep -q -F -- "$1" "$work/out"; then ok "${2:-輸出有「$1」}"; else bad "輸出沒有「$1」${2:+：$2}" "$work/out"; fi
}
expect_lacks() {
    if grep -q -F -- "$1" "$work/out"; then bad "輸出不該有「$1」${2:+：$2}" "$work/out"; else ok "${2:-輸出沒有「$1」}"; fi
}
expect_sql() {   # expect_sql <SQL> <預期的結果> [說明]（用 root 查）
    local got
    got=$(root_sql "$1" | tr '\n' ' ' | sed 's/ $//')
    if [ "$got" = "$2" ]; then ok "${3:-$1 → $2}"; else bad "${3:-$1}：得到「$got」，預期「$2」"; fi
}

# ---- docker ---------------------------------------------------------------------------
root_sql() { MYSQL_PWD=$root_pw docker exec -e MYSQL_PWD "$server" mysql -uroot --protocol=TCP -h127.0.0.1 -N -B -e "$1" 2>&1; }
admin_load() {   # admin_load <主機上的檔案>：用 adam_admin（像 RDS 的 master）執行
    MYSQL_PWD=$admin_pw docker exec -i -e MYSQL_PWD "$server" \
        mysql --protocol=TCP -h127.0.0.1 -uadam_admin --default-character-set=utf8mb4 <"$1"
}
reset_db() {   # 重建 adam：schema.sql＋正式環境的權限（test_grants.sql），都用 adam_admin
    root_sql "DROP DATABASE IF EXISTS adam" >/dev/null
    admin_load "$db_dir/schema.sql" && admin_load "$db_dir/tools/test_grants.sql"
}

# mig [-d 資料夾] [-e 變數=值]… [--] 指令…：用建好的映像檔跑 runner（預設 DB_HOST=db、adam_migrator、TLS REQUIRED）。
# 輸出在 $work/out，結束碼在 $rc（也是回傳值，背景執行時用 wait 拿）
mig() {
    local dir='' envs=() vol=()
    while [ $# -gt 0 ]; do
        case $1 in
            -d) dir=$2; shift 2 ;;
            -e) envs+=(-e "$2"); shift 2 ;;
            --) shift; break ;;
            *) break ;;
        esac
    done
    [ -n "$dir" ] && vol=(-v "$dir:/migrations:ro" -e MIGRATIONS_DIR=/migrations)
    DB_PASSWORD=$mig_pw docker run --rm --name "$runner_name" --network "$net" \
        -e DB_HOST=db -e DB_PASSWORD -e DB_SSL_MODE=REQUIRED \
        ${vol[@]+"${vol[@]}"} ${envs[@]+"${envs[@]}"} "$image" "$@" >"$work/out" 2>&1
    rc=$?
    cat "$work/out" >>"$outputs"
    return "$rc"
}

# mkdir_mig <名稱> <tests/migrations 裡的檔案…>：001～007（正式的）＋指定的測試檔，印出資料夾
mkdir_mig() {
    local d=$work/m-$1 f
    shift
    rm -rf "$d"
    mkdir -p "$d"
    cp "$db_dir"/migrations/*.sql "$d/"
    for f in "$@"; do cp "$here/migrations/$f" "$d/"; done
    chmod -R a+rX "$d"
    printf '%s' "$d"
}

sha_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

wait_for() {   # wait_for <最多幾秒> <SQL> <預期的結果>
    local i=0 limit=$(($1 * 5))
    while [ "$i" -lt "$limit" ]; do
        [ "$(root_sql "$2")" = "$3" ] && return 0
        sleep 0.2
        i=$((i + 1))
    done
    return 1
}


# =============================================================================
# 1. 映像檔、用法、lint（不連資料庫）
# =============================================================================
t "建映像檔"
if docker build -q -t "$image" "$db_dir" >"$work/out" 2>&1; then
    image_built=1
    ok "docker build $db_dir"
else
    bad "docker build 失敗" "$work/out"
    exit 1
fi
uid=$(docker run --rm --entrypoint id "$image" -u)
if [ "$uid" != 0 ]; then ok "不用 root 跑（uid $uid）"; else bad "映像檔用 root 跑"; fi
img_ca=$(docker run --rm --entrypoint sha256sum "$image" /opt/adam-db/certs/rds-global-bundle.pem | cut -d' ' -f1)
if [ "$img_ca" = "$(sha_of "$db_dir/certs/rds-global-bundle.pem")" ]; then ok "映像檔裡的 RDS CA 和 repo 的一樣"; else bad "映像檔裡的 RDS CA 不一樣"; fi
docker run --rm --entrypoint sh "$image" -c 'cd /opt/adam-db/migrations && sha256sum *.sql' >"$work/img-sha.txt"
(cd "$db_dir/migrations" && for f in *.sql; do printf '%s  %s\n' "$(sha_of "$f")" "$f"; done) >"$work/repo-sha.txt"
if cmp -s "$work/img-sha.txt" "$work/repo-sha.txt"; then ok "映像檔裡的 migrations/ 和 repo 的一樣"; else bad "映像檔裡的 migrations/ 不一樣" "$work/img-sha.txt"; fi
docker run --rm --entrypoint sh "$image" -c 'ls /opt/adam-db; ls /opt/adam-db/*/' >"$work/out" 2>&1
expect_lacks "tests" "映像檔裡沒有 tests/"
expect_lacks "schema.sql" "映像檔裡沒有 schema.sql"

t "用法和設定錯誤：結束碼 2"
docker run --rm "$image" >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "沒有參數＝status，少了 DB_HOST"
expect_has "少了 DB_HOST"
expect_has "migrate: result=usage code=2" "最後一行是結果"
docker run --rm -e DB_HOST=db "$image" status >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "少了 DB_PASSWORD"
expect_has "少了 DB_PASSWORD"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD "$image" migrate >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "不認得的指令"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD "$image" apply --force >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "apply 不認得的參數"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD -e DB_SSL_MODE=SOMETIMES "$image" status >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "DB_SSL_MODE 不對"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD -e DB_SSL_MODE=VERIFY_CA -e DB_SSL_CA=/nope.pem "$image" status >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "VERIFY_CA 但讀不到 DB_SSL_CA"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD -e MIGRATE_LOCK_WAIT=soon "$image" status >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 2 "MIGRATE_LOCK_WAIT 不是數字"
d=$(mkdir_mig badname)
cp "$d/007_analysis_queue.sql" "$d/008-Bad-Name.sql"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD -v "$d:/migrations:ro" -e MIGRATIONS_DIR=/migrations "$image" status >"$work/out" 2>&1; rc=$?
expect_rc 2 "migration 的檔名不對"
d=$(mkdir_mig dupnum 008_test_note_table.sql)
cp "$here/migrations/009_test_note_procedure.sql" "$d/008_test_other.sql"
DB_PASSWORD=$mig_pw docker run --rm -e DB_HOST=db -e DB_PASSWORD -v "$d:/migrations:ro" -e MIGRATIONS_DIR=/migrations "$image" status >"$work/out" 2>&1; rc=$?
expect_rc 2 "migration 的編號重複"
expect_has "編號重複"

t "lint：破壞性的語句"
docker run --rm "$image" lint >"$work/out" 2>&1; rc=$?
expect_rc 3 "正式的 001～007：001～003 的 DROP PROCEDURE 沒有重新 GRANT（就是 T42 第 9 節的問題）"
for f in 001_photo_and_reports 002_account_self_service 003_service_report_states; do expect_has "destructive: $f.sql:"; done
for f in 004_ai_transcripts 005_service_outcome 006_coaching 007_analysis_queue; do expect_lacks "destructive: $f.sql:" "$f.sql 沒有誤判"; done
docker run --rm -v "$here:/t:ro" "$image" lint /t/lint/destructive.sql >"$work/out" 2>&1; rc=$?
expect_rc 3 "lint/destructive.sql"
for want in "destructive.sql:2: DROP TABLE" "destructive.sql:3: DROP INDEX" "destructive.sql:4: DROP VIEW" \
            "destructive.sql:5: TRUNCATE" "destructive.sql:6: RENAME TABLE" "destructive.sql:7: ALTER TABLE TBL_X ... DROP" \
            "destructive.sql:8: ALTER TABLE TBL_X ... DROP" "destructive.sql:9: ALTER TABLE TBL_X ... MODIFY" \
            "destructive.sql:10: ALTER TABLE TBL_X ... CHANGE" "destructive.sql:11: ALTER TABLE TBL_X ... RENAME" \
            "destructive.sql:12: DELETE 沒有 WHERE" "destructive.sql:13: UPDATE 沒有 WHERE" "destructive.sql:14: REVOKE" \
            "destructive.sql:15: REPLACE" "destructive.sql:16: DROP TABLE｜DROP TABLE TBL_HIDDEN" \
            "destructive.sql:17: DROP PROCEDURE SP_REMOVED（拿掉之後沒有再建立" \
            "destructive.sql:18: DROP TABLE｜DROP TABLE TBL_DYNAMIC" \
            "destructive.sql:20: DROP PROCEDURE SP_REGRANT_MISSING（重新建立了，但之後沒有 GRANT EXECUTE" \
            "destructive.sql:22: DELETE 沒有 WHERE"; do
    expect_has "$want"
done
docker run --rm -v "$here:/t:ro" "$image" lint /t/lint/clean.sql /t/migrations/008_test_note_table.sql \
    /t/migrations/009_test_note_procedure.sql /t/migrations/010_test_note_index.sql /t/migrations/012_test_add_column.sql \
    >"$work/out" 2>&1; rc=$?
expect_rc 0 "註解、字串、ON DELETE、REPLACE()、暫時的程序、DROP＋CREATE＋GRANT 都不算破壞性的"
docker run --rm -v "$here:/t:ro" "$image" lint /t/lint/declared.sql /t/migrations/011_test_drop_column.sql >"$work/out" 2>&1; rc=$?
expect_rc 3 "標了 -- migrate: destructive 的檔案、字串裡的 ALTER TABLE … DROP COLUMN"
expect_has "declared.sql:2: 檔案標了 -- migrate: destructive"
expect_has "011_test_drop_column.sql:10: ALTER TABLE TBL_TEST_NOTE ... DROP"

# =============================================================================
# 2. 拋棄式 MySQL 8.4
# =============================================================================
t "啟動 $mysql_image"
docker network create "$net" >/dev/null || { bad "docker network create 失敗"; exit 1; }
MYSQL_ROOT_PASSWORD=$root_pw docker run -d --name "$server" --network "$net" --network-alias db \
    -e MYSQL_ROOT_PASSWORD "$mysql_image" >/dev/null || { bad "docker run $mysql_image 失敗"; exit 1; }
ready=0
for _ in $(seq 1 90); do
    if root_sql "SELECT 1" 2>/dev/null | grep -qx 1; then ready=1; break; fi
    sleep 2
done
if [ "$ready" = 1 ]; then ok "MySQL 起來了"; else docker logs "$server" 2>&1 | tail -20; bad "MySQL 沒有起來"; exit 1; fi
if MYSQL_PWD=$root_pw docker exec -i -e MYSQL_PWD "$server" mysql -uroot --protocol=TCP -h127.0.0.1 \
        <"$db_dir/tools/test_accounts.sql"; then
    ok "建立 adam_admin、adam_migrator、adam_app、adam_analysis"
else
    bad "建立帳號失敗"; exit 1
fi
root_sql "SHOW GRANTS FOR 'adam_migrator'@'%'" >"$work/out"
bt='`'
expect_has "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, REFERENCES, INDEX, ALTER, EXECUTE, CREATE ROUTINE, ALTER ROUTINE ON ${bt}adam${bt}.* TO ${bt}adam_migrator${bt}@${bt}%${bt} WITH GRANT OPTION" \
    "adam_migrator 的權限和正式環境一樣"
expect_lacks "DROP" "adam_migrator 沒有 DROP"
expect_sql "SELECT ssl_type FROM mysql.user WHERE user = 'adam_migrator'" "ANY" "adam_migrator 要 TLS（REQUIRE SSL）"

t "連不上：結束碼 7"
mig -e DB_HOST=no-such-host.invalid -- status
expect_rc 7 "主機不存在"
expect_has "result=no-connection"
expect_lacks "no-such-host.invalid" "錯誤訊息裡的主機名稱遮掉了"
DB_PASSWORD=wrong-test-only-password docker run --rm --network "$net" -e DB_HOST=db -e DB_PASSWORD -e DB_SSL_MODE=REQUIRED \
    "$image" status >"$work/out" 2>&1; rc=$?; cat "$work/out" >>"$outputs"
expect_rc 7 "密碼不對"
expect_has "Access denied"
expect_lacks "wrong-test-only-password" "密碼不在輸出裡"
mig -e DB_SSL_MODE=DISABLED -- status
expect_rc 7 "adam_migrator 是 REQUIRE SSL，不用 TLS 連不上"
mig -e DB_SSL_MODE=VERIFY_IDENTITY -- status
expect_rc 7 "預設的 VERIFY_IDENTITY＋RDS 的 CA：不認測試伺服器自己簽的憑證"

t "基準檢查沒過：結束碼 5，什麼都沒記錄"
root_sql "DROP DATABASE IF EXISTS adam; CREATE DATABASE adam" >/dev/null
mig -- apply
expect_rc 5 "空的資料庫"
expect_has "是空的"
expect_sql "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'adam'" "0" "沒有建立記錄表"
reset_db || bad "reset_db 失敗"
root_sql "ALTER TABLE adam.tbl_service DROP CHECK chk_service_outcome; DROP TABLE adam.tbl_analysis_worker; DROP PROCEDURE adam.sp_coaching_cancel" >/dev/null
mig -- apply
expect_rc 5 "缺了 005 的 CHECK、006 的程序、007 的資料表"
expect_has "baseline: MISSING 005 constraint tbl_service.chk_service_outcome"
expect_has "baseline: MISSING 006 procedure sp_coaching_cancel"
expect_has "baseline: MISSING 007 table tbl_analysis_worker"
expect_has "baseline: MISSING 007 column tbl_analysis_worker.worker_id"
expect_has "result=baseline-failed code=5"
expect_sql "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'adam' AND TABLE_NAME = 'tbl_schema_migration'" "0" "沒有建立記錄表"
mig -- status
expect_rc 5 "status 也是 5"
expect_sql "SELECT IS_FREE_LOCK('$lock_name')" "1" "鎖放掉了"

t "第一次執行：基準檢查通過，記錄 001～007（不重跑）"
reset_db || bad "reset_db 失敗"
docker cp "$server:/var/lib/mysql/ca.pem" "$work/server-ca.pem" >/dev/null && chmod a+r "$work/server-ca.pem"
# 沒有掛 CA 檔：讀不到是設定錯誤（2）；掛上伺服器自己的 CA 之後 VERIFY_CA 要過
mig -e DB_SSL_MODE=VERIFY_CA -e DB_SSL_CA=/ca/server-ca.pem -- status
expect_rc 2 "VERIFY_CA 讀不到 CA 檔"
DB_PASSWORD=$mig_pw docker run --rm --network "$net" -e DB_HOST=db -e DB_PASSWORD -e DB_SSL_MODE=VERIFY_CA \
    -e DB_SSL_CA=/ca/server-ca.pem -v "$work/server-ca.pem:/ca/server-ca.pem:ro" "$image" status >"$work/out" 2>&1; rc=$?
cat "$work/out" >>"$outputs"
expect_rc 0 "VERIFY_CA＋伺服器的 CA：status 成功（第一次執行，還沒記錄）"
expect_has "第一次執行"
expect_has "baseline: 001～007 的 297 項都在"
expect_sql "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'adam' AND TABLE_NAME = 'tbl_schema_migration'" "0" "status 不寫任何東西"
mig -e MIGRATE_SOURCE_COMMIT=0123456789abcdef0123456789abcdef01234567 -- apply
expect_rc 0 "apply"
expect_has "result=baseline code=0"
expect_sql "SELECT CONCAT_WS('|', COUNT(*), SUM(baseline), MIN(applied_by), MIN(source_commit)) FROM adam.tbl_schema_migration" \
    "7|7|adam_migrator@%|0123456789abcdef0123456789abcdef01234567" "記錄了 7 列，都是 baseline，誰套用的、哪個 commit"
root_sql "SELECT CONCAT(sha256, '  ', file_name) FROM adam.tbl_schema_migration ORDER BY migration_id" >"$work/db-sha.txt"
if cmp -s "$work/db-sha.txt" "$work/repo-sha.txt"; then ok "記錄的 SHA-256 和檔案的一樣"; else bad "記錄的 SHA-256 不一樣" "$work/db-sha.txt"; fi
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE applied_at BETWEEN UTC_TIMESTAMP() - INTERVAL 5 MINUTE AND UTC_TIMESTAMP() + INTERVAL 1 MINUTE" "7" "套用時間是 UTC"
expect_sql "SELECT IS_FREE_LOCK('$lock_name')" "1" "鎖放掉了"
mig -- apply
expect_rc 0 "再跑一次"
expect_has "result=up-to-date code=0" "什麼都不做"
expect_has "applied: 7 個（001～007"
mig -- status
expect_rc 0 "status"
expect_has "applied: 007 007_analysis_queue.sql"
expect_has "待套用 0 個"

t "套用新的 migration（DELIMITER、DROP＋CREATE＋GRANT），再跑一次什麼都不做"
dA=$(mkdir_mig a 008_test_note_table.sql 009_test_note_procedure.sql)
mig -d "$dA" -- status
expect_rc 0 "status"
expect_has "pending: 008 008_test_note_table.sql"
expect_has "pending: 009 009_test_note_procedure.sql"
expect_has "其中破壞性的 0 個"
mig -d "$dA" -- apply
expect_rc 0 "apply"
expect_has "apply: 008 008_test_note_table.sql 完成"
expect_has "apply: 009 009_test_note_procedure.sql 完成"
expect_has "result=applied code=0"
expect_sql "SELECT CONCAT_WS('|', GROUP_CONCAT(migration_id ORDER BY migration_id), SUM(baseline)) FROM adam.tbl_schema_migration WHERE migration_id > 7" "8,9|0" "記錄了 008、009（不是 baseline）"
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE migration_id > 7 AND duration_ms IS NOT NULL AND source_commit IS NULL" "2" "花了多久；沒有 MIGRATE_SOURCE_COMMIT 就是 NULL"
expect_sql "SELECT DEFINER FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = 'adam' AND ROUTINE_NAME = 'sp_test_note_count'" "adam_migrator@%" "程序的 DEFINER 是 adam_migrator"
expect_sql "SELECT Proc_priv FROM mysql.procs_priv WHERE User = 'adam_app' AND Routine_name = 'sp_test_note_count'" "Execute" "adam_app 拿到 EXECUTE"
MYSQL_PWD=adam-test-only-app docker exec -e MYSQL_PWD "$server" mysql --protocol=TCP -h127.0.0.1 -uadam_app --ssl-mode=REQUIRED -N -B \
    -e "CALL adam.sp_test_note_count('S1'); INSERT INTO adam.tbl_test_note (service_id) SELECT 'nope' FROM DUAL WHERE FALSE" >"$work/out" 2>&1; rc=$?
expect_rc 0 "adam_app 可以呼叫新的程序、寫新的資料表"
mig -d "$dA" -- apply
expect_rc 0 "再跑一次"
expect_has "result=up-to-date"
expect_has "applied: 9 個（001～009"

t "已經套用的檔案被改了：結束碼 4，什麼都沒套用"
dB=$(mkdir_mig b 008_test_note_table.sql 009_test_note_procedure.sql 010_test_note_index.sql)
printf '%s\n' "-- 改了一個字" >>"$dB/008_test_note_table.sql"
mig -d "$dB" -- apply
expect_rc 4 "SHA-256 不同"
expect_has "changed: 008"
expect_has "result=changed code=4"
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE migration_id = 10" "0" "010 沒有套用"
expect_sql "SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = 'adam' AND INDEX_NAME = 'idx_test_note_created'" "0" "010 的索引沒有建"
mig -d "$dB" -- status
expect_rc 4 "status 也是 4"
dB2=$(mkdir_mig b2 009_test_note_procedure.sql)
cp "$here/migrations/008_test_note_table.sql" "$dB2/008_test_note_renamed.sql"
mig -d "$dB2" -- apply
expect_rc 4 "改了檔名也算"

t "破壞性的被擋下：結束碼 3，什麼都沒套用；--allow-destructive 之後照樣跑"
dC=$(mkdir_mig c 008_test_note_table.sql 009_test_note_procedure.sql 010_test_note_index.sql 011_test_drop_column.sql)
mig -d "$dC" -- status
expect_rc 0 "status 列出來"
expect_has "destructive: 011_test_drop_column.sql:10:"
expect_has "其中破壞性的 1 個"
mig -d "$dC" -- apply
expect_rc 3 "apply 被擋下"
expect_has "result=destructive code=3"
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE migration_id >= 10" "0" "010（不是破壞性的）也沒有套用"
expect_sql "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'adam' AND TABLE_NAME = 'tbl_test_note' AND COLUMN_NAME = 'note_extra'" "1" "欄位還在"
mig -d "$dC" -- apply --allow-destructive
expect_rc 0 "--allow-destructive"
expect_has "apply: 011 011_test_drop_column.sql 完成"
expect_sql "SELECT GROUP_CONCAT(migration_id ORDER BY migration_id) FROM adam.tbl_schema_migration WHERE migration_id >= 10" "10,11" "010、011 都記錄了"
expect_sql "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'adam' AND TABLE_NAME = 'tbl_test_note' AND COLUMN_NAME = 'note_extra'" "0" "欄位刪掉了（adam_migrator 沒有 DROP 也做得到 ALTER TABLE … DROP COLUMN）"

t "資料庫有、映像檔沒有的 migration（換回舊版）：不算錯"
mig -d "$dA" -- apply
expect_rc 0 "結束碼 0"
expect_has "db-only: 010 010_test_note_index.sql"
expect_has "db-only: 011 011_test_drop_column.sql"
expect_has "result=up-to-date"

t "中途失敗：結束碼 1，成功的照樣記錄，後面的不跑"
dE=$(mkdir_mig e 008_test_note_table.sql 009_test_note_procedure.sql 010_test_note_index.sql 011_test_drop_column.sql \
     012_test_add_column.sql 013_test_fails.sql 014_test_after_failure.sql)
mig -d "$dE" -- apply
expect_rc 1 "013 失敗"
expect_has "apply: 012 012_test_add_column.sql 完成"
expect_has "apply: 013 013_test_fails.sql 失敗"
expect_has "ERROR 1146" "印出 MySQL 的錯誤"
expect_has "result=failed code=1"
expect_lacks "apply: 014" "014 沒有跑"
expect_sql "SELECT GROUP_CONCAT(migration_id ORDER BY migration_id) FROM adam.tbl_schema_migration WHERE migration_id >= 12" "12" "只記錄了 012"
expect_sql "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'adam' AND TABLE_NAME = 'tbl_test_after_failure'" "0" "014 的資料表沒有建"
expect_sql "SELECT IS_FREE_LOCK('$lock_name')" "1" "失敗之後鎖放掉了"

t "順序亂了：結束碼 1，什麼都沒套用"
reset_db || bad "reset_db 失敗"
dG=$(mkdir_mig g 008_test_note_table.sql 010_test_note_index.sql)
mig -d "$dG" -- apply
expect_rc 0 "先套用 008、010"
cp "$here/migrations/009_test_note_procedure.sql" "$dG/" && chmod a+r "$dG/009_test_note_procedure.sql"
mig -d "$dG" -- apply
expect_rc 1 "之後才出現的 009"
expect_has "result=out-of-order code=1"
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE migration_id = 9" "0" "009 沒有套用"

t "等不到鎖：結束碼 6；runner 的程序 argv 裡沒有密碼"
MYSQL_PWD=$root_pw docker exec -d -e MYSQL_PWD "$server" mysql -uroot --protocol=TCP -h127.0.0.1 \
    -e "SELECT GET_LOCK('$lock_name', 0), SLEEP(60)"
if wait_for 10 "SELECT IS_USED_LOCK('$lock_name') IS NOT NULL" 1; then ok "另一條連線拿著鎖"; else bad "拿不到測試用的鎖"; fi
start=$(date +%s)
mig -e MIGRATE_LOCK_WAIT=4 -d "$dA" -- apply &
bg=$!
# runner 在等鎖的時候，讀它的容器裡每個程序的 argv（等到拿鎖的那條 mysql 連線出現）
: >"$work/cmdlines.txt"
for _ in $(seq 1 40); do
    docker exec "$runner_name" sh -c 'for f in /proc/[0-9]*/cmdline; do tr "\0" " " <"$f"; echo; done' \
        >"$work/cmdlines.txt" 2>/dev/null
    grep -q -- "--unbuffered" "$work/cmdlines.txt" && break
    sleep 0.2
done
wait "$bg"; rc=$?
elapsed=$(( $(date +%s) - start ))
expect_rc 6 "等 4 秒之後放棄（花了 $elapsed 秒）"
expect_has "result=lock-timeout code=6"
if grep -q -- "--user=adam_migrator" "$work/cmdlines.txt"; then ok "看得到 runner 的 mysql 程序"; else bad "沒看到 runner 的 mysql 程序" "$work/cmdlines.txt"; fi
if grep -q -F -- "$mig_pw" "$work/cmdlines.txt"; then bad "argv 裡有密碼" "$work/cmdlines.txt"; else ok "argv 裡沒有密碼"; fi
holder=$(root_sql "SELECT IS_USED_LOCK('$lock_name')")
root_sql "KILL $holder" >/dev/null
if wait_for 10 "SELECT IS_FREE_LOCK('$lock_name')" 1; then ok "測試用的鎖放掉了"; else bad "測試用的鎖沒放掉"; fi

t "中斷（SIGTERM、SIGKILL）：鎖一定放掉，進行中的那一個不記錄"
dF=$(mkdir_mig f 008_test_note_table.sql 010_test_note_index.sql 015_test_slow.sql)
mig -d "$dF" -- apply &
bg=$!
if wait_for 20 "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE INFO LIKE 'DO SLEEP%'" 1; then ok "015 開始跑了"; else bad "015 沒有開始"; fi
docker kill --signal TERM "$runner_name" >/dev/null 2>&1
wait "$bg"; rc=$?
expect_rc 1 "SIGTERM → 結束碼 1"
expect_has "收到 SIGTERM"
expect_has "result=interrupted code=1"
if wait_for 5 "SELECT IS_FREE_LOCK('$lock_name')" 1; then ok "鎖放掉了"; else bad "鎖沒放掉"; fi
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE migration_id = 15" "0" "015 沒有記錄"
root_sql "KILL QUERY $(root_sql "SELECT ID FROM information_schema.PROCESSLIST WHERE INFO LIKE 'DO SLEEP%' LIMIT 1")" >/dev/null 2>&1
mig -d "$dF" -- apply &
bg=$!
if wait_for 20 "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE INFO LIKE 'DO SLEEP%' AND USER = 'adam_migrator'" 1; then ok "015 又開始跑了"; else bad "015 沒有開始"; fi
docker kill "$runner_name" >/dev/null 2>&1
wait "$bg"; rc=$?
if [ "$rc" -ne 0 ]; then ok "SIGKILL（結束碼 $rc）"; else bad "SIGKILL 之後結束碼是 0"; fi
if wait_for 15 "SELECT IS_FREE_LOCK('$lock_name')" 1; then ok "SIGKILL 之後鎖也放掉了（連線斷了）"; else bad "SIGKILL 之後鎖沒放掉"; fi
expect_sql "SELECT COUNT(*) FROM adam.tbl_schema_migration WHERE migration_id = 15" "0" "015 沒有記錄"

t "密碼沒有出現在任何輸出裡"
if grep -q -F -- "$mig_pw" "$outputs"; then bad "輸出裡有 adam_migrator 的密碼" "$outputs"; else ok "runner 的所有輸出（$(grep -c . "$outputs") 行）都沒有密碼"; fi

docker rm -f -v "$runner_name" "$server" >/dev/null 2>&1
docker network rm "$net" >/dev/null 2>&1

# =============================================================================
# 3. tools/verify.sh（每個情境是一個暫時的 git repo；verify.sh 自己開、刪 MySQL 容器）
# =============================================================================
# new_repo <名稱>：暫時的 git repo，先只有 myapp/tests/fixtures/schema.sql，印出路徑
new_repo() {
    local r=$work/repo-$1
    rm -rf "$r"
    mkdir -p "$r/myapp/tests/fixtures"
    cp "$db_dir/../../myapp/tests/fixtures/schema.sql" "$r/myapp/tests/fixtures/schema.sql"
    git -C "$r" init -q
    git -C "$r" config user.name adam-db-test
    git -C "$r" config user.email adam-db-test@example.invalid
    git -C "$r" config commit.gpgsign false
    printf '%s' "$r"
}
add_db() {   # add_db <repo>：放進目前的 services/db（不含 tests/）
    mkdir -p "$1/services"
    rm -rf "$1/services/db"
    mkdir -p "$1/services/db"
    (cd "$db_dir" && tar -cf - --exclude ./tests .) | tar -xf - -C "$1/services/db"
}
commit() { git -C "$1" add -A && git -C "$1" commit -q -m "$2" && git -C "$1" tag -f "$3" >/dev/null; }
verify() {   # verify <repo>：跑那個 repo 裡的 verify.sh --base-ref base
    ADAM_DB_TEST_PREFIX=$prefix bash "$1/services/db/tools/verify.sh" --base-ref base >"$work/out" 2>&1
    rc=$?
    cat "$work/out" >>"$outputs"
}
# 把 008_test_note_table.sql 的 CREATE TABLE 加進 schema.sql 和 fixture（不含 GRANT：schema.sql 不給權限）
add_note_table_to_schemas() {
    local f
    for f in "$1/services/db/schema.sql" "$1/myapp/tests/fixtures/schema.sql"; do
        grep -v -E '^(GRANT|SET NAMES|USE )' "$here/migrations/008_test_note_table.sql" >>"$f"
    done
}

t "verify.sh：上一版還沒有 services/db/（現在的 main）→ 略過升級比對"
r=$(new_repo v1)
commit "$r" "base without services/db" base
add_db "$r"
commit "$r" "add services/db" head
verify "$r"
expect_rc 0
expect_has "略過升級比對"
expect_has "OK 這個分支的 schema.sql＝myapp/tests/fixtures/schema.sql（結構）"
expect_has "OK 新建的資料庫跑過 runner 之後結構不變"

t "verify.sh：上一版有 services/db/，這個分支多一個 migration（schema.sql、fixture 都更新了）"
r=$(new_repo v2)
add_db "$r"
commit "$r" "base with services/db" base
cp "$here/migrations/008_test_note_table.sql" "$r/services/db/migrations/"
add_note_table_to_schemas "$r"
commit "$r" "add 008" head
verify "$r"
expect_rc 0
expect_has "新增的 migration： 008_test_note_table.sql"
expect_has "OK 上一版的 runner 套用上一版的 migration（result=baseline）"
expect_has "OK 這個分支的 runner 套用新增的 migration（result=applied）"
expect_has "OK 再跑一次什麼都不做（result=up-to-date）"
expect_has "OK migrations/008_test_note_table.sql 直接再執行一次也成功"
expect_has "OK adam_app、adam_analysis 的權限沒有少"
expect_has "OK 升級後的結構＝這個分支的 schema.sql"
expect_has "OK 新建的資料庫：runner 第一次 apply（基準檢查，重跑 008 以後的）（result=applied）"

t "verify.sh：多了 migration 但 schema.sql 沒有跟著改 → 失敗，印出差異"
r=$(new_repo v3)
add_db "$r"
commit "$r" "base with services/db" base
cp "$here/migrations/008_test_note_table.sql" "$r/services/db/migrations/"
commit "$r" "add 008 only" head
verify "$r"
expect_rc 1
expect_has "FAIL 升級後的結構＝這個分支的 schema.sql"
expect_has "+table|tbl_test_note|"

t "verify.sh：DROP PROCEDURE＋CREATE 只 GRANT 給 adam_app → adam_analysis 少了 EXECUTE → 失敗"
r=$(new_repo v4)
add_db "$r"
commit "$r" "base with services/db" base
{
    printf '%s\n' "SET NAMES utf8mb4;" "USE adam;" "DELIMITER \$\$" "DROP PROCEDURE IF EXISTS sp_insert_text_storage \$\$"
    sed -n '/^CREATE PROCEDURE sp_insert_text_storage(/,/^END \$\$/p' "$db_dir/schema.sql"
    printf '%s\n' "DELIMITER ;" "GRANT EXECUTE ON PROCEDURE adam.sp_insert_text_storage TO 'adam_app'@'%';"
} >"$r/services/db/migrations/008_test_regrant_one.sql"
commit "$r" "add 008 regrant to one account" head
verify "$r"
expect_rc 1
expect_has "沒有破壞性的語句" "lint 看得到 GRANT，不算破壞性的"
expect_has "FAIL 套用新的 migration 之後，adam_app／adam_analysis 少了這些權限"
expect_has "routine|adam_analysis|adam|PROCEDURE|sp_insert_text_storage|Execute"
expect_has "OK 升級後的結構＝這個分支的 schema.sql" "程序的內容一樣，結構比對照樣過"

t "verify.sh：靜態檢查（改了已經有的 migration、新增破壞性的、schema.sql 建帳號、編號太小）→ 失敗，不開 MySQL"
r=$(new_repo v5)
add_db "$r"
cp "$here/migrations/008_test_note_table.sql" "$r/services/db/migrations/"
commit "$r" "base with services/db and 008" base
printf '%s\n' "-- 改一個字" >>"$r/services/db/migrations/007_analysis_queue.sql"
cp "$here/migrations/011_test_drop_column.sql" "$r/services/db/migrations/009_test_drop_column.sql"
{ cat "$here/lint/declared.sql"; printf '%s\n' "ALTER TABLE tbl_test_note DROP COLUMN note_extra;"; } >"$r/services/db/migrations/010_test_declared.sql"
cp "$here/migrations/012_test_add_column.sql" "$r/services/db/migrations/005_test_too_small.sql"
printf '%s\n' "INSERT INTO tbl_account (account_id) VALUES ('x');" >>"$r/services/db/schema.sql"
commit "$r" "bad changes" head
verify "$r"
expect_rc 1
expect_has "上一版的 migrations/007_analysis_queue.sql 內容被改了"
expect_has "migrations/009_test_drop_column.sql 有破壞性的語句"
expect_has "migrations/010_test_declared.sql 標了 -- migrate: destructive"
expect_lacks "FAIL migrations/010_test_declared.sql" "標了的不算失敗"
expect_has "編號重複" "005 重複"
expect_has "schema.sql 不能建帳號"
expect_has "不跑資料庫的部分"
expect_lacks "啟動 mysql" "沒有開 MySQL"

t "verify.sh：結構比對抓得到型別、NULL、預設值、定序、索引、外鍵、CHECK、參數、程序內容；註解和空白不算"
r=$(new_repo v6)
commit "$r" "base without services/db" base
add_db "$r"
f=$r/myapp/tests/fixtures/schema.sql
sed -e "s/account_level           CHAR(1)      NOT NULL DEFAULT '1',/account_level           CHAR(1)      NOT NULL DEFAULT '2',/" \
    -e "s/service_todos         TEXT         NULL,/service_todos         MEDIUMTEXT   NULL,/" \
    -e "s/text_name        VARCHAR(255)    NOT NULL,/text_name        VARCHAR(255)    NULL,/" \
    -e "s/CONSTRAINT chk_service_status CHECK (service_status IN (1, 2, 3)),/CONSTRAINT chk_service_status CHECK (service_status IN (1, 2, 3, 4)),/" \
    -e "s/KEY idx_service_start (service_start),/KEY idx_service_start (service_start, service_status),/" \
    -e "s/COLLATE utf8mb4_0900_bin NOT NULL,/COLLATE utf8mb4_bin NOT NULL,/" \
    -e "s/IN p_username VARCHAR(64) CHARACTER SET utf8mb4/IN p_username VARCHAR(65) CHARACTER SET utf8mb4/" \
    -e "/CREATE PROCEDURE sp_get_account_name(/,/END [\$][\$]/s/WHERE a.account_id = p_account_id;/WHERE a.account_id = p_account_id LIMIT 1;/" \
    -e "/CONSTRAINT fk_text_service FOREIGN KEY/,/ON DELETE/s/ON DELETE CASCADE/ON DELETE RESTRICT/" \
    -e "/CREATE PROCEDURE sp_get_account_password(/,/END [\$][\$]/s/    FROM tbl_account AS a/      FROM   tbl_account    AS a  # 只是註解和空白/" \
    -e "s/COMMENT = '帳號。/COMMENT = '帳號（只改了註解）。/" \
    "$f" >"$f.new" && mv "$f.new" "$f"
commit "$r" "fixture drifted" head
verify "$r"
expect_rc 1
bt='`'
for want in "+column|tbl_account|005|account_level|char(1)|NO|2|" \
            "+column|tbl_service|007|service_todos|mediumtext|" \
            "+column|tbl_text_storage|003|text_name|varchar(255)|YES|" \
            "+column|tbl_account|003|account_password|varchar(255)|NO|<null>|utf8mb4|utf8mb4_bin|" \
            "+check|tbl_service|chk_service_status|YES|(${bt}service_status${bt} in (1,2,3,4))" \
            "+index|tbl_service|idx_service_start|1|02|service_status|" \
            "+fk|tbl_text_storage|fk_text_service|service_id|1|tbl_service|service_id|RESTRICT|RESTRICT|" \
            "+param|PROCEDURE|sp_get_login|01|IN|p_username|varchar(65)|" \
            "+routine-body|PROCEDURE|sp_get_account_name|"; do
    expect_has "$want"
done
expect_lacks "+routine-body|PROCEDURE|sp_get_account_password|" "程序裡只改註解和空白不算"
expect_lacks "-routine-body|PROCEDURE|sp_get_account_password|" "程序裡只改註解和空白不算（另一邊）"
expect_lacks "+table|tbl_account|" "資料表的 COMMENT 不算"

# =============================================================================
t "結果"
if grep -q -F -- "$mig_pw" "$outputs"; then bad "輸出裡有 adam_migrator 的密碼"; else ok "所有輸出都沒有 adam_migrator 的密碼"; fi
say "通過 $passed 項，失敗 $failed 項"
[ "$failed" -eq 0 ]
