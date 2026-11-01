"""設定頁 Help 的 About、Check Connection、Copy Diagnostic Summary（new-feature S3，T40）。

- 每一項探測：成功、失敗、逾時、沒有設定；S3 用 moto，分析服務用本機的小 HTTP 伺服器（只回 GET /healthz）。
- 摘要只放白名單的欄位：測試在環境變數放假的帳密、主機、網址，確認一個都沒有出現在摘要裡。
"""
import importlib.metadata
import json
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import botocore.exceptions
import mysql.connector
import pytest
import requests
from PySide6.QtGui import QGuiApplication

from myapp import diagnostics
from myapp.diagnostics import ConnectionCheck, HelpCenter, Outcome
from myapp.notifications import Notifier
from myapp.tasks import TaskRunner
from tests.integration.helpers import toasts, wait_until

METADATA = {"Formal-Name": "myapp", "Version": "0.0.1", "App-ID": "com.example.myapp"}


# ---------------------------------------------------------------- 小工具


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def health_server(status=200, body=None, delay=0.0, raw=None):
    """只回 GET 的小伺服器；requests 記下收到的 (方法, 路徑)。"""
    server_state = SimpleNamespace(requests=[])

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            server_state.requests.append((self.command, self.path))
            time.sleep(delay)
            data = raw if raw is not None else json.dumps(
                body if body is not None else {"status": "ok", "queue_depth": 0}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _reply
        do_POST = _reply

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    server_state.url = f"http://127.0.0.1:{server.server_port}/analyze"
    try:
        yield server_state
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def silent_server():
    """接受連線但不回應：讀取逾時。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    accepted = []
    stop = threading.Event()

    def accept():
        sock.settimeout(0.1)
        while not stop.is_set():
            try:
                accepted.append(sock.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        stop.set()
        thread.join(2)
        for conn in accepted:
            conn.close()
        sock.close()


def client_error(code, status):
    return botocore.exceptions.ClientError({"Error": {"Code": code, "Message": "secret-host.example"},
                                            "ResponseMetadata": {"HTTPStatusCode": status}}, "GetObject")


class FakeDb:
    """db lane 的替身：cursor() 是 context manager，記下執行的 SQL。"""

    def __init__(self, error=None):
        self.error = error
        self.sql = []

    @contextmanager
    def cursor(self, dictionary=False):
        if self.error is not None:
            raise self.error
        db = self

        class Cursor:
            def execute(self, sql, params=()):
                db.sql.append(sql)

            def fetchall(self):
                return [(1,)]

        yield Cursor()


# ---------------------------------------------------------------- About


def test_about_reads_the_briefcase_metadata_and_this_computer():
    about = diagnostics.about_info(METADATA)
    assert about["appName"] == "myapp" and about["version"] == "0.0.1"
    assert about["python"].startswith("3.13.") and about["qt"].startswith("6.")
    assert about["pyside"].startswith("6.") and about["system"].startswith("macOS ")
    assert about["architecture"] in ("arm64", "x86_64")


def test_about_without_metadata_says_unavailable_instead_of_guessing(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "metadata", missing)
    about = diagnostics.about_info()
    assert about["version"] == "Unavailable" and about["appName"] == "Unavailable"
    assert about["python"] != "Unavailable"


def test_about_reads_the_installed_metadata_when_there_is_one(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "metadata", lambda name: METADATA if name == "myapp" else None)
    assert diagnostics.about_info()["version"] == "0.0.1"


def test_about_fields_that_cannot_be_read_are_unavailable(monkeypatch):
    monkeypatch.setattr(diagnostics.platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagnostics.platform, "release", lambda: "6.1")
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: (_ for _ in ()).throw(OSError("no")))
    about = diagnostics.about_info({})
    assert about["system"] == "Linux 6.1" and about["architecture"] == "Unavailable"
    assert about["version"] == "Unavailable"


# ---------------------------------------------------------------- 網址


@pytest.mark.parametrize("url, health", [
    ("http://10.0.0.5:5000/analyze", "http://10.0.0.5:5000/healthz"),
    ("https://analysis.example/api/analyze/", "https://analysis.example/api/healthz"),
    ("http://127.0.0.1:5056", "http://127.0.0.1:5056/healthz"),
    ("http://host/analyze?x=1#y", "http://host/healthz"),
    ("", ""), ("not a url", ""), ("ftp://host/analyze", ""), (None, ""),
])
def test_health_url_replaces_the_last_part_of_the_analyze_url(url, health):
    assert diagnostics.health_url(url) == health


# ---------------------------------------------------------------- 探測


def test_database_check_runs_select_1(monkeypatch):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    db = FakeDb()
    assert diagnostics.check_database(db) == Outcome(True, "OK")
    assert db.sql == ["SELECT 1"]


def test_database_check_without_settings_does_not_connect(monkeypatch):
    db = FakeDb(error=AssertionError("should not connect"))
    assert diagnostics.check_database(db) == Outcome(False, "DB_NOT_CONFIGURED")


def test_files_check_with_moto_finds_the_bucket(s3):
    outcome = diagnostics.check_files()
    assert outcome.ok and outcome.code == "OK" and "NoSuchKey" in outcome.technical


def test_files_check_when_the_probe_file_exists_reads_one_byte(s3):
    s3.put_object(Bucket="adam-test", Key=diagnostics.S3_PROBE_KEY, Body=b"abc")
    assert diagnostics.check_files() == Outcome(True, "OK", "GetObject on the test file: found")


def test_files_check_treats_access_denied_on_the_missing_file_as_reached(monkeypatch):
    """桌面端的 IAM 沒有 ListBucket：不存在的物件回 AccessDenied，表示連得到、憑證被接受。"""
    monkeypatch.setenv("S3_BUCKET", "adam-media")
    fake = SimpleNamespace(get_object=lambda **kwargs: (_ for _ in ()).throw(client_error("AccessDenied", 403)))
    monkeypatch.setattr(diagnostics, "s3_probe_client", lambda: fake)
    outcome = diagnostics.check_files()
    assert outcome.ok and "AccessDenied" in outcome.technical


def test_files_check_raises_for_a_missing_bucket(s3, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "no-such-bucket")
    with pytest.raises(botocore.exceptions.ClientError) as caught:
        diagnostics.check_files()
    assert diagnostics.classify("files", caught.value)[0] == "S3_BUCKET_NOT_FOUND"


def test_files_check_without_a_bucket_does_not_connect(monkeypatch):
    monkeypatch.setattr(diagnostics, "s3_probe_client", lambda: pytest.fail("should not connect"))
    assert diagnostics.check_files() == Outcome(False, "S3_NOT_CONFIGURED")


def test_files_check_without_credentials(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "adam-media")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with pytest.raises(botocore.exceptions.NoCredentialsError) as caught:
        diagnostics.check_files()
    assert diagnostics.classify("files", caught.value)[0] == "S3_NO_CREDENTIALS"


def test_files_check_with_an_unknown_profile(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "adam-media")
    monkeypatch.setenv("AWS_PROFILE", "adam-desktop")
    with pytest.raises(botocore.exceptions.ProfileNotFound) as caught:
        diagnostics.check_files()
    assert diagnostics.classify("files", caught.value)[0] == "S3_NO_CREDENTIALS"


def aws_env(monkeypatch, endpoint):
    monkeypatch.setenv("S3_BUCKET", "adam-media")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)


def test_files_check_offline(monkeypatch):
    aws_env(monkeypatch, f"http://127.0.0.1:{closed_port()}")
    with pytest.raises(botocore.exceptions.EndpointConnectionError) as caught:
        diagnostics.check_files()
    assert diagnostics.classify("files", caught.value)[0] == "S3_UNREACHABLE"


def test_files_check_times_out_without_retrying(monkeypatch):
    monkeypatch.setattr(diagnostics, "S3_READ_TIMEOUT", 0.3)
    with silent_server() as port:
        aws_env(monkeypatch, f"http://127.0.0.1:{port}")
        started = time.monotonic()
        with pytest.raises(botocore.exceptions.ReadTimeoutError) as caught:
            diagnostics.check_files()
        assert time.monotonic() - started < 3  # 不重試
    assert diagnostics.classify("files", caught.value)[0] == "S3_TIMEOUT"


def test_analysis_check_gets_healthz_and_never_posts_analyze(monkeypatch):
    with health_server(body={"status": "ok", "models": "ready", "queue_depth": 2}) as server:
        monkeypatch.setenv("ANALYZE_API_URL", server.url)
        outcome = diagnostics.check_analysis()
    assert outcome == Outcome(True, "OK", "GET /healthz: HTTP 200, queue 2")
    assert server.requests == [("GET", "/healthz")]


@pytest.mark.parametrize("status, body, raw, expected", [
    (200, {"status": "ok", "models": "loading"}, None, (True, "OK_STARTING")),
    (200, {"status": "ok", "models": "failed"}, None, (False, "API_MODELS_FAILED")),
    (200, {"status": "starting"}, None, (False, "API_BAD_RESPONSE")),
    (200, None, b"<html>proxy</html>", (False, "API_BAD_RESPONSE")),
    (503, None, None, (False, "API_HTTP_503")),
    (404, None, None, (False, "API_HTTP_404")),
])
def test_analysis_check_results(monkeypatch, status, body, raw, expected):
    with health_server(status, body, raw=raw) as server:
        monkeypatch.setenv("ANALYZE_API_URL", server.url)
        outcome = diagnostics.check_analysis()
    assert (outcome.ok, outcome.code) == expected


def test_analysis_check_without_settings_does_not_connect(monkeypatch):
    monkeypatch.setattr(diagnostics.requests, "get", lambda *args, **kwargs: pytest.fail("should not connect"))
    assert diagnostics.check_analysis() == Outcome(False, "API_NOT_CONFIGURED")


def test_analysis_check_offline_and_timeout(monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{closed_port()}/analyze")
    with pytest.raises(requests.ConnectionError) as caught:
        diagnostics.check_analysis()
    assert diagnostics.classify("analysis", caught.value)[0] == "API_UNREACHABLE"

    monkeypatch.setattr(diagnostics, "HTTP_TIMEOUT", (1, 0.3))
    with health_server(delay=1.0) as server:
        monkeypatch.setenv("ANALYZE_API_URL", server.url)
        with pytest.raises(requests.Timeout) as caught:
            diagnostics.check_analysis()
    assert diagnostics.classify("analysis", caught.value)[0] == "API_TIMEOUT"


# ---------------------------------------------------------------- 例外 → 代碼、訊息


@pytest.mark.parametrize("name, error, code", [
    ("database", mysql.connector.Error(errno=2003), "DB_UNREACHABLE"),
    ("database", mysql.connector.Error(errno=2005), "DB_UNREACHABLE"),
    ("database", mysql.connector.Error(errno=2013), "DB_CONNECTION_LOST"),
    ("database", mysql.connector.Error(errno=1045), "DB_SIGN_IN_FAILED"),
    ("database", mysql.connector.Error(errno=1049), "DB_NOT_FOUND"),
    ("database", mysql.connector.Error(errno=2026), "DB_TLS_FAILED"),
    ("database", mysql.connector.InterfaceError("MySQL Connection not available."), "DB_UNREACHABLE"),
    ("database", mysql.connector.Error(errno=1146), "DB_ERROR_1146"),
    ("database", mysql.connector.DatabaseError("odd"), "DB_ERROR"),
    ("database", RuntimeError("DbLane belongs to the db lane thread"), "UNEXPECTED"),
    ("files", client_error("InvalidAccessKeyId", 403), "S3_CREDENTIALS_REJECTED"),
    ("files", client_error("ExpiredToken", 400), "S3_CREDENTIALS_REJECTED"),
    ("files", client_error("SignatureDoesNotMatch", 403), "S3_CREDENTIALS_REJECTED"),
    ("files", client_error("NoSuchBucket", 404), "S3_BUCKET_NOT_FOUND"),
    ("files", client_error("AllAccessDisabled", 403), "S3_ACCESS_DENIED"),
    ("files", client_error("403", 403), "S3_ACCESS_DENIED"),
    ("files", client_error("SlowDown", 503), "S3_HTTP_503"),
    ("files", botocore.exceptions.ClientError({"Error": {"Code": "Weird code!"}}, "GetObject"), "S3_ERROR"),
    ("files", botocore.exceptions.ConnectTimeoutError(endpoint_url="http://secret"), "S3_TIMEOUT"),
    ("files", botocore.exceptions.HTTPClientError(error="x"), "S3_UNREACHABLE"),
    ("files", botocore.exceptions.PartialCredentialsError(provider="env", cred_var="key"), "S3_NO_CREDENTIALS"),
    ("files", ValueError("x"), "UNEXPECTED"),
    ("analysis", requests.ConnectTimeout(), "API_TIMEOUT"),
    ("analysis", requests.exceptions.InvalidURL(), "API_NOT_CONFIGURED"),
    ("analysis", requests.exceptions.TooManyRedirects(), "API_ERROR"),
    ("analysis", ValueError("x"), "UNEXPECTED"),
])
def test_classify_turns_errors_into_fixed_codes_without_messages(name, error, code):
    found, technical = diagnostics.classify(name, error)
    assert found == code and diagnostics.CODE.fullmatch(found)
    assert "secret" not in technical


CODES = ["OK", "OK_STARTING", "DB_NOT_CONFIGURED", "DB_UNREACHABLE", "DB_CONNECTION_LOST", "DB_SIGN_IN_FAILED",
         "DB_NOT_FOUND", "DB_TLS_FAILED", "DB_ERROR_1146", "S3_NOT_CONFIGURED", "S3_NO_CREDENTIALS",
         "S3_CREDENTIALS_REJECTED", "S3_BUCKET_NOT_FOUND", "S3_ACCESS_DENIED", "S3_UNREACHABLE", "S3_TIMEOUT",
         "S3_HTTP_503", "API_NOT_CONFIGURED", "API_UNREACHABLE", "API_TIMEOUT", "API_MODELS_FAILED", "API_HTTP_500",
         "UNEXPECTED"]


@pytest.mark.parametrize("code", CODES)
def test_messages_say_what_happened_and_what_to_do_without_technical_words(code):
    for name in ("database", "files", "analysis"):
        message = diagnostics.outcome_message(name, code)
        assert message.endswith(".") and 1 <= message.count(". ") + 1 <= 3
        for word in ("S3", "ANALYZE_API_URL", ".env", "errno", "HTTP", "bucket", code):
            assert word not in message


def test_messages_name_the_next_step():
    assert diagnostics.outcome_message("database", "DB_UNREACHABLE") == (
        "Can't reach the database. Check your network and try again.")
    assert diagnostics.outcome_message("files", "S3_CREDENTIALS_REJECTED") == (
        "The storage credentials are expired or not valid. Contact your administrator.")
    assert diagnostics.outcome_message("analysis", "API_TIMEOUT") == (
        "The analysis service didn't respond in time. Try again in a moment.")


# ---------------------------------------------------------------- 摘要


def test_summary_lists_only_whitelisted_fields():
    about = diagnostics.about_info(METADATA)
    results = {"database": {"state": "available", "checkedAt": "2026-11-01 10:31:05", "code": "OK",
                            "technical": "OperationalError at secret-db.internal:3306"},
               "files": {"state": "failed", "checkedAt": "2026-11-01 10:31:06", "code": "S3_NO_CREDENTIALS"},
               "analysis": {"state": "notChecked", "checkedAt": "", "code": ""}}
    now = datetime(2026, 11, 1, 10, 32, tzinfo=timezone(timedelta(hours=-7)))
    text = diagnostics.summary_text(about, results, now)
    lines = text.splitlines()
    assert lines[:4] == ["ADAM diagnostic summary", "Created: 2026-11-01 10:32:00 (UTC-07:00)", "App: myapp",
                         "Version: 0.0.1"]
    assert lines[-4:] == ["Database: Available at 2026-11-01 10:31:05 (OK)",
                          "Cloud Storage: Failed at 2026-11-01 10:31:06 (S3_NO_CREDENTIALS)",
                          "Analysis Service: Not checked",
                          "Each check only tests whether that service answers."]
    assert "secret" not in text


def test_summary_drops_odd_characters_and_values_that_do_not_fit_the_whitelist():
    about = {"appName": "myapp", "version": "1.0 <script> http://evil.example/x?token=abc", "python": "3.13",
             "qt": "6.12", "pyside": "6.12", "system": "macOS 26", "architecture": "arm64"}
    results = {"database": {"state": "failed", "checkedAt": "yesterday at db.example", "code": "lower-case host"},
               "files": {"state": "bogus"}}
    text = diagnostics.summary_text(about, results, datetime(2026, 11, 1, 10, 0))
    assert "<" not in text and "/" not in text and "?" not in text and "=" not in text
    assert "Version: 1.0 script httpevil.example" in text  # 只留英數與少數符號，最長 40 個字
    assert "Database: Failed\n" in text and "db.example" not in text and "lower-case" not in text
    assert "Cloud Storage: Not checked" in text
    assert "Created: 2026-11-01 10:00:00\n" in text  # 沒有時區時不寫


# ---------------------------------------------------------------- ConnectionCheck


@pytest.fixture
def services():
    tasks = TaskRunner(lanes={"db": 1, "io": 2})
    services = SimpleNamespace(tasks=tasks, notifier=Notifier(), db=FakeDb())
    yield services
    tasks.shutdown()


def by_name(check):
    return {item["name"]: item for item in check.items}


def test_items_start_not_checked_and_say_what_each_service_does(services):
    check = ConnectionCheck(services)
    assert [(item["label"], item["state"], item["message"], item["checkedAt"]) for item in check.items] == [
        ("Database", "notChecked", "Keeps accounts, services and scores.", ""),
        ("Cloud Storage", "notChecked", "Keeps recordings, charts and reports.", ""),
        ("Analysis Service", "notChecked", "Scores recordings after they're uploaded.", ""),
    ]
    assert not check.checking


def test_check_runs_the_three_probes_in_the_background_once(services, monkeypatch):
    release = threading.Event()
    threads = []

    def probe(outcome):
        def run():
            threads.append(threading.current_thread() is threading.main_thread())
            release.wait(5)
            return outcome
        return run

    monkeypatch.setattr(diagnostics, "check_database", lambda db: probe(Outcome(True, "OK"))())
    monkeypatch.setitem(diagnostics.PROBES, "files", probe(Outcome(False, "S3_NO_CREDENTIALS")))
    monkeypatch.setitem(diagnostics.PROBES, "analysis", probe(Outcome(True, "OK_STARTING", "GET /healthz")))
    check = ConnectionCheck(services)
    finished = []
    check.finished.connect(lambda: finished.append(True))
    assert check.check()
    assert check.checking and {item["state"] for item in check.items} == {"checking"}
    assert not check.check()  # 不重複送出
    release.set()
    wait_until(lambda: not check.checking)
    assert finished == [True] and threads == [False, False, False]
    items = by_name(check)
    assert (items["database"]["state"], items["files"]["state"], items["analysis"]["state"]) == (
        "available", "failed", "available")
    assert items["files"]["message"] == "This computer has no storage credentials. Contact your administrator."
    assert items["analysis"]["message"].startswith("The analysis service is starting.")
    assert items["analysis"]["technical"].startswith("OK_STARTING · GET /healthz · ")
    assert items["analysis"]["technical"].endswith(" s")
    datetime.strptime(items["database"]["checkedAt"], "%Y-%m-%d %H:%M:%S")
    assert check.results()["files"]["code"] == "S3_NO_CREDENTIALS"
    assert toasts(services.notifier) == []  # 檢查失敗不跳 toast


def test_a_failing_probe_is_classified_and_the_others_still_finish(services, monkeypatch):
    services.db = FakeDb(error=mysql.connector.Error(errno=2003, msg="Can't connect to 'secret-db:3306'"))
    monkeypatch.setenv("DB_HOST", "secret-db")
    monkeypatch.setitem(diagnostics.PROBES, "files", lambda: Outcome(True, "OK"))
    monkeypatch.setitem(diagnostics.PROBES, "analysis",
                        lambda: (_ for _ in ()).throw(requests.ReadTimeout("http://secret-api:5000")))
    check = ConnectionCheck(services)
    check.check()
    wait_until(lambda: not check.checking)
    items = by_name(check)
    assert items["database"]["state"] == "failed" and items["database"]["message"] == (
        "Can't reach the database. Check your network and try again.")
    assert items["database"]["technical"].startswith("DB_UNREACHABLE · Error 2003 · ")
    assert items["files"]["state"] == "available"
    assert items["analysis"]["state"] == "failed" and items["analysis"]["technical"].startswith("API_TIMEOUT")
    assert all("secret" not in item["technical"] + item["message"] for item in check.items)


def test_a_probe_still_running_from_before_is_not_sent_again(services, monkeypatch):
    release = threading.Event()
    services.tasks.submit("io", diagnostics.KEY_PREFIX + "files", lambda token, report: release.wait(5))
    monkeypatch.setitem(diagnostics.PROBES, "analysis", lambda: Outcome(True, "OK"))
    check = ConnectionCheck(services)
    assert check.check()
    assert by_name(check)["files"]["state"] == "notChecked"
    wait_until(lambda: not check.checking)
    release.set()
    wait_until(lambda: not services.tasks.isBusy(diagnostics.KEY_PREFIX + "files"))


def test_check_without_services_or_with_every_probe_busy_does_nothing(services):
    assert not ConnectionCheck(None).check()
    release = threading.Event()
    for name, lane in diagnostics.CHECKS:
        services.tasks.submit(lane, diagnostics.KEY_PREFIX + name, lambda token, report: release.wait(5))
    check = ConnectionCheck(services)
    assert not check.check() and not check.checking
    release.set()
    wait_until(lambda: not any(services.tasks.isBusy(diagnostics.KEY_PREFIX + name) for name, _ in diagnostics.CHECKS))


def test_an_outcome_with_an_odd_code_is_stored_as_unexpected(services):
    check = ConnectionCheck(services)
    check._done("files", Outcome(False, "lower case"))
    assert check.results()["files"]["code"] == "UNEXPECTED"


# ---------------------------------------------------------------- HelpCenter：剪貼簿


SECRETS = {"DB_HOST": "secret-db.internal.example", "DB_USER": "adam_secret_user", "DB_PASSWORD": "hunter2-secret",
           "DB_NAME": "adam_secret_db", "S3_BUCKET": "secret-bucket-name", "AWS_ACCESS_KEY_ID": "AKIASECRETKEY",
           "AWS_SECRET_ACCESS_KEY": "wJalrSECRETACCESSKEY", "AWS_SESSION_TOKEN": "SECRETSESSIONTOKEN",
           "AWS_PROFILE": "adam-secret-profile", "AWS_REGION": "us-west-2"}


def test_copy_summary_puts_only_the_whitelist_on_the_clipboard(services, monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    port = closed_port()
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{port}/analyze?key=SECRETQUERY")
    monkeypatch.setenv("AWS_ENDPOINT_URL", f"http://127.0.0.1:{closed_port()}")
    services.db = FakeDb(error=mysql.connector.Error(errno=1045, msg="Access denied for 'adam_secret_user'@'x'"))
    center = HelpCenter(services)
    assert center.connection.check()
    wait_until(lambda: not center.connection.checking, timeout=15)
    assert center.copySummary()
    text = QGuiApplication.clipboard().text()
    assert text == center.summary()
    assert "Database: Failed at " in text and "(DB_SIGN_IN_FAILED)" in text
    assert "Cloud Storage: Failed at " in text and "Analysis Service: Failed at " in text
    for value in [*SECRETS.values(), "SECRETQUERY", str(port), "127.0.0.1", "http", "s3://", "@"]:
        assert value not in text, value
    assert [toast["title"] for toast in toasts(services.notifier)] == ["Diagnostic summary copied"]


def test_help_center_exposes_about_connection_and_storage(services):
    center = HelpCenter(services)
    assert set(center.about) == {"appName", "version", "python", "qt", "pyside", "system", "architecture"}
    assert center.connection.parent() is center and center.storage.parent() is center
    places = center._places()
    assert set(places) == {"recordings", "cache"}


def test_help_center_without_services_still_copies(monkeypatch):
    center = HelpCenter(None)
    assert center.copySummary() and "Database: Not checked" in QGuiApplication.clipboard().text()
    monkeypatch.setattr(diagnostics.QGuiApplication, "clipboard", lambda: None)
    assert not center.copySummary()


def test_help_center_tells_the_reports_page_to_forget_cleared_pages(services):
    calls = []
    reports = SimpleNamespace(forget_pages=lambda: calls.append(True))
    center = HelpCenter(services, reports=reports)
    center.storage.cleared.emit()
    assert calls == [True]
