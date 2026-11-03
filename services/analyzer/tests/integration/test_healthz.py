# tests/integration/test_healthz.py — /healthz：API 不載入模型，models 看 RDS 裡 worker 的心跳（tbl_analysis_worker）。
#   有 worker 等工作或正在做是 ready；只有載入中的是 loading；沒有活著的 worker、或都載入失敗是 failed。
#   桌面端的連線檢查只看 status、models（failed／loading）、queue_depth（等待＋執行中的工作數），這三個的意思不變。
#   worker.py 的主程式（main → analysis_queue.serve）先登記 loading 再載入模型，所以載入中的 /healthz 是 loading。
import signal
from contextlib import contextmanager

import mysql.connector
import pytest

import analyzer

pytestmark = pytest.mark.integration
SID, OTHER = "S20261030120000", "S20261030121500"
NO_WORKERS = {"loading": 0, "ready": 0, "busy": 0, "failed": 0}


@contextmanager
def stoppable():
    """serve() 會把 SIGTERM、SIGINT 換成「停止 worker」：測試直接呼叫它裝的那個（不送真的訊號），結束後換回 pytest 的。"""
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_without_any_worker_models_are_failed(api):
    assert api.get("/healthz").get_json() == {
        "status": "ok", "models": "failed", "workers": NO_WORKERS, "queue_depth": 0,
        "jobs": {"queued": 0, "running": 0}, "outbox_pending": 0, "oldest_pending_sec": 0, "stale_jobs": 0,
        "sqs": "configured"}


@pytest.mark.parametrize("state, models", [
    ("loading", "loading"), ("ready", "ready"), ("busy", "ready"),
    ("failed", "failed"),  # 模型載入失敗：收得到工作，但不能分析
    ("stopped", "failed"),  # 正常結束（docker stop）
])
def test_models_follow_the_workers(api, worker, state, models):
    worker.report(state, SID if state == "busy" else None)
    health = api.get("/healthz").get_json()
    assert health["models"] == models
    assert health["workers"] == {**NO_WORKERS, **({state: 1} if state != "stopped" else {})}


def test_a_worker_that_stopped_reporting_is_not_counted(api, db, worker):
    worker.report("ready")
    db.query("UPDATE tbl_analysis_worker SET seen_at = UTC_TIMESTAMP(3) - INTERVAL 10 MINUTE")  # 被 kill、OOM
    assert api.get("/healthz").get_json()["models"] == "failed"


def test_queue_depth_counts_the_waiting_jobs(api, db, service):
    db.add_service(SID, 1)
    db.add_service(OTHER, 1)
    api.post("/analyze", json={"service_id": SID})
    api.post("/analyze", json={"service_id": OTHER})
    health = api.get("/healthz").get_json()
    assert (health["queue_depth"], health["jobs"], health["outbox_pending"]) == (2, {"queued": 2, "running": 0}, 2)
    service.publisher.run_once()
    health = api.get("/healthz").get_json()
    assert (health["queue_depth"], health["outbox_pending"]) == (2, 0)  # 送到 SQS 了，還沒有 worker 做


def test_healthz_answers_while_the_worker_loads_its_models(api, db, worker_module, monkeypatch):
    """worker.py 的 main()：開服務結果的 worker → serve() 登記 loading（說明是 Whisper 的大小）→ 載入模型 → ready。"""
    started = []
    monkeypatch.setattr(worker_module.outcome_jobs, "start", lambda: started.append(True))
    seen = {}
    with stoppable() as stop:
        def load_models():  # 載入模型要一段時間：這時候的 /healthz
            seen["health"] = api.get("/healthz").get_json()
            seen["workers"] = list(db.workers().values())
            stop()  # 載入完就收到 SIGTERM：worker 登記 ready 之後馬上正常結束，不去領工作

        monkeypatch.setattr(analyzer, "load_models", load_models)
        assert worker_module.main() == 0

    assert started == [True]
    assert (seen["health"]["status"], seen["health"]["models"]) == ("ok", "loading")
    assert seen["workers"] == [("loading", None, f"whisper {worker_module.WHISPER_MODEL}")]
    assert [state for state, _, _ in db.workers().values()] == ["stopped"]


def test_failed_model_loading_shows_in_healthz(api, db, worker_module, monkeypatch):
    """模型載入失敗：worker 登記 failed 和原因、不領工作（/healthz 說 failed），等到被停止；已接受的工作留在佇列裡等。"""
    monkeypatch.setattr(worker_module.outcome_jobs, "start", lambda: None)
    db.add_service(SID, 1)
    api.post("/analyze", json={"service_id": SID})
    with stoppable() as stop:
        def missing_models():
            stop()  # 先要求停止，serve() 才不會一直等下去
            raise RuntimeError("cannot load pyannote/speaker-diarization-3.1")

        monkeypatch.setattr(analyzer, "load_models", missing_models)
        assert worker_module.main() == 1

    health = api.get("/healthz").get_json()
    assert (health["models"], health["workers"], health["queue_depth"]) == ("failed", {**NO_WORKERS, "failed": 1}, 1)
    assert list(db.workers().values()) == [
        ("failed", None, "RuntimeError: cannot load pyannote/speaker-diarization-3.1")]
    assert (db.status(SID), db.job(SID)["job_state"]) == (2, "queued")


def test_database_error_returns_503(api, service, monkeypatch):
    def pool_exhausted():
        raise mysql.connector.errors.PoolError("Failed getting connection; pool exhausted")

    monkeypatch.setattr(service.pool, "get_connection", pool_exhausted)
    r = api.get("/healthz")
    assert (r.status_code, r.get_json()) == (503, {"status": "error", "database": "unavailable"})
