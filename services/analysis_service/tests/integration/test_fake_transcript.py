# tests/integration/test_fake_transcript.py — 假分析順便存一份假的逐字稿（new-feature N6，只有第 0 階段假服務有）。
# 格式和移植版 analyzer.py 的 process_text() 一樣，報表的 AI 說明（ollama_explain.py）讀得懂；內容一看就是假的。
# SQS（T49）之後檔名帶這一次的 token（text/{service_id}_{tag}_transcript.txt），位置在提交的交易裡登記。
import os

import mysql.connector
import pytest

import ollama_explain
from fake_helpers import Recorder, add_recording, chart_rows, job, keys, text_rows
from report_helpers import STAFF_ACCOUNT, add_report, pdf_text, report_row
from test_report_explanations import KEY, MODEL, FakeOllama  # 共用測試裡的假 /api/chat

pytestmark = pytest.mark.integration
SID = "S20261031110000"
PROCEDURE = "EXECUTE ON PROCEDURE adam.sp_insert_text_storage"


def transcript_name(db):
    return f"{SID}_{job(db, SID)['result_token'][:12]}_transcript.txt"


def test_analysis_saves_an_obviously_fake_transcript(api, service, db, tmp_path, drain, worker_module):
    add_recording(service, db, tmp_path, SID, cams=())  # 只有錄音
    assert api.post("/analyze", json={"service_id": SID}).status_code == 200
    assert drain() == ["done"]
    assert db.status(SID) == 3

    name = transcript_name(db)
    obj = service.s3.get_object(Bucket=service.S3_BUCKET, Key=f"text/{name}")
    assert obj["ContentType"] == "text/plain; charset=utf-8"
    lines = ollama_explain.parse_transcript(obj["Body"].read().decode("utf-8"))
    assert {line.side for line in lines} == {"customer", "staff"}
    assert len(lines) == len(worker_module.FAKE_TRANSCRIPT)
    assert all(line.text.startswith("(placeholder) ") for line in lines)
    assert text_rows(db, SID) == [(name, f"s3://{service.S3_BUCKET}/text/{name}")]


def test_no_recording_means_no_transcript(api, service, db, tmp_path, drain):
    add_recording(service, db, tmp_path, SID, audio=False)
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    assert db.status(SID) == 3
    assert text_rows(db, SID) == [] and keys(service, "text/") == []


@pytest.mark.parametrize("broken", ["s3", "db"])
def test_transcript_problems_do_not_fail_the_analysis(api, service, db, tmp_path, drain, worker_module, monkeypatch,
                                                      broken, caplog):
    """EC2 role 還沒有 text/* 的 PutObject，或 adam_analysis 還沒有 sp_insert_text_storage 的 EXECUTE：
    分析照樣完成（同一個交易的分數、圖表照樣提交），只是沒有逐字稿，服務結果（W1）也不排。"""
    enqueued = Recorder(db)
    monkeypatch.setattr(worker_module.outcome_jobs, "enqueue", enqueued)
    user = f"'{os.environ['DB_USER']}'@'%'"
    if broken == "s3":
        original = worker_module.s3.put_object

        def put_object(**kwargs):
            if kwargs["Key"].startswith("text/"):
                raise PermissionError("AccessDenied")
            return original(**kwargs)

        monkeypatch.setattr(worker_module.s3, "put_object", put_object)
    else:
        db.query(f"REVOKE {PROCEDURE} FROM {user}")
    try:
        add_recording(service, db, tmp_path, SID, cams=())
        api.post("/analyze", json={"service_id": SID})
        assert drain() == ["done"]
    finally:
        if broken == "db":
            db.query(f"GRANT {PROCEDURE} TO {user}")
    assert db.status(SID) == 3  # 分析照樣完成
    assert db.query("SELECT COUNT(*) FROM tbl_service_score WHERE service_id = %s", SID) == [(1,)]
    assert len(chart_rows(db, SID)) == 2  # 總覽、錄音音量
    assert text_rows(db, SID) == [] and enqueued.calls == []
    expected = "(PermissionError)" if broken == "s3" else "(MySQL 1370): grant adam_analysis EXECUTE"
    assert f"fake transcript not saved for {SID} {expected}" in caplog.text


def test_other_database_errors_fail_the_commit_and_are_retried(api, service, db, tmp_path, drain, worker_module,
                                                              monkeypatch):
    """登記逐字稿時的其他資料庫錯誤（例如 deadlock，MySQL 會撤回整個交易）不能當成「沒有權限」略過：
    這一次整個 rollback、重試，不會只提交一半。"""
    deadlock = mysql.connector.errors.DatabaseError(msg="Deadlock found when trying to get lock", errno=1213)
    write_results = worker_module.write_results

    class Cursor:  # 只有第一次 CALL sp_insert_text_storage 丟出 deadlock，其他照原本的 cursor 做
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, sql, args=()):
            if "sp_insert_text_storage" in sql and not failed:
                failed.append(sql)
                raise deadlock
            return self.cursor.execute(sql, args)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    failed = []
    monkeypatch.setattr(worker_module, "write_results", lambda cur, *args: write_results(Cursor(cur), *args))
    add_recording(service, db, tmp_path, SID, cams=())
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["retry", "done"]
    assert failed and job(db, SID)["attempts"] == 2
    assert text_rows(db, SID) == [(transcript_name(db), f"s3://{service.S3_BUCKET}/text/{transcript_name(db)}")]


def test_fake_analysis_feeds_the_whole_ai_flow(api, service, db, tmp_path, drain, wait_for_outcomes, wait_for_reports,
                                               worker_module, monkeypatch):
    """假分析 → 假逐字稿 → 報表 → AI 說明（假的 /api/chat）→ PDF：流程接得起來（PLAN 9：用假資料把流程做好）。

    PLAN1 W1 之後，分析提交之後會先用同一個 Ollama 填服務結果（service_outcome.py）：第一個回答給它，等它做完才產生報表。
    """
    fake = FakeOllama()
    try:
        plain = {m: {p: {s: f"A short {m} {p} note about the {s}." for s in ("customer", "staff")}
                     for p in ("comment", "improvement")} for m in ("audio", "text", "facial")}
        plain["text"]["comment"]["customer"] = 'The customer said "I have been waiting for a while now".'
        fake.replies.append((200, {"outcome": "Unknown", "outcome_evidence": "", "todos": []}, 0))
        fake.replies.append((200, plain, 0))
        monkeypatch.setenv("OLLAMA_BASE_URL", fake.url)
        monkeypatch.setenv("OLLAMA_API_KEY", KEY)
        monkeypatch.setenv("OLLAMA_MODEL", MODEL)
        add_recording(service, db, tmp_path, SID, cams=())
        api.post("/analyze", json={"service_id": SID})
        assert drain() == ["done"]
        wait_for_outcomes()
        report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
        api.post("/reports", json={"report_id": report_id})
        wait_for_reports()
    finally:
        fake.close()
    assert report_row(db, report_id)["ai_model"] == MODEL
    explanation = [r for r in fake.requests if r["body"]["messages"][0]["content"] == ollama_explain.SYSTEM_PROMPT]
    sent = explanation[0]["body"]["messages"][1]["content"]
    assert sent.count("(placeholder)") == len(worker_module.FAKE_TRANSCRIPT)  # 送出的是假逐字稿的英文翻譯
    pdf = service.s3.get_object(Bucket=service.S3_BUCKET, Key=f"reports/{report_id}.pdf")["Body"].read()
    text = pdf_text(pdf)
    assert "I have been waiting for a while now" in text and "AI-generated explanation" in text
