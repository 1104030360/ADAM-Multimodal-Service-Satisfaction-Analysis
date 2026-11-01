"""頁首的「搜尋服務 ID」（新版介面 UD5，new-UIUX PLAN §4、§5-3；取捨見 tradeoff/T32）。

輸入服務 ID 的開頭，列出符合的服務，選一筆就打開明細（打開由 QML 呼叫 serverHome／adminHome 的
load_service_media(serviceId, startTime) 再換頁）。服務人員只搜得到自己的；管理員搜得到全部，也能用員工名字搜。

- 一次搜尋 1 個往返，在 db lane 上，最多 10 筆（多查 1 筆，用來知道還有沒有更多：hasMore）。
- 打字時不會一直查：setQuery() 每打一個字都可以呼叫，停 250 ms 才送出（單發的 QTimer）。
  新的查詢取代還在跑的舊查詢（replace=True），而且每次送出都有編號，畫面上只會出現最新那次的結果。
- 輸入的規則（parse_query）：
  - 先做 NFKC（全形的「Ｓ２０２６」當成「S2026」），去掉前後空白。
  - 服務 ID：開頭可以有 S（大小寫都可以），後面是數字，數字之間可以有 - / : 和空白
    （「2026-10-31 14:30」→「S202610311430」）。至少 1 個數字、最多 14 個（ID 是 S＋YYYYMMDDHHMMSS）。
  - 管理員：不像服務 ID 的字就當成名字，比對名字的開頭或名字裡任一個字的開頭；不分大小寫和重音
    （欄位的定序是 utf8mb4_0900_ai_ci）。名字開頭符合的排在前面，再依時間新的在前。
  - 服務人員打了不可能是服務 ID 的字：state 是 invalid，不查。只打了一個「S」還不查（idle）。
- LIKE 的萬用字元（% _ \）一律跳脫，值都用參數傳，不拼進 SQL。

QML 讀的屬性：state（idle、invalid、loading、ready、empty、error）、results、hasMore、refreshing、
errorDetail、errorTechnical、failureCount、searchesNames、limit。錯誤只放在屬性裡，由下拉清單顯示，不跳對話框。
"""
import logging
import re
import unicodedata
from collections import namedtuple

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from myapp.notifications import user_message

logger = logging.getLogger(__name__)

DEBOUNCE_MS = 250  # 停止打字多久才送出
RESULT_LIMIT = 10  # 下拉清單最多幾筆
ID_DIGITS = 14     # 服務 ID 是「S」＋YYYYMMDDHHMMSS
NAME_MAX = 100     # tbl_account.account_name 是 VARCHAR(100)：比這長的不可能符合
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # 和服務查詢頁的 start_datetime 一樣：明細頁頁首的時間

# TaskRunner 的 key（db lane）。和服務查詢頁的 staff:search、admin:search 分開，兩邊不會互相取代
SEARCH_KEYS = {"staff": "staff:header-search", "admin": "admin:header-search"}

IDLE, INVALID, LOADING, READY, EMPTY, ERROR = "idle", "invalid", "loading", "ready", "empty", "error"

# kind：idle（沒有要查的）、invalid（不可能符合）、id（value 是「S」＋數字）、name（value 是名字的開頭）
Query = namedtuple("Query", "kind value")
NOTHING = Query(IDLE, "")
IMPOSSIBLE = Query(INVALID, "")

_ID_SHAPE = re.compile(r"[Ss]?[0-9\s/:-]*")  # S 可有可無，後面是數字和分隔符號
_SEPARATORS = re.compile(r"[\s/:-]+")

_COLUMNS = """
SELECT s.service_id, s.service_start, s.service_status, a.account_name
FROM tbl_service AS s
JOIN tbl_account AS a ON a.account_id = s.account_id
"""
# 服務 ID 的開頭（主鍵的範圍查詢）。服務人員多一個 account_id 條件：只看得到自己的
ID_SQL = _COLUMNS + """WHERE s.service_id LIKE %s{account}
ORDER BY s.service_start DESC, s.service_id DESC
LIMIT %s"""
# 名字的開頭，或名字裡任一個字的開頭（「Chen」找得到「Mia Chen」）。名字開頭符合的排前面，再依時間新的在前
NAME_SQL = _COLUMNS + """WHERE a.account_name LIKE %s OR a.account_name LIKE %s
ORDER BY (a.account_name LIKE %s) DESC, s.service_start DESC, s.service_id DESC
LIMIT %s"""


