"""送分析與分析頁（步驟 3-6～3-10）：真的 MySQL、本機的假分析服務、真的 analysis.qml。

F2：三個清單在 db lane 一次查完（1 個往返），放在增量模型裡；送出分析的 POST 還在 GUI 執行緒（第三波才搬）。
"""
import pytest
from PySide6.QtCore import QMetaObject, QObject

from myapp.init import server_home_init
from myapp.init.server_home_init import ANALYSIS_KEY, ServiceModel
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, unused_port, wait_until

pytestmark = pytest.mark.integration

SID = "S20261030120000"


def ids(rows):
    return [row["serviceId"] for row in rows.rows()]


def refreshed(model):
    model.refresh()
    wait_until(lambda: not model.busy)
    return model


def server_error(analysis_service, monkeypatch):
    analysis_service.status = 500


def nothing_listening(analysis_service, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")


def test_analyze_sends_service_id_and_moves_service_to_analyzing(services, db, analysis_service):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = refreshed(ServiceModel(services, STAFF_ID))
    assert ids(model.notAnalyzedModel) == [SID]
    assert model.analyzeService(SID) is True
    assert analysis_service.requests == [("/analyze", {"service_id": SID})]
    wait_until(lambda: ids(model.analyzingModel) == [SID])  # 改狀態和重新整理在 db lane
    assert db.status(SID) == 2
    assert ids(model.notAnalyzedModel) == []
    [row] = model.analyzingModel.rows()
    assert row["status"] == 2 and row["waitingSince"] > 0  # 從送出的時候開始等


@pytest.mark.parametrize("break_service", [server_error, nothing_listening])
def test_failed_request_keeps_service_not_analyzed(services, db, analysis_service, monkeypatch, break_service):
    break_service(analysis_service, monkeypatch)
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = refreshed(ServiceModel(services, STAFF_ID))
    assert model.analyzeService(SID) is False  # QML 收到 false 就跳出「Couldn't send for analysis」
    assert not services.tasks.isBusy(f"staff:analyze:{SID}")  # 沒送出，就不改狀態
    assert db.status(SID) == 1
    assert ids(model.notAnalyzedModel) == [SID]


def test_status_never_moves_back(services, db, analysis_service):
    db.add_service(SID, "2026-10-30 12:00:00", status=3)  # 分析服務比用戶端先寫好「已完成」
    model = ServiceModel(services, STAFF_ID)
    assert model.analyzeService(SID)
    wait_until(lambda: ids(model.completedModel) == [SID])
    assert db.status(SID) == 3


def test_refresh_is_one_round_trip(services, db, db_proxy, no_network_on_gui_thread):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=2)
    db.add_service("S20261030140000", "2026-10-30 14:00:00", status=3)
    model = refreshed(ServiceModel(services, STAFF_ID))  # 第一次：連線、建 cursor（不算在預算裡）
    db_proxy.reset()
    refreshed(model)
    assert db_proxy.snapshot()["rt"] == 1  # 以前 3 個查詢、12 個往返（ENG-01、ENG-05）
    assert [len(rows.rows()) for rows in (model.completedModel, model.analyzingModel, model.notAnalyzedModel)] == [
        1, 1, 1]


def test_refresh_only_changes_rows_that_changed(services, db):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=1)
    model = refreshed(ServiceModel(services, STAFF_ID))
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


def test_lists_show_own_services_newest_first_with_scores_or_none(services, db):
    db.add_service(SID, "2026-10-30 12:00:00")
    db.add_scores(SID, customer=80.4, server=69.6)
    db.add_service("S20261030130000", "2026-10-30 13:00:00")  # 已完成，但還沒有分數列
    db.add_service("S20261030150000", "2026-10-30 15:00:00")
    db.add_scores("S20261030150000", customer=-1, server=-1)  # -1：沒有資料
    db.add_service("S20261030140000", "2026-10-30 14:00:00", account_id=ADMIN_ID)  # 別人的服務
    model = refreshed(ServiceModel(services, STAFF_ID))
    scores = {row["serviceId"]: (row["staffScore"], row["customerScore"]) for row in model.completedModel.rows()}
    assert ids(model.completedModel) == ["S20261030150000", "S20261030130000", SID]
    assert scores == {SID: (69.6, 80.4), "S20261030130000": (None, None), "S20261030150000": (None, None)}
    [row] = [row for row in model.completedModel.rows() if row["serviceId"] == SID]
    assert (row["startTime"], row["endTime"]) == ("2026-10-30 12:00:00", "2026-10-30 12:05:00")


def test_analysis_that_goes_back_to_not_analyzed_is_marked_failed(services, db):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)
    model = refreshed(ServiceModel(services, STAFF_ID))
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
    """服務人員端的主視窗（和登入後一樣用 launch() 開），等 launch() 在背景載入的資料回來。"""
    server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS))
    return engine.rootObjects()[0]


def open_analysis_page(home):
    qml(home, "mouseArea5.clicked(null)")  # 側邊欄的「Analysis」：換頁，清單在背景重新整理
    wait_until(lambda: qml(home, "mainContentLoader.status === Loader.Ready"))
    return qml(home, "mainContentLoader.item")


def list_rows(view):
    """ListView 已經建立的列（delegate），依位置排序。

    用 findChild 找列裡的子項目時，要留著列的參照：PySide 把找到的子項目掛在列的 Python 物件底下，
    列的 Python 物件被回收時，子項目的 Python 物件也跟著失效（T17）。
    """
    rows = [item for item in view.property("contentItem").childItems() if item.objectName() == "serviceRow"]
    return sorted(rows, key=lambda item: item.property("index"))


def test_analysis_page_shows_dialog_when_analysis_service_is_down(staff_home, db, services, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    page = open_analysis_page(staff_home)
    view = page.findChild(QObject, "notAnalyzedList")
    wait_until(lambda: len(list_rows(view)) == 1)
    [row] = list_rows(view)
    button = row.findChild(QObject, "analyzeButton")
    assert button.property("text") == "Analyze"
    qml(button, "clicked()")
    assert qml(page, "analyzeFailedDialog.visible")
    assert qml(page, "analyzeFailedDialog.title") == "Couldn't send for analysis"


def test_analysis_page_picks_up_finished_analysis_without_restart(staff_home, db, services, engine):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)  # app 開著的時候送出的服務
    page = open_analysis_page(staff_home)  # 側邊欄按鈕會重新查詢
    service_model = engine.rootContext().contextProperty("serviceModel")
    wait_until(lambda: ids(service_model.analyzingModel) == [SID])

    db.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))  # 分析服務做完了
    timer = page.findChild(QObject, "refreshTimer")
    assert timer.property("running") and timer.property("repeat") and timer.property("interval") == 15000
    QMetaObject.invokeMethod(timer, "triggered")  # 不等 15 秒，直接觸發
    assert services.tasks.isBusy(ANALYSIS_KEY)  # 在背景重新整理
    wait_until(lambda: ids(service_model.completedModel) == [SID])
