#!/bin/bash
# deploy/tests/cases.sh — deploy.sh、install.sh、adam-deploy.service 的每一條路。run.sh 在 amazonlinux:2023 的容器裡
# 以 root 執行（直接跑會改到這台機器的 /opt/adam、/usr/local/bin，不要在 EC2 或自己的電腦上跑）。
# 假的 docker、aws、curl、df、systemctl 在 fakes/，裝在 /usr/local/bin（deploy.sh 的 PATH 先找那裡）。
# 每個情境從乾淨的 /opt/adam、/tmp/fake 開始；deploy.sh 用 env -i 執行（和 SSM Run Command 一樣幾乎沒有環境變數）。
set -uo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
F=/tmp/fake
OUT=/tmp/deploy-out
LOG=/var/log/adam-deploy.log
GHCR=ghcr.io/1104030360/adam-analyzer
MIG=ghcr.io/1104030360/adam-db-migrations
OLDER=3333333333333333333333333333333333333333 # 更早部署過的 commit
OLD=1111111111111111111111111111111111111111   # 上一次部署的 commit
NEW=2222222222222222222222222222222222222222   # desired-commit
PW=adam-test-only-migrator-7Kq9Zr              # 測試專用的密碼：不能出現在任何輸出、log、參數裡
DBHOST=adam-db.c1a2b3c4d5e6.us-east-1.rds.amazonaws.com # analysis.env 的 DB_HOST（假的，形狀和 RDS 的一樣）
BUCKET=adam-media-123456789012-us-east-1
SQSURL=https://sqs.us-east-1.amazonaws.com/123456789012/adam-analysis
# deploy.sh 的 stdout 會公開在 GitHub Actions 的 log：假的 docker、aws 的輸出裡帶著這些，stdout 裡一個都不能有
LEAKS=(adam-db.c1a2b3c4d5e6 rds.amazonaws.com 172.31. ip-172-31 140.82.112.33 123456789012 adam-media-
    sqs.us-east-1.amazonaws.com S20261101203243 i-0123456789abcdef0 "fake log line" fake-status-marker)
PASS=0 FAIL=0 CASES=0 CASE='' SHOWN='' RC=0

if [ "$(id -u)" != 0 ] || [ ! -f /etc/amazon-linux-release ]; then
    echo "cases.sh 只在 run.sh 開的 amazonlinux:2023 容器裡跑（它會改 /opt/adam、/usr/local/bin）"
    exit 2
fi
for f in docker aws curl df systemctl; do install -m 755 "$SRC/tests/fakes/$f.sh" "/usr/local/bin/$f"; done

# ---------- 小工具 ----------
ok() { PASS=$((PASS + 1)); }
bad() {
    FAIL=$((FAIL + 1))
    echo "  FAIL [$CASE] $1"
    if [ -z "$SHOWN" ]; then
        SHOWN=1
        echo "  ---- 輸出"
        sed 's/^/  | /' "$OUT" 2>/dev/null
        echo "  ---- calls.log"
        sed 's/^/  | /' "$F/calls.log" 2>/dev/null
    fi
}
check() { # check <說明> <指令…>
    local what=$1
    shift
    if "$@"; then ok; else bad "$what"; fi
}
has() { grep -qsF -- "$2" "$1"; }
lacks() { ! grep -qsF -- "$2" "$1"; } # 檔案不存在也算沒有
called() { grep -qxF -- "$1" "$F/calls.log"; }    # 完全一樣的一行
called_re() { grep -qE -- "$1" "$F/calls.log"; } # 符合的一行
not_called_re() { ! grep -qE -- "$1" "$F/calls.log"; }
count_re() { grep -cE -- "$1" "$F/calls.log"; }
line_of() { grep -nE -- "$1" "$F/calls.log" | head -n 1 | cut -d: -f1; }
is() { [ "$1" = "$2" ]; }
cfield() { cat "$F/containers/$1/$2" 2>/dev/null; } # cfield <容器> <欄位>
image_of() { awk -v r="$1" '$1 == r { print $2 }' "$F/tags" | tail -n 1; }
has_image() { [ -n "$(image_of "$1")" ]; }
no_image() { [ -z "$(image_of "$1")" ]; }
no_match() { ! compgen -G "$1" >/dev/null; }
scenario() { printf '%s\n' "$@" >>"$F/env"; } # scenario FAKE_X=… …（後面的蓋掉前面的）

begin() { # begin <情境>：乾淨的 EC2
    CASE=$1 SHOWN='' CASES=$((CASES + 1))
    echo "-- $CASE"
    rm -rf "$F" /opt/adam "$LOG" "$LOG.1" /run/adam-deploy.lock /home/ec2-user /etc/systemd/system/adam-deploy.service "$OUT"
    mkdir -p "$F" /opt/adam/models /var/lib/docker
    : >"$F/calls.log"
    : >"$F/tags"
    printf 'FAKE_DESIRED=%s\nFAKE_PASSWORD=%s\n' "$NEW" "$PW" >"$F/env"
    install -m 755 "$SRC/deploy.sh" /opt/adam/deploy.sh
    install -m 600 /dev/null /opt/adam/analysis.env
    printf 'DB_HOST=%s\nDB_PORT=3306\nDB_NAME=adam\nDB_USER=adam_analysis\nDB_PASSWORD=analysis-test-only\nS3_BUCKET=%s\nSQS_ANALYSIS_QUEUE_URL=%s\nAWS_REGION=us-east-1\n' \
        "$DBHOST" "$BUCKET" "$SQSURL" >/opt/adam/analysis.env
    # HEALTH_INTERVAL=0：假 worker 每被問一次往前走一步，不用真的等；HEALTH_TIMEOUT 放寬，機器很忙時成功的路徑也不會逾時。
    # 要等到逾時的情境用 short_timeout 改成 4 秒
    printf 'HEALTH_TIMEOUT=60\nHEALTH_INTERVAL=0\nPULL_RETRY_DELAY=0\nLOCK_WAIT=1\n' >/opt/adam/deploy.conf
}
conf() { printf '%s\n' "$@" >>/opt/adam/deploy.conf; }
short_timeout() { conf HEALTH_TIMEOUT=4; } # 新版要等到逾時的情境（換回去的那一次也用 4 秒，假 worker 問兩次就 ready）
said_ready() { grep -qx ready "$F/healthz.log" 2>/dev/null; } # /healthz 曾經回答 models: ready

ec2_today() { # 現在的 EC2：手動建的 adam-analyzer:1、:2，API＋worker-1 跑 :2
    docker _fake-image adam-analyzer:1 good
    docker _fake-image adam-analyzer:2 "${1:-good}"
    docker _fake-container adam-analysis adam-analyzer:2 api running
    docker _fake-container adam-analysis-worker-1 adam-analyzer:2 worker running
}
after_ghcr_deploy() { # 之前用這個腳本部署過 $OLD（previous 是 :2）
    docker _fake-image adam-analyzer:2 good
    docker _fake-tag adam-analyzer:previous adam-analyzer:2
    docker _fake-image "$GHCR:$OLD" "${1:-good}"
    docker _fake-image "$MIG:$OLD" ''
    docker _fake-container adam-analysis "$GHCR:$OLD" api running
    docker _fake-container adam-analysis-worker-1 "$GHCR:$OLD" worker running
    echo "$OLD" >/opt/adam/deployed-commit
}

