"""服務人員端（登入後的畫面）的 Python 物件。

F2（前端重建第二波）：資料庫的讀寫都搬到 db lane（ENG-01、ENG-06、ENG-10）。
- launch() 先把外殼顯示出來（GUI 執行緒 0 個網路往返），帳號名稱、首頁的三張卡片（homeDashboard）在 db lane 載入；
  分析清單等打開分析頁才查（新版介面 U1，T28：launch 的往返預算）。
- 頁面的資料一律是屬性（Property＋notify），清單是增量模型（RowListModel），不再用一次性的 signal 推資料（ENG-08）。
  每個區塊都有 loading、error、ready 三種狀態；「沒有資料」由頁面依資料判斷（DS-25～27、DS-33）。
- db lane 的工作都是 fn(token, report)，裡面用 services.db.cursor()；預存程序用 execute("CALL …")。
- F3、F4（第三波）：錄影存檔在背景（record_widget.py 的 RecordingSaver）、明細頁和報表在 media.py、
  送出分析在 ServiceModel、分析完成的通知在 watcher.py。
"""
import logging
import re
import sys
import time
import requests
import os
from PySide6.QtCore import QCoreApplication, QLocale, QObject, Property, Qt, QSettings, QTimer, QUrl, Signal, Slot
from datetime import datetime
import pyaudio
from pathlib import Path
from myapp.db_lane import DbLane
from myapp.diagnostics import HelpCenter
from myapp.home_dashboard import HomeDashboard
from myapp.notifications import Notifier, user_message
from myapp.pending_saves import PendingSaveStore
from myapp.search import ServiceSearch
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs, open_settings
from myapp.watcher import POLL_KEY as WATCHER_KEY, AnalysisWatcher, DockAttention
from .record_widget import SAVE_ROLES, SAVED, QuitGuard, RecordingSaver, RecordWidget
import cv2
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# TaskRunner 的 key（同一個 key 同時只跑一個，ENG-14）
ACCOUNT_NAME_KEY = "staff:account-name"
ANALYSIS_KEY = "staff:analysis"
SEARCH_KEY = "staff:search"
# 新版首頁（U1）的三張卡片：homeDashboard 的 Overview、Recent Performance、Score Trend，各 1 個往返
HOME_KEY_PREFIX = "staff:home"
HOME_KEYS = tuple(f"{HOME_KEY_PREFIX}:{name}" for name in ("overview", "recent", "trend"))
# launch() 在背景載入的：帳號名稱、首頁的三張卡片、監看要看的服務（ENG-16），1＋3＋1＝5 個往返（ENG-21 的預算）。
# 分析清單等打開分析頁才查（T28）
LAUNCH_KEYS = (ACCOUNT_NAME_KEY, *HOME_KEYS, WATCHER_KEY)
# 「Can't refresh analysis status」：清單的重新整理和監看的查詢共用這一則（同一次斷線只跳一則）
ANALYSIS_REFRESH_TOAST_KEY = "analysis-refresh"

_FIRST_ROLE = int(Qt.ItemDataRole.UserRole)


