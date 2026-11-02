"""新版首頁的資料（新版介面 U1，new-UIUX PLAN §5）：服務人員端和管理員端共用。

兩端的首頁是同一個版面，資料只差在範圍：服務人員只看自己的服務（account_id），管理員看全部員工。
所以查詢、整理資料的純函式和 QML 讀的物件都放在這裡，兩端各建一個 HomeDashboard（context property
homeDashboard），第二階段的 QML 卡片可以兩端共用（T28）。

- 卡片：Overview（期間的分數方塊和服務方塊）、Score Trend，加上服務人員的 Recent Performance 或管理員的
  Staff Ranking。每張卡片是一個 HomeSection：loading、empty、error、ready 加上 refreshing，和管理員端的
  _Section 同一套（DS-25～27、DS-33）。一張讀不到只有那張顯示錯誤，其他照常（PLAN §5-2）；
  已經有資料時重新查詢，舊的數字留著，refreshing 為 true。
- 都在 db lane，每張卡片 1 個查詢、1 個往返（PLAN §5-3）。分數一律排除 -1（AVG(NULLIF(欄位, -1))）。
  都用 dictionary cursor：其他頁面的查詢也用它，已經建好了（多建一種 cursor 要多 1 個往返，T16）。
- 期間的邊界用這台電腦的日期算（today()），不用資料庫的 CURDATE()：service_start 是用戶端用本機時間寫的，
  RDS 預設 UTC，用 CURDATE() 的話「今天」會差 8 小時（db_schema.sql 的 time_zone 說明）。
- Overview 一次查完三個期間（Today、Last 7 days、Last 30 days）和各自的上一期：換期間不必再查（T28）。
  Customer／Staff、趨勢的分數也只是換一份已經有的資料，不再查詢。
"""
import logging
import re
from datetime import date, datetime, time, timedelta

from PySide6.QtCharts import QXYSeries  # import 之後，QML 傳進來的 LineSeries 才會是 QXYSeries
from PySide6.QtCore import Property, QCoreApplication, QObject, QPointF, Signal, Slot

from myapp.notifications import user_message

logger = logging.getLogger(__name__)

LOADING, EMPTY, ERROR, READY = "loading", "empty", "error", "ready"

# 期間 → 天數（含今天）。key 也是 QML 用的名字；SQL 的別名不能用數字開頭，另外對一份
PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30}
_PERIOD_ALIASES = {"today": "today", "7d": "d7", "30d": "d30"}
METRICS = {"overall": "total", "text": "text", "audio": "audio", "facial": "facial"}  # 畫面上的名稱 → 欄位的字尾
SIDES = {"customer": "customer", "staff": "server"}  # 畫面上的名稱 → 欄位的字首（server_* 是服務人員的分數）
STATUSES = {"completed": 3, "analyzing": 2, "notAnalyzed": 1}  # 服務方塊 → tbl_service.service_status
GOOD, FAIR = 85, 70  # 分數帶：Good 85 以上、Fair 70～84、Needs improvement 70 以下（PLAN §2）
TREND_DAYS = 14
RECENT_COUNT = 8
RANKING_DAYS = 30
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # 經過 _tr() 才顯示


