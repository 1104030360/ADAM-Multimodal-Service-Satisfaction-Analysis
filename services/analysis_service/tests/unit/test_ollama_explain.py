# tests/unit/test_ollama_explain.py — N6 的 AI 說明（ollama_explain.py）。兩個分析服務共用同一份，必須逐位元組相同。
#
# 不連 ollama.com：每個測試在 127.0.0.1 開一個假的 /api/chat，檢查送出的請求，再回各種答案（好的 JSON、壞的 JSON、
# 少欄位、401／404／429／5xx、太慢、轉址……）。每個測試結束時都檢查金鑰和逐字稿沒有出現在 log 裡。
import copy
import http.client
import io
import json
import logging
import os
import socket
import threading
import time
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import ollama_explain as ai
import report_model

KEY = "sk-test-0123456789-DO-NOT-LOG"
MODEL = "gemma4:31b"
NOW = datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc)

# analyzer.py 的 process_text() 寫出來的樣子："\n".join(["Customer: 原文", "Translated: 英文\n", …])
TRANSCRIPT = "\n".join([
    "Customer: 我已經等了二十分鐘了。", "Translated: I have been waiting for twenty minutes.\n",
    "Staff: 很抱歉讓您久等了。", "Translated: I am sorry to keep you waiting.\n",
    "Customer: 我想改訂單的地址。", "Translated: I want to change the address on my order.\n",
    "Staff: 沒問題，我現在幫您改。", "Translated: No problem, I’m changing it for you now.\n",
    "Customer: 我的訂單號碼是 5521。", "Translated: My order number is 5521.\n",
])
SECRET_SENTENCES = ["waiting for twenty minutes", "address on my order", "order number is 5521"]

SCORES = {"customer": {"total": 72.4, "audio": 70.1, "text": 66.0, "facial": 78.6},
          "staff": {"total": 86.3, "audio": 84.0, "text": 89.5, "facial": 85.4}}
REPORT_DATA = {  # report_model.parse_report_data() 的格式
    "audio": {"customer": {"positive": 0.2, "neutral": 0.5, "negative": 0.3},
              "staff": {"positive": 0.5, "neutral": 0.4, "negative": 0.1}},
    "text": {"customer": {"positive": 0.1, "neutral": 0.6, "negative": 0.3},
             "staff": {"positive": 0.7, "neutral": 0.3, "negative": 0.0}},
    "facial": {"customer": [(s, 0) for s in range(10)] + [(s, 1) for s in range(10, 20)],
               "staff": [(s, 1) for s in range(20)]},
}
GOOD_CELLS = {
    "audio": {
        "comment": {"customer": "The customer scored 70.1; their voice was 30% negative while waiting.",
                    "staff": "The staff member kept a calm, mostly positive tone and scored 84.0."},
        "improvement": {"customer": "Acknowledge the wait early so the customer feels heard.",
                        "staff": "Keep the same steady pace when the customer sounds upset."},
    },
    "text": {
        "comment": {"customer": 'The customer said "I have been waiting for twenty minutes", which lowered the '
                                'score to 66.0.',
                    "staff": 'The staff member apologised: "I am sorry to keep you waiting."'},
        "improvement": {"customer": 'Offer a clear next step as soon as the customer mentions "the address on my '
                                    'order".',
                        "staff": 'Confirm the change out loud, as in "I\'m changing it for you now".'},
    },
    "facial": {
        "comment": {"customer": "The customer looked mostly neutral at first and mostly positive later (78.6).",
                    "staff": "The staff member smiled through most of the service and scored 85.4."},
        "improvement": {"customer": "Keep eye contact while the customer explains the problem.",
                        "staff": "Keep the friendly expression during the waiting part too."},
    },
}


def good_cells():
    return copy.deepcopy(GOOD_CELLS)


def chat_reply(content, **extra):
    """/api/chat 不串流的回答；content 是 dict 時轉成 JSON 字串（format 要求的輸出）。"""
    if not isinstance(content, str):
        content = json.dumps(content)
    body = {"model": MODEL, "created_at": "2026-11-01T20:30:00Z",
            "message": {"role": "assistant", "content": content},
            "done": True, "done_reason": "stop", "eval_count": 420}
    body.update(extra)
    return body


# =============================================================================
# 假的 Ollama
# =============================================================================
class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # 用戶端已經放棄（逾時測試）時寫回去會斷線：不印
        pass


