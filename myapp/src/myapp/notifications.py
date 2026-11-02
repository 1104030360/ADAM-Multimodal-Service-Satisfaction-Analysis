"""通知：例外轉成使用者看得懂的訊息（ENG-12），以及 toast 的佇列（ENG-15、DS-21）。

- `user_message(exc)`：例外 → (severity, title, detail, retryable)，英文字串（PLAN D1）。技術代碼不放進文字。
- `Notifier`：toast 的模型與規則。Python 只管佇列；計時在 QML（滑鼠停在上面、焦點在裡面、
  視窗不在前景時暫停），所以這裡沒有計時器。context property 名稱是 `notifier`。

模型的列是「新的在前面」（第 0 列最新）：ToastHost 的 ListView 用 `ListView.BottomToTop`，
第 0 列在最下面，所以新的在最下面、舊的往上推（DS-21）。
"""
import errno
import logging
import threading
import time
from concurrent.futures import CancelledError
from dataclasses import dataclass, fields
from functools import partial
from itertools import count

import boto3.exceptions
import botocore.exceptions
import mysql.connector
import requests
import s3transfer.exceptions
from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, Signal, Slot

from myapp.tasks import Cancelled

try:  # C 擴充有時直接丟出自己的例外，不是 mysql.connector.Error
    from _mysql_connector import MySQLInterfaceError
except ImportError:  # pragma: no cover
    MySQLInterfaceError = ()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 例外 → 訊息（ENG-12）


@dataclass(frozen=True)
class _Message:
    severity: str
    title: str
    detail: str
    retryable: bool
    key: str = ""  # 同一類的問題共用一則 toast（例如每個工作都連不上資料庫）


SILENT = _Message("", "", "", False)
DB_OFFLINE = _Message("error", "Can't reach the database", "Check your network and try again.", True, "db-offline")
DB_ERROR = _Message("error", "Database request failed", "If this keeps happening, contact your administrator.", False)
S3_OFFLINE = _Message("error", "Can't reach cloud storage", "Check your network and try again.", True, "s3-offline")
S3_ACCESS = _Message("error", "Can't access cloud storage",
                     "The storage credentials are missing, expired, or don't have permission. "
                     "Contact your administrator.", False)
S3_NOT_FOUND = _Message("warning", "File not found in cloud storage", "It may have been moved or deleted.", False)
ANALYSIS_OFFLINE = _Message("error", "Can't reach the analysis service", "Check your network and try again.", True)
ANALYSIS_TIMEOUT = _Message("error", "The analysis service didn't respond", "It may be busy. Try again in a moment.",
                            True)
ANALYSIS_HTTP = _Message("error", "The analysis service returned an error",
                         "Try again in a moment. If it keeps happening, contact your administrator.", True)
FILE_NOT_FOUND = _Message("error", "File not found on this computer", "It may have been moved or deleted.", False)
NO_PERMISSION = _Message("error", "Can't access a file on this computer",
                         "ADAM doesn't have permission to open or save it.", False)
DISK_FULL = _Message("error", "Not enough disk space", "Free up space on this computer, then try again.", False)
UNEXPECTED = _Message("error", "Something went wrong",
                      "An unexpected error occurred and was logged. If it keeps happening, contact your administrator.",
                      False)

# 2002 socket、2003 連不上、2005 找不到主機、2006 伺服器不見了、2013 查詢中斷線、2055 連線中斷、4031 閒置被切斷
DB_OFFLINE_ERRNOS = {2002, 2003, 2005, 2006, 2013, 2055, 4031}
S3_ACCESS_CODES = {"AccessDenied", "AllAccessDisabled", "ExpiredToken", "ExpiredTokenException", "Forbidden",
                   "InvalidAccessKeyId", "InvalidToken", "SignatureDoesNotMatch", "403"}
S3_NOT_FOUND_CODES = {"NoSuchKey", "NotFound", "404"}  # NoSuchBucket 是設定錯誤，不算「找不到檔案」
S3_CREDENTIAL_ERRORS = (botocore.exceptions.NoCredentialsError, botocore.exceptions.PartialCredentialsError,
                        botocore.exceptions.CredentialRetrievalError)
S3_RETRIES_EXCEEDED = (boto3.exceptions.RetriesExceededError, s3transfer.exceptions.RetriesExceededError)


def user_message(exc):
    """(severity, title, detail, retryable)：照 ENG-12 分類，英文字串照 ENGLISH_COPY.md 的規則。

    Cancelled 回傳 ("", "", "", False)，表示不顯示。retryable 表示 toast 上放「Retry」有沒有意義。
    """
    message = _classify(exc)
    return message.severity, message.title, message.detail, message.retryable


