#!/bin/bash
# 測試用的假 docker（cases.sh 裝成 /usr/local/bin/docker）。只認 deploy.sh、install.sh 用到的子指令、參數和格式，
# 其他的一律失敗（結束碼 64）：deploy.sh 用錯參數時測試就會發現。
#
# 狀態在 $FAKE_DIR（預設 /tmp/fake）：
#   tags                      一行一個「ref 映像檔ID」
#   images/<ID>/behavior      這個映像檔跑起來的行為（見下面）
#   containers/<名字>/<欄位>  image、ref、role（api｜worker）、behavior、hostname、status、exit、restarts、started、polls、
#                             labels、argv（printf %q，看得出哪幾個字是同一個參數）
#   db/<worker_id>            tbl_analysis_worker 的狀態（假 curl 的 /healthz 從這裡算；worker_id 是「hostname:1」）
#   calls.log                 每一次呼叫一行；環境變數裡有 DB_PASSWORD 時加上 [env DB_PASSWORD]（不寫值）
#   migrate-env               migration 那一次拿到的參數和連線設定（密碼只寫 matches 或 wrong-or-missing）
# 情境在 $FAKE_DIR/env（cases.sh 寫；後面的蓋掉前面的）：
#   FAKE_DOCKER_DOWN=1        daemon 沒在跑：每個指令都失敗
#   FAKE_PULL_FAIL=<regex>    符合的 ref 拉不到（manifest unknown）
#   FAKE_PULL_FLAKY=<n>       前 n 次 pull 失敗（網路逾時），之後成功
#   FAKE_RUN_FAIL=<regex>     用符合的映像檔 run -d／create 失敗（port 被占用）
#   FAKE_NEW_BEHAVIOR=good    pull 下來的 adam-analyzer 的行為：
#                               good     worker 第 1 次問 loading，之後 ready
#                               never    worker 一直 loading
#                               fail     worker loading，之後 failed（載入模型失敗）
#                               crash    worker 一直重新啟動（docker exec 失敗、RestartCount 一直加）
#                               api-down API 不回應（RestartCount 一直加），worker 和 good 一樣
#                               stale-ready worker 的狀態檔是重新啟動之前的 process 留下的 ready（比這一次啟動早），
#                                        這一次的 process 一直在載入
#   FAKE_MIGRATE_RC=0         migration 的 runner 的結束碼
#   FAKE_CHECKDB_RC=0         check-db 的結束碼
#   FAKE_STOP_KILLS=1         docker stop 時 worker 30 秒內沒停下來（137）：沒登記 stopped，db 留著它最後的狀態
#   FAKE_IMAGE_KB=6291456     有 $FAKE_DIR/df-avail-kb 時，pull、刪掉一個映像檔，假 df 的剩餘空間跟著減、加
#   FAKE_LOG_LINES=<n>        docker logs 每個容器有 n 行、一行 400 個字（預設 2 行短的）
# 準備狀態用的子指令（不記進 calls.log）：_fake-image <ref> <behavior>、_fake-tag <新的 ref> <已有的 ref>、
#   _fake-container <名字> <ref> <api|worker> <running|exited|created>
set -uo pipefail
F=${FAKE_DIR:-/tmp/fake}
mkdir -p "$F/images" "$F/containers" "$F/db"
touch "$F/tags" "$F/calls.log"
# shellcheck source=/dev/null
if [ -f "$F/env" ]; then . "$F/env"; fi

INSPECT_FMT='{{.Name}} {{.Image}} {{.State.Status}} {{.State.ExitCode}} {{.RestartCount}} {{.State.StartedAt}}'
NEVER_STARTED=0001-01-01T00:00:00Z
JOB_IMAGE='' JOB_ENVS=() JOB_CMD=()

