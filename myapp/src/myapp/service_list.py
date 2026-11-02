"""服務清單：兩端的 Services 頁和服務明細共用的部分（新版介面 U2，PLAN §6-1、§6-4、§6-5、§6-9；T31）。

- RowListModel：依主鍵增量更新的清單模型（ENG-08）。服務人員端的分析頁、兩端的 Services 頁都用它。
- Services 頁的查詢：只列分析完成的服務，新的在前，一次只取畫面要的筆數（LIMIT），
  總筆數和最早、最晚的日期用 window function 在同一個查詢裡算（1 個往返），見 search_page()。
- 一次顯示 PAGE_SIZE（8）筆，Show More 用同樣的條件、多 8 筆的 LIMIT 再查一次（T31 第 1 節）。
- 介面上的文字（英文，PLAN D1）：日期、長度、範圍、筆數。月份不用 strftime 的 %b：
  QCoreApplication 在 macOS 上會 setlocale(LC_ALL, "")，%b 會變成系統語言的月份（例如「11月」）。
- 分數先四捨五入到小數 1 位（display_score）：分數帶由 QML 的 Theme.bandName() 用同一個數字判斷，
  畫面上的數字和分數帶的字才會一致（84.96 顯示 85.0，就是 Good）。
- 日期條件的檢查（date_errors）：格式不對、To 早於 From 都是欄位錯誤，不送出查詢（T31 第 7 節）。

文字的格式化都在 GUI 執行緒做（db lane 的工作只回傳資料庫的值）。
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from PySide6.QtCore import QAbstractListModel, QCoreApplication, QModelIndex, Property, Qt, Signal

PAGE_SIZE = 8  # 清單一次幾筆（UD7）
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DATE_FORMAT = "%Y-%m-%d"

_FIRST_ROLE = int(Qt.ItemDataRole.UserRole)


def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：經過 translate，日後加翻譯檔就能換語言；%1、%2… 一次換成 values。"""
    text = QCoreApplication.translate("ServiceList", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


# ---------------------------------------------------------------- 清單模型（ENG-08）


class RowListModel(QAbstractListModel):
    """依主鍵增量更新的清單模型（ENG-08）。角色名稱就是每一列 dict 的鍵。

    replace(rows) 只對真的變動的列發 remove、insert、move、dataChanged：清單不整份重設，
    捲動位置和沒變的 delegate 都留著（以前是 model = null 再設回去，捲動位置會跳回最上面，UX-21）。
    Show More 多查 8 筆時，前面的列沒變，只會在最後插入新的列。
    """

    countChanged = Signal()

    def __init__(self, roles, key, parent=None):
        super().__init__(parent)
        self._roles = tuple(roles)
        self._key = key
        self._rows = []

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._rows)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        return {_FIRST_ROLE + i: name.encode() for i, name in enumerate(self._roles)}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        offset = int(role) - _FIRST_ROLE
        if not index.isValid() or not 0 <= index.row() < len(self._rows) or not 0 <= offset < len(self._roles):
            return None
        return self._rows[index.row()][self._roles[offset]]

    def rows(self):
        """目前的每一列（複本），給 Python 端和測試讀。"""
        return [dict(row) for row in self._rows]

    def replace(self, rows):
        """換成 rows（依畫面順序，主鍵不重複）。"""
        before = len(self._rows)
        wanted = {row[self._key] for row in rows}
        for i in reversed(range(len(self._rows))):
            if self._rows[i][self._key] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        for target, row in enumerate(rows):
            row = {name: row.get(name) for name in self._roles}
            key = row[self._key]
            current = next((i for i in range(target, len(self._rows)) if self._rows[i][self._key] == key), None)
            if current is None:
                self.beginInsertRows(QModelIndex(), target, target)
                self._rows.insert(target, row)
                self.endInsertRows()
                continue
            if current != target:  # 前面的都排好了，所以 current 一定在 target 後面
                self.beginMoveRows(QModelIndex(), current, current, QModelIndex(), target)
                self._rows.insert(target, self._rows.pop(current))
                self.endMoveRows()
            changed = [name for name in self._roles if self._rows[target][name] != row[name]]
            if changed:
                self._rows[target] = row
                index = self.index(target)
                self.dataChanged.emit(index, index, [_FIRST_ROLE + self._roles.index(name) for name in changed])
        if len(self._rows) != before:
            self.countChanged.emit()


# ---------------------------------------------------------------- 分數、日期、長度的文字


