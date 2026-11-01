"""報表（new-feature N4、N5，T37）：管理員產生、看、發佈；服務人員只看得到自己已發佈的。

真的 MySQL（sp_list_reports、sp_list_service_report_states）、moto 的 S3、假的分析服務（記下 /reports 的 POST）。
分析服務那一邊由測試模擬：用 PyMuPDF 做一份小 PDF 放到 S3 的 reports/{report_id}.pdf，再把那一列改成 ready。
輪詢不等計時器：檢查計時器的間隔之後，直接呼叫 reportsModel.poll()（和計時器到了一樣）。
每個測試都要求 QML 零警告，GUI 執行緒碰網路就失敗（no_network_on_gui_thread）。
"""
import os

import fitz
import pytest
from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine

from myapp import media, reports
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

OTHER_ID = "00000000-0000-0000-0000-0000000000b1"
SID = "S20261030120000"
START = "2026-10-30 12:00:00"


# ── fixture 與小工具 ─────────────────────────────────────────────────────────

@pytest.fixture
def qml_warnings(engine):
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


@pytest.fixture
def ui(engine, services, db, s3, analysis_service, qml_warnings, no_network_on_gui_thread):
    """所有測試共用的條件：真的 TaskRunner、拋棄式 MySQL、moto 的 S3、假的分析服務；GUI 執行緒不准碰網路；QML 零警告。"""
    return engine


@pytest.fixture
def second_engine(qml_warnings):
    """另一端（服務人員）用自己的 engine：兩端的 context property 名稱一樣，不能放在同一個 engine。"""
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield engine
    engine.deleteLater()
    assert warnings == []


def launch(module, engine, account_id, services):
    home = module.launch(engine, account_id, services)
    window = engine.rootObjects()[-1]
    keys = server_home_init.LAUNCH_KEYS if module is server_home_init else ("admin:profile",)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in keys))
    return home, window


def show(window, page):
    """和側邊欄的按鈕一樣換頁。"""
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def find(root, name):
    found = root.findChild(QObject, name)
    if found is not None:
        return found
    matches = find_all(root, name)
    assert matches, f"找不到 {name}"
    return matches[0]


def find_all(root, name):
    found, pending = [], [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
    return found


def items(page, count):
    """清單裡的 count 個報表按鈕，由上而下。"""
    view = find(page, "reportList")
    wait_until(lambda: len(find_all(view, "reportItem")) == count)
    return sorted(find_all(view, "reportItem"), key=lambda item: item.y())


def text(item, name):
    return find(item, name).property("text")


def idle(services):
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in (
        reports.LIST_KEY, reports.STATES_KEY, reports.CREATE_KEY, "reports:download")))


def pdf_bytes(pages=1):
    document = fitz.Document()  # 不用 fitz.open：no_network_on_gui_thread 把它包起來了
    for number in range(pages):
        document.new_page().insert_text((72, 72), f"Report page {number + 1}")
    data = document.tobytes()
    document.close()
    return data


def finish_report(db, s3, report_id, pages=1):
    """模擬分析服務：PDF 上傳到 S3 的 s3_key，再把那一列改成 ready（和契約的 WHERE 一樣）。"""
    s3.put_object(Bucket="adam-test", Key=f"reports/{report_id}.pdf", Body=pdf_bytes(pages),
                  ContentType="application/pdf")
    db.execute("UPDATE tbl_report SET report_status = 'ready' WHERE report_id = %s AND report_status = 'generating'",
               (report_id,))


def add_report(db, report_id, kind="service", status="ready", published=False, service_id=SID, staff=STAFF_ID,
               period=("2026-10-01", "2026-10-31"), minutes_ago=0, reason=None):
    """直接放一列報表（管理員產生、分析服務做好的樣子）。"""
    db.execute(
        "INSERT INTO tbl_report (report_id, report_kind, service_id, staff_account_id, period_start, period_end, "
        "report_status, failure_reason, s3_key, created_by, created_at, published_at, published_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(3) - INTERVAL %s SECOND, "
        "IF(%s, NOW(), NULL), IF(%s, %s, NULL))",
        (report_id, kind, service_id if kind == "service" else None, staff if kind != "company" else None,
         None if kind == "service" else period[0], None if kind == "service" else period[1], status, reason,
         f"reports/{report_id}.pdf", ADMIN_ID, minutes_ago * 60, published, published, ADMIN_ID))