class FakeOllama:
    """127.0.0.1 上的 /api/chat。replies 依序用完後回 GOOD_CELLS。"""

    def __init__(self):
        self.requests = []
        self.replies = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload, delay, headers = fake.replies.pop(0) if fake.replies else \
                    (200, chat_reply(GOOD_CELLS), 0.0, {})
                if delay:
                    time.sleep(delay)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def reply(self, payload, status=200, delay=0.0, headers=None):
        self.replies.append((status, payload, delay, headers or {}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    server = FakeOllama()
    yield server
    server.close()


@pytest.fixture
def config(ollama):
    return ai.Config(ollama.url, MODEL, timeout=5.0, api_key=KEY)


@pytest.fixture(autouse=True)
def nothing_secret_in_logs(caplog):
    """每個測試：金鑰、逐字稿的句子都不能出現在 log 裡（包含 DEBUG）。"""
    caplog.set_level(logging.DEBUG)
    yield
    text = caplog.text
    assert KEY not in text
    for sentence in SECRET_SENTENCES:
        assert sentence not in text


def run(config, scores=SCORES, report_data=REPORT_DATA, transcript=TRANSCRIPT, **kwargs):
    kwargs.setdefault("now", NOW)
    return ai.explain(scores, report_data, transcript, config=config, duration_sec=387, **kwargs)


def user_document(request):
    content = request["body"]["messages"][1]["content"]
    return json.loads(content.split("\n\n", 1)[1])


# =============================================================================
# 請求與好的回答
# =============================================================================
def test_good_answer_gives_every_cell_and_the_labels(ollama, config):
    result = run(config)
    assert result is not None
    assert result.cells == GOOD_CELLS  # 都通過檢查：一字不改
    assert result.cell("text", "comment", "customer").startswith('The customer said "I have been')
    assert (result.model, result.prompt_version, result.generated_at) == (MODEL, "n6-v1", NOW)


def test_request_shape_follows_the_ollama_chat_api(ollama, config):
    run(config)
    (request,) = ollama.requests
    assert request["path"] == "/api/chat"
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    assert request["headers"]["Content-Type"] == "application/json"
    body = request["body"]
    assert body["model"] == MODEL
    assert body["stream"] is False
    assert body["format"] == ai.ANSWER_SCHEMA
    assert body["options"]["temperature"] <= 0.2
    assert "think" not in body  # 沒設 OLLAMA_THINK：用模型的預設
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == ai.SYSTEM_PROMPT
    assert json.dumps(ai.ANSWER_SCHEMA, separators=(",", ":")) in ai.SYSTEM_PROMPT


def test_schema_asks_for_exactly_three_modalities_two_parts_two_sides():
    schema = ai.ANSWER_SCHEMA
    assert schema["required"] == ["audio", "text", "facial"]
    for modality in ai.MODALITIES:
        part = schema["properties"][modality]
        assert part["required"] == ["comment", "improvement"]
        for name in ai.PARTS:
            assert part["properties"][name]["required"] == ["customer", "staff"]
            assert part["properties"][name]["properties"]["customer"] == {"type": "string"}


def test_document_sends_scores_statistics_and_english_lines_only(ollama, config):
    run(config)
    document = user_document(ollama.requests[0])
    facts = document["facts"]
    assert facts["service"] == {"score_range": [0, 100], "length_seconds": 387}
    assert facts["customer"]["audio"] == {"score": 70.1, "band": "Fair",
                                          "voice_emotion_percent": {"positive": 20, "neutral": 50, "negative": 30}}
    assert facts["customer"]["text"]["word_sentiment_percent"] == {"positive": 10, "neutral": 60, "negative": 30}
    assert facts["customer"]["text"]["transcript_lines"] == 3
    assert facts["staff"]["overall"] == {"score": 86.3, "band": "Good"}
    assert facts["customer"]["text"]["band"] == "Needs improvement"
    assert facts["customer"]["facial"]["expression_percent"] == {"positive": 50, "neutral": 50, "negative": 0}
    assert facts["customer"]["facial"]["first_half"] == "mostly neutral"
    assert facts["customer"]["facial"]["second_half"] == "mostly positive"
    assert facts["customer"]["facial"]["observed_seconds"] == {"from": 0, "to": 19}
    assert document["transcript"][:2] == [
        {"speaker": "Customer", "text": "I have been waiting for twenty minutes."},
        {"speaker": "Staff", "text": "I am sorry to keep you waiting."}]
    assert document["no_data_cells"] == []
    body = json.dumps(ollama.requests[0]["body"], ensure_ascii=False)
    assert "我已經等了" not in body  # 原文不送：引言只檢查英文翻譯


def test_think_is_sent_only_when_configured(ollama):
    run(ai.Config(ollama.url, MODEL, timeout=5.0, think=False, api_key=KEY))
    run(ai.Config(ollama.url, MODEL, timeout=5.0, think="low", api_key=KEY))
    assert [r["body"]["think"] for r in ollama.requests] == [False, "low"]


def test_generated_at_defaults_to_utc_now(ollama, config):
    before = datetime.now(timezone.utc)
    result = ai.explain(SCORES, REPORT_DATA, TRANSCRIPT, config=config)
    assert before <= result.generated_at <= datetime.now(timezone.utc)
    assert result.generated_at.tzinfo == timezone.utc


# =============================================================================
# 壞的回答
# =============================================================================
@pytest.mark.parametrize("content", ["not json at all", "[1, 2, 3]", '{"audio": "fine"}', "", "   "])
def test_malformed_content_gives_none(ollama, config, content, caplog):
    ollama.reply(chat_reply(content))
    assert run(config) is None
    assert "AI explanation unavailable" in caplog.text


@pytest.mark.parametrize("content", ["{ not json }", "text { still: not json } text"])
def test_braces_without_json_give_none(ollama, config, content):
    ollama.reply(chat_reply(content))
    assert run(config) is None


@pytest.mark.parametrize("wrap", ["```json\n{}\n```", "Here is the JSON:\n{}\nDone."])
def test_json_wrapped_in_a_fence_or_words_is_still_read(ollama, config, wrap):
    ollama.reply(chat_reply(wrap.replace("{}", json.dumps(GOOD_CELLS))))
    assert run(config) is not None


@pytest.mark.parametrize("path", [("text",), ("facial", "improvement"), ("audio", "comment", "staff")])
def test_missing_field_gives_none(ollama, config, path):
    cells = good_cells()
    target = cells
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    ollama.reply(chat_reply(cells))
    assert run(config) is None


@pytest.mark.parametrize("value", [5, None, ["a"], {"x": "y"}, True])
def test_wrong_type_gives_none(ollama, config, value):
    cells = good_cells()
    cells["facial"]["comment"]["customer"] = value
    ollama.reply(chat_reply(cells))
    assert run(config) is None


def test_extra_fields_are_ignored(ollama, config):
    cells = good_cells()
    cells["overall"] = {"comment": "extra"}
    cells["audio"]["comment"]["manager"] = "extra"
    ollama.reply(chat_reply(cells))
    result = run(config)
    assert result is not None and "overall" not in result.cells


def test_empty_cell_gives_none(ollama, config, caplog):
    cells = good_cells()
    cells["audio"]["improvement"]["staff"] = "   "
    ollama.reply(chat_reply(cells))
    assert run(config) is None
    assert "empty audio.improvement.staff" in caplog.text


def test_empty_cell_is_fine_where_the_code_writes_no_data(ollama, config):
    cells = good_cells()
    cells["audio"]["comment"]["customer"] = ""
    cells["audio"]["improvement"]["customer"] = ""
    ollama.reply(chat_reply(cells))
    scores = copy.deepcopy(SCORES)
    scores["customer"]["audio"] = -1
    result = run(config, scores=scores)
    assert result.cell("audio", "comment", "customer") == ai.NO_DATA


@pytest.mark.parametrize("reply, reason", [
    (b"<html>busy</html>", "not JSON"),
    ({"error": "the model failed to generate a response"}, "answered with an error"),
    (chat_reply(json.dumps(GOOD_CELLS)[:200], done_reason="length"), "cut off"),
    ({"model": MODEL, "done": True}, "no message.content"),
    (chat_reply("", message={"role": "assistant", "content": "", "thinking": "hmm"}), "no message.content"),
])
def test_bad_response_bodies_give_none(ollama, config, reply, reason, caplog):
    ollama.reply(reply)
    assert run(config) is None
    assert reason in caplog.text


def test_too_large_response_gives_none(ollama, config, monkeypatch, caplog):
    monkeypatch.setattr(ai, "MAX_RESPONSE_BYTES", 100)
    assert run(config) is None
    assert "too large" in caplog.text


# =============================================================================
# HTTP 錯誤、逾時、連線
# =============================================================================
@pytest.mark.parametrize("status, reason", [
    (401, "API key was refused (HTTP 401)"),
    (403, "API key was refused (HTTP 403)"),
    (404, "model was not found (HTTP 404); check OLLAMA_MODEL"),
    (429, "usage limit was reached (HTTP 429)"),
    (400, "request was refused (HTTP 400)"),
    (500, "Ollama server error (HTTP 500)"),
    (502, "Ollama server error (HTTP 502)"),
])
def test_http_errors_give_none_with_a_short_reason(ollama, config, status, reason, caplog):
    ollama.reply({"error": f"something about {KEY}"}, status=status)  # 伺服器回的內容不進 log
    assert run(config) is None
    assert reason in caplog.text
    assert "something about" not in caplog.text
    assert len(ollama.requests) == 1  # 預算 5 秒：不夠重試


def test_server_error_is_retried_once_when_time_allows(ollama, monkeypatch):
    monkeypatch.setattr(ai, "RETRY_PAUSE_SEC", 0)
    config = ai.Config(ollama.url, MODEL, timeout=30.0, api_key=KEY)
    ollama.reply({"error": "busy"}, status=503)
    assert run(config) is not None
    assert len(ollama.requests) == 2

    ollama.requests.clear()
    ollama.reply({"error": "busy"}, status=500)
    ollama.reply({"error": "busy"}, status=502)
    assert run(config) is None
    assert len(ollama.requests) == 2  # 只重試一次


@pytest.mark.parametrize("status", [400, 503])
def test_the_retry_leaves_out_format(ollama, monkeypatch, status, caplog):
    """官方文件說 Ollama 的雲端不支援 structured outputs：拒絕 format（400）或暫時出錯時，重試一次不帶 format。"""
    monkeypatch.setattr(ai, "RETRY_PAUSE_SEC", 0)
    ollama.reply({"error": "format is not supported"}, status=status)
    result = run(ai.Config(ollama.url, MODEL, timeout=30.0, api_key=KEY))
    assert result is not None and result.cells == GOOD_CELLS
    first, second = (request["body"] for request in ollama.requests)
    assert first["format"] == ai.ANSWER_SCHEMA and "format" not in second
    assert {k: v for k, v in first.items() if k != "format"} == second  # 其他都一樣（含系統提示裡的 schema）
    assert f"Ollama request failed (HTTP {status}); trying once more without format" in caplog.text


def test_an_answer_that_ignores_format_is_still_read_when_it_is_json(ollama, config):
    """雲端忽略 format 時，模型照系統提示回 JSON（可能包在 ``` 裡）：一樣讀得懂、一樣檢查。"""
    ollama.reply(chat_reply("```json\n" + json.dumps(GOOD_CELLS, indent=2) + "\n```"))
    assert run(config).cells == GOOD_CELLS


@pytest.mark.parametrize("status", [401, 404, 429])
def test_client_errors_are_not_retried(ollama, monkeypatch, status):
    monkeypatch.setattr(ai, "RETRY_PAUSE_SEC", 0)
    ollama.reply({"error": "no"}, status=status)
    assert run(ai.Config(ollama.url, MODEL, timeout=30.0, api_key=KEY)) is None
    assert len(ollama.requests) == 1


def test_slow_answer_past_the_budget_gives_none(ollama, caplog):
    ollama.reply(chat_reply(GOOD_CELLS), delay=3.0)
    started = time.monotonic()
    assert run(ai.Config(ollama.url, MODEL, timeout=0.5, api_key=KEY)) is None
    assert time.monotonic() - started < 2.0
    assert "within" in caplog.text


def test_connection_refused_gives_none(caplog):
    with socket.socket() as probe:  # 拿一個沒有人聽的連接埠
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert run(ai.Config(f"http://127.0.0.1:{port}", MODEL, timeout=5.0, api_key=KEY)) is None
    assert "connection error" in caplog.text


def test_redirects_are_not_followed_so_the_key_stays_put(ollama, config, caplog):
    other = FakeOllama()
    try:
        ollama.reply({}, status=302, headers={"Location": f"{other.url}/api/chat"})
        assert run(config) is None
        assert other.requests == []
        assert "redirects are not followed (HTTP 302)" in caplog.text
    finally:
        other.close()


class FakeOpener:
    """取代 _OPENER：依序丟出例外或回傳回答，測試 urllib 不容易在本機造出來的錯誤（連線逾時、連線被中斷）。"""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def open(self, request, timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())


@pytest.mark.parametrize("outcomes, calls, reason", [
    ([urllib.error.URLError(TimeoutError("connect"))], 1, "no answer within the time limit"),
    ([urllib.error.URLError(ConnectionResetError()), chat_reply(GOOD_CELLS)], 2, None),
    ([urllib.error.URLError(OSError()), urllib.error.URLError(ConnectionResetError())], 2,
     "connection error (ConnectionResetError)"),
    ([http.client.RemoteDisconnected(), chat_reply(GOOD_CELLS)], 2, None),
    ([http.client.RemoteDisconnected(), ConnectionResetError()], 2, "connection error (ConnectionResetError)"),
])
def test_connection_problems_are_retried_once(monkeypatch, outcomes, calls, reason, caplog):
    opener = FakeOpener(*outcomes)
    monkeypatch.setattr(ai, "_OPENER", opener)
    monkeypatch.setattr(ai, "RETRY_PAUSE_SEC", 0)
    result = run(ai.Config("https://ollama.com", MODEL, timeout=30.0, api_key=KEY))
    assert opener.calls == calls
    assert (result is None) == (reason is not None)
    if reason:
        assert reason in caplog.text


def test_no_time_left_means_no_request(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr(ai, "_OPENER", opener)
    with pytest.raises(ai._Failure, match="time limit"):
        ai._post(ai.Config("https://ollama.com", MODEL, api_key=KEY), (b"{}", b"{}"), time.monotonic() - 1)
    assert opener.calls == 0


def test_the_total_budget_holds_even_if_a_read_never_ends(monkeypatch, caplog):
    monkeypatch.setattr(ai, "_post", lambda config, payload, deadline: time.sleep(2))
    started = time.monotonic()
    assert run(ai.Config("https://ollama.com", MODEL, timeout=0.3, api_key=KEY)) is None
    assert time.monotonic() - started < 1.5
    assert "no answer within 0 s (OLLAMA_TIMEOUT_SEC)" in caplog.text


def test_header_problems_never_put_the_key_in_the_log(ollama, caplog):
    bad = ai.Config(ollama.url, MODEL, timeout=5.0, api_key=KEY + "\nX-Evil: 1")  # http.client 的錯誤訊息含標頭的值
    assert run(bad) is None
    assert "unexpected" in caplog.text
    assert ollama.requests == []


# =============================================================================
# 防止亂寫：引言、數字、No data
# =============================================================================
def test_unbalanced_quotation_mark_removes_the_sentence(ollama, config, caplog):
    cells = good_cells()
    cells["text"]["comment"]["customer"] = 'The customer was tense. They said "I have been waiting.'
    ollama.reply(chat_reply(cells))
    assert run(config).cell("text", "comment", "customer") == "The customer was tense."
    assert "unbalanced quotation mark" in caplog.text


def test_quote_on_the_wrong_side_is_removed_with_its_sentence(ollama, config, caplog):
    cells = good_cells()
    cells["text"]["comment"]["staff"] = ('The staff member stayed calm. '
                                         'The customer said "I have been waiting for twenty minutes".')
    cells["text"]["comment"]["customer"] = 'The customer was tense. They heard "I am sorry to keep you waiting".'
    ollama.reply(chat_reply(cells))
    result = run(config)
    assert result.cell("text", "comment", "staff") == "The staff member stayed calm."
    assert result.cell("text", "comment", "customer") == "The customer was tense."
    assert "2 quote from the other side" in caplog.text


@pytest.mark.parametrize("quote", ["this is the worst shop ever", "I waited for twenty minutes",
                                   "wait", "I have been waiting for twenty minutes and nobody came", ""])
def test_quote_not_in_the_transcript_is_removed(ollama, config, quote, caplog):
    cells = good_cells()
    cells["text"]["comment"]["customer"] = f'The customer was upset. They said "{quote}" twice.'
    ollama.reply(chat_reply(cells))
    assert run(config).cell("text", "comment", "customer") == "The customer was upset."
    assert "quote not in the transcript" in caplog.text


@pytest.mark.parametrize("cell", [
    'The customer said “i HAVE been   waiting for twenty minutes.”',
    'The customer said "I have been ... twenty minutes".',
    'The customer said "I have been … minutes".',
    'The customer gave "my order number is 5521" and waited.',
    'The customer mentioned order 5521.',
    "The customer said 'I have been waiting for twenty minutes'.",
    "The customer said ‘I want to change the address’.",
    'The customer said 「I have been waiting」.',
])
def test_quotes_match_ignoring_case_spacing_curly_quotes_and_ellipses(ollama, config, cell):
    cells = good_cells()
    cells["text"]["comment"]["customer"] = cell
    ollama.reply(chat_reply(cells))
    kept = run(config).cell("text", "comment", "customer")
    assert kept != ai.NO_DATA and "customer" in kept


@pytest.mark.parametrize("cell, reason", [
    ("The customer was upset. They said 'this is the worst shop ever'.", "quote not in the transcript"),
    ("The customer was upset. They heard ‘I am sorry to keep you waiting’.", "quote from the other side"),
    ("The customer was upset. They said 'I have been waiting.", "unbalanced quotation mark"),
])
def test_single_quoted_quotes_are_checked_too(ollama, config, cell, reason, caplog):
    cells = good_cells()
    cells["text"]["comment"]["customer"] = cell
    ollama.reply(chat_reply(cells))
    assert run(config).cell("text", "comment", "customer") == "The customer was upset."
    assert reason in caplog.text


def test_apostrophes_are_not_quotes(ollama, config):
    cells = good_cells()
    cells["audio"]["comment"]["customer"] = "The customer’s tone was tense, and the customers' wait didn't help."
    ollama.reply(chat_reply(cells))
    assert run(config).cell("audio", "comment", "customer") == (
        "The customer's tone was tense, and the customers' wait didn't help.")


def test_curly_apostrophe_in_the_transcript_matches_a_straight_one(ollama, config):
    ollama.reply(chat_reply(GOOD_CELLS))  # 逐字稿是 I’m，回答是 I'm
    assert "changing it for you now" in run(config).cell("text", "improvement", "staff")


def test_lines_without_a_known_speaker_are_never_quotable(ollama, config):
    transcript = TRANSCRIPT + "\nManager: 請稍等。\nTranslated: Please hold the line.\n\nTranslated: Orphan line.\n"
    assert all(line.text not in ("Please hold the line.", "Orphan line.")
               for line in ai.parse_transcript(transcript))
    cells = good_cells()
    cells["text"]["comment"]["staff"] = 'The staff member said "please hold the line". They were calm.'
    ollama.reply(chat_reply(cells))
    assert run(config, transcript=transcript).cell("text", "comment", "staff") == "They were calm."


def test_a_cell_with_nothing_left_is_unavailable_not_no_data(ollama, config):
    """No data 只給分數 -1、Text 沒有句子的格子；這一格有分數，只是說明不能用。"""
    cells = good_cells()
    cells["audio"]["comment"]["staff"] = 'They said "you are the best customer today".'
    ollama.reply(chat_reply(cells))
    result = run(config)
    assert result.cell("audio", "comment", "staff") == ai.UNAVAILABLE == report_model.EXPLANATION_UNAVAILABLE
    assert result.cell("audio", "comment", "customer") == GOOD_CELLS["audio"]["comment"]["customer"]


def test_everything_rejected_gives_none(ollama, config, caplog):
    fake = {m: {p: {s: 'They said "something nobody said".' for s in ai.SIDES} for p in ai.PARTS}
            for m in ai.MODALITIES}
    ollama.reply(chat_reply(fake))
    assert run(config) is None
    assert "nothing usable" in caplog.text


@pytest.mark.parametrize("cell, kept", [
    ("The customer scored 55.5 in Audio. Their tone was tense.", "Their tone was tense."),
    ("The customer scored 70 in Audio.", "The customer scored 70 in Audio."),  # 70.1 四捨五入
    ("The customer scored 70.1 out of 100.", "The customer scored 70.1 out of 100."),
    ("Their voice was 30% negative.", "Their voice was 30% negative."),
    ("Their voice was 35% negative. It was tense.", "It was tense."),
    ("The customer scored 84.0 like the staff. It was tense.", "It was tense."),  # 服務人員的分數
    ("It took 387 seconds.", "It took 387 seconds."),  # 共用的資料
    ("The 1st try failed.", "The 1st try failed."),  # 不是單獨的數字
])
def test_numbers_must_come_from_that_sides_input(ollama, config, cell, kept):
    cells = good_cells()
    cells["audio"]["comment"]["customer"] = cell
    ollama.reply(chat_reply(cells))
    assert run(config).cell("audio", "comment", "customer") == kept


def test_minus_one_side_is_no_data_and_the_other_side_stays(ollama, config):
    scores = copy.deepcopy(SCORES)
    scores["customer"]["audio"] = -1
    scores["staff"]["facial"] = -1.0
    ollama.reply(chat_reply(GOOD_CELLS))  # 模型照樣寫了那兩側：程式蓋掉
    result = run(config, scores=scores)
    assert result.cell("audio", "comment", "customer") == ai.NO_DATA
    assert result.cell("audio", "improvement", "customer") == ai.NO_DATA
    assert result.cell("audio", "comment", "staff") == GOOD_CELLS["audio"]["comment"]["staff"]
    assert result.cell("facial", "comment", "staff") == ai.NO_DATA
    assert result.cell("facial", "improvement", "customer") == GOOD_CELLS["facial"]["improvement"]["customer"]
    document = user_document(ollama.requests[0])
    assert document["no_data_cells"] == ["audio.customer", "facial.staff"]
    assert document["facts"]["customer"]["audio"] == {"score": None, "note": "No data"}


def test_no_transcript_makes_text_no_data_and_keeps_the_rest(ollama, config):
    cells = good_cells()
    cells["audio"]["comment"]["staff"] = 'The staff member was calm. They said "no problem".'
    ollama.reply(chat_reply(cells))
    result = run(config, transcript=None)
    for part in ai.PARTS:
        for side in ai.SIDES:
            assert result.cell("text", part, side) == ai.NO_DATA
    assert result.cell("audio", "comment", "staff") == "The staff member was calm."  # 沒有逐字稿：引言都對不上
    assert result.cell("facial", "comment", "customer") == GOOD_CELLS["facial"]["comment"]["customer"]
    document = user_document(ollama.requests[0])
    assert document["transcript"] == []
    assert document["no_data_cells"] == ["text.customer", "text.staff"]


def test_a_side_without_lines_gets_no_data_for_text_only(ollama, config):
    staff_only = "Staff: 你好。\nTranslated: I am sorry to keep you waiting.\n"
    result = run(config, transcript=staff_only)
    assert result.cell("text", "comment", "customer") == ai.NO_DATA
    assert result.cell("text", "comment", "staff") == GOOD_CELLS["text"]["comment"]["staff"]
    assert result.cell("audio", "comment", "customer") == GOOD_CELLS["audio"]["comment"]["customer"]


def test_nothing_to_explain_makes_no_call(ollama, config, caplog):
    scores = {side: {m: -1 for m in ("total", *ai.MODALITIES)} for side in ai.SIDES}
    assert run(config, scores=scores) is None
    assert ollama.requests == []
    assert "no score to explain" in caplog.text


def test_model_writing_no_data_where_there_is_a_score_is_unavailable(ollama, config, caplog):
    cells = good_cells()
    cells["facial"]["improvement"]["staff"] = "No data."
    ollama.reply(chat_reply(cells))
    assert run(config).cell("facial", "improvement", "staff") == ai.UNAVAILABLE
    assert "1 No data written by the model" in caplog.text


def test_only_unavailable_cells_give_none(ollama, config):
    cells = {m: {p: {s: "No data" for s in ai.SIDES} for p in ai.PARTS} for m in ai.MODALITIES}
    ollama.reply(chat_reply(cells))
    assert run(config) is None


def test_markdown_and_bullets_are_tidied(ollama, config):
    cells = good_cells()
    cells["facial"]["comment"]["staff"] = "- **The staff member**   smiled a lot."
    ollama.reply(chat_reply(cells))
    assert run(config).cell("facial", "comment", "staff") == "The staff member smiled a lot."


# =============================================================================
# 長度
# =============================================================================
def test_long_text_keeps_whole_sentences_up_to_the_limit(ollama, config):
    first = "The staff member kept a calm and friendly tone for the whole service."
    second = "They slowed down when the customer sounded upset."
    third = "This is a third sentence that should not be kept."
    cells = good_cells()
    cells["audio"]["comment"]["staff"] = " ".join([first, second, third])
    cells["audio"]["improvement"]["staff"] = " ".join([first, first, first])
    ollama.reply(chat_reply(cells))
    result = run(config)
    assert result.cell("audio", "comment", "staff") == f"{first} {second}"
    assert result.cell("audio", "improvement", "staff") == f"{first} {first}"


def test_one_very_long_sentence_is_cut_between_words(ollama, config):
    cells = good_cells()
    cells["facial"]["comment"]["staff"] = "The staff member " + "smiled warmly and " * 20 + "kept eye contact."
    ollama.reply(chat_reply(cells))
    text = run(config).cell("facial", "comment", "staff")
    assert len(text) <= ai.MAX_CELL_CHARS
    assert text.endswith("and…") or text.endswith("warmly…") or text.endswith("smiled…")


def test_a_cut_inside_a_quote_drops_the_partial_quote(ollama, config):
    cells = good_cells()
    cells["text"]["comment"]["customer"] = ("The customer, who had a long and difficult afternoon with several "
                                            "earlier problems and a late delivery, finally said \"I have been waiting for twenty "
                                            "minutes\" to explain everything.")
    ollama.reply(chat_reply(cells))
    text = run(config).cell("text", "comment", "customer")
    assert len(text) <= ai.MAX_CELL_CHARS and text.count('"') == 0 and text.endswith("…")


def test_every_cell_fits_the_pdf_limits(ollama, config):
    long = "This sentence is long enough to matter in a small cell of the report page. " * 6
    cells = {m: {p: {s: long for s in ai.SIDES} for p in ai.PARTS} for m in ai.MODALITIES}
    ollama.reply(chat_reply(cells))
    result = run(config)
    for m in ai.MODALITIES:
        for p in ai.PARTS:
            for s in ai.SIDES:
                assert len(result.cell(m, p, s)) <= ai.MAX_CELL_CHARS
                assert len(ai.split_sentences(result.cell(m, p, s))) <= ai.MAX_CELL_SENTENCES


@pytest.mark.parametrize("text, sentences", [
    ("One. Two! Three?", ["One.", "Two!", "Three?"]),
    ('He said "I waited. Nobody came." Then he left.', ['He said "I waited. Nobody came."', "Then he left."]),
    ('It scored 78.6 today. Fine.', ["It scored 78.6 today.", "Fine."]),
    ('Really?! Yes.', ["Really?!", "Yes."]),
    ("No ending", ["No ending"]),
])
def test_split_sentences(text, sentences):
    assert ai.split_sentences(text) == sentences


# =============================================================================
# 逐字稿
# =============================================================================
@pytest.mark.parametrize("value, expected", [
    (None, None), (True, None), ("abc", None), (float("nan"), None), (-1, None), (-1.0, None), ("83.94", 83.9),
    (0, 0.0), (85, 85.0),
])
def test_score_value(value, expected):
    assert ai.score_value(value) == expected


@pytest.mark.parametrize("stats", [
    None, "x", {"positive": 0.5}, {"positive": "a", "neutral": 0.1, "negative": 0.1},
    {"positive": -0.5, "neutral": 0.1, "negative": 0.1}, {"positive": 0, "neutral": 0, "negative": 0},
])
def test_bad_proportions_are_left_out(stats):
    assert ai._percents(stats) is None


@pytest.mark.parametrize("points", [None, [], "x", [(0, "a")], [(0,)], [(0, float("inf"))], [5]])
def test_bad_facial_points_are_left_out(points):
    assert ai._facial(points) is None


def test_bad_statistics_only_drop_the_statistics(ollama, config):
    data = {"audio": {"customer": {"positive": "?"}}, "text": "broken", "facial": {"staff": [(0, "x")]}}
    run(config, report_data=data)
    facts = user_document(ollama.requests[0])["facts"]
    assert facts["customer"]["audio"] == {"score": 70.1, "band": "Fair"}
    assert "word_sentiment_percent" not in facts["customer"]["text"]
    assert facts["staff"]["facial"] == {"score": 85.4, "band": "Good"}


def test_parse_transcript_reads_the_analyzer_format():
    lines = ai.parse_transcript(TRANSCRIPT)
    assert [(line.side, line.text) for line in lines] == [
        ("customer", "I have been waiting for twenty minutes."),
        ("staff", "I am sorry to keep you waiting."),
        ("customer", "I want to change the address on my order."),
        ("staff", "No problem, I’m changing it for you now."),
        ("customer", "My order number is 5521."),
    ]
    assert ai.parse_transcript(None) == [] and ai.parse_transcript("") == []
    assert ai.parse_transcript("Customer: 只有原文\n\nStaff: 你好\r\nTranslated:   Hello   there \r\n") == [
        ai.Line("staff", "Hello there")]
    assert ai.parse_transcript("Staff: 嗯\nTranslated:   \n") == []


def test_very_long_transcript_is_cut_in_the_middle_but_still_checked(ollama, config, monkeypatch):
    monkeypatch.setattr(ai, "MAX_TRANSCRIPT_CHARS", 300)
    filler = "".join(f"Staff: 好。\nTranslated: Filler sentence number {i} for the test.\n\n" for i in range(30))
    transcript = TRANSCRIPT + "\n" + filler + "Customer: 好。\nTranslated: Thanks for your help today.\n"
    cells = good_cells()
    cells["text"]["comment"]["staff"] = 'The staff member said "filler sentence number 15 for the test".'
    ollama.reply(chat_reply(cells))
    result = run(config, transcript=transcript)
    document = user_document(ollama.requests[0])
    assert document["facts"]["service"]["transcript_lines_omitted"] > 0
    assert document["transcript"][-1] == {"speaker": "Customer", "text": "Thanks for your help today."}
    assert sum(len(line["text"]) for line in document["transcript"]) <= 300
    assert "filler sentence number 15" in result.cell("text", "comment", "staff")  # 檢查用完整的逐字稿


class FakeS3:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.calls = data, error, []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        if self.error:
            raise self.error
        return {"Body": io.BytesIO(self.data)}


def inputs(s3, key="text/S20261101123000_transcript.txt"):
    return SimpleNamespace(service_id="S20261101123000", service_start=datetime(2026, 11, 1, 12, 30),
                           service_end=datetime(2026, 11, 1, 12, 36, 27), scores=SCORES, report_data=REPORT_DATA,
                           transcript_key=key, s3=s3, bucket="adam-media-test")


def test_explain_report_downloads_the_transcript_and_uses_the_service_length(ollama, config):
    s3 = FakeS3(TRANSCRIPT.encode("utf-8"))
    result = ai.explain_report(inputs(s3), config=config, now=NOW)
    assert result.cell("text", "comment", "staff") == GOOD_CELLS["text"]["comment"]["staff"]
    assert s3.calls == [("adam-media-test", "text/S20261101123000_transcript.txt")]
    assert user_document(ollama.requests[0])["facts"]["service"]["length_seconds"] == 387


def test_explain_report_without_a_transcript_still_explains(ollama, config, caplog):
    result = ai.explain_report(inputs(FakeS3(error=RuntimeError("AccessDenied"))), config=config)
    assert result.cell("text", "comment", "customer") == ai.NO_DATA
    assert result.cell("audio", "comment", "customer") == GOOD_CELLS["audio"]["comment"]["customer"]
    assert "transcript download failed (RuntimeError)" in caplog.text
    assert ai.explain_report(inputs(None, key=None), config=config) is not None


def test_fetch_transcript_only_reads_small_files_under_text(monkeypatch, caplog):
    s3 = FakeS3(b"x" * 50)
    assert ai.fetch_transcript(s3, "b", "avatars/me.png") is None
    assert s3.calls == []
    monkeypatch.setattr(ai, "MAX_TRANSCRIPT_BYTES", 10)
    assert ai.fetch_transcript(s3, "b", "text/S1_transcript.txt") is None
    assert "larger than 10 bytes" in caplog.text
    assert ai.fetch_transcript(FakeS3(b"ok"), "b", "text/a.txt") == "ok"
    assert ai.fetch_transcript(FakeS3(b"ok"), None, "text/a.txt") is None


def test_explain_report_is_off_without_configuration(ollama, caplog):
    s3 = FakeS3(TRANSCRIPT.encode())
    assert ai.explain_report(inputs(s3), environ={"OLLAMA_BASE_URL": ollama.url}) is None
    assert s3.calls == [] and ollama.requests == []
    assert caplog.text.count("AI explanations are off") == 1


def test_explain_never_raises(config, caplog):
    assert ai.explain(object(), None, None, config=config) is None
    assert "unexpected AttributeError" in caplog.text
    assert ai.explain_report(SimpleNamespace(), config=config) is None


# =============================================================================
# 設定
# =============================================================================
def env(**values):
    base = {"OLLAMA_API_KEY": KEY, "OLLAMA_MODEL": MODEL}
    base.update(values)
    return {k: v for k, v in base.items() if v is not None}


def test_load_config_defaults():
    config = ai.load_config(env())
    assert (config.base_url, config.model, config.timeout, config.think) == ("https://ollama.com", MODEL, 90.0, None)
    assert config.api_key == KEY
    assert KEY not in repr(config) and KEY not in str(config)


@pytest.mark.parametrize("values", [
    {"OLLAMA_API_KEY": None}, {"OLLAMA_MODEL": None}, {"OLLAMA_API_KEY": "  "}, {"OLLAMA_MODEL": ""},
    {"OLLAMA_API_KEY": "<從 Ollama 帳號建立的金鑰>"}, {"OLLAMA_MODEL": "<模型名稱>"},
])
def test_missing_key_or_model_turns_explanations_off(values, caplog):
    assert ai.load_config(env(**values)) is None
    assert caplog.text.count("AI explanations are off: OLLAMA_API_KEY or OLLAMA_MODEL is not set") == 1


@pytest.mark.parametrize("key", ["abc def", "abc\tdef", "kéy"])
def test_a_key_that_cannot_be_a_header_turns_explanations_off(key, caplog):
    assert ai.load_config(env(OLLAMA_API_KEY=key)) is None
    assert "contains spaces" in caplog.text


@pytest.mark.parametrize("value, expected", [
    (None, "https://ollama.com"), ("", "https://ollama.com"), ("https://ollama.com/", "https://ollama.com"),
    ("https://ollama.com/api", "https://ollama.com"), ("https://ollama.com/api/", "https://ollama.com"),
    ("https://proxy.example.com:8443/ollama", "https://proxy.example.com:8443/ollama"),
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434"), ("http://localhost:11434/", "http://localhost:11434"),
    ("http://[::1]:11434", "http://[::1]:11434"),
    ("http://ollama.com", None), ("http://10.0.0.5:11434", None), ("ftp://ollama.com", None),
    ("https://user:pw@ollama.com", None), ("https://ollama.com/?x=1", None), ("https://:99999", None),
    ("ollama.com", None), ("https://ollama.com:notaport", None),
])
def test_base_url(value, expected):
    assert ai.base_url(value) == expected


def test_bad_base_url_turns_explanations_off(caplog):
    assert ai.load_config(env(OLLAMA_BASE_URL="http://ollama.com")) is None
    assert "must be an https://" in caplog.text


def test_quotes_around_values_are_removed():
    """docker run --env-file 不會拿掉引號：OLLAMA_API_KEY="…" 的引號會變成金鑰的一部分。"""
    config = ai.load_config({"OLLAMA_API_KEY": f'"{KEY}"', "OLLAMA_MODEL": f"'{MODEL}'",
                             "OLLAMA_BASE_URL": '"https://ollama.com/"', "OLLAMA_TIMEOUT_SEC": '"45"',
                             "OLLAMA_THINK": "'false'"})
    assert (config.api_key, config.model, config.base_url, config.timeout, config.think) == (
        KEY, MODEL, "https://ollama.com", 45.0, False)
    assert ai._setting({"X": '"'}, "X") == '"' and ai._setting({"X": "'a\""}, "X") == "'a\""


@pytest.mark.parametrize("values, expected", [
    ({}, 90.0), ({"OLLAMA_TIMEOUT_SEC": "45"}, 45.0), ({"OLLAMA_TIMEOUT_SEC": "abc"}, 90.0),
    ({"OLLAMA_TIMEOUT_SEC": "-3"}, 90.0), ({"OLLAMA_TIMEOUT_SEC": "nan"}, 90.0),
    ({"OLLAMA_TIMEOUT_SEC": "200"}, 120.0),  # 報表上限 180 − 60
    ({"REPORT_TIMEOUT_SEC": "130"}, 70.0), ({"REPORT_TIMEOUT_SEC": "100"}, 50.0),
    ({"REPORT_TIMEOUT_SEC": "x"}, 90.0),
])
def test_timeout_stays_under_the_report_limit(values, expected):
    assert ai.timeout_budget(values) == expected


def test_lowering_the_timeout_is_logged(caplog):
    assert ai.load_config(env(OLLAMA_TIMEOUT_SEC="500")).timeout == 120.0
    assert "OLLAMA_TIMEOUT_SEC lowered to 120 s" in caplog.text


@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), ("true", True), ("False", False), ("low", "low"), (" HIGH ", "high"), ("2", None),
])
def test_think(value, expected):
    assert ai.parse_think(value) == expected


