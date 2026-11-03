# tests/integration/test_report_explanations.py — N6：每次服務報表的 AI 說明走完整條路（new-feature PLAN 7.4、9）。
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）。
# POST /reports → 背景工作 → 從 S3（moto）讀逐字稿 → 呼叫 127.0.0.1 的假 /api/chat → 檢查 → 畫進 PDF → ai_model、ai_prompt_version。
# 不連 ollama.com：OLLAMA_BASE_URL 指向測試自己開的假伺服器。
import json
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import ollama_explain
import report_model
from report_helpers import STAFF_ACCOUNT, add_report, add_scored_service, pdf_text, report_row

pytestmark = pytest.mark.integration
SID = "S20261031093000"
START = datetime(2026, 10, 31, 9, 30)
KEY = "sk-integration-test-key-0001"
MODEL = "gemma4:31b"
SCORES = dict(server_total=83.9, server_audio=78.6, server_text=88.5, server_facial=84.7,
              customer_total=74.2, customer_audio=70.1, customer_text=66.0, customer_facial=78.6)
TRANSCRIPT = "\n".join([  # analyzer.py 的 process_text() 寫出來的格式
    "Customer: 我已經等了二十分鐘了。", "Translated: I have been waiting for twenty minutes.\n",
    "Staff: 很抱歉讓您久等了。", "Translated: I am sorry to keep you waiting.\n",
    "Customer: 我想改訂單的地址。", "Translated: I want to change the address on my order.\n",
    "Staff: 沒問題，我現在幫您改。", "Translated: No problem, I will change it for you now.\n",
])
ANSWER = {
    "audio": {"comment": {"customer": "The customer's voice was 30% negative, which explains the 70.1.",
                          "staff": "The staff member kept a calm tone through the service, scoring 78.6."},
              "improvement": {"customer": "Acknowledge the wait early so the customer feels heard.",
                              "staff": "Keep the same steady pace when the customer sounds upset."}},
    "text": {"comment": {"customer": 'The customer said "I have been waiting for twenty minutes".',
                         "staff": 'The staff member apologised: "I am sorry to keep you waiting."'},
             "improvement": {"customer": "Offer a clear next step as soon as the wait comes up.",
                             "staff": 'Confirm changes out loud, as in "I will change it for you now".'}},
    "facial": {"comment": {"customer": "The customer looked mostly neutral, giving 78.6.",
                           "staff": 'The staff member smiled. They said "you are my favourite customer".'},
               "improvement": {"customer": "Keep eye contact while the customer explains the problem.",
                               "staff": "Keep the friendly expression during the waiting part too."}},
}


class FakeOllama:
    """127.0.0.1 上的 /api/chat：記下請求，依序回 replies（用完後回 ANSWER）。"""

    def __init__(self):
        self.requests, self.replies = [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"headers": dict(self.headers), "body": body})
                status, payload, delay = fake.replies.pop(0) if fake.replies else (200, ANSWER, 0)
                time.sleep(delay)
                if status == 200:
                    payload = {"model": MODEL, "message": {"role": "assistant", "content": json.dumps(payload)},
                               "done": True, "done_reason": "stop"}
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:  # 用戶端已經放棄（逾時的測試）
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda request, address: None
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setenv("OLLAMA_BASE_URL", fake.url)
    monkeypatch.setenv("OLLAMA_API_KEY", KEY)
    monkeypatch.setenv("OLLAMA_MODEL", MODEL)
    yield fake
    fake.close()


def put(service, key, body, content_type):
    service.s3.put_object(Bucket=service.S3_BUCKET, Key=key, Body=body, ContentType=content_type)


def prepare(service, db, transcript_row=True, transcript_object=True, **score_changes):
    """一筆分析完成的服務：兩側的分數、兩側的 report-data，逐字稿（tbl_text_storage 的一列＋S3 的 text/）。"""
    add_scored_service(db, SID, START, START + timedelta(seconds=387), **{**SCORES, **score_changes})
    data = report_model.build_report_data(
        SID, source="test",
        audio={"staff": {"positive": 0.5, "neutral": 0.4, "negative": 0.1},
               "customer": {"positive": 0.2, "neutral": 0.5, "negative": 0.3}},
        text={"staff": {"positive": 0.6, "neutral": 0.38, "negative": 0.02},
              "customer": {"positive": 0.1, "neutral": 0.6, "negative": 0.3}},
        facial={"staff": [(t, 1) for t in range(388)], "customer": [(t, 0) for t in range(388)]})
    put(service, report_model.report_data_key(SID), report_model.dumps_report_data(data), "application/json")
    name = f"{SID}_transcript.txt"
    if transcript_row:
        db.query("INSERT INTO tbl_text_storage (service_id, text_name, text_url) VALUES (%s, %s, %s)",
                 SID, name, f"s3://{service.S3_BUCKET}/text/{name}")
    if transcript_object:
        put(service, f"text/{name}", TRANSCRIPT.encode("utf-8"), "text/plain; charset=utf-8")
    return add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)


