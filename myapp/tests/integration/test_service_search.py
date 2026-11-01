"""頁首的搜尋服務 ID（UD5、T32）接上真的 MySQL 8.4 與 TaskRunner。

- 服務人員只搜得到自己的服務；管理員搜得到全部，也能用名字（開頭或任一個字的開頭，不分大小寫和重音）搜。
- LIKE 的萬用字元（% _ \\）照字面比對；最多 10 筆，依時間新的在前，hasMore 表示還有更多。
- 一次搜尋 1 個往返；打字很快時只送出最後那一次。連不上資料庫時錯誤留在搜尋的狀態裡，Retry 會再查。
- 兩個外殼都註冊了 serviceSearch，它和外殼活得一樣久；結果的 serviceId、startTime 可以直接打開明細頁。
- 頁首的搜尋框（第二階段）：打字 → 清單 → ↓、Enter 或點一下 → 明細頁；明細頁沒有搜尋框，回到其他頁又出現。
"""
import gc
import os

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from myapp.init import admin_home_init, server_home_init
from myapp.search import EMPTY, ERROR, IDLE, LOADING, READY, SEARCH_KEYS, ServiceSearch
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, unused_port, wait_until
from tests.integration.test_shared_metaobjects import app_objects, dynamic_slots

pytestmark = pytest.mark.integration

OFFLINE = "Can't reach the database. Check your network and try again."
MIA, CHEN, TOM, JOSE = (f"00000000-0000-0000-0000-0000000000a{i}" for i in range(1, 5))


def start_of(service_id):
    """S20261031143000 → 2026-10-31 14:30:00（服務 ID 就是開始錄影的時間）。"""
    digits = service_id[1:]
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}:{digits[12:14]}"


def add(db, service_id, account_id=STAFF_ID, status=3):
    db.add_service(service_id, start_of(service_id), status=status, account_id=account_id)


def settled(box, services):
    return (not box.pending and box.state != LOADING and not box.refreshing
            and not any(services.tasks.isBusy(key) for key in SEARCH_KEYS.values()))


def search(box, services, text):
    """打字、不等 debounce 直接送出，等結果回來；回傳 serviceId 的清單。"""
    box.setQuery(text)
    box.searchNow()
    wait_until(lambda: settled(box, services), describe=lambda: (box.state, box.refreshing, box.pending))
    return [result["serviceId"] for result in box.results]


@pytest.fixture
def staff_box(services):
    return ServiceSearch.for_staff(services, STAFF_ID)


@pytest.fixture
def admin_box(services):
    return ServiceSearch.for_admin(services)


@pytest.fixture
def people(db):
    """五位服務人員、各自的服務。ID 的開頭都一樣（S2026…），只有帳號不同。"""
    db.add_account(MIA, "mia", "Mia Chen")
    db.add_account(CHEN, "chen", "Chen Wei")
    db.add_account(TOM, "tom", "Tom Moore")
    db.add_account(JOSE, "jose", "José Ruiz")
    add(db, "S20261030090000")                    # 佔位的服務人員（STAFF_ID）
    add(db, "S20261031090000", status=1)
    add(db, "S20261031100000", MIA)
    add(db, "S20261029100000", MIA, status=2)
    add(db, "S20261028100000", CHEN)
    add(db, "S20261031110000", TOM)
    add(db, "S20261027080000", JOSE)


# ── 誰看得到什麼 ─────────────────────────────────────────────────────────────

def test_staff_only_find_their_own_services(staff_box, services, people, no_network_on_gui_thread):
    assert search(staff_box, services, "S2026") == ["S20261031090000", "S20261030090000"]
    assert staff_box.results[0] == {"serviceId": "S20261031090000", "startTime": "2026-10-31 09:00:00", "status": 1,
                                    "staffName": STAFF_NAME}
    assert search(staff_box, services, "2026-10-30") == ["S20261030090000"]  # 只打數字（日期的寫法）也可以
    assert search(staff_box, services, "S20261031100000") == []  # 別人的服務：就算打完整的 ID 也查不到
    assert staff_box.state == EMPTY
    staff_box.setQuery("Mia")  # 服務人員不能用名字搜：不查
    assert staff_box.state == "invalid" and not staff_box.pending


def test_admins_find_everyones_services_by_id(admin_box, services, people, no_network_on_gui_thread):
    assert search(admin_box, services, "s2026103") == [  # 依時間新的在前
        "S20261031110000", "S20261031100000", "S20261031090000", "S20261030090000"]
    assert [result["staffName"] for result in admin_box.results] == ["Tom Moore", "Mia Chen", STAFF_NAME, STAFF_NAME]
    assert [result["status"] for result in admin_box.results] == [3, 3, 1, 3]