def test_model_name_is_kept_exactly(ollama):
    config = ai.load_config(env(OLLAMA_MODEL="  gpt-oss:20b ", OLLAMA_BASE_URL=ollama.url))
    ollama.reply(chat_reply(GOOD_CELLS))
    assert run(config).model == "gpt-oss:20b"
    assert ollama.requests[0]["body"]["model"] == "gpt-oss:20b"


# =============================================================================
# 給使用者的連線檢查
# =============================================================================
def test_live_check_needs_configuration():
    ok, text = ai.live_check({})
    assert not ok and text.startswith("Not checked")


def test_live_check_reports_success_and_failure(ollama):
    environ = env(OLLAMA_BASE_URL=ollama.url)
    plain = {m: {p: {s: f"A short {m} {p} note for the {s}." for s in ai.SIDES} for p in ai.PARTS}
             for m in ai.MODALITIES}
    ollama.reply(chat_reply(plain))
    ok, text = ai.live_check(environ)
    assert ok and text.startswith(f"OK: {MODEL}") and "12 of 12 cells" in text
    assert KEY not in text
    assert user_document(ollama.requests[0])["transcript"][0] == {  # 只送假資料
        "speaker": "Customer", "text": "Hi, I would like to change the delivery address on my order."}

    ollama.reply({"error": "unauthorized"}, status=401)
    ok, text = ai.live_check(environ)
    assert not ok and text.startswith("FAILED")


