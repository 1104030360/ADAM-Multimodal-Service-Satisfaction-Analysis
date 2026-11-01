"""新版首頁（U1，T28）不需要真的資料庫的規則：期間的邊界、變化 %、分數帶、趨勢的空白日子、排名的順序，
以及每張卡片的狀態（一張讀不到，其他照常；重新查詢時舊的數字留著）。

背景工作用真的 TaskRunner，db lane 換成假的（FakeLane）：哪一種查詢回什麼、丟什麼例外，由測試決定。
查詢本身接真的 MySQL 的測試在 tests/integration/test_staff_home.py、test_admin.py。
"""
import re
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

import mysql.connector
import pytest

from myapp import home_dashboard
from myapp.home_dashboard import (HomeDashboard, band, delta_percent, empty_overview, fill_trend, overview_from_row,
                                  overview_params, overview_sql, period_bounds, rank_staff, recent_rows, score,
                                  short_day, trend_days, trend_sql, value_runs)
from myapp.notifications import Notifier
from myapp.tasks import TaskRunner
from tests.integration.helpers import wait_until

DAY = date(2026, 11, 1)
OFFLINE = "Can't reach the database. Check your network and try again."


def at(text):
    return datetime.fromisoformat(text)


# ---------------------------------------------------------------- 期間的邊界


@pytest.mark.parametrize("period, start, previous", [
    ("today", "2026-11-01", "2026-10-31"),   # 和昨天整天比
    ("7d", "2026-10-26", "2026-10-19"),      # 今天和前 6 天；上一期是再前面 7 天
    ("30d", "2026-10-03", "2026-09-03"),
])
def test_a_period_includes_today_and_compares_with_the_same_length_before(period, start, previous):
    assert period_bounds(period, DAY) == (at(start), at(previous))


def test_periods_cross_month_and_year_ends():
    assert period_bounds("7d", date(2026, 1, 3)) == (at("2025-12-28"), at("2025-12-21"))
    assert period_bounds("30d", date(2028, 3, 1)) == (at("2028-02-01"), at("2028-01-02"))  # 閏年的 2 月有 29 天
    assert period_bounds("today", date(2026, 3, 1)) == (at("2026-03-01"), at("2026-02-28"))


def test_overview_query_takes_its_dates_from_this_computer_not_the_database():
    sql = overview_sql(per_account=True)
    params = overview_params(DAY, "staff-1")
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= set(params)  # 每個佔位都有值
    assert params["since"] == at("2026-09-03")  # 範圍 60 天：30 天和它的上一期
    assert "CURDATE" not in sql and "NOW()" not in sql  # RDS 預設 UTC，「今天」會差 8 小時
    assert len(re.findall(r"END\) AS (\w+)", sql)) == 63  # 三個期間各 4 個筆數、員工人數、本期和上一期各 8 個平均
    admin = overview_sql(per_account=False)
    assert "account_id = %(account_id)s" not in admin and "account_id" not in overview_params(DAY)
    assert set(re.findall(r"%\((\w+)\)s", trend_sql(True))) == {"account_id", "start", "end"}
    assert "account_id" not in trend_sql(False).split("WHERE")[1]


# ---------------------------------------------------------------- 分數、分數帶、變化


@pytest.mark.parametrize("value, expected", [
    (None, ""), (100.0, "good"), (85.0, "good"), (84.9, "fair"), (70.0, "fair"), (69.9, "needsImprovement"),
    (0.0, "needsImprovement"),
])
def test_band_thresholds_are_85_and_70(value, expected):
    assert band(value) == expected


def test_scores_hide_missing_data_and_band_follows_the_shown_value():
    assert score(None) is None and score(-1) is None and score(-1.0) is None  # 「—」，不是 -1 或 0
    assert score(0) == 0.0 and score(72.45001) == 72.5
    assert score(84.96) == 85.0 and band(score(84.96)) == "good"  # 顯示 85.0 就是 Good，不會寫 85.0 · Fair


@pytest.mark.parametrize("current, previous, expected", [
    (82.0, 79.5, 3.1),
    (76.0, 78.0, -2.6),
    (80.0, 80.0, 0.0),
    (None, 80.0, None),   # 這一期沒有分數
    (80.0, None, None),   # 上一期沒有分數：不比，不是 +100%
    (80.0, 0.0, None),    # 不能除以 0
])
def test_change_is_relative_to_the_previous_period(current, previous, expected):
    assert delta_percent(current, previous) == expected


def test_change_uses_the_unrounded_averages():
    tile = home_dashboard.tile(70.04, 69.96)
    assert (tile["value"], tile["previous"], tile["delta"], tile["band"]) == (70.0, 70.0, 0.1, "fair")


