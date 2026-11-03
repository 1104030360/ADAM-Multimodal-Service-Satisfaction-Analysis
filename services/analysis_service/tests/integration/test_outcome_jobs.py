# tests/integration/test_outcome_jobs.py — PLAN1 W1：服務結果與待辦寫回 tbl_service（service_outcome.OutcomeJobs）。
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）。
# 真的 MySQL 8.4（服務用最小權限的 adam_analysis，grants.sql）＋moto 的 S3＋127.0.0.1 的假 /api/chat，不連 ollama.com。
# 整條「分析 → 逐字稿 → 背景工作 → 三欄」在各服務自己的 test_fake_outcome.py、test_outcome_flow.py。
import json
import os
import threading
import time
from datetime import datetime

import pytest

import service_outcome
from report_helpers import add_scored_service
from test_report_explanations import KEY, MODEL, FakeOllama  # 共用測試裡的假 /api/chat

pytestmark = pytest.mark.integration
SID = "S20261101100000"
TRANSCRIPT_KEY = f"text/{SID}_transcript.txt"
RUN, NEWER_RUN = "1" * 32, "2" * 32
SCORES = dict(server_total=83.9, server_audio=78.6, server_text=88.5, server_facial=84.7,
              customer_total=74.2, customer_audio=70.1, customer_text=66.0, customer_facial=78.6)
TRANSCRIPT = "\n".join([  # analyzer.py 的 process_text() 寫出來的格式
    "Customer: 我的金融卡壞了。", "Translated: My debit card stopped working at the machine.\n",
    "Staff: 我幫您補發一張新卡。", "Translated: I will order a replacement card for you now.\n",
    "Staff: 新卡會在五個工作天內寄到。", "Translated: The new card will be mailed to your home within 5 business days.\n",
    "Customer: 好，謝謝。", "Translated: Great, thank you for your help.\n",
])
ANSWER = {"outcome": "Resolved", "outcome_evidence": "I will order a replacement card for you now",
          "todos": [{"text": "Mail the new card within 5 business days.",
                     "evidence": "The new card will be mailed to your home within 5 business days"}]}
OUTCOME_COLUMNS = "service_outcome, service_todos, service_outcome_model"
GRANTED = "UPDATE (service_outcome, service_todos, service_outcome_model, service_outcome_run) ON adam.tbl_service"


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setenv("OLLAMA_BASE_URL", fake.url)
    monkeypatch.setenv("OLLAMA_API_KEY", KEY)
    monkeypatch.setenv("OLLAMA_MODEL", MODEL)
    yield fake
    fake.close()


def prepare(service, db, run=RUN, transcript=TRANSCRIPT):
    """一筆分析完成的服務：分數、這次分析清過的三欄（service_outcome_run = run），S3 上的逐字稿。"""
    add_scored_service(db, SID, datetime(2026, 11, 1, 10), status=3, **SCORES)
    db.query("UPDATE tbl_service SET service_outcome_run = %s WHERE service_id = %s", run, SID)
    if transcript is not None:
        service.s3.put_object(Bucket=service.S3_BUCKET, Key=TRANSCRIPT_KEY, Body=transcript.encode("utf-8"),
                              ContentType="text/plain; charset=utf-8")
    return service_outcome.Job(SID, run, TRANSCRIPT_KEY)


def outcome(db):
    return db.query(f"SELECT {OUTCOME_COLUMNS} FROM tbl_service WHERE service_id = %s", SID)[0]


def service_and_scores(db):
    status = db.query("SELECT service_status FROM tbl_service WHERE service_id = %s", SID)[0][0]
    scores = db.query("SELECT customer_total, server_total, customer_text, server_text FROM tbl_service_score "
                      "WHERE service_id = %s", SID)[0]
    return status, scores


def sent_document(request):
    content = request["body"]["messages"][1]["content"]
    return json.loads(content[len(service_outcome.USER_PREFIX):])


def test_the_checked_outcome_is_written_and_nothing_else_changes(service, db, ollama, caplog):
    ollama.replies.append((200, ANSWER, 0))
    job = prepare(service, db)
    before = service_and_scores(db)
    assert service.outcome_jobs.run(job) == 1
    assert outcome(db) == ("Resolved", "Mail the new card within 5 business days.", MODEL)
    assert service_and_scores(db) == before  # 狀態、分數都不動
    document = sent_document(ollama.requests[0])
    assert document == {"transcript": [
        {"speaker": "Customer", "text": "My debit card stopped working at the machine."},
        {"speaker": "Staff", "text": "I will order a replacement card for you now."},
        {"speaker": "Staff", "text": "The new card will be mailed to your home within 5 business days."},
        {"speaker": "Customer", "text": "Great, thank you for your help."}]}  # 只有逐字稿，沒有分數
    assert ollama.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in caplog.text and "replacement card" not in caplog.text


