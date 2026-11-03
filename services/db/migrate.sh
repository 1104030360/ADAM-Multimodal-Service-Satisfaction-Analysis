#!/usr/bin/env bash
# =============================================================================
# migrate.sh：ADAM 資料庫 migration 的 runner（adam-db-migrations 映像檔的 ENTRYPOINT；CD 計畫第 9 節）。
#   status                      只讀：已套用的、待套用的、待套用裡哪些是破壞性的
#   apply [--allow-destructive] 第一次先做基準檢查（001～007）並記錄；之後照編號只跑待套用的（先檢查完全部再動手）
#   lint [檔案…]                不連資料庫：列出 migration 檔裡破壞性的語句（CI 的 tools/verify.sh 用）
# 連線設定只從環境變數來：DB_HOST、DB_PASSWORD（必要），DB_PORT、DB_NAME、DB_USER、DB_SSL_MODE、DB_SSL_CA、
#   MIGRATE_LOCK_WAIT。密碼只用 MYSQL_PWD 交給 mysql 客戶端，不會出現在任何程序的 argv、log 裡。
# 輸出一行一件事，最後一行是 `migrate: result=<字> code=<數字>`。結束碼、規則：services/db/README.md。
# 要用 bash 3.2 也跑得動（macOS 的 /bin/bash）：不用關聯陣列、mapfile、${x,,}。
# =============================================================================
set -u
set -o pipefail
export LC_ALL=C

script_dir=$(cd "$(dirname "$0")" && pwd)
MIGRATIONS_DIR=${MIGRATIONS_DIR:-$script_dir/migrations}
BASELINE_SQL=$script_dir/baseline/check_001_007.sql
SCAN_AWK=$script_dir/lib/sqlscan.awk
DEFAULT_SSL_CA=$script_dir/certs/rds-global-bundle.pem
readonly BASELINE_LAST=7          # 001～007：runner 之前手動套用的，第一次執行時只檢查、記錄，不重跑
readonly RECORD_TABLE=tbl_schema_migration

tmp=
cleaned=0
lock_open=0
lock_pid=
child_pid=

say() { printf '%s\n' "$*"; }

# 把 mysql 的錯誤訊息印出來（每行加上前綴）；遮掉主機名稱和密碼（GitHub 上的 log 是公開的）
show_err() {
    local f=$1 line host_pat=${DB_HOST:-} pw_pat=${DB_PASSWORD:-}
    [ -s "$f" ] || return 0
    case $host_pat in *[\*\?\[]*) host_pat= ;; esac
    case $pw_pat in *[\*\?\[]*) pw_pat= ;; esac
    while IFS= read -r line || [ -n "$line" ]; do
        case $line in *"Using a password on the command line"*) continue ;; esac
        [ "${#host_pat}" -ge 6 ] && line=${line//$host_pat/<DB_HOST>}
        [ "${#pw_pat}" -ge 4 ] && line=${line//$pw_pat/<DB_PASSWORD>}
        say "  mysql: $line"
    done <"$f"
}

cleanup() {
    [ "$cleaned" = 1 ] && return 0
    cleaned=1
    if [ -n "$child_pid" ]; then kill "$child_pid" 2>/dev/null; child_pid=; fi
    release_lock
    if [ -n "$tmp" ]; then rm -rf "$tmp"; fi
    return 0
}

finish() {   # finish <結束碼> <結果>
    cleanup
    say "migrate: result=$2 code=$1"
    exit "$1"
}

usage_error() {
    say "migrate: $*"
    say "migrate: 用法：migrate.sh status | apply [--allow-destructive] | lint [檔案…]（說明見 services/db/README.md）"
    finish 2 usage
}

# shellcheck disable=SC2329  # 由下面的 trap 呼叫
on_signal() {
    say "migrate: 收到 $1，停下來。這一輪已經成功的照樣記錄了；正在跑的那一個沒有記錄，下次會重跑（migration 都要可以重跑）"
    finish 1 interrupted
}
trap 'on_signal SIGTERM' TERM
trap 'on_signal SIGINT' INT
trap 'cleanup' EXIT

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

now_ms() {
    local t
    t=$(date +%s%3N 2>/dev/null)
    case $t in *[!0-9]*|'') t=$(( $(date +%s) * 1000 )) ;; esac
    printf '%s\n' "$t"
}

