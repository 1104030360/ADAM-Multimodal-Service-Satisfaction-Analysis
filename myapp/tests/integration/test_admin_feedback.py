"""管理員端看得到的回饋（前端重建 F1 接線＋F2，CONTRACTS §3；新版介面 U1）：新增帳號、外殼的通知、首頁三張卡片的
空狀態、錯誤、重新整理和切換。

- 每個測試都開著 no_network_on_gui_thread（GUI 執行緒碰網路就失敗），並且要求 QML 零警告。
- 資料庫經過 db_proxy：數往返次數，也可以讓它斷線（kill）。只斷言結果和往返次數，不斷言時間。
- 等待一律用 wait_until。toast 要等畫面畫出來才看得到；用到它的子控制項時留著 toast 的參照（T17）。
"""
import gc
import json
import threading
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from myapp import home_dashboard
from myapp.init import admin_home_init
from myapp.passwords import verify_password
from tests.integration.helpers import ADMIN_ID, find_button, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

FORM_FIELDS = ("usernameField", "passwordField", "nameField", "jobTitleField")


# ---------------------------------------------------------------- fixture 與小工具


def settled(home):
    """首頁和帳號資料都載入完了（成功、沒有資料或失敗都算），也不在重新整理。"""
    sections = (home.profile, *home.homeDashboard.sections)
    return all(section.state != "loading" and not section.refreshing for section in sections)


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


def launch(engine, services, account_id=ADMIN_ID):
    home = admin_home_init.launch(engine, account_id, services)
    window = engine.rootObjects()[-1]
    wait_until(window.isExposed)
    wait_until(lambda: settled(home))
    return SimpleNamespace(home=home, window=window, services=services)


@pytest.fixture
def admin(db, db_proxy, engine, services, qml_warnings, no_network_on_gui_thread):
    """等級 5 的管理員登入後的主畫面。資料庫經過 db_proxy；GUI 執行緒不准碰網路；QML 零警告。"""
    ui = launch(engine, services)
    ui.proxy = db_proxy
    return ui


def open_page(ui, name):
    qml(ui.window, f'mainContentLoader.source = "../../qml/pages/{name}"')  # 和側邊欄按鈕的寫法一樣
    wait_until(lambda: qml(ui.window, "mainContentLoader.status === Loader.Ready"))
    return qml(ui.window, "mainContentLoader.item")


def child(item, name):
    found = item.findChild(QObject, name)
    assert found is not None, name
    return found


def scroll_into_view(item):
    """item 在可以捲動的區域（Flickable）裡、沒有整個露出來時，先捲到它在中間（新版介面的內容區比較矮）。"""
    flickable = item.parentItem()
    while flickable is not None and not flickable.inherits("QQuickFlickable"):
        flickable = flickable.parentItem()
    if flickable is None:
        return
    top = item.mapToItem(flickable, QPointF(0, 0)).y()  # 相對於看得到的範圍
    if 0 <= top and top + item.height() <= flickable.height():
        return
    lowest = flickable.property("originY")
    highest = lowest + max(0.0, flickable.property("contentHeight") - flickable.height())
    wanted = flickable.property("contentY") + top - (flickable.height() - item.height()) / 2
    flickable.setProperty("contentY", min(highest, max(lowest, wanted)))


def click(window, item):
    scroll_into_view(item)
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)


def fill(page, **fields):
    for field, value in fields.items():
        qml(page, f"{field}.text = {json.dumps(value)}")


def type_text(window, text):
    """像使用者一樣打字，送到有焦點的輸入框（QTest.keyClicks 只收 QWidget；中文字也要能打）。"""
    for char in text:
        QTest.sendKeyEvent(QTest.KeyAction.Click, window, Qt.Key.Key_unknown, char, Qt.KeyboardModifier.NoModifier)


def render_frames(window, count=2):
    """請視窗再畫 count 個畫格，等它們畫完：排隊中的 Canvas 重畫、轉場都會在這期間跑完。"""
    frames = []

    def swapped():
        frames.append(True)

    window.frameSwapped.connect(swapped)
    try:
        for _ in range(count):
            drawn = len(frames)
            window.update()
            wait_until(lambda: len(frames) > drawn)
    finally:
        window.frameSwapped.disconnect(swapped)


def field_error(page, field):
    """欄位下方 FieldError 顯示的文字。"""
    return child(child(page, field), "fieldErrorText").property("text")


