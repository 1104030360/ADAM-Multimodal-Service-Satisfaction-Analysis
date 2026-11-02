"""頁首的搜尋框 ServiceSearchField.qml（UD5、T32 第二階段）：在 offscreen 的 harness 視窗測外觀和行為。

背後是真的 ServiceSearch（search.py），TaskRunner 和資料庫用 test_search.py 的替身：查詢先不執行，
由測試決定什麼時候、用什麼資料交回。debounce 設成 50 ms（QTest 打字時每個字之間會處理事件）。
"""
import json
from datetime import datetime
from types import SimpleNamespace

import mysql.connector
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from myapp.search import ServiceSearch
from tests.integration.helpers import qml, wait_until
from tests.unit.test_qml_components import find, harness  # noqa: F401  harness 是 fixture
from tests.unit.test_search import STAFF, FakeDb, FakeRunner, row

OFFLINE = "Can't reach the database. Check your network and try again."

BODY = """
    property var picked: []
    ServiceSearchField {
        id: box
        objectName: "box"
        x: 300
        y: 20
        width: 400
        search: searchBox
        onChosen: (serviceId, startTime) => window.picked = window.picked.concat([[serviceId, startTime]])
    }
"""


@pytest.fixture
def runner():
    return FakeRunner(FakeDb())


def setup(harness, runner, admin=False):  # noqa: F811
    services = SimpleNamespace(tasks=runner, db=runner.db)
    search = (ServiceSearch.for_admin(services, debounce_ms=50) if admin
              else ServiceSearch.for_staff(services, STAFF, debounce_ms=50))
    window = harness.load(BODY, searchBox=search)
    box = find(window, "box")
    field = find(window, "searchInput")
    qml(field, "forceActiveFocus()")
    return SimpleNamespace(window=window, box=box, field=field, search=search, runner=runner,
                           dropdown=find(window, "searchDropdown"), list=find(window, "searchResults"))


def type_text(window, text):
    """一個字一個字打（QTest.keyClicks 只收 QWidget）。"""
    for char in text:
        QTest.keyClick(window, char)


def type_and_answer(ui, text, rows=(), broken=None):
    """打字，等 debounce 送出，用 rows（或 broken）交回最後那一次查詢。"""
    before = len(ui.runner.submitted)
    type_text(ui.window, text)
    wait_until(lambda: len(ui.runner.submitted) > before)
    ui.runner.answer(rows, broken)


def heard(ui, text):
    """等到 text 被唸出來（結果到了的那一句用 Qt.callLater 唸，下一輪事件才發生）。"""
    wait_until(lambda: text in js(ui.box, "announced"), describe=lambda: js(ui.box, "announced"))


def is_open(ui):
    return qml(ui.box, "dropdownOpen")


def js(obj, expression):
    """QML 的陣列、物件回到 Python（qml() 拿到的是 QJSValue）。"""
    return json.loads(qml(obj, f"JSON.stringify({expression})"))


def item(ui, index):
    """清單的第 index 列（ListView 的 delegate 用 itemAtIndex 找，findChild 找不到，T17）。"""
    wait_until(lambda: qml(ui.list, f"itemAtIndex({index}) !== null"))
    return qml(ui.list, f"itemAtIndex({index})")


def rows_shown(ui):
    count = qml(ui.list, "count")
    return [qml(item(ui, i), "modelData.serviceId") for i in range(count)]


def theme(window, token):
    return QColor(qml(window, f"Theme.{token}"))


TWO = [row("S20261031143000"), row("S20261030090500", start=datetime(2026, 10, 30, 9, 5), status=2)]


# ── 外觀 ─────────────────────────────────────────────────────────────────────

