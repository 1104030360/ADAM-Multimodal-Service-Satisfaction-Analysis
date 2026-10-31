"""送分析與分析頁（步驟 3-6～3-10）：真的 MySQL、本機的假分析服務、真的 analysis.qml。"""
import pytest
from PySide6.QtCore import QMetaObject, QObject

from myapp.database import DatabaseConnector
from myapp.init import server_home_init
from myapp.init.server_home_init import ServiceModel
from tests.integration.helpers import ADMIN_ID, STAFF_ID, find_button, qml, unused_port

pytestmark = pytest.mark.integration

SID = "S20261030120000"


def ids(services):
    return [service["service_id"] for service in services]


def server_error(analysis_service, monkeypatch):
    analysis_service.status = 500


def nothing_listening(analysis_service, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")


def test_analyze_sends_service_id_and_moves_service_to_in_progress(db, analysis_service):
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = ServiceModel(DatabaseConnector(), STAFF_ID)
    assert model.analyzeService(SID) is True
    assert analysis_service.requests == [("/analyze", {"service_id": SID})]
    assert db.status(SID) == 2
    assert (ids(model.uncompletedModel), ids(model.inprogressModel)) == ([], [SID])


@pytest.mark.parametrize("break_service", [server_error, nothing_listening])
def test_failed_request_keeps_service_uncompleted(db, analysis_service, monkeypatch, break_service):
    break_service(analysis_service, monkeypatch)
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    model = ServiceModel(DatabaseConnector(), STAFF_ID)
    assert model.analyzeService(SID) is False  # QML 收到 false 就跳出「送出分析失敗」
    assert db.status(SID) == 1
    assert ids(model.uncompletedModel) == [SID]


def test_status_never_moves_back(db):
    db.add_service(SID, "2026-10-30 12:00:00", status=3)  # 分析服務比用戶端先寫好「已完成」
    ServiceModel(DatabaseConnector(), STAFF_ID).update_service_status(SID, "2")
    assert db.status(SID) == 3


def test_refresh_notifies_qml_only_when_a_list_changes(db):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)
    model = ServiceModel(DatabaseConnector(), STAFF_ID)
    changed = []
    model.uncompletedModelChanged.connect(lambda: changed.append("uncompleted"))
    model.inprogressModelChanged.connect(lambda: changed.append("inprogress"))
    model.completedModelChanged.connect(lambda: changed.append("completed"))

    def refresh():  # 分析頁每 15 秒做一次的事
        model.fetch_uncompleted_services()
        model.fetch_inprogress_services()
        model.fetch_completed_services()

    refresh()
    assert changed == []  # 資料沒變：清單不重設、捲動位置不跑掉
    db.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))
    refresh()
    assert changed == ["inprogress", "completed"]


def test_completed_list_shows_own_scores_and_blank_when_missing(db):
    db.add_service(SID, "2026-10-30 12:00:00")
    db.add_scores(SID, customer=80, server=70)
    db.add_service("S20261030130000", "2026-10-30 13:00:00")  # 已完成，但還沒有分數列
    db.add_service("S20261030140000", "2026-10-30 14:00:00", account_id=ADMIN_ID)  # 別人的服務
    model = ServiceModel(DatabaseConnector(), STAFF_ID)
    scores = {s["service_id"]: (s["customer_total"], s["server_total"]) for s in model.completedModel}
    assert scores == {SID: ("80.0", "70.0"), "S20261030130000": ("", "")}  # 不是 "None"


@pytest.fixture
def staff_home(engine, db):
    """服務人員端的主視窗（和登入後一樣用 launch() 開）。"""
    server_home_init.launch(engine, STAFF_ID)
    return engine.rootObjects()[0]


def open_analysis_page(home):
    qml(home, "mouseArea5.clicked(null)")  # 側邊欄的「Analysis」
    return qml(home, "mainContentLoader.item")


def test_analysis_page_shows_dialog_when_analysis_service_is_down(staff_home, db, monkeypatch):
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{unused_port()}/analyze")
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    page = open_analysis_page(staff_home)
    qml(find_button(page, "分析"), "clicked()")
    assert qml(page, "analyzeFailedDialog.visible")


def test_analysis_page_picks_up_finished_analysis_without_restart(staff_home, db, engine):
    db.add_service(SID, "2026-10-30 12:00:00", status=2)  # app 開著的時候送出的服務
    page = open_analysis_page(staff_home)  # 側邊欄按鈕會重新查詢
    service_model = engine.rootContext().contextProperty("serviceModel")
    assert ids(service_model.inprogressModel) == [SID]

    db.execute("UPDATE tbl_service SET service_status = 3 WHERE service_id = %s", (SID,))  # 分析服務做完了
    timer = next(o for o in page.findChildren(QObject) if o.metaObject().className() == "QQmlTimer")
    assert timer.property("running") and timer.property("repeat")
    QMetaObject.invokeMethod(timer, "triggered")  # 不等 15 秒，直接觸發
    assert ids(service_model.completedModel) == [SID]