def test_admins_find_services_by_staff_name(admin_box, services, people, no_network_on_gui_thread):
    assert search(admin_box, services, "MIA") == ["S20261031100000", "S20261029100000"]  # 不分大小寫
    # 名字的開頭（Chen Wei）排在前面，再來是名字裡某個字的開頭（Mia Chen），各自依時間新的在前
    assert search(admin_box, services, "chen") == ["S20261028100000", "S20261031100000", "S20261029100000"]
    assert search(admin_box, services, "jose") == ["S20261027080000"]  # 不分重音（utf8mb4_0900_ai_ci）
    assert search(admin_box, services, "服務") == ["S20261031090000", "S20261030090000"]
    assert search(admin_box, services, "oore") == []  # 不比對字的中間
    assert search(admin_box, services, "  tom   moore ") == ["S20261031110000"]


def test_like_wildcards_match_literally(db, admin_box, services, no_network_on_gui_thread):
    for number, (username, name) in enumerate([("ab", "A_B Lee"), ("axb", "AxB Lee"), ("off", "50% Off"),
                                               ("kim", "500 Kim"), ("slash", "Back\\slash"), ("back", "Backslash")]):
        account_id = f"00000000-0000-0000-0000-0000000000b{number}"
        db.add_account(account_id, username, name)
        add(db, f"S2026103112000{number}", account_id)
    names = lambda: [result["staffName"] for result in admin_box.results]  # noqa: E731
    search(admin_box, services, "A_")
    assert names() == ["A_B Lee"]  # _ 不是「任一個字」
    search(admin_box, services, "50%")
    assert names() == ["50% Off"]  # % 不是「任何字串」
    search(admin_box, services, "Back\\")
    assert names() == ["Back\\slash"]
    search(admin_box, services, "Back")
    assert sorted(names()) == ["Back\\slash", "Backslash"]


# ── 筆數、往返、debounce ──────────────────────────────────────────────────────

def test_at_most_ten_newest_first_with_has_more(db, staff_box, services, no_network_on_gui_thread):
    ids = [f"S2026103114{minute:02d}00" for minute in range(12)]  # 14:00～14:11，最新的在最後
    for service_id in ids:
        add(db, service_id)
    assert search(staff_box, services, "S20261031") == list(reversed(ids))[:10]
    assert staff_box.hasMore
    assert search(staff_box, services, "S202610311411") == ["S20261031141100"]
    assert not staff_box.hasMore


def test_one_round_trip_per_search_and_fast_typing_sends_only_the_last(
        db, db_proxy, staff_box, services, no_network_on_gui_thread):
    add(db, "S20261031090000")
    add(db, "S20261030090000")
    search(staff_box, services, "S2025")  # 連線、建立 cursor 不算在預算裡（T16）
    db_proxy.reset()
    assert search(staff_box, services, "S20261031") == ["S20261031090000"]
    assert db_proxy.snapshot()["rt"] == 1  # 走 TLS（和 RDS 一樣），proxy 看不到指令，只數往返

    db_proxy.reset()
    for text in ("S", "S2", "S20", "S202", "S2026"):  # 打字很快：每個字之間不到 250 ms
        staff_box.setQuery(text)
    assert staff_box.pending and not services.tasks.isBusy(SEARCH_KEYS["staff"])
    wait_until(lambda: settled(staff_box, services))  # 真的等 debounce 的計時器
    assert [result["serviceId"] for result in staff_box.results] == ["S20261031090000", "S20261030090000"]
    assert db_proxy.snapshot()["rt"] == 1


def test_an_unreachable_database_shows_an_error_then_retry_recovers(
        db, staff_box, services, monkeypatch, no_network_on_gui_thread):
    add(db, "S20261031090000")
    real_port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # DbLane 第一次用到才連線：連不上
    assert search(staff_box, services, "S2026") == []
    assert (staff_box.state, staff_box.errorDetail, staff_box.failureCount) == (ERROR, OFFLINE, 1)
    assert "2003" in staff_box.errorTechnical  # 收在 Details 裡的技術細節
    monkeypatch.setenv("DB_PORT", real_port)  # 資料庫回來了
    assert staff_box.retry()
    wait_until(lambda: settled(staff_box, services))
    assert (staff_box.state, staff_box.failureCount, staff_box.errorDetail) == (READY, 0, "")
    assert [result["serviceId"] for result in staff_box.results] == ["S20261031090000"]


# ── 外殼 ─────────────────────────────────────────────────────────────────────

def test_both_shells_register_the_search_and_keep_it_alive(engine, services, db, no_network_on_gui_thread):
    server_home_init.launch(engine, STAFF_ID, services)  # 不留回傳值，和 MainWindow 一樣
    staff_window = engine.rootObjects()[-1]
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS))
    gc.collect()  # setContextProperty 不會留住 Python 物件：要靠 home（外殼留著它）
    assert qml(staff_window, "serviceSearch !== null && serviceSearch.state") == IDLE
    assert qml(staff_window, "serviceSearch.searchesNames") is False
    assert qml(staff_window, "serviceSearch.limit") == 10

    home = admin_home_init.launch(engine, ADMIN_ID, services)
    admin_window = engine.rootObjects()[-1]
    wait_until(lambda: not services.tasks.isBusy("admin:profile"))
    gc.collect()
    assert qml(admin_window, "serviceSearch.searchesNames") is True
    # test_shared_metaobjects 從 home 往下找得到它，而且沒有 PySide 動態加的 slot（T23 第 9 節）
    assert home.serviceSearch in app_objects(home) and dynamic_slots(home.serviceSearch) == []


