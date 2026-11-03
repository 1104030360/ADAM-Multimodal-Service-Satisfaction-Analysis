# tests/integration/conftest.py — 整合測試的環境：
#   真的 MySQL 8.4（docker 網路上的拋棄式容器，載入 myapp/tests/fixtures/schema.sql 與 grants.sql）、moto 模擬的 S3 與 SQS
#   （主佇列＋DLQ，maxReceiveCount 3，和 Console 建的一樣）、Flask 的 test client。
#   服務用最小權限的帳號 adam_analysis 連資料庫；測試自己用 root 準備資料、檢查結果。
#   API（app.py）和 worker（worker.py）都在測試的 process 裡：送出器不開執行緒（OUTBOX_PUBLISHER=off）、worker 不跑主迴圈，
#   測試用 drain() 一步一步推：app.publisher.run_once()（outbox → SQS）→ Worker.poll_once()（SQS → 分析 → 提交）。
#   不載入模型（pyannote、Whisper、翻譯模型等）：分析這一步換成 FakeAnalysis。
import copy
import dataclasses
import json
import os
import threading
import time
from contextlib import closing
from pathlib import Path

import mysql.connector
import pytest
from moto import mock_aws  # 要在 import app 之前 import，moto 才攔得到 app、worker 建立的 S3、SQS client

import analysis_queue
import analyzer
import predict_0
import report_helpers
import report_model

# 和 grants.sql、docker run 的指令一致；DB_HOST 一定要從外面給
os.environ.setdefault("DB_NAME", "adam")
os.environ.setdefault("DB_USER", "adam_analysis")
os.environ.setdefault("DB_PASSWORD", "adam-test-only")
os.environ.setdefault("DB_ROOT_PASSWORD", "adam-test-root")
os.environ.setdefault("S3_BUCKET", "adam-test-media")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")  # moto 不檢查金鑰，但 boto3 一定要有
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
# SQS：moto 的帳號（123456789012）和網址的格式是固定的，import app 之前就能設好（use_test_queue()）
QUEUE_NAME, DLQ_NAME = "adam-analysis-test", "adam-analysis-test-dlq"
QUEUE_URL = f"https://sqs.{os.environ['AWS_REGION']}.amazonaws.com/123456789012/{QUEUE_NAME}"  # 區域：tests/conftest.py

STAFF_ACCOUNT = "00000000-0000-0000-0000-000000000002"  # 測試用 schema（myapp/tests/fixtures/schema.sql）的種子帳號
WORKER_ID = "pytest-worker"


class Db:
    """測試自己用的資料庫連線（root）：準備資料、讀出結果。"""

    def __init__(self, conn):
        self.conn = conn

    def query(self, sql, *args):
        with closing(self.conn.cursor()) as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.with_rows else None

    def add_service(self, sid, status):
        # 和桌面端一樣用預存程序建立；最後一個是 OUT 參數
        self.query("CALL sp_insert_service(%s, %s, NOW(), NOW(), %s, @n)", sid, STAFF_ACCOUNT, status)

    def add_media(self, sid, name, url=None):
        """登記一個錄影或錄音檔的位置。url 預設是 s3://<bucket>/video 或 audio/<檔名>。"""
        folder, procedure = ("audio", "sp_insert_audio_storage") if name.endswith(".wav") \
            else ("video", "sp_insert_video_storage")
        if url is None:
            url = f"s3://{os.environ['S3_BUCKET']}/{folder}/{name}"
        self.query(f"CALL {procedure}(%s, %s, %s, NOW())", sid, name, url)

    def set_status(self, sid, status):
        self.query("UPDATE tbl_service SET service_status=%s WHERE service_id=%s", status, sid)

    def status(self, sid):
        return self.query("SELECT service_status FROM tbl_service WHERE service_id=%s", sid)[0][0]

    def scores(self, sid):
        cols = ["customer_text", "customer_audio", "customer_facial", "customer_total",
                "server_text", "server_audio", "server_facial", "server_total"]
        rows = self.query(f"SELECT {', '.join(cols)} FROM tbl_service_score WHERE service_id=%s", sid)
        return dict(zip(cols, rows[0])) if rows else None

    def charts(self, sid):
        return self.query("SELECT charts_name, charts_url FROM tbl_charts_storage "
                          "WHERE service_id=%s ORDER BY charts_id", sid)

    def texts(self, sid):
        return self.query("SELECT text_name, text_url FROM tbl_text_storage WHERE service_id=%s", sid)

    def job(self, sid):
        """分析工作（tbl_analysis_job）那一列；沒有是 None。"""
        cols = ["job_state", "attempts", "max_attempts", "lease_token", "result_token", "report_data_key", "last_error"]
        rows = self.query(f"SELECT {', '.join(cols)} FROM tbl_analysis_job WHERE service_id=%s", sid)
        return dict(zip(cols, rows[0])) if rows else None

    def outbox(self, sid):
        """這筆服務的 outbox（還要、已經送到 SQS 的紀錄），舊的在前。"""
        cols = ["outbox_state", "send_attempts", "sqs_message_id"]
        rows = self.query(f"SELECT {', '.join(cols)} FROM tbl_analysis_outbox WHERE service_id=%s "
                          "ORDER BY created_at, event_id", sid)
        return [dict(zip(cols, row)) for row in rows]

    def workers(self):
        """worker 的心跳（tbl_analysis_worker）：{worker_id: (狀態, 在做哪一筆, 說明)}。"""
        rows = self.query("SELECT worker_id, worker_state, service_id, detail FROM tbl_analysis_worker")
        return {worker_id: (state, sid, detail) for worker_id, state, sid, detail in rows}


