"""分析完成的監看（ENG-16、UX-17）：不管在哪一頁、app 在不在前景，分析完成或失敗都會知道。

AnalysisWatcher（服務人員端，server_home_init.launch() 建立）：
- 監看哪些服務：start() 先在 db lane 查一次這個帳號分析中（狀態 2）的服務；之後每次送出分析成功就 add(sid)。
- 輪詢：還有要看的服務（或第一次查詢還沒成功）才計時。GUI 執行緒只負責計時，查詢在 db lane，
  用主鍵 IN (...) 一次查完（1 個往返）；上一次還沒回來就略過這一次。
- 間隔：前 10 分鐘每 15 秒，之後每 30 秒，30 分鐘後每 60 秒，從最後一次送出（或 start()）算起；
  有新的送出就回到 15 秒。
- 狀態轉換：2 → 3 發 finished(sid)；2 → 1 發 failed(sid)（分析服務失敗時會把 2 改回 1，D4）；查不到就不再看。
- 每次查詢成功發 pollSucceeded()、失敗發 pollFailed(error)。「Can't refresh analysis status」那一則警告
  由 ServiceModel 決定要不要跳，和清單的重新整理共用同一則。

DockAttention：app 不在前景時，Dock 圖示跳動、加上徽章（ENG-17、D6），切回 app 時清掉徽章。不用系統通知的橫幅。
帳號的偏好「Bounce Dock Icon」關掉時只是不跳，徽章照舊（new-feature PLAN 13.3 S2，T39）。
"""
import logging
import math
import time

import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

logger = logging.getLogger(__name__)

POLL_KEY = "watcher:poll"  # TaskRunner 的 key（db lane）：第一次的查詢和之後的輪詢都用這個，同時只跑一個
# （從最後一次送出算起的秒數上限, 間隔秒數）
BACKOFF = ((10 * 60, 15), (30 * 60, 30), (math.inf, 60))

# 登入後查一次：這個帳號分析中的服務。會用到 idx_service_account_status_start（db_schema.sql）
IN_FLIGHT_SQL = "SELECT service_id FROM tbl_service WHERE account_id = %s AND service_status = 2"
# 輪詢：主鍵查找
STATUS_SQL = "SELECT service_id, service_status FROM tbl_service WHERE service_id IN ({placeholders})"


def poll_interval(elapsed):
    """從最後一次送出經過 elapsed 秒時，輪詢的間隔（秒）。"""
    for limit, interval in BACKOFF:
        if elapsed < limit:
            return interval
    return BACKOFF[-1][1]  # pragma: no cover  最後一段的上限是無限大


