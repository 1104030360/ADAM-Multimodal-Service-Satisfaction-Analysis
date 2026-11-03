# tests/integration/conftest.py — 第 0 階段假服務的整合測試環境（和 analyzer/ 的 tests/integration/ 同一套做法）：
#   真的 MySQL 8.4（docker 網路上的拋棄式容器，載入 myapp/tests/fixtures/schema.sql 與 grants.sql）、moto 模擬的 S3 與 SQS、Flask 的 test client。
#   服務用最小權限的帳號 adam_analysis 連資料庫；測試自己用 root 準備資料、檢查結果。
# SQS（T49，docs/rebuild/sqs/PLAN.md 第 6 節）：API（app.py）和 worker（worker.py）都在這個測試行程裡。
#   送出器不開執行緒（OUTBOX_PUBLISHER=off），worker 也不跑主迴圈：drain() 自己送 outbox、讓 worker 一則一則做，
#   每一步都是同步的，測試看得到中間的狀態。佇列（主佇列＋DLQ，maxReceiveCount 3）每個測試在 mock_aws() 裡重建。
#   佇列本身（認領、續期、重送、DLQ、管理指令）的測試在共用的 tests/unit/test_analysis_queue.py、tests/queue/。
import json
import os
import threading
import time
from contextlib import closing
from dataclasses import dataclass, replace

import mysql.connector
import pytest
from moto import mock_aws  # 要在 import app、worker 之前 import，moto 才攔得到它們建立的 S3、SQS client

import analysis_queue
import report_helpers

