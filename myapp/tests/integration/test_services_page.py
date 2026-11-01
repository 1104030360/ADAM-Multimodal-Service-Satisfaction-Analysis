"""新版 Services 頁（兩端）和服務明細的頁首、分數列的資料（新版介面 U2，PLAN §6-4、§6-5、§6-9；T31）。

接真的 MySQL 8.4：
- 查詢：只有分析完成的、新的在前、一次 8 筆；總筆數和日期範圍在同一個查詢（每次搜尋、每次 Show More 都是 1 個往返）。
- 分數：-1 和沒有分數的那一列是 None（畫面「—」），不是 0；分數帶跟著畫面上的數字。
- 明細：頁首（日期、時間、分析狀態、服務人員）和 8 個分數跟檔案清單同一個查詢，還是 1 個往返。
- 畫面（每一頁的主要任務，PLAN §7）：Services 列出 8 筆、Show More、Details 打開明細；明細的頁首、分數列、
  圖表放在淺灰的紙上；兩端各跑一次，QML 零警告。
GUI 執行緒一律不碰網路（no_network_on_gui_thread）。
"""
from datetime import date
from itertools import count

import pytest
from PySide6.QtCore import QObject

from myapp import media
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, wait_until

pytestmark = pytest.mark.integration

OTHER_ID = "00000000-0000-0000-0000-000000000003"
OTHER_NAME = "王小明"
_keys = count(1)


@pytest.fixture(autouse=True)
def gui_thread_stays_off_the_network(db, no_network_on_gui_thread):
    """db fixture 自己的連線（放測試資料用）開好之後，才開始禁止 GUI 執行緒碰網路。"""


def warm_up(services):
    """先在 db lane 連線、建好 dictionary cursor（第一次要多幾個往返），之後的往返數才是查詢本身的。"""
    done = []

    def work(token, report):
        with services.db.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT 1 AS one")
            return cursor.fetchall()

    services.tasks.submit("db", f"test:warm-up:{next(_keys)}", work, on_ok=done.append)
    wait_until(lambda: done)


def add_completed(db, count_, account_id=STAFF_ID, day=20, scores=(80, 90)):
    """count_ 筆分析完成的服務：10/day 起每天 10:00 一筆，每筆 5 分鐘；分數是 (顧客, 服務人員)。回傳服務 ID（新的在前）。"""
    ids = []
    for i in range(count_):
        sid = f"S202610{day + i:02d}10000{0 if account_id == STAFF_ID else 1}"
        db.add_service(sid, f"2026-10-{day + i:02d} 10:00:00", account_id=account_id)
        if scores is not None:
            db.add_scores(sid, customer=scores[0], server=scores[1])
        ids.append(sid)
    return ids[::-1]


def settle(services, key):
    wait_until(lambda: not services.tasks.isBusy(key))


# ---------------------------------------------------------------- 服務人員端


def test_staff_list_shows_eight_newest_completed_services_and_counts_all(services, db, db_proxy):
    ids = add_completed(db, 10)
    db.add_service("S20261031100000", "2026-10-31 10:00:00", status=2)  # 還在分析：不列
    db.add_service("S20261031110000", "2026-10-31 11:00:00", account_id=ADMIN_ID)  # 別人的：不列
    manager = server_home_init.ServiceManager(services, STAFF_ID)
    warm_up(services)
    db_proxy.reset()
    assert manager.search("", "")
    settle(services, server_home_init.SEARCH_KEY)
    assert db_proxy.snapshot()["rt"] == 1  # 這一頁、總筆數、日期範圍：一個查詢
    assert [row["serviceId"] for row in manager.listModel.rows()] == ids[:8]
    assert (manager.status, manager.totalCount, manager.hasMore) == ("ready", 10, True)
    assert (manager.countText, manager.subtitle) == ("10 services", "Oct 20 – Oct 29, 2026 · newest first")
    assert manager.shownText == "Showing 8 of 10"

    db_proxy.reset()
    assert manager.showMore()
    settle(services, server_home_init.SEARCH_KEY)
    assert db_proxy.snapshot()["rt"] == 1
    assert [row["serviceId"] for row in manager.listModel.rows()] == ids
    assert not manager.hasMore and manager.shownText == "Showing 10 of 10"


