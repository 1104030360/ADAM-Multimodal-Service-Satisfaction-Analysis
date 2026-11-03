"""設定頁 Help 區塊（new-feature S3、S5，T40）：About、Check Connection、Copy Diagnostic Summary，以及 Storage。

context property 是 helpCenter（HelpCenter），兩端各一份（launch() 註冊）：
- helpCenter.about：版本與基本環境（Briefcase 的 metadata、Python、Qt、macOS）。缺的欄位寫「Unavailable」，不猜。
- helpCenter.connection（ConnectionCheck）：三項唯讀、已經有權限的探測，各自在背景 lane、各自有 timeout：
  - Database：db lane 上的 SELECT 1（共用的 DbLane；連線 5 秒、讀取 10 秒逾時）。
  - Cloud Storage：io lane 對一個不存在的物件 GetObject（只拿 1 byte）。桌面端的 IAM 只有 video/、audio/、charts/ 的
    Get/Put，沒有 ListBucket，所以不用 head_bucket（會是 403）。回應是 NoSuchKey 或 AccessDenied 都表示：連得到、
    憑證被接受、bucket 存在；憑證錯、過期、bucket 不存在、連不到則各有代碼。不重試，連線 3 秒、讀取 5 秒逾時。
  - Analysis Service：io lane 的 GET /healthz（兩個分析服務都有），網址從 ANALYZE_API_URL 換掉最後一段。
    絕不送 /analyze 當健康檢查。逾時 (3, 5) 秒。
  一項成功只代表那一項回應了，不代表錄影、上傳、分析整條都沒問題（畫面上的說明也這樣寫）。
  檢查失敗不影響 app 的其他功能：不跳 toast、不停用任何東西，結果只寫在這一區。
- helpCenter.copySummary()：只把白名單的欄位放進剪貼簿（版本、環境、檢查時間、各項結果、整理過的代碼）。
  不放原始的例外訊息、log、帳密、主機、網址、bucket、連線字串、帳號或顧客的資料；也不傳到任何地方。
- helpCenter.storage：local_storage.CacheStorage（S5）。
"""
import importlib.metadata
import logging
import os
import platform
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import boto3
import botocore.exceptions
import mysql.connector
import PySide6
import requests
from botocore.config import Config
from PySide6.QtCore import QCoreApplication, Property, QObject, Signal, Slot, qVersion
from PySide6.QtGui import QGuiApplication

from myapp import local_storage, media

try:  # C 擴充有時直接丟出自己的例外，不是 mysql.connector.Error
    from _mysql_connector import MySQLInterfaceError
except ImportError:  # pragma: no cover
    MySQLInterfaceError = ()

logger = logging.getLogger(__name__)

APP_DISTRIBUTION = "myapp"  # briefcase dev 產生的 src/myapp.dist-info（main.py 也讀它）

# 檢查項目：(名稱, lane)。名稱也是 TaskRunner 的 key 的後半段（help:check:<名稱>）
CHECKS = (("database", "db"), ("files", "io"), ("analysis", "io"))
KEY_PREFIX = "help:check:"
NOT_CHECKED, CHECKING, AVAILABLE, FAILED = "notChecked", "checking", "available", "failed"

S3_CONNECT_TIMEOUT = 3     # 秒；測試可以改短
S3_READ_TIMEOUT = 5
HTTP_TIMEOUT = (3, 5)      # (連線, 讀取) 秒
# GetObject 的對象：一定不存在的物件，放在桌面端可以讀的 charts/ 底下。萬一真的有，也只拿第 1 個 byte
S3_PROBE_KEY = "charts/adam-connection-check"

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # DS-51 的時間格式
CODE = re.compile(r"^[A-Z0-9_]{1,40}$")
SAFE_VALUE = re.compile(r"[^A-Za-z0-9 ._()+\-]")  # 摘要裡的版本字串只留這些字元

