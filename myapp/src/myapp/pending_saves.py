"""還沒存完的錄影（T26）：重開 app 之後，服務人員還能按 Retry 把它存好。

- 存在 QSettings("ADAM", "myapp")，和 UiPrefs 的「減少動態」同一個地方（ui_prefs.open_settings()；
  macOS 是 ~/Library/Preferences 的 plist，測試是暫存資料夾裡的 INI）。
- 一段錄影一個 key：pendingSaves/<service_id>，值是一段 JSON（版本 1）：帳號、三個檔案的絕對路徑、開始和結束時間、
  存到哪一步（stage），以及上傳完之後的三個 s3:// URL。
  - stage "upload"：還沒上傳完，Retry 要重傳三個檔案。
  - stage "database"：檔案都上傳了（有 urls），Retry 只寫資料庫。
- RecordingSaver 開始存檔、上傳完、失敗時寫入，存好就刪掉；launch() 時讀回這個帳號的（RecordingSaver.restore()）。
- 只在 GUI 執行緒讀寫：本機的一個小檔案，不是網路；寫完馬上 sync()，app 當掉也還在。
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QSettings

from myapp.ui_prefs import open_settings

logger = logging.getLogger(__name__)

GROUP = "pendingSaves"
VERSION = 1
STAGES = ("upload", "database")  # 和 RecordingSaver._failed 的 stage 一樣
FILE_COUNT = 3  # 顧客鏡頭、員工鏡頭、麥克風


@dataclass(frozen=True)
class PendingSave:
    """一段還沒存完的錄影。時間是本機時間（和資料庫的 DATETIME 一樣，不帶時區）。"""

    service_id: str
    account_id: str
    files: tuple            # 三個檔案的絕對路徑：cam0、cam1、audio
    start_time: datetime
    end_time: datetime      # 可以是 None（資料表的 service_end 允許 NULL）
    stage: str              # STAGES
    urls: tuple = None      # stage "database" 才有：三個 s3:// URL，和 files 同順序

    def to_json(self):
        return json.dumps({
            "version": VERSION, "service_id": self.service_id, "account_id": self.account_id,
            "files": list(self.files), "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time is not None else None,
            "stage": self.stage, "urls": list(self.urls) if self.urls is not None else None,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, text):
        """看不懂（JSON 壞了、版本不對、欄位少了或型別不對）就丟 ValueError。"""
        try:
            data = json.loads(text)
            if data["version"] != VERSION:
                raise ValueError(f"version {data['version']!r}")
            stage, files, urls, end = data["stage"], data["files"], data["urls"], data["end_time"]
            if stage not in STAGES or not _paths(files) or not (_paths(urls) if stage == "database" else urls is None):
                raise ValueError(f"stage {stage!r} with files {files!r} and urls {urls!r}")
            if not isinstance(data["service_id"], str) or not isinstance(data["account_id"], str):
                raise ValueError("service_id and account_id must be strings")
            return cls(data["service_id"], data["account_id"], tuple(files), datetime.fromisoformat(data["start_time"]),
                       None if end is None else datetime.fromisoformat(end), stage,
                       None if urls is None else tuple(urls))
        except (KeyError, TypeError) as error:  # 不是 dict、少了欄位、時間不是字串
            raise ValueError(f"not a pending save: {error!r}") from error


def _paths(value):
    return isinstance(value, list) and len(value) == FILE_COUNT and all(isinstance(item, str) for item in value)


class PendingSaveStore:
    """pendingSaves 群組的讀寫。settings 沒給時用 ui_prefs.open_settings()。"""

    def __init__(self, settings=None):
        self._settings = settings if settings is not None else open_settings()

    def put(self, record):
        """新增或覆寫這一段的紀錄。寫不進去只記 log：存檔照常進行，只是重開 app 之後就沒有 Retry 了。"""
        self._settings.setValue(f"{GROUP}/{record.service_id}", record.to_json())
        self._sync(f"remember {record.service_id}")

    def remove(self, service_id):
        self._settings.remove(f"{GROUP}/{service_id}")
        self._sync(f"forget {service_id}")

    def load(self, account_id):
        """這個帳號還沒存完的錄影，依 service_id（也就是開始的時間）排序。

        別的帳號的留著，等那個人登入；看不懂的紀錄刪掉（log 記一筆），不然每次登入都會再遇到一次。
        """
        settings = self._settings
        settings.beginGroup(GROUP)
        try:
            stored = {key: settings.value(key, "", type=str) for key in settings.childKeys()}
        finally:
            settings.endGroup()
        records = []
        for key, text in sorted(stored.items()):
            try:
                record = PendingSave.from_json(text)
                if record.service_id != key:
                    raise ValueError(f"the record is for {record.service_id!r}")
            except ValueError as error:
                logger.warning("dropping the unreadable pending save %s: %s", key, error)
                self.remove(key)
                continue
            if record.account_id == account_id:
                records.append(record)
        return records

    def _sync(self, what):
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            logger.warning("couldn't %s in %s: %s", what, self._settings.fileName(), self._settings.status())
