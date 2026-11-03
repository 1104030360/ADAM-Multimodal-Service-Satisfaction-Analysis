#!/usr/bin/env bash
# Tests ../ssm_deploy.sh with fake_aws standing in for the AWS CLI: every branch of the deploy step,
# without AWS. Run from anywhere; needs jq.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script=$here/../ssm_deploy.sh
# shellcheck source=assert.sh
source "$here/assert.sh"

COMMIT=0123456789abcdef0123456789abcdef01234567
INSTANCE=i-0123456789abcdef0
dir=

# new_case NAME: a fresh fake AWS with the usual first answers (the parameter is written, version 7).
new_case() {
  begin "$1"
  [[ -z $dir ]] || rm -rf "$dir"
  dir=$(mktemp -d)
  mkdir "$dir/bin"
  ln -s "$here/fake_aws" "$dir/bin/aws"
  answer put-parameter 1 0 7
}

# answer OPERATION N EXIT_CODE TEXT
answer() {
  printf '%s\n%s\n' "$3" "$4" >"$dir/$1.$2"
}

# invocation N STATUS DETAILS RESPONSE_CODE STDOUT [STDERR]
invocation() {
  answer get-command-invocation "$1" 0 "$(jq -n --arg s "$2" --arg d "$3" --argjson r "$4" --arg o "$5" \
    --arg e "${6:-}" '{Status: $s, StatusDetails: $d, ResponseCode: $r, StandardOutputContent: $o,
                      StandardErrorContent: $e}')"
}

# deploy [VAR=VALUE...]: runs the script; sets $code, writes $dir/out and $dir/summary.
deploy() {
  code=0
  env PATH="$dir/bin:$PATH" FAKE_AWS_DIR="$dir" GITHUB_STEP_SUMMARY="$dir/summary" COMMIT="$COMMIT" \
    REPOSITORY=Owner/Repo.Name EC2_INSTANCE_ID="$INSTANCE" ALLOW_DESTRUCTIVE=false POLL_INTERVAL_SEC=0 \
    SEND_RETRY_SEC=0 SEND_RETRY_FOR_SEC=30 INVOCATION_GRACE_SEC=30 "$@" "$script" >"$dir/out" 2>&1 || code=$?
  touch "$dir/calls" "$dir/summary"
}

calls() { # OPERATION: how many times the fake AWS CLI ran it
  grep -c " $1 " "$dir/calls" || true
}

running_with() { # STATUS DETAILS RESPONSE_CODE STDOUT: a running instance and one finished invocation
  answer describe-instances 1 0 running
  answer send-command 1 0 cmd-1
  invocation 1 "$1" "$2" "$3" "$4"
}

deploy_line() { # CODE
  printf 'step one\nstep two\ndeploy: result=x code=%s commit=%s' "$1" "$COMMIT"
}

new_case "stopped instance: parameter written, nothing sent"
answer describe-instances 1 0 stopped
deploy
assert_eq "$code" 0 "exit code"
assert_contains "$dir/calls" "ssm put-parameter --name /adam/analyzer/desired-commit --type String --overwrite --value $COMMIT"
assert_eq "$(calls send-command)" 0 "send-command calls"
assert_contains "$dir/summary" "EC2 停機中，下次開機時部署 $COMMIT。"

new_case "stopping instance counts as stopped"
answer describe-instances 1 0 stopping
deploy
assert_eq "$code" 0 "exit code"
assert_contains "$dir/summary" "EC2 停機中"

new_case "pending instance: the boot unit deploys"
answer describe-instances 1 0 pending
deploy
assert_eq "$code" 0 "exit code"
assert_contains "$dir/summary" "開機時的 adam-deploy.service 會部署 $COMMIT"

new_case "terminated instance fails"
answer describe-instances 1 0 terminated
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "::error::EC2 $INSTANCE 已經是 terminated"