def test_staff_field_is_a_44px_pill_with_its_name(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    assert ui.box.height() == 44 and qml(ui.box, "implicitWidth") == 360   # 內頁 360、首頁 460（wide）
    assert ui.field.property("placeholderText") == "Search by service ID"
    assert qml(ui.field, "Accessible.name") == "Search by service ID"     # 打了字 placeholder 會消失，名稱不靠它
    background = find(ui.window, "searchBackground")
    assert QColor(background.property("color")) == theme(ui.window, "field")
    assert qml(background, "radius") == 22
    assert (qml(background, "border.width"), QColor(qml(background, "border.color"))) == (
        2, theme(ui.window, "focusOnDark"))   # 有焦點：框線變亮、變粗（DS-30）
    qml(find(ui.window, "field"), "forceActiveFocus()")
    assert (qml(background, "border.width"), QColor(qml(background, "border.color"))) == (
        1, theme(ui.window, "fieldBorder"))
    qml(ui.box, "wide = true")
    assert qml(ui.box, "implicitWidth") == 460


def test_admin_field_also_searches_names(harness, runner):  # noqa: F811
    ui = setup(harness, runner, admin=True)
    assert ui.field.property("placeholderText") == "Search by service ID or staff name"
    assert qml(ui.field, "Accessible.name") == "Search by service ID or staff name"


# ── 結果 ─────────────────────────────────────────────────────────────────────

def test_typing_lists_results_with_time_and_status(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_text(ui.window, "2026")
    assert not is_open(ui)  # 查詢中、還不到 busyDelay：不開空的清單
    wait_until(lambda: runner.submitted)
    assert len(runner.submitted) == 1 and runner.db.executed == []  # debounce：4 個字只查一次
    runner.answer(TWO)
    wait_until(lambda: is_open(ui))
    assert rows_shown(ui) == ["S20261031143000", "S20261030090500"]
    first = item(ui, 0)
    assert find(first, "resultServiceId").property("text") == "S20261031143000"
    assert find(first, "resultDetail").property("text") == "Oct 31, 2026 · 14:30"  # 服務人員端不寫名字
    assert qml(first, "spoken") == "S20261031143000, Oct 31, 2026 · 14:30, Completed"
    assert qml(first, "Accessible.name") == qml(first, "spoken")
    assert qml(item(ui, 1), "spoken") == "S20261030090500, Oct 30, 2026 · 09:05, Analyzing"
    assert qml(ui.box, "currentIndex") == 0 and qml(first, "highlighted")
    assert qml(ui.field, "Accessible.description") == "2 services found"
    heard(ui, "2 services found")
    assert js(ui.box, "announced") == ["2 services found"]
    assert ui.field.hasActiveFocus()  # 焦點留在欄位上，可以繼續打字


def test_admin_rows_name_the_staff_member(harness, runner):  # noqa: F811
    ui = setup(harness, runner, admin=True)
    type_and_answer(ui, "mia", [row("S20261031143000", name="Mia Chen")])
    wait_until(lambda: is_open(ui))
    assert find(item(ui, 0), "resultDetail").property("text") == "Mia Chen · Oct 31, 2026 · 14:30"
    assert qml(item(ui, 0), "spoken") == "S20261031143000, Mia Chen · Oct 31, 2026 · 14:30, Completed"


def test_more_than_ten_says_keep_typing(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", [row(f"S202610311430{i:02d}") for i in range(11)])
    wait_until(lambda: is_open(ui))
    assert qml(ui.list, "count") == 10
    more = find(ui.window, "searchMore")
    assert more.isVisible() and more.property("text") == "Showing the first 10. Keep typing to narrow it down."
    assert qml(ui.field, "Accessible.description") == "More than 10 services found"


def test_no_match_and_an_id_that_cannot_exist_are_explained(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "S1999", [])
    wait_until(lambda: is_open(ui))
    message = find(ui.window, "searchMessageText")
    assert message.property("text") == "No services match “S1999”"
    assert not ui.list.isVisible()
    type_text(ui.window, "x")  # 服務人員打了字母：不查，說明 ID 長什麼樣子
    assert message.property("text") == "Service IDs start with S and the date, for example S20261031143000."
    assert len(runner.submitted) == 1 and is_open(ui)
    heard(ui, message.property("text"))


def test_a_slow_search_shows_searching_after_the_busy_delay(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_text(ui.window, "2026")
    wait_until(lambda: runner.submitted)
    wait_until(lambda: is_open(ui), timeout=2)  # 查詢沒回來：busyDelay（300 ms）之後才出現
    assert find(ui.window, "searchMessageText").property("text") == "Searching…"
    runner.answer(TWO)
    wait_until(lambda: ui.list.isVisible())
    assert not find(ui.window, "searchMessage").isVisible()


def test_new_text_dims_the_old_results_until_the_new_ones_arrive(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", TWO)
    heard(ui, "2 services found")
    type_text(ui.window, "1")
    assert qml(ui.list, "opacity") == 0.5 and is_open(ui)  # 舊的結果留著、變淡（DS-33）
    QTest.keyClick(ui.window, Qt.Key.Key_Return)  # 舊的結果不能被打開
    assert js(ui.window, "picked") == []
    wait_until(lambda: len(runner.submitted) == 2)
    runner.answer(TWO[:1])
    wait_until(lambda: qml(ui.list, "opacity") == 1)
    assert rows_shown(ui) == ["S20261031143000"]
    heard(ui, "1 service found")
    assert js(ui.box, "announced") == ["2 services found", "1 service found"]


def test_an_error_stays_in_the_dropdown_with_retry(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", broken=mysql.connector.InterfaceError(errno=2003, msg="Can't connect"))
    wait_until(lambda: is_open(ui))
    assert find(ui.window, "searchError").isVisible()
    assert find(ui.window, "searchErrorTitle").property("text") == "Couldn't search"
    assert find(ui.window, "searchErrorDetail").property("text") == OFFLINE
    heard(ui, f"Couldn't search. {OFFLINE}")
    assert js(ui.box, "announced") == [f"Couldn't search. {OFFLINE}"]
    QTest.keyClick(ui.window, Qt.Key.Key_Return)  # Enter 就是 Retry
    assert len(runner.submitted) == 2 and ui.search.state == "loading"
    runner.answer(broken=mysql.connector.InterfaceError(errno=2003, msg="Can't connect"))
    retry = find(ui.window, "searchRetry")
    wait_until(lambda: retry.isVisible())
    qml(retry, "clicked()")
    assert len(runner.submitted) == 3
    assert ui.field.hasActiveFocus()  # Retry 不拿焦點
    runner.answer(TWO)
    wait_until(lambda: ui.list.isVisible())


# ── 鍵盤、滑鼠 ───────────────────────────────────────────────────────────────

def test_arrows_move_the_highlight_and_enter_opens_it(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", TWO)
    heard(ui, "2 services found")
    QTest.keyClick(ui.window, Qt.Key.Key_Down)
    assert qml(ui.box, "currentIndex") == 1
    assert js(ui.box, "announced")[-1] == "S20261030090500, Oct 30, 2026 · 09:05, Analyzing"
    QTest.keyClick(ui.window, Qt.Key.Key_Down)  # 最後一列：停住
    assert qml(ui.box, "currentIndex") == 1
    QTest.keyClick(ui.window, Qt.Key.Key_Up)
    QTest.keyClick(ui.window, Qt.Key.Key_Up)
    assert qml(ui.box, "currentIndex") == 0
    QTest.keyClick(ui.window, Qt.Key.Key_Down)
    QTest.keyClick(ui.window, Qt.Key.Key_Enter)
    assert js(ui.window, "picked") == [["S20261030090500", "2026-10-30 09:05:00"]]
    wait_until(lambda: not is_open(ui))
    assert ui.field.property("text") == "" and ui.search.state == "idle"   # 清空欄位，回到 idle


def test_escape_closes_the_dropdown_then_clears_the_field(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", TWO)
    wait_until(lambda: is_open(ui))
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    wait_until(lambda: not is_open(ui))
    assert ui.field.property("text") == "2026"
    QTest.keyClick(ui.window, Qt.Key.Key_Down)  # ↓ 再打開
    wait_until(lambda: is_open(ui))
    assert qml(ui.box, "currentIndex") == 0
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)
    wait_until(lambda: not is_open(ui))
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)  # 清單已經關了：清空欄位
    assert ui.field.property("text") == "" and ui.search.state == "idle"
    assert len(runner.submitted) == 1


def test_clicking_a_row_opens_it_and_clicking_outside_closes(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", TWO)
    wait_until(lambda: is_open(ui))
    QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(900, 600).toPoint())
    wait_until(lambda: not is_open(ui))  # 點了外面
    type_text(ui.window, "1")  # 又打字：清單再出現
    wait_until(lambda: len(runner.submitted) == 2)
    runner.answer(TWO)
    wait_until(lambda: is_open(ui))
    second = item(ui, 1)
    wait_until(lambda: second.isVisible() and second.height() > 0)
    point = second.mapToScene(QPointF(second.width() / 2, second.height() / 2)).toPoint()
    QTest.mouseMove(ui.window, point)
    wait_until(lambda: qml(ui.box, "currentIndex") == 1)   # 滑鼠移上去：選到那一列
    QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert js(ui.window, "picked") == [["S20261030090500", "2026-10-30 09:05:00"]]
    wait_until(lambda: not is_open(ui))
    assert ui.field.property("text") == ""


def test_leaving_the_field_closes_the_dropdown(harness, runner):  # noqa: F811
    ui = setup(harness, runner)
    type_and_answer(ui, "2026", TWO)
    wait_until(lambda: is_open(ui))
    qml(find(ui.window, "field"), "forceActiveFocus()")  # Tab 到別的地方
    wait_until(lambda: not is_open(ui))
    qml(ui.field, "forceActiveFocus()")  # 回到欄位：結果再出現
    wait_until(lambda: is_open(ui))


def test_without_a_search_object_the_field_is_inert(harness):  # noqa: F811
    window = harness.load(BODY.replace("search: searchBox", "search: null"))
    field = find(window, "searchInput")
    qml(field, "forceActiveFocus()")
    type_text(window, "2026")
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert not qml(find(window, "box"), "dropdownOpen") and js(window, "picked") == []