def rid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def report_rows(db):
    return db.rows("SELECT report_id, report_kind, service_id, report_status, failure_reason, published_at IS NOT NULL "
                   "FROM tbl_report ORDER BY created_at")


def toast_titles(services):
    return [(toast["severity"], toast["title"]) for toast in toasts(services.notifier)]


def seed_staff(db):
    db.execute("UPDATE tbl_account SET account_name = 'Mia Chen' WHERE account_id = %s", (STAFF_ID,))
    db.add_account(OTHER_ID, "leo", "Leo Park")


# ── 管理員：產生、Report ready、Publish；服務人員：一次輪詢內看得到，只有自己的 ─────────────

def test_admin_generates_checks_and_publishes_a_service_report_and_staff_see_it(ui, second_engine, services, db, s3,
                                                                              analysis_service):
    seed_staff(db)
    db.add_service(SID, START)
    db.add_scores(SID, customer=80, server=84)
    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page = show(window, "admin_reports_page.qml")
    wait_until(lambda: home.reportsModel.listState == "empty")
    empty = find(page, "reportsEmpty")
    assert empty.isVisible() and empty.property("title") == "No reports yet"
    assert empty.property("actionText") == "New Report"

    # 表單：Service → 員工 → 他最近分析完成的服務 → Generate Report
    qml(page, "openGenerator()")
    dialog = qml(page, "generator")
    wait_until(lambda: home.reportsModel.staffState == "ready")
    assert [option["text"] for option in home.reportsModel.staffOptions] == ["Leo Park (leo)",
                                                                              "Mia Chen (placeholder_staff)"]
    qml(dialog, f'staffField.picked("{STAFF_ID}")')
    wait_until(lambda: home.reportsModel.servicesState == "ready")
    assert home.reportsModel.serviceOptions == [{"value": SID, "text": f"{SID} · Oct 30, 12:00"}]
    qml(dialog, "generateButton.clicked()")  # 還沒選服務
    assert home.reportsModel.subjectError == "Choose a service."
    assert analysis_service.requests == []
    qml(dialog, f'serviceField.picked("{SID}")')
    assert home.reportsModel.subjectError == ""
    qml(dialog, "generateButton.clicked()")
    wait_until(lambda: not qml(dialog, "opened"))  # 新增成功就關掉
    wait_until(lambda: analysis_service.requests != [])
    [(path, body)] = analysis_service.requests
    report_id = body["report_id"]
    assert path == "/reports" and set(body) == {"report_id"}
    assert report_rows(db) == [(report_id, "service", SID, "generating", None, 0)]
    assert db.rows("SELECT staff_account_id, s3_key, created_by FROM tbl_report") == [
        (STAFF_ID, f"reports/{report_id}.pdf", ADMIN_ID)]

    # 產生中：那一列是 Generating，檢視器說在產生；每 3 秒輪詢
    [item] = items(page, 1)
    assert text(item, "reportTitle") == f"{SID} · Mia Chen"
    assert find(item, "reportChip").property("label") == "Generating"
    assert home.reportsModel.selectedId == report_id
    assert find(page, "generatingState").isVisible() and not find(page, "paper").isVisible()
    assert home.reportsModel.polling and home.reportsModel._timer.interval() == reports.ADMIN_POLL_S * 1000
    assert home.reportsModel.generatingKeys == [f"service|{SID}"]

    # 服務人員這時候：報表頁空的，分析頁寫 Not generated yet
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page = show(staff_window, "server_reports_page.qml")
    wait_until(lambda: staff.reportsModel.listState == "empty")
    assert find(staff_page, "reportsEmpty").property("title") == "No published reports yet"
    assert staff.reportsModel.polling and staff.reportsModel._timer.interval() == reports.STAFF_POLL_S * 1000

    # 分析服務做好了：下一次輪詢看到 ready，跳「Report ready」，自己打開 PDF
    finish_report(db, s3, report_id, pages=2)
    home.reportsModel.poll()
    wait_until(lambda: home.pdfmodel.viewState == "ready" and home.pdfmodel.current == report_id)
    assert ("success", "Report ready") in toast_titles(services)
    assert not home.reportsModel.polling  # 沒有產生中的了
    assert find(item, "reportChip").property("label") == "Ready"
    assert (home.pdfmodel.page, home.pdfmodel.pageCount) == (1, 2)
    image = find(page, "pageImage")
    wait_until(lambda: qml(image, "status === Image.Ready"))
    assert find(page, "publishNote").property("text") == "Staff can't see this report until you publish it."
    assert find(page, "publishButton").isVisible()

    # 還沒發佈：服務人員的清單不變
    staff.reportsModel.poll()
    idle(services)
    assert staff.reportsModel.listState == "empty"

    qml(find(page, "publishButton"), "clicked()")
    wait_until(lambda: ("success", "Report published") in toast_titles(services))
    assert db.rows("SELECT published_by, published_at IS NOT NULL FROM tbl_report") == [(ADMIN_ID, 1)]
    assert find(item, "reportChip").property("label") == "Published"
    assert not find(page, "publishButton").isVisible()
    assert find(page, "publishNote").property("text").endswith(". Staff can see it.")

    # 服務人員：下一次輪詢（15 秒）就看得到，跳「New report」
    staff.reportsModel.poll()
    wait_until(lambda: staff.reportsModel.listState == "ready")
    wait_until(lambda: staff.pdfmodel.viewState == "ready" and staff.pdfmodel.current == report_id)
    [staff_item] = items(staff_page, 1)
    assert text(staff_item, "reportTitle") == f"{SID} · Mia Chen"
    assert text(staff_item, "reportMeta").startswith("Service report · Published ")
    assert not find(staff_item, "reportChip").isVisible()  # 服務人員的清單都是已發佈的
    assert not find(staff_page, "publishRow").isVisible()
    new = [toast for toast in toasts(services.notifier) if toast["title"] == "New report"]
    assert new and new[0]["actionId"] == f"open-report:{report_id}"
    del staff


