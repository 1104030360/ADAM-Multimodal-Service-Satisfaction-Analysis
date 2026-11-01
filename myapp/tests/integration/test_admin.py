"""管理員端（步驟 2-4、4-1～4-5；前端重建 F2；新版介面 U1）：首頁的三張卡片、搜尋、帳號管理接真的 MySQL 8.4。

資料庫的工作都在 db lane（services fixture 的 TaskRunner＋DbLane），no_network_on_gui_thread 確認 GUI 執行緒不碰網路。
結果放在屬性裡，等待一律用 wait_until。畫面上的回饋（toast、欄位錯誤、空狀態）在 test_admin_feedback.py。
"""
from datetime import date

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from myapp import home_dashboard
from myapp.home_dashboard import HomeDashboard
from myapp.init import admin_home_init
from myapp.init.admin_home_init import AdminHome, ServiceManager
from myapp.passwords import verify_password
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, table_row, wait_until

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def gui_thread_stays_off_the_network(db, no_network_on_gui_thread):
    """db fixture 自己的連線（放測試資料用）開好之後，才開始禁止 GUI 執行緒碰網路。"""


def loaded(section):
    """等這個區塊載入完：不是 loading（或還沒搜尋過的 idle），也不在重新整理。"""
    wait_until(lambda: section.state not in ("loading", "idle") and not section.refreshing)


# ---------------------------------------------------------------- 新版首頁（U1，T28）：全部員工


def admin_dashboard(services, monkeypatch, day=date(2026, 10, 30)):
    """管理員端的 homeDashboard（全部員工）。期間從這台電腦的日期往前算：測試把「今天」換成 day。"""
    monkeypatch.setattr(home_dashboard, "today", lambda: day)
    return HomeDashboard(services, None, "admin:home")


def settle(services, dashboard):
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in dashboard.keys))


def staff_member(db, number, name):
    account_id = f"00000000-0000-0000-0000-{number:012d}"
    db.add_account(account_id, f"staff{number}", name)
    return account_id


def test_admin_overview_and_trend_cover_every_staff_member(db, services, monkeypatch):
    other = staff_member(db, 3, "王小明")
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80, server=70)
    db.add_service("S20261030100000", "2026-10-30 10:00:00", account_id=other)
    db.add_scores("S20261030100000", customer=60, server=-1)
    db.add_service("S20261029100000", "2026-10-29 10:00:00", status=1, account_id=other)
    db.add_service("S20261028100000", "2026-10-28 10:00:00", status=2)

    dashboard = admin_dashboard(services, monkeypatch)
    assert dashboard.recent is None and dashboard.keys == (
        "admin:home:overview", "admin:home:ranking", "admin:home:trend")
    dashboard.refresh()
    settle(services, dashboard)
    today, week = dashboard.overview.periods["today"], dashboard.overview.periods["7d"]
    assert (today["count"], today["completed"], today["staffCount"]) == (2, 2, 2)  # 「2 services · 2 staff」
    assert today["customer"]["overall"]["value"] == 70.0 and today["staff"]["overall"]["value"] == 70.0
    assert (week["count"], week["analyzing"], week["notAnalyzed"], week["staffCount"]) == (4, 1, 1, 2)
    trend = dashboard.trend
    assert trend.days[-1] == "2026-10-30" and trend.series["customer"]["overall"][-1] == 70.0
    assert trend.series["customer"]["overall"][-2] is None  # 10/29 只有還沒分析的服務


def test_staff_ranking_lists_everyone_with_analyzed_services_in_30_days(db, services, monkeypatch):
    names = ["Ava", "Ben", "Cai", "Dee", "Eli", "Fay", "Gus"]  # 7 個人：以前只留前 5、後 5
    for number, name in enumerate(names, start=3):
        account = staff_member(db, number, name)
        for day in (1, 2):
            service_id = f"S202610{number:02d}{day:02d}0000"
            db.add_service(service_id, f"2026-10-{number:02d} {day:02d}:00:00", account_id=account)
            db.add_scores(service_id, customer=50 + number * 5, server=-1 if name == "Cai" else 100 - number * 5)
    early = staff_member(db, 20, "Old")
    db.add_service("S20260930230000", "2026-09-30 23:00:00", account_id=early)  # 30 天前：不算
    db.add_scores("S20260930230000", customer=99, server=99)
    waiting = staff_member(db, 21, "Wai")
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2, account_id=waiting)  # 還在分析：不算

    dashboard = admin_dashboard(services, monkeypatch)
    ranking = dashboard.ranking
    ranking.refresh()
    settle(services, dashboard)
    assert ranking.state == "ready"
    assert [row["name"] for row in ranking.byCustomer] == ["Gus", "Fay", "Eli", "Dee", "Cai", "Ben", "Ava"]
    assert [row["name"] for row in ranking.byStaff] == ["Ava", "Ben", "Dee", "Eli", "Fay", "Gus", "Cai"]  # 沒有分數的在最後
    assert ranking.byCustomer[0] == {"accountId": "00000000-0000-0000-0000-000000000009", "name": "Gus", "label": "Gus",
                                     "count": 2,
                                     "customer": 95.0, "staff": 55.0, "customerBand": "good",
                                     "staffBand": "needsImprovement"}


