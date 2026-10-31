"""整合測試用的小工具：放測試資料、讀 QML 畫面。"""
import socket

from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem  # noqa: F401  import 之後，PySide6 才會把 QML 物件轉成 QQuickItem

ADMIN_ID = "00000000-0000-0000-0000-000000000001"  # db_schema.sql 的佔位帳號
STAFF_ID = "00000000-0000-0000-0000-000000000002"
STAFF_NAME = "服務人員（佔位）"
SCORE_COLUMNS = ("total", "text", "audio", "facial")


class Database:
    """測試自己的資料庫連線（root、autocommit）：放測試資料、檢查 app 寫進去的結果。"""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)

    def rows(self, sql, params=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def add_account(self, account_id, username, name, level="1"):
        self.execute("INSERT INTO tbl_account VALUES (%s, %s, 'not-a-hash', %s, %s, NULL)",
                     (account_id, username, name, level))

    def add_service(self, service_id, start, status=3, account_id=STAFF_ID):
        self.execute("INSERT INTO tbl_service (service_id, account_id, service_start, service_end, service_status) "
                     "VALUES (%s, %s, %s, %s + INTERVAL 5 MINUTE, %s)", (service_id, account_id, start, start, status))

    def add_scores(self, service_id, customer, server, **columns):
        """顧客的 4 個分數都是 customer、服務人員的都是 server；個別欄位用 columns 覆寫。"""
        scores = {f"customer_{c}": customer for c in SCORE_COLUMNS} | {f"server_{c}": server for c in SCORE_COLUMNS}
        scores |= columns
        names = ", ".join(scores)
        self.execute(f"INSERT INTO tbl_service_score (service_id, {names}) VALUES (%s{', %s' * len(scores)})",
                     (service_id, *scores.values()))

    def add_media(self, service_id, table, url):
        """table：video、audio 或 charts。名稱取 URL 的檔名，和 app 的寫法一樣。"""
        name = url.rsplit("/", 1)[-1]
        self.execute(f"INSERT INTO tbl_{table}_storage (service_id, {table}_name, {table}_url) VALUES (%s, %s, %s)",
                     (service_id, name, url))

    def status(self, service_id):
        return self.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (service_id,))[0][0]


def qml(obj, expression):
    """在 obj 所屬的 QML 環境執行一段 JavaScript 並回傳結果；可以直接用 .qml 檔裡的 id。"""
    context = QQmlEngine.contextForObject(obj)
    assert context is not None, f"{obj} 不是 QML 建立的物件"
    expr = QQmlExpression(context, obj, expression)
    value, is_undefined = expr.evaluate()
    assert not expr.hasError(), expr.error().toString()
    return None if is_undefined else value


def table_row(model, row):
    """像 QML 的表格一樣，用角色名稱（roleNames）讀出一列：{"service_id": ..., ...}。"""
    names = {bytes(name).decode(): role for role, name in model.roleNames().items()}
    index = model.index(row, 0)
    return {name: model.data(index, names[name]) for name in ("service_id", "name", "start_datetime")}


def find_button(root, text):
    """在 root 底下（畫面上的子元件）找文字是 text 的按鈕。"""
    for item in root.childItems():
        if item.inherits("QQuickAbstractButton") and item.property("text") == text:
            return item
        found = find_button(item, text)
        if found is not None:
            return found
    return None


def unused_port():
    """一個目前沒有程式在聽的 port（連過去會馬上被拒絕）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def freeze_db_clock(connector, moment):
    """讓 app 那條連線的 NOW()、CURDATE() 停在 moment（MySQL 的 SET TIMESTAMP，只影響這條連線）。"""
    with connector.connection.cursor() as cursor:
        cursor.execute("SET TIMESTAMP = UNIX_TIMESTAMP(%s)", (moment,))