def test_staff_see_only_their_own_published_reports_and_regenerating_keeps_the_old_one(ui, services, db, s3):
    seed_staff(db)
    db.add_service(SID, START)
    db.add_service("S20261031090000", "2026-10-31 09:00:00", account_id=OTHER_ID)
    add_report(db, rid(1), published=True, minutes_ago=50)                                  # 自己的、已發佈
    add_report(db, rid(2), kind="staff", published=True, minutes_ago=40)                    # 自己的員工報表、已發佈
    add_report(db, rid(3), kind="staff", period=("2026-09-01", "2026-09-30"), minutes_ago=30)  # 自己的、還沒發佈
    add_report(db, rid(4), service_id="S20261031090000", staff=OTHER_ID, published=True)    # 別人的
    add_report(db, rid(5), kind="company", published=True)                                  # 全公司：服務人員看不到
    add_report(db, rid(6), kind="staff", staff=OTHER_ID, published=True)                    # 別人的員工報表
    s3.put_object(Bucket="adam-test", Key=f"reports/{rid(1)}.pdf", Body=pdf_bytes())
    s3.put_object(Bucket="adam-test", Key=f"reports/{rid(2)}.pdf", Body=pdf_bytes(3))
    home, window = launch(server_home_init, ui, STAFF_ID, services)
    page = show(window, "server_reports_page.qml")
    wait_until(lambda: home.reportsModel.listState == "ready")
    newest, older = items(page, 2)
    assert [text(newest, "reportTitle"), text(older, "reportTitle")] == [
        "Mia Chen · Oct 1 – Oct 31, 2026", f"{SID} · Mia Chen"]
    assert find(page, "reportCount").property("text") == "2 reports"
    wait_until(lambda: home.pdfmodel.viewState == "ready")  # 一進來就打開最新的一份
    assert (home.pdfmodel.current, home.pdfmodel.pageCount) == (rid(2), 3)
    assert toasts(services.notifier) == []  # 第一次載入不跳

    # 管理員重新產生自己那一份服務報表（還沒發佈）：服務人員仍看到上一份
    add_report(db, rid(7), status="generating")
    home.reportsModel.poll()
    idle(services)
    assert {row["reportId"] for row in home.reportsModel.listModel.rows()} == {rid(1), rid(2)}
    finish_report(db, s3, rid(7))
    home.reportsModel.poll()
    idle(services)
    assert {row["reportId"] for row in home.reportsModel.listModel.rows()} == {rid(1), rid(2)}
    assert toasts(services.notifier) == []
    # 發佈新的一份：取代舊的，跳一則通知；舊的那一列還在資料庫裡（R5）
    db.execute("UPDATE tbl_report SET published_at = NOW(), published_by = %s WHERE report_id = %s", (ADMIN_ID, rid(7)))
    home.reportsModel.select(rid(1))
    home.reportsModel.poll()
    wait_until(lambda: {row["reportId"] for row in home.reportsModel.listModel.rows()} == {rid(7), rid(2)})
    assert [toast["title"] for toast in toasts(services.notifier)] == ["New report"]
    assert home.reportsModel.selectedId == rid(7)  # 選到的那一份被新的一份取代：跟著同一個對象
    wait_until(lambda: home.pdfmodel.viewState == "ready" and home.pdfmodel.current == rid(7))
    assert len(db.rows("SELECT report_id FROM tbl_report WHERE service_id = %s", (SID,))) == 2


