#!/bin/bash
# 測試用的假 aws（cases.sh 裝成 /usr/local/bin/aws）：只認 ssm get-parameter。值在 $FAKE_DIR/env：
#   FAKE_DESIRED      /adam/analyzer/desired-commit 的值（空的 = 不存在）
#   FAKE_PASSWORD     /adam/db/migrator-password 的值（空的 = 不存在；沒有 --with-decryption 只給密文）
#   FAKE_AWS_FAIL=1   每一次都 AccessDenied
# calls.log 只記參數（密碼不會在參數裡）。
set -uo pipefail
F=${FAKE_DIR:-/tmp/fake}
mkdir -p "$F"
# shellcheck source=/dev/null
if [ -f "$F/env" ]; then . "$F/env"; fi
printf 'aws %s\n' "$*" >>"$F/calls.log"

if [ "${1:-} ${2:-}" != "ssm get-parameter" ]; then
    echo "fake aws: unsupported: $*" >&2
    exit 64
fi
shift 2
region='' name='' decrypt=0 query='' output=''
while [ $# -gt 0 ]; do
    case $1 in
    --region) region=$2 && shift ;;
    --name) name=$2 && shift ;;
    --with-decryption) decrypt=1 ;;
    --query) query=$2 && shift ;;
    --output) output=$2 && shift ;;
    *)
        echo "fake aws: unknown option $1" >&2
        exit 64
        ;;
    esac
    shift
done
if [ -z "$region" ]; then # EC2 上沒有設預設的區域：一定要帶 --region
    echo "You must specify a region. You can also configure your region by running \"aws configure\"." >&2
    exit 253
fi
if [ "$output" != text ]; then
    echo "fake aws: only --output text" >&2
    exit 64
fi
if [ "${FAKE_AWS_FAIL:-0}" = 1 ]; then
    echo "An error occurred (AccessDeniedException) when calling the GetParameter operation: User: arn:aws:sts::123456789012:assumed-role/adam-analysis-role/i-0123456789abcdef0 is not authorized to perform: ssm:GetParameter on resource: arn:aws:ssm:$region:123456789012:parameter$name" >&2
    exit 254
fi
case $name in
/adam/analyzer/desired-commit) value=${FAKE_DESIRED-} ;;
/adam/db/migrator-password)
    value=${FAKE_PASSWORD-}
    if [ -n "$value" ] && [ "$decrypt" = 0 ]; then value=AQICAHhfakeciphertext; fi
    ;;
*) value='' ;;
esac
if [ -z "$value" ]; then
    echo "An error occurred (ParameterNotFound) when calling the GetParameter operation: Parameter $name not found." >&2
    exit 254
fi
# shellcheck disable=SC2016 # 反引號是 JMESPath 的數字
case $query in
Parameter.Value) printf '%s\n' "$value" ;;
Parameter.Name) printf '%s\n' "$name" ;;
'length(Parameter.Value) > `0`') echo True ;;
*)
    echo "fake aws: unsupported --query $query" >&2
    exit 64
    ;;
esac
