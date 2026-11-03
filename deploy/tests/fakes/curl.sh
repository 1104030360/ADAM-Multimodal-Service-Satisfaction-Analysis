#!/bin/bash
# 測試用的假 curl（cases.sh 裝成 /usr/local/bin/curl）：只回答 deploy.sh 的 /healthz。
# 和分析服務的 analysis_queue.health() 一樣從 worker 的心跳算：有 ready 或 busy 就是 ready、只有 loading 是 loading、
# 其他是 failed。心跳在假 docker 的 $FAKE_DIR/db（worker_id 一樣的新舊 worker 寫同一個檔案，和真的資料表一樣）。
# 每一次回答的 models 記在 $FAKE_DIR/healthz.log。
set -uo pipefail
F=${FAKE_DIR:-/tmp/fake}
printf 'curl %s\n' "$*" >>"$F/calls.log"
url=${!#}
api=$F/containers/adam-analysis
if [ "$url" != http://127.0.0.1:5000/healthz ]; then
    echo "fake curl: unsupported URL $url" >&2
    exit 64
fi
if [ ! -d "$api" ] || [ "$(cat "$api/status")" != running ]; then
    echo "curl: (7) Failed to connect to 127.0.0.1 port 5000 after 0 ms: Couldn't connect to server" >&2
    exit 7
fi
if [ "$(cat "$api/behavior")" = api-down ]; then # 起不來、一直被 docker 重新啟動
    echo $(($(cat "$api/restarts") + 1)) >"$api/restarts"
    echo "curl: (52) Empty reply from server" >&2
    exit 52
fi
declare -A n=([loading]=0 [ready]=0 [busy]=0 [failed]=0)
for f in "$F"/db/*; do
    [ -f "$f" ] || continue
    s=$(cat "$f")
    if [ -n "${n[$s]+x}" ]; then n[$s]=$((n[$s] + 1)); fi
done
if [ $((n[ready] + n[busy])) -gt 0 ]; then
    models=ready
elif [ "${n[loading]}" -gt 0 ]; then
    models=loading
else
    models=failed
fi
echo "$models" >>"$F/healthz.log" # 每一次回答的 models（測試看「曾經說過 ready」）
printf '{"jobs":{"queued":0,"running":0},"models":"%s","oldest_pending_sec":0,"outbox_pending":0,"queue_depth":0,"sqs":"configured","stale_jobs":0,"status":"ok","workers":{"busy":%d,"failed":%d,"loading":%d,"ready":%d}}\n' \
    "$models" "${n[busy]}" "${n[failed]}" "${n[loading]}" "${n[ready]}"