def test_staff_rows_have_length_and_scores_without_minus_one(services, db):
    db.add_service("S20261030120000", "2026-10-30 12:00:00")  # 5 分鐘（helpers 的 add_service）
    db.add_scores("S20261030120000", customer=84.96, server=-1)  # 服務人員的總分沒有資料
    db.add_service("S20261030130000", "2026-10-30 13:00:00")  # 分析完成，但沒有分數那一列
    manager = server_home_init.ServiceManager(services, STAFF_ID)
    manager.search("2026-10-30", "2026-10-30")
    settle(services, server_home_init.SEARCH_KEY)
    newer, older = manager.listModel.rows()
    assert newer["serviceId"] == "S20261030130000"
    assert (newer["customerScore"], newer["staffScore"]) == (None, None)
    assert older == {
        "serviceId": "S20261030120000", "staffName": STAFF_NAME, "startTime": "2026-10-30 12:00:00",
        "dateText": "Oct 30, 12:00" if date.today().year == 2026 else "Oct 30, 2026, 12:00",
        "lengthText": "5 min",
        "customerScore": 85.0,  # 四捨五入到小數 1 位：畫面是 85.0，Theme.bandName 也用這個數字，所以是 Good
        "staffScore": None,
    }
    assert manager.rangeText == "Oct 30, 2026"


# ---------------------------------------------------------------- 管理員端


def test_admin_list_filters_by_staff_and_lists_only_completed_newest_first(services, db, db_proxy):
    db.add_account(OTHER_ID, "wang", OTHER_NAME)
    mine = add_completed(db, 6)
    theirs = add_completed(db, 4, account_id=OTHER_ID, day=22)
    db.add_service("S20261031100000", "2026-10-31 10:00:00", status=1)  # 還沒分析：以前列得出來，現在不列
    home = admin_home_init.AdminHome(ADMIN_ID, services)  # 留著 home：ServiceManager 是它的子物件
    manager = home.serviceManager
    manager.loadNames()
    wait_until(lambda: manager.namesState == "ready")
    warm_up(services)  # 名單用的是 tuple 的 cursor；第一次建 dictionary cursor 要多 1 個往返
    db_proxy.reset()
    assert manager.search("", "", "")
    wait_until(lambda: manager.state == "ready" and not manager.refreshing)
    assert db_proxy.snapshot()["rt"] == 1
    everyone = sorted(mine + theirs, reverse=True)  # 服務 ID 就是開始時間：新的在前
    assert [row["serviceId"] for row in manager.listModel.rows()] == everyone[:8]
    assert {row["staffName"] for row in manager.listModel.rows()} == {STAFF_NAME, OTHER_NAME}
    assert (manager.totalCount, manager.countText) == (10, "10 services")
    assert manager.subtitle == "All staff · Oct 20 – Oct 25, 2026 · newest first"

    assert manager.showMore()
    wait_until(lambda: not manager.loadingMore)
    assert [row["serviceId"] for row in manager.listModel.rows()] == everyone

    assert manager.search(OTHER_NAME, "2026-10-23", "")
    wait_until(lambda: manager.state == "ready" and not manager.refreshing)
    assert [row["serviceId"] for row in manager.listModel.rows()] == theirs[:3]
    assert manager.subtitle == f"{OTHER_NAME} · Oct 23 – Oct 25, 2026 · newest first"
    assert not manager.hasMore
    del home


# ---------------------------------------------------------------- 服務明細：頁首和分數列


def open_detail(services, sid, start=""):
    model, charts = media.MediaModel(), media.ChartsModel()
    model.open(sid, start, services, charts)
    wait_until(lambda: model.state in ("ready", "error") and not model.busy)
    return model