no_secret() { # 密碼不能出現在 stdout、log、任何指令的參數裡
    check "密碼沒有出現在輸出裡" lacks "$OUT" "$PW"
    check "密碼沒有出現在 log 裡" lacks "$LOG" "$PW"
    check "密碼沒有出現在任何指令的參數裡" lacks "$F/calls.log" "$PW"
}
has_re() { grep -qE -- "$2" "$1"; }
starts_this_run() { # stdout 說的 sudo tail -n +N：log 從第 N 行開始，正好是這一次的開頭
    local n
    n=$(grep -o 'sudo tail -n +[0-9]*' "$OUT" | head -n 1 | grep -o '[0-9]*$')
    [ -n "$n" ] && tail -n +"$n" "$LOG" | head -n 1 | grep -q '==== deploy.sh 開始'
}
no_leaks() { # stdout 會公開：不能有 RDS 的主機名、IP、帳號 ID、bucket、SQS 的網址、服務編號、容器的 log、status
    local leak
    for leak in "${LEAKS[@]}"; do
        check "stdout 沒有「$leak」" lacks "$OUT" "$leak"
    done
}
shown() { # SHOW=<情境名稱的 regex>（run.sh 傳進來）：印出那些情境的輸出，看訊息寫得好不好
    if [ -n "${SHOW:-}" ] && [[ $CASE =~ $SHOW ]]; then
        echo "  ---- 輸出（結束碼 $RC）"
        sed 's/^/  | /' "$OUT"
    fi
}
run_deploy() { # run_deploy [參數…]：和 SSM 一樣幾乎沒有環境變數
    env -i /opt/adam/deploy.sh "$@" >"$OUT" 2>&1
    RC=$?
    shown
    no_secret
    no_leaks
}
expect() { # expect <結束碼> <result> [commit]：結束碼和固定的最後一行
    local last
    last=$(tail -n 1 "$OUT")
    check "結束碼 $1（實際 $RC）" is "$RC" "$1"
    check "最後一行是 deploy: result=$2 code=$1 commit=${3:-$NEW}（實際：$last）" \
        is "$last" "deploy: result=$2 code=$1 commit=${3:-$NEW}"
    check "log 也有最後一行" has "$LOG" "deploy: result=$2 code=$1"
}
untouched() { # 換版本之前失敗：舊的容器完全不動
    check "沒有 stop、tag、run -d、create、rm 舊的容器" not_called_re '^docker (stop|tag|create|run -d|rm -f adam-analysis)'
    check "API 還在跑原本的映像檔" is "$(cfield adam-analysis status) $(cfield adam-analysis image)" "running $(image_of adam-analyzer:2)"
    check "worker-1 還在跑原本的映像檔" is "$(cfield adam-analysis-worker-1 status) $(cfield adam-analysis-worker-1 image)" \
        "running $(image_of adam-analyzer:2)"
    check "沒有寫 deployed-commit" [ ! -e /opt/adam/deployed-commit ]
    check "沒有寫 failed-commit" [ ! -e /opt/adam/failed-commit ]
}
LOGOPTS="--log-opt max-size=20m --log-opt max-file=5"
api_args() { # api_args <映像檔> <commit> [nolog]
    local logopts=" $LOGOPTS"
    [ "${3:-}" = nolog ] && logopts=''
    echo "docker run -d --name adam-analysis --restart unless-stopped --env-file /opt/adam/analysis.env -p 5000:5000 --label adam.commit=$2$logopts $1"
}
worker_args() { # worker_args <名字> <映像檔> <commit> [nolog]
    local logopts=" $LOGOPTS"
    [ "${4:-}" = nolog ] && logopts=''
    echo "--name $1 --hostname $1 --restart unless-stopped --stop-timeout 30 --env-file /opt/adam/analysis.env -v /opt/adam/models:/models:ro --health-cmd python -m analysis_queue alive --health-interval 60s --health-start-period 300s --label adam.commit=$3$logopts $2 python worker.py"
}

# ---------- 沒有要做的 ----------
begin "沒有 desired-commit：什麼都不做"
ec2_today
scenario FAKE_DESIRED=
run_deploy --retry-failed
expect 0 no-desired-commit none
check "沒有碰 docker" not_called_re '^docker '
check "讀 desired-commit 有帶 --region" \
    called "aws ssm get-parameter --region us-east-1 --name /adam/analyzer/desired-commit --query [Parameter.Value, Parameter.LastModifiedDate] --output text"

begin "已經是這一版：什麼都不做"
ec2_today
echo "$NEW" >/opt/adam/deployed-commit
run_deploy --retry-failed
expect 0 up-to-date
check "沒有碰 docker" not_called_re '^docker '
check "沒有讀密碼" not_called_re 'migrator-password'

begin "開機時遇到失敗過的版本：跳過"
ec2_today
echo "$NEW" >/opt/adam/failed-commit
run_deploy
expect 0 skipped-failed
check "沒有碰 docker" not_called_re '^docker '
check "說怎麼再試" has "$OUT" "--retry-failed"

begin "開機時：失敗之後 desired-commit 又被寫過（比 failed-commit 新）：照樣再試"
ec2_today
echo "$NEW" >/opt/adam/failed-commit
touch -d '2026-11-03T00:00:00Z' /opt/adam/failed-commit
scenario FAKE_DESIRED_MODIFIED=2026-11-03T01:23:45.678000+00:00
run_deploy
expect 0 deployed
check "說為什麼再試" has "$OUT" "desired-commit 在那之後又被寫過一次（2026-11-03T01:23:45.678000+00:00）：照樣再試"
check "成功之後清掉 failed-commit" [ ! -e /opt/adam/failed-commit ]

begin "開機時：desired-commit 比 failed-commit 舊：跳過"
ec2_today
echo "$NEW" >/opt/adam/failed-commit
touch -d '2026-11-03T02:00:00Z' /opt/adam/failed-commit
scenario FAKE_DESIRED_MODIFIED=2026-11-03T01:23:45.678000+00:00
run_deploy
expect 0 skipped-failed
check "沒有碰 docker" not_called_re '^docker '

begin "開機時：看不出 desired-commit 什麼時候寫的：跳過，log 記一行"
ec2_today
echo "$NEW" >/opt/adam/failed-commit
touch -d '2026-11-03T00:00:00Z' /opt/adam/failed-commit
scenario FAKE_DESIRED_MODIFIED=None
run_deploy
expect 0 skipped-failed
check "log 記一行" has "$LOG" "看不出 desired-commit 什麼時候寫的（「None」）"

# ---------- 正常部署 ----------
begin "正常部署：第一次，從手動建的 adam-analyzer:2 換成 GHCR 的映像檔"
ec2_today
run_deploy --retry-failed
expect 0 deployed
check "寫了 deployed-commit" is "$(cat /opt/adam/deployed-commit 2>/dev/null)" "$NEW"
check "沒有 failed-commit" [ ! -e /opt/adam/failed-commit ]
check "拉了分析服務的映像檔" called "docker pull -q $GHCR:$NEW"
check "拉了 migration 的映像檔" called "docker pull -q $MIG:$NEW"
check "密碼要解密" called "aws ssm get-parameter --region us-east-1 --name /adam/db/migrator-password --query Parameter.Value --output text --with-decryption"
check "migration：apply（沒有 --allow-destructive）" grep -qx 'args=apply' "$F/migrate-env"
check "migration：DB_HOST 從 analysis.env" grep -qx "DB_HOST=$DBHOST" "$F/migrate-env"
check "migration：DB_PORT" grep -qx 'DB_PORT=3306' "$F/migrate-env"
check "migration：DB_NAME" grep -qx 'DB_NAME=adam' "$F/migrate-env"
check "migration：DB_USER 是 adam_migrator（不是 analysis.env 的）" grep -qx 'DB_USER=adam_migrator' "$F/migrate-env"
check "migration：密碼從環境變數拿到" grep -qx 'DB_PASSWORD=matches' "$F/migrate-env"
check "migration：-e 只給名字（值不在參數裡）" \
    called "docker run --rm --name adam-deploy-migrate -e DB_HOST -e DB_PORT -e DB_NAME -e DB_USER -e DB_PASSWORD $MIG:$NEW apply [env DB_PASSWORD]"