new_case "running instance: deploy.sh succeeds after polling"
answer describe-instances 1 0 running
answer send-command 1 0 cmd-1
invocation 1 InProgress InProgress -1 ""
invocation 2 Success Success 0 "$(deploy_line 0)"
deploy
assert_eq "$code" 0 "exit code"
assert_eq "$(calls get-command-invocation)" 2 "get-command-invocation calls"
assert_contains "$dir/summary" "部署完成"
assert_contains "$dir/summary" "deploy: result=x code=0 commit=$COMMIT"
assert_contains "$dir/summary" "cmd-1"
assert_contains "$dir/calls" "--document-name AWS-RunShellScript"
assert_contains "$dir/calls" "--timeout-seconds 600"
assert_eq "$(jq -r '.executionTimeout[0]' "$dir/parameters.json")" 3600 "executionTimeout"
assert_eq "$(jq -r '.commands | join("\n")' "$dir/parameters.json")" "set -eu
install -d -m 755 /opt/adam
curl -fsSL --retry 3 -o /opt/adam/.deploy.sh.new \"https://raw.githubusercontent.com/Owner/Repo.Name/$COMMIT/deploy/deploy.sh\"
chmod 755 /opt/adam/.deploy.sh.new
mv -f /opt/adam/.deploy.sh.new /opt/adam/deploy.sh
/opt/adam/deploy.sh --retry-failed" "commands"

new_case "allow destructive migrations adds the flag"
running_with Success Success 0 "$(deploy_line 0)"
deploy ALLOW_DESTRUCTIVE=true
assert_eq "$code" 0 "exit code"
assert_eq "$(jq -r '.commands[-1]' "$dir/parameters.json")" \
  "/opt/adam/deploy.sh --retry-failed --allow-destructive-migrations" "last command"

for case_code in 10 11 12 13 14 15 16 17; do
  new_case "deploy.sh exit code $case_code"
  running_with Failed Failed "$case_code" "$(deploy_line "$case_code")"
  deploy
  assert_eq "$code" 1 "exit code"
  assert_contains "$dir/out" "::error::"
  assert_contains "$dir/summary" "code=$case_code"
done
assert_contains "$dir/out" "換回上一版也失敗"

new_case "exit code 13 explains how to allow destructive migrations"
running_with Failed Failed 13 "$(deploy_line 13)"
deploy
assert_contains "$dir/out" "有破壞性的 migration 待套用"
assert_contains "$dir/out" "allow_destructive_migrations"

new_case "exit code 10 before deploy.sh knew the commit (commit=none)"
running_with Failed Failed 10 "waiting for the lock
deploy: result=locked code=10 commit=none"
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "另一個 deploy.sh 正在跑"
assert_contains "$dir/summary" "deploy: result=locked code=10 commit=none"

new_case "exit code 16 says the previous version runs"
running_with Failed Failed 16 "$(deploy_line 16)"
deploy
assert_contains "$dir/out" "已經換回上一版"

new_case "exit code outside the contract"
running_with Failed Failed 42 "$(deploy_line 42)"
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "deploy.sh 的結束碼是 42（約定裡沒有）"

new_case "deploy.sh never ran (download failed)"
running_with Failed Failed 22 "curl: (22) The requested URL returned error: 404"
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "deploy.sh 沒有跑完（結束碼 22）"

new_case "truncated output falls back to the response code"
running_with Failed Failed 12 "$(head -c 24000 /dev/zero | tr '\0' x)"
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "EC2 拉不到映像檔"
assert_contains "$dir/out" "前 24000 個字元"

