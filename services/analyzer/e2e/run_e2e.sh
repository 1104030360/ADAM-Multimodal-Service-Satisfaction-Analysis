#!/bin/bash
# 移植版分析服務（real-analyze）的端對端測試，在 Mac 上跑：拋棄式的 MySQL 8.4（測試用的 schema＋最小權限的 grants.sql）、
# moto（模擬 S3 和 SQS）、分析服務的 API 容器和 worker 容器（同一個映像檔；worker 載入真的模型、離線）。
# SQS（T49，docs/rebuild/sqs/PLAN.md）：API 收下工作寫進 outbox，送到 moto 的佇列，worker 領走、做完才刪。
# 桌面端那一側（上傳、登記、送分析、報表）由 e2e_steps.py 在同一個映像檔的拋棄式容器裡做，Mac 上不用另外裝東西。
# 跑完一律刪掉這些容器和網路；有任何檢查失敗時結束碼是 1。
#
# 用法：services/analyzer/e2e/run_e2e.sh（在哪個目錄執行都可以）。可以用環境變數改：
#   IMAGE=adam-analyzer:dev     分析服務的映像檔（不會幫你 build）
#   WHISPER_MODEL=medium        small、medium、large-v3-turbo（要先用 download_models.py 下載）
#   RECORDING=S20261101203243   再分析一段使用者的錄影：~/audio/<ID>_audio.wav、~/video/<ID>_cam0.avi、_cam1.avi（唯讀）；
#                               RECORDING= 只分析合成的對話（fixtures/，make_dialogue.py 產生）
#   OUT=<資料夾>                 逐字稿、report-data、圖、報表 PDF、記憶體紀錄（預設 mktemp）。不要放在 repo 裡：錄影是個人資料
#   CODE=<資料夾>                把那個資料夾的 *.py 唯讀掛到 /app，取代映像檔裡的（只改了程式時不用重建映像檔；改了套件要重建）
#   PORT=18080                  分析服務（API）只開在 127.0.0.1 的這個 port
#   RESTART_TEST=1              分析第一筆的途中重新啟動 worker（docker restart）：工作要交還、由重新啟動的 worker 做完，
#                               而且這一次不算一次嘗試（T49 的關機處理）
#   MODELS=~/adam-models        download_models.py 下載的模型，唯讀掛到 /models
#   SCHEMA=<檔案>               MySQL 第一次啟動時載入的 schema（預設是 repo 裡的 myapp/tests/fixtures/schema.sql）
# EC2 上也能跑（不碰 RDS 和真的 S3）：services/analyzer/ 放在 ~/analyzer、myapp/tests/fixtures/schema.sql 放在 ~/schema.sql，
#   IMAGE=adam-analyzer:2 MODELS=/opt/adam/models SCHEMA=~/schema.sql RECORDING= OUT=~/e2e-out bash ~/analyzer/e2e/run_e2e.sh
#   （用 bash 執行：scp 送過去的檔案不一定保留可執行的權限）
# 分析服務不連網（HF_HUB_OFFLINE=1），也不設 OLLAMA_*：報表的 AI 說明和服務結果（W1）在本機不跑。
# 用到的名稱：網路 adam-e2e-net，容器 adam-e2e-mysql、adam-e2e-s3（moto：S3＋SQS）、adam-e2e-api、adam-e2e-worker，
# 和每一步一個（標籤 adam-e2e-step）。
# 不碰 adam-mysql、adam-s3、adam-analysis，也不用 3306、5055、5056、5000。
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ANALYZER=$(dirname "$HERE")
SERVICES=$(dirname "$ANALYZER")
REPO=$(dirname "$SERVICES")
SCHEMA=${SCHEMA:-$REPO/myapp/tests/fixtures/schema.sql}  # git 裡的測試用 schema（migration 001～007 都在裡面）
IMAGE=${IMAGE:-adam-analyzer:dev}
WHISPER_MODEL=${WHISPER_MODEL:-medium}
RECORDING=${RECORDING-S20261101203243}
PORT=${PORT:-18080}
RESTART_TEST=${RESTART_TEST:-}
MODELS=${MODELS:-$HOME/adam-models}
OUT=${OUT:-$(mktemp -d "${TMPDIR:-/tmp}/adam-e2e.XXXXXX")}  # 這種寫法 macOS 和 Linux（EC2）都能用
NET=adam-e2e-net
BUCKET=adam-media-e2e
GRANTS=$ANALYZER/tests/integration/grants.sql
ROOT_PW="e2e-$(openssl rand -hex 8)"
APP_PW=$(sed -n "s/.*IDENTIFIED BY '\([^']*\)'.*/\1/p" "$GRANTS")  # grants.sql 裡 adam_analysis 的密碼（只給測試用）
MOTO=$(awk '$0 == "name = \"moto\"" {getline; gsub(/[^0-9.]/, ""); print; exit}' "$ANALYZER/uv.lock")  # 和整合測試同一版