def shown_toasts(window):
    """外殼的 ToastHost 已經建立的 toast（第 0 列最新）。ListView 的項目要用 itemAtIndex 拿（T17）。"""
    view = child(window, "toastList")
    return [qml(view, f"itemAtIndex({i})") for i in range(qml(view, "count"))]


def toast_titled(window, title):
    def matching():
        return [toast for toast in shown_toasts(window) if toast is not None and toast.property("title") == title]
    wait_until(lambda: len(matching()) == 1)
    return matching()[0]


def find_item(root, name):
    """在畫面樹（childItems）裡找 objectName 是 name 的項目：Repeater、ListView 建立的項目沒有 QObject parent。"""
    pending = [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"找不到 {name}")


def js(obj, expression):
    """qml() 的結果；JS 陣列、物件轉成 Python 的 list、dict。"""
    result = qml(obj, expression)
    return result.toVariant() if hasattr(result, "toVariant") else result


def tile_value(page, name):
    tile = find_item(page, name)  # 留著方塊的參照，子物件的 wrapper 才不會失效（T17）
    value = child(tile, "tileValue")
    return value.property("text")


def run_on_db_lane(services, work):
    done = []
    services.tasks.submit("db", "test:setup", lambda token, report: work(), on_ok=done.append)
    wait_until(lambda: done)


def warm_up(services):
    """先連上，兩種 cursor 各建一次（各 1 個 PING），之後才開始數往返（T16）。"""
    def work():
        for dictionary in (False, True):
            with services.db.cursor(dictionary=dictionary) as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()
        return True
    run_on_db_lane(services, work)


@contextmanager
def db_lane_blocked(services):
    """在 db lane 放一個等 gate 的工作：with 裡送進去的查詢都排在它後面。離開 with（包括斷言失敗）就放行。"""
    gate = threading.Event()
    services.tasks.submit("db", "test:gate", lambda token, report: gate.wait(10))
    try:
        yield gate
    finally:
        gate.set()


def reconnect_directly(monkeypatch, mysql_server):
    """網路恢復：DbLane 下一次連線不經過已經斷掉的 proxy，直接連測試資料庫。"""
    monkeypatch.setenv("DB_HOST", mysql_server["host"])
    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))


# ---------------------------------------------------------------- 主畫面先出來，資料在背景（ENG-06、ENG-21）


def test_launch_shows_the_shell_first_and_loads_in_the_background(db, db_proxy, engine, services, qml_warnings,
                                                                   no_network_on_gui_thread):
    warm_up(services)
    with db_lane_blocked(services):
        db_proxy.reset()
        home = admin_home_init.launch(engine, ADMIN_ID, services)
        window = engine.rootObjects()[-1]
        page = qml(window, "mainContentLoader.item")
        assert page is not None  # 外殼和首頁已經建好了
        assert qml(window, "header.userName") == ""  # 名字還在載入
        for name in ("overviewSkeleton", "rankingSkeleton", "trendSkeleton"):  # 還沒載入：灰色骨架，不是 0
            assert child(page, name).isVisible(), name
        assert db_proxy.snapshot()["rt"] == 0  # db lane 被擋著，這段期間的往返只可能來自 GUI 執行緒
    wait_until(lambda: settled(home))
    assert db_proxy.snapshot()["rt"] == 4  # 名字和等級 1，首頁的三張卡片各 1（預算 ≤ 5，T28）
    assert qml(window, "header.userName") == "系統管理員（佔位）"


# ---------------------------------------------------------------- 新增帳號（UX-33、UX-34、DS-52）


def open_add_page(ui):
    """打開新增帳號頁，等等級選單有選項，再畫一個畫格：Layout 在畫格之前才排好位置，沒畫過就點，會點到舊的位置。"""
    page = open_page(ui, "admin_add_page.qml")
    wait_until(lambda: qml(page, "levelField.count") > 0)
    render_frames(ui.window)
    return page