def escape_like(text):
    """LIKE 的萬用字元當成一般的字：\\ 先跳脫，再跳脫 % 和 _（MySQL 預設的跳脫字元是 \\）。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_query(text, names=False):
    """使用者打的字 → Query。names：可以用名字搜（管理員）。規則見模組說明。"""
    text = unicodedata.normalize("NFKC", text or "").strip()
    if not text:
        return NOTHING
    if _ID_SHAPE.fullmatch(text):
        digits = _SEPARATORS.sub("", text.lstrip("Ss"))
        if digits:
            return Query("id", "S" + digits) if len(digits) <= ID_DIGITS else IMPOSSIBLE
        if not names:  # 只打了「S」（或只有分隔符號）：還不能查，但也不算錯，可能正要打數字
            return NOTHING if text in ("S", "s") else IMPOSSIBLE
    if not names:
        return IMPOSSIBLE
    name = " ".join(text.split())  # 中間連續的空白當成一個
    return Query("name", name) if len(name) <= NAME_MAX else IMPOSSIBLE


def search_sql(query, account_id=None, limit=RESULT_LIMIT):
    """(SQL, 參數)。account_id 不是 None 時只查這個帳號的服務。多查 1 筆，用來判斷 hasMore。"""
    if query.kind == "id":
        account = "" if account_id is None else " AND s.account_id = %s"
        params = [escape_like(query.value) + "%"]
        if account_id is not None:
            params.append(account_id)
        return ID_SQL.format(account=account), (*params, limit + 1)
    if query.kind == "name":
        if account_id is not None:
            raise ValueError("name search is for admins only")
        start = escape_like(query.value) + "%"
        word = "% " + start
        return NAME_SQL, (start, word, start, limit + 1)
    raise ValueError(f"nothing to search for: {query!r}")


def as_result(row):
    """資料庫的一列 → 下拉清單的一筆（QML 讀的鍵）。"""
    start = row["service_start"]
    return {
        "serviceId": str(row["service_id"]),
        "startTime": start.strftime(TIME_FORMAT) if start else "",
        "status": int(row["service_status"]),  # 1 已上傳、2 分析中、3 分析完成
        "staffName": row["account_name"] or "",
    }


def find_services(db, query, account_id=None, limit=RESULT_LIMIT):
    """在 db lane 執行：符合的服務，最多 limit＋1 筆（依時間新的在前）。1 個往返。

    用 dictionary cursor：和兩端其他的查詢共用同一個 cursor（DbLane 每種 cursor 第一次建立時要多 1 個往返）。
    """
    sql, params = search_sql(query, account_id, limit)
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(sql, params)
        return [as_result(row) for row in cursor.fetchall()]


def _error_texts(error):
    """InlineError 的兩段文字：原因（ENG-12 的英文訊息）、收在 Details 裡的技術細節。"""
    _, title, detail, _ = user_message(error)
    reason = f"{title}. {detail}" if detail else title
    text = str(error)
    return reason, f"{type(error).__name__}: {text}" if text else type(error).__name__


class ServiceSearch(QObject):
    """頁首搜尋框背後的資料（context property serviceSearch）。只在 GUI 執行緒使用。

    建立：ServiceSearch.for_staff(services, account_id)（只查自己的）、ServiceSearch.for_admin(services)（全部＋名字）。

    - setQuery(text)：每打一個字都呼叫。空白 → idle；不可能符合 → invalid；其他的停 debounce_ms 才送出。
      和目前要查的一樣（例如只多了空白）就什麼都不做。
    - searchNow()：不等了，馬上送出還在等的查詢（例如按 Enter）。retry()：錯誤之後再查一次。clear()：回到 idle。
    - 狀態：還沒有結果時是 loading；已經有結果（ready、empty）又要查新的字時，結果留著、refreshing 是 true，
      新的到了再換（DS-33：不閃成空白）。錯誤時清掉結果，state 是 error。
    - results：[{serviceId, startTime, status, staffName}]，依時間新的在前，最多 limit 筆；hasMore：還有更多。
    """

    stateChanged = Signal()
    resultsChanged = Signal()

    def __init__(self, services, account_id=None, *, everyone=False, parent=None, debounce_ms=DEBOUNCE_MS):
        super().__init__(parent)
        if not everyone and not account_id:
            raise ValueError("a staff search needs the account_id")  # 絕不因為少傳參數就查到全部人的
        self._services = services
        self._account_id = None if everyone else account_id
        self._names = everyone
        self._key = SEARCH_KEYS["admin" if everyone else "staff"]
        self._target = NOTHING  # 要顯示的查詢
        self._shown = None      # 目前 results 是哪一個查詢的結果
        self._generation = 0    # 每次送出加 1：只收最新那一次的結果
        self._state = IDLE
        self._refreshing = False
        self._results = []
        self._has_more = False
        self._error_detail = self._error_technical = ""
        self._failures = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._fire)

    @classmethod
    def for_staff(cls, services, account_id, parent=None, **kwargs):
        return cls(services, account_id, parent=parent, **kwargs)

    @classmethod
    def for_admin(cls, services, parent=None, **kwargs):
        return cls(services, everyone=True, parent=parent, **kwargs)

    # ------------------------------------------------------------ QML 讀的屬性

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(bool, notify=stateChanged)
    def refreshing(self):
        """已經有結果，新的字還在查：舊的結果留著（畫面可以變淡）。"""
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

    @Property("QVariantList", notify=resultsChanged)
    def results(self):
        return self._results

    @Property(bool, notify=resultsChanged)
    def hasMore(self):
        return self._has_more

    @Property(bool, constant=True)
    def searchesNames(self):
        """管理員：也可以用員工名字搜（搜尋框的提示文字、結果要不要顯示名字）。"""
        return self._names

    @Property(int, constant=True)
    def limit(self):
        return RESULT_LIMIT

    @property
    def pending(self):
        """還在等 debounce 的計時（測試用）。"""
        return self._timer.isActive()

    # ------------------------------------------------------------ QML 呼叫

    @Slot(str)
    def setQuery(self, text):
        query = parse_query(text, self._names)
        if query == self._target:
            return
        self._target = query
        self._timer.stop()
        self._abandon()
        if query.kind in (IDLE, INVALID):
            self._show([], False, query.kind)
            self._shown = None
        elif query == self._shown:  # 改回結果還在畫面上的那個字（例如刪掉剛打的字）
            self._update(refreshing=False)
        else:
            if self._state in (READY, EMPTY):
                self._update(refreshing=True)
            else:
                self._show([], False, LOADING)
            self._timer.start()

    @Slot(result=bool)
    def searchNow(self):
        """馬上送出還在等 debounce 的查詢。回傳是否送出了（沒有在等的就不送）。"""
        if not self._timer.isActive():
            return False
        self._timer.stop()
        return self._submit()

    @Slot(result=bool)
    def retry(self):
        """錯誤之後再查一次（InlineError 的 Retry）。"""
        if self._state != ERROR:
            return False
        self._show([], False, LOADING)
        return self._submit()

    @Slot()
    def clear(self):
        """搜尋框清空、按 Escape、選好一筆之後：回到 idle，還在查的不要了。"""
        self.setQuery("")

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    @Slot()
    def _fire(self):
        """debounce 的時間到了。"""
        self._submit()

    def _submit(self):
        query = self._target
        if query.kind not in ("id", "name") or self._services is None:
            return False
        self._generation += 1
        generation = self._generation
        db, account_id = self._services.db, self._account_id
        submitted = self._services.tasks.submit(
            "db", self._key, lambda token, report: find_services(db, query, account_id),
            on_ok=lambda rows: self._found(generation, query, rows),
            on_err=lambda error: self._failed(generation, error), replace=True)
        if not submitted:  # TaskRunner 已經關了（程式結束中）
            logger.info("service search %r was not submitted", query)
        return submitted

    def _abandon(self):
        """要查的字變了：還在跑的舊查詢不要了（之後回來的結果也會因為編號不對被丟掉）。"""
        self._generation += 1
        if self._services is not None and self._services.tasks.isBusy(self._key):
            self._services.tasks.cancel(self._key)

    def _found(self, generation, query, rows):
        if generation != self._generation:
            return
        self._shown = query
        self._failures = 0
        self._error_detail = self._error_technical = ""
        self._show(rows[:RESULT_LIMIT], len(rows) > RESULT_LIMIT, READY if rows else EMPTY)

    def _failed(self, generation, error):
        if generation != self._generation:
            return
        logger.warning("service search failed: %r", error)
        self._shown = None
        self._failures += 1
        self._error_detail, self._error_technical = _error_texts(error)
        self._show([], False, ERROR)

    def _show(self, results, has_more, state):
        if results != self._results or has_more != self._has_more:
            self._results, self._has_more = list(results), has_more
            self.resultsChanged.emit()
        if state != ERROR:
            self._error_detail = self._error_technical = ""
        self._update(state=state, refreshing=False, force=state == ERROR)

    def _update(self, state=None, refreshing=None, force=False):
        before = (self._state, self._refreshing)
        if state is not None:
            self._state = state
        if refreshing is not None:
            self._refreshing = refreshing
        if force or before != (self._state, self._refreshing):
            self.stateChanged.emit()
