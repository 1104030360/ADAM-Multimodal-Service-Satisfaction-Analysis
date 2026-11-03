# tests/unit/test_analysis_queue.py — analysis_queue.py 不需要資料庫的部分（SQS 分析工作佇列，T49）。
# analysis_service/ 與 analyzer/ 共用同一份，必須逐位元組相同（tools/check_report_copies.py）。
#
# 設定與檢查、檔名、訊息內容、退避、Worker 對每一種認領結果的處理、heartbeat、關機、暫存資料夾、serve()、管理指令的參數、
# check-db（讀 grants.sql、每一項怎麼試、MySQL 的錯誤碼怎麼分、輸出與結束碼）。
# 資料庫與 SQS 都換成記錄呼叫的小替身；真的 MySQL 8.4＋moto 的流程在 tests/queue/。
import json
import logging
import os
import re
import runpy
import signal
import socket
import sys
import tempfile
import threading
import time
from dataclasses import fields, replace

import pytest

import analysis_queue as aq

URL = "https://sqs.us-east-1.amazonaws.com/123456789012/adam-analysis"
SID = "S20261102093000"
TOKEN = "0123456789abcdef0123456789abcdef"
SETTINGS = aq.Settings(queue_url=URL, wait_sec=0)
DEFAULTS = {"lease_sec": 300, "heartbeat_sec": 60, "visibility_sec": 900, "wait_sec": 20, "max_attempts": 3,
            "retry_delay_sec": 60, "job_timeout_sec": 7200, "publish_interval_sec": 5.0, "stale_job_sec": 3600,
            "worker_stale_sec": 120}
ENVIRONMENT = ["SQS_ANALYSIS_QUEUE_URL", *aq.Settings.ENV.values()]


class FakeCursor:
    def __init__(self, db):
        self.db, self.rowcount = db, -1

    def execute(self, sql, args=()):
        self.db.executed.append((sql, args))
        self.rowcount = self.db.rowcounts.pop(0) if self.db.rowcounts else 1

    def fetchall(self):
        return self.db.results.pop(0) if self.db.results else []

    def close(self):
        self.db.closed.append("cursor")


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1

    def close(self):
        self.db.closed.append("connection")


class FakeDb:
    """很小的 pool 替身：記下執行的 SQL 與參數；fetchall() 依序回傳 results、rowcount 依序用 rowcounts。
    unavailable=True：連不上（get_connection() 丟出 ConnectionError）。"""

    def __init__(self, results=(), rowcounts=(), unavailable=False):
        self.results, self.rowcounts, self.unavailable = list(results), list(rowcounts), unavailable
        self.executed, self.closed, self.commits, self.rollbacks = [], [], 0, 0

    def get_connection(self):
        if self.unavailable:
            raise ConnectionError("database is down")
        return FakeConnection(self)

    def reports(self):
        """Worker.report() 寫過的 (狀態, detail)。"""
        return [(args[1], args[3]) for sql, args in self.executed if sql == aq.REPORT_SQL]

    def states(self):
        return [state for state, _ in self.reports()]


