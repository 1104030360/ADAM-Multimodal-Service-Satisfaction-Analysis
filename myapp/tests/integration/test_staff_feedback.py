"""服務人員端與登入的回饋（前端重建 F2，CONTRACTS §3）：資料庫都在 db lane，畫面上分清「載入中、沒有資料、錯誤」。

- 登入：背景驗證、按鈕忙碌、Enter 送出、帳號密碼錯和連不上資料庫各自的說明與焦點（DS-31、UX-01～03）
- launch()：外殼馬上出現；GUI 執行緒 0 個往返、背景 5 個（ENG-21）
- 首頁（U1，T28）：載入中是骨架、沒有服務是空狀態＋New Recording、重新查詢時舊的數字留著（Updating…）、
  一張卡片讀不到只有那張顯示錯誤；服務方塊打開分析頁、長條打開明細、換 Customer／Staff 不再查詢
- 分析頁：U2 改版後搬到 test_analysis_page.py（T30）
- 服務查詢：日期格式錯不查、空狀態、錯誤重試、連按只留最後一次（UX-24）
- 錄影頁：打開才探測裝置，探測中、裝置不夠、探測失敗各有畫面（UX-13、ENG-06）

斷線用兩招：DB_PORT 指到沒有人在聽的 port（連不上），以及 KILL db lane 那條連線（已經連上的斷掉）。
"""
import gc
import os
import random
import threading
from contextlib import contextmanager
from datetime import date
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest
import sounddevice
from PySide6.QtCore import QObject, QPointF, Qt, QUrl, qInstallMessageHandler
from PySide6.QtTest import QAbstractItemModelTester, QTest

from myapp import devices, home_dashboard, main
from myapp.database import verify_login
from myapp.init import server_home_init
from myapp.init.server_home_init import (ACCOUNT_NAME_KEY, ANALYSIS_KEY, HOME_KEYS, LAUNCH_KEYS, SEARCH_KEY,
                                        RowListModel)
from myapp.main import MainWindow
from myapp.services import CLOSE_DB_KEY
from tests.fakes import FakeSaver, every_save_status
from tests.integration.helpers import STAFF_ID, STAFF_NAME, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

LOGIN_QML = Path(main.__file__).parent / "qml" / "pages" / "login.qml"
DATE_HINT = "Enter the date as YYYY-MM-DD, for example 2026-10-31."
ORDER_HINT = "The To date is before the From date. Choose the same day or later."
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
    """ListView 已經建立的列，依 index 排序（不用 JS 的 itemAtIndex()）。剛被移除、還沒刪掉的列（index 是 -1）不算。"""
    rows = [item for item in view.property("contentItem").childItems()
            if item.objectName() == name and item.property("index") >= 0]
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


def js(obj, expression):
    """qml() 的結果；JS 陣列、物件轉成 Python 的 list、dict。"""
    result = qml(obj, expression)
    return result.toVariant() if hasattr(result, "toVariant") else result


def scroll_into_view(item):
    """首頁比測試的視窗（800×600）高：item 沒有整個露出來時，把整頁捲到它在中間。
    用最外層的 Flickable（頁面）：長條圖的 ListView 也是 Flickable，捲它沒有用。"""
    flickable, parent = None, item.parentItem()
    while parent is not None:
        if parent.inherits("QQuickFlickable"):
            flickable = parent
        parent = parent.parentItem()
    top = item.mapToItem(flickable, QPointF(0, 0)).y()
    if 0 <= top and top + item.height() <= flickable.height():
        return
    highest = max(0.0, flickable.property("contentHeight") - flickable.height())
    wanted = flickable.property("contentY") + top - (flickable.height() - item.height()) / 2
    flickable.setProperty("contentY", min(highest, max(0.0, wanted)))


def center_of(item):
    scroll_into_view(item)
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


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


# ── launch() 與首頁（新版介面 U1，T28）─────────────────────────────────────────

