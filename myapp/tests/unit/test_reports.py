"""報表的桌面端（new-feature N4、N5，T37）：myapp/reports.py 的 ReportsModel 和 media.py 的 PDFHandler。

- 不連網路的部分：/reports 的位址、失敗的原因、清單的文字、表單的檢查、狀態的對應。
- ReportsModel 用假的 TaskRunner（finish() 才執行工作）和假的 DbLane（照 SQL 回傳準備好的列），不等計時器：
  輪詢的開始和停止、通知只在狀態改變（管理員）或新出現（服務人員）時跳、產生、Retry、發佈、選取。
- PDFHandler 用真的 TaskRunner（io、pdf lane），S3 的下載換成複製本機的檔案；PDF 用 PyMuPDF 在暫存資料夾現做。
真的 SQL、moto 的 S3、畫面在整合測試 test_reports_flow.py。
"""
import shutil
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import fitz
import mysql.connector
import pytest
import requests
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage

from myapp import media, reports, storage
from myapp.init import admin_home_init, server_home_init
from myapp.notifications import Notifier
from myapp.reports import Report, ReportsModel, Subject
from myapp.tasks import TaskRunner
from tests.integration.helpers import toasts, wait_until

ADMIN = "00000000-0000-0000-0000-000000000001"
STAFF = "00000000-0000-0000-0000-000000000002"
TODAY = date(2026, 11, 1)


# ── /reports 的位址與失敗的原因 ────────────────────────────────────────────────

@pytest.mark.parametrize("reports_url, analyze_url, expected", [
    ("", "http://10.0.0.5:5056/analyze", "http://10.0.0.5:5056/reports"),
    ("", "http://10.0.0.5:5056/analyze/", "http://10.0.0.5:5056/reports"),     # 結尾的斜線不算
    ("", "https://svc.example/api", "https://svc.example/api/reports"),          # 結尾不是 /analyze：接上
    ("http://other:9000/reports", "http://10.0.0.5:5056/analyze", "http://other:9000/reports"),  # 有設定就用它
    ("", "", ""),
])
def test_reports_url_comes_from_the_env_or_the_analyze_url(monkeypatch, reports_url, analyze_url, expected):
    monkeypatch.setenv("REPORTS_API_URL", reports_url)
    monkeypatch.setenv("ANALYZE_API_URL", analyze_url)
    assert reports.reports_api_url() == expected


class Response:
    def __init__(self, status):
        self.status_code = status


def test_post_sends_only_the_report_id_and_accepts_any_2xx(monkeypatch):
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, json, timeout: sent.append((url, json, timeout)) or Response(202))
    reports.post_report("http://svc/reports", "abc")
    assert sent == [("http://svc/reports", {"report_id": "abc"}, (3, 10))]
    monkeypatch.setattr(requests, "post", lambda url, json, timeout: Response(409))
    with pytest.raises(requests.HTTPError):
        reports.post_report("http://svc/reports", "abc")
    with pytest.raises(reports.ReportServiceNotConfigured):
        reports.post_report("", "abc")


@pytest.mark.parametrize("error, reason", [
    (reports.ReportServiceNotConfigured("x"), "The analysis service address isn't set up on this computer."),
    (requests.ConnectionError("refused"), "Couldn't reach the analysis service."),
    (requests.ReadTimeout("slow"), "The analysis service didn't respond."),
    (requests.HTTPError("HTTP 500 at http://10.0.0.5"), "The analysis service couldn't start this report."),
    (ValueError("boom"), "Couldn't send the request to the analysis service."),
])
def test_failure_reasons_are_short_sentences_without_urls_or_codes(error, reason):
    assert reports.failure_reason(error) == reason
    assert "http" not in reason and len(reason) <= reports.REASON_MAX


# ── 文字 ─────────────────────────────────────────────────────────────────────

def report(report_id="r1", kind="service", status="ready", published=False, **fields):
    defaults = {"service": dict(service_id="S20261101090000", staff_id=STAFF, staff_name="Mia Chen",
                                staff_username="mia"),
                "staff": dict(staff_id=STAFF, staff_name="Mia Chen", staff_username="mia",
                              period_start=date(2026, 10, 1), period_end=date(2026, 10, 31)),
                "company": dict(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31))}[kind]
    values = {**defaults, "status": status, "s3_key": f"reports/{report_id}.pdf",
              "created_at": datetime(2026, 11, 1, 10, 12),
              "published_at": datetime(2026, 11, 1, 10, 20) if published else None, **fields}
    return Report(report_id, kind, **values)


def test_titles_name_the_subject():
    assert reports.report_title(report()) == "S20261101090000 · Mia Chen"
    assert reports.report_title(report(kind="staff")) == "Mia Chen · Oct 1 – Oct 31, 2026"
    assert reports.report_title(report(kind="company")) == "Company · Oct 1 – Oct 31, 2026"
    assert reports.report_title(report(kind="staff", staff_name="", staff_username="mia")) == "mia · Oct 1 – Oct 31, 2026"
    assert reports.report_title(report(staff_name="")) == "S20261101090000"
    across = report(kind="company", period_start=date(2025, 12, 1), period_end=date(2026, 1, 31))
    assert reports.report_title(across) == "Company · Dec 1, 2025 – Jan 31, 2026"


