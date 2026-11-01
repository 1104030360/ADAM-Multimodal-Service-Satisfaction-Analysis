"""管理員端（admin_home.qml）的 Python 物件。

前端重建 F2（CONTRACTS §3）：資料庫的讀寫都送到 TaskRunner 的 db lane，用 DbLane 那一條連線做；
GUI 執行緒上不碰資料庫。結果放進屬性（Property＋notify），頁面不管什麼時候建立，都拿得到最新的狀態（ENG-08）。

- 每個區塊（分數、排名、趨勢、搜尋、帳號資料）都有四種狀態：loading、empty、error、ready（DS-25～27）。
  已經有資料時重新載入，舊資料留著，refreshing 為 true（畫面變淡，DS-33）。
- 錯誤不再只 print：區塊原地顯示錯誤和重試；新增帳號的結果一定有 toast 或欄位錯誤（UX-33、UX-38）。
- 明細頁的下載、報表的 PDF 也在背景（第三波 F3）：和服務人員端共用 myapp/media.py。
"""
import calendar
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCharts import QXYSeries  # import 之後，QML 傳進來的 LineSeries／ScatterSeries 才會是 QXYSeries
from PySide6.QtCore import Property, QObject, QPointF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QStandardItem, QStandardItemModel

from myapp.db_lane import DbLane
from myapp.notifications import Notifier, user_message
from myapp.passwords import hash_password
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 服務明細、報表（前端重建 F3）
# 兩端共用 myapp/media.py（UX-46）。名稱留在這裡：AdminHome、launch() 照舊建立和註冊 ChartsModel、MediaModel、PDFHandler
from myapp.media import ChartsModel, MediaModel, MediaService, PDFHandler, clear_cache  # noqa: E402,F401


# ---------------------------------------------------------------- 區塊的載入狀態（ENG-08、DS-25～27）

LOADING, EMPTY, ERROR, READY, IDLE = "loading", "empty", "error", "ready", "idle"
NO_SCORE = -1.0  # 和資料庫一樣：-1 表示「沒有資料」，畫面顯示「—」
SCORE_KEYS = ("total", "text", "audio", "facial")
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

    def _added(self, payload):
        self._retry_payload = None
        self._show_errors(dict.fromkeys(self.FIELDS, ""))
        detail = fill(self.tr("%1 · %2 · Level %3"), payload["username"], payload["name"], payload["level"])
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

    def _on_busy_changed(self, key, busy):
        if key == self.ADD_KEY:
            self.busyChanged.emit()

    def _on_action(self, action_id, toast_id):
        if action_id == self.RETRY_ACTION and self._retry_payload is not None and not self.busy:
            self._submit_add(self._retry_payload)


# ---------------------------------------------------------------- 管理員首頁：趨勢、分數、排名


def trend_points(rows):
    """[(日, 服務人員平均, 顧客平均)] → ({日: 分數}, {日: 分數})。沒有資料的日子不放（UX-30：是空白，不是 0）。"""
    staff, customer = {}, {}
    for day, staff_score, customer_score in rows:
        if staff_score is not None:  # 某天的分數全是 -1 時，NULLIF 之後只剩 NULL，AVG 也是 NULL
            staff[int(day)] = float(staff_score)
        if customer_score is not None:
            customer[int(day)] = float(customer_score)
    return staff, customer


def as_points(scores):
    """{日: 分數} → [{"x": 日, "y": 分數}]，依日期排序（給 QML 的 ScatterSeries）。"""
    return [{"x": day, "y": scores[day]} for day in sorted(scores)]


def as_runs(scores):
    """{日: 分數} → 連續的日子一段：[[{"x", "y"}, …], …]。每一段畫一條線，段和段之間斷開（UX-30）。"""
    runs = []
    for point in as_points(scores):
        if runs and point["x"] == runs[-1][-1]["x"] + 1:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


