"""送分析與分析頁（步驟 3-6～3-10）：真的 MySQL、本機的假分析服務、真的 analysis.qml。

F2：三個清單在 db lane 一次查完（1 個往返），放在增量模型裡。
F3／F4：送出分析的 POST 在 io lane，收到 200 之後在 db lane 重新整理清單（SQS，T49：狀態 2 是分析服務收下工作時
寫的，假的分析服務也照做，桌面端不寫）；分析頁不再自己每 15 秒重新整理，
改由 app 層的 AnalysisWatcher 發現狀態變了（完整的流程與通知見 test_analysis_flow.py）。
U2（T30）：每個分頁一次 8 筆、多載入一頁（Show More 馬上出現），總數在每一列上；View Results 要看的那一筆
不在第一頁時，同一個往返多取到它；送出的時間記在本機。分頁的規則和格式的單元測試在 tests/unit/test_analysis_page.py，
新的分析頁（分頁、欄位、Show More、各種狀態）在 test_analysis_page.py。
"""
from datetime import datetime

import pytest
from PySide6.QtCore import QObject

from myapp.init import server_home_init
from myapp.init.server_home_init import ANALYSIS_KEY, LAUNCH_KEYS, WATCHER_KEY, SentTimes, ServiceModel
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

SID = "S20261030120000"


def ids(rows):
    return [row["serviceId"] for row in rows.rows()]


@pytest.fixture
def new_model(services):
    """ServiceModel(services, STAFF_ID, …)。測試結束時 stop()，和外殼關掉時一樣：不留還在跑的 30 秒計時器
    給之後的測試（gc 收掉還在計時的物件時，後面的測試曾經 segfault，T30）。"""
    made = []

    def make(**kwargs):
        made.append(ServiceModel(services, STAFF_ID, **kwargs))
        return made[-1]

    yield make
    for model in made:
        model.stop()


def refreshed(model):
    model.refresh()
    wait_until(lambda: not model.busy)
    return model


def sent(services, service_id):
    """等送出分析的兩段工作（POST、之後的重新整理）都結束。"""
    wait_until(lambda: not services.tasks.isBusy(f"analyze:{service_id}")
               and not services.tasks.isBusy(f"analysis:sent:{service_id}"))


def server_error(analysis_service, monkeypatch):
    analysis_service.status = 500


