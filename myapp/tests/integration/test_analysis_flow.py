"""送出分析與分析完成的通知（前端重建 F3 的送出、F4，CONTRACTS §4 的 C3）：真的外殼和頁面、真的 MySQL、
本機的假分析服務。

- 送出：按鈕忙碌（Sending…）→ POST 在 io lane → 狀態在 db lane 改成 2 → toast「Sent for analysis」→ 那一列移到 Analyzing
- 送不出去：那一列回到 Analyze，常駐的錯誤 toast＋Retry；按 Retry 再送（UX-18、DS-20）
- 監看（ENG-16）：在別頁也收得到「Analysis complete」（View Results 會換到分析頁並標出那一列）、
  「Analysis failed」（Retry）；清單和首頁分數在背景重新整理
- app 不在前景：Dock 圖示跳動、加上徽章，切回來清掉（ENG-17、D6）
- 每次監看只有 1 個網路往返；「更新不到」只跳一則警告，和清單的重新整理共用
"""
import os
import threading
from itertools import count
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication, QWindow

from myapp.init import server_home_init
from myapp.init.server_home_init import ANALYSIS_KEY, HOME_SCORES_KEY, LAUNCH_KEYS, WATCHER_KEY
from tests.integration.helpers import STAFF_ID, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

SID = "S20261031093000"
OTHER = "S20261031101500"
_keys = count(1)