fail() { echo "$*"; exit 2; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "找不到映像檔 $IMAGE：先在 services/analyzer 執行 docker build -t $IMAGE ."
[ -f "$SCHEMA" ] || fail "找不到 schema：$SCHEMA（EC2 上設定 SCHEMA=<schema.sql 的路徑>）"
[ -f "$HERE/fixtures/dialogue.wav" ] || fail "找不到 fixtures/dialogue.wav：先在 Mac 上執行 python3 $HERE/make_dialogue.py"
[ -d "$MODELS/whisper" ] || fail "找不到 $MODELS/whisper：先用 download_models.py 下載模型"
lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && fail "本機的 $PORT 已經有人在用：設定 PORT=<別的 port>"
MEDIA=()
if [ -n "$RECORDING" ]; then
  for file in "audio/${RECORDING}_audio.wav" "video/${RECORDING}_cam0.avi" "video/${RECORDING}_cam1.avi"; do
    [ -f "$HOME/$file" ] || fail "找不到 ~/$file（只分析合成的對話：RECORDING=）"
    MEDIA+=(-v "$HOME/$file:/media/$(basename "$file"):ro")
  done
fi
CODE_MOUNTS=()
if [ -n "${CODE:-}" ]; then
  CODE=$(cd "$CODE" && pwd) || exit 2
  for file in "$CODE"/*.py; do CODE_MOUNTS+=(-v "$file:/app/$(basename "$file"):ro"); done
fi
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd) || exit 2

# moto 同時是 S3 和 SQS（AWS_ENDPOINT_URL 兩個都用）；它的帳號固定是 123456789012，佇列由 e2e_steps.py prepare 建立
AWS_ENV=(-e S3_BUCKET=$BUCKET -e AWS_REGION=us-east-1 -e AWS_ENDPOINT_URL=http://adam-e2e-s3:5000
         -e AWS_ACCESS_KEY_ID=testing -e AWS_SECRET_ACCESS_KEY=testing -e AWS_EC2_METADATA_DISABLED=true
         -e SQS_ANALYSIS_QUEUE_URL=http://adam-e2e-s3:5000/123456789012/adam-analysis)
DB_ENV=(-e DB_HOST=adam-e2e-mysql -e DB_NAME=adam -e DB_USER=adam_analysis -e DB_PASSWORD="$APP_PW")

cleanup() {
  [ -n "${SAMPLER:-}" ] && kill "$SAMPLER" 2>/dev/null
  docker rm -fv adam-e2e-api adam-e2e-worker adam-e2e-s3 adam-e2e-mysql $(docker ps -aq --filter label=adam-e2e-step) \
    >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT
trap 'exit 130' INT TERM
cleanup  # 上次中斷留下的

step() {  # e2e_steps.py 的一步，在同一個映像檔的拋棄式容器裡跑
  # 用執行這個腳本的使用者身分跑：映像檔預設的 uid 10001 寫不進 EC2 上屬於 ec2-user 的 $OUT（Mac 的 Docker Desktop 會自己對應）
  docker run --rm --label adam-e2e-step --user "$(id -u):$(id -g)" -e HOME=/tmp --network "$NET" "${AWS_ENV[@]}" "${DB_ENV[@]}" \
    -e DB_ROOT_PASSWORD="$ROOT_PW" -e API=http://adam-e2e-api:5000 -e WHISPER_MODEL="$WHISPER_MODEL" \
    -v "$HERE:/e2e:ro" -v "$OUT:/out" ${MEDIA[@]+"${MEDIA[@]}"} "$IMAGE" python /e2e/e2e_steps.py "$@"
}

event() { echo "$(date +%s) event $*" >> "$OUT/memory.log"; }

sample_memory() {  # worker（載入模型、做分析的那一個）用了多少記憶體，大約每 2～3 秒一筆（docker stats 一次要 1～2 秒）
  # docker stats 不算別的容器先讀進來的檔案快取：模型檔是 mmap 的（safetensors），換成剛開機的 EC2 時這些也要算。
  # 所以另外記容器裡所有行程的 RSS 加總（含已經讀進記憶體的模型檔）
  local usage rss
  while usage=$(docker stats --no-stream --format '{{.MemUsage}}' adam-e2e-worker 2>/dev/null); do
    rss=$(docker exec adam-e2e-worker sh -c 'cat /proc/[0-9]*/status' 2>/dev/null \
      | awk '$1 == "VmRSS:" {kib += $2} END {print kib + 0}')
    echo "$(date +%s) sample ${usage%% /*} ${rss}KiB" >> "$OUT/memory.log"
    sleep 1
  done
}

