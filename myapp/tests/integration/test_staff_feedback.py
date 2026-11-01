"""服務人員端與登入的回饋（前端重建 F2，CONTRACTS §3）：資料庫都在 db lane，畫面上分清「載入中、沒有資料、錯誤」。

- 登入：背景驗證、按鈕忙碌、Enter 送出、帳號密碼錯和連不上資料庫各自的說明與焦點（DS-31、UX-01～03）
- launch()：外殼馬上出現；GUI 執行緒 0 個往返、背景 ≤ 5 個（ENG-21）
- 首頁：還沒有資料是「—」不是 0、資料到了 Canvas 重畫、重新整理時舊的分數變淡、錯誤可以重試（DS-33、UX-05、UX-06）
- 分析頁：分頁數量、空狀態、狀態標籤、等了多久、-1 顯示「—」、增量更新、自動更新失敗只警告一次（UX-19～21）
- 服務查詢：日期格式錯不查、空狀態、錯誤重試、連按只留最後一次（UX-24）
- 錄影頁：打開才探測裝置，探測中、裝置不夠、探測失敗各有畫面（UX-13、ENG-06）

斷線用兩招：DB_PORT 指到沒有人在聽的 port（連不上），以及 KILL db lane 那條連線（已經連上的斷掉）。
"""
import gc
import os
import random
import threading
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest
import shiboken6
from PySide6.QtCore import QObject, Qt, QUrl, qInstallMessageHandler
from PySide6.QtTest import QAbstractItemModelTester, QTest

from myapp import main
from myapp.database import verify_login
from myapp.init import server_home_init
from myapp.init.server_home_init import (ACCOUNT_NAME_KEY, ANALYSIS_KEY, HOME_SCORES_KEY, LAUNCH_KEYS, SEARCH_KEY,
                                        RowListModel)
from myapp.main import MainWindow
from myapp.services import CLOSE_DB_KEY
from tests.integration.helpers import STAFF_ID, STAFF_NAME, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

LOGIN_QML = Path(main.__file__).parent / "qml" / "pages" / "login.qml"
DATE_HINT = "Enter the date as YYYY-MM-DD, for example 2026-10-31."
OFFLINE = "Can't reach the database. Check your network and try again."
_keys = count(1)


# ── 小工具 ───────────────────────────────────────────────────────────────────

def on_db_lane(tasks, lane, fn):
    """在 db lane 上執行 fn(lane) 並等它做完（db lane 的連線只能在那條執行緒用）。"""
    done = []
    tasks.submit("db", f"test:{next(_keys)}", lambda token, report: fn(lane), on_ok=lambda result: done.append(result))
    wait_until(lambda: done)
    return done[0]


def query(lane, sql, params=()):
    with lane.cursor(dictionary=True) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


@contextmanager
def db_lane_held(tasks):
    """db lane 卡在一個等待中的工作上：期間送出的工作都排在後面，畫面停在「載入中」。離開 with 才放行。"""
    gate = threading.Event()
    key = f"test:hold:{next(_keys)}"
    tasks.submit("db", key, lambda token, report: gate.wait(10))
    try:
        yield
    finally:
        gate.set()
        wait_until(lambda: not tasks.isBusy(key))


def freeze_db_clock(services, moment):
    """db lane 那條連線的 NOW()、CURDATE() 停在 moment（SET TIMESTAMP 只影響這條連線）。"""
    on_db_lane(services.tasks, services.db, lambda lane: query(lane, "SET TIMESTAMP = UNIX_TIMESTAMP(%s)", (moment,)))


def cut_database(services, db, monkeypatch):
    """斷線：db lane 已經連上的那條被切斷，重連也連不上。回傳恢復用的函式。"""
    [row] = on_db_lane(services.tasks, services.db, lambda lane: query(lane, "SELECT CONNECTION_ID() AS id"))
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    db.execute(f"KILL {row['id']}")
    return lambda: monkeypatch.setenv("DB_PORT", port)


def find(root, name):
    """用 objectName 找子物件。用它的子物件時要留著 root 的參照（T17：PySide 的 wrapper 會跟著失效）。"""
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def find_item(root, name):
    """在畫面樹（childItems）裡找 objectName 是 name 的項目：Repeater、ListView 建立的項目沒有 QObject parent，
    findChild 找不到。"""
    pending = [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"找不到 {name}")