def test_add_account_success_shows_a_toast_clears_the_form_and_focuses_username(admin, db):
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    fill(page, usernameField="new_staff", passwordField="S3cret-pass", nameField="新同事", jobTitleField="櫃檯人員")
    warm_up(admin.services)  # INSERT 用一般的 cursor；首頁的查詢都用 dictionary cursor，建 cursor 不算（T16）
    admin.proxy.reset()
    click(admin.window, child(page, "addAccountButton"))

    toast = toast_titled(admin.window, "Account added")
    assert (toast.property("severity"), toast.property("detail")) == (
        "success", "新同事 can log in as new_staff (level 1 · Staff).")
    assert admin.proxy.snapshot()["rt"] == 1  # 一個 INSERT
    assert [qml(page, f"{field}.text") for field in FORM_FIELDS] == ["", "", "", ""]
    wait_until(lambda: qml(page, "usernameField.input.activeFocus"))  # 接著新增下一個
    stored, level, job_title = db.rows("SELECT account_password, account_level, account_occupation_name "
                                       "FROM tbl_account WHERE account_username = 'new_staff'")[0]
    assert verify_password("S3cret-pass", stored) and (level, job_title) == ("1", "櫃檯人員")


def test_add_account_with_the_keyboard_only(admin, db):
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    wait_until(lambda: qml(page, "usernameField.input.activeFocus"))  # 一打開就可以打字
    type_text(admin.window, "kb_staff")
    QTest.keyClick(admin.window, Qt.Key.Key_Tab)
    type_text(admin.window, "S3cret-pass")
    QTest.keyClick(admin.window, Qt.Key.Key_Tab)  # 密碼後面是 Show：Space 顯示出來核對
    assert qml(page, "showButton.activeFocus") and qml(page, "showButton.visualFocus")
    QTest.keyClick(admin.window, Qt.Key.Key_Space)
    assert qml(page, "passwordField.echoMode === TextInput.Normal")
    QTest.keyClick(admin.window, Qt.Key.Key_Tab)
    type_text(admin.window, "鍵盤")
    QTest.keyClick(admin.window, Qt.Key.Key_Tab)
    assert qml(page, "levelField.activeFocus")
    QTest.keyClick(admin.window, Qt.Key.Key_Tab)  # 等級用預設的 1 · Staff
    type_text(admin.window, "櫃檯")
    QTest.keyClick(admin.window, Qt.Key.Key_Return)  # Enter 送出
    toast_titled(admin.window, "Account added")
    assert db.rows("SELECT account_name, account_occupation_name FROM tbl_account "
                   "WHERE account_username = 'kb_staff'") == [("鍵盤", "櫃檯")]


def test_duplicate_username_is_a_field_error_and_moves_focus_there(admin, db):
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    fill(page, usernameField="placeholder_staff", passwordField="pw", nameField="重複")
    click(admin.window, child(page, "addAccountButton"))

    wait_until(lambda: field_error(page, "usernameField") != "")
    assert field_error(page, "usernameField") == "Username placeholder_staff is already taken. Choose another one."
    wait_until(lambda: qml(page, "usernameField.input.activeFocus"))
    assert [qml(page, f"{field}.text") for field in FORM_FIELDS] == ["placeholder_staff", "pw", "重複", ""]
    assert toasts(admin.services.notifier) == []  # 欄位錯誤就夠了，不另外跳通知
    assert db.rows("SELECT COUNT(*) FROM tbl_account")[0][0] == 2


def test_required_fields_are_checked_on_submit_without_touching_the_database(admin):
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    admin.proxy.reset()
    click(admin.window, child(page, "addAccountButton"))

    assert [field_error(page, field) for field in ("usernameField", "passwordField", "nameField")] == [
        "Enter a username.", "Enter a password.", "Enter a name."]
    assert field_error(page, "jobTitleField") == ""  # 職稱可以空白
    wait_until(lambda: qml(page, "usernameField.input.activeFocus"))  # 焦點移到第一個有錯的欄位
    assert admin.proxy.snapshot()["rt"] == 0
    type_text(admin.window, "a")  # 改了這個欄位：它的錯誤先拿掉，其他的留著
    assert field_error(page, "usernameField") == ""
    assert field_error(page, "passwordField") == "Enter a password."


def test_offline_add_keeps_the_entries_and_retry_on_the_toast_saves(admin, db, mysql_server, monkeypatch):
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    fill(page, usernameField="new_staff", passwordField="S3cret-pass", nameField="新同事", jobTitleField="櫃檯人員")
    admin.proxy.kill()  # 連線斷掉，之後也連不上
    click(admin.window, child(page, "addAccountButton"))

    toast = toast_titled(admin.window, "Account wasn't saved")
    assert toast.property("detail") == "Can't reach the database. Your entries are still here, so you can retry."
    assert (toast.property("severity"), toast.property("actionLabel"), toast.property("timeoutMs")) == (
        "error", "Retry", 0)  # 常駐，直到重試或關掉
    assert [qml(page, f"{field}.text") for field in FORM_FIELDS] == ["new_staff", "S3cret-pass", "新同事", "櫃檯人員"]

    reconnect_directly(monkeypatch, mysql_server)
    click(admin.window, child(toast, "toastAction"))  # 通知上的 Retry
    toast_titled(admin.window, "Account added")
    assert db.rows("SELECT COUNT(*) FROM tbl_account WHERE account_username = 'new_staff'") == [(1,)]
    assert [qml(page, f"{field}.text") for field in FORM_FIELDS] == ["", "", "", ""]


