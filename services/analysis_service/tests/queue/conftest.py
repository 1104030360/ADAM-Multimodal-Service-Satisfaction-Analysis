# tests/queue/conftest.py — analysis_queue.py 的整合測試環境（SQS 分析工作佇列，T49；sqs/PLAN.md 第 5 節標 A 的那幾列）。
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）。
#   真的 MySQL 8.4（docker 網路上的拋棄式容器，載入 myapp/tests/fixtures/schema.sql 與這個服務的 tests/integration/grants.sql）、
#   moto 模擬的 SQS（每個測試一組新的主佇列＋DLQ，RedrivePolicy 的 maxReceiveCount 是 3，和 Console 的設定相同）。
#   模組用最小權限的帳號 adam_analysis 透過 pool 連資料庫，所以也驗證了 grants.sql 給三張表與 tbl_service 的權限夠用；
#   測試自己用 root 準備資料、把 lease 或 outbox 的時間往前調（模組只用資料庫的 UTC_TIMESTAMP(3) 當時鐘）、檢查結果。
# 不依賴 tests/integration/conftest.py（那是各服務 app.py 的環境），也不 import app、worker：這裡只測共用的模組。
# 寫結果用 sp_insert_text_storage（兩個服務的 grants.sql 都有它的 EXECUTE）；各服務自己的圖表列在 B、C 的整合測試。
import itertools
import json
import os
import tempfile
import threading
import time
from contextlib import closing

import boto3
import mysql.connector
import pytest
from moto import mock_aws

import analysis_queue

# 和 grants.sql、docker run 的指令一致（和 tests/integration/conftest.py 相同的值）；DB_HOST 一定要從外面給
os.environ.setdefault("DB_NAME", "adam")
os.environ.setdefault("DB_USER", "adam_analysis")
os.environ.setdefault("DB_PASSWORD", "adam-test-only")
os.environ.setdefault("DB_ROOT_PASSWORD", "adam-test-root")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")  # moto 不檢查金鑰，但 boto3 一定要有
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

STAFF_ACCOUNT = "00000000-0000-0000-0000-000000000002"  # 測試用 schema（myapp/tests/fixtures/schema.sql）的種子帳號
SID_PREFIX = "S2099"  # 這些測試的服務編號都用它開頭：清理時只刪這些
REGION = "us-east-1"
JOB_COLUMNS = ("job_state", "attempts", "max_attempts", "lease_token", "lease_until", "last_error", "result_token",
               "report_data_key", "accepted_at", "started_at", "finished_at", "updated_at")
# 這個資料夾不放 __init__.py：放了會變成套件 queue，蓋掉標準函式庫的 queue 模組

_sids = itertools.count(1)


def next_sid():
    """一個還沒用過的服務編號（S2099 開頭，S 後面 14 位數字）。"""
    return f"{SID_PREFIX}{next(_sids):010d}"


def connect_as_root(timeout=60, autocommit=True):
    """MySQL 容器第一次啟動時要先跑 schema、grants.sql（幾秒到二十幾秒），這段時間連不上就重試。"""
    deadline = time.time() + timeout
    while True:
        try:
            return mysql.connector.connect(
                host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")), user="root",
                password=os.environ["DB_ROOT_PASSWORD"], database=os.environ["DB_NAME"], autocommit=autocommit)
        except mysql.connector.Error:
            if time.time() > deadline:
                raise
            time.sleep(2)


