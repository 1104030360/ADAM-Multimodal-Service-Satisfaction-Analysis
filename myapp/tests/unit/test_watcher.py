"""分析完成的監看（ENG-16）與 Dock 提醒（ENG-17）：用假的 TaskRunner、資料庫和時鐘，不連網路、不等計時器。

- 退避：15 秒，10 分鐘後 30 秒，30 分鐘後 60 秒；有新的送出就回到 15 秒。
- 狀態轉換：2 → 3 完成、2 → 1 失敗、查不到就不再看；上一次還沒回來就略過這一次。
- Dock：app 不在前景才跳動、加徽章；切回 app 清掉徽章。
"""
import math
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QGuiApplication

from myapp import watcher as watcher_module
from myapp.watcher import IN_FLIGHT_SQL, POLL_KEY, AnalysisWatcher, DockAttention, poll_interval

ACCOUNT = "00000000-0000-0000-0000-000000000002"
MINUTE = 60


class FakeDb:
    """DbLane 的替身：記下執行的 SQL；fetchall() 回傳 rows，broken 不是 None 時查詢丟出它。"""

    def __init__(self):
        self.rows = []
        self.broken = None
        self.executed = []

    @contextmanager
    def cursor(self, dictionary=False):
        assert dictionary  # 和服務人員端其他查詢共用同一個 cursor：不多建一個（建 cursor 要多 1 個往返）
        yield self

    def execute(self, sql, params):
        if self.broken is not None:
            raise self.broken
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeRunner:
    """TaskRunner 的替身：工作先不執行，由測試決定什麼時候、在什麼資料上交回結果。"""

    def __init__(self, db):
        self.db = db
        self.submitted = []
        self.running = {}
        self.cancelled = []

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        if key in self.running:
            return False  # 和真的一樣：同一個 key 還在跑就拒絕
        task = SimpleNamespace(lane=lane, key=key, fn=fn, on_ok=on_ok, on_err=on_err)
        self.submitted.append(task)
        self.running[key] = task
        return True

    def isBusy(self, key):
        return key in self.running

    def cancel(self, key):
        self.cancelled.append(key)
        self.running.pop(key, None)

    def answer(self, rows=(), broken=None):
        """像 db lane 一樣：資料庫裡是 rows（或查詢丟出 broken）時，執行最後送出的工作，再交回結果。"""
        self.db.rows, self.db.broken = list(rows), broken
        self.db.executed.clear()
        task = self.submitted[-1]
        del self.running[task.key]
        try:
            result = task.fn(None, None)
        except Exception as error:  # noqa: BLE001  交回給 on_err，和 TaskRunner 一樣
            task.on_err(error)
        else:
            task.on_ok(result)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def in_flight(*service_ids):
    return [{"service_id": sid} for sid in service_ids]


def statuses(**status_by_id):
    return [{"service_id": sid, "service_status": status} for sid, status in status_by_id.items()]


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def runner(db):
    return FakeRunner(db)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def watcher(runner, db, clock):
    """AnalysisWatcher，和它發出的 signal（events）。"""
    watcher = AnalysisWatcher(runner, db, ACCOUNT, clock=clock)
    watcher.events = []
    watcher.finished.connect(lambda sid: watcher.events.append(("finished", sid)))
    watcher.failed.connect(lambda sid: watcher.events.append(("failed", sid)))
    watcher.pollFailed.connect(lambda error: watcher.events.append(("pollFailed", error)))
    watcher.pollSucceeded.connect(lambda: watcher.events.append(("pollSucceeded",)))
    yield watcher
    watcher.stop()


def started(watcher, runner, *service_ids):
    """start()；第一次的查詢回來時，分析中的是 service_ids。"""
    watcher.start()
    runner.answer(in_flight(*service_ids))
    watcher.events.clear()
    return watcher


# ── 間隔 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("elapsed, interval", [
    (0, 15), (10 * MINUTE - 1, 15), (10 * MINUTE, 30), (30 * MINUTE - 1, 30), (30 * MINUTE, 60), (8 * 60 * MINUTE, 60),
])
def test_interval_backs_off_from_15_to_30_to_60_seconds(elapsed, interval):
    assert poll_interval(elapsed) == interval


def test_interval_backs_off_with_time_and_resets_on_a_new_send(watcher, runner, clock):
    started(watcher, runner, "S1")
    assert watcher.next_poll_ms == 15_000
    clock.advance(11 * MINUTE)
    watcher.tick()  # 每次觸發後，依當時的間隔重新計時
    assert watcher.next_poll_ms == 30_000
    runner.answer(statuses(S1=2))
    clock.advance(20 * MINUTE)
    watcher.tick()
    assert watcher.next_poll_ms == 60_000
    watcher.add("S2")  # 有新的送出：回到 15 秒，不必等 60 秒那一次
    assert (watcher.interval(), watcher.next_poll_ms) == (15, 15_000)


# ── 第一次查詢 ───────────────────────────────────────────────────────────────

def test_start_asks_once_for_the_accounts_services_in_flight(watcher, runner, db):
    watcher.start()
    [task] = runner.submitted
    assert (task.lane, task.key) == ("db", POLL_KEY)  # 在 db lane：GUI 執行緒不碰網路
    runner.answer(in_flight("S20261030120000", "S20261030130000"))
    assert db.executed == [(IN_FLIGHT_SQL, (ACCOUNT,))]
    assert watcher.watched == ["S20261030120000", "S20261030130000"]
    assert watcher.events == [("pollSucceeded",)]
    assert watcher.polling and watcher.next_poll_ms == 15_000


def test_nothing_in_flight_means_no_polling(watcher, runner):
    started(watcher, runner)
    assert not watcher.polling
    watcher.tick()
    assert len(runner.submitted) == 1  # 沒有要看的服務就不查