def test_a_failed_request_marks_the_row_failed_and_retry_makes_a_new_row(ui, services, db, s3, analysis_service):
    seed_staff(db)
    analysis_service.report_status = 500
    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page = show(window, "admin_reports_page.qml")
    wait_until(lambda: home.reportsModel.listState == "empty")
    assert home.reportsModel.generate("company", "", "", "2026-10-01", "2026-10-31")
    wait_until(lambda: any(row[3] == "failed" for row in report_rows(db)))
    [first] = report_rows(db)
    assert first[1:] == ("company", None, "failed", "The analysis service couldn't start this report.", 0)
    wait_until(lambda: home.reportsModel.selected.get("status") == "failed")
    [item] = items(page, 1)
    assert text(item, "reportTitle") == "Company · Oct 1 – Oct 31, 2026"
    assert text(item, "reportMeta") == "The analysis service couldn't start this report."
    assert find(item, "reportChip").property("label") == "Failed"
    error = find(page, "generateError")
    assert error.isVisible() and error.property("detail") == "The analysis service couldn't start this report."
    failure = [toast for toast in toasts(services.notifier) if toast["title"] == "Report wasn't generated"]
    assert failure and failure[0]["actionId"] == f"retry-report:{first[0]}"

    analysis_service.report_status = 202
    qml(error, "retry()")
    wait_until(lambda: len(report_rows(db)) == 2)
    second = report_rows(db)[1]
    assert second[1:4] == ("company", None, "generating") and second[0] != first[0]
    assert report_rows(db)[0][3] == "failed"  # 舊的留著（R5）
    wait_until(lambda: home.reportsModel.selectedId == second[0])
    assert [row["reportId"] for row in home.reportsModel.listModel.rows()] == [second[0]]  # 清單只有最新的一筆
    assert [body["report_id"] for path, body in analysis_service.requests] == [first[0], second[0]]

    # 全公司報表：好了也不能發佈（服務人員看不到）
    finish_report(db, s3, second[0])
    home.reportsModel.poll()
    wait_until(lambda: home.pdfmodel.viewState == "ready")
    assert not find(page, "publishButton").isVisible()
    assert find(page, "publishNote").property("text") == "Only admins can see company reports."


