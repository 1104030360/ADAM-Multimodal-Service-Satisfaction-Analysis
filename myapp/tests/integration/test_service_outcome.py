"""服務結果與待辦（PLAN1 W1，T44）在服務明細：兩端都看得到、標示 AI 產生、沒有填時是 Unknown；服務人員只看得到自己的服務。

接真的 MySQL 8.4（db_schema.sql 已經有 migration 005 的欄位）。三欄由分析服務寫，這裡直接放進 tbl_service。
- 資料：和頁首、分數、檔案清單同一個查詢（1 個往返）；服務人員端多一個 account_id 的條件。
- 畫面（ServiceOutcomeCard.qml）：五種結果的標籤（字＋圖示）、待辦一項一行、「AI-generated from the transcript · 模型」；
  NULL 是 Unknown、沒有項目、說明有逐字稿時分析會填；兩端都有；QML 零警告。
- 權限：服務人員打開別人的服務 ID → 和不存在的服務一樣（serviceFound 是 false），沒有影片、錄音、圖表、分數、結果，
  也不下載任何東西；管理員端照舊打得開。
GUI 執行緒一律不碰網路（no_network_on_gui_thread）。
"""
from itertools import count

import pytest
from PySide6.QtCore import QObject

from myapp import media
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, wait_until

pytestmark = pytest.mark.integration

OTHER_ID, OTHER_NAME = "00000000-0000-0000-0000-0000000000b1", "Leo Park"
MODEL = "gemma4:31b"
AI_NOTE = f"AI-generated from the transcript · {MODEL}"
NOT_FILLED = "Not filled in yet. The analysis fills this in when the service has a transcript."
# (服務 ID, service_outcome, service_todos, 標籤, 圖示)
CASES = [
    ("S20261101090000", "Resolved", "Mail the new debit card within 5 business days.", "Resolved",
     "circle-check_successOnDark.svg"),
    ("S20261101091000", "Documents missing", "Customer brings a utility bill on Friday.\nFinish the address change.",
     "Documents Missing", "triangle-alert_warningOnDark.svg"),
    ("S20261101092000", "Handoff needed", "Send the loan form to the loans team.", "Handoff Needed",
     "arrow-right_warningOnDark.svg"),
    ("S20261101093000", "Unresolved", "", "Unresolved", "circle-x_errorOnDark.svg"),
    ("S20261101094000", "Unknown", "", "Unknown", "circle-dashed_textPrimary.svg"),
    ("S20261101095000", None, None, "Unknown", "circle-dashed_textPrimary.svg"),  # 還沒填
]
_keys = count(1)


@pytest.fixture(autouse=True)
def gui_thread_stays_off_the_network(db, no_network_on_gui_thread):
    """db fixture 自己的連線（放測試資料用）開好之後，才開始禁止 GUI 執行緒碰網路。"""


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


def add_service(db, sid, account_id=STAFF_ID, outcome=None, todos=None, model=MODEL, status=3):
    start = f"{sid[1:5]}-{sid[5:7]}-{sid[7:9]} {sid[9:11]}:{sid[11:13]}:{sid[13:15]}"
    db.add_service(sid, start, status=status, account_id=account_id)
    db.add_scores(sid, customer=80, server=86)
    db.execute("UPDATE tbl_service SET service_outcome = %s, service_todos = %s, service_outcome_model = %s "
               "WHERE service_id = %s", (outcome, todos, model if outcome else None, sid))


