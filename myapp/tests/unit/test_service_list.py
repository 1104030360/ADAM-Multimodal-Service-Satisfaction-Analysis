"""Services 頁和服務明細共用的規則（新版介面 U2，PLAN §6-4、§6-5、§6-9；T31），不需要真的資料庫。

- 文字：長度、日期、範圍、筆數；分數四捨五入到小數 1 位（分數帶由 QML 的 Theme.bandName 用同一個數字判斷）。
- 日期的檢查：格式不對、To 早於 From；改了欄位之後錯誤先拿掉。
- 查詢的條件與參數（LIMIT 是最後一個參數）。
- 分頁：一次 8 筆、Show More 多查 8 筆、失敗時清單留著、新的搜尋從 8 筆重來；兩端各跑一次。
  背景工作用真的 TaskRunner，db lane 換成依 LIMIT 回傳資料的假 DbLane（PagingLane）。
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import mysql.connector
import pytest

from myapp import service_list
from myapp.init import admin_home_init, server_home_init
from myapp.notifications import Notifier
from myapp.service_list import (Criteria, SearchPage, clear_date_error, count_text, date_errors, display_score,
                                length_text, list_date_text, list_row, page_range, range_text, search_query,
                                shown_text, time_range_text)
from myapp.tasks import TaskRunner
from tests.integration.helpers import wait_until

STAFF_ID = "00000000-0000-0000-0000-000000000002"
T0 = datetime(2026, 11, 1, 9, 15)


# ---------------------------------------------------------------- 文字


@pytest.mark.parametrize("seconds, expected", [
    (0, "Under 1 min"), (59, "Under 1 min"),
    (60, "1 min"), (5 * 60 + 29, "5 min"), (5 * 60 + 30, "6 min"),  # 分鐘四捨五入
    (59 * 60 + 29, "59 min"), (59 * 60 + 30, "1 h"),
    (65 * 60, "1 h 5 min"), (125 * 60 + 40, "2 h 6 min"),
])
def test_length_text_rounds_to_minutes(seconds, expected):
    assert length_text(T0, T0 + timedelta(seconds=seconds)) == expected


def test_length_is_empty_when_unknown():
    assert length_text(T0, None) == ""          # 還沒有結束時間
    assert length_text(None, T0) == ""
    assert length_text(T0, T0 - timedelta(minutes=1)) == ""  # 結束早於開始：資料有問題，不顯示負的


@pytest.mark.parametrize("first, last, expected", [
    (date(2026, 10, 19), date(2026, 11, 1), "Oct 19 – Nov 1, 2026"),        # 設計稿
    (date(2026, 10, 19), date(2026, 10, 25), "Oct 19 – Oct 25, 2026"),
    (date(2025, 12, 20), date(2026, 1, 3), "Dec 20, 2025 – Jan 3, 2026"),  # 跨年：兩邊都寫年份
    (date(2026, 11, 1), date(2026, 11, 1), "Nov 1, 2026"),                 # 同一天
    (datetime(2026, 10, 19, 23, 59), datetime(2026, 11, 1, 0, 0), "Oct 19 – Nov 1, 2026"),  # datetime 只看日期
    (date(2026, 10, 19), None, "From Oct 19, 2026"),
    (None, date(2026, 11, 1), "Until Nov 1, 2026"),
    (None, None, "All dates"),
])
def test_range_text(first, last, expected):
    assert range_text(first, last) == expected


def test_months_do_not_follow_the_system_locale():
    """strftime 的 %b 在 QApplication 建立之後跟著系統語言（例如「11月」）；介面一律英文（PLAN D1）。"""
    assert [range_text(date(2026, month, 1), None)[5:8] for month in range(1, 13)] == list(service_list.MONTHS)


def test_list_date_text_adds_the_year_only_for_other_years():
    today = date(2026, 11, 1)
    assert list_date_text(datetime(2026, 11, 1, 11, 45), today) == "Nov 1, 11:45"  # 設計稿
    assert list_date_text(datetime(2025, 12, 31, 14, 5), today) == "Dec 31, 2025, 14:05"
    assert list_date_text(None, today) == ""


def test_time_range_text():
    assert time_range_text(T0, T0 + timedelta(minutes=6)) == "09:15–09:21"
    assert time_range_text(T0, None) == "09:15"
    assert time_range_text(T0, T0 - timedelta(minutes=1)) == "09:15"
    assert time_range_text(None, None) == ""


@pytest.mark.parametrize("count, expected", [(0, "No services"), (1, "1 service"), (18, "18 services"),
                                             (1234, "1,234 services")])
def test_count_text(count, expected):
    assert count_text(count) == expected


def test_shown_text_and_subtitles():
    assert shown_text(8, 18) == "Showing 8 of 18"
    assert service_list.list_subtitle("Oct 19 – Nov 1, 2026") == "Oct 19 – Nov 1, 2026 · newest first"
    assert service_list.list_subtitle("Oct 19 – Nov 1, 2026", service_list.staff_label("")) == (
        "All staff · Oct 19 – Nov 1, 2026 · newest first")
    assert service_list.staff_label("Mia Chen") == "Mia Chen"


@pytest.mark.parametrize("raw, shown", [
    (None, None), (-1, None), (-1.0, None),     # 沒有資料：不是 0（畫面「—」，沒有分數帶）
    (100, 100.0), (85, 85.0),
    (84.96, 85.0),          # 畫面上是 85.0：Theme.bandName 用這個數字，分數帶是 Good，和數字一致
    (84.94, 84.9), (69.96, 70.0), (69.94, 69.9), (0, 0.0),
])
def test_scores_are_rounded_to_what_the_screen_shows(raw, shown):
    assert display_score(raw) == shown


def test_list_row_has_the_roles_the_table_reads():
    row = {"service_id": "S20261101091500", "name": "Mia Chen", "service_start": T0,
           "service_end": T0 + timedelta(minutes=6, seconds=10), "customer_total": 91.66, "server_total": -1}
    assert list_row(row, date(2026, 11, 1)) == {
        "serviceId": "S20261101091500", "staffName": "Mia Chen", "startTime": "2026-11-01 09:15:00",
        "dateText": "Nov 1, 09:15", "lengthText": "6 min", "customerScore": 91.7, "staffScore": None,
    }
    assert set(list_row(row, date(2026, 11, 1))) == set(service_list.LIST_ROLES)


FORMAT = "Enter the date as YYYY-MM-DD, for example 2026-10-31."
ORDER = "The To date is before the From date. Choose the same day or later."


@pytest.mark.parametrize("start, end, errors", [
    ("", "", ("", "")),
    (" 2026-10-19 ", "2026-11-01", ("", "")),
    ("2026-11-01", "2026-11-01", ("", "")),         # 同一天可以
    ("2026/10/19", "2026-02-30", (FORMAT, FORMAT)),  # 格式不對、沒有這一天
    ("2026-11-02", "2026-11-01", ("", ORDER)),      # To 早於 From：錯誤在 To 下面
    ("2026-11-02", "nope", ("", FORMAT)),           # To 的格式不對時只說格式
])
def test_date_errors(start, end, errors):
    first, last, found = date_errors(start, end)
    assert (found["start"], found["end"]) == errors
    if errors == ("", ""):
        assert (first, last) == (service_list.parse_date(start), service_list.parse_date(end))


def test_editing_a_field_clears_its_error_and_the_order_error():
    assert clear_date_error({"start": FORMAT, "end": FORMAT}, "start") == {"start": "", "end": FORMAT}
    assert clear_date_error({"start": "", "end": ORDER}, "start") == {"start": "", "end": ""}  # 改 From 可能就解決了
    assert clear_date_error({"start": FORMAT, "end": ORDER}, "end") == {"start": FORMAT, "end": ""}


# ---------------------------------------------------------------- 查詢的條件


def test_query_for_one_staff_member_covers_the_whole_end_day():
    sql, params = search_query(Criteria(datetime(2026, 10, 19), datetime(2026, 11, 1), account_id=STAFF_ID), 16)
    assert "s.service_status = 3" in sql and "ORDER BY s.service_start DESC, s.service_id DESC" in sql
    assert "s.account_id = %s" in sql and "a.account_name" not in sql.split("WHERE")[1]
    assert params == (STAFF_ID, datetime(2026, 10, 19), datetime(2026, 11, 2), 16)  # 結束日：小於隔天 00:00
    assert sql.rstrip().endswith("LIMIT %s")


def test_query_for_admins_filters_by_name_or_not_at_all():
    sql, params = search_query(Criteria(staff_name="Mia Chen"), 8)
    assert "a.account_name = %s" in sql and params == ("Mia Chen", 8)
    sql, params = search_query(Criteria(), 8)  # All staff、日期都沒填
    assert "%s" not in sql.split("WHERE")[1].split("LIMIT")[0] and params == (8,)


def test_page_range_fills_empty_dates_from_the_results():
    page = SearchPage([{}], 3, datetime(2026, 3, 3, 10), datetime(2026, 11, 1, 9))
    assert page_range(Criteria(), page) == "Mar 3 – Nov 1, 2026"
    assert page_range(Criteria(start=datetime(2026, 1, 1)), page) == "Jan 1 – Nov 1, 2026"  # 填了的照填的
    assert page_range(Criteria(end=datetime(2026, 12, 31)), page) == "Mar 3 – Dec 31, 2026"
    empty = SearchPage([], 0)
    assert page_range(Criteria(start=datetime(2026, 10, 19)), empty) == "From Oct 19, 2026"
    assert page_range(Criteria(), empty) == "All dates"


# ---------------------------------------------------------------- 分頁（兩端）


def dataset(count):
    """count 筆分析完成的服務，新的在前（和 SQL 的 ORDER BY 一樣）。"""
    rows = []
    for i in range(count):
        start = datetime(2026, 11, 1, 18) - timedelta(hours=3 * i)
        rows.append({"service_id": f"S{start:%Y%m%d%H%M%S}", "name": "Mia Chen", "service_start": start,
                     "service_end": start + timedelta(minutes=5), "customer_total": 80.0, "server_total": 90.0})
    return rows


class PagingLane:
    """取代 DbLane：依 SQL 最後一個參數（LIMIT）回傳 rows 的前幾筆，加上 window function 算的欄位。

    limits 記下每次查詢的 LIMIT；error 不是 None 時丟出它。
    """

    def __init__(self, rows):
        self.rows, self.limits, self.error, self._result = rows, [], None, []

    @contextmanager
    def cursor(self, dictionary=False):
        yield self

    def execute(self, sql, params=()):
        self.limits.append(params[-1])
        if self.error is not None:
            raise self.error
        starts = [row["service_start"] for row in self.rows]
        extra = {"total_count": len(self.rows), "first_start": min(starts, default=None),
                 "last_start": max(starts, default=None)}
        self._result = [dict(row, **extra) for row in self.rows[:params[-1]]]

    def fetchall(self):
        return self._result


@pytest.fixture
def services():
    services = SimpleNamespace(tasks=TaskRunner(), notifier=Notifier(), db=PagingLane(dataset(18)), ui_prefs=None)
    yield services
    services.tasks.shutdown()


class Staff:
    key = server_home_init.SEARCH_KEY

    def __init__(self, services):
        self.manager = server_home_init.ServiceManager(services, STAFF_ID)

    def search(self, start="", end=""):
        return self.manager.search(start, end)

    def searching(self):
        return self.manager.busy


class Admin:
    key = "admin:search"

    def __init__(self, services):
        self.manager = admin_home_init.ServiceManager(services)

    def search(self, start="", end=""):
        return self.manager.search("", start, end)

    def searching(self):
        return self.manager.refreshing or self.manager.state == "loading"


SIDES = pytest.mark.parametrize("side", [Staff, Admin], ids=["staff", "admin"])


def settle(services, side):
    wait_until(lambda: not services.tasks.isBusy(side.key))


def shown_ids(manager):
    return [row["serviceId"] for row in manager.listModel.rows()]


@SIDES
def test_a_search_lists_eight_and_counts_all(side, services):
    page = side(services)
    manager = page.manager
    assert (manager.totalCount, manager.hasMore, manager.countText, manager.shownText) == (0, False, "", "")
    assert not manager.showMore()  # 還沒查過
    assert page.search()
    settle(services, side)
    assert services.db.limits == [8]
    assert manager.listModel.count == 8
    assert shown_ids(manager) == [row["service_id"] for row in services.db.rows[:8]]  # 新的在前
    assert (manager.totalCount, manager.hasMore) == (18, True)
    assert (manager.countText, manager.shownText) == ("18 services", "Showing 8 of 18")
    assert manager.rangeText == "Oct 30 – Nov 1, 2026"  # 日期沒填：用查到的服務最早、最晚的那一天


@SIDES
def test_show_more_adds_eight_at_the_end_without_resetting_the_list(side, services):
    page = side(services)
    manager = page.manager
    page.search()
    settle(services, side)
    model = manager.listModel
    inserted, removed = [], []
    model.rowsInserted.connect(lambda parent, first, last: inserted.extend(range(first, last + 1)))
    model.rowsRemoved.connect(lambda parent, first, last: removed.extend(range(first, last + 1)))
    model.modelReset.connect(lambda: removed.append("reset"))

    assert manager.showMore()
    assert manager.loadingMore and not page.searching()  # 不是重新搜尋：Search 按鈕不忙碌、清單不變淡
    assert not manager.showMore()  # 查詢中再按一次：不重複送出
    settle(services, side)
    assert services.db.limits == [8, 16]  # 同樣的條件，LIMIT 多 8
    assert not manager.loadingMore
    assert (inserted, removed) == (list(range(8, 16)), [])  # 前 8 列沒動，捲動位置留著
    assert manager.shownText == "Showing 16 of 18" and manager.hasMore

    assert manager.showMore()
    settle(services, side)
    assert model.count == 18 and not manager.hasMore and manager.shownText == "Showing 18 of 18"
    assert not manager.showMore()  # 都列出來了
    assert services.db.limits == [8, 16, 24]


@SIDES
def test_show_more_failure_keeps_the_list_and_can_be_retried(side, services):
    page = side(services)
    manager = page.manager
    page.search()
    settle(services, side)
    services.db.error = mysql.connector.Error(msg="Lost connection to MySQL server during query", errno=2013)
    assert manager.showMore()
    settle(services, side)
    assert manager.listModel.count == 8 and manager.totalCount == 18  # 已經列出的留著
    assert not manager.loadingMore and manager.moreFailureCount == 1
    assert manager.moreErrorDetail == "Can't reach the database. Check your network and try again."
    assert manager.moreErrorTechnical.startswith("Error: 2013")
    state = manager.status if side is Staff else manager.state
    assert state == "ready"  # 不是整個區塊的錯誤

    services.db.error = None
    assert manager.showMore()  # 再按一次就是重試
    assert manager.moreErrorDetail == ""
    settle(services, side)
    assert manager.listModel.count == 16 and manager.moreFailureCount == 0


@SIDES
def test_a_new_search_starts_again_from_eight(side, services):
    page = side(services)
    manager = page.manager
    page.search()
    settle(services, side)
    manager.showMore()
    settle(services, side)
    assert manager.listModel.count == 16
    services.db.rows = dataset(3)
    assert page.search("2026-11-01", "2026-11-01")
    settle(services, side)
    assert services.db.limits[-1] == 8
    assert (manager.listModel.count, manager.totalCount, manager.hasMore) == (3, 3, False)
    assert manager.rangeText == "Nov 1, 2026"  # 填了日期：照填的寫


def test_admin_subtitle_names_the_staff_filter(services):
    manager = admin_home_init.ServiceManager(services)
    manager.search("Mia Chen", "2026-10-19", "2026-11-01")
    settle(services, Admin)
    assert manager.staffText == "Mia Chen"
    assert manager.subtitle == "Mia Chen · Oct 19 – Nov 1, 2026 · newest first"
    manager.search("", "2026-10-19", "2026-11-01")
    settle(services, Admin)
    assert manager.subtitle == "All staff · Oct 19 – Nov 1, 2026 · newest first"  # 設計稿


def test_staff_subtitle_and_an_empty_result(services):
    manager = server_home_init.ServiceManager(services, STAFF_ID)
    manager.search("2026-10-19", "2026-11-01")
    settle(services, Staff)
    assert manager.subtitle == "Oct 19 – Nov 1, 2026 · newest first"  # 設計稿
    services.db.rows = []
    manager.search("2026-09-01", "")
    settle(services, Staff)
    assert (manager.status, manager.totalCount, manager.countText) == ("ready", 0, "No services")
    assert manager.rangeText == "From Sep 1, 2026" and not manager.hasMore