pad3() { printf '%03d' "$1"; }

# ---- 設定 -------------------------------------------------------------------------
check_config() {
    DB_HOST=${DB_HOST:-}
    DB_PASSWORD=${DB_PASSWORD:-}
    DB_PORT=${DB_PORT:-3306}
    DB_NAME=${DB_NAME:-adam}
    DB_USER=${DB_USER:-adam_migrator}
    DB_SSL_MODE=${DB_SSL_MODE:-VERIFY_IDENTITY}
    DB_SSL_CA=${DB_SSL_CA:-$DEFAULT_SSL_CA}
    LOCK_WAIT=${MIGRATE_LOCK_WAIT:-60}
    [ -n "$DB_HOST" ] || usage_error "少了 DB_HOST"
    [ -n "$DB_PASSWORD" ] || usage_error "少了 DB_PASSWORD（只從環境變數拿）"
    case $DB_PORT in ''|*[!0-9]*) usage_error "DB_PORT 要是數字：$DB_PORT" ;; esac
    case $LOCK_WAIT in ''|*[!0-9]*) usage_error "MIGRATE_LOCK_WAIT 要是 0 以上的整數（秒）：$LOCK_WAIT" ;; esac
    local re='^[A-Za-z0-9_]{1,40}$'
    [[ $DB_NAME =~ $re ]] || usage_error "DB_NAME 只能有英數字和底線：$DB_NAME"
    DB_SSL_MODE=$(printf '%s' "$DB_SSL_MODE" | tr '[:lower:]' '[:upper:]')
    case $DB_SSL_MODE in
        DISABLED|PREFERRED|REQUIRED) ;;
        VERIFY_CA|VERIFY_IDENTITY)
            [ -r "$DB_SSL_CA" ] || usage_error "DB_SSL_MODE=$DB_SSL_MODE 要 CA 憑證，讀不到 DB_SSL_CA=$DB_SSL_CA" ;;
        *) usage_error "DB_SSL_MODE 要是 DISABLED、PREFERRED、REQUIRED、VERIFY_CA、VERIFY_IDENTITY 其中之一：$DB_SSL_MODE" ;;
    esac
    LOCK_NAME="adam_schema_migration:$DB_NAME"
    mysql_opts=(--no-defaults --protocol=TCP "--host=$DB_HOST" "--port=$DB_PORT" "--user=$DB_USER"
                --default-character-set=utf8mb4 --connect-timeout=10 "--ssl-mode=$DB_SSL_MODE")
    case $DB_SSL_MODE in VERIFY_CA|VERIFY_IDENTITY) mysql_opts+=("--ssl-ca=$DB_SSL_CA") ;; esac
    commit_sql=NULL
    re='^[0-9a-f]{7,40}$'
    if [[ ${MIGRATE_SOURCE_COMMIT:-} =~ $re ]]; then commit_sql="'$MIGRATE_SOURCE_COMMIT'"; fi
}

# db [mysql 的選項…]：連 DB_NAME。密碼只經由環境變數 MYSQL_PWD；3>&- 讓子程序不要拿著鎖連線的管線
db() {
    MYSQL_PWD=$DB_PASSWORD mysql "${mysql_opts[@]}" "$@" "$DB_NAME" 3>&-
}

