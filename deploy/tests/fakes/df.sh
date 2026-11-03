#!/bin/bash
# 測試用的假 df（cases.sh 裝成 /usr/local/bin/df）：有 $FAKE_DIR/df-avail-kb 時回報那麼多剩餘空間（KB），
# 沒有就交給真的 df。假 docker 的 pull、rmi 會跟著加減。
F=${FAKE_DIR:-/tmp/fake}
if [ -f "$F/df-avail-kb" ]; then
    printf 'Filesystem     1024-blocks  Used Available Capacity Mounted on\n'
    printf '/dev/nvme0n1p1    41922540     0 %9s      50%% /\n' "$(cat "$F/df-avail-kb")"
    exit 0
fi
exec /usr/bin/df "$@"
