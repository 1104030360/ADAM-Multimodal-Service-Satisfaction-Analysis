#!/usr/bin/env bash
# Checks whether an image tag exists on GitHub Container Registry without downloading the image: the same
# registry calls docker pull starts with (a pull token, then the manifest).
#
#   .github/scripts/ghcr_manifest.sh ghcr.io/OWNER/NAME:TAG
#
# Anonymous by default, which is how the EC2 instance pulls. With GHCR_TOKEN (and GHCR_USER) set, it asks
# as that user instead, which also sees private packages. The token is passed to curl on standard input,
# never on its command line.
# Exit codes:
#   0  the tag exists
#   3  the package exists, the tag does not
#   4  access denied: anonymously this means the package is private or does not exist yet
#   1  anything else (network errors, unexpected answers, a malformed reference)
set -euo pipefail

reference=${1:-}
if [[ ! $reference =~ ^ghcr\.io/([a-z0-9][a-z0-9._/-]*):([A-Za-z0-9_][A-Za-z0-9._-]{0,127})$ ]]; then
  echo "不是 ghcr.io/OWNER/NAME:TAG 格式的映像檔：$reference" >&2
  exit 1
fi
repository=${BASH_REMATCH[1]}
tag=${BASH_REMATCH[2]}
registry=${GHCR_REGISTRY_URL:-https://ghcr.io} # the tests point this at a fake registry
accept='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json'
accept+=', application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# 1. A pull token for the repository.
token_url="$registry/token?service=ghcr.io&scope=repository:$repository:pull"
if [[ -n ${GHCR_TOKEN:-} ]]; then
  config="user = \"${GHCR_USER:-github-actions}:$GHCR_TOKEN\""
else
  config=
fi
if ! code=$(curl -sS --retry 3 --max-time 30 -o "$work/token.json" -w '%{http_code}' -K - "$token_url" <<<"$config"); then
  echo "連不上 $registry（拿 $repository 的 token）" >&2
  exit 1
fi
case $code in
  200) ;;
  401 | 403)
    echo "$reference：沒有權限（匿名時代表套件是私人的，或還不存在）"
    exit 4
    ;;
  *)
    echo "$reference：拿 token 時 $registry 回 HTTP $code" >&2
    exit 1
    ;;
esac
if ! token=$(jq -er '.token' "$work/token.json"); then
  echo "$reference：$registry 回的 token 讀不懂" >&2
  exit 1
fi

# 2. The manifest of the tag (HEAD: nothing is downloaded).
manifest_url="$registry/v2/$repository/manifests/$tag"
if ! code=$(curl -sS --retry 3 --max-time 30 -o /dev/null -w '%{http_code}' -I -K - "$manifest_url" \
  <<<"header = \"Authorization: Bearer $token\""$'\n'"header = \"Accept: $accept\""); then
  echo "連不上 $registry（讀 $reference 的 manifest）" >&2
  exit 1
fi
case $code in
  200)
    echo "$reference：存在"
    exit 0
    ;;
  404)
    echo "$reference：套件裡沒有這個 tag"
    exit 3
    ;;
  401 | 403)
    echo "$reference：沒有權限（匿名時代表套件是私人的）"
    exit 4
    ;;
  *)
    echo "$reference：讀 manifest 時 $registry 回 HTTP $code" >&2
    exit 1
    ;;
esac
