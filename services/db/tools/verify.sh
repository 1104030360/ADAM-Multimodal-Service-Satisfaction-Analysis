#!/usr/bin/env bash
# =============================================================================
# tools/verify.sh：CI 的 migration 驗證（CD 計畫 9-2 第 5 點、CONTRACTS 第 5 節）。
# 用法（需要 docker、mysql:8.4 映像檔、完整的 git 歷史）：
#   bash services/db/tools/verify.sh --base-ref origin/main
# 「這個分支」是工作目錄裡的檔案（CI 裡就是 PR 的 merge commit）；「上一版」是 --base-ref 那個 commit。
# 檢查（任何一項沒過結束碼 1；用法錯誤 2）：
#   1. 靜態：migration 的檔名、編號；上一版已經有的 migration 不能改、不能刪；新增的編號要比上一版的都大；
#      新增的不能有破壞性的語句（檔案標了 `-- migrate: destructive` 的照樣列出來，但不算失敗：部署時 runner 會停下來等人確認）；
#      schema.sql 不能建帳號。
#   2. 升級（上一版有 services/db/ 才做；沒有就略過）：拋棄式 MySQL 8.4 → 上一版的 schema.sql＋正式環境的帳號和權限
#      → 上一版的 runner 套用上一版的 migration → 這個分支的 runner 套用新增的，再跑一次要什麼都不做 → 新增的直接再執行
#      一次（要可以重跑）→ adam_app、adam_analysis 的權限沒有少 → 結構和這個分支的 schema.sql 建出來的一樣。
#   3. 新建：這個分支的 schema.sql 建的資料庫，這個分支的 runner apply 兩次都要成功（基準檢查要過，008 以後的要能在
#      已經有的結構上重跑）而且結構不變；schema.sql 和 myapp/tests/fixtures/schema.sql 建出來的結構一樣。
# 結構比對：tools/schema_dump.sql＋lib/sqlscan.awk（說明在 schema_dump.sql 的開頭）。
# 容器名稱的前綴是 ADAM_DB_TEST_PREFIX（預設 adam-db-test-）；不對外開 port，結束時刪掉。
# =============================================================================
set -u
set -o pipefail
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
db_dir=$(cd "$here/.." && pwd)
prefix=${ADAM_DB_TEST_PREFIX:-adam-db-test-}
image=${MYSQL_IMAGE:-mysql:8.4}
root_pw=adam-test-only-root
admin_pw=adam-test-only-admin
mig_pw=adam-test-only-migrator      # 和 test_accounts.sql 一樣
container=
work=
failures=0

say() { printf '%s\n' "$*"; }
fail() { say "verify: FAIL $*"; failures=$((failures + 1)); }
die() { say "verify: $*"; finish 1; }

cleanup() {
    if [ -n "$container" ]; then docker rm -f -v "$container" >/dev/null 2>&1; container=; fi
    if [ -n "$work" ]; then rm -rf "$work"; work=; fi
}
finish() {
    cleanup
    if [ "$1" -eq 0 ]; then say "verify: result=ok"; else say "verify: result=failed code=$1"; fi
    exit "$1"
}
trap 'cleanup' EXIT
trap 'say "verify: 被中斷"; finish 1' INT TERM

usage() {
    say "用法：bash services/db/tools/verify.sh --base-ref <git ref>（例如 origin/main）"
    finish 2
}

base_ref=
while [ $# -gt 0 ]; do
    case $1 in
        --base-ref) [ $# -ge 2 ] || usage; base_ref=$2; shift 2 ;;
        --base-ref=*) base_ref=${1#--base-ref=}; shift ;;
        -h|--help) usage ;;
        *) say "verify: 不認得的參數：$1"; usage ;;
    esac
done
[ -n "$base_ref" ] || usage

