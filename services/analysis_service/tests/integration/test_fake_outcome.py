# tests/integration/test_fake_outcome.py — PLAN1 W1 走完整條路（只有第 0 階段假服務有這份）：
# /analyze → worker 的假分析提交（同一個交易清掉上一次的服務結果、run 編號＝這一次的 token）→ 假逐字稿
# → worker 自己的佇列 → 假的 /api/chat → tbl_service 的三欄。共用的檢查（寫回、晚到的舊結果、各種失敗）在 test_outcome_jobs.py。
import logging
import os
import time

import mysql.connector
import pytest

import service_outcome
from fake_helpers import FailFirst, add_recording, job, text_rows
from test_report_explanations import KEY, MODEL, FakeOllama  # 共用測試裡的假 /api/chat

pytestmark = pytest.mark.integration
SID, OTHER = "S20261101110000", "S20261101111500"
# 假逐字稿（worker.FAKE_TRANSCRIPT）裡的句子當證據
ANSWER = {"outcome": "Resolved", "outcome_evidence": "I will take care of it right away",
          "todos": [{"text": "Check on the customer's order.", "evidence": "I would like to check on my order"}]}
UNKNOWN = {"outcome": "Unknown", "outcome_evidence": "", "todos": []}
GRANTED = "UPDATE (service_outcome, service_todos, service_outcome_model, service_outcome_run) ON adam.tbl_service"


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setenv("OLLAMA_BASE_URL", fake.url)
    monkeypatch.setenv("OLLAMA_API_KEY", KEY)
    monkeypatch.setenv("OLLAMA_MODEL", MODEL)
    yield fake
    fake.close()


def outcome(db, sid=SID):
    return db.query("SELECT service_outcome, service_todos, service_outcome_model FROM tbl_service "
                    "WHERE service_id = %s", sid)[0]


def outcome_run(db, sid=SID):
    return db.query("SELECT service_outcome_run FROM tbl_service WHERE service_id = %s", sid)[0][0]


def scores(db, sid=SID):
    return db.query("SELECT customer_total, server_total, customer_audio, server_audio FROM tbl_service_score "
                    "WHERE service_id = %s", sid)


def test_the_fake_analysis_fills_the_outcome_from_its_transcript(api, service, db, tmp_path, ollama, drain,
                                                                  wait_for_outcomes, worker_module):
    ollama.replies.append((200, ANSWER, 0))
    add_recording(service, db, tmp_path, SID, cams=())
    assert api.post("/analyze", json={"service_id": SID}).status_code == 200
    assert drain() == ["done"]
    assert db.status(SID) == 3 and scores(db)
    wait_for_outcomes()
    assert outcome(db) == ("Resolved", "Check on the customer's order.", MODEL)
    assert outcome_run(db) == job(db, SID)["result_token"]  # run 編號就是提交的那一次的 token
    sent = ollama.requests[0]["body"]["messages"][1]["content"]
    assert sent.count("(placeholder)") == len(worker_module.FAKE_TRANSCRIPT)  # 送出的是假逐字稿的英文翻譯
    assert "score" not in sent.lower()  # 不送分數


def test_a_failed_commit_writes_nothing_and_the_retry_fills_the_outcome(api, service, db, tmp_path, ollama, drain,
                                                                       wait_for_outcomes, monkeypatch):
    """第一次在提交的交易裡失敗（清服務結果那一句出錯）：分數、圖表、逐字稿的位置全部 rollback，也不排服務結果；
    重試的那一次提交之後才排入，Ollama 只被問一次。"""
    ollama.replies.append((200, ANSWER, 0))
    error = mysql.connector.errors.DatabaseError("2013: Lost connection to MySQL server during query")
    monkeypatch.setattr(service_outcome, "clear", FailFirst(service_outcome.clear, error))
    add_recording(service, db, tmp_path, SID, cams=())
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["retry", "done"]
    row = job(db, SID)
    assert (row["job_state"], row["attempts"], db.status(SID)) == ("done", 2, 3)
    assert len(text_rows(db, SID)) == 1  # 第一次登記的逐字稿跟著 rollback 了
    wait_for_outcomes()
    assert outcome(db)[0] == "Resolved" and outcome_run(db) == row["result_token"]
    assert len(ollama.requests) == 1


def test_a_slow_ai_does_not_hold_up_the_next_analysis(api, service, db, tmp_path, ollama, drain, wait_for_outcomes):
    ollama.replies += [(200, ANSWER, 5), (200, UNKNOWN, 0)]
    add_recording(service, db, tmp_path, SID, cams=())
    add_recording(service, db, tmp_path, OTHER, cams=())
    started = time.monotonic()
    api.post("/analyze", json={"service_id": SID})
    api.post("/analyze", json={"service_id": OTHER})
    assert drain() == ["done", "done"]  # 兩筆分析都提交了，第一筆的 AI 還在等
    assert time.monotonic() - started < 5
    assert (db.status(SID), db.status(OTHER)) == (3, 3)
    assert outcome(db, SID) == outcome(db, OTHER) == (None, None, None)
    wait_for_outcomes()
    assert sorted([outcome(db, SID)[0], outcome(db, OTHER)[0]]) == ["Resolved", "Unknown"]


def test_no_recording_means_no_transcript_and_no_outcome(api, service, db, tmp_path, ollama, drain,
                                                         wait_for_outcomes):
    add_recording(service, db, tmp_path, SID, cams=(), audio=False)
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    wait_for_outcomes()
    assert db.status(SID) == 3
    assert ollama.requests == [] and outcome(db) == (None, None, None)


def test_with_ai_off_the_analysis_is_unchanged(api, service, db, tmp_path, drain, wait_for_outcomes, caplog):
    caplog.set_level(logging.INFO)  # 「AI 關著」是 INFO
    add_recording(service, db, tmp_path, SID, cams=())
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    wait_for_outcomes()
    assert db.status(SID) == 3 and scores(db)
    assert outcome(db) == (None, None, None) and outcome_run(db) == job(db, SID)["result_token"]  # 清過了，只是沒有填
    assert "AI service outcomes are off: OLLAMA_API_KEY or OLLAMA_MODEL is not set" in caplog.text


def test_without_migration_005_grant_the_analysis_still_completes(api, service, db, tmp_path, ollama, drain,
                                                                  wait_for_outcomes, caplog):
    """EC2 上的服務換成新版、RDS 還沒執行 005 的 GRANT：分析照樣完成、分數照樣寫，只是不填服務結果。"""
    user = f"'{os.environ['DB_USER']}'@'%'"
    db.query(f"REVOKE {GRANTED} FROM {user}")
    try:
        add_recording(service, db, tmp_path, SID, cams=())
        api.post("/analyze", json={"service_id": SID})
        assert drain() == ["done"]
        wait_for_outcomes()
    finally:
        db.query(f"GRANT {GRANTED} TO {user}")
    assert db.status(SID) == 3 and scores(db)
    assert ollama.requests == [] and outcome(db) == (None, None, None)
    assert "(MySQL 1143); apply migration 005 and its GRANT" in caplog.text