def nothing_listening(analysis_service, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")


def test_analyze_sends_service_id_and_moves_service_to_analyzing(new_model, services, db, analysis_service,
                                                                  no_network_on_gui_thread):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = refreshed(new_model())
    assert ids(model.notAnalyzedModel) == [SID]
    watched = []
    model.analysisSent.connect(watched.append)
    assert model.analyzeService(SID) is True
    assert services.tasks.isBusy(f"analyze:{SID}")  # POST 在 io lane，GUI 執行緒不等它
    [row] = model.notAnalyzedModel.rows()
    assert row["sending"] is True  # 那一列的按鈕忙碌
    wait_until(lambda: ids(model.analyzingModel) == [SID])  # 重新整理在 db lane
    assert analysis_service.requests == [("/analyze", {"service_id": SID})]
    assert db.status(SID) == 2  # 分析服務收下時寫的
    assert ids(model.notAnalyzedModel) == []
    [row] = model.analyzingModel.rows()
    assert row["sentText"] == "Sent less than 1 min ago" and row["sending"] is False  # 從送出的時候開始等
    assert watched == [SID]  # 監看從這裡開始看這一筆


@pytest.mark.parametrize("break_service", [server_error, nothing_listening])
def test_failed_request_keeps_service_not_analyzed(new_model, services, db, analysis_service, monkeypatch,
                                                   break_service):
    break_service(analysis_service, monkeypatch)
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = refreshed(new_model())
    assert model.analyzeService(SID) is True  # 已經在背景送出；結果用 toast 說
    sent(services, SID)
    [toast] = toasts(services.notifier)
    assert (toast["severity"], toast["title"], toast["actionId"]) == (
        "error", "Couldn't send for analysis", f"retry-analyze:{SID}")
    assert db.status(SID) == 1  # 沒送到：分析服務沒收下，狀態還是 1
    [row] = model.notAnalyzedModel.rows()
    assert (row["serviceId"], row["sending"]) == (SID, False)  # 那一列回到 Analyze


def test_status_never_moves_back(new_model, services, db, analysis_service):
    db.add_service(SID, "2026-10-30 12:00:00", status=3)  # 分析服務比用戶端先寫好「已完成」
    model = new_model()
    assert model.analyzeService(SID)
    wait_until(lambda: ids(model.completedModel) == [SID])
    assert db.status(SID) == 3


def test_the_desktop_leaves_the_status_to_the_analysis_service(new_model, services, db, db_proxy, analysis_service,
                                                               no_network_on_gui_thread):
    """收到 200 之後桌面端不寫狀態（SQS，T49）。分析服務回 200、可是這時狀態是 1（例如收下之後 worker 很快就失敗、
    又改回 1）：清單照資料庫把它留在 Not analyzed，不會被桌面端晚到的 2 蓋成 Analyzing、一直等不到結果。"""
    analysis_service.marks_sent = False
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = refreshed(new_model())  # 第一次：連線、建 cursor（不算在預算裡）
    db_proxy.reset()
    assert model.analyzeService(SID)
    sent(services, SID)
    assert db_proxy.snapshot()["rt"] == 1  # 只有重新整理那 1 個往返（以前先多 1 個 UPDATE）
    assert db.status(SID) == 1
    assert (ids(model.notAnalyzedModel), ids(model.analyzingModel)) == ([SID], [])
    [toast] = toasts(services.notifier)
    assert toast["title"] == "Sent for analysis"  # 分析服務收下了；失敗由監看看到 1 時說（test_analysis_flow.py）


def test_refresh_is_one_round_trip(new_model, services, db, db_proxy, no_network_on_gui_thread):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=2)
    db.add_service("S20261030140000", "2026-10-30 14:00:00", status=3)
    model = refreshed(new_model())  # 第一次：連線、建 cursor（不算在預算裡）
    db_proxy.reset()
    refreshed(model)
    assert db_proxy.snapshot()["rt"] == 1  # 以前 3 個查詢、12 個往返（ENG-01、ENG-05）
    assert [len(rows.rows()) for rows in (model.completedModel, model.analyzingModel, model.notAnalyzedModel)] == [
        1, 1, 1]


def test_refresh_only_changes_rows_that_changed(new_model, services, db):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=1)
    model = refreshed(new_model())
    changes = []
    for name in ("completedModel", "analyzingModel", "notAnalyzedModel"):
        rows = getattr(model, name)
        rows.rowsInserted.connect(lambda *args, name=name: changes.append((name, "insert")))
        rows.rowsRemoved.connect(lambda *args, name=name: changes.append((name, "remove")))
        rows.dataChanged.connect(lambda *args, name=name: changes.append((name, "change")))
        rows.modelReset.connect(lambda name=name: changes.append((name, "reset")))

    refreshed(model)
    assert changes == []  # 資料沒變：清單不重設、捲動位置不跑掉
    db.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))  # 分析做完了
    refreshed(model)
    assert sorted(changes) == [("analyzingModel", "remove"), ("completedModel", "insert")]
    assert (ids(model.completedModel), ids(model.notAnalyzedModel)) == ([SID], ["S20261030130000"])


def test_lists_show_own_services_newest_first_with_scores_or_none(new_model, services, db):
    db.add_service(SID, "2026-10-30 12:00:00")
    db.add_scores(SID, customer=80.44, server=69.6)
    db.add_service("S20261030130000", "2026-10-30 13:00:00")  # 已完成，但還沒有分數列
    db.add_service("S20261030150000", "2026-10-30 15:00:00")
    db.add_scores("S20261030150000", customer=-1, server=-1)  # -1：沒有資料
    db.add_service("S20261030140000", "2026-10-30 14:00:00", account_id=ADMIN_ID)  # 別人的服務
    model = refreshed(new_model(clock=lambda: datetime(2026, 11, 1, 9, 0).timestamp()))
    scores = {row["serviceId"]: (row["staffScore"], row["customerScore"]) for row in model.completedModel.rows()}
    assert ids(model.completedModel) == ["S20261030150000", "S20261030130000", SID]
    # 小數一位：畫面上的數字和分數帶用同一個值；-1 和沒有分數列都是 None，不顯示 -1
    assert scores == {SID: (69.6, 80.4), "S20261030130000": (None, None), "S20261030150000": (None, None)}
    assert model.completedModel.total == 3
    [row] = [row for row in model.completedModel.rows() if row["serviceId"] == SID]
    assert row["startTime"] == "2026-10-30 12:00:00"  # View 打開明細時交給 openServiceDetail()
    assert (row["recorded"], row["length"]) == ("Oct 30, 12:00", "5 min")  # U2 的 Recorded、Length