def add_files(db, s3, sid):
    """兩支影片、錄音、一張圖表：資料表有位置，S3 上也有檔案。"""
    keys = [f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi", f"audio/{sid}_audio.wav", f"charts/{sid}_a.png"]
    for key in keys:
        db.add_media(sid, key.split("/")[0], f"s3://adam-test/{key}")
        s3.put_object(Bucket="adam-test", Key=key, Body=key.encode())
    return keys


def warm_up(services):
    """先在 db lane 連線、建好 dictionary cursor（第一次要多幾個往返），之後的往返數才是查詢本身的。"""
    done = []

    def work(token, report):
        with services.db.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT 1 AS one")
            return cursor.fetchall()

    services.tasks.submit("db", f"test:outcome-warm-up:{next(_keys)}", work, on_ok=done.append)
    wait_until(lambda: done)


def opened(model, charts=None, services=None, sid=""):
    model.open(sid, "", services, charts or media.ChartsModel())
    wait_until(lambda: model.state in ("ready", "error") and not model.busy)
    return model


def find(root, name):
    found = root.findChild(QObject, name)
    if found is None:  # Repeater、Loader 建立的項目沒有 QObject parent：沿著畫面樹找
        found = next(iter(named(root, name)), None)
    assert found is not None, name
    return found


def named(root, name):
    found, pending = [], [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
    return found


# ---------------------------------------------------------------- 資料


@pytest.mark.parametrize("owner", [None, STAFF_ID], ids=["admin", "staff"])
def test_the_outcome_comes_with_the_header_in_one_round_trip(services, db, db_proxy, owner):
    sid, value, todos, _, _ = CASES[1]
    add_service(db, sid, outcome=value, todos=todos)
    warm_up(services)
    db_proxy.reset()
    model = opened(media.MediaModel(owner=owner), services=services, sid=sid)
    assert db_proxy.snapshot()["rt"] == 1  # 頁首、分數、服務結果、檔案清單：還是一個查詢
    assert (model.outcome, model.outcomeFilled, model.outcomeModel) == ("documentsMissing", True, MODEL)
    assert model.todos == ["Customer brings a utility bill on Friday.", "Finish the address change."]
    assert model.hasScores and model.staffName == STAFF_NAME
    model.close()


def test_staff_cannot_open_another_staff_members_service(services, db, s3, tmp_path):
    """PLAN1：服務人員只看得到自己的服務。知道別人的服務 ID 也打不開：和不存在的服務一樣，什麼都不下載。"""
    db.add_account(OTHER_ID, "leo", OTHER_NAME)
    sid = "S20261101100000"
    add_service(db, sid, account_id=OTHER_ID, outcome="Unresolved", todos="Call the customer back tomorrow.")
    add_files(db, s3, sid)

    charts = media.ChartsModel()
    staff = opened(media.MediaModel(owner=STAFF_ID), charts, services, sid)
    assert staff.state == "ready" and not staff.serviceFound
    assert [staff.slot(kind).state for kind in ("cam0", "cam1", "audio")] == ["missing"] * 3
    assert (charts.state, charts.totalCount) == ("empty", 0)
    assert (staff.outcome, staff.todos, staff.outcomeModel, staff.hasScores) == ("", [], "", False)
    assert (staff.headerText, staff.staffName, staff.analysisStatus) == ("", "", 0)
    assert not (tmp_path / "media").exists() and not (tmp_path / "charts").exists()  # 沒有下載任何檔案
    staff.close()

    # 自己的服務照常打得開（同一個服務人員端的模型）
    own = "S20261101101500"
    add_service(db, own, outcome="Resolved", todos="")
    mine = opened(media.MediaModel(owner=STAFF_ID), services=services, sid=own)
    assert mine.serviceFound and mine.outcome == "resolved"
    mine.close()

    # 管理員端不受影響：別的服務人員的服務、檔案、結果都在
    admin_charts = media.ChartsModel()
    admin = opened(media.MediaModel(), admin_charts, services, sid)
    wait_until(lambda: not admin.pending and admin_charts.state == "ready")
    assert admin.serviceFound and admin.staffName == OTHER_NAME and admin.outcome == "unresolved"
    assert [admin.slot(kind).state for kind in ("cam0", "cam1", "audio")] == ["ready"] * 3
    admin.close()


def test_the_staff_home_restricts_its_detail_to_its_own_account(services, db):
    """服務人員端的 mediaModel 帶著自己的 account_id（管理員端沒有）：以後新的入口（例如 W2 的連結）也一樣被擋。"""
    staff = server_home_init.ServerHome(STAFF_ID, services)
    admin = admin_home_init.AdminHome(ADMIN_ID, services)
    try:
        assert staff.mediaModel._owner == STAFF_ID and admin.mediaModel._owner is None
    finally:
        staff.stop()
        admin.close()


# ---------------------------------------------------------------- 畫面


@pytest.mark.parametrize("side", ["staff", "admin"])
def test_the_card_shows_each_outcome_its_todos_and_the_ai_label(engine, services, db, qml_warnings, side):
    # 管理員端看的是別的服務人員的服務（權限內的每一筆）；服務人員端看自己的
    account = STAFF_ID if side == "staff" else OTHER_ID
    db.add_account(OTHER_ID, "leo", OTHER_NAME)
    for sid, value, todos, _, _ in CASES:
        add_service(db, sid, account_id=account, outcome=value, todos=todos)
    module = server_home_init if side == "staff" else admin_home_init
    home = module.launch(engine, STAFF_ID if side == "staff" else ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    for sid, value, todos, label, icon in CASES:
        detail = qml(window, f'openServiceDetail("{sid}", "")')
        wait_until(lambda: home.mediaModel.serviceId == sid and home.mediaModel.state == "ready")  # noqa: B023
        card = find(detail, "serviceOutcomeCard")
        wait_until(card.isVisible)
        assert find(card, "outcomeLabel").property("text") == label, sid
        assert find(card, "outcomeIcon").property("source").fileName() == icon
        assert qml(card, "Accessible.name") == "Outcome and to-dos for this service"
        items = [item.property("text") for item in named(card, "todoItem")]
        assert items == [todo for todo in (todos or "").split("\n") if todo]
        if value is None:  # 還沒填：Unknown、沒有項目、說明什麼時候會有
            assert find(card, "outcomeNote").property("text") == NOT_FILLED
            assert not find(card, "todosTitle").isVisible() and not find(card, "noTodos").isVisible()
        else:
            assert find(card, "outcomeNote").property("text") == AI_NOTE
            assert find(card, "todosTitle").isVisible()
            assert find(card, "noTodos").isVisible() is (not items)
        if items:
            assert qml(named(card, "todoItem")[0], "Accessible.name") == f"To-do 1: {items[0]}"
    # 結果在分數列下面、影片的上面
    order = [item.objectName() for item in find(detail, "scoreStrip").parentItem().childItems() if item.isVisible()]
    assert order.index("scoreStrip") < order.index("serviceOutcomeCard") < order.index("mediaCard")
    del home


def test_staff_opening_another_staff_members_service_sees_nothing(engine, services, db, s3, qml_warnings, tmp_path):
    db.add_account(OTHER_ID, "leo", OTHER_NAME)
    sid = "S20261101103000"
    add_service(db, sid, account_id=OTHER_ID, outcome="Handoff needed", todos="Send the form to the loans team.")
    add_files(db, s3, sid)
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = engine.rootObjects()[-1]
    detail = qml(window, f'openServiceDetail("{sid}", "")')
    wait_until(lambda: home.mediaModel.state == "ready" and not home.mediaModel.busy)
    assert not home.mediaModel.serviceFound
    assert not find(detail, "serviceOutcomeCard").isVisible()  # 沒有結果、待辦
    assert find(detail, "noScores").isVisible() and not find(detail, "customerScores").isVisible()
    assert [find(detail, f"{kind}Box").property("slotState") for kind in ("cam0", "cam1")] == ["missing", "missing"]
    assert qml(window, "header.subtitle") == ""  # 沒有日期、服務人員、狀態
    assert home.chartsModel.state == "empty"
    assert not (tmp_path / "media").exists()
    del home
