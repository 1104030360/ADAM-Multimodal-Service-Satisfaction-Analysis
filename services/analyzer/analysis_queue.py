# analysis_queue.py — 分析工作佇列：RDS 的工作與 outbox＋Amazon SQS＋獨立的 worker process
# （docs/AWS tutorial/05_Amazon SQS.md、docs/rebuild/sqs/PLAN.md、tradeoff T49）。
# analysis_service/ 與 analyzer/ 共用，兩邊逐位元組相同（tools/check_report_copies.py 檢查）；
# 各自的 app.py（API）與 worker.py（分析）只負責「怎麼分析、結果寫到哪裡」。
#
# 一筆工作的路線（PLAN 第 2 節）：
#   1. API 的 accept()：一個交易裡建立（或重開）tbl_analysis_job、新增 tbl_analysis_outbox、服務狀態 1 → 2，commit 後回 200。
#   2. API process 裡的 OutboxPublisher：一直送到期的 outbox 到 SQS，成功才標成 sent；失敗就退避重試（PLAN 5-2）。
#   3. worker process 的 Worker：long polling 領一則 → 在 RDS 認領（lease_token、lease_until）→ 分析時每 heartbeat_sec 秒
#      同時延長 SQS 的 visibility 和 RDS 的 lease → 一個交易裡確認 lease、寫結果、工作 done、服務狀態 3 → 刪訊息（PLAN 第 6 節）。
#   失敗時不刪訊息、交還 lease；這一輪的次數用完才把工作標成 failed、服務狀態 2 → 1（桌面端顯示失敗、可以 Retry）。
#
# 幾個約定：
# - 時間一律用資料庫的 UTC_TIMESTAMP(3)：只有一個時鐘，不受連線 time_zone（America/Los_Angeles）和夏令時間影響。
# - 會同時改服務和工作的交易，一律先鎖 tbl_service 那一列、再鎖 tbl_analysis_job，順序相同就不會互鎖（deadlock）。
# - Standard 佇列可能重複交付、outbox 也可能重送：同一筆服務的重複訊息靠 RDS 的工作狀態與 lease 擋下，結果只會提交一次。
# - 輸出檔名帶這一次的 token（run_name()、report_data_key()）：被搶走 lease 的舊 worker 晚到的上傳，不會蓋掉提交的那一份。
import json
import logging
import os
import random
import re
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields
from typing import Callable, Optional

import boto3
from botocore.config import Config

log = logging.getLogger("adam-analysis.queue")

SID_RE = re.compile(r"S[0-9]{14}")  # 用 fullmatch：不接受結尾的換行與非 ASCII 的數字
WORKDIR_PREFIX = "adam-job-"  # worker 的暫存資料夾（錄影檔是個人資料）：開機時清掉上一次沒清的
ALIVE_FILE = os.path.join(tempfile.gettempdir(), "adam-worker-alive")  # worker 容器的 HEALTHCHECK 看它的修改時間
ERROR_CHARS = 1000  # last_error 的欄位長度


# ---------- 設定 ----------
@dataclass(frozen=True)
class Settings:
    """環境變數（analysis.env）→ 設定。只有 SQS_ANALYSIS_QUEUE_URL 一定要設；其他是 PLAN 的起始值，量過之後再調。"""

    queue_url: str = ""
    lease_sec: int = 300  # RDS 的 lease。要比 visibility 短：worker 死掉、訊息再出現時，lease 已經到期，別人才接得手
    heartbeat_sec: int = 60  # 多久續期一次（SQS 的 visibility＋RDS 的 lease，PLAN 6-3）
    visibility_sec: int = 900  # 領取與每次續期的隱藏時間，和 Console 的 Visibility timeout（15 分鐘）相同
    wait_sec: int = 20  # long polling 最多等幾秒（Console 的 Receive message wait time）
    max_attempts: int = 3  # 同一筆服務這一輪最多認領幾次（所有訊息合計，PLAN 第 8 節）
    retry_delay_sec: int = 60  # 失敗、或還不能處理的訊息，多久之後再出現
    job_timeout_sec: int = 7200  # 一筆最多續期多久；SQS 單次接收的隱藏時間最多 12 小時，不能無限延長
    publish_interval_sec: float = 5.0  # 送出器沒事做時多久看一次 outbox
    stale_job_sec: int = 3600  # queued／running 多久沒有動靜要在 log 和 /healthz 提醒
    worker_stale_sec: int = 120  # worker 多久沒出現就當作不在了（/healthz）

    ENV = {
        "lease_sec": "ANALYSIS_LEASE_SEC", "heartbeat_sec": "ANALYSIS_HEARTBEAT_SEC",
        "visibility_sec": "ANALYSIS_VISIBILITY_SEC", "wait_sec": "ANALYSIS_WAIT_SEC",
        "max_attempts": "ANALYSIS_MAX_ATTEMPTS", "retry_delay_sec": "ANALYSIS_RETRY_DELAY_SEC",
        "job_timeout_sec": "ANALYSIS_JOB_TIMEOUT_SEC", "publish_interval_sec": "ANALYSIS_PUBLISH_INTERVAL_SEC",
        "stale_job_sec": "ANALYSIS_STALE_JOB_SEC", "worker_stale_sec": "ANALYSIS_WORKER_STALE_SEC",
    }

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        values = {"queue_url": environ.get("SQS_ANALYSIS_QUEUE_URL", "").strip()}
        types = {f.name: type(f.default) for f in fields(cls)}
        for name, var in cls.ENV.items():
            raw = environ.get(var, "").strip()
            if raw:
                try:
                    values[name] = types[name](raw)
                except ValueError:
                    raise ValueError(f"{var} must be a number, got {raw!r}") from None
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self):
        problems = []
        if not 0 < self.heartbeat_sec < self.lease_sec:
            problems.append("ANALYSIS_HEARTBEAT_SEC must be above 0 and below ANALYSIS_LEASE_SEC")
        if not self.lease_sec <= self.visibility_sec <= 43200:
            problems.append("ANALYSIS_VISIBILITY_SEC must be between ANALYSIS_LEASE_SEC and 43200 (12 hours)")
        if not 0 <= self.wait_sec <= 20:
            problems.append("ANALYSIS_WAIT_SEC must be between 0 and 20")
        if not 1 <= self.max_attempts <= 100:
            problems.append("ANALYSIS_MAX_ATTEMPTS must be between 1 and 100")
        if not 0 <= self.retry_delay_sec <= self.visibility_sec:
            problems.append("ANALYSIS_RETRY_DELAY_SEC must be between 0 and ANALYSIS_VISIBILITY_SEC")
        if not 0 < self.job_timeout_sec <= 43200 - self.visibility_sec:
            problems.append("ANALYSIS_JOB_TIMEOUT_SEC must be above 0 and leave room for one visibility timeout in 12 hours")
        # 送出器的間隔是 float：NaN 和任何數比較都是 False（Event.wait(nan) 馬上回來，送出器不停地查資料庫），
        # inf 或太大的數讓 Event.wait() 丟出 OverflowError（送出器的執行緒停掉，接受的工作永遠送不出去）
        if not 0 < self.publish_interval_sec <= threading.TIMEOUT_MAX or self.stale_job_sec <= 0 \
                or self.worker_stale_sec <= 0:
            problems.append("ANALYSIS_PUBLISH_INTERVAL_SEC, ANALYSIS_STALE_JOB_SEC and ANALYSIS_WORKER_STALE_SEC must be above 0")
        if problems:
            raise ValueError("; ".join(problems))


