# tests/integration/test_fake_api.py — 假服務的 API（app.py）在 SQS 之後的行為（docs/rebuild/sqs/PLAN.md 第 3 節、T49）：
# POST /analyze 只接受工作（工作＋outbox＋狀態 1 → 2，一個交易），送到 SQS 的是 API 裡的送出器，分析在 worker；
# /healthz 從 RDS 讀 worker 的心跳、工作數與 outbox。只有第 0 階段假服務有這份；移植版的在 analyzer/tests/。
from datetime import datetime

import mysql.connector
import pytest

from fake_helpers import add_recording, job
from report_helpers import add_scored_service

pytestmark = pytest.mark.integration
SID = "S20261102090000"
NO_WORKERS = {"loading": 0, "ready": 0, "busy": 0, "failed": 0}


class BrokenPool:
    """連不上資料庫的連線池（RDS 重新開機、網路斷了）。"""

    def get_connection(self):
        raise mysql.connector.errors.InterfaceError("2003: Can't connect to MySQL server")


def add_service(db, sid=SID, status=1):
    add_scored_service(db, sid, datetime(2026, 11, 2, 9), status=status, no_scores=True)


def outbox(db, sid=SID):
    return db.query("SELECT outbox_state, sqs_message_id IS NOT NULL FROM tbl_analysis_outbox WHERE service_id = %s",
                    sid)


def healthz(api):
    response = api.get("/healthz")
    return response.status_code, response.get_json()


# ---------- POST /analyze ----------
def test_analyze_accepts_the_job_and_the_publisher_sends_it(api, service, db, queues):
    add_service(db)
    response = api.post("/analyze", json={"service_id": SID})
    assert response.status_code == 200  # 現行桌面端只認 200
    assert response.get_json() == {"service_id": SID, "accepted": True, "state": "queued"}
    assert db.status(SID) == 2  # 狀態 2 由 API 在同一個交易裡寫（桌面端收到 200 之後不再寫）
    assert job(db, SID)["job_state"] == "queued" and job(db, SID)["attempts"] == 0
    assert outbox(db) == [("pending", 0)]
    assert queues.count(queues.main) == 0  # 200 是「已經持久接受」，還沒送到 SQS

    assert service.publisher.run_once() == 1
    assert outbox(db) == [("sent", 1)]  # 收到 SQS 的 MessageId 才標成 sent
    assert queues.count(queues.main) == 1


def test_repeated_requests_share_one_job_until_it_is_done(api, service, db, tmp_path, drain):
    add_recording(service, db, tmp_path, SID)
    assert api.post("/analyze", json={"service_id": SID}).get_json()["state"] == "queued"
    second = api.post("/analyze", json={"service_id": SID})  # 例如桌面端逾時後又按了一次
    assert second.status_code == 200 and second.get_json()["state"] == "already_queued"
    assert len(outbox(db)) == 1  # 不再新增 outbox
    assert drain() == ["done"]
    assert db.status(SID) == 3
    third = api.post("/analyze", json={"service_id": SID})
    assert (third.status_code, third.get_json()) == (409, {"error": "already_completed"})


def test_completed_service_from_before_the_queue_is_409(api, db):
    add_service(db, status=3)  # SQS 之前分析完成的服務：沒有工作那一列
    response = api.post("/analyze", json={"service_id": SID})
    assert (response.status_code, response.get_json()) == (409, {"error": "already_completed"})
    assert job(db, SID) is None


def test_unknown_service_is_404(api, db):
    response = api.post("/analyze", json={"service_id": SID})
    assert (response.status_code, response.get_json()) == (404, {"error": "service_not_found"})
    assert db.query("SELECT COUNT(*) FROM tbl_analysis_outbox") == [(0,)]


@pytest.mark.parametrize("body", [
    "not json", ["S20261102090000"], {}, {"service_id": 20261102090000}, {"service_id": "S2026110209000"},
    {"service_id": "S20261102090000\n"}, {"service_id": "S２０２６１１０２０９００００"}],
    ids=["not-json", "not-object", "missing", "number", "13-digits", "trailing-newline", "full-width-digits"])