def test_page_objects_live_as_long_as_the_shell(engine, services, db):
    """setContextProperty 不會留住 Python 物件：launch() 要自己讓它們和外殼活得一樣久，gc 之後頁面還能用。"""
    server_home_init.launch(engine, STAFF_ID, services)  # 不留回傳值，和 MainWindow 一樣
    window = engine.rootObjects()[-1]
    wait_for_launch(services)
    gc.collect()
    for name in ("serverHome", "homeDashboard", "serviceModel", "serviceManager", "deviceFinder"):
        assert qml(window, f"{name} !== null"), name
    page = show_page(window, "analysisButton")
    assert qml(page, "vm !== null")
    qml(window, "serverHome.onLoadAnalysis()")
    assert services.tasks.isBusy(ANALYSIS_KEY)
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))


def add_scored_services(db, monkeypatch):
    """10/30 兩筆分析完成的服務：顧客 (88+60)/2=74、服務人員 (72+91)/2=81.5；顧客的文字分數只有一筆。
    首頁的期間從這台電腦的日期往前算（home_dashboard.today()）：「今天」換成 10/30。"""
    monkeypatch.setattr(home_dashboard, "today", lambda: date(2026, 10, 30))
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=88, server=72, customer_text=-1)
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_scores("S20261030100000", customer=60, server=91)


def tile_value(page, name):
    """分數方塊（Repeater 建的，findChild 找不到）或服務方塊上的大數字。"""
    tile = find_item(page, name)  # 留著方塊的參照，子物件的 wrapper 才不會失效（T17）
    value = find(tile, "tileValue")
    return value.property("text")


def test_launch_shows_the_shell_at_once_and_loads_in_the_background(engine, services, db, db_proxy,
                                                                    no_network_on_gui_thread):
    # 和登入後一樣：db lane 的連線已經連上（登入用過）。往返預算不含連線與建立 cursor（T16）
    on_db_lane(services.tasks, services.db, lambda lane: verify_login(lane, "placeholder_staff", "not-it"))
    db_proxy.reset()
    with db_lane_held(services.tasks):
        home, window = launch_staff(engine, services)
        assert window.isVisible()
        assert db_proxy.snapshot()["rt"] == 0  # GUI 執行緒上 0 個往返（以前 15 個）
        assert all(services.tasks.isBusy(key) for key in LAUNCH_KEYS)  # 名字、首頁的三張卡片、監看都排在 db lane
        assert not services.tasks.isBusy(ANALYSIS_KEY)  # 分析清單等打開分析頁才查（T28）
        assert qml(window, "header.userName") == ""
        page = qml(window, "mainContentLoader.item")
        for name in ("overviewSkeleton", "recentSkeleton", "trendSkeleton"):  # 還沒載入：灰色骨架，不是 0（PLAN 5-2）
            assert find(page, name).isVisible(), name
    wait_for_launch(services)
    assert db_proxy.snapshot()["rt"] == 5  # ENG-21 的預算：名字 1、首頁的三張卡片 3、監看 1
    assert qml(window, "header.userName") == STAFF_NAME == home.accountName


def test_home_shows_the_scores_when_they_arrive_and_switches_without_querying(engine, services, db, db_proxy,
                                                                              monkeypatch, no_network_on_gui_thread):
    add_scored_services(db, monkeypatch)
    with db_lane_held(services.tasks):
        home, window = launch_staff(engine, services)
        page = qml(window, "mainContentLoader.item")
        assert not find_item(page, "scoreTile_overall").isVisible()
    wait_for_launch(services)
    assert not find(page, "overviewSkeleton").isVisible()
    overall = find_item(page, "scoreTile_overall")
    assert overall.isVisible()
    assert (tile_value(page, "scoreTile_overall"), tile_value(page, "scoreTile_text")) == ("74.0", "60.0")
    assert find(overall, "tileBand").property("text") == "Fair"  # 分數旁邊一定寫字（PLAN 第 2 節）
    assert qml(overall, "Accessible.name") == "Overall 74.0, Fair"  # 上一期沒有分數：不寫變化
    assert not find(overall, "tileDelta").isVisible()
    assert find(page, "overviewSubtitle").property("text") == "Last 30 days · 2 services recorded"
    assert tile_value(page, "completedTile") == "2"

    db_proxy.reset()
    qml(page, 'overview.side = "staff"')  # Customer／Staff、期間、趨勢的分數：資料都在，不再查詢（T28）
    assert tile_value(page, "scoreTile_overall") == "81.5"
    assert find(page, "scoresLabel").property("text") == "Staff scores"
    qml(page, 'overview.period = "today"')
    assert find(page, "overviewSubtitle").property("text") == "Today · 2 services recorded"
    qml(page, 'trend.metric = "text"')
    assert js(page, "trend.customerValues")[-1] == 60.0
    assert db_proxy.snapshot()["rt"] == 0