class Db:
    """測試自己用的 root 連線（autocommit）：準備資料、改時間、讀出結果。

    背景執行緒（例如 FakeAnalysis 的 after_commit）也會用它，所以一次只讓一條執行緒用這條連線。
    時間一律用 UTC_TIMESTAMP(3)，和模組一樣；不要用 NOW()。
    """

    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.Lock()

    def query(self, sql, *args):
        with self.lock, closing(self.conn.cursor()) as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.with_rows else None

    def connect(self):
        """另一條 root 連線（不 autocommit），例如模擬另一個 process 正拿著某一列的鎖。用完自己 close()。"""
        return connect_as_root(autocommit=False)

    def clean(self):
        """三張 SQS 表整個清空（/healthz 的數字要準），再刪這些測試的服務（分數、逐字稿等 ON DELETE CASCADE）。"""
        self.query("DELETE FROM tbl_analysis_worker")
        self.query("DELETE FROM tbl_analysis_outbox")
        self.query("DELETE FROM tbl_analysis_job")
        self.query("DELETE FROM tbl_service WHERE service_id LIKE %s", f"{SID_PREFIX}%")

    # ---------- 服務 ----------
    def add_service(self, sid, status=1):
        self.query("INSERT INTO tbl_service (service_id, account_id, service_start, service_end, service_status) "
                   "VALUES (%s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP(), %s)", sid, STAFF_ACCOUNT, status)
        return sid

    def set_status(self, sid, status):
        self.query("UPDATE tbl_service SET service_status = %s WHERE service_id = %s", status, sid)

    def status(self, sid):
        return self.query("SELECT service_status FROM tbl_service WHERE service_id = %s", sid)[0][0]

    def results(self, sid):
        """提交的結果列（FakeAnalysis 的 write 登記的逐字稿檔名），依寫入順序。"""
        return [name for (name,) in self.query(
            "SELECT text_name FROM tbl_text_storage WHERE service_id = %s ORDER BY text_id", sid)]

    # ---------- 工作 ----------
    def job(self, sid):
        """工作那一列存的值（dict）；沒有是 None。"""
        rows = self.query(f"SELECT {', '.join(JOB_COLUMNS)} FROM tbl_analysis_job WHERE service_id = %s", sid)
        return dict(zip(JOB_COLUMNS, rows[0])) if rows else None

    def lease(self, sid):
        """(lease 還有效嗎, 還剩幾秒)，用資料庫的時鐘算；沒有 lease 時是 (False, None)。"""
        valid, left = self.query("SELECT COALESCE(lease_until > UTC_TIMESTAMP(3), 0), "
                                 "TIMESTAMPDIFF(MICROSECOND, UTC_TIMESTAMP(3), lease_until) / 1000000 "
                                 "FROM tbl_analysis_job WHERE service_id = %s", sid)[0]
        return bool(valid), None if left is None else float(left)

    def age(self, sid, column):
        """工作的某個時間欄位離現在（UTC）幾秒。"""
        return float(self.query(f"SELECT TIMESTAMPDIFF(MICROSECOND, {column}, UTC_TIMESTAMP(3)) / 1000000 "
                                "FROM tbl_analysis_job WHERE service_id = %s", sid)[0][0])

    def add_job(self, sid, state, attempts=0, max_attempts=3, lease=None, token=None, last_error=None,
                updated_sec_ago=0):
        """直接寫一列工作。lease 是 "valid"（還有 300 秒）或 "expired"（1 秒前到期）；只有 running 能有 lease。"""
        until = {None: None, "valid": 300, "expired": -1}[lease]
        token = token or (("a" * 32) if lease else None)
        self.query("INSERT INTO tbl_analysis_job (service_id, job_state, attempts, max_attempts, lease_token, lease_until, "
                   "last_error, accepted_at, started_at, finished_at, updated_at) VALUES (%s, %s, %s, %s, %s, "
                   "IF(%s IS NULL, NULL, UTC_TIMESTAMP(3) + INTERVAL %s SECOND), %s, "
                   "UTC_TIMESTAMP(3) - INTERVAL %s SECOND, IF(%s > 0, UTC_TIMESTAMP(3), NULL), "
                   "IF(%s IN ('done', 'failed'), UTC_TIMESTAMP(3), NULL), UTC_TIMESTAMP(3) - INTERVAL %s SECOND)",
                   sid, state, attempts, max_attempts, token, until, until, last_error, updated_sec_ago, attempts,
                   state, updated_sec_ago)

    def expire_lease(self, sid):
        """lease 到期（真的要等 lease_sec 秒）：把到期時間改成 1 秒前。"""
        self.query("UPDATE tbl_analysis_job SET lease_until = UTC_TIMESTAMP(3) - INTERVAL 1 SECOND "
                   "WHERE service_id = %s AND lease_until IS NOT NULL", sid)

    # ---------- outbox ----------
    def outbox(self, sid=None):
        """outbox 的列（dict），依建立的順序。due_in：離下一次可以送還有幾秒（負的是已經到期）。"""
        where, args = ("WHERE service_id = %s", (sid,)) if sid else ("", ())
        rows = self.query("SELECT event_id, service_id, outbox_state, send_attempts, last_error, sqs_message_id, "
                          "sent_at IS NOT NULL, TIMESTAMPDIFF(MICROSECOND, UTC_TIMESTAMP(3), next_attempt_at) / 1000000 "
                          f"FROM tbl_analysis_outbox {where} ORDER BY created_at, event_id", *args)
        names = ("event_id", "service_id", "outbox_state", "send_attempts", "last_error", "sqs_message_id", "sent",
                 "due_in")
        return [dict(zip(names, row[:6] + (bool(row[6]), float(row[7])))) for row in rows]

    def add_event(self, sid, state="pending", created_sec_ago=0, sent_sec_ago=None, event_id=None):
        event_id = event_id or analysis_queue.new_token()
        self.query("INSERT INTO tbl_analysis_outbox (event_id, service_id, outbox_state, send_attempts, next_attempt_at, "
                   "created_at, sent_at) VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3) - INTERVAL %s SECOND, "
                   "IF(%s IS NULL, NULL, UTC_TIMESTAMP(3) - INTERVAL %s SECOND))",
                   event_id, sid, state, 1 if state == "sent" else 0, created_sec_ago, sent_sec_ago, sent_sec_ago)
        return event_id

    def make_due(self, sid=None):
        """待送的紀錄全部到期（退避、拿走後的 TAKE_SEC 都當作已經過了）。"""
        where, args = ("AND service_id = %s", (sid,)) if sid else ("", ())
        self.query("UPDATE tbl_analysis_outbox SET next_attempt_at = UTC_TIMESTAMP(3) - INTERVAL 1 SECOND "
                   f"WHERE outbox_state = 'pending' {where}", *args)

    # ---------- worker 的心跳 ----------
    def add_worker(self, worker_id, state="ready", seen_sec_ago=0, service_id=None, detail=None):
        self.query("INSERT INTO tbl_analysis_worker (worker_id, worker_state, service_id, detail, started_at, seen_at) "
                   "VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(3) - INTERVAL %s SECOND, UTC_TIMESTAMP(3) - INTERVAL %s SECOND)",
                   worker_id, state, service_id, detail, seen_sec_ago, seen_sec_ago)

    def worker(self, worker_id):
        rows = self.query("SELECT worker_state, service_id, detail, started_at, "
                          "TIMESTAMPDIFF(MICROSECOND, seen_at, UTC_TIMESTAMP(3)) / 1000000 "
                          "FROM tbl_analysis_worker WHERE worker_id = %s", worker_id)
        if not rows:
            return None
        state, service_id, detail, started_at, seen = rows[0]
        return {"worker_state": state, "service_id": service_id, "detail": detail, "started_at": started_at,
                "seen_ago": float(seen)}