def make_sqs_client(region=None):
    """每個 process 啟動後自己建立一個，之後重用（PLAN 5-2）；不要在 fork 之前建立後跨 process 共用。

    read_timeout 要比 long polling 的 20 秒長。本機用 moto 時，boto3 會自己讀 AWS_ENDPOINT_URL（或 AWS_ENDPOINT_URL_SQS）。
    """
    return boto3.client(
        "sqs", region_name=region or os.getenv("AWS_REGION"),
        config=Config(connect_timeout=3, read_timeout=30, retries={"mode": "standard", "total_max_attempts": 3}))


# ---------- 小工具 ----------
def new_token():
    return uuid.uuid4().hex


def run_tag(token):
    """檔名裡用的 token 前 12 個字（48 位元，同一筆服務的不同次不會撞）。"""
    return token[:12]


def run_name(service_id, token, name):
    """輸出檔名帶這一次的 token（PLAN 6-4）：{sid}_emotion_TEXT.png → {sid}_{tag}_emotion_TEXT.png。
    桌面端的 media.py 用網址的檔名當本機快取的名字，所以要換檔名，不能只多一層 S3 資料夾。"""
    rest = name[len(service_id) + 1:] if name.startswith(f"{service_id}_") else name
    return f"{service_id}_{run_tag(token)}_{rest}"


def report_data_key(service_id, token):
    """這一次的 report-data（報表要畫的數字）。提交時記在 tbl_analysis_job.report_data_key，報表從那裡讀。"""
    return f"report-data/{service_id}_{run_tag(token)}.json"


def describe(exc):
    """寫進 last_error 的一行：例外的類別和訊息，不含 traceback（traceback 在 log 裡）。"""
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:ERROR_CHARS]


def parse_body(body):
    """SQS 訊息的內容 → service_id；格式不對是 None。"""
    try:
        data = json.loads(body or "")
    except (ValueError, RecursionError):  # 很深的巢狀（"[[[[…"）是 RecursionError：一樣當作格式不對，留給 DLQ
        return None
    sid = data.get("service_id") if isinstance(data, dict) else None
    return sid if isinstance(sid, str) and SID_RE.fullmatch(sid) else None


@contextmanager
def transaction(pool):
    """一條連線、一個交易：區塊正常結束才 commit，丟出例外（包括 return 之外的任何離開方式）就 rollback。"""
    with closing(pool.get_connection()) as conn, closing(conn.cursor()) as cur:
        try:
            yield cur
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


# ---------- SQL ----------
LOCK_SERVICE_SQL = "SELECT service_status FROM tbl_service WHERE service_id = %s FOR UPDATE"
LOCK_JOB_SQL = ("SELECT job_state, attempts, max_attempts, COALESCE(lease_until > UTC_TIMESTAMP(3), 0), "
                "TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(3), lease_until) "
                "FROM tbl_analysis_job WHERE service_id = %s FOR UPDATE")
NEW_JOB_SQL = ("INSERT INTO tbl_analysis_job (service_id, job_state, attempts, max_attempts, accepted_at, updated_at) "
               "VALUES (%s, 'queued', 0, %s, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))")
REOPEN_JOB_SQL = ("UPDATE tbl_analysis_job SET job_state = 'queued', attempts = 0, max_attempts = %s, lease_token = NULL, "
                  "lease_until = NULL, accepted_at = UTC_TIMESTAMP(3), started_at = NULL, finished_at = NULL, "
                  "updated_at = UTC_TIMESTAMP(3) WHERE service_id = %s")
NEW_EVENT_SQL = ("INSERT INTO tbl_analysis_outbox (event_id, service_id, outbox_state, next_attempt_at, created_at) "
                 "VALUES (%s, %s, 'pending', UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))")
# 狀態 2 由這裡寫（PLAN 5-1）：桌面端收到 200 之後不再寫，所以晚到的桌面端寫入不會把失敗的 1 蓋回 2
MARK_SENT_SQL = "UPDATE tbl_service SET service_status = 2 WHERE service_id = %s AND service_status = 1"
MARK_FAILED_SQL = "UPDATE tbl_service SET service_status = 1 WHERE service_id = %s AND service_status = 2"
MARK_DONE_SQL = "UPDATE tbl_service SET service_status = 3 WHERE service_id = %s AND service_status IN (1, 2)"

CLAIM_SQL = ("UPDATE tbl_analysis_job SET job_state = 'running', lease_token = %s, "
             "lease_until = UTC_TIMESTAMP(3) + INTERVAL %s SECOND, attempts = attempts + 1, "
             "started_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3) WHERE service_id = %s")
EXTEND_SQL = ("UPDATE tbl_analysis_job SET lease_until = UTC_TIMESTAMP(3) + INTERVAL %s SECOND, "
              "updated_at = UTC_TIMESTAMP(3) WHERE service_id = %s AND lease_token = %s AND job_state = 'running' "
              "AND lease_until > UTC_TIMESTAMP(3)")
HOLDS_SQL = ("SELECT COUNT(*) FROM tbl_analysis_job WHERE service_id = %s AND lease_token = %s "
             "AND job_state = 'running' AND lease_until > UTC_TIMESTAMP(3)")
FINISH_SQL = ("UPDATE tbl_analysis_job SET job_state = 'done', result_token = %s, report_data_key = %s, "
              "lease_token = NULL, lease_until = NULL, last_error = NULL, finished_at = UTC_TIMESTAMP(3), "
              "updated_at = UTC_TIMESTAMP(3) WHERE service_id = %s AND lease_token = %s AND job_state = 'running' "
              "AND lease_until > UTC_TIMESTAMP(3)")
OWNED_SQL = ("SELECT attempts, max_attempts FROM tbl_analysis_job WHERE service_id = %s AND lease_token = %s "
             "AND job_state = 'running' FOR UPDATE")
RELEASE_SQL = ("UPDATE tbl_analysis_job SET job_state = %s, attempts = %s, lease_token = NULL, lease_until = NULL, "
               "last_error = %s, finished_at = IF(%s = 'failed', UTC_TIMESTAMP(3), NULL), "
               "updated_at = UTC_TIMESTAMP(3) WHERE service_id = %s")