@pytest.mark.parametrize("level", [2, 3, 4, 5])
def test_level_picker_lists_only_levels_the_admin_can_assign(db, engine, services, qml_warnings,
                                                             no_network_on_gui_thread, level):
    account_id = f"00000000-0000-0000-0000-00000000010{level}"
    db.add_account(account_id, f"admin{level}", f"管理員{level}", level=str(level))
    ui = launch(engine, services, account_id)
    page = open_add_page(ui)
    wait_until(lambda: qml(page, "levelField.count") == level)
    assert [qml(page, f"levelField.textAt({i})") for i in range(level)] == (
        ["1 · Staff"] + [f"{n} · Admin" for n in range(2, level + 1)])
    assert child(page, "levelHelp").property("text") == f"You can add levels 1–{level} (your level is {level})."


def test_level_above_own_level_is_rejected_on_the_level_field(db, engine, services, qml_warnings,
                                                              no_network_on_gui_thread):
    db.add_account("00000000-0000-0000-0000-000000000103", "admin3", "管理員3", level="3")
    ui = launch(engine, services, "00000000-0000-0000-0000-000000000103")
    page = open_add_page(ui)
    wait_until(lambda: qml(page, "levelField.count") == 3)
    qml(page, 'accountManager.addAccount("boss", "pw", "老闆", "5", "")')  # 繞過選單：Python 端照樣擋
    level_error = child(page, "levelError")
    assert level_error.property("text") == "Level 5 is above your level (3). Choose level 3 or lower."
    assert level_error.isVisible() and not child(page, "levelHelp").isVisible()
    wait_until(lambda: qml(page, "levelField.activeFocus"))
    assert db.rows("SELECT COUNT(*) FROM tbl_account WHERE account_username = 'boss'") == [(0,)]


def test_update_account_is_hidden(admin):
    page = open_add_page(admin)
    assert find_button(page, "Add Account") is not None
    assert find_button(page, "Update Account") is None  # PLAN D8：沒有帳號清單可以選，先拿掉


def texts(item):
    """item 底下看得到的 Text 的字（Repeater、Row 建立的也算），依畫面上的順序。"""
    found, pending = [], [item]
    while pending:
        current = pending.pop(0)
        if current.inherits("QQuickText") and current.isVisible() and current.property("text"):
            found.append(current.property("text"))
        pending.extend(current.childItems())
    return found


def test_add_account_page_layout_and_copy(admin):
    """新版介面 PLAN 6-8：標題在外殼的頁首；表單卡「New account」，旁邊「Account levels」說明 1 和 2～5（D9）。"""
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    assert (qml(admin.window, "header.title"), qml(admin.window, "header.subtitle")) == (
        "Add Account", "Add a staff or admin account.")
    assert texts(child(page, "formCard"))[0] == "New account"  # 頁面不再自己畫一次頁面標題
    assert child(page, "passwordHelp").property("text") == "Hand it over in person. It can't be seen in the app later."
    assert texts(child(page, "levelsCard")) == [
        "Account levels",
        "1 · Staff", "Records services and sees only their own results.",
        "2–5 · Admin", "Sees every staff member's services and scores. Can add accounts up to their own level."]
    assert "(optional)" in texts(child(page, "jobTitleField"))  # 職稱可以空白，標籤上寫清楚
    assert qml(page, "jobTitleField.input.Accessible.name") == "Job Title"


