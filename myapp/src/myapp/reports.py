"""報表的桌面端（new-feature PLAN N4、N5；R1、R5、R6；取捨見 T37）。

產生 PDF 的是分析服務（R1），桌面端只負責：要求產生、看狀態、發佈、下載與顯示。和分析服務的約定：
1. 管理員按 Generate Report：db lane 新增一列 tbl_report（新的 uuid4、report_status = generating、
   s3_key = reports/{report_id}.pdf、created_by = 管理員），然後 io lane POST {REPORTS_API_URL} {"report_id": …}，
   timeout (3, 10)。POST 失敗（連不到、逾時、不是 2xx）就在 db lane 把那一列改成 failed，原因是一句英文。
2. 分析服務在背景產生、上傳到 S3 的 s3_key，再把那一列改成 ready 或 failed（它不碰 published_*）。
3. 產生中超過 10 分鐘的，管理員端列清單前改成 failed（Timed out），不會一直停在 generating。
4. 發佈只在桌面端（db lane）：published_at、published_by；已經發佈的再按一次不改時間；沒有取消發佈。
5. 清單一律經過 CALL sp_list_reports(account_id)：服務人員只有自己已發佈的（不含全公司），管理員全部；每個對象最新一筆。
6. 每次產生（包括 Retry）都是新的一列，舊的留著（R5）；服務人員在新的一筆發佈之前，看到的仍是上一筆已發佈的。

ReportsModel（context property reportsModel，兩端各一個）：
- 清單（listModel、listState）在頁面打開時讀一次（pageOpened）；管理員在有產生中的列時每 ADMIN_POLL_S 秒、
  服務人員每 STAFF_POLL_S 秒重新查，頁面關掉就停（pageClosed）。
- 通知：管理員看到 generating → ready 跳「Report ready」、→ failed 跳失敗和 Retry；服務人員在頁面開著時，
  第一次載入之後才出現的報表跳「New report」。
- 服務人員分析頁的 Report 欄：serviceStates（CALL sp_list_service_report_states，只有狀態，不含未發佈的報表）。
- 管理員明細頁的報表卡：detailReport（清單裡那筆服務最新的一列）。
- 打開報表：select() 交給 media.PDFHandler（io lane 下載到 reports-pdf 快取、pdf lane 畫頁面）。
- 所有資料庫工作在 db lane（一個查詢一個往返，CALL 用 execute），HTTP、S3 在 io lane，PyMuPDF 在 pdf lane。
  文字（英文，PLAN D1）都在 GUI 執行緒產生。
"""
import logging
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

import requests
from PySide6.QtCore import QCoreApplication, Property, QObject, QTimer, Signal, Slot

from myapp.notifications import user_message
from myapp.service_list import RowListModel, date_errors, list_date_text, range_text

logger = logging.getLogger(__name__)

SERVICE, STAFF, COMPANY = "service", "staff", "company"
REPORT_KINDS = (SERVICE, STAFF, COMPANY)
GENERATING, READY, FAILED = "generating", "ready", "failed"
PUBLISHED = "published"  # 只用在狀態標籤（chipState）：資料庫的 report_status 沒有這個值，看 published_at
POST_TIMEOUT = (3, 10)   # (連線逾時, 讀取逾時) 秒；分析服務收到就回 202，不等 PDF 做完
STALE_MINUTES = 10
STALE_REASON = "Timed out"  # 契約（T37）：逾時的 generating 改成 failed 的原因
REASON_MAX = 255            # tbl_report.failure_reason 的長度
ADMIN_POLL_S = 3            # 管理員：有產生中的列時多久查一次
STAFF_POLL_S = 15           # 服務人員：頁面開著時多久查一次（PLAN 7.3）
RECENT_SERVICES = 20        # 產生每次服務報表時，選單列出那位員工最近幾筆分析完成的服務

# TaskRunner 的 key
LIST_KEY = "reports:list"
STATES_KEY = "reports:states"
CREATE_KEY = "reports:create"
STAFF_KEY = "reports:staff"
SERVICES_KEY = "reports:services"
OPEN_ACTION = "open-report"    # 通知上的 Open／View：open-report:<report_id>
RETRY_ACTION = "retry-report"  # 通知上的 Retry：retry-report:<report_id>
VIEWS = ("reports", "analysis", "detail")  # 會用到這個模型的頁面（pageOpened、pageClosed）

LIST_SQL = "CALL sp_list_reports(%s)"
STATES_SQL = "CALL sp_list_service_report_states(%s)"
EXPIRE_SQL = (f"UPDATE tbl_report SET report_status = 'failed', failure_reason = %s "
              f"WHERE report_status = 'generating' AND created_at < NOW(3) - INTERVAL {STALE_MINUTES} MINUTE")
# 每次服務報表：那筆服務要是分析完成的；staff_account_id 用那筆服務的 account_id（同一個往返）
INSERT_SERVICE_SQL = (
    "INSERT INTO tbl_report (report_id, report_kind, service_id, staff_account_id, report_status, s3_key, created_by) "
    "SELECT %s, 'service', s.service_id, s.account_id, 'generating', %s, %s "
    "FROM tbl_service AS s WHERE s.service_id = %s AND s.service_status = 3")