def test_meta_text_and_chip_state_follow_the_status():
    # 管理員：狀態標籤已經說了狀態，第二行只寫種類和時間（已發佈的是發佈的時間）
    assert reports.meta_text(report(status="generating"), True, TODAY) == "Service report · Nov 1, 10:12"
    assert reports.meta_text(report(), True, TODAY) == "Service report · Nov 1, 10:12"
    assert reports.meta_text(report(published=True), True, TODAY) == "Service report · Nov 1, 10:20"
    assert reports.meta_text(report(published=True), False, TODAY) == "Service report · Published Nov 1, 10:20"
    assert reports.meta_text(report(created_at=None), False, TODAY) == "Service report"
    failed = report(status="failed", failure_reason="Timed out")
    assert reports.meta_text(failed, True, TODAY) == "Timed out"  # 管理員：失敗的列寫原因
    assert reports.meta_text(report(status="failed"), True, TODAY) == "This report couldn't be generated."
    assert reports.meta_text(report(kind="company"), True, date(2027, 1, 2)) == "Company report · Nov 1, 2026, 10:12"
    assert [reports.chip_state(item) for item in (report(status="generating"), report(), report(published=True),
                                                   report(status="failed"))] == [
        "generating", "ready", "published", "failed"]


def test_row_actions_follow_the_side_and_the_status():
    row = reports.report_row(report(), True, TODAY)
    assert (row["canPublish"], row["canRetry"], row["publishedText"]) == (True, False, "")
    assert not reports.report_row(report(), False, TODAY)["canPublish"]  # 服務人員不能發佈
    assert not reports.report_row(report(kind="company"), True, TODAY)["canPublish"]  # 全公司報表服務人員看不到
    assert not reports.report_row(report(published=True), True, TODAY)["canPublish"]  # 已經發佈：沒有取消發佈
    published = reports.report_row(report(published=True), True, TODAY)
    assert published["publishedText"] == "Published Nov 1, 10:20"
    failed = reports.report_row(report(status="failed", failure_reason="Timed out"), True, TODAY)
    assert (failed["canRetry"], failed["failureReason"]) == (True, "Timed out")
    assert set(row) == set(reports.REPORT_ROLES)


def test_subject_keys_match_the_qml_form():
    assert Subject("service", service_id="S1").key == "service|S1"
    assert Subject("staff", staff_id="a", start=date(2026, 10, 1), end=date(2026, 10, 31)).key == (
        "staff|a|2026-10-01|2026-10-31")
    assert Subject("company", start=date(2026, 10, 1), end=date(2026, 10, 31)).key == "company|2026-10-01|2026-10-31"
    assert report(kind="staff").subject.key == "staff|" + STAFF + "|2026-10-01|2026-10-31"


def test_options_and_default_period():
    assert reports.staff_option("a", "Mia Chen", "mia") == {"value": "a", "text": "Mia Chen (mia)"}
    assert reports.staff_option("a", "Mia Chen", "") == {"value": "a", "text": "Mia Chen"}
    assert reports.service_option("S1", datetime(2026, 10, 31, 9, 15), TODAY) == {"value": "S1",
                                                                                    "text": "S1 · Oct 31, 09:15"}
    assert reports.default_period(TODAY) == {"start": "2026-11-01", "end": "2026-11-01"}
    assert reports.default_period(date(2026, 10, 17)) == {"start": "2026-10-01", "end": "2026-10-17"}


@pytest.mark.parametrize("args, errors", [
    (("service", "", "", "", ""), {"subject": "Choose a service."}),
    (("staff", "", "", "2026-10-01", "2026-10-31"), {"subject": "Choose a staff member."}),
    (("company", "", "", "", ""), {"start": "Enter a date.", "end": "Enter a date."}),
    (("company", "", "", "2026-13-01", "2026-10-31"),
     {"start": "Enter the date as YYYY-MM-DD, for example 2026-10-31."}),
    (("company", "", "", "2026-10-31", "2026-10-01"),
     {"end": "The To date is before the From date. Choose the same day or later."}),
    (("weekly", "", "", "", ""), {"subject": "Choose a report type."}),
])
def test_form_errors(args, errors):
    subject, found = reports.validate(*args)
    assert subject is None
    assert {field: text for field, text in found.items() if text} == errors


def test_valid_forms_make_subjects():
    assert reports.validate("service", STAFF, "S1", "", "")[0] == Subject("service", service_id="S1")
    staff = reports.validate("staff", STAFF, "S1", " 2026-10-01 ", "2026-10-31")[0]
    assert staff == Subject("staff", staff_id=STAFF, start=date(2026, 10, 1), end=date(2026, 10, 31))
    assert reports.validate("company", STAFF, "", "2026-10-01", "2026-10-01")[0].staff_id == ""


# ── ReportsModel（假的 TaskRunner、假的資料庫）─────────────────────────────────

class FakeRunner:
    """TaskRunner 的替身：工作先不執行，finish() 才在這裡執行並交回結果。"""

    def __init__(self):
        self.running = {}
        self.submitted = []

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        if key in self.running and not replace:
            return False
        self.running[key] = (lane, fn, on_ok, on_err)
        self.submitted.append((lane, key))
        return True

    def isBusy(self, key):
        return key in self.running

    def cancel(self, key):
        self.running.pop(key, None)

    def finish(self, key):
        lane, fn, on_ok, on_err = self.running.pop(key)
        try:
            result = fn(None, None)
        except Exception as error:  # noqa: BLE001  和 TaskRunner 一樣交給 on_err
            if on_err is not None:
                on_err(error)
            return
        if on_ok is not None:
            on_ok(result)

    def finish_all(self, prefix=""):
        for key in [key for key in self.running if key.startswith(prefix)]:
            self.finish(key)