def test_show_password_reveals_it_and_the_next_account_starts_hidden(admin, db):
    page = open_add_page(admin)
    wait_until(lambda: qml(page, "levelField.count") == 5)
    show = child(page, "showPasswordButton")
    assert qml(page, "passwordField.echoMode === TextInput.Password")
    assert (show.property("text"), qml(show, "Accessible.name")) == ("Show", "Show password")
    width = show.width()
    click(admin.window, show)
    assert qml(page, "passwordField.echoMode === TextInput.Normal")
    assert (show.property("text"), qml(show, "Accessible.name")) == ("Hide", "Hide password")
    assert show.width() == width  # 換字時按鈕不變寬，密碼欄不跟著縮放

    fill(page, usernameField="shown_pw", passwordField="S3cret-pass", nameField="顯示")
    click(admin.window, child(page, "addAccountButton"))
    toast_titled(admin.window, "Account added")
    assert qml(page, "passwordField.echoMode === TextInput.Password")  # 下一個帳號的密碼預設不顯示
    assert show.property("text") == "Show"


# ---------------------------------------------------------------- 外殼的通知（DS-21）


def test_shell_shows_toasts_of_all_four_severities_without_warnings(admin):
    icons = {}
    for severity in ("success", "info", "warning", "error"):
        admin.services.notifier.notify(severity, f"A {severity} toast", "Details here.")
        toast = toast_titled(admin.window, f"A {severity} toast")
        icons[toast.property("severity")] = child(toast, "toastIcon").property("source").fileName()
    assert icons == {"success": "circle-check_successOnDark.svg", "info": "info_infoOnDark.svg",
                     "warning": "triangle-alert_warningOnDark.svg", "error": "circle-alert_errorOnDark.svg"}

    # 最多 3 則：第 4 則進來時，最舊的非常駐（success）退場；新的在最下面，貼著右下角、距邊 24
    wait_until(lambda: [toast.property("title") for toast in shown_toasts(admin.window) if toast is not None]
               == ["A error toast", "A warning toast", "A info toast"])
    newest = shown_toasts(admin.window)[0]

    def bottom_right():
        corner = newest.mapToScene(QPointF(newest.width(), newest.height()))
        return corner.x(), corner.y()

    wait_until(lambda: bottom_right() == (admin.window.width() - 24, admin.window.height() - 24))


def test_toasts_are_visible_when_motion_is_off(admin):
    """減少動態、測試的 animationScale 0：動畫時間是 0，ToastHost 不套用轉場，toast 直接看得到（T19 抓到的 bug）。"""
    admin.services.notifier.notify("info", "Can you see me", "")
    toast = toast_titled(admin.window, "Can you see me")
    render_frames(admin.window, 3)
    assert toast.property("opacity") == 1


# ---------------------------------------------------------------- 首頁（U1，T28）：空狀態、錯誤、重新整理、切換（PLAN 5-2、5-4）


def seed_staff(db, today, scores):
    """scores：[(名字, 顧客分數, 員工分數)]，每人今天一筆分析完成的服務。回傳帳號。"""
    accounts = []
    for number, (name, customer, staff) in enumerate(scores, start=3):
        account = f"00000000-0000-0000-0000-{number:012d}"
        db.add_account(account, f"staff{number}", name)
        service_id = f"S{today:%Y%m%d}{number:02d}0000"
        db.add_service(service_id, f"{today} {number:02d}:00:00", account_id=account)
        db.add_scores(service_id, customer=customer, server=staff)
        accounts.append(account)
    return accounts


def refreshed_page(admin):
    """首頁重新查詢（和側邊欄的 Home 一樣是頁面建立時查；這裡直接叫），等三張卡片都回來。"""
    page = qml(admin.window, "mainContentLoader.item")
    admin.home.homeDashboard.refresh()
    wait_until(lambda: settled(admin.home))
    return page


def test_home_without_data_shows_empty_states_not_zeros(admin):
    page = qml(admin.window, "mainContentLoader.item")
    empty = child(page, "overviewEmpty")
    assert empty.isVisible() and child(empty, "emptyTitle").property("text") == "No services in the last 30 days"
    assert child(empty, "emptyDescription").property("text") == (
        "Scores show up here after staff record services and they're analyzed.")
    assert not child(page, "newRecordingButton").isVisible()  # 管理員端不錄影：沒有 New Recording
    assert child(page, "overviewSubtitle").property("text") == "Last 30 days · 0 services"
    assert not child(page, "sideToggle").isVisible() and not child(page, "periodBox").isVisible()
    ranking = child(page, "rankingEmpty")
    assert ranking.isVisible()
    assert child(ranking, "emptyTitle").property("text") == "No analyzed services in the last 30 days"
    assert not child(page, "rankingBars").isVisible()
    trend = child(page, "trendEmpty")
    assert trend.isVisible() and child(trend, "emptyTitle").property("text") == "No scores in the last 14 days"


