#!/bin/bash
# =============================================================================
# deploy.sh — 把 EC2 上的分析服務（services/analyzer）換成 Parameter Store 記的版本
# （docs/rebuild/cd/PLAN.md 第 3 節、CONTRACTS.md 第 4 節；怎麼裝、怎麼看在 deploy/README.md）。
#
# 裝在 /opt/adam/deploy.sh，以 root 執行，同一時間只跑一個（flock）：
#   - GitHub Actions 的 deploy-analyzer.yml 透過 SSM Run Command：
#       /opt/adam/deploy.sh --retry-failed [--allow-destructive-migrations]
#   - 開機時 adam-deploy.service（不帶參數）：補上停機期間合併的版本；之前失敗過的版本不再試
#
# 流程：讀 /adam/analyzer/desired-commit → 和 /opt/adam/deployed-commit 一樣就結束
#   → 事前檢查（analysis.env、模型的資料夾、docker、migrator 的密碼、硬碟）→ 拉兩個映像檔
#   → migration（adam-db-migrations，帳號 adam_migrator）→ check-db（新版要的資料表、欄位、權限都在）
#   → 現在的映像檔記成 adam-analyzer:previous → worker 先 docker stop（手上的工作交還給佇列）→ 換 API → 起 worker
#   → 等 /healthz 的 models: ready，而且這次起的每個 worker 自己回報 ready（最多 300 秒）
#   → 成功：寫 deployed-commit、清掉 failed-commit、印 status、清掉用不到的舊映像檔
#   → 失敗：用 previous 照原樣重建、再等一次；寫 failed-commit
# 換版本之前的任何一步失敗，舊的容器完全不動。
#
# 結束碼：0 部署好了或沒有要做的｜10 等不到鎖｜11 事前檢查沒過｜12 拉不到映像檔｜13 有破壞性的 migration 待套用
#   ｜14 migration 失敗｜15 check-db 沒過｜16 新版沒有 ready、已換回上一版｜17 換回上一版也失敗
# stdout 精簡（SSM 只拿得到前 24,000 個字元），完整的 append 到 /var/log/adam-deploy.log。最後一行固定是
#   deploy: result=<字> code=<數字> commit=<sha>
# 設定都有預設值（下面「設定」），可以在 /opt/adam/deploy.conf（bash 語法）蓋掉。
# =============================================================================
set -uo pipefail
umask 022
# SSM Run Command、systemd 給的環境很少：PATH 可能沒有 /usr/local/bin、HOME 沒設（docker、aws 會抱怨）
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root LC_ALL=C AWS_PAGER=""
exec 2>&1 </dev/null # 錯誤訊息也走 stdout（SSM 照順序收到）；不會有人打字

# ---------- 設定（/opt/adam/deploy.conf 可以蓋掉）----------
CONF_FILE=/opt/adam/deploy.conf
REGION=us-east-1
ANALYZER_REPO=ghcr.io/1104030360/adam-analyzer
MIGRATIONS_REPO=ghcr.io/1104030360/adam-db-migrations
PREVIOUS_TAG=adam-analyzer:previous
DESIRED_PARAM=/adam/analyzer/desired-commit
PASSWORD_PARAM=/adam/db/migrator-password
MIGRATOR_USER=adam_migrator
ENV_FILE=/opt/adam/analysis.env
MODELS_DIR=/opt/adam/models
DEPLOYED_FILE=/opt/adam/deployed-commit
FAILED_FILE=/opt/adam/failed-commit
LOG_FILE=/var/log/adam-deploy.log
LOG_MAX_BYTES=5000000 # 超過就換成 .1（只留一份舊的）
LOCK_FILE=/run/adam-deploy.lock
LOCK_WAIT=900 # 另一個 deploy.sh 在跑時最多等幾秒
API_NAME=adam-analysis
WORKER_PREFIX=adam-analysis-worker-
API_PORT=5000
HEALTH_URL='' # 空的就是 http://127.0.0.1:$API_PORT/healthz
HEALTH_TIMEOUT=300
HEALTH_INTERVAL=5
MAX_RESTARTS=3 # 新的容器重新啟動到這個次數就不等了
MIN_FREE_GB=10 # 要拉映像檔時，docker 的資料夾所在的硬碟至少要剩幾 GB
PULL_TIMEOUT=1800
PULL_RETRY_DELAY=10
MIGRATE_TIMEOUT=900
CHECK_DB_TIMEOUT=300
DOCKER_TIMEOUT=120
AWS_TIMEOUT=60
LOG_OPTS=(--log-opt max-size=20m --log-opt max-file=5) # 容器的 log 輪替：json-file 預設不輪替，會一直長
STDOUT_BUDGET=14000 # 附在 stdout 的詳細輸出（容器 log、status…）合計最多幾個位元組：SSM 只拿得到前 24,000 個字元，最後一行要在裡面

