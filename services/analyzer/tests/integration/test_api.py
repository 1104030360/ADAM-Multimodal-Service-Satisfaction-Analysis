# tests/integration/test_api.py — POST /analyze 的驗證與狀態碼（docs/rebuild/analysis_api.yaml；SQS：sqs/PLAN.md 第 3 節）
#   200 的意思是「已經持久接受」：工作、outbox、狀態 2 在同一個交易裡 commit 了；還沒分析，也不保證已經送到 SQS。
import json
import os

import mysql.connector
import pytest

pytestmark = pytest.mark.integration
SID = "S20261030120000"


@pytest.mark.parametrize("body", [
    "not json",  # body 不是 JSON：當成空物件
    [SID],  # 是 JSON，但不是物件
    {},  # 沒有 service_id
    {"service_id": 20261030120000},  # 不是字串
    {"service_id": "S2026103012000"},  # 只有 13 位數字
    {"service_id": f"{SID}\n"},  # 結尾多一個換行
    {"service_id": "S" + "٢" * 14},  # 非 ASCII 的數字
], ids=["not-json", "not-object", "missing", "not-string", "bad-format", "trailing-newline", "non-ascii-digits"])
def test_invalid_service_id_is_rejected(api, body):
    if isinstance(body, str):
        r = api.post("/analyze", data=body, content_type="application/json")
    else:
        r = api.post("/analyze", json=body)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_service_id"


@pytest.mark.parametrize("force", [True, 1, "yes"])
def test_force_is_rejected_and_nothing_is_written(api, db, force):
    """教學 5-1：訊息只帶 service_id，分不出重跑的新一輪和舊訊息，這一版不支援重跑已完成的服務。"""
    db.add_service(SID, 3)
    r = api.post("/analyze", json={"service_id": SID, "force": force})
    assert (r.status_code, r.get_json()) == (400, {"error": "force_not_supported"})
    assert (db.status(SID), db.job(SID), db.outbox(SID)) == (3, None, [])


@pytest.mark.parametrize("force", [False, 0, None])
def test_a_false_force_is_an_ordinary_request(api, db, force):
    db.add_service(SID, 1)
    r = api.post("/analyze", json={"service_id": SID, "force": force})
    assert (r.status_code, r.get_json()["state"]) == (200, "queued")


@pytest.mark.parametrize("stored_status, code, error", [
    (None, 404, "service_not_found"),  # tbl_service 沒有這一筆
    (3, 409, "already_completed"),  # 已經完成：回 200 的話，桌面端會以為又在分析了
])
def test_unknown_or_completed_service_is_not_queued(api, db, service, stored_status, code, error):
    if stored_status is not None:
        db.add_service(SID, stored_status)
    r = api.post("/analyze", json={"service_id": SID})
    assert (r.status_code, r.get_json()) == (code, {"error": error})
    assert service.publisher.run_once() == 0  # outbox 沒有東西要送
    if stored_status is not None:
        assert (db.status(SID), db.job(SID)) == (3, None)


def test_accepting_commits_the_job_the_outbox_and_status_2(api, db, service, queues):
    db.add_service(SID, 1)  # 桌面端收到 200 之後不再自己寫 2（教學 5-1）
    r = api.post("/analyze", json={"service_id": SID})
    assert (r.status_code, r.get_json()) == (200, {"service_id": SID, "accepted": True, "state": "queued"})
    assert db.status(SID) == 2  # 回 200 之前就 commit 了
    assert db.job(SID) == {"job_state": "queued", "attempts": 0, "max_attempts": 3, "lease_token": None,
                           "result_token": None, "report_data_key": None, "last_error": None}
    assert db.outbox(SID) == [{"outbox_state": "pending", "send_attempts": 0, "sqs_message_id": None}]
    assert queues.count() == (0, 0)  # 還沒送到 SQS：送出器在背景送

    assert service.publisher.run_once() == 1
    message = queues.receive()
    body = json.loads(message["Body"])
    assert body["service_id"] == SID and len(body["event_id"]) == 32
    assert db.outbox(SID) == [{"outbox_state": "sent", "send_attempts": 1, "sqs_message_id": message["MessageId"]}]


def test_a_failure_inside_the_transaction_writes_nothing(api, db):
    """工作、outbox、狀態 2 是同一個交易：寫 outbox 失敗時，工作和狀態也不會留下來，桌面端收到 503 可以再按一次。"""
    db.add_service(SID, 1)
    user = f"'{os.environ['DB_USER']}'@'%'"
    db.query(f"REVOKE INSERT ON adam.tbl_analysis_outbox FROM {user}")
    try:
        r = api.post("/analyze", json={"service_id": SID})
    finally:
        db.query(f"GRANT INSERT ON adam.tbl_analysis_outbox TO {user}")
    assert (r.status_code, r.get_json()) == (503, {"error": "db_unavailable"})
    assert (db.status(SID), db.job(SID), db.outbox(SID)) == (1, None, [])


def test_database_error_returns_503(api, service, monkeypatch):
    def pool_exhausted():
        raise mysql.connector.errors.PoolError("Failed getting connection; pool exhausted")

    monkeypatch.setattr(service.pool, "get_connection", pool_exhausted)
    r = api.post("/analyze", json={"service_id": SID})
    assert (r.status_code, r.get_json()) == (503, {"error": "db_unavailable"})


def test_repeated_request_is_accepted_once(api, db, add_recording, fake_analysis, drain):
    db.add_service(SID, 1)
    add_recording(SID)
    first = api.post("/analyze", json={"service_id": SID})
    second = api.post("/analyze", json={"service_id": SID})  # 例如連按兩次「分析」
    assert first.status_code == second.status_code == 200  # 桌面端只認 200
    assert first.get_json()["state"] == "queued"
    assert second.get_json() == {"service_id": SID, "accepted": True, "state": "already_queued"}
    assert len(db.outbox(SID)) == 1  # 重複的請求不再送一則
    drain()
    assert len(fake_analysis.calls) == 1 and db.status(SID) == 3


def test_request_for_a_running_job_is_not_queued_again(api, db, service, add_recording, fake_analysis, worker,
                                                      drain, background):
    db.add_service(SID, 1)
    add_recording(SID)
    api.post("/analyze", json={"service_id": SID})
    service.publisher.run_once()
    fake_analysis.release.clear()  # worker 停在分析這一步
    job = background(worker.poll_once, 0)
    assert fake_analysis.started.wait(5)
    second = api.post("/analyze", json={"service_id": SID})
    health = api.get("/healthz").get_json()
    fake_analysis.release.set()
    job.join(10)

    assert second.get_json()["state"] == "already_queued"
    assert (health["queue_depth"], health["jobs"], health["workers"]["busy"]) == (1, {"queued": 0, "running": 1}, 1)
    assert drain() == 0  # 沒有多送一則
    assert len(fake_analysis.calls) == 1 and db.status(SID) == 3  # 只分析一次（一筆要在 CPU 上分析好幾分鐘）


def test_completed_analysis_is_not_run_again(api, db, add_recording, fake_analysis, drain):
    db.add_service(SID, 1)
    add_recording(SID)
    api.post("/analyze", json={"service_id": SID})
    drain()
    r = api.post("/analyze", json={"service_id": SID})
    assert (r.status_code, r.get_json()) == (409, {"error": "already_completed"})
    assert drain() == 0 and len(fake_analysis.calls) == 1 and len(db.outbox(SID)) == 1
