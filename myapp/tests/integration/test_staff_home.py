"""服務人員端的首頁與服務搜尋（步驟 3-11、4-4）：F2 起在 db lane 查詢，結果放在屬性和清單模型裡。

新版首頁（U1，T28）的 homeDashboard：Overview 的三個期間、Recent Performance、Score Trend 接真的 MySQL，
各 1 個往返；launch() 不再查分析清單（往返預算）。純函式和卡片的狀態在 tests/unit/test_home_dashboard.py。
"""
import threading
from contextlib import contextmanager
from datetime import date
from itertools import count

import pytest

from myapp import home_dashboard
from myapp.home_dashboard import HomeDashboard
from myapp.init import server_home_init
from myapp.init.server_home_init import ANALYSIS_KEY, HOME_KEYS, LAUNCH_KEYS, SEARCH_KEY, ServiceManager
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, table_row, wait_until

pytestmark = pytest.mark.integration

_keys = count(1)


def on_db_lane(services, fn):
    """在 db lane 上執行 fn(db) 並等它做完（測試準備用；db lane 的連線只能在那條執行緒用）。"""
    done = []
    services.tasks.submit("db", f"test:{next(_keys)}", lambda token, report: fn(services.db),
                          on_ok=lambda result: done.append(result))
    wait_until(lambda: done)
    return done[0]


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


# ---------------------------------------------------------------- 新版首頁（U1，T28）


@pytest.fixture
def frozen_today(monkeypatch):
    """首頁的期間從這台電腦的日期往前算（home_dashboard.today()），不是資料庫的 CURDATE()：測試直接換掉它。"""
    def freeze(day):
        monkeypatch.setattr(home_dashboard, "today", lambda: day)
    return freeze


def settle(services, dashboard):
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in dashboard.keys))


def refreshed(services, dashboard):
    dashboard.refresh()
    settle(services, dashboard)
    return dashboard


def staff_dashboard(services):
    return HomeDashboard(services, STAFF_ID, server_home_init.HOME_KEY_PREFIX)


@contextmanager
def db_lane_held(services):
    """db lane 卡在一個等待中的工作上：期間送出的查詢都排在後面。離開 with 才放行。"""
    gate = threading.Event()
    key = f"test:hold:{next(_keys)}"
    services.tasks.submit("db", key, lambda token, report: gate.wait(10))
    try:
        yield
    finally:
        gate.set()
        wait_until(lambda: not services.tasks.isBusy(key))


def warm_up(lane):
    """先連上、建好 dictionary cursor：連線和建 cursor 不算在往返預算裡（T16）。"""
    with lane.cursor(dictionary=True) as cursor:
        cursor.execute("SELECT 1 AS one")
        return cursor.fetchall()


def add(db, service_id, start, status=3, customer=None, server=None, account_id=STAFF_ID, **columns):
    db.add_service(service_id, start, status=status, account_id=account_id)
    if customer is not None:
        db.add_scores(service_id, customer=customer, server=server, **columns)