def test_unverifiable_parts_are_not_written(service, db, ollama):
    """證據對不上的結果寫 Unknown（不是 Resolved），逐字稿裡沒有的待辦不寫；沒有待辦是空字串。"""
    ollama.replies.append((200, {"outcome": "Resolved", "outcome_evidence": "The fee was refunded to you",
                                 "todos": [{"text": "Refund the 30 dollar fee.",
                                            "evidence": "The fee was refunded to you"}]}, 0))
    assert service.outcome_jobs.run(prepare(service, db)) == 1
    assert outcome(db) == ("Unknown", "", MODEL)


def test_a_late_answer_from_an_older_run_cannot_overwrite_the_newer_one(service, db, ollama):
    """等 AI 回答時分析又重跑了（寫分數的交易清掉三欄、換了編號）：舊的那一次寫不進去，三欄維持 NULL。"""
    ollama.replies.append((200, ANSWER, 1.5))
    job = prepare(service, db)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("rows", service.outcome_jobs.run(job)))
    worker.start()
    deadline = time.monotonic() + 10
    while not ollama.requests and time.monotonic() < deadline:  # 已經送出去了、還在等回答
        time.sleep(0.02)
    db.query(service_outcome.CLEAR_SQL, NEWER_RUN, SID)  # 重跑的那一次
    worker.join(10)
    assert result == {"rows": 0}
    assert outcome(db) == (None, None, None)

    ollama.replies.append((200, ANSWER, 0))  # 新的那一次照常寫
    assert service.outcome_jobs.run(service_outcome.Job(SID, NEWER_RUN, TRANSCRIPT_KEY)) == 1
    assert outcome(db)[0] == "Resolved"


def test_a_replaced_run_does_not_call_ollama(service, db, ollama):
    prepare(service, db, run=NEWER_RUN)  # 這一筆排隊時，分析又重跑了
    assert service.outcome_jobs.run(service_outcome.Job(SID, RUN, TRANSCRIPT_KEY)) == 0
    assert ollama.requests == [] and outcome(db) == (None, None, None)


@pytest.mark.parametrize("reply", [
    (429, {"error": "you have reached your usage limit"}, 0),
    (401, {"error": "unauthorized"}, 0),
    (200, {**ANSWER, "outcome": "Done"}, 0),  # 五個值以外
    (200, {"outcome": "Resolved"}, 0),  # 少欄位
], ids=["429", "401", "invalid-outcome", "missing-fields"])
def test_a_failed_call_leaves_the_columns_empty(service, db, ollama, reply):
    ollama.replies.append(reply)
    job = prepare(service, db)
    before = service_and_scores(db)
    assert service.outcome_jobs.run(job) == 0
    assert outcome(db) == (None, None, None)
    assert service_and_scores(db) == before


def test_a_slow_answer_gives_up_within_its_budget(service, db, ollama, monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SEC", "1")
    ollama.replies.append((200, ANSWER, 3))
    job = prepare(service, db)
    started = time.monotonic()
    assert service.outcome_jobs.run(job) == 0
    assert time.monotonic() - started < 2.5
    assert outcome(db) == (None, None, None)


def test_with_ai_off_nothing_is_sent(service, db, ollama, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY")
    assert service.outcome_jobs.run(prepare(service, db)) == 0
    assert ollama.requests == [] and outcome(db) == (None, None, None)


def test_without_a_transcript_file_nothing_is_sent(service, db, ollama, caplog):
    assert service.outcome_jobs.run(prepare(service, db, transcript=None)) == 0
    assert ollama.requests == [] and outcome(db) == (None, None, None)
    assert "transcript download failed" in caplog.text and "the outcome stays not filled" in caplog.text


def test_the_worker_fills_the_outcome_in_the_background(service, db, ollama, wait_for_outcomes):
    ollama.replies.append((200, ANSWER, 0))
    job = prepare(service, db)
    service.outcome_jobs.enqueue(job.service_id, job.run_id, job.transcript_key)
    wait_for_outcomes()
    assert outcome(db) == ("Resolved", "Mail the new card within 5 business days.", MODEL)


def test_clear_without_the_grant_keeps_the_rest_of_the_transaction(service, db, caplog):
    """資料庫還沒執行 005 的 GRANT：清不掉的那一句只記 log，同一個交易的其他寫入照樣 commit（分析不會因此失敗）。"""
    add_scored_service(db, SID, datetime(2026, 11, 1, 10), status=2, **SCORES)
    user = f"'{os.environ['DB_USER']}'@'%'"
    db.query(f"REVOKE {GRANTED} FROM {user}")
    try:
        conn = service.pool.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))
            assert service_outcome.clear(cursor, SID, NEWER_RUN) is False
            conn.commit()
            cursor.close()
        finally:
            conn.close()
    finally:
        db.query(f"GRANT {GRANTED} TO {user}")
    assert db.query("SELECT service_status, service_outcome_run FROM tbl_service WHERE service_id = %s", SID) == [
        (3, None)]
    assert "apply migration 005 and its GRANT" in caplog.text
