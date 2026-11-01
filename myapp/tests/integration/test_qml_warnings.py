"""打開每個畫面時，QML engine 都不該發出警告：綁定錯誤、找不到圖片、樣式不支援自訂、錨點衝突、binding loop。"""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl

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
def test_staff_page_has_no_warnings(engine, db, qml_warnings, page):
    server_home_init.launch(engine, STAFF_ID)
    home = engine.rootObjects()[0]
    qml(home, f'mainContentLoader.source = "../../qml/pages/{page}"')  # 和側邊欄按鈕的寫法一樣
    settle()
    assert qml_warnings == []


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_admin_page_has_no_warnings(engine, db, qml_warnings, page):
    admin_home_init.launch(engine, ADMIN_ID)
    home = engine.rootObjects()[0]
    qml(home, f'mainContentLoader.source = "../../qml/pages/{page}"')
    settle()
    assert qml_warnings == []