# ---------------------------------------------------------------- 趨勢：沒有服務的日子是空白


def trend_row(day, value, **columns):
    row = {"day": day} | {f"{side}_{metric}": value for side in ("customer", "staff")
                          for metric in ("overall", "text", "audio", "facial")}
    return row | columns


def test_trend_covers_fourteen_days_ending_today():
    days = trend_days(DAY)
    assert (len(days), days[0], days[-1]) == (14, date(2026, 10, 19), DAY)
    assert [short_day(day) for day in (days[0], days[-1])] == ["Oct 19", "Nov 1"]  # 不跟著系統的語言


def test_trend_days_without_services_are_null_not_zero():
    days = trend_days(DAY)
    series = fill_trend([
        trend_row(date(2026, 10, 19), 80.04),
        trend_row(date(2026, 10, 27), 70.0, customer_overall=None),  # 那天顧客的總分全是 -1
        trend_row(DAY, 90.0),
        trend_row(date(2026, 10, 18), 10.0),  # 範圍外：不畫
    ], days)
    customer = series["customer"]["overall"]
    assert customer[0] == 80.0 and customer[-1] == 90.0
    assert customer[8] is None and series["staff"]["overall"][8] == 70.0
    assert customer[1:8] == [None] * 7 and customer[9:13] == [None] * 4
    assert set(series) == {"customer", "staff"}
    assert set(series["staff"]) == {"overall", "text", "audio", "facial"}


def test_trend_lines_break_at_days_without_data():
    assert value_runs([80.0, 81.0, None, 79.0, None, None, 82.0, 83.0]) == [
        [{"x": 0, "y": 80.0}, {"x": 1, "y": 81.0}], [{"x": 3, "y": 79.0}],
        [{"x": 6, "y": 82.0}, {"x": 7, "y": 83.0}]]
    assert value_runs([None] * 14) == [] and value_runs([]) == []


# ---------------------------------------------------------------- 排名、最近的服務


def staff(name, customer, staff_score, account=None, count=1):
    return {"accountId": account or name, "name": name, "count": count, "customer": customer, "staff": staff_score}


def test_ranking_is_highest_first_with_missing_scores_last():
    rows = [staff("Ava", 80.0, 70.0), staff("Leo", None, 90.0), staff("Mia", 91.2, None),
            staff("Bea", 80.0, 75.0), staff("Bea", 80.0, 60.0, account="b-2")]
    assert [(row["name"], row["accountId"]) for row in rank_staff(rows, "customer")] == [
        ("Mia", "Mia"), ("Ava", "Ava"), ("Bea", "Bea"), ("Bea", "b-2"), ("Leo", "Leo")]  # 同分照名字、再照帳號
    assert [row["name"] for row in rank_staff(rows, "staff")] == ["Leo", "Bea", "Ava", "Bea", "Mia"]


def test_recent_services_are_drawn_oldest_first():
    newest_first = [
        {"service_id": "S20261101094500", "service_start": at("2026-11-01 09:45:00"), "customer_total": 84.96,
         "server_total": -1},
        {"service_id": "S20261030142000", "service_start": at("2026-10-30 14:20:00"), "customer_total": 69.9,
         "server_total": 74.0},
    ]
    assert recent_rows(newest_first) == [
        {"serviceId": "S20261030142000", "start": "2026-10-30 14:20:00", "dayLabel": "Oct 30", "timeLabel": "14:20",
         "customer": 69.9, "staff": 74.0, "customerBand": "needsImprovement", "staffBand": "fair"},
        {"serviceId": "S20261101094500", "start": "2026-11-01 09:45:00", "dayLabel": "Nov 1", "timeLabel": "09:45",
         "customer": 85.0, "staff": None, "customerBand": "good", "staffBand": ""},
    ]


# ---------------------------------------------------------------- Overview 的那一列


def overview_row(**values):
    """Overview 查詢的一列：筆數都是 0、平均都是 NULL，再用 values 覆寫。欄位名稱從 SQL 讀，和查詢對得上。"""
    columns = re.findall(r"END\) AS (\w+)", overview_sql(per_account=True))
    row = {name: None if "_customer_" in name or "_staff_" in name else 0 for name in columns}
    unknown = set(values) - set(row)
    assert not unknown, unknown
    return row | values