INSERT_STAFF_SQL = (
    "INSERT INTO tbl_report (report_id, report_kind, staff_account_id, period_start, period_end, report_status, "
    "s3_key, created_by) "
    "SELECT %s, 'staff', a.account_id, %s, %s, 'generating', %s, %s "
    "FROM tbl_account AS a WHERE a.account_id = %s AND a.account_level = '1'")
INSERT_COMPANY_SQL = (
    "INSERT INTO tbl_report (report_id, report_kind, period_start, period_end, report_status, s3_key, created_by) "
    "VALUES (%s, 'company', %s, %s, 'generating', %s, %s)")
MARK_FAILED_SQL = ("UPDATE tbl_report SET report_status = 'failed', failure_reason = %s "
                   "WHERE report_id = %s AND report_status = 'generating'")
PUBLISH_SQL = ("UPDATE tbl_report SET published_at = NOW(), published_by = %s "
               "WHERE report_id = %s AND report_status = 'ready' AND published_at IS NULL")
STAFF_SQL = ("SELECT account_id, account_name, account_username FROM tbl_account "
             "WHERE account_level = '1' ORDER BY account_name, account_username")
SERVICES_SQL = ("SELECT service_id, service_start FROM tbl_service WHERE account_id = %s AND service_status = 3 "
                "ORDER BY service_start DESC, service_id DESC LIMIT %s")

# 清單每一列的角色（ReportsView.qml）
REPORT_ROLES = ("reportId", "kind", "title", "kindText", "metaText", "chipState", "status", "published",
                "failureReason", "publishedText", "canPublish", "canRetry", "serviceId", "subjectKey")


def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：經過 translate；%1、%2… 一次換成 values（像 QString::arg）。"""
    text = QCoreApplication.translate("Reports", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def post_key(report_id):
    return f"reports:post:{report_id}"


def fail_key(report_id):
    return f"reports:fail:{report_id}"


def publish_key(report_id):
    return f"reports:publish:{report_id}"


def toast_key(report_id):
    """同一份報表的通知共用一則（產生中失敗、完成、發佈：就地更新）。"""
    return f"report:{report_id}"


def s3_key(report_id):
    return f"reports/{report_id}.pdf"


# ---------------------------------------------------------------- 分析服務的 /reports


class ReportServiceNotConfigured(RuntimeError):
    """REPORTS_API_URL、ANALYZE_API_URL 都沒有設定。"""


def reports_api_url():
    """/reports 的位址：REPORTS_API_URL；沒設定時用 ANALYZE_API_URL，把結尾的 /analyze 換成 /reports
    （結尾不是 /analyze 時接上 /reports）。都沒有是空字串。"""
    explicit = os.getenv("REPORTS_API_URL", "").strip()
    if explicit:
        return explicit
    analyze = os.getenv("ANALYZE_API_URL", "").strip().rstrip("/")
    if not analyze:
        return ""
    if analyze.endswith("/analyze"):
        return analyze[:-len("/analyze")] + "/reports"
    return analyze + "/reports"


def post_report(url, report_id):
    """在 io lane 執行：請分析服務產生 report_id 這份報表。2xx（202）就算送到；其他丟例外。"""
    if not url:
        raise ReportServiceNotConfigured("REPORTS_API_URL and ANALYZE_API_URL are not set")
    response = requests.post(url, json={"report_id": report_id}, timeout=POST_TIMEOUT)
    if not 200 <= response.status_code < 300:
        raise requests.HTTPError(f"/reports returned HTTP {response.status_code}", response=response)


def failure_reason(exc):
    """POST 失敗時寫進 failure_reason 的一句英文（畫面直接顯示：不放網址、狀態碼、例外的內容）。"""
    if isinstance(exc, ReportServiceNotConfigured):
        reason = _tr("The analysis service address isn't set up on this computer.")
    elif isinstance(exc, requests.ConnectionError):
        reason = _tr("Couldn't reach the analysis service.")
    elif isinstance(exc, requests.Timeout):
        reason = _tr("The analysis service didn't respond.")
    elif isinstance(exc, requests.HTTPError):
        reason = _tr("The analysis service couldn't start this report.")
    else:
        reason = _tr("Couldn't send the request to the analysis service.")
    return reason[:REASON_MAX]


def reason_text(exc):
    """資料庫、S3 的錯誤：「Can't reach the database. Check your network and try again.」"""
    _, title, detail, _ = user_message(exc)
    return f"{title}. {detail}" if detail else title


# ---------------------------------------------------------------- 資料（db lane 回傳的列 → Report）


@dataclass(frozen=True)
class Subject:
    """報表的對象：每次服務（service_id）、員工一段期間（staff_id、start、end）、全公司一段期間（start、end）。"""
    kind: str
    service_id: str = ""
    staff_id: str = ""
    start: date = None
    end: date = None

    @property
    def key(self):
        """同一個對象的報表有同一個 key（R5：重新產生是同一個對象的新的一列）。QML 的 subjectKey() 要一樣。"""
        if self.kind == SERVICE:
            return f"service|{self.service_id}"
        period = f"{_day(self.start)}|{_day(self.end)}"
        return f"staff|{self.staff_id}|{period}" if self.kind == STAFF else f"company|{period}"


