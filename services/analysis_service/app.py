# analysis_service/app.py — 第 0 階段假服務的 API（SQS 之後只有 API：docs/rebuild/sqs/PLAN.md 第 1、3 節、T49）
# POST /analyze 只接受工作：analysis_queue.accept() 在一個交易裡建立工作、outbox、服務狀態 1 → 2，commit 之後回 200。
# 同一個 process 裡的 OutboxPublisher 把 outbox 送到 SQS；分析由 worker 容器（同一個映像檔的 `python worker.py`，
# 假分數、圖表、報表的假數字、假逐字稿都在 worker.py）領去做，提交成功才是狀態 3。
# GET /healthz：從 RDS 讀 worker 的心跳、工作數、outbox（analysis_queue.health()）。
# new-feature N4、N5：POST /reports 產生 PDF 報表（reports.py、report_model.py、report_pdf.py，和 analyzer/ 共用同一份），
# 只在 API 裡跑一份；報表的數字讀提交成功的那一次分析記在 tbl_analysis_job 的 report-data。
import logging
import os

import boto3
from flask import Flask, jsonify, request
from mysql.connector import pooling

import analysis_queue
import reports

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("adam-analysis")

S3_BUCKET = os.environ["S3_BUCKET"]

s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
pool = pooling.MySQLConnectionPool(
    pool_name="adam", pool_size=8,  # 4 個 HTTP 執行緒＋報表的背景工作＋送出器，pool 用完會直接丟錯
    host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")), user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"],
    time_zone=os.getenv("DB_TIME_ZONE", "America/Los_Angeles"))  # new-feature PLAN R4：和 RDS 的 time_zone 相同
settings = analysis_queue.Settings.from_env()  # SQS_ANALYSIS_QUEUE_URL 與 ANALYSIS_*；數字設錯就不啟動

app = Flask(__name__)


# ---------- HTTP ----------
@app.post("/analyze")
def analyze():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    sid = body.get("service_id")
    if not isinstance(sid, str) or not analysis_queue.SID_RE.fullmatch(sid):
        return jsonify(error="invalid_service_id", detail="service_id 必須是 S + 14 位數字"), 400
    if body.get("force"):  # 教學 5-1：這一版不支援重跑已完成的服務，明確拒絕，不默默當成一般的分析
        return jsonify(error="force_not_supported"), 400
    try:
        code, result = analysis_queue.accept(pool, sid, settings)
    except Exception:
        log.exception("db error")
        return jsonify(error="db_unavailable"), 503
    return jsonify(result), code  # 200 queued／already_queued（現行用戶端只認 200）、404、409


@app.get("/healthz")
def healthz():
    """桌面端的連線檢查只看 status、models（failed／loading）、queue_depth，這三個的意思和 SQS 之前一樣。"""
    try:
        state = analysis_queue.health(pool, settings)
    except Exception as exc:
        log.warning("healthz: the database is unavailable: %s", analysis_queue.describe(exc))
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", **state)


# 報表（new-feature N4、N5）：自己的佇列與 worker（reports.py），只在 API 裡跑一份
report_jobs = reports.ReportJobs(pool, s3, S3_BUCKET)
app.register_blueprint(reports.blueprint(report_jobs))
# outbox 的送出器（PLAN 5-2）：SQS client 在這個 process 裡建立（gunicorn 不加 --preload，fork 之後才 import app）
publisher = analysis_queue.OutboxPublisher(pool, analysis_queue.make_sqs_client(), settings)

report_jobs.start()
if os.getenv("OUTBOX_PUBLISHER", "on").strip().lower() != "off":  # off 只給整合測試用：測試自己呼叫 run_once()
    publisher.start()