wait_ready() {  # 等 /healthz 說有 worker 載好模型了（worker 的心跳，tbl_analysis_worker）；載入失敗、容器停了、或 15 分鐘還沒好就放棄
  # worker 還沒登記之前 models 也是 failed（沒有 worker 活著），所以載入失敗要看 workers 裡的 failed 是不是大於 0
  local health state
  for _ in $(seq 450); do
    health=$(curl -s "http://127.0.0.1:$PORT/healthz")
    state=$(sed -n 's/.*"models": *"\([a-z]*\)".*/\1/p' <<< "$health")
    [ "$state" = ready ] && return 0
    grep -q '"failed": *[1-9]' <<< "$health" && return 1
    [ "$(docker inspect -f '{{.State.Running}}' adam-e2e-api 2>/dev/null)" = true ] || return 1
    [ "$(docker inspect -f '{{.State.Running}}' adam-e2e-worker 2>/dev/null)" = true ] || return 1
    sleep 2
  done
  return 1
}

render() {  # 報表的第 1 頁轉成 PNG：桌面端的 uv 環境有 PyMuPDF（沒有就跳過）
  local python=$REPO/myapp/.venv/bin/python
  [ -x "$python" ] || return 0
  "$python" -I -c 'import sys, fitz; fitz.open(sys.argv[1])[0].get_pixmap(dpi=110).save(sys.argv[2])' "$1" "$2" \
    && echo "     報表第 1 頁：$2"
}

echo "== 啟動 MySQL 8.4、moto $MOTO（S3＋SQS；輸出在 $OUT）"
docker network create "$NET" >/dev/null || exit 1
docker run -d --name adam-e2e-mysql --network "$NET" -e MYSQL_ROOT_PASSWORD="$ROOT_PW" \
  -v "$SCHEMA:/docker-entrypoint-initdb.d/1-schema.sql:ro" \
  -v "$GRANTS:/docker-entrypoint-initdb.d/2-grants.sql:ro" mysql:8.4 >/dev/null || exit 1
docker run -d --name adam-e2e-s3 --network "$NET" python:3.12-slim \
  sh -c "pip install -q --root-user-action=ignore 'moto[server]==$MOTO' && moto_server -H 0.0.0.0 -p 5000" >/dev/null || exit 1
if ! step prepare; then
  docker logs --tail 20 adam-e2e-mysql 2>&1
  docker logs --tail 20 adam-e2e-s3 2>&1
  exit 1
fi

SIDS=("S$(TZ=America/Los_Angeles date +%Y%m%d%H%M%S)")  # 合成的對話：和桌面端一樣，ID 是開始錄影的時間（服務現場的時區）
step add "${SIDS[0]}" /e2e/fixtures/dialogue.wav --dialogue || exit 1
if [ -n "$RECORDING" ]; then
  step add "$RECORDING" "/media/${RECORDING}_audio.wav" \
    --video "/media/${RECORDING}_cam0.avi" "/media/${RECORDING}_cam1.avi" || exit 1
  SIDS+=("$RECORDING")
fi

