"""員工輔導與後續追蹤（PLAN1 W2；取捨見 T45，畫面改版見 T47）。

流程：
1. 管理員在服務明細看影片，在播放器目前的位置加時間註記（Add Moment），寫目標、為什麼、追蹤期限，送給那筆服務的
   員工（明細頁影片旁邊的輔導面板，CoachingCard.qml）。員工是那筆服務的 account_id，不是畫面選的。
2. 服務人員在 My Actions 看到目標、為什麼、作為例子的原始服務（時間註記跳到那一部影片的那一秒），按 Got It（確認，
   只代表已回應，不是完成），在對話裡回應。
3. 管理員在 Coaching 頁一次只看到下一步：選同一位員工後來、分析完成的一筆服務當後續服務（還沒有就等，可以 Check Again），
   看過之後 Mark Complete（寫結論）或 Keep Practicing（寫原因、新的目標和期限；歷史留著舊的值）。
   完成一定要有後續服務和結論；AI 分數、員工的確認都不會讓行動完成。

資料庫：三張 tbl_coaching_* 只經由 sp_coaching_* 讀寫（migrations/006_coaching.sql）。每個程序帶呼叫者的 account_id，
由資料庫決定服務人員只看得到自己的、只能回應和確認，管理員看得到全部。寫入包在 START TRANSACTION … COMMIT 裡
（中途失敗整筆 rollback）；不能做的事，程序用 SIGNAL 回報 coaching:<原因>（errno 1644），這裡換成英文。
主管的修改帶著畫面上的 version（樂觀鎖）：兩個管理員改同一項，後到的拿到「changed elsewhere」和 Reload。

CoachingModel（context property coachingModel，兩端各一個）：
- 清單（listModel、listState）在頁面打開時讀一次（pageOpened）；不輪詢（launch 的往返預算不變，T45 第 4-6 節）。
  管理員的清單分 Open、Completed（、Cancelled）三個分頁，上面一行是兩個指標：有後續證據的完成比例、回顧時間
  （目標建立到完成的天數中位數）。還沒有後續服務的進行中行動，清單的工作順便問一次有沒有能選的後續服務
  （每項 1 個往返），標籤才分得出「Pick a Follow-up」和「Waiting for a Later Service」（T47 第 3 節）。
- 明細（current）：選一項時讀一次（1 個往返、3 個結果集）；寫入之後在同一個工作裡重新讀。管理員打開還沒有後續服務的
  進行中行動時，每次都重查能選的後續服務（followUpOptions，含員工的分數）：舊版只查一次，之後一直是空的（T47 第 2 節）。
- 明細頁的輔導面板（card、draft）：和這筆服務有關的行動、這筆影片上的時間註記，以及新目標的草稿（存在這裡，
  換頁再回來還在）。Add Moment 先打開一個說明欄（pending，時間是按下去的那一秒）；Send Goal 時還開著的說明
  先存成一個時間註記，不會默默丟掉（T47 第 1 節）。從 Coaching 打開的服務，面板對準那一項（cardFocusId）：
  時間註記直接加到那一項、存進資料庫。
- 打開服務（openService）：只接受目前讀到的行動裡的服務（原始、後續、看過的後續），發 openServiceRequested，
  外殼換到明細頁、影片載入後跳到那一秒。服務人員因此不能經由行動打開別人的服務。
- 所有資料庫工作在 db lane（CALL 用 execute，一個查詢一個往返），文字（英文，PLAN D1）在 GUI 執行緒產生。
"""
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

import mysql.connector
from PySide6.QtCore import QCoreApplication, Property, QObject, Signal, Slot

from myapp.notifications import user_message
from myapp.service_list import MONTHS, RowListModel, clock_text, format_time

logger = logging.getLogger(__name__)

OPEN, COMPLETED, CANCELLED = "open", "completed", "cancelled"
OVERDUE = "overdue"          # 只用在狀態標籤：進行中、期限已過
TABS = (OPEN, COMPLETED, CANCELLED)   # 管理員清單的分頁
GOAL_MAX = 300               # tbl_coaching_action.goal
TEXT_MAX = 2000              # 評語、回應、結論、原因
NOTE_MAX = 300               # 時間註記的說明
DEFAULT_DUE_DAYS = 14        # 新目標的期限預設是兩週後
ELIGIBLE_MAX = 50            # 清單的工作最多問幾項「有沒有能選的後續服務」（每項 1 個往返）
SIGNAL_ERRNO = 1644          # SIGNAL SQLSTATE '45000' 的 errno
DUPLICATE_KEY = 1062

# TaskRunner 的 key
LIST_KEY = "coaching:list"
DETAIL_KEY = "coaching:detail"
CARD_KEY = "coaching:card"
OPTIONS_KEY = "coaching:options"
CREATE_KEY = "coaching:create"
# 通知上的動作：Retry（再送一次失敗的寫入）、Reload（別人改過了：重新讀）、Open（換到那一項）
RETRY_ACTION = "coaching-retry"
RELOAD_ACTION = "coaching-reload"
OPEN_ACTION = "coaching-open"
VIEWS = ("coaching", "actions", "card")  # 管理員的 Coaching 頁、服務人員的 My Actions、明細頁的輔導面板
ADMIN_PAGE = "admin_coaching_page.qml"
STAFF_PAGE = "server_actions_page.qml"

LIST_SQL = "CALL sp_coaching_list(%s)"
DETAIL_SQL = "CALL sp_coaching_detail(%s, %s)"
FOR_SERVICE_SQL = "CALL sp_coaching_for_service(%s, %s)"
OPTIONS_SQL = "CALL sp_coaching_follow_up_options(%s, %s)"
CREATE_SQL = "CALL sp_coaching_create(%s, %s, %s, %s, %s, %s)"
ADD_MARKER_SQL = "CALL sp_coaching_add_marker(%s, %s, %s, %s, %s)"
REMOVE_MARKER_SQL = "CALL sp_coaching_remove_marker(%s, %s)"
RESPOND_SQL = "CALL sp_coaching_respond(%s, %s, %s)"
ACKNOWLEDGE_SQL = "CALL sp_coaching_acknowledge(%s, %s, %s)"
LINK_SQL = "CALL sp_coaching_link_follow_up(%s, %s, %s, %s)"
COMPLETE_SQL = "CALL sp_coaching_complete(%s, %s, %s, %s)"
ADJUST_SQL = "CALL sp_coaching_adjust(%s, %s, %s, %s, %s, %s)"
CANCEL_SQL = "CALL sp_coaching_cancel(%s, %s, %s, %s)"
# 能選的後續服務旁邊的員工分數（設計稿「Staff 84.0 Fair」）：adam_app 本來就能讀 tbl_service_score（Services 頁）
SCORES_SQL = "SELECT service_id, server_total FROM tbl_service_score WHERE service_id IN ({})"

# 清單每一列的角色（CoachingView.qml）
ACTION_ROLES = ("actionId", "goal", "staffName", "initials", "dueText", "dueAlert", "chipState", "status")