# 摘要用固定的英文（給支援人員看，不跟著翻譯走）
SUMMARY_NAMES = {"database": "Database", "files": "Cloud Storage", "analysis": "Analysis Service"}
SUMMARY_STATES = {NOT_CHECKED: "Not checked", CHECKING: "Checking", AVAILABLE: "Available", FAILED: "Failed"}

S3_CREDENTIAL_CODES = {"InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken", "ExpiredTokenException",
                       "InvalidToken", "TokenRefreshRequired", "InvalidClientTokenId", "UnrecognizedClientException"}
S3_REACHED_CODES = {"NoSuchKey", "404", "AccessDenied"}  # 不存在的物件會得到這幾個：連得到、憑證被接受
DB_UNREACHABLE_ERRNOS = {2002, 2003, 2005}
DB_LOST_ERRNOS = {2006, 2013, 2055, 4031}


def _tr(text, *values):
    """介面上的英文（PLAN D1）：經過 translate，日後可以加翻譯；%1、%2… 一次換成 values。"""
    text = QCoreApplication.translate("Diagnostics", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def _unavailable():
    return _tr("Unavailable")


# ---------------------------------------------------------------- About


def app_metadata(name=APP_DISTRIBUTION):
    """Briefcase 的 metadata（src/myapp.dist-info）；沒有時（還沒跑過 briefcase dev、測試）是空的 dict。"""
    try:
        return dict(importlib.metadata.metadata(name) or {})
    except Exception as exc:  # PackageNotFoundError，或 metadata 檔壞掉
        logger.info("no app metadata for %s: %r", name, exc)
        return {}


def _system():
    release = platform.mac_ver()[0]
    if release:
        return f"macOS {release}"
    name, version = platform.system(), platform.release()
    return f"{name} {version}".strip()


def about_info(metadata=None):
    """About 的欄位，值都是字串；讀不到的寫「Unavailable」。metadata 沒給時讀 app 的。"""
    metadata = app_metadata() if metadata is None else metadata
    unavailable = _unavailable()

    def safe(read):
        try:
            return str(read() or "").strip() or unavailable
        except Exception:  # 版本資訊讀不到不該讓設定頁打不開
            return unavailable

    return {
        "appName": safe(lambda: metadata.get("Formal-Name")),
        "version": safe(lambda: metadata.get("Version")),
        "python": safe(platform.python_version),
        "qt": safe(qVersion),
        "pyside": safe(lambda: PySide6.__version__),
        "system": safe(_system),
        "architecture": safe(platform.machine),
    }


# ---------------------------------------------------------------- 探測（在背景 lane 執行）


@dataclass(frozen=True)
class Outcome:
    """一項檢查的結果。code 是固定的代碼（摘要只放它）；technical 是安全的技術細節（不含主機、網址、訊息）。"""
    ok: bool
    code: str
    technical: str = ""


def health_url(analyze_url):
    """ANALYZE_API_URL（…/analyze）→ 同一台的 …/healthz。沒設定或不是 http(s) 網址時是空字串。"""
    if not analyze_url:
        return ""
    parts = urlsplit(analyze_url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    path = path[: path.rfind("/")] if "/" in path else ""
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/healthz", "", ""))


def check_database(db):
    """db lane：共用連線上的 SELECT 1。沒有資料庫的設定時不連線。"""
    if not os.getenv("DB_HOST"):
        return Outcome(False, "DB_NOT_CONFIGURED")
    with db.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchall()
    return Outcome(True, "OK")


def s3_probe_client():
    """這次檢查專用的 client：不重試、逾時很短。憑證和 storage.s3_client() 一樣由 boto3 自己找。"""
    session = boto3.session.Session(profile_name=os.getenv("AWS_PROFILE") or None,
                                    region_name=os.getenv("AWS_REGION") or None)
    return session.client("s3", config=Config(connect_timeout=S3_CONNECT_TIMEOUT, read_timeout=S3_READ_TIMEOUT,
                                               retries={"max_attempts": 1, "mode": "standard"}))


def check_files():
    """io lane：對一個不存在的物件 GetObject（只拿 1 byte）。沒有設定 bucket 時不連線。"""
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        return Outcome(False, "S3_NOT_CONFIGURED")
    client = s3_probe_client()
    try:
        response = client.get_object(Bucket=bucket, Key=S3_PROBE_KEY, Range="bytes=0-0")
    except botocore.exceptions.ClientError as exc:
        code = _client_error_code(exc)
        if code in S3_REACHED_CODES:
            return Outcome(True, "OK", f"GetObject on a missing test file: {code}")
        raise
    response["Body"].close()
    return Outcome(True, "OK", "GetObject on the test file: found")


def check_analysis():
    """io lane：GET /healthz。200 而且 status 是 ok 才算有回應；models 是 failed 算失敗（收得到工作但不能分析）。"""
    url = health_url(os.getenv("ANALYZE_API_URL", ""))
    if not url:
        return Outcome(False, "API_NOT_CONFIGURED")
    response = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
    if response.status_code != 200:
        return Outcome(False, f"API_HTTP_{int(response.status_code)}", f"GET /healthz: HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") != "ok":
        return Outcome(False, "API_BAD_RESPONSE", "GET /healthz: HTTP 200, unexpected body")
    models = body.get("models")
    queue = body.get("queue_depth")
    extra = f", queue {queue}" if isinstance(queue, int) else ""
    if models == "failed":
        return Outcome(False, "API_MODELS_FAILED", f"GET /healthz: models failed{extra}")
    if models == "loading":
        return Outcome(True, "OK_STARTING", f"GET /healthz: models loading{extra}")
    return Outcome(True, "OK", f"GET /healthz: HTTP 200{extra}")


PROBES = {"database": check_database, "files": check_files, "analysis": check_analysis}


# ---------------------------------------------------------------- 例外 → 代碼


def _client_error_code(exc):
    return str(exc.response.get("Error", {}).get("Code", ""))


def classify(name, exc):
    """探測丟出的例外 → (代碼, 安全的技術細節)。代碼是固定的英文大寫，不含例外訊息。"""
    kind = type(exc).__name__
    if name == "database":
        if isinstance(exc, (mysql.connector.Error, MySQLInterfaceError)):
            errno = getattr(exc, "errno", None)
            number = errno if isinstance(errno, int) and errno > 0 else None
            technical = f"{kind} {number}" if number else kind
            if number in DB_UNREACHABLE_ERRNOS:
                return "DB_UNREACHABLE", technical
            if number in DB_LOST_ERRNOS:
                return "DB_CONNECTION_LOST", technical
            if number in (1044, 1045):
                return "DB_SIGN_IN_FAILED", technical
            if number == 1049:
                return "DB_NOT_FOUND", technical
            if number == 2026:
                return "DB_TLS_FAILED", technical
            if number is None and isinstance(exc, (mysql.connector.InterfaceError, mysql.connector.OperationalError)):
                return "DB_UNREACHABLE", technical
            return (f"DB_ERROR_{number}" if number else "DB_ERROR"), technical
        return "UNEXPECTED", kind
    if name == "files":
        if isinstance(exc, (botocore.exceptions.NoCredentialsError, botocore.exceptions.PartialCredentialsError,
                            botocore.exceptions.CredentialRetrievalError, botocore.exceptions.ProfileNotFound)):
            return "S3_NO_CREDENTIALS", kind
        if isinstance(exc, botocore.exceptions.ClientError):
            code = _client_error_code(exc)
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            safe_code = code if re.fullmatch(r"[A-Za-z0-9]{1,40}", code) else "?"
            technical = f"GetObject: {safe_code}" + (f" (HTTP {status})" if isinstance(status, int) else "")
            if code in S3_CREDENTIAL_CODES:
                return "S3_CREDENTIALS_REJECTED", technical
            if code == "NoSuchBucket":
                return "S3_BUCKET_NOT_FOUND", technical
            if code in ("AllAccessDisabled", "AccountProblem", "403") or status == 403:
                return "S3_ACCESS_DENIED", technical
            return (f"S3_HTTP_{status}" if isinstance(status, int) else "S3_ERROR"), technical
        if isinstance(exc, (botocore.exceptions.ReadTimeoutError, botocore.exceptions.ConnectTimeoutError)):
            return "S3_TIMEOUT", kind
        if isinstance(exc, (botocore.exceptions.ConnectionError, botocore.exceptions.HTTPClientError)):
            return "S3_UNREACHABLE", kind
        return "UNEXPECTED", kind
    if isinstance(exc, requests.Timeout):  # 包含 ConnectTimeout
        return "API_TIMEOUT", kind
    if isinstance(exc, requests.ConnectionError):
        return "API_UNREACHABLE", kind
    if isinstance(exc, (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema,
                        requests.exceptions.InvalidSchema)):
        return "API_NOT_CONFIGURED", kind
    if isinstance(exc, requests.RequestException):
        return "API_ERROR", kind
    return "UNEXPECTED", kind


def outcome_message(name, code):
    """給人看的一兩句：先講結果，再講下一步（DS-50、DS-52）。技術代碼不放進來。"""
    if code == "OK":
        return {"database": _tr("Connected to the database."),
                "files": _tr("Cloud storage answered and accepted this computer's storage credentials."),
                "analysis": _tr("The analysis service is running.")}[name]
    if code == "OK_STARTING":
        return _tr("The analysis service is starting. New analyses wait until it's ready.")
    admin = _tr("Contact your administrator.")
    network = _tr("Check your network and try again.")
    messages = {
        "DB_NOT_CONFIGURED": _tr("The database isn't set up on this computer."),
        "DB_UNREACHABLE": _tr("Can't reach the database."),
        "DB_CONNECTION_LOST": _tr("The connection to the database was lost."),
        "DB_SIGN_IN_FAILED": _tr("The database didn't accept this computer's sign-in."),
        "DB_NOT_FOUND": _tr("The database in this computer's settings doesn't exist."),
        "DB_TLS_FAILED": _tr("Couldn't make a secure connection to the database."),
        "S3_NOT_CONFIGURED": _tr("Cloud storage isn't set up on this computer."),
        "S3_NO_CREDENTIALS": _tr("This computer has no storage credentials."),
        "S3_CREDENTIALS_REJECTED": _tr("The storage credentials are expired or not valid."),
        "S3_BUCKET_NOT_FOUND": _tr("The storage location in this computer's settings doesn't exist."),
        "S3_ACCESS_DENIED": _tr("This computer doesn't have permission to use cloud storage."),
        "S3_UNREACHABLE": _tr("Can't reach cloud storage."),
        "S3_TIMEOUT": _tr("Cloud storage didn't respond in time."),
        "API_NOT_CONFIGURED": _tr("The analysis service isn't set up on this computer."),
        "API_UNREACHABLE": _tr("Can't reach the analysis service."),
        "API_TIMEOUT": _tr("The analysis service didn't respond in time."),
        "API_MODELS_FAILED": _tr("The analysis service is running but can't analyze."),
    }
    next_steps = {"DB_UNREACHABLE": network, "DB_CONNECTION_LOST": network, "S3_UNREACHABLE": network,
                  "S3_TIMEOUT": network, "API_UNREACHABLE": network,
                  "API_TIMEOUT": _tr("Try again in a moment.")}
    if code in messages:
        return f"{messages[code]} {next_steps.get(code, admin)}"
    if code.startswith("DB_"):
        return _tr("The database returned an error. If this keeps happening, contact your administrator.")
    if code.startswith("S3_"):
        return _tr("Cloud storage returned an error. Try again in a moment.")
    if code.startswith("API_"):
        return _tr("The analysis service returned an error. Try again in a moment.")
    return _tr("The check couldn't finish. If this keeps happening, contact your administrator.")


# ---------------------------------------------------------------- 摘要（白名單）


def _safe_value(text, limit=40):
    """版本、系統名稱：只留英數和少數符號，最長 limit 個字（metadata 是外部檔案，不能原樣放進剪貼簿）。"""
    return SAFE_VALUE.sub("", str(text))[:limit].strip() or "Unavailable"


def summary_text(about, results, now=None):
    """Copy Diagnostic Summary 的內容。about：about_info()；results：{名稱: {state, checkedAt, code}}。

    只用白名單的欄位：版本與環境（過濾過字元）、時間（自己產生）、狀態（固定的字）、代碼（固定的格式）。
    """
    now = now or datetime.now().astimezone()
    offset = now.strftime("%z")
    zone = f" (UTC{offset[:3]}:{offset[3:]})" if offset else ""
    lines = [
        "ADAM diagnostic summary",
        f"Created: {now.strftime(TIME_FORMAT)}{zone}",
        f"App: {_safe_value(about.get('appName', ''))}",
        f"Version: {_safe_value(about.get('version', ''))}",
        f"Python: {_safe_value(about.get('python', ''))}",
        f"Qt: {_safe_value(about.get('qt', ''))} (PySide6 {_safe_value(about.get('pyside', ''))})",
        f"System: {_safe_value(about.get('system', ''))} ({_safe_value(about.get('architecture', ''))})",
    ]
    for name, _lane in CHECKS:
        result = results.get(name, {})
        state = result.get("state", NOT_CHECKED)
        line = f"{SUMMARY_NAMES[name]}: {SUMMARY_STATES.get(state, 'Not checked')}"
        checked = result.get("checkedAt", "")
        if state in (AVAILABLE, FAILED) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", checked):
            line += f" at {checked}"
        code = result.get("code", "")
        if state in (AVAILABLE, FAILED) and CODE.fullmatch(code):
            line += f" ({code})"
        lines.append(line)
    lines.append("Each check only tests whether that service answers.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- QML 的物件


class ConnectionCheck(QObject):
    """Check Connection（helpCenter.connection）。

    - items：三項，順序固定（Database、Cloud Storage、Analysis Service），每一項是
      {name, label, state, message, checkedAt, technical}。state：notChecked、checking、available、failed。
      message 是給人看的一兩句（還沒有結果時是這一項做什麼）；technical 收在 Details（代碼、例外類別、errno、
      HTTP 狀態、花了多久）。
    - check()：三項同時送出（db lane 一項、io lane 兩項）。還有任何一項在跑時回傳 False（防重複送出）。
      每一項回來就更新那一項；全部回來時 checking 變回 false，發 finished。
    - services 是 None 時（直接建立 ServerHome 的測試）不能檢查。
    """

    itemsChanged = Signal()
    checkingChanged = Signal()
    finished = Signal()

    def __init__(self, services, parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self._services = services
        self._clock = clock
        self._results = {name: {"state": NOT_CHECKED, "code": "", "checkedAt": "", "technical": ""}
                         for name, _lane in CHECKS}
        self._started = {}
        self._running = set()

    @Property("QVariantList", notify=itemsChanged)
    def items(self):
        labels = {"database": _tr("Database"), "files": _tr("Cloud Storage"), "analysis": _tr("Analysis Service")}
        # 還沒有結果時寫這一項是做什麼的（狀態標籤已經寫了 Not Checked／Checking…，不重複）
        roles = {"database": _tr("Keeps accounts, services and scores."),
                 "files": _tr("Keeps recordings, charts and reports."),
                 "analysis": _tr("Scores recordings after they're uploaded.")}
        items = []
        for name, _lane in CHECKS:
            result = self._results[name]
            state = result["state"]
            if state in (NOT_CHECKED, CHECKING):
                message = roles[name]
            else:
                message = outcome_message(name, result["code"])
            items.append({"name": name, "label": labels[name], "state": state, "message": message,
                          "checkedAt": result["checkedAt"], "technical": result["technical"]})
        return items

    @Property(bool, notify=checkingChanged)
    def checking(self):
        return bool(self._running)

    def results(self):
        """{名稱: {state, code, checkedAt, technical}}：摘要用。"""
        return {name: dict(result) for name, result in self._results.items()}

    @Slot(result=bool)
    def check(self):
        if self._services is None or self._running:
            return False
        tasks = self._services.tasks
        for name, lane in CHECKS:
            key = KEY_PREFIX + name
            if tasks.isBusy(key):  # 上一次的還沒真的停下來（例如被取消之後還在等逾時）
                continue
            probe = PROBES[name]
            work = (lambda token, report: check_database(self._services.db)) if name == "database" \
                else (lambda token, report, probe=probe: probe())
            if tasks.submit(lane, key, work, on_ok=lambda outcome, name=name: self._done(name, outcome),
                            on_err=lambda exc, name=name: self._failed(name, exc)):
                self._running.add(name)
                self._started[name] = self._clock()
                self._results[name] = {"state": CHECKING, "code": "", "checkedAt": "", "technical": ""}
        if not self._running:
            return False
        self.itemsChanged.emit()
        self.checkingChanged.emit()
        return True

    def _elapsed(self, name):
        return f"{max(0.0, self._clock() - self._started.get(name, self._clock())):.2f} s"

    def _done(self, name, outcome):
        technical = " · ".join(part for part in (outcome.code, outcome.technical, self._elapsed(name)) if part)
        self._finish(name, AVAILABLE if outcome.ok else FAILED, outcome.code, technical)

    def _failed(self, name, exc):
        logger.warning("connection check %s failed: %r", name, exc)
        code, technical = classify(name, exc)
        self._finish(name, FAILED, code, " · ".join((code, technical, self._elapsed(name))))

    def _finish(self, name, state, code, technical):
        self._running.discard(name)
        self._results[name] = {"state": state, "code": code if CODE.fullmatch(code) else "UNEXPECTED",
                               "checkedAt": datetime.now().strftime(TIME_FORMAT), "technical": technical}
        self.itemsChanged.emit()
        if not self._running:
            self.checkingChanged.emit()
            self.finished.emit()


class HelpCenter(QObject):
    """設定頁 Help 區塊的 Python 端（context property helpCenter）。

    reports：報表頁的 PDFHandler（有的話）。清掉下載快取時，它畫好的頁面圖也沒了，要它忘掉目前的頁面。
    """

    def __init__(self, services, reports=None, parent=None):
        super().__init__(parent)
        self._services = services
        self._about = about_info()
        self._connection = ConnectionCheck(services, self)
        self._storage = local_storage.CacheStorage(services, media.download_cache_dirs, self._places, self)
        if reports is not None and hasattr(reports, "forget_pages"):
            self._storage.cleared.connect(reports.forget_pages)

    @staticmethod
    def _places():
        """看剩餘空間、也顯示在畫面上的地方：錄影的資料夾和下載快取所在的資料夾（T51）。"""
        return {"recordings": local_storage.recordings_dir(), "cache": media.cache_folder()}

    @Property("QVariantMap", constant=True)
    def about(self):
        return dict(self._about)

    @Property(QObject, constant=True)
    def connection(self):
        return self._connection

    @Property(QObject, constant=True)
    def storage(self):
        return self._storage

    @Slot(result=str)
    def summary(self):
        return summary_text(self._about, self._connection.results())

    @Slot(result=bool)
    def copySummary(self):
        """把摘要放進剪貼簿，跳一則 toast。回傳是否成功（沒有剪貼簿時 False）。不傳到任何地方。"""
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(self.summary())
        if self._services is not None:
            self._services.notifier.notify("success", _tr("Diagnostic summary copied"),
                                           _tr("Paste it where your administrator asks for it."))
        return True