unsupported() {
    echo "fake docker: unsupported: $*" >&2
    exit 64
}
tag_id() { awk -v r="$1" '$1 == r { print $2 }' "$F/tags" | tail -n 1; }
tags_of() { awk -v i="$1" '$2 == i { print $1 }' "$F/tags"; }
untag() {
    local rest
    rest=$(awk -v r="$1" '$1 != r' "$F/tags")
    if [ -n "$rest" ]; then printf '%s\n' "$rest" >"$F/tags"; else : >"$F/tags"; fi
}
set_tag() {
    untag "$1"
    printf '%s %s\n' "$1" "$2" >>"$F/tags"
}
resolve() { # ref 或 ID → ID
    local ref=$1 id
    if [[ $ref == sha256:* ]]; then
        [ -d "$F/images/$ref" ] && echo "$ref"
        return
    fi
    [[ ${ref##*/} == *:* ]] || ref=$ref:latest
    id=$(tag_id "$ref")
    [ -n "$id" ] && [ -d "$F/images/$id" ] && echo "$id"
}
new_id() { echo "sha256:$(printf '%s' "$1" | sha256sum | cut -c 1-64)"; }
cget() { cat "$F/containers/$1/$2" 2>/dev/null; }
cset() { printf '%s\n' "$3" >"$F/containers/$1/$2"; }
is_running() {
    local s
    s=$(cget "$1" status)
    [ "$s" = running ] || [ "$s" = restarting ]
}
in_use() {
    local c
    for c in "$F"/containers/*/; do
        [ -f "$c/image" ] && [ "$(cat "$c/image")" = "$1" ] && return 0
    done
    return 1
}
df_add() {
    [ -f "$F/df-avail-kb" ] || return 0
    echo $(($(cat "$F/df-avail-kb") + $1)) >"$F/df-avail-kb"
}
now() { date -u +%Y-%m-%dT%H:%M:%S.%NZ; }

# ---------- 準備狀態 ----------
fake_image() {
    local id
    id=$(new_id "$1")
    mkdir -p "$F/images/$id"
    echo "$2" >"$F/images/$id/behavior"
    set_tag "$1" "$id"
}
fake_container() { # <名字> <ref> <role> <status>
    local id
    id=$(resolve "$2")
    [ -n "$id" ] || unsupported "_fake-container: no image $2"
    mkdir -p "$F/containers/$1"
    cset "$1" image "$id"
    cset "$1" ref "$2"
    cset "$1" role "$3"
    cset "$1" behavior "$(cat "$F/images/$id/behavior")"
    cset "$1" hostname "$1"
    cset "$1" status "$4"
    cset "$1" exit 0
    cset "$1" restarts 0
    cset "$1" polls 9
    cset "$1" labels ''
    cset "$1" argv ''
    if [ "$4" = created ]; then cset "$1" started "$NEVER_STARTED"; else cset "$1" started "$(now)"; fi
    if [ "$3" = worker ] && [ "$4" = running ]; then echo ready >"$F/db/$1:1"; fi
}

# ---------- 子指令 ----------
do_pull() {
    [ $# = 2 ] && [ "$1" = -q ] || unsupported pull "$@"
    local ref=$2 n behavior=''
    if [ -n "${FAKE_PULL_FAIL:-}" ] && [[ $ref =~ $FAKE_PULL_FAIL ]]; then
        echo "Error response from daemon: manifest unknown" >&2
        exit 1
    fi
    n=$(cat "$F/pull-failures" 2>/dev/null || echo 0)
    if [ "$n" -lt "${FAKE_PULL_FLAKY:-0}" ]; then
        echo $((n + 1)) >"$F/pull-failures"
        echo "error pulling image configuration: download failed after attempts=6: dial tcp 140.82.112.33:443: i/o timeout" >&2
        exit 1
    fi
    if [[ $ref == */adam-analyzer:* ]]; then behavior=${FAKE_NEW_BEHAVIOR:-good}; fi
    fake_image "$ref" "$behavior"
    df_add $((-${FAKE_IMAGE_KB:-6291456}))
    echo "$ref"
}

do_image() {
    local id ref fmt repo
    case ${1:-} in
    inspect)
        [ $# = 4 ] && [ "$2" = -f ] || unsupported image "$@"
        id=$(resolve "$4")
        if [ -z "$id" ]; then
            echo "Error: No such image: $4" >&2
            exit 1
        fi
        case $3 in
        '{{.Id}}') echo "$id" ;;
        '{{range .RepoTags}}{{println .}}{{end}}')
            tags_of "$id"
            echo
            ;;
        *) unsupported image "$@" ;;
        esac
        ;;
    ls)
        [ "${2:-}" = --format ] && [ $# -le 4 ] || unsupported image "$@"
        fmt=$3 repo=${4:-}
        while read -r ref id; do
            [ -n "$ref" ] || continue
            if [ -n "$repo" ] && [[ $ref != "$repo:"* ]]; then continue; fi
            case $fmt in
            '{{.Repository}}:{{.Tag}}') echo "$ref" ;;
            '{{.Repository}}:{{.Tag}} {{.Size}}') echo "$ref 6.2GB" ;;
            *) unsupported image "$@" ;;
            esac
        done <"$F/tags"
        ;;
    *) unsupported image "$@" ;;
    esac
}