# ---- migration 檔 ------------------------------------------------------------------
# file_name[編號]、file_sha[編號]、file_nums（照編號排好的編號清單）
load_files() {
    local f base num re='^([0-9]{3})_[a-z0-9_]+\.sql$'
    file_nums=
    [ -d "$MIGRATIONS_DIR" ] || usage_error "找不到 migration 的資料夾：$MIGRATIONS_DIR"
    for f in "$MIGRATIONS_DIR"/*; do
        [ -e "$f" ] || continue
        base=${f##*/}
        case $base in *.sql) ;; *) continue ;; esac
        [[ $base =~ $re ]] || usage_error "migration 的檔名不對：$base（要像 008_add_something.sql：三位數編號、底線、小寫英數字和底線）"
        num=$((10#${BASH_REMATCH[1]}))
        [ "$num" -ge 1 ] || usage_error "migration 的編號從 001 開始：$base"
        if [ -n "${file_name[$num]:-}" ]; then usage_error "migration 的編號重複：${file_name[$num]} 和 $base"; fi
        file_name[num]=$base
        file_sha[num]=$(sha256_of "$f") || usage_error "讀不到 $base"
        file_nums="$file_nums $num"
    done
}

# ---- 破壞性的語句 -----------------------------------------------------------------------
# scan <檔案> <輸出檔>：有破壞性的語句（或檔案標了 -- migrate: destructive）回傳 1
scan() {
    if ! awk -v mode=check -f "$SCAN_AWK" "$1" >"$2"; then
        say "migrate: 檢查 ${1##*/} 的語句時出錯"
        finish 1 error
    fi
    [ ! -s "$2" ]
}

print_scan() {   # print_scan <前綴> <檔名> <scan 的輸出檔>
    local line no reason stmt
    while IFS= read -r line; do
        no=${line%%$'\t'*}; line=${line#*$'\t'}
        reason=${line%%$'\t'*}; stmt=${line#*$'\t'}
        if [ "$reason" = declared ]; then say "$1: $2:$no: $stmt"
        else say "$1: $2:$no: $reason｜$stmt"; fi
    done <"$3"
}

cmd_lint() {
    local f files=() bad=0 n=0
    if [ $# -eq 0 ]; then
        load_files
        for n in $file_nums; do files+=("$MIGRATIONS_DIR/${file_name[$n]}"); done
    else
        files=("$@")
    fi
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/migrate.XXXXXX") || finish 1 error
    if [ "${#files[@]}" -eq 0 ]; then
        say "migrate: 沒有 migration 檔"
        finish 0 clean
    fi
    n=0
    for f in "${files[@]}"; do
        [ -r "$f" ] || usage_error "讀不到 $f"
        n=$((n + 1))
        if ! scan "$f" "$tmp/scan"; then
            bad=$((bad + 1))
            print_scan destructive "${f##*/}" "$tmp/scan"
        fi
    done
    say "migrate: 檢查了 $n 個檔案，$bad 個有破壞性的語句"
    [ "$bad" -eq 0 ] && finish 0 clean
    finish 3 destructive
}

# ---- 連線與鎖 ------------------------------------------------------------------------
connect() {
    if ! db -N -B -e "SELECT CURRENT_USER(), VERSION()" >"$tmp/probe" 2>"$tmp/err"; then
        say "migrate: 連不上資料庫 $DB_NAME（帳號 $DB_USER，TLS $DB_SSL_MODE）"
        show_err "$tmp/err"
        finish 7 no-connection
    fi
    local cu ver
    IFS=$'\t' read -r cu ver <"$tmp/probe"
    say "migrate: 連上資料庫 $DB_NAME（帳號 $cu，MySQL $ver，TLS $DB_SSL_MODE）"
}

# 用一條一直開著的連線拿 GET_LOCK：mysql 在背景讀 FIFO，這個 shell 拿著寫的那一端（fd 3）。
# 這個 shell 結束（包括被 kill）時寫的那一端關掉，mysql 讀到 EOF 就結束，連線斷了鎖就放掉。
acquire_lock() {
    mkfifo "$tmp/lock.in" || finish 1 error
    db -N -B --unbuffered <"$tmp/lock.in" >"$tmp/lock.out" 2>"$tmp/lock.err" &
    lock_pid=$!
    exec 3>"$tmp/lock.in"
    lock_open=1
    say "migrate: 等鎖 $LOCK_NAME（最多 $LOCK_WAIT 秒；同一時間只有一個 runner）"
    printf "SELECT COALESCE(GET_LOCK('%s', %d), -1);\n" "$LOCK_NAME" "$LOCK_WAIT" >&3
    local tries=0 limit=$(( (LOCK_WAIT + 30) * 5 )) got
    while [ ! -s "$tmp/lock.out" ]; do
        if ! kill -0 "$lock_pid" 2>/dev/null; then
            [ -s "$tmp/lock.out" ] && break
            say "migrate: 拿鎖的連線斷了"
            show_err "$tmp/lock.err"
            finish 7 no-connection
        fi
        tries=$((tries + 1))
        if [ "$tries" -gt "$limit" ]; then
            say "migrate: 資料庫一直沒有回應 GET_LOCK"
            finish 6 lock-timeout
        fi
        sleep 0.2
    done
    IFS= read -r got <"$tmp/lock.out"
    case $got in
        1) say "migrate: 拿到鎖" ;;
        0) say "migrate: 等了 $LOCK_WAIT 秒還拿不到鎖：另一個 runner 正在跑。什麼都沒做"
           finish 6 lock-timeout ;;
        *) say "migrate: GET_LOCK 失敗（$got）"
           show_err "$tmp/lock.err"
           finish 1 error ;;
    esac
}

# 鎖的連線還在不在（每跑一個 migration 之前看一次）
lock_alive() { kill -0 "$lock_pid" 2>/dev/null; }

release_lock() {
    [ "$lock_open" = 1 ] || return 0
    lock_open=0
    if kill -0 "$lock_pid" 2>/dev/null; then
        trap '' PIPE
        printf "DO RELEASE_LOCK('%s');\n" "$LOCK_NAME" >&3 2>/dev/null
        trap - PIPE
    fi
    exec 3>&-
    local i=0
    while kill -0 "$lock_pid" 2>/dev/null && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
    kill -9 "$lock_pid" 2>/dev/null
    wait "$lock_pid" 2>/dev/null
    return 0
}

# ---- 資料庫裡的記錄 -------------------------------------------------------------------
# record_state：has_records=1 表示有記錄表而且至少一列；table_count 是這個資料庫的資料表數
record_state() {
    if ! db -N -B -e "SELECT (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()
                                AND TABLE_NAME = '$RECORD_TABLE'),
                             (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE())" \
            >"$tmp/state" 2>"$tmp/err"; then
        show_err "$tmp/err"; finish 1 error
    fi
    local has_table
    IFS=$'\t' read -r has_table table_count <"$tmp/state"
    has_records=0
    rec_nums=
    if [ "$has_table" = 1 ]; then
        if ! db -N -B -e "SELECT migration_id, file_name, sha256, DATE_FORMAT(applied_at, '%Y-%m-%d %H:%i:%s'),
                                 applied_by, baseline FROM $RECORD_TABLE ORDER BY migration_id" \
                >"$tmp/records" 2>"$tmp/err"; then
            show_err "$tmp/err"; finish 1 error
        fi
        local id name sha at by base
        while IFS=$'\t' read -r id name sha at by base; do
            [ -n "$id" ] || continue
            rec_name[id]=$name; rec_sha[id]=$sha; rec_at[id]=$at; rec_by[id]=$by; rec_base[id]=$base
            rec_nums="$rec_nums $id"
            has_records=1
        done <"$tmp/records"
    fi
}