def test_an_unreachable_analysis_service_is_the_reason(ui, services, db, monkeypatch):
    db.add_service(SID, START)
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")
    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    assert home.reportsModel.generateForService(SID)
    wait_until(lambda: report_rows(db) and report_rows(db)[0][3] == "failed")
    assert report_rows(db)[0][4] == "Couldn't reach the analysis service."
    wait_until(lambda: home.reportsModel.count == 1 and home.reportsModel.selected.get("status") == "failed")
    assert ("error", "Report wasn't generated") in toast_titles(services)


def test_reports_api_url_overrides_the_analyze_url(ui, services, db, analysis_service, monkeypatch):
    monkeypatch.setenv("REPORTS_API_URL", os.environ["ANALYZE_API_URL"].replace("/analyze", "/reports"))
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")  # 這個連不到
    db.add_service(SID, START)
    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    assert home.reportsModel.generateForService(SID)
    wait_until(lambda: analysis_service.requests != [])
    assert analysis_service.requests[0][0] == "/reports"
    idle(services)
    assert report_rows(db)[0][3] == "generating"


def test_stale_generating_rows_become_timed_out_when_the_admin_lists(ui, services, db):
    add_report(db, rid(1), kind="company", status="generating", minutes_ago=11)
    add_report(db, rid(2), kind="company", status="generating", period=("2026-09-01", "2026-09-30"), minutes_ago=2)
    staff_home, staff_window = launch(server_home_init, ui, STAFF_ID, services)
    staff_home.reportsModel.pageOpened("reports")
    idle(services)
    assert [row[3] for row in report_rows(db)] == ["generating", "generating"]  # 服務人員不改
    staff_home.reportsModel.pageClosed("reports")
    admin = admin_home_init.AdminHome(ADMIN_ID, services)
    admin.reportsModel.pageOpened("reports")
    idle(services)
    assert [(row[0], row[3], row[4]) for row in report_rows(db)] == [(rid(1), "failed", "Timed out"),
                                                                      (rid(2), "generating", None)]
    assert admin.reportsModel.polling  # 還有一份在產生
    admin.close()


def test_the_analysis_page_report_column_shows_the_state_and_opens_published_reports(ui, services, db, s3):
    seed_staff(db)
    for sid, start in (("S20261030090000", "2026-10-30 09:00:00"), ("S20261030100000", "2026-10-30 10:00:00"),
                       ("S20261030110000", "2026-10-30 11:00:00")):
        db.add_service(sid, start)
        db.add_scores(sid, customer=80, server=82)
    add_report(db, rid(1), service_id="S20261030100000")                                # 有 PDF、還沒發佈
    add_report(db, rid(2), service_id="S20261030110000", published=True, minutes_ago=5)  # 已發佈
    add_report(db, rid(3), service_id="S20261030110000")                                # 更新的、還沒發佈
    add_report(db, rid(4), service_id="S20261030090000", status="generating")           # 產生中：還沒有 PDF
    s3.put_object(Bucket="adam-test", Key=f"reports/{rid(2)}.pdf", Body=pdf_bytes(2))
    home, window = launch(server_home_init, ui, STAFF_ID, services)
    home.onLoadAnalysis()
    page = show(window, "analysis.qml")
    wait_until(lambda: home.reportsModel.statesLoaded and home.serviceModel.status == "ready")
    assert home.reportsModel.serviceStates == {"S20261030100000": {"state": "unpublished", "reportId": ""},
                                               "S20261030110000": {"state": "published", "reportId": rid(2)}}
    qml(page, 'showTab("completed")')
    view = find(page, "completedList")
    wait_until(lambda: len(find_all(view, "serviceRow")) == 3)
    rows = sorted(find_all(view, "serviceRow"), key=lambda row: row.y())
    by_id = {row.property("serviceId"): row for row in rows}
    assert text(by_id["S20261030090000"], "reportStateText") == "Not generated yet"
    assert text(by_id["S20261030100000"], "reportStateText") == "Not published yet"
    assert not find(by_id["S20261030110000"], "reportStateText").isVisible()
    assert by_id["S20261030100000"].property("reportText") == "Not published yet"
    assert "Not published yet" in qml(by_id["S20261030100000"], "Accessible.name")
    button = find(by_id["S20261030110000"], "reportButton")
    assert button.property("text") == "Report"
    assert home.reportsModel.polling  # 分析頁開著：每 15 秒更新狀態
    qml(button, "clicked()")
    assert qml(window, "mainContentLoader.source.toString()").endswith("server_reports_page.qml")
    wait_until(lambda: home.pdfmodel.viewState == "ready" and home.pdfmodel.current == rid(2))
    assert home.reportsModel.selectedId == rid(2)
    reports_page = qml(window, "mainContentLoader.item")  # 留著參照，子物件的 wrapper 才不會失效（T17）
    image = find(reports_page, "pageImage")
    wait_until(lambda: qml(image, "status === Image.Ready"))


