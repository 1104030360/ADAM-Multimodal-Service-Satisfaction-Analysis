# tests/queue/test_queue_flow.py — analysis_queue.py 接上真的 MySQL 8.4 與 moto 的 SQS（SQS 分析工作佇列，T49）。
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）；環境在同一個資料夾的 conftest.py。
# 檔名不能和 tests/unit/test_analysis_queue.py 相同：analyzer/ 用 pytest 預設的 prepend 匯入模式，同名會收集失敗。
# 兩種匯入模式（analysis_service/ 用 importlib）都要能跑，所以不 import 別的測試檔，小工具放在這裡或 conftest.py。
# sqs/PLAN.md 第 5 節（教學第 9 節）標 A 的驗收對到這些測試：
#   commit 後、送 SQS 前停止 API       test_a_restarted_api_sends_what_was_accepted_before_it_stopped
#   SQS 暫時無法連線                  test_an_sqs_outage_keeps_the_row_pending_and_backs_off
#   SQS 收到、outbox 還沒標記就中斷     test_a_crash_after_sqs_accepted_resends_and_still_commits_once
#   重複 HTTP／重複 SQS 訊息           test_concurrent_requests_for_one_service_create_exactly_one_job、
#                                    test_a_duplicate_message_after_done_is_deleted_without_another_run、
#                                    test_two_workers_never_hold_a_valid_lease_at_the_same_time
#   worker 計算中被終止                test_after_the_lease_expires_another_worker_takes_over_and_the_old_one_cannot_commit
#   分析超過 15 分鐘                   test_the_heartbeat_extends_the_visibility_and_the_lease_while_the_job_runs、
#                                    test_the_heartbeat_stops_after_job_timeout_sec_and_the_late_result_is_discarded
#   結果 commit 後、刪訊息前中斷        test_a_crash_between_commit_and_delete_is_cleaned_up_by_the_next_receive
#   持續失敗直到 DLQ                   test_three_failures_fail_the_job_and_the_message_ends_in_the_dlq、
#                                    test_admin_fail_marks_only_jobs_nobody_holds、test_admin_requeue_starts_a_new_round
import json
import logging
import os
import re
import signal
import threading
import time
from contextlib import closing
from dataclasses import replace

import mysql.connector
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import analysis_queue as aq

pytestmark = pytest.mark.integration
LOGGER = "adam-analysis.queue"


def queued(sid, state="queued"):
    """accept() 的 200 回應。"""
    return 200, {"service_id": sid, "accepted": True, "state": state}


def wait_until(condition, timeout=10, interval=0.02):
    """等背景執行緒（worker、heartbeat、送出器）做到某一步；timeout 秒內沒有就失敗。"""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"{timeout} 秒內沒有等到"
        time.sleep(interval)


class Crash(BaseException):
    """process 在這一步整個不見了（kill -9、OOM、斷電）：模組的 except Exception 攔不到，什麼都來不及做。"""


class SqsSpy:
    """包住 moto 的 SQS client，記下每次呼叫的參數。

    fail[動作]：呼叫前丟出這個例外（例如 SQS 暫時連不上）；crash_after：真的呼叫完才丟出 Crash（SQS 收下了，process 才不見）；
    before[動作]：呼叫前先執行（例如第二次 long polling 時送出 SIGTERM）。
    """

    def __init__(self, sqs):
        self.sqs, self.calls, self.fail, self.crash_after, self.before = sqs, [], {}, set(), {}

    def called(self, name):
        return [kwargs for called, kwargs in self.calls if called == name]

    def __getattr__(self, name):
        method = getattr(self.sqs, name)

        def call(**kwargs):
            self.calls.append((name, kwargs))
            if name in self.before:
                self.before[name]()
            if name in self.fail:
                raise self.fail[name]
            result = method(**kwargs)
            if name in self.crash_after:
                raise Crash(name)
            return result

        return call


class FlakyPool:
    """包住真的 pool：第一次執行到某一句 SQL 時丟出資料庫錯誤（例如連線在交易中途斷了），其他照常。"""

    def __init__(self, pool, sql):
        self.pool, self.sql, self.failed = pool, sql, False

    def get_connection(self):
        return _FlakyConnection(self, self.pool.get_connection())


class _FlakyConnection:
    def __init__(self, flaky, conn):
        self.flaky, self.conn = flaky, conn

    def cursor(self):
        return _FlakyCursor(self.flaky, self.conn.cursor())

    def __getattr__(self, name):  # commit、rollback、close 交給真的連線
        return getattr(self.conn, name)


class _FlakyCursor:
    def __init__(self, flaky, cur):
        self.flaky, self.cur = flaky, cur

    def execute(self, sql, args=()):
        if sql == self.flaky.sql and not self.flaky.failed:
            self.flaky.failed = True
            raise mysql.connector.errors.OperationalError("2013 (HY000): Lost connection to MySQL server during query")
        return self.cur.execute(sql, args)

    def __getattr__(self, name):  # fetchall、rowcount、close
        return getattr(self.cur, name)


class SafeWorker(aq.Worker):
    """serve() 建立的 Worker 換成它：exit_process 只記下結束碼，萬一要強制結束也不會結束 pytest。"""

    created = []

    def __init__(self, *args, **kwargs):
        self.exits = []
        kwargs.setdefault("exit_process", self.exits.append)
        super().__init__(*args, **kwargs)
        SafeWorker.created.append(self)


def recording_errors(load_models, errors):
    """serve() 的 load_models：裡面的檢查出錯時記在 errors，不丟進 serve()（它會當作模型載入失敗，一直等 SIGTERM，
    測試就停住了）；serve() 回來之後再看 errors。"""
    def load():
        try:
            load_models()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    return load


@pytest.fixture
def restore_signals():
    """serve() 會換掉 SIGTERM、SIGINT 的處理；測試結束時換回來。"""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


# ---------- API：accept()（教學 5-1） ----------
def test_an_unknown_service_is_404_and_nothing_is_written(pool, settings, db, new_sid):
    sid = new_sid()
    assert aq.accept(pool, sid, settings) == (404, {"error": "service_not_found"})
    assert db.job(sid) is None and db.outbox() == []


def test_a_completed_service_is_409_and_nothing_is_written(pool, settings, db, add_service):
    sid = add_service(3)
    assert aq.accept(pool, sid, settings) == (409, {"error": "already_completed"})
    assert db.job(sid) is None and db.outbox() == [] and db.status(sid) == 3
    # 工作已經 done（例如狀態被手動改回 2）：一樣 409，不重跑（這一版不支援 force）
    done = add_service(2)
    db.add_job(done, "done", attempts=1)
    before = db.job(done)
    assert aq.accept(pool, done, settings) == (409, {"error": "already_completed"})
    assert db.job(done) == before and db.outbox() == [] and db.status(done) == 2


