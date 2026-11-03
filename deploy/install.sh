#!/bin/bash
# =============================================================================
# install.sh — 在 EC2 上裝部署腳本（deploy/README.md 的「第一次部署」第 4 步）。可以重跑。
#   sudo bash install.sh        （deploy.sh、adam-deploy.service、install.sh 放在同一個資料夾）
# 做的事：
#   1. 檢查需要的指令（docker、aws、curl、flock、systemctl、timeout）、docker 和 SSM agent 在跑
#   2. /opt/adam/deploy.sh（755）、/etc/systemd/system/adam-deploy.service（644）
#   3. /home/ec2-user/analyzer/analysis.env 搬到 /opt/adam/analysis.env（600、root）；已經有就不蓋
#   4. systemctl enable adam-deploy.service：之後每次開機部署 Parameter Store 記的版本。
#      這裡不啟動它：不在安裝時突然換掉正在跑的容器，也不和 GitHub 送來的部署搶（deploy.sh 有鎖，但沒必要排隊）
#   5. 確認讀得到 Parameter Store 的兩個參數（desired-commit 可能還沒有；密碼只測讀得到，不印）
# 改了 adam-deploy.service 要再跑一次（deploy.sh 每次部署都由 workflow 換成那個 commit 的，不用重跑）。
# =============================================================================
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root AWS_PAGER=""
SRC_DIR=$(cd "$(dirname "$0")" && pwd)

# 和 deploy.sh 相同的預設；/opt/adam/deploy.conf 改過的話照它
REGION=us-east-1
DESIRED_PARAM=/adam/analyzer/desired-commit
PASSWORD_PARAM=/adam/db/migrator-password
ENV_FILE=/opt/adam/analysis.env
MODELS_DIR=/opt/adam/models
OLD_ENV_FILE=/home/ec2-user/analyzer/analysis.env
UNIT_FILE=/etc/systemd/system/adam-deploy.service
if [ -r /opt/adam/deploy.conf ] && bash -n /opt/adam/deploy.conf; then
    # shellcheck source=/dev/null
    . /opt/adam/deploy.conf
fi

WARNINGS=()
warn() {
    WARNINGS+=("$*")
    echo "   注意：$*"
}
die() {
    echo "停下來：$*"
    exit 1
}

if [ "$(id -u)" != 0 ]; then die "要用 root 執行：sudo bash $0"; fi
for f in deploy.sh adam-deploy.service; do
    [ -f "$SRC_DIR/$f" ] || die "找不到 $SRC_DIR/$f（deploy.sh、adam-deploy.service、install.sh 要放在同一個資料夾）"
done

echo "== 1. 檢查"
missing=()
for c in docker aws curl flock systemctl timeout; do
    command -v "$c" >/dev/null || missing+=("$c")
done
if [ "${#missing[@]}" -gt 0 ]; then
    die "少了這些指令：${missing[*]}（Amazon Linux 2023 內建 aws、curl、flock；docker 用 sudo dnf install -y docker）"
fi
echo "   docker、aws、curl、flock、systemctl、timeout 都有"
if version=$(timeout 30 docker info --format '{{.ServerVersion}}' 2>/dev/null); then
    echo "   docker $version 在跑"
else
    warn "docker 沒在跑：sudo systemctl enable --now docker"
fi
if ! systemctl is-enabled --quiet docker; then
    warn "docker 開機時不會啟動，容器和開機時的部署都起不來：sudo systemctl enable docker"
fi
if systemctl is-active --quiet amazon-ssm-agent; then
    echo "   amazon-ssm-agent 在跑"
else
    warn "amazon-ssm-agent 沒在跑，GitHub 的部署送不到這台：sudo systemctl enable --now amazon-ssm-agent（還有 docs/rebuild/cd/PLAN.md 第 8 節第 5、6 步）"
fi

echo "== 2. 裝 deploy.sh 和 adam-deploy.service"
install -d -m 755 /opt/adam || die "建不了 /opt/adam"
# 先寫到旁邊再換名字（換 inode）：正在跑的 deploy.sh 不受影響
if ! install -m 755 "$SRC_DIR/deploy.sh" /opt/adam/.deploy.sh.new || ! mv -f /opt/adam/.deploy.sh.new /opt/adam/deploy.sh; then
    die "裝不了 /opt/adam/deploy.sh"
fi
install -m 644 "$SRC_DIR/adam-deploy.service" "$UNIT_FILE" || die "裝不了 $UNIT_FILE"
echo "   /opt/adam/deploy.sh、$UNIT_FILE"

echo "== 3. analysis.env"
# 搬到 /opt/adam 之後，原位置留一個指向它的符號連結：docs 裡手動換容器的指令（sudo docker run --env-file
# ~/analyzer/analysis.env）照樣能用。docker 的 CLI 以 root 讀它；不加 sudo 讀不到（檔案是 600、root）
link_old() {
    if [ -L "$OLD_ENV_FILE" ] || [ -e "$OLD_ENV_FILE" ] || [ ! -d "$(dirname "$OLD_ENV_FILE")" ]; then return 0; fi
    if ln -s "$ENV_FILE" "$OLD_ENV_FILE"; then
        echo "   $OLD_ENV_FILE 是指向 $ENV_FILE 的符號連結（sudo docker run --env-file 照樣讀得到）"
    else
        warn "在 $OLD_ENV_FILE 留不了符號連結"
    fi
}
if [ -f "$ENV_FILE" ]; then
    chown root:root "$ENV_FILE" && chmod 600 "$ENV_FILE"
    echo "   $ENV_FILE 已經有了，不蓋掉（權限 600、root）"
    if [ -L "$OLD_ENV_FILE" ]; then
        : # 之前留的符號連結
    elif [ -e "$OLD_ENV_FILE" ]; then
        warn "舊的 $OLD_ENV_FILE 還在，而且是一般的檔案：確認 $ENV_FILE 是對的之後刪掉它（sudo rm $OLD_ENV_FILE），再跑一次 install.sh 換成符號連結"
    else
        link_old
    fi