# 容器的資訊一律用這個格式查（測試的假 docker 也只認這一個）
INSPECT_FMT='{{.Name}} {{.Image}} {{.State.Status}} {{.State.ExitCode}} {{.RestartCount}} {{.State.StartedAt}}'
# worker 自己登記的狀態：analysis_queue 的 Worker.report() 每次登記都寫 ALIVE_FILE（在容器自己的 /tmp，新的容器讀不到舊 worker 的）
ALIVE_PY='import os, analysis_queue as q; print(open(q.ALIVE_FILE).read().strip() or "none", int(os.path.getmtime(q.ALIVE_FILE)))'
READY_RE='"models": *"ready"'

FINISHED=0 INTERRUPTED=0 XTRACE=0
DESIRED=none TMPD='' LAST_CAPTURE='' PROBLEM='' LAST_HEALTH='' READY_AFTER=0 REMOVED_COUNT=0 OUT_BYTES=0
RETRY_FAILED=0 ALLOW_DESTRUCTIVE=0
ENV_DB_HOST='' ENV_DB_PORT='' ENV_DB_NAME='' MIGRATOR_PASSWORD='' DOCKER_ROOT=/var/lib/docker
NEW_IMAGE='' MIGRATION_IMAGE='' NEW_ID='' CUR_ID='' PREV_ID='' PREV_COMMIT=unknown
TO_PULL=() WORKERS=() WORKER_START=() STARTED=()