def rows_of(view, name):
    """ListView 已經建立的列，依 index 排序（不用 JS 的 itemAtIndex()）。"""
    rows = [item for item in view.property("contentItem").childItems() if item.objectName() == name]
    return sorted(rows, key=lambda item: item.property("index"))


def launch_staff(engine, services):
    home = server_home_init.launch(engine, STAFF_ID, services)
    return home, engine.rootObjects()[-1]


def wait_for_launch(services):
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))


def show_page(window, sidebar_button):
    """按側邊欄的按鈕（SidebarButton 的 id），等新頁面載入完成。"""
    qml(window, f"{sidebar_button}.click()")
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def ring_text(page, side, kind):
    ring = find_item(page, f"scoreRing_{side}_{kind}")
    return find(ring, "scoreText").property("text")


# ── 登入 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def login(engine, db):
    """登入視窗（跳過開場動畫）。回傳 controller（MainWindow）、window、form。"""
    controller = MainWindow(engine)
    engine.load(QUrl.fromLocalFile(str(LOGIN_QML)))
    window = engine.rootObjects()[0]
    qml(window, "stackView.replace(loginComponent)")
    wait_until(lambda: not qml(window, "stackView.busy"))
    yield SimpleNamespace(controller=controller, window=window, form=qml(window, "stackView.currentItem"))
    if controller.services.close_db():
        wait_until(lambda: not controller.tasks.isBusy(CLOSE_DB_KEY))
    controller.tasks.shutdown()


def fill(form, username, password):
    qml(form, "username_textfield").setProperty("text", username)
    qml(form, "password_textfield").setProperty("text", password)


def login_error(form):
    return qml(form, "error_message.text")


def test_login_starts_on_username_and_enter_submits(engine, login, no_network_on_gui_thread):
    form, window = login.form, login.window
    username, password = qml(form, "username_textfield"), qml(form, "password_textfield")
    wait_until(username.hasActiveFocus)  # 一進來就能打帳號（UX-03）
    assert qml(form, "username_textfield.Accessible.name") == "Username"
    username.setProperty("text", "placeholder_staff")
    QTest.keyClick(window, Qt.Key.Key_Tab)
    wait_until(password.hasActiveFocus)
    password.setProperty("text", "CHANGE-ME-staff-0000")
    QTest.keyClick(window, Qt.Key.Key_Return)  # 在密碼欄按 Enter 就送出
    assert qml(form, "loginButton.busy")  # 立刻忙碌：擋住第二次送出
    assert qml(form, "username_textfield.readOnly") and qml(form, "password_textfield.readOnly")  # 唯讀但不停用
    assert qml(form, "username_textfield.enabled")
    wait_until(lambda: len(engine.rootObjects()) == 2)  # 驗證在 db lane；登入後的畫面也不在 GUI 執行緒連線
    assert not window.isVisible()
    assert engine.rootObjects()[1].title() == "Staff Home"


def test_wrong_password_says_so_and_selects_the_password(login, no_network_on_gui_thread):
    form = login.form
    fill(form, "placeholder_staff", "CHANGE-ME-staff-000X")
    qml(form, "loginButton.clicked()")
    wait_until(lambda: login_error(form) != "")
    assert login_error(form) == "Incorrect username or password. Try again."
    password = qml(form, "password_textfield")
    wait_until(password.hasActiveFocus)  # 焦點回到密碼欄並全選，可以直接重打
    assert (qml(form, "password_textfield.selectionStart"), qml(form, "password_textfield.selectionEnd")) == (
        0, len("CHANGE-ME-staff-000X"))
    assert not qml(form, "loginButton.busy") and not qml(form, "password_textfield.readOnly")
    assert qml(form, "error_row.visible && error_row.Accessible.role === Accessible.AlertMessage")
    assert toasts(login.controller.notifier) == []  # 登入錯誤留在表單裡，不跳通知（DS-20）

    fill(form, "placeholder_staff", "CHANGE-ME-staff-0000")
    qml(form, "loginButton.clicked()")
    assert login_error(form) == ""  # 再送出時，上一次的錯誤清掉