check "只有 migration 那一次的環境變數有密碼" is "$(count_re 'env DB_PASSWORD')" 1
check "check-db：新的映像檔、analysis.env" \
    called "docker run --rm --name adam-deploy-check-db --env-file /opt/adam/analysis.env $GHCR:$NEW python -m analysis_queue check-db"
check "check-db 在 migration 之後" [ "$(line_of 'adam-deploy-check-db --env-file')" -gt "$(line_of 'adam-deploy-migrate -e')" ]
check "previous 是原本的 :2" is "$(image_of adam-analyzer:previous)" "$(image_of adam-analyzer:2)"
check "worker 用 docker stop（交還手上的工作）" called "docker stop adam-analysis-worker-1"
check "worker 在 API 之前停" [ "$(line_of '^docker stop adam-analysis-worker-1')" -lt "$(line_of '^docker stop adam-analysis$')" ]
check "換版本在 check-db 之後" [ "$(line_of '^docker stop ')" -gt "$(line_of 'adam-deploy-check-db --env-file')" ]
check "API 的參數照 sqs/DEPLOY.md 第 6 節（加 label、log 輪替）" called "$(api_args "$GHCR:$NEW" "$NEW")"
check "worker 的參數照 sqs/DEPLOY.md 第 6 節（加 label、log 輪替）" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW")"
check "--health-cmd 是一個參數" grep -qF -- '--health-cmd python\ -m\ analysis_queue\ alive' "$F/containers/adam-analysis-worker-1/argv"
check "API 是新版" is "$(cfield adam-analysis image)" "$(image_of "$GHCR:$NEW")"
check "worker-1 是新版、在跑" is "$(cfield adam-analysis-worker-1 image) $(cfield adam-analysis-worker-1 status)" "$(image_of "$GHCR:$NEW") running"
check "label 是這個 commit" grep -qx "adam.commit=$NEW" "$F/containers/adam-analysis-worker-1/labels"
check "問過 worker 自己的狀態" [ "$(cfield adam-analysis-worker-1 polls)" -ge 2 ]
check "說上一版是手動建的 :2" has "$OUT" "上一版記成 adam-analyzer:previous（adam-analyzer:2）"
check "成功時 stdout 只有一行 /healthz 的計數" \
    has_re "$OUT" '^== ready（[0-9]+ 秒）：models=ready loading=0 ready=1 busy=0 failed=0 queue_depth=0$'
check "status 不在 stdout、在 log 裡" has "$LOG" "fake-status-marker"
check "log 是完整的：status 的服務編號" has "$LOG" "S20261101203243 failed"
check "log 是完整的：migration 連的主機" has "$LOG" "migrate: connected to $DBHOST:3306 as 'adam_migrator'@'172.31.5.10'"
check "log 是完整的：check-db 的最後一行" has "$LOG" "check-db: 42 OK as 'adam_analysis'@'172.31.40.7'"
check "stdout 的 check-db 那一行換掉了 IP" has "$OUT" "check-db: 42 OK as 'adam_analysis'@'<IP>'"
check "手動建的 :1 沒動" has_image adam-analyzer:1
check "手動建的 :2 沒動" has_image adam-analyzer:2
check "log 有每一步的完整輸出" has "$LOG" "----- check-db"
check "log 是 600（完整、不過濾，只有 root 讀得到）" is "$(stat -c %a "$LOG")" 600
check "stdout 精簡（遠小於 24,000 字元）" [ "$(wc -c <"$OUT")" -lt 6000 ]
check "暫存資料夾清掉了" no_match '/tmp/adam-deploy.*'

begin "第二次部署：previous 換成上一個 commit，清掉更舊的"
after_ghcr_deploy
docker _fake-image "$GHCR:$OLDER" good
docker _fake-image "$MIG:$OLDER" ''
run_deploy --retry-failed
expect 0 deployed
check "previous 是 $OLD" is "$(image_of adam-analyzer:previous)" "$(image_of "$GHCR:$OLD")"
check "$OLD 留著（它是 previous）" has_image "$GHCR:$OLD"
check "這一版留著" has_image "$GHCR:$NEW"
check "更舊的分析服務映像檔刪了" no_image "$GHCR:$OLDER"
check "上一版的 migration 映像檔刪了" no_image "$MIG:$OLD"
check "更舊的 migration 映像檔刪了" no_image "$MIG:$OLDER"
check "這一版的 migration 映像檔留著" has_image "$MIG:$NEW"
check "手動建的 :2 沒動" has_image adam-analyzer:2
check "rmi 不加 -f" not_called_re '^docker rmi -f'
check "說清掉幾個" has "$OUT" "清掉 3 個"

begin "容器已經是新版、只是沒記下來（上一次寫 deployed-commit 之前中斷）：previous 不變"
docker _fake-image adam-analyzer:2 good
docker _fake-image "$GHCR:$OLD" good
docker _fake-tag adam-analyzer:previous "$GHCR:$OLD"
docker _fake-image "$GHCR:$NEW" good
docker _fake-image "$MIG:$NEW" ''
docker _fake-container adam-analysis "$GHCR:$NEW" api running
docker _fake-container adam-analysis-worker-1 "$GHCR:$NEW" worker running
echo "$OLD" >/opt/adam/deployed-commit
run_deploy --retry-failed
expect 0 deployed
check "沒有 docker tag（現在的就是新版）" not_called_re '^docker tag '
check "previous 還是 $OLD" is "$(image_of adam-analyzer:previous)" "$(image_of "$GHCR:$OLD")"
check "$OLD 留著" has_image "$GHCR:$OLD"

begin "兩個 worker 都在跑：兩個一起停、一起換"
ec2_today
docker _fake-container adam-analysis-worker-2 adam-analyzer:2 worker running
run_deploy --retry-failed
expect 0 deployed
check "一起 docker stop" called "docker stop adam-analysis-worker-1 adam-analysis-worker-2"
check "worker-1 run -d" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW")"
check "worker-2 run -d" called "docker run -d $(worker_args adam-analysis-worker-2 "$GHCR:$NEW" "$NEW")"
check "兩個都等到自己 ready" [ "$(cfield adam-analysis-worker-2 polls)" -ge 2 ]

begin "worker-2 原本停著：換成新版，照樣停著"
ec2_today
docker _fake-container adam-analysis-worker-2 adam-analyzer:2 worker exited
run_deploy --retry-failed
expect 0 deployed
check "worker-1 run -d" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW")"
check "worker-2 用 create（不啟動）" called "docker create $(worker_args adam-analysis-worker-2 "$GHCR:$NEW" "$NEW")"
check "worker-2 是新版、沒有在跑" is "$(cfield adam-analysis-worker-2 image) $(cfield adam-analysis-worker-2 status)" "$(image_of "$GHCR:$NEW") created"

