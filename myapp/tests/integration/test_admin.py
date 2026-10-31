"""管理員端（步驟 2-4、4-1～4-5）：首頁、趨勢、排名、搜尋、帳號管理。"""
from datetime import date, datetime

import pytest

from myapp.database import DatabaseConnector
from myapp.init import admin_home_init
from myapp.init.admin_home_init import AdminHome, ServiceManager, ServiceScoreManager, TrendDataManager
from tests.fakes import ticking_clock
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, freeze_db_clock, qml, table_row

pytestmark = pytest.mark.integration


def collect_trend(manager):
    """回傳 {日: 服務人員分數}、{日: 顧客分數}，load_trends_for_month 之後就會填好。"""
    server, customer = {}, {}
    manager.serverPointAdded.connect(lambda day, score: server.__setitem__(int(day), score))
    manager.customerPointAdded.connect(lambda day, score: customer.__setitem__(int(day), score))
    return server, customer


def test_trend_covers_whole_month_and_skips_missing_scores(db):
    db.add_service("S20261001100000", "2026-10-01 10:00:00")
    db.add_scores("S20261001100000", customer=80, server=70)
    db.add_service("S20261031233000", "2026-10-31 23:30:00")  # 月底最後一天的晚上
    db.add_scores("S20261031233000", customer=-1, server=90)  # 顧客分數沒有資料
    db.add_service("S20261031120000", "2026-10-31 12:00:00", status=2)  # 分數寫好了、但還沒完成：不算
    db.add_scores("S20261031120000", customer=0, server=0)
    db.add_service("S20261101000000", "2026-11-01 00:00:00")  # 下個月：不算
    db.add_scores("S20261101000000", customer=10, server=10)

    manager = TrendDataManager(DatabaseConnector())
    server, customer = collect_trend(manager)
    manager.load_trends_for_month(10, 2026)
    assert sorted(server) == list(range(1, 32))  # 每一天都有一個點，沒有資料的日子是 0
    assert (server[1], customer[1]) == (70.0, 80.0)
    assert (server[31], customer[31]) == (90.0, 0)
    assert server[15] == 0


def test_add_button_loads_home_data_for_current_month(db, monkeypatch):
    db.add_service("S20261015100000", "2026-10-15 10:00:00")
    db.add_scores("S20261015100000", customer=80, server=-1)
    monkeypatch.setattr(admin_home_init, "datetime", ticking_clock(start=datetime(2026, 10, 20)))
    home = AdminHome(ADMIN_ID)
    server, customer = collect_trend(home.trendDataManager)
    ranking = []
    home.rankingModel.updateRanking.connect(lambda *lists: ranking.extend(lists))
    home.onLoadHome()  # 側邊欄「Add」按鈕呼叫的；之前會丟 AttributeError
    assert customer[15] == 80.0  # 這個月（2026 年 10 月）的趨勢
    assert ranking[0] == [{"name": STAFF_NAME, "score": 80.0}]  # 顧客分數前 5 名


def test_admin_dashboard_averages_all_staff_and_skips_missing_scores(db):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80, server=70, customer_text=-1)
    db.add_service("S20261030100000", "2026-10-30 10:00:00", account_id=ADMIN_ID)  # 所有人的服務都算
    db.add_scores("S20261030100000", customer=60, server=90)
    db.add_service("S20261027100000", "2026-10-27 10:00:00")  # 3 天前：只算進本週
    db.add_scores("S20261027100000", customer=100, server=100)
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2)  # 還在分析：不算
    db.add_scores("S20261030110000", customer=0, server=0)

    manager = ServiceScoreManager(DatabaseConnector())
    freeze_db_clock(manager.db_connector, "2026-10-30 18:00:00")
    received = {}
    manager.updateDailyServiceCount.connect(lambda count: received.update(daily_count=count))
    manager.updateWeeklyServiceCount.connect(lambda count: received.update(weekly_count=count))
    manager.updateDailyScores.connect(lambda customer, server: received.update(daily_customer=customer))
    manager.load_scores()
    assert (received["daily_count"], received["weekly_count"]) == (2, 3)
    assert received["daily_customer"] == {"total": 70.0, "text": 60.0, "audio": 70.0, "facial": 70.0}


def test_search_filters_by_staff_name_and_covers_whole_end_day(db):
    db.add_account("00000000-0000-0000-0000-000000000003", "wang", "王小明")
    db.add_service("S20261031235900", "2026-10-31 23:59:00")  # 結束日的最後一分鐘
    db.add_service("S20261031100000", "2026-10-31 10:00:00", account_id="00000000-0000-0000-0000-000000000003")
    db.add_service("S20261101000000", "2026-11-01 00:00:00")
    manager = ServiceManager(DatabaseConnector())
    manager.load_names()
    assert sorted(manager.names) == sorted([STAFF_NAME, "王小明"])  # 下拉選單只列服務人員（等級 1）
    manager.search_services(STAFF_NAME, "2026-10-31", "2026-10-31")
    assert [row["service_id"] for row in manager.results] == ["S20261031235900"]
    assert table_row(manager.resultsModel, 0) == {
        "service_id": "S20261031235900", "name": STAFF_NAME, "start_datetime": "2026-10-31 23:59:00"}


@pytest.fixture
def account_results(db):
    """管理員（等級 5）的 AccountManager，以及它送出的 (成功與否, 訊息)。"""
    manager = AdminHome(ADMIN_ID).accountManager
    results = []
    manager.accountAdded.connect(lambda ok, message: results.append((ok, message)))
    manager.accountUpdated.connect(lambda ok, message: results.append((ok, message)))
    return manager, results


def test_new_account_is_stored_hashed_and_can_log_in(db, account_results):
    manager, results = account_results
    manager.add_account("new_staff", "S3cret-pass", "新同事", "1", "服務人員")
    assert results == [(True, "Account successfully added")]
    stored = db.rows("SELECT account_password FROM tbl_account WHERE account_username = 'new_staff'")[0][0]
    assert stored.startswith("pbkdf2_sha256$")  # 不存明碼
    login = DatabaseConnector().check_login("new_staff", "S3cret-pass")
    assert login["success"] and login["account_level"] == "1"


def test_updated_password_replaces_old_one(db, account_results):
    manager, results = account_results
    manager.update_account(STAFF_ID, "placeholder_staff", "New-pass-2026", STAFF_NAME, "1", "服務人員")
    assert results == [(True, "Account successfully updated")]
    connector = DatabaseConnector()
    assert connector.check_login("placeholder_staff", "New-pass-2026")["success"]
    assert not connector.check_login("placeholder_staff", "CHANGE-ME-staff-0000")["success"]


def test_duplicate_username_is_reported(db, account_results):
    manager, results = account_results
    manager.add_account("placeholder_staff", "pw", "重複", "1", "服務人員")
    ok, message = results[0]
    assert not ok and message.startswith("Failed to add account")


def test_trend_page_starts_at_current_month_and_redraws_without_stacking(engine, db):
    admin_home_init.launch(engine, ADMIN_ID)
    page = qml(engine.rootObjects()[0], "mainContentLoader.item")  # admin_home_page.qml
    today = date.today()  # QML 用 JavaScript 的 new Date()，也就是電腦現在的日期
    assert qml(page, "yearSelector.currentText") == str(today.year)
    assert qml(page, "monthSelector.currentIndex") == today.month - 1
    assert qml(page, "serverLineSeries.count") == 31  # 頁面建立完成就畫了一次
    qml(page, "monthSelector.currentIndex = 0; monthSelector.activated(0)")  # 使用者改選一月
    assert qml(page, "serverLineSeries.count") == 31  # 重畫，不是疊上去