def test_offline_login_keeps_the_password_and_focuses_the_button(engine, login, monkeypatch,
                                                                   no_network_on_gui_thread):
    form, window = login.form, login.window
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 資料庫連不上
    fill(form, "placeholder_staff", "CHANGE-ME-staff-0000")
    qml(form, "loginButton.clicked()")
    wait_until(lambda: login_error(form) != "")
    assert login_error(form) == "Can't connect to the database. Check your network and try again."
    assert qml(form, "password_textfield.text") == "CHANGE-ME-staff-0000"  # 密碼沒錯，不清掉
    button = qml(form, "loginButton")
    wait_until(button.hasActiveFocus)
    assert button.property("visualFocus")  # 看得到焦點框
    assert toasts(login.controller.notifier) == []

    monkeypatch.setenv("DB_PORT", port)  # 網路恢復：焦點在按鈕上，按 Enter 直接重試
    QTest.keyClick(window, Qt.Key.Key_Return)
    wait_until(lambda: len(engine.rootObjects()) == 2)


def test_database_problem_other_than_network_is_not_called_a_wrong_password(login, monkeypatch):
    form = login.form
    monkeypatch.setenv("DB_PASSWORD", "not-the-database-password")  # 例如資料庫設定錯了：1045 Access denied
    fill(form, "placeholder_staff", "CHANGE-ME-staff-0000")
    qml(form, "loginButton.clicked()")
    wait_until(lambda: login_error(form) != "")
    assert login_error(form) == "Can't log in because of a database problem. Contact your administrator."


def test_login_button_ignores_repeat_submits_while_busy(login, monkeypatch):
    form, controller = login.form, login.controller
    attempts = []
    real_verify = main.verify_login
    monkeypatch.setattr(main, "verify_login", lambda *args: attempts.append(args[1:]) or real_verify(*args))
    fill(form, "placeholder_staff", "CHANGE-ME-staff-000X")
    button = qml(form, "loginButton")
    button.setProperty("busyDelay", 0)  # 不等 300 ms 就換成忙碌的樣子
    with db_lane_held(controller.tasks):  # 驗證還沒做完
        qml(form, "loginButton.clicked()")
        assert controller.loggingIn and button.property("busy")
        qml(form, "submit()")  # 再按一次（或在欄位按 Enter）：被擋下
        assert controller.logIn("placeholder_staff", "again") is False  # Python 端也防重複送出（ENG-14）
        wait_until(lambda: button.property("busyShown"))
        assert find(button, "busyLabel").property("text") == "Logging in…"
    wait_until(lambda: login_error(form) != "")
    assert attempts == [("placeholder_staff", "CHANGE-ME-staff-000X")]


# ── launch() 與首頁分數 ───────────────────────────────────────────────────────

def test_page_objects_live_as_long_as_the_shell(engine, services, db):
    """setContextProperty 不會留住 Python 物件：launch() 要自己讓它們和外殼活得一樣久，gc 之後頁面還能用。"""
    server_home_init.launch(engine, STAFF_ID, services)  # 不留回傳值，和 MainWindow 一樣
    window = engine.rootObjects()[-1]
    wait_for_launch(services)
    gc.collect()
    for name in ("serverHome", "serviceScoreManager", "serviceModel", "serviceManager", "resultsModel",
                 "deviceFinder"):
        assert qml(window, f"{name} !== null"), name
    page = show_page(window, "analysisButton")
    assert qml(page, "vm !== null")
    qml(window, "serverHome.onLoadAnalysis()")
    assert services.tasks.isBusy(ANALYSIS_KEY)
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))


def add_scored_services(db):
    """10/30 兩筆分析完成的服務：顧客 (88+60)/2=74、服務人員 (72+91)/2=81.5；顧客的文字分數只有一筆。"""
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=88, server=72, customer_text=-1)
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_scores("S20261030100000", customer=60, server=91)


def test_launch_shows_the_shell_at_once_and_loads_in_the_background(engine, services, db, db_proxy,
                                                                    no_network_on_gui_thread):
    # 和登入後一樣：db lane 的連線已經連上（登入用過）。往返預算不含連線與建立 cursor（T16）
    on_db_lane(services.tasks, services.db, lambda lane: verify_login(lane, "placeholder_staff", "not-it"))
    db_proxy.reset()
    with db_lane_held(services.tasks):
        home, window = launch_staff(engine, services)
        assert window.isVisible()
        assert db_proxy.snapshot()["rt"] == 0  # GUI 執行緒上 0 個往返（以前 15 個）
        assert all(services.tasks.isBusy(key) for key in LAUNCH_KEYS)  # 帳號名稱、分數、分析清單都排在 db lane
        assert qml(window, "username.text") == ""
        page = qml(window, "mainContentLoader.item")
        assert ring_text(page, "customer", "total") == "—"
    wait_for_launch(services)
    assert db_proxy.snapshot()["rt"] <= 5  # ENG-21 的預算；實際是 4（名字、分數、清單、監看各 1）
    assert qml(window, "username.text") == STAFF_NAME == home.accountName