FAIL_JOB_SQL = ("UPDATE tbl_analysis_job SET job_state = 'failed', lease_token = NULL, lease_until = NULL, "
                "last_error = %s, finished_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3) WHERE service_id = %s")
# 最後一次認領的 worker 不見了（被 kill、OOM），lease 也到期了：這一筆不會再有人做
ABANDONED_ERROR = "the worker stopped during the last attempt (lease expired)"
ABANDONED_SQL = ("SELECT service_id FROM tbl_analysis_job WHERE job_state = 'running' AND attempts >= max_attempts "
                 "AND lease_until <= UTC_TIMESTAMP(3) ORDER BY lease_until LIMIT 50")
FAIL_ABANDONED_SQL = ("UPDATE tbl_analysis_job SET job_state = 'failed', lease_token = NULL, lease_until = NULL, "
                      "last_error = %s, finished_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3) "
                      "WHERE service_id = %s AND job_state = 'running' AND attempts >= max_attempts "
                      "AND lease_until <= UTC_TIMESTAMP(3)")

DUE_SQL = ("SELECT event_id, service_id FROM tbl_analysis_outbox WHERE outbox_state = 'pending' "
           "AND next_attempt_at <= UTC_TIMESTAMP(3) ORDER BY next_attempt_at, created_at LIMIT %s FOR UPDATE SKIP LOCKED")
# 拿走一筆就先把下一次可以送的時間往後推：送到一半 process 不見了，過一陣子會再送；兩個 process 不會同時送同一筆
TAKE_SQL = ("UPDATE tbl_analysis_outbox SET send_attempts = send_attempts + 1, "
            "next_attempt_at = UTC_TIMESTAMP(3) + INTERVAL %s SECOND WHERE event_id = %s")
SENT_SQL = ("UPDATE tbl_analysis_outbox SET outbox_state = 'sent', sent_at = UTC_TIMESTAMP(3), sqs_message_id = %s, "
            "last_error = NULL WHERE event_id = %s AND outbox_state = 'pending'")
RETRY_SQL = ("UPDATE tbl_analysis_outbox SET next_attempt_at = UTC_TIMESTAMP(3) + INTERVAL %s SECOND, last_error = %s "
             "WHERE event_id = %s AND outbox_state = 'pending'")
ATTEMPTS_SQL = "SELECT send_attempts FROM tbl_analysis_outbox WHERE event_id = %s"
PURGE_OUTBOX_SQL = ("DELETE FROM tbl_analysis_outbox WHERE outbox_state = 'sent' "
                    "AND sent_at < UTC_TIMESTAMP(3) - INTERVAL 7 DAY LIMIT 500")
PURGE_WORKERS_SQL = "DELETE FROM tbl_analysis_worker WHERE seen_at < UTC_TIMESTAMP(3) - INTERVAL 7 DAY"
STALE_SQL = ("SELECT service_id, job_state, attempts, TIMESTAMPDIFF(SECOND, updated_at, UTC_TIMESTAMP(3)) "
             "FROM tbl_analysis_job WHERE job_state IN ('queued', 'running') "
             "AND (lease_until IS NULL OR lease_until <= UTC_TIMESTAMP(3)) "
             "AND updated_at < UTC_TIMESTAMP(3) - INTERVAL %s SECOND ORDER BY updated_at LIMIT 50")

# started_at 是開始載入模型的時間：只有從別的狀態變成 loading 才重算（載入中每 heartbeat_sec 秒再報一次 loading 不算）。
# 它要寫在 worker_state 前面：同一句裡後面的設定看到的是前面已經改過的值
REPORT_SQL = ("INSERT INTO tbl_analysis_worker (worker_id, worker_state, service_id, detail, started_at, seen_at) "
              "VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)) AS new "
              "ON DUPLICATE KEY UPDATE started_at = IF(new.worker_state = 'loading' AND "
              "tbl_analysis_worker.worker_state <> 'loading', new.started_at, tbl_analysis_worker.started_at), "
              "worker_state = new.worker_state, service_id = new.service_id, "
              "detail = COALESCE(new.detail, tbl_analysis_worker.detail), seen_at = new.seen_at")
WORKERS_SQL = ("SELECT worker_state, COUNT(*) FROM tbl_analysis_worker "
               "WHERE seen_at >= UTC_TIMESTAMP(3) - INTERVAL %s SECOND GROUP BY worker_state")
JOB_COUNTS_SQL = ("SELECT job_state, COUNT(*) FROM tbl_analysis_job WHERE job_state IN ('queued', 'running') "
                  "GROUP BY job_state")
PENDING_SQL = ("SELECT COUNT(*), COALESCE(TIMESTAMPDIFF(SECOND, MIN(created_at), UTC_TIMESTAMP(3)), 0) "
               "FROM tbl_analysis_outbox WHERE outbox_state = 'pending'")
STALE_COUNT_SQL = ("SELECT COUNT(*) FROM tbl_analysis_job WHERE job_state IN ('queued', 'running') "
                   "AND (lease_until IS NULL OR lease_until <= UTC_TIMESTAMP(3)) "
                   "AND updated_at < UTC_TIMESTAMP(3) - INTERVAL %s SECOND")


# ---------- API：先持久保存（PLAN 5-1） ----------
def accept(pool, service_id, settings):
    """POST /analyze 的交易。回傳 (HTTP 狀態碼, JSON)；資料庫錯誤往外丟（app 回 503）。

    - 沒有這筆服務：404。已經完成（狀態 3，或工作 done）：409，和原本一樣不重跑（這一版不支援 force）。
    - 已經有等待或執行中的工作：200 already_queued，不再新增 outbox（重複的 HTTP 請求）。
    - 其他（第一次、上一輪失敗）：建立或重開工作、新增 outbox、狀態 1 → 2，commit 之後 200 queued。
      200 的意思是「已經持久接受」，不是已經完成，也不保證已經送到 SQS。
    """
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (service_id,))  # 同一筆服務的請求、認領、提交排隊，不會互鎖
        rows = cur.fetchall()
        if not rows:
            return 404, {"error": "service_not_found"}
        if int(rows[0][0]) == 3:
            return 409, {"error": "already_completed"}
        cur.execute(LOCK_JOB_SQL, (service_id,))
        job = cur.fetchall()
        if not job:
            cur.execute(NEW_JOB_SQL, (service_id, settings.max_attempts))
        elif job[0][0] in ("queued", "running"):
            cur.execute(MARK_SENT_SQL, (service_id,))  # 例如狀態被手動改回 1、工作卻還在：補回 2
            return 200, {"service_id": service_id, "accepted": True, "state": "already_queued"}
        elif job[0][0] == "done":
            return 409, {"error": "already_completed"}
        else:  # failed：桌面端的 Retry 開新的一輪（次數重算）
            cur.execute(REOPEN_JOB_SQL, (settings.max_attempts, service_id))
        cur.execute(NEW_EVENT_SQL, (new_token(), service_id))
        cur.execute(MARK_SENT_SQL, (service_id,))
    return 200, {"service_id": service_id, "accepted": True, "state": "queued"}