def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：經過 translate，日後加翻譯檔就能換語言；%1、%2… 一次換成 values
    （像 QString::arg）。和 media.py 的 _tr 一樣，context 是 ServerHome。"""
    text = QCoreApplication.translate("ServerHome", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def error_text(error):
    """區塊錯誤（InlineError）的兩段文字：原因（ENG-12 的英文訊息）、收在 Details 裡的技術細節。"""
    _, title, detail, _ = user_message(error)
    reason = f"{title}. {detail}" if detail else title
    return reason, f"{type(error).__name__}: {error}"


def format_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def score_or_none(value):
    """分數欄位：-1 表示沒有資料（db_schema.sql）。沒有資料一律是 None，畫面顯示「—」，不顯示 -1 或 0。"""
    return None if value is None or value < 0 else float(value)


# ---------------------------------------------------------------- 服務明細、報表（前端重建 F3）
# 兩端共用 myapp/media.py（UX-46）。名稱留在這裡：ServerHome、launch() 照舊建立和註冊 ChartsModel、MediaModel、PDFHandler
from myapp.media import ChartsModel, MediaModel, MediaService, PDFHandler, clear_cache  # noqa: E402,F401


# 清單模型搬到 myapp/service_list.py（兩端的 Services 頁都用）；名稱留在這裡：分析頁的 ServiceModel、測試照舊 import
from myapp.service_list import (LIST_ROLES, PAGE_SIZE, Criteria, RowListModel, clear_date_error,  # noqa: E402,F401
                                count_text, date_errors, list_rows, list_subtitle, page_range, search_page,
                                shown_text)


# ---------------------------------------------------------------- 服務查詢（UX-24；新版 Services 頁，PLAN §6-4、T31）


class ServiceManager(QObject):
    """Services 頁（server_service_page.qml，PLAN §6-4）的資料：自己的、分析完成的服務，新的在前。

    - search(start, end)：先檢查日期（不連網路）：格式不對、To 早於 From，錯誤寫在 startDateError／endDateError、不送出；
      對了就在 db lane 查詢，replace=True：連按時只留最後一次的結果。每次搜尋都從 8 筆開始。
      頁面第一次打開時用空白的日期查一次（全部，新的在前）。
    - status：idle（還沒查過）、ready、error；busy 是搜尋中（Show More 不算）。查到 0 筆＝ready 而且 totalCount 是 0。
    - startDate、endDate：上一次送出的條件。換頁回來時欄位照舊，條件和結果對得起來（UX-24）。
    - 清單（listModel，角色見 service_list.LIST_ROLES）一次 8 筆（UD7）：totalCount 是全部符合的筆數，hasMore 表示還有沒列出的。
      showMore() 用同樣的條件多查 8 筆（1 個往返）；loadingMore 是查詢中，失敗時清單留著，
      moreErrorDetail／moreErrorTechnical／moreFailureCount 說明原因，再按一次 Show More 就是重試（T31 第 1 節）。
    - 標題（T31 第 2 節）：countText「18 services」、rangeText「Oct 19 – Nov 1, 2026」（沒填的日期用查到的服務補上）、
      subtitle「Oct 19 – Nov 1, 2026 · newest first」、shownText「Showing 8 of 18」。還沒查過時都是空字串。
    """

    statusChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    criteriaChanged = Signal()
    dateErrorsChanged = Signal()
    pageChanged = Signal()          # totalCount、hasMore 和標題的文字
    loadingMoreChanged = Signal()   # loadingMore 和 Show More 的錯誤

    def __init__(self, services, account_id):
        super(ServiceManager, self).__init__()
        self.services = services
        self.account_id = account_id
        self._list = RowListModel(LIST_ROLES, "serviceId", self)
        self._status = "idle"
        self._busy = False
        self._start_date = self._end_date = ""
        self._date_errors = {"start": "", "end": ""}
        self._error_detail = self._error_technical = ""
        self._failure_count = 0
        self._criteria = None  # 上一次送出的條件（Criteria）：Show More 用同一組
        self._page = None      # 上一次查到的結果（service_list.SearchPage）
        self._shown_criteria = None  # 畫面上這份結果的條件（標題的範圍用）
        self._loading_more = False
        self._more_detail = self._more_technical = ""
        self._more_failures = 0

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=criteriaChanged)
    def startDate(self):
        return self._start_date

    @Property(str, notify=criteriaChanged)
    def endDate(self):
        return self._end_date

    @Property(str, notify=dateErrorsChanged)
    def startDateError(self):
        return self._date_errors["start"]

    @Property(str, notify=dateErrorsChanged)
    def endDateError(self):
        return self._date_errors["end"]

    @Property(str, notify=errorChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=errorChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=errorChanged)
    def failureCount(self):
        return self._failure_count

    # ------------------------------------------------------------ 清單與標題（新版 Services 頁）

    @Property(QObject, constant=True)
    def listModel(self):
        """畫面上的列（RowListModel，角色見 service_list.LIST_ROLES）。"""
        return self._list

    @Property(int, notify=pageChanged)
    def totalCount(self):
        return self._page.total if self._page is not None else 0

    @Property(bool, notify=pageChanged)
    def hasMore(self):
        return self._page is not None and len(self._page.rows) < self._page.total

    @Property(str, notify=pageChanged)
    def countText(self):
        return count_text(self._page.total) if self._page is not None else ""

    @Property(str, notify=pageChanged)
    def rangeText(self):
        return page_range(self._shown_criteria, self._page) if self._page is not None else ""

    @Property(str, notify=pageChanged)
    def subtitle(self):
        return list_subtitle(self.rangeText) if self._page is not None else ""

    @Property(str, notify=pageChanged)
    def shownText(self):
        return shown_text(len(self._page.rows), self._page.total) if self._page is not None else ""

    @Property(bool, notify=loadingMoreChanged)
    def loadingMore(self):
        return self._loading_more

    @Property(str, notify=loadingMoreChanged)
    def moreErrorDetail(self):
        return self._more_detail

    @Property(str, notify=loadingMoreChanged)
    def moreErrorTechnical(self):
        return self._more_technical

    @Property(int, notify=loadingMoreChanged)
    def moreFailureCount(self):
        return self._more_failures

    # ------------------------------------------------------------ 搜尋、Show More

    @Slot(str, str, result=bool)
    def search(self, start_text, end_text):
        """回傳是否已經送出查詢（日期有錯就不送）。"""
        start, end, errors = date_errors(start_text, end_text)
        if errors != self._date_errors:
            self._date_errors = errors
            self.dateErrorsChanged.emit()
        if any(errors.values()):
            return False
        self._start_date, self._end_date = start_text.strip(), end_text.strip()
        self.criteriaChanged.emit()
        self._criteria = Criteria(start, end, account_id=self.account_id)
        self._fetch(PAGE_SIZE, more=False)
        self._set_busy(True)
        self._set_loading_more(False, "", "", reset_failures=True)  # 還在跑的 Show More 被這次搜尋取代了
        return True

    @Slot(result=bool)
    def showMore(self):
        """多列 8 筆：同樣的條件、LIMIT 多 8 再查一次（1 個往返）。回傳是否送出（沒有更多、正在查時不送）。"""
        if self._busy or self._loading_more or self._status != "ready" or not self.hasMore:
            return False
        self._fetch(len(self._page.rows) + PAGE_SIZE, more=True)
        self._set_loading_more(True, "", "")
        return True

    @Slot()
    def retry(self):
        """區塊錯誤的 Retry：用上一次送出的條件再查一次。"""
        self.search(self._start_date, self._end_date)

    @Slot(str)
    def clearDateError(self, field):
        """使用者改了 field（start、end）那一格：錯誤先拿掉，下一次按 Search 再檢查（service_list.clear_date_error）。"""
        errors = clear_date_error(self._date_errors, field)
        if errors != self._date_errors:
            self._date_errors = errors
            self.dateErrorsChanged.emit()

    def _fetch(self, limit, more):
        db, criteria = self.services.db, self._criteria
        on_ok = (lambda page: self._more_found(criteria, page)) if more else (lambda page: self._found(criteria, page))
        self.services.tasks.submit("db", SEARCH_KEY, lambda token, report: search_page(db, criteria, limit),
                                   on_ok=on_ok, on_err=self._more_failed if more else self._failed, replace=True)

    def _show(self, criteria, page):
        self._page, self._shown_criteria = page, criteria
        self._list.replace(list_rows(page))
        self.pageChanged.emit()

    def _found(self, criteria, page):
        self._show(criteria, page)
        if self._failure_count:
            self._failure_count = 0
            self.errorChanged.emit()
        self._set_status("ready")
        self._set_busy(False)

    def _failed(self, error):
        logger.warning("service search failed: %r", error)
        self._error_detail, self._error_technical = error_text(error)
        self._failure_count += 1
        self.errorChanged.emit()
        self._set_status("error")
        self._set_busy(False)

    def _more_found(self, criteria, page):
        self._show(criteria, page)
        self._set_loading_more(False, "", "", reset_failures=True)

    def _more_failed(self, error):
        """Show More 失敗：已經列出的留著，原因寫在按鈕旁邊；再按一次就是重試。"""
        logger.warning("loading more services failed: %r", error)
        self._more_failures += 1
        self._set_loading_more(False, *error_text(error))

    def _set_loading_more(self, loading, detail, technical, reset_failures=False):
        before = (self._loading_more, self._more_detail, self._more_technical, self._more_failures)
        self._loading_more, self._more_detail, self._more_technical = loading, detail, technical
        if reset_failures:
            self._more_failures = 0
        if before != (self._loading_more, self._more_detail, self._more_technical, self._more_failures):
            self.loadingMoreChanged.emit()

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    def _set_busy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


# ---------------------------------------------------------------- 錄影頁：鏡頭和麥克風（UX-13、UX-14、ENG-06）

def probe_cameras():
    """試開 0～9 號鏡頭，回傳（清單文字, 鏡頭編號）。要在 GUI 執行緒呼叫，原因見 DeviceFinder。"""
    names, ids = [], []
    for camera_id in range(10):
        cap = cv2.VideoCapture(camera_id)
        if cap.isOpened():
            names.append(_tr("Camera %1", camera_id))  # 使用編號代替名稱
            ids.append(camera_id)
            cap.release()  # 釋放攝影機
    return names, ids


def probe_microphones():
    """有輸入聲道的音訊裝置，回傳（名稱, PyAudio 裝置編號）。"""
    audio = pyaudio.PyAudio()
    try:
        names, ids = [], []
        for device_index in range(audio.get_host_api_info_by_index(0).get("deviceCount")):
            info = audio.get_device_info_by_host_api_device_index(0, device_index)
            if info.get("maxInputChannels") > 0:
                names.append(info.get("name"))
                ids.append(info.get("index", device_index))
        return names, ids
    finally:
        audio.terminate()  # 以前沒有 terminate（ENG-06）；PortAudio 有參照計數，不影響錄影視窗自己的那一份


SAVED_ROWS_KEPT = 5  # Saving 卡片上最多留幾筆已經存好的（最新的幾筆；還在存的、沒存好的全部留著）


class SavesModel(RowListModel):
    """錄影頁「Saving」卡片的清單（新版介面 UD9、T29）：deviceFinder.saves。

    - 一列是一段錄影的存檔（RecordingSaver 的 SaveJob，角色見 record_widget.SAVE_ROLES 和 SaveJob.row()）：
      這次登入之後停止的每一段，加上登入時放回來、上次沒存完的（T26）。新的在上面（service_id 由新到舊），
      狀態變了那一列就地更新，不會換位置。
    - 留多久：還在存的、沒存好的（包括存不了的）一直留到結束 app；已經存好的只留最新的 SAVED_ROWS_KEPT 筆，
      更舊的到分析頁找（Not Analyzed）。
    - retry()、analyze() 和通知上的 Retry、Analyze 走同一條路（RecordingSaver.retry()、request_analyze()）。
    - DeviceFinder 一建立就有這個清單（QML 一打開錄影頁就讀得到），RecordingSaver 建立之後才 attach()：
      沒有錄影、也沒有沒存完的錄影時，不為了這個清單建立 RecordingSaver（T26 第 6 節）。
    """

    def __init__(self, parent=None):
        super().__init__(SAVE_ROLES, "serviceId", parent)
        self._saver = None

    def attach(self, saver):
        """接上 RecordingSaver（只接一次，DeviceFinder 建立它的時候）：之後每一次 changed 都重排一次
        （筆數很少，一次只差一列）。"""
        self._saver = saver
        saver.changed.connect(self._on_changed)
        self._sync()

    @Slot(str, result=bool)
    def retry(self, service_id):
        """Retry：和通知上的 Retry 一樣（retry-upload:<sid>）。回傳 False 表示這一筆現在不能重試。"""
        return self._saver is not None and self._saver.retry(service_id)

    @Slot(str, result=bool)
    def analyze(self, service_id):
        """Analyze：和通知上的 Analyze 一樣，發 analyze:<sid> 給送出分析的流程（ServerHome._on_action）。
        回傳 False 表示還沒存好、或已經送去分析了。"""
        return self._saver is not None and self._saver.request_analyze(service_id)

    @Slot(str)
    def _on_changed(self, service_id):
        self._sync()

    def _sync(self):
        jobs = sorted(self._saver.jobs(), key=lambda job: job.service_id, reverse=True)  # 新的在上面
        saved = [job for job in jobs if job.state == SAVED]
        dropped = {id(job) for job in saved[SAVED_ROWS_KEPT:]}
        self.replace([job.row() for job in jobs if id(job) not in dropped])


class DeviceFinder(QObject):
    """錄影頁的鏡頭和麥克風清單，以及錄影視窗和錄好之後的存檔（F3）。

    - 不在建構子探測（以前登入時就把 0～9 號鏡頭都打開一次，UX-02、UX-13）：打開錄影頁才探測，結果留著；
      「Scan Again」重新探測。
    - status：idle（還沒探測）、scanning、ready、error。
    - 探測在 GUI 執行緒做（T18）：OpenCV 在 macOS 上還沒取得相機權限時，只有在主執行緒才能跳出授權詢問
      （cv2 的訊息：「can not spin main run loop from other thread」）。所以先把 status 設成 scanning，
      等 SCAN_DELAY_MS 讓「Looking for cameras and microphones…」畫出來，再探測。
    - startRecording() 打開錄影視窗（同時只有一個）。停止錄影之後的上傳、寫資料庫由 RecordingSaver 在背景做，
      屬於這裡（和外殼活得一樣久），不屬於視窗：視窗關了也會做完（UX-08、ENG-03）。
    - 上次沒存完的錄影（T26）：launch() 時 restore_saves() 從本機讀回這個帳號的，交給 RecordingSaver，一樣可以 Retry。
    - saves：錄影頁 Saving 卡片的清單（SavesModel，UD9、T29），列出每一段的存檔，通知關掉之後還看得到。
    - 結束程式前（UX-09、UX-29）：外殼的 Exit 按鈕和 onClosing 呼叫 stopRecording()，再看 uploadCount 決定要不要問。
      ⌘Q 不會經過 onClosing（T20），所以錄影中、存檔中時用 QuitGuard 在 app 上攔 QEvent.Quit，改發 quitRequested。
    """

    camerasChanged = Signal()
    microphonesChanged = Signal()
    statusChanged = Signal()
    recordingChanged = Signal()  # 錄影視窗開始或停止錄影
    uploadsChanged = Signal()  # 背景存檔的筆數或 bytes 變了
    quitRequested = Signal()  # ⌘Q（或其他 QEvent.Quit）被擋下來了：外殼要問使用者

    SCAN_DELAY_MS = 50

    def __init__(self, server_home):
        super().__init__()
        self.server_home = server_home
        self._cameras = []
        self._camera_ids = []  # 清單第 n 項對應的鏡頭編號
        self._microphones = []
        self._microphone_ids = []  # 清單第 n 項對應的 PyAudio 裝置編號
        self._status = "idle"
        self._error_technical = ""
        self._recorder = None  # 開著的錄影視窗
        self._saver = None  # 要有 AppServices 才能建立：第一次錄影時，或登入時有沒存完的錄影（T26）
        self._saves = SavesModel(self)  # 一開始就有（空的），RecordingSaver 建立時接上
        self._quit_allowed = False

    @Property(list, notify=camerasChanged)
    def cameras(self):
        return self._cameras

    @Property(list, notify=microphonesChanged)
    def microphones(self):
        return self._microphones

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(str, notify=statusChanged)
    def errorTechnical(self):
        return self._error_technical

    @Slot()
    def ensureScanned(self):
        """錄影頁打開時呼叫：第一次才探測，之後用上次的結果。"""
        if self._status == "idle":
            self.rescan()

    @Slot()
    def rescan(self):
        if self._status == "scanning":
            return
        self._set_status("scanning")
        QTimer.singleShot(self.SCAN_DELAY_MS, self, self._scan)

    def _scan(self):
        try:
            cameras, camera_ids = probe_cameras()
            microphones, microphone_ids = probe_microphones()
        except Exception as error:  # 例如 PortAudio 初始化失敗：顯示區塊錯誤，可以重試
            logger.exception("device probe failed")
            self._error_technical = f"{type(error).__name__}: {error}"
            self._set_status("error")
            return
        self._error_technical = ""
        self._camera_ids, self._microphone_ids = camera_ids, microphone_ids
        if cameras != self._cameras:
            self._cameras = cameras
            self.camerasChanged.emit()
        if microphones != self._microphones:
            self._microphones = microphones
            self.microphonesChanged.emit()
        self._set_status("ready")

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    # ------------------------------------------------------------ 錄影視窗與存檔（F3）

    @Property(QObject, constant=True)
    def saves(self):
        """錄影頁 Saving 卡片的清單（SavesModel）。"""
        return self._saves

    @property
    def saver(self):
        if self._saver is None:
            self._saver = RecordingSaver(self.server_home.services, on_saved=self._saved, parent=self)
            self._saver.inFlightChanged.connect(self._uploads_changed)
            self._saves.attach(self._saver)
        return self._saver

    @property
    def recorder(self):
        return self._recorder

    def restore_saves(self):
        """launch()：這個帳號上次沒存完的錄影放回存檔清單，每一筆跳一則附 Retry 的通知（T26）。只讀本機的設定。

        沒有的話不建立 RecordingSaver，照舊等第一次錄影：它接著 app、TaskRunner、Notifier 的 signal，
        被 gc 在背景執行緒收掉時要拿 GIL，多一個就多一個卡死的機會（T26 第 6 節）。
        """
        account_id = self.server_home.getID()
        if not PendingSaveStore().load(account_id):
            return []
        return self.saver.restore(account_id)

    @Slot(int, int, int)
    def startRecording(self, cam0_index, cam1_index, mic_index):
        """打開錄影視窗。已經開著的：錄影中就帶到最前面，不開第二個；沒在錄就換成這次選的裝置。"""
        recorder = self._recorder
        if recorder is not None and recorder.recording:
            recorder.showNormal()
            recorder.raise_()
            recorder.activateWindow()
            return
        if recorder is not None:
            recorder.close()
        account_id = self.server_home.getID()  # Retrieve the account_id dynamically
        # QML 傳的是下拉選單的位置，換成真正的裝置編號；名稱顯示在狀態列（DS-34）
        widget = RecordWidget(self._camera_ids[cam0_index], self._camera_ids[cam1_index],
                              self._microphone_ids[mic_index], account_id, saver=self.saver,
                              camera_names=(self._cameras[cam0_index], self._cameras[cam1_index]),
                              microphone_name=self._microphones[mic_index])
        widget.recordingChanged.connect(self._recording_changed)
        widget.closed.connect(lambda: self._forget(widget))
        self._recorder = widget
        widget.show()

    def _forget(self, widget):
        if self._recorder is widget:
            self._recorder = None
            widget.deleteLater()
            self._recording_changed()

    @Property(bool, notify=recordingChanged)
    def recording(self):
        return self._recorder is not None and self._recorder.recording

    def _in_flight(self):
        return self._saver.in_flight() if self._saver is not None else []

    @Property(int, notify=uploadsChanged)
    def uploadCount(self):
        """還在收尾、上傳或寫資料庫的錄影數。"""
        return len(self._in_flight())

    @Property(str, notify=uploadsChanged)
    def uploadServiceId(self):
        jobs = self._in_flight()
        return jobs[0].service_id if jobs else ""

    @Property(float, notify=uploadsChanged)
    def uploadDone(self):
        return float(sum(job.done for job in self._in_flight()))

    @Property(float, notify=uploadsChanged)
    def uploadTotal(self):
        """還在存的錄影加起來的 bytes；有一筆還不知道大小（檔案還在寫）時是 0。"""
        jobs = self._in_flight()
        return float(sum(job.total for job in jobs)) if all(job.total > 0 for job in jobs) else 0.0

    @Slot(result=bool)
    def stopRecording(self):
        """結束程式前：錄影中的先停止並存檔（UX-09）。回傳是否停止了一段錄影。"""
        if self._recorder is not None and self._recorder.recording:
            self._recorder.stop_recording()
            return True
        return False

    @Slot()
    def allowQuit(self):
        """使用者選了 Quit Anyway：之後的 QEvent.Quit 不再攔。"""
        self._quit_allowed = True
        self._update_quit_guard()

    def _should_block_quit(self):
        return not self._quit_allowed and (self.recording or bool(self._in_flight()))

    def quit_blocked(self):
        """QuitGuard 擋下 ⌘Q 之後呼叫：錄影中的先停止並存檔，再請外殼問（server_home.qml 的 Exit 按鈕）。"""
        self.stopRecording()
        self.quitRequested.emit()

    def _update_quit_guard(self):
        QuitGuard.instance().set_blocking(self, self._should_block_quit())

    @Slot(bool)
    def _recording_changed(self, recording=None):
        self.recordingChanged.emit()
        self._update_quit_guard()

    @Slot()
    def _uploads_changed(self):
        self.uploadsChanged.emit()
        self._update_quit_guard()

    def _saved(self, service_id):
        """寫好資料庫了：分析頁的 Not Analyzed 清單重新整理（CONTRACTS §4 的跨流程呼叫）。"""
        model = getattr(self.server_home, "serviceModel", None)
        if model is not None:
            model.refresh()


# ---------------------------------------------------------------- 分析頁（ENG-05、UX-19～21、U2）、送出分析（UX-18）

PAGE_SIZE = 8  # 每個分頁一次顯示幾筆，下面是「Showing 8 of 41」和 Show More（UD7）
# 分頁照流程排（UD6）：Not analyzed → Analyzing → Completed，各自對應 service_status 1、2、3
TABS = ("notAnalyzed", "analyzing", "completed")
TAB_STATUS = {"notAnalyzed": 1, "analyzing": 2, "completed": 3}
CLOCK_TICK_S = 30  # 「Sent 21 min ago」以分鐘顯示，30 秒更新一次就夠（DS-44）

# 三個分頁一次查完（1 個往返）。每個分頁只取前 limit 筆（錄影時間新的在前），每一列帶著那個分頁的總數：
# 回應的大小和累積了多少服務無關（ENG-01：每個動作的回應小於 8 KB；T16 的 read_timeout）。
# pin 是要露出來的服務（View Results）：它那個分頁至少取到「它那一頁的下一頁」，和 shown_for(位置) + PAGE_SIZE
# 一樣，所以同一個往返就看得到它，不用再查一次。排名只用 tbl_service（idx_service_account_status_start），
# 分數只 JOIN 留下來的那幾列。
ANALYSIS_SQL = """
WITH ranked AS (
  SELECT service_id, service_start, service_end, service_status,
         ROW_NUMBER() OVER (PARTITION BY service_status ORDER BY service_start DESC, service_id DESC) AS position,
         COUNT(*) OVER (PARTITION BY service_status) AS total
  FROM tbl_service
  WHERE account_id = %(account_id)s AND service_status IN (1, 2, 3)
), pinned AS (
  SELECT ranked.*,
         MAX(CASE WHEN service_id = %(pin)s THEN position END) OVER (PARTITION BY service_status) AS pin_position
  FROM ranked
)
SELECT p.service_id, p.service_start, p.service_end, p.service_status, p.position, p.total,
       sc.customer_total, sc.server_total
