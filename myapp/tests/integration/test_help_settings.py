"""設定頁的 Help 區塊（new-feature S3、S5，T40），兩端都跑：

- About：有 Briefcase 的 metadata 時寫版本，沒有時寫 Unavailable。
- Check Connection：真的 MySQL（SELECT 1）、moto 的 S3、本機的 /healthz；全部在背景 lane、不重複送出。
  連不上、逾時的時候頁面照樣打開，三項各自寫原因；app 的其他頁面照常可以用。
- Copy Diagnostic Summary：剪貼簿裡只有白名單的欄位，沒有帳密、主機、網址。
- Storage：快取大小、剩餘空間；Clear Download Cache 先確認、只清快取，錄影和還沒存完的錄影留著；
  有下載正在用快取時停用；清完之後明細頁可以再下載、報表頁會重畫頁面。
- 每個測試都要求 QML 零警告，GUI 執行緒碰網路就失敗。
"""
import importlib.metadata
import json
import os
import socket
import threading
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import fitz
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject
from PySide6.QtGui import QColor, QGuiApplication, QImage

from myapp import diagnostics, media
from myapp.db_lane import DbLane
from myapp.init import admin_home_init, server_home_init
from myapp.notifications import Notifier
from myapp.pending_saves import PendingSave, PendingSaveStore
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, toasts, wait_until

pytestmark = pytest.mark.integration

SID = "S20261030120000"
START = "2026-10-30 12:00:00"
SIDES = pytest.mark.parametrize("home_init, account_id, page, detail_page, reports_page", [
    (server_home_init, STAFF_ID, "server_setting_button.qml", "server_service_detail_page.qml",
     "server_reports_page.qml"),
    (admin_home_init, ADMIN_ID, "admin_setting_page.qml", "admin_service_detail_page.qml", "admin_reports_page.qml"),
], ids=["staff", "admin"])


# ---------------------------------------------------------------- fixture 與小工具


@pytest.fixture
def qml_warnings(engine):
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


@pytest.fixture
def probes_off_gui_thread(monkeypatch):
    """三項探測只能在背景 lane 執行（no_network_on_gui_thread 管不到 requests.get 和探測用的 S3 client）。"""
    violations = []

    def guarded(name, original):
        def call(*args, **kwargs):
            if threading.current_thread() is threading.main_thread():
                violations.append(name)
                raise AssertionError(f"{name} ran on the GUI thread")
            return original(*args, **kwargs)
        return call

    monkeypatch.setattr(diagnostics, "check_database", guarded("check_database", diagnostics.check_database))
    for name in ("files", "analysis"):
        monkeypatch.setitem(diagnostics.PROBES, name, guarded(name, diagnostics.PROBES[name]))
    monkeypatch.setattr(diagnostics.requests, "get", guarded("requests.get", diagnostics.requests.get))
    yield violations
    assert violations == []


@contextmanager
def health_server(body=None):
    """分析服務的替身：只回 GET /healthz；記下收到的 (方法, 路徑)。POST 一律 405（健康檢查不能送分析）。"""
    state = SimpleNamespace(requests=[])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.requests.append(("GET", self.path))
            data = json.dumps(body or {"status": "ok", "models": "ready", "queue_depth": 0}).encode()
            self.send_response(200 if self.path == "/healthz" else 404)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            state.requests.append(("POST", self.path))
            self.send_response(405)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    state.port = server.server_port
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def healthz(monkeypatch):
    with health_server() as server:
        monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{server.port}/analyze")
        yield server


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Opened(SimpleNamespace):
    """打開的設定頁。page 要留著參照：頁面的 wrapper 留著，子物件的 wrapper 才不會失效（T17）。"""


def open_settings(engine, home_init, account_id, page, services):
    home = home_init.launch(engine, account_id, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in launch_keys(home_init)))
    item = show(window, page)
    return Opened(home=home, window=window, page=item, view=item.findChild(QObject, "settingsView"))


def launch_keys(home_init):
    if home_init is server_home_init:
        return server_home_init.LAUNCH_KEYS
    return ()