def health(pool, settings):
    """/healthz 的內容（不含 status）。資料庫錯誤往外丟（app 回 503）。

    models：有 worker 等工作或正在做是 ready；只有載入中的是 loading；其他（全部載入失敗、沒有 worker 活著）是 failed，
    桌面端的連線檢查會說「分析服務收得到工作，但不能分析」。queue_depth 是等待＋執行中的工作數。
    """
    with transaction(pool) as cur:
        cur.execute(WORKERS_SQL, (settings.worker_stale_sec,))
        workers = {state: int(count) for state, count in cur.fetchall()}
        cur.execute(JOB_COUNTS_SQL)
        jobs = {state: int(count) for state, count in cur.fetchall()}
        cur.execute(PENDING_SQL)
        pending, oldest = cur.fetchall()[0]
        cur.execute(STALE_COUNT_SQL, (settings.stale_job_sec,))
        stale = int(cur.fetchall()[0][0])
    if workers.get("ready", 0) + workers.get("busy", 0):
        models = "ready"
    elif workers.get("loading", 0):
        models = "loading"
    else:
        models = "failed"
    return {
        "models": models,
        "workers": {state: workers.get(state, 0) for state in ("loading", "ready", "busy", "failed")},
        "queue_depth": jobs.get("queued", 0) + jobs.get("running", 0),
        "jobs": {"queued": jobs.get("queued", 0), "running": jobs.get("running", 0)},
        "outbox_pending": int(pending), "oldest_pending_sec": int(oldest), "stale_jobs": stale,
        "sqs": "configured" if settings.queue_url else "not_configured",
    }


# ---------- 送出器（PLAN 5-2） ----------
class OutboxPublisher:
    """API process 裡的背景執行緒：一直把到期的 outbox 送到 SQS。

    收到 SQS 成功的回應才標成 sent；失敗（包括回應不明確的逾時）就保留待送、退避重試，所以可能重送，這是預期的。
    不看 service_status：狀態 2 的可能正在算，或已經進了 DLQ（PLAN 5-2）。另外每 5 分鐘清掉七天前已送出的紀錄，
    並在 log 提醒很久沒有動靜的工作。
    """

    BATCH = 10
    TAKE_SEC = 60  # 拿走一筆之後，多久沒標成 sent 就可以再送
    HOUSEKEEPING_SEC = 300

    def __init__(self, pool, sqs, settings):
        self.pool, self.sqs, self.settings = pool, sqs, settings
        self.stopping = threading.Event()
        self.thread = None
        self.last_housekeeping = None

    def start(self):
        if not self.settings.queue_url:
            log.error("SQS_ANALYSIS_QUEUE_URL is not set: accepted analyses wait in the outbox until it is")
        self.thread = threading.Thread(target=self.loop, daemon=True, name="outbox")
        self.thread.start()
        return self

    def stop(self, timeout=None):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def loop(self):
        while not self.stopping.is_set():
            sent = 0
            try:
                sent = self.run_once()
                if self.last_housekeeping is None or time.monotonic() - self.last_housekeeping > self.HOUSEKEEPING_SEC:
                    self.last_housekeeping = time.monotonic()
                    self.housekeeping()
            except Exception:
                log.exception("outbox publisher failed; trying again")  # 例如資料庫暫時連不上
            if sent < self.BATCH:  # 這一批沒滿：沒有更多到期的，等一下再看
                self.stopping.wait(self.settings.publish_interval_sec)

    def take_due(self):
        """拿走到期的待送紀錄（最多 BATCH 筆），回傳 [(event_id, service_id)]。SKIP LOCKED：別的 process 正在拿的跳過。"""
        with transaction(self.pool) as cur:
            cur.execute(DUE_SQL, (self.BATCH,))
            due = cur.fetchall()
            for event_id, _ in due:
                cur.execute(TAKE_SQL, (self.TAKE_SEC, event_id))
        return due

    def run_once(self):
        """送出一批，回傳送出幾筆。沒有設定 Queue URL 時不送（紀錄留在 outbox，設定好就會送）。"""
        if not self.settings.queue_url:
            return 0
        sent = 0
        for event_id, service_id in self.take_due():
            try:
                response = self.sqs.send_message(
                    QueueUrl=self.settings.queue_url,
                    MessageBody=json.dumps({"service_id": service_id, "event_id": event_id}))
            except Exception as exc:
                self.retry_later(event_id, exc)
                continue
            with transaction(self.pool) as cur:  # 標記前 process 不見了：之後會再送一次，worker 擋得下重複的
                cur.execute(SENT_SQL, (str(response.get("MessageId", ""))[:128] or None, event_id))
            log.info("outbox %s sent to SQS for %s", event_id, service_id)
            sent += 1
        return sent

    def retry_later(self, event_id, exc):
        with transaction(self.pool) as cur:
            cur.execute(ATTEMPTS_SQL, (event_id,))
            rows = cur.fetchall()
            attempts = int(rows[0][0]) if rows else 1
            delay = self.backoff(attempts)
            cur.execute(RETRY_SQL, (delay, describe(exc), event_id))
        log.warning("outbox %s not sent (attempt %d), retrying in %d s: %s", event_id, attempts, delay, describe(exc))

    @staticmethod
    def backoff(attempts):
        """5、10、20、40… 秒，最多 5 分鐘，再加最多 20% 的隨機值（SQS 恢復時不要所有紀錄同時湧上）。"""
        base = min(300, 5 * 2 ** max(0, attempts - 1))
        return int(base + random.uniform(0, base * 0.2))

    def housekeeping(self):
        fail_abandoned(self.pool)
        with transaction(self.pool) as cur:
            cur.execute(PURGE_OUTBOX_SQL)
            cur.execute(PURGE_WORKERS_SQL)
            cur.execute(STALE_SQL, (self.settings.stale_job_sec,))
            stale = cur.fetchall()
        for service_id, state, attempts, idle in stale:
            log.warning("job %s has been %s for %d min with no worker holding it (attempts %d): check the workers, "
                        "the DLQ and `python -m analysis_queue status`", service_id, state, int(idle) // 60, attempts)


# ---------- worker：認領、續期、寫完才刪除（PLAN 第 6 節） ----------
@dataclass(frozen=True)
class Job:
    """交給各服務 process() 的一筆工作。輸出都寫在 workdir（做完一定刪掉），檔名用 run_name(service_id, token, …)。"""

    service_id: str
    token: str  # 這一次的 lease token，也當服務結果（W1）的 run 編號
    attempt: int  # 這一輪第幾次認領（1 起算）
    receive_count: int  # 這則 SQS 訊息第幾次被領（ApproximateReceiveCount）
    workdir: str


@dataclass
class Prepared:
    """process() 做完、輸出都上傳之後交回來的東西。

    write(cur)：在提交的交易裡寫分數、逐字稿、圖表位置等（lease 已經確認、服務那一列已經鎖住；不要 commit）。
    report_data_key：這一次 report-data 的 S3 key（沒有是 None），記在工作那一列，報表從那裡讀。
    after_commit()：提交之後才做的事，例如排入服務結果（W1）；失敗只記 log。
    """

    write: Callable
    report_data_key: Optional[str] = None
    after_commit: Optional[Callable] = None


class LeaseLost(Exception):
    """lease 已經不是這個 worker 的（到期被別人接手、或管理員結束了這筆）：這一次的結果不能提交。"""


@dataclass(frozen=True)
class Claim:
    result: str  # claimed、done、busy、failed、missing
    attempt: int = 0
    lease_left: int = 0


def claim(pool, service_id, token, settings):
    """在 RDS 認領（PLAN 6-2）：只有「可以執行、而且沒有有效 lease」時寫入新的 token；不用狀態 2 當鎖。"""
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (service_id,))
        if not cur.fetchall():
            return Claim("missing")
        cur.execute(LOCK_JOB_SQL, (service_id,))
        rows = cur.fetchall()
        if not rows:
            return Claim("missing")
        state, attempts, max_attempts, lease_valid, lease_left = rows[0]
        if state in ("done", "failed"):
            return Claim(state)
        if state == "running" and lease_valid:
            return Claim("busy", lease_left=int(lease_left or 0))
        if attempts >= max_attempts:  # 最後一次認領的 worker 沒交還就不見了（被 kill、OOM）：整筆失敗
            cur.execute(FAIL_JOB_SQL, (ABANDONED_ERROR, service_id))
            cur.execute(MARK_FAILED_SQL, (service_id,))
            log.error("job %s failed: the worker stopped during the last attempt", service_id)
            return Claim("failed")
        cur.execute(CLAIM_SQL, (token, settings.lease_sec, service_id))
        return Claim("claimed", attempt=int(attempts) + 1)


