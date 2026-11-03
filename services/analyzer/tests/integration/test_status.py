# tests/integration/test_status.py — 服務狀態（tbl_service.service_status）怎麼走（教學 5-1、6-4；docs/rebuild/sqs/PLAN.md）：
#   1 → 2：API 接受的交易裡寫（桌面端不再寫）；2 → 3：worker 提交結果的交易裡寫；
#   失敗時維持 2，這一輪的最後一次（第 3 次）也失敗才改回 1，桌面端顯示失敗、可以 Retry（開新的一輪）。
import os

import pytest

pytestmark = pytest.mark.integration
SID = "S20261030120000"


def failing_rounds(worker, db, rounds):
    """worker 一次領一則，回傳每一次之後的（狀態, 工作狀態, 次數）。"""
    seen = []
    for _ in range(rounds):
        assert worker.poll_once(wait_sec=0) == 1
        job = db.job(SID)
        seen.append((db.status(SID), job["job_state"], job["attempts"]))
    return seen


def test_status_goes_from_1_to_2_to_3(api, db, service, add_recording, fake_analysis, drain):
    db.add_service(SID, 1)
    add_recording(SID)
    api.post("/analyze", json={"service_id": SID})
    assert db.status(SID) == 2  # 還沒送到 SQS、還沒有 worker 領，狀態已經是 2
    service.publisher.run_once()
    assert db.status(SID) == 2
    drain()
    assert db.status(SID) == 3 and db.job(SID)["job_state"] == "done"


def test_a_failure_keeps_status_2_until_the_last_attempt(api, db, service, queues, add_recording, fake_analysis,
                                                         worker):
    db.add_service(SID, 1)
    add_recording(SID)
    fake_analysis.error = RuntimeError("the model crashed")
    api.post("/analyze", json={"service_id": SID})
    service.publisher.run_once()

    # 前兩次失敗：桌面端還是顯示分析中，訊息交還 SQS 給下一次（不要每次例外就寫回 1）
    assert failing_rounds(worker, db, 2) == [(2, "queued", 1), (2, "queued", 2)]
    assert queues.count() == (1, 0)
    # 第 3 次（這一輪最後一次）也失敗：工作 failed、狀態改回 1
    assert failing_rounds(worker, db, 1) == [(1, "failed", 3)]
    assert db.job(SID)["last_error"] == "RuntimeError: the model crashed"
    # 訊息被領了 3 次：下一次領的時候 SQS 把它移到 DLQ，留著查
    assert worker.poll_once(wait_sec=0) == 0
    assert (queues.count(), queues.dead()) == ((0, 0), 1)
    # 每一次的暫存資料夾（裡面是錄影、錄音）都刪掉了
    assert len(fake_analysis.calls) == 3 and not any(os.path.exists(call["work"]) for call in fake_analysis.calls)
    assert (db.scores(SID), db.charts(SID), db.texts(SID)) == (None, [], [])


def test_retry_after_the_last_failure_starts_a_new_round(api, db, add_recording, fake_analysis, drain):
    db.add_service(SID, 1)
    add_recording(SID)
    fake_analysis.error = RuntimeError("the model crashed")
    api.post("/analyze", json={"service_id": SID})
    drain()
    assert (db.status(SID), db.job(SID)["job_state"]) == (1, "failed")

    fake_analysis.error = None  # 修好了，使用者按 Retry
    r = api.post("/analyze", json={"service_id": SID})
    assert (r.status_code, r.get_json()["state"]) == (200, "queued")
    assert db.status(SID) == 2
    assert db.job(SID)["job_state"] == "queued" and db.job(SID)["attempts"] == 0  # 次數重算
    assert [row["outbox_state"] for row in db.outbox(SID)] == ["sent", "pending"]  # 新的一則
    drain()
    job = db.job(SID)
    assert db.status(SID) == 3 and (job["job_state"], job["attempts"], job["last_error"]) == ("done", 1, None)


def test_a_duplicate_message_after_completion_changes_nothing(api, db, queues, add_recording, fake_analysis, drain):
    """Standard 佇列可能重複交付，outbox 也可能重送：已經完成的直接刪掉，不重算、圖表列不會變多、狀態維持 3。"""
    db.add_service(SID, 1)
    add_recording(SID)
    api.post("/analyze", json={"service_id": SID})
    drain()
    charts, scores = db.charts(SID), db.scores(SID)
    queues.sqs.send_message(QueueUrl=queues.url, MessageBody=f'{{"service_id": "{SID}"}}')

    assert drain() == 1
    assert queues.count() == (0, 0) and queues.dead() == 0  # 刪掉了，不是留給 DLQ
    assert len(fake_analysis.calls) == 1
    assert (db.status(SID), db.charts(SID), db.scores(SID)) == (3, charts, scores)
