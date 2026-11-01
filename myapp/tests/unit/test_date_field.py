"""日期欄的月曆（new-feature N2，qml/pages/DateField.qml）：在 offscreen 用小的 harness 視窗測行為。

- 對外照舊是 text（YYYY-MM-DD）和 invalid；選了一天和打字一樣發 textEdited()，一段期間發 rangeChosen(start, end)。
- 「今天」用 today 屬性固定（2026-11-01 是星期日），測試不受這台電腦的日期影響。
- 日期格式和結束日早於開始日的檢查在 Python（service_list.date_errors），這裡確認選出來的字串照樣被檢查。
- 每個測試都要零 QML 警告。
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from myapp.service_list import date_errors
from tests.integration.helpers import qml, wait_until

QML_DIR = Path(__file__).resolve().parents[2] / "src" / "myapp" / "qml"
TODAY = "2026-11-01"
ORDER_ERROR = "The To date is before the From date. Choose the same day or later."

HARNESS = """\
import QtQuick
import QtQuick.Controls.Basic
import "{pages}"
import "{components}"

Window {{
    id: window
    width: 1000
    height: 700
    visible: true
    color: Theme.contentBg
    Binding {{ target: Theme; property: "animationScale"; value: 0 }}
    Binding {{ target: Theme; property: "motionMode"; value: 2 }}

    property var edits: []      // 每次 textEdited 時的「欄位:text」
    property var ranges: []     // rangeChosen 的「start..end」

    // 和 Services 頁一樣：From 有一段期間的快捷，選了把兩格都填好
    DateField {{
        id: fromField
        objectName: "fromField"
        x: 20; y: 20
        accessibleName: "From date"
        today: "{today}"
        rangeOptions: true
        onTextEdited: window.edits = window.edits.concat(["from:" + text])
        onRangeChosen: (start, end) => {{
            window.ranges = window.ranges.concat([start + ".." + end])
            fromField.text = start
            toField.text = end
        }}
    }}

    DateField {{
        id: toField
        objectName: "toField"
        x: 520; y: 20
        accessibleName: "To date"
        today: "{today}"
        onTextEdited: window.edits = window.edits.concat(["to:" + text])
    }}

    TextField {{ id: other; objectName: "other"; x: 20; y: 640; width: 200 }}
}}
"""


@pytest.fixture
def ui(tmp_path):
    """harness 視窗：from、to 兩個日期欄和一個別的欄位。結束時檢查零 QML 警告。"""
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))

    def load(today=TODAY):
        path = tmp_path / "Harness.qml"
        path.write_text(HARNESS.format(pages=QUrl.fromLocalFile(str(QML_DIR / "pages")).toString(),
                                       components=QUrl.fromLocalFile(str(QML_DIR / "components")).toString(),
                                       today=today), encoding="utf-8")
        engine.load(QUrl.fromLocalFile(str(path)))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[-1]
        wait_until(window.isExposed)
        window.requestActivate()
        wait_until(window.isActive)  # 按鍵只送到作用中的視窗
        return SimpleNamespace(window=window, start=child(window, "fromField"), end=child(window, "toField"),
                               other=child(window, "other"))

    yield load
    for window in engine.rootObjects():
        window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert warnings == []


def child(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def popup(field):
    return child(field, "calendarPopup")


def is_open(field):
    return field.property("calendarOpen")


def items(root):
    """root 底下畫面上的每一個 item（MonthGrid、DayOfWeekRow 的 delegate 不是 QObject 的子物件，findChild 找不到）。"""
    for item in root.childItems():
        yield item
        yield from items(item)


def cells(field):
    return [item for item in items(qml(popup(field), "contentItem")) if item.objectName().startswith("day-")]


def cell(field, date):
    """月曆上那一天的格子（objectName 是 day-YYYY-MM-DD）。"""
    found = [item for item in cells(field) if item.objectName() == f"day-{date}"]
    assert len(found) == 1, date
    return found[0]


def type_text(window, text):
    keys = {"-": Qt.Key.Key_Minus, **{str(digit): Qt.Key(Qt.Key.Key_0.value + digit) for digit in range(10)}}
    for character in text:
        QTest.keyClick(window, keys[character], Qt.KeyboardModifier.NoModifier)


def click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def drawn(window):
    """等視窗畫出下一個畫格。

    Qt Quick 在畫每一個畫格之前才排版面（polish）：MonthGrid 的格子、Layout 的高度、被推回視窗裡的 Popup、
    下拉清單的列。使用者點得到的一定是畫出來、排好的東西；測試不等就點，第一次打開的月曆格子還全部疊在左上角，
    會點到疊在最上面的最後一格（T35 第 7 節）。
    """
    frames = []

    def swapped():
        frames.append(True)

    window.frameSwapped.connect(swapped)
    try:
        window.update()
        wait_until(lambda: frames)
    finally:
        window.frameSwapped.disconnect(swapped)


def laid_out(window, field):
    """月曆畫好了：在視窗裡，6 週 42 格各在自己的位置上。"""
    content = qml(popup(field), "contentItem")
    corner = content.mapToScene(QPointF(0, 0))
    inside = (corner.x() >= 0 and corner.y() >= 0 and corner.x() + content.width() <= window.width()
              and corner.y() + content.height() <= window.height())
    places = {(round(point.x()), round(point.y())) for point in (day.mapToScene(QPointF(0, 0)) for day in cells(field))}
    return inside and len(places) == 42


def opened(page, field):
    """月曆開著，而且已經畫出來、排好版面：之後才點裡面的東西。"""
    wait_until(lambda: is_open(field))
    drawn(page.window)
    wait_until(lambda: laid_out(page.window, field))


def open_calendar(page, field, into_calendar=False):
    """和點欄位（False）或按 ↓、點月曆圖示（True）一樣打開月曆，等它畫好。"""
    qml(field, f"openCalendar({'true' if into_calendar else 'false'})")
    opened(page, field)


def shown_month(field):
    return (qml(popup(field), "contentItem") and child(popup(field), "monthSelect").property("displayText"),
            child(popup(field), "yearSelect").property("displayText"))


def edits(window):
    value = qml(window, "edits")
    return value.toVariant() if hasattr(value, "toVariant") else list(value)


def ranges(window):
    value = qml(window, "ranges")
    return value.toVariant() if hasattr(value, "toVariant") else list(value)


def press(window, key, times=1):
    for _ in range(times):
        QTest.keyClick(window, key)


# ── 打開、跟著打字 ──────────────────────────────────────────────────────────

def test_clicking_the_field_opens_this_month_and_typing_still_works(ui):
    page = ui()
    click(page.window, page.start)
    opened(page, page.start)
    assert page.start.hasActiveFocus()  # 焦點留在欄位上
    assert shown_month(page.start) == ("November", "2026")  # 欄位是空的：今天那個月
    assert qml(child(popup(page.start), "monthGrid"), "month === Calendar.November && year === 2026")

    type_text(page.window, "2026-03-15")  # 照樣可以打字；打出完整的日期，月曆跟著跳過去
    assert page.start.property("text") == "2026-03-15"
    assert shown_month(page.start) == ("March", "2026")
    assert qml(cell(page.start, "2026-03-15"), "selected")
    assert is_open(page.start) and page.start.hasActiveFocus()
    assert edits(page.window) == ["from:" + "2026-03-15"[:n] for n in range(1, 11)]  # 打字時的 textEdited

    QTest.keyClick(page.window, Qt.Key.Key_Return)  # Enter：頁面送出查詢，月曆收起來
    assert not is_open(page.start)


@pytest.mark.parametrize("text", ["2026/10/01", "2026-02-30", "26-10-01", "abcd-ef-gh"])
def test_a_wrong_date_in_the_field_opens_on_today(ui, text):
    page = ui()
    page.start.setProperty("text", text)
    open_calendar(page, page.start)
    assert shown_month(page.start) == ("November", "2026")
    assert not any(qml(cell(page.start, f"2026-11-{day:02d}"), "selected") for day in range(1, 31))
    assert qml(page.start, f'parse("{text}") === null')


def test_week_starts_on_sunday_and_today_is_marked(ui):
    page = ui()
    open_calendar(page, page.start)
    row = child(popup(page.start), "dayOfWeekRow")
    labels = sorted((item.mapToScene(QPointF(0, 0)).x(), item.property("text")) for item in items(row)
                    if item.property("shortName"))
    assert [label for _x, label in labels] == ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
    days = cells(page.start)
    assert len(days) == 42  # 6 週
    def position(item):
        point = item.mapToScene(QPointF(0, 0))
        return point.y(), point.x()

    first = min(days, key=position)
    # 第一欄是星期日：2026-11-01 是星期日，排在第一欄（1 號剛好是星期日時，Qt 的 MonthGrid 會多顯示前一週）
    assert first.objectName() == "day-2026-10-25"
    assert position(cell(page.start, "2026-11-01"))[1] == position(first)[1]
    assert position(cell(page.start, "2026-11-07"))[1] == max(position(item)[1] for item in days)  # 星期六最後一欄
    marked = [item.objectName() for item in days if child(item, "todayMark").isVisible()]
    assert marked == ["day-2026-11-01"]
    assert qml(cell(page.start, TODAY), "Accessible.name") == "Sunday, November 1, 2026, today"
    assert qml(cell(page.start, "2026-10-31"), "inMonth") is False  # 前一個月的格子：字比較淡
    assert qml(child(page.start, "calendarButton"), "Accessible.name") == "Choose a date"


def test_today_follows_the_computers_local_date_when_not_fixed(ui):
    from datetime import date

    page = ui(today="")
    open_calendar(page, page.start)
    now = date.today()
    assert qml(page.start, "format(todayDate())") == now.isoformat()
    assert child(cell(page.start, now.isoformat()), "todayMark").isVisible()


# ── 選日期、Today、Clear、一段期間 ───────────────────────────────────────────

def test_clicking_a_day_picks_it_like_typing(ui):
    page = ui()
    click(page.window, page.start)
    opened(page, page.start)
    click(page.window, cell(page.start, "2026-11-12"))
    assert page.start.property("text") == "2026-11-12"
    assert not is_open(page.start) and page.start.hasActiveFocus()  # 焦點回到欄位
    assert edits(page.window) == ["from:2026-11-12"]  # 和打字一樣：頁面清掉這一格的錯誤

    click(page.window, page.start)  # 再打開：停在選好的那一天
    opened(page, page.start)
    assert qml(cell(page.start, "2026-11-12"), "selected")
    assert qml(cell(page.start, "2026-11-12"), "Accessible.name") == "Thursday, November 12, 2026, selected"


def test_today_and_clear(ui):
    page = ui()
    open_calendar(page, page.start)
    click(page.window, child(popup(page.start), "todayOption"))
    assert page.start.property("text") == TODAY and not is_open(page.start)
    open_calendar(page, page.start)
    click(page.window, child(popup(page.start), "clearOption"))
    assert page.start.property("text") == "" and not is_open(page.start)  # 空的是不限
    assert edits(page.window) == ["from:" + TODAY, "from:"]


@pytest.mark.parametrize("today, option, expected", [
    ("2026-11-01", "last7DaysOption", "2026-10-26..2026-11-01"),     # 含今天共 7 天，跨月
    ("2026-03-05", "last7DaysOption", "2026-02-27..2026-03-05"),     # 2 月只有 28 天
    ("2026-01-03", "last7DaysOption", "2025-12-28..2026-01-03"),     # 跨年
    ("2026-03-05", "thisMonthOption", "2026-03-01..2026-03-05"),     # 這個月 1 號到今天
    ("2026-11-01", "thisMonthOption", "2026-11-01..2026-11-01"),
], ids=["last-7-days", "last-7-days-february", "last-7-days-new-year", "this-month", "this-month-first-day"])
def test_a_range_fills_both_fields_through_range_chosen(ui, today, option, expected):
    page = ui(today=today)
    open_calendar(page, page.start)
    click(page.window, child(popup(page.start), option))
    assert ranges(page.window) == [expected]
    assert f"{page.start.property('text')}..{page.end.property('text')}" == expected
    assert not is_open(page.start) and page.start.hasActiveFocus()
    assert edits(page.window) == []  # 欄位自己不改：由頁面填兩格


def test_range_options_only_where_the_page_asks_for_them(ui):
    page = ui()
    open_calendar(page, page.end)
    assert not child(popup(page.end), "last7DaysOption").isVisible()
    assert not child(popup(page.end), "thisMonthOption").isVisible()
    assert child(popup(page.end), "todayOption").isVisible() and child(popup(page.end), "clearOption").isVisible()


def test_an_end_date_picked_before_the_start_date_is_still_a_range_error(ui):
    """結束日早於開始日：選出來的字串照樣被 Python 的檢查擋下來（錯誤在 To 下面，Search 不送出）。"""
    page = ui()
    open_calendar(page, page.start)
    click(page.window, cell(page.start, "2026-11-12"))
    open_calendar(page, page.end)
    click(page.window, cell(page.end, "2026-11-03"))
    start, end = page.start.property("text"), page.end.property("text")
    assert (start, end) == ("2026-11-12", "2026-11-03")
    assert date_errors(start, end)[2] == {"start": "", "end": ORDER_ERROR}


# ── 換月、換年 ───────────────────────────────────────────────────────────────

def test_month_steps_and_selects_change_the_month_shown(ui):
    page = ui()
    page.start.setProperty("text", "2026-01-31")
    open_calendar(page, page.start)
    content = popup(page.start)
    click(page.window, child(content, "previousMonthButton"))
    assert shown_month(page.start) == ("December", "2025")  # 跨年
    click(page.window, child(content, "nextMonthButton"))
    click(page.window, child(content, "nextMonthButton"))
    assert shown_month(page.start) == ("February", "2026")
    assert qml(page.start, "format(cursorDate)") == "2026-02-28"  # 31 號在 2 月沒有：停在最後一天

    month = child(content, "monthSelect")
    click(page.window, month)
    wait_until(lambda: qml(month, "popup.opened"))
    drawn(page.window)  # 清單的列也是畫之前才排好
    assert qml(month, "popup.height") <= 320  # 12 個月放不下時清單自己捲動
    options = qml(month, "popup.contentItem")
    wait_until(lambda: any(item.property("text") == "April" for item in items(options)))
    [april] = [item for item in items(options) if item.property("text") == "April" and item.inherits("QQuickItemDelegate")]
    click(page.window, april)  # 點清單裡的一項（另一個 popup）：月曆不會因為「點了外面」就關掉
    wait_until(lambda: not qml(month, "popup.visible"))
    assert shown_month(page.start) == ("April", "2026") and is_open(page.start)
    qml(month, "activated(7); picked(\"7\")")  # 選 August
    assert shown_month(page.start) == ("August", "2026")
    qml(child(content, "yearSelect"), 'picked("2024")')
    assert shown_month(page.start) == ("August", "2024")
    assert is_open(page.start)  # 換月、換年不會關掉月曆，也不會選日期
    assert page.start.property("text") == "2026-01-31"


def test_year_select_offers_ten_years_back_and_the_year_shown(ui):
    page = ui()
    open_calendar(page, page.start)
    years = qml(child(popup(page.start), "yearSelect"), "options.map(option => option.text).join(',')")
    assert years.split(",") == [str(year) for year in range(2016, 2028)]
    page.start.setProperty("text", "2001-05-05")
    years = qml(child(popup(page.start), "yearSelect"), "options.map(option => option.text).join(',')")
    assert years.split(",")[0] == "2001"


# ── 鍵盤 ─────────────────────────────────────────────────────────────────────

def test_keyboard_moves_the_day_picks_with_enter_and_returns_focus(ui):
    page = ui()
    page.start.setProperty("text", "2026-11-12")
    page.start.forceActiveFocus()
    press(page.window, Qt.Key.Key_Down)  # ↓：打開月曆，焦點進到日期上
    grid = child(popup(page.start), "monthGrid")
    wait_until(grid.hasActiveFocus)
    assert child(cell(page.start, "2026-11-12"), "cursorRing").isVisible()

    press(page.window, Qt.Key.Key_Right)
    assert qml(page.start, "format(cursorDate)") == "2026-11-13"
    assert qml(page.start, "announced[announced.length - 1]") == "Friday, November 13, 2026"  # 唸出停在哪一天
    press(page.window, Qt.Key.Key_Up)
    assert qml(page.start, "format(cursorDate)") == "2026-11-06"
    press(page.window, Qt.Key.Key_Left, 6)  # 跨到上一個月：月曆跟著換
    assert qml(page.start, "format(cursorDate)") == "2026-10-31"
    assert shown_month(page.start) == ("October", "2026")
    press(page.window, Qt.Key.Key_Down)
    assert qml(page.start, "format(cursorDate)") == "2026-11-07"
    press(page.window, Qt.Key.Key_PageDown)
    assert qml(page.start, "format(cursorDate)") == "2026-12-07"
    press(page.window, Qt.Key.Key_Home)
    assert qml(page.start, "format(cursorDate)") == "2026-12-01"
    press(page.window, Qt.Key.Key_End)
    assert qml(page.start, "format(cursorDate)") == "2026-12-31"
    press(page.window, Qt.Key.Key_PageUp)
    assert qml(page.start, "format(cursorDate)") == "2026-11-30"
    assert page.start.property("text") == "2026-11-12"  # 移動不會改欄位

    press(page.window, Qt.Key.Key_Return)  # Enter：選定
    assert page.start.property("text") == "2026-11-30"
    assert not is_open(page.start)
    wait_until(page.start.hasActiveFocus)  # 焦點回到欄位
    assert edits(page.window) == ["from:2026-11-30"]


def test_escape_closes_without_changing_and_focus_returns(ui):
    page = ui()
    page.start.setProperty("text", "2026-11-12")
    page.start.forceActiveFocus()
    press(page.window, Qt.Key.Key_Down)
    wait_until(child(popup(page.start), "monthGrid").hasActiveFocus)
    press(page.window, Qt.Key.Key_Right)
    press(page.window, Qt.Key.Key_Escape)
    assert not is_open(page.start)
    wait_until(page.start.hasActiveFocus)
    assert page.start.property("text") == "2026-11-12" and edits(page.window) == []

    click(page.window, page.start)  # 焦點在欄位上時，Esc 也是關掉月曆
    opened(page, page.start)
    press(page.window, Qt.Key.Key_Escape)
    assert not is_open(page.start) and page.start.hasActiveFocus()


def test_space_picks_and_tab_moves_through_the_calendar(ui):
    page = ui()
    page.start.forceActiveFocus()
    press(page.window, Qt.Key.Key_Down)
    content = popup(page.start)
    grid = child(content, "monthGrid")
    wait_until(grid.hasActiveFocus)
    order = []
    for _ in range(9):
        press(page.window, Qt.Key.Key_Tab)
        order.append(page.window.activeFocusItem().objectName())
    assert order == ["todayOption", "last7DaysOption", "thisMonthOption", "clearOption", "previousMonthButton",
                     "monthSelect", "yearSelect", "nextMonthButton", "monthGrid"]
    assert is_open(page.start)  # Tab 在月曆裡移動，不會關掉它
    press(page.window, Qt.Key.Key_Tab)
    assert page.window.activeFocusItem().objectName() == "todayOption"
    press(page.window, Qt.Key.Key_Return)  # Enter 按下快捷
    assert page.start.property("text") == TODAY

    page.start.setProperty("text", "")
    press(page.window, Qt.Key.Key_Down)
    wait_until(grid.hasActiveFocus)
    press(page.window, Qt.Key.Key_Space)  # Space 也是選定：欄位空的時候停在今天
    assert page.start.property("text") == TODAY


def test_the_calendar_closes_when_focus_or_a_click_goes_elsewhere(ui):
    page = ui()
    click(page.window, page.start)
    opened(page, page.start)
    page.other.forceActiveFocus()  # Tab 走了
    wait_until(lambda: not is_open(page.start))

    click(page.window, page.start)
    opened(page, page.start)
    click(page.window, page.end)  # 點了另一個日期欄：這個收起來、那個打開
    wait_until(lambda: not is_open(page.start) and is_open(page.end))
    assert page.end.hasActiveFocus()

    QTest.mouseClick(page.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     page.other.mapToScene(QPointF(5, 5)).toPoint())
    wait_until(lambda: not is_open(page.end))


def test_the_calendar_icon_opens_the_calendar_with_focus_on_the_days(ui):
    page = ui()
    click(page.window, child(page.start, "calendarButton"))
    opened(page, page.start)
    wait_until(child(popup(page.start), "monthGrid").hasActiveFocus)
    assert qml(child(page.start, "calendarButton"), "focusPolicy === Qt.NoFocus")  # 不多一個 Tab 停點
