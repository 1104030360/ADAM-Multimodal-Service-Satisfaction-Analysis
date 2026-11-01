"""兩端的 Services 頁用月曆選日期（new-feature N2，DateField.qml）：不用打字就能查；結束日早於開始日不送出查詢。

- 用滑鼠點：日期欄 → 月曆的上一個月、某一天 → Search，和使用者一樣。
- 「今天」固定在 2026-11-01（DateField 的 today），測試不受這台電腦的日期影響。
- 送出幾個查詢用 db_proxy 算往返（日期有錯時是 0）。
- 每個測試都要零 QML 警告。
"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from myapp.database import verify_login
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, table_row, wait_until
from tests.integration.test_staff_feedback import on_db_lane

pytestmark = pytest.mark.integration

TODAY = "2026-11-01"
ORDER_ERROR = "The To date is before the From date. Choose the same day or later."
STAFF = SimpleNamespace(module=server_home_init, account=STAFF_ID, page="server_service_page.qml")
ADMIN = SimpleNamespace(module=admin_home_init, account=ADMIN_ID, page="admin_service_page.qml")
BOTH_SIDES = pytest.mark.parametrize("side", [STAFF, ADMIN], ids=["staff", "admin"])


@pytest.fixture
def qml_warnings(engine):
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


def idle(services):
    return not services.tasks._running


def open_services(side, engine, services):
    """登入後打開 Services（第一次打開會查一次全部，等它查完）；兩個日期欄的「今天」固定。"""
    side.module.launch(engine, side.account, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: idle(services))
    qml(window, "servicesButton.click()")
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    page = qml(window, "mainContentLoader.item")
    wait_until(lambda: idle(services))
    qml(page, f'startDateField.today = "{TODAY}"; endDateField.today = "{TODAY}"')
    return SimpleNamespace(window=window, page=page, start=child(page, "startDateField"),
                           end=child(page, "endDateField"), manager=engine.rootContext().contextProperty("serviceManager"))


def child(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def items(root):
    for item in root.childItems():
        yield item
        yield from items(item)


def click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def calendar(field):
    return child(field, "calendarPopup")


def drawn(window):
    """等視窗畫出下一個畫格。

    Qt Quick 在畫每一個畫格之前才排版面（polish）：MonthGrid 的格子、Layout 的高度、被推回視窗裡的 Popup。
    使用者點得到的一定是畫出來、排好的東西；測試不等就點，第一次打開的月曆格子還全部疊在左上角，
    會點到疊在最上面的最後一格（10 月的月曆是 2026-11-07，T35 第 7 節）。
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


def laid_out(ui, field):
    """月曆畫好了：在視窗裡（放不下時 Popup 會被推回來），6 週 42 格各在自己的位置上。"""
    content = qml(calendar(field), "contentItem")
    corner = content.mapToScene(QPointF(0, 0))
    inside = (corner.x() >= 0 and corner.y() >= 0 and corner.x() + content.width() <= ui.window.width()
              and corner.y() + content.height() <= ui.window.height())
    days = [item for item in items(content) if item.objectName().startswith("day-")]
    places = {(round(point.x()), round(point.y())) for point in (day.mapToScene(QPointF(0, 0)) for day in days)}
    return inside and len(days) == 42 and len(places) == 42


def open_calendar(ui, field):
    """點日期欄打開月曆，等它畫出來、排好版面（drawn），才點裡面的東西。"""
    click(ui.window, field)
    wait_until(lambda: field.property("calendarOpen"))
    drawn(ui.window)
    wait_until(lambda: laid_out(ui, field))


def pick(ui, field, date, months_back=0):
    """打開月曆，往前翻 months_back 個月，點那一天。"""
    open_calendar(ui, field)
    for _ in range(months_back):
        click(ui.window, child(calendar(field), "previousMonthButton"))
    grid = child(calendar(field), "monthGrid")
    wait_until(lambda: (qml(grid, "year"), qml(grid, "month")) == (int(date[:4]), int(date[5:7]) - 1))
    drawn(ui.window)  # 換月之後格子就地換成新的日期，位置不變；照樣等畫出來再點，和使用者一樣
    wait_until(lambda: laid_out(ui, field))

    def the_day():  # 這個月的那一格（前後月份補上的格子也叫 day-…，但 inMonth 是 false）
        return [item for item in items(qml(calendar(field), "contentItem"))
                if item.objectName() == f"day-{date}" and item.property("inMonth")]

    [day] = the_day()
    click(ui.window, day)
    wait_until(lambda: not field.property("calendarOpen"))
    assert field.property("text") == date  # 點到的就是指著的那一天