# ---------- 輸出 ----------
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() {
    printf '%s %s\n' "$(stamp)" "$*" >>"$LOG_FILE" 2>/dev/null
    return 0
}
say() {
    local line="$*"
    printf '%s\n' "$line"
    OUT_BYTES=$((OUT_BYTES + ${#line} + 1)) # LC_ALL=C：${#…} 是位元組數
    log "$line"
}
emit() { # emit <檔案>：附在 stdout（縮排）；放不進 STDOUT_BUDGET 就只說在 log 裡（內容已經在 log 裡）
    local text
    text=$(sed 's/^/    /' "$1" 2>/dev/null)
    [ -n "$text" ] || return 0
    if [ $((OUT_BYTES + ${#text} + 1)) -gt "$STDOUT_BUDGET" ]; then
        say "    （這裡還有 $(wc -l <"$1") 行，stdout 放不下，在 $LOG_FILE）"
        return 0
    fi
    printf '%s\n' "$text"
    OUT_BYTES=$((OUT_BYTES + ${#text} + 1))
}
show() { # show <檔案> <行數>：附上最後幾行（一行最多 300 位元組）
    tail -n "$2" "$1" 2>/dev/null | cut -b 1-300 >"$TMPD/show"
    emit "$TMPD/show"
}
finish() { # finish <結束碼> <result>：印最後一行、結束（鎖跟著 process 一起放掉）
    FINISHED=1
    say "deploy: result=$2 code=$1 commit=$DESIRED"
    exit "$1"
}
# shellcheck disable=SC2329 # trap 叫它
on_exit() {
    local rc=$?
    if [ "$FINISHED" != 1 ]; then
        if [ "$INTERRUPTED" = 1 ]; then
            say "deploy: result=interrupted code=$rc commit=$DESIRED"
        else
            say "deploy: result=error code=$rc commit=$DESIRED"
        fi
    fi
    if [ -n "$TMPD" ]; then rm -rf "$TMPD"; fi
}
trap on_exit EXIT
trap 'INTERRUPTED=1; exit 143' TERM
trap 'INTERRUPTED=1; exit 130' INT

usage() {
    cat <<'EOF'
用法：/opt/adam/deploy.sh [--retry-failed] [--allow-destructive-migrations]
  --retry-failed                  desired-commit 之前失敗、換回過上一版（/opt/adam/failed-commit）也照樣再試
  --allow-destructive-migrations  允許有破壞性的 migration（先在 RDS 建 snapshot）
說明在 deploy/README.md。
EOF
}

# ---------- 小工具 ----------
dk() { timeout "$DOCKER_TIMEOUT" docker "$@"; }

capture() { # capture <名字> <指令…>：輸出（stdout＋stderr）存在 $TMPD/<名字>，也附到 log；回傳指令的結束碼
    local name=$1 rc
    shift
    LAST_CAPTURE=$name
    "$@" >"$TMPD/$name" 2>&1
    rc=$?
    { printf -- '----- %s（結束碼 %s）\n' "$name" "$rc"; cat "$TMPD/$name"; } >>"$LOG_FILE" 2>/dev/null
    return "$rc"
}

# 密碼經手的地方關掉 set -x（有人用 bash -x 除錯時，變數的指派和「VAR=… 指令」都會被印出來）
xtrace_off() {
    case $- in *x*) XTRACE=1 ;; *) XTRACE=0 ;; esac
    set +x
} 2>/dev/null
xtrace_restore() { if [ "$XTRACE" = 1 ]; then set -x; fi; } 2>/dev/null

first_line() { if [ -r "$1" ]; then head -n 1 "$1" | tr -d '[:space:]'; fi; }
write_state() { printf '%s\n' "$2" >"$1.tmp" && mv -f "$1.tmp" "$1"; }

# analysis.env 有秘密、值可能有特殊字元：不 source，只取要用的三個（和 docker --env-file 一樣，同名的取最後一個）
env_value() { sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'; }

read_param() { # read_param <名稱> [decrypt]：印出值；0 讀到、3 不存在、1 其他錯誤（訊息在 $TMPD/aws.err，也進 log）
    local args=(ssm get-parameter --region "$REGION" --name "$1" --query Parameter.Value --output text) rc
    if [ "${2:-}" = decrypt ]; then args+=(--with-decryption); fi
    timeout "$AWS_TIMEOUT" aws "${args[@]}" 2>"$TMPD/aws.err"
    rc=$?
    if [ "$rc" = 0 ]; then return 0; fi
    { printf -- '----- aws ssm get-parameter --name %s（結束碼 %s）\n' "$1" "$rc"; cat "$TMPD/aws.err"; } >>"$LOG_FILE" 2>/dev/null
    if grep -q ParameterNotFound "$TMPD/aws.err"; then return 3; fi
    return 1
}

image_id() { dk image inspect -f '{{.Id}}' "$1" 2>/dev/null; }

image_commit() { # 映像檔是哪個 commit（看 ghcr 的 tag）；不是這個腳本拉的（例如手動建的 adam-analyzer:2）印 unknown
    local sha
    sha=$(dk image inspect -f '{{range .RepoTags}}{{println .}}{{end}}' "$1" 2>/dev/null |
        sed -n "s|^${ANALYZER_REPO}:\([0-9a-f]\{40\}\)\$|\1|p" | head -n 1)
    printf '%s\n' "${sha:-unknown}"
}

describe_image() { # 給人看的：ghcr 的 commit，不是的話是它的其他 tag（例如手動建的 adam-analyzer:2）
    local tags commit
    tags=$(dk image inspect -f '{{range .RepoTags}}{{println .}}{{end}}' "$1" 2>/dev/null | grep -vxF -e "$PREVIOUS_TAG" -e '')
    commit=$(printf '%s\n' "$tags" | sed -n "s|^${ANALYZER_REPO}:\([0-9a-f]\{40\}\)\$|\1|p" | head -n 1)
    if [ -n "$commit" ]; then
        printf '%s\n' "$commit"
    elif [ -n "$tags" ]; then
        printf '%s\n' "$tags" | head -n 3 | paste -sd ' ' -
    else
        printf '%s\n' "${PREV_ID:7:12}"
    fi
}

inspect_containers() { dk inspect -f "$INSPECT_FMT" "$@" | sed 's|^/||'; }

managed_names() { # 現在的 API 和 worker 容器（不管有沒有在跑）：API 在最前面，worker 照編號排
    local all
    all=$(dk ps -a --format '{{.Names}}') || return 1
    printf '%s\n' "$all" | grep -xF "$API_NAME"
    printf '%s\n' "$all" | grep -E "^${WORKER_PREFIX}[A-Za-z0-9_.-]+\$" | sort -V
    return 0
}

managed_image_ids() { # 現在的 API、worker 用的映像檔 ID
    local list names=()
    list=$(managed_names) || return 0
    [ -n "$list" ] || return 0
    mapfile -t names <<<"$list"
    inspect_containers "${names[@]}" 2>/dev/null | awk '{ print $2 }'
}

free_gb() { # docker 的資料夾所在的硬碟還剩幾 GB
    local dir=$DOCKER_ROOT
    [ -d "$dir" ] || dir=/
    df -Pk "$dir" 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }'
}

remove_old_images() { # remove_old_images <要留的映像檔 ID…>：只清這個腳本自己拉的兩個 repo（手動建的 adam-analyzer:1、:2 不碰）
    local keep=" $* " ref id
    REMOVED_COUNT=0
    while IFS= read -r ref; do
        case $ref in "" | *"<none>"*) continue ;; esac
        [ "$ref" = "$MIGRATIONS_REPO:$DESIRED" ] && continue
        id=$(image_id "$ref") || continue
        case $keep in *" $id "*) continue ;; esac
        if dk rmi "$ref" >>"$LOG_FILE" 2>&1; then # 還有容器在用的，docker 會拒絕（不加 -f）
            REMOVED_COUNT=$((REMOVED_COUNT + 1))
            log "刪掉舊的映像檔 $ref"
        fi
    done < <(
        dk image ls --format '{{.Repository}}:{{.Tag}}' "$ANALYZER_REPO"
        dk image ls --format '{{.Repository}}:{{.Tag}}' "$MIGRATIONS_REPO"
    )
}

# ---------- 設定、鎖、log ----------
load_config() {
    if [ -e "$CONF_FILE" ]; then
        if ! bash -n "$CONF_FILE"; then
            say "$CONF_FILE 的語法有錯"
            return 1
        fi
        # shellcheck source=/dev/null
        . "$CONF_FILE"
    fi
    HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:$API_PORT/healthz}
    local v
    for v in LOG_MAX_BYTES LOCK_WAIT API_PORT HEALTH_TIMEOUT HEALTH_INTERVAL MAX_RESTARTS MIN_FREE_GB PULL_TIMEOUT \
        PULL_RETRY_DELAY MIGRATE_TIMEOUT CHECK_DB_TIMEOUT DOCKER_TIMEOUT AWS_TIMEOUT STDOUT_BUDGET; do
        if [[ ! ${!v} =~ ^[0-9]+$ ]]; then
            say "$CONF_FILE：$v 要是整數（現在是「${!v}」）"
            return 1
        fi
    done
}

take_lock() {
    if ! exec 9>>"$LOCK_FILE"; then
        say "開不了鎖的檔案 $LOCK_FILE"
        finish 11 precheck-failed
    fi
    if ! flock -w "$LOCK_WAIT" 9; then
        say "另一個 deploy.sh 在跑（$LOCK_FILE），等了 $LOCK_WAIT 秒還沒結束；舊版照常跑"
        finish 10 locked
    fi
}

rotate_log() {
    local size
    size=$(stat -c %s "$LOG_FILE" 2>/dev/null) || return 0
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
        mv -f "$LOG_FILE" "$LOG_FILE.1"
        install -m 640 /dev/null "$LOG_FILE"
    fi
}

# ---------- 換版本之前（失敗時舊的容器完全不動）----------
read_desired() {
    local value rc
    value=$(read_param "$DESIRED_PARAM")
    rc=$?
    case $rc in
    0) ;;
    3)
        say "Parameter Store 沒有 $DESIRED_PARAM：還沒有要部署的版本"
        finish 0 no-desired-commit
        ;;
    *)
        say "讀不到 Parameter Store 的 $DESIRED_PARAM（EC2 的角色要有 ssm:GetParameter）："
        show "$TMPD/aws.err" 5
        finish 11 precheck-failed
        ;;
    esac
    if [[ ! $value =~ ^[0-9a-f]{40}$ ]]; then
        say "$DESIRED_PARAM 不是 40 個字元的小寫 commit SHA：「${value:0:80}」"
        finish 11 precheck-failed
    fi
    DESIRED=$value
}

read_password() { # migrator 的密碼只放在變數裡，交給 docker run -e DB_PASSWORD（不在 argv、log 裡，set -x 也印不出來）
    local rc empty=0
    xtrace_off
    MIGRATOR_PASSWORD=$(read_param "$PASSWORD_PARAM" decrypt)
    rc=$?
    [ -n "$MIGRATOR_PASSWORD" ] || empty=1
    xtrace_restore
    case $rc in
    0)
        [ "$empty" = 0 ] && return 0
        say "Parameter Store 的 $PASSWORD_PARAM 是空的"
        ;;
    3) say "Parameter Store 沒有 $PASSWORD_PARAM（adam_migrator 的密碼，docs/rebuild/cd/PLAN.md 第 8 節第 9 步）" ;;
    *)
        say "讀不到 Parameter Store 的 $PASSWORD_PARAM（EC2 的角色要有 ssm:GetParameter）："
        show "$TMPD/aws.err" 5
        ;;
    esac
    return 1
}