def fail_if_abandoned(pool, service_id):
    """最後一次認領的 worker 不見了、lease 也到期了：整筆失敗、服務狀態 2 → 1（和 claim() 裡的同一件事）。
    回傳有沒有改。先鎖服務再鎖工作，條件在 UPDATE 裡再確認一次：查出來之後可能已經有人改過。"""
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (service_id,))
        cur.fetchall()
        cur.execute(FAIL_ABANDONED_SQL, (ABANDONED_ERROR, service_id))
        if cur.rowcount != 1:
            return False
        cur.execute(MARK_FAILED_SQL, (service_id,))
    log.error("job %s failed: the worker stopped during the last attempt", service_id)
    return True


def fail_abandoned(pool):
    """送出器的例行檢查（T49）：把最後一次認領的 worker 不見了的工作標成失敗，回傳是哪幾筆。

    SQS 的 maxReceiveCount 和 max_attempts 一樣（教學都是 3）時：三次接收都耗在被 kill 的 worker 上，第 4 次接收 SQS 直接把
    訊息搬進 DLQ、不會再交給 worker，claim() 就沒機會把它標成失敗，服務會一直停在狀態 2。這種工作不會再被做，
    所以不用等管理員：狀態回 1，桌面端照原本的失敗通知與 Retry。"""
    with transaction(pool) as cur:
        cur.execute(ABANDONED_SQL)
        candidates = [service_id for (service_id,) in cur.fetchall()]
    return [service_id for service_id in candidates if fail_if_abandoned(pool, service_id)]


def extend(pool, service_id, token, settings):
    """續期 RDS 的 lease。回傳還是不是自己的（False：已經到期、被接手或被結束，不要再提交）。"""
    with transaction(pool) as cur:
        cur.execute(EXTEND_SQL, (settings.lease_sec, service_id, token))
        if cur.rowcount == 1:
            return True
        cur.execute(HOLDS_SQL, (service_id, token))  # 同一毫秒內續期兩次時 rowcount 是 0，但 lease 還是自己的
        return int(cur.fetchall()[0][0]) == 1


def commit(pool, job, prepared):
    """一個交易：鎖服務 → 用 token 與未過期條件把工作改成 done → 寫結果 → 服務狀態 3（PLAN 6-4）。

    lease 已經不是自己的就丟 LeaseLost（整個交易 rollback，什麼都沒寫）。結果可以安全重試：失敗就整個 rollback。
    """
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (job.service_id,))
        cur.fetchall()
        cur.execute(FINISH_SQL, (job.token, prepared.report_data_key, job.service_id, job.token))
        if cur.rowcount != 1:
            raise LeaseLost(f"{job.service_id}: lease {run_tag(job.token)} is no longer valid")
        prepared.write(cur)
        cur.execute(MARK_DONE_SQL, (job.service_id,))


def release(pool, service_id, token, error, counted=True):
    """這一次沒有做完：交還 lease。回傳工作的新狀態（queued、failed）；lease 已經不是自己的時是 None。

    counted=True（失敗）：次數用完就 failed、服務狀態 2 → 1（桌面端顯示失敗，按 Retry 開新的一輪）；
    還有次數就回 queued，服務維持 2（PLAN 6-4：不要每次例外就寫回 1）。
    counted=False（收到 SIGTERM 正常交還）：這一次不算，回 queued。
    """
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (service_id,))
        cur.fetchall()
        cur.execute(OWNED_SQL, (service_id, token))
        rows = cur.fetchall()
        if not rows:
            return None
        attempts, max_attempts = int(rows[0][0]), int(rows[0][1])
        if not counted:
            attempts = max(0, attempts - 1)
        final = "failed" if counted and attempts >= max_attempts else "queued"
        cur.execute(RELEASE_SQL, (final, attempts, error[:ERROR_CHARS], final, service_id))
        if final == "failed":
            cur.execute(MARK_FAILED_SQL, (service_id,))
        return final


class Heartbeat:
    """處理期間每 heartbeat_sec 秒：延長 RDS 的 lease、SQS 的 visibility，順便告訴 /healthz 這個 worker 還活著。

    在另一條執行緒跑，模型在算的時候也會續期（PLAN 6-3）。lease 已經不是自己的就停止並設 lost；
    超過 job_timeout_sec 也停止續期（SQS 單次接收最多 12 小時），讓 lease 到期、這一次的結果提交不了。
    """

    def __init__(self, worker, service_id, token, receipt):
        self.worker, self.service_id, self.token, self.receipt = worker, service_id, token, receipt
        self.lost = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"heartbeat-{service_id}")

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stopping.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join()

    def run(self):
        settings = self.worker.settings
        started = time.monotonic()
        while not self.stopping.wait(settings.heartbeat_sec):
            if time.monotonic() - started > settings.job_timeout_sec:
                log.error("job %s ran longer than %d s: no more renewals, its lease will expire",
                          self.service_id, settings.job_timeout_sec)
                return
            try:
                if not extend(self.worker.pool, self.service_id, self.token, settings):
                    log.warning("job %s: lease %s lost, its result will not be committed",
                                self.service_id, run_tag(self.token))
                    self.lost.set()
                    return
            except Exception:
                log.exception("job %s: renewing the lease failed; trying again", self.service_id)
            try:
                self.worker.sqs.change_message_visibility(
                    QueueUrl=settings.queue_url, ReceiptHandle=self.receipt, VisibilityTimeout=settings.visibility_sec)
            except Exception as exc:
                log.warning("job %s: extending the SQS visibility failed: %s", self.service_id, describe(exc))
            self.worker.report("busy", self.service_id)


