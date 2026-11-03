# reports.py — POST /reports 與報表的背景工作（new-feature PLAN 7、8；docs 的 reports_contract.md）
#
# 兩個分析服務共用同一份（說明見 report_model.py 開頭）。app.py 只要：
#     report_jobs = reports.ReportJobs(pool, s3, S3_BUCKET)
#     app.register_blueprint(reports.blueprint(report_jobs))
#     report_jobs.start()
# - 自己一條 worker、自己的佇列：報表不會排在一筆要跑好幾分鐘的分析後面。
# - 每份報表有時間上限（REPORT_TIMEOUT_SEC，預設 180 秒），超過就是 failed「Timed out」，worker 接著做下一份。
#   上限包含 N6 的 explain()（AI 說明，一次呼叫最多約 90 秒，OLLAMA_TIMEOUT_SEC）：explain() 在這個背景工作裡呼叫，
#   不在 HTTP handler 裡；AI 慢的時候只會拖到這一份，最多拖到上限，下一份照常做。上限要比桌面端的 10 分鐘短。
#   Python 的執行緒停不下來：超時的那份留在背景跑完，但它在上傳前、寫狀態前都會先看有沒有被取消，
#   寫狀態也有 WHERE report_status = 'generating'，所以不會把 failed 蓋回 ready。
# - 佇列在記憶體裡：重新啟動時，把 10 分鐘內還是 generating 的報表重新排入（更舊的桌面端會改成 failed）。
# - 狀態只從 generating 走到 ready 或 failed；failure_reason 是給管理員看的英文短句，不放錯誤細節、網址或密碼。
# - 不碰 published_at、published_by（發佈是桌面端的事，R6）。
import logging
import os
import queue
import threading
from contextlib import closing

from flask import Blueprint, jsonify, request

import report_model as model
import report_pdf

log = logging.getLogger("adam-reports")

TIME_LIMIT_SEC = float(os.getenv("REPORT_TIMEOUT_SEC", "180"))  # 讀資料＋AI 說明（約 90 秒）＋畫圖＋上傳
RECOVER_WINDOW_MIN = 10  # 和桌面端的逾時一樣（reports_contract.md 第 4 點）
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 大頭照（N3 限制 5 MB）、report-data JSON
PHOTO_PREFIX = "avatars/"  # 只讀 N3 的大頭照，不讀其他前綴（也是 EC2 role 只給 avatars/* 讀取權限的原因）

# failure_reason：畫面直接顯示（PLAN 7.3）
TIMED_OUT = "Timed out"
NOT_ANALYZED = "The service has no completed analysis."
SERVICE_NOT_FOUND = "The service was not found."
STAFF_NOT_FOUND = "The staff account was not found."
INVALID_REPORT = "The report request is not valid."
READ_FAILED = "Could not read the report data."
RENDER_FAILED = "Could not create the PDF."
UPLOAD_FAILED = "Could not upload the PDF."
UNKNOWN_FAILED = "Report generation failed."