class FakeSqs:
    """SQS client 的替身：記下每次呼叫。received 是下一次 receive_message 回傳的訊息；fail[動作] 丟出指定的例外；
    on_receive 在每次 receive_message 時先執行（例如送出 SIGTERM）；listener 收到每一個動作的名字。"""

    def __init__(self):
        self.calls, self.fail, self.received, self.attributes = [], {}, [], {}
        self.on_receive = self.listener = None

    def _call(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.listener is not None:
            self.listener(name)
        if name in self.fail:
            raise self.fail[name]

    def called(self, name):
        return [kwargs for called, kwargs in self.calls if called == name]

    def receive_message(self, **kwargs):
        if self.on_receive is not None:
            self.on_receive()
        self._call("receive_message", kwargs)
        return {"Messages": [self.received.pop(0)]} if self.received else {}

    def change_message_visibility(self, **kwargs):
        self._call("change_message_visibility", kwargs)

    def delete_message(self, **kwargs):
        self._call("delete_message", kwargs)

    def get_queue_attributes(self, **kwargs):
        self._call("get_queue_attributes", kwargs)
        return {"Attributes": dict(self.attributes)}


class Analysis:
    """各服務 process(job) 的替身：記下 Job（和當時 workdir 在不在），回傳 Prepared，或丟出 error。"""

    def __init__(self, error=None, after_commit=None):
        self.jobs, self.workdirs, self.error, self.after_commit = [], [], error, after_commit

    def __call__(self, job):
        self.jobs.append(job)
        self.workdirs.append(os.path.isdir(job.workdir))
        if self.error is not None:
            raise self.error
        return aq.Prepared(write=lambda cur: None, report_data_key=aq.report_data_key(job.service_id, job.token),
                           after_commit=self.after_commit)


def new_worker(process=None, pool=None, sqs=None, settings=SETTINGS):
    """Worker 接上替身；exit_process 只記下結束碼（worker.exits）。"""
    exits = []
    worker = aq.Worker(pool or FakeDb(), sqs or FakeSqs(), settings, process or Analysis(), worker_id="unit-worker",
                       exit_process=exits.append)
    worker.exits = exits
    return worker


def message(body=None, count="1", receipt="receipt-1"):
    """SQS 收到的一則訊息；count=None 是沒有 Attributes 的訊息。"""
    sent = {"MessageId": "m-1", "ReceiptHandle": receipt,
            "Body": json.dumps({"service_id": SID}) if body is None else body}
    if count is not None:
        sent["Attributes"] = {"ApproximateReceiveCount": count}
    return sent


def visibility(seconds, receipt="receipt-1"):
    return "change_message_visibility", {"QueueUrl": URL, "ReceiptHandle": receipt, "VisibilityTimeout": seconds}


def claims(monkeypatch, *answers):
    """claim() 依序回傳 answers（例外就丟出），並記下收到的 (service_id, token)。"""
    calls, answers = [], list(answers)

    def fake_claim(pool, service_id, token, settings):
        calls.append((service_id, token))
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(aq, "claim", fake_claim)
    return calls


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"{timeout} 秒內沒有等到"
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """暫存資料夾、ALIVE_FILE 放在 tmp_path；設定的環境變數一律清掉（測試自己設）。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(aq, "ALIVE_FILE", str(tmp_path / "adam-worker-alive"))
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def restore_signals():
    """serve() 會換掉 SIGTERM、SIGINT 的處理；測試結束時換回來。"""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


# ---------- 設定 ----------
def test_settings_default_to_the_plan_s_starting_values():
    settings = aq.Settings.from_env({})
    assert settings.queue_url == ""
    assert {name: getattr(settings, name) for name in DEFAULTS} == DEFAULTS
    assert type(settings.publish_interval_sec) is float and type(settings.lease_sec) is int
    assert set(aq.Settings.ENV) == {field.name for field in fields(aq.Settings)} - {"queue_url"}  # 每一個都能設


def test_settings_read_every_variable_and_strip_spaces():
    environ = {"SQS_ANALYSIS_QUEUE_URL": f"  {URL}\n", "ANALYSIS_LEASE_SEC": " 600 ", "ANALYSIS_HEARTBEAT_SEC": "30",
               "ANALYSIS_VISIBILITY_SEC": "1200", "ANALYSIS_WAIT_SEC": "10", "ANALYSIS_MAX_ATTEMPTS": "5",
               "ANALYSIS_RETRY_DELAY_SEC": "0", "ANALYSIS_JOB_TIMEOUT_SEC": "3600",
               "ANALYSIS_PUBLISH_INTERVAL_SEC": "0.5", "ANALYSIS_STALE_JOB_SEC": "1800",
               "ANALYSIS_WORKER_STALE_SEC": "90"}
    assert aq.Settings.from_env(environ) == aq.Settings(
        queue_url=URL, lease_sec=600, heartbeat_sec=30, visibility_sec=1200, wait_sec=10, max_attempts=5,
        retry_delay_sec=0, job_timeout_sec=3600, publish_interval_sec=0.5, stale_job_sec=1800, worker_stale_sec=90)


def test_blank_variables_keep_the_defaults():
    assert aq.Settings.from_env({"ANALYSIS_LEASE_SEC": "", "ANALYSIS_WAIT_SEC": "   "}) == aq.Settings()


def test_from_env_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("SQS_ANALYSIS_QUEUE_URL", URL)
    monkeypatch.setenv("ANALYSIS_MAX_ATTEMPTS", "4")
    assert aq.Settings.from_env() == aq.Settings(queue_url=URL, max_attempts=4)


@pytest.mark.parametrize("variable, raw", [("ANALYSIS_LEASE_SEC", "five"), ("ANALYSIS_MAX_ATTEMPTS", "2.5"),
                                           ("ANALYSIS_WAIT_SEC", "20s"), ("ANALYSIS_PUBLISH_INTERVAL_SEC", "fast")])
def test_settings_that_are_not_numbers_are_rejected(variable, raw):
    with pytest.raises(ValueError) as error:
        aq.Settings.from_env({variable: raw})
    assert str(error.value) == f"{variable} must be a number, got {raw!r}"
    assert error.value.__suppress_context__  # from None：不帶 int()、float() 原本的錯誤


HEARTBEAT = "ANALYSIS_HEARTBEAT_SEC must be above 0 and below ANALYSIS_LEASE_SEC"
VISIBILITY = "ANALYSIS_VISIBILITY_SEC must be between ANALYSIS_LEASE_SEC and 43200 (12 hours)"
WAIT = "ANALYSIS_WAIT_SEC must be between 0 and 20"
ATTEMPTS = "ANALYSIS_MAX_ATTEMPTS must be between 1 and 100"
RETRY = "ANALYSIS_RETRY_DELAY_SEC must be between 0 and ANALYSIS_VISIBILITY_SEC"
TIMEOUT = "ANALYSIS_JOB_TIMEOUT_SEC must be above 0 and leave room for one visibility timeout in 12 hours"
POSITIVE = "ANALYSIS_PUBLISH_INTERVAL_SEC, ANALYSIS_STALE_JOB_SEC and ANALYSIS_WORKER_STALE_SEC must be above 0"


@pytest.mark.parametrize("changes, problems", [
    ({"heartbeat_sec": 0}, [HEARTBEAT]),
    ({"heartbeat_sec": 300}, [HEARTBEAT]),  # 要比 lease 短
    ({"visibility_sec": 299}, [VISIBILITY]),  # 要比 lease 長：worker 死掉、訊息再出現時，lease 已經到期
    ({"visibility_sec": 43201}, [VISIBILITY, TIMEOUT]),
    ({"wait_sec": -1}, [WAIT]), ({"wait_sec": 21}, [WAIT]),
    ({"max_attempts": 0}, [ATTEMPTS]), ({"max_attempts": 101}, [ATTEMPTS]),
    ({"retry_delay_sec": -1}, [RETRY]), ({"retry_delay_sec": 901}, [RETRY]),
    ({"job_timeout_sec": 0}, [TIMEOUT]), ({"job_timeout_sec": 43200 - 900 + 1}, [TIMEOUT]),
    ({"publish_interval_sec": 0.0}, [POSITIVE]), ({"stale_job_sec": 0}, [POSITIVE]), ({"worker_stale_sec": 0}, [POSITIVE]),
    # NaN 和什麼比都是 False，inf 讓 Event.wait() 丟出 OverflowError：送出器會空轉或停掉
    ({"publish_interval_sec": float("nan")}, [POSITIVE]), ({"publish_interval_sec": float("inf")}, [POSITIVE]),
    ({"publish_interval_sec": 1e300}, [POSITIVE]),
    ({"heartbeat_sec": 0, "wait_sec": 30, "max_attempts": 0, "stale_job_sec": 0}, [HEARTBEAT, WAIT, ATTEMPTS, POSITIVE]),
])
def test_each_rule_is_checked_and_every_problem_is_reported(changes, problems):
    with pytest.raises(ValueError) as error:
        replace(aq.Settings(), **changes).validate()
    assert str(error.value) == "; ".join(problems)


@pytest.mark.parametrize("changes", [
    {"heartbeat_sec": 1}, {"heartbeat_sec": 299}, {"visibility_sec": 300}, {"wait_sec": 0}, {"wait_sec": 20},
    {"max_attempts": 1}, {"max_attempts": 100}, {"retry_delay_sec": 0}, {"retry_delay_sec": 900},
    {"job_timeout_sec": 1}, {"job_timeout_sec": 43200 - 900}, {"visibility_sec": 43199, "job_timeout_sec": 1},
    {"publish_interval_sec": 0.001},
])
def test_the_edges_of_each_rule_are_allowed(changes):
    replace(aq.Settings(), **changes).validate()


@pytest.mark.parametrize("raw", ["nan", "inf", "-1"])
def test_from_env_validates_what_it_read(raw):
    with pytest.raises(ValueError) as error:
        aq.Settings.from_env({"ANALYSIS_PUBLISH_INTERVAL_SEC": raw})
    assert str(error.value) == POSITIVE


def test_the_sqs_client_waits_longer_than_long_polling(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    client = aq.make_sqs_client()  # 只建立，不連線
    assert client.meta.region_name == "ap-northeast-1"
    assert client.meta.config.read_timeout == 30 > DEFAULTS["wait_sec"]
    assert client.meta.config.connect_timeout == 3
    assert client.meta.config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert aq.make_sqs_client("eu-west-1").meta.region_name == "eu-west-1"


# ---------- 檔名、訊息 ----------
def test_tokens_are_32_hex_characters_and_never_repeat():
    tokens = {aq.new_token() for _ in range(1000)}
    assert len(tokens) == 1000 and all(re.fullmatch(r"[0-9a-f]{32}", token) for token in tokens)


@pytest.mark.parametrize("name, expected", [
    (f"{SID}_emotion_TEXT.png", f"{SID}_0123456789ab_emotion_TEXT.png"),  # 原本就以 service_id 開頭
    ("transcript.txt", f"{SID}_0123456789ab_transcript.txt"),
    ("S20261102093001_emotion_TEXT.png", f"{SID}_0123456789ab_S20261102093001_emotion_TEXT.png"),  # 別筆的不拿掉
    (SID, f"{SID}_0123456789ab_{SID}"),
])
def test_output_names_carry_this_run_s_token(name, expected):
    assert aq.run_tag(TOKEN) == "0123456789ab"
    assert aq.run_name(SID, TOKEN, name) == expected


def test_each_run_has_its_own_report_data_key():
    assert aq.report_data_key(SID, TOKEN) == f"report-data/{SID}_0123456789ab.json"
    assert aq.report_data_key(SID, "f" * 32) != aq.report_data_key(SID, TOKEN)


@pytest.mark.parametrize("error, text", [(ValueError("bad video"), "ValueError: bad video"),
                                         (RuntimeError(), "RuntimeError:"), (KeyError("cam1"), "KeyError: 'cam1'")])
def test_describe_is_the_class_and_the_message(error, text):
    assert aq.describe(error) == text


def test_describe_fits_the_last_error_column():
    text = aq.describe(ValueError("x" * 5000))
    assert len(text) == aq.ERROR_CHARS == 1000 and text.startswith("ValueError: xxx")


@pytest.mark.parametrize("body", [json.dumps({"service_id": SID}), json.dumps({"service_id": SID, "event_id": TOKEN}),
                                  f' {{"service_id":"{SID}"}}\n'])
def test_a_message_body_gives_its_service_id(body):
    assert aq.parse_body(body) == SID


@pytest.mark.parametrize("body", [
    None, "", "not json", "{", "[]", "null", "42", json.dumps(SID), json.dumps({"sid": SID}),
    json.dumps({"service_id": None}), json.dumps({"service_id": 20261102093000}), json.dumps({"service_id": [SID]}),
    json.dumps({"service_id": SID + "\n"}),  # fullmatch：結尾的換行不算
    json.dumps({"service_id": " " + SID}), json.dumps({"service_id": SID.lower()}),
    json.dumps({"service_id": SID[:-1]}), json.dumps({"service_id": SID + "0"}),
    json.dumps({"service_id": "S２０２６１１０２０９３０００"}),  # 全形數字
    json.dumps({"service_id": "S٢٠٢٦١١٠٢٠٩٣٠٠٠"}),  # 阿拉伯－印度數字
    "[" * 100000,  # 很深的巢狀：json 丟 RecursionError
])
def test_anything_else_is_not_a_service_id(body):
    assert aq.parse_body(body) is None


# ---------- 交易 ----------
def test_a_transaction_commits_when_the_block_ends():
    db = FakeDb()
    with aq.transaction(db) as cur:
        cur.execute("UPDATE t SET x = 1")
    assert (db.commits, db.rollbacks) == (1, 0) and db.closed == ["cursor", "connection"]


def test_returning_from_inside_a_transaction_also_commits():
    db = FakeDb()

    def early():
        with aq.transaction(db) as cur:
            cur.execute("SELECT 1")
            return "early"

    assert early() == "early" and (db.commits, db.rollbacks) == (1, 0)


@pytest.mark.parametrize("error", [ValueError("bad"), KeyboardInterrupt()])
def test_any_exception_rolls_back_and_propagates(error):
    db = FakeDb()
    with pytest.raises(type(error)):
        with aq.transaction(db) as cur:
            cur.execute("UPDATE t SET x = 1")
            raise error
    assert (db.commits, db.rollbacks) == (0, 1) and db.closed == ["cursor", "connection"]


@pytest.mark.parametrize("rowcount, holds, expected", [(1, None, True), (0, 1, True), (0, 0, False)])
def test_extend_asks_again_only_when_nothing_changed(rowcount, holds, expected):
    # 同一毫秒內續期兩次時 UPDATE 改了 0 列（值一樣），lease 還是自己的
    db = FakeDb(results=[] if holds is None else [[(holds,)]], rowcounts=[rowcount])
    assert aq.extend(db, SID, TOKEN, SETTINGS) is expected
    assert db.executed[0] == (aq.EXTEND_SQL, (SETTINGS.lease_sec, SID, TOKEN))
    assert [sql for sql, _ in db.executed[1:]] == ([] if holds is None else [aq.HOLDS_SQL])


# ---------- 送出器 ----------
@pytest.mark.parametrize("attempts, base", [(0, 5), (1, 5), (2, 10), (3, 20), (4, 40), (5, 80), (6, 160), (7, 300),
                                            (8, 300), (50, 300), (10_000, 300)])
def test_backoff_doubles_up_to_five_minutes_plus_up_to_20_percent(monkeypatch, attempts, base):
    monkeypatch.setattr(aq.random, "uniform", lambda low, high: low)
    assert aq.OutboxPublisher.backoff(attempts) == base
    monkeypatch.setattr(aq.random, "uniform", lambda low, high: high)
    assert aq.OutboxPublisher.backoff(attempts) == int(base * 1.2)


def test_backoff_jitter_stays_inside_its_bounds():
    for attempts in range(1, 12):
        base = min(300, 5 * 2 ** (attempts - 1))
        assert all(base <= aq.OutboxPublisher.backoff(attempts) <= base * 1.2 for _ in range(200))


def test_without_a_queue_url_the_publisher_does_not_touch_the_database():
    publisher = aq.OutboxPublisher(FakeDb(unavailable=True), FakeSqs(), replace(SETTINGS, queue_url=""))
    assert publisher.run_once() == 0


def test_retry_later_counts_one_attempt_when_the_row_is_gone(monkeypatch, caplog):
    monkeypatch.setattr(aq.random, "uniform", lambda low, high: low)
    db = FakeDb(results=[[]])  # 例如服務剛好被刪掉（CASCADE）
    aq.OutboxPublisher(db, FakeSqs(), SETTINGS).retry_later("e" * 32, ConnectionError("refused"))
    assert db.executed[-1] == (aq.RETRY_SQL, (5, "ConnectionError: refused", "e" * 32))
    assert f"outbox {'e' * 32} not sent (attempt 1), retrying in 5 s: ConnectionError: refused" in caplog.text


class Steps(threading.Event):
    """OutboxPublisher.stopping 的替身：記下每次 wait()，不真的等。"""

    def __init__(self, events):
        super().__init__()
        self.events = events

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        return self.is_set()


def test_the_loop_waits_only_after_a_batch_that_was_not_full():
    events = []
    publisher = aq.OutboxPublisher(FakeDb(), FakeSqs(), SETTINGS)
    publisher.stopping = Steps(events)
    sizes = iter([aq.OutboxPublisher.BATCH, 3, 0])

    def run_once():
        sent = next(sizes)
        events.append(("run", sent))
        if sent == 0:
            publisher.stopping.set()
        return sent

    publisher.run_once = run_once
    publisher.housekeeping = lambda: events.append("housekeeping")
    publisher.loop()
    interval = SETTINGS.publish_interval_sec
    # 滿 10 筆：還有，馬上送下一批；清理只在第一次（之後每 HOUSEKEEPING_SEC 一次）
    assert events == [("run", 10), "housekeeping", ("run", 3), ("wait", interval), ("run", 0), ("wait", interval)]


def test_housekeeping_runs_again_once_its_interval_has_passed():
    events = []
    publisher = aq.OutboxPublisher(FakeDb(), FakeSqs(), SETTINGS)
    publisher.stopping = Steps(events)
    publisher.HOUSEKEEPING_SEC = -1  # 每一輪都已經過了間隔
    runs = iter([0, 0])

    def run_once():
        sent = next(runs)
        if len([event for event in events if event == "housekeeping"]) == 1:
            publisher.stopping.set()
        return sent

    publisher.run_once = run_once
    publisher.housekeeping = lambda: events.append("housekeeping")
    publisher.loop()
    assert events.count("housekeeping") == 2


def test_the_loop_keeps_going_while_the_database_is_down(caplog):
    publisher = aq.OutboxPublisher(FakeDb(unavailable=True), FakeSqs(), replace(SETTINGS, publish_interval_sec=0.01))
    publisher.start()
    wait_until(lambda: caplog.text.count("outbox publisher failed; trying again") >= 2)
    publisher.stop(timeout=5)
    assert not publisher.thread.is_alive() and publisher.last_housekeeping is None
    assert "SQS_ANALYSIS_QUEUE_URL is not set" not in caplog.text


def test_stopping_a_publisher_that_never_started_is_harmless():
    publisher = aq.OutboxPublisher(FakeDb(), FakeSqs(), SETTINGS)
    publisher.stop(timeout=1)
    assert publisher.thread is None and publisher.stopping.is_set()


# ---------- worker：每一種認領結果 ----------
@pytest.mark.parametrize("count, shown", [("2", 2), (None, 1)])
def test_an_invalid_message_is_left_for_the_dlq(monkeypatch, caplog, count, shown):
    calls = claims(monkeypatch)
    worker = new_worker()
    assert worker.handle(message("not json", count=count)) == "invalid"
    assert calls == [] and worker.sqs.calls == [visibility(60)]  # 不當作成功刪掉，retry_delay_sec 之後再出現
    assert f"message m-1 has no valid service_id (receive {shown}): left for the DLQ" in caplog.text


def test_a_stopping_worker_hands_the_message_straight_back(monkeypatch):
    calls = claims(monkeypatch)
    worker = new_worker()
    worker.stopping.set()
    assert worker.handle(message()) == "returned"
    assert calls == [] and worker.sqs.calls == [visibility(0)]


def test_a_claim_error_leaves_the_message_and_waits(monkeypatch, caplog):
    claims(monkeypatch, ConnectionError("database is down"))
    monkeypatch.setattr(aq.Worker, "DB_RETRY_SEC", 0)
    worker = new_worker()
    assert worker.handle(message()) == "error"
    assert worker.sqs.calls == []  # visibility 不動：到期之後再出現
    assert f"job {SID}: claiming failed, waiting 0 s: ConnectionError: database is down" in caplog.text


def test_a_stop_cuts_the_wait_after_a_claim_error_short(monkeypatch):
    claims(monkeypatch, ConnectionError("database is down"))
    worker = new_worker()
    stopper = threading.Timer(0.05, worker.stopping.set)
    stopper.start()
    started = time.monotonic()
    assert worker.handle(message()) == "error"
    assert time.monotonic() - started < aq.Worker.DB_RETRY_SEC / 2  # SIGTERM 不必等滿 30 秒
    stopper.join()


def test_a_claimed_job_runs_with_this_token(monkeypatch):
    calls = claims(monkeypatch, aq.Claim("claimed", attempt=2))
    worker = new_worker()
    runs = []
    monkeypatch.setattr(worker, "run_job", lambda *args: runs.append(args) or "done")
    assert worker.handle(message(count="3", receipt="r-3")) == "done"
    [(service_id, token)] = calls
    assert service_id == SID and re.fullmatch(r"[0-9a-f]{32}", token)
    assert runs == [(SID, token, 2, 3, "r-3")]


def test_a_message_for_a_done_job_is_deleted_without_another_run(monkeypatch):
    claims(monkeypatch, aq.Claim("done"))
    worker = new_worker()
    assert worker.handle(message()) == "duplicate"
    assert worker.sqs.calls == [("delete_message", {"QueueUrl": URL, "ReceiptHandle": "receipt-1"})]
    assert worker.process.jobs == []


@pytest.mark.parametrize("lease_left, seconds", [(0, 30), (-5, 30), (100, 130), (2000, 900)])
def test_a_busy_job_puts_the_message_off_until_the_lease_could_expire(monkeypatch, lease_left, seconds):
    claims(monkeypatch, aq.Claim("busy", lease_left=lease_left))
    worker = new_worker()
    assert worker.handle(message()) == "busy"
    assert worker.sqs.calls == [visibility(seconds)]  # 不刪、不算一次；最多 visibility_sec


@pytest.mark.parametrize("result, text", [("failed", f"job {SID} has failed: leaving this message for the DLQ (receive 1)"),
                                          ("missing", f"message for {SID} has no analysis job (receive 1): left for the DLQ")])
def test_failed_and_missing_jobs_leave_the_message_for_the_dlq(monkeypatch, caplog, result, text):
    claims(monkeypatch, aq.Claim(result))
    worker = new_worker()
    assert worker.handle(message()) == result
    assert worker.sqs.calls == [visibility(60)] and text in caplog.text


# ---------- worker：一筆工作 ----------
def test_a_finished_job_is_committed_then_the_after_step_then_deleted(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(aq, "commit", lambda pool, job, prepared: events.append(("commit", job, prepared)))
    analysis = Analysis(after_commit=lambda: events.append(("after_commit",)))
    worker = new_worker(analysis)
    worker.sqs.listener = lambda name: events.append((name,))
    assert worker.run_job(SID, TOKEN, 1, 2, "receipt-1") == "done"
    [job] = analysis.jobs
    assert job == aq.Job(SID, TOKEN, 1, 2, job.workdir) and analysis.workdirs == [True]
    assert os.path.dirname(job.workdir) == str(tmp_path) and not os.path.exists(job.workdir)  # 做完一定刪
    assert os.path.basename(job.workdir).startswith(f"adam-job-{SID}-0123456789ab-")
    assert [event[0] for event in events] == ["commit", "after_commit", "delete_message"]
    assert events[0][2].report_data_key == aq.report_data_key(SID, TOKEN)
    assert worker.current is None and worker.pool.states() == ["busy", "ready"]


def test_a_job_without_an_after_step_is_still_deleted(monkeypatch):
    monkeypatch.setattr(aq, "commit", lambda pool, job, prepared: None)
    worker = new_worker()
    assert worker.run_job(SID, TOKEN, 1, 1, "receipt-1") == "done"
    assert worker.sqs.called("delete_message") == [{"QueueUrl": URL, "ReceiptHandle": "receipt-1"}]


def test_a_failing_after_step_is_only_logged(monkeypatch, caplog):
    monkeypatch.setattr(aq, "commit", lambda pool, job, prepared: None)

    def after_commit():
        raise RuntimeError("outcome queue is full")

    worker = new_worker(Analysis(after_commit=after_commit))
    assert worker.run_job(SID, TOKEN, 1, 1, "receipt-1") == "done"
    assert len(worker.sqs.called("delete_message")) == 1
    assert f"job {SID}: the step after commit failed" in caplog.text


def test_a_lost_lease_discards_the_result(monkeypatch, caplog):
    def lost(pool, job, prepared):
        raise aq.LeaseLost(f"{job.service_id}: lease {aq.run_tag(job.token)} is no longer valid")

    released = []
    monkeypatch.setattr(aq, "commit", lost)
    monkeypatch.setattr(aq, "release", lambda *args, **kwargs: released.append(args))
    worker = new_worker()
    assert worker.run_job(SID, TOKEN, 1, 1, "receipt-1") == "lost"
    assert worker.sqs.calls == [] and released == []  # 不刪、不延後、不交還：已經不是自己的
    assert f"job {SID}: {SID}: lease 0123456789ab is no longer valid; result discarded" in caplog.text
    assert worker.current is None and not os.path.exists(worker.process.jobs[0].workdir)


@pytest.mark.parametrize("final, result", [("queued", "retry"), ("failed", "failed"), (None, "retry")])
def test_a_failed_analysis_gives_the_lease_back(monkeypatch, caplog, final, result):
    releases = []
    monkeypatch.setattr(aq, "release", lambda pool, service_id, token, error, counted=True:
                        releases.append((service_id, token, error, counted)) or final)
    worker = new_worker(Analysis(error=ValueError("cannot read the video")))
    assert worker.run_job(SID, TOKEN, 2, 2, "receipt-1") == result
    assert releases == [(SID, TOKEN, "ValueError: cannot read the video", True)]
    assert worker.sqs.calls == [visibility(60)]  # 不刪：retry_delay_sec 之後再出現
    assert f"job {SID} failed (attempt 2)" in caplog.text
    gave_up = f"job {SID} failed for good after its last attempt: service status is back to 1"
    assert (gave_up in caplog.text) == (final == "failed")
    assert worker.current is None and worker.pool.states() == ["busy", "ready"]


def test_an_error_while_giving_the_lease_back_is_only_logged(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise ConnectionError("database is down")

    monkeypatch.setattr(aq, "release", broken)
    worker = new_worker(Analysis(error=ValueError("x")))
    assert worker.run_job(SID, TOKEN, 1, 1, "receipt-1") == "retry"
    assert f"job {SID}: giving the lease back failed; it will expire in 300 s" in caplog.text


def test_sqs_errors_while_deleting_or_delaying_are_only_logged(caplog):
    worker = new_worker()
    worker.sqs.fail = {"delete_message": ConnectionError("x"), "change_message_visibility": ConnectionError("y")}
    worker.delete("r")
    worker.delay("r", 12.7)
    assert worker.sqs.calls[-1] == visibility(12, receipt="r")  # 秒數取整數
    assert "deleting a message failed (it will come back and be deleted then): ConnectionError: x" in caplog.text
    assert "changing a message's visibility failed: ConnectionError: y" in caplog.text


def test_poll_once_asks_for_one_message_and_its_receive_count(monkeypatch):
    worker = new_worker(settings=replace(SETTINGS, wait_sec=20))
    handled = []
    monkeypatch.setattr(worker, "handle", lambda received: handled.append(received) or "done")
    worker.sqs.received.append(message())
    assert worker.poll_once() == 1 and handled == [message()]
    assert worker.poll_once(wait_sec=0) == 0
    first, second = worker.sqs.called("receive_message")
    assert first == {"QueueUrl": URL, "MaxNumberOfMessages": 1, "WaitTimeSeconds": 20, "VisibilityTimeout": 900,
                     "MessageSystemAttributeNames": ["ApproximateReceiveCount"]}
    assert second["WaitTimeSeconds"] == 0
    assert worker.pool.states() == ["ready"]  # 沒有訊息時才報 ready


def test_run_survives_receive_errors_until_it_is_stopped(caplog):
    worker = new_worker()
    worker.sqs.fail["receive_message"] = ConnectionError("no network")
    worker.sqs.on_receive = worker.stopping.set  # 第一次就失敗，同時收到 SIGTERM：不必等 5 秒
    worker.run()
    assert "worker unit-worker: receiving from SQS failed, trying again in 5 s: ConnectionError: no network" in caplog.text
    assert worker.pool.states() == ["ready", "stopped"]


def test_report_writes_the_row_and_touches_the_alive_file():
    worker = new_worker()
    worker.report("loading", detail="x" * 300)
    assert worker.pool.executed[-1] == (aq.REPORT_SQL, ("unit-worker", "loading", None, "x" * 255))
    worker.report("busy", SID)
    assert worker.pool.executed[-1] == (aq.REPORT_SQL, ("unit-worker", "busy", SID, "x" * 255))  # detail 沿用
    with open(aq.ALIVE_FILE, encoding="ascii") as alive:
        assert alive.read() == "busy"


def test_report_failures_never_stop_the_worker(monkeypatch, tmp_path, caplog):
    worker = new_worker(pool=FakeDb(unavailable=True))
    monkeypatch.setattr(aq, "ALIVE_FILE", str(tmp_path / "missing" / "adam-worker-alive"))
    worker.report("ready")  # 不丟例外：心跳寫不進去不能讓分析失敗
    assert "worker unit-worker: reporting ready failed: ConnectionError: database is down" in caplog.text


def test_a_worker_is_named_after_its_host_and_process(monkeypatch):
    worker = aq.Worker(FakeDb(), FakeSqs(), SETTINGS, Analysis())
    assert worker.worker_id == f"{socket.gethostname()}:{os.getpid()}"[:64]
    assert worker.exit_process is os._exit and worker.current is None
    monkeypatch.setattr(aq.socket, "gethostname", lambda: "h" * 80)
    assert aq.Worker(FakeDb(), FakeSqs(), SETTINGS, Analysis()).worker_id == "h" * 64  # 欄位只有 64 字


# ---------- heartbeat ----------
class Beats:
    """Heartbeat 用到的 worker 的那幾樣（settings、pool、sqs、report）；events 依序記下每一拍做了什麼。"""

    def __init__(self, settings):
        self.settings, self.pool, self.sqs, self.events = settings, FakeDb(), FakeSqs(), []
        self.sqs.listener = self.events.append

    def report(self, state, service_id=None, detail=None):
        self.events.append(("report", state, service_id))


def renewals(monkeypatch, beats, answer=True):
    """extend() 的替身：記下一次 "extend"，回傳 answer（例外就丟出）。"""
    def fake_extend(pool, service_id, token, settings):
        beats.events.append("extend")
        assert (service_id, token, settings) == (SID, TOKEN, beats.settings)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(aq, "extend", fake_extend)


def test_each_beat_renews_the_lease_then_the_visibility_then_reports(monkeypatch):
    beats = Beats(replace(SETTINGS, heartbeat_sec=0.01))
    renewals(monkeypatch, beats)
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1").start()
    wait_until(lambda: beats.events.count(("report", "busy", SID)) >= 3)
    heartbeat.stop()
    assert not heartbeat.thread.is_alive() and not heartbeat.lost.is_set()
    assert beats.events[:3] == ["extend", "change_message_visibility", ("report", "busy", SID)]
    assert beats.sqs.calls[0] == visibility(900)  # 每次都從現在起再藏 visibility_sec 秒
    count = len(beats.events)
    time.sleep(0.05)
    assert len(beats.events) == count  # 停了就不再續期
    heartbeat.stop()  # 再停一次也沒事


def test_stopping_before_the_first_beat_renews_nothing(monkeypatch):
    beats = Beats(SETTINGS)  # 60 秒一拍
    renewals(monkeypatch, beats)
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1").start()
    started = time.monotonic()
    heartbeat.stop()
    assert time.monotonic() - started < 5 and beats.events == []


def test_a_lost_lease_stops_the_heartbeat(monkeypatch, caplog):
    beats = Beats(replace(SETTINGS, heartbeat_sec=0.01))
    renewals(monkeypatch, beats, answer=False)
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1").start()
    assert heartbeat.lost.wait(5)
    heartbeat.thread.join(5)
    assert not heartbeat.thread.is_alive() and beats.events == ["extend"]  # 不再延長 visibility，也不報 busy
    assert f"job {SID}: lease 0123456789ab lost, its result will not be committed" in caplog.text


def test_a_renewal_error_is_logged_and_the_visibility_still_extended(monkeypatch, caplog):
    beats = Beats(replace(SETTINGS, heartbeat_sec=0.01))
    renewals(monkeypatch, beats, answer=ConnectionError("database is down"))
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1").start()
    wait_until(lambda: ("report", "busy", SID) in beats.events)
    heartbeat.stop()
    assert beats.events[:3] == ["extend", "change_message_visibility", ("report", "busy", SID)]
    assert not heartbeat.lost.is_set() and f"job {SID}: renewing the lease failed; trying again" in caplog.text


def test_a_visibility_error_is_logged_and_the_worker_still_reports(monkeypatch, caplog):
    beats = Beats(replace(SETTINGS, heartbeat_sec=0.01))
    renewals(monkeypatch, beats)
    beats.sqs.fail["change_message_visibility"] = ConnectionError("SQS is down")
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1").start()
    wait_until(lambda: ("report", "busy", SID) in beats.events)
    heartbeat.stop()
    assert f"job {SID}: extending the SQS visibility failed: ConnectionError: SQS is down" in caplog.text


def test_the_heartbeat_gives_up_after_job_timeout_sec(monkeypatch, caplog):
    beats = Beats(replace(SETTINGS, heartbeat_sec=0.01, job_timeout_sec=0.1))
    renewals(monkeypatch, beats)
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1").start()
    heartbeat.thread.join(5)
    assert not heartbeat.thread.is_alive() and not heartbeat.stopping.is_set()  # 自己停的
    assert beats.events.count("extend") >= 1 and not heartbeat.lost.is_set()
    assert f"job {SID} ran longer than 0 s: no more renewals, its lease will expire" in caplog.text


def test_the_heartbeat_can_be_stopped_from_its_own_thread(monkeypatch, caplog):
    beats = Beats(replace(SETTINGS, heartbeat_sec=0.01))
    heartbeat = aq.Heartbeat(beats, SID, TOKEN, "receipt-1")

    def stop_from_inside(*args):
        heartbeat.stop()  # 不能 join 自己
        return True

    monkeypatch.setattr(aq, "extend", stop_from_inside)
    heartbeat.start()
    heartbeat.thread.join(5)
    assert not heartbeat.thread.is_alive() and "renewing the lease failed" not in caplog.text


# ---------- 關機 ----------
def test_stop_requested_while_idle_just_stops_taking_work():
    worker = new_worker()
    before = {thread.name for thread in threading.enumerate()}
    worker.stop_requested()
    assert worker.stopping.is_set() and worker.exits == []
    assert "shutdown" not in {thread.name for thread in threading.enumerate()} - before


def test_give_up_after_lets_a_job_that_finishes_in_time_end_normally(monkeypatch):
    released = []
    monkeypatch.setattr(aq, "release", lambda *args, **kwargs: released.append(args))
    worker = new_worker()
    worker.current = (SID, TOKEN, "receipt-1", "/nonexistent")
    finisher = threading.Timer(0.05, setattr, args=(worker, "current", None))
    finisher.start()
    worker.give_up_after(5)
    finisher.join()
    assert worker.exits == [] and released == [] and worker.sqs.calls == []


def test_give_up_after_gives_the_job_back_and_ends_the_process(monkeypatch, tmp_path, caplog):
    releases, order = [], []
    monkeypatch.setattr(aq, "release", lambda pool, service_id, token, error, counted=True:
                        releases.append((service_id, token, error, counted)) or "queued")
    monkeypatch.setattr(logging, "shutdown", lambda: order.append("logging.shutdown"))  # 真的會關掉 pytest 的 handler
    workdir = tmp_path / "adam-job-left"
    workdir.mkdir()
    (workdir / "cam0.avi").write_bytes(b"recording")
    worker = new_worker()
    worker.exit_process = lambda code: order.append(("exit", code))
    worker.current = (SID, TOKEN, "receipt-1", str(workdir))
    worker.give_up_after(0.05)
    assert releases == [(SID, TOKEN, "the worker was stopped before it finished (not counted)", False)]
    assert worker.sqs.calls == [visibility(0)]  # 馬上可以被別的 worker 領
    assert not workdir.exists() and worker.pool.states() == ["stopped"]
    assert order == ["logging.shutdown", ("exit", 0)]  # log 先寫完才結束
    assert f"job {SID}: stopping before it finished; giving it back for another worker" in caplog.text


def test_stop_requested_during_a_job_starts_the_countdown(monkeypatch):
    monkeypatch.setattr(aq, "release", lambda *args, **kwargs: "queued")
    monkeypatch.setattr(logging, "shutdown", lambda: None)
    worker = new_worker()
    worker.current = (SID, TOKEN, "receipt-1", "/nonexistent")
    worker.stop_requested(grace_sec=0.05)
    wait_until(lambda: worker.exits == [0])
    assert worker.stopping.is_set()


def test_cleanup_removes_only_leftover_work_folders(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="adam-analysis.queue")
    leftover = tmp_path / f"adam-job-{SID}-0123456789ab-x1"
    leftover.mkdir()
    (leftover / "cam0.avi").write_bytes(b"recording")
    (tmp_path / "adam-job-notes.txt").write_text("not a folder")
    (tmp_path / "keep-me").mkdir()
    aq.cleanup_workdirs(str(tmp_path))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["adam-job-notes.txt", "keep-me"]
    assert f"removed a leftover work folder: adam-job-{SID}-0123456789ab-x1" in caplog.text


def test_cleanup_defaults_to_the_temp_folder(tmp_path):
    (tmp_path / "adam-job-old").mkdir()
    aq.cleanup_workdirs()  # tempfile.gettempdir()：isolated 換成了 tmp_path
    assert not (tmp_path / "adam-job-old").exists()


# ---------- serve() ----------
def test_serve_needs_a_queue_url(caplog):
    assert aq.serve(FakeDb(unavailable=True), Analysis()) == 2  # 設定從（清空的）環境變數讀
    assert aq.serve(FakeDb(), Analysis(), settings=replace(SETTINGS, queue_url="")) == 2
    assert "SQS_ANALYSIS_QUEUE_URL is not set: the worker has nothing to read" in caplog.text


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_serve_loads_the_models_then_works_until_a_signal(restore_signals, monkeypatch, tmp_path, signum):
    leftover = tmp_path / "adam-job-old"
    leftover.mkdir()
    sqs = FakeSqs()
    sqs.on_receive = lambda: signal.getsignal(signum)(signum, None)  # long polling 時收到 SIGTERM（或 Ctrl-C）
    monkeypatch.setattr(aq, "make_sqs_client", lambda: sqs)
    db, loaded = FakeDb(), []
    assert aq.serve(db, Analysis(), load_models=lambda: loaded.append(db.states()), detail="whisper medium",
                    settings=SETTINGS) == 0
    assert loaded == [["loading"]] and not leftover.exists()  # 先清暫存、登記 loading，才載入模型
    assert db.reports() == [("loading", "whisper medium"), ("ready", "whisper medium"), ("ready", "whisper medium"),
                            ("stopped", "whisper medium")]
    assert len(sqs.called("receive_message")) == 1


def test_serve_keeps_reporting_loading_while_the_models_load(restore_signals):
    sqs = FakeSqs()
    sqs.on_receive = lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    db, alive = FakeDb(), []

    def load_models():  # 載了好幾拍（真的可能要好幾分鐘，比 worker_stale_sec 久）
        # 不在這裡 assert：例外丟進 serve() 會被當作載入失敗，一直等 SIGTERM，測試就停住了
        deadline = time.monotonic() + 5
        while db.states().count("loading") < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        alive.append(aq.main(["alive"]))  # 載入中 HEALTHCHECK 也通過

    settings = replace(SETTINGS, heartbeat_sec=0.01)
    assert aq.serve(db, Analysis(), load_models=load_models, settings=settings, sqs=sqs) == 0
    states = db.states()
    ready = states.index("ready")
    assert ready >= 3 and set(states[:ready]) == {"loading"} and alive == [0]
    assert states[ready:] == ["ready", "ready", "stopped"]  # 載完就不再報 loading


def test_keep_reporting_beats_only_while_the_block_runs():
    worker = new_worker()
    with aq.keep_reporting(worker, "loading", 0.01):
        wait_until(lambda: worker.pool.states().count("loading") >= 2)
    count = len(worker.pool.states())
    time.sleep(0.05)
    assert len(worker.pool.states()) == count and set(worker.pool.states()) == {"loading"}
    assert "report-loading" not in {thread.name for thread in threading.enumerate()}


def test_keep_reporting_stops_before_an_error_leaves_the_block():
    worker = new_worker()
    with pytest.raises(RuntimeError):
        with aq.keep_reporting(worker, "loading", 0.01):
            wait_until(lambda: "loading" in worker.pool.states())
            raise RuntimeError("CUDA out of memory")
    count = len(worker.pool.states())
    time.sleep(0.05)
    assert len(worker.pool.states()) == count  # 例外丟出去之前就停了：之後寫的 failed 不會被蓋掉


def test_serve_without_models_to_load_goes_straight_to_work(restore_signals):
    sqs = FakeSqs()
    sqs.on_receive = lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    db = FakeDb()
    assert aq.serve(db, Analysis(), settings=SETTINGS, sqs=sqs) == 0  # 假服務：load_models=None
    assert db.states() == ["loading", "ready", "ready", "stopped"]


class Patient(threading.Event):
    """worker.stopping 的替身：第一次 wait(60) 當作 60 秒過了，第二次當作收到 SIGTERM。"""

    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) == 2:
            self.set()
        return self.is_set()


def test_serve_reports_a_model_failure_and_waits_to_be_stopped(restore_signals, monkeypatch, caplog):
    created = []

    class QuietWorker(aq.Worker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, exit_process=lambda code: None, **kwargs)
            self.stopping = Patient()
            created.append(self)

    monkeypatch.setattr(aq, "Worker", QuietWorker)
    db = FakeDb()

    def load_models():
        wait_until(lambda: db.states().count("loading") >= 2)  # 載了一陣子才失敗
        raise RuntimeError("CUDA out of memory")

    settings = replace(SETTINGS, heartbeat_sec=0.01)
    assert aq.serve(db, Analysis(), load_models=load_models, settings=settings, sqs=FakeSqs()) == 1
    [worker] = created
    assert worker.stopping.waits == [60, 60]  # 一直等到被停止，不領工作（免得容器一直重新啟動）
    reports = db.reports()
    failed = reports.index(("failed", "RuntimeError: CUDA out of memory"))
    assert failed >= 2 and set(reports[:failed]) == {("loading", None)}
    assert reports[failed:] == [("failed", "RuntimeError: CUDA out of memory")] * 2  # 停下的 loading 不會蓋掉 failed
    assert worker.sqs.calls == [] and "model loading failed" in caplog.text


# ---------- 管理指令 ----------
GOOD_QUEUE = {
    "QueueArn": "arn:aws:sqs:us-east-1:123456789012:adam-analysis", "VisibilityTimeout": "900",
    "ReceiveMessageWaitTimeSeconds": "20", "MessageRetentionPeriod": "345600",
    "RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:aws:sqs:us-east-1:123456789012:adam-analysis-dlq",
                                 "maxReceiveCount": 3}),
    "ApproximateNumberOfMessages": "2", "ApproximateNumberOfMessagesNotVisible": "1",
}


def test_check_sqs_reads_the_attributes_once_and_agrees_with_the_plan():
    sqs = FakeSqs()
    sqs.attributes = dict(GOOD_QUEUE)
    lines, ok = aq.check_sqs(sqs, SETTINGS)
    assert ok and sqs.calls == [("get_queue_attributes", {"QueueUrl": URL, "AttributeNames": ["All"]})]
    assert lines[0] == f"QueueArn: {GOOD_QUEUE['QueueArn']}" and lines[-1] == "messages: 2 waiting, 1 in flight"
    assert all(line.startswith("OK   ") for line in lines[1:-1]) and len(lines) == 7


@pytest.mark.parametrize("changes, settings, line", [
    ({"VisibilityTimeout": "30"}, SETTINGS, "DIFF VisibilityTimeout: 30 (expected 900)"),
    ({}, replace(SETTINGS, visibility_sec=1200), "DIFF VisibilityTimeout: 900 (expected 1200)"),
    ({"ReceiveMessageWaitTimeSeconds": "0"}, SETTINGS, "DIFF ReceiveMessageWaitTimeSeconds: 0 (expected 20)"),
    ({"MessageRetentionPeriod": "1209600"}, SETTINGS, "DIFF MessageRetentionPeriod: 1209600 (expected 345600)"),
    ({"RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:dlq", "maxReceiveCount": "2"})}, SETTINGS,
     "DIFF RedrivePolicy.maxReceiveCount: 2 (expected >= 3)"),
    ({"RedrivePolicy": None}, SETTINGS, "DIFF RedrivePolicy.deadLetterTargetArn:  (expected (a DLQ))"),
])
def test_check_sqs_marks_each_difference(changes, settings, line):
    sqs = FakeSqs()
    sqs.attributes = {name: value for name, value in {**GOOD_QUEUE, **changes}.items() if value is not None}
    lines, ok = aq.check_sqs(sqs, settings)
    assert not ok and line in lines


def test_check_sqs_accepts_more_receives_than_attempts():
    """Console 的 Maximum receives 設 5（比 max_attempts 的 3 多）也可以：多的次數給標成失敗與 SIGTERM 交還用。"""
    sqs = FakeSqs()
    sqs.attributes = {**GOOD_QUEUE, "RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:dlq", "maxReceiveCount": 5})}
    lines, ok = aq.check_sqs(sqs, SETTINGS)
    assert ok and "OK   RedrivePolicy.maxReceiveCount: 5 (expected >= 3)" in lines


def test_alive_checks_how_old_the_alive_file_is():
    assert aq.main(["alive"]) == 1  # 還沒有回報過
    with open(aq.ALIVE_FILE, "w", encoding="ascii") as alive:
        alive.write("ready")
    assert aq.main(["alive"]) == 0
    old = time.time() - 121
    os.utime(aq.ALIVE_FILE, (old, old))
    assert aq.main(["alive"]) == 1  # 兩分鐘沒有回報


def test_alive_does_not_need_the_settings(monkeypatch):
    monkeypatch.setenv("ANALYSIS_WAIT_SEC", "99")  # 設定錯了，HEALTHCHECK 一樣回答
    assert aq.main(["alive"]) == 1
    with pytest.raises(ValueError) as error:
        aq.main(["status"])
    assert str(error.value) == WAIT


@pytest.mark.parametrize("argv", [[], ["bogus"], ["fail"], ["requeue"], ["fail", SID, "--bogus"], ["status", "x"],
                                  ["check-db", "x"], ["check-db", "--grants"]])
def test_bad_arguments_exit_with_the_usage(capsys, argv):
    with pytest.raises(SystemExit) as stopped:
        aq.main(argv)
    assert stopped.value.code == 2 and "usage: python -m analysis_queue" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["fail", "requeue"])
@pytest.mark.parametrize("service_id", ["S123", SID + "\n", SID.lower(), "S２０２６１１０２０９３０００", f"{SID}; DROP"])
def test_a_malformed_service_id_is_refused_before_connecting(monkeypatch, capsys, command, service_id):
    monkeypatch.setattr(aq, "make_pool", lambda: pytest.fail("connected to the database"))
    assert aq.main([command, service_id]) == 2
    assert capsys.readouterr().out == "service_id must be S followed by 14 digits\n"


def test_status_fail_and_requeue_each_open_a_pool_and_return_their_code(monkeypatch, capsys):
    pools, calls = [], []
    monkeypatch.setenv("SQS_ANALYSIS_QUEUE_URL", URL)
    monkeypatch.setattr(aq, "make_pool", lambda: pools.append(object()) or pools[-1])
    monkeypatch.setattr(aq, "print_status", lambda pool, settings: calls.append(("status", pool, settings)))
    monkeypatch.setattr(aq, "admin_fail", lambda pool, service_id, reason:
                        calls.append(("fail", pool, service_id, reason)) or "ok: job failed")
    monkeypatch.setattr(aq, "admin_requeue", lambda pool, service_id, settings:
                        calls.append(("requeue", pool, service_id)) or "already done: not requeued")
    assert aq.main(["status"]) == 0
    assert aq.main(["fail", SID, "--reason", "bad input"]) == 0
    assert aq.main(["fail", SID]) == 0
    assert aq.main(["requeue", SID]) == 1  # 不是 ok 開頭：結束碼 1
    assert calls == [("status", pools[0], aq.Settings(queue_url=URL)), ("fail", pools[1], SID, "bad input"),
                     ("fail", pools[2], SID, "no retry"), ("requeue", pools[3], SID)]
    assert capsys.readouterr().out == "ok: job failed\nok: job failed\nalready done: not requeued\n"


def test_check_sqs_command_prints_the_lines_without_the_database(monkeypatch, capsys):
    sqs = FakeSqs()
    sqs.attributes = dict(GOOD_QUEUE)
    monkeypatch.setattr(aq, "make_sqs_client", lambda: sqs)
    monkeypatch.setattr(aq, "make_pool", lambda: pytest.fail("check-sqs connected to the database"))
    assert aq.main(["check-sqs"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"QueueArn: {GOOD_QUEUE['QueueArn']}" and len(out) == 7
    sqs.attributes["VisibilityTimeout"] = "30"
    assert aq.main(["check-sqs"]) == 1


def test_running_the_module_exits_with_main_s_code(monkeypatch, tmp_path):
    configured = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: configured.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["analysis_queue", "alive"])
    # 重新執行一份模組（python -m analysis_queue alive）：它的 ALIVE_FILE 在 tempfile.tempdir（tmp_path），還沒有這個檔
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(aq.__file__, run_name="__main__")
    assert stopped.value.code == 1 and configured == [{"level": logging.INFO}]


# ---------- check-db（CD 部署前的資料庫檢查：docs/rebuild/cd/PLAN.md 第 4 節第 3 點、CONTRACTS.md 第 3 節） ----------
DB_ENV = {"DB_HOST": "db.example", "DB_USER": "adam_analysis", "DB_PASSWORD": "adam-test-only", "DB_NAME": "adam"}
SUMMARY_TAIL = "account=adam_analysis@% database=adam needs=tests/integration/grants.sql"
NEEDS = [aq.Need("SELECT", "t", "a"), aq.Need("UPDATE", "t"), aq.Need("EXECUTE", "sp"), aq.Need("DELETE", "t")]


class MysqlError(Exception):
    """mysql.connector.Error 的替身：有 errno 和 msg。"""

    def __init__(self, errno, msg):
        super().__init__(f"{errno} (HY000): {msg}")
        self.errno, self.msg = errno, msg


class FakeServer:
    """check-db 的連線替身。columns、routines 是這個帳號在 information_schema 看得到的欄位、程序（和參數個數）；
    answers[整句 SQL] 是例外（丟出）或結果列；沒寫到的 EXPLAIN 成功、CALL 回 1318（參數個數不對）。executed 記下每一句。"""

    def __init__(self, columns=None, routines=None, answers=None, account="adam_analysis@%", database="adam"):
        self.columns = {"t": ["a", "b"]} if columns is None else columns
        self.routines = {"sp": 3} if routines is None else routines
        self.answers, self.account, self.database = answers or {}, account, database
        self.executed, self.closed = [], []

    def cursor(self):
        return FakeServerCursor(self)

    def close(self):
        self.closed.append("connection")


class FakeServerCursor:
    def __init__(self, server):
        self.server, self.rows = server, []

    def execute(self, sql, args=()):
        server = self.server
        server.executed.append(sql)
        if sql in server.answers:
            answer = server.answers[sql]
            if isinstance(answer, BaseException):
                raise answer
            self.rows = answer
        elif sql == "SELECT CURRENT_USER(), DATABASE()":
            self.rows = [(server.account, server.database)]
        elif sql == aq.VISIBLE_COLUMNS_SQL:
            self.rows = [(table, column) for table, columns in server.columns.items() for column in columns]
        elif sql == aq.VISIBLE_ROUTINES_SQL:
            self.rows = list(server.routines.items())
        elif sql.startswith("CALL "):
            raise MysqlError(1318, "Incorrect number of arguments for PROCEDURE adam.sp; expected 3, got 4")
        else:
            self.rows = [(1, "SIMPLE")]

    def fetchall(self):
        return self.rows

    def close(self):
        self.server.closed.append("cursor")


def grants_file(tmp_path, text):
    path = tmp_path / "grants.sql"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_sql_statements_drop_comments_and_split_on_semicolons():
    text = ("-- a comment; not a statement\nCREATE USER 'u'@'%';  # another; comment\n"
            "GRANT SELECT /* a; comment */ ON adam.t TO 'u'@'%';;\n  \nGRANT DELETE ON adam.t TO 'u'@'%' # no newline")
    assert aq.sql_statements(text) == ["CREATE USER 'u'@'%'", "GRANT SELECT   ON adam.t TO 'u'@'%'",
                                       "GRANT DELETE ON adam.t TO 'u'@'%'"]


def test_quotes_hide_semicolons_and_comment_marks():
    text = ("SELECT 'a;b', \"--c\", `#d`, 'it\\'s;', 'it''s;' FROM t; SELECT `x\\`; SELECT 1--2;\n"
            "SELECT 3 --")
    assert aq.sql_statements(text) == ["SELECT 'a;b', \"--c\", `#d`, 'it\\'s;', 'it''s;' FROM t", "SELECT `x\\`",
                                       "SELECT 1--2", "SELECT 3"]  # 反引號裡的 \ 不是跳脫；--2 不是註解


@pytest.mark.parametrize("text, message", [("GRANT SELECT ON 't", "a ' quote is not closed"),
                                           ("GRANT /* SELECT", "a /* comment is not closed")])
def test_an_unclosed_quote_or_comment_is_an_error(text, message):
    with pytest.raises(ValueError) as error:
        aq.sql_statements(text)
    assert str(error.value) == message


@pytest.mark.parametrize("statement, needs", [
    ("GRANT SELECT, INSERT, UPDATE, DELETE ON adam.t TO 'u'@'%'",
     [aq.Need("SELECT", "t"), aq.Need("INSERT", "t"), aq.Need("UPDATE", "t"), aq.Need("DELETE", "t")]),
    ("GRANT SELECT (a, b), UPDATE (c) ON adam.t TO 'u'@'%'",
     [aq.Need("SELECT", "t", "a"), aq.Need("SELECT", "t", "b"), aq.Need("UPDATE", "t", "c")]),
    ("GRANT EXECUTE ON PROCEDURE adam.sp TO 'u'@'%'", [aq.Need("EXECUTE", "sp")]),
    ("grant select(a) ,\n insert ( b )  on table t\nto u with grant option",
     [aq.Need("SELECT", "t", "a"), aq.Need("INSERT", "t", "b")]),
    ("GRANT SELECT (`we``ird`) ON `adam`.`tbl``x` TO 'u'@'%'", [aq.Need("SELECT", "tbl`x", "we`ird")]),
])
def test_a_grant_is_one_need_per_privilege_and_column(statement, needs):
    assert aq.parse_grant(statement) == needs


@pytest.mark.parametrize("statement, message", [
    ("GRANT SELECT ON adam.* TO 'u'@'%'", "only GRANTs on one table or one procedure can be checked"),
    ("GRANT EXECUTE ON FUNCTION adam.f TO 'u'@'%'", "only GRANTs on one table or one procedure can be checked"),
    ("GRANT reporting_role TO 'u'@'%'", "only GRANTs on one table or one procedure can be checked"),
    ("GRANT SELECT ON adam.t", "only GRANTs on one table or one procedure can be checked"),
    ("GRANT ALL PRIVILEGES ON adam.t TO 'u'@'%'", "ALL PRIVILEGES cannot be checked"),
    ("GRANT SELECT, ALTER ON adam.t TO 'u'@'%'", "ALTER cannot be checked"),
    ("GRANT EXECUTE ON adam.t TO 'u'@'%'", "EXECUTE cannot be checked"),  # EXECUTE 只給程序
    ("GRANT SELECT ON PROCEDURE adam.sp TO 'u'@'%'", "SELECT cannot be checked"),
    ("GRANT SELECT (a ON adam.t TO 'u'@'%'", "SELECT (a cannot be checked"),
    ("GRANT DELETE (a) ON adam.t TO 'u'@'%'", "bad column list"),
    ("GRANT SELECT () ON adam.t TO 'u'@'%'", "bad column list"),
    ("GRANT UPDATE (a,) ON adam.t TO 'u'@'%'", "bad column list"),
])
def test_grants_check_db_cannot_check_are_errors_not_skipped(statement, message):
    with pytest.raises(ValueError) as error:
        aq.parse_grant(statement)
    assert str(error.value) == f"{message}: {statement}"


def test_read_needs_keeps_the_file_order_without_repeats(tmp_path):
    path = grants_file(tmp_path, (
        "-- a test file\nCREATE USER 'u'@'%' IDENTIFIED BY 'p;w' REQUIRE SSL;\n"
        "GRANT SELECT (a, b) ON adam.t TO 'u'@'%';\n"
        "GRANT UPDATE (a) ON adam.t TO 'u'@'%';\n"
        "GRANT SELECT ON adam.t TO 'u'@'%';  -- the whole table: (a) and (b) above are part of it\n"
        "GRANT UPDATE (a), DELETE ON adam.t TO 'u'@'%';\n"
        "GRANT EXECUTE ON PROCEDURE adam.sp TO 'u'@'%';\nGRANT EXECUTE ON PROCEDURE adam.sp TO 'u'@'%';\n"))
    assert aq.read_needs(path) == [aq.Need("UPDATE", "t", "a"), aq.Need("SELECT", "t"), aq.Need("DELETE", "t"),
                                   aq.Need("EXECUTE", "sp")]


def test_a_file_without_grants_is_an_error(tmp_path):
    with pytest.raises(ValueError) as error:
        aq.read_needs(grants_file(tmp_path, "CREATE USER 'u'@'%';\n-- GRANT SELECT ON adam.t TO 'u'@'%';\n"))
    assert str(error.value) == "there is no GRANT in it"


def test_every_grant_in_this_service_s_grants_sql_is_read():
    """和逐行拆開的結果一樣：兩個服務的 grants.sql 都是一行一句 GRANT、只給 adam_analysis（每一句都讀到了）。"""
    expected = []
    with open(aq.GRANTS_FILE, encoding="utf-8") as source:
        for line in source:
            match = re.fullmatch(r"GRANT (.+) ON (?:PROCEDURE )?adam\.(\w+) TO 'adam_analysis'@'%';", line.strip())
            assert match or not line.startswith("GRANT"), line
            if not match:
                continue
            for privilege, columns in re.findall(r"(\w+)(?: \(([^)]*)\))?", match[1]):
                if columns:
                    expected.extend(aq.Need(privilege, match[2], column) for column in columns.split(", "))
                else:
                    expected.append(aq.Need(privilege, match[2]))
    needs = aq.read_needs()
    assert needs == list(dict.fromkeys(expected)) and len(needs) > 20
    for need in [aq.Need("UPDATE", "tbl_service", "service_status"), aq.Need("EXECUTE", "sp_insert_text_storage"),
                 aq.Need("INSERT", "tbl_analysis_job"), aq.Need("DELETE", "tbl_analysis_worker")]:
        assert need in needs  # 兩個服務都要的
    assert aq.GRANTS_FILE == os.path.join(os.path.dirname(os.path.abspath(aq.__file__)), "tests", "integration",
                                          "grants.sql")


def test_a_need_reads_like_its_grant():
    assert [str(need) for need in (aq.Need("SELECT", "t"), aq.Need("UPDATE", "t", "c"), aq.Need("EXECUTE", "sp"))] == [
        "SELECT ON t", "UPDATE (c) ON t", "EXECUTE ON PROCEDURE sp"]


@pytest.mark.parametrize("need, columns, parameters, sql", [
    (aq.Need("SELECT", "t"), (), 0, "EXPLAIN SELECT * FROM `t` WHERE FALSE"),
    (aq.Need("SELECT", "t", "c"), (), 0, "EXPLAIN SELECT `c` FROM `t` WHERE FALSE"),
    (aq.Need("INSERT", "t"), (), 0, "EXPLAIN INSERT INTO `t` () VALUES ()"),
    (aq.Need("INSERT", "t", "c"), (), 0, "EXPLAIN INSERT INTO `t` (`c`) VALUES (DEFAULT)"),
    (aq.Need("UPDATE", "t", "c"), ["a", "b"], 0, "EXPLAIN UPDATE `t` SET `c` = DEFAULT WHERE FALSE"),
    (aq.Need("UPDATE", "t"), ["a", "b"], 0, "EXPLAIN UPDATE `t` SET `a` = DEFAULT, `b` = DEFAULT WHERE FALSE"),
    (aq.Need("UPDATE", "t"), (), 0, "EXPLAIN UPDATE `t` SET `check_db_probe` = DEFAULT WHERE FALSE"),
    (aq.Need("DELETE", "t"), (), 0, "EXPLAIN DELETE FROM `t` WHERE FALSE"),
    (aq.Need("EXECUTE", "sp"), (), 3, "CALL `sp`(NULL, NULL, NULL, NULL)"),  # 參數多一個：MySQL 在執行前就回 1318
    (aq.Need("EXECUTE", "sp"), (), 0, "CALL `sp`(NULL)"),
    (aq.Need("SELECT", "we`ird", "c`ol"), (), 0, "EXPLAIN SELECT `c``ol` FROM `we``ird` WHERE FALSE"),
])
def test_each_need_is_tried_with_a_statement_mysql_does_not_run(need, columns, parameters, sql):
    assert aq.probe_sql(need, columns, parameters) == sql


def test_check_db_tries_the_tables_first_then_the_procedures_in_a_read_only_session():
    server = FakeServer()
    lines, code = aq.check_db(server, NEEDS)
    assert code == 0 and lines == ["OK      SELECT (a) ON t", "OK      UPDATE ON t", "OK      EXECUTE ON PROCEDURE sp",
                                   "OK      DELETE ON t", f"check-db: result=ok code=0 ok=4 missing=0 denied=0 {SUMMARY_TAIL}"]
    assert server.executed == [
        "SELECT CURRENT_USER(), DATABASE()", aq.VISIBLE_COLUMNS_SQL, aq.VISIBLE_ROUTINES_SQL,
        "EXPLAIN SELECT `a` FROM `t` WHERE FALSE", "EXPLAIN UPDATE `t` SET `a` = DEFAULT, `b` = DEFAULT WHERE FALSE",
        "EXPLAIN DELETE FROM `t` WHERE FALSE", "SET SESSION transaction_read_only = ON",
        "CALL `sp`(NULL, NULL, NULL, NULL)"]  # 看得到 3 個參數：多給一個
    assert server.closed == ["cursor"]  # 連線由 run_check_db() 關


def test_without_procedures_the_session_is_left_as_it_is():
    server = FakeServer()
    assert aq.check_db(server, [aq.Need("SELECT", "t")])[1] == 0
    assert "SET SESSION transaction_read_only = ON" not in server.executed


@pytest.mark.parametrize("need, errno, status", [
    (aq.Need("SELECT", "t"), 1146, "MISSING"), (aq.Need("SELECT", "t", "a"), 1054, "MISSING"),
    (aq.Need("EXECUTE", "sp"), 1305, "MISSING"), (aq.Need("SELECT", "t"), 1142, "DENIED"),
    (aq.Need("UPDATE", "t", "a"), 1143, "DENIED"), (aq.Need("EXECUTE", "sp"), 1370, "DENIED"),
])
def test_mysql_s_error_code_says_missing_or_denied(need, errno, status):
    server = FakeServer(answers={aq.probe_sql(need, ["a", "b"], 3): MysqlError(errno, "what MySQL said")})
    lines, code = aq.check_db(server, [need, aq.Need("DELETE", "t")])
    assert code == 1 and lines == [f"{status:<7} {need}: {errno} what MySQL said", "OK      DELETE ON t",
                                   f"check-db: result=failed code=1 ok=1 missing={int(status == 'MISSING')} "
                                   f"denied={int(status == 'DENIED')} {SUMMARY_TAIL}"]


@pytest.mark.parametrize("need, errno", [(aq.Need("SELECT", "hidden"), 1142),  # 整張表都看不到
                                         (aq.Need("SELECT", "t", "secret"), 1143),  # 表看得到、這一欄看不到
                                         (aq.Need("EXECUTE", "hidden_sp"), 1370)])
def test_denied_things_the_account_cannot_see_may_also_be_missing(need, errno):
    server = FakeServer(answers={aq.probe_sql(need): MysqlError(errno, "denied")})
    lines, code = aq.check_db(server, [need])
    assert code == 1 and lines[0] == f"DENIED  {need}: {errno} denied{aq.INVISIBLE_HINT}"


def test_visibility_ignores_case_like_mysql():
    needs = [aq.Need("UPDATE", "t", "a"), aq.Need("EXECUTE", "sp")]
    server = FakeServer(columns={"t": ["A"]}, routines={"SP": 2}, answers={
        aq.probe_sql(needs[0]): MysqlError(1143, "denied"), "CALL `sp`(NULL, NULL, NULL)": MysqlError(1370, "denied")})
    lines, code = aq.check_db(server, needs)
    assert code == 1 and lines[:2] == ["DENIED  UPDATE (a) ON t: 1143 denied", "DENIED  EXECUTE ON PROCEDURE sp: 1370 denied"]


def test_an_unexpected_error_stops_the_check_with_code_2():
    needs = [aq.Need("SELECT", "t"), aq.Need("DELETE", "t"), aq.Need("EXECUTE", "sp")]
    server = FakeServer(answers={"EXPLAIN SELECT * FROM `t` WHERE FALSE": MysqlError(1142, "denied"),
                                 "EXPLAIN DELETE FROM `t` WHERE FALSE": MysqlError(2013, "Lost connection to MySQL")})
    lines, code = aq.check_db(server, needs)
    assert code == 2 and lines == [  # 試過的照樣列出來；沒辦法判斷比有缺的優先
        "DENIED  SELECT ON t: 1142 denied", "check-db: could not check DELETE ON t: 2013 Lost connection to MySQL",
        f"check-db: result=error code=2 ok=0 missing=0 denied=1 {SUMMARY_TAIL}"]
    assert not any(sql.startswith(("SET", "CALL")) for sql in server.executed)


def test_errors_that_are_not_from_mysql_are_described():
    server = FakeServer(answers={"EXPLAIN SELECT * FROM `t` WHERE FALSE": RuntimeError("boom")})
    lines, code = aq.check_db(server, [aq.Need("SELECT", "t")])
    assert code == 2 and lines[0] == "check-db: could not check SELECT ON t: RuntimeError: boom"


@pytest.mark.parametrize("failing, account, database", [("SELECT CURRENT_USER(), DATABASE()", "-", "-"),
                                                        (aq.VISIBLE_COLUMNS_SQL, "adam_analysis@%", "adam"),
                                                        (aq.VISIBLE_ROUTINES_SQL, "adam_analysis@%", "adam")])
def test_an_error_while_looking_around_is_code_2(failing, account, database):
    server = FakeServer(answers={failing: MysqlError(2013, "Lost connection to MySQL")})
    lines, code = aq.check_db(server, NEEDS)
    assert code == 2 and lines == ["check-db: could not finish: 2013 Lost connection to MySQL",
                                   f"check-db: result=error code=2 ok=0 missing=0 denied=0 account={account} "
                                   f"database={database} needs=tests/integration/grants.sql"]


def test_a_procedure_that_really_runs_is_not_taken_as_ok():
    server = FakeServer(answers={"CALL `sp`(NULL, NULL, NULL, NULL)": []})  # 不會發生：多一個參數，MySQL 不執行
    lines, code = aq.check_db(server, [aq.Need("EXECUTE", "sp")])
    assert code == 2 and lines[0] == ("check-db: could not check EXECUTE ON PROCEDURE sp: "
                                      "the procedure ran, the extra argument should have stopped it")


def test_without_a_default_database_the_summary_shows_a_dash():
    lines, _ = aq.check_db(FakeServer(database=None), [aq.Need("SELECT", "t")])
    assert lines[-1].endswith(" account=adam_analysis@% database=- needs=tests/integration/grants.sql")


@pytest.mark.parametrize("changes, message", [
    ({"DB_HOST": None}, "DB_HOST is not set"), ({"DB_USER": None}, "DB_USER is not set"),
    ({"DB_PASSWORD": None}, "DB_PASSWORD is not set"), ({"DB_NAME": None}, "DB_NAME is not set"),
    ({"DB_HOST": " "}, "DB_HOST is not set"), ({"DB_USER": ""}, "DB_USER is not set"), ({"DB_NAME": ""}, "DB_NAME is not set"),
    ({"DB_PORT": "33o6"}, "DB_PORT must be a number, got '33o6'"),
])
def test_check_db_needs_the_service_s_db_variables(changes, message):
    environ = {name: value for name, value in {**DB_ENV, **changes}.items() if value is not None}
    lines, code = aq.run_check_db(environ=environ, connect=lambda settings: pytest.fail("connected"))
    assert code == 2 and lines == [f"check-db: {message}", "check-db: result=error code=2 ok=0 missing=0 denied=0 "
                                                           "account=- database=- needs=tests/integration/grants.sql"]


@pytest.mark.parametrize("error, text", [
    (MysqlError(2003, "Can't connect to MySQL server on 'db.example:3306' (111)"),
     "2003 Can't connect to MySQL server on 'db.example:3306' (111)"),
    (MysqlError(1298, "Unknown or incorrect time zone: 'Mars/Phobos'"), "1298 Unknown or incorrect time zone: 'Mars/Phobos'"),
    (OSError("network is unreachable"), "OSError: network is unreachable"),
])
def test_check_db_that_cannot_connect_is_code_2(error, text):
    def refuse(settings):
        raise error

    lines, code = aq.run_check_db(environ=DB_ENV, connect=refuse)
    assert code == 2 and lines[0] == f"check-db: cannot connect to the database: {text}"


def test_check_db_reads_the_grants_file_before_connecting(tmp_path):
    def never(settings):
        pytest.fail("connected")

    missing = str(tmp_path / "missing.sql")
    lines, code = aq.run_check_db(missing, environ=DB_ENV, connect=never)
    assert code == 2 and lines[0].startswith(f"check-db: cannot read {missing}: [Errno 2] No such file or directory")
    assert lines[1].endswith(f" needs={missing}")
    path = grants_file(tmp_path, "-- nothing to grant\n")
    assert aq.run_check_db(path, environ=DB_ENV, connect=never) == (
        [f"check-db: cannot read {path}: there is no GRANT in it",
         f"check-db: result=error code=2 ok=0 missing=0 denied=0 account=- database=- needs={path}"], 2)


def test_check_db_connects_like_the_service_and_closes_the_connection(tmp_path):
    connected, server = [], FakeServer()
    path = grants_file(tmp_path, "GRANT SELECT (a) ON adam.t TO 'adam_analysis'@'%';\n")
    lines, code = aq.run_check_db(path, environ={**DB_ENV, "DB_TIME_ZONE": "UTC"},
                                  connect=lambda settings: connected.append(settings) or server)
    assert code == 0 and lines == ["OK      SELECT (a) ON t", "check-db: result=ok code=0 ok=1 missing=0 denied=0 "
                                                              f"account=adam_analysis@% database=adam needs={path}"]
    assert connected == [{"host": "db.example", "port": 3306, "user": "adam_analysis", "password": "adam-test-only",
                          "database": "adam", "time_zone": "UTC"}]
    assert server.closed == ["cursor", "connection"]


def test_an_error_while_closing_does_not_change_the_result(tmp_path):
    server = FakeServer()

    def broken_close():
        raise MysqlError(2013, "Lost connection to MySQL")

    server.close = broken_close
    path = grants_file(tmp_path, "GRANT DELETE ON adam.t TO 'adam_analysis'@'%';\n")
    assert aq.run_check_db(path, environ=DB_ENV, connect=lambda settings: server)[1] == 0


def test_connection_settings_are_the_service_s(monkeypatch):
    assert aq.connection_settings(DB_ENV) == {"host": "db.example", "port": 3306, "user": "adam_analysis",
                                              "password": "adam-test-only", "database": "adam",
                                              "time_zone": "America/Los_Angeles"}
    for name, value in {**DB_ENV, "DB_PORT": "13306", "DB_TIME_ZONE": "UTC"}.items():
        monkeypatch.setenv(name, value)
    assert aq.connection_settings() == {**aq.connection_settings(DB_ENV), "port": 13306, "time_zone": "UTC"}


def test_make_pool_uses_the_same_settings(monkeypatch):
    from mysql.connector import pooling
    made = []
    monkeypatch.setattr(pooling, "MySQLConnectionPool", lambda **kwargs: made.append(kwargs) or "pool")
    for name, value in DB_ENV.items():
        monkeypatch.setenv(name, value)
    for name in ("DB_PORT", "DB_TIME_ZONE"):
        monkeypatch.delenv(name, raising=False)
    assert aq.make_pool(size=5) == "pool"
    assert made == [{"pool_name": "adam-admin", "pool_size": 5, **aq.connection_settings(DB_ENV)}]


def test_check_db_has_its_own_autocommit_connection_with_a_timeout(monkeypatch):
    import mysql.connector
    calls = []
    monkeypatch.setattr(mysql.connector, "connect", lambda **kwargs: calls.append(kwargs) or "connection")
    assert aq.connect_for_check({"host": "db.example"}) == "connection"
    assert calls == [{"host": "db.example", "autocommit": True, "connection_timeout": aq.CHECK_DB_TIMEOUT_SEC}]


def test_the_check_db_command_prints_the_lines_and_exits_with_their_code(monkeypatch, capsys):
    runs = []
    monkeypatch.setattr(aq, "run_check_db", lambda path: runs.append(path) or (["OK      SELECT ON t", "check-db: x"], 1))
    monkeypatch.setattr(aq, "make_pool", lambda: pytest.fail("check-db used the admin pool"))
    monkeypatch.setenv("ANALYSIS_WAIT_SEC", "99")  # SQS 的設定錯了也不影響：check-db 只看資料庫
    assert aq.main(["check-db"]) == 1
    assert aq.main(["check-db", "--grants", "/elsewhere/grants.sql"]) == 1
    assert runs == [aq.GRANTS_FILE, "/elsewhere/grants.sql"]
    assert capsys.readouterr().out == "OK      SELECT ON t\ncheck-db: x\n" * 2


def test_the_check_db_command_checks_every_grant_of_this_service(monkeypatch, capsys):
    needs = aq.read_needs()
    server = FakeServer(columns={need.name: ["x"] for need in needs if need.privilege != "EXECUTE"},
                        routines={need.name: 3 for need in needs if need.privilege == "EXECUTE"})
    for name, value in DB_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(aq, "connect_for_check", lambda settings: server)
    assert aq.main(["check-db"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"OK      {need}" for need in needs] + [
        f"check-db: result=ok code=0 ok={len(needs)} missing=0 denied=0 {SUMMARY_TAIL}"]
    assert server.closed == ["cursor", "connection"]