def test_a_result_has_what_the_detail_page_needs(engine, services, db, no_network_on_gui_thread):
    add(db, "S20261031090502")
    home = server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS))
    search(home.serviceSearch, services, "S2026")
    [result] = home.serviceSearch.results
    home.load_service_media(result["serviceId"], result["startTime"])  # 第二階段的下拉清單會這樣打開明細
    assert (home.mediaModel.serviceId, home.mediaModel.startTime) == ("S20261031090502", "2026-10-31 09:05:02")
    wait_until(lambda: not home.mediaModel.busy)


# ── 頁首的搜尋框（第二階段） ───────────────────────────────────────────────────

@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告：測試結束時要是空的。"""
    found = []
    engine.warnings.connect(lambda errors: found.extend(error.toString() for error in errors))
    yield found
    assert found == []


def open_shell(module, account_id, engine, services):
    """登入後的主畫面，1440×900、作用中（按鍵只送到作用中的視窗）。"""
    home = module.launch(engine, account_id, services)
    window = engine.rootObjects()[-1]
    window.showNormal()
    window.setGeometry(0, 0, 1440, 900)
    wait_until(window.isExposed)
    window.requestActivate()
    wait_until(window.isActive)
    return home, window


def child(window, name):
    found = window.findChild(QObject, name)
    assert found is not None, name
    return found


def type_text(window, text):
    for char in text:
        QTest.keyClick(window, char)


def results_open(box):
    return qml(box, "dropdownOpen && status === 'ready' && !refreshing")


def shown_ids(box):
    return qml(box, "results.map(row => row.serviceId).join(' ')").split()


def test_staff_header_search_opens_the_detail_page_with_the_keyboard(
        engine, services, db, people, qml_warnings, no_network_on_gui_thread):
    home, window = open_shell(server_home_init, STAFF_ID, engine, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS))
    box, field = child(window, "serviceSearchField"), child(window, "searchInput")
    assert box.isVisible() and box.width() == 460  # 首頁：最寬 460
    assert field.property("placeholderText") == "Search by service ID"
    user = child(window, "headerUser")
    assert box.mapToScene(QPointF(box.width(), 0)).x() < user.mapToScene(QPointF(0, 0)).x()  # 在頭像左邊

    qml(field, "forceActiveFocus()")
    type_text(window, "2026-10-3")
    wait_until(lambda: results_open(box))
    assert shown_ids(box) == ["S20261031090000", "S20261030090000"]  # 只有自己的
    QTest.keyClick(window, Qt.Key.Key_Down)
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert qml(window, "currentPage") == "server_service_detail_page.qml"
    assert (home.mediaModel.serviceId, home.mediaModel.startTime) == ("S20261030090000", "2026-10-30 09:00:00")
    assert not box.isVisible()  # 明細頁沒有搜尋框
    assert field.property("text") == "" and not qml(box, "dropdownOpen") and home.serviceSearch.state == IDLE

    qml(window, "mainContentLoader.item.goBack()")  # ‹ Services
    wait_until(lambda: qml(window, "currentPage") == "server_service_page.qml")
    wait_until(lambda: box.isVisible() and box.width() == 360)  # 內頁 360（頁首重新排版之後）
    wait_until(lambda: not home.mediaModel.busy)


def test_admin_header_search_finds_a_staff_member_and_a_click_opens_the_service(
        engine, services, db, people, qml_warnings, no_network_on_gui_thread):
    home, window = open_shell(admin_home_init, ADMIN_ID, engine, services)
    wait_until(lambda: not services.tasks.isBusy("admin:profile"))
    box, field = child(window, "serviceSearchField"), child(window, "searchInput")
    assert field.property("placeholderText") == "Search by service ID or staff name"

    qml(field, "forceActiveFocus()")
    type_text(window, "chen")
    wait_until(lambda: results_open(box))
    assert shown_ids(box) == ["S20261028100000", "S20261031100000", "S20261029100000"]
    results = child(window, "searchResults")
    wait_until(lambda: qml(results, "itemAtIndex(1) !== null"))
    row = qml(results, "itemAtIndex(1)")
    assert qml(row, "Accessible.name") == "S20261031100000, Mia Chen · Oct 31, 2026 · 10:00, Completed"
    wait_until(lambda: row.isVisible() and row.height() > 0)
    point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert qml(window, "currentPage") == "admin_service_detail_page.qml"
    assert (home.mediaModel.serviceId, home.mediaModel.startTime) == ("S20261031100000", "2026-10-31 10:00:00")
    assert not box.isVisible() and field.property("text") == ""
    wait_until(lambda: not home.mediaModel.busy)