FROM pinned AS p
LEFT JOIN tbl_service_score AS sc ON sc.service_id = p.service_id
WHERE p.position <= GREATEST(
        CASE p.service_status WHEN 1 THEN %(limit_1)s WHEN 2 THEN %(limit_2)s ELSE %(limit_3)s END,
        COALESCE(((p.pin_position - 1) DIV %(page)s + 2) * %(page)s, 0))
ORDER BY p.service_start DESC, p.service_id DESC
"""
# 狀態只會往前走（1 → 2 → 3）：分析服務可能已經寫好 3，不能被這裡蓋回 2
MARK_SENT_SQL = "UPDATE tbl_service SET service_status = 2 WHERE service_id = %s AND service_status < 2"
# 分析頁每一列的角色。startTime 是資料庫的格式（「2026-10-30 12:00:00」），View 打開明細時交給 openServiceDetail()
SERVICE_ROLES = ("serviceId", "startTime", "recorded", "length", "sentText", "customerScore", "staffScore",
                 "failed", "sending")
ANALYZE_TIMEOUT = (3, 10)  # (連線逾時, 讀取逾時)，單位是秒；分析服務收到請求就回 200，不等分析做完
REFRESH_RETRY_S = 15       # 已經有資料、重新整理失敗時，幾秒後自己再試（DS-52「Retrying in 15 seconds.」）
SENT_GROUP = "analysisSent"  # QSettings 的群組：analysisSent/<帳號>/<service_id> = 送出的時間（epoch ms）


def analyze_key(service_id):
    """送出分析：io lane 的 POST，也是「送出的結果」那一則通知（CONTRACTS §4）。"""
    return f"analyze:{service_id}"


def mark_key(service_id):
    """收到 200 之後：db lane 把狀態改成 2、重新整理清單。"""
    return f"analysis:mark:{service_id}"


def outcome_key(service_id):
    """監看發現的「分析完成」「分析失敗」那一則通知。"""
    return f"analysis:{service_id}"


def post_analysis(url, service_id):
    """在 io lane 執行：請分析服務分析這一筆（ENG-05）。分析服務收到就回 200，不等分析做完。

    連不到、逾時丟 requests 的例外；200 以外的狀態碼丟 requests.HTTPError（ENG-12：分析服務回應錯誤）。
    """
    if not url:
        raise RuntimeError("ANALYZE_API_URL is not set")
    response = requests.post(url, json={"service_id": service_id}, timeout=ANALYZE_TIMEOUT)
    if response.status_code != 200:
        raise requests.HTTPError(f"analysis service returned HTTP {response.status_code}", response=response)


def toast_reason(error):
    """toast 的內文：原因，加上下一步的第一句（DS-21：內文最多兩行），例如
    「Can't reach the analysis service. Check your network and try again.」（DS-52）。"""
    _, title, detail, _ = user_message(error)
    next_step = detail.split(". ")[0].rstrip(".")
    return f"{title}. {next_step}." if next_step else f"{title}."