check_disk() { # 拉映像檔之前：不夠就先清掉這個腳本拉過、現在用不到的，還是不夠就停
    local free keep=()
    free=$(free_gb)
    if [ -n "$free" ] && [ "$free" -lt "$MIN_FREE_GB" ]; then
        log "硬碟剩 $free GB，先清掉這個腳本拉過、現在用不到的舊映像檔"
        mapfile -t keep < <(managed_image_ids)
        keep+=("$(image_id "$PREVIOUS_TAG")" "$(image_id "$NEW_IMAGE")") # 這一版已經在本機的話也要留著
        remove_old_images "${keep[@]}"
        free=$(free_gb)
    fi
    if [ -z "$free" ]; then
        log "看不出硬碟還剩多少（df 失敗），照樣拉"
        return 0
    fi
    if [ "$free" -lt "$MIN_FREE_GB" ]; then
        say "硬碟只剩 $free GB（$DOCKER_ROOT），要拉映像檔至少要 $MIN_FREE_GB GB；舊版照常跑。"
        say "用不到的映像檔要手動刪（deploy/README.md 的「硬碟」）。現在有："
        dk image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null | head -n 12 >"$TMPD/images"
        emit "$TMPD/images"
        return 1
    fi
    log "硬碟剩 $free GB"
}

