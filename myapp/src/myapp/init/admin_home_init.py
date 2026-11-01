"""管理員端（admin_home.qml）的 Python 物件。

前端重建 F2（CONTRACTS §3）：資料庫的讀寫都送到 TaskRunner 的 db lane，用 DbLane 那一條連線做；
GUI 執行緒上不碰資料庫。結果放進屬性（Property＋notify），頁面不管什麼時候建立，都拿得到最新的狀態（ENG-08）。

- 每個區塊（搜尋、帳號資料）都有四種狀態：loading、empty、error、ready（DS-25～27）。
  首頁的三張卡片在 myapp/home_dashboard.py（新版介面 U1，兩端共用，T28），用的是同一套狀態。
  已經有資料時重新載入，舊資料留著，refreshing 為 true（畫面變淡，DS-33）。
- 錯誤不再只 print：區塊原地顯示錯誤和重試；新增帳號的結果一定有 toast 或欄位錯誤（UX-33、UX-38）。
- 明細頁的下載、報表的 PDF 也在背景（第三波 F3）：和服務人員端共用 myapp/media.py。
"""
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Property, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QStandardItem, QStandardItemModel

from myapp.db_lane import DbLane
from myapp.home_dashboard import HomeDashboard
from myapp.notifications import Notifier, user_message
from myapp.passwords import hash_password
from myapp.search import ServiceSearch
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 服務明細、報表（前端重建 F3）
# 兩端共用 myapp/media.py（UX-46）。名稱留在這裡：AdminHome、launch() 照舊建立和註冊 ChartsModel、MediaModel、PDFHandler
from myapp.media import ChartsModel, MediaModel, MediaService, PDFHandler, clear_cache  # noqa: E402,F401


# ---------------------------------------------------------------- 區塊的載入狀態（ENG-08、DS-25～27）

LOADING, EMPTY, ERROR, READY, IDLE = "loading", "empty", "error", "ready", "idle"
DATE_FORMAT = "%Y-%m-%d"


def fill(template, *values):
    """像 QString::arg：把 %1、%2… 換成 values，一次換完（使用者打的字裡就算有「%2」也不會被換掉）。"""
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), template)


def technical_detail(exc):
    """收在 InlineError「Details」裡的技術細節，例如「InterfaceError: 2003: Can't connect to MySQL server …」。"""
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def reason_text(exc):
    """區塊錯誤的原因：「Can't reach the database. Check your network and try again.」（DS-50：先講發生什麼，再講下一步）。"""
    _, title, detail, _ = user_message(exc)
    return f"{title}. {detail}" if detail else title


class _Section(QObject):
    """畫面上一個區塊的資料狀態。子類別把結果存成自己的屬性，這裡只管狀態。

    - state：loading（第一次載入，還沒有資料）、empty（載入成功但沒有資料）、error（載入失敗）、ready（有資料）。
      要使用者先操作才載入的區塊（搜尋），一開始是 idle。
    - refreshing：已經有資料（ready、empty）時又重新載入。畫面留著舊資料、變淡，不換成 placeholder（DS-25）。
    - 錯誤：errorDetail 是給人看的原因，errorTechnical 收在「Details」裡，failureCount 是連續失敗的次數
      （InlineError 在 ≥ 3 時多一行建議）。區塊的標題由 QML 決定（qsTr）。
    """

    stateChanged = Signal()

    def __init__(self, services, key, state=LOADING, parent=None):
        super().__init__(parent)
        self.services = services
        self.key = key  # TaskRunner 的 key：同一個區塊同時只有一個工作
        self._state = state
        self._refreshing = False
        self._error_detail = ""
        self._error_technical = ""
        self._failures = 0

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(bool, notify=stateChanged)
    def refreshing(self):
        return self._refreshing

    @Property(str, notify=stateChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=stateChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=stateChanged)
    def failureCount(self):
        return self._failures

    def _submit(self, work, on_data, replace=False):
        """在 db lane 執行 work(token, report)；成功時在 GUI 執行緒呼叫 on_data(result)，它回傳「是不是空的」。

        回傳是否送出：同一個區塊已經在載入時，replace=False 不重複送（結果回來時一樣會更新畫面）。
        """
        submitted = self.services.tasks.submit(
            "db", self.key, work, on_ok=lambda result: self._succeeded(on_data, result), on_err=self._failed,
            replace=replace)
        if submitted:
            if self._state in (READY, EMPTY):
                self._update(refreshing=True)
            else:
                self._update(state=LOADING)
        return submitted

    def _succeeded(self, on_data, result):
        empty = on_data(result)
        self._failures = 0
        self._update(state=EMPTY if empty else READY, refreshing=False, detail="", technical="", force=True)

    def _failed(self, exc):
        logger.warning("%s failed: %r", self.key, exc)
        self._failures += 1
        self._update(state=ERROR, refreshing=False, detail=reason_text(exc), technical=technical_detail(exc),
                     force=True)

    def _update(self, state=None, refreshing=None, detail=None, technical=None, force=False):
        before = (self._state, self._refreshing, self._error_detail, self._error_technical)
        if state is not None:
            self._state = state
        if refreshing is not None:
            self._refreshing = refreshing
        if detail is not None:
            self._error_detail = detail
        if technical is not None:
            self._error_technical = technical
        if force or before != (self._state, self._refreshing, self._error_detail, self._error_technical):
            self.stateChanged.emit()