def test_home_ranks_every_staff_member_and_switches_without_querying(admin, db):
    """排名列出每一位員工（以前只留前 5、後 5），由高到低；Customer／Staff、期間、分數換了都不再查詢（T28）。"""
    today = date.today()
    names = ["Ava", "Ben", "Cai", "Dee", "Eli", "Fay", "Gus"]
    seed_staff(db, today, [(name, 60 + 5 * n, 95 - 5 * n) for n, name in enumerate(names)])
    page = refreshed_page(admin)
    assert child(page, "overviewSubtitle").property("text") == "Last 30 days · 7 services · 7 staff"
    assert tile_value(page, "scoreTile_overall") == "75.0"  # (60 + 65 + … + 90) / 7
    assert child(page, "scoresLabel").property("text") == "Customer scores, all staff"
    bars = child(page, "rankingBars")
    assert js(bars, "bars.map(bar => bar.label)") == names[::-1]  # 顧客分數由高到低
    assert js(bars, "bars.map(bar => bar.sublabel)") == ["1 service"] * 7
    assert child(page, "rankingSubtitle").property("text") == "Customer overall score by staff · last 30 days"
    assert page.findChild(QObject, "openRankingButton") is None  # 卡片裡已經列出每一位：沒有 ↗（T28）

    # 管理員端沒有分析頁：服務方塊只是數字，不能點、不接受焦點
    completed = child(page, "completedTile")
    assert not qml(completed, "interactive") and qml(completed, "Accessible.role === Accessible.StaticText")
    assert qml(completed, "focusPolicy === Qt.NoFocus")
    assert child(child(page, "notAnalyzedTile"), "tileNote").property("text") == "Nothing waiting"

    admin.proxy.reset()
    staff = child(page, "sideStaff")  # 用鍵盤：Tab 到 Staff，按空白鍵
    staff.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    assert qml(staff, "visualFocus")
    QTest.keyClick(admin.window, Qt.Key.Key_Space)
    wait_until(lambda: qml(page, "overview.side") == "staff")
    assert qml(staff, "Accessible.checked") and not qml(child(page, "sideCustomer"), "Accessible.checked")
    assert js(bars, "bars.map(bar => bar.label)") == names  # 員工分數由高到低
    assert child(page, "rankingSubtitle").property("text") == "Staff overall score by staff · last 30 days"
    assert child(page, "scoresLabel").property("text") == "Staff scores, all staff"
    qml(page, 'overview.period = "today"')
    assert child(page, "overviewSubtitle").property("text") == "Today · 7 services · 7 staff"
    qml(page, 'trend.metric = "facial"')
    assert child(page, "trendSubtitle").property("text") == "Facial · all staff · last 14 days"
    assert admin.proxy.snapshot()["rt"] == 0


def test_home_controls_have_names_and_the_trend_chart_describes_itself(admin, db):
    today = date.today()
    seed_staff(db, today, [("Ava", 92, 64)])
    page = refreshed_page(admin)
    assert (qml(page, "overview.Accessible.name"), qml(child(page, "sideToggle"), "Accessible.name")) == (
        "Overview", "Show scores for")
    assert (child(page, "periodBox").property("displayText"), qml(child(page, "periodBox"), "Accessible.name")) == (
        "Last 30 days", "Period")
    assert qml(child(page, "metricBox"), "Accessible.name") == "Score"
    chart = child(page, "trendChart")
    assert qml(chart, "Accessible.role === Accessible.Chart")
    label = home_dashboard.short_day(today)
    first = home_dashboard.short_day(home_dashboard.trend_days(today)[0])
    assert qml(chart, "Accessible.name") == f"Overall score, daily average from {first} to {label}"
    assert qml(chart, "Accessible.description").startswith(f"Customer · 92.0 on {label}. Staff · 64.0 on {label}")
    assert child(page, "customerLegend").property("text") == f"Customer · 92.0 on {label}"
    overall = find_item(page, "scoreTile_overall")
    assert qml(overall, "Accessible.name") == "Overall 92.0, Good"