def test_main_prints_usage_without_the_flag(capsys):
    assert ai.main([]) == 2
    assert "--live-check" in capsys.readouterr().err


def test_main_runs_the_live_check(monkeypatch, capsys):
    monkeypatch.setattr(ai, "live_check", lambda: (True, "OK: fake"))
    assert ai.main(["--live-check"]) == 0
    assert capsys.readouterr().out.strip() == "OK: fake"


# =============================================================================
# 接到報表：report_model.explain()
# =============================================================================
def test_report_model_explain_turns_the_result_into_the_report_explanation(ollama, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", KEY)
    monkeypatch.setenv("OLLAMA_MODEL", MODEL)
    monkeypatch.setenv("OLLAMA_BASE_URL", ollama.url)
    s3 = FakeS3(TRANSCRIPT.encode("utf-8"))
    inputs = report_model.ReportInputs(
        service_id="S20261101123000", service_start=datetime(2026, 11, 1, 12, 30),
        service_end=datetime(2026, 11, 1, 12, 36, 27), scores=SCORES, report_data=REPORT_DATA,
        transcript_key="text/S20261101123000_transcript.txt", s3=s3, bucket="adam-media-test")
    explanation = report_model.explain(inputs)
    assert isinstance(explanation, report_model.Explanation)
    assert explanation.text.comment == report_model.Perspectives(**GOOD_CELLS["text"]["comment"])
    assert explanation.facial.improvement == report_model.Perspectives(**GOOD_CELLS["facial"]["improvement"])
    assert (explanation.model, explanation.prompt_version) == (MODEL, ai.PROMPT_VERSION)
    assert report_model.ai_note(explanation).startswith(f"AI-generated explanation · {MODEL} · ")


def test_report_model_explain_is_none_without_configuration(ollama, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    inputs = report_model.ReportInputs("S1", datetime(2026, 11, 1), None, SCORES, REPORT_DATA, None)
    assert report_model.explain(inputs) is None
    assert ollama.requests == []


def test_tests_never_see_a_real_key(ollama_live_environ):
    """tests/conftest.py 一律拿掉 OLLAMA_*：在有 analysis.env 的容器裡跑測試也不會連 Ollama Cloud。

    只有 OLLAMA_LIVE_TEST=1 時，連線檢查（test_ollama_live.py）才從 ollama_live_environ 拿到設定；印出來看不到值。
    """
    for name in ("OLLAMA_API_KEY", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "OLLAMA_TIMEOUT_SEC", "OLLAMA_THINK"):
        assert name not in os.environ
    if os.environ.get("OLLAMA_LIVE_TEST") != "1":
        assert ollama_live_environ == {}
    hidden = type(ollama_live_environ)({"OLLAMA_API_KEY": KEY})
    assert KEY not in repr(hidden) and KEY not in str(hidden) and "OLLAMA_API_KEY" in repr(hidden)
