#!/usr/bin/env bash
# Deploy step of deploy-analyzer.yml (docs/rebuild/cd/PLAN.md section 3, CONTRACTS.md section 4):
#   1. records the commit the instance should run: /adam/analyzer/desired-commit in Parameter Store;
#   2. instance stopped or starting: done, because adam-deploy.service deploys that commit at boot;
#   3. instance running: runs that commit's deploy/deploy.sh there through Systems Manager Run Command,
#      waits for it, prints its output, and explains its exit code.
#
# Environment: COMMIT (40 lowercase hex digits), REPOSITORY (owner/name), EC2_INSTANCE_ID, ALLOW_DESTRUCTIVE
# (true or false), and the AWS credentials and AWS_REGION from aws-actions/configure-aws-credentials.
# Writes a summary to $GITHUB_STEP_SUMMARY when it is set. The tests shorten the waits with
# POLL_INTERVAL_SEC, SEND_RETRY_SEC, SEND_RETRY_FOR_SEC, and INVOCATION_GRACE_SEC.
# Exit status: 0 when deployed, when there was nothing to do, or when the instance is not running; else 1.
set -euo pipefail

PARAMETER=/adam/analyzer/desired-commit
# Run Command stops deploy.sh after this long: deploy.sh may wait up to 15 minutes for another deploy.sh
# (the boot unit), then pull, migrate, and wait up to 5 minutes for the new version and 5 more to roll back.
EXECUTION_TIMEOUT_SEC=3600
DELIVERY_TIMEOUT_SEC=600   # how long Systems Manager keeps trying to reach the SSM agent
OUTPUT_LIMIT=24000         # Run Command returns only this many characters of stdout
POLL_INTERVAL_SEC=${POLL_INTERVAL_SEC:-10}
SEND_RETRY_SEC=${SEND_RETRY_SEC:-20}
SEND_RETRY_FOR_SEC=${SEND_RETRY_FOR_SEC:-300}
INVOCATION_GRACE_SEC=${INVOCATION_GRACE_SEC:-60}
WAIT_LIMIT_SEC=$((EXECUTION_TIMEOUT_SEC + DELIVERY_TIMEOUT_SEC + 120))

summary=${GITHUB_STEP_SUMMARY:-/dev/null}
export AWS_PAGER="" # never wait for a pager
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

row() { # NAME VALUE: one row of the summary table
  printf '| %s | %s |\n' "$1" "$2" >>"$summary"
}
fail() {
  echo "::error::$1"
  row 結果 "**失敗**：$1"
  exit 1
}
finish() {
  echo "$1"
  row 結果 "$1"
  exit 0
}
# The last AWS CLI error, indented behind a marker so that no line can act as a workflow command.
show_error() {
  sed 's/^/  | /' "$work/err" || true
}
# show_output TITLE FILE: text from the instance, printed with workflow commands switched off.
show_output() {
  local token
  token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
  echo "::group::$1"
  echo "::stop-commands::$token"
  cat "$2"
  printf '\n'
  echo "::$token::"
  echo "::endgroup::"
}
markdown() { # keeps text from breaking the summary table
  printf '%s' "$1" | sed 's/|/\\|/g; s/`/'"'"'/g'
}

{
  echo "### 部署到 EC2"
  echo
  echo "| 項目 | 值 |"
  echo "|---|---|"
} >>"$summary"

commit=${COMMIT:-}
if [[ ! $commit =~ ^[0-9a-f]{40}$ ]]; then
  fail "COMMIT 不是 40 個字元的小寫 SHA。"
fi
if [[ ! ${REPOSITORY:-} =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  fail "REPOSITORY 不是 owner/name 的格式。"
fi
instance=${EC2_INSTANCE_ID:-}
if [[ ! $instance =~ ^i-[0-9a-f]{8,17}$ ]]; then
  fail "repository variable EC2_INSTANCE_ID 不像 EC2 的 Instance ID（i- 開頭）。"
fi

row commit "\`$commit\`"

# 1. The version to run. The boot-time unit reads it too, so it is written before looking at the instance.
if ! version=$(aws ssm put-parameter --name "$PARAMETER" --type String --overwrite --value "$commit" \
  --query Version --output text 2>"$work/err"); then
  show_error
  fail "寫不進 Parameter Store 的 $PARAMETER：檢查部署角色（ADAMGitHubDeploy）的 ssm:PutParameter。"
fi
echo "$PARAMETER = $commit（第 $version 版）"
row desired-commit "已寫入 \`$PARAMETER\`（第 $version 版）"

# 2. Is the instance running?
instance_state() {
  aws ec2 describe-instances --instance-ids "$instance" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>"$work/err"
}
# unless_running STATE: returns only for a running instance; otherwise finishes or fails here.
unless_running() {
  case $1 in
    running) return 0 ;;
    pending) finish "EC2 正在開機：開機時的 adam-deploy.service 會部署 $commit。" ;;
    stopping | stopped) finish "EC2 停機中，下次開機時部署 $commit。" ;;
    shutting-down | terminated) fail "EC2 $instance 已經是 $1：檢查 repository variable EC2_INSTANCE_ID。" ;;
    *) fail "EC2 $instance 的狀態是「$1」，不知道怎麼處理。" ;;
  esac
}
if ! state=$(instance_state); then
  show_error
  fail "讀不到 EC2 $instance 的狀態：Instance ID 對嗎？部署角色有 ec2:DescribeInstances 嗎？"