root=$(git -C "$db_dir" rev-parse --show-toplevel 2>/dev/null) || die "$db_dir 不在 git repo 裡"
# services/db 在 repo 裡的路徑（用 git 算：macOS 的 /var 是 /private/var 的連結，字串相減會錯）
rel=$(git -C "$db_dir" rev-parse --show-prefix)
rel=${rel%/}
base=$(git -C "$root" rev-parse --verify --quiet "$base_ref^{commit}") || {
    say "verify: 找不到 --base-ref $base_ref（CI 要 checkout 完整的歷史：fetch-depth: 0）"
    finish 2
}
fixture=$root/myapp/tests/fixtures/schema.sql
[ -r "$fixture" ] || die "找不到 $fixture"
work=$(mktemp -d "${TMPDIR:-/tmp}/adam-db-verify.XXXXXX") || die "建不了暫存資料夾"
chmod 755 "$work"
say "verify: 上一版 $base_ref（${base:0:12}），這個分支＝工作目錄 $root"

# =============================================================================
# 1. 靜態檢查
# =============================================================================
name_re='^([0-9]{3})_[a-z0-9_]+\.sql$'
head_files=
for f in "$db_dir"/migrations/*.sql; do
    [ -e "$f" ] || continue
    b=${f##*/}
    if [[ $b =~ $name_re ]]; then head_files="$head_files $b"; else fail "檔名不對：migrations/$b（要像 008_add_something.sql）"; fi
done
dups=$(for b in $head_files; do printf '%s\n' "${b:0:3}"; done | sort | uniq -d | tr '\n' ' ')
[ -z "$dups" ] || fail "migration 的編號重複：$dups"

base_has_db=0
if git -C "$root" cat-file -e "$base:$rel/migrate.sh" 2>/dev/null; then base_has_db=1; fi
base_files=
if [ "$base_has_db" = 1 ]; then
    base_files=$(git -C "$root" ls-tree --name-only "$base" "$rel/migrations/" | sed 's#.*/##' | grep -E "$name_re" | tr '\n' ' ')
fi

max_base=0
for b in $base_files; do
    n=$((10#${b:0:3}))
    [ "$n" -gt "$max_base" ] && max_base=$n
    if [ ! -e "$db_dir/migrations/$b" ]; then
        fail "上一版的 migrations/$b 被刪掉或改名了：已經套用的 migration 不能動"
    elif ! git -C "$root" show "$base:$rel/migrations/$b" | cmp -s - "$db_dir/migrations/$b"; then
        fail "上一版的 migrations/$b 內容被改了：正式環境記錄了它的 SHA-256，部署會停在結束碼 4。要改就新增一個 migration"
    fi
done

new_files=
for b in $head_files; do
    case " $base_files " in *" $b "*) continue ;; esac
    n=$((10#${b:0:3}))
    if [ "$base_has_db" = 0 ] && [ "$n" -le 7 ]; then continue; fi   # 001～007：runner 之前手動套用的基準
    new_files="$new_files $b"
    if [ "$base_has_db" = 1 ] && [ "$n" -le "$max_base" ]; then
        fail "新增的 migrations/$b 的編號不比上一版最大的 $(printf %03d "$max_base") 大：改成更大的編號"
    fi
done
say "verify: 新增的 migration：${new_files:- （沒有）}"

if grep -n -i -E "insert[[:space:]]+(ignore[[:space:]]+)?into[[:space:]]+\`?tbl_account|placeholder_(admin|staff)" "$db_dir/schema.sql" \
        | grep -v -E '^[0-9]+:[[:space:]]*--' >"$work/accounts.txt"; then
    fail "schema.sql 不能建帳號（新建的正式資料庫不能有公開密碼的帳號）："
    sed 's/^/  /' "$work/accounts.txt"
fi

declared_new=
if [ -n "$new_files" ]; then
    lint_args=()
    for b in $new_files; do lint_args+=("/head-db/migrations/$b"); done
    docker run --rm -e LC_ALL=C -v "$db_dir:/head-db:ro" --entrypoint bash "$image" \
        /head-db/migrate.sh lint "${lint_args[@]}" >"$work/lint.txt" 2>&1
    lint_rc=$?
    case $lint_rc in
        0) say "verify: 新增的 migration 沒有破壞性的語句" ;;
        3)
            for b in $new_files; do
                grep -F "destructive: $b:" "$work/lint.txt" >"$work/lint.one" || continue
                if grep -q -i -E '^[[:space:]]*--[[:space:]]*migrate:[[:space:]]*destructive([^a-z_]|$)' "$db_dir/migrations/$b"; then
                    declared_new="$declared_new $b"
                    say "verify: migrations/$b 標了 -- migrate: destructive（部署時會停下來，要手動允許）："
                    sed 's/^/  /' "$work/lint.one"
                else
                    fail "migrations/$b 有破壞性的語句（README「破壞性的語句」）。真的要刪，等用不到的版本上線之後再加，並在檔案裡標 -- migrate: destructive："
                    sed 's/^/  /' "$work/lint.one"
                fi
            done ;;
        *) fail "lint 失敗（結束碼 $lint_rc）："; sed 's/^/  /' "$work/lint.txt" ;;
    esac