def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：經過 translate；%1、%2… 一次換成 values（像 QString::arg）。"""
    text = QCoreApplication.translate("Coaching", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def write_key(action_id):
    """同一項行動同時只有一個寫入（防重複送出，ENG-14）。"""
    return f"coaching:write:{action_id}"


def toast_key(action_id):
    return f"coaching:{action_id}"


# ---------------------------------------------------------------- 資料（db lane 回傳的列）


def _text(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return "" if value is None else str(value)


def _day(value):
    return value.date() if isinstance(value, datetime) else value


def _score(value):
    """員工的總分：-1（沒有資料）、NULL 是 None。"""
    return None if value is None or float(value) < 0 else float(value)


@dataclass(frozen=True)
class Action:
    """sp_coaching_list、sp_coaching_detail 的一列（還沒格式化）。"""
    action_id: str
    source_service_id: str
    staff_id: str
    goal: str
    due_date: date
    status: str = OPEN
    source_start: datetime = None
    source_end: datetime = None
    staff_name: str = ""
    staff_username: str = ""
    created_by: str = ""
    created_by_name: str = ""
    follow_up_service_id: str = ""
    follow_up_start: datetime = None
    acknowledged_at: datetime = None
    concluded_by_name: str = ""
    concluded_at: datetime = None
    created_at: datetime = None
    updated_at: datetime = None
    version: int = 1
    last_staff_at: datetime = None
    last_admin_at: datetime = None
    manager_comment: str = ""
    conclusion: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(
            action_id=_text(row["action_id"]), source_service_id=_text(row["source_service_id"]),
            staff_id=_text(row["staff_account_id"]), goal=_text(row["goal"]), due_date=_day(row["due_date"]),
            status=_text(row["action_status"]), source_start=row.get("source_start"),
            source_end=row.get("source_end"), staff_name=_text(row.get("staff_name")),
            staff_username=_text(row.get("staff_username")), created_by=_text(row.get("created_by")),
            created_by_name=_text(row.get("created_by_name")),
            follow_up_service_id=_text(row.get("follow_up_service_id")), follow_up_start=row.get("follow_up_start"),
            acknowledged_at=row.get("acknowledged_at"), concluded_by_name=_text(row.get("concluded_by_name")),
            concluded_at=row.get("concluded_at"), created_at=row.get("created_at"), updated_at=row.get("updated_at"),
            version=int(row.get("version") or 1), last_staff_at=row.get("last_staff_at"),
            last_admin_at=row.get("last_admin_at"), manager_comment=_text(row.get("manager_comment")),
            conclusion=_text(row.get("conclusion")))

    @property
    def is_open(self):
        return self.status == OPEN


@dataclass(frozen=True)
class Marker:
    """時間註記：seconds 從那一筆服務自己的開頭（service_start）算。"""
    marker_id: int
    service_id: str
    seconds: int
    note: str = ""
    author_name: str = ""
    created_at: datetime = None
    service_start: datetime = None
    action_id: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(marker_id=int(row["marker_id"]), service_id=_text(row.get("service_id")),
                   seconds=int(row["offset_seconds"]), note=_text(row.get("note")),
                   author_name=_text(row.get("created_by_name")), created_at=row.get("created_at"),
                   service_start=row.get("service_start"), action_id=_text(row.get("action_id")))


@dataclass(frozen=True)
class Event:
    """歷史的一列（舊的在前）。"""
    event_id: int
    kind: str
    author_id: str
    author_name: str
    author_role: str
    body: str = ""
    goal_before: str = ""
    goal_after: str = ""
    due_before: date = None
    due_after: date = None
    service_id: str = ""
    service_start: datetime = None
    marker_seconds: int = None
    created_at: datetime = None

    @classmethod
    def from_row(cls, row):
        seconds = row.get("marker_seconds")
        return cls(event_id=int(row["event_id"]), kind=_text(row["event_type"]), author_id=_text(row["author_id"]),
                   author_name=_text(row.get("author_name")), author_role=_text(row.get("author_role")),
                   body=_text(row.get("body")), goal_before=_text(row.get("goal_before")),
                   goal_after=_text(row.get("goal_after")), due_before=_day(row.get("due_before")),
                   due_after=_day(row.get("due_after")), service_id=_text(row.get("service_id")),
                   service_start=row.get("service_start"),
                   marker_seconds=None if seconds is None else int(seconds), created_at=row.get("created_at"))


@dataclass(frozen=True)
class Detail:
    action: Action
    markers: tuple = ()
    events: tuple = ()


@dataclass(frozen=True)
class ServiceAction:
    """明細頁輔導面板的一項：relation 是 source（從這筆訂的）、follow_up（這筆是後續服務）、reviewed（調整時看過）。"""
    action_id: str
    goal: str
    staff_name: str
    due_date: date
    status: str
    relation: str
    version: int = 1
    follow_up_service_id: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(action_id=_text(row["action_id"]), goal=_text(row["goal"]), staff_name=_text(row.get("staff_name")),
                   due_date=_day(row["due_date"]), status=_text(row["action_status"]),
                   relation=_text(row["relation"]), version=int(row.get("version") or 1),
                   follow_up_service_id=_text(row.get("follow_up_service_id")))


@dataclass(frozen=True)
class Snapshot:
    """寫入之後在同一個工作裡重新讀的畫面：沒有開著的是 None。"""
    actions: tuple = None
    detail: object = None        # Detail；那一項看不到了是 False
    card: tuple = None           # (ServiceAction, …), (Marker, …)
    result: tuple = ()           # 寫入那個 CALL 回傳的列（加時間註記：marker_id）


# ---------------------------------------------------------------- 資料庫（只能在 db lane 的工作裡用）


def _rows(cursor):
    """這個結果集的列，然後跳到下一個結果集（CALL 最後還有一個狀態的結果集）。"""
    rows = cursor.fetchall() if cursor.with_rows else []
    cursor.nextset()
    return rows


def _drain(cursor):
    while cursor.nextset():
        pass


def list_actions(db, caller):
    """CALL sp_coaching_list（1 個往返）。"""
    with db.cursor(dictionary=True) as cursor:
        return _read_list(cursor, caller)


def _read_list(cursor, caller):
    cursor.execute(LIST_SQL, (caller,))
    actions = tuple(Action.from_row(row) for row in cursor.fetchall())
    _drain(cursor)
    return actions


def list_with_eligibility(db, caller):
    """管理員的清單（1 個往返），加上還沒有後續服務的進行中行動各有幾筆能選的後續服務（每項 1 個往返，
    最多 ELIGIBLE_MAX 項）：回傳 (行動, {action_id: 筆數})。規則只在 sp_coaching_follow_up_options 裡，
    清單的標籤和明細的「Pick a Follow-up」一定一致（T47 第 3 節）。"""
    with db.cursor(dictionary=True) as cursor:
        actions = _read_list(cursor, caller)
        eligible = {}
        for action in [action for action in actions if action.is_open and not action.follow_up_service_id]:
            if len(eligible) >= ELIGIBLE_MAX:
                break
            cursor.execute(OPTIONS_SQL, (caller, action.action_id))
            eligible[action.action_id] = len(cursor.fetchall() if cursor.with_rows else [])
            _drain(cursor)
        return actions, eligible


def load_detail(db, caller, action_id):
    """CALL sp_coaching_detail（1 個往返、3 個結果集）。看不到（別人的、不存在）是 None。"""
    with db.cursor(dictionary=True) as cursor:
        return _read_detail(cursor, caller, action_id)


def _read_detail(cursor, caller, action_id):
    cursor.execute(DETAIL_SQL, (caller, action_id))
    actions = _rows(cursor)
    markers = _rows(cursor)
    events = cursor.fetchall() if cursor.with_rows else []
    _drain(cursor)
    if not actions:
        return None
    return Detail(Action.from_row(actions[0]), tuple(Marker.from_row(row) for row in markers),
                  tuple(Event.from_row(row) for row in events))


def load_card(db, caller, service_id):
    """CALL sp_coaching_for_service（1 個往返、2 個結果集）：(行動, 這筆影片上的時間註記)。"""
    with db.cursor(dictionary=True) as cursor:
        return _read_card(cursor, caller, service_id)


def _read_card(cursor, caller, service_id):
    cursor.execute(FOR_SERVICE_SQL, (caller, service_id))
    actions = _rows(cursor)
    markers = cursor.fetchall() if cursor.with_rows else []
    _drain(cursor)
    return (tuple(ServiceAction.from_row(row) for row in actions),
            tuple(Marker.from_row({**row, "service_id": service_id}) for row in markers))


def load_follow_up_options(db, caller, action_id):
    """能當後續服務的服務：CALL sp_coaching_follow_up_options（1 個往返），有的話再讀它們的員工總分（1 個往返）。
    回傳 [(service_id, service_start, 分數或 None)]，新的在前。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(OPTIONS_SQL, (caller, action_id))
        rows = [(_text(row["service_id"]), row["service_start"]) for row in cursor.fetchall()]
        _drain(cursor)
        scores = {}
        if rows:
            cursor.execute(SCORES_SQL.format(", ".join(["%s"] * len(rows))), [service_id for service_id, _ in rows])
            scores = {_text(row["service_id"]): _score(row["server_total"]) for row in cursor.fetchall()}
        return [(service_id, start, scores.get(service_id)) for service_id, start in rows]


@dataclass(frozen=True)
class Views:
    """寫入之後要重新讀哪些畫面。"""
    list: bool = False
    detail: str = ""      # 選到的那一項
    card: str = ""        # 明細頁的那一筆服務


def _read_views(cursor, caller, views):
    actions = _read_list(cursor, caller) if views.list else None
    detail = None
    if views.detail:
        detail = _read_detail(cursor, caller, views.detail) or False
    card = _read_card(cursor, caller, views.card) if views.card else None
    return actions, detail, card


def write(db, caller, statements, views):
    """一個交易寫入（START TRANSACTION、每個 CALL、COMMIT），再讀開著的畫面。中途失敗 DbLane 會 rollback。

    statements：[(sql, params)]。往返：2＋CALL 的數量＋重新讀的畫面。回傳 Snapshot（result 是最後一個 CALL 回傳的列）。
    """
    with db.cursor(dictionary=True) as cursor:
        cursor.execute("START TRANSACTION")
        result = []
        for sql, params in statements:
            cursor.execute(sql, params)
            result = cursor.fetchall() if cursor.with_rows else []
            _drain(cursor)
        cursor.execute("COMMIT")
        actions, detail, card = _read_views(cursor, caller, views)
        return Snapshot(actions, detail, card, tuple(result))


def create(db, caller, action_id, service_id, goal, comment, due, markers, views):
    """建立一項行動和它的時間註記（同一個交易），再讀開著的畫面。

    重試時用同一個 action_id：上一次其實寫好了（COMMIT 成功、結果沒回來）就撞到主鍵（1062），
    這時看得到那一項就當作已經建立，不再寫一次（多 2 個往返）。
    """
    statements = [(CREATE_SQL, (caller, action_id, service_id, goal, comment, due))]
    statements += [(ADD_MARKER_SQL, (caller, action_id, service_id, seconds, note)) for seconds, note in markers]
    try:
        return write(db, caller, statements, views)
    except mysql.connector.Error as error:
        if error.errno != DUPLICATE_KEY:
            raise
        with db.cursor(dictionary=True) as cursor:
            existing = _read_detail(cursor, caller, action_id)
            if existing is None:
                raise
            actions, detail, card = _read_views(cursor, caller, views)
            return Snapshot(actions, detail, card)


# ---------------------------------------------------------------- 錯誤（程序的 SIGNAL、連線）