def parse_date(text):
    """「YYYY-MM-DD」→ datetime；空白 → None（不限）；格式不對（包括沒有這一天，例如 2026-02-30）丟 ValueError。"""
    text = text.strip()
    return datetime.strptime(text, DATE_FORMAT) if text else None


def date_errors(start_text, end_text):
    """檢查 From、To 兩格：回傳 (start, end, {"start": 錯誤, "end": 錯誤})，沒有錯誤是空字串。

    格式不對（DS-52 的範本）寫在那一格下面；兩格都對但 To 早於 From，寫在 To 下面（不送出，免得查到 0 筆卻不知道為什麼）。
    """
    dates, errors = {}, {}
    for field, text in (("start", start_text), ("end", end_text)):
        try:
            dates[field], errors[field] = parse_date(text), ""
        except ValueError:
            dates[field], errors[field] = None, _tr("Enter the date as YYYY-MM-DD, for example 2026-10-31.")
    if dates["start"] is not None and dates["end"] is not None and dates["end"] < dates["start"]:
        errors["end"] = _order_error()
    return dates["start"], dates["end"], errors


def _order_error():
    return _tr("The To date is before the From date. Choose the same day or later.")


def clear_date_error(errors, field):
    """使用者改了 field（start、end）那一格：那一格的錯誤拿掉，等下一次 Search 再檢查。
    改的是 From、而 To 下面是「早於 From」的錯誤時，那一則也拿掉（改 From 可能就解決了）。"""
    order = _order_error()
    return {name: "" if name == field or (field == "start" and name == "end" and error == order) else error
            for name, error in errors.items()}


def format_time(value):
    """資料庫的時間 → 「2026-10-30 12:00:00」（Details 傳給明細頁的頁首）；沒有是空字串。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def display_score(value):
    """分數欄位 → 畫面上的分數：-1、NULL（沒有資料）是 None（畫面「—」），其他四捨五入到小數 1 位。

    分數帶（Theme.bandName、bandColor）用這個數字判斷，所以和畫面上的數字一致。
    """
    return None if value is None or value < 0 else round(float(value), 1)


def day_text(day):
    """「Oct 31, 2026」。"""
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def clock_text(moment):
    """「09:15」（24 小時制，和設計稿一樣）。"""
    return f"{moment.hour:02d}:{moment.minute:02d}"


def list_date_text(moment, today):
    """清單的 Date 欄：今年的寫「Nov 1, 11:45」（設計稿）；其他年份的加上年份「Dec 31, 2025, 14:00」。"""
    if moment is None:
        return ""
    day = f"{MONTHS[moment.month - 1]} {moment.day}"
    if moment.year != today.year:
        day += f", {moment.year}"
    return f"{day}, {clock_text(moment)}"


def time_range_text(start, end):
    """明細頁頁首的時間：「09:15–09:21」；沒有結束時間（或結束早於開始）只寫開始。"""
    if start is None:
        return ""
    if end is None or end < start:
        return clock_text(start)
    return f"{clock_text(start)}–{clock_text(end)}"


def length_text(start, end):
    """服務的長度：「6 min」「1 h 5 min」；不到 1 分鐘「Under 1 min」；不知道（沒有結束時間）是空字串。

    分鐘四捨五入（5 分 40 秒是 6 min）。
    """
    if start is None or end is None or end < start:
        return ""
    seconds = int((end - start).total_seconds())
    if seconds < 60:
        return _tr("Under 1 min")
    minutes = (seconds + 30) // 60
    if minutes < 60:
        return _tr("%1 min", minutes)
    hours, minutes = divmod(minutes, 60)
    return _tr("%1 h %2 min", hours, minutes) if minutes else _tr("%1 h", hours)


def range_text(first, last):
    """清單標題的日期範圍：「Oct 19 – Nov 1, 2026」；同一年只寫一次年份，同一天只寫一天。

    first、last 是 date（或 datetime），任一個可以是 None：「From Oct 19, 2026」「Until Nov 1, 2026」「All dates」。
    """
    first = first.date() if isinstance(first, datetime) else first
    last = last.date() if isinstance(last, datetime) else last
    if first is not None and last is not None:
        if first == last:
            return day_text(first)
        if first.year == last.year:
            return f"{MONTHS[first.month - 1]} {first.day} – {day_text(last)}"
        return f"{day_text(first)} – {day_text(last)}"
    if first is not None:
        return _tr("From %1", day_text(first))
    if last is not None:
        return _tr("Until %1", day_text(last))
    return _tr("All dates")


def count_text(count):
    """「18 services」「1 service」「No services」（千位加逗號，不跟著系統語言）。"""
    if count == 0:
        return _tr("No services")
    if count == 1:
        return _tr("1 service")
    return _tr("%1 services", f"{count:,}")


def shown_text(shown, total):
    """清單下方：「Showing 8 of 18」。"""
    return _tr("Showing %1 of %2", f"{shown:,}", f"{total:,}")


def staff_label(staff_name):
    """管理員端的篩選：選了服務人員就是名字，沒選是「All staff」。"""
    return staff_name or _tr("All staff")


def list_subtitle(range_part, staff=""):
    """清單標題旁的說明：「Oct 19 – Nov 1, 2026 · newest first」；管理員端前面加上篩選「All staff · …」。"""
    if staff:
        return _tr("%1 · %2 · newest first", staff, range_part)
    return _tr("%1 · newest first", range_part)


# ---------------------------------------------------------------- Services 頁的查詢（1 個往返）

# 總筆數、最早和最晚的開始時間用 window function：LIMIT 之前就算好，所以是全部符合條件的服務，不是這一頁的
SEARCH_SQL = """
SELECT s.service_id, a.account_name AS name, s.service_start, s.service_end,
       sc.customer_total, sc.server_total,
       COUNT(*) OVER () AS total_count,
       MIN(s.service_start) OVER () AS first_start,
       MAX(s.service_start) OVER () AS last_start