# ---------------------------------------------------------------- 帳號資料（側邊欄的名字、新增帳號的等級上限）


class AccountProfile(_Section):
    """登入的管理員的名字和等級：launch() 時在背景載入（1 個往返），不再在 GUI 執行緒呼叫預存程序（ENG-06）。

    載入失敗時，除了區塊的錯誤狀態，再跳一則可以重試的通知：名字和等級是整個管理員端都要用的。
    """

    # 子類別的屬性要用自己的 signal 當 notify：PySide 不認得基底類別的 signal（notify 會變成空的）
    profileChanged = Signal()

    RETRY_ACTION = "admin:profile:retry"

    def __init__(self, services, account_id, parent=None):
        super().__init__(services, "admin:profile", parent=parent)
        self.account_id = account_id
        self._name = ""
        self._level = 0  # 0＝還不知道
        self._toast_id = 0
        services.notifier.actionTriggered.connect(self._on_action)

    @Property(str, notify=profileChanged)
    def name(self):
        return self._name

    @Property(int, notify=profileChanged)
    def level(self):
        return self._level

    @Slot(result=bool)
    def load(self):
        db, account_id = self.services.db, self.account_id

        def work(token, report):
            with db.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT account_name, account_level FROM tbl_account WHERE account_id = %s",
                               (account_id,))
                return cursor.fetchone()

        return self._submit(work, self._apply)

    def _apply(self, row):
        if row is None:  # 帳號被刪掉了：當成錯誤，不要讓等級上限變成 0 卻看起來正常
            raise LookupError(f"account {self.account_id} not found")
        self._name = row["account_name"]
        self._level = int(row["account_level"])
        self.profileChanged.emit()
        if self._toast_id:
            self.services.notifier.dismiss(self._toast_id)
            self._toast_id = 0
        return False

    def _succeeded(self, on_data, result):
        try:
            super()._succeeded(on_data, result)
        except LookupError as exc:
            self._failed(exc)

    def _failed(self, exc):
        super()._failed(exc)
        self._toast_id = self.services.notifier.fromException(exc, key=self.key, actionId=self.RETRY_ACTION)

    @Slot(str, int)
    def _on_action(self, action_id, toast_id):
        if action_id == self.RETRY_ACTION:
            self.load()


