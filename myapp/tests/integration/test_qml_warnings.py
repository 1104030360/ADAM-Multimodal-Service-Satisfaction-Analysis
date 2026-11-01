"""打開每個畫面時，QML engine 都不該發出警告：綁定錯誤、找不到圖片、樣式不支援自訂、錨點衝突、binding loop。"""
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl

from myapp import main, storage
from myapp.init import admin_home_init, server_home_init
from myapp.main import MainWindow
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, wait_until

pytestmark = pytest.mark.integration

LOGIN_QML = Path(main.__file__).parent / "qml" / "pages" / "login.qml"
STAFF_PAGES = ["server_home_button.qml", "record_widget.qml", "analysis.qml", "server_service_page.qml",
               "server_service_detail_page.qml", "server_reports_page.qml", "server_setting_button.qml"]
ADMIN_PAGES = ["admin_home_page.qml", "admin_add_page.qml", "admin_service_page.qml",
               "admin_service_detail_page.qml", "admin_reports_page.qml", "admin_setting_page.qml"]


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的警告，格式和終端機上看到的一樣（file:///….qml:行:欄: 訊息）。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    return warnings


def settle(seconds=0.3):
    """讓已經開始的動畫和轉場跑完。"""
    deadline = time.monotonic() + seconds
    wait_until(lambda: time.monotonic() >= deadline)


def show_page(home, page):
    """和側邊欄按鈕一樣換頁，等 Loader 載入完成（非同步載入晚出現的警告才不會漏掉，ENG-21）。"""
    qml(home, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(home, "mainContentLoader.status === Loader.Ready"))


def wait_for_staff_data(services):
    """launch() 在背景載入的資料都回來了：綁定已經用真的資料重新計算過。"""
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS))


def test_login_window_has_no_warnings(engine, db, qml_warnings):
    controller = MainWindow(engine)  # QML 裡的 mainWindow；留著它，QML 才叫得到
    engine.load(QUrl.fromLocalFile(str(LOGIN_QML)))
    window = engine.rootObjects()[0]
    qml(window, "stackView.replace(loginComponent)")  # 開場動畫結束後做的事
    wait_until(lambda: not qml(window, "stackView.busy"))  # 轉場跑完，開場畫面才會被拿掉
    settle()
    assert qml_warnings == []
    del controller


@pytest.mark.parametrize("page", STAFF_PAGES)
def test_staff_page_has_no_warnings(engine, services, qml_warnings, page):
    server_home_init.launch(engine, STAFF_ID, services)
    home = engine.rootObjects()[0]
    show_page(home, page)
    wait_for_staff_data(services)
    settle()
    assert qml_warnings == []


def test_staff_shell_shows_every_severity_as_a_toast_without_warnings(engine, services, qml_warnings):
    server_home_init.launch(engine, STAFF_ID, services)
    home = engine.rootObjects()[0]
    show_page(home, "analysis.qml")  # 清單用 toastHost.occupiedHeight 當 bottomMargin
    wait_for_staff_data(services)
    for severity in ("success", "info", "warning", "error"):
        services.notifier.notify(severity, f"A {severity} notification", "Some detail for this notification.")
    services.notifier.progress("upload:S20261031093000", 58.6e6, 124.0e6, "Uploading S20261031093000", "File 2 of 3")
    host = home.findChild(QObject, "toastHost")
    # 最多 3 則：成功、資訊被擠掉，留下警告、錯誤、進度（DS-21）
    wait_until(lambda: qml(host, "count") == 3 and qml(host, "height") > 0)
    assert qml(host, "occupiedHeight") == qml(host, "height") + 24
    page = qml(home, "mainContentLoader.item")
    assert qml(page, "listBottomMargin") == qml(host, "occupiedHeight")
    settle()
    assert qml_warnings == []


def test_staff_quit_dialog_has_no_warnings(engine, services, qml_warnings, monkeypatch):
    """錄影中按 Quit：停止並存檔，上傳還沒完成就跳出確認（F3，UX-29）。對話框放在側邊欄的 Quit 按鈕裡。"""
    uploading, release = threading.Event(), threading.Event()

    def stuck_upload(local_path, key, token=None, report=None):  # 測試放行之前，上傳一直沒有完成
        uploading.set()
        while not release.wait(0.01):
            token.raise_if_cancelled()
        raise ConnectionError("upload stopped by the test")

    monkeypatch.setattr(storage, "upload_with_progress", stuck_upload)
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = engine.rootObjects()[0]
    show_page(window, "record_widget.qml")
    wait_until(lambda: qml(window, "deviceFinder.status") == "ready")
    finder = home.deviceFinder
    finder.startRecording(0, 1, 0)
    recorder = finder.recorder
    try:
        recorder.start_recording()
        qml(window, "exitButton.click()")
        wait_until(lambda: uploading.is_set() and qml(window.findChild(QObject, "quitDialog"), "opened"))
        wait_for_staff_data(services)
        settle()
        assert qml_warnings == []
    finally:
        release.set()  # 上傳失敗收尾：不留下還在存的錄影（QuitGuard 才會拿掉）
        wait_until(lambda: finder.uploadCount == 0)
        recorder.close()


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_admin_page_has_no_warnings(engine, services, qml_warnings, page):
    admin_home_init.launch(engine, ADMIN_ID, services)
    home = engine.rootObjects()[0]
    show_page(home, page)
    settle()
    assert qml_warnings == []


