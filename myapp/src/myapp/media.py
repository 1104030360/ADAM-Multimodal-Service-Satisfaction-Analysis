"""服務明細（影片、錄音、圖表）的下載，以及報表（PDF）的檢視（前端重建 F3，CONTRACTS §4 的 C2）。

服務人員端和管理員端以前各有一份 MediaService、ChartsModel、MediaModel、PDFHandler（UX-46），現在共用這裡的。
兩個 init 模組 import 同樣的名稱：ServerHome／AdminHome 照舊建立、launch() 照舊註冊成 mediaModel、chartsModel、pdfHandler。

明細頁（UX-22、UX-23、ENG-04）：
- Details 先換頁：load_service_media() 只改屬性、送出工作，GUI 執行緒不碰網路。
- 檔案清單在 db lane 查（1 個往返，key media:<sid>），頁首（日期、時間、分析狀態）和分數列也在同一個查詢
  （新版介面 U2，T31）；每個檔案一個 io 工作（key media:<sid>:<檔名>），
  用 storage.download_with_progress，進度（TaskRunner 已節流到每秒 4 次）寫進那個檔案自己的 FileSlot。
- 兩條佇列，各一次一個檔案：圖表（小，先好）；影片和錄音（顧客 → 員工 → 錄音）。這一頁最多佔 io lane 的 2 條執行緒，
  上傳和送出分析不會排在一堆下載後面。
- 快取：本機已經有這個檔名就直接用。下載先寫暫存檔、成功才改名，所以有就是完整的；Quit 時 clearDir() 清掉。
- 離開明細頁（頁面的 Component.onDestruction 呼叫 close()）：取消還沒下載完的，已經下載好的留在快取（T21）。
- 一個檔案失敗只影響它自己：那個位置顯示錯誤和 Retry；圖表有幾張沒載入時，圖表區上方有警告列（DS-27、DS-52）。

報表（PLAN D7、UX-26、UX-27、ENG-07）：PDFHandler 列出 src/myapp/pdf/ 裡的 PDF（新的在前，名稱、頁數、日期）；
沒有來源時頁面顯示空狀態。開檔、畫頁面、數頁數都在 pdf lane（PyMuPDF 只在一條執行緒用），頁面圖存到快取資料夾，
檔名帶版本，換文件不會顯示到舊圖。
"""
import logging
import os
import re
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

import fitz
from PySide6.QtCore import QCoreApplication, QDateTime, QLocale, Property, QObject, QUrl, Signal, Slot

from myapp import local_storage, storage
from myapp.database import DatabaseConnector
from myapp.notifications import S3_NOT_FOUND, S3_OFFLINE, UNEXPECTED, user_message
from myapp.service_list import day_text, display_score, format_time, time_range_text
from myapp.tasks import CancelToken, TaskRunner

logger = logging.getLogger(__name__)

# 區塊和檔案的狀態（DS-25～27）
IDLE, LOADING, EMPTY, READY, ERROR = "idle", "loading", "empty", "ready", "error"
MISSING, WAITING, DOWNLOADING = "missing", "waiting", "downloading"
PENDING = (WAITING, DOWNLOADING)
MEDIA_KINDS = ("cam0", "cam1", "audio")  # 顧客鏡頭、員工鏡頭、錄音

# 快取的位置：沒有設定 LOCAL_MEDIA_DIR／LOCAL_CHARTS_DIR 時，和以前一樣在 init/ 底下（模組旁邊的 media/、charts/）
CACHE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "init")
# 報表的來源：還沒有決定（PLAN D7），先看套件裡的 pdf/ 資料夾；repo 裡沒有這個資料夾，所以報表頁是空狀態
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf")

# 一個查詢取回一筆服務的所有檔案（ENG-04：以前 4 個查詢）。每個鏡頭、錄音取最新的一列；圖表依寫入的順序。
# UNION 裡個別的 ORDER BY 不保證最後的順序，所以多選 part、seq 兩欄，最後再排一次
FILES_UNION = """
(SELECT 1 AS part, 0 AS seq, 'cam0' AS kind, video_url AS url FROM tbl_video_storage
  WHERE service_id = %s AND video_name LIKE %s ORDER BY video_id DESC LIMIT 1)
UNION ALL
(SELECT 2, 0, 'cam1', video_url FROM tbl_video_storage
  WHERE service_id = %s AND video_name LIKE %s ORDER BY video_id DESC LIMIT 1)
UNION ALL
(SELECT 3, 0, 'audio', audio_url FROM tbl_audio_storage
  WHERE service_id = %s ORDER BY audio_id DESC LIMIT 1)
UNION ALL
(SELECT 4, charts_id, 'chart', charts_url FROM tbl_charts_storage WHERE service_id = %s)
"""

# 明細頁打開時的查詢（新版介面 U2，T31 第 3 節）：服務本身（時間、狀態、服務人員、8 個分數）＋檔案清單，還是 1 個往返。
# 每個檔案一列，服務的欄位在每一列重複（最多十來列）；沒有檔案時剩一列、kind 是 NULL；沒有這筆服務時 0 列
DETAIL_SQL = f"""
SELECT s.service_start, s.service_end, s.service_status, a.account_name,
       sc.customer_total, sc.customer_text, sc.customer_audio, sc.customer_facial,
       sc.server_total, sc.server_text, sc.server_audio, sc.server_facial,
       f.kind, f.url
FROM tbl_service AS s
LEFT JOIN tbl_account AS a ON a.account_id = s.account_id
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
LEFT JOIN ({FILES_UNION}) AS f ON TRUE
WHERE s.service_id = %s
ORDER BY f.part, f.seq
"""
SCORE_KINDS = ("total", "text", "audio", "facial")
SCORE_SIDES = (("customer", "customer"), ("staff", "server"))  # （畫面上的名稱, 資料表欄位的前綴）