def test_failed_first_query_is_retried_on_the_next_tick(watcher, runner, db):
    watcher.start()
    error = ConnectionError("database is down")
    runner.answer(broken=error)
    assert watcher.events == [("pollFailed", error)]
    assert watcher.polling  # 還不知道有哪些服務在分析：照間隔重試
    watcher.tick()
    runner.answer(in_flight("S1"))
    assert db.executed == [(IN_FLIGHT_SQL, (ACCOUNT,))]
    assert watcher.watched == ["S1"]


# ── 輪詢與狀態轉換 ───────────────────────────────────────────────────────────

def test_poll_looks_up_primary_keys_and_reports_each_transition(watcher, runner, db):
    started(watcher, runner, "S1", "S2", "S3", "S4")
    watcher.tick()
    runner.answer(statuses(S1=3, S2=1, S3=2))  # S4 查不到（例如被刪掉）
    assert db.executed == [("SELECT service_id, service_status FROM tbl_service WHERE service_id IN (%s, %s, %s, %s)",
                            ("S1", "S2", "S3", "S4"))]
    assert watcher.events == [("pollSucceeded",), ("finished", "S1"), ("failed", "S2")]
    assert watcher.watched == ["S3"]  # 還在分析的繼續看；查不到的不再看，也不通知


def test_skips_a_tick_while_the_previous_poll_is_still_running(watcher, runner):
    started(watcher, runner, "S1")
    watcher.tick()
    watcher.tick()  # 上一次還沒回來：略過
    assert [task.key for task in runner.submitted] == [POLL_KEY, POLL_KEY]  # 第一次的查詢＋一次輪詢
    assert watcher.polling  # 照樣排下一次
    runner.answer(statuses(S1=3))
    assert watcher.events == [("pollSucceeded",), ("finished", "S1")]


def test_service_sent_during_a_poll_is_kept(watcher, runner):
    started(watcher, runner, "S1")
    watcher.tick()  # 只問 S1
    watcher.add("S2")
    runner.answer(statuses(S1=2))
    assert watcher.watched == ["S1", "S2"]


def test_failed_poll_is_reported_and_retried(watcher, runner):
    started(watcher, runner, "S1")
    watcher.tick()
    error = TimeoutError("read timeout")
    runner.answer(broken=error)
    assert watcher.events == [("pollFailed", error)]
    assert watcher.watched == ["S1"] and watcher.polling


def test_polling_stops_when_nothing_is_left_and_starts_again_on_a_new_send(watcher, runner):
    started(watcher, runner, "S1")
    watcher.tick()
    runner.answer(statuses(S1=3))
    assert not watcher.polling
    watcher.add("S2")
    assert watcher.polling and watcher.next_poll_ms == 15_000
    watcher.tick()
    assert runner.submitted[-1].key == POLL_KEY


def test_stop_cancels_the_poll_and_the_timer(watcher, runner):
    started(watcher, runner, "S1")
    watcher.tick()
    watcher.stop()
    assert runner.cancelled == [POLL_KEY] and not watcher.polling
    watcher.tick()
    watcher.add("S2")
    assert len(runner.submitted) == 2 and not watcher.polling  # 外殼關了：不再查


def test_timer_triggers_the_poll(watcher, runner, monkeypatch):
    monkeypatch.setattr(watcher_module, "BACKOFF", ((math.inf, 0.01),))  # 10 ms：不必真的等 15 秒
    started(watcher, runner, "S1")
    deadline = time.monotonic() + 5
    while len(runner.submitted) < 2:
        assert time.monotonic() < deadline, "計時器沒有觸發"
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert runner.submitted[-1].key == POLL_KEY


# ── Dock：app 不在前景時 ──────────────────────────────────────────────────────

class FakeWindow:
    def __init__(self):
        self.alerts = []

    def alert(self, msec):
        self.alerts.append(msec)


@pytest.fixture
def dock(monkeypatch):
    """app 的狀態由測試決定；記下 Dock 徽章被設成的數字。"""
    state = SimpleNamespace(value=Qt.ApplicationState.ApplicationInactive, badges=[])
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: state.value))
    monkeypatch.setattr(QGuiApplication, "setBadgeNumber", lambda app, number: state.badges.append(number))
    return state


def set_app_state(state):
    QGuiApplication.instance().applicationStateChanged.emit(state)


def test_dock_bounces_and_counts_while_the_app_is_in_the_background(dock):
    attention, window = DockAttention(), FakeWindow()
    assert attention.request(window) and attention.request(window)
    assert (window.alerts, dock.badges, attention.unread) == ([0, 0], [1, 2], 2)  # 0：跳到使用者切回來為止
    set_app_state(Qt.ApplicationState.ApplicationActive)
    assert (dock.badges[-1], attention.unread) == (0, 0)  # 切回 app：徽章清掉


def test_dock_stays_quiet_while_the_app_is_in_front(dock):
    dock.value = Qt.ApplicationState.ApplicationActive
    attention, window = DockAttention(), FakeWindow()
    assert attention.request(window) is False
    assert (window.alerts, dock.badges) == ([], [])  # 在前景時 toast 就夠了
    attention.clear()
    assert dock.badges == []  # 沒有徽章就不必清


def test_dock_badge_stays_until_the_app_is_active_again(dock):
    attention = DockAttention()
    assert attention.request(None)  # 沒有視窗也照樣加徽章
    set_app_state(Qt.ApplicationState.ApplicationHidden)
    assert (dock.badges, attention.unread) == ([1], 1)  # 還沒切回來：徽章留著
    attention.clear()
    assert dock.badges == [1, 0]