def test_home_shows_dashes_until_scores_arrive_then_repaints(engine, services, db, no_network_on_gui_thread):
    add_scored_services(db)
    freeze_db_clock(services, "2026-10-30 18:00:00")
    with db_lane_held(services.tasks):
        home, window = launch_staff(engine, services)
        page = qml(window, "mainContentLoader.item")
        ring = find_item(page, "scoreRing_customer_total")
        wait_until(lambda: ring.property("paintCount") >= 1)  # 第一次只畫軌道
        painted = ring.property("paintCount")
        assert {ring_text(page, side, kind) for side in ("customer", "staff")
                for kind in ("total", "text", "audio", "facial")} == {"—"}  # 還沒載入：「—」，不是 0（UX-05）
        assert not find(page, "countText").isVisible()
        assert qml(ring, "children[0].Accessible.name") == "Customer overall score, loading"  # 和管理員首頁一樣（F5）
    wait_until(lambda: not services.tasks.isBusy(HOME_SCORES_KEY))
    assert (ring_text(page, "customer", "total"), ring_text(page, "customer", "text")) == ("74", "60")
    assert ring_text(page, "staff", "total") == "82"
    wait_until(lambda: ring.property("paintCount") > painted)  # 資料到了才重畫（UX-06）
    assert find(page, "countText").property("text") == "Services analyzed today: 2"
    assert qml(ring, "children[0].Accessible.name") == "Customer overall score 74, fair"

    qml(page, "showDaily = false")
    assert find(page, "countText").property("text") == "Services analyzed in the last 7 days: 2"  # UX-07
    assert find(page, "weeklyTab").property("selected") and not find(page, "dailyTab").property("selected")