begin "原本沒有 worker：建 worker-1"
docker _fake-image adam-analyzer:2 good
docker _fake-container adam-analysis adam-analyzer:2 api running
run_deploy --retry-failed
expect 0 deployed
check "建了 worker-1" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW")"

begin "worker 都停著：啟動第一個（部署要有 worker 才確認得了）"
ec2_today
docker _fake-container adam-analysis-worker-1 adam-analyzer:2 worker exited # 蓋掉 ec2_today 的
docker _fake-container adam-analysis-worker-2 adam-analyzer:2 worker exited
run_deploy --retry-failed
expect 0 deployed
check "worker-1 run -d" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW")"
check "worker-2 照樣停著" called "docker create $(worker_args adam-analysis-worker-2 "$GHCR:$NEW" "$NEW")"

begin "映像檔已經在本機：照樣 pull（只拿 manifest），不看硬碟"
ec2_today
docker _fake-image "$GHCR:$NEW" good
docker _fake-image "$MIG:$NEW" ''
echo $((1 * 1048576)) >"$F/df-avail-kb"
run_deploy --retry-failed
expect 0 deployed
check "分析服務的映像檔照樣 pull" called "docker pull -q $GHCR:$NEW"
check "migration 的映像檔照樣 pull" called "docker pull -q $MIG:$NEW"
check "沒有看硬碟（不用整個下載）" lacks "$OUT" "硬碟只剩"

begin "GHCR 上同一個 tag 重建過：照樣 pull，跑新的那一份"
ec2_today
docker _fake-image "$GHCR:$NEW" good
docker _fake-image "$MIG:$NEW" ''
before=$(image_of "$GHCR:$NEW")
scenario FAKE_PULL_NEW_DIGEST=1
run_deploy --retry-failed
expect 0 deployed
check "tag 指到新的映像檔" [ "$(image_of "$GHCR:$NEW")" != "$before" ]
check "API 跑的是新的那一份" is "$(cfield adam-analysis image)" "$(image_of "$GHCR:$NEW")"
check "worker 跑的是新的那一份" is "$(cfield adam-analysis-worker-1 image)" "$(image_of "$GHCR:$NEW")"

begin "拉不到、但本機有這個 tag：用本機的，警告一行"
ec2_today
docker _fake-image "$GHCR:$NEW" good
docker _fake-image "$MIG:$NEW" ''
scenario "FAKE_PULL_FAIL='adam-(analyzer|db-migrations):$NEW'"
run_deploy --retry-failed
expect 0 deployed
check "警告用本機的那一份" has "$OUT" "注意：拉不到 $GHCR:$NEW，用本機已經有的那一份"
check "migration 的也一樣" has "$OUT" "注意：拉不到 $MIG:$NEW，用本機已經有的那一份"
check "跑的是本機的" is "$(cfield adam-analysis image)" "$(image_of "$GHCR:$NEW")"

# ---------- log driver ----------
begin "docker 的 log driver 是 journald：容器不加 --log-opt（不然 docker run 會拒絕）"
ec2_today
scenario FAKE_LOG_DRIVER=journald
run_deploy --retry-failed
expect 0 deployed
check "API 不加 --log-opt" called "$(api_args "$GHCR:$NEW" "$NEW" nolog)"
check "worker 不加 --log-opt" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW" nolog)"
check "log 記一行" has "$LOG" "docker 的預設 log driver 是「journald」：這次的容器不加 LOG_OPTS"

begin "log driver 是 journald、新版沒有 ready：換回上一版也不加 --log-opt"
ec2_today
short_timeout
scenario FAKE_LOG_DRIVER=journald FAKE_NEW_BEHAVIOR=never
run_deploy --retry-failed
expect 16 rolled-back
check "換回去的 worker 不加 --log-opt" \
    called "docker run -d $(worker_args adam-analysis-worker-1 adam-analyzer:previous unknown nolog)"

begin "docker 的 log driver 是 local：照樣加 --log-opt"
ec2_today
scenario FAKE_LOG_DRIVER=local
run_deploy --retry-failed
expect 0 deployed
check "API 加 --log-opt" called "$(api_args "$GHCR:$NEW" "$NEW")"
check "worker 加 --log-opt" called "docker run -d $(worker_args adam-analysis-worker-1 "$GHCR:$NEW" "$NEW")"

begin "--allow-destructive-migrations：轉給 runner 的 --allow-destructive"
ec2_today
run_deploy --retry-failed --allow-destructive-migrations
expect 0 deployed
check "runner 拿到 apply --allow-destructive" grep -qx 'args=apply --allow-destructive' "$F/migrate-env"

begin "--retry-failed：失敗過的版本照樣再試，成功之後清掉 failed-commit"
ec2_today
echo "$NEW" >/opt/adam/failed-commit
run_deploy --retry-failed
expect 0 deployed
check "清掉 failed-commit" [ ! -e /opt/adam/failed-commit ]

begin "pull 暫時失敗：重試之後成功"
ec2_today
scenario FAKE_PULL_FLAKY=2
run_deploy --retry-failed
expect 0 deployed
check "試了 3 次" is "$(count_re "^docker pull -q $GHCR:$NEW\$")" 3

# ---------- 換版本之前失敗：舊的容器完全不動 ----------
begin "拉不到映像檔"
ec2_today
scenario "FAKE_PULL_FAIL=adam-analyzer:$NEW"
run_deploy --retry-failed
expect 12 pull-failed
untouched
check "manifest unknown 不重試" is "$(count_re "^docker pull -q $GHCR:$NEW\$")" 1
check "附上 docker 的錯誤" has "$OUT" "manifest unknown"
check "提醒 GHCR 的套件要公開" has "$OUT" "Public"
check "沒有跑 migration" not_called_re 'adam-deploy-migrate -e'

begin "拉映像檔一直連不上（網路）：試 3 次"
ec2_today
scenario FAKE_PULL_FLAKY=9
run_deploy --retry-failed
expect 12 pull-failed
untouched
check "試了 3 次" is "$(count_re "^docker pull -q $GHCR:$NEW\$")" 3
check "docker 的錯誤換掉了 IP" has "$OUT" "dial tcp <IP>:443: i/o timeout"
check "log 裡是原樣" has "$LOG" "dial tcp 140.82.112.33:443"

begin "有破壞性的 migration（runner 回 3）"
ec2_today
scenario FAKE_MIGRATE_RC=3
run_deploy --retry-failed
expect 13 destructive-migration
untouched
check "附上 runner 說的是哪一個" has "$OUT" "008_drop_old_column.sql is destructive"
check "說要先建 snapshot、手動勾選" has "$OUT" "snapshot"
check "沒有跑 check-db" not_called_re 'check-db'

for code in 1 4 7 125; do
    begin "migration 失敗（runner 回 $code）"
    ec2_today
    scenario "FAKE_MIGRATE_RC=$code"
    run_deploy --retry-failed
    expect 14 migration-failed
    untouched
    check "附上 runner 的最後一行" has "$OUT" "migrate: result=failed code=$code"
    check "MySQL 的錯誤換掉了 IP" has "$OUT" "Access denied for user 'adam_migrator'@'<IP>'"
    check "其他 RDS 的主機名換掉了" has "$OUT" "Can't connect to MySQL server on '<RDS>:3306'"
    check "analysis.env 的 DB_HOST 換掉了" has "$OUT" "migrate: connected to <DB_HOST>:3306 as 'adam_migrator'@'<IP>' (<HOST>)"
    check "log 裡是原樣" has "$LOG" "Access denied for user 'adam_migrator'@'172.31.5.10'"
    check "沒有跑 check-db" not_called_re 'check-db'