def db_row(item):
    """Report → sp_list_reports 的一列。"""
    return {"report_id": item.report_id, "report_kind": item.kind, "service_id": item.service_id or None,
            "staff_account_id": item.staff_id or None, "staff_name": item.staff_name or None,
            "staff_username": item.staff_username or None, "period_start": item.period_start,
            "period_end": item.period_end, "report_status": item.status, "failure_reason": item.failure_reason or None,
            "s3_key": item.s3_key, "created_at": item.created_at, "published_at": item.published_at}


class FakeDb:
    """DbLane 的替身：記下執行的 SQL；sp_list_reports 回傳 self.reports（新的在前），其他的照設定。"""

    def __init__(self):
        self.reports = []
        self.states = []
        self.staff = []
        self.services = []
        self.rowcount = 1
        self.error = None
        self.executed = []
        self.on_insert = None  # INSERT 時呼叫 on_insert(參數)：模擬新的一列出現在清單裡

    @contextmanager
    def cursor(self, dictionary=False):
        yield FakeCursor(self)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.db.executed.append((sql.split()[0] + " " + sql.split()[1], params))
        if self.db.error is not None:
            raise self.db.error
        if sql.startswith("CALL sp_list_reports"):
            self.rows = [db_row(item) for item in self.db.reports]
        elif sql.startswith("CALL sp_list_service_report_states"):
            self.rows = self.db.states
        elif sql.startswith("SELECT account_id"):
            self.rows = self.db.staff
        elif sql.startswith("SELECT service_id"):
            self.rows = self.db.services
        else:
            self.rowcount = self.db.rowcount
            if sql.startswith("INSERT") and self.db.on_insert is not None:
                self.db.on_insert(params)

    def fetchall(self):
        return self.rows


class FakePdf:
    """PDFHandler 的替身：記下打開、清掉。"""

    def __init__(self):
        self.current = ""
        self.opened = []
        self.cleared = 0

    def open_report(self, report_id, key, title, target_height=0):
        self.current = report_id
        self.opened.append((report_id, key, title, target_height))

    def clear(self):
        self.current = ""
        self.cleared += 1


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def notifier():
    return Notifier()


@pytest.fixture
def pdf():
    return FakePdf()


@pytest.fixture
def make_model(runner, db, notifier, pdf):
    """建立 ReportsModel；測試結束時 stop()（和外殼關掉時一樣），不留計時器給之後的測試。"""
    services = SimpleNamespace(tasks=runner, db=db, notifier=notifier)
    made = []

    def make(admin=True):
        made.append(ReportsModel(services, ADMIN if admin else STAFF, admin=admin, pdf=pdf))
        return made[-1]

    yield make
    for model in made:
        model.stop()


def titles(notifier):
    return [(toast["severity"], toast["title"], toast["detail"], toast["actionId"]) for toast in toasts(notifier)]


def test_opening_the_page_lists_once_and_the_admin_expires_stale_rows_first(make_model, runner, db):
    model = make_model(admin=True)
    assert model.listState == "idle"
    model.pageOpened("reports")
    assert model.listState == "loading" and runner.submitted == [("db", reports.LIST_KEY)]
    db.reports = [report("r2", kind="company"), report("r1")]
    runner.finish(reports.LIST_KEY)
    assert (model.listState, model.count, model.firstReportId()) == ("ready", 2, "r2")
    assert [sql for sql, params in db.executed] == ["UPDATE tbl_report", "CALL sp_list_reports(%s)"]
    assert db.executed[0][1] == ("Timed out",) and db.executed[1][1] == (ADMIN,)
    assert [row["reportId"] for row in model.listModel.rows()] == ["r2", "r1"]


def test_the_detail_card_lists_without_expiring_until_it_polls(make_model, runner, db):
    """明細頁的預算（T31：檔案清單 1 個往返）：報表卡打開時只 CALL；有產生中的、開始輪詢時才順便改逾時的。"""
    model = make_model(admin=True)
    db.reports = [report("r1", status="generating")]
    model.pageOpened("detail")
    runner.finish(reports.LIST_KEY)
    assert [sql for sql, params in db.executed] == ["CALL sp_list_reports(%s)"]
    assert model.polling
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert [sql for sql, params in db.executed][1:] == ["UPDATE tbl_report", "CALL sp_list_reports(%s)"]
    model.refresh()  # Retry
    runner.finish(reports.LIST_KEY)
    assert [sql for sql, params in db.executed][3:] == ["UPDATE tbl_report", "CALL sp_list_reports(%s)"]


def test_staff_never_expire_rows_and_see_an_empty_list(make_model, runner, db):
    model = make_model(admin=False)
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert (model.listState, model.firstReportId()) == ("empty", "")
    assert [sql for sql, params in db.executed] == ["CALL sp_list_reports(%s)"]
    assert db.executed[0][1] == (STAFF,)


