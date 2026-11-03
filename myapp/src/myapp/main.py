"""
My first application
"""

import importlib.metadata
import logging
import sys
from pathlib import Path

from PySide6 import QtWidgets, QtQml
from PySide6.QtCore import QUrl, QObject, Property, Signal, Slot
from myapp import config
from myapp.database import DatabaseConnector, verify_login
from myapp.init import server_home_init, admin_home_init
from myapp.db_lane import DbLane
from myapp.notifications import Notifier, user_message
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs

logger = logging.getLogger(__name__)

LOGIN_KEY = "login"  # TaskRunner 的 key：同時只會有一個登入（ENG-14）


class MainWindow(QObject):
    """登入視窗的 Python 端（QML 的 mainWindow）。

    F2：啟動時不連資料庫，登入在 db lane 背景驗證（UX-01、ENG-02、ENG-06）。QML 呼叫 logIn()，
    結果從三個 signal 回來：登入成功、帳號或密碼不正確、連不上資料庫（或其他錯誤）。
    找不到設定檔、或少了登入要用的變數時（T51），settingsProblem 是登入畫面上的說明，logIn() 不連資料庫。
    """

    loggingInChanged = Signal()
    loginSucceeded = Signal(str, str)  # accountId, accountLevel（字串，"1" 是服務人員）
    loginRejected = Signal()           # 帳號或密碼不正確
    # "offline"：連不上資料庫；"error"：其他錯誤（例如資料庫設定錯了）；"settings"：設定不完整（settingsProblem）
    loginFailed = Signal(str)
    loginRequested = Signal()          # 登出了（N1）：login.qml 換一份新的表單，登入視窗再出現
    settingsProblemChanged = Signal()

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        # 背景工作、通知、動態設定（F0）。登入視窗和兩個外殼用同一個 engine，
        # 所以在載入 login.qml 之前註冊，之後每個畫面都看得到；參照留在這裡，不會被回收
        self.tasks = TaskRunner(self)
        self.notifier = Notifier(self)
        self.ui_prefs = UiPrefs(self)
        # 登入和登入後的畫面共用這一份；db lane 的連線只有一條（ENG-10），第一次用到時才連
        self.services = AppServices(self.tasks, self.notifier, DbLane(), self.ui_prefs)
        self.tasks.failed.connect(self._on_task_failed)  # 沒給 on_err 的工作失敗時，跳一則通知
        self.tasks.busyChanged.connect(self._on_busy_changed)
        self._settings_problem = config.problem()
        context = self.engine.rootContext()
        context.setContextProperty("mainWindow", self)
        context.setContextProperty("tasks", self.tasks)
        context.setContextProperty("notifier", self.notifier)
        context.setContextProperty("uiPrefs", self.ui_prefs)

    @Slot(str, object)
    def _on_task_failed(self, key, error):
        self.notifier.fromException(error)

    @Slot(str, bool)
    def _on_busy_changed(self, key, busy):
        if key == LOGIN_KEY:
            self.loggingInChanged.emit()

    @Property(bool, notify=loggingInChanged)
    def loggingIn(self):
        return self.tasks.isBusy(LOGIN_KEY)

    @Property(str, notify=settingsProblemChanged)
    def settingsProblem(self):
        """設定檔要放在哪裡、少了哪些變數（英文，config.problem()）；設定齊全是空字串。"""
        return self._settings_problem

    @Slot(str, str, result=bool)
    def logIn(self, username, password):
        """在 db lane 驗證帳號密碼。回傳 False 表示上一次登入還沒結束（防重複送出）。

        設定不完整時先重新找一次設定檔（使用者可能剛放好），還是不完整就發 loginFailed("settings")，不連資料庫。
        """
        if self._settings_problem and not self._reload_settings():
            self.loginFailed.emit("settings")
            return True
        return self.tasks.submit("db", LOGIN_KEY,
                                 lambda token, report: verify_login(self.services.db, username, password),
                                 on_ok=self._login_done, on_err=self._login_error)

    def _reload_settings(self):
        """讀一個本機的小檔案（不是網路），在 GUI 執行緒做。回傳設定是不是齊全了。"""
        config.load()
        problem = config.problem()
        if problem != self._settings_problem:
            self._settings_problem = problem
            self.settingsProblemChanged.emit()
        return not problem

    def _login_done(self, account):
        if account is None:
            self.loginRejected.emit()
        else:
            self.loginSucceeded.emit(account["accountId"], account["accountLevel"])

    def _login_error(self, error):
        # 登入視窗沒有外殼，錯誤留在表單裡，不跳 toast（ENG-15、DS-20）。能重試的就是連不上（ENG-12）
        retryable = user_message(error)[3]
        logger.warning("login failed: %r", error)
        self.loginFailed.emit("offline" if retryable else "error")

    @Slot(str)
    def launchServerHome(self, account_id):
        home = server_home_init.launch(self.engine, account_id, self.services)
        home.session.loggedOut.connect(self.showLogin)  # Log Out（N1）：回到登入視窗

    @Slot(str)
    def launchAdminHome(self, account_id):
        home = admin_home_init.launch(self.engine, account_id, self.services)
        home.session.loggedOut.connect(self.showLogin)

    @Slot()
    def showLogin(self):
        """登出之後（N1）：登入視窗再出現，不重播開場圖；表單是新的（帳號、密碼都空白，焦點在帳號欄）。
        登入成功時 login.qml 只把視窗關掉（藏起來），所以是同一個視窗。"""
        self.loginRequested.emit()


def main():
    try:
        print("Starting application")
        # 設定檔只在這裡讀一次（T51）：環境變數 → ~/Library/Application Support/ADAM/.env → repo 裡的 .env。
        # DbLane 第一次連線時才讀 DB_*，錄影、快取的位置也是用到時才讀
        config.load()
        app_module = sys.modules["__main__"].__package__
        metadata = importlib.metadata.metadata(app_module)
        QtWidgets.QApplication.setApplicationName(metadata["Formal-Name"])

        app = QtWidgets.QApplication(sys.argv)
        print("QGuiApplication created")

        engine = QtQml.QQmlApplicationEngine()
        print("QQmlApplicationEngine created")

        main_window = MainWindow(engine)
        print("MainWindow created")

        qml_file = Path(__file__).resolve().parent / "qml/pages/login.qml"
        print(f"Loading QML file from {qml_file}")

        engine.load(QUrl.fromLocalFile(qml_file))
        print("QML file loaded")

        if not engine.rootObjects():
            print("No root objects found")
            sys.exit(-1)

        mainWindow = engine.rootObjects()[0]
        print("Main window object retrieved")

        screen = app.primaryScreen().availableGeometry()
        window = mainWindow.geometry()
        x = (screen.width() - window.width()) / 2
        y = (screen.height() - window.height()) / 2
        mainWindow.setPosition(x, y)
        print("Main window positioned")

        def on_app_about_to_quit():
            main_window.tasks.shutdown()  # 先取消背景工作，不等它們結束（ENG-14）
            DatabaseConnector.close_shared()  # 沒拿到 services 的舊呼叫方式（只剩測試）建過的舊連線；app 本身不會建立

        app.aboutToQuit.connect(on_app_about_to_quit)

        sys.exit(app.exec())
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(-1)


if __name__ == "__main__":
    main()