def _day(value):
    return value.strftime("%Y-%m-%d") if value is not None else ""


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def _text(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return "" if value is None else str(value)


@dataclass(frozen=True)
class Report:
    """sp_list_reports 的一列（還沒格式化）。"""
    report_id: str
    kind: str
    service_id: str = ""
    staff_id: str = ""
    staff_name: str = ""
    staff_username: str = ""
    period_start: date = None
    period_end: date = None
    status: str = GENERATING
    failure_reason: str = ""
    s3_key: str = ""
    created_at: datetime = None
    published_at: datetime = None

    @classmethod
    def from_row(cls, row):
        return cls(
            report_id=_text(row["report_id"]), kind=_text(row["report_kind"]), service_id=_text(row.get("service_id")),
            staff_id=_text(row.get("staff_account_id")), staff_name=_text(row.get("staff_name")),
            staff_username=_text(row.get("staff_username")), period_start=_as_date(row.get("period_start")),
            period_end=_as_date(row.get("period_end")), status=_text(row["report_status"]),
            failure_reason=_text(row.get("failure_reason")), s3_key=_text(row.get("s3_key")),
            created_at=row.get("created_at"), published_at=row.get("published_at"))

    @property
    def published(self):
        return self.published_at is not None

    @property
    def subject(self):
        return Subject(self.kind, self.service_id if self.kind == SERVICE else "",
                       self.staff_id if self.kind == STAFF else "", self.period_start, self.period_end)


def list_reports(db, account_id, expire=False):
    """在 db lane 執行：CALL sp_list_reports（1 個往返）。expire：管理員先把逾時的 generating 改成 failed（多 1 個往返）。"""
    with db.cursor(dictionary=True) as cursor:
        if expire:
            cursor.execute(EXPIRE_SQL, (STALE_REASON,))
        cursor.execute(LIST_SQL, (account_id,))
        return [Report.from_row(row) for row in cursor.fetchall()]


def list_service_states(db, account_id):
    """在 db lane 執行：CALL sp_list_service_report_states（1 個往返）。{service_id: (state, published_report_id)}。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(STATES_SQL, (account_id,))
        return {_text(row["service_id"]): (_text(row["report_state"]), _text(row.get("published_report_id")))
                for row in cursor.fetchall()}


class SubjectUnavailable(LookupError):
    """新增時對象不在了：那筆服務不是分析完成的（或不存在）、那個帳號不是服務人員（或不存在）。"""


def create_report(db, report_id, subject, created_by, account_id):
    """在 db lane 執行：新增一列 generating（1 個往返），再列清單（1 個往返）。對象不在時丟 SubjectUnavailable。"""
    key = s3_key(report_id)
    with db.cursor(dictionary=True) as cursor:
        if subject.kind == SERVICE:
            cursor.execute(INSERT_SERVICE_SQL, (report_id, key, created_by, subject.service_id))
        elif subject.kind == STAFF:
            cursor.execute(INSERT_STAFF_SQL, (report_id, subject.start, subject.end, key, created_by, subject.staff_id))
        else:
            cursor.execute(INSERT_COMPANY_SQL, (report_id, subject.start, subject.end, key, created_by))
        if cursor.rowcount != 1:
            raise SubjectUnavailable(subject.key)
        cursor.execute(LIST_SQL, (account_id,))
        return [Report.from_row(row) for row in cursor.fetchall()]


def mark_failed(db, report_id, reason, account_id):
    """在 db lane 執行：產生中的那一列改成 failed（已經不是 generating 就不動），再列清單。2 個往返。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(MARK_FAILED_SQL, (reason, report_id))
        cursor.execute(LIST_SQL, (account_id,))
        return [Report.from_row(row) for row in cursor.fetchall()]


def publish_report(db, report_id, account_id):
    """在 db lane 執行：發佈（只有 ready、還沒發佈的才會改；R6），再列清單。回傳 (改了幾列, 清單)。2 個往返。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(PUBLISH_SQL, (account_id, report_id))
        changed = cursor.rowcount
        cursor.execute(LIST_SQL, (account_id,))
        return changed, [Report.from_row(row) for row in cursor.fetchall()]


def list_staff(db):
    """在 db lane 執行：服務人員（等級 1），依名字、登入帳號排。1 個往返。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(STAFF_SQL)
        return [(_text(row["account_id"]), _text(row["account_name"]), _text(row["account_username"]))
                for row in cursor.fetchall()]


def list_recent_services(db, staff_id, limit=RECENT_SERVICES):
    """在 db lane 執行：這位員工最近 limit 筆分析完成的服務，新的在前。1 個往返。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(SERVICES_SQL, (staff_id, int(limit)))
        return [(_text(row["service_id"]), row["service_start"]) for row in cursor.fetchall()]


# ---------------------------------------------------------------- 畫面上的文字（GUI 執行緒）


def kind_text(kind):
    return {SERVICE: _tr("Service report"), STAFF: _tr("Staff report"), COMPANY: _tr("Company report")}.get(kind, "")


def period_text(start, end):
    """「Oct 1 – Oct 31, 2026」（service_list.range_text）。"""
    return range_text(start, end)


def report_title(report):
    """清單和檢視器的名稱：「S20261101090000 · Mia Chen」「Mia Chen · Oct 1 – Oct 31, 2026」
    「Company · Oct 1 – Oct 31, 2026」。名字是報表對象目前的名字（sp_list_reports）。"""
    if report.kind == SERVICE:
        return _tr("%1 · %2", report.service_id, report.staff_name) if report.staff_name else report.service_id
    period = period_text(report.period_start, report.period_end)
    if report.kind == STAFF:
        return _tr("%1 · %2", report.staff_name or report.staff_username or _tr("Staff"), period)
    return _tr("Company · %1", period)


def chip_state(report):
    """狀態標籤（StatusChip.reportState）：generating、ready（還沒發佈）、published、failed。"""
    return PUBLISHED if report.published else report.status


def published_text(report, today):
    return _tr("Published %1", list_date_text(report.published_at, today)) if report.published else ""


def meta_text(report, admin, today):
    """清單第二行。管理員：「Service report · Nov 1, 10:12」（前面的狀態標籤已經說了是產生中、好了還是已發佈；
    已發佈的寫發佈的時間，其他的寫產生的時間），失敗的寫原因。服務人員：「Service report · Published Nov 1, 10:20」。"""
    if admin and report.status == FAILED:
        return report.failure_reason or _tr("This report couldn't be generated.")
    if admin:
        when = list_date_text(report.published_at if report.published else report.created_at, today)
    else:
        when = published_text(report, today) or list_date_text(report.created_at, today)
    return _tr("%1 · %2", kind_text(report.kind), when) if when else kind_text(report.kind)


def report_row(report, admin, today):
    """Report → 清單模型的一列（REPORT_ROLES）。"""
    return {
        "reportId": report.report_id, "kind": report.kind, "title": report_title(report),
        "kindText": kind_text(report.kind), "metaText": meta_text(report, admin, today),
        "chipState": chip_state(report), "status": report.status, "published": report.published,
        "failureReason": report.failure_reason if report.status == FAILED else "",
        "publishedText": published_text(report, today),
        "canPublish": admin and report.status == READY and not report.published and report.kind != COMPANY,
        "canRetry": admin and report.status == FAILED,
        "serviceId": report.service_id, "subjectKey": report.subject.key,
    }


def staff_option(account_id, name, username):
    """員工選單的一項：「Mia Chen (mia.chen)」：同名的人分得出來（PLAN 13.7）。"""
    return {"value": account_id, "text": _tr("%1 (%2)", name, username) if username else name}


def service_option(service_id, start, today):
    return {"value": service_id, "text": _tr("%1 · %2", service_id, list_date_text(start, today))
            if start is not None else service_id}


def default_period(today):
    """新報表的預設期間：這個月 1 號到今天（電腦的日期，和 N2 的 Today 一樣）。"""
    return {"start": _day(today.replace(day=1)), "end": _day(today)}


def validate(kind, staff_id, service_id, start_text, end_text):
    """產生報表的表單：回傳 (Subject 或 None, {"subject", "start", "end"} 的錯誤)。"""
    errors = {"subject": "", "start": "", "end": ""}
    if kind not in REPORT_KINDS:
        errors["subject"] = _tr("Choose a report type.")
        return None, errors
    if kind == SERVICE:
        if not service_id:
            errors["subject"] = _tr("Choose a service.")
            return None, errors
        return Subject(SERVICE, service_id=service_id), errors
    if kind == STAFF and not staff_id:
        errors["subject"] = _tr("Choose a staff member.")
    start, end, date_problems = date_errors(start_text, end_text)
    errors.update(date_problems)
    for field, value, text in (("start", start, start_text), ("end", end, end_text)):
        if not errors[field] and value is None and not text.strip():
            errors[field] = _tr("Enter a date.")
    if any(errors.values()):
        return None, errors
    return Subject(kind, staff_id=staff_id if kind == STAFF else "", start=start.date(), end=end.date()), errors


# ---------------------------------------------------------------- 模型


class ReportsModel(QObject):
    """報表頁、分析頁的 Report 欄、管理員明細頁的報表卡（context property reportsModel）。

    services 是 AppServices；None（直接建立外殼、不經過 launch() 的測試）時什麼都不做。pdf 是 media.PDFHandler。
    """

    listChanged = Signal()           # listState、refreshing、錯誤
    selectedChanged = Signal()       # selectedId、selected、detailReport
    busyChanged = Signal()           # submitting、publishing、generatingKeys
    formChanged = Signal()           # 表單的錯誤
    optionsChanged = Signal()        # staffOptions、serviceOptions 和它們的狀態
    statesChanged = Signal()         # serviceStates
    reportCreated = Signal(str)      # 新增成功（產生中）：表單關掉
    openRequested = Signal(str)      # 通知的 Open／View：外殼換到報表頁、選這一份

    def __init__(self, services, account_id, admin=False, pdf=None, parent=None):
        super().__init__(parent)
        self.services = services
        self.account_id = account_id
        self._admin = bool(admin)
        self._pdf = pdf
        self._list = RowListModel(REPORT_ROLES, "reportId", self)
        self._reports = []           # [Report]，新的在前
        self._list_state = "idle"
        self._list_busy = False
        self._error_detail = self._error_technical = ""
        self._failures = 0
        self._selected = ""
        self._selected_subject = ""  # 選到的那一份的對象：重新產生、Retry 之後換成同一個對象的新的一列
        self._target_height = 0
        self._detail_service = ""
        self._submitting = False
        self._publishing = set()
        self._errors = {"subject": "", "start": "", "end": ""}
        self._form_error = ""
        self._staff = []             # [(account_id, name, username)]
        self._staff_state = "idle"
        self._services_for = ""
        self._recent = []            # [(service_id, service_start)]
        self._services_state = "idle"
        self._states = {}            # {service_id: (state, published_report_id)}
        self._states_loaded = False
        self._views = Counter()      # 開著的頁面（reports、analysis、detail）
        self._statuses = {}          # 管理員：上一次看到的每一份 {report_id: status}
        self._seen = None            # 服務人員：已經看過的 report_id；None＝這次打開頁面還沒載入過
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        if services is not None:
            services.notifier.actionTriggered.connect(self._on_action)

    # ------------------------------------------------------------ QML 讀的屬性

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
    def count(self):
        return len(self._reports)

    @Property(str, notify=selectedChanged)
    def selectedId(self):
        return self._selected

    @Property("QVariantMap", notify=selectedChanged)
    def selected(self):
        """選到的那一份（清單的一列，REPORT_ROLES）；沒有是空的。"""
        report = self._find(self._selected)
        return report_row(report, self._admin, date.today()) if report is not None else {}

    @Property("QVariantMap", notify=selectedChanged)
    def detailReport(self):
        """管理員明細頁那筆服務最新的報表（清單的一列）；沒有是空的。"""
        report = self._service_report(self._detail_service)
        return report_row(report, self._admin, date.today()) if report is not None else {}

    @Property(bool, notify=busyChanged)
    def submitting(self):
        return self._submitting

    @Property("QVariantList", notify=busyChanged)
    def publishing(self):
        return sorted(self._publishing)

    @Property("QVariantList", notify=busyChanged)
    def generatingKeys(self):
        """產生中的對象（Subject.key）：表單、明細頁的按鈕用它決定要不要忙碌。"""
        return sorted({report.subject.key for report in self._reports if report.status == GENERATING})

    @Property(str, notify=formChanged)
    def subjectError(self):
        return self._errors["subject"]

    @Property(str, notify=formChanged)
    def startDateError(self):
        return self._errors["start"]

    @Property(str, notify=formChanged)
    def endDateError(self):
        return self._errors["end"]

    @Property(str, notify=formChanged)
    def formError(self):
        return self._form_error

    @Property("QVariantList", notify=optionsChanged)
    def staffOptions(self):
        return [staff_option(*staff) for staff in self._staff]

    @Property(str, notify=optionsChanged)
    def staffState(self):
        return self._staff_state

    @Property("QVariantList", notify=optionsChanged)
    def serviceOptions(self):
        today = date.today()
        return [service_option(service_id, start, today) for service_id, start in self._recent]

    @Property(str, notify=optionsChanged)
    def servicesState(self):
        return self._services_state

    @Property(str, notify=optionsChanged)
    def servicesFor(self):
        return self._services_for

    @Property("QVariantMap", notify=statesChanged)
    def serviceStates(self):
        """服務人員分析頁的 Report 欄：{service_id: {"state": "published"／"unpublished", "reportId"}}；
        沒有的服務是還沒有 PDF（Not generated yet）。"""
        return {service_id: {"state": state, "reportId": report_id}
                for service_id, (state, report_id) in self._states.items()}

    @Property(bool, notify=statesChanged)
    def statesLoaded(self):
        return self._states_loaded

    @Property(bool, notify=listChanged)
    def polling(self):
        return self._timer.isActive()

    # ------------------------------------------------------------ 頁面打開、關掉、輪詢

    @Slot(str)
    def pageOpened(self, view):
        """報表頁（reports）、分析頁（analysis）、管理員明細頁（detail）打開：讀一次，需要時開始輪詢。"""
        if view not in VIEWS:
            return
        self._views[view] += 1
        if view == "reports" and not self._admin:
            self._seen = None  # 「第一次載入」：這次打開頁面讀到的不跳通知（PLAN 7.3）
        self._load(view)
        self._update_timer()

    @Slot(str)
    def pageClosed(self, view):
        if self._views[view] > 0:
            self._views[view] -= 1
        if self._views[view] == 0:
            del self._views[view]
        self._update_timer()

    @Slot()
    def poll(self):
        """計時器到了（測試也可以直接呼叫）：重新查開著的頁面要的資料。上一次還沒回來就略過。"""
        for view in list(self._views):
            self._load(view, polling=True)
        self._update_timer()

    @Slot()
    def refresh(self):
        """區塊錯誤的 Retry、明細頁的 Retry。"""
        self._refresh_list(expire=self._admin)

    def stop(self):
        """外殼關掉時：不再計時；之後才回來的結果不再開計時器。"""
        self._stopped = True
        self._timer.stop()

    def _load(self, view, polling=False):
        """管理員把逾時的 generating 改成 failed（多 1 個往返）：打開報表頁、輪詢（有產生中的才會輪詢）時做。
        明細頁打開時不做：那一頁的預算是檔案清單 1 個往返（T31），報表卡只多 1 個（CALL）。"""
        if view == "analysis":
            self._refresh_states()
        else:
            self._refresh_list(expire=self._admin and (view == "reports" or polling))

    def _update_timer(self):
        wanted = 0
        if not self._stopped and self.services is not None:
            if not self._admin and self._views:
                wanted = STAFF_POLL_S
            elif self._admin and ({"reports", "detail"} & set(self._views)) and any(
                    report.status == GENERATING for report in self._reports):
                wanted = ADMIN_POLL_S
        if not wanted:
            if self._timer.isActive():
                self._timer.stop()
                self.listChanged.emit()
            return
        if not self._timer.isActive() or self._timer.interval() != wanted * 1000:
            self._timer.start(wanted * 1000)
            self.listChanged.emit()

    # ------------------------------------------------------------ 清單

    def _refresh_list(self, expire=False):
        if self.services is None:
            return False
        db, account_id = self.services.db, self.account_id
        submitted = self.services.tasks.submit("db", LIST_KEY, lambda token, report: list_reports(db, account_id, expire),
                                               on_ok=self._listed, on_err=self._list_failed)
        if submitted:
            self._list_busy = True
            if self._list_state in ("idle", "error"):
                self._list_state = "loading"
            self.listChanged.emit()
        return submitted

    def _listed(self, reports):
        self._list_busy = False
        self._failures = 0
        self._error_detail = self._error_technical = ""
        self._apply(reports)
        self._list_state = "ready" if reports else "empty"
        self.listChanged.emit()
        self._update_timer()

    def _list_failed(self, exc):
        logger.warning("loading the reports failed: %r", exc)
        self._list_busy = False
        if self._list_state in ("ready", "empty"):  # 已經有清單：留著，下一次輪詢再試
            self.listChanged.emit()
            return
        self._failures += 1
        self._error_detail = reason_text(exc)
        self._error_technical = f"{type(exc).__name__}: {exc}"
        self._list_state = "error"
        self.listChanged.emit()

    def _apply(self, reports):
        """套用查到的清單：通知狀態的變化、更新列、維持選取、打開剛變成 ready 的那一份。"""
        today = date.today()
        if self._admin:
            self._notify_admin(reports)
        else:
            self._notify_staff(reports)
        self._reports = list(reports)
        self._list.replace([report_row(report, self._admin, today) for report in reports])
        self._follow_selection()
        self.busyChanged.emit()  # generatingKeys
        self.selectedChanged.emit()

    def _notify_admin(self, reports):
        """產生中的變成 ready 或 failed：跳通知（只有這一次執行看過它在產生中的）。"""
        for report in reports:
            before = self._statuses.get(report.report_id)
            if before == GENERATING and report.status == READY:
                self._report_ready(report)
            elif before == GENERATING and report.status == FAILED:
                self._report_failed(report)
        self._statuses = {report.report_id: report.status for report in reports}

    def _notify_staff(self, reports):
        """這次打開頁面之後才出現的報表（不是第一次載入）：跳「New report」。"""
        ids = {report.report_id for report in reports}
        if self._seen is not None and "reports" in self._views:
            fresh = [report for report in reports if report.report_id not in self._seen]
            if len(fresh) == 1:
                report = fresh[0]
                self._notify("info", _tr("New report"), _tr("%1 is ready to read.", report_title(report)),
                             toast_key(report.report_id), f"{OPEN_ACTION}:{report.report_id}", _tr("View"))
            elif fresh:
                self._notify("info", _tr("%1 new reports", len(fresh)), _tr("They're ready to read."),
                             "reports:new", f"{OPEN_ACTION}:{fresh[0].report_id}", _tr("View"))
        self._seen = ids if self._seen is None else self._seen | ids

    def _report_ready(self, report):
        if report.kind == COMPANY:
            detail = _tr("%1 is ready. Only admins can see company reports.", report_title(report))
        else:
            detail = _tr("%1 is ready. Check it, then publish it for staff.", report_title(report))
        self._notify("success", _tr("Report ready"), detail, toast_key(report.report_id),
                     f"{OPEN_ACTION}:{report.report_id}", _tr("Open"))

    def _report_failed(self, report):
        reason = report.failure_reason or _tr("This report couldn't be generated.")
        self._notify("error", _tr("Report wasn't generated"), _tr("%1: %2", report_title(report), reason),
                     toast_key(report.report_id), f"{RETRY_ACTION}:{report.report_id}", _tr("Retry"))

    def _notify(self, severity, title, detail, key, action_id="", action_label=""):
        return self.services.notifier.notify(severity, title, detail, key, action_id, action_label)

    def _find(self, report_id):
        return next((report for report in self._reports if report.report_id == report_id), None) if report_id else None

    def _service_report(self, service_id):
        if not service_id:
            return None
        return next((report for report in self._reports
                     if report.kind == SERVICE and report.service_id == service_id), None)

    # ------------------------------------------------------------ 選取、打開

    @Slot(str)
    @Slot(str, int)
    def select(self, report_id, target_height=0):
        """選一份（清單、通知的 Open、分析頁的 Report、明細頁的 Open）。ready 的就打開（下載、畫第 1 頁）；
        還在產生、失敗的，檢視器顯示狀態。清單還沒載入時先記著，載入後再打開。"""
        if target_height > 0:
            self._target_height = target_height
        changed = report_id != self._selected
        self._selected = report_id
        report = self._find(report_id)
        self._selected_subject = report.subject.key if report is not None else ""
        self._show_selected(force=True)  # 同一份再選一次也回到第 1 頁（和以前的 open() 一樣）
        if changed:
            self.selectedChanged.emit()

    @Slot(result=str)
    def firstReportId(self):
        """清單的第一份（最新的）；沒有是空字串。報表頁一進來選它。"""
        return self._reports[0].report_id if self._reports else ""

    @Slot(str)
    def requestOpen(self, report_id):
        """換到報表頁並選這一份（外殼接 openRequested）。"""
        if report_id:
            self.openRequested.emit(report_id)

    def _follow_selection(self):
        """清單換了：選到的那一份不在了（重新產生、Retry 之後被新的一列取代）就換成同一個對象最新的一列。"""
        if self._selected and self._find(self._selected) is None and self._selected_subject:
            replacement = next((report for report in self._reports
                                if report.subject.key == self._selected_subject), None)
            if replacement is not None:
                self._selected = replacement.report_id
        report = self._find(self._selected)
        if report is not None:
            self._selected_subject = report.subject.key
        self._show_selected(force=False)

    def _show_selected(self, force):
        if self._pdf is None:
            return
        report = self._find(self._selected)
        if report is not None and report.status == READY:
            if force or self._pdf.current != report.report_id:
                self._pdf.open_report(report.report_id, report.s3_key, report_title(report), self._target_height)
        elif self._pdf.current:
            self._pdf.clear()

    # ------------------------------------------------------------ 產生（管理員）

    @Slot(str, str, str, str, str, result=bool)
    def generate(self, kind, staff_id, service_id, start_text, end_text):
        """表單的 Generate Report。回傳是否送出：欄位有錯、同一個對象已經在產生中、上一份還在送，都不送。"""
        if not self._admin or self.services is None or self._submitting:
            return False
        subject, errors = validate(kind, staff_id, service_id, start_text, end_text)
        self._set_form(errors, "")
        if subject is None:
            return False
        if subject.key in self.generatingKeys:
            self._set_form(errors, _tr("This report is already being generated."))
            return False
        return self._create(subject, from_form=True)

    @Slot(str, result=bool)
    def generateForService(self, service_id):
        """明細頁的 Generate Report、Generate Again。"""
        if not self._admin or self.services is None or self._submitting or not service_id:
            return False
        subject = Subject(SERVICE, service_id=service_id)
        if subject.key in self.generatingKeys:
            return False
        return self._create(subject, from_form=False)

    @Slot(str, result=bool)
    def retry(self, report_id):
        """失敗的那一份再產生一次：同一個對象的新的一列（R5），舊的留著。"""
        report = self._find(report_id)
        if not self._admin or report is None or self.services is None or self._submitting:
            return False
        if report.subject.key in self.generatingKeys:
            return False
        return self._create(report.subject, from_form=False)

    @Slot(str)
    def clearFormError(self, field):
        """使用者改了那一格（subject、start、end）：那一格的錯誤先拿掉，下一次按 Generate Report 再檢查。"""
        errors = dict(self._errors)
        if field in errors:
            errors[field] = ""
        if field == "start" and errors["end"]:
            errors["end"] = ""  # 「To 早於 From」可能是改 From 解決的
        self._set_form(errors, "")

    @Slot(result="QVariantMap")
    def defaultPeriod(self):
        return default_period(date.today())

    def _set_form(self, errors, form_error):
        if errors != self._errors or form_error != self._form_error:
            self._errors, self._form_error = dict(errors), form_error
            self.formChanged.emit()

    def _create(self, subject, from_form):
        report_id = str(uuid.uuid4())
        db, created_by = self.services.db, self.account_id
        submitted = self.services.tasks.submit(
            "db", CREATE_KEY, lambda token, report: create_report(db, report_id, subject, created_by, created_by),
            on_ok=lambda reports: self._created(report_id, reports),
            on_err=lambda exc: self._create_failed(subject, from_form, exc))
        if submitted:
            self._set_submitting(True)
        return submitted

    def _created(self, report_id, reports):
        self._set_submitting(False)
        self._statuses[report_id] = GENERATING  # 之後看到 ready、failed 才跳通知
        self._listed(reports)
        self.select(report_id)
        self.reportCreated.emit(report_id)
        url = reports_api_url()
        if not self.services.tasks.submit("io", post_key(report_id), lambda token, report: post_report(url, report_id),
                                          on_err=lambda exc: self._post_failed(report_id, exc)):
            logger.warning("couldn't send the report request %s (task runner closed)", report_id)

    def _create_failed(self, subject, from_form, exc):
        self._set_submitting(False)
        if isinstance(exc, SubjectUnavailable):
            message = (_tr("%1 isn't a completed service.", subject.service_id) if subject.kind == SERVICE
                       else _tr("This staff member's account isn't available."))
        else:
            logger.warning("creating a report failed: %r", exc)
            message = reason_text(exc)
        if from_form:
            self._set_form(self._errors, message)
        else:
            self._notify("error", _tr("Couldn't start the report"), message, "reports:create")

    def _post_failed(self, report_id, exc):
        """分析服務沒收到：那一列改成 failed（db lane），原因寫進資料庫，畫面和通知都看得到。"""
        reason = failure_reason(exc)
        logger.warning("requesting report %s failed: %r", report_id, exc)
        db, account_id = self.services.db, self.account_id
        self.services.tasks.submit("db", fail_key(report_id),
                                   lambda token, report: mark_failed(db, report_id, reason, account_id),
                                   on_ok=self._listed, on_err=lambda error: self._mark_failed_failed(report_id, reason,
                                                                                                     error))

    def _mark_failed_failed(self, report_id, reason, exc):
        """連資料庫也寫不進去：那一列在資料庫裡還是 generating，10 分鐘後會變成 Timed out；先用通知說原因。"""
        logger.warning("marking report %s failed: %r", report_id, exc)
        report = self._find(report_id)
        title = report_title(report) if report is not None else report_id
        self._notify("error", _tr("Report wasn't generated"), _tr("%1: %2", title, reason), toast_key(report_id),
                     f"{RETRY_ACTION}:{report_id}" if report is not None else "",
                     _tr("Retry") if report is not None else "")

    def _set_submitting(self, submitting):
        if submitting != self._submitting:
            self._submitting = submitting
            self.busyChanged.emit()

    # ------------------------------------------------------------ 發佈（管理員）

    @Slot(str, result=bool)
    def publish(self, report_id):
        """Publish：只有 ready、還沒發佈的。發佈之後服務人員才看得到（R6）；沒有取消發佈。"""
        report = self._find(report_id)
        if (not self._admin or self.services is None or report is None or report.status != READY
                or report.published or report.kind == COMPANY or report_id in self._publishing):
            return False
        db, account_id = self.services.db, self.account_id
        submitted = self.services.tasks.submit(
            "db", publish_key(report_id), lambda token, task: publish_report(db, report_id, account_id),
            on_ok=lambda result: self._published(report_id, *result),
            on_err=lambda exc: self._publish_failed(report_id, exc))
        if submitted:
            self._publishing.add(report_id)
            self.busyChanged.emit()
        return submitted

    def _published(self, report_id, changed, reports):
        self._publishing.discard(report_id)
        self._listed(reports)
        report = self._find(report_id)
        if report is not None and report.published:
            self._notify("success", _tr("Report published"), _tr("Staff can now see %1.", report_title(report)),
                         toast_key(report_id))
        else:
            self._notify("warning", _tr("Report wasn't published"),
                         _tr("A newer report replaced it, or it isn't ready."), toast_key(report_id))

    def _publish_failed(self, report_id, exc):
        logger.warning("publishing report %s failed: %r", report_id, exc)
        self._publishing.discard(report_id)
        self.busyChanged.emit()
        self._notify("error", _tr("Report wasn't published"), reason_text(exc), toast_key(report_id))

    # ------------------------------------------------------------ 表單的選單（管理員）

    @Slot()
    def loadStaff(self):
        """員工選單：第一次打開表單時查（1 個往返）；之後用同一份，失敗了再按一次就重查。"""
        if self.services is None or self._staff_state in ("loading", "ready", "empty"):
            return
        db = self.services.db
        if self.services.tasks.submit("db", STAFF_KEY, lambda token, report: list_staff(db),
                                      on_ok=self._staff_loaded, on_err=self._staff_failed):
            self._staff_state = "loading"
            self.optionsChanged.emit()

    @Slot(str)
    def loadServices(self, staff_id):
        """這位員工最近分析完成的服務（1 個往返）。換人時取代還沒回來的查詢。"""
        if self.services is None or not staff_id:
            return
        if staff_id == self._services_for and self._services_state in ("loading", "ready", "empty"):
            return
        db = self.services.db
        self._services_for, self._recent, self._services_state = staff_id, [], "loading"
        self.optionsChanged.emit()
        self.services.tasks.submit("db", SERVICES_KEY, lambda token, report: list_recent_services(db, staff_id),
                                   on_ok=lambda rows: self._services_loaded(staff_id, rows),
                                   on_err=lambda exc: self._services_failed(staff_id, exc), replace=True)

    def _staff_loaded(self, staff):
        self._staff = staff
        self._staff_state = "ready" if staff else "empty"
        self.optionsChanged.emit()

    def _staff_failed(self, exc):
        logger.warning("loading the staff list failed: %r", exc)
        self._staff_state = "error"
        self.optionsChanged.emit()

    def _services_loaded(self, staff_id, rows):
        if staff_id != self._services_for:
            return
        self._recent = rows
        self._services_state = "ready" if rows else "empty"
        self.optionsChanged.emit()

    def _services_failed(self, staff_id, exc):
        if staff_id != self._services_for:
            return
        logger.warning("loading the services of %s failed: %r", staff_id, exc)
        self._services_state = "error"
        self.optionsChanged.emit()

    # ------------------------------------------------------------ 分析頁的 Report 欄（服務人員）、明細頁（管理員）

    def _refresh_states(self):
        if self.services is None or self._admin:
            return False
        db, account_id = self.services.db, self.account_id
        return self.services.tasks.submit("db", STATES_KEY,
                                          lambda token, report: list_service_states(db, account_id),
                                          on_ok=self._states_arrived, on_err=self._states_failed)

    def _states_arrived(self, states):
        if states != self._states or not self._states_loaded:
            self._states, self._states_loaded = states, True
            self.statesChanged.emit()

    def _states_failed(self, exc):
        logger.warning("loading the report states failed: %r", exc)  # Report 欄維持上一次的（或「—」）

    @Slot(str)
    def watchService(self, service_id):
        """管理員明細頁打開了哪一筆服務：detailReport 跟著它。"""
        if service_id != self._detail_service:
            self._detail_service = service_id
            self.selectedChanged.emit()

    # ------------------------------------------------------------ 通知上的動作

    @Slot(str, int)
    def _on_action(self, action_id, toast_id):
        kind, _, report_id = action_id.partition(":")
        if not report_id:
            return
        if kind == OPEN_ACTION:
            self.openRequested.emit(report_id)
        elif kind == RETRY_ACTION:
            self.retry(report_id)
