#!/bin/bash
# =============================================================================
# deploy.sh — 把 EC2 上的分析服務（services/analyzer）換成 Parameter Store 記的版本
# （docs/rebuild/cd/PLAN.md 第 3 節、CONTRACTS.md 第 4 節；怎麼裝、怎麼看在 deploy/README.md）。
#
# 裝在 /opt/adam/deploy.sh，以 root 執行，同一時間只跑一個（flock）：
#   - GitHub Actions 的 deploy-analyzer.yml 透過 SSM Run Command：
#       /opt/adam/deploy.sh --retry-failed [--allow-destructive-migrations]
#   - 開機時 adam-deploy.service（不帶參數）：補上停機期間合併的版本；之前失敗過的版本不再試
#     （除非 desired-commit 在失敗之後又被寫過一次）
#
# 流程：讀 /adam/analyzer/desired-commit → 和 /opt/adam/deployed-commit 一樣就結束
#   → 事前檢查（analysis.env、模型的資料夾、docker 和它的 log driver、migrator 的密碼、要整個下載時的硬碟）
#   → 一律 docker pull 兩個映像檔（拉不到、本機有的話用本機的）
#   → migration（adam-db-migrations，帳號 adam_migrator）→ check-db（新版要的資料表、欄位、權限都在）
#   → 現在的映像檔記成 adam-analyzer:previous → worker 先 docker stop（手上的工作交還給佇列）→ 換 API → 起 worker
#   → 等 /healthz 的 models: ready，而且這次起的每個 worker 自己回報 ready（最多 300 秒）
#   → 成功：寫 deployed-commit、清掉 failed-commit、印 status、清掉用不到的舊映像檔
#   → 失敗：用 previous 照原樣重建、再等一次；寫 failed-commit
# 換版本之前的任何一步失敗，舊的容器完全不動。整次最多 DEPLOY_BUDGET 秒（含等鎖）：換容器之前要剩 SWITCH_RESERVE 秒，
# 不然不換（18）；pull、migration、check-db 的逾時也用剩下的時間截。開機的 unit、SSM 給的時間都比預算長，換到一半不會被停掉。
#
# 結束碼：0 部署好了或沒有要做的｜10 等不到鎖｜11 事前檢查沒過｜12 拉不到映像檔｜13 有破壞性的 migration 待套用
#   ｜14 migration 失敗｜15 check-db 沒過｜16 新版沒有 ready、已換回上一版｜17 換回上一版也失敗｜18 時間不夠、沒有換版本
# stdout 會原樣出現在公開 repo 的 GitHub Actions log 裡：只放精簡、可以公開的內容（每一行都經過 public 過濾；
# 容器的 log、status 不放），SSM 也只拿得到前 24,000 個字元。完整的（不過濾）append 到 /var/log/adam-deploy.log（600、root）。
# 最後一行固定是 deploy: result=<字> code=<數字> commit=<sha>
# 設定都有預設值（下面「設定」），可以在 /opt/adam/deploy.conf（bash 語法）蓋掉。
# =============================================================================
set -uo pipefail
umask 022
# SSM Run Command、systemd 給的環境很少：PATH 可能沒有 /usr/local/bin、HOME 沒設（docker、aws 會抱怨）
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root LC_ALL=C AWS_PAGER=""
exec 2>&1 # 錯誤訊息也走 stdout（SSM 照順序收到）。只有 say、emit 會過濾，所以指令的輸出一律先存檔（capture），不直接印

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
LOG_OPTS=(--log-opt max-size=20m --log-opt max-file=5) # 容器的 log 輪替：json-file 預設不輪替，會一直長（只在 log driver 是 json-file、local 時加）
DEPLOY_BUDGET=2700 # 整次最多幾秒（從開始執行、含等鎖）：開機的 unit 給 50 分鐘、SSM 給 3000 秒，都比這個長
SWITCH_RESERVE=900 # 開始換容器時至少要剩幾秒：停、起容器＋等 ready（HEALTH_TIMEOUT）＋萬一換回上一版再等一次
STDOUT_BUDGET=14000 # 附在 stdout 的詳細輸出（容器 log、status…）合計最多幾個位元組：SSM 只拿得到前 24,000 個字元，最後一行要在裡面