def test_home_without_data_shows_dashes_and_says_why(engine, services, db, no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    assert find(page, "countText").property("text") == "No completed analyses today"
    assert {ring_text(page, "staff", kind) for kind in ("total", "text", "audio", "facial")} == {"—"}
    ring = find_item(page, "scoreRing_staff_total")
    assert qml(ring, "children[0].Accessible.name") == "Staff overall score, no data"
    legend = find(page, "scoreLegend")  # 弧線顏色的門檻寫出來（UX-07），和管理員首頁同一個元件
    assert legend.isVisible() and qml(legend, "Accessible.name") == (
        "Arc colors: good is 85 and up, fair is 70 to 84, needs improvement is below 70.")
    # 分頁是共用的 SegmentTab：選取的是石墨灰白字（D2、DS-12）
    daily, weekly = find(page, "dailyTab"), find(page, "weeklyTab")
    assert qml(daily, "Qt.colorEqual(background.color, Theme.segmentSelected) && Accessible.selected")
    assert qml(weekly, "Qt.colorEqual(background.color, Theme.segmentIdle) && !Accessible.selected")
    qml(page, "showDaily = false")
    assert find(page, "countText").property("text") == "No completed analyses in the last 7 days"


def test_home_refresh_keeps_the_old_scores_dimmed(engine, services, db, no_network_on_gui_thread):
    add_scored_services(db)
    freeze_db_clock(services, "2026-10-30 18:00:00")
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    rows, indicator = find(page, "scoreRows"), find(page, "updatingIndicator")
    assert rows.property("opacity") == 1 and not indicator.isVisible()
    with db_lane_held(services.tasks):
        qml(window, "homeButton.click()")  # 側邊欄 Home：在背景重新載入
        wait_until(lambda: qml(page, "refreshing"))
        assert rows.property("opacity") == 0.5 and indicator.isVisible()  # 舊的分數留著、變淡，旁邊「Updating…」
        assert ring_text(page, "customer", "total") == "74"
    wait_until(lambda: not services.tasks.isBusy(HOME_SCORES_KEY))
    assert rows.property("opacity") == 1 and not indicator.isVisible()


def test_home_failure_offers_retry_and_recovers(engine, services, db, monkeypatch, no_network_on_gui_thread):
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 一開始就連不上
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    error = find(page, "scoresError")
    assert error.isVisible() and not find(page, "scoreRows").isVisible()  # 整排換成區塊錯誤（DS-33）
    assert error.property("title") == "Couldn't load scores"
    assert error.property("detail") == OFFLINE
    assert "2003" in error.property("technicalDetail")  # 技術細節收在 Details 裡
    assert home.accountName == ""

    for failures in (2, 3):  # 一直失敗：第 3 次多一行建議（DS-27）
        qml(error, "retry()")
        wait_until(lambda: error.property("failureCount") == failures)
    assert error.property("showsHint")

    monkeypatch.setenv("DB_PORT", port)
    qml(error, "retry()")  # Retry：分數和還沒載入的帳號名稱一起重試
    assert error.property("busy")
    wait_until(lambda: not services.tasks.isBusy(HOME_SCORES_KEY) and not services.tasks.isBusy(ACCOUNT_NAME_KEY))
    assert not error.isVisible() and find(page, "scoreRows").isVisible()
    assert qml(window, "username.text") == STAFF_NAME


# ── 分析頁 ───────────────────────────────────────────────────────────────────

def open_analysis(engine, services):
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = show_page(window, "analysisButton")
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    return window, page


def cell(row, name):
    return find(row, name).property("text")


def chip(row):
    return find(row, "statusChip").property("label")


def test_analysis_tabs_show_counts_chips_waiting_time_and_scores(engine, services, db, no_network_on_gui_thread):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80.6, server=70.4)
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_scores("S20261030100000", customer=-1, server=-1)  # 沒有分數
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2)
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=1)
    window, page = open_analysis(engine, services)
    assert [find(page, f"{key}Tab").property("text") for key in ("completed", "analyzing", "notAnalyzed")] == [
        "Completed (2)", "Analyzing (1)", "Not Analyzed (2)"]
    assert qml(page, "selectedTab") == "notAnalyzed"  # 剛錄好的在這裡（UX-19）

    not_analyzed = find(page, "notAnalyzedList")
    rows = rows_of(not_analyzed, "serviceRow")
    assert [cell(row, "serviceIdCell") for row in rows] == ["S20261030130000", "S20261030120000"]  # 新的在前
    assert {chip(row) for row in rows} == {"Not Analyzed"}
    assert find(rows[0], "analyzeButton").property("text") == "Analyze"

    qml(page, 'selectedTab = "completed"')
    rows = rows_of(find(page, "completedList"), "serviceRow")
    assert [cell(row, "scoreCell") for row in rows] == ["— / —", "70 / 81"]  # 不顯示 -1，四捨五入（UX-20）
    assert {chip(row) for row in rows} == {"Completed"}
    assert qml(rows[1], "Accessible.name") == "S20261030090000, Completed, staff / customer 70 / 81"

    qml(page, 'selectedTab = "analyzing"')
    [row] = rows_of(find(page, "analyzingList"), "serviceRow")
    assert chip(row) == "Analyzing"
    assert cell(row, "waitingText") == "Waiting less than 1 min"
    since = row.property("waitingSince")
    qml(page, f"now = {since} + 3 * 60000")
    assert cell(row, "waitingText") == "Waiting 3 min"
    qml(page, f"now = {since} + 65 * 60000")
    assert cell(row, "waitingText") == "Waiting 1 h 5 min"


def test_each_analysis_tab_has_an_empty_state(engine, services, db, no_network_on_gui_thread):
    window, page = open_analysis(engine, services)
    expected = {"completed": "No completed analyses yet", "analyzing": "Nothing is being analyzed",
                "notAnalyzed": "No services to analyze"}
    for key, title in expected.items():
        qml(page, f'selectedTab = "{key}"')
        empty = find(page, f"{key}Empty")
        assert empty.isVisible() and empty.property("title") == title
        assert find(page, f"{key}Tab").property("text").endswith("(0)")
    qml(page, 'selectedTab = "completed"')
    empty = find(page, "completedEmpty")
    assert empty.property("actionText") == "View Not Analyzed"
    qml(empty, "actionTriggered()")
    assert qml(page, "selectedTab") == "notAnalyzed"


