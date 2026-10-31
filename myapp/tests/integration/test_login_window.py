"""啟動與登入（main.py、login.qml）：密碼比對雜湊，依帳號等級開服務人員端或管理員端。"""
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication

from myapp import main
from myapp.database import DatabaseConnector
from myapp.main import MainWindow
from tests.integration.helpers import STAFF_NAME, qml

pytestmark = pytest.mark.integration

LOGIN_QML = Path(main.__file__).parent / "qml" / "pages" / "login.qml"


@pytest.fixture
def login_form(engine, db):
    """打開登入視窗、跳過 2.5 秒的開場動畫，回傳登入表單。"""
    controller = MainWindow(engine)  # QML 裡的 mainWindow；yield 讓它活到測試結束
    engine.load(QUrl.fromLocalFile(str(LOGIN_QML)))
    window = engine.rootObjects()[0]
    qml(window, "stackView.replace(loginComponent)")
    yield qml(window, "stackView.currentItem")
    del controller


def submit(form, username, password):
    qml(form, "username_textfield").setProperty("text", username)
    qml(form, "password_textfield").setProperty("text", password)
    qml(form, "mouseArea.clicked(null)")  # 按 Login；MouseArea 的 clicked 要一個滑鼠事件參數，給 null


@pytest.mark.parametrize("username, password, title, name", [
    ("placeholder_staff", "CHANGE-ME-staff-0000", "Server Home", STAFF_NAME),
    ("placeholder_admin", "CHANGE-ME-admin-0000", "Admin Home", "系統管理員（佔位）"),
], ids=["staff", "admin"])
def test_login_opens_home_for_account_level(engine, login_form, username, password, title, name):
    submit(login_form, username, password)
    login_window, home = engine.rootObjects()
    assert not login_window.isVisible()
    assert home.title() == title
    assert qml(home, "username.text") == name  # 側邊欄顯示登入者的名字


@pytest.mark.parametrize("username, password", [
    ("placeholder_staff", "CHANGE-ME-staff-000X"),
    ("nobody", "CHANGE-ME-staff-0000"),
], ids=["wrong-password", "unknown-user"])
def test_failed_login_stays_on_login_window(engine, login_form, username, password):
    submit(login_form, username, password)
    assert len(engine.rootObjects()) == 1
    assert qml(login_form, "error_message.visible")


class FakeApplication:
    """main() 會自己建立 QApplication，但一個程式只能有一個（測試已經建好了）。

    這個替身用現有的螢幕；exec() 不進入事件迴圈，直接執行「即將結束」的處理，回傳 0。
    """

    def __init__(self, argv):
        self.quit_handlers = []
        self.aboutToQuit = SimpleNamespace(connect=self.quit_handlers.append)

    @staticmethod
    def setApplicationName(name):
        pass

    def primaryScreen(self):
        return QGuiApplication.primaryScreen()

    def exec(self):
        for handler in self.quit_handlers:
            handler()
        return 0


def test_main_opens_login_window_and_closes_database_on_quit(engine, db, monkeypatch, capsys):
    # main() 讀套件資訊；用 briefcase dev 執行時才會有，這裡直接給
    monkeypatch.setattr(importlib.metadata, "metadata", lambda name: {"Formal-Name": "myapp"})
    monkeypatch.setattr(main, "QtWidgets", SimpleNamespace(QApplication=FakeApplication))
    monkeypatch.setattr(main, "QtQml", SimpleNamespace(QQmlApplicationEngine=lambda: engine))  # 測試結束時一起刪掉
    with pytest.raises(SystemExit) as exited:
        main.main()
    assert exited.value.code == 0, capsys.readouterr().out
    login_window = engine.rootObjects()[0]
    assert login_window.title() == "Welcome to ADAM" and login_window.isVisible()
    assert not DatabaseConnector()._connection.is_connected()  # 用 _connection：.connection 會重新連線