def generate(api, service, report_id, wait_for_reports):
    assert api.post("/reports", json={"report_id": report_id}).status_code == 202
    wait_for_reports()
    obj = service.s3.get_object(Bucket=service.S3_BUCKET, Key=f"reports/{report_id}.pdf")
    return pdf_text(obj["Body"].read())


def test_ai_explanation_is_checked_printed_and_recorded(api, service, db, ollama, wait_for_reports, caplog):
    report_id = prepare(service, db)
    text = generate(api, service, report_id, wait_for_reports)

    row = report_row(db, report_id)
    assert (row["report_status"], row["ai_model"], row["ai_prompt_version"]) == (
        "ready", MODEL, ollama_explain.PROMPT_VERSION)
    assert f"AI-generated explanation · {MODEL} · " in text
    assert "Explanation unavailable" not in text
    for expected in ("I have been waiting for twenty minutes", "I am sorry to keep you waiting",
                     "steady pace", "The staff member smiled."):
        assert expected in text, expected
    assert "favourite customer" not in text  # 逐字稿裡沒有這句：連同那一句拿掉
    assert "change the address" not in text  # 逐字稿不印在 PDF 上，只有檢查過的引言

    (request,) = ollama.requests
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    body = request["body"]
    assert (body["model"], body["stream"], body["format"]) == (MODEL, False, ollama_explain.ANSWER_SCHEMA)
    document = json.loads(body["messages"][1]["content"].split("\n\n", 1)[1])
    assert document["transcript"][0] == {"speaker": "Customer", "text": "I have been waiting for twenty minutes."}
    assert document["facts"]["customer"]["audio"]["voice_emotion_percent"]["negative"] == 30
    assert document["facts"]["service"]["length_seconds"] == 387
    assert KEY not in caplog.text


def test_ai_failure_still_makes_the_report(api, service, db, ollama, wait_for_reports, caplog):
    ollama.replies.append((429, {"error": "you have reached your usage limit"}, 0))
    report_id = prepare(service, db)
    text = generate(api, service, report_id, wait_for_reports)
    row = report_row(db, report_id)
    assert (row["report_status"], row["failure_reason"], row["ai_model"], row["ai_prompt_version"]) == (
        "ready", None, None, None)
    assert text.count("Explanation unavailable") == 12
    assert "AI-generated explanation" not in text
    assert "usage limit was reached (HTTP 429)" in caplog.text


def test_minus_one_side_and_unreadable_transcript_are_no_data(api, service, db, ollama, wait_for_reports):
    report_id = prepare(service, db, transcript_object=False, customer_audio=-1)  # 有位置、S3 上沒有檔案
    text = generate(api, service, report_id, wait_for_reports)
    assert report_row(db, report_id)["ai_model"] == MODEL
    assert text.count("No data") == 6  # Audio 的顧客兩格、Text 的四格
    assert "steady pace" in text and "Keep eye contact" in text
    assert "I have been waiting" not in text
    document = json.loads(ollama.requests[0]["body"]["messages"][1]["content"].split("\n\n", 1)[1])
    assert document["transcript"] == []
    assert document["no_data_cells"] == ["audio.customer", "text.customer", "text.staff"]


def test_slow_ai_gives_up_within_its_own_budget(api, service, db, ollama, wait_for_reports, monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SEC", "1")
    ollama.replies.append((200, ANSWER, 3))
    report_id = prepare(service, db)
    started = time.monotonic()
    text = generate(api, service, report_id, wait_for_reports)
    assert time.monotonic() - started < 10
    row = report_row(db, report_id)
    assert (row["report_status"], row["failure_reason"], row["ai_model"]) == ("ready", None, None)
    assert text.count("Explanation unavailable") == 12


def test_without_configuration_nothing_is_sent(api, service, db, ollama, wait_for_reports, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY")
    report_id = prepare(service, db)
    text = generate(api, service, report_id, wait_for_reports)
    assert ollama.requests == []
    assert report_row(db, report_id)["ai_model"] is None
    assert text.count("Explanation unavailable") == 12