fi

if [ "$failures" -gt 0 ]; then
    say "verify: 靜態檢查有 $failures 項沒過，不跑資料庫的部分"
    finish 1
fi

# =============================================================================
# 拋棄式 MySQL
# =============================================================================
if [ "$base_has_db" = 1 ]; then
    mkdir -p "$work/base"
    git -C "$root" archive "$base" "$rel" | tar -x -C "$work/base" || die "取不出上一版的 $rel"
    chmod -R a+rX "$work/base"
    base_db=$work/base/$rel
else
    mkdir -p "$work/base-empty"
    base_db=$work/base-empty
fi
cp "$fixture" "$work/fixture.sql"
chmod a+r "$work/fixture.sql"

container=${prefix}verify-$$
say "verify: 啟動 $image（$container）"
docker run -d --name "$container" -e MYSQL_ROOT_PASSWORD=$root_pw \
    -v "$db_dir:/head-db:ro" -v "$base_db:/base-db:ro" -v "$work/fixture.sql:/fixture.sql:ro" \
    "$image" >/dev/null || die "docker run 失敗"

ready=0
for _ in $(seq 1 90); do
    if docker exec -e MYSQL_PWD=$root_pw "$container" mysql -uroot --protocol=TCP -h127.0.0.1 -N -e 'SELECT 1' >/dev/null 2>&1; then
        ready=1; break
    fi
    sleep 2
done
[ "$ready" = 1 ] || { docker logs "$container" 2>&1 | tail -20; die "MySQL 沒有起來"; }