def shown_for(position, page=PAGE_SIZE):
    """要看得到第 position 筆（從 1 數），分頁要顯示幾筆：補滿到整頁（第 10 筆 → 16）。"""
    return -(-position // page) * page


def recorded_text(start, now):
    """Recorded 欄：「Nov 1, 14:05」；不是今年的加上年份：「Dec 30, 2025, 10:00」。沒有時間是空字串。

    月份一律英文（PLAN D1）：不用 strftime 的 %b，因為 QCoreApplication 會呼叫 setlocale，%b 會跟著系統語系。
    """
    if start is None:
        return ""
    return QLocale.c().toString(start, "MMM d, HH:mm" if start.year == now.year else "MMM d, yyyy, HH:mm")


def length_text(start, end):
    """Length 欄：錄了多久，四捨五入到分鐘：「7 min」「1 h」「1 h 5 min」，不到 1 分鐘是「Less than 1 min」。

    不知道是空字串（畫面顯示「—」）：service_end 是 NULL（資料表允許），或比開始早（時鐘被調過）。
    """
    if start is None or end is None or end < start:
        return ""
    seconds = (end - start).total_seconds()
    if seconds < 60:
        return _tr("Less than 1 min")
    hours, minutes = divmod(int(seconds + 30) // 60, 60)
    if not hours:
        return _tr("%1 min", minutes)
    return _tr("%1 h %2 min", hours, minutes) if minutes else _tr("%1 h", hours)


def sent_text(sent_at, now):
    """Analyzing 的 Sent 欄（以分鐘顯示，DS-44）。sent_at、now 是 epoch ms。

    sent_at 是 0 表示不知道什麼時候送的（別台電腦送的，或這台電腦沒記到）：「Sent earlier」，
    不拿「第一次看到」的時間冒充送出的時間（T30）。時鐘被往回調時當成剛送出。
    """
    if not sent_at:
        return _tr("Sent earlier")
    minutes = max(0, int((now - sent_at) // 60000))
    if minutes < 1:
        return _tr("Sent less than 1 min ago")
    if minutes < 60:
        return _tr("Sent %1 min ago", minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return _tr("Sent %1 h %2 min ago", hours, minutes) if minutes else _tr("Sent %1 h ago", hours)
    days = hours // 24
    return _tr("Sent 1 day ago") if days == 1 else _tr("Sent %1 days ago", days)


def default_tab(not_analyzed, analyzing, completed):
    """一打開分析頁停在哪個分頁（參數是三個分頁的總數）。

    有沒送出的就是 Not analyzed（要動作的在這裡，UX-19）；沒有就是 Completed。Completed 也是空的、
    但有分析中的，停在 Analyzing，不停在空的分頁；三個都是空的，停在流程的第一步 Not analyzed
    （它的空狀態說明錄好的服務會出現在這裡）。
    """
    if not_analyzed:
        return "notAnalyzed"
    if completed:
        return "completed"
    return "analyzing" if analyzing else "notAnalyzed"


def _score(value):
    """分數到小數一位：畫面顯示的數字和分數帶（Good 85+、Fair 70–84）用同一個值。-1 一律是 None。"""
    value = score_or_none(value)
    return None if value is None else round(value, 1)


def list_services(db, account_id, limits=(2 * PAGE_SIZE,) * 3, pin=None, mark_sent=None):
    """在 db lane 執行：自己的服務，每個分頁（狀態 1、2、3）取前 limits[i] 筆，新的在前；每一列有
    position（在分頁裡第幾筆）和 total（分頁的總數）。pin 見 ANALYSIS_SQL。1 個往返。

    mark_sent 是剛送出分析的服務，先把它改成 2（多 1 個往返）。
    """
    params = {"account_id": account_id, "pin": pin, "page": PAGE_SIZE,
              **{f"limit_{status}": int(limit) for status, limit in zip((1, 2, 3), limits)}}
    with db.cursor(dictionary=True) as cursor:
        if mark_sent:
            cursor.execute(MARK_SENT_SQL, (mark_sent,))
        cursor.execute(ANALYSIS_SQL, params)
        return cursor.fetchall()


class SentTimes:
    """送出分析的時間（epoch ms），記在這台電腦（QSettings，和 T26 的 pendingSaves 同一個地方）：
    重開 app 之後「Sent 21 min ago」還是對的。資料庫沒有送出的時間（UX-19），這一版也不改 schema（PLAN 第 1 節）。

    - key：analysisSent/<帳號>/<service_id>。同一台電腦換人登入，各看各的。
    - 收到 200、狀態改成 2 時記下；看到它不是分析中了（完成、失敗、Analyzing 全部載入了卻沒有它）就刪掉。
    - 只在 GUI 執行緒讀寫：本機的小檔案，不是網路；寫完馬上 sync()。
    """

    def __init__(self, account_id, settings=None):
        self._settings = settings if settings is not None else open_settings()
        self._group = f"{SENT_GROUP}/{account_id}"
        self._times = self._read()

    def _read(self):
        """看不懂的值（不是正數）刪掉，log 記一筆：不然每次登入都會再遇到一次。"""
        settings = self._settings
        settings.beginGroup(self._group)
        try:
            stored = {key: settings.value(key) for key in settings.childKeys()}
        finally:
            settings.endGroup()
        times = {}
        for service_id, value in stored.items():
            try:
                sent_at = float(value)
            except (TypeError, ValueError):
                sent_at = 0.0
            if sent_at > 0 and sent_at != float("inf"):
                times[service_id] = sent_at
                continue
            logger.warning("dropping the unreadable analysis send time %s=%r", service_id, value)
            settings.remove(f"{self._group}/{service_id}")
        return times

    def get(self, service_id):
        """送出的時間（epoch ms）；不知道是 0。"""
        return self._times.get(service_id, 0.0)

    def ids(self):
        return set(self._times)

    def remember(self, service_id, sent_at):
        self._times[service_id] = float(sent_at)
        self._settings.setValue(f"{self._group}/{service_id}", float(sent_at))
        self._sync(f"remember {service_id}")

    def forget(self, service_id):
        if self._times.pop(service_id, None) is None:
            return
        self._settings.remove(f"{self._group}/{service_id}")
        self._sync(f"forget {service_id}")

    def _sync(self, what):
        """寫不進去只記 log：送出照常，只是重開 app 之後那一筆會顯示「Sent earlier」。"""
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            logger.warning("couldn't %s in %s: %s", what, self._settings.fileName(), self._settings.status())


class PagedRowModel(RowListModel):
    """分析頁的一個分頁（UD7）：顯示載入的前 shown 筆，每按一次 Show More 多 PAGE_SIZE 筆。

    - count：現在顯示幾筆；total：這個分頁在資料庫裡一共幾筆（分頁上的數字、「Showing 8 of 41」）。
    - limit：要載入幾筆，永遠比顯示的多一頁：按 Show More 時下一頁已經在手上，馬上顯示，不等往返；
      之後發 moreWanted，ServiceModel 在背景再多載入一頁（1 個往返）。
    - hasMore：還有沒顯示的（要不要有 Show More）；loadingMore：要顯示的還沒載入（例如連按兩次、
      或預先載入失敗了），這時 Show More 忙碌，再按也不會多要。
    - 分析頁每次打開都回到前 PAGE_SIZE 筆（reset()）。
    """

    totalChanged = Signal()
    pagingChanged = Signal()  # hasMore、loadingMore
    moreWanted = Signal()     # 顯示的變多了，要再多載入一頁

    def __init__(self, roles, key, parent=None, page_size=PAGE_SIZE):
        super().__init__(roles, key, parent)
        self._page = page_size
        self._shown = page_size
        self._loaded = []
        self._total = 0

    @Property(int, notify=totalChanged)
    def total(self):
        return self._total

    @Property(bool, notify=pagingChanged)
    def hasMore(self):
        return len(self._rows) < self._total

    @Property(bool, notify=pagingChanged)
    def loadingMore(self):
        return len(self._rows) < min(self._total, self._shown)

    @Property(int, constant=True)
    def pageSize(self):
        return self._page

    @property
    def shown(self):
        """要顯示幾筆（載入的不夠時，實際顯示的 count 會比較少）。"""
        return self._shown

    @property
    def limit(self):
        return self._shown + self._page

    def loaded(self):
        """載入的每一列（複本），包括還沒顯示的下一頁。"""
        return [dict(row) for row in self._loaded]

    def satisfied(self):
        """載入的夠不夠顯示到 limit（總數比較少時，全部載入就夠了）。"""
        return len(self._loaded) >= min(self._total, self.limit)

    def set_rows(self, rows, total):
        """查詢的結果：這個分頁載入的列（依畫面順序）和總數。只對變了的列發 signal（RowListModel）。"""
        before = self._paging()
        self._loaded = list(rows)
        if total != self._total:
            self._total = total
            self.totalChanged.emit()
        self._show(before)

    @Slot()
    def showMore(self):
        """Show More：多顯示一頁（已經載入的馬上出現），再請 ServiceModel 多載入一頁。"""
        if not self.hasMore or self.loadingMore:
            return
        before = self._paging()
        self._shown += self._page
        self._show(before)
        self.moreWanted.emit()

    def reveal(self, key):
        """讓載入的那一列顯示出來（補滿到它那一頁）。回傳它在分頁裡的位置（從 0 數），沒載入是 -1。"""
        index = next((i for i, row in enumerate(self._loaded) if row[self._key] == key), -1)
        if index >= self._shown:
            before = self._paging()
            self._shown = shown_for(index + 1, self._page)
            self._show(before)
        return index

    def reset(self):
        """回到前 PAGE_SIZE 筆（分析頁重新打開）。"""
        if self._shown != self._page:
            before = self._paging()
            self._shown = self._page
            self._show(before)

    def _paging(self):
        return self.hasMore, self.loadingMore

    def _show(self, before):
        self.replace(self._loaded[:self._shown])
        if self._paging() != before:
            self.pagingChanged.emit()


class ServiceModel(QObject):
    """分析頁（analysis.qml）的三個分頁、送出分析，以及分析狀態的通知（DS-52）。

    - 三個分頁：notAnalyzedModel、analyzingModel、completedModel（PagedRowModel：count、total、hasMore、
      loadingMore、showMore()）。每一列的角色見 SERVICE_ROLES。
    - status：loading（還沒有資料）、ready、error；busy 是重新整理中。loading 時頁面顯示骨架，
      不顯示空狀態（DS-25）；ready 之後才知道每個分頁是不是空的。
    - defaultTab：打開分析頁時停在哪個分頁（default_tab()）；還沒載入好是空字串。
    - 清單什麼時候載入：登入時不載入（PLAN 5-3：首頁的 Overview 已經有各狀態的筆數），打開分析頁時才第一次查
      （pageOpened()：側邊欄、首頁的服務方塊、View Results）。之後錄影存好、監看發現狀態變了、送出分析、
      Show More（預先載入下一頁）時重新整理。每次 1 個往返，只取每個分頁要顯示的再多一頁（T30）。
    - 「更新不到」（重新整理或監看的查詢失敗）：清單留著，只跳一則警告（DS-52），恢復後自己關掉；重新整理失敗時
      15 秒後自己再試。清單已經有資料，或監看正在等分析結果時才跳（分析頁沒打開過也一樣，T30）；
      分析頁第一次載入就失敗是區塊錯誤（DS-27），不跳通知。
    - sentText：什麼時候送出分析的。資料庫沒有送出的時間（UX-19），所以是這台電腦收到 200 的時間，
      記在 QSettings（SentTimes），重開 app 也還在；不知道（別台電腦送的）就是「Sent earlier」（T30）。
      每 30 秒更新一次（只有分析中的列會變）。
    - failed：這次執行看過它是 2、後來又變回 1，或監看看到 2 → 1（分析服務失敗時會這樣做，D4、DS-22）。
    - sending：送出中（POST 加上改狀態），那一列的按鈕忙碌（DS-24）。
    - analyzeService()：POST 在 io lane；收到 200 之後在 db lane 改狀態、重新整理清單。結果用 toast 告訴使用者。
    """

    statusChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    defaultTabChanged = Signal()
    analysisSent = Signal(str)  # 送出成功（分析服務收到了、狀態也改成 2）：監看從這裡開始看這一筆

    def __init__(self, services, account_id, *, clock=time.time, sent_times=None):
        """clock：現在幾秒（epoch），算「Sent 21 min ago」用，測試可以換掉。sent_times：SentTimes，測試可以換掉。"""
        super(ServiceModel, self).__init__()
        self.services = services
        self.account_id = account_id
        self._clock = clock
        self._tabs = {tab: PagedRowModel(SERVICE_ROLES, "serviceId", self) for tab in TABS}
        for model in self._tabs.values():
            model.moreWanted.connect(self.refresh)
        self._status = "loading"
        self._busy = False
        self._default_tab = ""
        self._error_detail = self._error_technical = ""
        self._failure_count = 0
        self._rows = []  # 上一次查到的清單：送出中的狀態改變、時間到了（sentText）時重新套用
        self._last_status = {}  # 上一次看到的狀態
        self._sent = sent_times if sent_times is not None else SentTimes(account_id)
        self._failed = set()
        self._sending = set()
        self._pin = None  # 要露出來的服務（View Results）：下一次查詢會多取到它（ANALYSIS_SQL）
        self._stopped = False  # 外殼關掉了（stop()）：不再計時
        self._refresh_warning = 0  # 「Can't refresh analysis status」那一則的 toastId
        self._toast_ids = {}  # 通知的 key → toastId：Notifier 只能用 toastId 關掉
        self._retry = QTimer(self)  # 已經有資料、重新整理失敗時，過一會兒自己再試
        self._retry.setSingleShot(True)
        self._retry.setInterval(REFRESH_RETRY_S * 1000)
        self._retry.timeout.connect(self.refresh)
        self._ticker = QTimer(self)  # 有分析中的列時，每 30 秒更新 sentText
        self._ticker.setInterval(CLOCK_TICK_S * 1000)
        self._ticker.timeout.connect(self._tick)

    @Property(QObject, constant=True)
    def completedModel(self):
        return self._tabs["completed"]

    @Property(QObject, constant=True)
    def analyzingModel(self):
        return self._tabs["analyzing"]

    @Property(QObject, constant=True)
    def notAnalyzedModel(self):
        return self._tabs["notAnalyzed"]

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=defaultTabChanged)
    def defaultTab(self):
        return self._default_tab

    @Property(str, notify=errorChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=errorChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=errorChanged)
    def failureCount(self):
        return self._failure_count

    @Slot()
    @Slot(str)
    def pageOpened(self, reveal=""):
        """分析頁打開了（側邊欄、⌘3、View Results）：每個分頁回到前 8 筆，在背景重新整理（1 個往返）。

        reveal 是要露出來的服務（View Results）。已經載入的話馬上顯示到它那一頁；不管有沒有，這次的查詢都會
        多取到它（重新整理時它可能往下移），回來時再顯示一次。頁面用 indexOfService() 找它在第幾列。
        """
        for model in self._tabs.values():
            model.reset()
        self._pin = reveal or None
        if self._pin is not None:
            self._reveal(self._pin)
        self.refresh()

    @Slot()
    def refresh(self):
        """在 db lane 重新整理三個分頁（1 個往返）。上一次還沒回來就不再送，而且不會漏掉更新：
        db lane 只有一條執行緒、依序執行，所以呼叫者剛做完的資料庫工作（監看的查詢、錄影寫入、改狀態）
        如果排在還沒回來的那一次後面，結果不會先回來；還沒回來的那一次一定查得到它。
        要的筆數或要露出來的服務在這段期間變了（Show More、View Results），回來時會再查一次（_took）。"""
        request = self._request()
        db, account_id = self.services.db, self.account_id
        if self.services.tasks.submit("db", ANALYSIS_KEY, lambda token, report: list_services(db, account_id, *request),
                                      on_ok=lambda rows: self._loaded(request, rows), on_err=self._failed_to_load):
            self._set_busy(True)
            return True
        return False

    @Slot(str, str, result=int)
    def indexOfService(self, tab, service_id):
        """service_id 在分頁 tab（completed、analyzing、notAnalyzed）顯示的第幾列，沒有是 -1。分析頁捲到那一列用。"""
        rows = self._tabs.get(tab)
        ids = [row["serviceId"] for row in rows.rows()] if rows is not None else []
        return ids.index(service_id) if service_id in ids else -1

    def _request(self):
        """這次查詢要的：每個分頁的筆數（照 TABS 的順序，也就是狀態 1、2、3）、要露出來的服務。"""
        return tuple(self._tabs[tab].limit for tab in TABS), self._pin

    def _loaded(self, request, rows):
        if not self._took(rows, request):
            self._set_busy(False)

    def _took(self, rows, request):
        """拿到最新的清單（重新整理，或送出分析之後）：套用，清掉錯誤和「更新不到」的警告。
        要的比這次查的多（查詢期間按了 Show More、View Results）就再查一次；回傳有沒有再查。"""
        self._apply(rows, request[1], fresh=True)
        if self._failure_count:
            self._failure_count = 0
            self.errorChanged.emit()
        self._set_status("ready")
        self._retry.stop()
        self.refresh_recovered()
        return self._wants_more(request) and self.refresh()

    def _wants_more(self, request):
        if self._pin is not None and self._pin != request[1]:
            return True
        return not all(model.satisfied() for model in self._tabs.values())

    def _failed_to_load(self, error):
        logger.warning("loading the analysis lists failed: %r", error)
        self._set_busy(False)
        if self._status == "ready":  # 清單留著，只跳一則警告；過一會兒自己再試
            self.refresh_failed(error, REFRESH_RETRY_S)
            if not self._stopped:
                self._retry.start()
            return
        self._error_detail, self._error_technical = error_text(error)
        self._failure_count += 1
        self.errorChanged.emit()
        self._set_status("error")

    def refresh_failed(self, error, retry_in, watching=False):
        """分析狀態更新不到（清單的重新整理，或監看的查詢；retry_in 秒後會再試）。

        同一次斷線不管失敗幾次、從哪裡失敗都只有一則警告，恢復後自己關掉。什麼時候跳：
        - 清單已經有資料（分析頁打開過）：清單留著，但可能是舊的。
        - watching：監看正在等分析結果（有分析中的服務）。分析頁沒打開過也要跳：使用者在首頁等「Analysis complete」，
          不說的話他不知道通知會晚到（T30）。
        其他時候不跳：分析頁第一次載入就失敗是區塊錯誤（DS-27）；沒有在等的服務時，監看查不到也不影響任何東西。
        """
        if self._refresh_warning or (self._status != "ready" and not watching):
            return
        cause = user_message(error)[1]
        self._refresh_warning = self.services.notifier.notify(
            "warning", _tr("Can't refresh analysis status"), _tr("%1. Retrying in %2 seconds.", cause, retry_in),
            ANALYSIS_REFRESH_TOAST_KEY)

    @Slot()
    def refresh_recovered(self):
        """又查得到了（清單或監看）：「更新不到」的警告自己關掉。"""
        if self._refresh_warning:
            self.services.notifier.dismiss(self._refresh_warning)
            self._refresh_warning = 0

    def _apply(self, rows, pin=None, fresh=False):
        """把查到的清單分到三個分頁。也用來重新套用上一次的清單（送出中的狀態變了、sentText 該更新了）：
        只有真的變了的列會發 dataChanged。

        fresh：剛從資料庫查到的。只有這時候才清掉不在分析中的送出時間：重新套用的可能是還沒載入時的空清單
        （例如登入後還沒打開分析頁，就從通知按了 Analyze），拿它來清會把本機記的全部清掉。"""
        self._rows = rows
        now = self._clock() * 1000
        today = datetime.fromtimestamp(now / 1000)
        groups = {status: [] for status in TAB_STATUS.values()}
        totals = dict.fromkeys(TAB_STATUS.values(), 0)
        statuses = {}
        for row in rows:
            service_id, status = str(row["service_id"]), int(row["service_status"])
            statuses[service_id] = status
            totals[status] = int(row["total"])
            if status == 1 and self._last_status.get(service_id) == 2:
                self._failed.add(service_id)
            elif status != 1:
                self._failed.discard(service_id)
            if status != 2 and fresh:  # 不在分析中了：送出的時間沒用了
                self._sent.forget(service_id)
            groups[status].append({
                "serviceId": service_id,
                "startTime": format_time(row["service_start"]),
                "recorded": recorded_text(row["service_start"], today),
                "length": length_text(row["service_start"], row["service_end"]),
                "sentText": sent_text(self._sent.get(service_id), now) if status == 2 else "",
                "customerScore": _score(row["customer_total"]),
                "staffScore": _score(row["server_total"]),
                "failed": service_id in self._failed,
                "sending": service_id in self._sending,
            })
        self._last_status = statuses
        if fresh and len(groups[2]) >= totals[2]:  # 分析中的全部載入了：不在裡面的都不是分析中了
            analyzing = {row["serviceId"] for row in groups[2]}
            for service_id in self._sent.ids() - analyzing:
                self._sent.forget(service_id)
        for tab, status in TAB_STATUS.items():
            self._tabs[tab].set_rows(groups[status], totals[status])
        if pin is not None and pin == self._pin:  # 這次的查詢有多取到它：顯示到它那一頁
            self._reveal(pin)
            self._pin = None
        if self._tabs["analyzing"].count and not self._stopped:
            if not self._ticker.isActive():
                self._ticker.start()
        else:
            self._ticker.stop()
        self._update_default_tab()

    def stop(self):
        """外殼關掉時（ServerHome.stop()）：停掉 sentText 的計時和自己重試；之後才回來的結果也不再開計時器。"""
        self._stopped = True
        self._ticker.stop()
        self._retry.stop()

    def _reveal(self, service_id):
        for model in self._tabs.values():
            if model.reveal(service_id) >= 0:
                return

    @Slot()
    def _tick(self):
        """30 秒到了：sentText 用現在的時間重新算（只有變了的列會更新）。"""
        self._apply(self._rows)

    def _update_default_tab(self):
        tab = default_tab(*(self._tabs[tab].total for tab in TABS)) if self._status == "ready" else ""
        if tab != self._default_tab:
            self._default_tab = tab
            self.defaultTabChanged.emit()

    # ------------------------------------------------------------ 送出分析（UX-18、ENG-05、ENG-14）

    @Slot(str, result=bool)
    def analyzeService(self, service_id):
        """送出分析：POST 在 io lane，收到 200 之後在 db lane 把狀態改成 2、重新整理清單。
        整段期間那一列的按鈕忙碌；結果用 toast 告訴使用者。回傳 False 表示這一筆已經在送了（防重複送出）。"""
        if service_id in self._sending:
            return False
        url = os.getenv("ANALYZE_API_URL", "")
        if not self.services.tasks.submit("io", analyze_key(service_id),
                                          lambda token, report: post_analysis(url, service_id),
                                          on_ok=lambda result: self._posted(service_id),
                                          on_err=lambda error: self._not_sent(service_id, error)):
            return False
        # 上一次的「送不出去」「分析失敗」都過時了：使用者重新送出
        self._close_toast(analyze_key(service_id))
        self._close_toast(outcome_key(service_id))
        self._set_sending(service_id, True)
        return True

    def _posted(self, service_id):
        """分析服務收到了：在 db lane 把狀態改成 2，順便重新整理清單（2 個往返）。"""
        posted_at = self._clock() * 1000  # 從分析服務收到的時候開始等
        request = self._request()
        db, account_id = self.services.db, self.account_id
        if not self.services.tasks.submit(
                "db", mark_key(service_id),
                lambda token, report: list_services(db, account_id, *request, mark_sent=service_id),
                on_ok=lambda rows: self._marked(service_id, posted_at, request, rows),
                on_err=lambda error: self._not_sent(service_id, error)):
            self._set_sending(service_id, False)  # TaskRunner 已經關了（程式正在結束）

    def _marked(self, service_id, posted_at, request, rows):
        self._sent.remember(service_id, posted_at)  # 先記下：_took 套用時這一列就有送出的時間
        self._failed.discard(service_id)
        self._sending.discard(service_id)
        self._took(rows, request)  # 那一列移到 Analyzing（DS-24：按鈕跟著那一列換掉）
        self._show_toast("info", _tr("Sent for analysis"),
                         _tr("%1 is being analyzed. You'll be notified when it's done.", service_id),
                         analyze_key(service_id))
        self.analysisSent.emit(service_id)

    def _not_sent(self, service_id, error):
        """POST 失敗，或分析服務收到了、但狀態沒改成 2：那一列回到 Analyze（或 Retry），跳常駐的錯誤（DS-20）。
        重送沒關係：狀態只會往前走，分析服務也不會把同一筆排兩次（ENG-14）。"""
        logger.warning("sending %s for analysis failed: %r", service_id, error)
        self._set_sending(service_id, False)
        retryable = user_message(error)[3]
        self._show_toast("error", _tr("Couldn't send for analysis"), toast_reason(error), analyze_key(service_id),
                         f"retry-analyze:{service_id}" if retryable else "", _tr("Retry") if retryable else "")

    def _set_sending(self, service_id, sending):
        if sending:
            self._sending.add(service_id)
        else:
            self._sending.discard(service_id)
        self._apply(self._rows)  # 只有那一列的 sending 變了：清單只發那一列的 dataChanged

    # ------------------------------------------------------------ 監看發現狀態變了（ENG-16、UX-17）

    def analysis_finished(self, service_id):
        """2 → 3：成功 toast＋View Results，清單在背景重新整理（不管在哪一頁都會更新）。

        帳號的偏好「Show Analysis Completion Alerts」關掉時不跳這一則（S2，T39）：清單照樣重新整理，
        之前同一筆的通知（例如分析失敗）收掉，免得畫面上留著過期的失敗。失敗的通知（analysis_failed）不受影響。
        """
        self._sent.forget(service_id)
        if self._completion_alerts():
            self._show_toast("success", _tr("Analysis complete"), _tr("Scores and charts for %1 are ready.", service_id),
                             outcome_key(service_id), f"open-analysis:{service_id}", _tr("View Results"))
        else:
            self._close_toast(outcome_key(service_id))
        self.refresh()

    def _completion_alerts(self):
        prefs = getattr(self.services, "ui_prefs", None)
        return prefs is None or prefs.showCompletionAlerts

    def analysis_failed(self, service_id):
        """2 → 1（分析服務失敗時把狀態改回 1，D4）：那一列標成 Failed、按鈕變成 Retry，跳常駐的錯誤＋Retry。"""
        self._sent.forget(service_id)
        self._failed.add(service_id)
        self._show_toast("error", _tr("Analysis failed"),
                         _tr("%1 couldn't be analyzed. You can send it again.", service_id),
                         outcome_key(service_id), f"retry-analyze:{service_id}", _tr("Retry"))
        self.refresh()

    def _show_toast(self, severity, title, detail, key, action_id="", action_label=""):
        self._toast_ids[key] = self.services.notifier.notify(severity, title, detail, key, action_id, action_label)

    def _close_toast(self, key):
        toast_id = self._toast_ids.pop(key, 0)
        if toast_id:
            self.services.notifier.dismiss(toast_id)

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()
            self._update_default_tab()

    def _set_busy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


# ---------------------------------------------------------------- 外殼

class ServerHome(QObject):
    """服務人員端的外殼（server_home.qml 的 serverHome）。services 是 AppServices；
    只有直接建立、不經過 launch() 時（例如測試明細頁的下載）可以是 None，這時沒有監看。

    分析完成的通知（ENG-16、ENG-17）：watcher 看到狀態變了，serviceModel 跳 toast，清單和首頁的卡片在背景重新整理；
    app 不在前景時 Dock 圖示跳動、加上徽章。通知上分析的動作（analyze:、retry-analyze:、open-analysis:）也在這裡接。
    """

    accountNameChanged = Signal()
    openAnalysisRequested = Signal(str)  # View Results：外殼換到分析頁、選 Completed、標出那一列

    def __init__(self, account_id, services=None):
        super().__init__()
        self.account_id = account_id
        self.services = services
        self._account_name = ""
        print(f"ServerHome initialized with account_id: {self.account_id}")
        self.deviceFinder = DeviceFinder(self)  # 不在這裡探測：打開錄影頁時才做
        self.homeDashboard = HomeDashboard(services, account_id, HOME_KEY_PREFIX, self)  # 新版首頁（U1，T28）
        self.serviceModel = ServiceModel(services, account_id)
        self.shell_window = None  # 外殼視窗（launch() 設定）：app 不在前景時讓 Dock 圖示跳動
        self.attention = DockAttention(self, prefs=getattr(services, "ui_prefs", None))  # Bounce Dock Icon（S2）
        self.watcher = None if services is None else self._watch(services)
        self.serviceManager = ServiceManager(services, account_id)
        self.chartsModel = ChartsModel()
        self.mediaModel = MediaModel()
        self.pdfmodel = PDFHandler(services)
        self.serviceSearch = ServiceSearch.for_staff(services, account_id, parent=self)  # 頁首的搜尋（只查自己的）
        self.helpCenter = HelpCenter(services, reports=self.pdfmodel, parent=self)  # 設定頁的 Help 區塊（T40）

    @Property(str, notify=accountNameChanged)
    def accountName(self):
        return self._account_name

    def start(self):
        """launch() 呼叫：帳號名稱、首頁的三張卡片、監看要看的服務都在 db lane 載入，GUI 執行緒不碰網路。
        上次沒存完的錄影從本機的設定讀回來，每一筆跳一則附 Retry 的通知（T26）。

        分析清單不在這裡查（T28）：側邊欄的 Analysis（onLoadAnalysis）、首頁的服務方塊、View Results（openAnalysis）、
        錄影存好、監看發現狀態變了，都會重新整理它。這樣 launch 是 1＋3＋1＝5 個往返（ENG-21 的預算）。
        """
        self.loadAccountName()
        self.homeDashboard.refresh()
        if self.watcher is not None:
            self.watcher.start()
        self.deviceFinder.restore_saves()

    def stop(self):
        """外殼關掉時：停止監看和分析頁的計時，清掉 Dock 的徽章。"""
        if self.watcher is not None:
            self.watcher.stop()
        self.serviceModel.stop()
        self.attention.clear()
        self.shell_window = None

    def _watch(self, services):
        """建立分析完成的監看（ENG-16），接上清單、通知和 Dock。"""
        watcher = AnalysisWatcher(services.tasks, services.db, self.account_id, self)
        self.serviceModel.analysisSent.connect(watcher.add)
        watcher.finished.connect(self._analysis_finished)
        watcher.failed.connect(self._analysis_failed)
        watcher.pollFailed.connect(self._watch_failed)
        watcher.pollSucceeded.connect(self.serviceModel.refresh_recovered)
        services.notifier.actionTriggered.connect(self._on_action)
        return watcher

    @Slot(str)
    def _analysis_finished(self, service_id):
        self.serviceModel.analysis_finished(service_id)
        self.homeDashboard.refresh()  # 首頁的三張卡片：舊的數字留著，新的到了再換（DS-33、PLAN 5-4）
        self.attention.request(self.shell_window)

    @Slot(str)
    def _analysis_failed(self, service_id):
        self.serviceModel.analysis_failed(service_id)
        self.attention.request(self.shell_window)

    @Slot(object)
    def _watch_failed(self, error):
        self.serviceModel.refresh_failed(error, self.watcher.interval(), watching=bool(self.watcher.watched))

    @Slot(str, int)
    def _on_action(self, action_id, toast_id):
        """通知上的動作。只處理分析的（CONTRACTS §4）；其他的（例如 retry-upload:）不認得就不管。"""
        kind, _, service_id = action_id.partition(":")
        if not service_id:
            return
        if kind in ("analyze", "retry-analyze"):  # 錄影存好的「Analyze」、送不出去或分析失敗的「Retry」
            self.serviceModel.analyzeService(service_id)
        elif kind == "open-analysis":
            self.openAnalysis(service_id)

    @Slot(str)
    def openAnalysis(self, service_id):
        """「View Results」：外殼換到分析頁、選 Completed、標出那一列（UX-17）。和側邊欄一樣，換頁時重新整理清單；
        那一列不在前 8 筆的話，Completed 顯示到它那一頁（同一個往返，T30）。"""
        self.serviceModel.pageOpened(service_id)
        self.openAnalysisRequested.emit(service_id)

    @Slot()
    def loadAccountName(self):
        db, account_id = self.services.db, self.account_id

        def load(token, report):
            with db.cursor(dictionary=True) as cursor:
                cursor.execute("CALL sp_get_account_name(%s)", (account_id,))
                row = cursor.fetchone()
            return row["account_name"] if row else ""

        self.services.tasks.submit("db", ACCOUNT_NAME_KEY, load, on_ok=self._set_account_name,
                                   on_err=self._account_name_failed)

    def _set_account_name(self, name):
        if name != self._account_name:
            self._account_name = name
            self.accountNameChanged.emit()

    def _account_name_failed(self, error):
        # 名字只是顯示用：同一次斷線，首頁的卡片會顯示區塊錯誤；按 Retry 或 Home 時再載一次
        logger.warning("loading the account name failed: %r", error)

    @Slot()
    def onLoadHome(self):
        """側邊欄的 Home：三張卡片在背景重新查詢（舊的數字留著），還沒載入的帳號名稱一起再載一次。"""
        self.reloadAccountName()
        self.homeDashboard.refresh()

    @Slot()
    def reloadAccountName(self):
        """首頁卡片的 Retry：名字沒載入（例如同一次斷線）時再載一次。"""
        if not self._account_name:
            self.loadAccountName()

    @Slot()
    def onLoadAnalysis(self):
        """側邊欄打開分析頁：每個分頁回到前 8 筆，清單在背景重新整理（1 個往返）。"""
        self.serviceModel.pageOpened()

    @Slot(result=str)
    def getAccountName(self):
        """舊的介面：回傳已經載入的名字，不連資料庫（QML 改綁 accountName 屬性）。"""
        return self._account_name

    @Slot(result=str)
    def getID(self):
        return self.account_id

    @Slot(str)
    @Slot(str, str)
    def load_service_media(self, service_id, start_time=""):
        """Details：先換頁，檔案在背景查詢、下載（UX-22、ENG-04）。這裡不碰網路，只改 mediaModel 的屬性、送出工作。

        services 是 None（直接建立 ServerHome 的測試）時照舊同步：在這裡查詢、下載完才回來。
        """
        self.mediaModel.open(service_id, start_time, self.services, self.chartsModel)

    @Slot()
    def clearDir(self):
        """Quit：取消還在下載的檔案，清空影片、圖表、報表頁面的快取（和 MediaModel 用同一組資料夾）。"""
        clear_cache(self.mediaModel)


def _register_services(engine, services):
    """外殼的 QML 用到 tasks、notifier、uiPrefs。main.py 在登入前就註冊好了；
    沒經過 MainWindow 的呼叫端（測試）由這裡補上，已經有的不蓋掉。"""
    context = engine.rootContext()
    for name, obj in (("tasks", services.tasks), ("notifier", services.notifier), ("uiPrefs", services.ui_prefs)):
        if context.contextProperty(name) is None:
            context.setContextProperty(name, obj)


def launch(engine, account_id, services=None):
    """登入後打開服務人員端：外殼馬上出現，資料在 db lane 載入（GUI 執行緒 0 個網路往返，ENG-06、ENG-21）。

    services：main.py 的 AppServices（TaskRunner、Notifier、DbLane、UiPrefs）。回傳 ServerHome。
    沒給時（還沒改成傳 services 的舊呼叫端）自己建一份和 main.py 一樣的，外殼關掉時一起收掉。
    """
    standalone = services is None
    if standalone:
        services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
    _register_services(engine, services)
    services.ui_prefs.useAccount(account_id)  # 這個帳號的視窗、播放、通知偏好（S1、S2，T39），QML 一載入就讀得到
    home = ServerHome(account_id, services)
    context = engine.rootContext()
    context.setContextProperty("serverHome", home)
    context.setContextProperty("deviceFinder", home.deviceFinder)
    context.setContextProperty("homeDashboard", home.homeDashboard)
    context.setContextProperty("serviceModel", home.serviceModel)
    context.setContextProperty("serviceManager", home.serviceManager)
    context.setContextProperty("chartsModel", home.chartsModel)
    context.setContextProperty("mediaModel", home.mediaModel)
    context.setContextProperty("pdfHandler", home.pdfmodel)
    context.setContextProperty("serviceSearch", home.serviceSearch)
    context.setContextProperty("helpCenter", home.helpCenter)

    opened = len(engine.rootObjects())
    qml_file = Path(__file__).resolve().parent.parent / "qml/pages/server_home.qml"
    engine.load(QUrl.fromLocalFile(qml_file))
    if len(engine.rootObjects()) == opened:
        sys.exit(-1)

    # 剛載入的外殼（第 0 個是登入視窗，UX-04）。照這個帳號上次的視窗模式打開，第一次是全螢幕（S1）
    window = engine.rootObjects()[-1]
    services.ui_prefs.restoreWindow(window)
    home.shell_window = window

    # setContextProperty 不會留住 Python 物件（PySide6 6.12 實測）：home 和各頁的模型只被自己的循環參照留著，
    # 下一次 gc 就會被刪掉，QML 拿到 null。destroyed 的連線留著 closed，closed 的預設值留著 home，
    # 所以 home 和外殼活得一樣久。destroyed 會把被刪掉的物件當第一個參數傳進來，所以預設值放在 * 後面
    def closed(*destroyed, keep_alive=home):
        keep_alive.stop()  # 外殼關了：不再監看
        if standalone:
            services.tasks.shutdown()

    window.destroyed.connect(closed)

    home.start()
    return home