def test_home_without_data_says_why_and_offers_new_recording(engine, services, db, no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    empty = find(page, "overviewEmpty")
    assert empty.isVisible() and find(empty, "emptyTitle").property("text") == "No services in the last 30 days"
    assert find(empty, "emptyDescription").property("text") == (
        "Record a service. Its scores show up here after it's analyzed.")
    assert find(page, "overviewSubtitle").property("text") == "Last 30 days · 0 services recorded"
    assert not find(page, "sideToggle").isVisible() and not find(page, "periodBox").isVisible()
    assert find(page, "recentEmpty").isVisible() and find(page, "trendEmpty").isVisible()
    button = find(page, "newRecordingButton")
    assert button.isVisible() and button.property("variant") == "primary"
    qml(button, "clicked()")
    wait_until(lambda: qml(window, "currentPage") == "record_widget.qml")


def test_home_refresh_keeps_the_old_numbers_and_says_updating(engine, services, db, monkeypatch,
                                                              no_network_on_gui_thread):
    add_scored_services(db, monkeypatch)
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    subtitle = find(page, "overviewSubtitle")
    assert tile_value(page, "scoreTile_overall") == "74.0" and "Updating" not in subtitle.property("text")
    with db_lane_held(services.tasks):
        db.execute("UPDATE tbl_service_score SET customer_total = 90 WHERE service_id = 'S20261030100000'")
        qml(window, "homeButton.click()")  # 側邊欄 Home：三張卡片在背景重新查詢
        assert all(services.tasks.isBusy(key) for key in HOME_KEYS)
        wait_until(lambda: subtitle.property("text") == "Last 30 days · 2 services recorded · Updating…")  # 300 ms 後
        assert tile_value(page, "scoreTile_overall") == "74.0"  # 舊的數字留著，不閃成骨架（PLAN 5-4）
        assert not find(page, "overviewSkeleton").isVisible()
    wait_until(lambda: tile_value(page, "scoreTile_overall") == "89.0")
    wait_until(lambda: subtitle.property("text") == "Last 30 days · 2 services recorded")


def test_home_failure_shows_an_error_per_card_and_retry_recovers(engine, services, db, monkeypatch,
                                                                 no_network_on_gui_thread):
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 一開始就連不上
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    error = find(page, "overviewError")
    assert error.isVisible() and not find(page, "overviewSkeleton").isVisible()
    assert error.property("title") == "Couldn't load the overview"
    assert error.property("detail") == OFFLINE  # 還沒載入過：不寫「Scores from … are hidden」
    assert "2003" in error.property("technicalDetail")  # 技術細節收在 Details 裡
    assert find(page, "recentError").property("title") == "Couldn't load recent services"
    assert find(page, "trendError").property("title") == "Couldn't load the trend"
    assert home.accountName == ""

    for failures in (2, 3):  # 一直失敗：第 3 次多一行建議（DS-27）
        qml(error, "retry()")
        # 名字也跟著重載、也失敗：等它回來，最後一次 Retry 才不會因為同一個 key 還在跑而略過
        wait_until(lambda: error.property("failureCount") == failures and not services.tasks.isBusy(ACCOUNT_NAME_KEY))
    assert error.property("showsHint")

    monkeypatch.setenv("DB_PORT", port)
    qml(error, "retry()")  # Retry：只重查這一張卡片，還沒載入的帳號名稱一起再載一次
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in (HOME_KEYS[0], ACCOUNT_NAME_KEY)))
    assert not error.isVisible() and home.homeDashboard.overview.state == "empty"
    assert find(page, "trendError").isVisible()  # 其他卡片不受影響：各自重試（PLAN 5-2）
    assert qml(window, "header.userName") == STAFF_NAME


