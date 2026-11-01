"""背景工作：TaskRunner（db、io、pdf 三種 lane）、CancelToken、Throttle（ENG-11、ENG-13、ENG-14）。

網路、檔案、PDF 的工作都不在 GUI 執行緒做。頁面的 Python 物件在 GUI 執行緒呼叫 submit()，
工作在 lane 的執行緒執行，結果一律用 queued signal 帶回 GUI 執行緒：

    def work(token, report):                 # 在 lane 的執行緒執行
        for path in paths:
            token.raise_if_cancelled()       # 被取消就丟 Cancelled，不算錯誤
            upload(path)
            report(1, len(paths))            # 「這次又完成多少」，Throttle 會累加（見 Throttle）
        return len(paths)

    tasks.submit("io", f"upload:{sid}", work, on_ok=saved, on_err=failed)

- lane：db 只有 1 條執行緒（擁有 DbLane 的 MySQL 連線，ENG-10）；io 4 條（S3、HTTP）；pdf 1 條（PyMuPDF 不支援多執行緒）。
- key：同一個 key 還在跑就拒絕（防重複送出，ENG-14）；replace=True 則取消舊的、改跑新的（搜尋）。
- on_ok(result)、on_err(exc) 都在 GUI 執行緒呼叫，可以直接改屬性。沒給 on_err 時發 failed(key, exc)。
- 取消：cancel(key) 之後，這次送出的 on_ok、on_err 都不會被呼叫；工作真的停下來時才發 busyChanged(key, False)。
"""
import logging
import threading
import time
from concurrent.futures import CancelledError, Executor, ThreadPoolExecutor
from dataclasses import dataclass
from itertools import count

from PySide6.QtCore import QObject, Qt, Signal, Slot

logger = logging.getLogger(__name__)

DEFAULT_LANES = {"db": 1, "io": 4, "pdf": 1}  # lane 名稱 → 執行緒數


class Cancelled(Exception):
    """工作發現自己被取消時丟出（CancelToken.raise_if_cancelled）。TaskRunner 不把它當成錯誤。"""


