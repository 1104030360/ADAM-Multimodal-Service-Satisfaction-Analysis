"""新版的分析頁（new-UIUX PLAN 6-3、UD6、UD7；設計稿 StaffAnalysis；T30）：真的外殼、真的 analysis.qml、真的 MySQL。

- 三個分頁照流程排（Not analyzed → Analyzing → Completed），各有數量；沒有 Status 欄；預設的分頁
- 每個分頁自己的欄位和動作：Analyze（Sending…、分析失敗過是 Retry）、「Sent … ago」、分數＋分數帶、View → 明細
- 一次 8 筆、「Showing 8 of 20」、Show More 馬上多 8 筆
- 第一次載入：骨架、不顯示空狀態和假的 0；第一次就失敗：區塊錯誤＋Retry；之後更新不到：清單留著、只警告一次
- showTab()（首頁的服務方塊）、鍵盤的左右鍵
送出分析、監看、View Results 的流程在 test_analysis_flow.py；清單模型和查詢在 test_analysis.py。
"""
import os
import threading

import pytest
import shiboken6
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from myapp import media
from myapp.init import server_home_init
from myapp.init.server_home_init import ANALYSIS_KEY, LAUNCH_KEYS
from tests.integration.helpers import STAFF_ID, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

TABS = ("notAnalyzed", "analyzing", "completed")
OFFLINE = "Can't reach the database. Check your network and try again."


