"""本機儲存（new-feature S5，T40）：下載快取的大小、磁碟的剩餘空間、清除可以重新下載的快取。

設定頁 Help 區塊的 Storage 用 CacheStorage（helpCenter.storage）；Quit 時的 media.clear_cache() 也用這裡的
clear_folders()。三個地方（大小、Quit、Clear Download Cache）看的是同一份資料夾清單：media.download_cache_dirs()。

- 下載快取只放雲端有原檔、隨時能重新下載的東西（影片、圖表、報表頁面圖；之後的帳號照片、報表 PDF）。
- 不能清：錄影（目前目錄的 video/、audio/，briefcase dev 時是家目錄）、還沒存完的錄影（pending_saves 記的檔案）、
  雲端的資料。快取資料夾設定錯了（例如 LOCAL_MEDIA_DIR 指到家目錄，裡面有 video/）時，那個資料夾整個不清，回報失敗。
- 掃描大小、清除都在 io lane：不在 GUI 執行緒遞迴讀檔案。保護清單（QSettings）在 GUI 執行緒讀好再交給工作。
- 正在用快取的工作（下載、畫 PDF 頁面）還在跑時不清：看 TaskRunner 的 key 前綴（CACHE_TASK_PREFIXES）。
"""
import logging
import os
import re
import shutil
import stat
from contextlib import suppress
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, Property, QObject, Signal, Slot

from myapp.notifications import user_message
from myapp.pending_saves import GROUP as PENDING_GROUP, PendingSave
from myapp.ui_prefs import open_settings

logger = logging.getLogger(__name__)

SCAN_KEY = "storage:scan"
CLEAR_KEY = "storage:clear"
# 這些 key 的工作在跑時，快取正在被用：明細頁的下載（media:<sid>:<檔名>）、報表頁畫頁面（pdf:…）、
# 帳號照片的下載、處理、上傳（avatar:…、account:self:photo:…，T36）、報表 PDF 的下載（reports:download，T37；
# 報表的其他 key 是資料庫的查詢，不算）。新的快取下載加進來時，把它的前綴加在這裡
CACHE_TASK_PREFIXES = ("media:", "pdf:", "avatar:", "account:self:photo:", "reports:download")
RECORDING_FOLDERS = ("video", "audio")  # 錄影寫在「目前目錄」底下的這兩個資料夾（record_widget.Recording）

IDLE, LOADING, READY, ERROR = "idle", "loading", "ready", "error"  # CacheStorage.state


def _tr(text, *values):
    """介面上的英文（PLAN D1）：經過 translate，日後可以加翻譯；%1、%2… 一次換成 values。"""
    text = QCoreApplication.translate("LocalStorage", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def size_text(size):
    """「245.3 MB」：1000 進位（和 Finder 一樣），KB 以下不寫小數。0 是「Empty」。"""
    size = max(0, int(size))
    if size == 0:
        return _tr("Empty")
    if size < 1000:
        return _tr("%1 bytes", size)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1000
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "KB" else f"{size:.1f} {unit}"
    return ""  # pragma: no cover  迴圈一定會在 TB 回傳


# ---------------------------------------------------------------- 保護清單（GUI 執行緒讀）


@dataclass(frozen=True)
class Protected:
    """清快取時不能碰的地方（都是 realpath）：資料夾（錄影、家目錄）和檔案（還沒存完的錄影）。"""
    dirs: tuple = ()
    files: frozenset = frozenset()


def recording_dirs():
    """錄影的資料夾：目前目錄的 video/、audio/（絕對路徑）。"""
    return [os.path.abspath(name) for name in RECORDING_FOLDERS]


def pending_files(settings=None):
    """所有帳號還沒存完的錄影的檔案（QSettings 的 pendingSaves 群組）。只讀：看不懂的紀錄略過，不在這裡刪。"""
    settings = settings if settings is not None else open_settings()
    settings.beginGroup(PENDING_GROUP)
    try:
        texts = [settings.value(key, "", type=str) for key in settings.childKeys()]
    finally:
        settings.endGroup()
    files = []
    for text in texts:
        try:
            files.extend(PendingSave.from_json(text).files)
        except ValueError:
            continue
    return files


def protected_locations(settings=None):
    """在 GUI 執行緒呼叫（QSettings 只在 GUI 執行緒讀寫，pending_saves.py）。"""
    home = os.path.expanduser("~")
    dirs = [*recording_dirs(), home]
    return Protected(tuple(os.path.realpath(path) for path in dirs),
                     frozenset(os.path.realpath(path) for path in pending_files(settings)))


def _inside(path, folder):
    """path 是 folder 本身或在 folder 底下（都是 realpath）。"""
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def unsafe_reason(folder, protected):
    """這個快取資料夾不能整個清掉的原因；可以清是空字串。

    資料夾是根目錄、或包含（等於）錄影的資料夾、家目錄時不能清：設定錯了，清下去會刪到錄影。
    """
    real = os.path.realpath(folder)
    if os.path.dirname(real) == real:
        return "the folder is the root of a disk"
    for kept in protected.dirs:
        if _inside(kept, real):
            return "the folder contains recordings or the home folder"
    return ""


def cache_dirs_in_use(busy_keys):
    """busy_keys 裡有沒有正在用快取的工作（CACHE_TASK_PREFIXES）。"""
    return any(key.startswith(CACHE_TASK_PREFIXES) for key in busy_keys)


# ---------------------------------------------------------------- 大小、剩餘空間、清除（任何執行緒都能用）


def folders_size(folders, token=None):
    """這些資料夾裡所有檔案的大小（bytes）。符號連結不跟、不算；同一個檔案（資料夾重疊、硬連結）只算一次。"""
    seen, total = set(), 0
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            if token is not None:
                token.raise_if_cancelled()
            for name in files:
                try:
                    info = os.lstat(os.path.join(root, name))
                except OSError:  # 剛好被刪掉（例如下載的暫存檔改名了）
                    continue
                if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) in seen:
                    continue
                seen.add((info.st_dev, info.st_ino))
                total += info.st_size
    return total