class Worker:
    """一個 worker process 的主迴圈：一次只領一則（PLAN 6-1），做完、失敗或不能做都有交代，不會不刪也不延後就丟著。

    process(job) → Prepared 是各服務的分析：下載、分析、把輸出上傳到 S3（檔名帶 token），不寫資料庫。
    """

    DB_RETRY_SEC = 30  # 認領時資料庫連不上：等多久再領下一則

    def __init__(self, pool, sqs, settings, process, worker_id=None, exit_process=os._exit):
        self.pool, self.sqs, self.settings, self.process = pool, sqs, settings, process
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"[:64]
        self.exit_process = exit_process
        self.stopping = threading.Event()
        self.current = None  # (service_id, token, receipt, workdir)：正在做的那一筆
        self.detail = None

    # ---------- /healthz 用的心跳 ----------
    def report(self, state, service_id=None, detail=None):
        """寫 tbl_analysis_worker、碰一下 ALIVE_FILE。失敗只記 log：心跳寫不進去不能讓分析失敗。"""
        if detail is not None:
            self.detail = detail[:255]
        try:
            with transaction(self.pool) as cur:
                cur.execute(REPORT_SQL, (self.worker_id, state, service_id, self.detail))
        except Exception as exc:
            log.warning("worker %s: reporting %s failed: %s", self.worker_id, state, describe(exc))
        try:
            with open(ALIVE_FILE, "w", encoding="ascii") as alive:
                alive.write(state)
        except OSError:
            pass

    # ---------- 主迴圈 ----------
    def run(self):
        """模型載好之後呼叫：一直領工作，直到 stop_requested。"""
        self.report("ready")
        while not self.stopping.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                log.warning("worker %s: receiving from SQS failed, trying again in 5 s: %s", self.worker_id, describe(exc))
                self.stopping.wait(5)
        self.report("stopped")

    def poll_once(self, wait_sec=None):
        """long polling 一次（最多 wait_sec 秒），有訊息就處理。回傳處理了幾則。"""
        response = self.sqs.receive_message(
            QueueUrl=self.settings.queue_url, MaxNumberOfMessages=1,
            WaitTimeSeconds=self.settings.wait_sec if wait_sec is None else wait_sec,
            VisibilityTimeout=self.settings.visibility_sec,
            MessageSystemAttributeNames=["ApproximateReceiveCount"])
        messages = response.get("Messages", [])
        for message in messages:
            self.handle(message)
        if not messages:
            self.report("ready")
        return len(messages)

    def handle(self, message):
        """處理一則訊息，回傳發生了什麼（測試與 log 用）：
        done、retry、failed、busy、lost、duplicate、invalid、missing、returned、error。"""
        receipt = message["ReceiptHandle"]
        count = int((message.get("Attributes") or {}).get("ApproximateReceiveCount", "1"))
        service_id = parse_body(message.get("Body"))
        if service_id is None:  # 不當作成功刪掉：依接收次數進 DLQ，留著查（PLAN 6-2）
            log.error("message %s has no valid service_id (receive %d): left for the DLQ",
                      message.get("MessageId"), count)
            self.delay(receipt, self.settings.retry_delay_sec)
            return "invalid"
        if self.stopping.is_set():  # 正要結束：馬上還回去，讓別的 worker 領
            self.delay(receipt, 0)
            return "returned"
        token = new_token()
        try:
            result = claim(self.pool, service_id, token, self.settings)
        except Exception as exc:  # 資料庫暫時連不上：訊息留到 visibility 到期再出現，先別領下一則（不要一直耗掉接收次數）
            log.warning("job %s: claiming failed, waiting %d s: %s", service_id, self.DB_RETRY_SEC, describe(exc))
            self.stopping.wait(self.DB_RETRY_SEC)
            return "error"
        if result.result == "claimed":
            return self.run_job(service_id, token, result.attempt, count, receipt)
        if result.result == "done":  # 已經完成：重複的訊息（或提交後、刪除前中斷過）直接刪掉，不重算
            log.info("job %s is already done: deleting the duplicate message", service_id)
            self.delete(receipt)
            return "duplicate"
        if result.result == "busy":  # 另一個 worker 拿著有效的 lease：不算、也不刪，等它的 lease 快到期再看
            self.delay(receipt, min(self.settings.visibility_sec, max(30, result.lease_left + 30)))
            return "busy"
        if result.result == "failed":  # 這一輪已經結束：不重新開跑，訊息依接收次數進 DLQ
            log.warning("job %s has failed: leaving this message for the DLQ (receive %d)", service_id, count)
            self.delay(receipt, self.settings.retry_delay_sec)
            return "failed"
        log.error("message for %s has no analysis job (receive %d): left for the DLQ", service_id, count)
        self.delay(receipt, self.settings.retry_delay_sec)
        return "missing"

    def run_job(self, service_id, token, attempt, count, receipt):
        workdir = tempfile.mkdtemp(prefix=f"{WORKDIR_PREFIX}{service_id}-{run_tag(token)}-")
        job = Job(service_id, token, attempt, count, workdir)
        self.current = (service_id, token, receipt, workdir)
        self.report("busy", service_id)
        heartbeat = Heartbeat(self, service_id, token, receipt).start()
        log.info("job %s start (attempt %d, receive %d, lease %s)", service_id, attempt, count, run_tag(token))
        started = time.monotonic()
        try:
            prepared = self.process(job)
            # 提交前先停 heartbeat（等它結束）：提交只要一下子，lease 和 visibility 都還剩至少
            # lease_sec - heartbeat_sec 秒；也不會有續期和提交搶同一列、把成功的提交誤報成 lease lost
            heartbeat.stop()
            commit(self.pool, job, prepared)
        except LeaseLost as exc:
            log.warning("job %s: %s; result discarded", service_id, exc)
            return "lost"
        except Exception as exc:
            heartbeat.stop()
            log.exception("job %s failed (attempt %d)", service_id, attempt)
            final = self.release_quietly(service_id, token, describe(exc))
            self.delay(receipt, self.settings.retry_delay_sec)
            return "failed" if final == "failed" else "retry"
        finally:
            heartbeat.stop()
            shutil.rmtree(workdir, ignore_errors=True)
            self.current = None
            self.report("ready")
        if prepared.after_commit is not None:  # commit 成功、heartbeat 已經結束，最後才刪除（PLAN 6-4）
            try:
                prepared.after_commit()
            except Exception:
                log.exception("job %s: the step after commit failed", service_id)
        self.delete(receipt)  # commit 之後、刪除之前中斷也沒關係：下次收到時工作已經 done，直接刪
        log.info("job %s done in %.1f s", service_id, time.monotonic() - started)
        return "done"

    def release_quietly(self, service_id, token, error, counted=True):
        try:
            final = release(self.pool, service_id, token, error, counted)
        except Exception:
            log.exception("job %s: giving the lease back failed; it will expire in %d s",
                          service_id, self.settings.lease_sec)
            return None
        if final == "failed":
            log.error("job %s failed for good after its last attempt: service status is back to 1", service_id)
        return final

    def delete(self, receipt):
        """用這次接收的 ReceiptHandle 刪（不是 MessageId）。失敗只記 log：之後再收到會看到 done，再刪一次。"""
        try:
            self.sqs.delete_message(QueueUrl=self.settings.queue_url, ReceiptHandle=receipt)
        except Exception as exc:
            log.warning("deleting a message failed (it will come back and be deleted then): %s", describe(exc))

    def delay(self, receipt, seconds):
        try:
            self.sqs.change_message_visibility(
                QueueUrl=self.settings.queue_url, ReceiptHandle=receipt, VisibilityTimeout=int(seconds))
        except Exception as exc:
            log.warning("changing a message's visibility failed: %s", describe(exc))

    # ---------- 關機（PLAN 第 7 節：停止領新工作；沒做完的保留訊息供恢復） ----------
    def stop_requested(self, grace_sec=5.0):
        """SIGTERM、SIGINT：不再領新的。正在做的那一筆 grace_sec 秒內沒做完，就交還（這一次不算）、
        讓訊息馬上可以被領，然後結束 process（模型在 C 程式裡算，沒辦法安全地中途停下）。"""
        self.stopping.set()
        if self.current is not None:
            threading.Thread(target=self.give_up_after, args=(grace_sec,), daemon=True, name="shutdown").start()

    def give_up_after(self, grace_sec):
        deadline = time.monotonic() + grace_sec
        while self.current is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        current = self.current
        if current is None:
            return  # 做完了：主迴圈自己結束
        service_id, token, receipt, workdir = current
        log.warning("job %s: stopping before it finished; giving it back for another worker", service_id)
        self.release_quietly(service_id, token, "the worker was stopped before it finished (not counted)",
                             counted=False)
        self.delay(receipt, 0)
        shutil.rmtree(workdir, ignore_errors=True)
        self.report("stopped")
        logging.shutdown()
        self.exit_process(0)


