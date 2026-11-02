"""頁首的搜尋服務 ID（UD5、T32）：輸入的規則、LIKE 跳脫、SQL、debounce 與「只顯示最新的結果」。

用假的 TaskRunner 和 DbLane：工作先不執行，由測試決定什麼時候、用什麼資料交回結果，不連網路、不等計時器
（debounce 用 searchNow() 提早送出；只有一個測試真的等 QTimer）。
"""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import mysql.connector
import pytest
from PySide6.QtCore import QCoreApplication

from myapp import search
from myapp.search import (DEBOUNCE_MS, EMPTY, ERROR, IDLE, INVALID, LOADING, READY, RESULT_LIMIT, SEARCH_KEYS, Query,
                          ServiceSearch, as_result, escape_like, find_services, parse_query, search_sql)
from tests.integration.helpers import wait_until

STAFF = "00000000-0000-0000-0000-000000000002"
OFFLINE = "Can't reach the database. Check your network and try again."


class FakeDb:
    """DbLane 的替身：記下執行的 SQL；fetchall() 回傳 rows，broken 不是 None 時查詢丟出它。"""

    def __init__(self):
        self.rows = []
        self.broken = None
        self.executed = []
        self.dictionary = []

    @contextmanager
    def cursor(self, dictionary=False):
        self.dictionary.append(dictionary)
        yield self

    def execute(self, sql, params):
        if self.broken is not None:
            raise self.broken
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)


class FakeRunner:
    """TaskRunner 的替身，照真的 replace、cancel 的規則：被取代或取消的工作，結果不交回。"""

    def __init__(self, db):
        self.db = db
        self.submitted = []
        self.running = {}
        self.cancelled = []

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        if key in self.running and not replace:
            return False
        task = SimpleNamespace(lane=lane, key=key, fn=fn, on_ok=on_ok, on_err=on_err, cancelled=False)
        if key in self.running:
            self.running[key].cancelled = True
        self.submitted.append(task)
        self.running[key] = task
        return True

    def isBusy(self, key):
        return key in self.running

    def cancel(self, key):
        self.cancelled.append(key)
        task = self.running.pop(key, None)
        if task is not None:
            task.cancelled = True

    def run(self, task, rows=(), broken=None, deliver_even_if_cancelled=False):
        """在「資料庫」是 rows（或查詢丟出 broken）時執行 task，交回結果。"""
        self.db.rows, self.db.broken = list(rows), broken
        if self.running.get(task.key) is task:
            del self.running[task.key]
        if task.cancelled and not deliver_even_if_cancelled:
            return
        try:
            result = task.fn(None, None)
        except Exception as error:  # noqa: BLE001  交回給 on_err，和 TaskRunner 一樣
            task.on_err(error)
        else:
            task.on_ok(result)

    def answer(self, rows=(), broken=None):
        self.run(self.submitted[-1], rows, broken)


def row(service_id, start=datetime(2026, 10, 31, 14, 30), status=3, name="Mia Chen"):
    return {"service_id": service_id, "service_start": start, "service_status": status, "account_name": name}


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def runner(db):
    return FakeRunner(db)


def make(runner, everyone=False, **kwargs):
    services = SimpleNamespace(tasks=runner, db=runner.db)
    box = ServiceSearch.for_admin(services, **kwargs) if everyone else ServiceSearch.for_staff(services, STAFF, **kwargs)
    box.events = []
    box.stateChanged.connect(lambda: box.events.append(("state", box.state, box.refreshing)))
    box.resultsChanged.connect(lambda: box.events.append(("results", [r["serviceId"] for r in box.results])))
    return box


@pytest.fixture
def staff(runner):
    return make(runner)


@pytest.fixture
def admin(runner):
    return make(runner, everyone=True)


def typed(box, text):
    """打完 text、debounce 時間到（不等計時器，直接送出）。"""
    box.setQuery(text)
    assert box.searchNow()


# ── LIKE 跳脫 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, escaped", [
    ("Mia", "Mia"),
    ("50%", "50\\%"),
    ("a_b", "a\\_b"),
    ("back\\slash", "back\\\\slash"),
    ("\\%_", "\\\\\\%\\_"),  # 反斜線先跳脫，後面加的反斜線才不會被再跳脫一次
    ("", ""),
])
def test_escape_like_makes_wildcards_literal(text, escaped):
    assert escape_like(text) == escaped