def _existing(path):
    """path 或它最近的、已經存在的上層資料夾（快取資料夾可能還沒建立）。"""
    path = os.path.abspath(path)
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def disk_usage(places):
    """places：{"recordings": 路徑, "cache": 路徑}。同一顆磁碟的合併成一筆。

    回傳 [{"places": ("recordings", "cache"), "free": bytes, "total": bytes}]；讀不到的那個地方略過。
    """
    disks = {}
    for name, path in places.items():
        try:
            existing = _existing(path)
            device = os.stat(existing).st_dev
            if device not in disks:
                usage = shutil.disk_usage(existing)
                disks[device] = {"places": (), "free": usage.free, "total": usage.total}
        except OSError as exc:
            logger.warning("couldn't read the free space for %s: %r", name, exc)
            continue
        disks[device]["places"] += (name,)
    return list(disks.values())


@dataclass(frozen=True)
class ClearResult:
    freed: int        # 清掉多少 bytes（清之前的大小 − 清之後的大小）
    size: int         # 清完之後還剩多少
    failures: int     # 刪不掉的檔案（或整個不清的資料夾）數


def _clear_contents(folder, protected, failures):
    """一個一個刪 folder 底下的東西，略過 protected.files 裡的檔案（設定錯了，還沒存完的錄影在快取資料夾裡）。"""
    for root, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            if os.path.realpath(path) in protected.files:
                continue
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                failures.append((path, exc))
        for name in dirs:
            path = os.path.join(root, name)
            if os.path.islink(path):
                with suppress(FileNotFoundError):
                    os.remove(path)
                continue
            with suppress(OSError):  # 裡面還有留下來的檔案時刪不掉，沒關係
                os.rmdir(path)


def clear_folders(folders, protected=None, token=None):
    """清空每個快取資料夾的內容（資料夾本身留著；不存在的不建立）。回傳刪不掉的 [(路徑, 例外)]。

    protected 沒給時在這裡讀（只能在 GUI 執行緒，例如 Quit）；在 io lane 呼叫時要先在 GUI 執行緒讀好再傳進來。
    """
    protected = protected if protected is not None else protected_locations()
    failures = []
    for folder in dict.fromkeys(os.path.abspath(folder) for folder in folders):  # 去掉重複、保持順序
        if token is not None:
            token.raise_if_cancelled()
        if not os.path.isdir(folder):
            continue
        reason = unsafe_reason(folder, protected)
        if reason:
            logger.error("not clearing the cache folder %s: %s", folder, reason)
            failures.append((folder, RuntimeError(reason)))
            continue
        real = os.path.realpath(folder)
        if any(_inside(path, real) for path in protected.files):
            _clear_contents(folder, protected, failures)
            continue
        shutil.rmtree(folder, onexc=lambda function, path, exc: failures.append((path, exc)))
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            failures.append((folder, exc))
        logger.info("cleared %s", folder)
    for path, exc in failures:
        logger.warning("couldn't clear %s: %r", path, exc)
    return failures