def test_admin_detail_page_report_card_generates_publishes_and_opens(ui, services, db, s3, analysis_service):
    seed_staff(db)
    db.add_service(SID, START)
    db.add_scores(SID, customer=80, server=84)
    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page = qml(window, f'openServiceDetail("{SID}", "{START}")')
    wait_until(lambda: home.mediaModel.state == "ready")
    card = find(page, "serviceReportCard")
    wait_until(lambda: home.reportsModel.listState == "empty")
    note = find(card, "detailReportNote")
    assert note.property("text") == "No report for this service yet."
    generate = find(card, "detailGenerateReport")
    assert generate.property("text") == "Generate Report"
    qml(generate, "clicked()")
    wait_until(lambda: home.reportsModel.detailReport.get("status") == "generating")
    assert find(card, "detailReportChip").property("label") == "Generating"
    assert generate.property("busy")
    report_id = home.reportsModel.detailReport["reportId"]
    assert home.reportsModel.polling  # 明細頁開著、有產生中的：輪詢
    finish_report(db, s3, report_id)
    home.reportsModel.poll()
    wait_until(lambda: home.reportsModel.detailReport.get("status") == "ready")
    assert note.property("text") == "Staff can't see this report until you publish it."
    assert find(card, "detailOpenReport").isVisible() and not generate.isVisible()
    qml(find(card, "detailPublishReport"), "clicked()")
    wait_until(lambda: home.reportsModel.detailReport.get("published"))
    assert note.property("text").endswith(". Staff can see it.")
    assert generate.isVisible() and generate.property("text") == "Generate Again"
    qml(find(card, "detailOpenReport"), "clicked()")
    assert qml(window, "mainContentLoader.source.toString()").endswith("admin_reports_page.qml")
    wait_until(lambda: home.pdfmodel.viewState == "ready" and home.pdfmodel.current == report_id)
    assert not home.reportsModel.polling


def test_only_the_admin_sees_a_report_card_and_only_for_completed_services(ui, second_engine, services, db):
    db.add_service(SID, START, status=1)
    db.add_service("S20261031090000", "2026-10-31 09:00:00")
    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page = qml(window, f'openServiceDetail("{SID}", "{START}")')
    wait_until(lambda: home.mediaModel.state == "ready")
    assert not find(page, "reportCardLoader").property("active")  # 還沒分析完成：沒有報表卡
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page = qml(staff_window, 'openServiceDetail("S20261031090000", "2026-10-31 09:00:00")')
    wait_until(lambda: staff.mediaModel.state == "ready" and staff.mediaModel.analysisStatus == 3)
    assert not find(staff_page, "reportCardLoader").property("active")  # 服務人員的明細頁沒有報表卡
    del staff