def test_the_first_request_writes_the_job_the_outbox_and_status_2(pool, settings, db, add_service):
    sid = add_service(1)
    assert aq.accept(pool, sid, settings) == queued(sid)
    job = db.job(sid)
    assert (job["job_state"], job["attempts"], job["max_attempts"]) == ("queued", 0, settings.max_attempts)
    assert all(job[name] is None for name in ("lease_token", "lease_until", "started_at", "finished_at",
                                              "result_token", "report_data_key", "last_error"))
    assert 0 <= db.age(sid, "accepted_at") < 5  # UTC：資料庫的 UTC_TIMESTAMP(3)，不是連線的 America/Los_Angeles
    [event] = db.outbox(sid)
    assert re.fullmatch(r"[0-9a-f]{32}", event["event_id"])
    assert (event["outbox_state"], event["send_attempts"], event["sent"]) == ("pending", 0, False)
    assert event["due_in"] <= 0  # 馬上可以送
    assert db.status(sid) == 2  # 狀態 2 由 API 寫（桌面端收到 200 之後不再寫）


def test_accept_is_one_transaction(pool, settings, db, add_service):
    sid = add_service(1)
    with pytest.raises(mysql.connector.Error):
        aq.accept(FlakyPool(pool, aq.MARK_SENT_SQL), sid, settings)  # 最後一步（狀態 1 → 2）時連線斷了
    assert db.job(sid) is None and db.outbox() == [] and db.status(sid) == 1  # 先寫的工作、outbox 一起 rollback
    assert aq.accept(pool, sid, settings) == queued(sid)  # 恢復之後再按一次就好


def test_repeated_requests_get_the_same_job_without_another_message(pool, settings, db, add_service):
    sid = add_service(1)
    aq.accept(pool, sid, settings)
    before = db.job(sid)
    assert aq.accept(pool, sid, settings) == queued(sid, "already_queued")
    assert db.job(sid) == before and len(db.outbox(sid)) == 1
    # 正在執行（有 worker 拿著 lease）也一樣
    assert aq.claim(pool, sid, aq.new_token(), settings).result == "claimed"
    assert aq.accept(pool, sid, settings) == queued(sid, "already_queued")
    # 狀態被手動改回 1、工作卻還在：補回 2，也不多送一則
    db.set_status(sid, 1)
    assert aq.accept(pool, sid, settings) == queued(sid, "already_queued")
    assert db.status(sid) == 2 and len(db.outbox(sid)) == 1 and db.job(sid)["job_state"] == "running"


def test_retry_after_a_failed_round_reopens_the_job(pool, settings, db, add_service):
    sid = add_service(1)
    db.add_job(sid, "failed", attempts=3, last_error="ValueError: cannot read the video")
    db.add_event(sid, "sent", created_sec_ago=600, sent_sec_ago=600)
    assert aq.accept(pool, sid, replace(settings, max_attempts=5)) == queued(sid)  # 這一輪的上限照 API 現在的設定
    job = db.job(sid)
    assert (job["job_state"], job["attempts"], job["max_attempts"]) == ("queued", 0, 5)
    assert all(job[name] is None for name in ("lease_token", "lease_until", "started_at", "finished_at"))
    assert db.age(sid, "accepted_at") < 5
    assert [event["outbox_state"] for event in db.outbox(sid)] == ["sent", "pending"]  # 新的一則
    assert db.status(sid) == 2


def test_concurrent_requests_for_one_service_create_exactly_one_job(settings, db, add_service, new_pool, background):
    sid = add_service(1)
    pool = new_pool(8)
    ready = threading.Barrier(8)

    def request():
        ready.wait(10)
        return aq.accept(pool, sid, settings)

    replies = [run.join() for run in [background(request) for _ in range(8)]]
    assert {code for code, _ in replies} == {200}
    assert sorted(reply["state"] for _, reply in replies) == ["already_queued"] * 7 + ["queued"]
    assert len(db.outbox(sid)) == 1 and db.job(sid)["job_state"] == "queued" and db.status(sid) == 2


def test_a_service_accepted_before_the_queue_existed_gets_a_job(pool, settings, db, add_service):
    sid = add_service(2)  # 搬遷前就送出、狀態停在 2、沒有工作
    assert aq.accept(pool, sid, settings) == queued(sid)
    assert db.job(sid)["job_state"] == "queued" and len(db.outbox(sid)) == 1 and db.status(sid) == 2


