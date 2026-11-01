"""管理員端看得到的回饋（前端重建 F1 接線＋F2，CONTRACTS §3）：新增帳號、外殼的通知、各區塊的空狀態和錯誤。

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

from myapp.init import admin_home_init
from myapp.passwords import verify_password
from tests.integration.helpers import ADMIN_ID, find_button, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
FORM_FIELDS = ("usernameField", "passwordField", "nameField", "jobTitleField")


# ---------------------------------------------------------------- fixture 與小工具


def settled(home):
    """首頁和帳號資料都載入完了（成功、沒有資料或失敗都算），也不在重新整理。"""
    sections = (home.profile, home.serviceScoreManager, home.rankingModel, home.trendDataManager)
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


def click(window, item):
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


def rings(page, panel="dailyPanel"):
    """一個期間的 8 個分數環：顧客 4 個、服務人員 4 個（Repeater 的項目用 itemAt 拿）。"""
    panel_item = child(page, panel)
    return [qml(child(panel_item, repeater), f"itemAt({i})")
            for repeater in ("customerRings", "staffRings") for i in range(4)]


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


def freeze_lane_clock(services, moment):
    """讓 db lane 那條連線的 NOW()、CURDATE() 停在 moment（SET TIMESTAMP 只影響這條連線）。"""
    def work():
        with services.db.cursor() as cursor:
            cursor.execute("SET TIMESTAMP = UNIX_TIMESTAMP(%s)", (moment,))
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
        assert qml(window, "username.text") == ""  # 名字還在載入
        assert {child(ring, "scoreText").property("text") for ring in rings(page)} == {"—"}  # 還沒載入：「—」，不是 0
        assert db_proxy.snapshot()["rt"] == 0  # db lane 被擋著，這段期間的往返只可能來自 GUI 執行緒
    wait_until(lambda: settled(home))
    assert db_proxy.snapshot()["rt"] == 4  # 名字和等級、分數、排名、趨勢各 1 個往返（預算 ≤ 5）
    assert qml(window, "username.text") == "系統管理員（佔位）"


# ---------------------------------------------------------------- 新增帳號（UX-33、UX-34、DS-52）


def test_add_account_success_shows_a_toast_clears_the_form_and_focuses_username(admin, db):
    page = open_page(admin, "admin_add_page.qml")
    wait_until(lambda: qml(page, "levelField.count") == 5)
    fill(page, usernameField="new_staff", passwordField="S3cret-pass", nameField="新同事", jobTitleField="櫃檯人員")
    admin.proxy.reset()
    click(admin.window, child(page, "addAccountButton"))

    toast = toast_titled(admin.window, "Account added")
    assert (toast.property("severity"), toast.property("detail")) == ("success", "new_staff · 新同事 · Level 1")
    assert admin.proxy.snapshot()["rt"] == 1  # 一個 INSERT
    assert [qml(page, f"{field}.text") for field in FORM_FIELDS] == ["", "", "", ""]
    wait_until(lambda: qml(page, "usernameField.input.activeFocus"))  # 接著新增下一個
    stored, level, job_title = db.rows("SELECT account_password, account_level, account_occupation_name "
                                       "FROM tbl_account WHERE account_username = 'new_staff'")[0]
    assert verify_password("S3cret-pass", stored) and (level, job_title) == ("1", "櫃檯人員")


def test_add_account_with_the_keyboard_only(admin, db):
    page = open_page(admin, "admin_add_page.qml")
    wait_until(lambda: qml(page, "levelField.count") == 5)
    wait_until(lambda: qml(page, "usernameField.input.activeFocus"))  # 一打開就可以打字
    for text in ("kb_staff", "S3cret-pass", "鍵盤"):
        type_text(admin.window, text)
        QTest.keyClick(admin.window, Qt.Key.Key_Tab)
    assert qml(page, "levelField.activeFocus")
    QTest.keyClick(admin.window, Qt.Key.Key_Tab)  # 等級用預設的 1 · Staff
    type_text(admin.window, "櫃檯")
    QTest.keyClick(admin.window, Qt.Key.Key_Return)  # Enter 送出
    toast_titled(admin.window, "Account added")
    assert db.rows("SELECT account_name, account_occupation_name FROM tbl_account "
                   "WHERE account_username = 'kb_staff'") == [("鍵盤", "櫃檯")]


def test_duplicate_username_is_a_field_error_and_moves_focus_there(admin, db):
    page = open_page(admin, "admin_add_page.qml")
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
    page = open_page(admin, "admin_add_page.qml")
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
    page = open_page(admin, "admin_add_page.qml")
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
    page = open_page(ui, "admin_add_page.qml")
    wait_until(lambda: qml(page, "levelField.count") == level)
    assert [qml(page, f"levelField.textAt({i})") for i in range(level)] == (
        ["1 · Staff"] + [f"{n} · Admin" for n in range(2, level + 1)])
    assert child(page, "levelHelp").property("text") == (
        f"You can assign levels 1–{level} (your level is {level}). Admins can add accounts up to their own level.")


def test_level_above_own_level_is_rejected_on_the_level_field(db, engine, services, qml_warnings,
                                                              no_network_on_gui_thread):
    db.add_account("00000000-0000-0000-0000-000000000103", "admin3", "管理員3", level="3")
    ui = launch(engine, services, "00000000-0000-0000-0000-000000000103")
    page = open_page(ui, "admin_add_page.qml")
    wait_until(lambda: qml(page, "levelField.count") == 3)
    qml(page, 'accountManager.addAccount("boss", "pw", "老闆", "5", "")')  # 繞過選單：Python 端照樣擋
    level_error = child(page, "levelError")
    assert level_error.property("text") == "Level 5 is above your level (3). Choose level 3 or lower."
    assert level_error.isVisible() and not child(page, "levelHelp").isVisible()
    wait_until(lambda: qml(page, "levelField.activeFocus"))
    assert db.rows("SELECT COUNT(*) FROM tbl_account WHERE account_username = 'boss'") == [(0,)]


def test_update_account_is_hidden(admin):
    page = open_page(admin, "admin_add_page.qml")
    assert find_button(page, "Add Account") is not None
    assert find_button(page, "Update Account") is None  # PLAN D8：沒有帳號清單可以選，先拿掉


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


# ---------------------------------------------------------------- 首頁：沒有資料、錯誤、重新整理（DS-25～27、DS-33）


def test_home_without_data_shows_empty_states_not_zeros(admin):
    page = qml(admin.window, "mainContentLoader.item")
    daily = child(page, "dailyPanel")
    assert child(daily, "panelSummary").property("text") == "No completed analyses today"
    assert {child(ring, "scoreText").property("text") for ring in rings(page)} == {"—"}
    assert qml(rings(page)[0], "Accessible.name") == "Customer overall score, no data"
    legend = child(daily, "scoreLegend")  # 弧線顏色的門檻寫出來（UX-07）
    assert legend.isVisible() and qml(legend, "Accessible.name") == (
        "Arc colors: good is 85 and up, fair is 70 to 84, needs improvement is below 70.")

    qml(page, "root.currentTab = 2")
    empty = child(page, "rankingEmpty")
    assert empty.isVisible() and child(empty, "emptyTitle").property("text") == "No rankings yet"
    assert not child(page, "rankingGrid").isVisible()

    qml(page, "root.currentTab = 3")
    today = date.today()
    empty = child(page, "trendsEmpty")
    assert empty.isVisible()
    assert child(empty, "emptyTitle").property("text") == f"No completed analyses in {MONTHS[today.month - 1]} {today.year}"


def test_home_tabs_are_graphite_segments_for_mouse_and_keyboard(admin):
    """F5（D2、DS-12、UX-41）：四個分頁是和服務人員端共用的 SegmentTab。選取的是石墨灰白字（以前是 Material Green，
    白字 2.78:1），沒選的是 #E0E0E0；滑鼠和鍵盤都能切換，輔助技術知道它是分頁、哪一個被選取。"""
    page = qml(admin.window, "mainContentLoader.item")
    tabs = [child(page, name) for name in ("dailyTab", "weeklyTab", "rankingTab", "trendsTab")]
    assert [tab.property("text") for tab in tabs] == ["Daily Scores", "Weekly Scores", "Ranking", "Trends"]
    assert [tab.property("selected") for tab in tabs] == [True, False, False, False]
    assert qml(tabs[0], "Qt.colorEqual(background.color, Theme.segmentSelected)"
                        " && Qt.colorEqual(contentItem.color, Theme.segmentSelectedText)")
    assert qml(tabs[1], "Qt.colorEqual(background.color, Theme.segmentIdle)"
                        " && Qt.colorEqual(contentItem.color, Theme.segmentIdleText)")
    assert qml(tabs[0], "Accessible.role === Accessible.PageTab && Accessible.selected")

    click(admin.window, tabs[2])
    assert qml(page, "currentTab") == 2 and tabs[2].property("selected") and not tabs[0].property("selected")
    assert child(page, "rankingPanel").isVisible() and not child(page, "dailyPanel").isVisible()

    tabs[3].forceActiveFocus(Qt.FocusReason.TabFocusReason)  # 用鍵盤：Tab 到「Trends」，按空白鍵
    assert qml(tabs[3], "visualFocus")  # 看得到焦點框（DS-30）
    QTest.keyClick(admin.window, Qt.Key.Key_Space)
    wait_until(lambda: qml(page, "currentTab") == 3)
    assert child(page, "trendsPanel").isVisible() and not child(page, "rankingPanel").isVisible()