do_rmi() {
    [ $# = 1 ] || unsupported rmi "$@"
    local ref=$1 id others
    id=$(resolve "$ref")
    if [ -z "$id" ]; then
        echo "Error response from daemon: No such image: $ref" >&2
        exit 1
    fi
    others=$(tags_of "$id" | grep -cvxF -- "$ref")
    if [ "$others" = 0 ] && in_use "$id"; then
        echo "Error response from daemon: conflict: unable to remove repository reference \"$ref\" (must force) - container is using its referenced image ${id:7:12}" >&2
        exit 1
    fi
    untag "$ref"
    echo "Untagged: $ref"
    if [ "$others" = 0 ]; then
        rm -rf "${F:?}/images/$id"
        echo "Deleted: $id"
        df_add "${FAKE_IMAGE_KB:-6291456}"
    fi
}

do_inspect() {
    [ $# -ge 3 ] && [ "$1" = -f ] && [ "$2" = "$INSPECT_FMT" ] || unsupported inspect "$@"
    shift 2
    local n rc=0
    for n in "$@"; do
        if [ -d "$F/containers/$n" ]; then
            printf '/%s %s %s %s %s %s\n' "$n" "$(cget "$n" image)" "$(cget "$n" status)" "$(cget "$n" exit)" \
                "$(cget "$n" restarts)" "$(cget "$n" started)"
        else
            echo "Error: No such object: $n" >&2
            rc=1
        fi
    done
    exit "$rc"
}

do_stop() { # docker stop（worker 有 --stop-timeout 30：好好停下來會登記 stopped）
    [ $# -ge 1 ] || unsupported stop
    local n rc=0
    for n in "$@"; do
        if [ ! -d "$F/containers/$n" ]; then
            echo "Error response from daemon: No such container: $n" >&2
            rc=1
            continue
        fi
        if is_running "$n"; then
            if [ "$(cget "$n" role)" = worker ] && [ "${FAKE_STOP_KILLS:-0}" = 1 ]; then
                cset "$n" exit 137
            else
                cset "$n" exit 0
                if [ "$(cget "$n" role)" = worker ]; then echo stopped >"$F/db/$(cget "$n" hostname):1"; fi
            fi
            cset "$n" status exited
        fi
        echo "$n"
    done
    exit "$rc"
}

do_rm() { # rm -f 跑著的 worker 等於 SIGKILL：db 留著它最後的狀態
    local force=0 n rc=0
    if [ "${1:-}" = -f ]; then
        force=1
        shift
    fi
    [ $# -ge 1 ] || unsupported rm
    for n in "$@"; do
        if [ ! -d "$F/containers/$n" ]; then
            echo "Error response from daemon: No such container: $n" >&2
            rc=1
            continue
        fi
        if [ "$force" = 0 ] && is_running "$n"; then
            echo "Error response from daemon: cannot remove container \"/$n\": container is running: stop the container before removing or force remove" >&2
            rc=1
            continue
        fi
        rm -rf "${F:?}/containers/$n"
        echo "$n"
    done
    exit "$rc"
}

run_job() { # --rm 的一次性容器：migration 的 runner、check-db
    local e rc
    for e in "${JOB_ENVS[@]}"; do
        if [[ $e == *=* ]]; then # 值放在參數裡：ps、/proc 看得到
            echo "fake docker: -e ${e%%=*}=… puts the value on the command line" >&2
            exit 125
        fi
    done
    if [[ $JOB_IMAGE == */adam-db-migrations:* ]]; then
        {
            printf 'args=%s\n' "${JOB_CMD[*]}"
            for e in "${JOB_ENVS[@]}"; do
                if [ "$e" = DB_PASSWORD ]; then
                    if [ -n "${DB_PASSWORD:-}" ] && [ "$DB_PASSWORD" = "${FAKE_PASSWORD:-}" ]; then
                        echo DB_PASSWORD=matches
                    else
                        echo DB_PASSWORD=wrong-or-missing
                    fi
                else
                    printf '%s=%s\n' "$e" "${!e-<unset>}"
                fi
            done
        } >"$F/migrate-env"
        rc=${FAKE_MIGRATE_RC:-0}
        echo "migrate: baseline 001-007 recorded earlier"
        if [ "$rc" = 3 ]; then echo "migrate: pending 008_drop_old_column.sql is destructive (DROP COLUMN)"; fi
        if [ "$rc" = 0 ]; then echo "migrate: result=up-to-date code=0"; else echo "migrate: result=failed code=$rc"; fi
        exit "$rc"
    fi
    if [ "${JOB_CMD[*]}" = "python -m analysis_queue check-db" ]; then
        echo "OK      table tbl_analysis_job"
        if [ "${FAKE_CHECKDB_RC:-0}" != 0 ]; then
            echo "MISSING column tbl_service.service_outcome"
            echo "check-db: 1 missing"
        else
            echo "check-db: 42 OK"
        fi
        exit "${FAKE_CHECKDB_RC:-0}"
    fi
    unsupported "run --rm $JOB_IMAGE ${JOB_CMD[*]}"
}

do_run() { # run -d／run --rm／create
    local verb=$1
    shift
    local argv detach=0 rmflag=0 name='' hostname='' image='' id role=api labels=()
    argv=$(printf '%q ' "$@")
    JOB_ENVS=()
    while [ $# -gt 0 ]; do
        case $1 in
        -d)
            [ "$verb" = run ] || unsupported "$verb -d"
            detach=1
            ;;
        --rm) rmflag=1 ;;
        --name)
            name=$2
            shift
            ;;
        --hostname)
            hostname=$2
            shift
            ;;
        --label)
            labels+=("$2")
            shift
            ;;
        -e)
            JOB_ENVS+=("$2")
            shift
            ;;
        --restart | --env-file | -p | -v | --health-cmd | --health-interval | --health-start-period | --stop-timeout | --log-opt) shift ;;
        -*) unsupported "$verb option $1" ;;
        *)
            image=$1
            shift
            break
            ;;
        esac
        shift
    done
    [ -n "$image" ] || unsupported "$verb without an image"
    id=$(resolve "$image")
    if [ -z "$id" ]; then
        echo "Unable to find image '$image' locally" >&2
        exit 125
    fi
    if [ -n "$name" ] && [ -d "$F/containers/$name" ]; then
        echo "docker: Error response from daemon: Conflict. The container name \"/$name\" is already in use." >&2
        exit 125
    fi
    if [ "$rmflag" = 1 ]; then
        [ "$verb" = run ] && [ "$detach" = 0 ] || unsupported "$verb --rm -d"
        JOB_IMAGE=$image JOB_CMD=("$@")
        run_job
    fi
    [ -n "$name" ] || unsupported "$verb without --name"
    [ "$verb" = create ] || [ "$detach" = 1 ] || unsupported "run in the foreground: $name" # 會一直卡住
    if [ "$*" = "python worker.py" ]; then
        role=worker
    elif [ $# -gt 0 ]; then
        unsupported "$verb command $*"
    fi
    if [ -n "${FAKE_RUN_FAIL:-}" ] && [[ $image =~ $FAKE_RUN_FAIL ]]; then
        echo "docker: Error response from daemon: driver failed programming external connectivity: Bind for 0.0.0.0:5000 failed: port is already allocated." >&2
        exit 125
    fi
    mkdir -p "$F/containers/$name"
    cset "$name" image "$id"
    cset "$name" ref "$image"
    cset "$name" role "$role"
    cset "$name" behavior "$(cat "$F/images/$id/behavior" 2>/dev/null)"
    cset "$name" hostname "${hostname:-$name}"
    cset "$name" exit 0
    cset "$name" restarts 0
    cset "$name" polls 0
    printf '%s\n' "${labels[@]}" >"$F/containers/$name/labels"
    printf '%s\n' "$argv" >"$F/containers/$name/argv"
    if [ "$verb" = run ]; then
        cset "$name" status running
        cset "$name" started "$(now)"
    else
        cset "$name" status created
        cset "$name" started "$NEVER_STARTED"
    fi
    printf '%s' "$name" | sha256sum | cut -c 1-64
}