def problem_code(exc):
    """程序用 SIGNAL 回報的原因（coaching:changed → "changed"）；不是的話是空字串。"""
    message = getattr(exc, "msg", "") or ""
    if getattr(exc, "errno", None) == SIGNAL_ERRNO and message.startswith("coaching:"):
        return message.split(":", 1)[1]
    return ""


def problem_text(code):
    """程序拒絕的原因 → 一句英文（DS-50：先講發生什麼，再講下一步）。"""
    return {
        "forbidden": _tr("Your account can't make this change."),
        "not_found": _tr("This goal isn't available. It may not be yours, or it was removed."),
        "not_open": _tr("This goal is already closed. Reload to see what changed."),
        "changed": _tr("Someone else changed this goal. Reload to see the latest, then try again."),
        "invalid": _tr("Some details are missing. Check the fields and try again."),
        "not_staff": _tr("This service wasn't recorded by a staff account, so it can't have a goal."),
        "follow_up": _tr("Choose a later service by the same staff member that has finished analysis."),
        "no_follow_up": _tr("Pick a follow-up service before you mark this goal complete."),
        "marker_service": _tr("Moments can only go on the example service or its current follow-up."),
    }.get(code, _tr("This change couldn't be saved."))


def reason_text(exc):
    """資料庫的錯誤：「Can't reach the database. Check your network and try again.」"""
    _, title, detail, _ = user_message(exc)
    return f"{title}. {detail}" if detail else title


def is_retryable(exc):
    return not problem_code(exc) and user_message(exc)[3]


def technical_text(exc):
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# ---------------------------------------------------------------- 畫面上的文字（GUI 執行緒）


def clock(seconds):
    """時間註記：「02:15」；一小時以上「1:02:15」（和明細頁播放器的時間一樣）。"""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def short_day(value, today):
    """「Nov 15」；不是今年的加年份「Nov 15, 2027」。"""
    if value is None:
        return ""
    value = _day(value)
    text = f"{MONTHS[value.month - 1]} {value.day}"
    return text if value.year == today.year else f"{text}, {value.year}"


def moment_text(value, today):
    """「Nov 1, 09:40」；不是今年的加年份。"""
    if value is None:
        return ""
    return f"{short_day(value, today)}, {clock_text(value)}"


def first_name(name):
    """「Mia Chen」→「Mia」（對話、按鈕上的「Send Goal to Mia」）。"""
    parts = (name or "").split()
    return parts[0] if parts else ""


def initials(name):
    """頭像的字：「Mia Chen」→「MC」、「Leo」→「L」；中文名字是第一個字。"""
    parts = (name or "").split()
    if not parts:
        return ""
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()


def is_overdue(action, today):
    return action.is_open and action.due_date is not None and action.due_date < today


def unread(action):
    """服務人員還沒確認目前的目標（新的、或剛調整過）。"""
    return action.is_open and action.acknowledged_at is None


def admin_chip(action, today, eligible=None):
    """管理員清單的標籤（一次只說下一步）：overdue、review（有後續服務，等你看）、pick（有能選的後續服務）、
    waiting（還沒有）、completed、cancelled；還不知道有沒有能選的（超過 ELIGIBLE_MAX、還沒查）是 open。"""
    if not action.is_open:
        return action.status
    if is_overdue(action, today):
        return OVERDUE
    if action.follow_up_service_id:
        return "review"
    if eligible is None:
        return OPEN
    return "pick" if eligible > 0 else "waiting"


def staff_chip(action):
    """服務人員清單的標籤：new（還沒按 Got It）、progress、completed、cancelled。"""
    if not action.is_open:
        return action.status
    return "new" if unread(action) else "progress"


def due_text(action, today):
    """期限：「Due Nov 15」「Due today」；結案的寫結案的日子：「Done Nov 10」「Cancelled Nov 3」。"""
    if action.status == COMPLETED:
        return _tr("Done %1", short_day(action.concluded_at, today))
    if action.status == CANCELLED:
        return _tr("Cancelled %1", short_day(action.concluded_at, today))
    if action.due_date is None:
        return ""
    if action.due_date == today:
        return _tr("Due today")
    return _tr("Due %1", short_day(action.due_date, today))


def review_days(action):
    """回顧時間（T45 第 3 節）：目標建立到結案為完成的天數；不是完成的是 None。"""
    if action.status != COMPLETED or action.created_at is None or action.concluded_at is None:
        return None
    return max(0.0, (action.concluded_at - action.created_at).total_seconds() / 86400)


def days_text(days):
    """「9 days」「1 day」「Under 1 day」。"""
    if days is None:
        return ""
    if days < 1:
        return _tr("Under 1 day")
    whole = int(days + 0.5)
    return _tr("1 day") if whole == 1 else _tr("%1 days", whole)


def metrics(actions):
    """兩個指標（PLAN1 W2 的完成條件）：(完成數, 分母, 回顧時間的中位數天數或 None)。
    分母是撤回以外的行動：撤回的不算失敗也不算完成。完成一定有後續服務（資料庫的 CHECK），所以完成數就是
    「有後續證據的完成」。"""
    counted = [action for action in actions if action.status != CANCELLED]
    completed = [action for action in counted if action.status == COMPLETED]
    days = [value for value in (review_days(action) for action in completed) if value is not None]
    return len(completed), len(counted), (median(days) if days else None)


def share_percent(completed, total):
    """「38%」（四捨五入）；沒有行動是「—」。"""
    if total == 0:
        return "—"
    return _tr("%1%", int(completed * 100 / total + 0.5))


def shared_names(actions):
    """清單裡同名、但不是同一個人的名字：加登入帳號才分得出來（PLAN 13.7）。"""
    owners = {}
    for action in actions:
        owners.setdefault(action.staff_name, set()).add(action.staff_id)
    return frozenset(name for name, ids in owners.items() if len(ids) > 1)


def staff_text(action, shared=frozenset()):
    if action.staff_name in shared and action.staff_username:
        return _tr("%1 (%2)", action.staff_name, action.staff_username)
    return action.staff_name or action.staff_username


def action_row(action, admin, today, shared=frozenset(), eligible=None):
    """Action → 清單模型的一列（ACTION_ROLES）。eligible：能選的後續服務有幾筆（管理員；不知道是 None）。"""
    return {
        "actionId": action.action_id, "goal": action.goal, "staffName": staff_text(action, shared),
        "initials": initials(action.staff_name or action.staff_username), "dueText": due_text(action, today),
        "dueAlert": is_overdue(action, today),
        "chipState": admin_chip(action, today, eligible) if admin else staff_chip(action),
        "status": action.status,
    }


def marker_item(marker, editable=False):
    return {"markerId": marker.marker_id, "serviceId": marker.service_id, "seconds": marker.seconds,
            "clock": clock(marker.seconds), "note": marker.note, "editable": editable}


def role_text(role):
    return _tr("Admin") if role == "admin" else _tr("Staff")


def conversation_items(events, viewer, today):
    """歷史（舊的在前）→ 對話：雙方的回應是對話框（自己的在右邊），其他的事是置中的一行小字（設計稿 Conversation）。
    時間註記的加、拿掉不放進對話（在 Moments 裡看得到），資料庫照樣留著。viewer：正在看的帳號，自己寫的叫「You」。"""
    items = []
    linked = ""   # 目前連結的後續服務：連結新的就是「換掉」
    for event in events:
        mine = event.author_id == viewer
        who = _tr("You") if mine else first_name(event.author_name) or role_text(event.author_role)
        base = {"eventId": event.event_id, "mine": mine, "whenText": moment_text(event.created_at, today)}
        if event.kind == "response":
            author = _tr("You") if mine else event.author_name or role_text(event.author_role)
            items.append({**base, "kind": "message", "text": event.body, "author": author, "lines": []})
            continue
        service = short_day(event.service_start, today) or event.service_id
        lines = []
        if event.kind == "created":
            text = _tr("%1 set the goal", who)
        elif event.kind == "acknowledged":
            text = _tr("%1 read the goal", who)
        elif event.kind == "follow_up_linked":
            text = (_tr("%1 changed the follow-up to the %2 service", who, service) if linked
                    else _tr("%1 picked the %2 service as the follow-up", who, service))
            linked = event.service_id
        elif event.kind == "adjusted":
            text = (_tr("%1 asked for more practice after the %2 service", who, service) if event.service_id
                    else _tr("%1 edited the goal", who))
            lines = [event.body]
            if event.goal_before != event.goal_after:
                lines.append(_tr("New goal: %1", event.goal_after))
            if event.due_before != event.due_after:
                lines.append(_tr("Follow up by %1", short_day(event.due_after, today)))
            linked = ""
        elif event.kind == "completed":
            text, lines = _tr("%1 marked the goal complete", who), [event.body]
        elif event.kind == "cancelled":
            text, lines = _tr("%1 cancelled the goal", who), [event.body]
        else:   # 時間註記：在 Moments 裡
            continue
        items.append({**base, "kind": "event", "text": text, "author": "",
                      "lines": [line for line in lines if line]})
    return items


def staff_replied(detail):
    """員工在目前這一版的目標之後回應或按了 Got It（調整之後重新算）。"""
    if detail.action.acknowledged_at is not None:
        return True
    replied = False
    for event in detail.events:
        if event.kind == "adjusted":
            replied = False
        elif event.author_role == "staff" and event.kind in ("response", "acknowledged"):
            replied = True
    return replied