# ── 輸入的規則 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, query", [
    ("", Query(IDLE, "")),
    ("   ", Query(IDLE, "")),
    ("S", Query(IDLE, "")),                     # 只打了 S：還不能查，也不算錯（正要打數字）
    ("s", Query(IDLE, "")),
    ("S2026", Query("id", "S2026")),
    ("s2026", Query("id", "S2026")),            # 小寫的 s 也可以
    ("  S20261031 ", Query("id", "S20261031")),
    ("2026", Query("id", "S2026")),             # 只打數字：自動補上 S
    ("2026-10-31", Query("id", "S20261031")),   # 日期的寫法
    ("2026/10/31 14:30", Query("id", "S202610311430")),
    ("S 2026-10-31 14:30:00", Query("id", "S20261031143000")),
    ("Ｓ２０２６－１０", Query("id", "S202610")),  # 全形（NFKC）
    ("S20261031143000", Query("id", "S20261031143000")),  # 完整的 ID：14 個數字
    ("S202610311430001", Query(INVALID, "")),   # 15 個數字：不可能是服務 ID
    ("abc", Query(INVALID, "")),                # 服務人員不能用名字搜
    ("S2026x", Query(INVALID, "")),
    ("S2026_", Query(INVALID, "")),
    ("%", Query(INVALID, "")),
    ("-", Query(INVALID, "")),
    ("SS2026", Query(INVALID, "")),
])
def test_staff_input_rules(text, query):
    assert parse_query(text, names=False) == query


@pytest.mark.parametrize("text, query", [
    ("", Query(IDLE, "")),
    ("S", Query("name", "S")),                  # 管理員：單獨的 S 可能是名字的開頭
    ("2026-10", Query("id", "S202610")),        # 像服務 ID 的一律當服務 ID
    ("s2026", Query("id", "S2026")),
    ("S202610311430001", Query(INVALID, "")),
    ("Mia", Query("name", "Mia")),
    ("  mia   chen ", Query("name", "mia chen")),  # 前後的空白去掉，中間的併成一個
    ("ＭＩＡ", Query("name", "MIA")),           # 全形英文字母（NFKC）
    ("服務人員", Query("name", "服務人員")),
    ("50%_\\", Query("name", "50%_\\")),        # 萬用字元留著，查的時候才跳脫
    ("-", Query("name", "-")),
    ("x" * 100, Query("name", "x" * 100)),
    ("x" * 101, Query(INVALID, "")),            # 名字最長 100 個字
])
def test_admin_input_rules(text, query):
    assert parse_query(text, names=True) == query


# ── SQL ──────────────────────────────────────────────────────────────────────

def test_staff_id_search_is_limited_to_the_account_and_asks_for_one_extra_row():
    sql, params = search_sql(Query("id", "S2026"), STAFF)
    assert "s.service_id LIKE %s AND s.account_id = %s" in sql
    assert params == ("S2026%", STAFF, RESULT_LIMIT + 1)
    assert sql.rstrip().endswith("LIMIT %s")
    assert "ORDER BY s.service_start DESC, s.service_id DESC" in sql


def test_admin_id_search_has_no_account_filter():
    sql, params = search_sql(Query("id", "S2026"))
    assert "account_id = %s" not in sql
    assert params == ("S2026%", RESULT_LIMIT + 1)


def test_name_search_matches_the_start_of_the_name_or_of_any_word_with_wildcards_escaped():
    sql, params = search_sql(Query("name", "50%_"))
    assert "a.account_name LIKE %s OR a.account_name LIKE %s" in sql
    assert "ORDER BY (a.account_name LIKE %s) DESC, s.service_start DESC" in sql  # 名字開頭符合的排前面
    assert params == ("50\\%\\_%", "% 50\\%\\_%", "50\\%\\_%", RESULT_LIMIT + 1)


def test_name_search_is_never_built_for_one_account():
    with pytest.raises(ValueError):
        search_sql(Query("name", "Mia"), STAFF)


@pytest.mark.parametrize("query", [Query(IDLE, ""), Query(INVALID, "")])
def test_nothing_to_search_for_has_no_sql(query):
    with pytest.raises(ValueError):
        search_sql(query, STAFF)


def test_find_services_runs_one_query_on_the_shared_dictionary_cursor(db):
    db.rows = [row("S20261031143000", status=2)]
    assert find_services(db, Query("id", "S2026"), STAFF) == [{
        "serviceId": "S20261031143000", "startTime": "2026-10-31 14:30:00", "status": 2, "staffName": "Mia Chen"}]
    assert len(db.executed) == 1 and db.dictionary == [True]


def test_as_result_matches_what_the_detail_page_takes():
    """startTime 和服務查詢頁的 start_datetime 一樣，直接交給 load_service_media(serviceId, startTime)。"""
    result = as_result(row("S20261031090502", start=datetime(2026, 10, 31, 9, 5, 2), status="1", name=None))
    assert result == {"serviceId": "S20261031090502", "startTime": "2026-10-31 09:05:02", "status": 1,
                      "staffName": ""}


# ── 建立 ─────────────────────────────────────────────────────────────────────