def test_admin_home_tabs_and_search_states_have_no_warnings(engine, services, db, qml_warnings):
    """F5（D2）：管理員首頁的四個分頁（共用的 SegmentTab）、有資料的排名和趨勢圖，
    以及查詢頁（共用的 DateField）的日期錯誤和結果，都畫出來，零警告。"""
    from datetime import date

    today = date.today()
    db.add_service(f"S{today:%Y%m%d}090000", f"{today} 09:00:00")
    db.add_scores(f"S{today:%Y%m%d}090000", customer=92, server=64)
    home = admin_home_init.launch(engine, ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    sections = (home.serviceScoreManager, home.rankingModel, home.trendDataManager)
    wait_until(lambda: all(section.state not in ("loading", "idle") for section in sections))
    page = qml(window, "mainContentLoader.item")  # 留著參照，子物件的 wrapper 才不會失效（T17）
    for name in ("weeklyTab", "rankingTab", "trendsTab", "dailyTab"):
        tab = page.findChild(QObject, name)
        qml(tab, "clicked()")
        wait_until(lambda: qml(tab, "selected"))
        settle()
    show_page(window, "admin_service_page.qml")
    search = qml(window, "mainContentLoader.item")
    wait_until(lambda: home.serviceManager.namesState == "ready")
    qml(search, 'startDateField.text = "2026/10/01"; search()')  # 日期格式不對：欄位下方的錯誤、紅框
    wait_until(lambda: home.serviceManager.startDateError != "")
    qml(search, f'startDateField.text = "{today}"; search()')
    wait_until(lambda: home.serviceManager.state == "ready")
    settle()
    assert qml_warnings == []


@pytest.mark.parametrize("side", ["staff", "admin"])
def test_service_detail_and_reports_with_data_have_no_warnings(engine, services, db, s3, qml_warnings, side, tmp_path):
    """明細頁的每一種狀態（好了、失敗、沒有這個檔案、圖表警告列）和打開 PDF 的報表頁都畫出來，零警告（C2）。"""
    import fitz
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    sid = "S20261030120000"
    db.add_service(sid, "2026-10-30 12:00:00")
    for key in (f"video/{sid}_cam0.avi", f"audio/{sid}_audio.wav", f"charts/{sid}_a.png", f"charts/{sid}_b.png"):
        db.add_media(sid, key.split("/")[0], f"s3://adam-test/{key}")
    image, png = QImage(16, 12, QImage.Format.Format_RGB32), QByteArray()
    image.fill(QColor("#2E7D32"))
    buffer = QBuffer(png)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    s3.put_object(Bucket="adam-test", Key=f"charts/{sid}_a.png", Body=bytes(png))  # 其他的不在 S3：失敗的狀態
    s3.put_object(Bucket="adam-test", Key=f"audio/{sid}_audio.wav", Body=b"not really audio")
    reports = tmp_path / "report-source"
    reports.mkdir()
    document = fitz.Document()
    document.new_page()
    document.save(reports / f"{sid}.pdf")

    module = server_home_init if side == "staff" else admin_home_init
    home = module.launch(engine, STAFF_ID if side == "staff" else ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    home.load_service_media(sid, "2026-10-30 12:00:00")
    show_page(window, f"{'server' if side == 'staff' else 'admin'}_service_detail_page.qml")
    wait_until(lambda: home.mediaModel.state == "ready" and not home.mediaModel.pending
               and home.chartsModel.state == "ready")
    settle()
    page = qml(window, "mainContentLoader.item")  # 留著參照，子物件的 wrapper 才不會失效（T17）
    page.findChild(QObject, "tabBar").setProperty("currentIndex", 1)  # 圖表分頁：警告列、一張好的圖表
    settle()
    home.pdfmodel.reports_dir = str(reports)
    show_page(window, f"{'server' if side == 'staff' else 'admin'}_reports_page.qml")
    assert home.pdfmodel.open(f"{sid}.pdf", 600)
    wait_until(lambda: home.pdfmodel.viewState == "ready")
    settle()
    assert qml_warnings == []
    home.pdfmodel._tasks().shutdown()  # 沒有 services 的 PDFHandler 自己的 pdf lane