def test_ranking_boxes_are_neutral_and_trend_controls_have_names(admin, db):
    """排名的四格是中性的灰底、#333333 的字：紅綠只代表狀態（DS-12、DS-13），以前的綠、橘、藍、紅標題不到 4.5:1（UX-45）。
    趨勢的選單和圖表有給輔助技術的名稱（UX-41）。"""
    today = date.today()
    service_id = f"S{today:%Y%m%d}090000"
    db.add_service(service_id, f"{today} 09:00:00")
    db.add_scores(service_id, customer=92, server=64)
    page = qml(admin.window, "mainContentLoader.item")
    qml(page, "rankingModel.refresh(); root.reloadTrends()")
    wait_until(lambda: admin.home.rankingModel.state == "ready" and admin.home.trendDataManager.state == "ready")
    qml(page, "root.currentTab = 2")
    for name, title in (("topCustomers", "Top 5 Customers"), ("bottomCustomers", "Bottom 5 Customers"),
                        ("topStaff", "Top 5 Staff"), ("bottomStaff", "Bottom 5 Staff")):
        box = child(page, name)
        assert qml(box, "Qt.colorEqual(color, Theme.surfaceAlt) && Qt.colorEqual(border.color, Theme.divider)"), name
        assert qml(box, "Accessible.role === Accessible.List") and qml(box, "Accessible.name") == title

    qml(page, "root.currentTab = 3")
    assert (qml(page, "monthSelector.Accessible.name"), qml(page, "yearSelector.Accessible.name")) == ("Month", "Year")
    chart = child(page, "trendChart")
    assert qml(chart, "Accessible.role === Accessible.Chart")
    assert qml(chart, "Accessible.name") == f"Daily average scores, {MONTHS[today.month - 1]} {today.year}"
    assert qml(chart, "Accessible.description") == "Days with data: staff 1, customers 1."