done

begin "check-db 沒過"
ec2_today
scenario FAKE_CHECKDB_RC=1
run_deploy --retry-failed
expect 15 check-db-failed
untouched
check "印出缺的那一項" has "$OUT" "MISSING column tbl_service.service_outcome"
check "OK 的那幾行不印" lacks "$OUT" "OK      table"
check "DENIED 那一行換掉了 IP、DB_HOST" has "$OUT" "denied to user 'adam_analysis'@'<IP>' on <DB_HOST>"
check "log 裡是原樣" has "$LOG" "'adam_analysis'@'172.31.40.7' on $DBHOST"

begin "check-db 連不上資料庫（回 2）"
ec2_today
scenario FAKE_CHECKDB_RC=2
run_deploy --retry-failed
expect 15 check-db-failed
untouched

begin "硬碟不夠：不拉，舊版照常跑"
ec2_today
echo $((3 * 1048576)) >"$F/df-avail-kb"
run_deploy --retry-failed
expect 11 precheck-failed
untouched
check "沒有 pull" not_called_re '^docker pull'
check "說剩多少、要多少" has "$OUT" "硬碟只剩 3 GB"
check "列出現在有的映像檔" has "$OUT" "adam-analyzer:1 6.2GB"
check "手動建的 :1 沒刪" has_image adam-analyzer:1

begin "硬碟不夠：先清掉這個腳本拉過、用不到的舊映像檔就夠了"
after_ghcr_deploy
docker _fake-image "$GHCR:$OLDER" good
echo $((5 * 1048576)) >"$F/df-avail-kb"
run_deploy --retry-failed
expect 0 deployed
check "更舊的先刪了" no_image "$GHCR:$OLDER"
check "在 pull 之前刪" [ "$(line_of "^docker rmi $GHCR:$OLDER")" -lt "$(line_of '^docker pull')" ]
check "正在跑的 $OLD 沒刪" has_image "$GHCR:$OLD"

begin "硬碟不夠、只缺 migration 的映像檔：已經在本機的這一版不能被清掉"
after_ghcr_deploy
docker _fake-image "$GHCR:$OLDER" good
docker _fake-image "$GHCR:$NEW" good
echo $((5 * 1048576)) >"$F/df-avail-kb"
run_deploy --retry-failed
expect 0 deployed
check "分析服務的映像檔照樣 pull（只拿 manifest）" called "docker pull -q $GHCR:$NEW"
check "沒有刪這一版" not_called_re "^docker rmi $GHCR:$NEW"
check "更舊的刪了" no_image "$GHCR:$OLDER"
check "跑的是這一版" is "$(cfield adam-analysis image)" "$(image_of "$GHCR:$NEW")"

begin "沒有 analysis.env"
ec2_today
rm -f /opt/adam/analysis.env
run_deploy --retry-failed
expect 11 precheck-failed
untouched
check "說要跑 install.sh" has "$OUT" "install.sh"

begin "analysis.env 沒有 DB_HOST"
ec2_today
printf 'DB_NAME=adam\nDB_PASSWORD=analysis-test-only\n' >/opt/adam/analysis.env
run_deploy --retry-failed
expect 11 precheck-failed
untouched

begin "沒有模型的資料夾"
ec2_today
rm -rf /opt/adam/models
run_deploy --retry-failed
expect 11 precheck-failed
untouched

begin "docker 沒在跑"
ec2_today
scenario FAKE_DOCKER_DOWN=1
run_deploy --retry-failed
expect 11 precheck-failed
untouched
check "說 docker 沒在跑" has "$OUT" "docker 沒在跑"

begin "讀不到 Parameter Store"
ec2_today
scenario FAKE_AWS_FAIL=1
run_deploy --retry-failed
expect 11 precheck-failed none
untouched
check "附上 AWS 的錯誤" has "$OUT" "AccessDeniedException"
check "AWS 的錯誤訊息換掉了帳號、Instance ID" has "$OUT" "arn:aws:sts::<ACCOUNT>:assumed-role/adam-analysis-role/<INSTANCE>"
check "log 裡是原樣" has "$LOG" "arn:aws:sts::123456789012:assumed-role/adam-analysis-role/i-0123456789abcdef0"

begin "desired-commit 不是 commit SHA"
ec2_today
scenario FAKE_DESIRED=main
run_deploy --retry-failed
expect 11 precheck-failed none
untouched

begin "沒有 migrator 的密碼"
ec2_today
scenario FAKE_PASSWORD=
run_deploy --retry-failed
expect 11 precheck-failed
untouched
check "說是哪一個參數" has "$OUT" "/adam/db/migrator-password"
check "沒有 pull" not_called_re '^docker pull'

begin "不認得的參數"
run_deploy --retry-faild
expect 11 precheck-failed none
check "說是哪一個" has "$OUT" "不認得的參數：--retry-faild"

begin "deploy.conf 的數字不對"
conf HEALTH_TIMEOUT=five
run_deploy --retry-failed
expect 11 precheck-failed none

begin "--help"
run_deploy --help
check "結束碼 0" is "$RC" 0
check "印用法" has "$OUT" "用法：/opt/adam/deploy.sh"

begin "時間預算：讀 Parameter Store 就用完了換容器之前的時間：18，舊版照常跑"
ec2_today
conf DEPLOY_BUDGET=902 SWITCH_RESERVE=900
scenario FAKE_AWS_SLEEP_DESIRED=3
run_deploy --retry-failed
expect 18 out-of-time
untouched
check "沒有 pull" not_called_re '^docker pull'
check "說時間不夠" has "$OUT" "時間不夠了：整次最多 902 秒（含等鎖），換容器要留 900 秒"

begin "時間預算：等鎖的時間也算（同樣的設定、不用等鎖時部署得完）"
ec2_today
conf DEPLOY_BUDGET=910 SWITCH_RESERVE=900 LOCK_WAIT=10
scenario FAKE_AWS_SLEEP_DESIRED=3
run_deploy --retry-failed
expect 0 deployed
begin "時間預算：等鎖等了 8 秒，加上讀 Parameter Store 的 3 秒，超過 10 秒：18"
ec2_today
conf DEPLOY_BUDGET=910 SWITCH_RESERVE=900 LOCK_WAIT=10
scenario FAKE_AWS_SLEEP_DESIRED=3
flock -o /run/adam-deploy.lock sleep 8 &
holder=$!
for _ in $(seq 1 50); do flock -n /run/adam-deploy.lock true || break; sleep 0.1; done
run_deploy --retry-failed
wait "$holder" 2>/dev/null
expect 18 out-of-time
untouched

begin "時間預算：pull 用掉了剩下的時間：截斷、18，舊版照常跑"
ec2_today
conf DEPLOY_BUDGET=904 SWITCH_RESERVE=900
scenario FAKE_PULL_SLEEP=30
start=$SECONDS
run_deploy --retry-failed
took=$((SECONDS - start))
expect 18 out-of-time
untouched
check "pull 被截在剩下的時間內（$took 秒）" [ "$took" -lt 15 ]
check "說是拉映像檔的時候" has "$OUT" "不拉映像檔（拉 $GHCR:$NEW 的時候時間用完了）"