class AccountManager(QObject):
    """新增帳號（UX-33、UX-34、DS-27、DS-52）。結果一定告訴使用者：成功是 toast，欄位的問題是欄位錯誤，
    連不上資料庫是常駐的錯誤 toast（可以重試，表單內容留著）。

    - 送出前在 GUI 執行緒檢查必填欄位和等級（不碰網路）；等級的檢查也是授權檢查，QML 只列出能指派的等級，
      這裡照樣擋：管理員只能新增或修改不高於自己等級的帳號（PLAN D9）。
    - 寫入在 db lane：先雜湊密碼（PBKDF2，約 40 ms），再 INSERT（1 個往返）。同一時間只有一個（key account:add）。
    - accountAdded(ok, message)、accountUpdated(ok, message) 在每一次送出都會發一次；focusRequested(field) 要頁面把焦點移到
      第一個有錯的欄位（username、password、name、level）。
    """

    accountAdded = Signal(bool, str)
    accountUpdated = Signal(bool, str)
    focusRequested = Signal(str)
    busyChanged = Signal()
    fieldErrorsChanged = Signal()
    currentLevelChanged = Signal()

    ADD_KEY = "account:add"
    UPDATE_KEY = "account:update"
    RETRY_ACTION = "account:add:retry"
    FIELDS = ("username", "password", "name", "level")
    INSERT = ("INSERT INTO tbl_account (account_id, account_username, account_password, account_name, account_level, "
              "account_occupation_name) VALUES (UUID(), %s, %s, %s, %s, %s)")
    UPDATE = ("UPDATE tbl_account SET account_username = %s, account_password = %s, account_name = %s, "
              "account_level = %s, account_occupation_name = %s WHERE account_id = %s")

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self._level = 0  # 目前登入的管理員的等級；0＝還沒載入
        self._errors = dict.fromkeys(self.FIELDS, "")
        self._retry_payload = None  # 連不上資料庫時留著，按通知上的 Retry 再送一次
        self._failure_toast = 0  # 「Account wasn't saved」那一則的 toastId
        services.tasks.busyChanged.connect(self._on_busy_changed)
        services.notifier.actionTriggered.connect(self._on_action)

    # ------------------------------------------------------------ QML 讀的屬性

    @Property(int, notify=currentLevelChanged)
    def currentLevel(self):
        return self._level

    @currentLevel.setter
    def currentLevel(self, level):
        level = int(level)
        if level != self._level:
            self._level = level
            self.currentLevelChanged.emit()

    @Property("QVariantList", notify=currentLevelChanged)
    def assignableLevels(self):
        """能指派的等級：1 到自己的等級（DS-51）。還不知道自己的等級時是空的。"""
        return list(range(1, self._level + 1))

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self.services.tasks.isBusy(self.ADD_KEY)

    @Property(str, notify=fieldErrorsChanged)
    def usernameError(self):
        return self._errors["username"]

    @Property(str, notify=fieldErrorsChanged)
    def passwordError(self):
        return self._errors["password"]

    @Property(str, notify=fieldErrorsChanged)
    def nameError(self):
        return self._errors["name"]

    @Property(str, notify=fieldErrorsChanged)
    def levelError(self):
        return self._errors["level"]

    # ------------------------------------------------------------ QML 呼叫的 slot

    @Slot(str, str, str, str, str, result=bool)
    def addAccount(self, username, password, name, level, occupation):
        """檢查欄位、送出新增。回傳是否已經送出（欄位有錯、或上一筆還在新增中，回傳 False）。"""
        if self.busy:
            return False
        username, name, occupation = username.strip(), name.strip(), occupation.strip()
        errors = {
            "username": "" if username else self.tr("Enter a username."),
            "password": "" if password else self.tr("Enter a password."),
            "name": "" if name else self.tr("Enter a name."),
            "level": self._level_error(level),
        }
        if not self._show_errors(errors):
            self.accountAdded.emit(False, next(message for message in errors.values() if message))
            return False
        payload = {"username": username, "password": password, "name": name, "level": int(level),
                   "occupation": occupation}
        return self._submit_add(payload)

    @Slot(str, str, str, str, str, str, result=bool)
    def updateAccount(self, account_id, username, password, name, level, occupation):
        """修改帳號。畫面上先拿掉了（PLAN D8：沒有帳號清單可以選），規則和新增一樣，結果一樣不會沒有聲音。"""
        if self.services.tasks.isBusy(self.UPDATE_KEY):
            return False
        username, name = username.strip(), name.strip()
        errors = {
            "username": "" if username else self.tr("Enter a username."),
            "password": "" if password else self.tr("Enter a password."),
            "name": "" if name else self.tr("Enter a name."),
            "level": self._level_error(level),
        }
        if not self._show_errors(errors):
            self.accountUpdated.emit(False, next(message for message in errors.values() if message))
            return False
        db, values = self.services.db, (username, password, name, str(int(level)), occupation.strip() or None)

        def work(token, report):
            with db.cursor() as cursor:
                cursor.execute(self.UPDATE, (values[0], hash_password(values[1]), *values[2:], account_id))
                return cursor.rowcount

        return self.services.tasks.submit("db", self.UPDATE_KEY, work,
                                          on_ok=lambda rows: self._updated(username),
                                          on_err=lambda exc: self._update_failed(username, exc))

    @Slot(str)
    def clearFieldError(self, field):
        """使用者改了這個欄位：它的錯誤先拿掉（下次送出再檢查）。"""
        if self._errors.get(field):
            self._errors[field] = ""
            self.fieldErrorsChanged.emit()

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    def _level_error(self, level):
        if self._level <= 0:
            return self.tr("Your account level hasn't loaded yet. Try again in a moment.")
        try:
            value = int(level)
        except (TypeError, ValueError):
            return self.tr("Choose a level.")
        if value < 1:
            return self.tr("Choose a level.")
        if value > self._level:
            return fill(self.tr("Level %1 is above your level (%2). Choose level %2 or lower."), value, self._level)
        return ""

    def _show_errors(self, errors):
        """套用欄位錯誤；有錯時把焦點移到第一個有錯的欄位。回傳是否全部沒問題。"""
        if errors != self._errors:
            self._errors = dict(errors)
            self.fieldErrorsChanged.emit()
        first = next((field for field in self.FIELDS if errors[field]), None)
        if first is not None:
            self.focusRequested.emit(first)
            return False
        return True

    def _submit_add(self, payload):
        db = self.services.db

        def work(token, report):
            if "hash" not in payload:  # 重試時用上次算好的雜湊；明碼算完就丟掉
                payload["hash"] = hash_password(payload.pop("password"))
            with db.cursor() as cursor:
                cursor.execute(self.INSERT, (payload["username"], payload["hash"], payload["name"],
                                             str(payload["level"]), payload["occupation"] or None))
            return payload

        return self.services.tasks.submit("db", self.ADD_KEY, work, on_ok=self._added,
                                          on_err=lambda exc: self._add_failed(payload, exc))

    def _level_label(self, level):
        """「1 · Staff」「3 · Admin」：和新增帳號頁的選單同一個寫法（PLAN D9：1 是服務人員，2～5 是管理員）。"""
        return fill(self.tr("%1 · Staff") if int(level) == 1 else self.tr("%1 · Admin"), level)

    def _added(self, payload):
        self._retry_payload = None
        self._show_errors(dict.fromkeys(self.FIELDS, ""))
        # 一句話講清楚新帳號怎麼用（設計稿 AdminAddAccount）：「王小明 can log in as staff07 (level 1 · Staff).」
        detail = fill(self.tr("%1 can log in as %2 (level %3)."), payload["name"], payload["username"],
                      self._level_label(payload["level"]))
        # 同一個 key：之前「沒有存到」的錯誤通知就地變成成功，所以不再記著它
        self.services.notifier.notify("success", self.tr("Account added"), detail, self.ADD_KEY)
        self._failure_toast = 0
        self.accountAdded.emit(True, self.tr("Account added"))

    def _add_failed(self, payload, exc):
        errno = getattr(exc, "errno", None)
        if errno == 1062 and "username" in str(exc):  # ER_DUP_ENTRY：uk_account_username
            self._retry_payload = None
            self._dismiss_failure_toast()
            message = fill(self.tr("Username %1 is already taken. Choose another one."), payload["username"])
            self._errors = {**dict.fromkeys(self.FIELDS, ""), "username": message}
            self.fieldErrorsChanged.emit()
            self.focusRequested.emit("username")
            self.accountAdded.emit(False, message)
            return
        if errno == 3819:  # ER_CHECK_CONSTRAINT_VIOLATED：chk_account_level
            self._retry_payload = None
            self._dismiss_failure_toast()
            message = self.tr("Choose a level.")
            self._errors = {**dict.fromkeys(self.FIELDS, ""), "level": message}
            self.fieldErrorsChanged.emit()
            self.focusRequested.emit("level")
            self.accountAdded.emit(False, message)
            return
        _, title, detail, retryable = user_message(exc)
        logger.warning("adding account %r failed: %r", payload["username"], exc)
        if retryable:  # 連不上資料庫：常駐、可以重試；表單的內容留著（DS-52）
            self._retry_payload = payload
            message = self.tr("Can't reach the database. Your entries are still here, so you can retry.")
            self._failure_toast = self.services.notifier.notify(
                "error", self.tr("Account wasn't saved"), message, self.ADD_KEY, self.RETRY_ACTION, self.tr("Retry"))
        else:
            self._retry_payload = None
            message = f"{title}. {detail}"
            self._failure_toast = self.services.notifier.notify("error", self.tr("Account wasn't saved"), message,
                                                                self.ADD_KEY)
        self.accountAdded.emit(False, message)

    def _updated(self, username):
        self.services.notifier.notify("success", self.tr("Account updated"), username, self.UPDATE_KEY)
        self.accountUpdated.emit(True, self.tr("Account updated"))

    def _update_failed(self, username, exc):
        if getattr(exc, "errno", None) == 1062 and "username" in str(exc):
            message = fill(self.tr("Username %1 is already taken. Choose another one."), username)
            self._errors = {**dict.fromkeys(self.FIELDS, ""), "username": message}
            self.fieldErrorsChanged.emit()
            self.focusRequested.emit("username")
        else:
            _, title, detail, _ = user_message(exc)
            message = f"{title}. {detail}"
            self.services.notifier.notify("error", self.tr("Account wasn't updated"), message, self.UPDATE_KEY)
        self.accountUpdated.emit(False, message)

    def _dismiss_failure_toast(self):
        """之前「沒有存到」的通知已經不對了（例如重試的結果是帳號重複）：收掉，問題改寫在欄位上。"""
        if self._failure_toast:
            self.services.notifier.dismiss(self._failure_toast)
            self._failure_toast = 0

    @Slot(str, bool)
    def _on_busy_changed(self, key, busy):
        if key == self.ADD_KEY:
            self.busyChanged.emit()

    @Slot(str, int)
    def _on_action(self, action_id, toast_id):
        if action_id == self.RETRY_ACTION and self._retry_payload is not None and not self.busy:
            self._submit_add(self._retry_payload)