def test_service_tiles_and_the_open_button_lead_to_the_analysis_page(engine, services, db, monkeypatch,
                                                                     no_network_on_gui_thread):
    add_scored_services(db, monkeypatch)
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    tile = find(page, "notAnalyzedTile")
    assert qml(tile, "interactive && Accessible.role === Accessible.Button")
    assert qml(tile, "Accessible.name") == "Not analyzed 1, Review"
    assert qml(tile, "Qt.colorEqual(iconFill, Theme.warningOnLight)")  # 有還沒送出的：橘色的待辦方塊
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(tile))
    assert services.tasks.isBusy(ANALYSIS_KEY)  # launch 沒查分析清單：換頁時在背景查
    wait_until(lambda: qml(window, "currentPage") == "analysis.qml")
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    assert [row["serviceId"] for row in home.serviceModel.notAnalyzedModel.rows()] == ["S20261030120000"]

    page = show_page(window, "homeButton")  # 鍵盤：Tab 到 Completed，按空白鍵
    completed = find(page, "completedTile")
    completed.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    assert qml(completed, "visualFocus") and find(completed, "tileFocusRing").isVisible()  # DS-30
    QTest.keyClick(window, Qt.Key.Key_Space)
    wait_until(lambda: qml(window, "currentPage") == "analysis.qml")

    page = show_page(window, "homeButton")
    button = find(page, "openCompletedButton")  # ↗：分析頁的 Completed
    assert qml(button, "Accessible.name") == "Open in Analysis"
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(button))
    wait_until(lambda: qml(window, "currentPage") == "analysis.qml")


def test_recent_bars_show_both_scores_and_open_the_service(engine, services, db, monkeypatch,
                                                           no_network_on_gui_thread):
    add_scored_services(db, monkeypatch)
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = qml(window, "mainContentLoader.item")
    bars = find(page, "recentBars")
    assert js(bars, "bars.map(bar => bar.label + ' ' + bar.sublabel)") == ["09:00 Oct 30", "10:00 Oct 30"]  # 舊的在左
    groups = rows_of(find(bars, "barList"), "barGroup")
    first = groups[0]
    assert find(first, "valueLabel").property("text") == "88.0"
    assert qml(find(first, "valueBar"), "Qt.colorEqual(color, Theme.successOnLight)")  # 88：Good 的填色
    assert find(first, "compareBar").isVisible()  # 另一方（員工 72）是灰色
    assert qml(first, "Accessible.name") == "S20261030090000, Oct 30 09:00, customer 88.0, staff 72.0"
    assert find(bars, "barLegend").isVisible()

    QTest.mouseMove(window, center_of(first))  # 滑鼠移上去：服務 ID、時間、兩個分數
    tip = find(bars, "barTip")
    wait_until(lambda: tip.property("visible"))
    assert tip.property("text") == "S20261030090000\nOct 30 · 09:00\nCustomer 88.0 · Staff 72.0"

    qml(page, 'overview.side = "staff"')  # 彩色換成員工的分數，灰色是顧客的
    assert js(bars, "bars.map(bar => bar.value)") == [72.0, 91.0]
    first = rows_of(find(bars, "barList"), "barGroup")[0]  # 資料換了，每一組重新建立
    assert find(first, "valueLabel").property("text") == "72.0"

    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(first))
    wait_until(lambda: qml(window, "currentPage") == "server_service_detail_page.qml")  # window.openServiceDetail
    assert qml(window, "mediaModel.serviceId") == "S20261030090000"  # 明細頁打開的就是這一筆


# ── 服務查詢 ─────────────────────────────────────────────────────────────────