def test_overview_row_becomes_three_periods():
    periods = overview_from_row(overview_row(
        today_count=4, today_status3=2, today_status2=1, today_status1=1, today_staff=1,
        today_customer_overall=72.0, today_previous_customer_overall=80.0,
        d30_count=40, d30_staff_text=88.0), DAY)
    today = periods["today"]
    assert (today["count"], today["completed"], today["analyzing"], today["notAnalyzed"], today["staffCount"]) == (
        4, 2, 1, 1, 1)
    assert today["customer"]["overall"] == {"value": 72.0, "previous": 80.0, "delta": -10.0, "band": "fair"}
    assert today["staff"]["overall"] == {"value": None, "previous": None, "delta": None, "band": ""}
    assert (today["start"], today["previousStart"]) == ("2026-11-01", "2026-10-31")
    assert periods["30d"]["staff"]["text"]["band"] == "good" and periods["7d"]["count"] == 0
    assert periods.keys() == empty_overview(DAY).keys() == {"today", "7d", "30d"}


# ---------------------------------------------------------------- 卡片的狀態（假的 db lane）


class FakeCursor:
    def __init__(self, lane):
        self.lane = lane
        self.kind = None

    def execute(self, sql, params=()):
        self.kind = ("recent" if "LIMIT" in sql else "trend" if "GROUP BY DATE" in sql
                     else "ranking" if "account_name" in sql else "overview")
        self.lane.calls.append((self.kind, params))
        error = self.lane.errors.get(self.kind)
        if error is not None:
            raise error

    def fetchone(self):
        return self.lane.results[self.kind]

    def fetchall(self):
        return list(self.lane.results[self.kind])


class FakeLane:
    """取代 DbLane：依查詢的種類（overview、recent、trend、ranking）回傳 results，或丟出 errors 裡的例外。"""

    def __init__(self):
        self.results = {"overview": overview_row(), "recent": [], "trend": [], "ranking": []}
        self.errors = {}
        self.calls = []
        self.dictionary = []

    @contextmanager
    def cursor(self, dictionary=False):
        self.dictionary.append(dictionary)
        yield FakeCursor(self)


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setattr(home_dashboard, "today", lambda: DAY)
    services = SimpleNamespace(tasks=TaskRunner(), notifier=Notifier(), db=FakeLane(), ui_prefs=None)
    yield services
    services.tasks.shutdown()


def settle(services, dashboard):
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in dashboard.keys))


def mysql_error(errno, msg):
    return mysql.connector.Error(msg=msg, errno=errno)


def recent_row(hour, customer=80.0, staff_score=75.0):
    start = datetime(2026, 10, 31, hour)
    return {"service_id": f"S20261031{hour:02d}0000", "service_start": start, "customer_total": customer,
            "server_total": staff_score}


def test_staff_dashboard_sends_three_queries_for_the_same_day(services):
    dashboard = HomeDashboard(services, "staff-1", "staff:home")
    assert dashboard.keys == ("staff:home:overview", "staff:home:recent", "staff:home:trend")
    assert dashboard.ranking is None and dashboard.recent is not None
    assert {section.state for section in dashboard.sections} == {"loading"}  # 建構時不查
    assert services.db.calls == []
    services.db.results.update(overview=overview_row(d30_count=3), recent=[recent_row(9)],
                               trend=[trend_row(DAY, 80.0)])
    dashboard.refresh()
    settle(services, dashboard)
    assert [kind for kind, _ in services.db.calls] == ["overview", "recent", "trend"]
    overview, recent, trend = (params for _, params in services.db.calls)
    assert overview["account_id"] == recent[0] == trend["account_id"] == "staff-1"  # 只看自己的服務
    assert (overview["today_start"], trend["start"], trend["end"]) == (
        at("2026-11-01"), at("2026-10-19"), at("2026-11-02"))
    assert set(services.db.dictionary) == {True}  # 和其他頁面共用 dictionary cursor：不多 1 個往返
    assert [section.state for section in dashboard.sections] == ["ready", "ready", "ready"]
    assert dashboard.recent.rows[0]["serviceId"] == "S20261031090000"
    assert dashboard.trend.series["customer"]["overall"][-1] == 80.0
    assert re.fullmatch(r"\d\d:\d\d", dashboard.overview.updatedAt)


def test_admin_dashboard_ranks_everyone_instead_of_recent_services(services):
    dashboard = HomeDashboard(services, None, "admin:home")
    assert dashboard.keys == ("admin:home:overview", "admin:home:ranking", "admin:home:trend")
    assert dashboard.recent is None
    services.db.results["ranking"] = [
        {"account_id": f"a{n}", "account_name": f"Staff {n}", "services": n, "customer": 60.0 + n,
         "staff": 90.0 - n} for n in range(1, 8)]
    dashboard.refresh()
    settle(services, dashboard)
    overview_params_sent = services.db.calls[0][1]
    assert "account_id" not in overview_params_sent  # 全部員工
    ranking = dashboard.ranking
    assert ranking.state == "ready"
    assert [row["name"] for row in ranking.byCustomer] == [f"Staff {n}" for n in range(7, 0, -1)]  # 7 個人都列出來
    assert [row["name"] for row in ranking.byStaff] == [f"Staff {n}" for n in range(1, 8)]
    assert ranking.byCustomer[0] == {"accountId": "a7", "name": "Staff 7", "count": 7, "customer": 67.0,
                                    "staff": 83.0, "customerBand": "needsImprovement", "staffBand": "fair"}
    assert services.db.calls[1][1] == (at("2026-10-03"),)  # 30 天內（含今天）


