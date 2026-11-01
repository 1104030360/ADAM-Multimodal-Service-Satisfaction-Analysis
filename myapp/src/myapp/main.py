"""
My first application
"""

import importlib.metadata
import sys

from PySide6 import QtWidgets, QtQml
from pathlib import Path
from PySide6.QtCore import QUrl, QObject, Slot
from myapp.database import DatabaseConnector
from myapp.init import server_home_init, admin_home_init
from myapp.db_lane import DbLane
from myapp.notifications import Notifier
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs


class DatabaseConnectorWrapper(QObject):
    def __init__(self):
        super().__init__()
        print("Initializing DatabaseConnectorWrapper")
        self.db_connector = DatabaseConnector()
        if self.db_connector.connection:
            print("DatabaseConnectorWrapper initialized successfully")
        else:
            print("Failed to initialize DatabaseConnectorWrapper")

    @Slot(str, str, result='QVariantMap')
    def checkLogin(self, username, password):
        result = self.db_connector.check_login(username, password)
        return result


class MainWindow(QObject):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        # 背景工作、通知、動態設定（F0）。登入視窗和兩個外殼用同一個 engine，
        # 所以在載入 login.qml 之前註冊，之後每個畫面都看得到；參照留在這裡，不會被回收
        self.tasks = TaskRunner(self)
        self.notifier = Notifier(self)
        self.ui_prefs = UiPrefs(self)
        # 登入後的畫面共用這一份；db lane 的連線只有一條（ENG-10），程式結束時隨行程關閉
        self.services = AppServices(self.tasks, self.notifier, DbLane(), self.ui_prefs)
        self.tasks.failed.connect(self._on_task_failed)  # 沒給 on_err 的工作失敗時，跳一則通知
        self.db_connector_wrapper = DatabaseConnectorWrapper()
        context = self.engine.rootContext()
        context.setContextProperty("mainWindow", self)
        context.setContextProperty("DatabaseConnector", self.db_connector_wrapper)
        context.setContextProperty("tasks", self.tasks)
        context.setContextProperty("notifier", self.notifier)
        context.setContextProperty("uiPrefs", self.ui_prefs)

    def _on_task_failed(self, key, error):
        self.notifier.fromException(error)

    @Slot(str, str, result='QVariantMap')
    def checkLogin(self, username, password):
        result = self.db_connector_wrapper.checkLogin(username, password)
        return result

    @Slot(str)
    def launchServerHome(self, account_id):
        server_home_init.launch(self.engine, account_id, self.services)

    @Slot(str)
    def launchAdminHome(self, account_id):
        admin_home_init.launch(self.engine, account_id, self.services)


def main():
    try:
        print("Starting application")
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
            main_window.db_connector_wrapper.db_connector.close_connection()

        app.aboutToQuit.connect(on_app_about_to_quit)

        sys.exit(app.exec())
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(-1)


if __name__ == "__main__":
    main()