fi
echo "EC2 $instance：$state"
row EC2 "\`$instance\`（$state）"
unless_running "$state"

# 3. Run that commit's deploy.sh (CONTRACTS section 4). A new inode for deploy.sh leaves a running copy alone.
destructive=
if [[ ${ALLOW_DESTRUCTIVE:-false} == true ]]; then
  destructive=" --allow-destructive-migrations"
fi
jq -n --arg url "https://raw.githubusercontent.com/$REPOSITORY/$commit/deploy/deploy.sh" \
  --arg run "/opt/adam/deploy.sh --retry-failed$destructive" --arg timeout "$EXECUTION_TIMEOUT_SEC" '{
    commands: [
      "set -eu",
      "install -d -m 755 /opt/adam",
      ("curl -fsSL --retry 3 -o /opt/adam/.deploy.sh.new \"" + $url + "\""),
      "chmod 755 /opt/adam/.deploy.sh.new",
      "mv -f /opt/adam/.deploy.sh.new /opt/adam/deploy.sh",
      $run
    ],
    executionTimeout: [$timeout]
  }' >"$work/parameters.json"
echo "在 EC2 上執行：/opt/adam/deploy.sh --retry-failed$destructive"

# Right after a boot the SSM agent is not online yet, and Run Command answers InvalidInstanceId.
retry_until=$((SECONDS + SEND_RETRY_FOR_SEC))
while :; do
  if command_id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "ADAM deploy ${commit:0:12}" --timeout-seconds "$DELIVERY_TIMEOUT_SEC" \
    --parameters "file://$work/parameters.json" --query Command.CommandId --output text 2>"$work/err"); then
    break
  fi
  if ! grep -q InvalidInstanceId "$work/err"; then
    show_error
    fail "Systems Manager 不接受指令：檢查部署角色（ADAMGitHubDeploy）的 ssm:SendCommand，以及政策裡的 Instance ID。"
  fi
  if ((SECONDS >= retry_until)); then
    show_error
    fail "Systems Manager 看不到這台 EC2（SSM agent 沒有上線）：照 PLAN 第 8 節第 6 步檢查 Fleet Manager，修好之後 Re-run 這個 job。desired-commit 已經寫好，EC2 下次開機時也會部署它。"
  fi
  echo "SSM agent 還沒上線（InvalidInstanceId），$SEND_RETRY_SEC 秒後再試"
  sleep "$SEND_RETRY_SEC"
  if ! state=$(instance_state); then
    show_error
    fail "讀不到 EC2 $instance 的狀態。"
  fi
  unless_running "$state"
done
echo "Run Command：$command_id"
row "Run Command" "\`$command_id\`"

# 4. Wait for it. aws ssm wait gives up after about 100 seconds, so poll instead.
started=$SECONDS
reported=$SECONDS
errors=0
while :; do
  if ! aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" --output json \
    >"$work/invocation.json" 2>"$work/err"; then
    # The invocation shows up a few seconds after send-command; other errors may be throttling.
    if grep -q InvocationDoesNotExist "$work/err" && ((SECONDS - started < INVOCATION_GRACE_SEC)); then
      sleep "$POLL_INTERVAL_SEC"
      continue
    fi
    errors=$((errors + 1))
    if ((errors >= 5)); then
      show_error
      fail "讀不到 Run Command $command_id 的結果：到 Systems Manager → Run Command 看。"
    fi
    sleep "$POLL_INTERVAL_SEC"
    continue
  fi
  errors=0
  status=$(jq -r '.Status' "$work/invocation.json")
  case $status in
    Pending | InProgress | Delayed | Cancelling) ;;
    *) break ;;
  esac
  if ((SECONDS - started >= WAIT_LIMIT_SEC)); then
    fail "等了 $((WAIT_LIMIT_SEC / 60)) 分鐘 deploy.sh 還沒結束：到 Systems Manager → Run Command 看 $command_id。"
  fi
  if ((SECONDS - reported >= 60)); then
    echo "deploy.sh 還在跑（$status，已經 $((SECONDS - started)) 秒）"
    reported=$SECONDS
  fi
  sleep "$POLL_INTERVAL_SEC"