def open_search(engine, services):
    """打開 Services：第一次打開會用空白的日期查一次（全部，新的在前），等它查完。"""
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    page = show_page(window, "servicesButton")
    wait_until(lambda: not services.tasks.isBusy(SEARCH_KEY))
    return window, page


def search(page, start, end):
    find(page, "startDateField").setProperty("text", start)
    find(page, "endDateField").setProperty("text", end)
    qml(page, "searchButton.clicked()")


def test_search_checks_dates_before_querying(engine, services, db, no_network_on_gui_thread):
    window, page = open_search(engine, services)
    start, end = find(page, "startDateField"), find(page, "endDateField")
    search(page, "2026/10/30", "2026-10-31")
    assert find(page, "startDateError").property("text") == DATE_HINT  # 欄位下方說明格式（DS-52）
    assert find(page, "startDateError").isVisible() and not find(page, "endDateError").isVisible()
    assert qml(start, "invalid && Qt.colorEqual(background.border.color, Theme.errorOnDark)")
    assert qml(start, "Accessible.description") == DATE_HINT
    assert not services.tasks.isBusy(SEARCH_KEY)  # 格式不對就不查（以前會默默拿掉這個條件）
    wait_until(start.hasActiveFocus)  # 焦點移到有錯的欄位

    search(page, "2026-10-31", "2026-10-30")  # To 早於 From：錯誤在 To 下面，也不查
    assert find(page, "startDateError").property("text") == ""
    assert find(page, "endDateError").property("text") == ORDER_HINT
    assert not services.tasks.isBusy(SEARCH_KEY)
    wait_until(end.hasActiveFocus)
    start.setProperty("text", "2026-10-29")
    qml(start, "textEdited()")  # 改了 From：To 下面「早於 From」的錯誤先拿掉，下一次 Search 再檢查
    assert find(page, "endDateError").property("text") == ""


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
    assert row.property("serviceId") == "S20261030120000"


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
    wait_until(lambda: [row.property("serviceId") for row in rows_of(view, "resultRow")] == ["S20261030120000"])
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
    assert row.property("serviceId") == "S20261030120000"


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