elif [ -f "$OLD_ENV_FILE" ] && [ ! -L "$OLD_ENV_FILE" ]; then
    if install -m 600 -o root -g root "$OLD_ENV_FILE" "$ENV_FILE.new" && mv -f "$ENV_FILE.new" "$ENV_FILE"; then
        rm -f "$OLD_ENV_FILE"
        echo "   $OLD_ENV_FILE 搬到 $ENV_FILE（600、root）"
        link_old
    else
        rm -f "$ENV_FILE.new"
        die "搬不了 $OLD_ENV_FILE"
    fi
else
    warn "沒有 $ENV_FILE，也沒有 $OLD_ENV_FILE：部署會停在事前檢查（11）。照 docs/rebuild/real-analyze/DEPLOY.md 第 6 步、sqs/DEPLOY.md 第 4 步寫一份放在 $ENV_FILE（sudo，chmod 600）"
fi
if [ ! -d "$MODELS_DIR" ]; then
    warn "沒有模型的資料夾 $MODELS_DIR（docs/rebuild/real-analyze/DEPLOY.md 第 4 步）：部署會停在事前檢查（11）"
fi

echo "== 4. 開機時部署"
systemctl daemon-reload || die "systemctl daemon-reload 失敗"
systemctl enable adam-deploy.service || die "systemctl enable adam-deploy.service 失敗"
echo "   adam-deploy.service 已經 enable：每次開機部署 Parameter Store 記的版本（現在不啟動）"

echo "== 5. Parameter Store"
err=$(mktemp)
trap 'rm -f "$err"' EXIT
if value=$(timeout 60 aws ssm get-parameter --region "$REGION" --name "$DESIRED_PARAM" \
    --query Parameter.Value --output text 2>"$err"); then
    echo "   $DESIRED_PARAM = $value"
elif grep -q ParameterNotFound "$err"; then
    echo "   $DESIRED_PARAM 還沒有：GitHub 的 Deploy analyzer 第一次跑完之後才有"
else
    warn "讀不到 $DESIRED_PARAM：$(head -n 2 "$err")（EC2 的角色要有 ADAMDeployReads，PLAN 第 8 節第 5 步）"
fi
# 密碼：只問「解密之後長度大於 0 嗎」，值不離開 aws 這個 process
# shellcheck disable=SC2016 # 反引號是 JMESPath 的數字，不是指令替換
if ok=$(timeout 60 aws ssm get-parameter --region "$REGION" --name "$PASSWORD_PARAM" --with-decryption \
    --query 'length(Parameter.Value) > `0`' --output text 2>"$err"); then
    case $ok in
    True | true) echo "   $PASSWORD_PARAM 讀得到（不印出來）" ;;
    *) warn "$PASSWORD_PARAM 是空的：PLAN 第 8 節第 9 步" ;;
    esac
elif grep -q ParameterNotFound "$err"; then
    warn "還沒有 $PASSWORD_PARAM（adam_migrator 的密碼）：PLAN 第 8 節第 8、9 步"
else
    warn "讀不到 $PASSWORD_PARAM：$(head -n 2 "$err")（EC2 的角色要有 ADAMDeployReads，PLAN 第 8 節第 5 步）"
fi

echo
if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo "裝好了，但有 ${#WARNINGS[@]} 件事要處理："
    for w in "${WARNINGS[@]}"; do echo "  - $w"; done
else
    echo "裝好了。"
fi
cat <<EOF

下一步：
  1. 部署：GitHub → Actions → Deploy analyzer → Run workflow（或合併到 main）。
     Parameter Store 已經有版本、想現在就在這台部署：sudo /opt/adam/deploy.sh --retry-failed
  2. 看結果：sudo tail -n 80 /var/log/adam-deploy.log、cat /opt/adam/deployed-commit、
     sudo docker exec adam-analysis python -m analysis_queue status
  3. 之後每次開機會自己補上停機期間合併的版本（上一次的結果：systemctl status adam-deploy、journalctl -u adam-deploy）。
  4. 從現在起容器由 deploy.sh 換：不要再照 sqs/DEPLOY.md 手動 docker run。analysis.env 在 $ENV_FILE（sudo 才能讀寫）；
     $OLD_ENV_FILE 是指向它的符號連結，緊急時手動的 sudo docker run --env-file 照樣能用。
  開機時補部署跑的是這台現在的 /opt/adam/deploy.sh（上一次 workflow 或這個 install.sh 裝的），不是 desired-commit 那個 commit 的。
  5. 第一次部署成功之後，可以刪掉手動建的舊映像檔省硬碟（deploy/README.md 的「硬碟」）。
  adam-deploy.service 改了要再跑一次這個 install.sh。
EOF
