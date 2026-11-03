# tests/unit/test_service_outcome.py — PLAN1 W1 的服務結果與待辦（service_outcome.py）。兩個分析服務共用同一份，
# 必須逐位元組相同（tools/check_report_copies.py）。
#
# 不連 ollama.com：每個測試在 127.0.0.1 開一個假的 /api/chat，檢查送出的請求，再回各種答案。資料庫和 S3 用很小的替身
# （真的 MySQL 與 moto 在 tests/integration/test_service_outcome.py）。每個測試結束時都檢查金鑰、逐字稿的句子和
# 待辦的字沒有出現在 log 裡。
import copy
import io
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import ollama_explain as ai
import service_outcome as so

KEY = "sk-outcome-test-0123456789-DO-NOT-LOG"
MODEL = "gemma4:31b"

# analyzer.py 的 process_text() 寫出來的樣子："\n".join(["Customer: 原文", "Translated: 英文\n", …])
TRANSCRIPT = "\n".join([
    "Customer: 我搬家了，想改帳戶的地址。", "Translated: Hi, I moved and I want to update the address on my account.\n",
    "Staff: 可以，不過要地址證明，例如水電帳單。",
    "Translated: Sure, I can change it, but I need a proof of address like a utility bill.\n",
    "Customer: 我今天沒帶。", "Translated: I don't have one with me today.\n",
    "Staff: 沒關係，週五帶 2 張帳單來，我們再完成變更。",
    "Translated: No problem, please bring 2 recent bills on Friday and we will finish the change.\n",
    "Customer: 好，謝謝。", "Translated: Okay, thank you.\n",
])
SECRET_WORDS = ["proof of address", "recent bills on Friday", "update the address", "utility bill", "Okay, thank you"]
GOOD = {
    "outcome": "Documents missing",
    "outcome_evidence": "I need a proof of address like a utility bill",
    "todos": [
        {"text": "Customer brings 2 recent bills on Friday.", "evidence": "please bring 2 recent bills on Friday"},
        {"text": "Finish the address change after the bills arrive.", "evidence": "we will finish the change"},
    ],
}


def good(**changes):
    answer = copy.deepcopy(GOOD)
    answer.update(changes)
    return answer


def chat_reply(content, **extra):
    """/api/chat 不串流的回答；content 是 dict 時轉成 JSON 字串（format 要求的輸出）。"""
    if not isinstance(content, str):
        content = json.dumps(content)
    body = {"model": MODEL, "message": {"role": "assistant", "content": content}, "done": True, "done_reason": "stop"}
    body.update(extra)
    return body


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # 用戶端已經放棄（逾時的測試）時寫回去會斷線：不印
        pass