begin "時間預算：migration 跑到一半時間用完了：中斷、18，舊版照常跑"
ec2_today
docker _fake-image "$GHCR:$NEW" good
docker _fake-image "$MIG:$NEW" ''
conf DEPLOY_BUDGET=905 SWITCH_RESERVE=900
scenario FAKE_MIGRATE_SLEEP=30
start=$SECONDS
run_deploy --retry-failed
took=$((SECONDS - start))
expect 18 out-of-time
untouched
check "migration 被截在剩下的時間內（$took 秒）" [ "$took" -lt 15 ]
check "說 migration 被中斷" has "$OUT" "migration 跑到一半時間用完了，被中斷"
check "沒有跑 check-db" not_called_re 'adam-deploy-check-db --env-file'

begin "deploy.conf：DEPLOY_BUDGET 不比 SWITCH_RESERVE 大"
conf DEPLOY_BUDGET=900 SWITCH_RESERVE=900
run_deploy --retry-failed
expect 11 precheck-failed none
check "說哪裡不對" has "$OUT" "DEPLOY_BUDGET（900）要比 SWITCH_RESERVE（900）大"

begin "等不到鎖：另一個 deploy.sh 在跑"
ec2_today
flock -o /run/adam-deploy.lock sleep 20 &
holder=$!
for _ in $(seq 1 50); do flock -n /run/adam-deploy.lock true || break; sleep 0.1; done
run_deploy --retry-failed
kill "$holder" 2>/dev/null
wait "$holder" 2>/dev/null
expect 10 locked none
check "沒有碰 docker" not_called_re '^docker '

# ---------- 新版沒有 ready：換回上一版 ----------
begin "新版一直沒有 ready：換回上一版（:2）"
ec2_today
short_timeout
scenario FAKE_NEW_BEHAVIOR=never
seq 1 100 | sed 's/^/earlier run line /' >"$LOG" # 之前跑過幾次：log 裡已經有東西
run_deploy --retry-failed
expect 16 rolled-back
check "failed-commit 記了這一版" is "$(cat /opt/adam/failed-commit 2>/dev/null)" "$NEW"
check "沒有寫 deployed-commit" [ ! -e /opt/adam/deployed-commit ]
check "API 換回 previous" is "$(cfield adam-analysis ref) $(cfield adam-analysis image)" "adam-analyzer:previous $(image_of adam-analyzer:2)"
check "worker-1 換回 previous、在跑" is "$(cfield adam-analysis-worker-1 ref) $(cfield adam-analysis-worker-1 status)" "adam-analyzer:previous running"
check "換回去的參數一樣" called "docker run -d $(worker_args adam-analysis-worker-1 adam-analyzer:previous unknown)"
check ":2 不是 GHCR 的：label 寫 unknown" grep -qx 'adam.commit=unknown' "$F/containers/adam-analysis/labels"
check "說等了多久沒 ready" has "$OUT" "4 秒內沒有 ready"
check "/healthz 只附計數" has "$OUT" "/healthz：models=loading loading=1 ready=0 busy=0 failed=0 queue_depth=0"
check "說容器的 log 在哪、怎麼看" has "$OUT" "容器的 log（每個最後 50 行）只寫在 EC2 的 /var/log/adam-deploy.log：sudo tail -n +"
check "容器的 log 在 log 檔裡" has "$LOG" "fake log line 2 of adam-analysis-worker-1: outbox sent to $SQSURL"
check "照著說的指令看到的就是這一次的紀錄" starts_this_run
check "上一版 ready 那一行也只有計數" \
    has_re "$OUT" '^== 上一版 ready（[0-9]+ 秒）：models=ready loading=0 ready=1 busy=0 failed=0 queue_depth=0$'
check "換回去的 worker 也先 docker stop 新版的" [ "$(count_re '^docker stop adam-analysis-worker-1$')" -eq 2 ]
check "失敗時不清映像檔" has_image "$GHCR:$NEW"

begin "新版沒有 ready：換回上一個 commit（label 是那個 commit）"
after_ghcr_deploy
short_timeout
scenario FAKE_NEW_BEHAVIOR=never
run_deploy --retry-failed
expect 16 rolled-back
check "deployed-commit 還是 $OLD" is "$(cat /opt/adam/deployed-commit)" "$OLD"
check "API 跑回 $OLD 的映像檔" is "$(cfield adam-analysis image)" "$(image_of "$GHCR:$OLD")"
check "label 是 $OLD" grep -qx "adam.commit=$OLD" "$F/containers/adam-analysis/labels"
check "說換回哪個 commit" has "$OUT" "換回上一版 adam-analyzer:previous（$OLD）"

begin "新版的 worker 載入模型失敗：不等滿時間，馬上換回去"
ec2_today
conf HEALTH_TIMEOUT=120
scenario FAKE_NEW_BEHAVIOR=fail
start=$SECONDS
run_deploy --retry-failed
took=$((SECONDS - start))
expect 16 rolled-back
check "很快就換回去（$took 秒）" [ "$took" -lt 30 ]
check "說是載入模型失敗" has "$OUT" "載入模型失敗"

begin "舊 worker 被 kill、留下 ready 的心跳：不能當成新版 ready"
ec2_today
conf HEALTH_TIMEOUT=120
scenario FAKE_STOP_KILLS=1 FAKE_NEW_BEHAVIOR=crash
run_deploy --retry-failed
expect 16 rolled-back
check "/healthz 說過 ready（舊 worker 留下的）" said_ready
check "還是判定新版沒有 ready：一直重新啟動" has "$OUT" "一直重新啟動"

begin "worker 的狀態檔是重新啟動之前留下的 ready：不算（這一次的 process 還在載入）"
ec2_today
short_timeout
scenario FAKE_STOP_KILLS=1 FAKE_NEW_BEHAVIOR=stale-ready
run_deploy --retry-failed
expect 16 rolled-back
check "/healthz 說過 ready（舊 worker 留下的）" said_ready

begin "新版的 API 起不來"
ec2_today
conf HEALTH_TIMEOUT=120
scenario FAKE_NEW_BEHAVIOR=api-down
run_deploy --retry-failed
expect 16 rolled-back
check "說一直重新啟動" has "$OUT" "一直重新啟動"
check "附上 /healthz 連不上" has "$OUT" "/healthz：（連不上）"

begin "新版的容器建不起來（docker run 失敗）：換回上一版"
ec2_today
scenario "FAKE_RUN_FAIL=adam-analyzer:$NEW"
run_deploy --retry-failed
expect 16 rolled-back
check "附上 docker 的錯誤" has "$OUT" "port is already allocated"
check "API 換回 previous" is "$(cfield adam-analysis ref) $(cfield adam-analysis status)" "adam-analyzer:previous running"

begin "換回上一版也沒有 ready"
ec2_today never
short_timeout
scenario FAKE_NEW_BEHAVIOR=never
run_deploy --retry-failed
expect 17 rollback-failed
check "failed-commit 記了這一版" is "$(cat /opt/adam/failed-commit 2>/dev/null)" "$NEW"
check "上一版的容器留著（也許之後會 ready）" is "$(cfield adam-analysis ref)" adam-analyzer:previous