alive() { # deploy.sh 用 docker exec 讀 worker 自己的 ALIVE_FILE：每問一次，假 worker 往前走一步
    local n=$1 b polls state
    b=$(cget "$n" behavior)
    if [ "$b" = crash ]; then
        cset "$n" restarts $(($(cget "$n" restarts) + 1))
        cset "$n" status restarting
        echo "Error response from daemon: Container $n is restarting, wait until the container is running" >&2
        return 1
    fi
    if [ "$(cget "$n" status)" != running ]; then
        echo "Error response from daemon: container $n is not running" >&2
        return 1
    fi
    polls=$(($(cget "$n" polls) + 1))
    cset "$n" polls "$polls"
    if [ "$b" = stale-ready ] && [ "$polls" = 1 ]; then # 這一次的 process 還沒登記過：db 不動
        echo "ready $(($(date -d "$(cget "$n" started)" +%s) - 5))"
        return 0
    fi
    case $b in
    good | api-down) if [ "$polls" -ge 2 ]; then state=ready; else state=loading; fi ;;
    fail) if [ "$polls" -ge 2 ]; then state=failed; else state=loading; fi ;;
    *) state=loading ;;
    esac
    echo "$state" >"$F/db/$(cget "$n" hostname):1"
    echo "$state $(date +%s)"
}

