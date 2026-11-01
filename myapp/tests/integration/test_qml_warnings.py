"""打開每個畫面時，QML engine 都不該發出警告：綁定錯誤、找不到圖片、樣式不支援自訂、錨點衝突、binding loop。"""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl

from myapp import main
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


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_admin_page_has_no_warnings(engine, services, qml_warnings, page):
    admin_home_init.launch(engine, ADMIN_ID, services)
    home = engine.rootObjects()[0]
    show_page(home, page)
    settle()
    assert qml_warnings == []