begin "換回上一版時容器也建不起來"
ec2_today
short_timeout
scenario FAKE_NEW_BEHAVIOR=never "FAKE_RUN_FAIL=adam-analyzer:previous"
run_deploy --retry-failed
expect 17 rollback-failed
check "說容器建不起來" has "$OUT" "容器建不起來"

begin "沒有上一版可以換回去（原本沒有容器）"
short_timeout
scenario FAKE_NEW_BEHAVIOR=never
run_deploy --retry-failed
expect 17 rollback-failed
check "說沒有上一版" has "$OUT" "沒有上一版"
check "新版的容器留著" is "$(cfield adam-analysis image)" "$(image_of "$GHCR:$NEW")"
check "failed-commit 記了這一版" is "$(cat /opt/adam/failed-commit 2>/dev/null)" "$NEW"

begin "失敗過的版本：開機時（不帶參數）不再試"
ec2_today
short_timeout
scenario FAKE_NEW_BEHAVIOR=never
run_deploy --retry-failed
expect 16 rolled-back
: >"$F/calls.log"
run_deploy
expect 0 skipped-failed
check "沒有碰 docker" not_called_re '^docker '

begin "容器的 log 很長、換回去也失敗：容器的 log 都不在 stdout，最後一行照樣在"
ec2_today never
short_timeout
scenario FAKE_NEW_BEHAVIOR=never FAKE_LOG_LINES=200
run_deploy --retry-failed
expect 17 rollback-failed
check "stdout 很短（實際 $(wc -c <"$OUT") 位元組）" [ "$(wc -c <"$OUT")" -lt 6000 ]
check "log 裡是完整的（兩輪、每個容器 50 行）" [ "$(grep -c '^fake log line' "$LOG")" -ge 200 ]

begin "migration 的輸出放不進 STDOUT_BUDGET：只說在 log 裡，最後一行照樣在"
ec2_today
conf STDOUT_BUDGET=3000
scenario FAKE_MIGRATE_RC=1 FAKE_MIGRATE_LINES=200
run_deploy --retry-failed
expect 14 migration-failed
check "放不下的說在 log 裡" has "$OUT" "stdout 放不下，在 /var/log/adam-deploy.log"
check "stdout 沒有超過預算太多（實際 $(wc -c <"$OUT") 位元組）" [ "$(wc -c <"$OUT")" -lt 4500 ]
check "log 裡是完整的" [ "$(grep -c '^migrate:ydetail' "$LOG")" -eq 200 ]

# ---------- 其他 ----------
begin "--sanitize：不能公開的換成代號，可以公開的照留"
sanitized() { # sanitized <輸入> <預期>
    local got
    got=$(printf '%s\n' "$1" | env -i /opt/adam/deploy.sh --sanitize)
    check "「$1」→「$2」（實際：$got）" is "$got" "$2"
}
sanitized "host $DBHOST:3306" "host <DB_HOST>:3306"
sanitized "reader adam-db.cluster-ro-c1a2b3c4d5e6.us-east-1.rds.amazonaws.com" "reader <RDS>"
sanitized "user 'adam_analysis'@'172.31.40.7'" "user 'adam_analysis'@'<IP>'"
sanitized "dial tcp 140.82.112.33:443" "dial tcp <IP>:443"
sanitized "arn:aws:sts::123456789012:assumed-role/adam-analysis-role/i-0123456789abcdef0" \
    "arn:aws:sts::<ACCOUNT>:assumed-role/adam-analysis-role/<INSTANCE>"
sanitized "arn:aws:sqs:us-east-1:123456789012:adam-analysis" "arn:aws:sqs:us-east-1:<ACCOUNT>:adam-analysis"
sanitized "queue $SQSURL" "queue <SQS_URL>"
sanitized "queue https://sqs.us-west-2.amazonaws.com/210987654321/other-queue" "queue <SQS_URL>"
sanitized "s3://$BUCKET/video/S20261101203243_cam0.avi" "s3://<bucket>/video/<SERVICE_ID>_cam0.avi"
sanitized "s3://another-bucket/charts/x.png" "s3://<bucket>/charts/x.png"
sanitized "bucket $BUCKET" "bucket <bucket>"
sanitized "bucket adam-media-210987654321-ap-northeast-1 gone" "bucket <bucket> gone"
sanitized "https://other-bucket.s3.us-east-1.amazonaws.com/a.png" "https://<bucket>.s3.amazonaws.com/a.png"
sanitized "host ip-172-31-5-10.ec2.internal" "host <HOST>"
sanitized "key AKIAIOSFODNN7EXAMPLE" "key <AWS_KEY>"
sanitized "OLLAMA_API_KEY=sk-test-only-123 DB_PASSWORD=abc HF_TOKEN=x" \
    "OLLAMA_API_KEY=<secret> DB_PASSWORD=<secret> HF_TOKEN=<secret>"
sanitized "token hf_abcdefghijklmnopqrstuvwxyz0123" "token <HF_TOKEN>"
sanitized "job S20261101203243 done" "job <SERVICE_ID> done"
# 可以公開的照留：commit SHA、映像檔名稱、資料表和欄位、版本、時間、計數、結束碼
sanitized "deploy: result=deployed code=0 commit=0123456789abcdef0123456789abcdef01234567" \
    "deploy: result=deployed code=0 commit=0123456789abcdef0123456789abcdef01234567"
sanitized "deploy: result=locked code=10 commit=none" "deploy: result=locked code=10 commit=none"
sanitized "$GHCR:$NEW" "$GHCR:$NEW"
sanitized "$MIG:0123456789abcdef0123456789abcdef01234567" "$MIG:0123456789abcdef0123456789abcdef01234567"
sanitized "MISSING column tbl_service.service_outcome; procedure sp_coaching_list" \
    "MISSING column tbl_service.service_outcome; procedure sp_coaching_list"
sanitized "docker 25.0.8, mysql 8.4.7, 2026-11-03T10:00:00Z, 42 OK, queue_depth=0" \
    "docker 25.0.8, mysql 8.4.7, 2026-11-03T10:00:00Z, 42 OK, queue_depth=0"
sanitized "adam-analysis-worker-1 adam-analyzer:previous /opt/adam/analysis.env" \
    "adam-analysis-worker-1 adam-analyzer:previous /opt/adam/analysis.env"
check "--sanitize 不碰 docker、aws，也不加鎖" not_called_re '^(docker|aws) '
check "--sanitize 不寫 log" [ ! -e "$LOG" ]

begin "bash -x 也印不出密碼"
ec2_today
env -i bash -x /opt/adam/deploy.sh --retry-failed >"$OUT" 2>&1
RC=$?
no_secret
check "結束碼 0" is "$RC" 0
check "部署好了" has "$OUT" "deploy: result=deployed code=0 commit=$NEW"
check "真的有 trace" grep -q '^+ ' "$OUT"
check "看得到關掉 trace 的地方" has "$OUT" "xtrace_off"

begin "log 太大：換成 .1"
ec2_today
conf LOG_MAX_BYTES=1000
head -c 5000 /dev/zero | tr '\0' x >"$LOG"
echo "$NEW" >/opt/adam/deployed-commit
run_deploy --retry-failed
expect 0 up-to-date
check ".1 是原本的" is "$(stat -c %s "$LOG.1" 2>/dev/null)" 5000
check "新的 log 很小" [ "$(stat -c %s "$LOG")" -lt 1000 ]
check "新的 log 也是 600" is "$(stat -c %a "$LOG")" 600

