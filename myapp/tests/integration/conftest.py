"""整合測試的 fixture：拋棄式 MySQL 8.4、每個測試前清空資料、假的分析服務、QML engine。"""
import json
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import mysql.connector
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtQml import QQmlApplicationEngine

from myapp.db_lane import DbLane
from myapp.notifications import Notifier
from myapp.services import CLOSE_DB_KEY, AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs
from tests.integration.helpers import Database, wait_until
from tests.integration.latency_proxy import db_proxy  # noqa: F401  fixture：app 的 DB_* 改走計算往返次數的 proxy
from tests.integration.mysql_server import native_mysql, wait_for_mysql

SCHEMA = Path(__file__).resolve().parents[1] / "fixtures" / "schema.sql"
MYSQL_IMAGE = "mysql:8.4"
TABLES = ["tbl_report", "tbl_text_storage", "tbl_charts_storage", "tbl_audio_storage", "tbl_video_storage",
          "tbl_service_score", "tbl_service", "tbl_account",
          "tbl_coaching_event", "tbl_coaching_marker", "tbl_coaching_action"]   # PLAN1 W2（T45）


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, **kwargs)


@pytest.fixture(scope="session")
def mysql_server(pytestconfig):
    """整個測試期間共用一個拋棄式 MySQL 8.4；CI 缺少前置條件時必須失敗。"""
    def unavailable(reason):
        if pytestconfig.getoption("--require-integration"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    if not SCHEMA.exists():
        unavailable(f"找不到 {SCHEMA}")
    if pytestconfig.getoption("--mysql-backend") == "native":
        with native_mysql(SCHEMA) as server:
            yield server
        return
    if shutil.which("docker") is None or docker("info").returncode != 0:
        unavailable("Docker 無法使用，略過整合測試")
    if docker("image", "inspect", MYSQL_IMAGE).returncode != 0:
        unavailable(f"本機沒有 {MYSQL_IMAGE}，先執行 docker pull {MYSQL_IMAGE}")

    name = f"adam-pytest-mysql-{secrets.token_hex(4)}"  # 名稱固定開頭，萬一留下來，用 docker ps -a 找得到
    password = secrets.token_urlsafe(16)
    try:
        started = docker("run", "-d", "--name", name, "-p", "127.0.0.1::3306", "--tmpfs", "/var/lib/mysql",
                         "-e", f"MYSQL_ROOT_PASSWORD={password}", MYSQL_IMAGE)
        assert started.returncode == 0, started.stderr
        port = int(docker("port", name, "3306/tcp").stdout.strip().rsplit(":", 1)[1])
        server = {"host": "127.0.0.1", "port": port, "user": "root", "password": password}
        wait_for_mysql(server)
        with SCHEMA.open("rb") as schema:  # 用容器裡的 mysql 指令載入：它看得懂 DELIMITER
            loaded = subprocess.run(["docker", "exec", "-i", "-e", f"MYSQL_PWD={password}", name, "mysql", "-uroot"],
                                    stdin=schema, capture_output=True, timeout=120)
        assert loaded.returncode == 0, loaded.stderr.decode()
        yield server
    finally:
        docker("rm", "--force", "--volumes", name)


@pytest.fixture(scope="session")
def seed_accounts(mysql_server):
    """測試 schema 放進去的佔位帳號；每個測試開始時放回去。"""
    connection = mysql.connector.connect(**mysql_server, database="adam")
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM tbl_account")
        rows = cursor.fetchall()
    connection.close()
    return rows


@pytest.fixture
def db(mysql_server, seed_accounts, monkeypatch):
    """每個測試前清空所有資料表、放回佔位帳號，並把 app 的 DB_* 環境變數指到測試資料庫。"""
    monkeypatch.setenv("DB_HOST", mysql_server["host"])
    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))
    monkeypatch.setenv("DB_NAME", "adam")
    monkeypatch.setenv("DB_USER", mysql_server["user"])
    monkeypatch.setenv("DB_PASSWORD", mysql_server["password"])
    connection = mysql.connector.connect(**mysql_server, database="adam", autocommit=True)
    database = Database(connection)
    database.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in TABLES:
        database.execute(f"TRUNCATE TABLE {table}")
    database.execute("SET FOREIGN_KEY_CHECKS = 1")
    with connection.cursor() as cursor:
        # 欄位數照 schema（new-feature 加了 account_photo_key）：SELECT * 拿到幾欄就放回幾欄
        cursor.executemany(f"INSERT INTO tbl_account VALUES ({', '.join(['%s'] * len(seed_accounts[0]))})",
                           seed_accounts)
    yield database
    connection.close()


@pytest.fixture
def analysis_service(monkeypatch):
    """假的分析服務：記下收到的 JSON（路徑, 內容），回傳 status（預設 200）。ANALYZE_API_URL 會指到它。
    /reports（報表，new-feature N4）回傳 report_status（預設 202，和真的一樣），桌面端由 ANALYZE_API_URL 推出這個位址。"""
    service = SimpleNamespace(status=200, report_status=202, requests=[])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            service.requests.append((self.path, json.loads(body)))
            self.send_response(service.report_status if self.path == "/reports" else service.status)
            self.end_headers()

        def log_message(self, *args):  # 不要把每個請求印到終端機
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{server.server_port}/analyze")
    yield service
    server.shutdown()
    server.server_close()


@pytest.fixture
def services(db):
    """登入後共用的背景服務，和 main.py 的一樣：真的 TaskRunner、Notifier、UiPrefs，DbLane 連到這個測試的資料庫。"""
    services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
    yield services
    if services.close_db():  # 連線只能在 db lane 上關；等它關好，不留連線給下一個測試
        wait_until(lambda: not services.tasks.isBusy(CLOSE_DB_KEY))
    services.tasks.shutdown()


@pytest.fixture
def engine():
    """這個測試自己的 QML engine；結束時刪掉，它開的視窗也會一起關掉。"""
    engine = QQmlApplicationEngine()
    yield engine
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