def test_staff_search_needs_an_account(runner):
    services = SimpleNamespace(tasks=runner, db=runner.db)
    with pytest.raises(ValueError):
        ServiceSearch(services, None)  # 少傳 account_id 不能變成「查全部人的」
    with pytest.raises(ValueError):
        ServiceSearch.for_staff(services, "")


def test_initial_state_and_constants(staff, admin):
    assert (staff.state, staff.results, staff.hasMore, staff.refreshing) == (IDLE, [], False, False)
    assert (staff.searchesNames, admin.searchesNames) == (False, True)
    assert staff.limit == admin.limit == RESULT_LIMIT == 10
    assert staff._timer.interval() == DEBOUNCE_MS == 250


# ── debounce、只顯示最新的 ─────────────────────────────────────────────────────

def test_typing_waits_for_the_debounce_then_sends_one_query_for_the_last_text(staff, runner, db):
    for text in ("S", "S2", "S20", "S202", "S2026"):
        staff.setQuery(text)
    assert runner.submitted == [] and staff.pending  # 打字中：還沒查
    assert staff.state == LOADING  # 打第一個數字就顯示「查詢中」
    assert staff.searchNow()
    [task] = runner.submitted
    assert (task.lane, task.key) == ("db", SEARCH_KEYS["staff"])  # 在 db lane：GUI 執行緒不碰網路
    runner.answer([row("S20261031143000")])
    assert db.executed == [search_sql(Query("id", "S2026"), STAFF)]
    assert staff.state == READY
    assert staff.results == [{"serviceId": "S20261031143000", "startTime": "2026-10-31 14:30:00", "status": 3,
                              "staffName": "Mia Chen"}]
    assert not staff.searchNow()  # 沒有在等的查詢：不重複送出


def test_the_real_timer_sends_once_after_typing_stops(runner):
    box = make(runner, debounce_ms=30)
    for text in ("2", "20", "202"):
        box.setQuery(text)
        QCoreApplication.processEvents()
    assert runner.submitted == []
    wait_until(lambda: runner.submitted, timeout=2)
    QCoreApplication.processEvents()
    assert len(runner.submitted) == 1 and not box.pending
    runner.answer([])
    assert runner.db.executed[0][1][0] == "S202%"


def test_only_the_latest_texts_results_are_shown(staff, runner):
    typed(staff, "S2025")
    old = runner.submitted[-1]
    typed(staff, "S2026")  # 舊的還沒回來就換了字
    new = runner.submitted[-1]
    assert old.cancelled  # TaskRunner 取消舊的（replace／cancel）
    runner.run(new, [row("S20260101000000")])
    runner.run(old, [row("S20250101000000")], deliver_even_if_cancelled=True)  # 就算舊的結果還是送回來了
    assert [r["serviceId"] for r in staff.results] == ["S20260101000000"]


def test_a_stale_error_is_ignored_too(staff, runner):
    typed(staff, "S2025")
    old = runner.submitted[-1]
    typed(staff, "S2026")
    runner.run(runner.submitted[-1], [row("S20260101000000")])
    runner.run(old, broken=mysql.connector.InterfaceError("gone"), deliver_even_if_cancelled=True)
    assert staff.state == READY and staff.failureCount == 0


def test_the_same_query_is_not_sent_again(staff, runner):
    typed(staff, "S2026")
    runner.answer([row("S20261031143000")])
    for text in ("S2026 ", "s2026", "2026", " S 2026"):  # 寫法不同，查的是同一個
        staff.setQuery(text)
        assert not staff.pending
    assert len(runner.submitted) == 1 and staff.state == READY


def test_going_back_to_the_shown_text_cancels_the_pending_query(staff, runner):
    typed(staff, "S2026")
    runner.answer([row("S20261031143000")])
    staff.setQuery("S20261")
    assert staff.pending and staff.refreshing
    staff.setQuery("S2026")  # 刪掉剛打的字：結果就是畫面上那些
    assert not staff.pending and not staff.refreshing and staff.state == READY
    assert len(runner.submitted) == 1


def test_new_text_keeps_the_old_results_while_it_loads(staff, runner):
    typed(staff, "S2026")
    runner.answer([row("S20261031143000"), row("S20261030090000")])
    staff.events.clear()
    staff.setQuery("S20261031")
    assert (staff.state, staff.refreshing) == (READY, True)  # 不閃成空白（DS-33）
    assert len(staff.results) == 2
    staff.searchNow()
    runner.answer([row("S20261031143000")])
    assert (staff.state, staff.refreshing) == (READY, False)
    assert staff.events == [("state", READY, True), ("results", ["S20261031143000"]), ("state", READY, False)]


def test_identical_results_do_not_rebuild_the_list(staff, runner):
    typed(staff, "S2026")
    runner.answer([row("S20261031143000")])
    staff.events.clear()
    typed(staff, "S20261031")
    runner.answer([row("S20261031143000")])
    assert [event for event in staff.events if event[0] == "results"] == []  # 清單不重建：鍵盤選到的那一列留著