precheck() {
    local ref
    if [ ! -f "$ENV_FILE" ]; then
        say "找不到 $ENV_FILE（分析服務的設定）：先在 EC2 上跑 deploy/install.sh"
        return 1
    fi
    ENV_DB_HOST=$(env_value DB_HOST)
    ENV_DB_PORT=$(env_value DB_PORT)
    ENV_DB_NAME=$(env_value DB_NAME)
    ENV_DB_PORT=${ENV_DB_PORT:-3306}
    if [ -z "$ENV_DB_HOST" ] || [ -z "$ENV_DB_NAME" ]; then
        say "$ENV_FILE 裡沒有 DB_HOST 或 DB_NAME"
        return 1
    fi
    if [ ! -d "$MODELS_DIR" ]; then
        say "找不到模型的資料夾 $MODELS_DIR"
        return 1
    fi
    if ! DOCKER_ROOT=$(dk info --format '{{.DockerRootDir}}' 2>"$TMPD/docker.err"); then
        say "docker 沒在跑："
        show "$TMPD/docker.err" 5
        return 1
    fi
    DOCKER_ROOT=${DOCKER_ROOT:-/var/lib/docker}
    read_password || return 1
    NEW_IMAGE=$ANALYZER_REPO:$DESIRED
    MIGRATION_IMAGE=$MIGRATIONS_REPO:$DESIRED
    TO_PULL=()
    for ref in "$NEW_IMAGE" "$MIGRATION_IMAGE"; do
        image_id "$ref" >/dev/null || TO_PULL+=("$ref")
    done
    if [ "${#TO_PULL[@]}" -gt 0 ]; then check_disk || return 1; fi
    return 0
}

pull_images() { # commit 的 tag 不會變：本機已經有的就不拉（GHCR 連不上時也能換回舊的 commit）
    local ref i
    for ref in "$NEW_IMAGE" "$MIGRATION_IMAGE"; do
        case " ${TO_PULL[*]} " in
        *" $ref "*) ;;
        *)
            say "   已經有 $ref"
            continue
            ;;
        esac
        for i in 1 2 3; do
            if capture pull timeout "$PULL_TIMEOUT" docker pull -q "$ref"; then
                say "   $ref"
                continue 2
            fi
            grep -qiE 'manifest unknown|not found|denied|unauthorized' "$TMPD/pull" && break
            if [ "$i" -lt 3 ]; then sleep "$PULL_RETRY_DELAY"; fi
        done
        say "拉不到 $ref（GHCR 的套件要是 Public、這個 tag 要存在）；舊版照常跑："
        show "$TMPD/pull" 10
        return 1
    done
}

run_migrations() { # 結束碼照 runner（services/db）：0 好了、3 有破壞性的待套用、其他是失敗
    local args=(apply) rc
    if [ "$ALLOW_DESTRUCTIVE" = 1 ]; then args+=(--allow-destructive); fi
    dk rm -f adam-deploy-migrate >/dev/null 2>&1
    xtrace_off
    DB_HOST=$ENV_DB_HOST DB_PORT=$ENV_DB_PORT DB_NAME=$ENV_DB_NAME DB_USER=$MIGRATOR_USER DB_PASSWORD=$MIGRATOR_PASSWORD \
        capture migrate timeout "$MIGRATE_TIMEOUT" docker run --rm --name adam-deploy-migrate \
        -e DB_HOST -e DB_PORT -e DB_NAME -e DB_USER -e DB_PASSWORD "$MIGRATION_IMAGE" "${args[@]}"
    rc=$?
    MIGRATOR_PASSWORD=
    xtrace_restore
    dk rm -f adam-deploy-migrate >/dev/null 2>&1 # 逾時被 timeout 中斷時容器可能還在
    return "$rc"
}

