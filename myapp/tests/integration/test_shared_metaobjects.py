"""QML 讀得到的物件都用類別共用的 metaobject（T23 第 9 節）。

PySide 把 signal 接到沒有 @Slot 的方法時，會替那個實例動態加一個 slot，那個實例就有自己的 metaobject。
QML 依 metaobject 的位址快取 property cache：舊的實例被 gc 收掉、新的 metaobject 剛好用到同一個位址時，
QML 會用錯 property cache，偶爾讀到 adminHome.profile 是 undefined（測試會一直建新的主畫面才遇得到）。
所以接 signal 的方法一律加 @Slot(...)，這裡檢查登入後的物件都沒有動態加上的 slot。
"""
import pytest
from PySide6.QtCore import QObject

from myapp.init import admin_home_init, server_home_init
from myapp.main import MainWindow
from tests.integration.helpers import ADMIN_ID, STAFF_ID, wait_until

pytestmark = pytest.mark.integration


def dynamic_slots(obj):
    """PySide 替這個實例動態加的 slot。

    有動態 slot 的實例，metaobject 是 PySide 另外建的一層：superClass() 是類別本來的 metaobject（className 一樣），
    這一層只有動態加的 slot。不拿 staticMetaObject 來比：PySide 6.12 的 metaObject() 回傳的就是類別的
    staticMetaObject 那個 wrapper，還把它掛在這個物件底下，物件被刪掉時它跟著失效（之後在 Python 裡讀
    X.staticMetaObject 會 RuntimeError；目前沒有程式用到）。
    """
    meta = obj.metaObject()
    if meta.superClass().className() != meta.className():
        return []
    return [bytes(meta.method(index).name()).decode() for index in range(meta.methodOffset(), meta.methodCount())]


def app_objects(*roots):
    """roots 和它們的屬性、子物件裡，類別寫在 myapp 的 QObject（QML 的視窗、Qt 自己的物件不往下找）。"""
    found, pending = {}, list(roots)
    while pending:
        obj = pending.pop()
        if id(obj) in found or not type(obj).__module__.startswith("myapp"):
            continue
        found[id(obj)] = obj
        pending.extend(value for value in vars(obj).values() if isinstance(value, QObject))
        pending.extend(obj.findChildren(QObject))
    return list(found.values())


def with_dynamic_slots(objects):
    return {type(obj).__name__: dynamic_slots(obj) for obj in objects if dynamic_slots(obj)}


def test_login_controller_and_shared_services_keep_the_class_metaobject(engine):
    controller = MainWindow(engine)
    objects = app_objects(controller)
    assert {"MainWindow", "TaskRunner", "Notifier", "UiPrefs"} <= {type(obj).__name__ for obj in objects}
    assert with_dynamic_slots(objects) == {}
    controller.tasks.shutdown()


def test_staff_home_objects_keep_the_class_metaobject(engine, services):
    home = server_home_init.launch(engine, STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS))
    assert home.deviceFinder.saver is not None  # 第一次錄影才建立；先建好，連它接的 signal 一起檢查
    objects = app_objects(home, services.tasks, services.notifier, services.ui_prefs)
    assert {"ServerHome", "DeviceFinder", "RecordingSaver", "SavesModel", "ServiceModel", "AnalysisWatcher",
            "DockAttention", "MediaModel", "ChartsModel", "AccountProfile",
            "AccountSelfService", "ShellSession"} <= {type(obj).__name__ for obj in objects}
    assert with_dynamic_slots(objects) == {}


def test_admin_home_objects_keep_the_class_metaobject(engine, services):
    home = admin_home_init.launch(engine, ADMIN_ID, services)
    sections = (home.profile, *home.homeDashboard.sections)
    wait_until(lambda: all(section.state != "loading" for section in sections))
    objects = app_objects(home, services.tasks, services.notifier, services.ui_prefs)
    assert {"AdminHome", "AccountProfile", "AccountManager", "AccountSelfService", "ShellSession"} <= {
        type(obj).__name__ for obj in objects}
    assert with_dynamic_slots(objects) == {}
