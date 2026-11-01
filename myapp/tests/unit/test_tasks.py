"""tasks.py：TaskRunner、CancelToken、Throttle（ENG-11、ENG-13、ENG-14）。

工作什麼時候結束由測試用 threading.Event 決定，等結果用 wait_until（一邊處理 Qt 事件），不用固定的 sleep。
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QQmlContext, QQmlEngine, QQmlExpression

from myapp.tasks import Cancelled, CancelToken, TaskRunner, Throttle
from tests.integration.helpers import wait_until

GUI = threading.get_ident()


@pytest.fixture
def runner():
    tasks = TaskRunner()
    yield tasks
    tasks.shutdown()


@pytest.fixture
def events(runner):
    """依序記下 runner 發出的 signal。"""
    seen = []
    runner.busyChanged.connect(lambda key, busy: seen.append(("busy", key, busy)))
    runner.progressed.connect(lambda key, done, total: seen.append(("progress", key, done, total)))
    runner.failed.connect(lambda key, error: seen.append(("failed", key, error)))
    return seen


def blocked_until(gate, result=None):
    """一個工作：等 gate 打開才回傳 result。"""
    def work(token, report):
        assert gate.wait(5), "測試沒有打開 gate"
        return result
    return work


def raises(error):
    def work(token, report):
        raise error
    return work


# ---------------------------------------------------------------- 結果、錯誤、忙碌狀態


def test_result_arrives_on_gui_thread_and_busy_toggles(runner, events):
    gate = threading.Event()
    got = []
    assert runner.submit("db", "analysis:refresh", blocked_until(gate, 42),
                         on_ok=lambda result: got.append((result, threading.get_ident())))
    assert runner.isBusy("analysis:refresh")
    assert runner.submit("db", "analysis:refresh", lambda token, report: 0) is False  # 防重複送出
    gate.set()
    wait_until(lambda: got)
    assert got == [(42, GUI)]
    assert events == [("busy", "analysis:refresh", True), ("busy", "analysis:refresh", False)]
    assert not runner.isBusy("analysis:refresh")
    assert runner.submit("db", "analysis:refresh", lambda token, report: 0)  # 結束之後可以再送


@pytest.mark.parametrize("lane", ["db", "io", "pdf"])
def test_work_runs_on_its_lane_thread(runner, lane):
    names = []
    runner.submit(lane, "where", lambda token, report: threading.current_thread().name, on_ok=names.append)
    wait_until(lambda: names)
    assert names[0].startswith(f"{lane}-lane")


def test_error_goes_to_on_err_on_gui_thread(runner, events):
    errors = []
    runner.submit("io", "analyze:S1", raises(ConnectionError("down")),
                  on_err=lambda error: errors.append((error, threading.get_ident())))
    wait_until(lambda: errors)
    assert isinstance(errors[0][0], ConnectionError) and errors[0][1] == GUI
    assert [event for event in events if event[0] == "failed"] == []  # 有 on_err 就不發 failed


def test_error_without_on_err_emits_failed(runner, events):
    runner.submit("io", "analyze:S1", raises(ConnectionError("down")))
    wait_until(lambda: any(event[0] == "failed" for event in events))
    kind, key, error = events[-1]
    assert (kind, key, type(error)) == ("failed", "analyze:S1", ConnectionError)


def test_callback_error_is_logged_and_key_is_freed(runner, caplog):
    def broken(result):
        raise KeyError("bug in the page")

    runner.submit("db", "k", lambda token, report: 1, on_ok=broken)
    wait_until(lambda: not runner.isBusy("k"))
    assert "callback of task 'k' failed" in caplog.text
    assert runner.submit("db", "k", lambda token, report: 1)


# ---------------------------------------------------------------- 取消、取代、結束


def test_cancel_running_task_is_silent(runner, events):
    started, outcome = threading.Event(), []

    def work(token, report):
        stopped = threading.Event()
        token.on_cancel(stopped.set)  # 像 S3 的 TransferFuture.cancel()
        started.set()
        stopped.wait(5)
        token.raise_if_cancelled()
        return "finished"

    runner.submit("io", "upload:S1", work, on_ok=outcome.append, on_err=outcome.append)
    assert started.wait(5)
    runner.cancel("upload:S1")
    wait_until(lambda: not runner.isBusy("upload:S1"))
    assert outcome == []
    assert ("busy", "upload:S1", False) in events
    assert [event for event in events if event[0] == "failed"] == []


def test_result_of_cancelled_task_is_dropped(runner):
    """工作沒有檢查 token、照樣做完了：cancel 之後還是不交回結果（頁面可能已經關掉）。"""
    gate, outcome = threading.Event(), []
    runner.submit("io", "media:S1", blocked_until(gate, "file.avi"), on_ok=outcome.append)
    runner.cancel("media:S1")
    assert runner.isBusy("media:S1")  # 工作還沒停下來
    gate.set()
    wait_until(lambda: not runner.isBusy("media:S1"))
    assert outcome == []


def test_cancel_before_start_never_runs_the_work(runner, events):
    gate, ran = threading.Event(), []
    runner.submit("db", "first", blocked_until(gate))  # db lane 只有一條執行緒：第二個要排隊
    runner.submit("db", "second", lambda token, report: ran.append(True))
    runner.cancel("second")
    wait_until(lambda: not runner.isBusy("second"))
    assert runner.isBusy("first")
    gate.set()
    wait_until(lambda: not runner.isBusy("first"))
    assert ran == []


def test_work_that_raises_cancelled_is_silent(runner, events):
    outcome = []
    runner.submit("io", "k", raises(Cancelled()), on_err=outcome.append)
    wait_until(lambda: not runner.isBusy("k"))
    assert outcome == [] and [event for event in events if event[0] == "failed"] == []


def test_cancel_unknown_key_does_nothing(runner):
    runner.cancel("nothing-running")


def test_replace_cancels_old_task_and_delivers_only_the_new_one(runner, events):
    old_gate, new_gate, old_tokens, got = threading.Event(), threading.Event(), [], []

    def old(token, report):
        old_tokens.append(token)
        old_gate.wait(5)
        return "old"

    runner.submit("io", "search", old, on_ok=got.append)
    assert runner.submit("io", "search", blocked_until(new_gate, "new"), on_ok=got.append, replace=True)
    wait_until(lambda: old_tokens)
    assert old_tokens[0].cancelled
    old_gate.set()  # 舊的照樣做完：結果不能蓋掉新的
    new_gate.set()
    wait_until(lambda: got)
    wait_until(lambda: not runner.isBusy("search"))
    assert got == ["new"]
    assert events == [("busy", "search", True), ("busy", "search", False)]  # 換的時候不閃一下 False


def test_shutdown_cancels_everything_and_refuses_new_work(runner):
    gate, tokens, outcome = threading.Event(), [], []

    def running(token, report):
        tokens.append(token)
        gate.wait(5)
        return "late"

    runner.submit("db", "running", running, on_ok=outcome.append)
    runner.submit("db", "queued", lambda token, report: outcome.append("queued ran"))
    wait_until(lambda: tokens)
    runner.shutdown()
    assert tokens[0].cancelled
    assert not runner.isBusy("running") and not runner.isBusy("queued")
    assert runner.submit("io", "after", lambda token, report: 1) is False
    gate.set()
    QCoreApplication.processEvents()
    assert outcome == []
    runner.shutdown()  # 呼叫兩次沒關係


# ---------------------------------------------------------------- 進度


def test_progress_is_throttled_and_last_value_arrives_before_result(runner, events):
    def work(token, report):
        for _ in range(100):
            report(1, 100)
        return "done"

    runner.submit("io", "upload:S1", work, on_ok=lambda result: events.append(("ok", result)))
    wait_until(lambda: ("ok", "done") in events)
    progress = [event for event in events if event[0] == "progress"]
    assert progress[0] == ("progress", "upload:S1", 1.0, 100.0)  # 第一次立刻送出
    assert progress[-1] == ("progress", "upload:S1", 100.0, 100.0)  # 完成一定送出
    assert len(progress) < 10  # 100 次回報被節流
    assert events.index(progress[-1]) < events.index(("ok", "done"))


def test_final_progress_with_unknown_total_is_flushed(runner, events):
    def work(token, report):
        for _ in range(5):
            report(10, 0)  # 總量不知道
        return None

    runner.submit("io", "download", work)
    wait_until(lambda: not runner.isBusy("download"))
    progress = [event for event in events if event[0] == "progress"]
    assert progress[-1] == ("progress", "download", 50.0, 0.0)


def test_progress_of_replaced_task_is_dropped(runner, events):
    old_gate, old_done, new_gate = threading.Event(), threading.Event(), threading.Event()

    def old(token, report):
        old_gate.wait(5)
        report(1, 10)
        old_done.set()

    runner.submit("io", "search", old)
    runner.submit("io", "search", blocked_until(new_gate), replace=True)
    old_gate.set()
    assert old_done.wait(5)  # 舊工作的進度已經排進 GUI 執行緒的佇列
    QCoreApplication.processEvents()  # 處理掉它
    assert [event for event in events if event[0] == "progress"] == []
    new_gate.set()
    wait_until(lambda: not runner.isBusy("search"))


# ---------------------------------------------------------------- lane 與執行緒的規則


def test_custom_lanes_accept_thread_counts_and_executors():
    executor = ThreadPoolExecutor(1, thread_name_prefix="custom")
    tasks = TaskRunner(lanes={"db": executor, "net": 2})
    names = []
    tasks.submit("db", "a", lambda token, report: threading.current_thread().name, on_ok=names.append)
    tasks.submit("net", "b", lambda token, report: threading.current_thread().name, on_ok=names.append)
    wait_until(lambda: len(names) == 2)
    assert sorted(name.split("_")[0] for name in names) == ["custom", "net-lane"]
    with pytest.raises(ValueError, match="unknown lane 'io'"):
        tasks.submit("io", "c", lambda token, report: 1)
    tasks.shutdown()


def test_submit_and_cancel_only_from_gui_thread(runner):
    errors = []

    def from_worker():
        for call in (lambda: runner.submit("db", "k", lambda token, report: 1), lambda: runner.cancel("k")):
            try:
                call()
            except RuntimeError as error:
                errors.append(str(error))

    thread = threading.Thread(target=from_worker)
    thread.start()
    thread.join(5)
    assert errors == ["TaskRunner must be used from the GUI thread"] * 2


def test_qml_can_check_busy_and_cancel(runner):
    gate = threading.Event()
    runner.submit("io", "media:S1", blocked_until(gate))
    engine = QQmlEngine()
    context = QQmlContext(engine.rootContext())
    context.setContextProperty("tasks", runner)
    expression = QQmlExpression(context, None, 'tasks.isBusy("media:S1") && !tasks.isBusy("other")')
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    assert value is True
    QQmlExpression(context, None, 'tasks.cancel("media:S1")').evaluate()
    gate.set()
    wait_until(lambda: not runner.isBusy("media:S1"))


def test_results_after_runner_is_deleted_are_ignored(caplog):
    executor = ThreadPoolExecutor(1)
    tasks = TaskRunner(lanes={"db": executor})
    gate = threading.Event()

    def work(token, report):
        gate.wait(5)
        report(1, 2)  # 進度也送不出去，但不能丟例外

    tasks.submit("db", "k", work)
    shiboken6.delete(tasks)  # 例如程式結束時，物件比背景工作先被刪掉
    gate.set()
    executor.shutdown(wait=True)  # 等工作和 done callback 都跑完
    assert caplog.records == []


# ---------------------------------------------------------------- CancelToken


def test_cancel_token_calls_each_callback_once():
    token, calls = CancelToken(), []
    token.on_cancel(lambda: calls.append("transfer"))
    assert not token.cancelled
    token.raise_if_cancelled()  # 還沒取消：不丟例外
    token.cancel()
    token.cancel()  # 重複呼叫沒關係
    assert token.cancelled and calls == ["transfer"]
    token.on_cancel(lambda: calls.append("late"))  # 已經取消：立刻呼叫
    assert calls == ["transfer", "late"]
    with pytest.raises(Cancelled):
        token.raise_if_cancelled()


def test_cancel_token_from_another_thread_and_failing_callback(caplog):
    token, calls = CancelToken(), []
    token.on_cancel(lambda: 1 / 0)
    token.on_cancel(lambda: calls.append("still called"))
    thread = threading.Thread(target=token.cancel)
    thread.start()
    thread.join(5)
    assert token.cancelled and calls == ["still called"]
    assert "cancel callback" in caplog.text and caplog.records[0].levelno == logging.ERROR


# ---------------------------------------------------------------- Throttle（假時鐘）


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_throttle_limits_reports_to_4_per_second():
    clock, sent = Clock(), []
    throttle = Throttle(lambda done, total: sent.append((done, total)), hz=4, clock=clock)
    for i in range(50):  # 0.49 秒內 50 次
        clock.now = i * 0.01
        throttle(1, 100)
    assert sent == [(1, 100), (26, 100)]  # 第一次立刻送，之後每 0.25 秒最多一次
    throttle(50, 100)  # 完成一定立刻送出
    assert sent[-1] == (100, 100)
    throttle.flush()
    assert len(sent) == 3  # 沒有被擋下來的值，flush 不重送


def test_throttle_flush_sends_the_last_suppressed_value():
    clock, sent = Clock(), []
    throttle = Throttle(lambda done, total: sent.append((done, total)), clock=clock)
    throttle(10, 0)
    throttle(10, 0)
    throttle(10, 0)
    assert sent == [(10, 0)]
    throttle.flush()
    assert sent == [(10, 0), (30, 0)]  # 總量不知道時照樣累加


def test_throttle_clamps_and_skips_unchanged_values():
    clock, sent = Clock(), []
    throttle = Throttle(lambda done, total: sent.append((done, total)), clock=clock)
    throttle(-5, 100)  # s3transfer 重試時會倒退
    clock.now = 1
    throttle(0, 100)  # 值沒變，不送
    clock.now = 2
    throttle(250, 100)
    assert sent == [(0, 100), (100, 100)]


def test_throttle_sums_reports_from_many_threads_in_order():
    sent = []
    throttle = Throttle(lambda done, total: sent.append(done), hz=1000)
    threads = [threading.Thread(target=lambda: [throttle(1, 4000) for _ in range(1000)]) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    throttle.flush()
    assert sent[-1] == 4000
    assert sent == sorted(sent)  # 在鎖裡送出：不會先送新的、後送舊的


def test_throttle_needs_a_positive_rate():
    with pytest.raises(ValueError):
        Throttle(print, hz=0)