def test_admin_polls_only_while_something_is_generating_and_the_page_is_open(make_model, runner, db):
    model = make_model(admin=True)
    db.reports = [report("r1", status="generating")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert model.polling and model._timer.interval() == reports.ADMIN_POLL_S * 1000
    db.reports = [report("r1", status="ready")]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert not model.polling  # 沒有產生中的了
    db.reports = [report("r1"), report("r2", status="generating")]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert model.polling
    model.pageClosed("reports")
    assert not model.polling  # 頁面關掉就停


def test_staff_poll_every_15_seconds_while_a_page_is_open(make_model, runner):
    model = make_model(admin=False)
    model.pageOpened("reports")
    assert model.polling and model._timer.interval() == reports.STAFF_POLL_S * 1000
    model.pageOpened("analysis")
    model.pageClosed("reports")
    assert model.polling  # 分析頁還開著
    model.pageClosed("analysis")
    assert not model.polling
    model.pageClosed("analysis")  # 多關一次也沒關係
    assert not model.polling


def test_poll_skips_while_the_previous_query_is_still_running(make_model, runner):
    model = make_model(admin=False)
    model.pageOpened("reports")
    model.poll()
    assert runner.submitted == [("db", reports.LIST_KEY)]


def test_stop_ends_polling_for_good(make_model, runner, db):
    model = make_model(admin=False)
    model.pageOpened("reports")
    model.stop()
    assert not model.polling
    runner.finish(reports.LIST_KEY)  # 外殼關了之後才回來的結果
    model.pageOpened("reports")
    assert not model.polling


def test_staff_are_told_about_new_reports_but_not_on_the_first_load(make_model, runner, db, notifier):
    model = make_model(admin=False)
    db.reports = [report("old", published=True)]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert toasts(notifier) == []  # 第一次載入不跳
    db.reports = [report("new", kind="staff", published=True), report("old", published=True)]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert titles(notifier) == [("info", "New report", "Mia Chen · Oct 1 – Oct 31, 2026 is ready to read.",
                                 "open-report:new")]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert len(toasts(notifier)) == 1  # 看過的不再跳

    model.pageClosed("reports")
    model.pageOpened("reports")  # 重新打開頁面：又是「第一次載入」
    db.reports = [report("a", published=True), report("b", kind="company", published=True), *db.reports]
    runner.finish(reports.LIST_KEY)
    assert len(toasts(notifier)) == 1
    db.reports = [report("c", published=True), report("d", kind="staff", published=True), *db.reports]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert titles(notifier)[0] == ("info", "2 new reports", "They're ready to read.", "open-report:c")


def test_admin_is_told_when_a_generating_report_is_ready_or_failed(make_model, runner, db, notifier):
    model = make_model(admin=True)
    db.reports = [report("a", status="generating"), report("b", kind="company", status="generating"),
                  report("c", kind="staff", status="generating"), report("d")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert toasts(notifier) == []
    db.reports = [report("a"), report("b", kind="company"),
                  report("c", kind="staff", status="failed", failure_reason="Timed out"), report("d")]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert sorted(titles(notifier)) == sorted([
        ("success", "Report ready", "S20261101090000 · Mia Chen is ready. Check it, then publish it for staff.",
         "open-report:a"),
        ("success", "Report ready", "Company · Oct 1 – Oct 31, 2026 is ready. Only admins can see company reports.",
         "open-report:b"),
        ("error", "Report wasn't generated", "Mia Chen · Oct 1 – Oct 31, 2026: Timed out", "retry-report:c")])


def test_a_failed_first_load_is_a_block_error_and_later_failures_keep_the_list(make_model, runner, db):
    model = make_model(admin=False)
    db.error = OSError("down")
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert (model.listState, model.failureCount) == ("error", 1)
    assert model.errorTechnical == "OSError: down"
    db.error = None
    db.reports = [report("r1", published=True)]
    model.refresh()
    assert model.listState == "loading"
    runner.finish(reports.LIST_KEY)
    assert (model.listState, model.failureCount, model.errorDetail) == ("ready", 0, "")
    db.error = OSError("down again")
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert (model.listState, model.count) == ("ready", 1)  # 已經有清單：留著


def test_generate_checks_the_form_then_creates_a_row_and_posts_it(make_model, runner, db, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", "http://svc/analyze")
    posted = []
    monkeypatch.setattr(reports, "post_report", lambda url, report_id: posted.append((url, report_id)))
    model = make_model(admin=True)
    created = []
    model.reportCreated.connect(created.append)
    assert not model.generate("staff", "", "", "2026-10-01", "2026-10-31")
    assert model.subjectError == "Choose a staff member." and runner.submitted == []
    model.clearFormError("subject")
    assert model.subjectError == ""

    assert model.generate("staff", STAFF, "", "2026-10-01", "2026-10-31")
    assert model.submitting and not model.generate("company", "", "", "2026-10-01", "2026-10-31")  # 上一份還在送
    db.on_insert = lambda params: setattr(db, "reports", [report(params[0], kind="staff", status="generating")])
    runner.finish(reports.CREATE_KEY)
    new = db.reports[0].report_id
    insert_sql, insert_params = db.executed[0]
    assert insert_sql == "INSERT INTO" and insert_params == (new, date(2026, 10, 1), date(2026, 10, 31),
                                                             f"reports/{new}.pdf", ADMIN, STAFF)
    assert not model.submitting and created == [new]
    assert model.selectedId == new and model.generatingKeys == [f"staff|{STAFF}|2026-10-01|2026-10-31"]
    runner.finish(reports.post_key(new))
    assert posted == [("http://svc/reports", new)]
    # 同一個對象還在產生中：不再產生一份
    assert not model.generate("staff", STAFF, "", "2026-10-01", "2026-10-31")
    assert model.formError == "This report is already being generated."


def test_generate_reports_a_missing_service_and_database_errors_in_the_form(make_model, runner, db):
    model = make_model(admin=True)
    db.rowcount = 0  # INSERT … SELECT 沒有找到分析完成的服務
    assert model.generate("service", STAFF, "S1", "", "")
    runner.finish(reports.CREATE_KEY)
    assert model.formError == "S1 isn't a completed service."
    db.rowcount, db.error = 1, mysql.connector.errors.InterfaceError(errno=2003, msg="Can't connect to MySQL server")
    assert model.generate("service", STAFF, "S1", "", "")
    runner.finish(reports.CREATE_KEY)
    assert model.formError == "Can't reach the database. Check your network and try again."
    db.rowcount, db.error = 0, None
    assert model.generate("staff", "gone", "", "2026-10-01", "2026-10-01")
    runner.finish(reports.CREATE_KEY)
    assert model.formError == "This staff member's account isn't available."


def test_staff_cannot_generate_publish_or_retry(make_model, runner, db):
    model = make_model(admin=False)
    db.reports = [report("r1", published=True)]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert not model.generate("company", "", "", "2026-10-01", "2026-10-31")
    assert not model.generateForService("S1")
    assert not model.publish("r1") and not model.retry("r1")
    assert runner.submitted == [("db", reports.LIST_KEY)]


def test_a_failed_post_marks_the_row_failed_and_says_why(make_model, runner, db, notifier, monkeypatch):
    def refuse(url, report_id):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(reports, "post_report", refuse)
    model = make_model(admin=True)
    db.reports = [report("r1", status="generating")]
    model._created("r1", [report("r1", status="generating")])
    runner.finish(reports.post_key("r1"))
    assert runner.isBusy(reports.fail_key("r1"))
    db.reports = [report("r1", status="failed", failure_reason="Couldn't reach the analysis service.")]
    runner.finish(reports.fail_key("r1"))
    assert db.executed[0] == ("UPDATE tbl_report", ("Couldn't reach the analysis service.", "r1"))
    assert titles(notifier) == [("error", "Report wasn't generated",
                                 "S20261101090000 · Mia Chen: Couldn't reach the analysis service.",
                                 "retry-report:r1")]
    assert model.selected["status"] == "failed" and model.selected["canRetry"]


def test_when_even_marking_it_failed_fails_the_reason_is_still_shown(make_model, runner, db, notifier, monkeypatch):
    monkeypatch.setattr(reports, "post_report", lambda url, report_id: (_ for _ in ()).throw(requests.ReadTimeout()))
    model = make_model(admin=True)
    model._created("r1", [report("r1", status="generating")])
    runner.finish(reports.post_key("r1"))
    db.error = OSError("down")
    runner.finish(reports.fail_key("r1"))
    assert titles(notifier) == [("error", "Report wasn't generated",
                                 "S20261101090000 · Mia Chen: The analysis service didn't respond.",
                                 "retry-report:r1")]


def test_retry_makes_a_new_row_for_the_same_subject_and_the_selection_follows(make_model, runner, db, pdf,
                                                                               monkeypatch):
    monkeypatch.setattr(reports, "post_report", lambda url, report_id: None)
    model = make_model(admin=True)
    db.reports = [report("r1", status="failed", failure_reason="Timed out")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    model.select("r1")
    assert model.retry("r1")
    # 新的一列取代失敗的那一列（清單只有每個對象最新的一筆）
    db.on_insert = lambda params: setattr(db, "reports", [report(params[0], status="generating")])
    runner.finish(reports.CREATE_KEY)
    insert = next(params for sql, params in db.executed if sql.startswith("INSERT"))
    assert insert[1:] == ("reports/" + insert[0] + ".pdf", ADMIN, "S20261101090000")
    assert model.selectedId == insert[0]
    db.reports = [report("r3")]  # 之後又有一份更新的（例如另一位管理員產生的）：選取跟著同一個對象
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert model.selectedId == "r3"
    assert pdf.opened[-1][:3] == ("r3", "reports/r3.pdf", "S20261101090000 · Mia Chen")
    assert not model.retry("missing")


def test_detail_page_generate_and_retry_go_through_the_same_path(make_model, runner, db, notifier, monkeypatch):
    monkeypatch.setattr(reports, "post_report", lambda url, report_id: None)
    model = make_model(admin=True)
    db.error = OSError("down")
    assert model.generateForService("S1")
    runner.finish(reports.CREATE_KEY)
    assert titles(notifier)[0][:2] == ("error", "Couldn't start the report")  # 明細頁沒有表單：用通知
    assert model.formError == ""
    assert not model.generateForService("")


def test_publish_only_ready_unpublished_reports_and_announce_it(make_model, runner, db, notifier):
    model = make_model(admin=True)
    db.reports = [report("r1"), report("c1", kind="company"), report("p1", published=True),
                  report("g1", status="generating")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert not model.publish("c1") and not model.publish("p1") and not model.publish("g1")
    assert not model.publish("missing")
    assert model.publish("r1")
    assert model.publishing == ["r1"] and not model.publish("r1")  # 送出中不重送
    db.reports = [report("r1", published=True), *db.reports[1:]]
    runner.finish(reports.publish_key("r1"))
    assert model.publishing == []
    assert ("UPDATE tbl_report", (ADMIN, "r1")) in db.executed
    assert titles(notifier)[0] == ("success", "Report published", "Staff can now see S20261101090000 · Mia Chen.", "")


def test_publishing_a_replaced_report_says_it_was_not_published(make_model, runner, db, notifier):
    model = make_model(admin=True)
    db.reports = [report("r1")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert model.publish("r1")
    db.rowcount, db.reports = 0, [report("r2", status="generating")]
    runner.finish(reports.publish_key("r1"))
    assert titles(notifier)[0][:2] == ("warning", "Report wasn't published")
    db.reports = [report("r2")]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert model.publish("r2")
    db.error = OSError("down")
    runner.finish(reports.publish_key("r2"))
    assert titles(notifier)[0][:2] == ("error", "Report wasn't published") and model.publishing == []


def test_select_opens_ready_reports_and_clears_the_viewer_otherwise(make_model, runner, db, pdf):
    model = make_model(admin=True)
    model.select("r1", 1200)  # 清單還沒到（從通知過來）：先記著
    assert model.selectedId == "r1" and model.selected == {} and pdf.opened == []
    db.reports = [report("r1"), report("g1", status="generating")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    assert pdf.opened == [("r1", "reports/r1.pdf", "S20261101090000 · Mia Chen", 1200)]
    model.select("r1")  # 同一份再選一次：回到第 1 頁
    assert len(pdf.opened) == 2
    model.select("g1")
    assert pdf.cleared == 1 and model.selected["chipState"] == "generating"
    db.reports = [report("g1")]
    model.poll()
    runner.finish(reports.LIST_KEY)
    assert pdf.opened[-1][0] == "g1"  # 產生好了：自己打開


def test_service_states_for_the_staff_analysis_page(make_model, runner, db):
    model = make_model(admin=False)
    assert not model.statesLoaded and model.serviceStates == {}
    model.pageOpened("analysis")
    assert runner.submitted == [("db", reports.STATES_KEY)]
    db.states = [{"service_id": "S1", "report_state": "published", "published_report_id": "r1"},
                 {"service_id": "S2", "report_state": "unpublished", "published_report_id": None}]
    runner.finish(reports.STATES_KEY)
    assert model.statesLoaded
    assert model.serviceStates == {"S1": {"state": "published", "reportId": "r1"},
                                   "S2": {"state": "unpublished", "reportId": ""}}
    assert db.executed == [("CALL sp_list_service_report_states(%s)", (STAFF,))]
    db.error = OSError("down")
    model.poll()
    runner.finish(reports.STATES_KEY)
    assert model.serviceStates["S1"]["state"] == "published"  # 查不到：留著上一次的
    admin = make_model(admin=True)
    admin.pageOpened("analysis")  # 管理員沒有分析頁，也沒有自己的服務
    assert runner.submitted.count(("db", reports.STATES_KEY)) == 2


def test_detail_report_is_the_newest_row_of_that_service(make_model, runner, db):
    model = make_model(admin=True)
    db.reports = [report("r2", service_id="S2"), report("r1", service_id="S1", published=True),
                  report("st", kind="staff")]
    model.pageOpened("detail")
    runner.finish(reports.LIST_KEY)
    assert model.detailReport == {}
    model.watchService("S1")
    assert model.detailReport["reportId"] == "r1" and model.detailReport["published"]
    model.watchService("S9")
    assert model.detailReport == {}


def test_staff_and_service_options_for_the_form(make_model, runner, db):
    model = make_model(admin=True)
    db.staff = [{"account_id": "a1", "account_name": "Mia Chen", "account_username": "mia"}]
    model.loadStaff()
    assert model.staffState == "loading"
    model.loadStaff()  # 查詢中：不重送
    runner.finish(reports.STAFF_KEY)
    assert (model.staffState, model.staffOptions) == ("ready", [{"value": "a1", "text": "Mia Chen (mia)"}])
    model.loadStaff()
    assert not runner.isBusy(reports.STAFF_KEY)  # 已經有了：不再查

    db.services = [{"service_id": "S1", "service_start": datetime(2026, 10, 31, 9, 15)}]
    model.loadServices("a1")
    assert (model.servicesFor, model.servicesState) == ("a1", "loading")
    model.loadServices("a2")  # 換人：取代還沒回來的
    db.services = []
    runner.finish(reports.SERVICES_KEY)
    assert (model.servicesFor, model.servicesState, model.serviceOptions) == ("a2", "empty", [])
    model._services_loaded("a1", [("S1", None)])  # 舊的結果晚到：不要
    assert model.serviceOptions == []
    assert ("SELECT service_id,", ("a2", reports.RECENT_SERVICES)) in db.executed

    db.error = OSError("down")
    model.loadServices("a3")
    runner.finish(reports.SERVICES_KEY)
    assert model.servicesState == "error"
    fresh = make_model(admin=True)
    fresh.loadStaff()
    runner.finish(reports.STAFF_KEY)
    assert fresh.staffState == "error"
    db.error = None
    fresh.loadStaff()  # 失敗了：下一次打開表單再查
    assert fresh.staffState == "loading"


def test_toast_actions_open_or_retry(make_model, runner, db, notifier, monkeypatch):
    monkeypatch.setattr(reports, "post_report", lambda url, report_id: None)
    model = make_model(admin=True)
    db.reports = [report("r1", status="failed")]
    model.pageOpened("reports")
    runner.finish(reports.LIST_KEY)
    opened = []
    model.openRequested.connect(opened.append)
    notifier.actionTriggered.emit("open-report:r1", 1)
    notifier.actionTriggered.emit("open-analysis:S1", 2)  # 別人的動作：不管
    notifier.actionTriggered.emit("nothing", 3)
    assert opened == ["r1"]
    model.requestOpen("r1")
    model.requestOpen("")
    assert opened == ["r1", "r1"]
    notifier.actionTriggered.emit("retry-report:r1", 4)
    assert runner.isBusy(reports.CREATE_KEY)


def test_a_model_without_services_does_nothing():
    model = ReportsModel(None, STAFF)
    model.pageOpened("reports")
    model.pageOpened("analysis")
    model.pageOpened("nowhere")
    model.refresh()
    model.loadStaff()
    model.loadServices("a")
    assert (model.listState, model.polling) == ("idle", False)
    assert not model.generate("company", "", "", "2026-10-01", "2026-10-31")
    model.select("r1")
    model.stop()


def test_both_homes_build_the_model_and_the_shared_viewer():
    assert server_home_init.PDFHandler is admin_home_init.PDFHandler is media.PDFHandler
    staff = server_home_init.ServerHome(STAFF)
    tasks = TaskRunner()
    admin = admin_home_init.AdminHome(ADMIN, SimpleNamespace(tasks=tasks, notifier=Notifier(), db=FakeDb()))
    assert not staff.reportsModel.isAdmin and admin.reportsModel.isAdmin
    assert staff.reportsModel._pdf is staff.pdfmodel and admin.reportsModel._pdf is admin.pdfmodel
    staff.stop()
    admin.close()
    assert admin.reportsModel._stopped and staff.reportsModel._stopped
    tasks.shutdown()


# ── PDFHandler（真的 TaskRunner；S3 換成本機的檔案）──────────────────────────────

def make_pdf(path, pages, height=792):
    document = fitz.Document()
    for number in range(pages):
        document.new_page(width=612, height=height).insert_text((72, 72), f"Page {number + 1}")
    document.save(path)
    document.close()


@pytest.fixture
def lanes():
    tasks = TaskRunner()
    yield tasks
    tasks.shutdown()


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    """假的 S3：key 對到 tmp_path/bucket/ 底下的檔案；記下每次下載在哪一條執行緒。"""
    folder = tmp_path / "bucket"
    (folder / "reports").mkdir(parents=True)
    downloads = []

    def download(key, local_path, token=None, report=None):
        downloads.append((key, threading.current_thread().name))
        source = folder / key
        if not source.exists():
            raise FileNotFoundError(key)
        shutil.copyfile(source, local_path)
        return local_path

    monkeypatch.setattr(storage, "download_with_progress", download)
    return SimpleNamespace(folder=folder, downloads=downloads)


@pytest.fixture
def handler(lanes):
    return media.PDFHandler(SimpleNamespace(tasks=lanes))


RID = "4f1c7a52-6a3d-4f7e-9d0b-0d6a4b8f8e21"


def opened(handler, report_id=RID, target_height=0, title="S1 · Mia Chen"):
    handler.open_report(report_id, f"reports/{report_id}.pdf", title, target_height)
    wait_until(lambda: handler.viewState != "loading")
    return handler


def test_a_report_downloads_on_the_io_lane_and_renders_on_the_pdf_lane(handler, bucket, tmp_path, monkeypatch):
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 2)
    threads = []
    real_open = fitz.open
    monkeypatch.setattr(fitz, "open", lambda *args, **kwargs: threads.append(threading.current_thread().name)
                        or real_open(*args, **kwargs))
    opened(handler)
    assert (handler.viewState, handler.current, handler.page, handler.pageCount) == ("ready", RID, 1, 2)
    assert handler.currentTitle == "S1 · Mia Chen"
    assert bucket.downloads == [(f"reports/{RID}.pdf", bucket.downloads[0][1])]
    assert bucket.downloads[0][1].startswith("io-lane")
    assert threads and all(name.startswith("pdf-lane") for name in threads)  # PyMuPDF 只在 pdf lane 用
    cached = tmp_path / "reports-pdf" / f"{RID}.pdf"
    assert cached.exists() and handler.currentFileUrl == QUrl.fromLocalFile(str(cached)).toString()
    first = handler.pageSource
    assert Path(QUrl(first).toLocalFile()).parent == tmp_path / "reports"  # 頁面圖在報表快取

    assert not handler.previousPage()
    assert handler.nextPage()
    wait_until(lambda: handler.viewState == "ready")
    assert handler.page == 2 and handler.pageSource != first
    assert not handler.nextPage()
    assert handler.previousPage()
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.page, handler.pageSource) == (1, first)

    opened(handler)  # 再打開一次：用快取，不再下載
    assert len(bucket.downloads) == 1 and handler.page == 1


def test_page_resolution_follows_the_displayed_height(handler, bucket, tmp_path):
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1, height=800)
    opened(handler, target_height=1200)  # 顯示 600 px 高、2 倍的螢幕
    assert QImage(QUrl(handler.pageSource).toLocalFile()).height() == 1200
    pdf_path = str(tmp_path / "reports-pdf" / f"{RID}.pdf")
    default = media.render_page(pdf_path, 1, 0, str(tmp_path / "out"))[2]
    assert QImage(default).height() == 800 * media.DEFAULT_ZOOM  # 不知道顯示多大時用 2 倍
    huge = media.render_page(pdf_path, 1, 100_000, str(tmp_path / "out"))[2]
    assert QImage(huge).height() == 800 * media.MAX_ZOOM  # 最多 4 倍


def test_a_missing_report_in_cloud_storage_can_be_retried(handler, bucket):
    opened(handler)
    assert handler.viewState == "error" and handler.failureCount == 1
    assert handler.errorDetail == "This report isn't on this computer anymore. Retry to download it again."
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    assert handler.retry()
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.failureCount, handler.errorDetail) == (0, "")
    assert not handler.retry()  # 沒有錯誤時不重試


def test_a_damaged_pdf_is_deleted_so_retry_downloads_it_again(handler, bucket, tmp_path):
    (bucket.folder / "reports" / f"{RID}.pdf").write_bytes(b"this is not a pdf")
    opened(handler)
    assert handler.viewState == "error"
    assert handler.errorDetail == "This PDF can't be read. It may be damaged."
    assert "FileDataError" in handler.errorTechnical
    assert not (tmp_path / "reports-pdf" / f"{RID}.pdf").exists()  # 壞掉的不留在快取
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    assert handler.retry()
    wait_until(lambda: handler.viewState == "ready")
    assert len(bucket.downloads) == 2


def test_download_failures_name_the_cause(handler, monkeypatch):
    import botocore.exceptions

    def offline(key, local_path, token=None, report=None):
        raise botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.example")

    monkeypatch.setattr(storage, "download_with_progress", offline)
    opened(handler)
    assert handler.errorDetail == "The download of this report was interrupted. Check your network and try again."

    def missing(key, local_path, token=None, report=None):
        raise botocore.exceptions.ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    monkeypatch.setattr(storage, "download_with_progress", missing)
    assert handler.retry()
    wait_until(lambda: handler.viewState != "loading")
    assert handler.errorDetail == "This report isn't in cloud storage. It may have been moved or deleted."
    assert handler.failureCount == 2


def test_other_failures_use_the_general_message(handler, bucket, tmp_path, monkeypatch):
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the cache folder should be")
    monkeypatch.setattr(media, "reports_cache_dir", lambda: str(blocker))  # 快取位置是檔案：存不了圖
    opened(handler)
    assert handler.viewState == "error"
    assert handler.errorDetail.startswith("Something went wrong.")


def test_a_failed_download_leaves_no_temporary_file(tmp_path, monkeypatch):
    def broken(key, local_path, token=None, report=None):
        open(local_path, "wb").close()  # 暫存檔已經寫好，改名之前出事
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage, "download_with_progress", broken)
    with pytest.raises(OSError):
        media.fetch_report(RID, f"reports/{RID}.pdf")
    assert list((tmp_path / "reports-pdf").iterdir()) == []


def test_report_ids_must_be_safe_file_names(handler):
    for bad in ("", "../etc/passwd", "a/b", "a b"):
        with pytest.raises(ValueError):
            media.report_path(bad)
        with pytest.raises(ValueError):
            handler.open_report(bad, "reports/x.pdf", "x")
    assert handler.current == ""


def test_switching_or_clearing_ignores_the_previous_result(handler, bucket):
    other = "0b2e5f9a-1111-4222-8333-944455556666"
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    make_pdf(bucket.folder / "reports" / f"{other}.pdf", 2)
    handler.open_report(RID, f"reports/{RID}.pdf", "first")
    handler.open_report(other, f"reports/{other}.pdf", "second")  # 還沒好就換一份
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.current, handler.pageCount, handler.currentTitle) == (other, 2, "second")
    handler.open_report(RID, f"reports/{RID}.pdf", "first")
    handler.clear()  # 選到的那一份不是 ready：不顯示
    assert (handler.viewState, handler.current, handler.pageSource, handler.currentFileUrl) == ("idle", "", "", "")
    wait_until(lambda: not handler._tasks().isBusy(media.DOWNLOAD_REPORT_KEY))
    assert handler.viewState == "idle"


def test_without_services_the_handler_uses_its_own_lanes(bucket):
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    handler = media.PDFHandler()
    try:
        opened(handler)
        assert handler.viewState == "ready"
    finally:
        handler._tasks().shutdown()


def test_bind_switches_to_the_shared_lanes(bucket, lanes):
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    handler = media.PDFHandler()
    handler.bind(SimpleNamespace(tasks=lanes))
    seen = []
    lanes.busyChanged.connect(lambda key, busy: seen.append((key, busy)))
    opened(handler)
    assert ("reports:download", True) in seen and (f"pdf:{RID}", True) in seen
    assert handler._own_tasks is None


def test_the_report_cache_is_a_download_cache_and_its_download_blocks_clearing():
    """下載的 PDF 和其他可以重新下載的快取一起清（Quit、設定頁的 Clear Download Cache，T40）；
    下載中不能清，但報表的資料庫查詢（reports:list 等）不算用到快取。"""
    from myapp import local_storage

    assert media.report_pdf_dir() in media.download_cache_dirs()
    assert local_storage.cache_dirs_in_use({media.DOWNLOAD_REPORT_KEY})
    assert not local_storage.cache_dirs_in_use({reports.LIST_KEY, reports.STATES_KEY, reports.CREATE_KEY})


def test_forgetting_pages_drops_the_downloaded_report_so_it_downloads_again(handler, bucket, tmp_path):
    make_pdf(bucket.folder / "reports" / f"{RID}.pdf", 1)
    opened(handler)
    for folder in media.download_cache_dirs():  # 設定頁的 Clear Download Cache
        for path in Path(folder).glob("*"):
            path.unlink()
    handler.forget_pages()
    assert (handler.current, handler.viewState) == ("", "idle")
    opened(handler)
    assert handler.viewState == "ready" and len(bucket.downloads) == 2