def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：經過 translate，日後加翻譯檔就能換語言；%1、%2… 一次換成 values
    （像 QString::arg）。和 server_home_init.py、media.py 的 _tr 一樣；context 是 HomeDashboard。"""
    text = QCoreApplication.translate("HomeDashboard", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def today():
    """這台電腦的今天（期間、趨勢、排名都從這一天往前算）。測試用 monkeypatch 換掉。"""
    return date.today()


# ---------------------------------------------------------------- 純函式


def day_start(day):
    return datetime.combine(day, time.min)


def period_bounds(period, day):
    """(本期開始, 上一期開始)：本期是 day 和它前面共 n 天，從第一天的 00:00 起；上一期是再前面同樣長的 n 天。

    Today 的上一期是昨天整天，Last 7 days 的是再前面 7 天。本期沒有結束時間：到現在為止都算。
    """
    days = PERIOD_DAYS[period]
    start = day_start(day - timedelta(days=days - 1))
    return start, start - timedelta(days=days)


def score(value):
    """分數：NULL、-1（沒有資料）→ None；其他取到小數 1 位。畫面顯示的、分數帶、排序都用這個數。"""
    if value is None or value < 0:
        return None
    return round(float(value), 1)


def band(value):
    """分數帶（PLAN §2）："good"（85 以上）、"fair"（70～84）、"needsImprovement"（70 以下）；沒有分數是 ""。
    value 是 score() 取過位數的分數，所以 84.96 顯示 85.0，也算 good。"""
    if value is None:
        return ""
    return "good" if value >= GOOD else "fair" if value >= FAIR else "needsImprovement"


def delta_percent(current, previous):
    """和上一期比的變化（%）：(本期 − 上一期) ÷ 上一期 × 100，取到小數 1 位。

    用還沒取位數的平均算。任一期沒有分數、或上一期是 0（不能除）時是 None：畫面不顯示變化，不顯示 0%。
    """
    if current is None or previous is None or previous <= 0:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100, 1)


def short_day(day):
    """「Oct 30」：介面一律英文（PLAN D1），不跟著系統的語言設定（QML 的 Qt.formatDate 會跟著系統）。"""
    return _tr("%1 %2", _tr(_MONTHS[day.month - 1]), day.day)


def trend_days(day, count=TREND_DAYS):
    """趨勢的日子：day 和它前面共 count 天，舊的在前。"""
    return [day - timedelta(days=count - 1 - offset) for offset in range(count)]


def empty_series(count=TREND_DAYS):
    return {side: {metric: [None] * count for metric in METRICS} for side in SIDES}


def fill_trend(rows, days):
    """每天一列的查詢結果（day 和 8 個平均）→ {"customer": {"overall": [每天的分數…], …}, "staff": {…}}。

    沒有服務的日子（或那天的分數全是 -1）是 None，不是 0：畫的時候線在那裡斷開（UX-30、PLAN §2「數字照實畫」）。
    """
    index = {day: position for position, day in enumerate(days)}
    series = empty_series(len(days))
    for row in rows:
        position = index.get(row["day"])
        if position is None:  # 查詢的範圍和 days 一樣；萬一多出來（例如時區），不畫
            continue
        for side in SIDES:
            for metric in METRICS:
                series[side][metric][position] = score(row[f"{side}_{metric}"])
    return series


def value_runs(values):
    """[v0, v1, None, v3, …] → 連續有值的日子一段：[[{"x": 0, "y": v0}, {"x": 1, "y": v1}], [{"x": 3, …}]]。
    QML 每一段畫一條 LineSeries，沒有資料的日子斷開（和管理員端 Trends 的 as_runs 一樣，T19）。"""
    segments, previous = [], None
    for position, value in enumerate(values):
        if value is None:
            continue
        point = {"x": position, "y": value}
        if previous is not None and position == previous + 1:
            segments[-1].append(point)
        else:
            segments.append([point])
        previous = position
    return segments


def tile(current, previous):
    """一個分數方塊：{"value", "previous", "delta", "band"}。current、previous 是 SQL 算好的平均（NULL 是 None）。"""
    value = score(current)
    return {"value": value, "previous": score(previous), "delta": delta_percent(current, previous),
            "band": band(value)}


def empty_overview(day=None):
    """還沒載入時的形狀：數字是 0、分數是 None。QML 的綁定在載入前也讀得到每個欄位，不會出現 undefined 的錯誤。"""
    day = day or today()
    periods = {}
    for period in PERIOD_DAYS:
        start, previous = period_bounds(period, day)
        periods[period] = {
            "count": 0, "staffCount": 0, **dict.fromkeys(STATUSES, 0),
            "start": start.date().isoformat(), "previousStart": previous.date().isoformat(),
            **{side: {metric: tile(None, None) for metric in METRICS} for side in SIDES},
        }
    return periods


def overview_from_row(row, day):
    """Overview 查詢的那一列 → {"today": {...}, "7d": {...}, "30d": {...}}。每個期間：

    - count：這一期的服務數（不管分析到哪裡）；completed、analyzing、notAnalyzed：各狀態的筆數。
    - staffCount：這一期有服務的員工人數（管理員端的「9 staff」；服務人員端是 0 或 1）。
    - customer、staff：{"overall"|"text"|"audio"|"facial": tile()}，只算分析完成（狀態 3）的服務。
    - start、previousStart：本期、上一期的第一天（YYYY-MM-DD）。
    """
    periods = empty_overview(day)
    for period, alias in _PERIOD_ALIASES.items():
        entry = periods[period]
        entry["count"] = int(row[f"{alias}_count"] or 0)
        entry["staffCount"] = int(row[f"{alias}_staff"] or 0)
        for name, status in STATUSES.items():
            entry[name] = int(row[f"{alias}_status{status}"] or 0)
        for side in SIDES:
            for metric in METRICS:
                entry[side][metric] = tile(row[f"{alias}_{side}_{metric}"], row[f"{alias}_previous_{side}_{metric}"])
    return periods


def recent_rows(rows):
    """最近分析完成的服務（新的在前，查詢的順序）→ 長條圖由左到右的順序（舊的在前，PLAN §5-3「畫的時候倒過來」）。"""
    result = []
    for row in reversed(rows):
        start = row["service_start"]
        customer, staff = score(row["customer_total"]), score(row["server_total"])
        result.append({
            "serviceId": str(row["service_id"]),
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),  # 和分析頁一樣的格式：打開明細時一起帶過去
            "dayLabel": short_day(start.date()),
            "timeLabel": start.strftime("%H:%M"),
            "customer": customer, "staff": staff,
            "customerBand": band(customer), "staffBand": band(staff),
        })
    return result


def ranking_rows(rows):
    """排名查詢的每一列（每位員工一列）→ {"accountId", "name", "label", "count", "customer", "staff", 兩個分數帶}。

    依 account_id 分組，同名的人是兩列；label 是畫面上的名字，同名時後面加上登入帳號「王小明 (wang)」（T36）。
    """
    names = [row["account_name"] for row in rows]
    result = []
    for row in rows:
        customer, staff = score(row["customer"]), score(row["staff"])
        name, username = row["account_name"], row.get("account_username") or ""
        label = _tr("%1 (%2)", name, username) if username and names.count(name) > 1 else name
        result.append({"accountId": str(row["account_id"]), "name": name, "label": label,
                       "count": int(row["services"]), "customer": customer, "staff": staff,
                       "customerBand": band(customer), "staffBand": band(staff)})
    return result


def rank_staff(rows, side):
    """依 side（customer 或 staff）的平均由高到低。這一方沒有分數的排最後；同分照名字、再照帳號，順序固定。"""
    return sorted(rows, key=lambda row: (row[side] is None, -(row[side] or 0.0), row["name"], row["accountId"]))


# ---------------------------------------------------------------- 查詢（都在 db lane；每個 1 個往返）


def overview_sql(per_account):
    """三個期間的本期、上一期，一個查詢算完：每一期 4 個筆數、員工人數、8 個平均，上一期再 8 個平均，共 63 欄。

    用 CASE WHEN service_start >= 本期開始 分開兩期（PLAN §5-3）；範圍是 60 天（30 天和它的上一期）。
    服務數和各狀態的筆數算所有服務（所以 LEFT JOIN 分數），平均只算分析完成的。
    """
    columns = []
    for alias in _PERIOD_ALIASES.values():
        current = f"s.service_start >= %({alias}_start)s"
        previous = f"s.service_start >= %({alias}_previous)s AND s.service_start < %({alias}_start)s"
        columns.append(f"COUNT(CASE WHEN {current} THEN 1 END) AS {alias}_count")
        for status in STATUSES.values():
            columns.append(f"COUNT(CASE WHEN {current} AND s.service_status = {status} THEN 1 END) "
                           f"AS {alias}_status{status}")
        columns.append(f"COUNT(DISTINCT CASE WHEN {current} THEN s.account_id END) AS {alias}_staff")
        for side, prefix in SIDES.items():
            for metric, suffix in METRICS.items():
                value = f"NULLIF(sc.{prefix}_{suffix}, -1)"
                columns.append(f"AVG(CASE WHEN s.service_status = 3 AND {current} THEN {value} END) "
                               f"AS {alias}_{side}_{metric}")
                columns.append(f"AVG(CASE WHEN s.service_status = 3 AND {previous} THEN {value} END) "
                               f"AS {alias}_previous_{side}_{metric}")
    where = "s.account_id = %(account_id)s AND " if per_account else ""
    return ("SELECT " + ",\n       ".join(columns) + "\n"
            "FROM tbl_service AS s\n"
            "LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id\n"
            f"WHERE {where}s.service_start >= %(since)s")


def overview_params(day, account_id=None):
    params = {"account_id": account_id} if account_id else {}
    for period, alias in _PERIOD_ALIASES.items():
        params[f"{alias}_start"], params[f"{alias}_previous"] = period_bounds(period, day)
    params["since"] = min(params[f"{alias}_previous"] for alias in _PERIOD_ALIASES.values())
    return params


RECENT_SQL = f"""
SELECT s.service_id, s.service_start, sc.customer_total, sc.server_total
FROM tbl_service AS s
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
WHERE s.account_id = %s AND s.service_status = 3
ORDER BY s.service_start DESC, s.service_id DESC
LIMIT {RECENT_COUNT}
"""


def trend_sql(per_account):
    """近 14 天每天的平均（顧客、員工各 4 個分數），GROUP BY DATE(service_start)：沒有服務的日子沒有列。"""
    averages = [f"AVG(NULLIF(sc.{prefix}_{suffix}, -1)) AS {side}_{metric}"
                for side, prefix in SIDES.items() for metric, suffix in METRICS.items()]
    where = "s.account_id = %(account_id)s AND " if per_account else ""
    return ("SELECT DATE(s.service_start) AS day,\n       " + ",\n       ".join(averages) + "\n"
            "FROM tbl_service AS s\n"
            "JOIN tbl_service_score AS sc ON sc.service_id = s.service_id\n"
            f"WHERE {where}s.service_status = 3 AND s.service_start >= %(start)s AND s.service_start < %(end)s\n"
            "GROUP BY DATE(s.service_start)\n"
            "ORDER BY day")


# 每位員工 30 天內分析完成的服務數和兩個平均；有服務的員工都列出來（以前的 RankingModel 只留前 5、後 5）
RANKING_SQL = """
SELECT a.account_id, a.account_name, a.account_username, COUNT(*) AS services,
       AVG(NULLIF(sc.customer_total, -1)) AS customer,
       AVG(NULLIF(sc.server_total, -1)) AS staff
