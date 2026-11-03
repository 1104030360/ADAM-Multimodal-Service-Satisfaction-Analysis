#!/bin/bash
# 測試用的假 systemctl（cases.sh 裝成 /usr/local/bin/systemctl）：只認 install.sh 用到的。
#   FAKE_SSM_INACTIVE=1    amazon-ssm-agent 沒在跑
#   FAKE_DOCKER_DISABLED=1 docker 開機時不會啟動
set -uo pipefail
F=${FAKE_DIR:-/tmp/fake}
mkdir -p "$F"
# shellcheck source=/dev/null
if [ -f "$F/env" ]; then . "$F/env"; fi
printf 'systemctl %s\n' "$*" >>"$F/calls.log"
case $* in
"is-active --quiet amazon-ssm-agent") [ "${FAKE_SSM_INACTIVE:-0}" != 1 ] ;;
"is-enabled --quiet docker") [ "${FAKE_DOCKER_DISABLED:-0}" != 1 ] ;;
daemon-reload) exit 0 ;;
"enable adam-deploy.service")
    echo "Created symlink /etc/systemd/system/multi-user.target.wants/adam-deploy.service → /etc/systemd/system/adam-deploy.service." >&2
    ;;
*)
    echo "fake systemctl: unsupported: $*" >&2
    exit 64
    ;;
esac