# 和 grants.sql、docker run 的指令一致；DB_HOST 一定要從外面給
os.environ.setdefault("DB_NAME", "adam")
os.environ.setdefault("DB_USER", "adam_analysis")
os.environ.setdefault("DB_PASSWORD", "adam-test-only")
os.environ.setdefault("DB_ROOT_PASSWORD", "adam-test-root")
os.environ.setdefault("S3_BUCKET", "adam-test-media")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")  # moto 不檢查金鑰，但 boto3 一定要有
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
# 佇列的網址是 moto 固定的帳號與格式，所以 import app 之前就能設好。一定蓋掉：測試只用 moto 的佇列
os.environ["SQS_ANALYSIS_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/123456789012/adam-analysis"
os.environ["OUTBOX_PUBLISHER"] = "off"  # 送出器不開執行緒：drain() 自己呼叫 app.publisher.run_once()
for _name in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL_SQS"):
    os.environ.pop(_name, None)  # 本機開發指向 moto server 的網址：mock_aws() 攔不到，測試會真的連出去


class Db:
    """測試自己用的資料庫連線（root）：準備資料、讀出結果。"""

    def __init__(self, conn):
        self.conn = conn

    def query(self, sql, *args):
        with closing(self.conn.cursor()) as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.with_rows else None

    def status(self, sid):
        return self.query("SELECT service_status FROM tbl_service WHERE service_id=%s", sid)[0][0]


@dataclass
class Queues:
    """這個測試的 moto 佇列：主佇列與 DLQ 的網址。"""

    sqs: object
    main: str
    dlq: str

    def count(self, url):
        """佇列裡有幾則訊息：看得到的＋正在處理（隱藏）的。"""
        values = self.sqs.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return int(values["ApproximateNumberOfMessages"]) + int(values["ApproximateNumberOfMessagesNotVisible"])


def create_queues(sqs, url):
    """照教學第 3 節的 Console 設定：先建 DLQ（保留 14 天），再建主佇列（隱藏 15 分鐘、long polling 20 秒、
    保留 4 天、接收 3 次之後進 DLQ）。名字取自 app 的 SQS_ANALYSIS_QUEUE_URL。"""
    name = url.rsplit("/", 1)[1]
    dlq = sqs.create_queue(QueueName=f"{name}-dlq", Attributes={"MessageRetentionPeriod": "1209600"})["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    main = sqs.create_queue(QueueName=name, Attributes={
        "VisibilityTimeout": "900", "ReceiveMessageWaitTimeSeconds": "20", "MessageRetentionPeriod": "345600",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": arn, "maxReceiveCount": 3})})["QueueUrl"]
    assert main == url, f"moto 建立的佇列是 {main}，app 設定的是 {url}"
    return Queues(sqs, main, dlq)


class RecordingWorker(analysis_queue.Worker):
    """analysis_queue.Worker，另外記下每一則訊息 handle() 的結果（done、retry、failed、lost、busy、duplicate…）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.results = []

    def handle(self, message):
        result = super().handle(message)
        self.results.append(result)
        return result


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
    # Queue.join() 不能設 timeout：放在另一個執行緒裡等，最多等 timeout 秒，測試才不會卡住
    waiter = threading.Thread(target=jobs.join, daemon=True)
    waiter.start()
    waiter.join(timeout)
    assert not waiter.is_alive(), f"背景工作 {timeout} 秒內沒有做完"


@pytest.fixture(scope="session")
def db():
    if "DB_HOST" not in os.environ:
        pytest.fail("整合測試需要 MySQL：先啟動 MySQL 容器，再用 -e DB_HOST=<容器名稱> 執行")
    conn = connect_as_root()
    yield Db(conn)
    conn.close()


@pytest.fixture(scope="session")
def sqs():
    return analysis_queue.make_sqs_client()  # 建立時不連線；每個測試的 mock_aws() 裡才真的呼叫


@pytest.fixture(scope="session")
def service(db, sqs):
    """匯入 app.py（API）與 worker.py（假分析）。匯入時就會連資料庫、啟動報表的 worker，所以等真的要測才匯入。

    共用的測試只認得 service（app 模組）：服務結果（W1）現在在 worker 裡排入，worker 的 outcome_jobs 也掛在它上面。
    """
    report_helpers.clean(db)  # 上一次執行留下的 generating 報表，會被 app 啟動時的 report_jobs.recover() 排進佇列
    import app
    import worker
    worker.outcome_jobs.start()  # python worker.py 才會開這條；測試行程自己開
    app.outcome_jobs = worker.outcome_jobs
    return app


@pytest.fixture(scope="session")
def worker_module(service):
    """worker.py（假分析：process()、write_results()、FAKE_TRANSCRIPT、它自己的 s3、pool、outcome_jobs）。"""
    import worker
    return worker


@pytest.fixture(autouse=True)
def fresh_state(service, db, sqs):
    """每個測試都從空的資料庫、空的 S3 與 SQS 開始；結束前等背景工作做完，才不會影響下一個測試。"""
    report_helpers.clean(db)  # 服務刪掉時，分析工作與 outbox 跟著刪（外鍵 CASCADE）
    db.query("DELETE FROM tbl_analysis_worker")
    with mock_aws():
        service.s3.create_bucket(Bucket=service.S3_BUCKET)
        yield create_queues(sqs, service.settings.queue_url)
        wait_until_queue_is_done(service.report_jobs.jobs)
        wait_until_queue_is_done(service.outcome_jobs.jobs)  # PLAN1 W1 的服務結果


@pytest.fixture
def queues(fresh_state):
    """這個測試的主佇列與 DLQ（Queues）。"""
    return fresh_state


@pytest.fixture
def api(service):
    return service.app.test_client()


@pytest.fixture
def worker(service, worker_module, sqs):
    """領工作的 worker（不跑主迴圈，drain() 一次領一則）。失敗的訊息馬上可以再領（retry_delay_sec=0），
    drain() 一次就走完這一輪的重試；其他設定和 app 相同（同一個 Queue URL、lease、次數）。"""
    settings = replace(service.settings, retry_delay_sec=0)
    return RecordingWorker(worker_module.pool, sqs, settings, worker_module.process, worker_id="pytest-worker")


@pytest.fixture
def drain(service, worker):
    """送出 outbox → worker 領一則做一則，直到沒有到期的 outbox、佇列裡也沒有看得到的訊息
    （失敗的馬上再領；最後一次失敗之後的那次接收，moto 把訊息移到 DLQ）。回傳這一次每一則訊息的結果。"""

    def run(rounds=20):
        start = len(worker.results)
        for _ in range(rounds):
            sent = service.publisher.run_once()
            handled = worker.poll_once(wait_sec=0)
            if not sent and not handled:
                return worker.results[start:]
        pytest.fail(f"佇列 {rounds} 輪之後還沒做完：{worker.results[start:]}")

    return run


@pytest.fixture
def wait_for_reports(service):
    """等報表的佇列做完（每一份都已經是 ready、failed，或超時後由 worker 標成 failed）。"""
    return lambda timeout=30: wait_until_queue_is_done(service.report_jobs.jobs, timeout)


@pytest.fixture
def wait_for_outcomes(service):
    """等服務結果（PLAN1 W1）的佇列做完。"""
    return lambda timeout=30: wait_until_queue_is_done(service.outcome_jobs.jobs, timeout)