def cleanup_workdirs(root=None):
    """worker 開機時：刪掉上一次（被 kill、OOM）留下的暫存資料夾。容器重新啟動時 /tmp 還在，裡面可能有錄影檔。"""
    root = root or tempfile.gettempdir()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name.startswith(WORKDIR_PREFIX) and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            log.info("removed a leftover work folder: %s", name)


@contextmanager
def keep_reporting(worker, state, interval):
    """區塊執行期間每 interval 秒 worker.report(state)（例如載入模型的好幾分鐘）；離開區塊之前停下來、等它結束，
    之後寫的狀態（ready、failed）不會被晚到的這一個蓋掉。"""
    finished = threading.Event()

    def beat():
        while not finished.wait(interval):
            worker.report(state)

    thread = threading.Thread(target=beat, daemon=True, name=f"report-{state}")
    thread.start()
    try:
        yield
    finally:
        finished.set()
        thread.join()


def serve(pool, process, load_models=None, detail=None, settings=None, sqs=None):
    """worker.py 的主程式：清掉留下的暫存資料夾 → 登記 loading → 載入模型 → 一直領工作，直到 SIGTERM。回傳結束碼。

    模型載入失敗時登記 failed、不領工作（/healthz 會說 models failed），等到被停止為止，免得容器一直重新啟動。
    """
    settings = settings or Settings.from_env()
    if not settings.queue_url:
        log.error("SQS_ANALYSIS_QUEUE_URL is not set: the worker has nothing to read")
        return 2
    cleanup_workdirs()
    worker = Worker(pool, sqs or make_sqs_client(), settings, process)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop_requested())
    worker.report("loading", detail=detail)
    if load_models is not None:
        try:
            # 載入可能要好幾分鐘（EC2 剛開機、兩個 worker 同時載入）：期間每 heartbeat_sec 秒再報一次 loading。
            # 不然超過 worker_stale_sec，/healthz 會當作沒有 worker（models: failed），ALIVE_FILE 也會過期（HEALTHCHECK 失敗）
            with keep_reporting(worker, "loading", settings.heartbeat_sec):
                load_models()
        except Exception as exc:
            log.exception("model loading failed")
            worker.report("failed", detail=describe(exc))
            while not worker.stopping.wait(60):
                worker.report("failed")
            return 1
    log.info("worker %s ready", worker.worker_id)
    worker.run()
    log.info("worker %s stopped", worker.worker_id)
    return 0


# ---------- 管理指令（PLAN 第 8 節）：docker exec <容器> python -m analysis_queue … ----------
STATUS_JOBS_SQL = ("SELECT service_id, job_state, attempts, max_attempts, "
                   "COALESCE(lease_until > UTC_TIMESTAMP(3), 0), "
                   "TIMESTAMPDIFF(MINUTE, updated_at, UTC_TIMESTAMP(3)), COALESCE(last_error, '') "
                   "FROM tbl_analysis_job WHERE job_state IN ('queued', 'running') "
                   "OR (job_state = 'failed' AND finished_at > UTC_TIMESTAMP(3) - INTERVAL 7 DAY) "
                   "ORDER BY updated_at DESC LIMIT 50")
STATUS_WORKERS_SQL = ("SELECT worker_id, worker_state, COALESCE(service_id, ''), COALESCE(detail, ''), "
                      "TIMESTAMPDIFF(SECOND, seen_at, UTC_TIMESTAMP(3)) FROM tbl_analysis_worker "
                      "WHERE seen_at > UTC_TIMESTAMP(3) - INTERVAL 1 DAY ORDER BY worker_id")
LEGACY_SQL = ("SELECT s.service_id FROM tbl_service AS s LEFT JOIN tbl_analysis_job AS j ON j.service_id = s.service_id "
              "WHERE s.service_status = 2 AND j.service_id IS NULL ORDER BY s.service_id LIMIT 50")