def test_overview_splits_three_periods_and_compares_each_with_the_one_before(services, db, frozen_today,
                                                                             no_network_on_gui_thread):
    frozen_today(date(2026, 10, 30))
    add(db, "S20261030090000", "2026-10-30 09:00:00", customer=80, server=70, customer_text=-1)
    add(db, "S20261030100000", "2026-10-30 10:00:00", customer=60, server=90)
    add(db, "S20261030110000", "2026-10-30 11:00:00", status=2, customer=0, server=0)  # 分析中：算筆數，不算分數
    add(db, "S20261030120000", "2026-10-30 12:00:00", status=1)
    add(db, "S20261029100000", "2026-10-29 10:00:00", customer=50, server=50)  # 昨天：Today 的上一期
    add(db, "S20261020100000", "2026-10-20 10:00:00", customer=100, server=100)  # Last 7 days 的上一期
    add(db, "S20260915100000", "2026-09-15 10:00:00", customer=50, server=50)  # Last 30 days 的上一期
    add(db, "S20260815100000", "2026-08-15 10:00:00", customer=0, server=0)  # 60 天前：都不算
    add(db, "S20261030130000", "2026-10-30 13:00:00", customer=0, server=0, account_id=ADMIN_ID)  # 別人的

    dashboard = staff_dashboard(services)
    overview = dashboard.overview
    assert overview.state == "loading" and overview.periods["30d"]["count"] == 0  # 建構時不查
    overview.refresh()
    settle(services, dashboard)
    assert overview.state == "ready"
    today, week, month = (overview.periods[period] for period in ("today", "7d", "30d"))

    assert (today["count"], today["completed"], today["analyzing"], today["notAnalyzed"], today["staffCount"]) == (
        4, 2, 1, 1, 1)
    assert today["customer"]["overall"] == {"value": 70.0, "previous": 50.0, "delta": 40.0, "band": "fair"}
    assert today["customer"]["text"] == {"value": 60.0, "previous": 50.0, "delta": 20.0,
                                         "band": "needsImprovement"}  # -1 不算進平均
    assert today["staff"]["overall"] == {"value": 80.0, "previous": 50.0, "delta": 60.0, "band": "fair"}
    assert (today["start"], today["previousStart"]) == ("2026-10-30", "2026-10-29")

    assert (week["count"], week["completed"], week["notAnalyzed"]) == (5, 3, 1)
    assert week["customer"]["overall"] == {"value": 63.3, "previous": 100.0, "delta": -36.7,
                                           "band": "needsImprovement"}  # (80 + 60 + 50) / 3
    assert (month["count"], month["completed"], month["start"], month["previousStart"]) == (
        6, 4, "2026-10-01", "2026-09-01")
    assert month["customer"]["overall"] == {"value": 72.5, "previous": 50.0, "delta": 45.0, "band": "fair"}


def test_overview_without_services_in_30_days_is_empty_and_has_no_scores(services, db, frozen_today,
                                                                         no_network_on_gui_thread):
    frozen_today(date(2026, 10, 30))
    add(db, "S20260920100000", "2026-09-20 10:00:00", customer=80, server=80)  # 只有上一期有
    dashboard = staff_dashboard(services)
    dashboard.overview.refresh()
    settle(services, dashboard)
    month = dashboard.overview.periods["30d"]
    assert dashboard.overview.state == "empty"  # 設計稿 States 的「No services in the last 30 days」
    assert month["count"] == 0
    assert month["customer"]["overall"] == {"value": None, "previous": 80.0, "delta": None, "band": ""}


def test_recent_performance_is_the_last_eight_analyzed_services_oldest_first(services, db,
                                                                             no_network_on_gui_thread):
    for hour in range(8, 18):  # 10 筆分析完成的：只取最新的 8 筆
        add(db, f"S20261031{hour:02d}0000", f"2026-10-31 {hour:02d}:00:00", customer=hour * 5,
            server=-1 if hour == 17 else 70)
    add(db, "S20261031180000", "2026-10-31 18:00:00", status=2)  # 還在分析
    add(db, "S20261031190000", "2026-10-31 19:00:00", customer=99, server=99, account_id=ADMIN_ID)  # 別人的
    dashboard = staff_dashboard(services)
    dashboard.recent.refresh()
    settle(services, dashboard)
    rows = dashboard.recent.rows
    assert [row["serviceId"] for row in rows] == [f"S20261031{hour:02d}0000" for hour in range(10, 18)]  # 舊的在左
    assert rows[-1] == {"serviceId": "S20261031170000", "start": "2026-10-31 17:00:00", "dayLabel": "Oct 31",
                        "timeLabel": "17:00", "customer": 85.0, "staff": None, "customerBand": "good",
                        "staffBand": ""}  # -1 是沒有資料，不是 -1 分
    assert (rows[0]["customer"], rows[0]["customerBand"]) == (50.0, "needsImprovement")
    assert dashboard.recent.state == "ready"