def test_scores_refresh_keeps_old_rings_dimmed_and_repaints_on_change(admin, db):
    today = date.today()
    freeze_lane_clock(admin.services, f"{today} 18:00:00")
    service_id = f"S{today:%Y%m%d}090000"
    db.add_service(service_id, f"{today} 09:00:00")
    db.add_scores(service_id, customer=80, server=70)
    scores = admin.home.serviceScoreManager
    page = qml(admin.window, "mainContentLoader.item")
    scores.refresh()
    ring = rings(page)[0]  # 顧客的 Overall
    wait_until(lambda: child(ring, "scoreText").property("text") == "80")
    assert qml(ring, "Accessible.name") == "Customer overall score 80, fair"
    assert child(child(page, "dailyPanel"), "panelSummary").property("text") == "Services analyzed today: 1"
    render_frames(admin.window)  # 排隊中的重畫都畫完，之後才開始數
    paints = ring.property("paints")
    assert paints > 0

    panel = child(page, "dailyPanel")
    with db_lane_blocked(admin.services):
        db.execute("UPDATE tbl_service_score SET customer_total = 90 WHERE service_id = %s", (service_id,))
        scores.refresh()  # 排在被擋住的工作後面
        wait_until(lambda: child(panel, "rings").property("opacity") == 0.5)  # 過了 300 ms 才變淡
        assert child(panel, "panelStatus").property("text") == "Updating…"
        assert child(ring, "scoreText").property("text") == "80"  # 舊資料留著，不換成 placeholder
        assert qml(ring, "Accessible.name") == "Customer overall score 80, fair, updating"
        render_frames(admin.window)
        assert ring.property("paints") == paints  # 變淡、狀態列改變都不重畫 Canvas：資料沒變

    wait_until(lambda: child(ring, "scoreText").property("text") == "90")
    wait_until(lambda: child(panel, "rings").property("opacity") == 1)
    wait_until(lambda: ring.property("paints") > paints)  # 分數變了，Canvas 才重畫