def in_flight_services(db, account_id):
    """在 db lane 執行：這個帳號分析中（狀態 2）的服務 ID。1 個往返。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(IN_FLIGHT_SQL, (account_id,))
        return [str(row["service_id"]) for row in cursor.fetchall()]


def service_statuses(db, service_ids):
    """在 db lane 執行：{service_id: 狀態}，查不到的不在裡面。1 個往返。"""
    sql = STATUS_SQL.format(placeholders=", ".join(["%s"] * len(service_ids)))
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(sql, tuple(service_ids))
        return {str(row["service_id"]): int(row["service_status"]) for row in cursor.fetchall()}


class AnalysisWatcher(QObject):
    """監看分析中的服務，狀態變了就發 signal。只在 GUI 執行緒使用；tasks 是 TaskRunner，db 是 DbLane。"""

    finished = Signal(str)       # 2 → 3：分析完成
    failed = Signal(str)         # 2 → 1：分析失敗（D4）
    pollFailed = Signal(object)  # 這一次沒查到（例外）；下一次照間隔再試
    pollSucceeded = Signal()

    def __init__(self, tasks, db, account_id, parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self._tasks = tasks
        self._db = db
        self._account_id = account_id
        self._clock = clock
        self._watched = set()    # 分析中（狀態 2）的服務
        self._seeded = False     # start() 的第一次查詢成功了沒
        self._started = False
        self._since = clock()    # 最後一次送出（或 start()）的時間：決定間隔
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)  # 每次觸發後依當時的間隔重新計時，退避才會生效
        self._timer.timeout.connect(self.tick)

    @property
    def watched(self):
        return sorted(self._watched)

    @property
    def polling(self):
        """計時器開著：還有要看的服務，或第一次查詢還沒成功。"""
        return self._timer.isActive()

    @property
    def next_poll_ms(self):
        """目前排定的間隔（毫秒）；沒有在計時是 0。"""
        return self._timer.interval() if self._timer.isActive() else 0

    def interval(self):
        """下一次查詢的間隔（秒）：前 10 分鐘 15、之後 30、30 分鐘後 60，從最後一次送出（或 start()）算起。"""
        return poll_interval(self._clock() - self._since)

    def start(self):
        """登入後：在 db lane 查這個帳號分析中的服務（1 個往返）。沒查成功就照間隔一直重試。"""
        self._started = True
        self._since = self._clock()
        self._query()
        self._arm(restart=True)

    def stop(self):
        """外殼關掉時：不再計時，還沒回來的查詢結果也不要了。"""
        self._started = False
        self._timer.stop()
        self._tasks.cancel(POLL_KEY)

    @Slot(str)
    def add(self, service_id):
        """送出分析成功（狀態已經是 2）：開始看這一筆，間隔回到 15 秒。"""
        self._watched.add(service_id)
        self._since = self._clock()
        self._arm()

    @Slot()
    def tick(self):
        """計時器到了（測試也可以直接呼叫）：上一次的查詢還沒回來就略過這一次，然後依目前的間隔重新計時。"""
        if not self._started:
            return
        if not self._tasks.isBusy(POLL_KEY):
            self._query()
        self._arm(restart=True)

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    def _query(self):
        db, account_id = self._db, self._account_id
        if not self._seeded:
            self._tasks.submit("db", POLL_KEY, lambda token, report: in_flight_services(db, account_id),
                               on_ok=self._seeded_with, on_err=self._poll_failed)
            return
        if not self._watched:
            return
        asked = tuple(sorted(self._watched))  # 在 GUI 執行緒先複製一份，工作的執行緒不碰 self._watched
        self._tasks.submit("db", POLL_KEY, lambda token, report: service_statuses(db, asked),
                           on_ok=lambda statuses: self._polled(asked, statuses), on_err=self._poll_failed)

    def _seeded_with(self, service_ids):
        self._seeded = True
        self._watched.update(service_ids)
        self.pollSucceeded.emit()
        self._arm()

    def _polled(self, asked, statuses):
        self.pollSucceeded.emit()  # 先關掉「更新不到」的警告，再跳結果的通知
        for service_id in asked:  # 查詢期間新加的不在 asked 裡，這次不動它
            status = statuses.get(service_id)
            if status == 2:
                continue
            self._watched.discard(service_id)  # 完成、失敗、查不到（例如被刪掉）：都不再看
            if status == 3:
                self.finished.emit(service_id)
            elif status == 1:
                self.failed.emit(service_id)
        self._arm()

    def _poll_failed(self, error):
        logger.warning("analysis watcher poll failed: %r", error)
        self.pollFailed.emit(error)

    def _arm(self, restart=False):
        """還有要看的服務（或第一次查詢還沒成功）才計時。間隔變短時（新的送出）提早到新的間隔；
        restart 表示從現在開始重新計時（剛觸發過）。"""
        if not self._started or (self._seeded and not self._watched):
            self._timer.stop()
            return
        interval_ms = round(self.interval() * 1000)
        if restart or not self._timer.isActive() or self._timer.remainingTime() > interval_ms:
            self._timer.start(interval_ms)


class DockAttention(QObject):
    """app 不在前景時，讓使用者知道分析完成或失敗了（ENG-17、D6）。

    - Dock 圖示跳動：外殼視窗的 alert(0)，會一直跳到使用者切回 app。prefs（UiPrefs）的 bounceDockIcon 是 False 時不跳
      （S2）；每次提醒時才讀，設定頁改了馬上生效。沒有 prefs 時照舊會跳。
    - Dock 圖示的徽章：這段期間收到幾則（完成＋失敗）。切回 app 時清掉。不受 bounceDockIcon 影響。
    - app 在前景時什麼都不做：toast 就夠了。不用系統通知中心的橫幅（要打包、要授權，PLAN F6）。
    """

    def __init__(self, parent=None, *, prefs=None):
        super().__init__(parent)
        self._unread = 0
        self._prefs = prefs
        QGuiApplication.instance().applicationStateChanged.connect(self._state_changed)

    @property
    def unread(self):
        return self._unread

    @property
    def bounces(self):
        """不在前景時要不要讓 Dock 圖示跳動（帳號的偏好，S2）。"""
        return self._prefs is None or self._prefs.bounceDockIcon

    def request(self, window):
        """分析完成或失敗時呼叫。回傳是否真的提醒了（app 不在前景）。"""
        if QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
            return False
        if self.bounces and window is not None and shiboken6.isValid(window):
            window.alert(0)
        self._unread += 1
        QGuiApplication.instance().setBadgeNumber(self._unread)
        return True

    def clear(self):
        if not self._unread:
            return
        self._unread = 0
        app = QGuiApplication.instance()
        if app is not None:  # 程式結束時外殼最後才關，app 可能已經不在了
            app.setBadgeNumber(0)

    @Slot(Qt.ApplicationState)
    def _state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.clear()