class TrendDataManager(_Section):
    """趨勢圖：選定月份每一天的平均分數。只有有資料的日子才有點；x 軸是那個月實際的天數（UX-30）。

    換月份用 replace=True：使用者連續換月份時，只留最後一次的結果。圖表由 QML 呼叫 fillSeries()，
    每條線一次給完（ENG-19），不逐點送 signal。
    """

    trendChanged = Signal()

    QUERY = """
        SELECT DAY(service.service_start) AS day,
               AVG(NULLIF(score.server_total, -1)) AS server_score,
               AVG(NULLIF(score.customer_total, -1)) AS customer_score
        FROM tbl_service_score AS score
        JOIN tbl_service AS service ON score.service_id = service.service_id
        WHERE service.service_status = 3
          AND service.service_start >= %s AND service.service_start < %s
        GROUP BY DAY(service.service_start)
        ORDER BY DAY(service.service_start)
    """

    def __init__(self, services, parent=None):
        super().__init__(services, "admin:trends", parent=parent)
        today = date.today()
        self._month, self._year = today.month, today.year
        self._staff, self._customer = {}, {}

    @Property(int, notify=trendChanged)
    def month(self):
        return self._month

    @Property(int, notify=trendChanged)
    def year(self):
        return self._year

    @Property(int, notify=trendChanged)
    def daysInMonth(self):
        return calendar.monthrange(self._year, self._month)[1]

    @Property("QVariantList", notify=trendChanged)
    def staffPoints(self):
        return as_points(self._staff)

    @Property("QVariantList", notify=trendChanged)
    def customerPoints(self):
        return as_points(self._customer)

    @Property("QVariantList", notify=trendChanged)
    def staffRuns(self):
        return as_runs(self._staff)

    @Property("QVariantList", notify=trendChanged)
    def customerRuns(self):
        return as_runs(self._customer)

    @Slot(int, int, result=bool)
    def load(self, month, year):
        """載入 year 年 month 月。年份選單還沒準備好時 QML 會傳 0（Number("")），那時不查。"""
        if not (1 <= month <= 12 and 1 <= year < 9999):
            return False
        db = self.services.db
        start = datetime(year, month, 1)
        end = (start + timedelta(days=32)).replace(day=1)  # 下個月 1 號；用「小於」它，月底最後一天整天都算

        def work(token, report):
            with db.cursor() as cursor:
                cursor.execute(self.QUERY, (start, end))
                return cursor.fetchall()

        return self._submit(work, lambda rows: self._apply(month, year, rows), replace=True)

    @Slot(result=bool)
    def retry(self):
        return self.load(self._month, self._year)

    @Slot(QObject, "QVariantList")
    def fillSeries(self, series, points):
        """把整條線一次交給 QML 的 LineSeries／ScatterSeries（ENG-19：一次 replace，不逐點 append）。"""
        if not isinstance(series, QXYSeries):
            raise TypeError(f"fillSeries() needs a LineSeries or ScatterSeries, not {type(series).__name__}")
        series.replace([QPointF(point["x"], point["y"]) for point in points])

    def _apply(self, month, year, rows):
        self._month, self._year = month, year
        self._staff, self._customer = trend_points(rows)
        self.trendChanged.emit()
        return not self._staff and not self._customer


def scores_query():
    """一個查詢同時算每日（今天）和每週（7 天前到今天）：1 個往返，以前是 2 個。-1 不算進平均。"""
    periods = {"daily": "service.service_start >= CURDATE()", "weekly": None}
    columns = []
    for period, condition in periods.items():
        columns.append(f"COUNT(CASE WHEN {condition} THEN 1 END) AS {period}_count" if condition
                       else f"COUNT(*) AS {period}_count")
        for who in ("customer", "server"):
            for key in SCORE_KEYS:
                value = f"NULLIF(score.{who}_{key}, -1)"
                if condition:
                    value = f"CASE WHEN {condition} THEN {value} END"
                columns.append(f"AVG({value}) AS {period}_{who}_{key}")
    return (f"SELECT {', '.join(columns)} FROM tbl_service AS service "
            "JOIN tbl_service_score AS score ON service.service_id = score.service_id "
            "WHERE service.service_status = 3 AND service.service_start >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)")