@pytest.fixture(autouse=True)
def no_qml_warnings(engine):
    """每個流程都不能讓 QML 發出警告：送出中的按鈕、Failed 的列、標出的列、從通知換頁（test_qml_warnings 的規則）。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield
    assert warnings == []


# ── 小工具 ───────────────────────────────────────────────────────────────────

def launch_staff(engine, services):
    """登入後的服務人員端；等 launch() 在背景載入的資料（包括監看要看的服務）都回來。"""
    home = server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    return home


def settled(services):
    """監看的查詢，以及它觸發的清單、首頁分數的重新整理，都做完了。"""
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in (WATCHER_KEY, ANALYSIS_KEY, HOME_SCORES_KEY)))


def poll(home):
    """不等 15 秒，直接讓監看查一次。"""
    home.watcher.tick()
    settled(home.services)


def show_page(window, sidebar_button):
    qml(window, f"{sidebar_button}.click()")
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def open_analysis(home):
    page = show_page(home.shell_window, "analysisButton")
    wait_until(lambda: not home.services.tasks.isBusy(ANALYSIS_KEY))
    return page


def find(root, name):
    """用 objectName 找子物件。用它的子物件時要留著 root 的參照（T17）。"""
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def rows_of(view):
    """ListView 已經建立的列，依 index 排序（findChild 找不到 delegate）。"""
    rows = [item for item in view.property("contentItem").childItems() if item.objectName() == "serviceRow"]
    return sorted(rows, key=lambda item: item.property("index"))


def ids(rows):
    return [row["serviceId"] for row in rows.rows()]


def titled(notifier, title):
    return [toast for toast in toasts(notifier) if toast["title"] == title]


def the_toast(notifier, title):
    wait_until(lambda: len(titled(notifier, title)) == 1)
    return titled(notifier, title)[0]


def set_status(db, service_id, status):
    db.execute("UPDATE tbl_service SET service_status = %s WHERE service_id = %s", (status, service_id))


def cut_database(services, db, monkeypatch):
    """斷線：db lane 已經連上的那條被切斷，重連也連不上。回傳恢復用的函式。"""
    found = []

    def connection_id(token, report):
        with services.db.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT CONNECTION_ID() AS id")
            return cursor.fetchone()["id"]

    services.tasks.submit("db", f"test:{next(_keys)}", connection_id, on_ok=found.append)
    wait_until(lambda: found)
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    db.execute(f"KILL {found[0]}")
    return lambda: monkeypatch.setenv("DB_PORT", port)


# ── 送出 ─────────────────────────────────────────────────────────────────────

def test_analyze_shows_sending_then_a_toast_and_moves_the_row(engine, services, db, analysis_service, monkeypatch,
                                                              no_network_on_gui_thread):
    db.add_service(SID, "2026-10-31 09:30:00", status=1)
    home = launch_staff(engine, services)
    page = open_analysis(home)
    gate = threading.Event()  # 分析服務晚一點才回應：看得到忙碌的樣子
    post = server_home_init.post_analysis
    monkeypatch.setattr(server_home_init, "post_analysis", lambda url, sid: (gate.wait(5), post(url, sid))[1])

    [row] = rows_of(find(page, "notAnalyzedList"))
    button = find(row, "analyzeButton")
    button.setProperty("busyDelay", 0)  # 不必等 300 ms 才換字
    qml(button, "clicked()")
    assert button.property("busy")  # 一按就忙碌：擋住第二次點擊（DS-24）
    wait_until(lambda: find(button, "busyLabel").property("text") == "Sending…")
    assert qml(button, "Accessible.name") == f"Sending…, {SID}"
    assert home.serviceModel.analyzeService(SID) is False  # 送出中不會再送一次（ENG-14）
    assert services.tasks.isBusy(f"analyze:{SID}")  # POST 在 io lane：畫面照常

    gate.set()
    toast = the_toast(services.notifier, "Sent for analysis")
    assert (toast["severity"], toast["detail"]) == ("info", f"{SID} is being analyzed. You'll be notified when it's done.")
    assert ids(home.serviceModel.analyzingModel) == [SID] and ids(home.serviceModel.notAnalyzedModel) == []
    assert analysis_service.requests == [("/analyze", {"service_id": SID})]
    assert db.status(SID) == 2
    assert home.watcher.watched == [SID] and home.watcher.next_poll_ms == 15_000  # 監看開始看這一筆
    qml(page, 'selectedTab = "analyzing"')
    wait_until(lambda: len(rows_of(find(page, "analyzingList"))) == 1)
    [row] = rows_of(find(page, "analyzingList"))
    assert find(row, "statusChip").property("label") == "Analyzing"


@pytest.mark.parametrize("problem, reason", [
    ("http_error", "The analysis service returned an error. Try again in a moment."),
    ("offline", "Can't reach the analysis service. Check your network and try again."),
])
def test_send_failure_offers_retry_and_the_retry_goes_through(engine, services, db, analysis_service, monkeypatch,
                                                              no_network_on_gui_thread, problem, reason):
    url = os.environ["ANALYZE_API_URL"]
    if problem == "http_error":
        analysis_service.status = 500
    else:
        monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")
    db.add_service(SID, "2026-10-31 09:30:00", status=1)
    home = launch_staff(engine, services)
    page = open_analysis(home)
    [row] = rows_of(find(page, "notAnalyzedList"))
    button = find(row, "analyzeButton")
    qml(button, "clicked()")

    toast = the_toast(services.notifier, "Couldn't send for analysis")
    assert (toast["severity"], toast["detail"], toast["timeoutMs"]) == ("error", reason, 0)  # 常駐，直到處理
    assert (toast["actionLabel"], toast["actionId"]) == ("Retry", f"retry-analyze:{SID}")
    wait_until(lambda: not button.property("busy"))
    assert button.property("text") == "Analyze"  # 那一列回到 Analyze，在頁面上也能重送（DS-20）
    assert not services.tasks.isBusy(f"analysis:mark:{SID}") and db.status(SID) == 1

    analysis_service.status = 200
    monkeypatch.setenv("ANALYZE_API_URL", url)
    services.notifier.trigger(toast["toastId"])  # toast 上的 Retry
    wait_until(lambda: ids(home.serviceModel.analyzingModel) == [SID])
    assert [t["title"] for t in toasts(services.notifier)] == ["Sent for analysis"]
    assert db.status(SID) == 2 and home.watcher.watched == [SID]


def test_saved_recording_toast_can_send_for_analysis(engine, services, db, analysis_service,
                                                     no_network_on_gui_thread):
    """錄影存好的通知（C1，key upload:<sid>）上的「Analyze」：動作 analyze:<sid> 由這裡送出。"""
    home = launch_staff(engine, services)
    db.add_service(SID, "2026-10-31 09:30:00", status=1)
    failed = services.notifier.notify("error", f"{SID} wasn't saved", "", f"upload:{OTHER}",
                                      f"retry-upload:{OTHER}", "Retry")
    services.notifier.trigger(failed)  # 別人的動作（retry-upload:）：不認得就不管
    saved = services.notifier.notify("success", f"Saved {SID}", "Video and audio uploaded.", f"upload:{SID}",
                                     f"analyze:{SID}", "Analyze")
    services.notifier.trigger(saved)
    the_toast(services.notifier, "Sent for analysis")
    assert analysis_service.requests == [("/analyze", {"service_id": SID})]
    assert db.status(SID) == 2 and home.watcher.watched == [SID]


# ── 監看：完成、失敗 ─────────────────────────────────────────────────────────

def test_completion_reaches_another_page_and_view_results_opens_the_row(engine, services, db,
                                                                        no_network_on_gui_thread):
    db.add_service(SID, "2026-10-31 09:30:00", status=2)  # 已經送出、還在分析（例如登入前送的）
    home = launch_staff(engine, services)
    window = home.shell_window
    assert home.watcher.watched == [SID]  # 登入時查一次分析中的服務
    assert qml(window, "mainContentLoader.source").toString().endswith("server_home_button.qml")  # 在首頁，不在分析頁
    busy_keys = []
    services.tasks.busyChanged.connect(lambda key, busy: busy and busy_keys.append(key))

    set_status(db, SID, 3)  # 分析服務做完了
    db.add_scores(SID, customer=80, server=70)
    poll(home)
    toast = the_toast(services.notifier, "Analysis complete")
    assert (toast["severity"], toast["detail"]) == ("success", f"Scores and charts for {SID} are ready.")
    assert (toast["actionLabel"], toast["actionId"]) == ("View Results", f"open-analysis:{SID}")
    assert toast["timeoutMs"] >= 8000  # 有動作按鈕：至少停 8 秒（DS-21）
    assert busy_keys == [WATCHER_KEY, ANALYSIS_KEY, HOME_SCORES_KEY]  # 清單和首頁分數在背景重新整理
    assert ids(home.serviceModel.completedModel) == [SID] and home.watcher.watched == []
    assert not home.watcher.polling  # 沒有要看的了：不再查

    services.notifier.trigger(toast["toastId"])  # View Results
    wait_until(lambda: qml(window, "mainContentLoader.source").toString().endswith("analysis.qml"))
    page = qml(window, "mainContentLoader.item")
    assert (qml(page, "selectedTab"), qml(page, "highlightedServiceId")) == ("completed", SID)
    wait_until(lambda: len(rows_of(find(page, "completedList"))) == 1)
    [row] = rows_of(find(page, "completedList"))
    assert row.property("highlighted") and qml(row, "Accessible.selected")
    wait_until(lambda: not qml(page, "revealPending"))  # 那一列捲進畫面了
    assert titled(services.notifier, "Analysis complete") == []


def test_failure_is_marked_on_the_row_and_retry_sends_again(engine, services, db, analysis_service,
                                                            no_network_on_gui_thread):
    db.add_service(SID, "2026-10-31 09:30:00", status=2)
    home = launch_staff(engine, services)
    page = open_analysis(home)
    set_status(db, SID, 1)  # 分析服務失敗：把 2 改回 1（D4）
    poll(home)

    toast = the_toast(services.notifier, "Analysis failed")
    assert (toast["severity"], toast["detail"], toast["timeoutMs"]) == (
        "error", f"{SID} couldn't be analyzed. You can send it again.", 0)  # 常駐
    assert (toast["actionLabel"], toast["actionId"]) == ("Retry", f"retry-analyze:{SID}")
    wait_until(lambda: len(rows_of(find(page, "notAnalyzedList"))) == 1)
    [row] = rows_of(find(page, "notAnalyzedList"))
    assert find(row, "statusChip").property("label") == "Failed"  # DS-22
    assert find(row, "analyzeButton").property("text") == "Retry"  # 同一列也能重試（DS-20）

    services.notifier.trigger(toast["toastId"])  # toast 上的 Retry
    wait_until(lambda: ids(home.serviceModel.analyzingModel) == [SID])
    assert analysis_service.requests == [("/analyze", {"service_id": SID})]
    assert [t["title"] for t in toasts(services.notifier)] == ["Sent for analysis"]
    [row] = home.serviceModel.analyzingModel.rows()
    assert row["failed"] is False and home.watcher.watched == [SID]


def test_retry_from_the_row_closes_the_stale_failure_toast(engine, services, db, analysis_service,
                                                           no_network_on_gui_thread):
    db.add_service(SID, "2026-10-31 09:30:00", status=2)
    home = launch_staff(engine, services)
    page = open_analysis(home)
    set_status(db, SID, 1)
    poll(home)
    the_toast(services.notifier, "Analysis failed")
    wait_until(lambda: len(rows_of(find(page, "notAnalyzedList"))) == 1)
    [row] = rows_of(find(page, "notAnalyzedList"))
    qml(find(row, "analyzeButton"), "clicked()")  # 不按 toast，按那一列的 Retry
    assert titled(services.notifier, "Analysis failed") == []  # 已經重新送出：舊的失敗通知收掉
    the_toast(services.notifier, "Sent for analysis")


# ── app 不在前景 ─────────────────────────────────────────────────────────────

def test_dock_bounces_and_counts_while_the_app_is_in_the_background(engine, services, db, monkeypatch,
                                                                    no_network_on_gui_thread):
    dock = SimpleNamespace(state=Qt.ApplicationState.ApplicationInactive, badges=[], alerts=[])
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: dock.state))
    monkeypatch.setattr(QGuiApplication, "setBadgeNumber", lambda app, number: dock.badges.append(number))
    monkeypatch.setattr(QWindow, "alert", lambda window, msec: dock.alerts.append((window, msec)))
    db.add_service(SID, "2026-10-31 09:30:00", status=2)
    db.add_service(OTHER, "2026-10-31 10:15:00", status=2)
    home = launch_staff(engine, services)

    set_status(db, SID, 3)
    set_status(db, OTHER, 1)
    poll(home)
    assert {t["title"] for t in toasts(services.notifier)} == {"Analysis complete", "Analysis failed"}
    assert [msec for _, msec in dock.alerts] == [0, 0]  # 0：Dock 圖示一直跳到使用者切回來
    assert all(window is home.shell_window for window, _ in dock.alerts)
    assert dock.badges == [1, 2]  # 這段期間的完成＋失敗

    dock.state = Qt.ApplicationState.ApplicationActive
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert dock.badges == [1, 2, 0]  # 切回 app：徽章清掉

    db.add_service("S20261031110000", "2026-10-31 11:00:00", status=3)
    home.watcher.add("S20261031110000")
    poll(home)
    assert len(titled(services.notifier, "Analysis complete")) == 2
    assert (len(dock.alerts), dock.badges) == (2, [1, 2, 0])  # 在前景時只有 toast


# ── 成本與「更新不到」 ───────────────────────────────────────────────────────

def test_each_watcher_poll_is_one_round_trip(engine, services, db, db_proxy, no_network_on_gui_thread):
    for hour in (9, 10, 11):
        db.add_service(f"S20261031{hour:02d}0000", f"2026-10-31 {hour:02d}:00:00", status=2)
    home = launch_staff(engine, services)  # 第一次：連線、建 cursor（不算在預算裡，T16）
    assert len(home.watcher.watched) == 3

    db_proxy.reset()
    poll(home)  # 都還在分析
    assert db_proxy.snapshot()["rt"] == 1  # 主鍵 IN (...) 一次查完（ENG-16）；以前分析頁每 15 秒 12 個

    set_status(db, "S20261031090000", 3)
    db_proxy.reset()
    poll(home)
    assert db_proxy.snapshot()["rt"] == 3  # 監看 1，加上狀態變了才有的清單 1、首頁分數 1
    assert home.watcher.watched == ["S20261031100000", "S20261031110000"]


def test_watcher_and_list_share_one_refresh_warning(engine, services, db, monkeypatch, no_network_on_gui_thread):
    db.add_service(SID, "2026-10-31 09:30:00", status=2)
    home = launch_staff(engine, services)
    restore = cut_database(services, db, monkeypatch)
    poll(home)  # 監看查不到
    home.serviceModel.refresh()  # 清單也重新整理不到（例如錄影存好之後）
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    poll(home)
    [warning] = toasts(services.notifier)  # 同一次斷線：不管從哪裡失敗、失敗幾次，都只有一則
    assert (warning["severity"], warning["title"], warning["detail"], warning["count"]) == (
        "warning", "Can't refresh analysis status", "Can't reach the database. Retrying in 15 seconds.", 1)
    assert ids(home.serviceModel.analyzingModel) == [SID]  # 清單留著

    restore()
    poll(home)
    assert toasts(services.notifier) == []  # 又查得到了：警告自己關掉
    assert home.watcher.watched == [SID]