# 基準檢查：baseline_ok=1 全部都在；缺的寫在 $tmp/baseline.missing
baseline_check() {
    if ! db -N -B <"$BASELINE_SQL" >"$tmp/baseline" 2>"$tmp/err"; then
        show_err "$tmp/err"; finish 1 error
    fi
    local total missing
    total=$(grep -c . "$tmp/baseline")
    grep '^MISSING' "$tmp/baseline" >"$tmp/baseline.missing"
    missing=$(grep -c . "$tmp/baseline.missing")
    if [ "$missing" -eq 0 ] && [ "$total" -gt 0 ]; then
        baseline_ok=1
        say "baseline: 001～$(pad3 $BASELINE_LAST) 的 $total 項都在（資料表、欄位、索引、限制條件、程序、參數）"
    else
        baseline_ok=0
        say "baseline: $total 項裡缺了 $missing 項："
        local m k o
        while IFS=$'\t' read -r _ m k o; do say "baseline: MISSING $m $k $o"; done <"$tmp/baseline.missing"
    fi
}

# ---- 比對：已套用、改過的、待套用、資料庫有映像檔沒有的 ----------------------------------------
plan() {
    local n
    pending='' changed='' dbonly='' max_rec=0
    for n in $rec_nums; do
        [ "$n" -gt "$max_rec" ] && max_rec=$n
        [ -n "${file_name[$n]:-}" ] || dbonly="$dbonly $n"
    done
    for n in $file_nums; do
        if [ -n "${rec_name[$n]:-}" ]; then
            if [ "${rec_name[$n]}" != "${file_name[$n]}" ] || [ "${rec_sha[$n]}" != "${file_sha[$n]}" ]; then
                changed="$changed $n"
            fi
        else
            pending="$pending $n"
        fi
    done
}