def _classify(exc):
    if isinstance(exc, (Cancelled, CancelledError)):
        return SILENT
    # S3 下載時串流中斷（socket 逾時、讀到一半斷線）會重試幾次，都失敗才丟這個：原因一定是網路
    if isinstance(exc, S3_RETRIES_EXCEEDED):
        return S3_OFFLINE
    if isinstance(exc, boto3.exceptions.S3UploadFailedError):  # boto3 的 upload_file 把真正的原因包起來了
        inner = exc.__cause__ or exc.__context__
        return _classify(inner) if inner is not None else UNEXPECTED
    if isinstance(exc, (mysql.connector.Error, MySQLInterfaceError)):
        return DB_OFFLINE if _is_db_offline(exc) else DB_ERROR
    if isinstance(exc, botocore.exceptions.ClientError):
        return _classify_client_error(exc)
    if isinstance(exc, S3_CREDENTIAL_ERRORS):
        return S3_ACCESS
    if isinstance(exc, (botocore.exceptions.ConnectionError, botocore.exceptions.HTTPClientError)):
        return S3_OFFLINE
    # requests 的例外是 OSError 的子類別，要在 OSError 之前判斷
    if isinstance(exc, requests.ConnectionError):  # 包含 ConnectTimeout
        return ANALYSIS_OFFLINE
    if isinstance(exc, requests.Timeout):  # ReadTimeout
        return ANALYSIS_TIMEOUT
    if isinstance(exc, requests.HTTPError):
        return ANALYSIS_HTTP
    if isinstance(exc, FileNotFoundError):
        return FILE_NOT_FOUND
    if isinstance(exc, PermissionError):
        return NO_PERMISSION
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return DISK_FULL
    return UNEXPECTED


def _is_db_offline(exc):
    error_number = getattr(exc, "errno", None)
    if error_number in DB_OFFLINE_ERRNOS or (getattr(exc, "sqlstate", None) or "").startswith("08"):
        return True
    # 「MySQL Connection not available.」這類用戶端自己發現的斷線沒有 errno（-1）
    return error_number == -1 and isinstance(exc, (mysql.connector.InterfaceError, mysql.connector.OperationalError))


def _classify_client_error(exc):
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if status == 403 or code in S3_ACCESS_CODES:
        return S3_ACCESS
    if code in S3_NOT_FOUND_CODES:
        return S3_NOT_FOUND
    return UNEXPECTED


# ---------------------------------------------------------------- toast 的模型與規則（DS-21）

SEVERITIES = ("success", "info", "warning", "error", "progress")
DEFAULT_TIMEOUT_MS = {"success": 4000, "info": 5000, "warning": 8000, "error": 0, "progress": 0}  # 0＝常駐
ACTION_MIN_TIMEOUT_MS = 8000  # 有動作按鈕的，非常駐時至少停留這麼久
MAX_VISIBLE = 3
DUPLICATE_WINDOW_S = 2.0  # 這段時間內標題相同、沒有 key 的通知直接忽略
RETRY_LABEL = "Retry"


@dataclass(eq=False)  # 用身分比較：模型用 index() 找列
class _Toast:
    toastId: int
    severity: str
    title: str
    detail: str
    actionLabel: str
    actionId: str
    timeoutMs: int
    done: float = 0.0
    total: float = 0.0
    count: int = 1
    revision: int = 0
    key: str = ""  # 不是模型的角色


ROLES = tuple(f.name for f in fields(_Toast) if f.name != "key")
_FIRST_ROLE = int(Qt.ItemDataRole.UserRole)


