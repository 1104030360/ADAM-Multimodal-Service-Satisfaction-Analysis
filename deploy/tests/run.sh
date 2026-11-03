#!/bin/bash
# deploy/tests/run.sh — deploy.sh、install.sh、adam-deploy.service 的測試（CONTRACTS 第 5 節）：
#   bash deploy/tests/run.sh       （在哪個目錄執行都可以；結束碼 0 才是全部通過）
# 在 amazonlinux:2023（和 EC2 一樣的發行版）的容器裡，用 fakes/ 的假 docker、aws、curl、df、systemctl 跑 cases.sh 的每一條路，
# 也用容器裡的 systemd-analyze verify 檢查 unit。本機有 shellcheck 的話，也檢查 deploy/ 底下所有的 .sh。
# 需要 docker；第一次要網路（建測試用的映像檔時 dnf 要下載 flock、systemd，之後用 build cache）。測試本身不連網（--network none）。
# 用到的名稱：映像檔 cdc-deploy-test:<pid>、容器 cdC-deploy-test-<pid>，跑完都刪掉。
# PLATFORM=linux/amd64 可以指定平台（預設是這台機器的；EC2 是 x86_64，GitHub 的 runner 也是）。
# SHOW=<regex> 印出名稱符合的情境裡 deploy.sh、install.sh 的輸出（例如 SHOW='正常部署|runner 回 1'）。
# 這個檔案在 Mac 上用 macOS 內建的 bash 3.2 跑：不要用 mapfile、關聯陣列、set -u 下的空陣列。
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEPLOY=$(dirname "$HERE")
IMAGE=cdc-deploy-test:$$
NAME=cdC-deploy-test-$$
PLATFORM_ARGS=''
if [ -n "${PLATFORM:-}" ]; then PLATFORM_ARGS="--platform=$PLATFORM"; fi

# shellcheck disable=SC2329 # trap 叫它
cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1
    docker rmi "$IMAGE" >/dev/null 2>&1
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if ! docker info >/dev/null 2>&1; then
    echo "需要 docker（docker info 失敗）"
    exit 2
fi

echo "== 建測試用的映像檔（amazonlinux:2023＋flock、systemd-analyze）"
# shellcheck disable=SC2086 # PLATFORM_ARGS 是空的或一個參數
if ! docker build -q $PLATFORM_ARGS -t "$IMAGE" - >/dev/null <<'EOF'; then
FROM amazonlinux:2023
RUN dnf install -y --setopt=install_weak_deps=False util-linux-core systemd && dnf clean all && rm -rf /var/cache/dnf
EOF
    echo "建不了測試用的映像檔"
    exit 2
fi

echo "== cases.sh"
# 在背景跑、用 wait 等：Ctrl-C、kill 時 wait 馬上回來，EXIT 的 cleanup 才會刪掉容器（前景的 docker run 會擋住 trap）
# shellcheck disable=SC2086
docker run --rm $PLATFORM_ARGS --name "$NAME" --network none -e SHOW -v "$DEPLOY:/src/deploy:ro" "$IMAGE" \
    bash /src/deploy/tests/cases.sh &
wait $!
rc=$?

if command -v shellcheck >/dev/null 2>&1; then
    echo "== shellcheck"
    if shellcheck "$DEPLOY"/*.sh "$HERE"/*.sh "$HERE"/fakes/*.sh; then echo "沒有意見"; else rc=1; fi
else
    echo "== 沒有 shellcheck，跳過"
fi
exit "$rc"