class Queues:
    """這個測試的主佇列與 DLQ（moto）。測試自己看佇列用 sqs；worker、送出器各自拿到同一個 client 或它的替身。"""

    def __init__(self, sqs, url, dlq_url):
        self.sqs, self.url, self.dlq_url = sqs, url, dlq_url

    def send(self, body):
        """直接放一則訊息（例如重複交付、壞掉的內容）。body 不是字串時轉成 JSON。"""
        body = body if isinstance(body, str) else json.dumps(body)
        return self.sqs.send_message(QueueUrl=self.url, MessageBody=body)["MessageId"]

    def counts(self, url=None):
        """(看得到的, 被領走、還沒刪的) 訊息數。"""
        attributes = self.sqs.get_queue_attributes(
            QueueUrl=url or self.url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return int(attributes["ApproximateNumberOfMessages"]), int(attributes["ApproximateNumberOfMessagesNotVisible"])

    def take_all(self, url=None):
        """把佇列裡看得到的訊息全部領出來（藏 900 秒），回傳原始的訊息。領取會算進接收次數：只在測試最後檢查用。"""
        messages = []
        while True:
            batch = self.sqs.receive_message(QueueUrl=url or self.url, MaxNumberOfMessages=10, WaitTimeSeconds=0,
                                             VisibilityTimeout=900).get("Messages", [])
            if not batch:
                return messages
            messages.extend(batch)

    def bodies(self, url=None):
        """take_all() 的內容轉回 dict。"""
        return [json.loads(message["Body"]) for message in self.take_all(url)]

    def show(self, receipt):
        """模擬 visibility 到期（真的要等 15 分鐘）：這次領走的訊息馬上可以再被領。"""
        self.sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=receipt, VisibilityTimeout=0)