def listed(manager):
    model = manager.listModel
    return [table_row(model, row)["serviceId"] for row in range(model.rowCount())]


def errors(ui):
    return child(ui.page, "startDateError").property("text"), child(ui.page, "endDateError").property("text")


def seed(db):
    db.add_service("S20261020090000", "2026-10-20 09:00:00")
    db.add_service("S20261028090000", "2026-10-28 09:00:00")
    db.add_service("S20261030090000", "2026-10-30 09:00:00")


@BOTH_SIDES
def test_a_search_without_typing(side, engine, services, db, qml_warnings):
    seed(db)
    ui = open_services(side, engine, services)
    assert sorted(listed(ui.manager)) == ["S20261020090000", "S20261028090000", "S20261030090000"]
    pick(ui, ui.start, "2026-10-27", months_back=1)  # 月曆打開在 11 月（今天），翻到 10 月
    pick(ui, ui.end, "2026-10-29", months_back=1)
    assert (ui.start.property("text"), ui.end.property("text")) == ("2026-10-27", "2026-10-29")
    assert idle(services)  # 選日期不會自己查
    click(ui.window, child(ui.page, "searchButton"))
    wait_until(lambda: idle(services))
    assert listed(ui.manager) == ["S20261028090000"]
    assert errors(ui) == ("", "")


@BOTH_SIDES
def test_last_7_days_fills_both_dates_and_search_uses_them(side, engine, services, db, qml_warnings):
    seed(db)
    ui = open_services(side, engine, services)
    open_calendar(ui, ui.end)
    click(ui.window, child(calendar(ui.end), "last7DaysOption"))
    assert (ui.start.property("text"), ui.end.property("text")) == ("2026-10-26", "2026-11-01")
    click(ui.window, child(ui.page, "searchButton"))
    wait_until(lambda: idle(services))
    assert sorted(listed(ui.manager)) == ["S20261028090000", "S20261030090000"]


@BOTH_SIDES
def test_end_before_start_shows_the_error_and_sends_no_query(side, engine, services, db, db_proxy, qml_warnings):
    seed(db)
    ui = open_services(side, engine, services)
    before = listed(ui.manager)
    pick(ui, ui.start, "2026-10-30", months_back=1)
    pick(ui, ui.end, "2026-10-20", months_back=1)
    db_proxy.reset()
    click(ui.window, child(ui.page, "searchButton"))
    assert errors(ui) == ("", ORDER_ERROR)  # To 下面（FieldError），框線變紅
    assert child(ui.page, "endDateError").isVisible() and qml(ui.end, "invalid")
    wait_until(lambda: idle(services))
    assert db_proxy.snapshot()["rt"] == 0  # 沒有送出查詢
    assert listed(ui.manager) == before
    wait_until(ui.end.hasActiveFocus)  # 焦點移到有錯的欄位（DS-27）

    pick(ui, ui.end, "2026-10-31")  # 月曆打開在欄位的日期（10 月）；選了新的日期，錯誤先拿掉（和打字一樣）
    assert errors(ui) == ("", "")
    click(ui.window, child(ui.page, "searchButton"))
    wait_until(lambda: idle(services))
    assert listed(ui.manager) == ["S20261030090000"]


@BOTH_SIDES
def test_the_open_calendar_has_no_warnings(side, engine, services, db, qml_warnings):
    ui = open_services(side, engine, services)
    for field in (ui.start, ui.end):
        open_calendar(ui, field)
        assert child(calendar(field), "last7DaysOption").isVisible()  # 兩格都有一段期間的快捷
        month = child(calendar(field), "monthSelect")
        qml(month, "popup.open()")
        wait_until(lambda: qml(month, "popup.opened"))
        drawn(ui.window)
        qml(month, "popup.close()")
        QTest.keyClick(ui.window, Qt.Key.Key_Escape)
        wait_until(lambda: not field.property("calendarOpen"))