def test_length_is_unknown_without_an_end_time(new_model, services, db):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    db.execute("UPDATE tbl_service SET service_end = NULL WHERE service_id = %s", (SID,))  # 資料表允許 NULL
    model = refreshed(new_model())
    [row] = model.notAnalyzedModel.rows()
    assert row["length"] == ""  # 畫面顯示「—」


def add_many(db, count, status, day):
    """count 筆，錄影時間新的在前；回傳 service ID（照畫面順序）。"""
    added = []
    for i in range(count):
        hour, minute = divmod(count - i, 60)
        sid = f"S202610{day:02d}{8 + hour:02d}{minute:02d}00"
        db.add_service(sid, f"2026-10-{day:02d} {8 + hour:02d}:{minute:02d}:00", status=status)
        added.append(sid)
    return added


def test_each_tab_loads_one_page_ahead_and_the_response_stays_small(new_model, services, db, db_proxy,
                                                                   no_network_on_gui_thread):
    not_analyzed = add_many(db, 20, status=1, day=29)
    add_many(db, 3, status=2, day=28)
    completed = add_many(db, 300, status=3, day=27)
    model = refreshed(new_model())  # 第一次：連線、建 cursor（不算在預算裡）
    db_proxy.reset()
    refreshed(model)
    traffic = db_proxy.snapshot()
    # 300 筆的歷史也只回傳每個分頁要顯示的再多一頁：1 個往返、回應小於 8 KB（ENG-01）
    assert traffic["rt"] == 1 and traffic["s2c"] < 8 * 1024, traffic
    tabs = (model.notAnalyzedModel, model.analyzingModel, model.completedModel)
    assert [(tab.count, tab.total, tab.hasMore) for tab in tabs] == [(8, 20, True), (3, 3, False), (8, 300, True)]
    assert ids(model.completedModel) == completed[:8] and ids(model.notAnalyzedModel) == not_analyzed[:8]
    assert [len(tab.loaded()) for tab in tabs] == [16, 3, 16]
    assert model.defaultTab == "notAnalyzed"

    db_proxy.reset()
    model.completedModel.showMore()
    assert ids(model.completedModel) == completed[:16]  # 已經載入的下一頁：不等往返
    wait_until(lambda: not model.busy)
    assert db_proxy.snapshot()["rt"] == 1  # 背景再多載入一頁
    assert len(model.completedModel.loaded()) == 24 and model.completedModel.count == 16


def test_view_results_reveals_a_row_beyond_the_first_page_in_one_round_trip(services, db, db_proxy, staff_home,
                                                                           no_network_on_gui_thread):
    completed = add_many(db, 30, status=3, day=27)
    page = open_analysis_page(staff_home)  # 先看過一次：Completed 載入了前 16 筆
    wait_until(lambda: not staff_home.serviceModel.busy)
    target = completed[19]  # 第 20 筆：還沒載入
    db_proxy.reset()
    staff_home.openAnalysis(target)  # 通知上的 View Results
    wait_until(lambda: not staff_home.serviceModel.busy)
    assert db_proxy.snapshot()["rt"] == 1  # 同一個往返多取到它
    model = staff_home.serviceModel
    assert (model.completedModel.count, model.indexOfService("completed", target)) == (24, 19)
    page = qml(staff_home.shell_window, "mainContentLoader.item")
    assert (qml(page, "selectedTab"), qml(page, "highlightedServiceId")) == ("completed", target)
    wait_until(lambda: not qml(page, "revealPending"))  # 那一列捲進畫面了