# ---------- install.sh ----------
install_case() { # install_case <情境>：還沒裝過的 EC2（analysis.env 還在 ec2-user 的資料夾，模型在 /opt/adam/models）
    begin "$1"
    rm -f /opt/adam/deploy.sh /opt/adam/deploy.conf /opt/adam/analysis.env
    mkdir -p /home/ec2-user/analyzer
    printf 'DB_HOST=db.example.test\nDB_NAME=adam\nDB_PASSWORD=analysis-test-only\n' >/home/ec2-user/analyzer/analysis.env
    chmod 644 /home/ec2-user/analyzer/analysis.env
}
run_install() {
    env -i bash "$SRC/install.sh" >"$OUT" 2>&1
    RC=$?
    shown
    check "密碼沒有出現在輸出裡" lacks "$OUT" "$PW"
}

install_case "install.sh：第一次安裝"
run_install
check "結束碼 0（實際 $RC）" is "$RC" 0
check "/opt/adam/deploy.sh 是 755" is "$(stat -c %a /opt/adam/deploy.sh 2>/dev/null)" 755
check "/opt/adam/deploy.sh 和 repo 的一樣" cmp -s "$SRC/deploy.sh" /opt/adam/deploy.sh
check "unit 是 644" is "$(stat -c %a /etc/systemd/system/adam-deploy.service 2>/dev/null)" 644
check "analysis.env 搬過去了：600、root" is "$(stat -c '%a %U %G' /opt/adam/analysis.env 2>/dev/null)" "600 root root"
check "analysis.env 內容一樣" grep -qx 'DB_HOST=db.example.test' /opt/adam/analysis.env
check "原位置是指向 /opt/adam/analysis.env 的符號連結" \
    is "$(readlink /home/ec2-user/analyzer/analysis.env)" /opt/adam/analysis.env
check "說留了符號連結" has "$OUT" "是指向 /opt/adam/analysis.env 的符號連結"
check "透過符號連結讀得到（root）" grep -qx 'DB_HOST=db.example.test' /home/ec2-user/analyzer/analysis.env
check "daemon-reload" called "systemctl daemon-reload"
check "enable" called "systemctl enable adam-deploy.service"
check "沒有啟動 unit" not_called_re '^systemctl (start|restart|enable --now)'
check "沒有部署（沒有 pull、run、stop）" not_called_re '^docker (pull|run|stop)'
# shellcheck disable=SC2016 # 反引號是 JMESPath 的數字
check "密碼只問讀不讀得到" \
    called 'aws ssm get-parameter --region us-east-1 --name /adam/db/migrator-password --with-decryption --query length(Parameter.Value) > `0` --output text'
check "印出 desired-commit" has "$OUT" "/adam/analyzer/desired-commit = $NEW"
check "沒有要處理的事" has "$OUT" "裝好了。"
check "說下一步" has "$OUT" "下一步"
systemd-analyze verify /etc/systemd/system/adam-deploy.service >/tmp/verify.out 2>&1
RC=$?
check "systemd-analyze verify 的結束碼 0（實際 $RC）" is "$RC" 0
check "systemd-analyze verify 沒有任何警告：$(head -n 3 /tmp/verify.out)" [ ! -s /tmp/verify.out ]

install_case "install.sh：重跑：符號連結留著，不提醒"
run_install
: >"$F/calls.log"
run_install
check "結束碼 0（實際 $RC）" is "$RC" 0
check "符號連結還在" is "$(readlink /home/ec2-user/analyzer/analysis.env)" /opt/adam/analysis.env
check "沒有提醒舊的檔案" lacks "$OUT" "舊的 /home/ec2-user/analyzer/analysis.env 還在"
check "沒有要處理的事" has "$OUT" "裝好了。"

install_case "install.sh：重跑不蓋掉已經有的 analysis.env"
run_install
echo "OLLAMA_MODEL=edited-on-ec2" >>/opt/adam/analysis.env
before=$(sha256sum </opt/adam/analysis.env)
rm -f /home/ec2-user/analyzer/analysis.env # 使用者又在原位置放了一份一般的檔案
echo 'DB_HOST=stale.example.test' >/home/ec2-user/analyzer/analysis.env
: >"$F/calls.log"
run_install
check "結束碼 0（實際 $RC）" is "$RC" 0
check "analysis.env 沒被蓋掉" is "$(sha256sum </opt/adam/analysis.env)" "$before"
check "權限還是 600、root" is "$(stat -c '%a %U %G' /opt/adam/analysis.env)" "600 root root"
check "舊的檔案留著，提醒可以刪" has "$OUT" "舊的 /home/ec2-user/analyzer/analysis.env 還在，而且是一般的檔案"
check "舊的檔案沒被動" grep -qx 'DB_HOST=stale.example.test' /home/ec2-user/analyzer/analysis.env

install_case "install.sh：之前的 install.sh 刪掉了舊的 analysis.env：補上符號連結"
mv /home/ec2-user/analyzer/analysis.env /opt/adam/analysis.env
chmod 600 /opt/adam/analysis.env
run_install
check "結束碼 0（實際 $RC）" is "$RC" 0
check "補上符號連結" is "$(readlink /home/ec2-user/analyzer/analysis.env)" /opt/adam/analysis.env
check "deploy.sh 照樣更新" cmp -s "$SRC/deploy.sh" /opt/adam/deploy.sh
check "再 enable 一次也沒關係" called "systemctl enable adam-deploy.service"

install_case "install.sh：SSM agent 沒在跑、docker 開機不會啟動"
scenario FAKE_SSM_INACTIVE=1 FAKE_DOCKER_DISABLED=1
run_install
check "照樣裝好（結束碼 0）" is "$RC" 0
check "提醒 SSM agent" has "$OUT" "amazon-ssm-agent 沒在跑"
check "提醒 docker 開機啟動" has "$OUT" "sudo systemctl enable docker"
check "最後列出要處理的事" has "$OUT" "有 2 件事要處理"

install_case "install.sh：Parameter Store 還沒有東西"
scenario FAKE_DESIRED= FAKE_PASSWORD=
run_install
check "結束碼 0" is "$RC" 0
check "desired-commit 還沒有不是錯" has "$OUT" "還沒有：GitHub 的 Deploy analyzer 第一次跑完之後才有"
check "密碼還沒有要提醒" has "$OUT" "還沒有 /adam/db/migrator-password"

install_case "install.sh：沒有 analysis.env、模型"
rm -rf /home/ec2-user /opt/adam/models
run_install
check "照樣裝好（結束碼 0）" is "$RC" 0
check "提醒 analysis.env" has "$OUT" "沒有 /opt/adam/analysis.env"
check "提醒模型" has "$OUT" "沒有模型的資料夾"

install_case "install.sh：少了 flock"
mv /usr/bin/flock /usr/bin/flock.hidden
run_install
mv /usr/bin/flock.hidden /usr/bin/flock
check "停下來（結束碼 1）" is "$RC" 1
check "說少了 flock" has "$OUT" "少了這些指令：flock"
check "什麼都沒裝" [ ! -e /opt/adam/deploy.sh ]
check "analysis.env 沒搬" [ -e /home/ec2-user/analyzer/analysis.env ]

echo
echo "情境 $CASES 個、檢查 $((PASS + FAIL)) 項：通過 $PASS、失敗 $FAIL"
[ "$FAIL" = 0 ]