new_case "remote output cannot run workflow commands"
running_with Success Success 0 "::error::injected
::add-mask::secret
$(deploy_line 0)"
deploy
assert_eq "$code" 0 "exit code"
stop=$(grep -n '^::stop-commands::' "$dir/out" | head -n 1 | cut -d: -f1 || true)
injected=$(grep -n '^::error::injected' "$dir/out" | cut -d: -f1 || true)
token=$(grep '^::stop-commands::' "$dir/out" | head -n 1 | sed 's/^::stop-commands:://' || true)
resume=$(grep -n "^::$token::\$" "$dir/out" | head -n 1 | cut -d: -f1 || true)
if [[ -z $stop || -z $injected || -z $resume || ${#token} -lt 16 ]] || ((stop >= injected || injected >= resume)); then
  flunk "the instance's output is not between ::stop-commands:: and the resume token"
  show "$dir/out"
fi

new_case "SSM agent comes online after two InvalidInstanceId answers"
answer describe-instances 1 0 running
answer send-command 1 254 "An error occurred (InvalidInstanceId) when calling the SendCommand operation: Instances not in a valid state"
answer send-command 3 0 cmd-3
invocation 1 Success Success 0 "$(deploy_line 0)"
deploy
assert_eq "$code" 0 "exit code"
assert_eq "$(calls send-command)" 3 "send-command calls"
assert_eq "$(calls describe-instances)" 3 "describe-instances calls"

new_case "SSM agent never comes online"
answer describe-instances 1 0 running
answer send-command 1 254 "An error occurred (InvalidInstanceId) when calling the SendCommand operation"
deploy SEND_RETRY_FOR_SEC=0
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "SSM agent 沒有上線"

new_case "instance stops while waiting for the SSM agent"
answer describe-instances 1 0 running
answer describe-instances 2 0 stopping
answer send-command 1 254 "An error occurred (InvalidInstanceId) when calling the SendCommand operation"
deploy
assert_eq "$code" 0 "exit code"
assert_contains "$dir/summary" "EC2 停機中"

new_case "send-command denied fails at once"
answer describe-instances 1 0 running
answer send-command 1 254 "An error occurred (AccessDeniedException) when calling the SendCommand operation"
deploy
assert_eq "$code" 1 "exit code"
assert_eq "$(calls send-command)" 1 "send-command calls"
assert_contains "$dir/out" "Systems Manager 不接受指令"
assert_contains "$dir/out" "  | An error occurred (AccessDeniedException)"

new_case "the invocation appears a little after send-command"
answer describe-instances 1 0 running
answer send-command 1 0 cmd-1
answer get-command-invocation 1 254 "An error occurred (InvocationDoesNotExist) when calling the GetCommandInvocation operation"
invocation 2 Success Success 0 "$(deploy_line 0)"
deploy
assert_eq "$code" 0 "exit code"

new_case "execution timeout"
running_with TimedOut ExecutionTimedOut -1 "still pulling"
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "被 Systems Manager 停掉"

new_case "delivery timeout"
running_with TimedOut DeliveryTimedOut -1 ""
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "指令沒送到 EC2"

new_case "cancelled"
running_with Cancelled Canceled -1 ""
deploy
assert_eq "$code" 1 "exit code"
assert_contains "$dir/out" "Run Command 被取消了"

new_case "parameter cannot be written: nothing else happens"
answer put-parameter 1 254 "An error occurred (AccessDeniedException) when calling the PutParameter operation"
deploy
assert_eq "$code" 1 "exit code"
assert_eq "$(calls describe-instances)" 0 "describe-instances calls"
assert_contains "$dir/out" "寫不進 Parameter Store"

new_case "malformed commit: no AWS call"
deploy COMMIT=0123456789ABCDEF0123456789ABCDEF01234567
assert_eq "$code" 1 "exit code"
assert_eq "$(wc -l <"$dir/calls" | tr -d ' ')" 0 "AWS calls"

new_case "malformed instance id: no AWS call"
deploy EC2_INSTANCE_ID='i-123; reboot'
assert_eq "$code" 1 "exit code"
assert_eq "$(wc -l <"$dir/calls" | tr -d ' ')" 0 "AWS calls"

rm -rf "$dir"
end_tests "ssm_deploy.sh"
