# analyzer/app.py — 移植版分析服務的 API（gunicorn app:app）：接受分析、/healthz、/reports。不載入模型、不做分析。
# SQS（docs/rebuild/sqs/PLAN.md 第 3 節、T49）：POST /analyze 由 analysis_queue.accept() 在一個交易裡建立工作、outbox、
#   服務狀態 1 → 2，commit 之後回 200；這個 process 裡的 OutboxPublisher 再把 outbox 送到 SQS。
#   分析在 worker.py（同一個映像檔、另外的容器）：領工作、載入模型、分析、寫結果、狀態 3，失敗由 analysis_queue 重試、改回 1。
# new-feature N4、N5：POST /reports 產生 PDF 報表（reports.py、report_model.py、report_pdf.py，和 analysis_service/ 共用同一份）。
#   報表的佇列還在記憶體裡：只在 API 跑一份（gunicorn -w 1）。報表讀的 report-data 是 worker 提交的那一次（帶 token 的 key）。
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
    pool_name="adam", pool_size=8,  # 4 個 HTTP 執行緒 + 送出器 + 報表的 worker（逾時的報表會多一條），pool 用完會直接丟錯
    host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")), user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"],
    time_zone=os.getenv("DB_TIME_ZONE", "America/Los_Angeles"))  # new-feature PLAN R4：和 RDS 的 time_zone 相同
settings = analysis_queue.Settings.from_env()  # SQS_ANALYSIS_QUEUE_URL 與 ANALYSIS_*（analysis.env）；數字不合理就啟動失敗

app = Flask(__name__)


# ---------- HTTP ----------
@app.post("/analyze")
def analyze():
    """200 已經持久接受（queued、already_queued；不是做完，也不保證已經送到 SQS）、400、404、409、503。"""
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    sid = body.get("service_id")
    if not isinstance(sid, str) or not analysis_queue.SID_RE.fullmatch(sid):
        return jsonify(error="invalid_service_id", detail="service_id 必須是 S + 14 位數字"), 400
    if body.get("force"):  # 教學 5-1：訊息只帶 service_id，分不出重跑的新一輪和舊訊息；這一版不支援重跑已完成的服務
        return jsonify(error="force_not_supported"), 400
    try:
        code, result = analysis_queue.accept(pool, sid, settings)
    except Exception:
        log.exception("db error")
        return jsonify(error="db_unavailable"), 503
    if code == 200:
        log.info("analysis %s accepted (%s)", sid, result["state"])
    return jsonify(result), code  # 現行用戶端只認 200


@app.get("/healthz")
def healthz():
    """模型、等待的工作都看 RDS（worker 的心跳、工作數、outbox）。桌面端的連線檢查只看 status、models、queue_depth。"""
    try:
        details = analysis_queue.health(pool, settings)
    except Exception as exc:
        log.warning("healthz: database unavailable: %s", analysis_queue.describe(exc))
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", **details)


# 報表（new-feature N4、N5）：自己的佇列與 worker，不排在分析後面（reports.py）
report_jobs = reports.ReportJobs(pool, s3, S3_BUCKET)
app.register_blueprint(reports.blueprint(report_jobs))
# outbox → SQS（PLAN 5-2）。SQS client 在這個 process 裡建立：gunicorn 不加 --preload，import app 是在 fork 之後
publisher = analysis_queue.OutboxPublisher(pool, analysis_queue.make_sqs_client(), settings)

report_jobs.start()
if os.getenv("OUTBOX_PUBLISHER", "on").strip().lower() != "off":  # 整合測試設 off，自己呼叫 publisher.run_once()
    publisher.start()