# print_plan full｜brief：status 一個一行列出已套用的；apply 只用一行總結（部署的 log 要短）
print_plan() {
    local n count=0 first='' last=''
    for n in $rec_nums; do
        [ -n "${file_name[$n]:-}" ] || continue
        case " $changed " in *" $n "*) continue ;; esac
        if [ "$1" = brief ] && [ "${rec_at[$n]}" != - ]; then
            count=$((count + 1)); [ -n "$first" ] || first=$n; last=$n
            continue
        fi
        if [ "${rec_at[$n]}" = - ]; then
            say "baseline: $(pad3 "$n") ${rec_name[$n]} sha256=${rec_sha[$n]:0:12}（第一次 apply 時記成已套用，不重跑）"
        elif [ "${rec_base[$n]}" = 1 ]; then
            say "applied: $(pad3 "$n") ${rec_name[$n]} sha256=${rec_sha[$n]:0:12} at=${rec_at[$n]}Z by=${rec_by[$n]} (baseline)"
        else
            say "applied: $(pad3 "$n") ${rec_name[$n]} sha256=${rec_sha[$n]:0:12} at=${rec_at[$n]}Z by=${rec_by[$n]}"
        fi
    done
    if [ "$count" -gt 0 ]; then
        say "applied: $count 個（$(pad3 "$first")～$(pad3 "$last")，最新的是 ${rec_name[$last]}，${rec_at[$last]}Z；逐一列出用 status）"
    fi
    for n in $dbonly; do
        say "db-only: $(pad3 "$n") ${rec_name[$n]} sha256=${rec_sha[$n]:0:12} at=${rec_at[$n]}Z（資料庫記錄有、這個映像檔沒有：比較新的版本套用的，不算錯）"
    done
    for n in $changed; do
        say "changed: $(pad3 "$n") 記錄是 ${rec_name[$n]} sha256=${rec_sha[$n]:0:12}，映像檔是 ${file_name[$n]} sha256=${file_sha[$n]:0:12}"
    done
    for n in $pending; do
        say "pending: $(pad3 "$n") ${file_name[$n]} sha256=${file_sha[$n]:0:12}"
    done
}

# 待套用的破壞性檢查：destructive_count
scan_pending() {
    local n
    destructive_count=0
    for n in $pending; do
        if ! scan "$MIGRATIONS_DIR/${file_name[$n]}" "$tmp/scan"; then
            destructive_count=$((destructive_count + 1))
            print_scan destructive "${file_name[$n]}" "$tmp/scan"
        fi
    done
}