def test_score_trend_has_fourteen_days_and_null_for_days_without_services(services, db, frozen_today,
                                                                          no_network_on_gui_thread):
    frozen_today(date(2026, 11, 1))
    add(db, "S20261018235900", "2026-10-18 23:59:00", customer=10, server=10)  # 第 15 天前：不算
    add(db, "S20261019000000", "2026-10-19 00:00:00", customer=80, server=70)  # 第一天的 00:00
    add(db, "S20261019120000", "2026-10-19 12:00:00", customer=60, server=-1)  # 同一天平均；-1 不算
    add(db, "S20261025100000", "2026-10-25 10:00:00", customer=0, server=0, account_id=ADMIN_ID)  # 別人的
    add(db, "S20261027100000", "2026-10-27 10:00:00", status=2, customer=0, server=0)  # 還在分析
    add(db, "S20261101235900", "2026-11-01 23:59:00", customer=90, server=85)  # 今天的最後一分鐘
    add(db, "S20261102000000", "2026-11-02 00:00:00", customer=0, server=0)  # 明天：不算
    dashboard = staff_dashboard(services)
    trend = dashboard.trend
    trend.refresh()
    settle(services, dashboard)
    assert trend.state == "ready"
    assert (len(trend.days), trend.days[0], trend.days[-1]) == (14, "2026-10-19", "2026-11-01")
    assert (trend.labels[0], trend.labels[-1]) == ("Oct 19", "Nov 1")
    assert trend.series["customer"]["overall"] == [70.0] + [None] * 12 + [90.0]  # 沒有服務的日子是 null，不是 0
    assert trend.series["staff"]["overall"] == [70.0] + [None] * 12 + [85.0]
    assert trend.runs("customer", "overall") == [[{"x": 0, "y": 70.0}], [{"x": 13, "y": 90.0}]]  # 線在中間斷開


def test_one_card_failing_leaves_the_other_cards_on_screen(services, db, monkeypatch, no_network_on_gui_thread):
    add(db, "S20261031090000", "2026-10-31 09:00:00", customer=80, server=70)
    monkeypatch.setattr(home_dashboard, "RECENT_SQL", "SELECT nothing FROM tbl_missing WHERE account_id = %s")
    dashboard = refreshed(services, staff_dashboard(services))
    overview, recent, trend = dashboard.sections
    assert recent.state == "error" and overview.state in ("ready", "empty") and trend.state in ("ready", "empty")
    assert recent.errorDetail == "Database request failed. If this keeps happening, contact your administrator."
    assert "1146" in recent.errorTechnical  # 收在 Details 裡（Table 'adam.tbl_missing' doesn't exist）


def test_dashboard_is_three_round_trips_all_on_the_db_lane(services, db, db_proxy, no_network_on_gui_thread):
    on_db_lane(services, warm_up)
    dashboard = staff_dashboard(services)
    db_proxy.reset()
    with db_lane_held(services):
        dashboard.refresh()
        assert all(services.tasks.isBusy(key) for key in dashboard.keys)
        assert db_proxy.snapshot()["rt"] == 0  # GUI 執行緒 0 個往返
    settle(services, dashboard)
    assert db_proxy.snapshot()["rt"] == 3  # 每張卡片 1 個查詢、1 個往返（PLAN §5-3）
    assert dashboard.keys == HOME_KEYS


def test_launch_is_five_round_trips_and_leaves_the_analysis_list_to_the_analysis_page(engine, services, db, db_proxy,
                                                                                       no_network_on_gui_thread):
    """launch() 的往返（ENG-21 的預算 5，T28）：名字 1＋首頁的三張卡片 3＋監看 1。分析清單等打開分析頁才查。"""
    add(db, "S20261031090000", "2026-10-31 09:00:00", status=1)
    on_db_lane(services, warm_up)
    db_proxy.reset()
    busy = []
    services.tasks.busyChanged.connect(lambda key, is_busy: is_busy and busy.append(key))
    home = server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    assert db_proxy.snapshot()["rt"] == 5
    assert set(HOME_KEYS) <= set(busy)
    assert ANALYSIS_KEY not in busy and home.serviceModel.status == "loading"  # 清單還沒查
    assert qml(home.shell_window, "homeDashboard.overview.state !== 'loading' && homeDashboard.ranking === null")

    db_proxy.reset()
    qml(home.shell_window, "analysisButton.click()")  # 側邊欄的 Analysis：換頁，清單在背景查
    assert services.tasks.isBusy(ANALYSIS_KEY)
    wait_until(lambda: not services.tasks.isBusy(ANALYSIS_KEY))
    assert db_proxy.snapshot()["rt"] == 1
    assert [row["serviceId"] for row in home.serviceModel.notAnalyzedModel.rows()] == ["S20261031090000"]

    db_proxy.reset()
    qml(home.shell_window, "homeButton.click()")  # 側邊欄的 Home：三張卡片在背景重新查詢
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in HOME_KEYS))
    assert db_proxy.snapshot()["rt"] == 3