FROM tbl_service AS s
JOIN tbl_account AS a ON a.account_id = s.account_id
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
WHERE s.service_status = 3{conditions}
ORDER BY s.service_start DESC, s.service_id DESC
LIMIT %s
"""

# 清單每一列的角色（兩端的 Services 頁）。startTime 是 Details 傳給明細頁的開始時間；分數沒有資料是 null
LIST_ROLES = ("serviceId", "staffName", "startTime", "dateText", "lengthText", "customerScore", "staffScore")


@dataclass(frozen=True)
class Criteria:
    """一次搜尋的條件：start、end 是 datetime（當天 00:00）或 None；account_id 是只看哪一位服務人員（空字串＝全部）。
    staff_name 只用來顯示（標題的「王小明 · …」）：篩選一律用 account_id，名字可以改、可以重複（T36）。"""
    start: datetime = None
    end: datetime = None
    account_id: str = ""
    staff_name: str = ""


@dataclass(frozen=True)
class SearchPage:
    """查詢的結果：rows 是資料庫的列（最多 limit 筆，新的在前）；total 是全部符合條件的筆數；
    first、last 是全部符合條件的服務裡，最早、最晚的開始時間（沒有服務時是 None）。"""
    rows: list
    total: int
    first: datetime = None
    last: datetime = None
    limit: int = PAGE_SIZE


def search_query(criteria, limit):
    """（SQL, 參數）。結束日當天整天都算：條件用「小於隔天 00:00」。"""
    conditions, params = "", []
    if criteria.account_id:
        conditions += " AND s.account_id = %s"
        params.append(criteria.account_id)
    if criteria.start is not None:
        conditions += " AND s.service_start >= %s"
        params.append(criteria.start)
    if criteria.end is not None:
        conditions += " AND s.service_start < %s"
        params.append(criteria.end + timedelta(days=1))
    params.append(int(limit))
    return SEARCH_SQL.format(conditions=conditions), tuple(params)


def search_page(db, criteria, limit=PAGE_SIZE):
    """在 db lane 執行：分析完成的服務，新的在前，最多 limit 筆，加上總筆數和日期範圍。1 個往返。"""
    sql, params = search_query(criteria, limit)
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    if not rows:
        return SearchPage([], 0, limit=limit)
    first = rows[0]
    return SearchPage(rows, int(first["total_count"]), first["first_start"], first["last_start"], limit)


def list_row(row, today):
    """資料庫的一列 → 清單模型的一列（LIST_ROLES）。分數沒有資料是 None（畫面顯示「—」）。"""
    service_id, start, end = str(row["service_id"]), row["service_start"], row.get("service_end")
    return {
        "serviceId": service_id, "staffName": row["name"], "startTime": format_time(start),
        "dateText": list_date_text(start, today), "lengthText": length_text(start, end),
        "customerScore": display_score(row.get("customer_total")),
        "staffScore": display_score(row.get("server_total")),
    }


def list_rows(page, today=None):
    today = today or date.today()
    return [list_row(row, today) for row in page.rows]


def page_range(criteria, page):
    """標題的範圍：有填的日期照填的；沒填的那一邊用查到的服務最早、最晚的日期（PLAN §6-4）。"""
    first = criteria.start if criteria.start is not None else page.first
    last = criteria.end if criteria.end is not None else page.last
    return range_text(first, last)