# ---------------------------------------------------------------- 服務查詢


def parse_date(text):
    """「YYYY-MM-DD」→ datetime；空白 → None。格式不對丟 ValueError。"""
    text = text.strip()
    return datetime.strptime(text, DATE_FORMAT) if text else None


class ServiceManager(_Section):
    """服務查詢（UX-24、DS-26、DS-27）：服務人員名單和搜尋都在背景。

    - 名單：只列等級 1（服務人員）；namesState 是 loading、error、ready、empty。
    - 搜尋：日期格式不對時不查，錯誤顯示在那個欄位下方；新的搜尋取代還在跑的舊搜尋（replace=True）。
      state 一開始是 idle（還沒搜尋過）。lastQuery 是最後一次送出的條件，頁面重新建立時拿來還原欄位，
      空狀態也用它寫出「No services from … to …」。
    """

    namesChanged = Signal()
    dateErrorsChanged = Signal()
    resultsChanged = Signal()
    queryChanged = Signal()
    currentServiceIdChanged = Signal()

    NAMES_KEY = "admin:staff-names"
    NAMES_QUERY = "SELECT DISTINCT account_name FROM tbl_account WHERE account_level = 1 ORDER BY account_name"
    SEARCH_QUERY = """
        SELECT s.service_id, a.account_name AS name, s.service_start AS start_datetime
        FROM tbl_service s
        JOIN tbl_account a ON s.account_id = a.account_id
        WHERE 1 = 1
    """

    def __init__(self, services, parent=None):
        super().__init__(services, "admin:search", state=IDLE, parent=parent)
        self._currentServiceId = ""
        self._names = []
        self._names_state = LOADING
        self._date_errors = {"start": "", "end": ""}
        self._results = []
        self._query = {"name": "", "start": "", "end": ""}
        self.resultsModel = ResultsModel()

    @Property(str, notify=currentServiceIdChanged)
    def currentServiceId(self):
        return self._currentServiceId

    @Slot(str)
    def setCurrentServiceId(self, service_id):
        self._currentServiceId = service_id
        self.currentServiceIdChanged.emit()

    @Property(list, notify=namesChanged)
    def names(self):
        return self._names

    @Property(str, notify=namesChanged)
    def namesState(self):
        return self._names_state

    @Property(str, notify=dateErrorsChanged)
    def startDateError(self):
        return self._date_errors["start"]

    @Property(str, notify=dateErrorsChanged)
    def endDateError(self):
        return self._date_errors["end"]

    @Property("QVariant", notify=resultsChanged)
    def results(self):
        return self._results

    @Property("QVariantMap", notify=queryChanged)
    def lastQuery(self):
        """最後一次送出的條件 {"name", "start", "end"}（日期是使用者打的字）。"""
        return dict(self._query)

    @Slot(result=bool)
    def loadNames(self):
        db = self.services.db

        def work(token, report):
            with db.cursor() as cursor:
                cursor.execute(self.NAMES_QUERY)
                return [row[0] for row in cursor.fetchall()]

        submitted = self.services.tasks.submit("db", self.NAMES_KEY, work, on_ok=self._names_loaded,
                                               on_err=self._names_failed)
        if submitted and self._names_state != LOADING:
            self._names_state = LOADING
            self.namesChanged.emit()
        return submitted

    @Slot(str, str, str, result=bool)
    def search(self, name, start_date, end_date):
        """檢查日期、送出搜尋。回傳是否已經送出（日期格式不對時回傳 False）。"""
        dates, errors = {}, {}
        for field, text in (("start", start_date), ("end", end_date)):
            try:
                dates[field] = parse_date(text)
                errors[field] = ""
            except ValueError:
                dates[field] = None
                errors[field] = self.tr("Enter the date as YYYY-MM-DD, for example 2026-10-31.")
        if errors != self._date_errors:
            self._date_errors = errors
            self.dateErrorsChanged.emit()
        if any(errors.values()):
            return False

        query, params = self.SEARCH_QUERY, []
        if name:
            query += " AND a.account_name = %s"
            params.append(name)
        if dates["start"] is not None:
            query += " AND s.service_start >= %s"
            params.append(dates["start"])
        if dates["end"] is not None:  # 結束日當天整天都要算：條件用「小於隔天 00:00」
            query += " AND s.service_start < %s"
            params.append(dates["end"] + timedelta(days=1))
        query += " ORDER BY s.service_start, s.service_id"
        db = self.services.db

        def work(token, report):
            with db.cursor(dictionary=True) as cursor:
                cursor.execute(query, tuple(params))
                return cursor.fetchall()

        submitted = self._submit(work, self._show_results, replace=True)
        if submitted:
            self._query = {"name": name, "start": start_date.strip(), "end": end_date.strip()}
            self.queryChanged.emit()
        return submitted

    @Slot(result=bool)
    def retrySearch(self):
        """用最後一次送出的條件再搜尋一次（區塊錯誤的 Retry）。"""
        return self.search(self._query["name"], self._query["start"], self._query["end"])

    @Slot(str)
    def clearDateError(self, field):
        if self._date_errors.get(field):
            self._date_errors[field] = ""
            self.dateErrorsChanged.emit()

    def _show_results(self, rows):
        self._results = list(rows)
        self.resultsModel.clear()
        for row in rows:
            self.resultsModel.appendRow([
                QStandardItem(row["service_id"]),
                QStandardItem(row["name"]),
                QStandardItem(row["start_datetime"].strftime("%Y-%m-%d %H:%M:%S")),
            ])
        self.resultsChanged.emit()
        return not rows

    def _names_loaded(self, names):
        self._names = names
        self._names_state = READY if names else EMPTY
        self.namesChanged.emit()

    def _names_failed(self, exc):
        logger.warning("loading staff names failed: %r", exc)
        self._names_state = ERROR
        self.namesChanged.emit()