def test_a_damaged_report_shows_an_error_and_coming_back_keeps_the_page(ui, services, db, s3):
    db.add_service(SID, START)
    add_report(db, rid(1), published=True, minutes_ago=5)
    add_report(db, rid(2), kind="staff", published=True)
    s3.put_object(Bucket="adam-test", Key=f"reports/{rid(1)}.pdf", Body=pdf_bytes(3))
    s3.put_object(Bucket="adam-test", Key=f"reports/{rid(2)}.pdf", Body=b"this is not a pdf")
    home, window = launch(server_home_init, ui, STAFF_ID, services)
    page = show(window, "server_reports_page.qml")
    wait_until(lambda: home.pdfmodel.viewState == "error")
    error = find(page, "reportError")
    assert error.isVisible() and not find(page, "paper").isVisible()
    assert error.property("detail") == "This PDF can't be read. It may be damaged."
    assert not find(page, "openExternallyButton").isVisible()
    older = items(page, 2)[1]
    qml(older, "clicked()")
    wait_until(lambda: home.pdfmodel.viewState == "ready" and home.pdfmodel.current == rid(1))
    assert home.pdfmodel.nextPage()
    wait_until(lambda: home.pdfmodel.page == 2 and home.pdfmodel.viewState == "ready")
    show(window, "server_service_page.qml")
    page = show(window, "server_reports_page.qml")  # 回來：還是剛才那一份、那一頁
    assert (home.pdfmodel.current, home.pdfmodel.page) == (rid(1), 2)
    assert find(page, "pageCounter").property("text") == "Page 2 of 3"


def test_quit_clears_the_downloaded_reports(ui, services, db, s3, tmp_path):
    add_report(db, rid(1), kind="staff", published=True)
    s3.put_object(Bucket="adam-test", Key=f"reports/{rid(1)}.pdf", Body=pdf_bytes())
    home, window = launch(server_home_init, ui, STAFF_ID, services)
    show(window, "server_reports_page.qml")
    wait_until(lambda: home.pdfmodel.viewState == "ready")
    assert list((tmp_path / "reports-pdf").iterdir()) and list((tmp_path / "reports").iterdir())
    home.clearDir()
    assert list((tmp_path / "reports-pdf").iterdir()) == [] and list((tmp_path / "reports").iterdir()) == []
    assert media.report_pdf_dir() == str(tmp_path / "reports-pdf")


def test_the_list_error_can_be_retried(ui, services, db, monkeypatch):
    home, window = launch(server_home_init, ui, STAFF_ID, services)
    original = reports.list_reports
    monkeypatch.setattr(reports, "list_reports", lambda *args, **kwargs: (_ for _ in ()).throw(OSError(28, "full")))
    page = show(window, "server_reports_page.qml")
    wait_until(lambda: home.reportsModel.listState == "error")
    error = find(page, "reportsError")
    assert error.isVisible() and error.property("detail").startswith("Not enough disk space.")
    monkeypatch.setattr(reports, "list_reports", original)
    qml(error, "retry()")
    wait_until(lambda: home.reportsModel.listState == "empty")
    assert find(page, "reportsEmpty").isVisible()


# ── 表單的期間用月曆選（PLAN 7.3 用 N2 的日期選擇器）────────────────────────────

def test_the_report_period_is_picked_on_the_calendar_above_the_dialog(ui, services, db):
    """New Report 是對話框：月曆要疊在對話框上面（DateField 的 popupZ），用滑鼠點得到；This month 兩格一起填。"""
    from types import SimpleNamespace

    from tests.integration.test_date_picker import calendar, child, click, open_calendar, pick

    home, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page = show(window, "admin_reports_page.qml")
    wait_until(lambda: home.reportsModel.listState == "empty")
    qml(page, "openGenerator()")
    dialog = qml(page, "generator")
    wait_until(lambda: qml(dialog, "opened"))
    qml(dialog, 'chooseKind("company")')
    start, end = qml(dialog, "startDateField"), qml(dialog, "endDateField")
    for field in (start, end):
        field.setProperty("today", "2026-11-12")
    form = SimpleNamespace(window=window)
    assert qml(calendar(start), "z") > qml(dialog, "z")

    pick(form, start, "2026-10-20", months_back=1)  # 點到的就是指著的那一天（pick 裡檢查）
    assert qml(dialog, "opened")  # 點月曆不會關掉對話框

    open_calendar(form, end)
    click(window, child(calendar(end), "thisMonthOption"))
    wait_until(lambda: not end.property("calendarOpen"))
    assert (start.property("text"), end.property("text")) == ("2026-11-01", "2026-11-12")  # 1 號到今天
    qml(dialog, "close()")
