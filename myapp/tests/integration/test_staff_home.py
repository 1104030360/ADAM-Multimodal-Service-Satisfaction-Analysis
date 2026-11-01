"""服務人員端的首頁統計與服務搜尋（步驟 3-11、4-4）：F2 起在 db lane 查詢，結果放在屬性和清單模型裡。"""
from itertools import count

import pytest

from myapp.init.server_home_init import SEARCH_KEY, ServiceManager, ServiceScoreManager
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, table_row, wait_until

pytestmark = pytest.mark.integration

_keys = count(1)


def on_db_lane(services, fn):
    """在 db lane 上執行 fn(db) 並等它做完（測試準備用；db lane 的連線只能在那條執行緒用）。"""
    done = []
    services.tasks.submit("db", f"test:{next(_keys)}", lambda token, report: fn(services.db),
                          on_ok=lambda result: done.append(result))
    wait_until(lambda: done)
    return done[0]


def freeze_db_clock(services, moment):
    """讓 db lane 那條連線的 NOW()、CURDATE() 停在 moment（SET TIMESTAMP 只影響這條連線）。"""
    def freeze(db):
        with db.cursor(dictionary=True) as cursor:
            cursor.execute("SET TIMESTAMP = UNIX_TIMESTAMP(%s)", (moment,))
    on_db_lane(services, freeze)


def loaded(manager):
    manager.refresh()
    wait_until(lambda: not manager.busy)
    return manager


def test_dashboard_counts_and_averages_own_completed_services(services, db, no_network_on_gui_thread):
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

    manager = ServiceScoreManager(services, STAFF_ID)
    assert (manager.status, manager.daily["count"], manager.daily["customer"]["total"]) == ("loading", 0, None)
    freeze_db_clock(services, "2026-10-30 18:00:00")  # 「今天」固定是 10/30
    loaded(manager)

    assert manager.status == "ready"
    daily, weekly = manager.daily, manager.weekly
    assert (daily["count"], weekly["count"]) == (2, 3)
    assert type(daily["count"]) is int  # 首頁顯示「2」，不是「2.0」
    assert daily["customer"] == {"total": 70.0, "text": 60.0, "audio": 70.0, "facial": 70.0}
    assert daily["staff"] == {"total": 80.0, "text": 80.0, "audio": 80.0, "facial": 80.0}
    assert weekly["customer"]["total"] == 80.0  # (80 + 60 + 100) / 3


def test_dashboard_without_data_has_no_scores_rather_than_zero(services, db, no_network_on_gui_thread):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80, server=-1, customer_facial=-1)  # 服務人員的分數都沒有
    manager = ServiceScoreManager(services, STAFF_ID)
    freeze_db_clock(services, "2026-11-02 09:00:00")  # 3 天後：今天還沒有完成的服務
    loaded(manager)
    assert manager.daily == {"count": 0, "customer": dict.fromkeys(("total", "text", "audio", "facial")),
                             "staff": dict.fromkeys(("total", "text", "audio", "facial"))}
    assert manager.weekly["count"] == 1
    assert manager.weekly["customer"] == {"total": 80.0, "text": 80.0, "audio": 80.0, "facial": None}
    assert set(manager.weekly["staff"].values()) == {None}  # UX-05：沒有資料是 None，畫面顯示「—」，不是 0


def test_search_covers_whole_end_day_and_only_own_completed_services(services, db, no_network_on_gui_thread):
    db.add_service("S20261030235900", "2026-10-30 23:59:00")  # 結束日的最後一分鐘：要找到
    db.add_service("S20261031000000", "2026-10-31 00:00:00")  # 隔天
    db.add_service("S20261029235900", "2026-10-29 23:59:00")  # 開始日的前一天
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=2)  # 還在分析
    db.add_service("S20261030130000", "2026-10-30 13:00:00", account_id=ADMIN_ID)  # 別人的
    manager = ServiceManager(services, STAFF_ID)
    assert manager.search("2026-10-30", "2026-10-30")
    assert manager.busy and services.tasks.isBusy(SEARCH_KEY)  # 在背景查詢
    wait_until(lambda: not manager.busy)
    assert manager.status == "ready"
    assert manager.resultsModel.rowCount() == 1
    # QML 的表格用角色名稱讀每一格
    assert table_row(manager.resultsModel, 0) == {
        "service_id": "S20261030235900", "name": STAFF_NAME, "start_datetime": "2026-10-30 23:59:00"}


def test_search_without_dates_lists_every_completed_service_newest_first(services, db, no_network_on_gui_thread):
    db.add_service("S20261028100000", "2026-10-28 10:00:00")
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_service("S20261029100000", "2026-10-29 10:00:00", status=1)
    manager = ServiceManager(services, STAFF_ID)
    assert manager.search(" ", "")
    wait_until(lambda: not manager.busy)
    assert [row["service_id"] for row in manager.resultsModel.rows()] == ["S20261030100000", "S20261028100000"]
    assert (manager.startDate, manager.endDate) == ("", "")


@pytest.mark.parametrize("start, end, invalid", [
    ("2026/10/30", "", (True, False)),
    ("", "2026-02-30", (False, True)),  # 格式對但沒有這一天
    ("tomorrow", "31-10-2026", (True, True)),
], ids=["slashes", "no-such-day", "both"])
def test_search_with_a_bad_date_is_not_sent(services, start, end, invalid):
    manager = ServiceManager(services, STAFF_ID)
    assert manager.search(start, end) is False
    assert (manager.startDateInvalid, manager.endDateInvalid) == invalid
    assert not manager.busy and not services.tasks.isBusy(SEARCH_KEY)
    assert manager.status == "idle"
