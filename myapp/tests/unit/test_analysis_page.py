"""分析頁的資料（U2，T30）：格式（Recorded、Length、Sent）、預設的分頁、一次 8 筆的分頁、送出的時間記在本機；
送出分析收到 200 之後，桌面端只重新整理清單，狀態 2 是分析服務寫的（SQS，T49）。

ServiceModel 用假的 TaskRunner 和假的資料表（照 ANALYSIS_SQL 的規則回傳），不連網路、不等計時器；
真的 SQL 在整合測試 test_analysis.py 驗證同樣的事。
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import mysql.connector
import pytest
from PySide6.QtCore import QSettings

from myapp.init import server_home_init
from myapp.init.server_home_init import (ANALYSIS_KEY, ANALYSIS_SQL, PAGE_SIZE, SENT_GROUP, SERVICE_ROLES,
                                         PagedRowModel, SentTimes, ServiceModel, default_tab, length_text,
                                         recorded_text, sent_text, shown_for)
from myapp.notifications import Notifier
from myapp.ui_prefs import open_settings
from tests.integration.helpers import toasts

ACCOUNT = "00000000-0000-0000-0000-000000000002"
OTHER_ACCOUNT = "00000000-0000-0000-0000-000000000003"
MINUTE_MS = 60_000
NOW = datetime(2026, 11, 1, 14, 30)


# ── 格式 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds, text", [
    (0, "Less than 1 min"),
    (59, "Less than 1 min"),
    (60, "1 min"),
    (89, "1 min"),
    (90, "2 min"),                 # 四捨五入到分鐘
    (7 * 60 + 29, "7 min"),
    (7 * 60 + 30, "8 min"),
    (59 * 60 + 29, "59 min"),
    (59 * 60 + 30, "1 h"),         # 進位到 60 分鐘就是 1 小時
    (65 * 60, "1 h 5 min"),
    (120 * 60, "2 h"),
])
def test_length_is_rounded_to_minutes(seconds, text):
    start = datetime(2026, 11, 1, 9, 0)
    assert length_text(start, start + timedelta(seconds=seconds)) == text


@pytest.mark.parametrize("start, end", [
    (datetime(2026, 11, 1, 9, 0), None),                        # service_end 允許 NULL
    (datetime(2026, 11, 1, 9, 0), datetime(2026, 11, 1, 8, 59)),  # 結束比開始早（時鐘被調過）
    (None, datetime(2026, 11, 1, 9, 0)),
])
def test_unknown_length_is_empty_so_the_page_shows_a_dash(start, end):
    assert length_text(start, end) == ""


@pytest.mark.parametrize("ago_ms, text", [
    (0, "Sent less than 1 min ago"),
    (59_999, "Sent less than 1 min ago"),
    (MINUTE_MS, "Sent 1 min ago"),
    (21 * MINUTE_MS + 59_000, "Sent 21 min ago"),  # 經過的時間：無條件捨去
    (60 * MINUTE_MS, "Sent 1 h ago"),
    (65 * MINUTE_MS, "Sent 1 h 5 min ago"),
    (24 * 60 * MINUTE_MS, "Sent 1 day ago"),
    (3 * 24 * 60 * MINUTE_MS + 5 * MINUTE_MS, "Sent 3 days ago"),
    (-5 * MINUTE_MS, "Sent less than 1 min ago"),  # 時鐘被往回調：不顯示負的
])
def test_sent_text_counts_minutes_since_it_was_sent(ago_ms, text):
    now = 1_790_000_000_000
    assert sent_text(now - ago_ms, now) == text


def test_unknown_send_time_is_said_plainly():
    # 別台電腦送的：不知道什麼時候送的，不拿「第一次看到」的時間冒充（T30）
    assert sent_text(0, 1_790_000_000_000) == "Sent earlier"


def test_recorded_shows_month_day_and_time_in_english():
    assert recorded_text(datetime(2026, 11, 1, 14, 5), NOW) == "Nov 1, 14:05"
    assert recorded_text(datetime(2026, 1, 9, 8, 0), NOW) == "Jan 9, 08:00"
    assert recorded_text(datetime(2025, 12, 30, 10, 0), NOW) == "Dec 30, 2025, 10:00"  # 不是今年的加上年份
    assert recorded_text(None, NOW) == ""


@pytest.mark.parametrize("totals, tab", [
    ((3, 2, 41), "notAnalyzed"),  # 有沒送出的：要動作的在這裡
    ((0, 2, 41), "completed"),
    ((0, 0, 41), "completed"),
    ((0, 2, 0), "analyzing"),     # Completed 是空的、有分析中的：不停在空的分頁
    ((0, 0, 0), "notAnalyzed"),   # 全部是空的：流程的第一步
])
def test_default_tab(totals, tab):
    assert default_tab(*totals) == tab


@pytest.mark.parametrize("position, shown", [(1, 8), (8, 8), (9, 16), (16, 16), (17, 24), (41, 48)])
def test_showing_a_row_fills_up_its_page(position, shown):
    assert shown_for(position) == shown


# ── 一個分頁：一次 8 筆、Show More ────────────────────────────────────────────

def make_rows(count, prefix="S2026110100"):
    return [{"serviceId": f"{prefix}{i:04d}", "n": i} for i in range(count)]


class Recorder:
    """記下 PagedRowModel 發的 signal。"""

    def __init__(self, model):
        self.events = []
        model.rowsInserted.connect(lambda parent, first, last: self.events.append(("insert", first, last)))
        model.rowsRemoved.connect(lambda parent, first, last: self.events.append(("remove", first, last)))
        model.modelReset.connect(lambda: self.events.append(("reset",)))
        model.moreWanted.connect(lambda: self.events.append(("moreWanted",)))
        model.pagingChanged.connect(lambda: self.events.append(("paging",)))
        model.totalChanged.connect(lambda: self.events.append(("total",)))

    def take(self):
        events, self.events = self.events, []
        return events


def appended(first, end):
    """RowListModel 一列一列插入：first 到 end - 1。"""
    return [("insert", row, row) for row in range(first, end)]


def ids(model):
    return [row["serviceId"] for row in model.rows()]


def test_tab_shows_the_first_page_and_counts_everything():
    model = PagedRowModel(("serviceId", "n"), "serviceId")
    assert (model.count, model.total, model.hasMore, model.loadingMore, model.limit) == (0, 0, False, False, 16)
    model.set_rows(make_rows(16), 41)
    assert (model.count, model.total, model.hasMore, model.loadingMore) == (8, 41, True, False)
    assert ids(model) == [row["serviceId"] for row in make_rows(8)]
    assert len(model.loaded()) == 16  # 下一頁已經在手上
    assert model.satisfied() and model.pageSize == PAGE_SIZE


def test_show_more_shows_the_loaded_page_at_once_and_asks_for_the_next():
    model = PagedRowModel(("serviceId", "n"), "serviceId")
    model.set_rows(make_rows(16), 41)
    events = Recorder(model)
    model.showMore()
    assert model.count == 16  # 不等往返
    assert events.take() == [*appended(8, 16), ("moreWanted",)]  # 只在後面插入，不重設整份清單（捲動位置不跳）
    assert (model.limit, model.satisfied(), model.loadingMore) == (24, False, False)

    model.showMore()  # 下一頁還沒到又按一次：先記著要顯示 24 筆，按鈕忙碌
    assert (model.count, model.shown, model.loadingMore) == (16, 24, True)
    assert events.take() == [("paging",), ("moreWanted",)]
    model.showMore()  # 忙碌時再按：不會一直往上加
    assert (model.shown, events.take()) == (24, [])

    model.set_rows(make_rows(32), 41)  # 查詢回來了
    assert (model.count, model.loadingMore, model.hasMore) == (24, False, True)
    assert events.take() == [*appended(16, 24), ("paging",)]


def test_show_more_stops_at_the_total():
    model = PagedRowModel(("serviceId", "n"), "serviceId")
    model.set_rows(make_rows(10), 10)
    model.showMore()
    assert (model.count, model.hasMore, model.loadingMore, model.satisfied()) == (10, False, False, True)
    events = Recorder(model)
    model.showMore()  # 沒有更多了：什麼都不做
    assert events.take() == [] and model.shown == 16


def test_short_tab_has_no_show_more():
    model = PagedRowModel(("serviceId", "n"), "serviceId")
    model.set_rows(make_rows(3), 3)
    assert (model.count, model.total, model.hasMore) == (3, 3, False)
    events = Recorder(model)
    model.showMore()
    assert events.take() == []


def test_reveal_fills_up_to_the_rows_page_and_reset_goes_back_to_one_page():
    model = PagedRowModel(("serviceId", "n"), "serviceId")
    rows = make_rows(24)
    model.set_rows(rows, 30)
    assert model.reveal(rows[3]["serviceId"]) == 3 and model.count == 8  # 已經看得到：不動
    assert model.reveal(rows[9]["serviceId"]) == 9 and model.count == 16
    assert model.reveal("S-not-loaded") == -1 and model.count == 16
    model.reset()
    assert (model.count, model.shown, model.limit) == (8, 8, 16)


# ── 送出的時間記在本機 ───────────────────────────────────────────────────────

def test_send_times_survive_a_restart_and_stay_per_account():
    times = SentTimes(ACCOUNT)
    times.remember("S20261101130000", 1_790_000_000_000)
    SentTimes(OTHER_ACCOUNT).remember("S20261101131000", 1_790_000_100_000)
    again = SentTimes(ACCOUNT)  # 重開 app
    assert again.get("S20261101130000") == 1_790_000_000_000
    assert again.get("S20261101131000") == 0.0  # 別人的
    assert again.ids() == {"S20261101130000"}
    again.forget("S20261101130000")
    again.forget("S20261101130000")  # 已經沒有了：不出錯
    assert SentTimes(ACCOUNT).ids() == set()
    assert SentTimes(OTHER_ACCOUNT).ids() == {"S20261101131000"}


def test_unreadable_send_times_are_dropped():
    settings = open_settings()
    for value in ("not a number", "-5", "0", "inf"):
        settings.setValue(f"{SENT_GROUP}/{ACCOUNT}/S-{value}", value)
    settings.setValue(f"{SENT_GROUP}/{ACCOUNT}/S20261101130000", "1790000000000.0")
    settings.sync()
    assert SentTimes(ACCOUNT).ids() == {"S20261101130000"}
    settings = QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "ADAM", "myapp")
    settings.beginGroup(f"{SENT_GROUP}/{ACCOUNT}")
    assert settings.childKeys() == ["S20261101130000"]  # 看不懂的刪掉了，下次登入不會再遇到


# ── ServiceModel：假的 TaskRunner 和資料表 ─────────────────────────────────────

class FakeTable:
    """DbLane 的替身：照 ANALYSIS_SQL 的規則回傳（每個狀態取前 limit 筆，pin 那個狀態至少取到它的下一頁）。
    只認得 ANALYSIS_SQL：桌面端不寫狀態，狀態 2 是分析服務收下工作時寫的（accept()，T49）。"""

    def __init__(self):
        self.services = {}
        self.queries = []
        self.statements = []  # 執行過的每一句 SQL：table fixture 結束時檢查都是 ANALYSIS_SQL

    def add(self, service_id, start, status, end=None, customer=-1, staff=-1):
        self.services[service_id] = {"service_id": service_id, "service_start": start,
                                     "service_end": end or start + timedelta(minutes=5),
                                     "service_status": status, "customer_total": customer, "server_total": staff}

    def add_many(self, count, status, day=1):
        """count 筆，最新的在前：S2026110{day}HHMMSS。"""
        added = []
        for i in range(count):
            start = datetime(2026, 11, day, 8) + timedelta(minutes=count - i)
            service_id = start.strftime("S%Y%m%d%H%M%S")
            self.add(service_id, start, status)
            added.append(service_id)
        return added

    def accept(self, service_id):
        """分析服務收下工作（回 200 之前）：狀態 1 改成 2，和它的 MARK_SENT_SQL 一樣（SQS，T49）。"""
        service = self.services[service_id]
        if service["service_status"] == 1:
            service["service_status"] = 2

    @contextmanager
    def cursor(self, dictionary=False):
        assert dictionary
        yield self

    def execute(self, sql, params):
        self.statements.append(sql)
        if sql != ANALYSIS_SQL:  # 桌面端只讀清單，不寫狀態（T49）
            raise AssertionError(f"unexpected SQL: {sql}")
        self.queries.append(params)
        page, result = params["page"], []
        for status in (1, 2, 3):
            group = sorted((s for s in self.services.values() if s["service_status"] == status),
                           key=lambda s: (s["service_start"], s["service_id"]), reverse=True)
            limit = params[f"limit_{status}"]
            pinned = next((i + 1 for i, s in enumerate(group) if s["service_id"] == params["pin"]), None)
            if pinned is not None:
                limit = max(limit, ((pinned - 1) // page + 2) * page)
            result += [{**s, "position": i + 1, "total": len(group)} for i, s in enumerate(group[:limit])]
        self._result = sorted(result, key=lambda s: (s["service_start"], s["service_id"]), reverse=True)

    def fetchall(self):
        return self._result


class FakeRunner:
    """TaskRunner 的替身：工作先不執行，finish() 才在這裡執行並交回結果（和真的一樣，先不忙碌再呼叫 on_ok）。"""

    def __init__(self):
        self.running = {}

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        if key in self.running:
            return False
        self.running[key] = (fn, on_ok, on_err)
        return True

    def isBusy(self, key):
        return key in self.running

    def finish(self, key):
        fn, on_ok, on_err = self.running.pop(key)
        try:
            result = fn(None, None)
        except Exception as error:  # noqa: BLE001  和 TaskRunner 一樣交給 on_err
            on_err(error)
            return
        on_ok(result)


class Clock:
    def __init__(self, moment=NOW):
        self.now = moment.timestamp()

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += minutes * 60


@pytest.fixture
def table():
    table = FakeTable()
    yield table
    # 寫入的 SQL 會在 db lane 的工作裡丟例外、變成 on_err，測試本身不一定看得出來：結束時再檢查一次
    assert set(table.statements) <= {ANALYSIS_SQL}


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_model(table, runner, clock):
    """建立 ServiceModel；測試結束時 stop()，和外殼關掉時一樣，不留還在跑的計時器給之後的測試。"""
    services = SimpleNamespace(tasks=runner, db=table, notifier=Notifier())
    made = []

    def make():
        made.append(ServiceModel(services, ACCOUNT, clock=clock))
        return made[-1]

    yield make
    for model in made:
        model.stop()


def loaded(model, runner):
    """打開分析頁，等清單回來（以及它順便要的下一次）。"""
    model.pageOpened()
    while runner.isBusy(ANALYSIS_KEY):
        runner.finish(ANALYSIS_KEY)
    return model


def test_opening_the_page_before_the_list_arrives_is_loading_not_empty(make_model, table, runner):
    table.add_many(3, status=3)
    model = make_model()
    changes = []
    model.defaultTabChanged.connect(lambda: changes.append(model.defaultTab))
    model.pageOpened()
    # 還沒回來：頁面顯示骨架。total 是 0，但 status 是 loading，不能當成「沒有服務」（DS-25）
    assert (model.status, model.busy, model.defaultTab) == ("loading", True, "")
    assert [m.total for m in (model.notAnalyzedModel, model.analyzingModel, model.completedModel)] == [0, 0, 0]
    runner.finish(ANALYSIS_KEY)
    assert (model.status, model.busy, model.defaultTab) == ("ready", False, "completed")
    assert changes == ["completed"]  # 直接從「還不知道」變成 Completed，中間沒有先跳到別的分頁
    assert model.completedModel.count == 3


def test_first_load_failure_has_no_default_tab(make_model, table, runner):
    model = make_model()
    model.pageOpened()
    table.execute = lambda sql, params: (_ for _ in ()).throw(ConnectionError("offline"))
    runner.finish(ANALYSIS_KEY)
    assert (model.status, model.defaultTab, model.failureCount) == ("error", "", 1)


def test_each_tab_loads_one_page_ahead_with_its_total(make_model, table, runner):
    table.add_many(3, status=1, day=3)
    table.add_many(2, status=2, day=2)
    table.add_many(41, status=3, day=1)
    model = loaded(make_model(), runner)
    assert [(q["limit_1"], q["limit_2"], q["limit_3"], q["pin"]) for q in table.queries] == [(16, 16, 16, None)]
    counts = [(m.count, m.total, m.hasMore) for m in (model.notAnalyzedModel, model.analyzingModel,
                                                      model.completedModel)]
    assert counts == [(3, 3, False), (2, 2, False), (8, 41, True)]
    assert model.defaultTab == "notAnalyzed"


def test_show_more_is_instant_and_keeps_one_page_ahead(make_model, table, runner):
    table.add_many(41, status=3)
    model = loaded(make_model(), runner)
    completed = model.completedModel
    completed.showMore()
    assert completed.count == 16  # 已經載入的下一頁：馬上出現
    assert runner.isBusy(ANALYSIS_KEY) and model.busy  # 再多載入一頁（1 個往返）
    completed.showMore()  # 還沒回來又按：先記著
    assert (completed.count, completed.loadingMore) == (16, True)
    runner.finish(ANALYSIS_KEY)  # 這次只要到 24 筆
    assert (completed.count, completed.loadingMore) == (24, False)
    assert runner.isBusy(ANALYSIS_KEY) and model.busy  # 要的比這次查的多：自己再查一次，忙碌不中斷
    runner.finish(ANALYSIS_KEY)
    assert not runner.isBusy(ANALYSIS_KEY) and not model.busy
    assert [q["limit_3"] for q in table.queries] == [16, 24, 32]
    assert len(completed.loaded()) == 32


def test_reopening_the_page_goes_back_to_the_first_page(make_model, table, runner):
    table.add_many(41, status=3)
    model = loaded(make_model(), runner)
    model.completedModel.showMore()
    runner.finish(ANALYSIS_KEY)
    model.pageOpened()
    assert model.completedModel.count == 8  # 馬上回到 8 筆
    runner.finish(ANALYSIS_KEY)
    assert not runner.isBusy(ANALYSIS_KEY)  # 載入的比要的多：不用再查
    assert table.queries[-1]["limit_3"] == 16


def test_view_results_reveals_a_row_past_the_first_page_in_one_query(make_model, table, runner):
    completed = table.add_many(41, status=3)
    model = loaded(make_model(), runner)
    queries = len(table.queries)
    model.pageOpened(completed[19])  # 第 20 筆：還沒載入
    assert model.indexOfService("completed", completed[19]) == -1
    runner.finish(ANALYSIS_KEY)
    assert table.queries[queries]["pin"] == completed[19]
    assert model.indexOfService("completed", completed[19]) == 19 and model.completedModel.count == 24
    assert not runner.isBusy(ANALYSIS_KEY) and len(table.queries) == queries + 1  # 一次就夠，也已經多載入一頁
    assert len(model.completedModel.loaded()) == 32

    model.pageOpened(completed[12])  # 已經載入的：不等查詢就顯示到它那一頁
    assert model.indexOfService("completed", completed[12]) == 12 and model.completedModel.count == 16


def test_reveal_requested_during_a_refresh_is_queried_next(make_model, table, runner):
    completed = table.add_many(41, status=3)
    model = make_model()
    model.refresh()  # 例如監看發現分析完成了
    model.pageOpened(completed[30])  # 接著按 View Results：這次的查詢已經送出了
    runner.finish(ANALYSIS_KEY)
    assert runner.isBusy(ANALYSIS_KEY)  # 上一次沒有它：再查一次
    runner.finish(ANALYSIS_KEY)
    assert model.indexOfService("completed", completed[30]) == 30
    assert [q["pin"] for q in table.queries] == [None, completed[30]]


def test_reveal_of_a_service_that_is_gone_is_dropped(make_model, table, runner):
    table.add_many(3, status=3)
    model = loaded(make_model(), runner)
    model.pageOpened("S20200101000000")
    runner.finish(ANALYSIS_KEY)
    assert not runner.isBusy(ANALYSIS_KEY)
    model.refresh()
    runner.finish(ANALYSIS_KEY)
    assert table.queries[-1]["pin"] is None  # 不會一直帶著它查


def test_rows_carry_recorded_length_and_scores_with_one_decimal(make_model, table, runner):
    start = datetime(2026, 11, 1, 11, 45)
    table.add("S20261101114500", start, 3, end=start + timedelta(minutes=7, seconds=20), customer=81.44, staff=-1)
    model = loaded(make_model(), runner)
    [row] = model.completedModel.rows()
    assert set(row) == set(SERVICE_ROLES)
    assert (row["recorded"], row["length"]) == ("Nov 1, 11:45", "7 min")
    assert (row["customerScore"], row["staffScore"]) == (81.4, None)  # -1：沒有分數，不顯示 -1
    assert row["sentText"] == ""  # 只有分析中的有
    assert row["startTime"] == "2026-11-01 11:45:00"  # View 打開明細時交給 openServiceDetail()


def send(model, runner, monkeypatch, table, service_id):
    """按 Analyze：分析服務收下工作（狀態 1 → 2 是它寫的，T49）、回 200，之後在 db lane 重新整理清單。"""
    monkeypatch.setattr(server_home_init, "post_analysis", lambda url, sid: table.accept(sid))
    assert model.analyzeService(service_id)
    runner.finish(f"analyze:{service_id}")
    runner.finish(f"analysis:sent:{service_id}")


def test_send_time_is_remembered_and_counts_up(make_model, table, runner, clock, monkeypatch):
    table.add("S20261101134500", datetime(2026, 11, 1, 13, 45), 1)
    model = loaded(make_model(), runner)
    send(model, runner, monkeypatch, table, "S20261101134500")
    [row] = model.analyzingModel.rows()
    assert row["sentText"] == "Sent less than 1 min ago"
    assert SentTimes(ACCOUNT).get("S20261101134500") == clock.now * 1000  # 收到 200 的時間，記在本機

    changed = []
    model.analyzingModel.dataChanged.connect(lambda first, last, roles: changed.append((first.row(), list(roles))))
    clock.advance(21)
    model._tick()  # 30 秒的計時器
    [row] = model.analyzingModel.rows()
    assert row["sentText"] == "Sent 21 min ago"
    roles = {bytes(name).decode(): role for role, name in model.analyzingModel.roleNames().items()}
    assert changed == [(0, [roles["sentText"]])]  # 只有那一列的那一個角色

    restarted = loaded(make_model(), runner)  # 重開 app：送出的時間還在
    [row] = restarted.analyzingModel.rows()
    assert row["sentText"] == "Sent 21 min ago"


def test_clock_runs_only_while_something_is_analyzing_and_stops_with_the_shell(make_model, table, runner):
    table.add("S20261101131000", datetime(2026, 11, 1, 13, 10), 2)
    model = loaded(make_model(), runner)
    assert model._ticker.isActive() and model._ticker.interval() == 30_000  # sentText 每 30 秒（DS-44）
    table.services["S20261101131000"]["service_status"] = 3
    model.refresh()
    runner.finish(ANALYSIS_KEY)
    assert not model._ticker.isActive()  # 沒有分析中的：不計時

    table.services["S20261101131000"]["service_status"] = 2
    model.refresh()
    model.stop()  # 外殼關掉了（ServerHome.stop()）
    runner.finish(ANALYSIS_KEY)  # 之後才回來的結果
    assert not model._ticker.isActive()
    model.refresh()
    table.execute = lambda sql, params: (_ for _ in ()).throw(ConnectionError("offline"))
    runner.finish(ANALYSIS_KEY)
    assert not model._retry.isActive()  # 也不自己重試


def test_sent_elsewhere_says_sent_earlier(make_model, table, runner, clock):
    table.add("S20261101131000", datetime(2026, 11, 1, 13, 10), 2)  # 別台電腦送的
    model = loaded(make_model(), runner)
    [row] = model.analyzingModel.rows()
    assert row["sentText"] == "Sent earlier"  # 不拿第一次看到的時間冒充送出的時間（T30）
    clock.advance(30)
    model._tick()
    assert model.analyzingModel.rows()[0]["sentText"] == "Sent earlier"


def test_send_time_is_forgotten_once_the_analysis_is_over(make_model, table, runner, monkeypatch):
    for service_id in ("S20261101130000", "S20261101131000", "S20261101132000"):
        table.add(service_id, datetime.strptime(service_id, "S%Y%m%d%H%M%S"), 1)
    model = loaded(make_model(), runner)
    for service_id in ("S20261101130000", "S20261101131000", "S20261101132000"):
        send(model, runner, monkeypatch, table, service_id)
    assert SentTimes(ACCOUNT).ids() == {"S20261101130000", "S20261101131000", "S20261101132000"}

    table.services["S20261101130000"]["service_status"] = 3  # 完成：清單看到它不在分析中了
    model.refresh()
    runner.finish(ANALYSIS_KEY)
    table.services["S20261101131000"]["service_status"] = 1  # 失敗：監看說的
    model.analysis_failed("S20261101131000")
    assert SentTimes(ACCOUNT).ids() == {"S20261101132000"}
    runner.finish(ANALYSIS_KEY)
    del table.services["S20261101132000"]  # 不見了：Analyzing 全部載入了卻沒有它
    model.refresh()
    runner.finish(ANALYSIS_KEY)
    assert SentTimes(ACCOUNT).ids() == set()


def test_sending_before_the_list_loads_keeps_the_remembered_send_times(make_model, table, runner, monkeypatch):
    table.add("S20261101131000", datetime(2026, 11, 1, 13, 10), 2)
    table.add("S20261101140500", datetime(2026, 11, 1, 14, 5), 1)
    SentTimes(ACCOUNT).remember("S20261101131000", 1_790_000_000_000)  # 上次開 app 時送出的
    model = make_model()  # 重開 app，還沒打開分析頁：清單是空的
    monkeypatch.setattr(server_home_init, "post_analysis", lambda url, sid: table.accept(sid))
    assert model.analyzeService("S20261101140500")  # 從「錄影存好了」的通知按 Analyze
    assert model.status == "loading"
    assert SentTimes(ACCOUNT).ids() == {"S20261101131000"}  # 空的清單不能拿來清掉記錄
    runner.finish("analyze:S20261101140500")
    runner.finish("analysis:sent:S20261101140500")
    times = SentTimes(ACCOUNT)
    assert times.get("S20261101131000") == 1_790_000_000_000 and times.get("S20261101140500") > 0
    assert {row["serviceId"] for row in model.analyzingModel.rows()} == {"S20261101131000", "S20261101140500"}


# ── 收到 200 之後：狀態是分析服務寫的，桌面端只重新整理（SQS，T49）──────────────────

def lost_connection(sql, params):
    """db lane 的查詢：連不到資料庫（errno 2013，Notifier 說「Can't reach the database」）。"""
    raise mysql.connector.errors.OperationalError(msg="Lost connection to MySQL server during query", errno=2013)