def media_dir():
    return os.getenv("LOCAL_MEDIA_DIR") or os.path.join(CACHE_ROOT, "media")


def charts_dir():
    return os.getenv("LOCAL_CHARTS_DIR") or os.path.join(CACHE_ROOT, "charts")


def reports_cache_dir():
    """報表頁面圖的快取：和影片的快取放在同一層（測試時跟著 LOCAL_MEDIA_DIR 進暫存資料夾）。"""
    return os.path.join(os.path.dirname(os.path.normpath(media_dir())), "reports")


def list_key(service_id):
    return f"media:{service_id}"


def task_key(service_id, file_name):
    return f"media:{service_id}:{file_name}"


def _tr(text, *values):
    """介面上的英文（PLAN D1）：經過 translate，日後可以加翻譯；%1、%2… 一次換成 values（像 QString::arg）。"""
    text = QCoreApplication.translate("media", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def technical_text(exc, file_name=""):
    """收在「Details」裡的技術細節，例如「S…_cam0.avi · ClientError: An error occurred (404)…」。"""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return f"{file_name} · {text}" if file_name else text


def reason_text(exc):
    """區塊錯誤的原因（ENG-12）：「Can't reach the database. Check your network and try again.」"""
    _, title, detail, _ = user_message(exc)
    return f"{title}. {detail}" if detail else title


def status_text(status):
    """明細頁頁首的分析狀態（tbl_service.service_status）；還不知道是空字串。"""
    return {1: _tr("Not analyzed"), 2: _tr("Analyzing"), 3: _tr("Analysis completed")}.get(status, "")


def describe_failure(exc, file_name):
    """一個檔案下載失敗：(原因, 技術細節)。原因先講發生什麼，再講下一步（DS-50、DS-52）。"""
    _, title, _, _ = user_message(exc)
    if title == S3_OFFLINE.title:
        reason = _tr("The download of %1 was interrupted. Check your network and try again.", file_name)
    elif title == S3_NOT_FOUND.title:
        reason = _tr("%1 isn't in cloud storage. It may have been moved or deleted.", file_name)
    else:
        reason = reason_text(exc)
    return reason, technical_text(exc, file_name)


def _log_failure(what, exc):
    if user_message(exc)[1] == UNEXPECTED.title:  # 程式的問題：留下 traceback
        logger.error("%s failed", what, exc_info=exc)
    else:
        logger.warning("%s failed: %r", what, exc)


# ---------------------------------------------------------------- 檔案：查詢、快取、下載（任何執行緒都能用）


@dataclass(frozen=True)
class MediaFile:
    kind: str   # cam0（顧客）、cam1（員工）、audio、chart
    url: str    # 資料庫裡的位置：s3://<bucket>/<key>
    name: str   # 檔名：S3 上是 video/、audio/、charts/ 底下的這個名字，本機也存成這個名字

    @property
    def key(self):
        folder = {"audio": "audio", "chart": "charts"}.get(self.kind, "video")
        return f"{folder}/{self.name}"

    @property
    def local_path(self):
        return os.path.join(charts_dir() if self.kind == "chart" else media_dir(), self.name)


def _files_params(service_id):
    return (service_id, "%cam0%", service_id, "%cam1%", service_id, service_id)


def _media_file(kind, url):
    """一列 → MediaFile；沒有檔名的列（或 LEFT JOIN 沒有檔案的那一列）找不到檔案，回傳 None。"""
    kind = kind.decode() if isinstance(kind, (bytes, bytearray)) else kind
    url = url or ""
    name = os.path.basename(url)
    return MediaFile(kind, url, name) if kind and name else None


@dataclass(frozen=True)
class ServiceSummary:
    """明細頁頁首和分數列要的資料庫的值（還沒格式化；文字在 GUI 執行緒由 MediaModel 產生）。

    scores：{"customer": {"total", "text", "audio", "facial"}, "staff": {...}}，資料庫的原始值（-1 是沒有資料）。
    """
    start: object
    end: object
    status: int
    staff_name: str
    scores: dict


def load_detail(cursor, service_id):
    """一筆服務的頁首、分數和檔案（1 個往返，DETAIL_SQL）。回傳 (ServiceSummary 或 None, [MediaFile])；
    None 表示資料庫裡沒有這筆服務。cursor 的列可以是 tuple（用 column_names 對應欄位）或 dict。"""
    cursor.execute(DETAIL_SQL, (*_files_params(service_id), service_id))
    rows = cursor.fetchall()
    if rows and not isinstance(rows[0], dict):
        rows = [dict(zip(cursor.column_names, row)) for row in rows]
    if not rows:
        return None, []
    first = rows[0]
    summary = ServiceSummary(
        first["service_start"], first["service_end"], int(first["service_status"] or 0), first["account_name"] or "",
        {side: {kind: first[f"{prefix}_{kind}"] for kind in SCORE_KINDS} for side, prefix in SCORE_SIDES})
    files = [file for file in (_media_file(row["kind"], row["url"]) for row in rows) if file is not None]
    return summary, files


def fetch(file, token=None, report=None):
    """在 io lane 執行：本機已經有就直接用；沒有就下載。回傳本機路徑。

    先下載到這一次專用的暫存檔（storage 再先寫 .part），成功才改名：取消、失敗都不會留下半個檔案，
    同一個檔案被取消後馬上又下載（例如按 Back 又按 Details）時，兩次也不會寫到同一個暫存檔。
    """
    final = file.local_path
    if os.path.isfile(final):
        return final
    os.makedirs(os.path.dirname(final), exist_ok=True)
    temp = f"{final}.{uuid.uuid4().hex}.download"
    try:
        storage.download_with_progress(file.key, temp, token, report)
        os.replace(temp, final)
    except BaseException:
        with suppress(FileNotFoundError):
            os.remove(temp)
        raise
    return final


def download_cache_dirs():
    """可以重新下載的快取資料夾（new-feature S5，T40）：雲端有原檔、隨時能重新下載的東西才放進來。

    Quit 時的 clear_cache()、設定頁 Storage 的大小與 Clear Download Cache 都看這一份，三個地方一定一致。
    錄影（~/video、~/audio）、還沒上傳的檔案不能放進來（local_storage 另外會擋）。新的快取資料夾加在清單後面。
    """
    return [
        media_dir(),
        charts_dir(),
        reports_cache_dir(),
    ]


def clear_cache(media_model=None):
    """Quit 時（clearDir）：先取消還在下載的檔案，再清空 download_cache_dirs() 的內容（資料夾本身留著）。

    和設定頁的 Clear Download Cache 用同一個 local_storage.clear_folders()：錄影、還沒存完的錄影不會被清掉。
    """
    if media_model is not None:
        media_model.close()
    local_storage.clear_folders(download_cache_dirs())


class _InlineRunner:
    """沒有 TaskRunner 時（services 是 None：還沒改成傳 services 的呼叫端、測試）：工作在呼叫的執行緒當場做完。"""

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        try:
            result = fn(CancelToken(), lambda done, total: None)
        except Exception as exc:
            if on_err is not None:
                on_err(exc)
        else:
            if on_ok is not None:
                on_ok(result)
        return True

    def cancel(self, key):
        pass


_INLINE = _InlineRunner()


# ---------------------------------------------------------------- 明細頁


class FileSlot(QObject):
    """明細頁上一個檔案的位置（影片、錄音，或一張圖表）。QML 綁它的屬性。

    state：idle（還不知道有沒有這個檔案）、missing（這筆服務沒有）、waiting（排在 waitingFor 後面）、downloading、
    ready（source 是本機檔案的 URL）、error（errorDetail、errorTechnical；failureCount 是連續失敗幾次）。
    """

    changed = Signal()          # state、errorDetail、errorTechnical、failureCount、waitingFor
    sourceChanged = Signal()    # 只在 source 真的變了才發：MediaPlayer 的 source 被重設會停止播放
    progressChanged = Signal()  # done、total（bytes；total 0 表示還不知道總量）

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.file = None  # MediaFile
        self._state = IDLE
        self._source = ""
        self._done = self._total = 0.0
        self._detail = self._technical = ""
        self._failures = 0
        self._waiting_for = ""

    @Property(str, constant=True)
    def kind(self):
        return self._kind

    @Property(str, notify=changed)
    def state(self):
        return self._state

    @Property(str, notify=sourceChanged)
    def source(self):
        return self._source

    @Property(float, notify=progressChanged)
    def done(self):
        return self._done

    @Property(float, notify=progressChanged)
    def total(self):
        return self._total

    @Property(str, notify=changed)
    def errorDetail(self):
        return self._detail

    @Property(str, notify=changed)
    def errorTechnical(self):
        return self._technical

    @Property(int, notify=changed)
    def failureCount(self):
        return self._failures

    @Property(str, notify=changed)
    def waitingFor(self):
        """排在哪一個檔案後面：cam0、cam1、audio、chart；空字串＝沒有在排隊。"""
        return self._waiting_for

    # 以下都在 GUI 執行緒呼叫（MediaModel）

    def reset(self):
        self.file = None
        self._failures = 0
        self._set(IDLE, source="", detail="", technical="", waiting="")
        self.set_progress(0.0, 0.0)

    def assign(self, file):
        """檔案清單查好了：file 是 None 表示這筆服務沒有這個檔案。"""
        self.file = file
        self._failures = 0
        self._set(MISSING if file is None else WAITING, source="", detail="", technical="", waiting="")
        self.set_progress(0.0, 0.0)

    def wait(self, waiting_for):
        self._set(WAITING, waiting=waiting_for)

    def start(self):
        self.set_progress(0.0, 0.0)
        self._set(DOWNLOADING, waiting="")

    def set_progress(self, done, total):
        if (done, total) != (self._done, self._total):
            self._done, self._total = float(done), float(total)
            self.progressChanged.emit()

    def succeed(self, source):
        self._failures = 0
        self._set(READY, source=source, detail="", technical="", waiting="")

    def fail(self, detail, technical):
        self._failures += 1
        self._set(ERROR, source="", detail=detail, technical=technical, waiting="")

    def stop(self):
        """還在排隊或下載中的被取消了（離開明細頁）。"""
        if self._state in PENDING:
            self._set(IDLE, waiting="")

    def _set(self, state, source=None, detail=None, technical=None, waiting=None):
        if detail is not None:
            self._detail = detail
        if technical is not None:
            self._technical = technical
        if waiting is not None:
            self._waiting_for = waiting
        self._state = state
        if source is not None and source != self._source:
            self._source = source
            self.sourceChanged.emit()
        self.changed.emit()


class _Stream:
    """一次下載一個檔案的佇列。"""

    def __init__(self):
        self.running = None   # 下載中的 FileSlot
        self.queue = deque()  # 排隊中的 FileSlot


class ChartsModel(QObject):
    """明細頁的圖表分頁（DS-52「部分圖表沒有載入」）。圖表的 FileSlot 由 MediaModel 建立和下載，這裡整理成畫面要的數字。

    - chartsSources：已經下載好的圖表（本機檔案的 URL），依資料庫的順序。沒載入的不放，但 failedCount 會說有幾張。
    - state：idle、loading（還在查檔案清單）、empty（這筆服務沒有圖表）、downloading（還有圖表在下載或排隊）、
      ready（都結束了，至少一張好的；failedCount 不是 0 時畫面上方有警告列）、error（都結束了，一張也沒有載入）。
    - 下載中的那一張：currentNumber（第幾張，1 起算）、done、total。
    - retry()：沒載入的那幾張重新下載。
    """

    chartsSourceChanged = Signal()
    stateChanged = Signal()
    progressChanged = Signal()

    def __init__(self, services=None, parent=None):
        super().__init__(parent)
        self._owner = None    # MediaModel：重試交給它排進佇列
        self._slots = []
        self._listed = False
        self._phase = IDLE    # 清單還沒查好時是 idle 或 loading
        self._sources = []
        self._state = IDLE
        self._current = None  # 下載中的那一張

    def slots(self):
        return list(self._slots)

    @Property("QVariantList", notify=chartsSourceChanged)
    def chartsSources(self):
        return list(self._sources)

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(int, notify=stateChanged)
    def totalCount(self):
        return len(self._slots)

    @Property(int, notify=stateChanged)
    def readyCount(self):
        return len(self._sources)

    @Property(int, notify=stateChanged)
    def failedCount(self):
        return sum(1 for slot in self._slots if slot.state == ERROR)

    @Property(int, notify=stateChanged)
    def pendingCount(self):
        return sum(1 for slot in self._slots if slot.state in PENDING)

    @Property(int, notify=progressChanged)
    def currentNumber(self):
        return self._slots.index(self._current) + 1 if self._current is not None else 0

    @Property(float, notify=progressChanged)
    def done(self):
        return self._current.done if self._current is not None else 0.0

    @Property(float, notify=progressChanged)
    def total(self):
        return self._current.total if self._current is not None else 0.0

    @Property(str, notify=stateChanged)
    def errorDetail(self):
        failed = self._failed()
        return failed[0].errorDetail if failed else ""

    @Property(str, notify=stateChanged)
    def errorTechnical(self):
        return "\n".join(slot.errorTechnical for slot in self._failed())

    @Property(int, notify=stateChanged)
    def failureCount(self):
        return max((slot.failureCount for slot in self._failed()), default=0)

    @Slot(result=bool)
    def retry(self):
        return self._owner.retry_charts() if self._owner is not None else False

    # 以下由 MediaModel 呼叫（GUI 執行緒）

    def _attach(self, owner):
        self._owner = owner

    def _reset(self, loading):
        self._phase = LOADING if loading else IDLE
        self._replace([], listed=False)

    def _assign(self, slots):
        self._replace(slots, listed=True)

    def _failed(self):
        return [slot for slot in self._slots if slot.state == ERROR]

    def _replace(self, slots, listed):
        for slot in self._slots:  # 上一筆服務的圖表：不再影響畫面
            with suppress(RuntimeError, TypeError):
                slot.changed.disconnect(self._update)
            with suppress(RuntimeError, TypeError):
                slot.progressChanged.disconnect(self.progressChanged)
        self._slots = list(slots)
        self._listed = listed
        for slot in self._slots:
            slot.changed.connect(self._update)
            slot.progressChanged.connect(self.progressChanged)
        self._update()

    @Slot()
    def _update(self):
        sources = [slot.source for slot in self._slots if slot.state == READY]
        if sources != self._sources:
            self._sources = sources
            self.chartsSourceChanged.emit()
        if not self._listed:
            self._state = self._phase
        elif not self._slots:
            self._state = EMPTY
        elif self.pendingCount:
            self._state = DOWNLOADING
        elif self._sources:
            self._state = READY
        elif self._failed():
            self._state = ERROR
        else:  # 全部被取消了（離開明細頁）
            self._state = IDLE
        self._current = next((slot for slot in self._slots if slot.state == DOWNLOADING), None)
        self.stateChanged.emit()
        self.progressChanged.emit()


class MediaModel(QObject):
    """明細頁（ServiceDetailView.qml）：頁首、影片和錄音三個 FileSlot，並且安排所有檔案（包括圖表）的下載。

    - open(service_id, start_time, services, charts)：load_service_media() 呼叫。GUI 執行緒只改屬性、送出工作。
      services 是 None（舊的呼叫端、測試）時，在呼叫的執行緒當場做完，和以前一樣是同步的；清單用舊的共用連線查。
    - state：idle（還沒打開任何服務）、loading（查檔案清單中）、error（清單沒查到：errorDetail，reload() 重試）、ready。
      busy：清單查詢中（包括錯誤之後按 Retry）。
    - cam0（顧客）、cam1（員工）、audio：FileSlot。cam0Source、cam1Source、audioSource 是以前的名稱（＝各自的 source）。
    - canPlay：三個檔案都結束了（好了、這筆沒有、或失敗），而且至少一個好了。要同步播放，所以等下載完才能按 Play。
    - retry(kind)、close()：QML 呼叫。close() 取消還沒下載完的檔案，已經下載好的留在快取。
    - 頁首和分數列（新版介面 U2，PLAN §6-5、T31 第 3 節）：和檔案清單同一個查詢（DETAIL_SQL，還是 1 個往返）。
      Details 傳進來的開始時間先拿來顯示日期；查到之後換成資料庫的值。
      dateText「Oct 31, 2026」、timeText「09:15–09:21」、analysisStatus（1～3，還不知道是 0）、
      statusText「Analysis completed」、headerText（三段用「 · 」接起來）、staffName（這筆服務的服務人員）、
      scores {"customer": {total, text, audio, facial}, "staff": {…}}（小數 1 位，沒有資料是 null；
      分數帶由 QML 的 Theme.bandName() 判斷）、hasScores（至少有一個分數）、
      serviceFound（查過了、資料庫裡沒有這筆時是 false）。
    """

    headerChanged = Signal()
    stateChanged = Signal()
    playableChanged = Signal()
    cam0SourceChanged = Signal()
    cam1SourceChanged = Signal()
    audioSourceChanged = Signal()

    def __init__(self, services=None, charts=None, parent=None):
        super().__init__(parent)
        self._services = None
        self._charts = None
        self._connector = None  # services 是 None 時查清單用的舊連線（DatabaseConnector）
        self._slots = {kind: FileSlot(kind, self) for kind in MEDIA_KINDS}
        for kind, slot in self._slots.items():
            slot.changed.connect(self.playableChanged)
            slot.sourceChanged.connect(getattr(self, f"{kind}SourceChanged"))
        self._streams = {"charts": _Stream(), "media": _Stream()}
        self._keys = {}  # 下載工作的 key → FileSlot：進度要知道是哪一個位置
        self._service_id = self._start_time = ""
        self._summary = None   # ServiceSummary：查到之前是 None
        self._found = True
        self._state = IDLE
        self._busy = False
        self._detail = self._technical = ""
        self._failures = 0
        self._session = 0  # 每次 open()、close() 加 1：之前送出的工作的結果都不要了
        self._bind(services, charts if charts is not None else ChartsModel(parent=self))

    # ------------------------------------------------------------ QML 讀的屬性

    @Property(str, notify=headerChanged)
    def serviceId(self):
        return self._service_id

    @Property(str, notify=headerChanged)
    def startTime(self):
        return self._start_time

    @Property(bool, notify=headerChanged)
    def serviceFound(self):
        return self._found

    @Property(str, notify=headerChanged)
    def dateText(self):
        start = self._header_start()
        return day_text(start) if start is not None else ""

    @Property(str, notify=headerChanged)
    def timeText(self):
        start = self._header_start()
        return time_range_text(start, self._summary.end if self._summary is not None else None)

    @Property(int, notify=headerChanged)
    def analysisStatus(self):
        return self._summary.status if self._summary is not None else 0

    @Property(str, notify=headerChanged)
    def statusText(self):
        return status_text(self.analysisStatus)

    @Property(str, notify=headerChanged)
    def headerText(self):
        """「Oct 31, 2026 · 09:15–09:21 · Analysis completed」：還不知道的部分不寫。"""
        return " · ".join(part for part in (self.dateText, self.timeText, self.statusText) if part)

    @Property(str, notify=headerChanged)
    def staffName(self):
        return self._summary.staff_name if self._summary is not None else ""

    @Property("QVariantMap", notify=headerChanged)
    def scores(self):
        raw = self._summary.scores if self._summary is not None else {}
        return {side: {kind: display_score(raw.get(side, {}).get(kind)) for kind in SCORE_KINDS}
                for side, _ in SCORE_SIDES}

    @Property(bool, notify=headerChanged)
    def hasScores(self):
        return any(value is not None for values in self.scores.values() for value in values.values())

    def _header_start(self):
        """頁首的開始時間：查到之前用 Details 傳進來的（格式不對就不顯示）。"""
        if self._summary is not None:
            return self._summary.start
        try:
            return datetime.strptime(self._start_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=stateChanged)
    def errorDetail(self):
        return self._detail

    @Property(str, notify=stateChanged)
    def errorTechnical(self):
        return self._technical

    @Property(int, notify=stateChanged)
    def failureCount(self):
        return self._failures

    @Property(QObject, constant=True)
    def cam0(self):
        return self._slots["cam0"]

    @Property(QObject, constant=True)
    def cam1(self):
        return self._slots["cam1"]

    @Property(QObject, constant=True)
    def audio(self):
        return self._slots["audio"]

    @Property(bool, notify=playableChanged)
    def canPlay(self):
        slots = self._slots.values()
        return (self._state == READY and not any(slot.state in (IDLE, *PENDING) for slot in slots)
                and any(slot.state == READY for slot in slots))

    @Property(bool, notify=playableChanged)
    def pending(self):
        """影片或錄音還在排隊或下載中。"""
        return any(slot.state in PENDING for slot in self._slots.values())

    @Property(str, notify=cam0SourceChanged)
    def cam0Source(self):
        return self._slots["cam0"].source

    @Property(str, notify=cam1SourceChanged)
    def cam1Source(self):
        return self._slots["cam1"].source

    @Property(str, notify=audioSourceChanged)
    def audioSource(self):
        return self._slots["audio"].source

    def slot(self, kind):
        return self._slots[kind]

    # ------------------------------------------------------------ 打開、重試、離開

    def open(self, service_id, start_time="", services=None, charts=None, connector=None):
        """顯示 service_id 這筆服務（Details）：先清掉上一筆，再在背景查檔案清單、下載。"""
        self.close()
        self._bind(services, charts)
        self._connector = connector
        self._session += 1
        self._service_id, self._start_time = service_id, start_time
        self._summary, self._found = None, True
        self.headerChanged.emit()
        for slot in self._slots.values():
            slot.reset()
        self._charts._reset(loading=True)
        self._failures = 0
        self._detail = self._technical = ""
        self._state = LOADING
        self._list()

    @Slot(result=bool)
    def reload(self):
        """清單沒查到時的 Retry。"""
        if self._state != ERROR or self._busy:
            return False
        self._list()
        return True

    @Slot(str, result=bool)
    def retry(self, kind):
        """影片或錄音的 Retry：排到下一個下載（不會打斷正在下載的那一個）。"""
        slot = self._slots.get(kind)
        if slot is None or slot.state != ERROR or slot.file is None:
            return False
        self._requeue("media", [slot])
        return True

    def retry_charts(self):
        failed = [slot for slot in self._charts.slots() if slot.state == ERROR]
        if not failed:
            return False
        self._requeue("charts", failed)
        return True

    @Slot()
    def close(self):
        """離開明細頁：取消還沒下載完的檔案（storage 會刪掉 .part），已經下載好的留在快取；還在查的清單也取消。"""
        runner = self._runner()
        if self._busy:
            runner.cancel(list_key(self._service_id))
            self._busy = False
            if self._state == LOADING:
                self._state = IDLE
                self._charts._reset(loading=False)
            self.stateChanged.emit()
        for stream in self._streams.values():
            if stream.running is not None:
                stream.running.stop()
                stream.running = None
            for slot in stream.queue:
                slot.stop()
            stream.queue.clear()
        for key in self._keys:
            runner.cancel(key)
        self._keys.clear()
        self._session += 1

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    def _bind(self, services, charts):
        if services is not self._services:
            if self._services is not None:
                with suppress(RuntimeError, TypeError):
                    self._services.tasks.progressed.disconnect(self._on_progress)
            self._services = services
            if services is not None:
                services.tasks.progressed.connect(self._on_progress)
        if charts is not None and charts is not self._charts:
            self._charts = charts
            charts._attach(self)

    def _runner(self):
        return self._services.tasks if self._services is not None else _INLINE

    def _list(self):
        session, service_id = self._session, self._service_id
        if self._services is not None:
            db = self._services.db

            def work(token, report):
                # dictionary cursor：其他頁面的查詢都用它，已經建好了（多建一種 cursor 要多 1 個往返，ENG-01）
                with db.cursor(dictionary=True) as cursor:
                    return load_detail(cursor, service_id)
        else:
            connector = self._connector or DatabaseConnector()

            def work(token, report):
                cursor = connector.connection.cursor()
                try:
                    return load_detail(cursor, service_id)
                finally:
                    cursor.close()

        self._busy = True
        self.stateChanged.emit()
        self._runner().submit("db", list_key(service_id), work,
                              on_ok=lambda files: self._listed(session, files),
                              on_err=lambda exc: self._list_failed(session, exc), replace=True)

    def _listed(self, session, detail):
        if session != self._session:
            return
        summary, files = detail
        self._summary, self._found = summary, summary is not None
        if summary is not None:
            self._start_time = format_time(summary.start)
        self.headerChanged.emit()
        by_kind = {}
        charts = []
        for file in files:
            if file.kind == "chart":
                charts.append(file)
            elif file.kind in MEDIA_KINDS:
                by_kind.setdefault(file.kind, file)
        for kind, slot in self._slots.items():
            slot.assign(by_kind.get(kind))
        chart_slots = []
        for file in charts:
            slot = FileSlot("chart")
            slot.assign(file)
            chart_slots.append(slot)
        self._charts._assign(chart_slots)
        self._busy = False
        self._failures = 0
        self._detail = self._technical = ""
        self._state = READY
        self.stateChanged.emit()
        self.playableChanged.emit()
        self._enqueue("charts", chart_slots)  # 圖表小，先下載先顯示
        self._enqueue("media", [self._slots[kind] for kind in MEDIA_KINDS if kind in by_kind])

    def _list_failed(self, session, exc):
        if session != self._session:
            return
        _log_failure(f"listing the files of {self._service_id}", exc)
        self._busy = False
        self._failures += 1
        self._detail, self._technical = reason_text(exc), technical_text(exc)
        self._state = ERROR
        self.stateChanged.emit()

    def _enqueue(self, name, slots):
        self._streams[name].queue.extend(slots)
        self._pump(name)

    def _requeue(self, name, slots):
        stream = self._streams[name]
        for slot in reversed(slots):
            slot.wait("")
            stream.queue.appendleft(slot)
        self._pump(name)

    def _pump(self, name):
        stream = self._streams[name]
        while stream.running is None and stream.queue:
            slot = stream.queue.popleft()
            path = slot.file.local_path
            if os.path.isfile(path):  # 快取（只做一次 stat，不碰網路）
                slot.succeed(QUrl.fromLocalFile(path).toString())
                continue
            self._start(name, slot)
        ahead = stream.running
        for slot in stream.queue:
            slot.wait(ahead.kind if ahead is not None else "")
            ahead = slot

    def _start(self, name, slot):
        stream = self._streams[name]
        stream.running = slot
        key = task_key(self._service_id, slot.file.name)
        self._keys[key] = slot
        slot.start()
        session, file = self._session, slot.file
        self._runner().submit("io", key, lambda token, report: fetch(file, token, report),
                              on_ok=lambda path: self._fetched(session, name, key, slot, path),
                              on_err=lambda exc: self._fetch_failed(session, name, key, slot, exc), replace=True)

    def _finish(self, session, name, key, slot):
        """一個下載結束了：是這一次打開的服務才算數（以前送出的已經被取消，不會走到這裡）。"""
        if session != self._session:
            return False
        stream = self._streams[name]
        if stream.running is slot:
            stream.running = None
        self._keys.pop(key, None)
        return True

    def _fetched(self, session, name, key, slot, path):
        if self._finish(session, name, key, slot):
            slot.succeed(QUrl.fromLocalFile(path).toString())
            self._pump(name)

    def _fetch_failed(self, session, name, key, slot, exc):
        if self._finish(session, name, key, slot):
            _log_failure(f"downloading {slot.file.key}", exc)
            slot.fail(*describe_failure(exc, slot.file.name))
            self._pump(name)

    @Slot(str, float, float)
    def _on_progress(self, key, done, total):
        slot = self._keys.get(key)
        if slot is not None:
            slot.set_progress(done, total)


class MediaService:
    """一筆服務的檔案，舊的同步介面（ENG-21：直接測得到）。run() 在呼叫的執行緒查清單、下載，結果寫進兩個模型。

    畫面用的是 MediaModel.open()（背景）。這裡走同一套狀態，只是工作當場做完；db_connector 是舊的共用連線。
    """

    def __init__(self, service_id, db_connector=None):
        self.service_id = service_id
        self.db_connector = db_connector

    def run(self, media_model, charts_model):
        media_model.open(self.service_id, services=None, charts=charts_model, connector=self.db_connector)


# ---------------------------------------------------------------- 報表（PDF）

DEFAULT_ZOOM = 2.0  # 不知道顯示多大時：144 dpi
MIN_ZOOM, MAX_ZOOM = 1.0, 4.0


def render_page(pdf_path, page_number, target_height, out_dir):
    """在 pdf lane 執行：畫 pdf_path 的第 page_number 頁（1 起算，超出範圍會夾到頭尾），回傳 (總頁數, 頁數, 圖檔路徑)。

    解析度依顯示的高度（×devicePixelRatio，QML 傳進來的 target_height）決定，Retina 上不會糊（ENG-07）；
    不知道時用 2 倍。圖檔名帶 PDF 的修改時間、頁數和倍率：內容不同就是不同的 URL，Image 不會顯示到快取的舊圖。
    """
    with fitz.open(pdf_path) as document:
        count = document.page_count
        index = min(max(page_number, 1), count) - 1
        page = document.load_page(index)
        height = page.rect.height or 1
        zoom = min(max(target_height / height, MIN_ZOOM), MAX_ZOOM) if target_height > 0 else DEFAULT_ZOOM
        info = os.stat(pdf_path)
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        image = os.path.join(out_dir, f"{stem}-{info.st_mtime_ns}-{info.st_size}-p{index + 1}-z{round(zoom * 100)}.png")
        if not os.path.isfile(image):
            os.makedirs(out_dir, exist_ok=True)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            temp = f"{image}.{uuid.uuid4().hex}.part"
            try:
                pixmap.save(temp, output="png")
                os.replace(temp, image)
            except BaseException:
                with suppress(FileNotFoundError):
                    os.remove(temp)
                raise
    return count, index + 1, image


def report_title(name):
    """清單和檢視器上的名稱：檔名去掉 .pdf（清單裡全部都是 PDF，副檔名不提供資訊）。"""
    stem, extension = os.path.splitext(name)
    return stem if extension.lower() == ".pdf" else name


def modified_text(mtime_ns):
    """修改日期，例如「Nov 1, 2026」：介面是英文（PLAN D1），不跟著系統的語言。"""
    moment = QDateTime.fromMSecsSinceEpoch(mtime_ns // 1_000_000)
    return QLocale(QLocale.Language.English, QLocale.Country.UnitedStates).toString(moment.date(), "MMM d, yyyy")


def count_pages(folder, wanted, token=None):
    """在 pdf lane 執行：wanted 是 [(檔名, 版本)]，回傳 {(檔名, 版本): 頁數}；打不開、讀不出來的是 -1。"""
    counts = {}
    for name, version in wanted:
        if token is not None and token.cancelled:
            break
        try:
            with fitz.open(os.path.join(folder, name)) as document:
                counts[(name, version)] = document.page_count
        except Exception:  # noqa: BLE001（壞掉的、不見的 PDF：清單只是不寫頁數，打開時再顯示原因）
            counts[(name, version)] = -1
    return counts


def describe_report_failure(exc):
    """報表打不開：(原因, 技術細節)。"""
    if isinstance(exc, fitz.FileDataError):
        reason = _tr("This PDF can't be read. It may be damaged.")
    elif isinstance(exc, (fitz.FileNotFoundError, FileNotFoundError)):
        reason = _tr("This PDF isn't in the reports folder anymore. It may have been moved or deleted.")
    else:
        reason = reason_text(exc)
    return reason, technical_text(exc)


class PDFHandler(QObject):
    """報表頁（ReportsView.qml）。PDF 的開檔、畫頁面、數頁數都在 pdf lane（PyMuPDF 只在一條執行緒用，ENG-07）。

    - reports：reports_dir 裡的 PDF，新的在前（修改時間）。每一份是 {name, title, pages, modified}：
      name 是檔名（open() 用它），title 是去掉 .pdf 的名稱，modified 是修改日期（「Nov 1, 2026」），
      pages 是頁數（0＝還在數，-1＝讀不出來）。列目錄、看修改時間很快，在 GUI 執行緒做；頁數在 pdf lane 數，
      數完再更新同一份清單（新版介面 PLAN 6-6：每一份寫名稱、頁數、日期）。
      state：idle（還沒看過資料夾）、empty（沒有來源，PLAN D7）、ready。
    - 檢視：current（檔名）、currentTitle、page（1 起算）、pageCount、pageSource（頁面圖的 URL）、
      viewState：idle（還沒選）、loading、error（errorDetail、errorTechnical、failureCount）、ready。
    - services 是 None（直接建立、沒有給 services 的呼叫端；ServerHome、AdminHome 都會給）時，用自己的一條 pdf lane（T21）。
    """

    reportsChanged = Signal()
    viewChanged = Signal()

    LIST_KEY = "pdf:list"  # 數頁數的工作；畫頁面的是 pdf:<檔名>（檔名一定以 .pdf 結尾，不會撞名）

    def __init__(self, services=None, reports_dir=None, parent=None):
        super().__init__(parent)
        self._services = services
        self._own_tasks = None
        self.reports_dir = reports_dir or REPORTS_DIR
        self._reports = []
        self._pages = {}  # (檔名, 修改時間, 大小) → 頁數：同一個檔案沒變就不再數一次
        self._state = IDLE
        self._current = ""
        self._page = self._page_count = 0
        self._wanted_page = 1
        self._target_height = 0
        self._source = ""
        self._view = IDLE
        self._detail = self._technical = ""
        self._failures = 0

    @Property(str, notify=reportsChanged)
    def state(self):
        return self._state

    @Property("QVariantList", notify=reportsChanged)
    def reports(self):
        return [{key: report[key] for key in ("name", "title", "pages", "modified")} for report in self._reports]

    @Property(str, notify=viewChanged)
    def current(self):
        return self._current

    @Property(str, notify=viewChanged)
    def currentTitle(self):
        return report_title(self._current)

    @Property(str, notify=viewChanged)
    def currentFileUrl(self):
        return QUrl.fromLocalFile(os.path.join(self.reports_dir, self._current)).toString() if self._current else ""

    @Property(int, notify=viewChanged)
    def page(self):
        return self._page

    @Property(int, notify=viewChanged)
    def pageCount(self):
        return self._page_count

    @Property(str, notify=viewChanged)
    def pageSource(self):
        return self._source

    @Property(str, notify=viewChanged)
    def viewState(self):
        return self._view

    @Property(str, notify=viewChanged)
    def errorDetail(self):
        return self._detail

    @Property(str, notify=viewChanged)
    def errorTechnical(self):
        return self._technical

    @Property(int, notify=viewChanged)
    def failureCount(self):
        return self._failures

    @Slot()
    def refresh(self):
        """重新看一次報表資料夾（列目錄、看修改時間很快，在 GUI 執行緒做；數頁數、開 PDF 才到 pdf lane）。"""
        found = []
        try:
            with os.scandir(self.reports_dir) as entries:
                for entry in entries:
                    if entry.name.lower().endswith(".pdf") and entry.is_file():
                        info = entry.stat()
                        found.append((entry.name, info.st_mtime_ns, info.st_size))
        except OSError:  # 資料夾不存在（現在就是這樣，D7）或讀不到：都當成還沒有報表
            found = []
        found.sort(key=lambda item: (-item[1], item[0]))  # 新的在前；同一個時間依檔名
        self._reports = [{"name": name, "title": report_title(name), "pages": self._pages.get((name, mtime, size), 0),
                          "modified": modified_text(mtime), "version": (mtime, size)}
                         for name, mtime, size in found]
        self._state = READY if found else EMPTY
        self.reportsChanged.emit()
        self._count_pages()
        if self._current and self._current not in self._names():
            self._current, self._page, self._page_count, self._source, self._view = "", 0, 0, "", IDLE
            self.viewChanged.emit()

    @Slot(str, result=bool)
    @Slot(str, int, result=bool)
    def open(self, name, target_height=0):
        """打開 name 的第 1 頁。target_height：顯示區的高度（裝置像素），決定解析度。"""
        if name not in self._names():
            return False
        if name != self._current:
            self._failures = 0
        self._current = name
        self._wanted_page = 1
        if target_height > 0:
            self._target_height = target_height
        self._render()
        return True

    @Slot(result=bool)
    def nextPage(self):
        return self._go(self._page + 1)

    @Slot(result=bool)
    def previousPage(self):
        return self._go(self._page - 1)

    @Slot(result=bool)
    def retry(self):
        if not self._current or self._view != ERROR:
            return False
        self._render()
        return True

    def bind(self, services):
        """之後才拿到 services 時（建立時沒有給）：改用 app 共用的 pdf lane。"""
        self._services = services

    @Slot()
    def forget_pages(self):
        """設定頁的 Clear Download Cache 清掉了頁面圖（T40）：放掉目前的頁面，下次打開報表頁時重新畫第 1 頁。"""
        if not self._current and not self._source:
            return
        self._current, self._page, self._page_count, self._source, self._view = "", 0, 0, "", IDLE
        self._detail = self._technical = ""
        self._failures = 0
        self.viewChanged.emit()

    def _names(self):
        return {report["name"] for report in self._reports}

    def _go(self, page):
        if self._view != READY or not 1 <= page <= self._page_count or page == self._page:
            return False
        self._wanted_page = page
        self._render()
        return True

    def _tasks(self):
        if self._services is not None:
            return self._services.tasks
        if self._own_tasks is None:
            self._own_tasks = TaskRunner(self, lanes={"pdf": 1})
            app = QCoreApplication.instance()
            if app is not None:  # 和 app 共用的 TaskRunner 一樣：結束時取消，不等工作做完（ENG-14）
                app.aboutToQuit.connect(self._own_tasks.shutdown)
        return self._own_tasks

    def _count_pages(self):
        """還不知道頁數的報表，在 pdf lane 一次數完（排在畫頁面之前；只讀 PDF 的目錄，很快）。"""
        wanted = [(report["name"], report["version"]) for report in self._reports if not report["pages"]]
        if not wanted:
            return
        folder = self.reports_dir
        self._tasks().submit("pdf", self.LIST_KEY, lambda token, report: count_pages(folder, wanted, token),
                             on_ok=self._counted, on_err=lambda exc: _log_failure("counting report pages", exc),
                             replace=True)

    def _counted(self, counts):
        """counts：{(檔名, 版本): 頁數}。數的期間清單換過也沒關係：只更新檔名和版本都對得上的那幾份。"""
        self._pages.update({(name, *version): pages for (name, version), pages in counts.items()})
        changed = False
        for report in self._reports:
            pages = counts.get((report["name"], report["version"]))
            if pages is not None and pages != report["pages"]:
                report["pages"] = pages
                changed = True
        if changed:
            self.reportsChanged.emit()

    def _render(self):
        path = os.path.join(self.reports_dir, self._current)
        page, height, out_dir, name = self._wanted_page, self._target_height, reports_cache_dir(), self._current
        self._view = LOADING
        self.viewChanged.emit()
        # 同一份報表只留最新的要求（連按 Next 時，前一頁的畫圖被取消）
        self._tasks().submit("pdf", f"pdf:{name}", lambda token, report: render_page(path, page, height, out_dir),
                             on_ok=lambda result: self._rendered(name, result),
                             on_err=lambda exc: self._render_failed(name, exc), replace=True)

    def _rendered(self, name, result):
        if name != self._current:
            return
        self._page_count, self._page, image = result
        self._source = QUrl.fromLocalFile(image).toString()
        self._view = READY
        self._failures = 0
        self._detail = self._technical = ""
        self.viewChanged.emit()

    def _render_failed(self, name, exc):
        if name != self._current:
            return
        _log_failure(f"opening the report {name}", exc)
        self._detail, self._technical = describe_report_failure(exc)
        self._failures += 1
        self._view = ERROR
        self.viewChanged.emit()