class ServiceScoreManager(_Section):
    """管理員首頁的每日、每週分數（DS-33）。所有人的服務都算；-1（沒有資料）不算進平均。

    分數是 {"total", "text", "audio", "facial"}，沒有資料的是 -1（畫面顯示「—」，不畫 0）。
    dailyState、weeklyState 是各期間的狀態：今天沒有完成的服務（或分數全是 -1）時是 empty。
    """

    scoresChanged = Signal()
    periodStatesChanged = Signal()  # dailyState、weeklyState：跟著 stateChanged 一起發
    QUERY = scores_query()

    def __init__(self, services, parent=None):
        super().__init__(services, "admin:scores", parent=parent)
        self._counts = {"daily": 0, "weekly": 0}
        self._scores = {(period, who): dict.fromkeys(SCORE_KEYS, NO_SCORE)
                        for period in ("daily", "weekly") for who in ("customer", "server")}
        self.stateChanged.connect(self.periodStatesChanged)  # 分數在 stateChanged 之前就換好了

    @Property(int, notify=scoresChanged)
    def dailyCount(self):
        return self._counts["daily"]

    @Property(int, notify=scoresChanged)
    def weeklyCount(self):
        return self._counts["weekly"]

    @Property("QVariantMap", notify=scoresChanged)
    def dailyCustomerScores(self):
        return dict(self._scores["daily", "customer"])

    @Property("QVariantMap", notify=scoresChanged)
    def dailyStaffScores(self):
        return dict(self._scores["daily", "server"])

    @Property("QVariantMap", notify=scoresChanged)
    def weeklyCustomerScores(self):
        return dict(self._scores["weekly", "customer"])

    @Property("QVariantMap", notify=scoresChanged)
    def weeklyStaffScores(self):
        return dict(self._scores["weekly", "server"])

    @Property(str, notify=periodStatesChanged)
    def dailyState(self):
        return self._period_state("daily")

    @Property(str, notify=periodStatesChanged)
    def weeklyState(self):
        return self._period_state("weekly")

    @Slot(result=bool)
    def refresh(self):
        db = self.services.db

        def work(token, report):
            with db.cursor(dictionary=True) as cursor:
                cursor.execute(self.QUERY)
                return cursor.fetchone()

        return self._submit(work, self._apply)

    def _apply(self, row):
        for period in ("daily", "weekly"):
            self._counts[period] = int(row[f"{period}_count"] or 0)
            for who in ("customer", "server"):
                self._scores[period, who] = {
                    key: NO_SCORE if row[f"{period}_{who}_{key}"] is None else float(row[f"{period}_{who}_{key}"])
                    for key in SCORE_KEYS}
        self.scoresChanged.emit()
        return not self._has_data("daily") and not self._has_data("weekly")

    def _has_data(self, period):
        scores = [*self._scores[period, "customer"].values(), *self._scores[period, "server"].values()]
        return self._counts[period] > 0 and any(score >= 0 for score in scores)

    def _period_state(self, period):
        if self._state not in (READY, EMPTY):
            return self._state
        return READY if self._has_data(period) else EMPTY


def rank(rows):
    """[(姓名, 顧客平均, 服務人員平均)] → (顧客前 5、顧客後 5、服務人員前 5、服務人員後 5)，每一項是 {"name", "score"}。

    平均在 SQL 裡算好（排除 -1）；沒有分數（None 或 -1）的人不排名。分數取到小數第 2 位。
    同分時照姓名排序（SQL 的 ORDER BY），結果固定。
    """
    customers = [(name, round(float(customer), 2)) for name, customer, _ in rows
                 if customer is not None and customer != -1]
    staff = [(name, round(float(server), 2)) for name, _, server in rows if server is not None and server != -1]

    def entries(pairs):
        return [{"name": name, "score": score} for name, score in pairs]

    return (entries(sorted(customers, key=lambda pair: pair[1], reverse=True)[:5]),
            entries(sorted(customers, key=lambda pair: pair[1])[:5]),
            entries(sorted(staff, key=lambda pair: pair[1], reverse=True)[:5]),
            entries(sorted(staff, key=lambda pair: pair[1])[:5]))