class FakeAnalysis:
    """取代各服務的 process(job)：不下載、不分析，在 workdir 寫一個檔案（像下載的錄影），回傳 Prepared。

    write(cur) 在提交的交易裡用 sp_insert_text_storage 登記一個帶 token 的檔名。同一筆服務提交兩次的話檔名不同，
    結果列就會變成兩列：測試數它有沒有變多（和圖表列同一個道理）。
    """

    def __init__(self, db):
        self.db = db
        self.jobs = []  # 每次呼叫收到的 Job
        self.workdirs = []  # 呼叫時 workdir 在不在、寫不寫得進去
        self.writes = []  # write(cur) 寫了哪一次（token）
        self.after = []  # after_commit 被呼叫時資料庫裡的 (服務狀態, 工作狀態)
        self.error = None  # 例外：process() 丟出它（分析失敗）
        self.write_error = None  # 例外：write(cur) 寫完一列之後丟出它（提交途中失敗）
        self.started = threading.Event()  # 已經開始分析
        self.gate = threading.Event()  # 清掉它，process() 就停在這裡等 set()（最多 15 秒）
        self.gate.set()

    def __call__(self, job):
        self.jobs.append(job)
        sample = os.path.join(job.workdir, f"{job.service_id}_cam0.avi")
        with open(sample, "wb") as video:
            video.write(b"recording")
        self.workdirs.append((job.workdir, os.path.isfile(sample)))
        self.started.set()
        self.gate.wait(15)
        if self.error is not None:
            raise self.error
        return analysis_queue.Prepared(
            write=lambda cur: self.write(job, cur),
            report_data_key=analysis_queue.report_data_key(job.service_id, job.token),
            after_commit=lambda: self.after.append((self.db.status(job.service_id),
                                                    self.db.job(job.service_id)["job_state"])))

    def write(self, job, cur):
        name = analysis_queue.run_name(job.service_id, job.token, "transcript.txt")
        cur.execute("CALL sp_insert_text_storage(%s, %s, %s)", (job.service_id, name, f"s3://adam-test/text/{name}"))
        self.writes.append(job.token)
        if self.write_error is not None:
            raise self.write_error


class RecordingWorker(analysis_queue.Worker):
    """真的 Worker，另外記下收到的訊息與 handle() 的結果；exit_process 只記下結束碼，不會結束 pytest。"""

    def __init__(self, *args, **kwargs):
        self.messages, self.results, self.exits = [], [], []
        kwargs.setdefault("exit_process", self.exits.append)
        super().__init__(*args, **kwargs)

    def handle(self, message):
        self.messages.append(message)
        result = super().handle(message)
        self.results.append(result)
        return result


class Background:
    """在另一條執行緒執行 fn(*args)（例如另一個 worker 正在算）；join() 等它結束，回傳結果或丟出它的例外。"""

    def __init__(self, fn, *args):
        self.result = self.error = None
        self.thread = threading.Thread(target=self._run, args=(fn, args), daemon=True)
        self.thread.start()

    def _run(self, fn, args):
        try:
            self.result = fn(*args)
        except BaseException as exc:  # noqa: B902 — 交給 join() 在測試的執行緒丟出
            self.error = exc

    def join(self, timeout=15):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f"背景工作 {timeout} 秒內沒有做完"
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture(scope="session")
def db():
    if "DB_HOST" not in os.environ:
        pytest.fail("佇列的整合測試需要 MySQL：先啟動 MySQL 容器，再用 -e DB_HOST=<容器名稱> 執行")
    conn = connect_as_root()
    yield Db(conn)
    conn.close()


@pytest.fixture(scope="session")
def pool(db):
    """模組用的 pool：最小權限的 adam_analysis（DB_USER），和管理指令同一個 make_pool()。"""
    pool = analysis_queue.make_pool(size=8)
    yield pool
    pool._remove_connections()


