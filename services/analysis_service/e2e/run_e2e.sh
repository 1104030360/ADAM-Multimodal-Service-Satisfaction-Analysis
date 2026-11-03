#!/bin/zsh
# 本機端對端測試：拋棄式 MySQL 8.4（載入 git 裡的測試用 schema myapp/tests/fixtures/schema.sql）+ moto（模擬 S3 和 SQS）+ 第 0 階段分析服務的
# API 容器和兩個 worker 容器（同一個映像檔；SQS 的流程見 docs/rebuild/sqs/PLAN.md，T49）。
# 用戶端那一側用 repo 原本的 DatabaseConnector 與原始 SQL（見 e2e_phase0.py）。跑完一律刪除這些容器與網路。
#
# 用法（在 repo 根目錄）：services/analysis_service/e2e/run_e2e.sh
#   預設用桌面端的 uv 環境 myapp/.venv/bin/python（先在 myapp/ 執行過 uv sync）；
#   要用別的環境就設定 PY=<python 路徑>。那個環境需要：mysql-connector-python、python-dotenv、boto3、opencv-python、numpy
#   分析服務的映像檔每次都重新 docker build（沒改過的話會直接用快取，幾秒就好），名稱可以用 IMAGE=<名稱:標籤> 改
#   MySQL、moto、分析服務開在本機的 13306、15055、15056（可以用 MYSQL_PORT、S3_PORT、API_PORT 改），
#   和 steps/00_prepare.md 的 adam-mysql、adam-s3、adam-analysis（3306、5055、5056）不衝突，不用先停掉它們。
# 如果 docker pull／build 卡在 load metadata（憑證小幫手在等 keychain），可以先
#   export DOCKER_CONFIG=<只放一個 config.json，內容是 {}> 匿名下載公開映像檔。
set -u
HERE=${0:A:h}
SERVICE=${HERE:h}
SERVICES=${SERVICE:h}
REPO=${SERVICES:h}
SCHEMA=$REPO/myapp/tests/fixtures/schema.sql
PY=${PY:-$REPO/myapp/.venv/bin/python}
IMAGE=${IMAGE:-adam-analysis:sqs-test}
MYSQL_PORT=${MYSQL_PORT:-13306}
S3_PORT=${S3_PORT:-15055}
API_PORT=${API_PORT:-15056}
QUEUE=http://adam-e2e-s3:5000/123456789012/adam-analysis  # moto 的帳號固定是 123456789012
if [[ ! -x $PY ]]; then
  echo "找不到 $PY：先在 myapp/ 執行 uv sync，或設定 PY=<python 路徑>"; exit 2
fi
NET=adam-e2e-net
PW="e2e-$(openssl rand -hex 8)"
OUT=$(mktemp -d -t adam-e2e)

cleanup() {
  docker rm -fv adam-e2e-analysis adam-e2e-worker-1 adam-e2e-worker-2 adam-e2e-s3 adam-e2e-mysql >/dev/null 2>&1
  docker network rm $NET >/dev/null 2>&1
}
trap cleanup EXIT
cleanup

for port in $MYSQL_PORT $S3_PORT $API_PORT; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
    echo "本機 $port 已被占用（用 MYSQL_PORT、S3_PORT、API_PORT 換別的），測試中止"; exit 2
  fi
done

echo "== 建置 $IMAGE"
docker build -q -t $IMAGE $SERVICE >/dev/null || exit 1

echo "== 啟動 MySQL 8.4、moto"
docker network create $NET >/dev/null
docker run -d --name adam-e2e-mysql --network $NET -p 127.0.0.1:$MYSQL_PORT:3306 \
  -e MYSQL_ROOT_PASSWORD=$PW mysql:8.4 >/dev/null
docker run -d --name adam-e2e-s3 --network $NET -p 127.0.0.1:$S3_PORT:5000 python:3.12-slim \
  sh -c "pip install -q --root-user-action=ignore 'moto[server]' && moto_server -H 0.0.0.0 -p 5000" >/dev/null

for i in {1..90}; do
  docker exec adam-e2e-mysql mysql -uroot -p$PW -e "SELECT 1" >/dev/null 2>&1 && break
  sleep 2
done
echo "== 載入 schema（myapp/tests/fixtures/schema.sql）"
docker exec -i adam-e2e-mysql mysql -uroot -p$PW < $SCHEMA 2>&1 | grep -v "Using a password" || true

for i in {1..90}; do
  curl -s -o /dev/null http://127.0.0.1:$S3_PORT/ && break
  sleep 2
done

echo "== 建立 SQS 佇列（DLQ、主佇列；設定和 Console 相同）"
S3_PORT=$S3_PORT $PY -I - <<'EOF' || exit 1
import json, os, boto3
sqs = boto3.client("sqs", endpoint_url=f"http://127.0.0.1:{os.environ['S3_PORT']}", region_name="us-east-1",
                   aws_access_key_id="testing", aws_secret_access_key="testing")
dlq = sqs.create_queue(QueueName="adam-analysis-dlq", Attributes={"MessageRetentionPeriod": "1209600"})["QueueUrl"]
arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
sqs.create_queue(QueueName="adam-analysis", Attributes={
    "VisibilityTimeout": "900", "MessageRetentionPeriod": "345600", "ReceiveMessageWaitTimeSeconds": "20",
    "RedrivePolicy": json.dumps({"deadLetterTargetArn": arn, "maxReceiveCount": "3"})})
EOF

SERVICE_ENV=(-e S3_BUCKET=adam-media-e2e -e AWS_REGION=us-east-1
  -e AWS_ACCESS_KEY_ID=testing -e AWS_SECRET_ACCESS_KEY=testing -e AWS_ENDPOINT_URL=http://adam-e2e-s3:5000
  -e SQS_ANALYSIS_QUEUE_URL=$QUEUE
  -e DB_HOST=adam-e2e-mysql -e DB_PORT=3306 -e DB_NAME=adam -e DB_USER=root -e DB_PASSWORD=$PW)
echo "== 啟動分析服務：API＋兩個 worker"
docker run -d --name adam-e2e-analysis --network $NET -p 127.0.0.1:$API_PORT:5000 "${SERVICE_ENV[@]}" $IMAGE >/dev/null
for n in 1 2; do
  docker run -d --name adam-e2e-worker-$n --hostname adam-e2e-worker-$n --network $NET --stop-timeout 30 \
    "${SERVICE_ENV[@]}" $IMAGE python worker.py >/dev/null
done
for i in {1..60}; do  # 兩個 worker 都登記成 ready（假服務沒有模型，幾秒就好）
  curl -s http://127.0.0.1:$API_PORT/healthz | grep -q '"ready": *2' && break
  sleep 1
done

echo "== 執行測試"
# 不讀 repo 的 .env：桌面端的 DatabaseConnector 會呼叫 load_dotenv()，.env 裡 RDS 的 DB_SSL_CA 會拿去驗本機 MySQL 的憑證
PYTHON_DOTENV_DISABLED=1 env -u DB_SSL_CA -u AWS_ENDPOINT_URL \
  DB_HOST=127.0.0.1 DB_PORT=$MYSQL_PORT DB_NAME=adam DB_USER=root DB_PASSWORD=$PW S3_PORT=$S3_PORT API_PORT=$API_PORT \
  $PY -I $HERE/e2e_phase0.py $OUT
rc=$?

for name in adam-e2e-analysis adam-e2e-worker-1 adam-e2e-worker-2; do
  echo "\n== $name 的 log（最後 8 行）"
  docker logs $name 2>&1 | tail -8
done
echo "\n產生的圖表在 $OUT/charts/"
exit $rc