root_sql() { docker exec -e MYSQL_PWD=$root_pw "$container" mysql -uroot --protocol=TCP -h127.0.0.1 -N -B -e "$1"; }
as_admin() {   # as_admin <容器裡的檔案>：用 adam_admin（像 RDS 的 master）執行
    docker exec -e MYSQL_PWD=$admin_pw "$container" sh -c \
        'mysql --protocol=TCP -h127.0.0.1 -uadam_admin --default-character-set=utf8mb4 < "$1"' sh "$1"
}
as_migrator() {   # as_migrator <容器裡的檔案>：用 adam_migrator 直接執行
    docker exec -e MYSQL_PWD=$mig_pw "$container" sh -c \
        'mysql --protocol=TCP -h127.0.0.1 -uadam_migrator --ssl-mode=REQUIRED --default-character-set=utf8mb4 adam < "$1"' sh "$1"
}
runner() {   # runner <base|head> <指令…>：在容器裡跑那一版的 migrate.sh（adam_migrator、TLS REQUIRED）
    local which=$1; shift
    docker exec -e DB_HOST=127.0.0.1 -e DB_PASSWORD=$mig_pw -e DB_SSL_MODE=REQUIRED -e LC_ALL=C \
        "$container" bash "/$which-db/migrate.sh" "$@"
}
dump() {   # dump <輸出檔>：目前 adam 的結構（不含 tbl_schema_migration）
    docker exec -e MYSQL_PWD=$root_pw -e LC_ALL=C "$container" sh -c \
        'mysql -uroot --protocol=TCP -h127.0.0.1 --raw -N -B adam < /head-db/tools/schema_dump.sql | awk -v mode=dump -f /head-db/lib/sqlscan.awk' \
        | sort >"$1"
}
grants() {   # grants <輸出檔>：adam_app、adam_analysis 的權限，一個權限一行
    root_sql "SELECT CONCAT_WS('|', 'routine', User, Db, Routine_type, LOWER(Routine_name), Proc_priv)
                FROM mysql.procs_priv WHERE User IN ('adam_app', 'adam_analysis')
              UNION ALL
              SELECT CONCAT_WS('|', 'table', User, Db, Table_name, Table_priv)
                FROM mysql.tables_priv WHERE User IN ('adam_app', 'adam_analysis') AND Table_priv <> ''
              UNION ALL
              SELECT CONCAT_WS('|', 'column', User, Db, CONCAT(Table_name, '.', Column_name), Column_priv)
                FROM mysql.columns_priv WHERE User IN ('adam_app', 'adam_analysis')
              UNION ALL
              SELECT CONCAT_WS('|', 'schema', User, Db, '*', REPLACE(CONCAT_WS(',',
                       IF(Select_priv = 'Y', 'Select', NULL), IF(Insert_priv = 'Y', 'Insert', NULL),
                       IF(Update_priv = 'Y', 'Update', NULL), IF(Delete_priv = 'Y', 'Delete', NULL),
                       IF(Execute_priv = 'Y', 'Execute', NULL)), ' ', ''))
                FROM mysql.db WHERE User IN ('adam_app', 'adam_analysis')" \
        | awk -F'|' '{ n = split($NF, p, ","); for (i = 1; i <= n; i++) if (p[i] != "") { $NF = p[i]; print } }' OFS='|' \
        | sort -u >"$1"
}
compare() {   # compare <說明> <預期的檔案> <實際的檔案>
    if cmp -s "$2" "$3"; then say "verify: OK $1"; return 0; fi
    fail "$1（- 是預期的，+ 是實際的）："
    diff -u "$2" "$3" | sed -n '3,300p' | sed 's/^/  /'
    return 1
}
expect_result() {   # expect_result <說明> <runner 的輸出檔> <結束碼> <預期的 result>
    local rc=$3 got
    got=$(sed -n 's/^migrate: result=\([^ ]*\) code=.*/\1/p' "$2" | tail -1)
    if [ "$rc" -eq 0 ] && { [ -z "$4" ] || [ "$got" = "$4" ]; }; then
        say "verify: OK $1（result=$got）"
        return 0
    fi
    fail "$1：結束碼 $rc、result=$got（預期 0${4:+、$4}）："
    sed 's/^/  /' "$2"
    return 1
}

if ! root_sql "SELECT 1" >/dev/null || ! docker exec -i -e MYSQL_PWD=$root_pw "$container" \
        mysql -uroot --protocol=TCP -h127.0.0.1 < "$here/test_accounts.sql"; then
    die "建不了測試用的帳號"
fi

allow=()
[ -n "$declared_new" ] && allow=(--allow-destructive)

