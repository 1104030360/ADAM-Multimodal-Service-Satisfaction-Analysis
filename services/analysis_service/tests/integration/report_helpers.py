# tests/integration/report_helpers.py — 報表整合測試的小工具（analysis_service/ 與 analyzer/ 共用同一份）
# 都用測試自己的 root 連線（conftest.py 的 db）準備資料、讀結果；服務本身用最小權限的 adam_analysis。
import io
import threading
import uuid

from pypdf import PdfReader

ADMIN_ACCOUNT = "00000000-0000-0000-0000-000000000001"  # 測試用 schema（myapp/tests/fixtures/schema.sql）的種子帳號
STAFF_ACCOUNT = "00000000-0000-0000-0000-000000000002"
SEED_ACCOUNTS = (ADMIN_ACCOUNT, STAFF_ACCOUNT)
SCORES = ("customer_total", "server_total", "customer_text", "server_text",
          "customer_audio", "server_audio", "customer_facial", "server_facial")


def clean(db):
    """報表會擋住刪服務（外鍵 RESTRICT）：先刪報表，再刪服務（分數、影音、圖表 CASCADE），最後刪測試加的帳號。"""
    db.query("DELETE FROM tbl_report")
    db.query("DELETE FROM tbl_service")
    db.query("DELETE FROM tbl_account WHERE account_id NOT IN (%s, %s)", *SEED_ACCOUNTS)
    db.query("UPDATE tbl_account SET account_photo_key = NULL")


def add_account(db, account_id, name, title=None, photo_key=None, level="1"):
    db.query("INSERT INTO tbl_account (account_id, account_username, account_password, account_name, "
             "account_level, account_occupation_name, account_photo_key) VALUES (%s, %s, 'x', %s, %s, %s, %s)",
             account_id, f"user-{account_id}", name, level, title, photo_key)


def set_account(db, account_id, **columns):
    for column, value in columns.items():
        db.query(f"UPDATE tbl_account SET {column} = %s WHERE account_id = %s", value, account_id)


def add_scored_service(db, sid, start, end=None, status=3, account=STAFF_ACCOUNT, **scores):
    """一筆服務，加上分數列（沒給的分數是 -1）。scores=None 表示沒有分數列。"""
    db.query("INSERT INTO tbl_service (service_id, account_id, service_start, service_end, service_status) "
             "VALUES (%s, %s, %s, %s, %s)", sid, account, start, end, status)
    if scores.pop("no_scores", False):
        return
    values = [scores.get(column, -1) for column in SCORES]
    db.query(f"INSERT INTO tbl_service_score (service_id, {', '.join(SCORES)}) VALUES (%s{', %s' * len(SCORES)})",
             sid, *values)


def add_report(db, kind, service_id=None, staff=None, start=None, end=None, status="generating",
               created_at=None, s3_key=None, report_id=None):
    """桌面端按 Generate 時寫的那一列（reports_contract.md 第 1 點）。回傳 report_id。"""
    report_id = report_id or str(uuid.uuid4())
    db.query("INSERT INTO tbl_report (report_id, report_kind, service_id, staff_account_id, period_start, "
             "period_end, report_status, s3_key, created_by, created_at) "
             "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW(3)))",
             report_id, kind, service_id, staff, start, end, status, s3_key or f"reports/{report_id}.pdf",
             ADMIN_ACCOUNT, created_at)
    return report_id


def report_row(db, report_id):
    columns = ["report_status", "failure_reason", "ai_model", "ai_prompt_version", "published_at", "published_by"]
    rows = db.query(f"SELECT {', '.join(columns)} FROM tbl_report WHERE report_id = %s", report_id)
    return dict(zip(columns, rows[0])) if rows else None


def pdf_text(data):
    """PDF 裡所有的字（pypdf 依位置排好行、字之間加空白），連續的空白、換行都變成一個空白。"""
    return " ".join("\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages).split())


class Gate:
    """讓背景工作停在某一步：替換的函式先等 open()，測試就能在「正在產生」的時候做事。"""

    def __init__(self, function=None):
        self.function = function
        self.entered = threading.Event()
        self.opened = threading.Event()
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        self.opened.wait(30)
        return self.function(*args, **kwargs) if self.function else None

    def open(self):
        self.opened.set()


def wait_for_threads(prefix, timeout=30):
    """超時的報表留在背景跑完：等名字是 prefix 開頭的執行緒都結束，才不會影響下一個測試。"""
    for thread in threading.enumerate():
        if thread.name.startswith(prefix):
            thread.join(timeout)
            assert not thread.is_alive(), f"{thread.name} {timeout} 秒內沒有結束"
