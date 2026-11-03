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
    printf 'DB_HOST=db.example.test\nDB_PORT=3306\nDB_NAME=adam\nDB_USER=adam_analysis\nDB_PASSWORD=analysis-test-only\n' \
        >/opt/adam/analysis.env
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
shown() { # SHOW=<情境名稱的一部分>（run.sh 傳進來）：印出那些情境的輸出，看訊息寫得好不好
    if [ -n "${SHOW:-}" ] && [[ $CASE == *"$SHOW"* ]]; then
        echo "  ---- 輸出（結束碼 $RC）"
        sed 's/^/  | /' "$OUT"
    fi
}
run_deploy() { # run_deploy [參數…]：和 SSM 一樣幾乎沒有環境變數
    env -i /opt/adam/deploy.sh "$@" >"$OUT" 2>&1
    RC=$?
    shown
    no_secret
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
api_args() { echo "docker run -d --name adam-analysis --restart unless-stopped --env-file /opt/adam/analysis.env -p 5000:5000 --label adam.commit=$2 --log-opt max-size=20m --log-opt max-file=5 $1"; }
worker_args() { echo "--name $1 --hostname $1 --restart unless-stopped --stop-timeout 30 --env-file /opt/adam/analysis.env -v /opt/adam/models:/models:ro --health-cmd python -m analysis_queue alive --health-interval 60s --health-start-period 300s --label adam.commit=$3 --log-opt max-size=20m --log-opt max-file=5 $2 python worker.py"; }

# ---------- 沒有要做的 ----------
begin "沒有 desired-commit：什麼都不做"
ec2_today
scenario FAKE_DESIRED=
run_deploy --retry-failed
expect 0 no-desired-commit none
check "沒有碰 docker" not_called_re '^docker '
check "讀 desired-commit 有帶 --region" \
    called "aws ssm get-parameter --region us-east-1 --name /adam/analyzer/desired-commit --query Parameter.Value --output text"

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
check "migration：DB_HOST 從 analysis.env" grep -qx 'DB_HOST=db.example.test' "$F/migrate-env"
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
check "印了 status" has "$OUT" "fake-status-marker"
check "手動建的 :1 沒動" has_image adam-analyzer:1
check "手動建的 :2 沒動" has_image adam-analyzer:2
check "log 有每一步的完整輸出" has "$LOG" "----- check-db"
check "log 是 640" is "$(stat -c %a "$LOG")" 640
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

begin "映像檔已經在本機：不拉，也不看硬碟"
ec2_today
docker _fake-image "$GHCR:$NEW" good
docker _fake-image "$MIG:$NEW" ''
echo $((1 * 1048576)) >"$F/df-avail-kb"
run_deploy --retry-failed
expect 0 deployed
check "沒有 pull" not_called_re '^docker pull'
check "說已經有" has "$OUT" "已經有 $GHCR:$NEW"

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
check "只拉 migration 的" not_called_re "^docker pull -q $GHCR:"
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
run_deploy --retry-failed
expect 16 rolled-back
check "failed-commit 記了這一版" is "$(cat /opt/adam/failed-commit 2>/dev/null)" "$NEW"
check "沒有寫 deployed-commit" [ ! -e /opt/adam/deployed-commit ]
check "API 換回 previous" is "$(cfield adam-analysis ref) $(cfield adam-analysis image)" "adam-analyzer:previous $(image_of adam-analyzer:2)"
check "worker-1 換回 previous、在跑" is "$(cfield adam-analysis-worker-1 ref) $(cfield adam-analysis-worker-1 status)" "adam-analyzer:previous running"
check "換回去的參數一樣" called "docker run -d $(worker_args adam-analysis-worker-1 adam-analyzer:previous unknown)"
check ":2 不是 GHCR 的：label 寫 unknown" grep -qx 'adam.commit=unknown' "$F/containers/adam-analysis/labels"
check "說等了多久沒 ready" has "$OUT" "4 秒內沒有 ready"
check "附上 /healthz" has "$OUT" '"models":"loading"'
check "附上容器的 log" has "$OUT" "fake log line 2 of adam-analysis-worker-1"
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

begin "容器的 log 很長、換回去也失敗：stdout 還是在 24,000 字元內，最後一行照樣在"
ec2_today never
short_timeout
scenario FAKE_NEW_BEHAVIOR=never FAKE_LOG_LINES=200
run_deploy --retry-failed
expect 17 rollback-failed
check "stdout 不到 24,000 位元組（實際 $(wc -c <"$OUT")）" [ "$(wc -c <"$OUT")" -lt 24000 ]
check "放不下的說在 log 裡" has "$OUT" "stdout 放不下，在 /var/log/adam-deploy.log"
check "第一個容器的 log 還是有附上" has "$OUT" "fake log line 25 of adam-analysis "
check "log 裡是完整的" [ "$(grep -c '^fake log line' "$LOG")" -ge 100 ]

# ---------- 其他 ----------
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
check "新的 log 也是 640" is "$(stat -c %a "$LOG")" 640

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
check "舊的 analysis.env 拿掉了" [ ! -e /home/ec2-user/analyzer/analysis.env ]
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

install_case "install.sh：重跑不蓋掉已經有的 analysis.env"
run_install
echo "OLLAMA_MODEL=edited-on-ec2" >>/opt/adam/analysis.env
before=$(sha256sum </opt/adam/analysis.env)
mkdir -p /home/ec2-user/analyzer
echo 'DB_HOST=stale.example.test' >/home/ec2-user/analyzer/analysis.env
: >"$F/calls.log"
run_install
check "結束碼 0（實際 $RC）" is "$RC" 0
check "analysis.env 沒被蓋掉" is "$(sha256sum </opt/adam/analysis.env)" "$before"
check "權限還是 600、root" is "$(stat -c '%a %U %G' /opt/adam/analysis.env)" "600 root root"
check "舊的檔案留著，提醒可以刪" has "$OUT" "舊的 /home/ec2-user/analyzer/analysis.env 還在"
check "舊的檔案沒被動" grep -qx 'DB_HOST=stale.example.test' /home/ec2-user/analyzer/analysis.env
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