def test_cards_without_data_are_empty(services):
    dashboard = HomeDashboard(services, "staff-1", "staff:home")
    services.db.results["overview"] = overview_row(today_count=0, d7_count=0, d30_count=0, d30_previous_customer_overall=80.0)
    dashboard.refresh()
    settle(services, dashboard)
    assert [section.state for section in dashboard.sections] == ["empty", "empty", "empty"]
    admin = HomeDashboard(services, None, "admin:home")
    admin.refresh()
    settle(services, admin)
    assert admin.ranking.state == "empty"


def test_one_card_failing_leaves_the_others_alone(services):
    dashboard = HomeDashboard(services, "staff-1", "staff:home")
    services.db.results.update(overview=overview_row(d30_count=3), recent=[recent_row(9)])
    services.db.errors["trend"] = mysql_error(2013, "Lost connection to MySQL server during query")
    dashboard.refresh()
    settle(services, dashboard)
    overview, recent, trend = dashboard.sections
    assert (overview.state, recent.state, trend.state) == ("ready", "ready", "error")
    assert (trend.errorDetail, trend.failureCount) == (OFFLINE, 1)
    assert trend.errorTechnical.startswith("Error: 2013")  # 收在 Details 裡
    assert overview.errorDetail == "" and overview.failureCount == 0

    trend.refresh()  # 一直失敗：InlineError ≥ 3 次多一行建議
    settle(services, dashboard)
    assert trend.failureCount == 2
    del services.db.errors["trend"]
    assert trend.refresh()  # Retry
    assert trend.state == "loading"  # 錯誤的時候沒有數字可以留：換成骨架
    settle(services, dashboard)
    assert (trend.state, trend.failureCount, trend.errorDetail) == ("empty", 0, "")


def test_refreshing_keeps_the_old_numbers_until_new_ones_arrive(services):
    dashboard = HomeDashboard(services, "staff-1", "staff:home")
    services.db.results["overview"] = overview_row(d30_count=3, d30_customer_overall=80.0)
    dashboard.refresh()
    settle(services, dashboard)
    overview = dashboard.overview
    changes = []
    overview.overviewChanged.connect(lambda: changes.append(overview.periods["30d"]["customer"]["overall"]["value"]))

    services.db.results["overview"] = overview_row(d30_count=4, d30_customer_overall=82.0)
    assert overview.refresh()
    assert not overview.refresh()  # 還在查：不重複送，結果回來一樣會更新
    assert (overview.state, overview.refreshing) == ("ready", True)
    assert overview.periods["30d"]["customer"]["overall"]["value"] == 80.0  # 舊的數字留著，不閃成骨架
    settle(services, dashboard)
    assert (overview.state, overview.refreshing, changes) == ("ready", False, [82.0])

    overview.refresh()  # 結果一樣：不發 overviewChanged，畫面不重畫
    settle(services, dashboard)
    assert changes == [82.0]

    services.db.errors["overview"] = mysql_error(2003, "Can't connect to MySQL server")
    overview.refresh()
    settle(services, dashboard)
    assert (overview.state, overview.refreshing, overview.errorDetail) == ("error", False, OFFLINE)


def test_trend_runs_and_fill_series(services):
    dashboard = HomeDashboard(services, "staff-1", "staff:home")
    trend = dashboard.trend
    assert trend.labels[0] == "Oct 19" and trend.days[-1] == "2026-11-01"  # 載入前就有 14 天的軸
    services.db.results["trend"] = [trend_row(date(2026, 10, 19), 80.0), trend_row(date(2026, 10, 20), 81.0),
                                    trend_row(DAY, 85.0, staff_text=None)]
    dashboard.refresh()
    settle(services, dashboard)
    assert trend.runs("customer", "overall") == [[{"x": 0, "y": 80.0}, {"x": 1, "y": 81.0}], [{"x": 13, "y": 85.0}]]
    assert trend.runs("staff", "text") == [[{"x": 0, "y": 80.0}, {"x": 1, "y": 81.0}]]
    assert trend.runs("nobody", "overall") == [] and trend.runs("customer", "mood") == []
    with pytest.raises(TypeError):
        trend.fillSeries(services.notifier, [])