def admin_fail(pool, service_id, reason):
    """管理員確認沒有人在做、這一次不再重試：工作標成 failed、服務狀態 2 → 1（桌面端照原本的失敗通知與 Retry）。"""
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (service_id,))
        if not cur.fetchall():
            return "no such service"
        cur.execute(LOCK_JOB_SQL, (service_id,))
        rows = cur.fetchall()
        if not rows:
            return "no analysis job for this service"
        state, _, _, lease_valid, lease_left = rows[0]
        if state == "done":
            return "already done: nothing to fail"
        if state == "running" and lease_valid:
            return f"a worker holds its lease for {int(lease_left or 0)} more seconds: check the worker first"
        cur.execute(FAIL_JOB_SQL, (f"failed by an administrator: {reason}"[:ERROR_CHARS], service_id))
        cur.execute(MARK_FAILED_SQL, (service_id,))
    return "ok: job failed, service status back to 1 (the desktop shows it as failed with Retry)"


def admin_requeue(pool, service_id, settings):
    """修好原因之後重送：工作回 queued（次數重算）、新的 outbox、服務狀態 → 2。也用來接手搬遷前卡在 2 的服務。
    這是 DLQ redrive 之外的另一種重送，不需要 DLQ 的權限；DLQ 裡留下的舊訊息之後被收到時會看到工作的狀態。"""
    with transaction(pool) as cur:
        cur.execute(LOCK_SERVICE_SQL, (service_id,))
        rows = cur.fetchall()
        if not rows:
            return "no such service"
        if int(rows[0][0]) == 3:
            return "already completed (status 3): not requeued"
        cur.execute(LOCK_JOB_SQL, (service_id,))
        job = cur.fetchall()
        if not job:
            cur.execute(NEW_JOB_SQL, (service_id, settings.max_attempts))
        elif job[0][0] == "done":
            return "already done: not requeued"
        elif job[0][0] == "running" and job[0][3]:
            return f"a worker holds its lease for {int(job[0][4] or 0)} more seconds: not requeued"
        else:
            cur.execute(REOPEN_JOB_SQL, (settings.max_attempts, service_id))
        cur.execute(NEW_EVENT_SQL, (new_token(), service_id))
        cur.execute("UPDATE tbl_service SET service_status = 2 WHERE service_id = %s AND service_status IN (1, 2)",
                    (service_id,))
    return "ok: queued again (the outbox sends it to SQS within a few seconds)"


def check_sqs(sqs, settings):
    """唯讀：讀主佇列的設定，和 PLAN 第 3 節比對（教學 Step 6 的檢查）。回傳 (每一行, 全部符合嗎)。"""
    attributes = sqs.get_queue_attributes(QueueUrl=settings.queue_url, AttributeNames=["All"])["Attributes"]
    redrive = json.loads(attributes.get("RedrivePolicy") or "{}")
    expected = [
        ("VisibilityTimeout", attributes.get("VisibilityTimeout"), str(settings.visibility_sec)),
        ("ReceiveMessageWaitTimeSeconds", attributes.get("ReceiveMessageWaitTimeSeconds"), "20"),
        ("MessageRetentionPeriod", attributes.get("MessageRetentionPeriod"), "345600"),
        # 至少 max_attempts：多出來的接收次數（例如 Console 設 5）給「次數用完、標成失敗」和 SIGTERM 交還用，不會比較差
        ("RedrivePolicy.maxReceiveCount", str(redrive.get("maxReceiveCount", "")), f">= {settings.max_attempts}"),
        ("RedrivePolicy.deadLetterTargetArn", redrive.get("deadLetterTargetArn", ""), "(a DLQ)"),
    ]
    lines, ok = [f"QueueArn: {attributes.get('QueueArn')}"], True
    for name, actual, wanted in expected:
        if wanted == "(a DLQ)":
            good = bool(actual)
        elif wanted.startswith(">= "):
            good = actual.isdigit() and int(actual) >= settings.max_attempts
        else:
            good = actual == wanted
        ok &= good
        lines.append(f"{'OK  ' if good else 'DIFF'} {name}: {actual} (expected {wanted})")
    lines.append(f"messages: {attributes.get('ApproximateNumberOfMessages')} waiting, "
                 f"{attributes.get('ApproximateNumberOfMessagesNotVisible')} in flight")
    return lines, ok


def make_pool(size=2):
    """管理指令自己的連線（和 app.py 相同的環境變數）。"""
    from mysql.connector import pooling
    return pooling.MySQLConnectionPool(
        pool_name="adam-admin", pool_size=size, host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"],
        time_zone=os.getenv("DB_TIME_ZONE", "America/Los_Angeles"))


def print_status(pool, settings):
    print(json.dumps(health(pool, settings), indent=2))
    with transaction(pool) as cur:
        cur.execute(STATUS_WORKERS_SQL)
        workers = cur.fetchall()
        cur.execute(STATUS_JOBS_SQL)
        jobs = cur.fetchall()
        cur.execute(LEGACY_SQL)
        legacy = cur.fetchall()
    print("\nworkers (seen in the last day):")
    for worker_id, state, service_id, detail, ago in workers:
        print(f"  {worker_id:<40} {state:<8} {service_id:<16} seen {ago} s ago  {detail}")
    print("\njobs (queued, running, failed in the last 7 days):")
    for service_id, state, attempts, max_attempts, lease_valid, idle, error in jobs:
        lease = "lease held" if lease_valid else "no lease"
        print(f"  {service_id} {state:<8} attempt {attempts}/{max_attempts} {lease:<10} idle {idle} min  {error[:80]}")
    if legacy:
        print("\nstatus 2 with no analysis job (accepted before the SQS queue; `requeue` them):")
        for (service_id,) in legacy:
            print(f"  {service_id}")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="python -m analysis_queue", description="ADAM analysis queue admin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="workers, waiting and stuck jobs, outbox")
    fail = commands.add_parser("fail", help="mark a job failed (service status 2 -> 1)")
    fail.add_argument("service_id")
    fail.add_argument("--reason", default="no retry")
    requeue = commands.add_parser("requeue", help="queue a job again (new SQS message)")
    requeue.add_argument("service_id")
    commands.add_parser("check-sqs", help="read the main queue's settings (read-only)")
    commands.add_parser("alive", help="exit 0 if this worker reported in the last 2 minutes (HEALTHCHECK)")
    args = parser.parse_args(argv)
    if args.command == "alive":
        try:
            return 0 if time.time() - os.path.getmtime(ALIVE_FILE) < 120 else 1
        except OSError:
            return 1
    settings = Settings.from_env()
    if args.command == "check-sqs":
        lines, ok = check_sqs(make_sqs_client(), settings)
        print("\n".join(lines))
        return 0 if ok else 1
    if getattr(args, "service_id", None) and not SID_RE.fullmatch(args.service_id):
        print("service_id must be S followed by 14 digits")
        return 2
    pool = make_pool()
    if args.command == "status":
        print_status(pool, settings)
        return 0
    message = admin_fail(pool, args.service_id, args.reason) if args.command == "fail" \
        else admin_requeue(pool, args.service_id, settings)
    print(message)
    return 0 if message.startswith("ok") else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