@pytest.fixture
def new_pool(db):
    """另外一個 pool（例如另一個 process 的 worker，或同時有很多連線的測試）；測試結束時關掉。"""
    pools = []

    def make(size=4):
        pools.append(analysis_queue.make_pool(size=size))
        return pools[-1]

    yield make
    for extra in pools:
        extra._remove_connections()


@pytest.fixture(autouse=True)
def fresh(db, monkeypatch, tmp_path):
    """每個測試都從空的 SQS 表開始、結束後清掉自己的服務；worker 的暫存資料夾與 ALIVE_FILE 放在 tmp_path。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(analysis_queue, "ALIVE_FILE", str(tmp_path / "adam-worker-alive"))
    db.clean()
    yield
    db.clean()


@pytest.fixture
def new_sid():
    return next_sid


@pytest.fixture
def add_service(db):
    """建立一筆服務（預設狀態 1：錄好、還沒送分析），回傳 service_id。"""
    return lambda status=1: db.add_service(next_sid(), status)


@pytest.fixture
def queues(monkeypatch):
    """moto 的主佇列（visibility 900、long polling 20、保留 4 天、maxReceiveCount 3）與 DLQ，和教學第 3 節相同。"""
    for name in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_SQS"):  # 一律交給 moto，不能連到別的端點
        monkeypatch.delenv(name, raising=False)
    with mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        dlq_url = sqs.create_queue(QueueName="adam-analysis-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        url = sqs.create_queue(QueueName="adam-analysis", Attributes={
            "VisibilityTimeout": "900", "ReceiveMessageWaitTimeSeconds": "20", "MessageRetentionPeriod": "345600",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 3})})["QueueUrl"]
        yield Queues(sqs, url, dlq_url)


@pytest.fixture
def settings(queues):
    """起始值，只改測試要快的：不等 long polling、失敗的訊息馬上再出現、送出器 0.05 秒看一次。"""
    return analysis_queue.Settings(queue_url=queues.url, wait_sec=0, retry_delay_sec=0, publish_interval_sec=0.05)


@pytest.fixture
def analyses(db):
    """FakeAnalysis 的工廠（每個 worker 可以有自己的一個）；測試結束時放開所有停住的分析。"""
    created = []

    def make():
        created.append(FakeAnalysis(db))
        return created[-1]

    make.created = created
    yield make
    for analysis in created:
        analysis.gate.set()


@pytest.fixture
def analysis(analyses):
    return analyses()


@pytest.fixture
def background(queues, analyses):
    """在另一條執行緒跑（Background）。測試結束時先放開停住的分析、等它們做完，moto 才結束（不會連到真的 SQS）。"""
    runs = []

    def start(fn, *args):
        runs.append(Background(fn, *args))
        return runs[-1]

    yield start
    for analysis in analyses.created:
        analysis.gate.set()
    for run in runs:
        run.thread.join(15)


@pytest.fixture
def make_worker(pool, queues, settings, analysis):
    """RecordingWorker 的工廠：預設用 session 的 pool、moto 的 client、上面的 settings、FakeAnalysis；
    每個 worker 有自己的 worker_id（/healthz 的數字才準）。測試結束時叫它們停下。"""
    names = itertools.count(1)
    workers = []

    def make(process=None, pool=pool, sqs=None, settings=settings):
        worker = RecordingWorker(pool, sqs or queues.sqs, settings, process or analysis,
                                 worker_id=f"queue-test-{next(names)}")
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.stopping.set()


@pytest.fixture
def make_publisher(pool, queues, settings):
    """OutboxPublisher 的工廠（預設 session 的 pool、moto 的 client）；測試結束時停掉背景執行緒。"""
    publishers = []

    def make(pool=pool, sqs=None, settings=settings):
        publishers.append(analysis_queue.OutboxPublisher(pool, sqs or queues.sqs, settings))
        return publishers[-1]

    yield make
    for publisher in publishers:
        publisher.stop(timeout=5)


@pytest.fixture
def accepted(pool, settings, add_service, make_publisher):
    """一筆送進 SQS 的服務：狀態 1 → accept()（工作＋outbox＋狀態 2）→ 送出器送出一則訊息。回傳 service_id。"""

    def make():
        sid = add_service(1)
        assert analysis_queue.accept(pool, sid, settings)[0] == 200
        assert make_publisher().run_once() == 1
        return sid

    return make