def test_invalid_service_id_is_400(api, db, body):
    response = api.post("/analyze", json=body) if not isinstance(body, str) else api.post("/analyze", data=body)
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_service_id"
    assert db.query("SELECT COUNT(*) FROM tbl_analysis_job") == [(0,)]


@pytest.mark.parametrize("status", [1, 3])
@pytest.mark.parametrize("force", [True, 1, "yes"])
def test_force_is_rejected(api, db, status, force):
    """教學 5-1：這一版不支援重跑已完成的服務；有 force 的請求明確拒絕，不當成一般的分析（狀態 1 的也一樣）。"""
    add_service(db, status=status)
    response = api.post("/analyze", json={"service_id": SID, "force": force})
    assert (response.status_code, response.get_json()) == (400, {"error": "force_not_supported"})
    assert db.status(SID) == status and job(db, SID) is None


@pytest.mark.parametrize("force", [False, 0, None, ""])
def test_a_false_force_is_an_ordinary_analysis(api, db, force):
    add_service(db)
    response = api.post("/analyze", json={"service_id": SID, "force": force})
    assert response.status_code == 200 and response.get_json()["state"] == "queued"


def test_database_error_is_503(api, service, db, monkeypatch):
    add_service(db)
    monkeypatch.setattr(service, "pool", BrokenPool())
    response = api.post("/analyze", json={"service_id": SID})
    assert (response.status_code, response.get_json()) == (503, {"error": "db_unavailable"})
    assert db.status(SID) == 1 and job(db, SID) is None


# ---------- GET /healthz ----------
def test_healthz_reads_the_workers_and_the_jobs_from_the_database(api, service, db, tmp_path, worker, drain,
                                                                  monkeypatch):
    status, body = healthz(api)
    assert status == 200
    assert body == {"status": "ok", "models": "failed", "workers": NO_WORKERS, "queue_depth": 0,
                    "jobs": {"queued": 0, "running": 0}, "outbox_pending": 0, "oldest_pending_sec": 0,
                    "stale_jobs": 0, "sqs": "configured"}  # 沒有 worker 活著：收得到工作，但不能分析
    worker.report("loading")
    assert healthz(api)[1]["models"] == "loading"

    add_recording(service, db, tmp_path, SID)
    api.post("/analyze", json={"service_id": SID})
    body = healthz(api)[1]
    assert (body["queue_depth"], body["jobs"], body["outbox_pending"]) == (1, {"queued": 1, "running": 0}, 1)

    seen = []
    process = worker.process

    def watched(work):  # worker 正在做的時候看一次 /healthz
        seen.append(healthz(api)[1])
        return process(work)

    monkeypatch.setattr(worker, "process", watched)
    assert drain() == ["done"]
    during = seen[0]
    assert (during["models"], during["workers"]["busy"]) == ("ready", 1)
    assert (during["queue_depth"], during["jobs"]["running"], during["outbox_pending"]) == (1, 1, 0)
    body = healthz(api)[1]
    assert (body["models"], body["workers"], body["queue_depth"]) == ("ready", {**NO_WORKERS, "ready": 1}, 0)


def test_healthz_ignores_workers_that_stopped_reporting(api, db, worker):
    worker.report("ready")
    db.query("UPDATE tbl_analysis_worker SET seen_at = UTC_TIMESTAMP(3) - INTERVAL 10 MINUTE")  # 容器停了、被 kill
    status, body = healthz(api)
    assert (status, body["models"], body["workers"]) == (200, "failed", NO_WORKERS)


def test_healthz_without_the_database_is_503(api, service, monkeypatch):
    monkeypatch.setattr(service, "pool", BrokenPool())
    assert healthz(api) == (503, {"status": "error", "database": "unavailable"})