def test_detail_header_and_scores_come_with_the_file_list_in_one_round_trip(services, db, s3, db_proxy):
    sid = "S20261031091500"
    db.add_service(sid, "2026-10-31 09:15:00")
    db.add_scores(sid, customer=91.66, server=86, customer_text=90, server_facial=-1)
    db.add_media(sid, "video", f"s3://adam-test/video/{sid}_cam0.avi")
    db.add_media(sid, "charts", f"s3://adam-test/charts/{sid}_summary.png")
    warm_up(services)
    db_proxy.reset()
    model, charts = media.MediaModel(), media.ChartsModel()
    model.open(sid, "2026-10-31 09:15:00", services, charts)
    # 查到之前：Details 傳進來的開始時間先拿來顯示日期
    assert (model.dateText, model.timeText, model.analysisStatus, model.headerText) == (
        "Oct 31, 2026", "09:15", 0, "Oct 31, 2026 · 09:15")
    assert not model.hasScores and model.scores["customer"]["total"] is None
    wait_until(lambda: model.state == "ready")
    assert db_proxy.snapshot()["rt"] == 1  # 頁首、分數、檔案清單：一個查詢
    assert model.headerText == "Oct 31, 2026 · 09:15–09:20 · Analysis completed"
    assert (model.analysisStatus, model.statusText, model.staffName) == (3, "Analysis completed", STAFF_NAME)
    assert model.scores == {"customer": {"total": 91.7, "text": 90.0, "audio": 91.7, "facial": 91.7},
                            "staff": {"total": 86.0, "text": 86.0, "audio": 86.0, "facial": None}}
    assert model.hasScores
    assert model.slot("cam0").file.name == f"{sid}_cam0.avi" and model.slot("cam1").state == "missing"
    assert charts.totalCount == 1
    model.close()


def test_detail_of_a_service_still_being_analyzed_has_no_scores(services, db):
    db.add_service("S20261031100000", "2026-10-31 10:00:00", status=2)
    model = open_detail(services, "S20261031100000")  # 只給服務 ID（例如從首頁的長條圖打開）
    assert model.startTime == "2026-10-31 10:00:00"  # 從資料庫補上
    assert model.headerText == "Oct 31, 2026 · 10:00–10:05 · Analyzing"
    assert model.scores == {"customer": dict.fromkeys(("total", "text", "audio", "facial")),
                            "staff": dict.fromkeys(("total", "text", "audio", "facial"))}
    assert not model.hasScores
    assert model.serviceFound and [model.slot(kind).state for kind in ("cam0", "cam1", "audio")] == ["missing"] * 3


def test_detail_of_a_service_that_does_not_exist(services, db):
    model = open_detail(services, "S20000101000000")
    assert model.state == "ready" and not model.serviceFound
    assert (model.headerText, model.staffName, model.analysisStatus) == ("", "", 0)


# ---------------------------------------------------------------- 畫面：找服務、打開明細（兩端）


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


def find(root, name):
    found = root.findChild(QObject, name)
    if found is None:  # ListView 建立的列沒有 QObject parent：沿著畫面樹找
        pending = [root]
        while pending and found is None:
            item = pending.pop(0)
            found = item if item.objectName() == name else None
            pending.extend(item.childItems())
    assert found is not None, name
    return found


def rows(view):
    """表格已經建立的列（剛被移除、index 是 -1 的不算），依 index 排序。"""
    items = [item for item in view.property("contentItem").childItems()
             if item.objectName() == "resultRow" and item.property("index") >= 0]
    return sorted(items, key=lambda item: item.property("index"))


def texts(item, name):
    return [found.property("text") for found in _all(item, name)]


