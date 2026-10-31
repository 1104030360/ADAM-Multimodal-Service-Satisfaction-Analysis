"""服務人員端的首頁統計與服務搜尋（步驟 3-11、4-4）。"""
import pytest

from myapp.database import DatabaseConnector
from myapp.init.server_home_init import ServiceManager, ServiceScoreManager
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, freeze_db_clock, table_row

pytestmark = pytest.mark.integration


def test_dashboard_counts_and_averages_own_completed_services(db):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80, server=70, customer_text=-1)  # -1：這一項沒有資料
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_scores("S20261030100000", customer=60, server=90)
    db.add_service("S20261027100000", "2026-10-27 10:00:00")  # 3 天前：只算進本週
    db.add_scores("S20261027100000", customer=100, server=100)
    db.add_service("S20261001100000", "2026-10-01 10:00:00")  # 一個月前：都不算
    db.add_scores("S20261001100000", customer=0, server=0)
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2)  # 還在分析：不算
    db.add_scores("S20261030110000", customer=0, server=0)
    db.add_service("S20261030120000", "2026-10-30 12:00:00", account_id=ADMIN_ID)  # 別人的：不算
    db.add_scores("S20261030120000", customer=0, server=0)

    manager = ServiceScoreManager(STAFF_ID)
    freeze_db_clock(manager.db_connector, "2026-10-30 18:00:00")  # 「今天」固定是 10/30
    received = {}
    manager.updateDailyServiceCount.connect(lambda count: received.update(daily_count=count))
    manager.updateWeeklyServiceCount.connect(lambda count: received.update(weekly_count=count))
    manager.updateDailyScores.connect(lambda customer, server: received.update(daily=(customer, server)))
    manager.updateWeeklyScores.connect(lambda customer, server: received.update(weekly=(customer, server)))
    manager.load_data()

    assert (received["daily_count"], received["weekly_count"]) == (2, 3)
    assert type(received["daily_count"]) is int  # 首頁顯示「2 件」，不是「2.0 件」
    daily_customer, daily_server = received["daily"]
    assert daily_customer == {"total": 70.0, "text": 60.0, "audio": 70.0, "facial": 70.0}
    assert daily_server == {"total": 80.0, "text": 80.0, "audio": 80.0, "facial": 80.0}
    weekly_customer, _ = received["weekly"]
    assert weekly_customer["total"] == 80.0  # (80 + 60 + 100) / 3


def test_search_covers_whole_end_day_and_only_own_completed_services(db):
    db.add_service("S20261030235900", "2026-10-30 23:59:00")  # 結束日的最後一分鐘：要找到
    db.add_service("S20261031000000", "2026-10-31 00:00:00")  # 隔天
    db.add_service("S20261029235900", "2026-10-29 23:59:00")  # 開始日的前一天
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=2)  # 還在分析
    db.add_service("S20261030130000", "2026-10-30 13:00:00", account_id=ADMIN_ID)  # 別人的
    manager = ServiceManager(DatabaseConnector(), STAFF_ID)
    manager.search_services("2026-10-30", "2026-10-30")
    assert [row["service_id"] for row in manager.results] == ["S20261030235900"]
    # QML 的表格用角色名稱讀每一格
    assert table_row(manager.resultsModel, 0) == {
        "service_id": "S20261030235900", "name": STAFF_NAME, "start_datetime": "2026-10-30 23:59:00"}