def test_the_row_goes_where_the_database_says_after_the_send(make_model, table, runner, monkeypatch):
    # 分析服務收下了、回 200，可是 worker 很快就失敗、狀態已經改回 1：桌面端不寫 2，那一列照資料庫留在
    # Not analyzed（以前晚到的 2 會把它蓋成 Analyzing，一直等不到結果，SQS 教學 5-1）；之後由監看說失敗（D4）
    table.add("S20261101140500", datetime(2026, 11, 1, 14, 5), 1)
    model = loaded(make_model(), runner)
    sent = []
    model.analysisSent.connect(sent.append)

    def accepted_then_failed(url, service_id):
        table.accept(service_id)
        table.services[service_id]["service_status"] = 1

    monkeypatch.setattr(server_home_init, "post_analysis", accepted_then_failed)
    assert model.analyzeService("S20261101140500")
    runner.finish("analyze:S20261101140500")
    runner.finish("analysis:sent:S20261101140500")
    assert table.statements == [ANALYSIS_SQL, ANALYSIS_SQL]  # 打開分析頁、收到 200 之後：都只讀清單
    assert table.services["S20261101140500"]["service_status"] == 1
    [row] = model.notAnalyzedModel.rows()
    assert (row["serviceId"], row["sending"]) == ("S20261101140500", False)
    assert model.analyzingModel.rows() == []
    assert sent == ["S20261101140500"]  # 監看照樣看這一筆：看到 1 就跳「Analysis failed」
    model.analysis_failed("S20261101140500")
    runner.finish(ANALYSIS_KEY)
    assert model.notAnalyzedModel.rows()[0]["failed"] is True