done

details=$(jq -r '.StatusDetails // ""' "$work/invocation.json")
response=$(jq -r '.ResponseCode // -1' "$work/invocation.json")
length=$(jq -r '.StandardOutputContent // "" | length' "$work/invocation.json")
jq -r '.StandardOutputContent // ""' "$work/invocation.json" >"$work/stdout"
jq -r '.StandardErrorContent // ""' "$work/invocation.json" >"$work/stderr"
echo "Run Command 結束：$status（$details），結束碼 $response"
show_output "EC2 上的輸出（stdout）" "$work/stdout"
if [[ -n $(tr -d '[:space:]' <"$work/stderr") ]]; then
  show_output "EC2 上的錯誤輸出（stderr）" "$work/stderr"
fi

# 5. What happened, in words (CONTRACTS section 4).
explain() {
  case $1 in
    0) echo "部署完成，或沒有要做的（已經是這一版）。" ;;
    10) echo "另一個 deploy.sh 正在跑（例如開機時的 adam-deploy.service），這次沒有動作，舊版照常跑。等它結束後 Re-run 這個 job。" ;;
    11) echo "事前檢查沒過：/opt/adam/analysis.env 不在、docker 沒在跑、硬碟不夠，或讀不到 Parameter Store。舊版照常跑。" ;;
    12) echo "EC2 拉不到映像檔：GHCR 的兩個套件要是 Public、tag 要存在。舊版照常跑。" ;;
    13) echo "有破壞性的 migration 待套用，自動部署停在這裡，舊版照常跑。先在 RDS 建 snapshot，再手動 Run workflow、勾選 allow_destructive_migrations。" ;;
    14) echo "migration 失敗，舊版照常跑。看輸出裡 migrate: 開頭的幾行；這一輪已經成功的 migration 都有記錄。" ;;
    15) echo "資料庫檢查（check-db）沒過：新版需要的資料表、欄位、預存程序或權限不在（看 MISSING、DENIED 那幾行）。舊版照常跑。" ;;
    16) echo "新版 5 分鐘內沒有 models: ready，已經換回上一版（上一版在跑）。" ;;
    17) echo "換回上一版也失敗：分析服務可能停了。馬上到 EC2 看 docker ps 和 /var/log/adam-deploy.log。" ;;
    *) echo "deploy.sh 的結束碼是 $1（約定裡沒有）：看 EC2 的 /var/log/adam-deploy.log。" ;;
  esac
}

# commit=none: deploy.sh stopped before it knew the commit (locked, no parameter, unreadable parameter).
line_pattern='^deploy: result=[^ ]+ code=([0-9]+) commit=([0-9a-f]{40}|none)$'
last=$(grep -E '^deploy: result=' "$work/stdout" | tail -n 1 || true)
if [[ -n $last ]]; then
  row deploy.sh "\`$(markdown "$last")\`"
fi

if [[ $status == Success && $response == 0 ]]; then
  finish "$(explain 0)"
elif [[ $status == Failed || $status == Success ]]; then
  if [[ $last =~ $line_pattern ]]; then
    code=${BASH_REMATCH[1]}
    message=$(explain "$code")
    if [[ $code != "$response" ]]; then
      message="$message（Run Command 的結束碼是 $response）"
    fi
  elif ((length >= OUTPUT_LIMIT)); then
    message="$(explain "$response")（Run Command 只回傳 stdout 的前 $OUTPUT_LIMIT 個字元，看不到最後一行；完整的 log 在 EC2 的 /var/log/adam-deploy.log）"
  else
    message="deploy.sh 沒有跑完（結束碼 $response）：可能是下載 deploy.sh 失敗，或它被中斷了。看上面的輸出和 EC2 的 /var/log/adam-deploy.log。"
  fi
elif [[ $status == TimedOut && $details == DeliveryTimedOut ]]; then
  message="指令沒送到 EC2（SSM agent 離線）。desired-commit 已經寫好，EC2 下次開機時會部署；SSM agent 修好之後 Re-run 這個 job。"
elif [[ $status == TimedOut ]]; then
  message="deploy.sh 超過 $((EXECUTION_TIMEOUT_SEC / 60)) 分鐘，被 Systems Manager 停掉：分析服務可能停在一半，到 EC2 看 docker ps 和 /var/log/adam-deploy.log。"
elif [[ $status == Cancelled ]]; then
  message="Run Command 被取消了（$details）：到 EC2 看 docker ps 和 /var/log/adam-deploy.log，確認分析服務的狀態。"
else
  message="Run Command 結束在 $status（$details）：到 Systems Manager → Run Command 看 $command_id。"
fi
fail "$message"
