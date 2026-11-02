"""啟動與登入（main.py、login.qml）：在背景比對雜湊，依帳號等級開服務人員端或管理員端（F2）。

忙碌、焦點、連不上資料庫這些回饋的細節在 test_staff_feedback.py；開場圖（D3）在 test_navigation_a11y.py。
"""
import errno
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication

from myapp import main
from myapp.database import DatabaseConnector
from myapp.main import MainWindow
from myapp.services import CLOSE_DB_KEY
from tests.integration.helpers import STAFF_NAME, qml, toasts, wait_until

pytestmark = pytest.mark.integration

LOGIN_QML = Path(main.__file__).parent / "qml" / "pages" / "login.qml"


@pytest.fixture
def login_form(engine, db):
    """打開登入視窗，回傳登入表單。

    測試的動畫時間是 0（tests/conftest.py 的 ADAM_ANIMATION_SCALE），和減少動態一樣：開場圖整段不出現，
    第一個畫面就是表單，不必再等或手動換頁（D3、DS-43）。
    """
    controller = MainWindow(engine)  # QML 裡的 mainWindow；yield 讓它活到測試結束
    engine.load(QUrl.fromLocalFile(str(LOGIN_QML)))
    window = engine.rootObjects()[0]
    assert not qml(window, "splashShown")
    assert qml(window, "stackView.currentItem.objectName") == "loginForm" and qml(window, "stackView.depth") == 1
    yield qml(window, "stackView.currentItem")
    if controller.services.close_db():  # db lane 的連線只能在 db lane 上關
        wait_until(lambda: not controller.tasks.isBusy(CLOSE_DB_KEY))
    controller.tasks.shutdown()


def submit(form, username, password):
    qml(form, "username_textfield").setProperty("text", username)
    qml(form, "password_textfield").setProperty("text", password)
    qml(form, "loginButton.clicked()")  # 按 Log In


@pytest.mark.parametrize("username, password, title, name", [
    ("placeholder_staff", "CHANGE-ME-staff-0000", "Staff Home", STAFF_NAME),
    ("placeholder_admin", "CHANGE-ME-admin-0000", "Admin Home", "系統管理員（佔位）"),
], ids=["staff", "admin"])
def test_login_opens_home_for_account_level(engine, login_form, username, password, title, name):
    submit(login_form, username, password)
    wait_until(lambda: len(engine.rootObjects()) == 2)  # 驗證在背景，結果回來才開主畫面
    login_window, home = engine.rootObjects()
    assert not login_window.isVisible()
    assert home.title() == title
    wait_until(lambda: qml(home, "header.userName") == name)  # 頁首顯示登入者的名字（new-UIUX PLAN 第 4 節）


def test_a_replaced_login_form_ignores_login_results(engine, login_form):
    """被換掉、還沒真的刪掉的表單不處理登入結果：只開一個主畫面（其他測試會把一開始的表單再換成新的一份）。"""
    window = engine.rootObjects()[0]
    qml(window, "stackView.replace(loginComponent)")
    form = qml(window, "stackView.currentItem")
    assert qml(login_form, "StackView.status === StackView.Inactive")  # 舊的還活著，只是不在 StackView 裡
    wait_until(lambda: qml(form, "username_textfield.activeFocus"))  # 新的表單一樣一進來就能打帳號
    submit(form, "placeholder_staff", "CHANGE-ME-staff-0000")
    # 兩份表單都接的話，同一個 signal 會一次開兩個主畫面（1 → 3），不會停在 2
    wait_until(lambda: len(engine.rootObjects()) == 2)
    assert engine.rootObjects()[1].title() == "Staff Home"


@pytest.mark.parametrize("username, password", [
    ("placeholder_staff", "CHANGE-ME-staff-000X"),
    ("nobody", "CHANGE-ME-staff-0000"),
], ids=["wrong-password", "unknown-user"])
def test_failed_login_stays_on_login_window(engine, login_form, username, password):
    submit(login_form, username, password)
    wait_until(lambda: qml(login_form, "error_message.text") != "")
    assert qml(login_form, "error_message.text") == "Incorrect username or password. Try again."
    assert qml(login_form, "error_row.visible")
    assert len(engine.rootObjects()) == 1


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


def test_main_opens_login_window_without_touching_the_database(engine, db, monkeypatch, capsys,
                                                               no_network_on_gui_thread):
    # main() 讀套件資訊；用 briefcase dev 執行時才會有，這裡直接給
    monkeypatch.setattr(importlib.metadata, "metadata", lambda name: {"Formal-Name": "myapp"})
    monkeypatch.setattr(main, "QtWidgets", SimpleNamespace(QApplication=FakeApplication))
    monkeypatch.setattr(main, "QtQml", SimpleNamespace(QQmlApplicationEngine=lambda: engine))  # 測試結束時一起刪掉
    dotenv_calls = []
    monkeypatch.setattr(main, "load_dotenv", lambda: dotenv_calls.append(True))  # 測試不讀 .env
    windows, at_load = [], {}

    class RecordingMainWindow(MainWindow):  # 留住 main() 建立的物件，測試才看得到
        def __init__(self, engine):
            super().__init__(engine)
            windows.append(self)

    load = engine.load

    def load_and_record(url):  # 載入 login.qml 的那一刻，背景工作、通知、動態設定已經註冊好了
        at_load.update({name: engine.rootContext().contextProperty(name) for name in ("tasks", "notifier", "uiPrefs")})
        load(url)

    monkeypatch.setattr(main, "MainWindow", RecordingMainWindow)
    monkeypatch.setattr(engine, "load", load_and_record)
    with pytest.raises(SystemExit) as exited:
        main.main()
    assert exited.value.code == 0, capsys.readouterr().out
    login_window = engine.rootObjects()[0]
    assert login_window.title() == "Welcome to ADAM" and login_window.isVisible()
    # 啟動時不連資料庫（UX-01、ENG-02）：舊的共用連線沒有建立，DbLane 也還沒連；.env 由 main() 自己讀
    assert DatabaseConnector._instance is None
    assert dotenv_calls == [True]

    [window] = windows
    assert at_load["tasks"] is window.tasks and at_load["notifier"] is window.notifier
    assert at_load["uiPrefs"] is window.ui_prefs
    assert qml(login_window, "uiPrefs.animationScale") == 0  # QML 讀得到；測試時動畫關掉
    assert window.tasks.submit("io", "after-quit", lambda token, report: 1) is False  # 結束時 shutdown() 了
    # 沒給 on_err 的背景工作失敗時，跳出一則通知
    window.tasks.failed.emit("export", OSError(errno.ENOSPC, "No space left on device"))
    assert [toast["title"] for toast in toasts(window.notifier)] == ["Not enough disk space"]


def test_quitting_closes_the_shared_connection_only_if_it_was_opened(db):
    DatabaseConnector.close_shared()  # 沒建立過：不會為了關而連線
    assert DatabaseConnector._instance is None
    connector = DatabaseConnector()  # 例如明細頁下載時建立的
    assert connector.connection.is_connected()
    DatabaseConnector.close_shared()
    assert not connector._connection.is_connected()  # 用 _connection：.connection 會重新連線