do_exec() {
    [ $# -ge 2 ] || unsupported exec "$@"
    local n=$1
    shift
    if [ ! -d "$F/containers/$n" ]; then
        echo "Error response from daemon: No such container: $n" >&2
        exit 1
    fi
    if [ $# = 3 ] && [ "$1" = python ] && [ "$2" = -c ] && [[ $3 == *ALIVE_FILE* ]]; then
        alive "$n"
        exit
    fi
    if [ "$*" = "python -m analysis_queue status" ]; then
        if ! is_running "$n"; then
            echo "Error response from daemon: container $n is not running" >&2
            exit 1
        fi
        printf '{\n  "models": "ready",\n  "workers": {"loading": 0, "ready": 1, "busy": 0, "failed": 0}\n}\n\n'
        printf 'workers (seen in the last day):\n  fake-status-marker\n'
        exit 0
    fi
    unsupported exec "$n" "$@"
}

# ---------- 進入點 ----------
[ $# -gt 0 ] || unsupported "(no arguments)"
case $1 in
_fake-*) ;;
*)
    mark=''
    if [ -n "${DB_PASSWORD+x}" ]; then mark=' [env DB_PASSWORD]'; fi
    printf 'docker %s%s\n' "$*" "$mark" >>"$F/calls.log"
    if [ "${FAKE_DOCKER_DOWN:-0}" = 1 ]; then
        echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2
        exit 1
    fi
    ;;
esac
cmd=$1
shift
case $cmd in
_fake-image) fake_image "$1" "${2:-}" ;;
_fake-tag)
    id=$(resolve "$2")
    [ -n "$id" ] || unsupported "_fake-tag: no image $2"
    set_tag "$1" "$id"
    ;;
_fake-container) fake_container "$@" ;;
info)
    case $* in
    "--format {{.DockerRootDir}}") echo /var/lib/docker ;;
    "--format {{.ServerVersion}}") echo 25.0.8 ;;
    *) unsupported info "$@" ;;
    esac
    ;;
pull) do_pull "$@" ;;
image) do_image "$@" ;;
tag)
    [ $# = 2 ] || unsupported tag "$@"
    id=$(resolve "$1")
    if [ -z "$id" ]; then
        echo "Error response from daemon: No such image: $1" >&2
        exit 1
    fi
    set_tag "$2" "$id"
    ;;
rmi) do_rmi "$@" ;;
ps)
    [ "$*" = "-a --format {{.Names}}" ] || unsupported ps "$@"
    ls -1 "$F/containers"
    ;;
inspect) do_inspect "$@" ;;
stop) do_stop "$@" ;;
rm) do_rm "$@" ;;
run | create) do_run "$cmd" "$@" ;;
exec) do_exec "$@" ;;
logs)
    [ $# = 3 ] && [ "$1" = --tail ] || unsupported logs "$@"
    if [ ! -d "$F/containers/$3" ]; then
        echo "Error response from daemon: No such container: $3" >&2
        exit 1
    fi
    if [ -n "${FAKE_LOG_LINES:-}" ]; then
        pad=$(printf '%400s' '' | tr ' ' x)
        for i in $(seq 1 "$((FAKE_LOG_LINES < $2 ? FAKE_LOG_LINES : $2))"); do printf 'fake log line %s of %s %s\n' "$i" "$3" "$pad"; done
    else
        printf 'fake log line 1 of %s\nfake log line 2 of %s\n' "$3" "$3"
    fi
    ;;
*) unsupported "$cmd" "$@" ;;
esac