def test_home_refresh_keeps_old_numbers_and_says_updating(admin, db):
    today = date.today()
    seed_staff(db, today, [("Ava", 80, 70)])
    page = refreshed_page(admin)
    subtitle = child(page, "overviewSubtitle")
    assert tile_value(page, "scoreTile_overall") == "80.0"
    with db_lane_blocked(admin.services):
        db.execute("UPDATE tbl_service_score SET customer_total = 90")
        admin.home.homeDashboard.refresh()  # 排在被擋住的工作後面
        wait_until(lambda: subtitle.property("text").endswith(" · Updating…"))  # 過了 300 ms 才寫
        assert tile_value(page, "scoreTile_overall") == "80.0"  # 舊資料留著，不換成骨架（PLAN 5-4）
        assert not child(page, "overviewSkeleton").isVisible()
    wait_until(lambda: tile_value(page, "scoreTile_overall") == "90.0")
    wait_until(lambda: not subtitle.property("text").endswith("Updating…"))


def test_offline_at_launch_shows_block_errors_and_retry_recovers(db, engine, services, qml_warnings,
                                                                 no_network_on_gui_thread, mysql_server, monkeypatch):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 沒有人在聽：馬上被拒絕
    ui = launch(engine, services)
    assert ui.home.profile.state == "error"
    toast = toast_titled(ui.window, "Can't reach the database")  # 帳號資料是整個管理員端都要用的：跳通知
    assert toast.property("actionLabel") == "Retry"

    page = qml(ui.window, "mainContentLoader.item")
    for name, title in (("overviewError", "Couldn't load the overview"), ("rankingError", "Couldn't load the ranking"),
                        ("trendError", "Couldn't load the trend")):
        error = child(page, name)  # 一張卡片一個錯誤，不跳對話框（PLAN 5-2）
        assert error.isVisible() and child(error, "errorTitle").property("text") == title, name
    overview_error = child(page, "overviewError")
    assert child(overview_error, "errorDetail").property("text") == (
        "Can't reach the database. Check your network and try again.")
    assert child(overview_error, "detailsButton").isVisible()  # 技術細節收在 Details 裡

    reconnect_directly(monkeypatch, mysql_server)
    click(ui.window, child(child(page, "trendError"), "retryButton"))
    wait_until(lambda: ui.home.homeDashboard.trend.state == "empty")
    assert overview_error.isVisible()  # 只重查那一張：其他卡片照舊
    click(ui.window, child(overview_error, "retryButton"))
    wait_until(lambda: ui.home.homeDashboard.overview.state == "empty")
    assert not overview_error.isVisible() and child(page, "overviewEmpty").isVisible()

    page = open_page(ui, "admin_add_page.qml")  # 不知道自己的等級：表單換成區塊錯誤
    profile_error = child(page, "profileError")
    assert profile_error.isVisible()
    assert child(profile_error, "errorTitle").property("text") == "Your account details didn't load"
    click(ui.window, child(profile_error, "retryButton"))
    wait_until(lambda: qml(page, "levelField.count") == 5)
    assert not profile_error.isVisible()
    wait_until(lambda: not any(t is not None and t.property("title") == "Can't reach the database"
                               for t in shown_toasts(ui.window)))  # 載入成功，錯誤通知收掉


# ---------------------------------------------------------------- 服務查詢（UX-24、DS-26、DS-27）