def test_offline_at_launch_shows_block_errors_and_retry_recovers(db, engine, services, qml_warnings,
                                                                 no_network_on_gui_thread, mysql_server, monkeypatch):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 沒有人在聽：馬上被拒絕
    ui = launch(engine, services)
    assert ui.home.profile.state == "error"
    toast = toast_titled(ui.window, "Can't reach the database")  # 帳號資料是整個管理員端都要用的：跳通知
    assert toast.property("actionLabel") == "Retry"

    page = qml(ui.window, "mainContentLoader.item")
    scores_error = child(child(page, "dailyPanel"), "scoresError")
    assert scores_error.isVisible()
    assert child(scores_error, "errorTitle").property("text") == "Scores failed to load"
    assert child(scores_error, "errorDetail").property("text") == (
        "Can't reach the database. Check your network and try again.")
    assert child(scores_error, "detailsButton").isVisible()  # 技術細節收在 Details 裡
    for tab, name, title in ((2, "rankingError", "Ranking failed to load"), (3, "trendsError", "Trends failed to load")):
        qml(page, f"root.currentTab = {tab}")
        assert child(page, name).isVisible() and child(child(page, name), "errorTitle").property("text") == title

    reconnect_directly(monkeypatch, mysql_server)
    click(ui.window, child(child(page, "trendsError"), "retryButton"))
    wait_until(lambda: ui.home.trendDataManager.state == "empty")
    qml(page, "root.currentTab = 0")
    click(ui.window, child(scores_error, "retryButton"))
    wait_until(lambda: ui.home.serviceScoreManager.state == "empty")
    assert not scores_error.isVisible()

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
    assert qml(start, "invalid && Qt.colorEqual(background.border.color, Theme.errorOnLight)")
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
    for name in ("adminHome", "serviceScoreManager", "rankingModel", "trendDataManager", "serviceManager",
                 "resultsModel", "accountManager"):
        assert qml(window, f"{name} !== null"), name
    page = open_page(SimpleNamespace(window=window), "admin_add_page.qml")
    assert page is not None