# ---------- 送出器（教學 5-2） ----------
def test_the_publisher_sends_due_rows_and_marks_them_sent(pool, settings, db, queues, add_service, make_publisher,
                                                          caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    sids = [add_service(1) for _ in range(2)]
    for sid in sids:
        aq.accept(pool, sid, settings)
    publisher = make_publisher()
    assert publisher.run_once() == 2
    assert publisher.run_once() == 0  # 送出的不再送
    messages = {json.loads(message["Body"])["service_id"]: message for message in queues.take_all()}
    assert sorted(messages) == sorted(sids)
    for sid in sids:
        [event] = db.outbox(sid)
        assert json.loads(messages[sid]["Body"]) == {"service_id": sid, "event_id": event["event_id"]}
        assert (event["outbox_state"], event["sent"], event["send_attempts"]) == ("sent", True, 1)
        assert event["sqs_message_id"] == messages[sid]["MessageId"] and event["last_error"] is None
        assert f"outbox {event['event_id']} sent to SQS for {sid}" in caplog.text


def test_a_restarted_api_sends_what_was_accepted_before_it_stopped(pool, settings, db, queues, add_service,
                                                                   make_publisher):
    sid = add_service(1)
    aq.accept(pool, sid, settings)  # 舊的 API：commit、回 200 之後，送出器還沒送就停了
    assert make_publisher().run_once() == 1  # 重新啟動的 API 裡新的送出器接手，不用桌面端再按一次
    assert queues.bodies() == [{"service_id": sid, "event_id": db.outbox(sid)[0]["event_id"]}]


def test_an_sqs_outage_keeps_the_row_pending_and_backs_off(pool, settings, db, queues, add_service, make_publisher,
                                                           caplog):
    sid = add_service(1)
    aq.accept(pool, sid, settings)
    sqs = SqsSpy(queues.sqs)
    sqs.fail["send_message"] = EndpointConnectionError(endpoint_url="https://sqs.us-east-1.amazonaws.com/")
    publisher = make_publisher(sqs=sqs)
    assert publisher.run_once() == 0
    [event] = db.outbox(sid)
    assert (event["outbox_state"], event["send_attempts"]) == ("pending", 1)
    assert event["last_error"].startswith("EndpointConnectionError: Could not connect to the endpoint URL")
    assert 4 < event["due_in"] <= 6  # backoff(1)：5 秒，再加最多 20%
    assert publisher.run_once() == 0 and len(sqs.called("send_message")) == 1  # 還沒到期：不再打 SQS
    db.make_due()
    assert publisher.run_once() == 0
    [event] = db.outbox(sid)
    assert event["send_attempts"] == 2 and 9 < event["due_in"] <= 12  # 10 秒，再加最多 20%
    assert f"outbox {event['event_id']} not sent (attempt 2), retrying in" in caplog.text
    # SQS 恢復：到期之後送出
    del sqs.fail["send_message"]
    db.make_due()
    assert publisher.run_once() == 1
    [event] = db.outbox(sid)
    assert (event["outbox_state"], event["send_attempts"], event["last_error"]) == ("sent", 3, None)
    assert [body["service_id"] for body in queues.bodies()] == [sid]


def test_a_crash_after_sqs_accepted_resends_and_still_commits_once(pool, settings, db, queues, add_service,
                                                                   make_publisher, make_worker, analysis):
    sid = add_service(1)
    aq.accept(pool, sid, settings)
    sqs = SqsSpy(queues.sqs)
    sqs.crash_after.add("send_message")  # SQS 收下了，outbox 還沒標成 sent，API 就整個不見了
    with pytest.raises(Crash):
        make_publisher(sqs=sqs).run_once()
    [event] = db.outbox(sid)
    assert (event["outbox_state"], event["send_attempts"]) == ("pending", 1)
    assert 55 < event["due_in"] <= 60  # 拿走時往後推了 TAKE_SEC：那個 process 可能還在送，先不要重送
    restarted = make_publisher()
    assert restarted.run_once() == 0
    db.make_due()  # TAKE_SEC 過了
    assert restarted.run_once() == 1
    assert queues.counts() == (2, 0)  # 同一筆服務兩則訊息
    worker = make_worker()
    assert worker.poll_once() == 1 and worker.poll_once() == 1
    assert worker.results == ["done", "duplicate"]
    [job] = analysis.jobs  # 只算一次
    assert db.results(sid) == [aq.run_name(sid, job.token, "transcript.txt")]  # 結果列沒有變多
    assert db.status(sid) == 3 and queues.counts() == (0, 0)


def test_a_row_another_publisher_is_taking_is_skipped_not_waited_for(pool, settings, db, queues, add_service,
                                                                     make_publisher):
    locked, free = add_service(1), add_service(1)
    for sid in (locked, free):
        aq.accept(pool, sid, settings)
    [event] = db.outbox(locked)
    other = db.connect()  # 另一個 API process 正在拿這一筆（它的交易還沒結束）
    try:
        with closing(other.cursor()) as cur:
            cur.execute("SELECT event_id FROM tbl_analysis_outbox WHERE event_id = %s FOR UPDATE", (event["event_id"],))
            cur.fetchall()
            started = time.monotonic()
            assert make_publisher().run_once() == 1  # 只送沒被鎖住的那一筆
            assert time.monotonic() - started < 5  # SKIP LOCKED：跳過，不等鎖（innodb_lock_wait_timeout 是 50 秒）
        other.rollback()
    finally:
        other.close()
    assert [body["service_id"] for body in queues.bodies()] == [free]
    assert make_publisher().run_once() == 1  # 鎖放開之後照樣送出
    assert [body["service_id"] for body in queues.bodies()] == [locked]


def test_two_publishers_running_together_send_each_row_once(pool, settings, db, queues, add_service, make_publisher,
                                                            new_pool, background):
    sids = [add_service(1) for _ in range(15)]
    for sid in sids:
        aq.accept(pool, sid, settings)
    ready = threading.Barrier(2)

    def drain(publisher):
        ready.wait(10)
        sent = 0
        while True:
            batch = publisher.run_once()
            if not batch:
                return sent
            sent += batch

    runs = [background(drain, make_publisher(pool=new_pool(2))) for _ in range(2)]
    assert sum(run.join() for run in runs) == 15
    assert sorted(body["service_id"] for body in queues.bodies()) == sorted(sids)  # 每一筆剛好一則
    assert {event["outbox_state"] for event in db.outbox()} == {"sent"}


def test_without_a_queue_url_nothing_is_sent_and_the_row_waits(pool, settings, db, queues, add_service,
                                                               make_publisher, caplog):
    sid = add_service(1)
    aq.accept(pool, sid, settings)
    unset = replace(settings, queue_url="")
    publisher = make_publisher(settings=unset)
    assert publisher.run_once() == 0
    publisher.start()  # 背景執行緒照樣跑（清理、提醒），log 說沒有設定
    wait_until(lambda: publisher.last_housekeeping is not None)
    publisher.stop(timeout=5)
    assert not publisher.thread.is_alive()
    assert "SQS_ANALYSIS_QUEUE_URL is not set: accepted analyses wait in the outbox until it is" in caplog.text
    [event] = db.outbox(sid)
    assert (event["outbox_state"], event["send_attempts"]) == ("pending", 0) and queues.counts() == (0, 0)
    assert aq.health(pool, unset)["sqs"] == "not_configured"
    assert make_publisher().run_once() == 1  # 設定好之後就送


def test_housekeeping_purges_old_rows_and_warns_about_stuck_jobs(pool, settings, db, add_service, make_publisher,
                                                                 caplog):
    day = 86400
    old_sent, recent_sent, waiting = (add_service(2) for _ in range(3))
    db.add_event(old_sent, "sent", created_sec_ago=8 * day, sent_sec_ago=8 * day)
    db.add_event(recent_sent, "sent", created_sec_ago=day, sent_sec_ago=day)
    db.add_event(waiting, "pending", created_sec_ago=8 * day)  # 很久還沒送出的不刪：還要送
    db.add_worker("gone", seen_sec_ago=8 * day)
    db.add_worker("recent", seen_sec_ago=day)
    stuck_queued, stuck_running, held, fresh, done = (add_service(2) for _ in range(5))
    db.add_job(stuck_queued, "queued", updated_sec_ago=2 * 3600)
    db.add_job(stuck_running, "running", attempts=1, lease="expired", updated_sec_ago=2 * 3600)
    db.add_job(held, "running", attempts=1, lease="valid", updated_sec_ago=2 * 3600)  # 有人拿著 lease：不算卡住
    db.add_job(fresh, "queued", updated_sec_ago=60)
    db.add_job(done, "done", attempts=1, updated_sec_ago=2 * 3600)
    make_publisher().housekeeping()
    assert sorted(event["service_id"] for event in db.outbox()) == sorted([recent_sent, waiting])
    assert db.worker("gone") is None and db.worker("recent") is not None
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any(text.startswith(f"job {stuck_queued} has been queued for 120 min with no worker holding it "
                               "(attempts 0)") for text in warnings)
    assert any(text.startswith(f"job {stuck_running} has been running for 120 min") for text in warnings)
    assert not [text for text in warnings for sid in (held, fresh, done) if sid in text]
    assert aq.health(pool, settings)["stale_jobs"] == 2


def test_housekeeping_fails_a_job_whose_last_attempt_died_after_sqs_gave_up_on_it(pool, settings, db, add_service,
                                                                                 make_publisher, caplog):
    """三次接收都耗在被 kill 的 worker 上：第 4 次接收 SQS 直接搬進 DLQ，claim() 沒機會把它標成失敗。
    送出器的例行檢查替它做（T49），不然服務會一直停在狀態 2；還有次數、或還有人拿著 lease 的不動。"""
    dead, retrying, held = (add_service(2) for _ in range(3))
    db.add_job(dead, "running", attempts=3, lease="expired", updated_sec_ago=600)
    db.add_job(retrying, "running", attempts=1, lease="expired", updated_sec_ago=600)  # 訊息之後還會再出現
    db.add_job(held, "running", attempts=3, lease="valid", updated_sec_ago=600)
    make_publisher().housekeeping()
    row = db.job(dead)
    assert (row["job_state"], row["lease_token"], row["last_error"]) == ("failed", None, aq.ABANDONED_ERROR)
    assert db.status(dead) == 1
    assert [db.job(sid)["job_state"] for sid in (retrying, held)] == ["running", "running"]
    assert [db.status(sid) for sid in (retrying, held)] == [2, 2]
    assert f"job {dead} failed: the worker stopped during the last attempt" in caplog.text
    assert aq.fail_if_abandoned(pool, held) is False and aq.fail_abandoned(pool) == []


def test_the_publisher_thread_sends_full_batches_back_to_back(pool, settings, db, queues, add_service,
                                                              make_publisher):
    sids = [add_service(1) for _ in range(12)]
    for sid in sids:
        aq.accept(pool, sid, settings)
    publisher = make_publisher(settings=replace(settings, publish_interval_sec=30)).start()
    # 第一批滿 10 筆：不等，馬上送下一批。要是等了 30 秒才送剩下的 2 筆，這裡就會逾時
    wait_until(lambda: {event["outbox_state"] for event in db.outbox()} == {"sent"})
    publisher.stop(timeout=5)
    assert not publisher.thread.is_alive()
    assert sorted(body["service_id"] for body in queues.bodies()) == sorted(sids)


# ---------- worker：認領、續期、寫完才刪除（教學第 6 節） ----------
def test_a_message_is_analysed_committed_once_and_deleted(db, queues, accepted, make_worker, analysis):
    sid = accepted()
    worker = make_worker()
    assert worker.poll_once() == 1 and worker.results == ["done"]
    [job] = analysis.jobs
    assert (job.service_id, job.attempt, job.receive_count) == (sid, 1, 1)
    assert re.fullmatch(r"[0-9a-f]{32}", job.token)
    assert os.path.basename(job.workdir).startswith(f"adam-job-{sid}-{aq.run_tag(job.token)}-")
    assert analysis.workdirs == [(job.workdir, True)] and not os.path.exists(job.workdir)  # 錄影檔做完一定刪
    row = db.job(sid)
    assert (row["job_state"], row["result_token"], row["attempts"]) == ("done", job.token, 1)
    assert row["report_data_key"] == aq.report_data_key(sid, job.token)
    assert row["lease_token"] is None and row["last_error"] is None and row["finished_at"] is not None
    assert db.status(sid) == 3
    assert db.results(sid) == [aq.run_name(sid, job.token, "transcript.txt")] and analysis.writes == [job.token]
    assert analysis.after == [(3, "done")]  # after_commit 在提交之後才跑
    assert queues.counts() == (0, 0)  # 訊息刪掉了
    assert db.worker(worker.worker_id)["worker_state"] == "ready"


def test_a_duplicate_message_after_done_is_deleted_without_another_run(db, queues, accepted, make_worker, analysis):
    sid = accepted()
    worker = make_worker()
    worker.poll_once()
    queues.send({"service_id": sid})  # Standard 佇列可能重複交付
    assert worker.poll_once() == 1 and worker.results == ["done", "duplicate"]
    assert len(analysis.jobs) == 1 and len(db.results(sid)) == 1
    assert queues.counts() == (0, 0)


def test_a_message_for_a_job_someone_else_holds_is_put_off_not_deleted(pool, settings, db, queues, accepted,
                                                                       make_worker, analysis):
    sid = accepted()
    token = aq.new_token()
    assert aq.claim(pool, sid, token, settings) == aq.Claim("claimed", attempt=1)  # 另一個 worker 正在算
    sqs = SqsSpy(queues.sqs)
    worker = make_worker(sqs=sqs)
    assert worker.poll_once() == 1 and worker.results == ["busy"]
    assert analysis.jobs == []
    [delay] = sqs.called("change_message_visibility")
    assert 320 <= delay["VisibilityTimeout"] <= 330  # 那個 lease 還剩的秒數＋30 秒之後再看
    assert queues.counts() == (0, 1)  # 沒刪，只是藏起來
    row = db.job(sid)
    assert (row["lease_token"], row["attempts"]) == (token, 1)  # 不算一次


def test_after_the_lease_expires_another_worker_takes_over_and_the_old_one_cannot_commit(
        pool, settings, db, queues, accepted, analyses, analysis, make_worker, new_pool, background):
    sid = accepted()
    slow = analyses()
    slow.gate.clear()
    first = make_worker(slow)
    run = background(first.poll_once)
    assert slow.started.wait(10)
    [old] = slow.jobs
    # 第一個 worker 被終止（或卡住）：lease 和 visibility 都到期了
    db.expire_lease(sid)
    queues.show(first.messages[0]["ReceiptHandle"])
    second = make_worker(pool=new_pool(3))
    assert second.poll_once() == 1 and second.results == ["done"]
    [new] = analysis.jobs
    assert (new.attempt, new.receive_count) == (2, 2) and new.token != old.token
    # 舊的 worker 回來了：它的 token 不能續期，也不能提交
    assert aq.extend(pool, sid, old.token, settings) is False
    with pytest.raises(aq.LeaseLost):
        aq.commit(pool, old, aq.Prepared(write=lambda cur: pytest.fail("the old worker wrote its result")))
    slow.gate.set()
    assert run.join() == 1 and first.results == ["lost"]
    assert slow.writes == []
    row = db.job(sid)
    assert (row["job_state"], row["result_token"], row["attempts"]) == ("done", new.token, 2)
    assert db.results(sid) == [aq.run_name(sid, new.token, "transcript.txt")]  # 只有接手者的輸出
    assert db.status(sid) == 3 and queues.counts() == (0, 0)


def test_a_worker_that_fails_after_losing_its_lease_leaves_the_new_holder_alone(pool, settings, db, accepted, analyses,
                                                                               make_worker, background):
    sid = accepted()
    slow = analyses()
    slow.gate.clear()
    slow.error = MemoryError("the model ran out of memory")
    first = make_worker(slow)
    run = background(first.poll_once)
    assert slow.started.wait(10)
    db.expire_lease(sid)
    token = aq.new_token()
    assert aq.claim(pool, sid, token, settings) == aq.Claim("claimed", attempt=2)  # 別的 worker 已經接手
    slow.gate.set()
    assert run.join() == 1 and first.results == ["retry"]  # 舊的那次失敗了，但交還不了別人的 lease
    row = db.job(sid)
    assert (row["job_state"], row["lease_token"], row["attempts"], row["last_error"]) == ("running", token, 2, None)
    assert aq.release(pool, sid, slow.jobs[0].token, "late") is None and db.status(sid) == 2


def test_a_failing_write_rolls_back_the_whole_commit(db, queues, accepted, make_worker, analysis):
    sid = accepted()
    analysis.write_error = RuntimeError("lost the connection while writing")
    worker = make_worker()
    assert worker.poll_once() == 1 and worker.results == ["retry"]
    [job] = analysis.jobs
    assert analysis.writes == [job.token]  # 寫了一列，然後失敗
    row = db.job(sid)
    assert (row["job_state"], row["attempts"]) == ("queued", 1)
    assert row["last_error"] == "RuntimeError: lost the connection while writing"
    assert all(row[name] is None for name in ("lease_token", "result_token", "report_data_key", "finished_at"))
    assert db.results(sid) == [] and db.status(sid) == 2  # 寫了的那一列也 rollback；還會再試，服務維持 2
    assert queues.counts() == (1, 0)  # 沒刪：retry_delay_sec（這裡是 0）之後再出現
    analysis.write_error = None
    assert worker.poll_once() == 1 and worker.results == ["retry", "done"]
    assert db.job(sid)["attempts"] == 2 and db.status(sid) == 3 and len(db.results(sid)) == 1


def test_three_failures_fail_the_job_and_the_message_ends_in_the_dlq(pool, settings, db, queues, accepted,
                                                                     make_worker, make_publisher, analysis, caplog):
    sid = accepted()
    analysis.error = ValueError("cannot read the video")
    worker = make_worker()
    for _ in range(3):
        assert worker.poll_once() == 1
    assert worker.results == ["retry", "retry", "failed"]
    assert [message["Attributes"]["ApproximateReceiveCount"] for message in worker.messages] == ["1", "2", "3"]
    assert [job.attempt for job in analysis.jobs] == [1, 2, 3]
    row = db.job(sid)
    assert (row["job_state"], row["attempts"]) == ("failed", 3)
    assert row["last_error"] == "ValueError: cannot read the video"
    assert row["finished_at"] is not None and row["lease_token"] is None
    assert db.status(sid) == 1  # 桌面端顯示失敗、可以 Retry
    assert f"job {sid} failed for good after its last attempt: service status is back to 1" in caplog.text
    # 第 4 次接收：SQS 依 maxReceiveCount 把訊息移到 DLQ
    assert worker.poll_once() == 0
    assert queues.counts() == (0, 0)
    assert [body["service_id"] for body in queues.bodies(queues.dlq_url)] == [sid]
    # 桌面端的 Retry：新的一輪（次數重算），修好之後就完成
    analysis.error = None
    assert aq.accept(pool, sid, settings) == queued(sid)
    assert db.job(sid)["attempts"] == 0 and db.status(sid) == 2
    assert make_publisher().run_once() == 1
    assert worker.poll_once() == 1 and worker.results[-1] == "done"
    assert analysis.jobs[-1].attempt == 1 and db.status(sid) == 3


def test_messages_for_a_failed_job_are_left_for_the_dlq(db, queues, add_service, make_worker, analysis):
    sid = add_service(1)
    db.add_job(sid, "failed", attempts=3, last_error="ValueError: cannot read the video")
    queues.send({"service_id": sid})  # 上一輪留下的重複訊息
    worker = make_worker()
    for _ in range(3):
        assert worker.poll_once() == 1
    assert worker.results == ["failed"] * 3 and analysis.jobs == []  # 已經結束的一輪不會重新開跑
    assert worker.poll_once() == 0 and queues.counts() == (0, 0)
    assert [body["service_id"] for body in queues.bodies(queues.dlq_url)] == [sid]
    assert db.job(sid)["job_state"] == "failed" and db.status(sid) == 1


def test_messages_that_match_no_job_are_left_for_the_dlq(db, queues, add_service, new_sid, make_worker, analysis):
    no_job = add_service(2)  # 狀態 2、沒有工作（SQS 之前就接受的）
    bodies = ["not json", json.dumps({"service_id": "S1"}), json.dumps({"service_id": new_sid()}),
              json.dumps({"service_id": no_job})]
    for body in bodies:
        queues.send(body)
    worker = make_worker()
    for _ in range(20):
        if not worker.poll_once():
            break
    assert sorted(worker.results) == ["invalid"] * 6 + ["missing"] * 6  # 每一則都領了 3 次，沒有當作成功刪掉
    assert queues.counts() == (0, 0)
    assert sorted(message["Body"] for message in queues.take_all(queues.dlq_url)) == sorted(bodies)
    assert analysis.jobs == [] and db.job(no_job) is None and db.status(no_job) == 2


def test_a_database_outage_while_claiming_keeps_the_message(pool, db, queues, accepted, make_worker, analysis,
                                                            monkeypatch, caplog):
    sid = accepted()
    monkeypatch.setattr(aq.Worker, "DB_RETRY_SEC", 0)
    worker = make_worker(pool=FlakyPool(pool, aq.LOCK_SERVICE_SQL))
    assert worker.poll_once() == 1 and worker.results == ["error"]
    assert queues.counts() == (0, 1)  # visibility 不動：到期之後再出現
    assert db.job(sid)["attempts"] == 0 and analysis.jobs == []
    assert f"job {sid}: claiming failed, waiting 0 s: OperationalError" in caplog.text


def test_a_worker_that_died_on_its_last_attempt_fails_the_job(pool, settings, db, queues, accepted, make_worker,
                                                              analysis):
    sid = accepted()
    for _ in range(3):  # 每一次都在算到一半時被 kill（OOM）：沒有交還，lease 到期
        assert aq.claim(pool, sid, aq.new_token(), settings).result == "claimed"
        db.expire_lease(sid)
    worker = make_worker()
    assert worker.poll_once() == 1 and worker.results == ["failed"]
    row = db.job(sid)
    assert (row["job_state"], row["attempts"], row["lease_token"]) == ("failed", 3, None)
    assert row["last_error"] == "the worker stopped during the last attempt (lease expired)"
    assert db.status(sid) == 1 and analysis.jobs == []
    assert queues.counts() == (1, 0)  # 留給 DLQ


def test_a_crash_between_commit_and_delete_is_cleaned_up_by_the_next_receive(db, queues, accepted, make_worker,
                                                                             analysis):
    sid = accepted()
    sqs = SqsSpy(queues.sqs)
    sqs.fail["delete_message"] = Crash()  # 提交之後、刪除之前 process 不見了
    worker = make_worker(sqs=sqs)
    with pytest.raises(Crash):
        worker.poll_once()
    assert db.job(sid)["job_state"] == "done" and db.status(sid) == 3
    assert queues.counts() == (0, 1)  # 訊息還在
    queues.show(worker.messages[0]["ReceiptHandle"])  # visibility 到期
    again = make_worker()
    assert again.poll_once() == 1 and again.results == ["duplicate"]
    assert len(analysis.jobs) == 1 and len(db.results(sid)) == 1 and queues.counts() == (0, 0)


def test_a_failed_delete_is_logged_and_the_next_receive_deletes_it(db, queues, accepted, make_worker, analysis,
                                                                   caplog):
    sid = accepted()
    sqs = SqsSpy(queues.sqs)
    sqs.fail["delete_message"] = ClientError({"Error": {"Code": "InternalError", "Message": "try again"}},
                                             "DeleteMessage")
    worker = make_worker(sqs=sqs)
    assert worker.poll_once() == 1 and worker.results == ["done"]
    assert "deleting a message failed (it will come back and be deleted then): ClientError" in caplog.text
    assert queues.counts() == (0, 1)
    del sqs.fail["delete_message"]
    queues.show(worker.messages[0]["ReceiptHandle"])
    assert worker.poll_once() == 1 and worker.results == ["done", "duplicate"]
    assert queues.counts() == (0, 0) and len(analysis.jobs) == 1 and db.status(sid) == 3


def test_two_workers_never_hold_a_valid_lease_at_the_same_time(db, queues, accepted, analyses, analysis, make_worker,
                                                               new_pool, background):
    sid = accepted()
    queues.send({"service_id": sid})  # outbox 重送：同一筆服務兩則訊息
    slow = analyses()
    slow.gate.clear()
    first = make_worker(slow, pool=new_pool(3))
    second = make_worker(pool=new_pool(3))
    run = background(first.poll_once)
    assert slow.started.wait(10)
    assert second.poll_once() == 1 and second.results == ["busy"]  # 另一則到了第二個 worker：不開始算
    [old] = slow.jobs
    held = db.query("SELECT service_id, lease_token FROM tbl_analysis_job WHERE lease_until > UTC_TIMESTAMP(3)")
    assert held == [(sid, old.token)]  # 有效的 lease 只有一個
    slow.gate.set()
    assert run.join() == 1 and first.results == ["done"]
    queues.show(second.messages[0]["ReceiptHandle"])  # 延後的那一則再出現：已經完成，刪掉
    assert second.poll_once() == 1 and second.results == ["busy", "duplicate"]
    assert analysis.jobs == [] and len(db.results(sid)) == 1 and queues.counts() == (0, 0)


def test_concurrent_claims_give_the_lease_to_exactly_one_worker(settings, db, accepted, new_pool, background):
    sid = accepted()
    pool = new_pool(8)
    ready = threading.Barrier(8)
    tokens = [aq.new_token() for _ in range(8)]

    def try_claim(token):
        ready.wait(10)
        return aq.claim(pool, sid, token, settings)

    claims = [run.join() for run in [background(try_claim, token) for token in tokens]]
    assert sorted(claim.result for claim in claims) == ["busy"] * 7 + ["claimed"]
    winner = tokens[[claim.result for claim in claims].index("claimed")]
    row = db.job(sid)
    assert (row["lease_token"], row["attempts"]) == (winner, 1)


def test_the_heartbeat_extends_the_visibility_and_the_lease_while_the_job_runs(settings, db, queues, accepted,
                                                                               analyses, make_worker, background):
    sid = accepted()
    sqs = SqsSpy(queues.sqs)
    slow = analyses()
    slow.gate.clear()
    worker = make_worker(slow, sqs=sqs, settings=replace(settings, heartbeat_sec=0.2))
    run = background(worker.poll_once)
    assert slow.started.wait(10)
    receipt = worker.messages[0]["ReceiptHandle"]
    # 快到期了：lease 剩 2 秒、visibility 剩 1 秒（真的要等 5 分鐘、15 分鐘）
    db.query("UPDATE tbl_analysis_job SET lease_until = UTC_TIMESTAMP(3) + INTERVAL 2 SECOND WHERE service_id = %s", sid)
    queues.sqs.change_message_visibility(QueueUrl=queues.url, ReceiptHandle=receipt, VisibilityTimeout=1)
    before = len(sqs.called("change_message_visibility"))
    wait_until(lambda: len(sqs.called("change_message_visibility")) >= before + 2 and db.lease(sid)[1] > 250)
    time.sleep(1.2)  # 原本只剩 1 秒的 visibility 早就過了
    assert queues.counts() == (0, 1)  # 續期了：別的 worker 還領不到
    assert all(call == {"QueueUrl": queues.url, "ReceiptHandle": receipt, "VisibilityTimeout": 900}
               for call in sqs.called("change_message_visibility"))
    valid, left = db.lease(sid)
    assert valid and left > 250  # RDS 的 lease 也續回 lease_sec
    seen = db.worker(worker.worker_id)
    assert (seen["worker_state"], seen["service_id"]) == ("busy", sid) and seen["seen_ago"] < 2
    slow.gate.set()
    assert run.join() == 1 and worker.results == ["done"]
    renewals = len(sqs.called("change_message_visibility"))
    time.sleep(0.5)
    assert len(sqs.called("change_message_visibility")) == renewals  # 提交前就停了，不再續期


def test_the_heartbeat_stops_after_job_timeout_sec_and_the_late_result_is_discarded(
        settings, db, queues, accepted, analyses, analysis, make_worker, background, caplog):
    sid = accepted()
    sqs = SqsSpy(queues.sqs)
    slow = analyses()
    slow.gate.clear()
    worker = make_worker(slow, sqs=sqs, settings=replace(settings, heartbeat_sec=0.05, job_timeout_sec=1))
    run = background(worker.poll_once)
    assert slow.started.wait(10)
    wait_until(lambda: f"job {sid} ran longer than 1 s: no more renewals, its lease will expire" in caplog.text)
    renewals = len(sqs.called("change_message_visibility"))
    assert renewals >= 3  # 時限之前一直續期
    time.sleep(0.3)
    assert len(sqs.called("change_message_visibility")) == renewals  # 之後不再續期（SQS 單次接收最多 12 小時）
    db.expire_lease(sid)  # 沒有續期，lease 自己到期（真的要等 lease_sec）
    slow.gate.set()
    assert run.join() == 1 and worker.results == ["lost"]  # 太晚做完的結果提交不了
    assert db.job(sid)["job_state"] == "running" and db.results(sid) == [] and db.status(sid) == 2
    # visibility 到期之後別的 worker 接手，算這一輪的第 2 次
    queues.show(worker.messages[0]["ReceiptHandle"])
    again = make_worker()
    assert again.poll_once() == 1 and again.results == ["done"] and analysis.jobs[0].attempt == 2


def test_a_graceful_stop_gives_the_job_back_without_counting_the_attempt(db, queues, accepted, analyses, analysis,
                                                                          make_worker, background, monkeypatch,
                                                                          caplog):
    sid = accepted()
    monkeypatch.setattr(logging, "shutdown", lambda: None)  # 真的 shutdown 會關掉所有 log handler（包括 pytest 的）
    slow = analyses()
    slow.gate.clear()
    worker = make_worker(slow)
    run = background(worker.poll_once)
    assert slow.started.wait(10)
    [job] = slow.jobs
    worker.stop_requested(grace_sec=0.1)  # SIGTERM：模型還在算，等不到它做完
    wait_until(lambda: worker.exits == [0])
    row = db.job(sid)
    assert (row["job_state"], row["attempts"], row["lease_token"]) == ("queued", 0, None)  # 這一次不算
    assert row["last_error"] == "the worker was stopped before it finished (not counted)"
    assert db.status(sid) == 2 and not os.path.exists(job.workdir)
    assert db.worker(worker.worker_id)["worker_state"] == "stopped"
    assert queues.counts() == (1, 0)  # 訊息馬上還回去，別的 worker 可以領
    assert f"job {sid}: stopping before it finished; giving it back for another worker" in caplog.text
    # 真的 process 這時已經結束（os._exit）。這裡的替身沒有結束：讓它算完，也提交不了
    slow.gate.set()
    assert run.join() == 1 and worker.results == ["lost"] and slow.writes == []
    other = make_worker()
    assert other.poll_once() == 1 and other.results == ["done"] and analysis.jobs[0].attempt == 1


def test_a_stopping_worker_hands_messages_straight_back(db, queues, accepted, make_worker, analysis):
    sid = accepted()
    worker = make_worker()
    worker.stop_requested()  # 沒有正在做的：只是不再領新的
    assert worker.poll_once() == 1 and worker.results == ["returned"]
    assert queues.counts() == (1, 0) and analysis.jobs == [] and db.job(sid)["attempts"] == 0
    worker.run()  # 已經要停了：登記一下就結束
    assert db.worker(worker.worker_id)["worker_state"] == "stopped" and worker.exits == []


def test_worker_reports_keep_one_row_per_worker(db, new_sid, make_worker):
    worker = make_worker()
    worker.report("loading", detail="whisper medium")
    loading = db.worker(worker.worker_id)
    worker.report("ready")
    ready = db.worker(worker.worker_id)
    assert (ready["worker_state"], ready["detail"]) == ("ready", "whisper medium")  # detail 沿用
    assert ready["started_at"] == loading["started_at"]
    sid = new_sid()
    worker.report("busy", sid)
    assert db.worker(worker.worker_id)["service_id"] == sid
    worker.report("ready")
    assert db.worker(worker.worker_id)["service_id"] is None
    time.sleep(0.01)
    worker.report("loading")  # 重新載入模型：started_at 重算
    reloading = db.worker(worker.worker_id)["started_at"]
    assert reloading > loading["started_at"]
    time.sleep(0.01)
    worker.report("loading")  # 載入中再報一次（serve() 每 heartbeat_sec 秒）：還是開始載入的時間
    assert db.worker(worker.worker_id)["started_at"] == reloading
    assert db.query("SELECT COUNT(*) FROM tbl_analysis_worker")[0][0] == 1
    with open(aq.ALIVE_FILE, encoding="ascii") as alive:
        assert alive.read() == "loading"
    assert aq.main(["alive"]) == 0  # worker 容器的 HEALTHCHECK


def test_serve_cleans_up_registers_works_and_stops_on_sigterm(pool, settings, db, queues, accepted, analysis,
                                                              tmp_path, monkeypatch, restore_signals):
    leftover = tmp_path / "adam-job-S20990000000000-0123456789ab-x1"  # 上一次被 kill 的 worker 留下的錄影
    leftover.mkdir()
    (leftover / "cam0.avi").write_bytes(b"recording")
    keep = tmp_path / "not-a-job"
    keep.mkdir()
    sid = accepted()
    monkeypatch.setattr(aq, "Worker", SafeWorker)
    SafeWorker.created.clear()
    sqs = SqsSpy(queues.sqs)
    sqs.before["receive_message"] = lambda: len(sqs.called("receive_message")) == 2 and \
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # 做完一筆、回去等下一則時收到 SIGTERM
    registered, errors = [], []
    load = recording_errors(lambda: registered.extend(db.query("SELECT worker_state, detail FROM tbl_analysis_worker")),
                            errors)
    assert aq.serve(pool, analysis, load_models=load, detail="fake models", settings=settings, sqs=sqs) == 0
    assert errors == [] and registered == [("loading", "fake models")]  # 先登記 loading，才載入模型
    assert not leftover.exists() and keep.exists()
    assert db.job(sid)["job_state"] == "done" and db.status(sid) == 3 and queues.counts() == (0, 0)
    [worker] = SafeWorker.created
    assert worker.exits == [] and worker.stopping.is_set()
    assert db.query("SELECT worker_id, worker_state, detail FROM tbl_analysis_worker") == [
        (worker.worker_id, "stopped", "fake models")]


def test_serve_keeps_the_worker_fresh_while_the_models_load(pool, settings, db, queues, monkeypatch, restore_signals):
    monkeypatch.setattr(aq, "Worker", SafeWorker)
    SafeWorker.created.clear()
    quick = replace(settings, heartbeat_sec=0.1, worker_stale_sec=1)
    sqs = SqsSpy(queues.sqs)
    sqs.before["receive_message"] = lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # 載完就結束
    seen, errors = [], []

    def load_models():  # 比 worker_stale_sec 還久（真的是 EC2 剛開機、兩個 worker 同時載入的好幾分鐘）
        worker_id = SafeWorker.created[-1].worker_id
        first = db.worker(worker_id)
        time.sleep(1.5)
        seen.append((first, db.worker(worker_id), aq.health(pool, quick)["models"], aq.main(["alive"])))

    assert aq.serve(pool, lambda job: pytest.fail("no job was queued"), load_models=recording_errors(load_models, errors),
                    detail="fake models", settings=quick, sqs=sqs) == 0
    assert errors == []
    [(first, during, models, alive)] = seen
    assert (during["worker_state"], during["detail"]) == ("loading", "fake models")
    assert during["seen_ago"] < 0.5 and models == "loading" and alive == 0  # /healthz、HEALTHCHECK 都知道它還在載入
    assert during["started_at"] == first["started_at"]  # 開始載入的時間不因為再報一次而變


# ---------- /healthz ----------
def test_health_counts_workers_jobs_and_the_outbox(pool, settings, db, add_service):
    for worker_id, state in [("w-ready-1", "ready"), ("w-ready-2", "ready"), ("w-busy", "busy"),
                             ("w-loading", "loading"), ("w-failed", "failed"), ("w-stopped", "stopped")]:
        db.add_worker(worker_id, state)
    db.add_worker("w-gone", "ready", seen_sec_ago=settings.worker_stale_sec + 60)  # 很久沒出現：不算
    queued_job, running, stuck = add_service(2), add_service(2), add_service(2)
    db.add_job(queued_job, "queued")
    db.add_job(running, "running", attempts=1, lease="valid")
    db.add_job(stuck, "running", attempts=1, lease="expired", updated_sec_ago=settings.stale_job_sec + 60)
    db.add_job(add_service(3), "done", attempts=1)
    db.add_job(add_service(1), "failed", attempts=3)
    db.add_event(queued_job, "pending", created_sec_ago=90)
    db.add_event(running, "pending", created_sec_ago=10)
    db.add_event(stuck, "sent", created_sec_ago=600, sent_sec_ago=600)
    health = aq.health(pool, settings)
    assert 90 <= health.pop("oldest_pending_sec") <= 92
    assert health == {"models": "ready", "workers": {"loading": 1, "ready": 2, "busy": 1, "failed": 1},
                      "queue_depth": 3, "jobs": {"queued": 1, "running": 2}, "outbox_pending": 2, "stale_jobs": 1,
                      "sqs": "configured"}


def test_models_comes_from_the_workers_seen_lately(pool, settings, db):
    def models():
        return aq.health(pool, settings)["models"]

    empty = aq.health(pool, settings)
    assert (empty["models"], empty["queue_depth"], empty["outbox_pending"], empty["oldest_pending_sec"],
            empty["stale_jobs"]) == ("failed", 0, 0, 0, 0)  # 沒有 worker 活著：收得到工作，但不能分析
    db.add_worker("w-failed", "failed")
    assert models() == "failed"
    db.add_worker("w-loading", "loading")
    assert models() == "loading"
    db.add_worker("w-old", "ready", seen_sec_ago=settings.worker_stale_sec + 1)
    assert models() == "loading"
    db.add_worker("w-busy", "busy")
    assert models() == "ready"


# ---------- 管理指令（教學第 8 節） ----------
def test_admin_fail_marks_only_jobs_nobody_holds(pool, db, new_sid, add_service):
    assert aq.admin_fail(pool, new_sid(), "x") == "no such service"
    assert aq.admin_fail(pool, add_service(2), "x") == "no analysis job for this service"
    done = add_service(3)
    db.add_job(done, "done", attempts=1)
    assert aq.admin_fail(pool, done, "x") == "already done: nothing to fail"
    held = add_service(2)
    db.add_job(held, "running", attempts=1, lease="valid")
    assert re.fullmatch(r"a worker holds its lease for (29\d|300) more seconds: check the worker first",
                        aq.admin_fail(pool, held, "x"))
    assert db.job(held)["job_state"] == "running" and db.status(held) == 2
    for state, lease in [("queued", None), ("running", "expired"), ("failed", None)]:
        sid = add_service(2)
        db.add_job(sid, state, attempts=1, lease=lease)
        assert aq.admin_fail(pool, sid, "the recording is corrupt") == (
            "ok: job failed, service status back to 1 (the desktop shows it as failed with Retry)")
        row = db.job(sid)
        assert (row["job_state"], row["lease_token"]) == ("failed", None) and row["finished_at"] is not None
        assert row["last_error"] == "failed by an administrator: the recording is corrupt"
        assert db.status(sid) == 1


def test_admin_requeue_starts_a_new_round(pool, settings, db, new_sid, add_service, make_publisher, make_worker):
    assert aq.admin_requeue(pool, new_sid(), settings) == "no such service"
    assert aq.admin_requeue(pool, add_service(3), settings) == "already completed (status 3): not requeued"
    done = add_service(2)
    db.add_job(done, "done", attempts=1)
    assert aq.admin_requeue(pool, done, settings) == "already done: not requeued"
    held = add_service(2)
    db.add_job(held, "running", attempts=1, lease="valid")
    assert re.fullmatch(r"a worker holds its lease for (29\d|300) more seconds: not requeued",
                        aq.admin_requeue(pool, held, settings))
    assert db.job(held)["lease_token"] is not None and db.outbox() == []
    legacy = add_service(2)  # 搬遷前就卡在 2、沒有工作
    failed = add_service(1)
    db.add_job(failed, "failed", attempts=3, last_error="ValueError: cannot read the video")
    stranded = add_service(2)  # 最後一次的 worker 被 kill、訊息已經進了 DLQ：沒有人會再認領它
    db.add_job(stranded, "running", attempts=3, lease="expired")
    for sid in (legacy, failed, stranded):
        assert aq.admin_requeue(pool, sid, settings) == "ok: queued again (the outbox sends it to SQS within a few seconds)"
        row = db.job(sid)
        assert (row["job_state"], row["attempts"], row["max_attempts"]) == ("queued", 0, settings.max_attempts)
        assert row["lease_token"] is None and db.status(sid) == 2
        assert [event["outbox_state"] for event in db.outbox(sid)] == ["pending"]
    assert make_publisher().run_once() == 3
    worker = make_worker()
    for _ in range(3):
        worker.poll_once()
    assert worker.results == ["done"] * 3 and [db.status(sid) for sid in (legacy, failed, stranded)] == [3, 3, 3]


def test_print_status_lists_workers_jobs_and_legacy_services(pool, settings, db, add_service, make_worker, capsys):
    busy = add_service(2)
    db.add_job(busy, "running", attempts=1, lease="valid")
    worker = make_worker()
    worker.report("busy", busy, detail="whisper medium")
    failed = add_service(1)
    db.add_job(failed, "failed", attempts=3, last_error="ValueError: cannot read the video")
    legacy = add_service(2)
    db.add_job(add_service(3), "done", attempts=1)  # 完成的不列
    aq.print_status(pool, settings)
    health, rest = capsys.readouterr().out.split("\nworkers (seen in the last day):\n")
    assert json.loads(health)["jobs"] == {"queued": 0, "running": 1}
    assert re.search(rf"{worker.worker_id}\s+busy\s+{busy}\s+seen \d+ s ago  whisper medium", rest)
    jobs, legacy_part = rest.split("status 2 with no analysis job (accepted before the SQS queue; `requeue` them):")
    assert re.search(rf"{busy} running  attempt 1/3 lease held idle \d+ min", jobs)
    assert re.search(rf"{failed} failed   attempt 3/3 no lease   idle \d+ min  ValueError: cannot read the video", jobs)
    assert f"  {legacy}\n" in legacy_part and busy not in legacy_part


def test_check_sqs_compares_the_queue_with_the_plan(queues, settings):
    lines, ok = aq.check_sqs(queues.sqs, settings)
    assert ok and lines == [
        "QueueArn: arn:aws:sqs:us-east-1:123456789012:adam-analysis",
        "OK   VisibilityTimeout: 900 (expected 900)",
        "OK   ReceiveMessageWaitTimeSeconds: 20 (expected 20)",
        "OK   MessageRetentionPeriod: 345600 (expected 345600)",
        "OK   RedrivePolicy.maxReceiveCount: 3 (expected >= 3)",
        "OK   RedrivePolicy.deadLetterTargetArn: arn:aws:sqs:us-east-1:123456789012:adam-analysis-dlq "
        "(expected (a DLQ))",
        "messages: 0 waiting, 0 in flight"]
    # 照預設值建立、沒有 DLQ 的佇列：每一項不同都標出來
    plain = queues.sqs.create_queue(QueueName="adam-analysis-plain", Attributes={"VisibilityTimeout": "30"})["QueueUrl"]
    queues.sqs.send_message(QueueUrl=plain, MessageBody="x")
    lines, ok = aq.check_sqs(queues.sqs, replace(settings, queue_url=plain))
    assert not ok and lines[1:] == [
        "DIFF VisibilityTimeout: 30 (expected 900)",
        "DIFF ReceiveMessageWaitTimeSeconds: 0 (expected 20)",
        "OK   MessageRetentionPeriod: 345600 (expected 345600)",
        "DIFF RedrivePolicy.maxReceiveCount:  (expected >= 3)",
        "DIFF RedrivePolicy.deadLetterTargetArn:  (expected (a DLQ))",
        "messages: 1 waiting, 0 in flight"]


def test_the_command_line_runs_the_admin_commands_against_the_database(db, queues, new_sid, add_service, monkeypatch,
                                                                       capsys):
    monkeypatch.setenv("SQS_ANALYSIS_QUEUE_URL", queues.url)
    for variable in aq.Settings.ENV.values():
        monkeypatch.delenv(variable, raising=False)
    sid = add_service(2)
    db.add_job(sid, "queued")
    assert aq.main(["status"]) == 0  # 用 make_pool()：DB_* 的環境變數、最小權限的帳號
    assert sid in capsys.readouterr().out
    assert aq.main(["fail", sid, "--reason", "corrupt video"]) == 0
    assert capsys.readouterr().out.startswith("ok: job failed")
    assert db.job(sid)["last_error"] == "failed by an administrator: corrupt video" and db.status(sid) == 1
    assert aq.main(["requeue", sid]) == 0
    assert capsys.readouterr().out.startswith("ok: queued again")
    assert db.job(sid)["job_state"] == "queued" and db.status(sid) == 2
    assert aq.main(["fail", sid]) == 0 and db.job(sid)["last_error"] == "failed by an administrator: no retry"
    capsys.readouterr()
    assert aq.main(["requeue", new_sid()]) == 1 and capsys.readouterr().out == "no such service\n"
    assert aq.main(["fail", "S123"]) == 2
    assert capsys.readouterr().out == "service_id must be S followed by 14 digits\n"
    assert aq.main(["check-sqs"]) == 0  # make_sqs_client()：moto 的佇列
    assert "OK   RedrivePolicy.maxReceiveCount: 3 (expected >= 3)" in capsys.readouterr().out.splitlines()