class CancelToken:
    """取消的旗標：任何執行緒都能 cancel()；工作自己檢查，或用 on_cancel() 掛上取消時要做的事。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._callbacks = []

    @property
    def cancelled(self):
        return self._cancelled

    def cancel(self):
        """可以從任何執行緒呼叫，重複呼叫沒關係；每個 on_cancel 的 callback 只會被呼叫一次。"""
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            self._run(callback)

    def on_cancel(self, callback):
        """取消時呼叫 callback（例如 s3transfer 的 TransferFuture.cancel）；已經取消了就立刻呼叫。"""
        with self._lock:
            if not self._cancelled:
                self._callbacks.append(callback)
                return
        self._run(callback)

    def raise_if_cancelled(self):
        if self._cancelled:
            raise Cancelled()

    @staticmethod
    def _run(callback):
        try:
            callback()
        except Exception:  # 取消是「盡量」：一個 callback 失敗，不能讓其他的不執行，也不能讓 cancel() 丟例外
            logger.exception("cancel callback %r failed", callback)


class Throttle:
    """把很密的進度回報降到每秒最多 hz 次（DS-23：4 次／秒）。

    - `__call__(done, total)`：done 是「這一次又完成的量」，會被累加。s3transfer 的 Callback 一次給
      一段 bytes（重試時會是負的），而且同時來自好幾條執行緒，所以累加要加鎖。total 是目前知道的總量，
      0 表示還不知道（下載要等 HEAD 之後才知道大小）。
    - `report(done, total)` 收到的是累計值，夾在 0～total 之間（total 為 0 時只夾下限）。
    - 第一次立刻送出；累計到 total 時（完成）也立刻送出；其餘的最多每 1/hz 秒一次。數值沒變就不送。
    - `flush()`：補送最後一次被擋下來的值。工作結束時一定要呼叫，TaskRunner 會自己呼叫。
    - report 是在鎖裡面呼叫的（送出的順序和累加的順序一致，最後送出的一定是最新的值），
      所以 report 要很快（例如 emit 一個 queued signal），而且不能回頭呼叫同一個 Throttle。
    """

    def __init__(self, report, hz=4, clock=time.monotonic):
        if hz <= 0:
            raise ValueError("hz must be positive")
        self._report = report
        self._gap = 1.0 / hz
        self._clock = clock
        self._lock = threading.Lock()
        self._done = 0
        self._total = 0
        self._last_time = None
        self._last_value = None
        self._pending = False

    def __call__(self, done, total):
        with self._lock:
            self._done += done
            self._total = total
            now = self._clock()
            finished = 0 < self._total <= self._done
            if not finished and self._last_time is not None and now - self._last_time < self._gap:
                self._pending = True
                return
            self._send(now)

    def flush(self):
        with self._lock:
            if self._pending:
                self._send(self._clock())

    def _send(self, now):
        self._pending = False
        done = max(self._done, 0)
        if self._total > 0:
            done = min(done, self._total)
        value = (done, self._total)
        if value == self._last_value:
            return
        self._last_time, self._last_value = now, value
        self._report(*value)


@dataclass
class _Task:
    id: int
    future: object
    token: CancelToken
    on_ok: object
    on_err: object
    cancelled: bool = False  # cancel(key) 叫過了：不再交回結果


class TaskRunner(QObject):
    """在 GUI 執行緒上管理各 lane 的背景工作。submit、cancel、isBusy、shutdown 都要在 GUI 執行緒呼叫。"""

    busyChanged = Signal(str, bool)          # key, busy：按鈕停用、忙碌狀態（DS-24）
    progressed = Signal(str, float, float)   # key, done, total（total 0＝不知道總量）；已節流到 4 次／秒
    failed = Signal(str, object)             # key, exception：只有沒給 on_err 時才發
    _finished = Signal(str, int, object, object)  # key, 工作編號, result, error（從工作的執行緒送出）
    _progress = Signal(str, int, float, float)    # key, 工作編號, done, total

    def __init__(self, parent=None, lanes=None):
        """lanes：{名稱: 執行緒數或 Executor}，預設 {"db": 1, "io": 4, "pdf": 1}；測試可以換掉。"""
        super().__init__(parent)
        specs = DEFAULT_LANES if lanes is None else lanes
        self._lanes = {
            name: spec if isinstance(spec, Executor) else ThreadPoolExecutor(spec, thread_name_prefix=f"{name}-lane")
            for name, spec in specs.items()
        }
        self._running = {}  # key → _Task
        self._ids = count(1)
        self._closed = False
        self._gui_thread = threading.get_ident()
        # 一律 queued：add_done_callback 可能在 GUI 執行緒當場執行（工作已經結束、或開始前就被取消），
        # 不能因此變成同步呼叫 on_ok／on_err
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._progress.connect(self._forward_progress, Qt.ConnectionType.QueuedConnection)

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        """在 lane 的執行緒執行 fn(token, report)，report(done, total) 見 Throttle。

        回傳 True 表示已送出。同一個 key 還在跑時：replace=False 回傳 False（防重複送出）；
        replace=True 取消舊的、送出新的，舊的結果不會交回，忙碌狀態維持 True。
        shutdown() 之後一律回傳 False。
        """
        self._require_gui_thread()
        if self._closed:
            logger.warning("task %r submitted after shutdown; ignored", key)
            return False
        executor = self._lanes.get(lane)
        if executor is None:
            raise ValueError(f"unknown lane {lane!r}; lanes are {sorted(self._lanes)}")
        old = self._running.get(key)
        if old is not None:
            if not replace:
                return False
            self._cancel_task(old)
        task_id = next(self._ids)
        token = CancelToken()
        throttle = Throttle(lambda done, total: self._emit(self._progress, key, task_id, done, total))
        future = executor.submit(fn, token, throttle)
        self._running[key] = _Task(task_id, future, token, on_ok, on_err)
        if old is None:
            self.busyChanged.emit(key, True)
        future.add_done_callback(lambda done: self._on_done(key, task_id, throttle, done))
        return True

    @Slot(str, result=bool)
    def isBusy(self, key):
        return key in self._running

    @Slot(str)
    def cancel(self, key):
        """還沒開始的直接取消；已經開始的設定 token，由工作自己停下來。之後不會呼叫 on_ok、on_err。"""
        self._require_gui_thread()
        task = self._running.get(key)
        if task is not None:
            self._cancel_task(task)

    def shutdown(self):
        """程式結束時（aboutToQuit）：取消全部的 token，不等工作結束；之後的結果都不交回。"""
        self._closed = True
        tasks, self._running = list(self._running.values()), {}
        for task in tasks:
            task.cancelled = True
            task.token.cancel()
        for executor in self._lanes.values():
            executor.shutdown(wait=False, cancel_futures=True)

    def _require_gui_thread(self):
        if threading.get_ident() != self._gui_thread:
            raise RuntimeError("TaskRunner must be used from the GUI thread")

    @staticmethod
    def _cancel_task(task):
        task.cancelled = True
        task.future.cancel()  # 還沒開始的直接取消（done callback 會照常送回 _finished）
        task.token.cancel()   # 已經開始的：工作自己檢查 token；S3 由 on_cancel 呼叫 TransferFuture.cancel()

    def _on_done(self, key, task_id, throttle, future):
        """在工作的執行緒執行（開始前就被取消時，在呼叫 cancel 的執行緒）。"""
        throttle.flush()  # 最後一次進度在結果之前送出：同一條執行緒、同一個佇列，順序不會變
        if future.cancelled():
            result, error = None, Cancelled()
        else:
            error = future.exception()
            result = None if error is not None else future.result()
        self._emit(self._finished, key, task_id, result, error)

    @staticmethod
    def _emit(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:  # TaskRunner 已經被刪掉（程式結束中），結果沒有人要了
            pass

    def _forward_progress(self, key, task_id, done, total):
        task = self._running.get(key)
        if task is not None and task.id == task_id and not task.cancelled:
            self.progressed.emit(key, done, total)

    def _deliver(self, key, task_id, result, error):
        """在 GUI 執行緒執行。"""
        task = self._running.get(key)
        if task is None or task.id != task_id:
            return  # 已經被 replace 換掉，或是 shutdown 之後才結束
        del self._running[key]
        self.busyChanged.emit(key, False)
        if task.cancelled or isinstance(error, (Cancelled, CancelledError)):
            return  # 使用者自己取消的，不顯示錯誤
        try:
            if error is None:
                if task.on_ok is not None:
                    task.on_ok(result)
            elif task.on_err is not None:
                task.on_err(error)
            else:
                self.failed.emit(key, error)
        except Exception:  # callback 自己的 bug：記下來，不要讓事件迴圈停下來
            logger.exception("callback of task %r failed", key)