# 容器的資訊一律用這個格式查（測試的假 docker 也只認這一個）
INSPECT_FMT='{{.Name}} {{.Image}} {{.State.Status}} {{.State.ExitCode}} {{.RestartCount}} {{.State.StartedAt}}'
# worker 自己登記的狀態：analysis_queue 的 Worker.report() 每次登記都寫 ALIVE_FILE（在容器自己的 /tmp，新的容器讀不到舊 worker 的）
ALIVE_PY='import os, analysis_queue as q; print(open(q.ALIVE_FILE).read().strip() or "none", int(os.path.getmtime(q.ALIVE_FILE)))'
READY_RE='"models": *"ready"'

FINISHED=0 INTERRUPTED=0 XTRACE=0
DESIRED=none TMPD='' LAST_CAPTURE='' PROBLEM='' LAST_HEALTH='' READY_AFTER=0 REMOVED_COUNT=0 OUT_BYTES=0
RETRY_FAILED=0 ALLOW_DESTRUCTIVE=0
ENV_DB_HOST='' ENV_DB_PORT='' ENV_DB_NAME='' ENV_S3_BUCKET='' ENV_SQS_URL='' MIGRATOR_PASSWORD='' DOCKER_ROOT=/var/lib/docker
RUN_START_LINE=1 SANITIZE=0 LOG_DRIVER='' DESIRED_MODIFIED=''
NEW_IMAGE='' MIGRATION_IMAGE='' NEW_ID='' CUR_ID='' PREV_ID='' PREV_COMMIT=unknown
NOT_LOCAL=() RUN_LOG_OPTS=() WORKERS=() WORKER_START=() STARTED=()

# stdout 會公開（GitHub Actions 的 log）：這些換成代號。commit SHA、映像檔名稱、資料表和欄位的名稱、結束碼、計數照留。
# analysis.env 的 DB_HOST、SQS_ANALYSIS_QUEUE_URL、S3_BUCKET 的值在 public() 裡先整串換掉，再套這些（順序有關係）
PUBLIC_SED=(
    -e 's#https?://sqs[.][a-z0-9-]+[.]amazonaws[.]com/[0-9]+/[A-Za-z0-9_.-]+#<SQS_URL>#g'
    -e "s#s3://[^/[:space:]\"'<>]+#s3://<bucket>#g"
    -e 's#[A-Za-z0-9.-]+[.]s3([.-][a-z0-9-]+)?[.]amazonaws[.]com#<bucket>.s3.amazonaws.com#g'
    -e 's#adam-media-[A-Za-z0-9.-]*[A-Za-z0-9]#<bucket>#g'
    -e 's#[A-Za-z0-9.-]+[.]rds[.]amazonaws[.]com#<RDS>#g'
    -e 's#\b(AKIA|ASIA)[A-Z0-9]{16}\b#<AWS_KEY>#g'
    -e 's#\bhf_[A-Za-z0-9]{20,}#<HF_TOKEN>#g'
    -e 's#\b([A-Za-z_]*(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY))=[^[:space:]]+#\1=<secret>#gI'
    -e 's#\b[0-9]{12}\b#<ACCOUNT>#g'
    -e 's#\bip-[0-9]{1,3}(-[0-9]{1,3}){3}([.][A-Za-z0-9.-]*[A-Za-z0-9])?#<HOST>#g'
    -e 's#\b[0-9]{1,3}([.][0-9]{1,3}){3}\b#<IP>#g'
    -e 's#\bi-[0-9a-f]{8}([0-9a-f]{9})?\b#<INSTANCE>#g'
    -e 's#\bS[0-9]{14}([^0-9]|$)#<SERVICE_ID>\1#g'
)