# 第一次執行：記錄表＋001～007（baseline=1）在同一個交易寫入
record_baseline() {
    local n values='' sep=''
    for n in $file_nums; do
        [ "$n" -le "$BASELINE_LAST" ] || continue
        values="$values$sep($n, '${file_name[$n]}', '${file_sha[$n]}', UTC_TIMESTAMP(3), CURRENT_USER(), 1, $commit_sql, NULL)"
        sep=", "
    done
    if ! db -e "CREATE TABLE IF NOT EXISTS $RECORD_TABLE (
          migration_id  SMALLINT UNSIGNED NOT NULL COMMENT '檔名開頭的編號（008_x.sql → 8）',
          file_name     VARCHAR(255)      NOT NULL,
          sha256        CHAR(64)          CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '檔案內容的 SHA-256',
          applied_at    DATETIME(3)       NOT NULL COMMENT 'UTC',
          applied_by    VARCHAR(288)      NOT NULL COMMENT 'CURRENT_USER()',
          baseline      TINYINT(1)        NOT NULL DEFAULT 0 COMMENT '1＝runner 之前就手動套用、第一次執行時基準檢查通過後記錄的',
          source_commit VARCHAR(40)       NULL COMMENT '映像檔的 commit（MIGRATE_SOURCE_COMMIT）',
          duration_ms   INT UNSIGNED      NULL,
          PRIMARY KEY (migration_id)
        ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci
          COMMENT = 'migration runner 的記錄（services/db/README.md）；不要手動改';
        START TRANSACTION;
        INSERT INTO $RECORD_TABLE
          (migration_id, file_name, sha256, applied_at, applied_by, baseline, source_commit, duration_ms)
        VALUES $values;
        COMMIT;" >/dev/null 2>"$tmp/err"; then
        say "migrate: 記錄基準失敗"
        show_err "$tmp/err"
        finish 1 error
    fi
    say "migrate: 基準記好了：001～$(pad3 $BASELINE_LAST) 記成已套用（baseline，沒有重跑）"
}

apply_one() {   # apply_one <編號>
    local n=$1 name=${file_name[$1]} start ms rc warn
    if ! lock_alive; then
        say "migrate: 拿鎖的連線斷了，停下來（$(pad3 "$n") 沒有跑）"
        show_err "$tmp/lock.err"
        finish 1 error
    fi
    say "apply: $(pad3 "$n") $name 開始"
    start=$(now_ms)
    db --batch --show-warnings <"$MIGRATIONS_DIR/$name" >"$tmp/apply.out" 2>"$tmp/apply.err" &
    child_pid=$!
    wait "$child_pid"
    rc=$?
    child_pid=
    ms=$(( $(now_ms) - start ))
    if [ "$rc" -ne 0 ]; then
        say "apply: $(pad3 "$n") $name 失敗（mysql 結束碼 $rc），後面的不跑；這一個沒有記錄，修好之後會重跑"
        show_err "$tmp/apply.err"
        finish 1 failed
    fi
    warn=$(grep -c -E '^(Warning|Note) \(Code [0-9]+\)' "$tmp/apply.out")
    if ! db -e "INSERT INTO $RECORD_TABLE
                  (migration_id, file_name, sha256, applied_at, applied_by, baseline, source_commit, duration_ms)
                VALUES ($n, '$name', '${file_sha[$n]}', UTC_TIMESTAMP(3), CURRENT_USER(), 0, $commit_sql, $ms)" \
            >/dev/null 2>"$tmp/err"; then
        say "apply: $(pad3 "$n") $name 跑完了，但記錄失敗：下次會再跑一次（migration 都要可以重跑）"
        show_err "$tmp/err"
        finish 1 failed
    fi
    say "apply: $(pad3 "$n") $name 完成（$ms ms，警告或提示 $warn 個）"
    if [ "$warn" -gt 0 ]; then
        grep -E '^(Warning|Note) \(Code [0-9]+\)' "$tmp/apply.out" | head -5 | while IFS= read -r line; do say "  $line"; done
        [ "$warn" -gt 5 ] && say "  （還有 $((warn - 5)) 個）"
    fi
    applied_count=$((applied_count + 1))
}

# ---- 指令 ----------------------------------------------------------------------------
# 第一次執行（沒有記錄）時，把 001～007 當成已套用來算待套用的
assume_baseline() {
    local n
    for n in $(seq 1 $BASELINE_LAST); do
        [ -n "${file_name[$n]:-}" ] || usage_error "映像檔裡少了基準的 migration $(pad3 "$n")（001～$(pad3 $BASELINE_LAST) 都要在）"
        rec_name[n]=${file_name[$n]}; rec_sha[n]=${file_sha[$n]}; rec_at[n]=-; rec_by[n]=-; rec_base[n]=1
        rec_nums="$rec_nums $n"
    done
}