class ReportError(Exception):
    """已知的失敗：reason 是寫進 failure_reason 的英文短句。"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Cancelled(Exception):
    """這份報表已經超時，worker 標成 failed 了：不要再上傳、不要再改狀態。"""


def _step(reason, function, *args):
    """跑一個步驟；不是 ReportError、Cancelled 的例外換成這一步的 reason（細節只記 log）。"""
    try:
        return function(*args)
    except (ReportError, Cancelled):
        raise
    except Exception as exc:
        log.exception("report step failed: %s", reason)
        raise ReportError(reason) from exc


class ReportJobs:
    def __init__(self, pool, s3, bucket, time_limit=None):
        self.pool, self.s3, self.bucket = pool, s3, bucket
        self.time_limit = TIME_LIMIT_SEC if time_limit is None else time_limit
        self.jobs = queue.Queue()
        self.inflight = set()
        self.lock = threading.Lock()
        self.worker = None

    # ---------- DB ----------
    def rows(self, sql, args=()):
        with closing(self.pool.get_connection()) as conn, closing(conn.cursor(dictionary=True)) as cur:
            cur.execute(sql, args)
            result = cur.fetchall()
            conn.commit()
            return result

    def execute(self, sql, args=()):
        with closing(self.pool.get_connection()) as conn, closing(conn.cursor()) as cur:
            cur.execute(sql, args)
            conn.commit()
            return cur.rowcount

    def get_status(self, report_id):
        rows = self.rows(model.STATUS_SQL, (report_id,))
        return rows[0]["report_status"] if rows else None

    def mark_ready(self, report_id, explanation):
        ai_model = explanation.model[:100] if explanation else None  # 欄位長度：VARCHAR(100)、VARCHAR(32)
        prompt = explanation.prompt_version[:32] if explanation else None
        return self.execute(model.READY_SQL, (ai_model, prompt, report_id))

    def mark_failed(self, report_id, reason):
        return self.execute(model.FAILED_SQL, (reason[:255], report_id))

    # ---------- S3 ----------
    def download(self, key):
        """小檔案（大頭照、report-data JSON）；沒有這個檔是 None，其他錯誤往外丟。"""
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except self.s3.exceptions.NoSuchKey:
            return None
        with closing(response["Body"]) as body:
            if response.get("ContentLength", 0) > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"{key} is larger than {MAX_DOWNLOAD_BYTES} bytes")
            return body.read(MAX_DOWNLOAD_BYTES + 1)

    def photo(self, key):
        """大頭照：沒有、不在 avatars/、太大或下載失敗，都畫預設頭像（PLAN 7.4），不讓報表失敗。"""
        if not key or not key.startswith(PHOTO_PREFIX):
            return None
        try:
            data = self.download(key)
        except Exception:
            log.warning("photo download failed: %s", key, exc_info=True)
            return None
        if data is None or len(data) > MAX_DOWNLOAD_BYTES:
            return None
        return report_pdf.load_photo(data)

    def report_data(self, service_id, key=None):
        """report-data JSON：key 是提交成功的那一次分析記下的（T49）；沒有（SQS 之前分析的服務）就讀
        report-data/{service_id}.json。沒有檔案或讀不出來時，三張圖都是 No data（不估算）。"""
        key = key or model.report_data_key(service_id)
        try:
            data = self.download(key)
        except Exception:
            log.warning("report data download failed: %s", key, exc_info=True)
            return None
        parsed = model.parse_report_data(data)
        if data is not None and parsed is None:
            log.warning("report data is not valid: %s", key)
        return parsed

    # ---------- 三種報表 ----------
    def service_report(self, report):
        rows = self.rows(model.SERVICE_SQL, (report["service_id"],))
        if not rows:
            raise ReportError(SERVICE_NOT_FOUND)
        row = rows[0]
        if int(row["service_status"]) != 3 or row["score_service_id"] is None:
            raise ReportError(NOT_ANALYZED)
        report_data = self.report_data(row["service_id"], row.get("report_data_key"))
        # N6：在這個背景工作裡呼叫（受 REPORT_TIMEOUT_SEC 限制）；S3 client 與 bucket 一起給，需要時自己下載逐字稿
        explanation = safe_explain(model.report_inputs(row, report_data, self.s3, self.bucket))
        page = model.service_page(row, report_data, explanation)
        return page, row["account_photo_key"], explanation

    def staff_report(self, report):
        start, end = model.as_date(report["period_start"]), model.as_date(report["period_end"])
        accounts = self.rows(model.ACCOUNT_SQL, (report["staff_account_id"],))
        if not accounts:
            raise ReportError(STAFF_NOT_FOUND)
        rows = self.rows(model.STAFF_SERVICES_SQL,
                         (report["staff_account_id"], *model.period_query_bounds(start, end)))
        return model.staff_page(accounts[0], rows, start, end), accounts[0]["account_photo_key"], None

    def company_report(self, report):
        start, end = model.as_date(report["period_start"]), model.as_date(report["period_end"])
        rows = self.rows(model.COMPANY_SERVICES_SQL, model.period_query_bounds(start, end))
        return model.company_page(rows, start, end), None, None

    # ---------- 一份報表 ----------
    def run(self, report_id, cancelled):
        """讀資料 → 畫 PDF → 上傳 → ready。失敗丟 ReportError；已經超時丟 Cancelled。"""
        rows = _step(READ_FAILED, self.rows, model.REPORT_SQL, (report_id,))
        if not rows:
            log.info("report %s is gone", report_id)
            return
        report = rows[0]
        if report["report_status"] != "generating":  # 桌面端已經改成 failed（逾時），或是重複排入
            log.info("report %s is %s, skipped", report_id, report["report_status"])
            return
        if report["s3_key"] != f"reports/{report_id}.pdf" or report["report_kind"] not in model.REPORT_KINDS:
            raise ReportError(INVALID_REPORT)  # 只寫 reports/ 底下自己的檔案，不照資料表寫到別的位置
        load = {"service": self.service_report, "staff": self.staff_report,
                "company": self.company_report}[report["report_kind"]]
        page, photo_key, explanation = _step(READ_FAILED, load, report)
        photo = self.photo(photo_key)
        title = f"ADAM {report['report_kind']} report"
        pdf = _step(RENDER_FAILED, report_pdf.render, page, photo, title)
        if cancelled.is_set():
            raise Cancelled()
        _step(UPLOAD_FAILED, lambda: self.s3.put_object(Bucket=self.bucket, Key=report["s3_key"], Body=pdf,
                                                         ContentType="application/pdf"))
        if cancelled.is_set():
            raise Cancelled()
        self.mark_ready(report_id, explanation)

    def run_with_limit(self, report_id):
        """在另一條執行緒跑 run()，最多等 time_limit 秒；超時就標 failed「Timed out」，worker 接著做下一份。"""
        cancelled, done, outcome = threading.Event(), threading.Event(), {}

        def target():
            try:
                self.run(report_id, cancelled)
            except Cancelled:
                log.info("report %s finished after its time limit; result dropped", report_id)
            except ReportError as exc:
                outcome["reason"] = exc.reason
            except Exception:
                log.exception("report %s failed", report_id)
                outcome["reason"] = UNKNOWN_FAILED
            finally:
                done.set()

        threading.Thread(target=target, daemon=True, name=f"report-{report_id[:8]}").start()
        if not done.wait(self.time_limit):
            cancelled.set()
            log.warning("report %s timed out after %s s", report_id, self.time_limit)
            self.mark_failed(report_id, TIMED_OUT)
        elif "reason" in outcome:
            log.info("report %s failed: %s", report_id, outcome["reason"])
            self.mark_failed(report_id, outcome["reason"])

    # ---------- 佇列 ----------
    def enqueue(self, report_id):
        """排入佇列；已經在佇列裡（或正在做）就不再排一次（重複送出是冪等的）。"""
        with self.lock:
            if report_id in self.inflight:
                return False
            self.inflight.add(report_id)
        self.jobs.put(report_id)
        return True

    def worker_loop(self):
        while True:
            report_id = self.jobs.get()
            try:
                log.info("report %s start", report_id)
                self.run_with_limit(report_id)
                log.info("report %s end", report_id)
            except Exception:
                log.exception("report %s: could not record the result", report_id)
            finally:
                with self.lock:
                    self.inflight.discard(report_id)
                self.jobs.task_done()

    def recover(self):
        for row in self.rows(model.RECOVER_SQL, (RECOVER_WINDOW_MIN,)):
            self.enqueue(row["report_id"])

    def start(self):
        self.worker = threading.Thread(target=self.worker_loop, daemon=True, name="reports-worker")
        self.worker.start()
        try:
            self.recover()
        except Exception:
            log.exception("report recover failed")


def safe_explain(inputs):
    """N6 的說明失敗或逾時，報表照樣產生，四格寫 Explanation unavailable（PLAN 7.4、9）。"""
    try:
        return model.explain(inputs)
    except Exception:
        log.exception("explanation failed for %s", inputs.service_id)
        return None


def blueprint(jobs):
    """POST /reports {"report_id": "<uuid>"}：202 已排入（重複送出也是 202）、400 格式不對、404 沒有這份、
    409 不是 generating、503 資料庫連不上。產生在背景做，這裡馬上回答。"""
    bp = Blueprint("reports", __name__)

    @bp.post("/reports")
    def create_report():
        body = request.get_json(silent=True)
        report_id = body.get("report_id") if isinstance(body, dict) else None
        if not isinstance(report_id, str):
            return jsonify(error="invalid_report_id", detail="report_id must be a string"), 400
        try:
            status = jobs.get_status(report_id)
        except Exception:
            log.exception("db error")
            return jsonify(error="db_unavailable"), 503
        if status is None:
            return jsonify(error="report_not_found"), 404
        if status != "generating":
            return jsonify(error="report_not_generating", report_status=status), 409
        state = "queued" if jobs.enqueue(report_id) else "already_queued"
        return jsonify(report_id=report_id, state=state), 202

    return bp