def _all(root, name):
    found, pending = [], [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
    return found


@pytest.mark.parametrize("side", ["staff", "admin"])
def test_find_a_service_show_more_and_open_its_details(engine, services, db, qml_warnings, side):
    ids = add_completed(db, 10, scores=(91.66, 64))
    module = server_home_init if side == "staff" else admin_home_init
    home = module.launch(engine, STAFF_ID if side == "staff" else ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    qml(window, 'mainContentLoader.source = "../../qml/pages/%s_service_page.qml"' % ("server" if side == "staff" else "admin"))
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    page = qml(window, "mainContentLoader.item")  # 留著參照，子物件的 wrapper 才不會失效（T17）
    manager = home.serviceManager
    # 一打開就查一次全部（新的在前），一次 8 筆
    wait_until(lambda: manager.totalCount == 10 and not manager.loadingMore)
    view = find(page, "resultList")
    wait_until(lambda: len(rows(view)) == 8)
    assert [row.property("serviceId") for row in rows(view)] == ids[:8]
    assert find(page, "countText").property("text") == "10 services"
    subtitle = find(page, "subtitleText").property("text")
    assert subtitle == ("" if side == "staff" else "All staff · ") + "Oct 20 – Oct 29, 2026 · newest first"
    assert find(page, "shownText").property("text") == "Showing 8 of 10"
    # 分數一定配分數帶的字；管理員端多一欄 Staff
    first = rows(view)[0]
    assert texts(first, "scoreValue") == ["91.7", "64.0"] and texts(first, "scoreBand") == ["Good", "Needs improvement"]
    assert find(first, "staffCell").isVisible() is (side == "admin")
    assert find(first, "lengthCell").property("text") == "5 min"

    qml(find(page, "showMoreButton"), "clicked()")
    wait_until(lambda: len(rows(view)) == 10)
    assert not find(page, "listFooter").isVisible()  # 都列出來了

    # Details：外殼的 openServiceDetail，頁首換成服務 ID、「‹ Services」和說明
    second = rows(view)[1]  # 留著列的參照：子物件的 wrapper 才不會跟著失效（T17）
    button = find(second, "detailsButton")
    assert button.property("text") == "Details" and qml(button, "Accessible.name") == f"Details, {ids[1]}"
    qml(button, "clicked()")
    assert qml(window, "mainContentLoader.source.toString()").endswith("_service_detail_page.qml")
    wait_until(lambda: home.mediaModel.state == "ready")
    assert (qml(window, "header.title"), qml(window, "header.backText")) == (ids[1], "Services")
    expected = f"Oct {int(ids[1][7:9])}, 2026 · 10:00–10:05 · Analysis completed"
    assert qml(window, "header.subtitle") == (expected if side == "staff" else f"{STAFF_NAME} · {expected}")
    detail = qml(window, "mainContentLoader.item")
    customer, staff = find(detail, "customerScores"), find(detail, "staffScores")
    # Overall 一位小數，Text、Audio、Facial 取整數（設計稿）
    assert texts(find(customer, "overall"), "scoreValue") == ["91.7"]
    assert sorted(texts(customer, "scoreValue")) == ["91.7", "92", "92", "92"]
    assert find(customer, "overallBand").property("text") == "Good"
    assert texts(find(staff, "overall"), "scoreValue") == ["64.0"]
    assert sorted(texts(staff, "scoreValue")) == ["64", "64", "64", "64.0"]
    assert find(staff, "overallBand").property("text") == "Needs improvement"
    assert not find(detail, "noScores").isVisible()

    qml(find(window, "headerBack"), "clicked()")  # 回到 Services：結果和顯示到第幾筆都還在
    assert qml(window, "mainContentLoader.source.toString()").endswith("_service_page.qml")
    page = qml(window, "mainContentLoader.item")
    view = find(page, "resultList")
    wait_until(lambda: len(rows(view)) == 10)
    del home


def test_detail_without_scores_explains_why_and_charts_sit_on_paper(engine, services, db, s3, qml_warnings, tmp_path):
    sid = "S20261031100000"
    db.add_service(sid, "2026-10-31 10:00:00", status=2)  # 還在分析：沒有分數
    db.add_media(sid, "charts", f"s3://adam-test/charts/{sid}_a.png")
    s3.put_object(Bucket="adam-test", Key=f"charts/{sid}_a.png", Body=png())
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = engine.rootObjects()[-1]
    detail = qml(window, f'openServiceDetail("{sid}", "")')  # 只給服務 ID（首頁的長條圖、搜尋也是這樣打開）
    wait_until(lambda: home.mediaModel.state == "ready" and home.chartsModel.state == "ready")
    assert qml(window, "header.subtitle") == "Oct 31, 2026 · 10:00–10:05 · Analyzing"
    note = find(detail, "noScores")
    assert note.isVisible() and note.property("text") == "Scores appear here once the analysis is complete."
    assert not find(detail, "customerScores").isVisible()
    find(detail, "tabBar").setProperty("currentIndex", 1)
    frame = find(detail, "chartFrame")
    wait_until(lambda: qml(frame, "Qt.colorEqual(color, Theme.paper)"))  # 白底的圖表放在淺灰的紙上（UD8）
    assert find(detail, "chartCounter").property("text") == "Chart 1 of 1"
    assert not find(detail, "videoPage").isVisible() and find(detail, "chartsPage").isVisible()
    del home


def png():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image, data = QImage(16, 12, QImage.Format.Format_RGB32), QByteArray()
    image.fill(QColor("#FFFFFF"))
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)