def test_send_time_is_remembered_on_this_computer(new_model, services, db, analysis_service, no_network_on_gui_thread):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=2)  # 別台電腦送的
    model = refreshed(new_model())
    assert model.analyzeService(SID)
    sent(services, SID)
    assert ids(model.analyzingModel) == ["S20261030130000", SID]  # 錄影時間新的在前
    texts = {row["serviceId"]: row["sentText"] for row in model.analyzingModel.rows()}
    assert texts == {SID: "Sent less than 1 min ago", "S20261030130000": "Sent earlier"}
    sent_at = SentTimes(STAFF_ID).get(SID)
    assert sent_at > 0 and SentTimes(STAFF_ID).ids() == {SID}  # 只記這台電腦送出的

    restarted = refreshed(new_model(clock=lambda: sent_at / 1000 + 21 * 60))  # 21 分鐘後重開 app（同一台電腦）
    again = {row["serviceId"]: row["sentText"] for row in restarted.analyzingModel.rows()}
    assert again == {SID: "Sent 21 min ago", "S20261030130000": "Sent earlier"}

    db.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))  # 分析完成
    refreshed(restarted)
    assert SentTimes(STAFF_ID).ids() == set()  # 不在分析中了：記錄刪掉


def test_analysis_that_goes_back_to_not_analyzed_is_marked_failed(new_model, services, db):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)
    model = refreshed(new_model())
    db.execute("UPDATE tbl_service SET service_status = 1 WHERE service_id = %s", (SID,))  # 分析服務失敗（D4）
    refreshed(model)
    [row] = model.notAnalyzedModel.rows()
    assert (row["serviceId"], row["failed"]) == (SID, True)
    db.execute("UPDATE tbl_service SET service_status = 2 WHERE service_id = %s", (SID,))  # 重新送出
    refreshed(model)
    [row] = model.analyzingModel.rows()
    assert row["failed"] is False


@pytest.fixture
def staff_home(engine, services):
    """服務人員端（和登入後一樣用 launch() 開），等 launch() 在背景載入的資料回來。回傳 ServerHome。"""
    home = server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    return home


def open_analysis_page(home):
    window = home.shell_window
    qml(window, "analysisButton.click()")  # 側邊欄的 Analysis：換頁，清單在背景重新整理
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def list_rows(view):
    """ListView 已經建立的列（delegate），依位置排序。

    用 findChild 找列裡的子項目時，要留著列的參照：PySide 把找到的子項目掛在列的 Python 物件底下，
    列的 Python 物件被回收時，子項目的 Python 物件也跟著失效（T17）。
    """
    rows = [item for item in view.property("contentItem").childItems() if item.objectName() == "serviceRow"]
    return sorted(rows, key=lambda item: item.property("index"))


def test_analysis_page_reports_a_failed_send_in_a_toast_not_a_dialog(services, db, staff_home, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    page = open_analysis_page(staff_home)
    view = page.findChild(QObject, "notAnalyzedList")
    wait_until(lambda: len(list_rows(view)) == 1)
    [row] = list_rows(view)
    button = row.findChild(QObject, "analyzeButton")
    assert button.property("text") == "Analyze"
    qml(button, "clicked()")
    assert button.property("busy")  # 一按就忙碌（DS-24）
    sent(services, SID)
    wait_until(lambda: not button.property("busy"))  # 送不出去：按鈕恢復，可以再按
    assert button.property("text") == "Analyze"
    assert page.findChild(QObject, "analyzeFailedDialog") is None  # 一般錯誤不用對話框（DS-20）
    [toast] = toasts(services.notifier)
    assert (toast["title"], toast["detail"], toast["actionLabel"]) == (
        "Couldn't send for analysis", "Can't reach the analysis service. Check your network and try again.", "Retry")


def test_analysis_page_picks_up_finished_analysis_through_the_watcher(services, db, staff_home):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)  # app 開著的時候送出的服務
    staff_home.watcher.add(SID)
    page = open_analysis_page(staff_home)  # 側邊欄按鈕會重新查詢
    service_model = staff_home.serviceModel
    wait_until(lambda: ids(service_model.analyzingModel) == [SID])
    assert page.findChild(QObject, "refreshTimer") is None  # 頁面不再自己每 15 秒重新整理（ENG-16）

    db.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))  # 分析服務做完了
    assert staff_home.watcher.polling and staff_home.watcher.next_poll_ms == 15_000
    staff_home.watcher.tick()  # 不等 15 秒，直接觸發
    assert services.tasks.isBusy(WATCHER_KEY)  # 在 db lane 查狀態
    wait_until(lambda: ids(service_model.completedModel) == [SID])  # 狀態變了：清單在背景重新整理
    assert not services.tasks.isBusy(ANALYSIS_KEY) and not staff_home.watcher.polling