def clear_and_measure(folders, protected, token=None):
    """io lane 的 Clear Download Cache：清之前量一次、清、清完再量一次。"""
    before = folders_size(folders, token)
    failures = clear_folders(folders, protected, token)
    after = folders_size(folders)
    return ClearResult(max(0, before - after), after, len(failures))


def scan(folders, places, token=None):
    """io lane 的掃描：(快取大小, disk_usage(places))。"""
    return folders_size(folders, token), disk_usage(places)


# ---------------------------------------------------------------- 設定頁的 Storage（helpCenter.storage）


class CacheStorage(QObject):
    """設定頁 Help 區塊的 Storage（SettingsHelp.qml）。

    - refresh()：在 io lane 算快取大小和剩餘空間。state：idle（還沒算）、loading（算或清）、ready、error。
      cacheText「245.3 MB」（重新算的期間先顯示上一次的數字）；disks：[{label, text}]，例如
      {"Free space", "120.3 GB free of 494.4 GB"}；錄影和快取在不同的磁碟時是兩筆。
    - clear()：Clear Download Cache（QML 先跳確認）。清之前在 GUI 執行緒讀保護清單，清除和重新量大小在 io lane。
      成功跳一則 toast（resultText 是空的）；有檔案刪不掉、或整個失敗時 resultText 寫原因（resultFailed 是 true），
      大小照樣重新量。
    - canClear／blockedReason：正在用快取（明細頁的下載、報表頁畫頁面，CACHE_TASK_PREFIXES 的工作）時不能清，
      blockedReason 說為什麼；快取是空的、還沒算過大小、正在清的時候也不能清（不重複送出）。
      正在重新算大小時可以清：清除會取消那次掃描，自己量清完的大小。
    - cleared：清完之後發出（不論有沒有失敗）：有畫面拿著快取裡的檔案時（報表頁的頁面圖），接這個 signal 忘掉它。
    - services 是 None（直接建立 ServerHome 的測試）時什麼都不做。
    """

    changed = Signal()
    cleared = Signal()

    def __init__(self, services, folders, places=None, parent=None):
        """folders()：快取資料夾的清單（media.download_cache_dirs）；places()：{名稱: 路徑}，要看剩餘空間的地方。"""
        super().__init__(parent)
        self._services = services
        self._folders = folders
        self._places = places or (lambda: {"recordings": recording_dirs()[0],
                                           "cache": (list(folders()) or [os.getcwd()])[0]})
        self._size = 0
        self._disks = []
        self._measured = False  # 算過一次大小了：重新算的期間先顯示舊的數字，不閃「Calculating…」
        self._scanning = False
        self._clearing = False
        self._error = False
        self._result = ""
        self._result_failed = False
        self._busy_keys = set()  # 正在跑的、用到快取的工作
        if services is not None:
            services.tasks.busyChanged.connect(self._on_busy)

    # ------------------------------------------------------------ QML 讀的屬性

    @Property(str, notify=changed)
    def state(self):
        if self._scanning or self._clearing:
            return LOADING
        if self._error:
            return ERROR
        return READY if self._measured else IDLE

    @Property(float, notify=changed)
    def cacheBytes(self):
        return float(self._size)

    @Property(str, notify=changed)
    def cacheText(self):
        if self._error and not self._clearing:
            return _tr("Unavailable")
        if self._measured:
            return size_text(self._size)
        return _tr("Calculating…") if self._scanning else ""

    @Property("QVariantList", notify=changed)
    def disks(self):
        return [{"label": self._disk_label(disk["places"]),
                 "text": _tr("%1 free of %2", size_text(disk["free"]), size_text(disk["total"]))}
                for disk in self._disks]

    @Property(bool, notify=changed)
    def scanning(self):
        return self._scanning

    @Property(bool, notify=changed)
    def clearing(self):
        return self._clearing

    @Property(bool, notify=changed)
    def inUse(self):
        """明細頁的下載、報表頁畫頁面等用到快取的工作還在跑。"""
        return bool(self._busy_keys)

    @Property(str, notify=changed)
    def blockedReason(self):
        """不能清的原因（給人看的）；可以清、或還沒算好、正在清的時候是空字串。"""
        if self._clearing or not self._measured or self._error:
            return ""
        if self._busy_keys:
            return _tr("A video, chart, report or photo is downloading or opening. Try again when it's done.")
        if self._size == 0:
            return _tr("The download cache is empty.")
        return ""

    @Property(bool, notify=changed)
    def canClear(self):
        return (self._services is not None and self._measured and not self._error and not self._clearing
                and not self._busy_keys and self._size > 0)

    @Property(str, notify=changed)
    def resultText(self):
        return self._result

    @Property(bool, notify=changed)
    def resultFailed(self):
        return self._result_failed

    # ------------------------------------------------------------ QML 呼叫

    @Slot(result=bool)
    def refresh(self):
        """重新算大小和剩餘空間。回傳 False：沒有 services，或正在算、正在清。"""
        if self._services is None or self._clearing or self._scanning:
            return False
        folders, places = list(self._folders()), self._places()
        # replace：清除時取消的那次掃描可能還沒真的停下來，換成這一次
        submitted = self._services.tasks.submit("io", SCAN_KEY, lambda token, report: scan(folders, places, token),
                                                on_ok=self._scanned, on_err=self._scan_failed, replace=True)
        if submitted:
            self._scanning = True
            self.changed.emit()
        return submitted

    @Slot(result=bool)
    def clear(self):
        """Clear Download Cache（確認之後）。回傳 False：現在不能清（見 canClear）。"""
        if not self.canClear:
            return False
        folders, protected = list(self._folders()), protected_locations()
        submitted = self._services.tasks.submit(
            "io", CLEAR_KEY, lambda token, report: clear_and_measure(folders, protected, token),
            on_ok=self._cleared, on_err=self._clear_failed)
        if not submitted:
            return False
        if self._scanning:  # 清完會自己量；正在算的那次不要了
            self._services.tasks.cancel(SCAN_KEY)
            self._scanning = False
        self._clearing = True
        self._result, self._result_failed = "", False
        self.changed.emit()
        return True

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    @staticmethod
    def _disk_label(places):
        if len(places) > 1:
            return _tr("Free space")
        return _tr("Free space for recordings") if places[0] == "recordings" else _tr("Free space for the cache")

    @Slot(str, bool)
    def _on_busy(self, key, busy):
        if not key.startswith(CACHE_TASK_PREFIXES):
            return
        before = bool(self._busy_keys)
        if busy:
            self._busy_keys.add(key)
        else:
            self._busy_keys.discard(key)
        if bool(self._busy_keys) != before:
            self.changed.emit()

    def _scanned(self, result):
        self._size, self._disks = result
        self._scanning = False
        self._measured = True
        self._error = False
        self.changed.emit()

    def _scan_failed(self, exc):
        logger.warning("couldn't measure the download cache: %r", exc)
        self._scanning = False
        self._error = True
        self.changed.emit()

    def _cleared(self, result):
        self._clearing = False
        self._size = result.size
        self._measured = True
        self._error = False
        if result.failures:
            self._result_failed = True
            self._result = _tr("Some files couldn't be removed. Close any open videos or reports, then try again.")
        else:  # 成功只跳 toast；畫面上的大小已經變成清完的
            self._result_failed = False
            self._result = ""
            freed = _tr("Freed %1. ", size_text(result.freed)) if result.freed else ""
            self._services.notifier.notify("success", _tr("Download cache cleared"),
                                           freed + _tr("Videos, charts and reports download again when you open them."))
        self.changed.emit()
        self.cleared.emit()
        self.refresh()  # 剩餘空間也變了

    def _clear_failed(self, exc):
        logger.warning("clearing the download cache failed: %r", exc)
        self._clearing = False
        self._result_failed = True
        _, title, detail, _ = user_message(exc)
        self._result = f"{title}. {detail}" if detail else title
        self.changed.emit()
        self.cleared.emit()  # 可能清掉了一部分
        self.refresh()