def test_devices_are_probed_only_when_the_record_page_opens(engine, services, db, camera_opens, monkeypatch,
                                                          no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    wait_for_launch(services)
    assert camera_opens == []  # 登入時不開鏡頭（UX-02、UX-13）
    probe, shown = devices.probe_cameras, []

    def probe_cameras():
        # 在探測開始的那一刻看：show_page() 等頁面載入時，慢的電腦可能已經過了 SCAN_DELAY_MS、探測完了
        page = qml(window, "mainContentLoader.item")
        shown.append(find(page, "scanningState").isVisible())
        return probe()

    monkeypatch.setattr(devices, "probe_cameras", probe_cameras)
    page = open_record_page(window)
    assert shown == [True]  # 探測（會卡住畫面）之前，先畫出「Looking for cameras and microphones…」
    assert qml(window, "deviceFinder.status") == "ready"
    assert find(page, "deviceForm").isVisible() and not find(page, "scanningState").isVisible()
    assert (qml(page, "cam0ComboBox.currentIndex"), qml(page, "cam1ComboBox.currentIndex")) == (0, 1)  # 預設就合法
    assert find(page, "sameCameraError").property("text") == ""
    assert find(page, "openRecorderButton").isEnabled()
    assert find_item(page, "devicesCard").property("visible")  # 卡片裡寫找到幾個裝置（PLAN 6-2）
    assert qml(page, "foundText()") == "Found 2 cameras and 1 microphone."
    probes = len(camera_opens)

    show_page(window, "homeButton")
    page = show_page(window, "recordButton")  # 再回到錄影頁：用上次的結果，不再開鏡頭
    assert find(page, "deviceForm").isVisible() and len(camera_opens) == probes


def test_same_cameras_disable_open_recorder_with_a_field_error(engine, services, db, no_network_on_gui_thread):
    home, window = launch_staff(engine, services)
    page = open_record_page(window)
    assert not qml(page, "cam1ComboBox.invalid")
    qml(page, "cam1ComboBox.currentIndex = 0")
    error = find(page, "sameCameraError")
    assert error.property("text") == "This is the customer camera too. Choose the other camera."  # 設計稿的句子
    assert error.isVisible() and qml(page, "cam1ComboBox.invalid")  # 員工鏡頭的框變紅
    assert qml(page, "cam1ComboBox.Accessible.description") == error.property("text")
    assert not find(page, "openRecorderButton").isEnabled()
    qml(page, "cam1ComboBox.currentIndex = 1")
    assert error.property("text") == "" and find(page, "openRecorderButton").isEnabled()


def save_rows(page):
    """Saving 卡片已經建立的列，由上到下（Repeater 建立的項目沒有 QObject parent，從 childItems 找）。"""
    rows, pending = [], [page]
    while pending:
        item = pending.pop(0)
        if item.objectName() == "saveRow":
            rows.append(item)
        pending.extend(item.childItems())
    return sorted(rows, key=lambda row: row.mapToScene(QPointF(0, 0)).y())


def test_saving_card_shows_each_save_status_and_its_buttons(engine, services, db, no_network_on_gui_thread):
    """UD9、T29：每一段存檔一列。上傳中有第幾個檔案、MB、百分比和進度條；存好的有 Analyze；沒存好的有原因和 Retry。"""
    home, window = launch_staff(engine, services)
    page = open_record_page(window)
    card = find(page, "savingCard")
    assert not card.isVisible()  # 還沒有任何存檔：不顯示這張卡片
    saver = FakeSaver(every_save_status())
    home.deviceFinder.saves.attach(saver)
    assert card.isVisible()

    def shown(row, name):
        return find_item(row, name).isVisible()

    rows = save_rows(page)
    assert [(row.property("serviceId"), find_item(row, "saveStatus").property("text")) for row in rows] == [
        ("S20261101150000", "Finishing the recording…"),
        ("S20261101142000", "Uploading · file 2 of 3 · attempt 2"),
        ("S20261101141000", "Saving to the database…"),
        ("S20261101140500", "Saved at 14:12 · not analyzed yet"),
        ("S20261101130000", "Saved at 13:07 · sent for analysis"),
        ("S20261031170500", "Wasn't saved"),
        ("S20261031160000", "Can't be saved"),
    ]
    finishing, uploading, writing, saved, sent, failed, lost = rows
    assert [shown(row, "saveProgress") for row in rows] == [True, True, True, False, False, False, False]
    assert find_item(finishing, "saveProgress").property("value") == 0
    assert find_item(uploading, "saveProgress").property("value") == pytest.approx(58.6 / 124.0)
    assert find_item(writing, "saveProgress").property("value") == 1
    assert find_item(uploading, "saveAmount").property("text") == "58.6 / 124.0 MB · 47%"  # 設計稿的數字
    assert [shown(row, "saveAmount") for row in rows] == [False, True, False, False, False, False, False]
    assert [shown(row, "saveAnalyze") for row in rows] == [False, False, False, True, False, False, False]
    assert [shown(row, "saveRetry") for row in rows] == [False, False, False, False, False, True, False]
    assert find_item(failed, "saveReason").property("text") == (
        "The upload didn't finish. The recording is still on this computer.")
    assert shown(lost, "saveReason") and not shown(saved, "saveReason")
    assert qml(find_item(saved, "saveAnalyze"), "Accessible.name") == "Analyze S20261101140500"

    qml(find_item(saved, "saveAnalyze"), "clicked()")  # 按鈕交給 deviceFinder.saves，再到 RecordingSaver
    qml(find_item(failed, "saveRetry"), "clicked()")
    assert saver.calls == [("analyze", "S20261101140500"), ("retry", "S20261031170500")]


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
        raise sounddevice.PortAudioError("Unanticipated host error (fake)", -9999)

    microphone.query_hostapis = broken
    home, window = launch_staff(engine, services)
    page = open_record_page(window)
    error = find(page, "scanError")
    assert error.isVisible() and error.property("title") == "Couldn't check cameras and microphones"
    assert error.property("retryText") == "Scan Again"
    del microphone.query_hostapis
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