class Queues:
    """moto 的主佇列與 DLQ。count() 是（等著被領, 領走了還沒刪）的訊息數。"""

    def __init__(self, sqs, dlq_url):
        self.sqs, self.url, self.dlq_url = sqs, QUEUE_URL, dlq_url

    def count(self, url=None):
        attributes = self.sqs.get_queue_attributes(
            QueueUrl=url or self.url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return int(attributes["ApproximateNumberOfMessages"]), int(attributes["ApproximateNumberOfMessagesNotVisible"])

    def dead(self):
        """DLQ 裡有幾則（等著被領的）。"""
        return self.count(self.dlq_url)[0]

    def receive(self):
        """像 worker 一樣領一則（測試用來看訊息的內容）；沒有是 None。"""
        messages = self.sqs.receive_message(QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=0,
                                            MessageSystemAttributeNames=["ApproximateReceiveCount"])
        return (messages.get("Messages") or [None])[0]


def use_test_queue(mp):
    """SQS 的設定，一律蓋掉外面的（測試只用 moto）：SQS_ANALYSIS_QUEUE_URL 指向 moto 的佇列；送出器不開執行緒
    （OUTBOX_PUBLISHER=off，測試自己呼叫 app.publisher.run_once()）；ANALYSIS_* 用程式的預設值（lease 300 秒、最多 3 次…）。
    用 monkeypatch 設，用完就還原：其他資料夾的測試（tests/queue/ 等）看不到。"""
    mp.setenv("SQS_ANALYSIS_QUEUE_URL", QUEUE_URL)
    mp.setenv("OUTBOX_PUBLISHER", "off")
    for name in analysis_queue.Settings.ENV.values():
        mp.delenv(name, raising=False)


def connect_as_root(timeout=60):
    """MySQL 容器第一次啟動時要先跑 schema、grants.sql（幾秒到二十幾秒），這段時間連不上就重試。"""
    deadline = time.time() + timeout
    while True:
        try:
            return mysql.connector.connect(
                host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")), user="root",
                password=os.environ["DB_ROOT_PASSWORD"], database=os.environ["DB_NAME"], autocommit=True)
        except mysql.connector.Error:
            if time.time() > deadline:
                raise
            time.sleep(2)


def wait_until_queue_is_done(jobs, timeout=30):
    # Queue.join() 不能設 timeout：放在另一個執行緒裡等，最多等 timeout 秒，測試才不會卡住。
    # jobs 是有 jobs（queue.Queue）的背景工作：app.report_jobs（報表）、app.outcome_jobs（服務結果）
    waiter = threading.Thread(target=jobs.jobs.join, daemon=True)
    waiter.start()
    waiter.join(timeout)
    assert not waiter.is_alive(), f"背景工作 {timeout} 秒內沒有做完"


def make_worker(worker_module, settings):
    """一個 worker（analysis_queue.Worker，處理函式是 worker.py 的 process）：模型不載入、主迴圈不跑，測試自己 poll_once()。
    失敗、還不能做的訊息馬上可以再領（retry_delay_sec=0），drain() 才能一次走完三次嘗試。"""
    return analysis_queue.Worker(worker_module.pool, analysis_queue.make_sqs_client(),
                                 dataclasses.replace(settings, retry_delay_sec=0), worker_module.process,
                                 worker_id=WORKER_ID)


def drain_queue(service, queue_worker, limit=20):
    """outbox 送到 SQS、worker 一則一則領，直到兩邊都沒有事。回傳處理了幾則訊息。
    失敗的訊息馬上又領得到：三次都失敗的那一則，第四次領的時候 SQS（moto）把它移到 DLQ，迴圈就停了。"""
    handled = 0
    for _ in range(limit):
        sent = service.publisher.run_once()
        received = queue_worker.poll_once(wait_sec=0)
        handled += received
        if not sent and not received:
            return handled
    raise AssertionError(f"佇列 {limit} 輪之後還沒清空")


def clean(db):
    report_helpers.clean(db)  # 先刪報表（外鍵 RESTRICT），再刪服務（工作、outbox 和其他資料表都設了 ON DELETE CASCADE）
    db.query("DELETE FROM tbl_analysis_worker")  # worker 的心跳沒有外鍵


def start_thread(name, target, *args):
    """背景執行緒；名字以 analysis- 開頭的，每個測試結束前都會等它做完。"""
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread.start()
    return thread


@pytest.fixture(scope="session")
def db():
    if "DB_HOST" not in os.environ:
        pytest.fail("整合測試需要 MySQL：先啟動 MySQL 容器，再用 -e DB_HOST=<容器名稱> 執行")
    conn = connect_as_root()
    yield Db(conn)
    conn.close()


@pytest.fixture(scope="session")
def worker_module(service):
    """worker.py（service 已經匯入）。"""
    import worker
    return worker


@pytest.fixture(scope="session")
def service(db):
    """匯入 app.py（API）與 worker.py。匯入時就會連資料庫、啟動報表的 worker，所以等真的要測才匯入。

    服務結果（PLAN1 W1）現在在 worker.py 排入：共用的測試（test_outcome_jobs.py）用 service.outcome_jobs，掛同一個物件。
    """
    clean(db)  # 上一次執行留下的 generating 報表，會被 app 啟動時的 recover() 排進佇列
    with pytest.MonkeyPatch.context() as mp:
        use_test_queue(mp)  # app.settings 是匯入時讀的
        import app
        import worker
    worker.outcome_jobs.start()  # worker.py 的 main() 做的事；模型不載入、主迴圈不跑
    app.outcome_jobs = worker.outcome_jobs
    return app


@pytest.fixture(autouse=True)
def fresh_state(service, db, monkeypatch):
    """每個測試都從空的資料庫、空的 S3 與 SQS 開始。結束前等背景工作做完，再清掉這個測試留下的資料（例如還在排隊的工作、
    沒送出的 outbox、worker 的心跳），才不會影響下一個測試和之後別的資料夾的測試（tests/queue/）。回傳 Queues。"""
    use_test_queue(monkeypatch)  # 測試裡才讀設定的（例如 worker.py 的 main() → serve()）也用 moto 的佇列
    clean(db)
    with mock_aws():
        service.s3.create_bucket(Bucket=service.S3_BUCKET)
        sqs = analysis_queue.make_sqs_client()
        dlq = sqs.create_queue(QueueName=DLQ_NAME)["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        url = sqs.create_queue(QueueName=QUEUE_NAME, Attributes={  # 和 Console 建的主佇列相同（教學第 3 節）
            "VisibilityTimeout": "900", "ReceiveMessageWaitTimeSeconds": "20", "MessageRetentionPeriod": "345600",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"})})["QueueUrl"]
        assert url == QUEUE_URL
        yield Queues(sqs, dlq)
        report_helpers.wait_for_threads("analysis-")  # 測試在背景跑的分析
        wait_until_queue_is_done(service.report_jobs)
        wait_until_queue_is_done(service.outcome_jobs)  # PLAN1 W1 的服務結果
    clean(db)


@pytest.fixture
def queues(fresh_state):
    return fresh_state


@pytest.fixture
def api(service):
    return service.app.test_client()


@pytest.fixture
def worker(service, worker_module, fresh_state):
    """測試用的 worker（make_worker）：每個測試一個新的。"""
    return make_worker(worker_module, service.settings)


@pytest.fixture
def drain(service, worker):
    """把 outbox、SQS 都清空（drain_queue）。回傳處理了幾則訊息。"""
    return lambda limit=20: drain_queue(service, worker, limit)


@pytest.fixture
def background():
    """在背景執行緒跑（例如 worker 停在分析這一步時打 API）；測試結束前會等它做完。"""
    return lambda target, *args: start_thread(f"analysis-{getattr(target, '__name__', 'job')}", target, *args)


@pytest.fixture
def wait_for_reports(service):
    """等報表的佇列做完（每一份都已經是 ready、failed，或超時後由 worker 標成 failed）。"""
    return lambda timeout=30: wait_until_queue_is_done(service.report_jobs, timeout)


@pytest.fixture
def wait_for_outcomes(service):
    """等服務結果（PLAN1 W1）的佇列做完。"""
    return lambda timeout=30: wait_until_queue_is_done(service.outcome_jobs, timeout)


class FakeAnalysis:
    """取代 analyzer.process_service：不跑模型。

    記下每次收到的參數與檔案內容，在暫存資料夾寫出 5 張圖（依原本的固定順序）和逐字稿，回傳固定的分數。
    分數的十位數是角色（7 顧客、8 服務人員），個位數是模態（1 文字、2 聲音、3 人臉）。
    """

    CHARTS = ["emotion_TEXT", "emotion_AUDIO", "emotion_wave_face", "customer_emotions_face", "staff_emotions_face"]

    def __init__(self):
        self.calls = []
        self.scores = {"text": {"customer": 71, "staff": 81},
                       "audio": {"customer": 72, "staff": 82},
                       "face": {"customer": 73, "staff": 83}}
        self.started = threading.Event()  # 已經有工作開始分析
        self.release = threading.Event()  # 清掉它，工作就會停在分析這一步
        self.release.set()
        self.transcript_text = "Staff: 您好\nTranslated: Hello\n"  # 逐字稿的內容；None：這次沒有逐字稿（PLAN1 W1）
        self.with_report_data = True  # False：這次沒有報表要畫的數字
        self.error = None  # 設成一個例外：分析這一步丟出它（例如模型算到一半失敗）

    def charts(self, sid):
        return [f"{sid}_{name}.png" for name in self.CHARTS]

    def __call__(self, sid, media, work):
        files = {kind: Path(path).read_bytes() for kind, path in media.items()}
        self.calls.append({"sid": sid, "media": media, "files": files, "work": work})
        self.started.set()
        self.release.wait(10)
        if self.error is not None:
            raise self.error
        for name in self.charts(sid):
            Path(work, name).write_bytes(b"\x89PNG fake chart")
        transcript = None
        if self.transcript_text is not None:
            transcript = f"{sid}_transcript.txt"
            Path(work, transcript).write_text(self.transcript_text, encoding="utf-8")
        report_data = report_model.build_report_data(
            sid, source="test", audio={"staff": {"positive": 0.5, "neutral": 0.3, "negative": 0.2}},
            text=None, facial={"customer": [(0, 1), (1, 0)]}) if self.with_report_data else None
        return {**copy.deepcopy(self.scores), "charts": self.charts(sid), "transcript": transcript,
                "report_data": report_data}


@pytest.fixture
def fake_analysis(monkeypatch):
    fake = FakeAnalysis()
    monkeypatch.setattr(analyzer, "process_service", fake)
    monkeypatch.setattr(predict_0, "customer_model_rf", None)  # 總分固定用平均：(71+72+73)/3、(81+82+83)/3
    monkeypatch.setattr(predict_0, "staff_model_rf", None)
    yield fake
    fake.release.set()  # 測試中途失敗時，不要讓背景工作一直停著


@pytest.fixture
def add_recording(service, db):
    """建立一段錄影：S3 上有 cam0、cam1、audio 三個檔案，資料表登記了它們的位置。回傳 {種類: 檔案內容}。"""

    def add(sid):
        files = {}
        for kind, folder, name in [("cam0", "video", f"{sid}_cam0.avi"), ("cam1", "video", f"{sid}_cam1.avi"),
                                   ("audio", "audio", f"{sid}_audio.wav")]:
            files[kind] = f"{kind} of {sid}".encode()
            service.s3.put_object(Bucket=service.S3_BUCKET, Key=f"{folder}/{name}", Body=files[kind])
            db.add_media(sid, name)
        return files

    return add


@pytest.fixture
def deny_procedure(db):
    """暫時拿掉分析服務帳號對某個預存程序的 EXECUTE 權限；測試結束後還原。"""
    denied = []
    user = f"'{os.environ['DB_USER']}'@'%'"

    def deny(name):
        db.query(f"REVOKE EXECUTE ON PROCEDURE adam.{name} FROM {user}")
        denied.append(name)

    yield deny
    for name in denied:
        db.query(f"GRANT EXECUTE ON PROCEDURE adam.{name} TO {user}")