def show(window, page):
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def find(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def find_all(root, name):
    found, pending = [], [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
    return found


def row(view, name):
    """一項檢查（databaseCheck、filesCheck、analysisCheck）：(狀態標籤, 訊息, 檢查時間是否顯示)。"""
    check = find(view, name)
    return (find(check, "chipLabel").property("text"), find(check, "checkMessage").property("text"),
            find(check, "checkTime").isVisible())


def png_bytes():
    image = QImage(16, 12, QImage.Format.Format_RGB32)
    image.fill(QColor("#2E7D32"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write(path, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# ---------------------------------------------------------------- About


@SIDES
@pytest.mark.parametrize("metadata, version", [
    ({"Formal-Name": "myapp", "Version": "0.0.1"}, "0.0.1"), (None, "Unavailable")], ids=["metadata", "none"])
def test_about_shows_the_app_version_or_says_unavailable(engine, services, qml_warnings, monkeypatch, home_init,
                                                         account_id, page, detail_page, reports_page, metadata,
                                                         version):
    def read(name):
        if metadata is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return metadata

    monkeypatch.setattr(importlib.metadata, "metadata", read)
    opened = open_settings(engine, home_init, account_id, page, services)
    view = opened.view
    help_section = find(view, "helpSettings")
    assert help_section.isVisible() and help_section.property("title") == "Help"
    version_row = find(view, "aboutVersion")
    assert version_row.property("value") == version
    assert qml(version_row, "Accessible.name") == f"Version: {version}"
    assert find(view, "aboutPython").property("value").startswith("3.13.")
    assert find(view, "aboutQt").property("value").startswith("6.")
    assert find(view, "aboutSystem").property("value").startswith("macOS ")


# ---------------------------------------------------------------- Check Connection


@SIDES
def test_check_connection_reports_each_service_and_does_not_send_twice(engine, services, db, s3, healthz,
                                                                         qml_warnings, no_network_on_gui_thread,
                                                                         probes_off_gui_thread, home_init,
                                                                         account_id, page, detail_page,
                                                                         reports_page):
    opened = open_settings(engine, home_init, account_id, page, services)
    home, view = opened.home, opened.view
    assert row(view, "databaseCheck") == ("Not Checked", "Keeps accounts, services and scores.", False)
    button = find(view, "checkConnectionButton")
    assert button.property("text") == "Check Connection" and button.property("enabled")

    qml(button, "click()")
    assert button.property("busy")
    assert {row(view, name)[0] for name in ("databaseCheck", "filesCheck", "analysisCheck")} <= {
        "Checking…", "Available"}
    assert not home.helpCenter.connection.check()  # 還在檢查：不重複送出
    wait_until(lambda: not home.helpCenter.connection.checking, timeout=15)
    assert not button.property("busy")
    assert row(view, "databaseCheck") == ("Available", "Connected to the database.", True)
    assert row(view, "filesCheck") == (
        "Available", "Cloud storage answered and accepted this computer's storage credentials.", True)
    assert row(view, "analysisCheck") == ("Available", "The analysis service is running.", True)
    assert healthz.requests == [("GET", "/healthz")]  # 健康檢查從來不送 /analyze
    assert qml(find(view, "databaseCheck"), "Accessible.name") == "Database: Available"
    assert find(view, "helpSettings").property("announced").toVariant() == ["Connection check finished"]

    details = find(find(view, "filesCheck"), "checkDetailsButton")
    technical = find(find(view, "filesCheck"), "checkTechnical")
    assert details.isVisible() and not technical.isVisible()
    qml(details, "click()")
    assert technical.isVisible() and technical.property("text").startswith("OK · GetObject on a missing test file")
    assert toasts(services.notifier) == []


@SIDES
def test_offline_checks_fail_with_reasons_and_the_rest_of_the_app_keeps_working(engine, qml_warnings, monkeypatch,
                                                                                  home_init, account_id, page,
                                                                                  detail_page, reports_page):
    """資料庫、雲端儲存、分析服務都連不上：設定頁照樣打開，三項各自寫原因；其他頁面照常可以打開。"""
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", str(closed_port()))
    monkeypatch.setenv("DB_NAME", "adam")
    monkeypatch.setenv("DB_USER", "nobody")
    monkeypatch.setenv("S3_BUCKET", "adam-media")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_ENDPOINT_URL", f"http://127.0.0.1:{closed_port()}")
    monkeypatch.setenv("ANALYZE_API_URL", f"http://127.0.0.1:{closed_port()}/analyze")
    services = AppServices(TaskRunner(), Notifier(), DbLane(connect_timeout=2), UiPrefs())
    try:
        home = home_init.launch(engine, account_id, services)
        window = engine.rootObjects()[-1]
        settings_page = show(window, page)
        view = settings_page.findChild(QObject, "settingsView")
        assert find(view, "helpSettings").isVisible()
        qml(find(view, "checkConnectionButton"), "click()")
        wait_until(lambda: not home.helpCenter.connection.checking, timeout=20)
        assert row(view, "databaseCheck") == (
            "Failed", "Can't reach the database. Check your network and try again.", True)
        assert row(view, "filesCheck") == ("Failed", "Can't reach cloud storage. Check your network and try again.",
                                           True)
        assert row(view, "analysisCheck") == (
            "Failed", "Can't reach the analysis service. Check your network and try again.", True)
        assert find(view, "checkConnectionButton").property("enabled")  # 可以再試一次
        titles = [toast["title"] for toast in toasts(services.notifier)]  # 檢查失敗不跳 toast（啟動時的載入失敗會）
        assert "Can't reach cloud storage" not in titles and "Can't reach the analysis service" not in titles
        # 診斷失敗不停用任何東西：換到其他頁面照常打開，側邊欄的按鈕都能按
        services_page = "server_service_page.qml" if home_init is server_home_init else "admin_service_page.qml"
        other_page = show(window, services_page)
        assert other_page is not None
        assert all(qml(window, f"{button}.enabled") for button in ("homeButton", "exitButton"))
    finally:
        services.tasks.shutdown()


# ---------------------------------------------------------------- Copy Diagnostic Summary


@SIDES
def test_copy_diagnostic_summary_copies_only_the_whitelist(engine, services, db, s3, healthz, qml_warnings,
                                                           monkeypatch, home_init, account_id, page, detail_page,
                                                           reports_page):
    monkeypatch.setenv("AWS_SESSION_TOKEN", "FAKE-SECRET-SESSION-TOKEN")
    opened = open_settings(engine, home_init, account_id, page, services)
    home, view = opened.home, opened.view
    qml(find(view, "checkConnectionButton"), "click()")
    wait_until(lambda: not home.helpCenter.connection.checking, timeout=15)
    QGuiApplication.clipboard().setText("")
    copy = find(view, "copySummaryButton")
    assert copy.property("text") == "Copy Diagnostic Summary"
    qml(copy, "click()")
    text = QGuiApplication.clipboard().text()
    assert text.startswith("ADAM diagnostic summary\n")
    assert "Database: Available at " in text and "Cloud Storage: Available at " in text
    assert "Analysis Service: Available at " in text
    secrets = [os.environ[name] for name in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "S3_BUCKET",
                                             "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")]
    for value in [*secrets, str(healthz.port), os.environ["ANALYZE_API_URL"], "http", "s3://", STAFF_ID, ADMIN_ID]:
        assert value not in text, value
    assert "Diagnostic summary copied" in [toast["title"] for toast in toasts(services.notifier)]


# ---------------------------------------------------------------- Storage


def remember_pending(folder, service_id="S20261101093000"):
    files = (write(folder / "video" / f"{service_id}_cam0.avi"), write(folder / "video" / f"{service_id}_cam1.avi"),
             write(folder / "audio" / f"{service_id}_audio.wav"))
    PendingSaveStore().put(PendingSave(service_id, STAFF_ID, tuple(str(path) for path in files),
                                       datetime(2026, 11, 1, 9, 30), datetime(2026, 11, 1, 9, 35), "upload"))
    return files


@SIDES
def test_clear_download_cache_asks_first_and_removes_only_the_cache(engine, services, qml_warnings, tmp_path,
                                                                     home_init, account_id, page, detail_page,
                                                                     reports_page):
    cached = [write(tmp_path / "media" / f"{SID}_cam0.avi", 1_200_000), write(tmp_path / "charts" / "c.png", 300_000),
              write(tmp_path / "reports" / "r-p1.png", 500_000)]
    recordings = [write(tmp_path / "video" / "S20261101080000_cam0.avi"),
                  write(tmp_path / "audio" / "S20261101080000_audio.wav")]
    pending = remember_pending(tmp_path)
    opened = open_settings(engine, home_init, account_id, page, services)
    home, view = opened.home, opened.view
    storage = home.helpCenter.storage
    wait_until(lambda: storage.state == "ready")
    assert find(view, "cacheSize").property("value") == "2.0 MB"
    free = find_all(find(view, "storageGroup"), "freeSpace")
    assert [item.property("label") for item in free] == ["Free space"]
    assert " free of " in free[0].property("value")
    button = find(view, "clearCacheButton")
    assert button.property("enabled") and not find(view, "clearBlockedReason").isVisible()

    dialog = find(view, "clearCacheDialog")
    qml(button, "click()")
    wait_until(lambda: dialog.property("visible"))
    assert dialog.property("title") == "Clear the download cache?"
    assert dialog.property("body") == (
        "This removes 2.0 MB of downloaded videos, charts and reports from this computer. They download again when "
        "you open them. Recordings, unfinished uploads and cloud storage aren't changed.")
    qml(find(dialog, "cancelButton"), "click()")  # Keep Cache：什麼都不做
    wait_until(lambda: not dialog.property("visible"))
    assert all(path.exists() for path in cached)

    qml(button, "click()")
    wait_until(lambda: dialog.property("visible"))
    qml(find(dialog, "confirmButton"), "click()")  # Clear Cache
    wait_until(lambda: not storage.clearing and storage.state == "ready")
    assert not any(path.exists() for path in cached)
    assert all(path.exists() for path in (*recordings, *pending))  # 錄影、還沒存完的錄影都留著
    assert find(view, "cacheSize").property("value") == "Empty"
    assert not button.property("enabled")
    assert find(view, "clearBlockedReason").property("text") == "The download cache is empty."
    assert ("success", "Download cache cleared") in [(t["severity"], t["title"]) for t in toasts(services.notifier)]


@SIDES
def test_clearing_is_disabled_while_a_download_is_still_running(engine, services, qml_warnings, tmp_path, home_init,
                                                                  account_id, page, detail_page, reports_page):
    write(tmp_path / "media" / f"{SID}_cam0.avi", 1000)
    opened = open_settings(engine, home_init, account_id, page, services)
    home, view = opened.home, opened.view
    storage = home.helpCenter.storage
    wait_until(lambda: storage.state == "ready")
    button, reason = find(view, "clearCacheButton"), find(view, "clearBlockedReason")
    assert button.property("enabled")
    release = threading.Event()
    key = media.task_key(SID, f"{SID}_cam1.avi")  # 離開明細頁之後還在收尾的下載
    services.tasks.submit("io", key, lambda token, report: release.wait(10))
    assert not button.property("enabled") and reason.isVisible()
    assert reason.property("text") == "A video, chart, report or photo is downloading or opening. Try again when it's done."
    assert not storage.clear()
    release.set()
    wait_until(lambda: button.property("enabled"))
    assert not reason.isVisible()


@SIDES
def test_downloads_and_report_pages_work_again_after_clearing(engine, services, db, s3, qml_warnings, tmp_path,
                                                               monkeypatch, home_init, account_id, page, detail_page,
                                                               reports_page):
    """清掉之後：明細頁重新下載，報表頁重新下載 PDF、重新畫頁面（不顯示已經刪掉的圖，T37）。"""
    db.add_service(SID, START, account_id=STAFF_ID)
    key = f"charts/{SID}_summary.png"
    db.add_media(SID, "charts", f"s3://adam-test/{key}")
    s3.put_object(Bucket="adam-test", Key=key, Body=png_bytes())
    report_id = "7e57c1ea-0000-4000-8000-000000000001"  # 一份已發佈的報表（兩端都看得到），PDF 在 S3
    document = fitz.Document()
    document.new_page().insert_text((72, 72), "Page 1")
    s3.put_object(Bucket="adam-test", Key=f"reports/{report_id}.pdf", Body=document.tobytes())
    document.close()
    db.execute("INSERT INTO tbl_report (report_id, report_kind, service_id, staff_account_id, report_status, s3_key, "
               "created_by, published_at, published_by) VALUES (%s, 'service', %s, %s, 'ready', %s, %s, NOW(), %s)",
               (report_id, SID, STAFF_ID, f"reports/{report_id}.pdf", ADMIN_ID, ADMIN_ID))

    opened = open_settings(engine, home_init, account_id, page, services)
    home, window = opened.home, opened.window
    reports = show(window, reports_page)
    wait_until(lambda: home.pdfmodel.viewState == "ready")
    rendered = media.reports_cache_dir()
    downloaded = media.report_pdf_dir()
    assert os.listdir(rendered) and os.listdir(downloaded)
    home.load_service_media(SID, START)
    detail = show(window, detail_page)
    chart = tmp_path / "charts" / f"{SID}_summary.png"
    wait_until(chart.exists)

    settings_page = show(window, page)
    storage = home.helpCenter.storage
    wait_until(lambda: storage.canClear)
    assert storage.clear()
    wait_until(lambda: not storage.clearing and storage.state == "ready")
    assert not chart.exists() and os.listdir(rendered) == [] and os.listdir(downloaded) == []
    assert home.pdfmodel.current == "" and home.pdfmodel.pageSource == ""

    reports = show(window, reports_page)  # 重新打開選到的那一份：重新下載、重新畫
    wait_until(lambda: home.pdfmodel.viewState == "ready")
    assert os.listdir(downloaded)
    assert os.path.exists(media.QUrl(home.pdfmodel.pageSource).toLocalFile())
    image = find_all(reports, "pageImage")[0]
    wait_until(lambda: qml(image, "status === Image.Ready"))
    home.load_service_media(SID, START)
    detail = show(window, detail_page)
    wait_until(chart.exists)  # 下載又可以用了
    del reports, detail, settings_page