def progress_steps(detail, admin):
    """四個步驟（設計稿）：管理員 Goal set → {員工} replied → Follow-up → Your review；
    服務人員 Goal set → You replied → Later service → Review。state：done、current（進行中的第一個沒做的）、todo。"""
    action = detail.action
    staff = first_name(action.staff_name) or action.staff_username
    done = [True, staff_replied(detail), bool(action.follow_up_service_id), action.status == COMPLETED]
    labels = ([_tr("Goal set"), _tr("%1 replied", staff), _tr("Follow-up"), _tr("Your review")] if admin
              else [_tr("Goal set"), _tr("You replied"), _tr("Later service"), _tr("Review")])
    current = next((index for index, finished in enumerate(done) if not finished), -1) if action.is_open else -1
    return [{"label": label, "state": "done" if finished else "current" if index == current else "todo"}
            for index, (label, finished) in enumerate(zip(labels, done))]


def service_blocks(detail, today):
    """這一項的服務和各自影片上的時間註記：原始服務（例子）、目前的後續服務、調整時看過的（新的在前）。"""
    action = detail.action
    by_service = {}
    for marker in detail.markers:
        by_service.setdefault(marker.service_id, []).append(marker)

    def block(service_id, start, relation, reason=""):
        day = short_day(start, today) or service_id
        label = {"source": _tr("Example · %1", day), "follow_up": _tr("Follow-up · %1", day),
                 "reviewed": _tr("Reviewed · %1", day)}[relation]
        return {"serviceId": service_id, "startTime": format_time(start), "relation": relation, "label": label,
                "whenText": moment_text(start, today), "reason": reason,
                "markers": [marker_item(marker) for marker in by_service.get(service_id, [])]}

    blocks = [block(action.source_service_id, action.source_start, "source")]
    if action.follow_up_service_id:
        blocks.append(block(action.follow_up_service_id, action.follow_up_start, "follow_up"))
    seen = set()
    for event in reversed(detail.events):  # 新的在前；同一筆只列一次，原因是最新的
        if event.kind == "adjusted" and event.service_id and event.service_id not in seen:
            seen.add(event.service_id)
            blocks.append(block(event.service_id, event.service_start, "reviewed", event.body))
    return blocks


def detail_map(detail, admin, viewer, today):
    """明細（Detail）→ QML 的 current：目標、為什麼、步驟、服務和時間註記、結論、對話、能做的事。"""
    action = detail.action
    concluded = ""
    if action.status == COMPLETED:
        concluded = _tr("Completed by %1 · %2", action.concluded_by_name, short_day(action.concluded_at, today))
        days = review_days(action)
        if days is not None:
            concluded = _tr("%1 · %2 after the goal was set", concluded, days_text(days))
    elif action.status == CANCELLED:
        concluded = _tr("Cancelled by %1 · %2", action.concluded_by_name, short_day(action.concluded_at, today))
    due = due_text(action, today)
    admin_name = action.created_by_name or _tr("your admin")
    follow_up = {}
    if action.follow_up_service_id:
        follow_up = {"serviceId": action.follow_up_service_id, "startTime": format_time(action.follow_up_start),
                     "whenText": moment_text(action.follow_up_start, today)}
    return {
        "actionId": action.action_id, "goal": action.goal, "comment": action.manager_comment,
        "status": action.status, "isOpen": action.is_open, "version": action.version,
        "staffName": staff_text(action), "staffFirst": first_name(action.staff_name) or action.staff_username,
        "initials": initials(action.staff_name or action.staff_username),
        "adminName": admin_name, "adminFirst": first_name(action.created_by_name) or admin_name,
        "setText": _tr("Set %1 · %2", short_day(action.created_at, today), due),
        "fromText": _tr("From %1 · %2", admin_name, due),
        "dueText": due, "dueAlert": is_overdue(action, today),
        "unread": unread(action), "canAcknowledge": not admin and unread(action), "canRespond": action.is_open,
        "followUp": follow_up, "services": service_blocks(detail, today), "steps": progress_steps(detail, admin),
        "conclusion": action.conclusion, "concludedText": concluded,
        "closedDay": short_day(action.concluded_at, today),
        "conversation": conversation_items(detail.events, viewer, today),
    }


def relation_text(relation):
    return {"source": _tr("Example"), "follow_up": _tr("Follow-up"), "reviewed": _tr("Reviewed")}.get(relation, "")


def card_items(actions, markers, today):
    """明細頁輔導面板：和這筆服務有關的每一項，加上這部影片上屬於它的時間註記。canMark：進行中，而且這筆是它的
    原始服務或目前的後續服務（資料庫只讓時間註記加在這兩部影片上）。"""
    by_action = {}
    for marker in markers:
        by_action.setdefault(marker.action_id, []).append(marker)
    items = []
    for action in actions:
        open_here = action.status == OPEN and action.relation in ("source", "follow_up")
        as_action = Action(action.action_id, "", "", action.goal, action.due_date, action.status,
                           follow_up_service_id=action.follow_up_service_id)
        moments = [marker_item(marker, open_here) for marker in by_action.get(action.action_id, [])]
        items.append({"actionId": action.action_id, "goal": action.goal, "staffName": action.staff_name,
                      "relation": action.relation, "relationText": relation_text(action.relation),
                      "chipState": admin_chip(as_action, today),
                      "dueText": due_text(as_action, today) if action.status == OPEN else "",
                      "canMark": open_here, "markers": moments})
    return items


# ---------------------------------------------------------------- 表單的檢查（GUI 執行緒，不碰網路）


def parse_due(text, today):
    """期限：「YYYY-MM-DD」→ (date 或 None, 錯誤)。今天或之後才可以。"""
    text = (text or "").strip()
    if not text:
        return None, _tr("Choose a follow-up date.")
    try:
        value = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None, _tr("Enter the date as YYYY-MM-DD, for example 2026-11-15.")
    if value < today:
        return None, _tr("Choose today or a later date.")
    return value, ""


def required(text, limit, empty_message):
    """必填的文字：去掉頭尾的空白；空的或太長回傳錯誤。"""
    text = (text or "").strip()
    if not text:
        return text, empty_message
    if len(text) > limit:
        return text, _tr("Keep this under %1 characters.", limit)
    return text, ""


def validate_goal(goal, comment, due_text_value, today):
    """新目標：回傳 (值 dict 或 None, {"goal", "comment", "due"} 的錯誤)。為什麼是必填的（T47 第 6 節）。"""
    goal, goal_error = required(goal, GOAL_MAX, _tr("Enter the goal the staff member should practise."))
    comment, comment_error = required(comment, TEXT_MAX, _tr("Explain why you set this goal."))
    due, due_error = parse_due(due_text_value, today)
    errors = {"goal": goal_error, "comment": comment_error, "due": due_error}
    if any(errors.values()):
        return None, errors
    return {"goal": goal, "comment": comment, "due": due}, errors