FROM tbl_service AS s
JOIN tbl_account AS a ON a.account_id = s.account_id
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
WHERE s.service_status = 3 AND s.service_start >= %s
GROUP BY a.account_id, a.account_name, a.account_username
"""


def load_overview(db, day, account_id=None):
    """在 db lane 執行：Overview（1 個往返）。沒有 GROUP BY 的聚合查詢一定剛好一列。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(overview_sql(account_id is not None), overview_params(day, account_id))
        return overview_from_row(cursor.fetchone(), day)


def load_recent(db, account_id):
    """在 db lane 執行：最近 8 筆分析完成的服務（1 個往返）。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(RECENT_SQL, (account_id,))
        return recent_rows(cursor.fetchall())


def load_trend(db, day, account_id=None):
    """在 db lane 執行：近 14 天的每日平均（1 個往返）→ (日子, 每一方每個分數的 14 個值)。"""
    days = trend_days(day)
    params = {"start": day_start(days[0]), "end": day_start(day + timedelta(days=1))}
    if account_id is not None:
        params["account_id"] = account_id
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(trend_sql(account_id is not None), params)
        return days, fill_trend(cursor.fetchall(), days)


def load_ranking(db, day):
    """在 db lane 執行：30 天內每位員工的筆數和平均（1 個往返）。"""
    since = day_start(day - timedelta(days=RANKING_DAYS - 1))
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(RANKING_SQL, (since,))
        return ranking_rows(cursor.fetchall())


def error_text(exc):
    """區塊錯誤（InlineError）的兩段文字：原因（ENG-12 的英文訊息）、收在 Details 裡的技術細節。"""
    _, title, detail, _ = user_message(exc)
    text = str(exc)
    return (f"{title}. {detail}" if detail else title), (f"{type(exc).__name__}: {text}" if text
                                                          else type(exc).__name__)


# ---------------------------------------------------------------- QML 讀的物件


class HomeSection(QObject):
    """首頁的一張卡片的資料狀態（和管理員端的 _Section 同一套）。子類別把結果存成自己的屬性，這裡只管狀態。

    - state：loading（第一次載入，還沒有資料）、empty（載入成功但沒有資料）、error（載入失敗）、ready。
    - refreshing：已經有資料（ready、empty）時又重新查詢。畫面留著舊的數字（PLAN §5-4），不閃成骨架。
    - 錯誤：errorDetail 是給人看的原因，errorTechnical 收在 Details 裡，failureCount 是連續失敗的次數
      （InlineError 在 ≥ 3 時多一行建議）。updatedAt 是上一次載入成功的時間（「HH:MM」，還沒有是 ""）。
    - refresh()：在 db lane 查詢（1 個往返）。已經在查詢時不再送一次，結果回來時一樣會更新。
    """

    stateChanged = Signal()

    def __init__(self, services, account_id, key, parent=None):
        super().__init__(parent)
        self.services = services
        self.account_id = account_id  # None：全部員工（管理員端）
        self.key = key  # TaskRunner 的 key：同一張卡片同時只有一個查詢
        self._state = LOADING
        self._refreshing = False
        self._error_detail = self._error_technical = self._updated_at = ""
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

    @Property(str, notify=stateChanged)
    def updatedAt(self):
        return self._updated_at

    @Slot(result=bool)
    def refresh(self):
        """Retry、重新整理：從今天往前算。回傳是否送出。"""
        return self.load(today())

    def load(self, day):
        """在 db lane 查詢 day 往前的資料。HomeDashboard.refresh() 讓三張卡片用同一天。"""
        work = self._work(day)
        submitted = self.services.tasks.submit("db", self.key, lambda token, report: work(),
                                               on_ok=self._succeeded, on_err=self._failed)
        if submitted:
            if self._state in (READY, EMPTY):
                self._set(refreshing=True)
            else:
                self._set(state=LOADING)
        return submitted

    def _work(self, day):
        """回傳在 db lane 執行的函式（不帶參數）。子類別實作。"""
        raise NotImplementedError

    def _apply(self, result):
        """在 GUI 執行緒套用查詢結果、發自己的 signal，回傳「是不是沒有資料」。子類別實作。"""
        raise NotImplementedError

    def _succeeded(self, result):
        empty = self._apply(result)
        self._failures = 0
        self._set(state=EMPTY if empty else READY, refreshing=False, detail="", technical="",
                  updated=datetime.now().strftime("%H:%M"), force=True)

    def _failed(self, exc):
        logger.warning("%s failed: %r", self.key, exc)
        self._failures += 1
        detail, technical = error_text(exc)
        self._set(state=ERROR, refreshing=False, detail=detail, technical=technical, force=True)

    def _set(self, state=None, refreshing=None, detail=None, technical=None, updated=None, force=False):
        before = (self._state, self._refreshing, self._error_detail, self._error_technical, self._updated_at)
        if state is not None:
            self._state = state
        if refreshing is not None:
            self._refreshing = refreshing
        if detail is not None:
            self._error_detail = detail
        if technical is not None:
            self._error_technical = technical
        if updated is not None:
            self._updated_at = updated
        if force or before != (self._state, self._refreshing, self._error_detail, self._error_technical,
                               self._updated_at):
            self.stateChanged.emit()


class OverviewSection(HomeSection):
    """Overview 卡片：periods 是 {"today", "7d", "30d"} 三個期間（形狀見 overview_from_row）。

    三個期間一次查完，QML 換期間、換 Customer／Staff 都只是讀另一份，不再查詢。
    30 天內一筆服務都沒有時是 empty（設計稿 States 的「No services in the last 30 days」）；
    選的期間沒有服務、30 天內有的，由 QML 看那一期的 count（例如 Today 是 0）。
    """

    # 子類別的屬性要用自己的 signal 當 notify：PySide 不認得基底類別的 signal
    overviewChanged = Signal()

    def __init__(self, services, account_id, key, parent=None):
        super().__init__(services, account_id, key, parent)
        self._periods = empty_overview()

    @Property("QVariantMap", notify=overviewChanged)
    def periods(self):
        return self._periods

    def _work(self, day):
        db, account_id = self.services.db, self.account_id
        return lambda: load_overview(db, day, account_id)

    def _apply(self, periods):
        if periods != self._periods:
            self._periods = periods
            self.overviewChanged.emit()
        return periods["30d"]["count"] == 0


class RecentSection(HomeSection):
    """Recent Performance（服務人員）：最近 8 筆分析完成的服務，rows 由舊到新（長條圖由左到右）。

    每一列：serviceId、start（YYYY-MM-DD HH:MM:SS）、dayLabel（Oct 30）、timeLabel（14:20）、
    customer、staff（沒有分數是 null）、customerBand、staffBand。彩色、灰色哪一方由 QML 依 Customer／Staff 決定。
    """

    rowsChanged = Signal()

    def __init__(self, services, account_id, key, parent=None):
        super().__init__(services, account_id, key, parent)
        self._rows = []

    @Property("QVariantList", notify=rowsChanged)
    def rows(self):
        return self._rows

    def _work(self, day):
        db, account_id = self.services.db, self.account_id
        return lambda: load_recent(db, account_id)

    def _apply(self, rows):
        if rows != self._rows:
            self._rows = rows
            self.rowsChanged.emit()
        return not rows


class TrendSection(HomeSection):
    """Score Trend：近 14 天（含今天）每天的平均。

    - days：["2026-10-19", …]（14 天，舊的在前）；labels：["Oct 19", …]。
    - series：{"customer": {"overall": [14 個值], "text": …, "audio": …, "facial": …}, "staff": {…}}；
      沒有服務的日子是 null。換 Customer／Staff、換分數不再查詢。
    - runs(side, metric)：連續有值的日子一段（x 是第幾天，0～13），QML 每段畫一條線；fillSeries() 一次交給 LineSeries。
    - 14 天都沒有分數時是 empty。
    """

    trendChanged = Signal()

    def __init__(self, services, account_id, key, parent=None):
        super().__init__(services, account_id, key, parent)
        self._days = trend_days(today())
        self._series = empty_series()

    @Property("QVariantList", notify=trendChanged)
    def days(self):
        return [day.isoformat() for day in self._days]

    @Property("QVariantList", notify=trendChanged)
    def labels(self):
        return [short_day(day) for day in self._days]

    @Property("QVariantMap", notify=trendChanged)
    def series(self):
        return self._series

    @Slot(str, str, result="QVariantList")
    def runs(self, side, metric):
        """side 是 customer 或 staff，metric 是 overall、text、audio、facial；不認得的回傳空的。"""
        return value_runs(self._series.get(side, {}).get(metric, []))

    @Slot(QObject, "QVariantList")
    def fillSeries(self, series, points):
        """把一段線一次交給 QML 的 LineSeries／ScatterSeries（ENG-19：一次 replace，不逐點 append）。"""
        if not isinstance(series, QXYSeries):
            raise TypeError(f"fillSeries() needs a LineSeries or ScatterSeries, not {type(series).__name__}")
        series.replace([QPointF(point["x"], point["y"]) for point in points])

    def _work(self, day):
        db, account_id = self.services.db, self.account_id
        return lambda: load_trend(db, day, account_id)

    def _apply(self, result):
        days, series = result
        if (days, series) != (self._days, self._series):
            self._days, self._series = days, series
            self.trendChanged.emit()
        return all(value is None for side in series.values() for values in side.values() for value in values)


class RankingSection(HomeSection):
    """Staff Ranking（管理員）：30 天內有分析完成的服務的員工，每人的筆數和兩個平均。

    byCustomer、byStaff 是同一份資料的兩種排序（由高到低，沒有分數的在最後）：QML 換 Customer／Staff
    不再查詢。每一列：accountId、name、count、customer、staff、customerBand、staffBand。
    """

    rankingChanged = Signal()

    def __init__(self, services, account_id, key, parent=None):
        super().__init__(services, account_id, key, parent)
        self._rows = []

    @Property("QVariantList", notify=rankingChanged)
    def byCustomer(self):
        return rank_staff(self._rows, "customer")

    @Property("QVariantList", notify=rankingChanged)
    def byStaff(self):
        return rank_staff(self._rows, "staff")

    def _work(self, day):
        db = self.services.db
        return lambda: load_ranking(db, day)

    def _apply(self, rows):
        if rows != self._rows:
            self._rows = rows
            self.rankingChanged.emit()
        return not rows


class HomeDashboard(QObject):
    """新版首頁的資料（context property homeDashboard）：overview、trend，加上 recent（服務人員）或 ranking（管理員）。

    account_id 是服務人員的帳號（只看自己的）；None 表示管理員端（全部員工）。key_prefix 讓兩端的 TaskRunner key 分開。
    refresh() 送出三張卡片的查詢（3 個往返），三張用同一天算範圍；各自成功、失敗。建構時不碰資料庫。
    """

    def __init__(self, services, account_id=None, key_prefix="home", parent=None):
        super().__init__(parent)
        self.account_id = account_id
        self._overview = OverviewSection(services, account_id, f"{key_prefix}:overview", self)
        self._trend = TrendSection(services, account_id, f"{key_prefix}:trend", self)
        if account_id is None:
            self._recent, self._ranking = None, RankingSection(services, None, f"{key_prefix}:ranking", self)
        else:
            self._recent, self._ranking = RecentSection(services, account_id, f"{key_prefix}:recent", self), None

    @Property(QObject, constant=True)
    def overview(self):
        return self._overview

    @Property(QObject, constant=True)
    def trend(self):
        return self._trend

    @Property(QObject, constant=True)
    def recent(self):
        """服務人員端的 Recent Performance；管理員端是 null。"""
        return self._recent

    @Property(QObject, constant=True)
    def ranking(self):
        """管理員端的 Staff Ranking；服務人員端是 null。"""
        return self._ranking

    @property
    def sections(self):
        """依送出的順序：Overview、左下（Recent 或 Ranking）、趨勢。"""
        middle = self._recent if self._recent is not None else self._ranking
        return (self._overview, middle, self._trend)

    @property
    def keys(self):
        return tuple(section.key for section in self.sections)

    @Slot()
    def refresh(self):
        """三張卡片都重新查詢（各 1 個往返）。已經有資料的卡片留著舊的數字（refreshing）。"""
        day = today()
        for section in self.sections:
            section.load(day)