class ResultsModel(QStandardItemModel):
    ServiceIdRole = Qt.UserRole + 1
    NameRole = Qt.UserRole + 2
    StartDatetimeRole = Qt.UserRole + 3

    def __init__(self):
        super().__init__()

    def data(self, index, role):
        if not index.isValid():
            return None

        if role == self.ServiceIdRole:
            return index.siblingAtColumn(0).data(Qt.DisplayRole)
        elif role == self.NameRole:
            return index.siblingAtColumn(1).data(Qt.DisplayRole)
        elif role == self.StartDatetimeRole:
            return index.siblingAtColumn(2).data(Qt.DisplayRole)

        return super().data(index, role)

    def roleNames(self):
        roles = super().roleNames()
        roles[self.ServiceIdRole] = b'service_id'
        roles[self.NameRole] = b'name'
        roles[self.StartDatetimeRole] = b'start_datetime'
        return roles


# ---------------------------------------------------------------- 主畫面

HOME_KEY_PREFIX = "admin:home"  # 首頁三張卡片的 TaskRunner key：admin:home:overview、:ranking、:trend


class AdminHome(QObject):
    """管理員端的主畫面：建構時不碰資料庫（ENG-06），各頁的資料由頁面或 launch() 送到 db lane 載入。"""

    def __init__(self, account_id, services, parent=None):
        super().__init__(parent)
        self.account_id = account_id
        self.services = services
        self._profile = AccountProfile(services, account_id, self)
        self.homeDashboard = HomeDashboard(services, None, HOME_KEY_PREFIX, self)  # 新版首頁（U1，T28）：全部員工
        self.serviceManager = ServiceManager(services, self)
        self.accountManager = AccountManager(services, self)
        self.chartsModel = ChartsModel()
        self.mediaModel = MediaModel()
        self.pdfmodel = PDFHandler(services)
        self.serviceSearch = ServiceSearch.for_admin(services, parent=self)  # 頁首的搜尋（全部＋員工名字）
        self._profile.profileChanged.connect(self._sync_level)

    @Property(QObject, constant=True)
    def profile(self):
        """AccountProfile：name、level、state。側邊欄顯示 name；新增帳號頁依 level 列出能指派的等級。"""
        return self._profile

    @Slot(result=str)
    def getID(self):
        return self.account_id

    @Slot(str)
    @Slot(str, str)
    def load_service_media(self, service_id, start_time=""):
        """Details：先換頁，檔案在背景查詢（db lane）、下載（io lane）；GUI 執行緒不碰網路（UX-22、ENG-04）。"""
        self.mediaModel.open(service_id, start_time, self.services, self.chartsModel)

    @Slot()
    def clearDir(self):
        """Quit：取消還在下載的檔案，清空影片、圖表、報表頁面的快取（和 MediaModel 用同一組資料夾）。"""
        clear_cache(self.mediaModel)

    def close(self, owns_services=False):
        """主畫面關掉時：關掉明細頁用過的舊連線；services 是 launch() 自己建的，就一起收掉。"""
        if owns_services:
            self.services.tasks.shutdown()

    @Slot()
    def _sync_level(self):
        self.accountManager.currentLevel = self._profile.level