class FakeOllama:
    """127.0.0.1 上的 /api/chat。replies 依序用完後回 GOOD。"""

    def __init__(self):
        self.requests, self.replies = [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload, delay = fake.replies.pop(0) if fake.replies else (200, chat_reply(GOOD), 0.0)
                time.sleep(delay)
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
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

    def reply(self, payload, status=200, delay=0.0):
        self.replies.append((status, chat_reply(payload) if status == 200 and not _is_chat(payload) else payload,
                             delay))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _is_chat(payload):
    return isinstance(payload, dict) and "message" in payload


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
    """每個測試：金鑰、逐字稿的句子、待辦的字都不能出現在 log 裡（包含 DEBUG）。"""
    caplog.set_level(logging.DEBUG)
    yield
    text = caplog.text
    assert KEY not in text
    for words in SECRET_WORDS:
        assert words not in text


def lines():
    return ai.parse_transcript(TRANSCRIPT)


def sentences():
    return [ai.words(line.text) for line in lines()]


def check(answer):
    return so.check_answer(answer, lines(), MODEL)


def user_document(request):
    content = request["body"]["messages"][1]["content"]
    assert content.startswith(so.USER_PREFIX)
    return json.loads(content[len(so.USER_PREFIX):])


# =============================================================================
# 好的回答與送出的請求
# =============================================================================
def test_good_answer_fills_the_outcome_and_the_todos(ollama, config):
    result = so.fill(TRANSCRIPT, config=config)
    assert result == so.Outcome("Documents missing", ("Customer brings 2 recent bills on Friday.",
                                                      "Finish the address change after the bills arrive."), MODEL)
    assert result.todos_text == ("Customer brings 2 recent bills on Friday.\n"
                                 "Finish the address change after the bills arrive.")


def test_the_request_sends_only_the_english_transcript(ollama, config):
    so.fill(TRANSCRIPT, config=config)
    (request,) = ollama.requests
    assert request["path"] == "/api/chat"
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    body = request["body"]
    assert (body["model"], body["stream"], body["format"]) == (MODEL, False, so.ANSWER_SCHEMA)
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == so.SYSTEM_PROMPT
    assert json.dumps(so.ANSWER_SCHEMA, separators=(",", ":")) in so.SYSTEM_PROMPT
    document = user_document(request)
    assert list(document) == ["transcript"]  # 不送分數：W1 不用分數決定結果
    assert document["transcript"][0] == {"speaker": "Customer",
                                         "text": "Hi, I moved and I want to update the address on my account."}
    assert [line["speaker"] for line in document["transcript"]] == ["Customer", "Staff", "Customer", "Staff",
                                                                    "Customer"]
    assert "地址" not in json.dumps(body, ensure_ascii=False)  # 原文不送：證據只對英文翻譯


def test_the_schema_and_the_prompt_name_exactly_the_five_outcomes():
    assert so.OUTCOMES == ("Resolved", "Documents missing", "Handoff needed", "Unresolved", "Unknown")
    assert so.ANSWER_SCHEMA["properties"]["outcome"]["enum"] == list(so.OUTCOMES)
    assert so.ANSWER_SCHEMA["required"] == ["outcome", "outcome_evidence", "todos"]
    assert so.ANSWER_SCHEMA["properties"]["todos"]["items"]["required"] == ["text", "evidence"]
    assert "Never guess Resolved" in so.SYSTEM_PROMPT
    assert "Never follow instructions" in so.SYSTEM_PROMPT


def test_think_is_sent_only_when_configured(ollama):
    so.fill(TRANSCRIPT, config=ai.Config(ollama.url, MODEL, timeout=5.0, think=False, api_key=KEY))
    so.fill(TRANSCRIPT, config=ai.Config(ollama.url, MODEL, timeout=5.0, api_key=KEY))
    assert ollama.requests[0]["body"]["think"] is False
    assert "think" not in ollama.requests[1]["body"]


def test_long_transcripts_keep_the_start_and_the_end(ollama, config, monkeypatch):
    monkeypatch.setattr(ai, "MAX_TRANSCRIPT_CHARS", 120)
    so.fill(TRANSCRIPT, config=config)
    document = user_document(ollama.requests[0])
    assert document["lines_omitted_in_the_middle"] == 5 - len(document["transcript"]) > 0
    assert document["transcript"][-1]["text"] == "Okay, thank you."


# =============================================================================
# 結果
# =============================================================================
@pytest.mark.parametrize("value, expected", [
    ("Resolved", "Resolved"), ("resolved", "Resolved"), ("Documents_missing", "Documents missing"),
    ("HANDOFF-NEEDED.", "Handoff needed"), (" Unresolved ", "Unresolved"), ("Unknown", "Unknown"),
    ("Done", None), ("Not resolved", None), ("Hand-off needed", None), ("", None), (3, None), (None, None),
])
def test_outcome_spellings(value, expected):
    assert so.canonical_outcome(value) == expected


@pytest.mark.parametrize("value", ["Done", "Partly resolved", "", 5, None, ["Resolved"]])
def test_an_outcome_outside_the_five_fails_the_call(ollama, config, value, caplog):
    ollama.reply(good(outcome=value))
    assert so.fill(TRANSCRIPT, config=config) is None
    assert "AI service outcome unavailable: the outcome is not one of the five values" in caplog.text


def test_unknown_needs_no_evidence_and_keeps_stated_todos():
    result = check(good(outcome="Unknown", outcome_evidence=""))
    assert result.outcome == "Unknown" and len(result.todos) == 2


@pytest.mark.parametrize("evidence", [
    "The customer was very happy with the help",  # 逐字稿裡沒有
    "",  # 沒有給
    "thank you",  # 太短：那一句有 3 個字，只引 2 個
    "proof of address like a utility bill on Friday",  # 兩句拼起來
    "…",
])
def test_an_outcome_without_matching_evidence_becomes_unknown(evidence, caplog):
    result = check(good(outcome="Resolved", outcome_evidence=evidence))
    assert result.outcome == "Unknown"  # 看不出來就是 Unknown，不是 Resolved
    assert "Resolved without matching evidence (now Unknown)" in caplog.text


@pytest.mark.parametrize("evidence", [
    "I NEED A PROOF OF ADDRESS, LIKE A UTILITY BILL",  # 大小寫、標點不算
    "“I need a proof of address like a utility bill.”",  # 彎引號、句點
    "I need a proof … utility bill",  # … 省略中間的字
    "Okay, thank you",  # 3 個字的那一句：整句
])
def test_evidence_is_normalised_like_the_n6_quotes(evidence):
    assert check(good(outcome="Handoff needed", outcome_evidence=evidence)).outcome == "Handoff needed"


def test_evidence_matching_rules():
    found = sentences()
    assert so.evidence_line("please bring 2 recent bills on Friday", found) == 3
    assert so.evidence_line("bills … finish the change", found) == 3
    assert so.evidence_line("finish the change … bills", found) is None  # 順序不對
    assert so.evidence_line("Okay", found) is None  # 一個字證明不了什麼
    assert so.evidence_line(None, found) is None
    assert so.evidence_line("bring 2 recent bills", []) is None
    two_words = [ai.words("Yes, done.")]
    assert so.evidence_line("yes done", two_words) == 0  # 那一句只有 2 個字：整句可以


# =============================================================================
# 待辦
# =============================================================================
@pytest.mark.parametrize("item, reason", [
    ({"text": "Waive the annual fee.", "evidence": "The fee is waived for you"}, "to-do without matching evidence"),
    ({"text": "Waive the annual fee.", "evidence": "Okay, thank you"}, "to-do unrelated to its evidence"),
    ({"text": "Refund 500 dollars to the account.", "evidence": "update the address on my account"},
     "to-do with a number not in the transcript"),
    ({"text": "Bring 3 recent bills on Friday.", "evidence": "please bring 2 recent bills on Friday"},
     "to-do with a number not in the transcript"),
    ({"text": "Bring the bills."}, "to-do in the wrong shape"),
    ({"text": 7, "evidence": "please bring 2 recent bills on Friday"}, "to-do in the wrong shape"),
    ("Bring the bills on Friday.", "to-do in the wrong shape"),
    ({"text": "  **  ", "evidence": "please bring 2 recent bills on Friday"}, "empty to-do"),
])
def test_unverifiable_todos_are_dropped(item, reason, caplog):
    result = check(good(todos=[item, GOOD["todos"][1]]))
    assert result.todos == ("Finish the address change after the bills arrive.",)
    assert f"1 {reason}" in caplog.text


def test_todos_are_cleaned_capped_and_not_repeated(caplog):
    long_text = "Finish the address change " + "and double check every other detail " * 6
    items = [
        {"text": "1. **Bring** 2 recent\nbills on Friday", "evidence": "please bring 2 recent bills on Friday"},
        {"text": "- Finish the change", "evidence": "we will finish the change"},
        {"text": "Bring 2 recent bills on Friday.", "evidence": "please bring 2 recent bills on Friday"},  # 重複
        {"text": long_text, "evidence": "we will finish the change"},
        {"text": "Update the address on the account.", "evidence": "I want to update the address on my account"},
        {"text": "Bring a proof of address.", "evidence": "I need a proof of address like a utility bill"},
        {"text": "Change the address.", "evidence": "Sure, I can change it"},  # 第 6、7 項：超過 5 項
        {"text": "Bring the utility bill.", "evidence": "like a utility bill"},
    ]
    result = check(good(todos=items))
    assert result.todos[:2] == ("Bring 2 recent bills on Friday", "Finish the change")
    assert len(result.todos) == so.MAX_TODOS
    assert all("\n" not in todo and len(todo) <= so.MAX_TODO_CHARS for todo in result.todos)
    assert result.todos[2].endswith("…") and result.todos[2].startswith("Finish the address change and")
    assert "1 repeated to-do" in caplog.text and f"2 more than {so.MAX_TODOS} to-dos" in caplog.text


def test_no_stated_next_step_is_an_empty_string():
    result = check(good(outcome="Resolved", outcome_evidence="Sure, I can change it", todos=[]))
    assert (result.outcome, result.todos, result.todos_text) == ("Resolved", (), "")


def test_word_overlap_counts_forms_of_the_same_word():
    assert so.shares_a_word("Mail the cards", ai.words("I mailed your card yesterday"))
    assert so.shares_a_word("Set up the payment plan", ai.words("You can pay monthly"))
    assert so.shares_a_word("Show the ID at the counter", ai.words("I need to see your ID"))  # 兩個字母的實詞
    assert not so.shares_a_word("Explain the idea", ai.words("I need to see your ID"))  # id 和 idea 不是同一個字
    assert not so.shares_a_word("Thank the customer", ai.words("Okay, thank you"))  # 只有客套話
    assert so.numbers("Call at 3:30 on the 1,000 account") == {3.0, 30.0, 1000.0}


# =============================================================================
# 壞的回答與失敗
# =============================================================================
@pytest.mark.parametrize("content", [
    "not json at all", "[1, 2, 3]", '{"outcome": "Resolved"}', "   ",
    json.dumps(good(outcome_evidence=3)), json.dumps(good(todos="Bring the bills")),
    json.dumps({"outcome_evidence": "", "todos": []}),
])
def test_malformed_answers_fail(ollama, config, content, caplog):
    ollama.reply(chat_reply(content))
    assert so.fill(TRANSCRIPT, config=config) is None
    assert "AI service outcome unavailable" in caplog.text
    assert "AI explanation" not in caplog.text  # log 用 W1 的字，不是 N6 的


def test_json_in_a_code_fence_is_read(ollama, config):
    ollama.reply(chat_reply("```json\n" + json.dumps(GOOD) + "\n```"))
    assert so.fill(TRANSCRIPT, config=config).outcome == "Documents missing"


def test_a_cut_off_answer_fails(ollama, config, caplog):
    ollama.reply(chat_reply(GOOD, done_reason="length"))
    assert so.fill(TRANSCRIPT, config=config) is None
    assert "AI service outcome unavailable: the answer was cut off (done_reason=length)" in caplog.text


@pytest.mark.parametrize("status, reason", [
    (401, "the API key was refused (HTTP 401)"), (403, "the API key was refused (HTTP 403)"),
    (404, "the model was not found (HTTP 404)"), (429, "the usage limit was reached (HTTP 429)"),
])
def test_http_errors_fail_without_a_retry(ollama, config, status, reason, caplog):
    ollama.reply({"error": "no"}, status=status)
    assert so.fill(TRANSCRIPT, config=config) is None
    assert len(ollama.requests) == 1
    assert f"AI service outcome unavailable: {reason}" in caplog.text


def test_a_server_error_is_retried_once_without_format(ollama, monkeypatch, caplog):
    monkeypatch.setattr(ai, "RETRY_PAUSE_SEC", 0)
    ollama.reply({"error": "busy"}, status=500)
    ollama.reply({"error": "busy"}, status=500)
    assert so.fill(TRANSCRIPT, config=ai.Config(ollama.url, MODEL, timeout=30.0, api_key=KEY)) is None
    assert ["format" in request["body"] for request in ollama.requests] == [True, False]
    assert "AI service outcome unavailable: Ollama server error (HTTP 500)" in caplog.text


def test_a_slow_answer_fails_within_the_time_limit(ollama, caplog):
    ollama.reply(GOOD, delay=3.0)
    started = time.monotonic()
    assert so.fill(TRANSCRIPT, config=ai.Config(ollama.url, MODEL, timeout=1.0, api_key=KEY)) is None
    assert time.monotonic() - started < 2.5
    # 整次的上限（1 s）和 socket 的 timeout 差不多同時到：哪一個先到都一樣是「沒有回答」
    assert "AI service outcome unavailable: no answer within" in caplog.text


@pytest.mark.parametrize("transcript", [None, "", "Customer: 只有原文、沒有翻譯\n", "Translated: who said this?\n"])
def test_a_transcript_without_lines_makes_no_call(ollama, config, transcript, caplog):
    assert so.fill(transcript, config=config) is None
    assert ollama.requests == []
    assert "AI service outcome skipped: the transcript has no lines" in caplog.text


def test_fill_never_raises(config, monkeypatch, caplog):
    monkeypatch.setattr(so, "build_document", lambda lines: 1 / 0)
    assert so.fill(TRANSCRIPT, config=config) is None
    assert "unexpected ZeroDivisionError" in caplog.text


# =============================================================================
# 資料庫：分析的交易裡清掉上一次的結果；背景工作寫回
# =============================================================================
class FakeDbError(Exception):
    def __init__(self, errno):
        super().__init__(f"MySQL error {errno}")
        self.errno = errno


class FakeCursor:
    def __init__(self, db):
        self.db, self.rowcount, self.rows = db, -1, []

    def execute(self, sql, args=()):
        self.db.statements.append((sql, args))
        if self.db.error is not None:
            raise self.db.error
        if sql == so.CURRENT_SQL:
            self.rows = [(self.db.run,)] if self.db.run is not None else []
        elif sql == so.SAVE_SQL:
            self.rowcount = 1 if args[-1] == self.db.run else 0

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeDb:
    """get_connection() → 連線 → cursor()；記下每一句 SQL。run 是資料表裡 service_outcome_run 的值。"""

    def __init__(self, run="run-1", error=None):
        self.run, self.error, self.statements, self.commits = run, error, [], 0

    def get_connection(self):
        db = self

        class Connection:
            def cursor(self):
                return FakeCursor(db)

            def commit(self):
                db.commits += 1

            def close(self):
                pass

        return Connection()


class FakeS3:
    def __init__(self, data=TRANSCRIPT.encode("utf-8"), error=None):
        self.data, self.error, self.keys = data, error, []

    def get_object(self, Bucket, Key):
        self.keys.append((Bucket, Key))
        if self.error is not None:
            raise self.error
        return {"Body": io.BytesIO(self.data)}


SID = "S20261101093000"
JOB = so.Job(SID, "run-1", f"text/{SID}_transcript.txt")


def environ(ollama, **extra):
    return {"OLLAMA_API_KEY": KEY, "OLLAMA_MODEL": MODEL, "OLLAMA_BASE_URL": ollama.url, **extra}


def test_clear_sets_the_new_run_and_empties_the_three_columns():
    db = FakeDb()
    cursor = FakeCursor(db)
    assert so.clear(cursor, SID, "run-2") is True
    assert db.statements == [(so.CLEAR_SQL, ("run-2", SID))]
    assert "service_outcome = NULL, service_todos = NULL, service_outcome_model = NULL" in so.CLEAR_SQL
    assert len(so.new_run_id()) == 32 and so.new_run_id() != so.new_run_id()


@pytest.mark.parametrize("errno", [1054, 1142, 1143])
def test_clear_without_migration_005_skips_the_outcome(errno, caplog):
    assert so.clear(FakeCursor(FakeDb(error=FakeDbError(errno))), SID, "run-2") is False
    assert f"AI service outcomes are off: the database has no outcome columns or adam_analysis cannot update " \
           f"them (MySQL {errno}); apply migration 005 and its GRANT" in caplog.text


def test_clear_lets_other_database_errors_fail_the_transaction():
    with pytest.raises(FakeDbError):
        so.clear(FakeCursor(FakeDb(error=FakeDbError(2013))), SID, "run-2")
    with pytest.raises(RuntimeError):
        so.clear(FakeCursor(FakeDb(error=RuntimeError("no errno"))), SID, "run-2")


def test_a_job_saves_the_checked_result_for_its_own_run(ollama, caplog):
    db, s3 = FakeDb(), FakeS3()
    jobs = so.OutcomeJobs(db, s3, "bucket", environ=environ(ollama))
    assert jobs.run(JOB) == 1
    assert s3.keys == [("bucket", JOB.transcript_key)]
    assert db.statements[0] == (so.CURRENT_SQL, (SID,))
    assert db.statements[1] == (so.SAVE_SQL, ("Documents missing", "Customer brings 2 recent bills on Friday.\n"
                                              "Finish the address change after the bills arrive.", MODEL, SID, "run-1"))
    assert "AI service outcome for S20261101093000 saved" in caplog.text


def test_a_long_model_name_fits_the_column(ollama):
    db = FakeDb()
    name = "m" * 150
    so.OutcomeJobs(db, FakeS3(), "bucket", environ=environ(ollama, OLLAMA_MODEL=name)).run(JOB)
    assert db.statements[1][1][2] == "m" * so.MODEL_CHARS


def test_a_late_answer_for_an_older_run_is_dropped(ollama, monkeypatch, caplog):
    """開始時還是最新的一次；等 AI 的時候分析又重跑了（編號換了）：寫回 0 列，不蓋掉新的那一次。"""
    db = FakeDb()
    original = so.fill

    def slow_fill(transcript, *, config):
        db.run = "run-2"  # 新的一次分析清掉了三欄、換了編號
        return original(transcript, config=config)

    monkeypatch.setattr(so, "fill", slow_fill)
    assert so.OutcomeJobs(db, FakeS3(), "bucket", environ=environ(ollama)).run(JOB) == 0
    assert db.statements[-1][0] == so.SAVE_SQL
    assert "AI service outcome for S20261101093000 dropped: a newer analysis replaced this one" in caplog.text


def test_a_replaced_run_is_skipped_before_calling_ollama(ollama, caplog):
    db, s3 = FakeDb(run="run-2"), FakeS3()
    assert so.OutcomeJobs(db, s3, "bucket", environ=environ(ollama)).run(JOB) == 0
    assert (ollama.requests, s3.keys) == ([], [])
    assert "skipped: a newer analysis replaced this one" in caplog.text


def test_with_ai_off_nothing_is_read_or_sent(ollama, caplog):
    db, s3 = FakeDb(), FakeS3()
    assert so.OutcomeJobs(db, s3, "bucket", environ={}).run(JOB) == 0
    assert (db.statements, s3.keys, ollama.requests) == ([], [], [])
    assert "AI service outcomes are off: OLLAMA_API_KEY or OLLAMA_MODEL is not set" in caplog.text


@pytest.mark.parametrize("s3", [FakeS3(error=RuntimeError("NoSuchKey")), FakeS3(data=b"")],
                         ids=["missing", "empty"])
def test_without_a_readable_transcript_nothing_is_sent(ollama, s3, caplog):
    db = FakeDb()
    assert so.OutcomeJobs(db, s3, "bucket", environ=environ(ollama)).run(JOB) == 0
    assert ollama.requests == [] and [sql for sql, _ in db.statements] == [so.CURRENT_SQL]
    if s3.error is not None:
        assert "transcript download failed (RuntimeError); the outcome stays not filled" in caplog.text


def test_a_failed_call_writes_nothing(ollama):
    ollama.reply({"error": "limit"}, status=429)
    db = FakeDb()
    assert so.OutcomeJobs(db, FakeS3(), "bucket", environ=environ(ollama)).run(JOB) == 0
    assert [sql for sql, _ in db.statements] == [so.CURRENT_SQL]


def test_the_worker_keeps_going_after_a_failed_job(ollama, caplog):
    """資料庫連不上的那一筆只記一行 log；下一筆照常做。"""

    class Flaky(FakeDb):
        def get_connection(self):
            if not self.statements and self.commits == 0 and not getattr(self, "failed", False):
                self.failed = True
                raise FakeDbError(2003)
            return super().get_connection()

    db = Flaky()
    jobs = so.OutcomeJobs(db, FakeS3(), "bucket", environ=environ(ollama))
    jobs.start()
    jobs.enqueue(SID, "run-1", JOB.transcript_key)
    jobs.enqueue(SID, "run-1", JOB.transcript_key)
    waiter = threading.Thread(target=jobs.jobs.join, daemon=True)
    waiter.start()
    waiter.join(10)
    assert not waiter.is_alive()
    assert "AI service outcome for S20261101093000 not saved: FakeDbError: MySQL error 2003" in caplog.text
    assert [sql for sql, _ in db.statements] == [so.CURRENT_SQL, so.SAVE_SQL]
    assert jobs.worker.name == "outcome-worker" and jobs.worker.daemon