class RankingModel(_Section):
    """排名：全部期間、已完成的服務，依服務人員的平均分數取前 5 和後 5。

    平均改在 SQL 裡算（ENG-06）：回傳的列數是服務人員的人數，不再隨著服務的筆數變多。
    """

    rankingChanged = Signal()

    QUERY = """
        SELECT acc.account_name,
               AVG(NULLIF(score.customer_total, -1)) AS customer_score,
               AVG(NULLIF(score.server_total, -1)) AS server_score
        FROM tbl_service_score AS score
        JOIN tbl_service AS service ON score.service_id = service.service_id
        JOIN tbl_account AS acc ON service.account_id = acc.account_id
        WHERE service.service_status = 3
        GROUP BY acc.account_name
        ORDER BY acc.account_name
    """

    def __init__(self, services, parent=None):
        super().__init__(services, "admin:ranking", parent=parent)
        self._ranking = ([], [], [], [])
        self._ranked = 0

    @Property("QVariantList", notify=rankingChanged)
    def topCustomer(self):
        return self._ranking[0]

    @Property("QVariantList", notify=rankingChanged)
    def bottomCustomer(self):
        return self._ranking[1]

    @Property("QVariantList", notify=rankingChanged)
    def topStaff(self):
        return self._ranking[2]

    @Property("QVariantList", notify=rankingChanged)
    def bottomStaff(self):
        return self._ranking[3]

    @Property(int, notify=rankingChanged)
    def rankedCount(self):
        """有分數、進得了排名的服務人員人數。"""
        return self._ranked

    @Slot(result=bool)
    def refresh(self):
        db = self.services.db

        def work(token, report):
            with db.cursor() as cursor:
                cursor.execute(self.QUERY)
                return cursor.fetchall()

        return self._submit(work, self._apply)

    def _apply(self, rows):
        self._ranking = rank(rows)
        self._ranked = len({name for name, customer, server in rows
                            if (customer is not None and customer != -1) or (server is not None and server != -1)})
        self.rankingChanged.emit()
        return self._ranked == 0


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


class AdminHome(QObject):
    """管理員端的主畫面：建構時不碰資料庫（ENG-06），各頁的資料由頁面或 launch() 送到 db lane 載入。"""

    def __init__(self, account_id, services, parent=None):
        super().__init__(parent)
        self.account_id = account_id
        self.services = services
        self._profile = AccountProfile(services, account_id, self)
        self.serviceScoreManager = ServiceScoreManager(services, self)
        self.rankingModel = RankingModel(services, self)
        self.trendDataManager = TrendDataManager(services, self)
        self.serviceManager = ServiceManager(services, self)
        self.accountManager = AccountManager(services, self)
        self.chartsModel = ChartsModel()
        self.mediaModel = MediaModel()
        self.pdfmodel = PDFHandler(services)
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

    def _sync_level(self):
        self.accountManager.currentLevel = self._profile.level


def launch(engine, account_id, services=None):
    """建立管理員端的主畫面（admin_home.qml），回傳 AdminHome。

    GUI 執行緒上不碰資料庫（0 個往返）：帳號名稱和等級在這裡送到 db lane，首頁的分數、排名、趨勢由頁面自己送，
    每一項只載入一次（ENG-06），背景總共 4 個往返。

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
        "serviceScoreManager": home.serviceScoreManager,
        "rankingModel": home.rankingModel,
        "trendDataManager": home.trendDataManager,
        "serviceManager": home.serviceManager,
        "resultsModel": home.serviceManager.resultsModel,
        "accountManager": home.accountManager,
        "chartsModel": home.chartsModel,
        "mediaModel": home.mediaModel,
        "pdfHandler": home.pdfmodel,
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