def default_due(today):
    return (today + timedelta(days=DEFAULT_DUE_DAYS)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 模型


class CoachingModel(QObject):
    """輔導（context property coachingModel）：管理員的 Coaching 頁和明細頁的輔導面板、服務人員的 My Actions。

    services 是 AppServices；None（直接建立外殼、不經過 launch() 的測試）時什麼都不做。today：測試固定日期用。
    """

    listChanged = Signal()           # listState、清單、分頁、指標
    detailChanged = Signal()         # selectedId、current、detailState
    cardChanged = Signal()           # 明細頁的輔導面板（行動、時間註記、對準哪一項）
    draftChanged = Signal()          # 新目標的草稿：時間註記、打開的說明欄、送出了沒
    draftReset = Signal()            # 草稿整份換了（換一筆服務、送出了、Set Another Goal）：面板照草稿重填欄位
    busyChanged = Signal()           # creating、saving
    errorsChanged = Signal()         # 表單和動作的錯誤
    optionsChanged = Signal()        # 能選的後續服務
    saved = Signal(str, str)         # (action_id, 哪一種寫入)：對話框關掉、欄位清空
    openServiceRequested = Signal(str, str, int, str)  # 服務 ID、開始時間、第幾秒（-1＝不跳）、回去的頁面
    openActionRequested = Signal(str)  # 「Open in Coaching」、通知的 Open：外殼換到清單頁、選這一項

    def __init__(self, services, account_id, admin=False, today=None, parent=None):
        super().__init__(parent)
        self.services = services
        self.account_id = account_id
        self._admin = bool(admin)
        self._today = today          # 測試用；None 是這台電腦的日期
        self._list = RowListModel(ACTION_ROLES, "actionId", self)
        self._actions = ()           # 全部看得到的行動
        self._eligible = {}          # action_id → 能選的後續服務有幾筆（管理員）
        self._shared = frozenset()
        self._list_state = "idle"
        self._list_busy = False
        self._error_detail = self._error_technical = ""
        self._failures = 0
        self._tab = OPEN
        self._selected = ""
        self._detail = None          # Detail
        self._detail_state = "idle"  # idle、loading、ready、missing、error
        self._detail_error = self._detail_technical = ""
        self._card_service = ""
        self._card = ((), ())
        self._card_state = "idle"
        self._card_focus = ""        # 面板對準的行動（從 Coaching 打開的服務）；空的是新目標
        self._focus_next = None      # (service_id, action_id)：openService 之後，那一筆的面板對準這一項
        self._drafts = {}            # service_id → 草稿
        self._create_errors = {"goal": "", "comment": "", "due": "", "form": ""}
        self._action_error = {"actionId": "", "op": "", "text": ""}
        self._options_for = ""
        self._options = []
        self._options_state = "idle"
        self._options_busy = False
        self._creating = False
        self._saving = set()
        self._retries = {}           # action_id（或 CREATE_KEY）→ 失敗的寫入，通知上的 Retry 再送一次
        self._views = Counter()
        self._stopped = False
        if services is not None:
            services.notifier.actionTriggered.connect(self._on_action)

    def today(self):
        return self._today or date.today()

    # ------------------------------------------------------------ QML 讀的屬性：清單

    @Property(bool, constant=True)
    def isAdmin(self):
        return self._admin

    @Property(QObject, constant=True)
    def listModel(self):
        return self._list

    @Property(str, notify=listChanged)
    def listState(self):
        """idle（還沒查過）、loading（第一次查）、ready、empty、error。"""
        return self._list_state

    @Property(bool, notify=listChanged)
    def refreshing(self):
        return self._list_busy and self._list_state in ("ready", "empty")

    @Property(str, notify=listChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=listChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=listChanged)
    def failureCount(self):
        return self._failures

    @Property(int, notify=listChanged)
    def totalCount(self):
        return len(self._actions)

    @Property(int, notify=listChanged)
    def shownCount(self):
        return self._list.rowCount()

    @Property(str, notify=listChanged)
    def countText(self):
        """服務人員清單的標題：「2 goals」「1 goal」。"""
        total = len(self._actions)
        return _tr("1 goal") if total == 1 else _tr("%1 goals", total)

    @Property(str, notify=listChanged)
    def tab(self):
        """管理員清單的分頁：open、completed、cancelled（服務人員沒有分頁，一律 open）。"""
        return self._tab

    def _count(self, status):
        return sum(1 for action in self._actions if action.status == status)

    @Property(int, notify=listChanged)
    def openCount(self):
        return self._count(OPEN)

    @Property(int, notify=listChanged)
    def completedCount(self):
        return self._count(COMPLETED)

    @Property(int, notify=listChanged)
    def cancelledCount(self):
        return self._count(CANCELLED)

    # 最上面一行的兩個指標（管理員；全部的行動）
    @Property(str, notify=listChanged)
    def completedShare(self):
        """有後續證據的完成比例：「40%」；沒有行動是「—」。"""
        completed, total, _ = metrics(self._actions)
        return share_percent(completed, total)

    @Property(str, notify=listChanged)
    def completedOf(self):
        """「2 of 5」（撤回的不算）；沒有行動是空字串。"""
        completed, total, _ = metrics(self._actions)
        return _tr("%1 of %2", completed, total) if total else ""

    @Property(str, notify=listChanged)
    def reviewTimeText(self):
        """回顧時間：目標建立到完成的天數中位數，「8 days」；沒有完成的是「—」。"""
        _, _, days = metrics(self._actions)
        return days_text(days) if days is not None else "—"

    @Property(str, notify=listChanged)
    def openCountText(self):
        return str(self._count(OPEN))

    @Property(int, notify=listChanged)
    def overdueCount(self):
        today = self.today()
        return sum(1 for action in self._actions if is_overdue(action, today))

    # ------------------------------------------------------------ QML 讀的屬性：明細

    @Property(str, notify=detailChanged)
    def selectedId(self):
        return self._selected

    @Property("QVariantMap", notify=detailChanged)
    def current(self):
        """選到的那一項（detail_map）；還沒讀到、看不到是空的。"""
        if self._detail is None or self._detail.action.action_id != self._selected:
            return {}
        return detail_map(self._detail, self._admin, self.account_id, self.today())

    @Property(str, notify=detailChanged)
    def detailState(self):
        return self._detail_state

    @Property(str, notify=detailChanged)
    def detailError(self):
        return self._detail_error

    @Property(str, notify=detailChanged)
    def detailTechnical(self):
        return self._detail_technical

    @Property("QVariantList", notify=optionsChanged)
    def followUpOptions(self):
        """能選的後續服務：[{value, whenText, startTime, score（沒有是 -1）, scoreText}]，新的在前。"""
        today = self.today()
        return [{"value": service_id, "whenText": moment_text(start, today), "startTime": format_time(start),
                 "score": -1 if score is None else score, "scoreText": "" if score is None else f"{score:.1f}"}
                for service_id, start, score in self._options]

    @Property(str, notify=optionsChanged)
    def followUpState(self):
        """idle、loading（第一次查這一項）、ready、empty（還沒有能選的）、error。"""
        return self._options_state

    @Property(str, notify=optionsChanged)
    def followUpFor(self):
        return self._options_for

    @Property(bool, notify=optionsChanged)
    def followUpBusy(self):
        """正在查（包括 Check Again 的重查）：按鈕忙碌。"""
        return self._options_busy

    # ------------------------------------------------------------ QML 讀的屬性：明細頁的輔導面板

    @Property(str, notify=cardChanged)
    def cardState(self):
        return self._card_state

    @Property("QVariantList", notify=cardChanged)
    def cardActions(self):
        return card_items(*self._card, self.today())

    @Property(str, notify=cardChanged)
    def cardFocusId(self):
        """面板對準的行動（時間註記直接加到它）；空的是新目標。只會是能在這部影片上加註記的一項。"""
        return self._card_focus if self._markable(self._card_focus) else ""

    @Property("QVariantMap", notify=draftChanged)
    def draft(self):
        """這筆服務的草稿：goal、comment、due、markers（[{seconds, clock, note}]）、
        pending（打開的說明欄：{open, seconds, clock, note, target}）、sent（剛送出的：{actionId, count}，沒有是 {}）。"""
        draft = self._drafts.get(self._card_service) or self._new_draft()
        pending = draft["pending"]
        return {**{key: draft[key] for key in ("goal", "comment", "due")},
                "markers": [{"seconds": seconds, "clock": clock(seconds), "note": note}
                            for seconds, note in draft["markers"]],
                "pending": ({"open": True, "seconds": pending["seconds"], "clock": clock(pending["seconds"]),
                             "note": pending["note"], "target": pending["target"]} if pending else {"open": False}),
                "sent": dict(draft["sent"] or {})}

    # ------------------------------------------------------------ QML 讀的屬性：忙碌、錯誤

    @Property(bool, notify=busyChanged)
    def creating(self):
        return self._creating

    @Property("QVariantList", notify=busyChanged)
    def saving(self):
        """寫入中的行動（action_id）：那一項的按鈕忙碌。"""
        return sorted(self._saving)

    @Property("QVariantMap", notify=errorsChanged)
    def createErrors(self):
        return dict(self._create_errors)

    @Property("QVariantMap", notify=errorsChanged)
    def actionError(self):
        """最後一次寫入失敗的原因：{actionId, op, text}（對話框、明細、面板顯示）。"""
        return dict(self._action_error)

    # ------------------------------------------------------------ 頁面打開、關掉

    @Slot(str)
    def pageOpened(self, view):
        """Coaching、My Actions、明細頁的輔導面板打開：讀一次。不輪詢（T45 第 4-6 節）。"""
        if view not in VIEWS:
            return
        self._views[view] += 1
        if view == "card":
            self._load_card()
        else:
            self._refresh_list()
            if self._selected:
                self._load_detail(self._selected)

    @Slot(str)
    def pageClosed(self, view):
        if self._views[view] > 0:
            self._views[view] -= 1
        if self._views[view] == 0:
            del self._views[view]

    @Slot()
    def refresh(self):
        """區塊錯誤的 Retry、Reload：清單，選到的那一項也重讀。"""
        self._refresh_list()
        if self._selected:
            self._load_detail(self._selected)

    def stop(self):
        """外殼關掉時：之後才回來的結果不再改畫面（沒有計時器）。"""
        self._stopped = True

    def _list_open(self):
        return bool({"coaching", "actions"} & set(self._views))

    # ------------------------------------------------------------ 清單

    def _refresh_list(self):
        if self.services is None:
            return False
        db, caller = self.services.db, self.account_id
        if self._admin:
            job = lambda token, report: list_with_eligibility(db, caller)  # noqa: E731
        else:
            job = lambda token, report: (list_actions(db, caller), None)  # noqa: E731
        submitted = self.services.tasks.submit("db", LIST_KEY, job, on_ok=self._listed, on_err=self._list_failed)
        if submitted:
            self._list_busy = True
            if self._list_state in ("idle", "error"):
                self._list_state = "loading"
            self.listChanged.emit()
        return submitted

    def _listed(self, result):
        actions, eligible = result
        self._list_busy = False
        self._failures = 0
        self._error_detail = self._error_technical = ""
        if eligible is not None:
            self._eligible = dict(eligible)
        self._apply_list(actions)
        self.listChanged.emit()

    def _apply_list(self, actions):
        self._actions = tuple(actions)
        self._shared = shared_names(self._actions)
        self._list_state = "ready" if self._actions else "empty"
        tab = self._tab_of(self._selected)
        if self._admin and tab and tab != self._tab:  # 選到的那一項換了狀態（例如剛完成）：跟著它換分頁
            self._tab = tab
        self._show_rows()

    def _list_failed(self, exc):
        logger.warning("loading the coaching list failed: %r", exc)
        self._list_busy = False
        if self._list_state in ("ready", "empty"):  # 已經有清單：留著
            self.listChanged.emit()
            return
        self._failures += 1
        self._error_detail = reason_text(exc)
        self._error_technical = technical_text(exc)
        self._list_state = "error"
        self.listChanged.emit()

    def _tab_of(self, action_id):
        return next((action.status for action in self._actions if action.action_id == action_id), "")

    def _shown(self):
        if not self._admin:
            return list(self._actions)
        return [action for action in self._actions if action.status == self._tab]

    def _show_rows(self):
        today = self.today()
        self._list.replace([action_row(action, self._admin, today, self._shared, self._eligible.get(action.action_id))
                            for action in self._shown()])

    @Slot(str)
    def setTab(self, name):
        """管理員的分頁（Open、Completed、Cancelled）：換了就選那一頁的第一項。"""
        if not self._admin or name not in TABS or name == self._tab:
            return
        self._tab = name
        self._show_rows()
        self.listChanged.emit()
        if self._tab_of(self._selected) != name:
            self.select(self.firstActionId())

    @Slot(result=str)
    def firstActionId(self):
        """目前的分頁的第一項；沒有是空字串。頁面一進來選它。"""
        rows = self._list.rows()
        return rows[0]["actionId"] if rows else ""

    # ------------------------------------------------------------ 明細

    @Slot(str)
    def select(self, action_id):
        """選一項：讀它的明細（1 個往返）。同一項再選一次也重讀（拿最新的回應）。不在目前的分頁就換過去。"""
        if action_id != self._selected:
            self._selected = action_id
            self._detail = None
            self._action_error = {"actionId": "", "op": "", "text": ""}
            self.errorsChanged.emit()
        tab = self._tab_of(action_id)
        if self._admin and tab and tab != self._tab:
            self._tab = tab
            self._show_rows()
            self.listChanged.emit()
        if not action_id:
            self._detail_state = "idle"
            self.detailChanged.emit()
            return
        self._load_detail(action_id)

    def _load_detail(self, action_id):
        if self.services is None:
            return False
        db, caller = self.services.db, self.account_id
        submitted = self.services.tasks.submit(
            "db", DETAIL_KEY, lambda token, report: load_detail(db, caller, action_id),
            on_ok=lambda detail: self._detail_loaded(action_id, detail),
            on_err=lambda exc: self._detail_failed(action_id, exc), replace=True)
        if submitted:
            if self._detail is None or self._detail.action.action_id != action_id:
                self._detail_state = "loading"
            self.detailChanged.emit()
        return submitted

    def _detail_loaded(self, action_id, detail):
        if action_id != self._selected:
            return
        self._detail = detail
        self._detail_state = "ready" if detail is not None else "missing"
        self._detail_error = self._detail_technical = ""
        self.detailChanged.emit()
        self._check_follow_ups(detail)

    def _detail_failed(self, action_id, exc):
        if action_id != self._selected:
            return
        logger.warning("loading coaching action %s failed: %r", action_id, exc)
        self._detail_error = reason_text(exc)
        self._detail_technical = technical_text(exc)
        if self._detail is None:
            self._detail_state = "error"
        self.detailChanged.emit()

    def _check_follow_ups(self, detail):
        """管理員看還沒有後續服務的進行中行動：每次都重查能選的後續服務（頁面打開、選一項、寫入之後）。
        舊版只在換一項時查一次，沒有的時候一直是空的、選單不能按（使用者回報的問題，T47 第 2 節）。
        已經有後續服務的不查：要換的時候（Change）由畫面呼叫 loadFollowUps。"""
        if self._admin and detail is not None and detail.action.is_open and not detail.action.follow_up_service_id:
            self.loadFollowUps(detail.action.action_id)

    @Slot(str)
    def requestOpenAction(self, action_id):
        """輔導面板的「Open in Coaching」、通知的 Open：外殼換到清單頁、選這一項（在它的分頁）。"""
        if action_id:
            self.select(action_id)
            self.openActionRequested.emit(action_id)

    # ------------------------------------------------------------ 打開服務（時間註記跳到那一秒）

    def _known_services(self, action_id):
        """這一項看得到的服務（原始、後續、調整時看過的）：{service_id: 開始時間}。"""
        known = {}
        detail = self._detail_for(action_id)
        if detail is not None:
            action = detail.action
            known[action.source_service_id] = action.source_start
            if action.follow_up_service_id:
                known[action.follow_up_service_id] = action.follow_up_start
            for event in detail.events:
                if event.service_id and event.kind in ("adjusted", "follow_up_linked", "completed"):
                    known.setdefault(event.service_id, event.service_start)
        for action in self._actions:
            if action.action_id == action_id:
                known.setdefault(action.source_service_id, action.source_start)
                if action.follow_up_service_id:
                    known.setdefault(action.follow_up_service_id, action.follow_up_start)
        return known

    def _action_for(self, action_id):
        detail = self._detail_for(action_id)
        if detail is not None:
            return detail.action
        return next((action for action in self._actions if action.action_id == action_id), None)

    @Slot(str, str, int, result=bool)
    def openService(self, action_id, service_id, seconds):
        """打開這一項的一筆服務（-1＝不跳；其他的是影片的第幾秒）。只接受這一項讀到的服務：
        服務人員經由行動只打得開自己的服務（看不到別人的行動，就拿不到別人的服務 ID）。
        管理員打開的是進行中的這一項的原始服務或後續服務時，那一頁的輔導面板對準這一項（時間註記加到它）。"""
        known = self._known_services(action_id)
        if service_id not in known:
            logger.warning("refused to open %s through coaching action %s", service_id, action_id)
            return False
        action = self._action_for(action_id)
        if self._admin and action is not None and action.is_open and service_id in (
                action.source_service_id, action.follow_up_service_id):
            self._focus_next = (service_id, action_id)
        self.openServiceRequested.emit(service_id, format_time(known[service_id]), int(seconds),
                                       ADMIN_PAGE if self._admin else STAFF_PAGE)
        return True

    # ------------------------------------------------------------ 明細頁的輔導面板（管理員）

    @Slot(str)
    def watchService(self, service_id):
        """明細頁打開了哪一筆服務：面板跟著它（草稿是每一筆服務各一份）。從 Coaching 打開的，對準那一項。"""
        focus = None
        if self._focus_next is not None and self._focus_next[0] == service_id:
            focus = self._focus_next[1]
        self._focus_next = None
        if service_id == self._card_service:
            if focus is not None and focus != self._card_focus:
                self._card_focus = focus
                self.cardChanged.emit()
            return
        self._card_service = service_id
        self._card = ((), ())
        self._card_state = "idle"
        self._card_focus = focus or ""
        self._create_errors = {"goal": "", "comment": "", "due": "", "form": ""}
        self.cardChanged.emit()
        self.draftChanged.emit()
        self.draftReset.emit()
        self.errorsChanged.emit()
        if "card" in self._views:
            self._load_card()

    @Slot()
    def reloadCard(self):
        self._load_card()

    def _load_card(self):
        if self.services is None or not self._card_service:
            return False
        db, caller, service_id = self.services.db, self.account_id, self._card_service
        submitted = self.services.tasks.submit(
            "db", CARD_KEY, lambda token, report: load_card(db, caller, service_id),
            on_ok=lambda card: self._card_loaded(service_id, card),
            on_err=lambda exc: self._card_failed(service_id, exc), replace=True)
        if submitted and self._card_state in ("idle", "error"):
            self._card_state = "loading"
            self.cardChanged.emit()
        return submitted

    def _card_loaded(self, service_id, card):
        if service_id != self._card_service:
            return
        self._card = card
        self._card_state = "ready"
        self.cardChanged.emit()

    def _card_failed(self, service_id, exc):
        if service_id != self._card_service:
            return
        logger.warning("loading the coaching card for %s failed: %r", service_id, exc)
        if self._card_state != "ready":
            self._card_state = "error"
            self.cardChanged.emit()

    def _markable(self, action_id):
        """這一項能在面板這部影片上加時間註記（進行中，這筆是它的原始服務或目前的後續服務）。"""
        return bool(action_id) and any(action.action_id == action_id and action.status == OPEN
                                       and action.relation in ("source", "follow_up") for action in self._card[0])

    def _new_draft(self):
        return {"goal": "", "comment": "", "due": default_due(self.today()), "markers": [], "actionId": "",
                "pending": None, "sent": None}

    def _current_draft(self):
        if not self._card_service:
            return None
        return self._drafts.setdefault(self._card_service, self._new_draft())

    @Slot(str, str)
    def updateDraft(self, field, value):
        """表單的欄位（goal、comment、due）改了：存在草稿裡（換頁再回來還在）；那一格的錯誤先拿掉。
        字在 QML 的欄位裡，這裡不發 draftChanged（打一個字不用重畫時間註記）。"""
        draft = self._current_draft()
        if draft is None or field not in ("goal", "comment", "due") or draft[field] == value:
            return
        draft[field] = value
        if self._create_errors.get(field) or self._create_errors["form"]:
            self._create_errors = {**self._create_errors, field: "", "form": ""}
            self.errorsChanged.emit()

    @Slot(str, result=bool)
    def focusGoal(self, action_id):
        """面板對準一項（Mark Moments）或回到新目標（空字串，Set a New Goal）。說明欄開著時不換：先存或取消。"""
        draft = self._current_draft()
        if draft is None or draft["pending"] is not None or (action_id and not self._markable(action_id)):
            return False
        self._card_focus = action_id
        if not action_id and draft["sent"]:
            draft["sent"] = None
            self.draftChanged.emit()
            self.draftReset.emit()
        self.cardChanged.emit()
        return True

    @Slot(int, result=bool)
    def startMoment(self, seconds):
        """Add Moment：在影片的第 seconds 秒打開說明欄（時間是按下去的那一秒，之後影片怎麼動都不變）。
        對準一項時加到那一項；不然加到新目標的草稿。已經開著、剛送出（還沒按 Set Another Goal）時不開。"""
        draft = self._current_draft()
        target = self.cardFocusId
        if draft is None or seconds < 0 or draft["pending"] is not None or (draft["sent"] and not target):
            return False
        draft["pending"] = {"seconds": int(seconds), "note": "", "target": target}
        self.draftChanged.emit()
        return True

    @Slot(str)
    def updatePending(self, note):
        """說明欄的字：存在草稿裡（換頁再回來還在）。"""
        draft = self._current_draft()
        if draft is not None and draft["pending"] is not None:
            draft["pending"]["note"] = note
            if self._action_error["op"] == "marker":
                self.clearActionError()

    @Slot(result=bool)
    def savePending(self):
        """Save Moment：新目標的草稿馬上多一個時間註記；對準一項時寫進資料庫（成功了說明欄才關，失敗時字留著）。"""
        draft = self._current_draft()
        pending = draft["pending"] if draft is not None else None
        if pending is None:
            return False
        note = pending["note"].strip()
        if len(note) > NOTE_MAX:
            self._set_action_error(pending["target"], "marker", _tr("Keep the note under %1 characters.", NOTE_MAX))
            return False
        if not pending["target"]:
            draft["markers"] = sorted(draft["markers"] + [(pending["seconds"], note)])
            draft["pending"] = None
            self.draftChanged.emit()
            return True
        return self.addMarker(pending["target"], self._card_service, pending["seconds"], note)

    @Slot()
    def cancelPending(self):
        """Cancel：關掉說明欄（使用者自己按的，字不留）。"""
        draft = self._current_draft()
        if draft is not None and draft["pending"] is not None:
            draft["pending"] = None
            self.clearActionError()
            self.draftChanged.emit()

    @Slot(int)
    def removeDraftMarker(self, index):
        draft = self._current_draft()
        if draft is not None and 0 <= index < len(draft["markers"]):
            del draft["markers"][index]
            self.draftChanged.emit()

    @Slot()
    def newGoal(self):
        """Set Another Goal：送出的那一項收起來，回到空的草稿。"""
        draft = self._current_draft()
        if draft is not None and draft["sent"]:
            draft["sent"] = None
            self.draftChanged.emit()
            self.draftReset.emit()

    @Slot(result=bool)
    def createGoal(self):
        """Send Goal：說明欄還開著就先存成一個時間註記（不會默默丟掉，T47 第 1 節），檢查欄位，
        同一個交易寫行動和草稿裡的時間註記（db lane），再重讀面板（和開著的清單）。"""
        draft = self._current_draft()
        if not self._admin or self.services is None or draft is None or self._creating:
            return False
        if draft["pending"] is not None and not draft["pending"]["target"]:
            self.savePending()
        values, errors = validate_goal(draft["goal"], draft["comment"], draft["due"], self.today())
        self._create_errors = {**errors, "form": ""}
        self.errorsChanged.emit()
        if values is None:
            return False
        if not draft["actionId"]:
            draft["actionId"] = str(uuid.uuid4())  # 重試用同一個：撞到主鍵就是已經寫好了
        return self._submit_create(self._card_service, draft["actionId"], values, list(draft["markers"]))

    def _submit_create(self, service_id, action_id, values, markers):
        db, caller = self.services.db, self.account_id
        views = Views(list=self._list_open(), card=service_id)
        submitted = self.services.tasks.submit(
            "db", CREATE_KEY,
            lambda token, report: create(db, caller, action_id, service_id, values["goal"], values["comment"],
                                         values["due"], markers, views),
            on_ok=lambda snapshot: self._created(service_id, action_id, len(markers), snapshot),
            on_err=lambda exc: self._create_failed(service_id, action_id, values, markers, exc))
        if submitted:
            self._set_creating(True)
        return submitted

    def _created(self, service_id, action_id, count, snapshot):
        self._set_creating(False)
        self._retries.pop(CREATE_KEY, None)
        self._drafts[service_id] = {**self._new_draft(), "sent": {"actionId": action_id, "count": count}}
        self._create_errors = {"goal": "", "comment": "", "due": "", "form": ""}
        self.errorsChanged.emit()
        self.draftChanged.emit()
        if service_id == self._card_service:
            self.draftReset.emit()
        self._apply_snapshot(snapshot)
        if "card" in self._views and service_id == self._card_service:
            return  # 面板上就寫著「Goal sent to Mia」，不再跳通知
        staff = next((first_name(action.staff_name) for action in (snapshot.card or ((), ()))[0]
                      if action.action_id == action_id), "")
        detail = (_tr("%1 can see it in My Actions.", staff) if staff
                  else _tr("The staff member can see it in My Actions."))
        self._notify("success", _tr("Goal sent"), detail, toast_key(action_id), f"{OPEN_ACTION}:{action_id}",
                     _tr("Open"))

    def _create_failed(self, service_id, action_id, values, markers, exc):
        self._set_creating(False)
        code = problem_code(exc)
        if code:
            message = problem_text(code)
        else:
            logger.warning("creating a coaching action failed: %r", exc)
            message = reason_text(exc)
        self._create_errors = {**self._create_errors, "form": message}
        self.errorsChanged.emit()
        if is_retryable(exc):  # 連不上：表單留著，通知上可以 Retry（同一個 action_id）
            self._retries[CREATE_KEY] = lambda: self._submit_create(service_id, action_id, values, markers)
            self._notify("error", _tr("Goal wasn't sent"), message, "coaching:create",
                         f"{RETRY_ACTION}:{CREATE_KEY}", _tr("Retry"))

    def _set_creating(self, creating):
        if creating != self._creating:
            self._creating = creating
            self.busyChanged.emit()

    # ------------------------------------------------------------ 已經存在的行動：時間註記、回應、確認、後續、結案

    def _detail_for(self, action_id):
        return self._detail if self._detail is not None and self._detail.action.action_id == action_id else None

    def _version(self, action_id):
        """畫面上那一項的版本（樂觀鎖）：明細有就用明細的，再來是面板、清單的。"""
        detail = self._detail_for(action_id)
        if detail is not None:
            return detail.action.version
        for action in self._card[0]:
            if action.action_id == action_id:
                return action.version
        for action in self._actions:
            if action.action_id == action_id:
                return action.version
        return None

    def _staff_first(self, action_id):
        action = self._action_for(action_id)
        return first_name(action.staff_name) if action is not None else ""

    @Slot(str, str, int, str, result=bool)
    def addMarker(self, action_id, service_id, seconds, note):
        """在這部影片的第 seconds 秒加一個時間註記（管理員；原始服務或目前的後續服務）。
        面板的說明欄是這一項、這一秒的話，寫好了才關。"""
        if not self._admin or seconds < 0:
            return False
        note = (note or "").strip()
        if len(note) > NOTE_MAX:
            self._set_action_error(action_id, "marker", _tr("Keep the note under %1 characters.", NOTE_MAX))
            return False
        service = self._card_service

        def close_pending():
            draft = self._drafts.get(service)
            pending = draft["pending"] if draft is not None else None
            if pending is not None and pending["target"] == action_id and pending["seconds"] == int(seconds):
                draft["pending"] = None
                self.draftChanged.emit()

        # 不跳通知：時間註記馬上出現在面板上
        return self._write(action_id, "marker", [(ADD_MARKER_SQL, (self.account_id, action_id, service_id,
                                                                   int(seconds), note))], "", "", close_pending)

    @Slot(str, int, result=bool)
    def removeMarker(self, action_id, marker_id):
        if not self._admin:
            return False
        return self._write(action_id, "unmark", [(REMOVE_MARKER_SQL, (self.account_id, int(marker_id)))], "", "")

    @Slot(str, str, result=bool)
    def respond(self, action_id, text):
        """對話（服務人員：補充情境、回應、不同看法；管理員：回覆）。只新增一列歷史；訊息出現在對話裡，不跳通知。"""
        text, error = required(text, TEXT_MAX, _tr("Write a message first."))
        if error:
            self._set_action_error(action_id, "respond", error)
            return False
        return self._write(action_id, "respond", [(RESPOND_SQL, (self.account_id, action_id, text))], "", "")

    @Slot(str, result=bool)
    def acknowledge(self, action_id):
        """Got It：服務人員確認看過目前的目標（只代表已回應，不是完成）。目標在這段期間調整過就是 changed。"""
        version = self._version(action_id)
        if self._admin or version is None:
            return False
        return self._write(action_id, "acknowledge", [(ACKNOWLEDGE_SQL, (self.account_id, action_id, version))],
                           "", "")

    @Slot(str)
    def loadFollowUps(self, action_id):
        """能當後續服務的服務（2 個往返：程序、分數）：同一位員工、分析完成、比原始服務（和看過的）晚。
        Check Again、Change 也呼叫它。同一項重查時，已經有的留著（不閃一下）。"""
        if not self._admin or self.services is None or not action_id:
            return
        db, caller = self.services.db, self.account_id
        if action_id != self._options_for:
            self._options_for, self._options = action_id, []
            self._options_state = "loading"
        elif self._options_state in ("idle", "error"):
            self._options_state = "loading"
        self._options_busy = True
        self.optionsChanged.emit()
        self.services.tasks.submit("db", OPTIONS_KEY,
                                   lambda token, report: load_follow_up_options(db, caller, action_id),
                                   on_ok=lambda rows: self._options_loaded(action_id, rows),
                                   on_err=lambda exc: self._options_failed(action_id, exc), replace=True)

    def _options_loaded(self, action_id, rows):
        if action_id != self._options_for:
            return
        self._options = list(rows)
        self._options_state = "ready" if rows else "empty"
        self._options_busy = False
        self.optionsChanged.emit()
        if self._eligible.get(action_id) != len(rows):  # 清單的標籤跟著最新的結果（Pick、Waiting）
            self._eligible[action_id] = len(rows)
            self._show_rows()
            self.listChanged.emit()

    def _options_failed(self, action_id, exc):
        if action_id != self._options_for:
            return
        logger.warning("loading follow-up options for %s failed: %r", action_id, exc)
        self._options_busy = False
        if not self._options:
            self._options_state = "error"
        self.optionsChanged.emit()

    @Slot(str, str, result=bool)
    def linkFollowUp(self, action_id, service_id):
        """Use as Follow-up：連結（或換掉）後續服務。下一步的卡片馬上換成「Watch the follow-up」，不跳通知。"""
        version = self._version(action_id)
        if not self._admin or version is None:
            return False
        if not service_id:
            self._set_action_error(action_id, "link", _tr("Choose a follow-up service."))
            return False
        return self._write(action_id, "link", [(LINK_SQL, (self.account_id, action_id, version, service_id))],
                           "", "")

    @Slot(str, str, result=bool)
    def complete(self, action_id, conclusion):
        """Mark Complete：要有後續服務和結論（資料庫再檢查一次）。"""
        version = self._version(action_id)
        if not self._admin or version is None:
            return False
        conclusion, error = required(conclusion, TEXT_MAX, _tr("Write what you saw in the follow-up."))
        if error:
            self._set_action_error(action_id, "complete", error)
            return False
        staff = self._staff_first(action_id)
        return self._write(action_id, "complete", [(COMPLETE_SQL, (self.account_id, action_id, version, conclusion))],
                           _tr("Goal completed"), _tr("%1 can see your conclusion.", staff) if staff else "")

    @Slot(str, str, str, str, result=bool)
    def keepPracticing(self, action_id, reason, goal, due):
        """Keep Practicing（看過後續服務之後）：還要練什麼、新的目標和期限；看過的後續服務留在歷史、從行動上拿掉。"""
        staff = self._staff_first(action_id)
        return self._adjust(action_id, reason, goal, due, _tr("Write what still needs practice."),
                            _tr("%1 will see what to practise next.", staff) if staff else "")

    @Slot(str, str, str, str, result=bool)
    def adjust(self, action_id, reason, goal, due):
        """Edit Goal：改目標或期限，寫原因（舊的值留在歷史）。和 Keep Practicing 是同一個程序。"""
        staff = self._staff_first(action_id)
        return self._adjust(action_id, reason, goal, due, _tr("Write why you're changing the goal."),
                            _tr("%1 will see the new goal.", staff) if staff else "")

    def _adjust(self, action_id, reason, goal, due, empty_reason, success):
        version = self._version(action_id)
        if not self._admin or version is None:
            return False
        reason, reason_error = required(reason, TEXT_MAX, empty_reason)
        goal, goal_error = required(goal, GOAL_MAX, _tr("Enter the goal the staff member should practise."))
        due_value, due_error = parse_due(due, self.today())
        error = reason_error or goal_error or due_error
        if error:
            self._set_action_error(action_id, "adjust", error)
            return False
        return self._write(action_id, "adjust",
                           [(ADJUST_SQL, (self.account_id, action_id, version, reason, goal, due_value))],
                           _tr("Goal updated"), success)

    @Slot(str, str, result=bool)
    def cancelAction(self, action_id, reason):
        version = self._version(action_id)
        if not self._admin or version is None:
            return False
        reason, error = required(reason, TEXT_MAX, _tr("Explain why you're cancelling this goal."))
        if error:
            self._set_action_error(action_id, "cancel", error)
            return False
        return self._write(action_id, "cancel", [(CANCEL_SQL, (self.account_id, action_id, version, reason))],
                           _tr("Goal cancelled"), "")

    @Slot()
    def clearActionError(self):
        if self._action_error["text"]:
            self._action_error = {"actionId": "", "op": "", "text": ""}
            self.errorsChanged.emit()

    def _set_action_error(self, action_id, op, text):
        self._action_error = {"actionId": action_id, "op": op, "text": text}
        self.errorsChanged.emit()

    def _write(self, action_id, op, statements, title, detail, after=None):
        """一項行動的寫入：一個交易（db lane），在同一個工作裡重讀開著的畫面。同一項同時只有一個寫入。
        after：寫好之後、發 saved 之前要做的事（例如關掉面板的說明欄）。"""
        if self.services is None or not action_id or action_id in self._saving:
            return False
        db, caller = self.services.db, self.account_id
        views = Views(list=self._list_open(), detail=self._selected if self._selected == action_id else "",
                      card=self._card_service if "card" in self._views else "")
        submitted = self.services.tasks.submit(
            "db", write_key(action_id), lambda token, report: write(db, caller, statements, views),
            on_ok=lambda snapshot: self._written(action_id, op, title, detail, after, snapshot),
            on_err=lambda exc: self._write_failed(action_id, op, statements, title, detail, after, exc))
        if submitted:
            self._saving.add(action_id)
            self._action_error = {"actionId": "", "op": "", "text": ""}
            self.errorsChanged.emit()
            self.busyChanged.emit()
        return submitted

    def _written(self, action_id, op, title, detail, after, snapshot):
        self._saving.discard(action_id)
        self._retries.pop(action_id, None)
        self.busyChanged.emit()
        self._apply_snapshot(snapshot)
        if after is not None:
            after()
        if title:
            self._notify("success", title, detail, toast_key(action_id))
        self.saved.emit(action_id, op)

    def _write_failed(self, action_id, op, statements, title, detail, after, exc):
        self._saving.discard(action_id)
        self.busyChanged.emit()
        code = problem_code(exc)
        if code:
            message = problem_text(code)
        else:
            logger.warning("coaching %s on %s failed: %r", op, action_id, exc)
            message = reason_text(exc)
        self._set_action_error(action_id, op, message)
        if code in ("changed", "not_open"):  # 別人改過、已經結案：說清楚，可以重讀
            self._notify("warning", _tr("This goal changed elsewhere"), message, toast_key(action_id),
                         f"{RELOAD_ACTION}:{action_id}", _tr("Reload"))
        elif is_retryable(exc):  # 連不上：沒有寫進去（交易 rollback），可以再送一次
            self._retries[action_id] = lambda: self._write(action_id, op, statements, title, detail, after)
            self._notify("error", _tr("Change wasn't saved"), message, toast_key(action_id),
                         f"{RETRY_ACTION}:{action_id}", _tr("Retry"))
        else:
            self._notify("error", _tr("Change wasn't saved"), message, toast_key(action_id))

    def _apply_snapshot(self, snapshot):
        if self._stopped:
            return
        if snapshot.actions is not None:
            self._apply_list(snapshot.actions)
            self.listChanged.emit()
        if snapshot.detail is not None:
            detail = snapshot.detail or None
            if detail is None or detail.action.action_id == self._selected:
                self._detail = detail
                self._detail_state = "ready" if detail is not None else "missing"
                self.detailChanged.emit()
            if detail is not None and detail.action.action_id == self._selected:
                self._check_follow_ups(detail)  # 連結、調整之後能選的服務變了
        if snapshot.card is not None:
            self._card = snapshot.card
            self._card_state = "ready"
            self.cardChanged.emit()

    def _notify(self, severity, title, detail, key, action_id="", action_label=""):
        if self.services is not None:
            self.services.notifier.notify(severity, title, detail, key, action_id, action_label)

    # ------------------------------------------------------------ 通知上的動作

    @Slot(str, int)
    def _on_action(self, action_id, toast_id):
        kind, _, target = action_id.partition(":")
        if not target:
            return
        if kind == RETRY_ACTION:
            retry = self._retries.pop(target, None)
            if retry is not None:
                retry()
        elif kind == RELOAD_ACTION:
            if target == self._selected:
                self._load_detail(target)
            if self._list_open():
                self._refresh_list()
            if "card" in self._views:
                self._load_card()
        elif kind == OPEN_ACTION:
            self.requestOpenAction(target)

    @Slot(result=str)
    def defaultDue(self):
        return default_due(self.today())