def test_a_send_that_went_through_still_says_sent_when_the_refresh_fails(make_model, table, runner, clock,
                                                                         monkeypatch):
    # 200 就是分析服務已經把工作存好了：之後的重新整理失敗也不能說「Couldn't send for analysis」。送出的時間照記、
    # 跳「Sent for analysis」、監看開始看；清單和一般的重新整理失敗一樣：留著舊的、跳「更新不到」、15 秒後再試
    table.add("S20261101140500", datetime(2026, 11, 1, 14, 5), 1)
    model = loaded(make_model(), runner)
    notifier = model.services.notifier
    sent = []
    model.analysisSent.connect(sent.append)
    monkeypatch.setattr(server_home_init, "post_analysis", lambda url, sid: table.accept(sid))
    assert model.analyzeService("S20261101140500")
    runner.finish("analyze:S20261101140500")
    table.execute = lost_connection  # 桌面端這時連不到資料庫
    runner.finish("analysis:sent:S20261101140500")

    assert [(toast["severity"], toast["title"], toast["detail"]) for toast in toasts(notifier)] == [
        ("info", "Sent for analysis", "S20261101140500 is being analyzed. You'll be notified when it's done."),
        ("warning", "Can't refresh analysis status", "Can't reach the database. Retrying in 15 seconds.")]
    assert sent == ["S20261101140500"]
    assert SentTimes(ACCOUNT).get("S20261101140500") == clock.now * 1000  # 收到 200 的時間
    [row] = model.notAnalyzedModel.rows()  # 清單是舊的：那一列先留在原處，按鈕不再忙碌
    assert (row["serviceId"], row["sending"], row["failed"]) == ("S20261101140500", False, False)
    assert (model.status, model.failureCount, model._retry.isActive()) == ("ready", 0, True)

    del table.execute  # 又連得到了
    model.refresh()  # 15 秒到了（_retry 接的就是 refresh）
    runner.finish(ANALYSIS_KEY)
    [row] = model.analyzingModel.rows()
    assert (row["serviceId"], row["sentText"]) == ("S20261101140500", "Sent less than 1 min ago")
    assert [toast["title"] for toast in toasts(notifier)] == ["Sent for analysis"]  # 「更新不到」自己關掉
    assert not model._retry.isActive()