def test_admin_dashboard_is_three_round_trips(db, db_proxy, services, monkeypatch):
    dashboard = admin_dashboard(services, monkeypatch)

    def warm_up(token, report):  # 連線、建 dictionary cursor 不算在預算裡（T16）
        with services.db.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT 1 AS one")
            return cursor.fetchall()

    services.tasks.submit("db", "test:warm-up", warm_up)
    wait_until(lambda: not services.tasks.isBusy("test:warm-up"))
    db_proxy.reset()
    dashboard.refresh()
    settle(services, dashboard)
    assert db_proxy.snapshot()["rt"] == 3
    assert [section.state for section in dashboard.sections] == ["empty", "empty", "empty"]


def test_search_filters_by_staff_name_and_covers_whole_end_day(db, services):
    db.add_account("00000000-0000-0000-0000-000000000003", "wang", "王小明")
    db.add_service("S20261031235900", "2026-10-31 23:59:00")  # 結束日的最後一分鐘
    db.add_service("S20261031100000", "2026-10-31 10:00:00", account_id="00000000-0000-0000-0000-000000000003")
    db.add_service("S20261101000000", "2026-11-01 00:00:00")
    manager = ServiceManager(services)
    manager.loadNames()
    wait_until(lambda: manager.namesState == "ready")
    assert sorted(manager.names) == sorted([STAFF_NAME, "王小明"])  # 下拉選單只列服務人員（等級 1）
    staff_id = manager.staffIds[manager.names.index(STAFF_NAME)]  # 用 account_id 篩選（名字可以改、可以重複，T36）
    assert manager.search(staff_id, "2026-10-31", "2026-10-31")
    loaded(manager)
    assert [row["serviceId"] for row in manager.listModel.rows()] == ["S20261031235900"]
    row = table_row(manager.listModel, 0)
    assert (row["serviceId"], row["staffName"], row["startTime"]) == (
        "S20261031235900", STAFF_NAME, "2026-10-31 23:59:00")


@pytest.fixture
def account_results(db, services):
    """管理員（等級 5）的 AccountManager，以及它送出的 (成功與否, 訊息)。"""
    home = AdminHome(ADMIN_ID, services)  # AccountManager 是它的子物件：留著 home，子物件才不會跟著被刪掉
    home.profile.load()
    wait_until(lambda: home.profile.state == "ready")
    manager = home.accountManager
    assert manager.currentLevel == 5
    results = []
    manager.accountAdded.connect(lambda ok, message: results.append((ok, message)))
    manager.accountUpdated.connect(lambda ok, message: results.append((ok, message)))
    yield manager, results
    del home


def test_new_account_is_stored_hashed_and_can_log_in(db, account_results):
    manager, results = account_results
    assert manager.addAccount("new_staff", "S3cret-pass", "新同事", "1", "服務人員")
    wait_until(lambda: results)
    assert results == [(True, "Account added")]
    stored, level, job_title = db.rows("SELECT account_password, account_level, account_occupation_name "
                                       "FROM tbl_account WHERE account_username = 'new_staff'")[0]
    assert stored.startswith("pbkdf2_sha256$")  # 不存明碼
    assert verify_password("S3cret-pass", stored)  # 登入時 sp_get_login 取回雜湊，用同一個函式比對
    assert (level, job_title) == ("1", "服務人員")


def test_updated_password_replaces_old_one(db, account_results):
    manager, results = account_results
    assert manager.updateAccount(STAFF_ID, "placeholder_staff", "New-pass-2026", STAFF_NAME, "1", "服務人員")
    wait_until(lambda: results)
    assert results == [(True, "Account updated")]
    stored = db.rows("SELECT account_password FROM tbl_account WHERE account_id = %s", (STAFF_ID,))[0][0]
    assert verify_password("New-pass-2026", stored)
    assert not verify_password("CHANGE-ME-staff-0000", stored)


def test_duplicate_username_is_reported_on_the_username_field(db, account_results):
    manager, results = account_results
    manager.addAccount("placeholder_staff", "pw", "重複", "1", "服務人員")
    wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert manager.usernameError == message == "Username placeholder_staff is already taken. Choose another one."
    assert db.rows("SELECT COUNT(*) FROM tbl_account")[0][0] == 2


def test_opening_add_account_does_not_reload_home_data(engine, db, services, db_proxy):
    home = admin_home_init.launch(engine, ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    wait_until(window.isExposed)
    for section in (home.profile, *home.homeDashboard.sections):
        loaded(section)
    db_proxy.reset()
    add_button = qml(window, "addAccountButton")  # 側邊欄的 Add Account
    center = add_button.mapToScene(QPointF(add_button.width() / 2, add_button.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    wait_until(lambda: qml(window, "mainContentLoader.source.toString()").endswith("admin_add_page.qml"))
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    assert db_proxy.snapshot()["rt"] == 0  # 以前會把首頁的分數、排名、趨勢再查一次（UX-32）