check_db() { # 用分析服務自己的帳號（analysis.env）、新的映像檔，只讀
    local rc
    dk rm -f adam-deploy-check-db >/dev/null 2>&1
    capture check-db timeout "$CHECK_DB_TIMEOUT" docker run --rm --name adam-deploy-check-db --env-file "$ENV_FILE" \
        "$NEW_IMAGE" python -m analysis_queue check-db
    rc=$?
    dk rm -f adam-deploy-check-db >/dev/null 2>&1
    return "$rc"
}

# ---------- 換版本 ----------
plan() { # 照現在的容器決定要建哪些：API＋原本的每個 worker（原本在跑的才啟動）；沒有 worker 在跑時啟動第一個
    local list names=() out name image status active=0
    list=$(managed_names) || return 1
    WORKERS=() WORKER_START=() CUR_ID=
    if [ -n "$list" ]; then
        mapfile -t names <<<"$list"
        out=$(inspect_containers "${names[@]}") || return 1
        while read -r name image status _; do
            [ -n "$name" ] || continue
            [ -n "$CUR_ID" ] || CUR_ID=$image # API 排在最前面：有 API 用 API 的映像檔，沒有就用第一個 worker 的
            [ "$name" = "$API_NAME" ] && continue
            WORKERS+=("$name")
            case $status in
            running | restarting | paused)
                WORKER_START+=(1)
                active=$((active + 1))
                ;;
            *) WORKER_START+=(0) ;;
            esac
        done <<<"$out"
    fi
    if [ "${#WORKERS[@]}" = 0 ]; then
        WORKERS=("${WORKER_PREFIX}1")
        WORKER_START=(1)
    elif [ "$active" = 0 ]; then
        WORKER_START[0]=1
    fi
    return 0
}

describe_plan() {
    local i out="$API_NAME"
    for i in "${!WORKERS[@]}"; do
        if [ "${WORKER_START[$i]}" = 1 ]; then
            out+="、${WORKERS[$i]}"
        else
            out+="、${WORKERS[$i]}（原本停著：建好、不啟動）"
        fi
    done
    printf '%s' "$out"
}

remove_managed() { # 拿掉現在的 API 和 worker。worker 先 docker stop：停止領新工作、手上那一筆交還給佇列、登記 stopped（最多 30 秒）
    local list names=() workers=() n
    list=$(managed_names) || return 1
    [ -n "$list" ] || return 0
    mapfile -t names <<<"$list"
    for n in "${names[@]}"; do
        [ "$n" = "$API_NAME" ] || workers+=("$n")
    done
    if [ "${#workers[@]}" -gt 0 ]; then
        capture stop-workers timeout $((DOCKER_TIMEOUT + 60)) docker stop "${workers[@]}"
        log "worker 停下來的結束碼（137 是逾時被 kill，可能沒登記 stopped）：$(inspect_containers "${workers[@]}" 2>/dev/null | awk '{ printf "%s=%s ", $1, $4 }')"
        capture rm-workers dk rm -f "${workers[@]}"
    fi
    if [ "${names[0]}" = "$API_NAME" ]; then
        capture stop-api dk stop "$API_NAME"
        capture rm-api dk rm -f "$API_NAME"
    fi
    return 0
}

start_version() { # start_version <映像檔> <commit>：拿掉現在的容器，照 plan 用這個映像檔重建
    # docker run 的參數照 docs/rebuild/sqs/DEPLOY.md 第 6 節，只換映像檔和 env 檔的路徑，另外加 commit 的 label 和 log 輪替
    local image=$1 commit=$2 i name args=()
    STARTED=()
    remove_managed || return 1
    capture run-api dk run -d --name "$API_NAME" --restart unless-stopped --env-file "$ENV_FILE" -p "$API_PORT:5000" \
        --label "adam.commit=$commit" "${LOG_OPTS[@]}" "$image" || return 1
    for i in "${!WORKERS[@]}"; do
        name=${WORKERS[$i]}
        args=(--name "$name" --hostname "$name" --restart unless-stopped
            --stop-timeout 30 --env-file "$ENV_FILE" -v "$MODELS_DIR:/models:ro"
            --health-cmd "python -m analysis_queue alive" --health-interval 60s --health-start-period 300s
            --label "adam.commit=$commit" "${LOG_OPTS[@]}" "$image" python worker.py)
        if [ "${WORKER_START[$i]}" = 1 ]; then
            capture "run-$name" dk run -d "${args[@]}" || return 1
            STARTED+=("$name")
        else
            capture "create-$name" dk create "${args[@]}" || return 1
        fi
    done
}