def test_search_shows_date_errors_empty_results_and_rows(admin, db):
    db.add_service("S20261031100000", "2026-10-31 10:00:00")
    manager = admin.home.serviceManager
    page = open_page(admin, "admin_service_page.qml")
    wait_until(lambda: manager.namesState == "ready")
    assert qml(page, "nameComboBox.displayText") == "All Staff"  # 名單載入後不會自己選第一個人
    assert qml(page, "nameComboBox.count") == 2  # 「All Staff」＋一位服務人員
    assert qml(child(page, "searchButton"), "variant") == "primary"  # 這一頁唯一的主要按鈕：石墨灰（DS-12）
    qml(page, 'startDateField.text = "2026/10/01"; endDateField.text = "2026-10-31"')
    click(admin.window, child(page, "searchButton"))
    assert field_error(page, "startDateError") == "Enter the date as YYYY-MM-DD, for example 2026-10-31."
    assert field_error(page, "endDateError") == ""
    assert manager.state == "idle"  # 日期不對：沒有送出
    # 和服務人員端同一個日期欄（DateField.qml）：框線變紅，錯誤寫進給輔助技術的說明（DS-27）
    start, end = child(page, "startDateField"), child(page, "endDateField")
    assert qml(start, "invalid && Qt.colorEqual(background.border.color, Theme.errorOnDark)")
    assert qml(start, "Accessible.name") == "Start date"
    assert qml(start, "Accessible.description") == "Enter the date as YYYY-MM-DD, for example 2026-10-31."
    assert not qml(end, "invalid") and qml(end, "Accessible.description") == ""

    qml(page, 'startDateField.text = "2026-09-01"; endDateField.text = "2026-09-30"')
    admin.proxy.reset()
    click(admin.window, child(page, "searchButton"))
    wait_until(lambda: manager.state == "empty")
    assert admin.proxy.snapshot()["rt"] == 1
    assert field_error(page, "startDateError") == "" and not qml(start, "invalid")
    empty = child(page, "searchEmpty")
    assert child(empty, "emptyTitle").property("text") == "No services from 2026-09-01 to 2026-09-30"
    assert child(empty, "emptyDescription").property("text") == "Try a different date range."

    qml(page, 'startDateField.text = "2026-10-01"; endDateField.text = "2026-10-31"')
    qml(page, "nameComboBox.currentIndex = nameComboBox.find(serviceManager.names[0]); "
              "nameComboBox.activated(nameComboBox.currentIndex)")  # 使用者選了一個服務人員
    click(admin.window, child(page, "searchButton"))
    wait_until(lambda: manager.state == "ready")
    assert manager.lastQuery["name"] == "服務人員（佔位）"
    assert qml(child(page, "resultList"), "count") == 1 and not empty.isVisible()

    open_page(admin, "admin_add_page.qml")
    db.add_account("00000000-0000-0000-0000-000000000004", "lee", "李小華")  # 名單變了：選單會整個換掉
    page = open_page(admin, "admin_service_page.qml")  # 回到這一頁：上一次的條件和結果都還在
    assert (qml(page, "startDateField.text"), qml(page, "endDateField.text")) == ("2026-10-01", "2026-10-31")
    wait_until(lambda: qml(page, "nameComboBox.count") == 3)
    wait_until(lambda: qml(page, "nameComboBox.displayText") == "服務人員（佔位）")  # 選的人還是同一位
    assert qml(child(page, "resultList"), "count") == 1

    qml(page, "nameComboBox.currentIndex = 0; nameComboBox.activated(0)")  # 改回「All Staff」：不再篩選名字
    click(admin.window, child(page, "searchButton"))
    wait_until(lambda: manager.lastQuery["name"] == "" and manager.state == "ready")


def test_search_failure_is_a_block_error_with_retry(admin, db, mysql_server, monkeypatch):
    manager = admin.home.serviceManager
    page = open_page(admin, "admin_service_page.qml")
    wait_until(lambda: manager.namesState == "ready")
    admin.proxy.kill()
    qml(page, 'startDateField.text = "2026-10-01"')
    click(admin.window, child(page, "searchButton"))
    wait_until(lambda: manager.state == "error")
    error = child(page, "searchError")
    assert error.isVisible() and child(error, "errorTitle").property("text") == "Search failed"

    reconnect_directly(monkeypatch, mysql_server)
    click(admin.window, child(error, "retryButton"))
    wait_until(lambda: manager.state == "empty")
    assert child(child(page, "searchEmpty"), "emptyTitle").property("text") == "No services from 2026-10-01 on"


def test_staff_list_failure_can_be_retried_on_the_page(admin, db, mysql_server, monkeypatch):
    manager = admin.home.serviceManager
    admin.proxy.kill()
    page = open_page(admin, "admin_service_page.qml")
    wait_until(lambda: manager.namesState == "error")
    row = child(page, "namesError")
    assert row.isVisible()
    reconnect_directly(monkeypatch, mysql_server)
    click(admin.window, find_button(row, "Retry"))
    wait_until(lambda: manager.namesState == "ready")
    assert not row.isVisible()


# ---------------------------------------------------------------- launch() 的物件存活時間


def test_page_objects_live_as_long_as_the_shell(engine, services):
    """setContextProperty 不會留住 Python 物件（T18）：launch() 要讓它們和外殼活得一樣久，gc 之後頁面還能用。"""
    admin_home_init.launch(engine, ADMIN_ID, services)  # 不留回傳值，和 MainWindow 一樣
    window = engine.rootObjects()[-1]
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in ("admin:profile",)))
    gc.collect()
    for name in ("adminHome", "homeDashboard", "serviceManager", "resultsModel", "accountManager"):
        assert qml(window, f"{name} !== null"), name
    page = open_page(SimpleNamespace(window=window), "admin_add_page.qml")
    assert page is not None