# ---------- 輸出 ----------
public() { # stdin → stdout：拿掉不該出現在公開的 GitHub Actions log 裡的東西（log 檔不經過這裡，保持完整）
    local text
    text=$(cat)
    # 太短的值（例如 DB_HOST=db）整串換掉會誤傷一般的字：那種情況靠下面的規則
    if [ "${#ENV_DB_HOST}" -ge 6 ]; then text=${text//"$ENV_DB_HOST"/<DB_HOST>}; fi
    if [ "${#ENV_SQS_URL}" -ge 6 ]; then text=${text//"$ENV_SQS_URL"/<SQS_URL>}; fi
    if [ "${#ENV_S3_BUCKET}" -ge 6 ]; then text=${text//"$ENV_S3_BUCKET"/<bucket>}; fi
    printf '%s\n' "$text" | sed -E "${PUBLIC_SED[@]}"
}
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() {
    printf '%s %s\n' "$(stamp)" "$*" >>"$LOG_FILE" 2>/dev/null
    return 0
}
say() { # stdout 是過濾過的，log 是原樣
    local line="$*" safe
    safe=$(public <<<"$line")
    printf '%s\n' "$safe"
    OUT_BYTES=$((OUT_BYTES + ${#safe} + 1)) # LC_ALL=C：${#…} 是位元組數
    log "$line"
}
emit() { # emit <檔案>：過濾之後附在 stdout（縮排）；放不進 STDOUT_BUDGET 就只說在 log 裡（內容已經在 log 裡）
    local text
    text=$(sed 's/^/    /' "$1" 2>/dev/null | public)
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
last_line() { # 最後一行：每個字都是這個腳本決定的（DESIRED 是驗過的 SHA 或 none），不經過過濾，過濾壞了也一定印得出來
    printf 'deploy: result=%s code=%s commit=%s\n' "$1" "$2" "$DESIRED"
    log "deploy: result=$1 code=$2 commit=$DESIRED"
}
finish() { # finish <結束碼> <result>：印最後一行、結束（鎖跟著 process 一起放掉）
    FINISHED=1
    last_line "$2" "$1"
    exit "$1"
}
# shellcheck disable=SC2329 # trap 叫它
on_exit() {
    local rc=$?
    if [ "$FINISHED" != 1 ]; then
        if [ "$INTERRUPTED" = 1 ]; then last_line interrupted "$rc"; else last_line error "$rc"; fi
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
  --sanitize                      不部署：把 stdin 過濾成可以公開的樣子印出來（貼 log 到 GitHub 之前用）
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

# analysis.env 有秘密、值可能有特殊字元：不 source，只取要用的幾個（和 docker --env-file 一樣，同名的取最後一個）
env_value() { sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'; }
load_env_values() { # migration 要的 DB_HOST、DB_PORT、DB_NAME，和 stdout 要換掉的 S3_BUCKET、SQS 的網址（都不是密碼）
    [ -r "$ENV_FILE" ] || return 0
    ENV_DB_HOST=$(env_value DB_HOST)
    ENV_DB_PORT=$(env_value DB_PORT)
    ENV_DB_NAME=$(env_value DB_NAME)
    ENV_S3_BUCKET=$(env_value S3_BUCKET)
    ENV_SQS_URL=$(env_value SQS_ANALYSIS_QUEUE_URL)
}

health_summary() { # /healthz 的 JSON → 只留公開也沒關係的計數（models、workers、queue_depth）
    local body=$1 out='' k re
    if [ -z "$body" ]; then
        printf '（連不上）'
        return
    fi
    re='"models": *"([a-z]+)"'
    if [[ $body =~ $re ]]; then out="models=${BASH_REMATCH[1]}"; fi
    for k in loading ready busy failed; do
        re="\"$k\": *([0-9]+)"
        if [[ $body =~ $re ]]; then out+=" $k=${BASH_REMATCH[1]}"; fi
    done
    re='"queue_depth": *([0-9]+)'
    if [[ $body =~ $re ]]; then out+=" queue_depth=${BASH_REMATCH[1]}"; fi
    printf '%s' "${out:-（看不懂的回應）}"
}

log_hint() { printf 'sudo tail -n +%s %s' "$RUN_START_LINE" "$LOG_FILE"; } # 這一次的完整紀錄

read_param() { # read_param <名稱> <query> [decrypt]：印出結果；0 讀到、3 不存在、1 其他錯誤（訊息在 $TMPD/aws.err，也進 log）
    local args=(ssm get-parameter --region "$REGION" --name "$1" --query "$2" --output text) rc
    if [ "${3:-}" = decrypt ]; then args+=(--with-decryption); fi
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
    all=$(dk ps -a --format '{{.Names}}' 2>>"$LOG_FILE") || return 1 # docker 的錯誤只進 log（stdout 只走 say、emit）
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

# ---------- 時間預算 ----------
time_left() { echo $((DEPLOY_BUDGET - SWITCH_RESERVE - SECONDS)); } # 開始換容器之前還能用幾秒（SECONDS 從開始執行起算）
cap_time() { # cap_time <秒數>：不超過換容器之前剩下的時間，至少 1（timeout 0 是不限時，不能給）
    local left
    left=$(time_left)
    if [ "$left" -lt "$1" ]; then echo $((left > 1 ? left : 1)); else echo "$1"; fi
}
need_time() { # need_time <要做的事>：換容器之前的時間用完了就停（18），舊版照常跑
    if [ "$(time_left)" -le 0 ]; then
        say "時間不夠了：整次最多 $DEPLOY_BUDGET 秒（含等鎖），換容器要留 $SWITCH_RESERVE 秒，已經用了 $SECONDS 秒；不$1，舊版照常跑"
        finish 18 out-of-time
    fi
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
    local err
    if [ -e "$CONF_FILE" ]; then
        if ! err=$(bash -n "$CONF_FILE" 2>&1); then
            say "$CONF_FILE 的語法有錯：$err"
            return 1
        fi
        # shellcheck source=/dev/null
        . "$CONF_FILE"
    fi
    HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:$API_PORT/healthz}
    local v
    for v in LOG_MAX_BYTES LOCK_WAIT API_PORT HEALTH_TIMEOUT HEALTH_INTERVAL MAX_RESTARTS MIN_FREE_GB PULL_TIMEOUT \
        PULL_RETRY_DELAY MIGRATE_TIMEOUT CHECK_DB_TIMEOUT DOCKER_TIMEOUT AWS_TIMEOUT STDOUT_BUDGET DEPLOY_BUDGET \
        SWITCH_RESERVE; do
        if [[ ! ${!v} =~ ^[0-9]+$ ]]; then
            say "$CONF_FILE：$v 要是整數（現在是「${!v}」）"
            return 1
        fi
    done
    if [ "$DEPLOY_BUDGET" -le "$SWITCH_RESERVE" ]; then
        say "$CONF_FILE：DEPLOY_BUDGET（$DEPLOY_BUDGET）要比 SWITCH_RESERVE（$SWITCH_RESERVE）大"
        return 1
    fi
}

take_lock() {
    if ! exec 9>>"$LOCK_FILE"; then
        say "開不了鎖的檔案 $LOCK_FILE"
        finish 11 precheck-failed
    fi
    local secs
    secs=$(cap_time "$LOCK_WAIT") # 等鎖也算在預算裡
    if ! flock -w "$secs" 9; then
        say "另一個 deploy.sh 在跑（$LOCK_FILE），等了 $secs 秒還沒結束；舊版照常跑"
        finish 10 locked
    fi
}

rotate_log() {
    local size
    size=$(stat -c %s "$LOG_FILE" 2>/dev/null) || return 0
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
        mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null
        install -m 600 /dev/null "$LOG_FILE" 2>/dev/null
    fi
}

# ---------- 換版本之前（失敗時舊的容器完全不動）----------
read_desired() { # 值和最後寫入的時間一起讀（開機時要判斷「失敗之後是不是又被寫過」）
    local out value modified rc
    out=$(read_param "$DESIRED_PARAM" '[Parameter.Value, Parameter.LastModifiedDate]')
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
    read -r value modified <<<"$out" # text 輸出：值<tab>時間（ISO 8601）
    if [[ ! ${value:-} =~ ^[0-9a-f]{40}$ ]]; then
        say "$DESIRED_PARAM 不是 40 個字元的小寫 commit SHA：「${value:0:80}」"
        finish 11 precheck-failed
    fi
    DESIRED=$value
    DESIRED_MODIFIED=${modified:-}
}

rewritten_after_failure() { # desired-commit 比 failed-commit 新：失敗之後又被要求部署一次（例如修好之後、停機時 Run workflow）
    local param file
    file=$(stat -c %Y "$FAILED_FILE" 2>/dev/null) || file=''
    param=''
    if [ -n "$DESIRED_MODIFIED" ]; then param=$(date -d "$DESIRED_MODIFIED" +%s 2>/dev/null) || param=''; fi
    if [ -z "$param" ] || [ -z "$file" ]; then
        log "看不出 desired-commit 什麼時候寫的（「$DESIRED_MODIFIED」）或 failed-commit 的時間：照原本的規則跳過"
        return 1
    fi
    log "desired-commit 寫入：$DESIRED_MODIFIED（$param）；failed-commit：$file"
    [ "$param" -gt "$file" ]
}

read_password() { # migrator 的密碼只放在變數裡，交給 docker run -e DB_PASSWORD（不在 argv、log 裡，set -x 也印不出來）
    local rc empty=0
    xtrace_off
    MIGRATOR_PASSWORD=$(read_param "$PASSWORD_PARAM" Parameter.Value decrypt)
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

check_disk() { # 要整個下載映像檔之前：不夠就先清掉這個腳本拉過、現在用不到的，還是不夠就停
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
    load_env_values
    ENV_DB_PORT=${ENV_DB_PORT:-3306}
    if [ -z "$ENV_DB_HOST" ] || [ -z "$ENV_DB_NAME" ]; then
        say "$ENV_FILE 裡沒有 DB_HOST 或 DB_NAME"
        return 1
    fi
    if [ ! -d "$MODELS_DIR" ]; then
        say "找不到模型的資料夾 $MODELS_DIR"
        return 1
    fi
    local info
    if ! info=$(dk info --format '{{.LoggingDriver}} {{.DockerRootDir}}' 2>"$TMPD/docker.err"); then
        say "docker 沒在跑："
        show "$TMPD/docker.err" 5
        return 1
    fi
    read -r LOG_DRIVER DOCKER_ROOT <<<"$info"
    DOCKER_ROOT=${DOCKER_ROOT:-/var/lib/docker}
    case $LOG_DRIVER in # --log-opt max-size 只有 json-file、local 認得；其他的 driver，docker run 會拒絕（新版、換回去都建不起來）
    json-file | local) RUN_LOG_OPTS=("${LOG_OPTS[@]}") ;;
    *)
        RUN_LOG_OPTS=()
        log "docker 的預設 log driver 是「${LOG_DRIVER:-?}」：這次的容器不加 LOG_OPTS（--log-opt 只有 json-file、local 認得）"
        ;;
    esac
    read_password || return 1
    NEW_IMAGE=$ANALYZER_REPO:$DESIRED
    MIGRATION_IMAGE=$MIGRATIONS_REPO:$DESIRED
    NOT_LOCAL=()
    for ref in "$NEW_IMAGE" "$MIGRATION_IMAGE"; do
        image_id "$ref" >/dev/null || NOT_LOCAL+=("$ref")
    done
    if [ "${#NOT_LOCAL[@]}" -gt 0 ]; then check_disk || return 1; fi # 只有要整個下載時才看硬碟
    return 0
}

pull_images() { # 一律 docker pull：GHCR 上同一個 tag 可能重建過（沒變時只拿 manifest，很快）。拉不到、本機有的就用本機的
    local ref i local_copy
    for ref in "$NEW_IMAGE" "$MIGRATION_IMAGE"; do
        local_copy=1
        case " ${NOT_LOCAL[*]} " in *" $ref "*) local_copy=0 ;; esac
        for i in 1 2 3; do
            need_time "拉映像檔"
            if capture pull timeout "$(cap_time "$PULL_TIMEOUT")" docker pull -q "$ref"; then
                say "   $ref"
                continue 2
            fi
            need_time "拉映像檔（拉 $ref 的時候時間用完了）"
            grep -qiE 'manifest unknown|not found|denied|unauthorized' "$TMPD/pull" && break
            if [ "$i" -lt 3 ]; then sleep "$PULL_RETRY_DELAY"; fi
        done
        if [ "$local_copy" = 1 ]; then
            say "   注意：拉不到 $ref，用本機已經有的那一份（GHCR 上的可能不一樣）："
            show "$TMPD/pull" 3
            continue
        fi
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
        capture migrate timeout "$(cap_time "$MIGRATE_TIMEOUT")" docker run --rm --name adam-deploy-migrate \
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
    capture check-db timeout "$(cap_time "$CHECK_DB_TIMEOUT")" docker run --rm --name adam-deploy-check-db --env-file "$ENV_FILE" \
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
        out=$(inspect_containers "${names[@]}" 2>>"$LOG_FILE") || return 1
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
        --label "adam.commit=$commit" "${RUN_LOG_OPTS[@]}" "$image" || return 1
    for i in "${!WORKERS[@]}"; do
        name=${WORKERS[$i]}
        args=(--name "$name" --hostname "$name" --restart unless-stopped
            --stop-timeout 30 --env-file "$ENV_FILE" -v "$MODELS_DIR:/models:ro"
            --health-cmd "python -m analysis_queue alive" --health-interval 60s --health-start-period 300s
            --label "adam.commit=$commit" "${RUN_LOG_OPTS[@]}" "$image" python worker.py)
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

diagnose() { # 失敗時：stdout 只有 /healthz 的計數；容器的 log（服務編號、S3 的 key、Ollama 的回應…）只寫進 log 檔
    local c
    say "   /healthz：$(health_summary "$LAST_HEALTH")"
    for c in "$API_NAME" "${STARTED[@]}"; do
        capture "logs-$c" dk logs --tail 50 "$c"
    done
    say "   容器的 log（每個最後 50 行）只寫在 EC2 的 $LOG_FILE：$(log_hint)"
}

succeed() {
    write_state "$DEPLOYED_FILE" "$DESIRED"
    rm -f "$FAILED_FILE"
    say "== ready（$READY_AFTER 秒）：$(health_summary "$LAST_HEALTH")"
    # status 有服務編號、失敗工作的錯誤訊息（S3 的 key、Ollama 的回應）：只寫進 log 檔
    capture status dk exec "$API_NAME" python -m analysis_queue status
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
        say "== 上一版 ready（$READY_AFTER 秒）：$(health_summary "$LAST_HEALTH")"
        say "   $DESIRED 記在 $FAILED_FILE：開機時不再試，GitHub 的部署（--retry-failed）會再試"
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
        --sanitize) SANITIZE=1 ;;
        -h | --help)
            usage
            FINISHED=1
            exit 0
            ;;
        *) bad=$arg ;;
        esac
    done
    if [ "$SANITIZE" = 1 ]; then # 不部署、不加鎖：stdin 過濾之後印出來（讀得到 analysis.env 時連它的值一起換掉）
        load_config >/dev/null
        load_env_values
        public
        FINISHED=1
        exit 0
    fi
    exec </dev/null # 不會有人打字；docker、aws 不要等 stdin
    load_config || finish 11 precheck-failed
    load_env_values # 越早越好：之後印到 stdout 的都換得掉
    if [ "$(id -u)" != 0 ]; then
        say "要用 root 執行（sudo /opt/adam/deploy.sh）"
        finish 11 precheck-failed
    fi
    [ -e "$LOG_FILE" ] || install -m 600 /dev/null "$LOG_FILE" 2>/dev/null
    chmod 600 "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null # 完整、不過濾的紀錄：只有 root 讀得到
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
    RUN_START_LINE=$(($(wc -l <"$LOG_FILE" 2>/dev/null || echo 0) + 1))
    log "==== deploy.sh 開始（pid $$；參數：${*:-無}；sha256 $(sha256sum "$0" 2>/dev/null | cut -c 1-12)）"
    read_desired
    deployed=$(first_line "$DEPLOYED_FILE")
    if [ "$DESIRED" = "$deployed" ]; then
        say "已經是這一版，什麼都不用做"
        finish 0 up-to-date
    fi
    failed=$(first_line "$FAILED_FILE")
    if [ "$DESIRED" = "$failed" ] && [ "$RETRY_FAILED" != 1 ]; then
        if rewritten_after_failure; then
            say "這一版之前部署失敗過，但 desired-commit 在那之後又被寫過一次（$DESIRED_MODIFIED）：照樣再試"
        else
            say "這一版之前部署失敗、已經換回過上一版（$FAILED_FILE），開機時不再試。"
            say "要再試：GitHub 重新執行 Deploy analyzer，或 sudo /opt/adam/deploy.sh --retry-failed"
            finish 0 skipped-failed
        fi
    fi
    say "== 部署 $DESIRED（目前：${deployed:-沒有記錄}）"

    precheck || finish 11 precheck-failed
    say "== 拉映像檔"
    pull_images || finish 12 pull-failed

    need_time "跑 migration"
    if [ "$ALLOW_DESTRUCTIVE" = 1 ]; then say "== migration（允許破壞性的）"; else say "== migration"; fi
    run_migrations
    rc=$?
    if [ "$rc" != 0 ] && [ "$rc" != 3 ]; then # migration 都可以重跑：被時間預算中斷的，下一次會接著做
        need_time "換版本（migration 跑到一半時間用完了，被中斷；下一次部署會接著做）"
    fi
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

    need_time "跑 check-db"
    say "== check-db"
    if check_db; then
        say "   $(tail -n 1 "$TMPD/check-db")"
    else
        need_time "換版本（check-db 跑到一半時間用完了）"
        say "check-db 沒過（新版要的資料表、欄位、權限不全），舊版照常跑："
        grep -v '^OK' "$TMPD/check-db" >"$TMPD/check-db.bad"
        show "$TMPD/check-db.bad" 40
        finish 15 check-db-failed
    fi

    need_time "換版本"
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