worker_state() { # worker_state <容器> <這一次啟動的時間>：這個容器現在的 process 自己登記的狀態（loading、ready、busy、failed…）；
    # 還沒登記、或檔案是重新啟動之前的 process 留下的，印 none
    local out state mtime started
    out=$(dk exec "$1" python -c "$ALIVE_PY" 2>/dev/null) || {
        echo none
        return
    }
    read -r state mtime <<<"$out"
    started=0
    if [ -n "$2" ]; then started=$(date -d "$2" +%s 2>/dev/null) || started=0; fi
    if [[ $mtime =~ ^[0-9]+$ ]] && [ "$mtime" -ge "$started" ]; then echo "${state:-none}"; else echo none; fi
}

# 等 /healthz 的 models: ready，而且這次啟動的每個 worker 自己回報 ready 或 busy。只看 /healthz 會被舊 worker 騙：
# 新舊 worker 的 worker_id 一樣（hostname:1），舊的被 kill（沒登記 stopped）時，它最後的 ready 在 120 秒內還算數。
wait_ready() {
    local start=$SECONDS name restarts started_at state body all most w
    local -A started=()
    PROBLEM='' LAST_HEALTH=''
    say "== 等 models: ready（最多 $HEALTH_TIMEOUT 秒）"
    while :; do
        all=1 most=0
        while read -r name _ _ _ restarts started_at; do
            [ -n "$name" ] || continue
            started[$name]=$started_at
            if [ "${restarts:-0}" -gt "$most" ]; then most=$restarts; fi
        done < <(inspect_containers "$API_NAME" "${STARTED[@]}" 2>/dev/null)
        for w in "${STARTED[@]}"; do
            state=$(worker_state "$w" "${started[$w]:-}")
            case $state in
            ready | busy) ;;
            failed)
                PROBLEM="$w 載入模型失敗（它自己登記了 failed）"
                return 1
                ;;
            *) all=0 ;;
            esac
        done
        body=$(curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null) || body=
        LAST_HEALTH=$body
        if [ "$all" = 1 ] && [[ $body =~ $READY_RE ]]; then
            READY_AFTER=$((SECONDS - start))
            return 0
        fi
        if [ "$most" -ge "$MAX_RESTARTS" ]; then
            PROBLEM="容器一直重新啟動（$most 次）"
            return 1
        fi
        if [ $((SECONDS - start)) -ge "$HEALTH_TIMEOUT" ]; then
            PROBLEM="$HEALTH_TIMEOUT 秒內沒有 ready"
            return 1
        fi
        sleep "$HEALTH_INTERVAL"
    done
}

diagnose() { # 失敗時附上 /healthz 和容器 log 的最後幾行（完整的在 log）
    local c health=${LAST_HEALTH:-（連不上）}
    say "   /healthz：${health:0:500}"
    for c in "$API_NAME" "${STARTED[@]}"; do
        capture "logs-$c" dk logs --tail 25 "$c"
        say "   --- docker logs --tail 25 $c"
        show "$TMPD/logs-$c" 25
    done
}

succeed() {
    write_state "$DEPLOYED_FILE" "$DESIRED"
    rm -f "$FAILED_FILE"
    say "== ready（$READY_AFTER 秒）"
    if capture status dk exec "$API_NAME" python -m analysis_queue status; then
        say "--- python -m analysis_queue status"
        head -n 60 "$TMPD/status" >"$TMPD/status.head"
        emit "$TMPD/status.head"
    else
        say "   （python -m analysis_queue status 失敗，見 $LOG_FILE）"
    fi
    remove_old_images "$NEW_ID" "$PREV_ID"
    if [ "$REMOVED_COUNT" -gt 0 ]; then say "== 清掉 $REMOVED_COUNT 個用不到的舊映像檔"; fi
    finish 0 deployed
}

rollback() {
    write_state "$FAILED_FILE" "$DESIRED"
    if [ -z "$PREV_ID" ]; then
        say "沒有上一版（$PREVIOUS_TAG）可以換回去：新版的容器留著，分析服務可能停了"
        finish 17 rollback-failed
    fi
    say "== 換回上一版 $PREVIOUS_TAG（$(describe_image "$PREVIOUS_TAG")）"
    if ! start_version "$PREVIOUS_TAG" "$PREV_COMMIT"; then
        say "換回上一版也失敗：容器建不起來"
        show "$TMPD/$LAST_CAPTURE" 10
    elif wait_ready; then
        say "== 上一版 ready（$READY_AFTER 秒）。$DESIRED 記在 $FAILED_FILE：開機時不再試，GitHub 的部署（--retry-failed）會再試"
        finish 16 rolled-back
    else
        say "換回上一版也失敗：$PROBLEM"
        diagnose
    fi
    finish 17 rollback-failed
}