@pytest.fixture(autouse=True)
def no_qml_warnings(engine):
    """每個狀態都不能讓 QML 發出警告（test_qml_warnings 的規則；它只打開靜態的頁面）。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield
    assert warnings == []


# ── 小工具 ───────────────────────────────────────────────────────────────────

def launch_staff(engine, services):
    home = server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    return home, home.shell_window


def open_page(window, services):
    """和按側邊欄的 Analysis 一樣：換頁，清單在背景重新整理。"""
    qml(window, "analysisButton.click()")
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    return qml(window, "mainContentLoader.item")


def find(root, name):
    """用 objectName 找子物件。用它的子物件時要留著 root 的參照（T17）。"""
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def rows_of(view):
    """ListView 已經建立的列，依 index 排序（findChild 找不到 delegate）。"""
    rows = [item for item in view.property("contentItem").childItems() if item.objectName() == "serviceRow"]
    return sorted(rows, key=lambda item: item.property("index"))


def text(row, name):
    return find(row, name).property("text")


def tab_state(page):
    """{分頁: (數量, 數量看得到嗎, 選到了嗎)}"""
    return {key: (find(page, f"{key}Tab").property("count"), find(find(page, f"{key}Tab"), "countBadge").isVisible(),
                  find(page, f"{key}Tab").property("selected")) for key in TABS}


def add_services(db, count, status, day=29, scores=None):
    """count 筆，錄影時間新的在前（10 月 day 日 08:00 起每分鐘一筆）；回傳 service ID（照畫面順序）。"""
    added = []
    for i in range(count):
        minute = count - i
        sid = f"S202610{day:02d}{8 + minute // 60:02d}{minute % 60:02d}00"
        db.add_service(sid, f"2026-10-{day:02d} {8 + minute // 60:02d}:{minute % 60:02d}:00", status=status)
        if scores is not None:
            db.add_scores(sid, *scores)
        added.append(sid)
    return added


@pytest.fixture
def gated_list(monkeypatch):
    """分析清單的查詢先卡住（gate.set() 才放行）：打開分析頁時清單還沒回來。"""
    gate = threading.Event()
    original = server_home_init.list_services
    monkeypatch.setattr(server_home_init, "list_services",
                        lambda *args, **kwargs: (gate.wait(5), original(*args, **kwargs))[1])
    yield gate
    gate.set()  # 測試失敗時也放行，不讓 db lane 卡住


def cut_database(services, db, monkeypatch):
    """斷線：db lane 已經連上的那條被切斷，重連也連不上。回傳恢復用的函式。"""
    found = []

    def connection_id(token, report):
        with services.db.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT CONNECTION_ID() AS id")
            return cursor.fetchone()["id"]

    services.tasks.submit("db", "test:connection-id", connection_id, on_ok=found.append)
    wait_until(lambda: found)
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    db.execute(f"KILL {found[0]}")
    return lambda: monkeypatch.setenv("DB_PORT", port)


# ── 分頁、欄位、動作 ─────────────────────────────────────────────────────────

def test_tabs_follow_the_flow_with_counts_and_each_tab_has_its_own_columns(engine, services, db,
                                                                         no_network_on_gui_thread):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80.64, server=69.6)
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_scores("S20261030100000", customer=91, server=-1)  # 員工那一邊沒有分數
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2)  # 別台電腦送的
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=1)
    home, window = launch_staff(engine, services)
    page = open_page(window, services)

    tab_row = find(page, "notAnalyzedTab").parentItem()
    assert [item.objectName() for item in tab_row.childItems()] == [f"{key}Tab" for key in TABS]  # 照流程排（UD6）
    assert tab_state(page) == {"notAnalyzed": (2, True, True), "analyzing": (1, True, False),
                               "completed": (2, True, False)}  # 有沒送出的：預設停在 Not analyzed
    assert qml(find(page, "notAnalyzedTab"), "Accessible.name") == "Not analyzed, 2 services"

    rows = rows_of(find(page, "notAnalyzedList"))
    assert [text(row, "serviceIdCell") for row in rows] == ["S20261030130000", "S20261030120000"]  # 新的在前
    assert [(text(row, "recordedCell"), text(row, "lengthCell")) for row in rows] == [
        ("Oct 30, 13:00", "5 min"), ("Oct 30, 12:00", "5 min")]
    assert find(rows[0], "analyzeButton").property("text") == "Analyze"
    assert rows[0].findChild(QObject, "statusChip") is None  # 沒有 Status 欄：分頁已經說了（UD6）
    assert not find(rows[0], "failedNote").isVisible()
    assert qml(rows[0], "Accessible.name") == "S20261030130000, recorded Oct 30, 13:00, length 5 min"
    assert find(page, "notAnalyzedDescription").isVisible()
    assert not find(page, "notAnalyzedFooter").isVisible()  # 不到一頁：沒有「Showing」和 Show More

    qml(page, 'showTab("analyzing")')
    [row] = rows_of(find(page, "analyzingList"))
    assert text(row, "sentCell") == "Sent earlier"  # 不知道什麼時候送的：不假裝知道（T30）
    assert row.findChild(QObject, "analyzeButton") is None and row.findChild(QObject, "viewButton") is None

    qml(page, 'showTab("completed")')
    rows = rows_of(find(page, "completedList"))
    assert [(text(row, "customerCell"), text(row, "staffCell")) for row in rows] == [
        ("91.0 Good", "—"), ("80.6 Fair", "69.6 Needs improvement")]  # 小數一位＋分數帶；-1 是「—」
    assert qml(rows[1], "Accessible.name") == (
        "S20261030090000, recorded Oct 30, 09:00, customer 80.6 Fair, staff 69.6 Needs improvement")


@pytest.mark.parametrize("statuses, tab", [
    ((3, 2), "completed"),      # 沒有沒送出的：Completed
    ((2,), "analyzing"),        # Completed 也是空的：不停在空的分頁
    ((), "notAnalyzed"),        # 全部是空的：流程的第一步
])
def test_default_tab_when_nothing_waits_to_be_sent(engine, services, db, statuses, tab):
    for hour, status in enumerate(statuses, start=9):
        db.add_service(f"S20261030{hour:02d}0000", f"2026-10-30 {hour:02d}:00:00", status=status)
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    assert qml(page, "selectedTab") == tab


def test_view_opens_the_service_detail(engine, services, db, s3, no_network_on_gui_thread):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    [row] = rows_of(find(page, "completedList"))
    button = find(row, "viewButton")
    assert qml(button, "Accessible.name") == "View S20261030090000"
    qml(button, "clicked()")
    assert qml(window, "currentPage") == "server_service_detail_page.qml"  # 外殼的 openServiceDetail()
    assert home.mediaModel.serviceId == "S20261030090000"
    wait_until(lambda: not services.tasks.isBusy(media.list_key("S20261030090000")))  # 明細的檔案清單在背景查


# ── 一次 8 筆、Show More ─────────────────────────────────────────────────────

def test_show_more_adds_eight_rows_at_once_until_all_are_shown(engine, services, db, no_network_on_gui_thread):
    completed = add_services(db, 20, status=3, scores=(80, 75))
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    view = find(page, "completedList")
    wait_until(lambda: len(rows_of(view)) == 8)
    assert [text(row, "serviceIdCell") for row in rows_of(view)] == completed[:8]
    footer = find(page, "completedFooter")
    assert footer.isVisible() and text(footer, "showingText") == "Showing 8 of 20"
    assert tab_state(page)["completed"][0] == 20  # 分頁上的是總數，不是顯示的筆數

    more = find(footer, "showMoreButton")
    qml(more, "clicked()")
    assert home.serviceModel.completedModel.count == 16  # 下一頁已經在手上：不等往返
    wait_until(lambda: len(rows_of(view)) == 16 and not services.tasks.isBusy(ANALYSIS_KEY))
    assert text(footer, "showingText") == "Showing 16 of 20" and more.isVisible()
    qml(more, "clicked()")
    wait_until(lambda: len(rows_of(view)) == 20)
    assert text(footer, "showingText") == "Showing 20 of 20" and not more.isVisible()

    qml(window, "homeButton.click()")
    page = open_page(window, services)  # 再打開：回到一頁（UD7）
    wait_until(lambda: len(rows_of(find(page, "completedList"))) == 8)


# ── 載入中、錯誤、空的 ───────────────────────────────────────────────────────

def test_first_load_shows_a_skeleton_not_an_empty_list_or_zero_counts(engine, services, db, gated_list):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    home = server_home_init.launch(engine, STAFF_ID, services)  # 清單的查詢卡住：不等 launch() 載入
    window = home.shell_window
    qml(window, "analysisButton.click()")
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    page = qml(window, "mainContentLoader.item")
    assert home.serviceModel.status == "loading"
    wait_until(lambda: find(page, "loadingPlaceholder").isVisible())  # 過了 busyDelay 才出現，不轉圈
    for key in TABS:
        assert not find(page, f"{key}Empty").isVisible()  # 不先閃一下「沒有服務」（DS-25）
        assert not find(page, f"{key}Table").isVisible()
    assert tab_state(page) == {key: (0, False, False) for key in TABS}  # 不顯示假的 0，也還不選分頁

    gated_list.set()
    wait_until(lambda: home.serviceModel.status == "ready")
    assert not find(page, "loadingPlaceholder").isVisible()
    assert tab_state(page)["notAnalyzed"] == (1, True, True)  # 清單到了才停在預設的分頁
    assert len(rows_of(find(page, "notAnalyzedList"))) == 1


def test_a_tab_chosen_before_the_list_arrives_is_kept(engine, services, db, gated_list):
    """首頁的服務方塊：onLoadAnalysis()、showPageNow()，再 showTab()；清單還沒回來也一樣。"""
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = home.shell_window
    qml(window, "serverHome.onLoadAnalysis()")
    page = qml(window, 'showPageNow("analysis.qml")')
    qml(page, 'showTab("completed")')
    gated_list.set()
    wait_until(lambda: home.serviceModel.status == "ready")
    assert qml(page, "selectedTab") == "completed"  # 預設的分頁不會蓋掉使用者選的
    assert find(page, "completedEmpty").isVisible()
    empty = find(page, "completedEmpty")
    assert empty.property("actionText") == "View Not analyzed"  # 有沒送出的才給捷徑
    qml(empty, "actionTriggered()")
    assert qml(page, "selectedTab") == "notAnalyzed"
    qml(page, 'showTab("nope")')  # 不認得的分頁：不動
    assert qml(page, "selectedTab") == "notAnalyzed"


def test_each_tab_has_an_empty_state(engine, services, db, no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    expected = {"notAnalyzed": "No services to analyze", "analyzing": "Nothing is being analyzed",
                "completed": "No completed analyses yet"}
    for key, title in expected.items():
        qml(page, f'showTab("{key}")')
        empty = find(page, f"{key}Empty")
        assert empty.isVisible() and empty.property("title") == title
        assert not find(page, f"{key}Description").isVisible()  # 空狀態自己有說明
        assert tab_state(page)[key] == (0, True, True)  # 載入好了：0 是真的
    assert find(page, "completedEmpty").property("actionText") == ""  # Not analyzed 也是空的：不給捷徑


def test_first_load_failure_shows_retry_in_the_card(engine, services, db, monkeypatch, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    error = find(page, "listError")
    assert error.isVisible() and error.property("title") == "Couldn't load your services"
    assert error.property("detail") == OFFLINE
    assert tab_state(page) == {key: (0, False, False) for key in TABS}  # 失敗時不顯示假的 0
    assert toasts(services.notifier) == []  # 區塊錯誤，不跳通知（DS-27）
    monkeypatch.setenv("DB_PORT", port)
    qml(error, "retry()")
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    assert not error.isVisible() and find(page, "notAnalyzedTable").isVisible()
    assert tab_state(page)["notAnalyzed"] == (1, True, True)


def test_refresh_failure_keeps_the_list_and_warns_once(engine, services, db, monkeypatch):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    restore = cut_database(services, db, monkeypatch)
    for _ in range(2):  # 斷線期間重新整理失敗兩次：警告只有一則
        qml(window, "serviceModel.refresh()")
        wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    [warning] = toasts(services.notifier)
    assert (warning["severity"], warning["title"], warning["detail"], warning["count"]) == (
        "warning", "Can't refresh analysis status", "Can't reach the database. Retrying in 15 seconds.", 1)
    assert find(page, "notAnalyzedTable").isVisible() and not find(page, "listError").isVisible()  # 清單留著
    assert len(rows_of(find(page, "notAnalyzedList"))) == 1

    restore()
    qml(window, "serviceModel.refresh()")
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    assert toasts(services.notifier) == []  # 恢復後警告自己消失


def test_watcher_warns_even_if_the_list_has_no_data(engine, services, db, monkeypatch):
    """在首頁等分析結果時斷線：清單沒有資料（分析頁沒打開過、或第一次就沒載入成功）也要說「更新不到」，
    不然使用者不知道通知會晚到（T30）。"""
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=2)

    def no_list(*args, **kwargs):
        raise ConnectionError("the list is not loaded")

    monkeypatch.setattr(server_home_init, "list_services", no_list)  # 清單一直沒有資料；監看照常
    home, window = launch_staff(engine, services)
    assert home.serviceModel.status != "ready" and home.watcher.watched == ["S20261030120000"]
    assert toasts(services.notifier) == []  # 清單載入失敗本身不跳通知（DS-27）
    restore = cut_database(services, db, monkeypatch)
    home.watcher.tick()
    wait_until(lambda: len(toasts(services.notifier)) == 1)
    [warning] = toasts(services.notifier)
    assert warning["title"] == "Can't refresh analysis status"
    restore()
    home.watcher.tick()
    wait_until(lambda: toasts(services.notifier) == [])


def test_refresh_updates_rows_in_place(engine, services, db, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    view = find(page, "notAnalyzedList")
    [old_row] = rows_of(view)
    pointer = shiboken6.getCppPointer(old_row)[0]
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=1)  # 新錄好的一筆
    qml(window, "serviceModel.refresh()")  # 錄影存好之後的重新整理
    wait_until(lambda: len(rows_of(view)) == 2)
    first, second = rows_of(view)
    assert text(first, "serviceIdCell") == "S20261030130000"
    assert shiboken6.getCppPointer(second)[0] == pointer  # 沒變的那一列沒有重建（ENG-08）
    assert tab_state(page)["notAnalyzed"][0] == 2


# ── 鍵盤 ─────────────────────────────────────────────────────────────────────

def test_arrow_keys_move_between_tabs(engine, services, db):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    home, window = launch_staff(engine, services)
    page = open_page(window, services)
    tab = find(page, "notAnalyzedTab")
    qml(tab, "forceActiveFocus(Qt.TabFocusReason)")
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert qml(page, "selectedTab") == "analyzing" and find(page, "analyzingTab").property("activeFocus")
    QTest.keyClick(window, Qt.Key.Key_Left)
    QTest.keyClick(window, Qt.Key.Key_Left)  # 從第一個往左：繞到最後一個
    assert qml(page, "selectedTab") == "completed" and find(page, "completedTab").property("visualFocus")
