# tests/integration/test_outcome_flow.py — PLAN1 W1 走完整條路（移植版，SQS 之後）：
# /analyze → SQS → worker（FakeAnalysis，不跑模型）→ 一個交易寫分數、逐字稿、圖表並清掉上一次的服務結果（run 編號＝這一次的 token）
# → 提交之後才排進 worker 自己的 OutcomeJobs → 假的 /api/chat → tbl_service 的三欄。
# 共用的檢查（寫回、晚到的舊結果、各種失敗）在 test_outcome_jobs.py。
import os
import time

import pytest

from test_report_explanations import KEY, MODEL, FakeOllama  # 共用測試裡的假 /api/chat

pytestmark = pytest.mark.integration
SID, OTHER = "S20261101120000", "S20261101121500"
TRANSCRIPT = "\n".join([  # process_text() 的格式；句子多的那位是服務人員
    "Customer: 我想開一個外幣帳戶。", "Translated: I want to open a foreign currency account.\n",
    "Staff: 好的，需要您的護照。", "Translated: Sure, for that account I need your passport.\n",
    "Customer: 我今天沒有帶護照。", "Translated: I did not bring my passport today.\n",
    "Staff: 那請您明天帶護照再來一次。", "Translated: Then please come back tomorrow with your passport.\n",
    "Staff: 我會先把申請表留著。", "Translated: I will keep the application form ready for you.\n",
])
ANSWER = {"outcome": "Documents missing", "outcome_evidence": "I did not bring my passport today",
          "todos": [{"text": "Customer comes back tomorrow with the passport.",
                     "evidence": "please come back tomorrow with your passport"},
                    {"text": "Keep the application form ready.",
                     "evidence": "I will keep the application form ready for you"}]}
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


@pytest.fixture
def analysis(fake_analysis):
    fake_analysis.transcript_text = TRANSCRIPT
    return fake_analysis


def outcome(db, sid=SID):
    return db.query("SELECT service_outcome, service_todos, service_outcome_model FROM tbl_service "
                    "WHERE service_id = %s", sid)[0]


def outcome_run(db, sid=SID):
    return db.query("SELECT service_outcome_run FROM tbl_service WHERE service_id = %s", sid)[0][0]


def analyze(api, db, add_recording, sid=SID):
    db.add_service(sid, 1)
    add_recording(sid)
    assert api.post("/analyze", json={"service_id": sid}).status_code == 200


def test_the_analysis_fills_the_outcome_from_its_transcript(api, db, add_recording, analysis, ollama, drain,
                                                            wait_for_outcomes):
    ollama.replies.append((200, ANSWER, 0))
    analyze(api, db, add_recording)
    drain()
    assert db.status(SID) == 3 and db.scores(SID)["server_total"] == 82
    assert outcome_run(db) == db.job(SID)["result_token"]  # 這一次的 run 編號就是提交的 token
    wait_for_outcomes()
    assert outcome(db) == ("Documents missing", "Customer comes back tomorrow with the passport.\n"
                                                "Keep the application form ready.", MODEL)
    sent = ollama.requests[0]["body"]["messages"][1]["content"]
    assert "I want to open a foreign currency account." in sent and "護照" not in sent  # 英文翻譯，不送原文
    assert "82" not in sent  # 不送分數


def test_the_outcome_is_queued_only_after_the_commit(api, db, add_recording, analysis, worker_module, drain,
                                                     monkeypatch):
    """排進 OutcomeJobs 時，結果已經提交（測試自己的連線看得到狀態 3、工作 done）；run 編號是 token、逐字稿是帶 token 的檔名。"""
    queued = []

    def enqueue(sid, run_id, transcript_key):
        queued.append((sid, run_id, transcript_key, db.status(sid), db.job(sid)["job_state"]))

    monkeypatch.setattr(worker_module.outcome_jobs, "enqueue", enqueue)
    analyze(api, db, add_recording)
    drain()
    token = db.job(SID)["result_token"]
    assert queued == [(SID, token, f"text/{SID}_{token[:12]}_transcript.txt", 3, "done")]
    assert db.texts(SID)[0][0] == f"{SID}_{token[:12]}_transcript.txt"


def test_a_failed_attempt_does_not_queue_an_outcome(api, db, add_recording, analysis, worker_module, deny_procedure,
                                                    drain, monkeypatch):
    """提交失敗（這裡是寫圖表的權限被拿掉）的那幾次不排服務結果：交易 rollback 了，三欄和 run 編號都沒有被換掉。"""
    queued = []
    monkeypatch.setattr(worker_module.outcome_jobs, "enqueue", lambda *job: queued.append(job))
    deny_procedure("sp_insert_charts_storage")
    analyze(api, db, add_recording)
    drain()
    assert db.status(SID) == 1 and queued == [] and outcome_run(db) is None


def test_a_slow_ai_does_not_hold_up_the_next_analysis(api, db, add_recording, analysis, ollama, drain,
                                                      wait_for_outcomes):
    ollama.replies += [(200, ANSWER, 5), (200, UNKNOWN, 0)]
    started = time.monotonic()
    analyze(api, db, add_recording, SID)
    analyze(api, db, add_recording, OTHER)
    drain()  # 兩筆分析都做完了，第一筆的 AI 還在等
    assert time.monotonic() - started < 5
    assert (db.status(SID), db.status(OTHER)) == (3, 3)
    assert outcome(db, SID) == outcome(db, OTHER) == (None, None, None)
    wait_for_outcomes()
    assert (outcome(db, SID)[0], outcome(db, OTHER)[0]) == ("Documents missing", "Unknown")


def test_an_analysis_without_a_transcript_clears_the_old_outcome(api, db, add_recording, analysis, ollama, drain,
                                                                 wait_for_outcomes):
    """這一次沒有逐字稿（例如錄音不見了）：之前留下的結果清掉（例如狀態被手動改回 1 再分析），不送 AI。"""
    analysis.transcript_text = None
    db.add_service(SID, 1)
    db.query("UPDATE tbl_service SET service_outcome = 'Resolved', service_todos = '', service_outcome_model = %s, "
             "service_outcome_run = %s WHERE service_id = %s", MODEL, "1" * 32, SID)
    add_recording(SID)
    api.post("/analyze", json={"service_id": SID})
    drain()
    wait_for_outcomes()
    assert db.status(SID) == 3 and db.texts(SID) == []
    assert outcome(db) == (None, None, None) and outcome_run(db) == db.job(SID)["result_token"]
    assert ollama.requests == []


def test_without_migration_005_grant_the_analysis_still_completes(api, db, add_recording, analysis, ollama, drain,
                                                                  wait_for_outcomes, caplog):
    """EC2 上的服務換成新版、RDS 還沒執行 005 的 GRANT：分數、逐字稿、圖表照樣寫，只是不填服務結果。"""
    user = f"'{os.environ['DB_USER']}'@'%'"
    db.query(f"REVOKE {GRANTED} FROM {user}")
    try:
        analyze(api, db, add_recording)
        drain()
        wait_for_outcomes()
    finally:
        db.query(f"GRANT {GRANTED} TO {user}")
    assert db.status(SID) == 3 and db.scores(SID) is not None and len(db.texts(SID)) == 1
    assert ollama.requests == [] and outcome(db) == (None, None, None)
    assert "(MySQL 1143); apply migration 005 and its GRANT" in caplog.text