def launch(engine, account_id, services=None):
    """建立管理員端的主畫面（admin_home.qml），回傳 AdminHome。

    GUI 執行緒上不碰資料庫（0 個往返）：帳號名稱和等級在這裡送到 db lane，首頁的三張卡片（Overview、Staff Ranking、
    Score Trend）由首頁建立時送（homeDashboard.refresh()），背景總共 1＋3＝4 個往返（T28）。

    services：MainWindow 的 AppServices。沒給時（還沒改的舊測試直接呼叫 launch）自己建一份，
    並註冊外殼要用的 context property（notifier、uiPrefs、tasks），視窗關掉時一起收掉。
    """
    owns_services = services is None
    if owns_services:
        services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
    home = AdminHome(account_id, services)
    context = engine.rootContext()
    for name, obj in {
        "adminHome": home,
        "homeDashboard": home.homeDashboard,
        "serviceManager": home.serviceManager,
        "resultsModel": home.serviceManager.resultsModel,
        "accountManager": home.accountManager,
        "chartsModel": home.chartsModel,
        "mediaModel": home.mediaModel,
        "pdfHandler": home.pdfmodel,
        "serviceSearch": home.serviceSearch,
    }.items():
        context.setContextProperty(name, obj)
    # 外殼的 ToastHost、Theme 的 Binding 用這幾個；MainWindow 已經註冊同一份時不必重設（重設會讓綁定重新計算）
    for name, obj in (("tasks", services.tasks), ("notifier", services.notifier), ("uiPrefs", services.ui_prefs)):
        if context.contextProperty(name) is not obj:
            context.setContextProperty(name, obj)

    home.profile.load()  # 第一個送進 db lane：側邊欄的名字最先出來

    roots_before = len(engine.rootObjects())
    qml_file = Path(__file__).resolve().parent.parent / "qml/pages/admin_home.qml"
    engine.load(QUrl.fromLocalFile(qml_file))
    roots = engine.rootObjects()
    if len(roots) == roots_before:
        sys.exit(-1)

    window = roots[-1]  # 剛載入的主畫面（從登入視窗進來時，第 0 個是登入視窗）
    window.showFullScreen()
    # lambda 也讓 home 活得和視窗一樣久：QML 透過 context property 用它
    window.destroyed.connect(lambda *args: home.close(owns_services))
    return home