class ToastModel(QAbstractListModel):
    """ToastHost 的 ListView 用的模型；只由 Notifier 修改。第 0 列是最新的一則。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def roleNames(self):
        return {_FIRST_ROLE + i: name.encode() for i, name in enumerate(ROLES)}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        offset = int(role) - _FIRST_ROLE
        if not index.isValid() or not 0 <= index.row() < len(self.rows) or not 0 <= offset < len(ROLES):
            return None
        return getattr(self.rows[index.row()], ROLES[offset])

    def find(self, toast_id=None, key=None):
        for toast in self.rows:
            if toast.toastId == toast_id or (key and toast.key == key):
                return toast
        return None

    def insert_front(self, toast):
        self.beginInsertRows(QModelIndex(), 0, 0)
        self.rows.insert(0, toast)
        self.endInsertRows()

    def remove(self, toast):
        row = self.rows.index(toast)
        self.beginRemoveRows(QModelIndex(), row, row)
        del self.rows[row]
        self.endRemoveRows()

    def changed(self, toast, names=()):
        """names 空的表示全部角色都可能變了。"""
        if toast in self.rows:
            index = self.index(self.rows.index(toast))
            self.dataChanged.emit(index, index, [_FIRST_ROLE + ROLES.index(name) for name in names])

    def clear(self):
        self.beginResetModel()
        self.rows = []
        self.endResetModel()


class Notifier(QObject):
    """toast 的佇列與規則（DS-21）。任何執行緒都能呼叫；不是 GUI 執行緒時，用 queued signal 轉進來再處理。

    - 最多同時顯示 3 則。第 4 則進來時，最舊的非常駐通知退場；3 則都常駐時，新的排隊（overflowCount）。
    - 同一個 key 就地更新：count、revision 各加 1（QML 依 revision 重新計時）。
      進度（progress）變成結果時 count 不加，因為是同一件事；progress() 更新數字時兩個都不加。
    - 2 秒內標題相同、而且沒有 key 的通知直接忽略（key 不同的是不同的事，各自保留自己的動作按鈕）。
    - 從背景執行緒呼叫 notify() 時，回傳的 id 是先配好的；如果合併到同 key 的通知或被忽略，這個 id 不會出現在模型裡。
    """

    actionTriggered = Signal(str, int)  # actionId, toastId
    overflowCountChanged = Signal()
    _posted = Signal(object)  # 背景執行緒的呼叫，排進 GUI 執行緒再執行

    def __init__(self, parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self._model = ToastModel(self)
        self._queue = []  # 排隊中、還沒顯示的通知，先進先出
        self._recent = {}  # 標題 → (時間, toastId)：2 秒內同標題的通知直接忽略
        self._ids = count(1)
        self._ids_lock = threading.Lock()
        self._clock = clock
        self._gui_thread = threading.get_ident()
        self._posted.connect(self._run_posted, Qt.ConnectionType.QueuedConnection)

    @Property(QObject, constant=True)
    def model(self):
        return self._model

    @Property(int, notify=overflowCountChanged)
    def overflowCount(self):
        return len(self._queue)

    @Slot(str, str, result=int)
    @Slot(str, str, str, result=int)
    @Slot(str, str, str, str, result=int)
    @Slot(str, str, str, str, str, result=int)
    @Slot(str, str, str, str, str, str, result=int)
    @Slot(str, str, str, str, str, str, int, result=int)
    def notify(self, severity, title, detail="", key="", actionId="", actionLabel="", timeoutMs=-1):
        """新增一則通知，回傳 toastId。timeoutMs < 0 用預設值（DS-21），0 表示常駐。"""
        if severity not in DEFAULT_TIMEOUT_MS:
            raise ValueError(f"unknown severity {severity!r}; use one of {SEVERITIES}")
        timeout = DEFAULT_TIMEOUT_MS[severity] if timeoutMs < 0 else timeoutMs
        if actionId and timeout > 0:
            timeout = max(timeout, ACTION_MIN_TIMEOUT_MS)
        toast = _Toast(self._next_id(), severity, title, detail, actionLabel, actionId, timeout, key=key)
        if self._on_gui_thread():
            return self._add(toast)
        self._posted.emit(partial(self._add, toast))
        return toast.toastId

    @Slot(str, float, float)
    @Slot(str, float, float, str)
    @Slot(str, float, float, str, str)
    def progress(self, key, done, total, title="", detail=""):
        """建立或就地更新 key 這則進度通知（severity="progress"，常駐）。title、detail 空字串表示不變。"""
        if not key:
            raise ValueError("progress() needs a key")
        if self._on_gui_thread():
            self._progress_now(key, done, total, title, detail)
        else:
            self._posted.emit(partial(self._progress_now, key, done, total, title, detail))

    @Slot(int)
    def dismiss(self, toastId):
        if not self._on_gui_thread():
            self._posted.emit(partial(self.dismiss, toastId))
            return
        toast = self._model.find(toast_id=toastId)
        if toast is not None:
            self._model.remove(toast)
            self._promote()
            return
        for queued in self._queue:
            if queued.toastId == toastId:
                self._queue.remove(queued)
                self.overflowCountChanged.emit()
                return

    @Slot(int)
    def trigger(self, toastId):
        """按下 toast 上的動作按鈕：關掉這則，然後發 actionTriggered(actionId, toastId)。

        先關再發：接收端可能馬上用同一個 key 發新的通知（例如按 Retry 之後的上傳進度），不能被這裡關掉。
        """
        if not self._on_gui_thread():
            self._posted.emit(partial(self.trigger, toastId))
            return
        toast = self._model.find(toast_id=toastId) or next((t for t in self._queue if t.toastId == toastId), None)
        if toast is None:
            return
        self.dismiss(toastId)
        if toast.actionId:
            self.actionTriggered.emit(toast.actionId, toastId)

    def fromException(self, exc, key="", actionId="", actionLabel=""):
        """依 ENG-12 把例外變成一則通知，回傳 toastId；Cancelled 不顯示，回傳 0。

        key 沒給時用這一類的 key（例如 db-offline），讓同類的問題只留一則。動作按鈕只在 retryable 時放上去；
        actionLabel 沒給時是 "Retry"。完整的例外寫進 log（未預期的連 traceback）。
        """
        message = _classify(exc)
        if not message.severity:
            return 0
        if message is UNEXPECTED:
            logger.error("unexpected error: %r", exc, exc_info=exc)
        else:
            logger.warning("%s: %r", message.title, exc)
        if not message.retryable:
            actionId = actionLabel = ""
        elif actionId and not actionLabel:
            actionLabel = RETRY_LABEL
        return self.notify(message.severity, message.title, message.detail, key or message.key, actionId, actionLabel)

    @Slot()
    def clear(self):
        if not self._on_gui_thread():
            self._posted.emit(self.clear)
            return
        had_queue = bool(self._queue)
        self._queue = []
        self._recent = {}
        self._model.clear()
        if had_queue:
            self.overflowCountChanged.emit()

    # ------------------------------------------------------------ 以下都在 GUI 執行緒執行

    def _on_gui_thread(self):
        return threading.get_ident() == self._gui_thread

    def _next_id(self):
        with self._ids_lock:
            return next(self._ids)

    @staticmethod
    def _run_posted(call):
        call()

    def _find_key(self, key):
        return self._model.find(key=key) or next((t for t in self._queue if t.key == key), None)

    def _add(self, toast):
        now = self._clock()
        self._recent = {title: seen for title, seen in self._recent.items() if now - seen[0] < DUPLICATE_WINDOW_S}
        existing = self._find_key(toast.key) if toast.key else None
        if existing is not None:
            if existing.severity != "progress":
                existing.count += 1
            existing.revision += 1
            # notify() 決定整則的內容（進度的數字也清掉）；身分、count、revision 留著
            for name in ("severity", "title", "detail", "actionLabel", "actionId", "timeoutMs", "done", "total"):
                setattr(existing, name, getattr(toast, name))
            self._model.changed(existing)
            self._recent[toast.title] = (now, existing.toastId)
            return existing.toastId
        seen = self._recent.get(toast.title)
        if seen is not None and not toast.key:
            return seen[1]
        self._recent[toast.title] = (now, toast.toastId)
        self._show(toast)
        return toast.toastId

    def _progress_now(self, key, done, total, title, detail):
        toast = self._find_key(key)
        if toast is None:
            self._show(_Toast(self._next_id(), "progress", title, detail, "", "", 0, done, total, key=key))
            return
        converted = toast.severity != "progress"
        if converted:  # 例如失敗之後按 Retry：同一則變回進度
            toast.severity, toast.timeoutMs, toast.actionId, toast.actionLabel = "progress", 0, "", ""
            toast.revision += 1
        names = ["done", "total"]
        toast.done, toast.total = done, total
        if title and title != toast.title:
            toast.title = title
            names.append("title")
        if detail and detail != toast.detail:
            toast.detail = detail
            names.append("detail")
        self._model.changed(toast, () if converted else names)  # 轉換時很多角色都變了

    def _show(self, toast):
        rows = self._model.rows
        if len(rows) >= MAX_VISIBLE:
            transient = [t for t in rows if t.timeoutMs > 0]
            if not transient:
                self._queue.append(toast)
                self.overflowCountChanged.emit()
                return
            self._model.remove(transient[-1])  # 最舊的非常駐通知退場（第 0 列最新）
        self._model.insert_front(toast)

    def _promote(self):
        promoted = False
        while self._queue and len(self._model.rows) < MAX_VISIBLE:
            self._model.insert_front(self._queue.pop(0))
            promoted = True
        if promoted:
            self.overflowCountChanged.emit()