def test_a_send_before_the_page_loads_is_not_a_block_error_when_the_refresh_fails(make_model, table, runner,
                                                                                  monkeypatch):
    # 從「錄影存好了」的通知按 Analyze，分析頁還沒打開過：重新整理失敗不變成區塊錯誤、也不自己重試清單
    # （打開分析頁時才查）；「更新不到」照樣跳，因為監看接著就要等這一筆（T30）
    table.add("S20261101140500", datetime(2026, 11, 1, 14, 5), 1)
    model = make_model()
    monkeypatch.setattr(server_home_init, "post_analysis", lambda url, sid: table.accept(sid))
    assert model.analyzeService("S20261101140500")
    runner.finish("analyze:S20261101140500")
    table.execute = lost_connection
    runner.finish("analysis:sent:S20261101140500")
    assert [toast["title"] for toast in toasts(model.services.notifier)] == [
        "Sent for analysis", "Can't refresh analysis status"]
    assert (model.status, model.failureCount, model._retry.isActive()) == ("loading", 0, False)
    assert SentTimes(ACCOUNT).get("S20261101140500") > 0


def test_refresh_warning_waits_for_data_or_for_something_in_flight(make_model, runner):
    model = make_model()  # 分析頁還沒打開過：清單沒有資料
    notifier = model.services.notifier
    offline = ConnectionError("offline")
    model.refresh_failed(offline, 15)  # 例如登入時監看的第一次查詢：不知道有沒有在等的服務
    assert notifier.model.rowCount() == 0
    model.refresh_failed(offline, 15, watching=True)  # 監看在等分析結果：在首頁等通知的人要知道會晚到
    model.refresh_failed(offline, 30, watching=True)  # 同一次斷線只有一則
    assert notifier.model.rowCount() == 1
    model.refresh_recovered()
    assert notifier.model.rowCount() == 0


def test_failed_service_keeps_its_mark_beyond_the_loaded_page(make_model, table, runner):
    # Not analyzed 有 20 筆，失敗的那一筆錄得最早：在第 21 筆，還沒載入
    table.add("S20261020090000", datetime(2026, 10, 20, 9, 0), 2)
    table.add_many(20, status=1)
    model = loaded(make_model(), runner)
    table.services["S20261020090000"]["service_status"] = 1
    model.analysis_failed("S20261020090000")  # 監看看到 2 → 1
    runner.finish(ANALYSIS_KEY)
    model.notAnalyzedModel.showMore()
    model.notAnalyzedModel.showMore()
    while runner.isBusy(ANALYSIS_KEY):
        runner.finish(ANALYSIS_KEY)
    row = next(row for row in model.notAnalyzedModel.rows() if row["serviceId"] == "S20261020090000")
    assert row["failed"] is True  # 按 Show More 看到它時，還是 Failed、按鈕是 Retry