first_run_check() {   # 沒有記錄時：基準檢查，沒過就結束（5）
    say "migrate: 還沒有 $RECORD_TABLE 的記錄：第一次執行，先做基準檢查（001～$(pad3 $BASELINE_LAST)，不重跑）"
    if [ "$table_count" -eq 0 ]; then
        say "baseline: 資料庫 $DB_NAME 是空的：新建的資料庫先用 services/db/schema.sql 建立（README「新建資料庫」）"
        finish 5 baseline-failed
    fi
    baseline_check
    if [ "$baseline_ok" != 1 ]; then
        say "migrate: 基準檢查沒過：001～$(pad3 $BASELINE_LAST) 不完整，什麼都沒記錄（README「正式環境第一次執行」）"
        finish 5 baseline-failed
    fi
    assume_baseline
}

cmd_status() {
    check_config
    load_files
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/migrate.XXXXXX") || finish 1 error
    connect
    record_state
    local first=0
    if [ "$has_records" = 0 ]; then
        first=1
        first_run_check
    fi
    plan
    print_plan full
    scan_pending
    [ "$first" = 1 ] && say "migrate: 下一次 apply 會先記錄基準（001～$(pad3 $BASELINE_LAST)）"
    if [ -n "$changed" ]; then
        say "migrate: 已經套用的檔案被改過：apply 會停下來（4）"
        finish 4 changed
    fi
    say "migrate: 待套用 $(count_words "$pending") 個，其中破壞性的 $destructive_count 個"
    finish 0 ok
}

# shellcheck disable=SC2086  # 故意不加引號：用空白切開編號清單
count_words() { set -- $1; printf '%d' "$#"; }

cmd_apply() {
    local allow=0 arg
    for arg in "$@"; do
        case $arg in
            --allow-destructive) allow=1 ;;
            *) usage_error "apply 不認得的參數：$arg" ;;
        esac
    done
    check_config
    load_files
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/migrate.XXXXXX") || finish 1 error
    connect
    acquire_lock
    record_state            # 拿到鎖之後才讀：等鎖的時候別的 runner 可能套用了
    local first=0 n min_pending=
    if [ "$has_records" = 0 ]; then
        first=1
        first_run_check
    fi
    plan
    print_plan brief
    # 先檢查完全部再動手
    if [ -n "$changed" ]; then
        say "migrate: 已經套用的 migration 檔被改過（SHA-256 不同）：什麼都沒套用。已經套用的檔案不能改，要改就新增一個 migration"
        finish 4 changed
    fi
    for n in $pending; do min_pending=$n; break; done
    if [ -n "$min_pending" ] && [ "$min_pending" -lt "$max_rec" ]; then
        say "migrate: 待套用的 $(pad3 "$min_pending") 比已經套用的 $(pad3 "$max_rec") 舊（順序亂了）：什麼都沒套用。把它改成比 $(pad3 "$max_rec") 大的編號"
        finish 1 out-of-order
    fi
    scan_pending
    if [ "$destructive_count" -gt 0 ] && [ "$allow" != 1 ]; then
        say "migrate: 待套用的裡面有 $destructive_count 個破壞性的 migration：什麼都沒套用。先在 RDS 建 snapshot，再手動 Run workflow 並勾選允許破壞性的 migration（--allow-destructive）"
        finish 3 destructive
    fi
    [ "$destructive_count" -gt 0 ] && say "migrate: 允許破壞性的 migration（--allow-destructive）"
    [ "$first" = 1 ] && record_baseline
    applied_count=0
    for n in $pending; do apply_one "$n"; done
    if [ "$applied_count" -gt 0 ]; then finish 0 applied; fi
    if [ "$first" = 1 ]; then finish 0 baseline; fi
    say "migrate: 沒有要套用的"
    finish 0 up-to-date
}

# ---- main ---------------------------------------------------------------------------
[ $# -ge 1 ] || usage_error "少了指令"
cmd=$1
shift
case $cmd in
    status) [ $# -eq 0 ] || usage_error "status 不接參數：$*"; cmd_status ;;
    apply) cmd_apply "$@" ;;
    lint) cmd_lint "$@" ;;
    -h|--help|help)
        say "migrate.sh status | apply [--allow-destructive] | lint [檔案…]（說明見 services/db/README.md）"
        finish 0 help ;;
    *) usage_error "不認得的指令：$cmd" ;;
esac