def test_analysis_refresh_updates_rows_in_place(engine, services, db, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    window, page = open_analysis(engine, services)
    view = find(page, "notAnalyzedList")
    [old_row] = rows_of(view, "serviceRow")
    pointer = shiboken6.getCppPointer(old_row)[0]
    db.add_service("S20261030130000", "2026-10-30 13:00:00", status=1)  # 新錄好的一筆
    qml(window, "serviceModel.refresh()")  # 錄影存好之後的重新整理（頁面的 15 秒計時器在第三波換成監看）
    wait_until(lambda: len(rows_of(view, "serviceRow")) == 2)
    first, second = rows_of(view, "serviceRow")
    assert cell(first, "serviceIdCell") == "S20261030130000"
    assert shiboken6.getCppPointer(second)[0] == pointer  # 沒變的那一列沒有重建（ENG-08）


def test_analysis_first_load_failure_shows_retry(engine, services, db, monkeypatch, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    window, page = open_analysis(engine, services)
    error = find(page, "listError")
    assert error.isVisible() and error.property("title") == "Couldn't load your services"
    assert error.property("detail") == OFFLINE
    assert find(page, "notAnalyzedTab").property("text") == "Not Analyzed"  # 失敗時不顯示假的 0
    assert not find(page, "notAnalyzedTable").isVisible()
    monkeypatch.setenv("DB_PORT", port)
    qml(error, "retry()")
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    assert not error.isVisible() and find(page, "notAnalyzedTable").isVisible()
    assert find(page, "notAnalyzedTab").property("text") == "Not Analyzed (1)"


def test_auto_refresh_failure_keeps_the_list_and_warns_once(engine, services, db, monkeypatch):
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    window, page = open_analysis(engine, services)
    restore = cut_database(services, db, monkeypatch)
    for _ in range(2):  # 斷線期間重新整理失敗兩次：警告只有一則
        qml(window, "serviceModel.refresh()")
        wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    [warning] = toasts(services.notifier)
    assert (warning["severity"], warning["title"], warning["detail"], warning["count"]) == (
        "warning", "Can't refresh analysis status", "Can't reach the database. Retrying in 15 seconds.", 1)
    assert find(page, "notAnalyzedTable").isVisible() and not find(page, "listError").isVisible()  # 清單留著
    assert len(rows_of(find(page, "notAnalyzedList"), "serviceRow")) == 1

    restore()
    qml(window, "serviceModel.refresh()")
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    assert toasts(services.notifier) == []  # 恢復後警告自己消失


# ── 服務查詢 ─────────────────────────────────────────────────────────────────

def open_search(engine, services):
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    return window, show_page(window, "servicesButton")


def search(page, start, end):
    find(page, "startDateField").setProperty("text", start)
    find(page, "endDateField").setProperty("text", end)
    qml(page, "searchButton.clicked()")


def test_search_checks_dates_before_querying(engine, services, db, no_network_on_gui_thread):
    window, page = open_search(engine, services)
    assert find(page, "searchPrompt").isVisible()  # 還沒查過：說明怎麼查
    start = find(page, "startDateField")
    search(page, "2026/10/30", "2026-10-31")
    assert find(page, "startDateError").property("text") == DATE_HINT  # 欄位下方說明格式（DS-52）
    assert find(page, "endDateError").property("text") == ""
    assert qml(start, "Accessible.description") == DATE_HINT
    assert not services.tasks.isBusy(SEARCH_KEY)  # 格式不對就不查（以前會默默拿掉這個條件）
    wait_until(start.hasActiveFocus)  # 焦點移到有錯的欄位


def test_search_shows_an_empty_state_for_the_range(engine, services, db, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00")
    window, page = open_search(engine, services)
    find(page, "startDateField").setProperty("text", "2026-10-01")
    end = find(page, "endDateField")
    end.setProperty("text", "2026-10-29")
    end.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Return)  # 在日期欄按 Enter 就查
    assert qml(page, "searchButton.busy")
    wait_until(lambda: not services.tasks.isBusy(SEARCH_KEY))
    empty = find(page, "searchEmpty")
    assert empty.isVisible() and not find(page, "resultList").isVisible()
    assert empty.property("title") == "No services from 2026-10-01 to 2026-10-29"
    assert empty.property("description") == "Try a different date range."


def test_search_failure_shows_retry_and_recovers(engine, services, db, monkeypatch, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00")
    window, page = open_search(engine, services)
    restore = cut_database(services, db, monkeypatch)
    search(page, "2026-10-30", "2026-10-30")
    wait_until(lambda: not services.tasks.isBusy(SEARCH_KEY))
    error = find(page, "searchError")
    assert error.isVisible() and error.property("detail") == OFFLINE
    assert error.property("title") == "Couldn't search your services"
    restore()
    qml(error, "retry()")  # 用剛才送出的條件再查一次
    wait_until(lambda: not services.tasks.isBusy(SEARCH_KEY))
    assert not error.isVisible()
    view = find(page, "resultList")
    wait_until(lambda: len(rows_of(view, "resultRow")) == 1)  # delegate 在下一次畫面更新才建立
    [row] = rows_of(view, "resultRow")
    assert row.property("service_id") == "S20261030120000"


def test_rapid_searches_keep_only_the_last_result(engine, services, db, no_network_on_gui_thread):
    db.add_service("S20261028120000", "2026-10-28 12:00:00")
    db.add_service("S20261030120000", "2026-10-30 12:00:00")
    window, page = open_search(engine, services)
    with db_lane_held(services.tasks):
        search(page, "2026-10-28", "2026-10-28")
        assert qml(page, "searchButton.busy")
        qml(page, "searchButton.clicked()")  # 同樣的條件再按一次：不重送
        find(page, "startDateField").setProperty("text", "2026-10-30")
        find(page, "endDateField").setProperty("text", "2026-10-30")
        qml(page, "submit()")  # 改了日期按 Enter：取消前一次、改查新的（replace=True）
    wait_until(lambda: not services.tasks.isBusy(SEARCH_KEY))
    view = find(page, "resultList")
    wait_until(lambda: len(rows_of(view, "resultRow")) > 0)
    assert [row.property("service_id") for row in rows_of(view, "resultRow")] == ["S20261030120000"]
    assert qml(page, "manager.startDate") == "2026-10-30"


def test_search_page_keeps_the_last_criteria_when_reopened(engine, services, db, no_network_on_gui_thread):
    db.add_service("S20261030120000", "2026-10-30 12:00:00")
    window, page = open_search(engine, services)
    search(page, "2026-10-30", "")
    wait_until(lambda: not services.tasks.isBusy(SEARCH_KEY))
    show_page(window, "homeButton")  # 去首頁再回來
    page = show_page(window, "servicesButton")
    assert find(page, "startDateField").property("text") == "2026-10-30"  # 條件和結果對得起來（UX-24）
    assert find(page, "endDateField").property("text") == ""
    view = find(page, "resultList")
    wait_until(lambda: len(rows_of(view, "resultRow")) == 1)
    [row] = rows_of(view, "resultRow")
    assert row.property("service_id") == "S20261030120000"


# ── 錄影頁 ───────────────────────────────────────────────────────────────────

@pytest.fixture
def camera_opens(cameras, monkeypatch):
    opened = []

    def open_camera(index=None):
        opened.append(index)
        return cameras(index)

    monkeypatch.setattr(cv2, "VideoCapture", open_camera)
    return opened


def open_record_page(window):
    page = show_page(window, "recordButton")
    wait_until(lambda: qml(window, "deviceFinder.status") not in ("idle", "scanning"))
    return page


def test_devices_are_probed_only_when_the_record_page_opens(engine, services, db, camera_opens,
                                                          no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    assert camera_opens == []  # 登入時不開鏡頭（UX-02、UX-13）
    page = show_page(window, "recordButton")
    assert find(page, "scanningState").isVisible()  # 先畫出「Looking for cameras and microphones…」
    wait_until(lambda: qml(window, "deviceFinder.status") == "ready")
    assert find(page, "deviceForm").isVisible() and not find(page, "scanningState").isVisible()
    assert (qml(page, "cam0ComboBox.currentIndex"), qml(page, "cam1ComboBox.currentIndex")) == (0, 1)  # 預設就合法
    assert find(page, "sameCameraError").property("text") == ""
    assert find(page, "activateButton").isEnabled()
    probes = len(camera_opens)

    show_page(window, "homeButton")
    page = show_page(window, "recordButton")  # 再回到錄影頁：用上次的結果，不再開鏡頭
    assert find(page, "deviceForm").isVisible() and len(camera_opens) == probes


def test_same_cameras_disable_activate_with_a_field_error(engine, services, db, no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    page = open_record_page(window)
    qml(page, "cam1ComboBox.currentIndex = 0")
    assert find(page, "sameCameraError").property("text") == (
        "The staff and customer cameras are the same. Choose a different staff camera.")
    assert not find(page, "activateButton").isEnabled()


@pytest.mark.parametrize("cameras_connected, microphones, title", [
    ((), [("MacBook 麥克風", 1)], "No cameras found"),
    ((0,), [("MacBook 麥克風", 1)], "Only one camera found"),
    ((0, 1), [("MacBook 喇叭", 0)], "No microphone found"),
], ids=["no-camera", "one-camera", "no-microphone"])
def test_too_few_devices_show_an_empty_state_with_scan_again(engine, services, db, cameras, microphone,
                                                            cameras_connected, microphones, title):
    cameras.connected = cameras_connected
    microphone.devices = microphones
    home, window = launch_staff(engine, services)
    page = open_record_page(window)
    empty = find(page, "devicesEmpty")
    assert empty.isVisible() and empty.property("title") == title
    assert not find(page, "deviceForm").isVisible()

    cameras.connected = (0, 1)  # 接好之後按 Scan Again
    microphone.devices = [("MacBook 麥克風", 1)]
    qml(empty, "actionTriggered()")
    wait_until(lambda: qml(window, "deviceFinder.status") == "ready")
    assert find(page, "deviceForm").isVisible() and not empty.isVisible()


def test_probe_failure_shows_an_error_with_scan_again(engine, services, db, microphone):
    def broken(index):
        raise OSError(-9999, "Unanticipated host error (fake)")

    microphone.get_host_api_info_by_index = broken
    home, window = launch_staff(engine, services)
    page = open_record_page(window)
    error = find(page, "scanError")
    assert error.isVisible() and error.property("title") == "Couldn't check cameras and microphones"
    assert error.property("retryText") == "Scan Again"
    del microphone.get_host_api_info_by_index
    qml(error, "retry()")
    wait_until(lambda: qml(window, "deviceFinder.status") == "ready")
    assert find(page, "deviceForm").isVisible() and not error.isVisible()


# ── 增量清單模型（不需要資料庫）─────────────────────────────────────────────

@pytest.fixture
def checked_model():
    """RowListModel 掛上 Qt 的 QAbstractItemModelTester：每次變動都檢查模型的一致性，有問題就記下來。"""
    problems = []
    previous = qInstallMessageHandler(lambda mode, context, message: problems.append(message))
    model = RowListModel(("key", "value"), "key")
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    signals = []
    model.rowsInserted.connect(lambda parent, first, last: signals.append(("insert", first)))
    model.rowsRemoved.connect(lambda parent, first, last: signals.append(("remove", first)))
    model.rowsMoved.connect(lambda parent, start, end, destination, row: signals.append(("move", start, row)))
    model.dataChanged.connect(lambda top, bottom, roles: signals.append(("change", top.row())))
    model.modelReset.connect(lambda: signals.append(("reset",)))
    yield SimpleNamespace(model=model, signals=signals, problems=problems, tester=tester)
    qInstallMessageHandler(previous)
    assert problems == []


def replace(checked, rows):
    checked.signals.clear()
    checked.model.replace([dict(key=key, value=value) for key, value in rows])
    assert checked.model.rows() == [dict(key=key, value=value) for key, value in rows]
    return list(checked.signals)


def test_row_model_only_signals_what_changed(checked_model):
    counts = []
    checked_model.model.countChanged.connect(lambda: counts.append(checked_model.model.count))
    assert replace(checked_model, [("a", 1), ("b", 2), ("c", 3)]) == [("insert", 0), ("insert", 1), ("insert", 2)]
    assert replace(checked_model, [("a", 1), ("b", 2), ("c", 3)]) == []  # 沒變：什麼都不發
    assert replace(checked_model, [("c", 3), ("a", 1), ("b", 20)]) == [("move", 2, 0), ("change", 2)]
    assert replace(checked_model, [("d", 4), ("c", 3)]) == [("remove", 2), ("remove", 1), ("insert", 0)]
    assert counts == [3, 2]  # 數量變了才通知（分頁上的數字）
    assert checked_model.model.roleNames() == {256: b"key", 257: b"value"}


def test_row_model_reaches_any_target_order(checked_model):
    shuffle = random.Random(20261031)
    keys = list("abcdefgh")
    for _ in range(200):  # 隨機的新增、刪除、換順序、改值：結果一定和目標一樣，模型一直保持一致
        target = shuffle.sample(keys, shuffle.randint(0, len(keys)))
        replace(checked_model, [(key, shuffle.randint(0, 2)) for key in target])
        assert "reset" not in checked_model.signals