# ---------- 主程式 ----------
main() {
    local arg bad='' rc deployed failed
    for arg in "$@"; do
        case $arg in
        --retry-failed) RETRY_FAILED=1 ;;
        --allow-destructive-migrations) ALLOW_DESTRUCTIVE=1 ;;
        -h | --help)
            usage
            FINISHED=1
            exit 0
            ;;
        *) bad=$arg ;;
        esac
    done
    load_config || finish 11 precheck-failed
    if [ "$(id -u)" != 0 ]; then
        say "要用 root 執行（sudo /opt/adam/deploy.sh）"
        finish 11 precheck-failed
    fi
    [ -e "$LOG_FILE" ] || install -m 640 /dev/null "$LOG_FILE" 2>/dev/null
    if ! TMPD=$(mktemp -d -t adam-deploy.XXXXXX); then
        TMPD=
        say "建不了暫存資料夾"
        finish 11 precheck-failed
    fi
    if [ -n "$bad" ]; then
        say "不認得的參數：$bad"
        usage
        finish 11 precheck-failed
    fi

    take_lock
    rotate_log
    log "==== deploy.sh 開始（pid $$；參數：${*:-無}；sha256 $(sha256sum "$0" 2>/dev/null | cut -c 1-12)）"
    read_desired
    deployed=$(first_line "$DEPLOYED_FILE")
    if [ "$DESIRED" = "$deployed" ]; then
        say "已經是這一版，什麼都不用做"
        finish 0 up-to-date
    fi
    failed=$(first_line "$FAILED_FILE")
    if [ "$DESIRED" = "$failed" ] && [ "$RETRY_FAILED" != 1 ]; then
        say "這一版之前部署失敗、已經換回過上一版（$FAILED_FILE），開機時不再試。"
        say "要再試：GitHub 重新執行 Deploy analyzer，或 sudo /opt/adam/deploy.sh --retry-failed"
        finish 0 skipped-failed
    fi
    say "== 部署 $DESIRED（目前：${deployed:-沒有記錄}）"

    precheck || finish 11 precheck-failed
    say "== 拉映像檔"
    pull_images || finish 12 pull-failed

    if [ "$ALLOW_DESTRUCTIVE" = 1 ]; then say "== migration（允許破壞性的）"; else say "== migration"; fi
    run_migrations
    rc=$?
    case $rc in
    0) say "   $(tail -n 1 "$TMPD/migrate")" ;;
    3)
        say "有破壞性的 migration 待套用：這一輪什麼都沒套用，舊版照常跑。"
        say "要套用：先在 RDS 建 snapshot，再手動 Run workflow 並勾選允許破壞性的 migration"
        show "$TMPD/migrate" 30
        finish 13 destructive-migration
        ;;
    *)
        if [ "$rc" = 124 ]; then say "migration 逾時（$MIGRATE_TIMEOUT 秒），舊版照常跑："; else say "migration 失敗（結束碼 $rc），舊版照常跑："; fi
        show "$TMPD/migrate" 40
        finish 14 migration-failed
        ;;
    esac

    say "== check-db"
    if check_db; then
        say "   $(tail -n 1 "$TMPD/check-db")"
    else
        say "check-db 沒過（新版要的資料表、欄位、權限不全），舊版照常跑："
        grep -v '^OK' "$TMPD/check-db" >"$TMPD/check-db.bad"
        show "$TMPD/check-db.bad" 40
        finish 15 check-db-failed
    fi

    if ! plan; then
        say "讀不到現在的容器（docker ps、inspect 失敗），舊版照常跑"
        finish 11 precheck-failed
    fi
    NEW_ID=$(image_id "$NEW_IMAGE")
    if [ -n "$CUR_ID" ] && [ "$CUR_ID" != "$NEW_ID" ]; then
        if ! dk tag "$CUR_ID" "$PREVIOUS_TAG" >>"$LOG_FILE" 2>&1; then
            say "記不了上一版（docker tag 失敗），舊版照常跑"
            finish 11 precheck-failed
        fi
    fi
    PREV_ID=$(image_id "$PREVIOUS_TAG")
    if [ -n "$PREV_ID" ]; then PREV_COMMIT=$(image_commit "$PREVIOUS_TAG"); fi
    if [ -n "$PREV_ID" ]; then
        say "== 換版本：上一版記成 $PREVIOUS_TAG（$(describe_image "$PREVIOUS_TAG")）；$(describe_plan)"
    else
        say "== 換版本（沒有上一版可以換回去）：$(describe_plan)"
    fi
    if ! start_version "$NEW_IMAGE" "$DESIRED"; then
        say "新版的容器建不起來："
        show "$TMPD/$LAST_CAPTURE" 10
    elif wait_ready; then
        succeed
    else
        say "新版沒有 ready：$PROBLEM"
        diagnose
    fi
    rollback
}

main "$@"