def test_clearing_goes_idle_and_cancels(staff, runner):
    typed(staff, "S2026")
    task = runner.submitted[-1]
    staff.clear()
    assert task.cancelled and SEARCH_KEYS["staff"] in runner.cancelled
    assert (staff.state, staff.results, staff.refreshing) == (IDLE, [], False)
    runner.run(task, [row("S20261031143000")], deliver_even_if_cancelled=True)
    assert staff.state == IDLE and staff.results == []


def test_clearing_while_waiting_stops_the_timer(staff, runner):
    staff.setQuery("S2026")
    staff.setQuery("")
    assert not staff.pending and staff.state == IDLE and runner.submitted == []


def test_staff_typing_letters_is_invalid_and_not_sent(staff, runner):
    typed(staff, "S2026")
    runner.answer([row("S20261031143000")])
    staff.setQuery("Mia")
    assert (staff.state, staff.results, staff.pending) == (INVALID, [], False)
    assert len(runner.submitted) == 1


def test_a_lone_s_waits_for_digits(staff, runner):
    staff.setQuery("S")
    assert (staff.state, staff.pending) == (IDLE, False)


def test_admin_name_search(admin, runner, db):
    typed(admin, "chen")
    [task] = runner.submitted
    assert task.key == SEARCH_KEYS["admin"]
    runner.answer([row("S20261031143000", name="Chen Wei"), row("S20261030090000", name="Mia Chen")])
    assert db.executed == [search_sql(Query("name", "chen"))]
    assert [r["staffName"] for r in admin.results] == ["Chen Wei", "Mia Chen"]


# ── 筆數上限、沒有結果、錯誤 ───────────────────────────────────────────────────

def test_at_most_ten_results_and_has_more(staff, runner):
    typed(staff, "S2026")
    runner.answer([row(f"S202610311430{i:02d}") for i in range(RESULT_LIMIT + 1)])
    assert len(staff.results) == RESULT_LIMIT and staff.hasMore
    typed(staff, "S20261031")
    runner.answer([row(f"S202610311430{i:02d}") for i in range(RESULT_LIMIT)])
    assert len(staff.results) == RESULT_LIMIT and not staff.hasMore


def test_no_match_is_empty(staff, runner):
    typed(staff, "S1999")
    runner.answer([])
    assert (staff.state, staff.results, staff.hasMore) == (EMPTY, [], False)
    staff.setQuery("S19990")
    assert (staff.state, staff.refreshing) == (EMPTY, True)  # 沒有結果時也一樣：等新的結果


def test_an_error_stays_in_the_dropdown_and_retry_sends_again(staff, runner):
    typed(staff, "S2026")
    runner.answer([row("S20261031143000")])
    typed(staff, "S20261")
    runner.answer(broken=mysql.connector.InterfaceError(errno=2003, msg="Can't connect to MySQL server"))
    assert (staff.state, staff.results, staff.refreshing) == (ERROR, [], False)
    assert staff.errorDetail == OFFLINE
    assert staff.errorTechnical.startswith("InterfaceError: 2003")
    assert staff.failureCount == 1
    assert not staff.searchNow()

    assert staff.retry()
    assert staff.state == LOADING and staff.errorDetail == ""
    runner.answer(broken=mysql.connector.InterfaceError(errno=2003, msg="Can't connect to MySQL server"))
    assert staff.failureCount == 2  # 連續失敗的次數（InlineError 在 ≥ 3 時多一行建議）
    staff.retry()
    runner.answer([row("S20261031143000")])
    assert (staff.state, staff.failureCount, staff.errorDetail) == (READY, 0, "")
    assert runner.db.executed[-1][1][0] == "S20261%"


def test_retry_only_after_an_error(staff, runner):
    assert not staff.retry()
    typed(staff, "S2026")
    assert not staff.retry()
    assert len(runner.submitted) == 1


def test_typing_after_an_error_loads_without_the_old_error(staff, runner):
    typed(staff, "S2026")
    runner.answer(broken=mysql.connector.InterfaceError(errno=2003, msg="down"))
    staff.setQuery("S20261")
    assert (staff.state, staff.refreshing, staff.errorDetail) == (LOADING, False, "")


def test_without_services_nothing_is_sent():
    """ServerHome 可以不經過 launch() 直接建立（services 是 None）：搜尋框沒有用，但也不會壞。"""
    box = ServiceSearch.for_staff(None, STAFF)
    box.setQuery("S2026")
    assert not box.searchNow()


def test_a_closed_task_runner_is_logged_not_raised(staff, runner, monkeypatch):
    monkeypatch.setattr(runner, "submit", lambda *args, **kwargs: False)  # shutdown() 之後
    staff.setQuery("S2026")
    assert not staff.searchNow()