# =============================================================================
# 2. 升級：上一版 → 這個分支
# =============================================================================
if [ "$base_has_db" = 1 ]; then
    say "verify: 升級：上一版的 schema.sql＋runner，再套用這個分支新增的 migration"
    as_admin /base-db/schema.sql || die "上一版的 schema.sql 載入失敗"
    as_admin /head-db/tools/test_grants.sql || die "test_grants.sql 失敗"
    runner base apply --allow-destructive >"$work/base-apply.txt" 2>&1
    expect_result "上一版的 runner 套用上一版的 migration" "$work/base-apply.txt" $? "" || finish 1
    grants "$work/grants-before.txt"
    want=up-to-date
    [ -n "$new_files" ] && want=applied
    runner head apply ${allow[@]+"${allow[@]}"} >"$work/head-apply1.txt" 2>&1
    expect_result "這個分支的 runner 套用新增的 migration" "$work/head-apply1.txt" $? "$want" || finish 1
    sed 's/^/  /' "$work/head-apply1.txt" | grep -E '^  (apply|pending|destructive|migrate: 允許):' || true
    runner head apply ${allow[@]+"${allow[@]}"} >"$work/head-apply2.txt" 2>&1
    expect_result "再跑一次什麼都不做" "$work/head-apply2.txt" $? up-to-date
    for b in $new_files; do
        if as_migrator "/head-db/migrations/$b" >"$work/rerun.txt" 2>&1; then
            say "verify: OK migrations/$b 直接再執行一次也成功（可以重跑）"
        else
            fail "migrations/$b 不能重跑（第二次執行失敗；README「寫 migration 的規則」第 2 點）："
            sed 's/^/  /' "$work/rerun.txt"
        fi
    done
    grants "$work/grants-after.txt"
    comm -23 "$work/grants-before.txt" "$work/grants-after.txt" >"$work/grants-lost.txt"
    if [ -s "$work/grants-lost.txt" ]; then
        fail "套用新的 migration 之後，adam_app／adam_analysis 少了這些權限（DROP PROCEDURE 之後沒有重新 GRANT？）："
        sed 's/^/  /' "$work/grants-lost.txt"
    else
        say "verify: OK adam_app、adam_analysis 的權限沒有少（$(grep -c . "$work/grants-before.txt") 項 → $(grep -c . "$work/grants-after.txt") 項）"
    fi
    dump "$work/upgraded.txt"
    root_sql "DROP DATABASE adam" || die "DROP DATABASE 失敗"
else
    say "verify: 上一版（$base_ref）還沒有 $rel/：略過升級比對（CONTRACTS 第 5 節）"
fi

# =============================================================================
# 3. 新建：這個分支的 schema.sql
# =============================================================================
as_admin /head-db/schema.sql || die "這個分支的 schema.sql 載入失敗"
as_admin /head-db/tools/test_grants.sql || die "test_grants.sql 失敗"
dump "$work/schema.txt"
say "verify: 這個分支的 schema.sql：$(grep -c '^table|' "$work/schema.txt") 張表、$(grep -c '^routine|' "$work/schema.txt") 個程序"
if [ "$base_has_db" = 1 ]; then
    compare "升級後的結構＝這個分支的 schema.sql" "$work/schema.txt" "$work/upgraded.txt"
fi
runner head apply ${allow[@]+"${allow[@]}"} >"$work/fresh-apply1.txt" 2>&1
expect_result "新建的資料庫：runner 第一次 apply（基準檢查，重跑 008 以後的）" "$work/fresh-apply1.txt" $? ""
runner head apply ${allow[@]+"${allow[@]}"} >"$work/fresh-apply2.txt" 2>&1
expect_result "新建的資料庫：再跑一次什麼都不做" "$work/fresh-apply2.txt" $? up-to-date
dump "$work/schema-after-runner.txt"
compare "新建的資料庫跑過 runner 之後結構不變" "$work/schema.txt" "$work/schema-after-runner.txt"

root_sql "DROP DATABASE adam" || die "DROP DATABASE 失敗"
as_admin /fixture.sql || die "myapp/tests/fixtures/schema.sql 載入失敗"
dump "$work/fixture.txt"
compare "這個分支的 schema.sql＝myapp/tests/fixtures/schema.sql（結構）" "$work/schema.txt" "$work/fixture.txt"

if [ "$failures" -gt 0 ]; then
    say "verify: $failures 項沒過"
    finish 1
fi
finish 0