echo
echo "== 啟動分析服務：$IMAGE（API＋worker），WHISPER_MODEL=$WHISPER_MODEL${CODE:+，程式來自 $CODE}"
loading=$(date +%s)
event analyzer-start
# API：映像檔預設的 CMD（gunicorn app:app），不載入模型，不用掛 /models
docker run -d --name adam-e2e-api --network "$NET" -p "127.0.0.1:$PORT:5000" "${AWS_ENV[@]}" "${DB_ENV[@]}" \
  -e DB_TIME_ZONE=America/Los_Angeles ${CODE_MOUNTS[@]+"${CODE_MOUNTS[@]}"} "$IMAGE" >/dev/null || exit 1
# worker：同一個映像檔，python worker.py。和 EC2 一樣給 30 秒的 stop timeout（long polling 最多 20 秒）
docker run -d --name adam-e2e-worker --hostname adam-e2e-worker --network "$NET" --stop-timeout 30 \
  "${AWS_ENV[@]}" "${DB_ENV[@]}" -e DB_TIME_ZONE=America/Los_Angeles -e HF_HUB_OFFLINE=1 -e WHISPER_MODEL="$WHISPER_MODEL" \
  -v "$MODELS:/models:ro" ${CODE_MOUNTS[@]+"${CODE_MOUNTS[@]}"} "$IMAGE" python worker.py >/dev/null || exit 1
sample_memory &
SAMPLER=$!
if ! wait_ready; then
  echo "FAIL 模型沒有載入（$(( $(date +%s) - loading )) 秒）"
  docker logs --tail 20 adam-e2e-api 2>&1
  docker logs --tail 40 adam-e2e-worker 2>&1
  exit 1
fi
event ready
echo "PASS /healthz：模型載入完成，$(( $(date +%s) - loading )) 秒"

restart_worker_during() {  # RESTART_TEST=1：等 worker 開始做這一筆，10 秒後 docker restart（SIGTERM → 交還 → 重新載入模型 → 接著做）
  local sid=$1
  for _ in $(seq 300); do
    docker logs adam-e2e-worker 2>&1 | grep -q "job $sid start" && break
    sleep 1
  done
  sleep 10
  echo "     重新啟動 worker（分析 $sid 的途中）"
  event restart "$sid"
  docker restart adam-e2e-worker >/dev/null
}

FAILED=0
RESTARTED=
for sid in "${SIDS[@]}"; do
  event start "$sid"
  if [ -n "$RESTART_TEST" ] && [ -z "$RESTARTED" ]; then
    RESTARTED=$sid
    restart_worker_during "$sid" &
    RESTARTER=$!
    step analyze "$sid" --restarted
    analyzed=$?
    wait "$RESTARTER"
  else
    step analyze "$sid"
    analyzed=$?
  fi
  event end "$sid"
  if [ $analyzed -ne 0 ]; then
    FAILED=1
    continue
  fi
  step verify "$sid" || FAILED=1
  step report "$sid" || FAILED=1
  [ -f "$OUT/$sid/report.pdf" ] && render "$OUT/$sid/report.pdf" "$OUT/$sid/report-page1.png"
done

peak=$(docker exec adam-e2e-worker cat /sys/fs/cgroup/memory.peak 2>/dev/null) \
  && echo "$(date +%s) cgroup-peak $peak" >> "$OUT/memory.log"
kill "$SAMPLER" 2>/dev/null
SAMPLER=
step summary "${SIDS[@]}"

docker logs adam-e2e-api > "$OUT/api.log" 2>&1
docker logs adam-e2e-worker > "$OUT/analyzer.log" 2>&1  # 每一步的時間（speech: … whisper … s）；逐字稿不會寫進 log
lines=$([ $FAILED -eq 0 ] && echo 15 || echo 40)
echo
echo "== worker 的 log（最後 $lines 行，全部在 analyzer.log；API 的在 api.log；worker 容器：$(docker inspect -f \
  '{{.State.Status}}，OOMKilled={{.State.OOMKilled}}，重新啟動 {{.RestartCount}} 次' adam-e2e-worker)）"
tail -n $lines "$OUT/analyzer.log"
echo
echo "輸出在 $OUT"
exit $FAILED
