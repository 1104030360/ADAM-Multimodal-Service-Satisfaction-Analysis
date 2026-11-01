"""Log Out 和 Quit（new-feature N1）：兩個都先問；登出回到登入視窗，換一個帳號登入看到的是新帳號的名字和資料。

- 從登入視窗開始，和 main.py 一樣：MainWindow＋login.qml，登入後的外殼載入同一個 engine。
- Quit、關掉主視窗、⌘W、⌘Q 都問「Quit ADAM?」；Log Out 問「Log out?」。取消之後畫面不變。
- 登出：外殼刪掉、home 被放開（weakref＋gc.collect()，在主執行緒收，T34）、它註冊的 context property 變成 null，
  背景工作取消、通知清掉；登入視窗再出現（不重播開場圖），表單是新的。
- 上傳中按 Log Out 是上傳的警告（test_upload_flow.py）。
- 每個測試都要零 QML 警告。
"""
import gc
import inspect
import re
import time
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QUrl
from PySide6.QtTest import QTest

from myapp import main
from myapp.init import admin_home_init, server_home_init
from myapp.init.record_widget import QuitGuard
from myapp.main import MainWindow
from myapp.passwords import hash_password
from myapp.services import CLOSE_DB_KEY
from tests.integration.helpers import STAFF_ID, STAFF_NAME, qml, toasts, wait_until

pytestmark = pytest.mark.integration

LOGIN_QML = Path(main.__file__).parent / "qml" / "pages" / "login.qml"
CTRL = Qt.KeyboardModifier.ControlModifier  # Qt 在 macOS 上的 Ctrl 就是 ⌘
ADMIN_NAME = "系統管理員（佔位）"
STAFF = SimpleNamespace(username="placeholder_staff", password="CHANGE-ME-staff-0000", title="Staff Home",
                        home="serverHome", names=server_home_init.CONTEXT_NAMES)
ADMIN = SimpleNamespace(username="placeholder_admin", password="CHANGE-ME-admin-0000", title="Admin Home",
                        home="adminHome", names=admin_home_init.CONTEXT_NAMES)
BOTH_SIDES = pytest.mark.parametrize("side", [STAFF, ADMIN], ids=["staff", "admin"])
OTHER_STAFF_ID = "00000000-0000-0000-0000-000000000005"


@pytest.fixture
def app(engine, db):
    """登入視窗（MainWindow＋login.qml，和 main.py 一樣）。log_in() 回傳登入後的外殼視窗。"""
    controller = MainWindow(engine)
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.load(QUrl.fromLocalFile(str(LOGIN_QML)))
    login = engine.rootObjects()[0]
    quits = []
    engine.quit.connect(lambda: quits.append(True))  # Qt.quit()（測試裡 app 沒有在 exec，不會真的結束）

    def log_in(username, password):
        form = qml(login, "stackView.currentItem")
        qml(form, "username_textfield").setProperty("text", username)
        qml(form, "password_textfield").setProperty("text", password)
        qml(form, "loginButton.clicked()")
        wait_until(lambda: not login.isVisible() and len(engine.rootObjects()) > 1)  # 驗證在 db lane
        shell = engine.rootObjects()[-1]
        wait_until(lambda: idle(controller.tasks))  # 名字、首頁的卡片都載入了
        return shell

    yield SimpleNamespace(controller=controller, services=controller.services, engine=engine, login=login,
                          log_in=log_in, warnings=warnings, quits=quits)
    if controller.services.close_db():
        wait_until(lambda: not controller.tasks.isBusy(CLOSE_DB_KEY))
    controller.tasks.shutdown()
    assert warnings == []


def idle(tasks):
    """背景工作都做完了（包括被取消、還在收尾的）。"""
    return not tasks._running


def child(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def press(dialog, button):
    """按對話框的按鈕：confirmButton（Quit、Log Out）或 cancelButton（Don't Quit、Stay Logged In）。"""
    qml(child(dialog, button), "clicked()")
    wait_until(lambda: not qml(dialog, "visible"))


def deleted_later():
    """外殼是 deleteLater() 刪的；測試沒有在跑事件迴圈，要自己送 DeferredDelete。"""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def log_out(app, shell):
    """按 Log Out、在「Log out?」按 Log Out，等登入視窗出現、外殼刪掉。"""
    qml(shell, "logoutButton.click()")
    dialog = child(shell, "logoutDialog")
    wait_until(lambda: qml(dialog, "opened"))
    qml(child(dialog, "confirmButton"), "clicked()")
    wait_until(app.login.isVisible)
    deleted_later()
    wait_until(lambda: len(app.engine.rootObjects()) == 1)  # 刪掉的外殼不在 rootObjects() 裡


def context(app, name):
    return app.engine.rootContext().contextProperty(name)


# ── Quit ─────────────────────────────────────────────────────────────────────

@BOTH_SIDES
def test_quit_asks_first_and_dont_quit_leaves_everything_as_it_was(app, side):
    shell = app.log_in(side.username, side.password)
    qml(shell, "servicesButton.click()")
    wait_until(lambda: qml(shell, "mainContentLoader.status === Loader.Ready"))
    page = qml(shell, "mainContentLoader.item")
    qml(shell, "exitButton.click()")
    dialog = child(shell, "quitConfirmDialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert dialog.property("title") == "Quit ADAM?" and dialog.property("body") == ""
    assert child(dialog, "confirmButton").property("text") == "Quit"
    assert qml(child(dialog, "confirmButton"), "variant") == "secondary"  # 沒有東西會丟：不用紅色
    cancel = child(dialog, "cancelButton")
    assert cancel.property("text") == "Don't Quit"
    wait_until(cancel.hasActiveFocus)  # 預設是安全的選項（DS-28）

    press(dialog, "cancelButton")
    assert app.quits == [] and shell.isVisible()
    assert qml(shell, "currentPage") == ("admin_service_page.qml" if side is ADMIN else "server_service_page.qml")
    assert qml(shell, "mainContentLoader.item") is page  # 頁面沒有重建
    assert QuitGuard.instance().blocking(context(app, side.home).session)  # ⌘Q 照樣先問


@BOTH_SIDES
def test_confirming_quit_clears_the_cache_and_quits(app, side, tmp_path):
    shell = app.log_in(side.username, side.password)
    cached = tmp_path / "media" / "S20261030120000_cam0.avi"  # LOCAL_MEDIA_DIR（tests/conftest.py）
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"cached video")
    session = context(app, side.home).session
    qml(shell, "exitButton.click()")
    dialog = child(shell, "quitConfirmDialog")
    wait_until(lambda: qml(dialog, "opened"))
    qml(child(dialog, "confirmButton"), "clicked()")
    assert app.quits == [True]
    assert not cached.exists()  # clearDir()：和以前的 Quit 一樣
    assert not QuitGuard.instance().blocking(session)  # Qt.quit() 送出的 QEvent.Quit 不會又被擋下來


@BOTH_SIDES
def test_closing_the_window_cmd_w_and_cmd_q_ask_the_same_question(app, side):
    shell = app.log_in(side.username, side.password)
    shell.requestActivate()
    wait_until(shell.isActive)  # 快捷鍵只送到作用中的視窗
    dialog = child(shell, "quitConfirmDialog")
    for leave in ("close", "cmd-w", "cmd-q"):
        if leave == "close":
            shell.close()  # 紅色按鈕：onClosing 擋下來，先問
        elif leave == "cmd-w":
            QTest.keyClick(shell, Qt.Key.Key_W, CTRL)
        else:  # ⌘Q：送給 app 的 QEvent.Quit，QML 的 onClosing 收不到（T20），由 QuitGuard 擋下來
            quit_event = QEvent(QEvent.Type.Quit)
            QCoreApplication.sendEvent(QCoreApplication.instance(), quit_event)
            assert not quit_event.isAccepted()  # macOS 因此取消結束
        wait_until(lambda: qml(dialog, "opened"), describe=lambda: leave)
        assert shell.isVisible() and app.quits == []
        assert not qml(child(shell, "logoutDialog"), "visible")
        press(dialog, "cancelButton")
    assert app.quits == []


def test_quit_and_log_out_never_stack_two_dialogs(app):
    shell = app.log_in(STAFF.username, STAFF.password)
    qml(shell, "exitButton.click()")
    quit_dialog = child(shell, "quitConfirmDialog")
    wait_until(lambda: qml(quit_dialog, "opened"))
    qml(shell, "logoutButton.requestLogOut()")  # 例如 ⌘Q 擋下來的同時：不再開第二個
    qml(shell, "exitButton.requestQuit()")
    assert not qml(child(shell, "logoutDialog"), "visible") and not qml(child(shell, "quitDialog"), "visible")
    press(quit_dialog, "cancelButton")


# ── Log Out ──────────────────────────────────────────────────────────────────

@BOTH_SIDES
def test_log_out_asks_first_and_stay_logged_in_leaves_everything_as_it_was(app, side):
    shell = app.log_in(side.username, side.password)
    home = context(app, side.home)
    qml(shell, "logoutButton.click()")
    dialog = child(shell, "logoutDialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert dialog.property("title") == "Log out?"
    assert dialog.property("body") == "You'll return to the sign-in screen."
    assert child(dialog, "confirmButton").property("text") == "Log Out"
    assert child(dialog, "cancelButton").property("text") == "Stay Logged In"
    press(dialog, "cancelButton")
    assert shell.isVisible() and not app.login.isVisible()
    assert context(app, side.home) is home and not home.session.ended
    assert len(app.engine.rootObjects()) == 2


@pytest.mark.parametrize("first, second", [(STAFF, ADMIN), (ADMIN, STAFF)], ids=["staff-then-admin", "admin-then-staff"])
def test_log_out_returns_to_a_fresh_login_form_and_the_next_account_sees_its_own_name(app, first, second):
    shell = app.log_in(first.username, first.password)
    app.controller.ui_prefs.animationScale = 1  # 開場圖會出現的設定：登出之後也不重播
    log_out(app, shell)

    form = qml(app.login, "stackView.currentItem")
    assert form.objectName() == "loginForm" and qml(app.login, "stackView.depth") == 1  # 沒有開場圖
    assert (qml(form, "username_textfield.text"), qml(form, "password_textfield.text")) == ("", "")
    assert qml(form, "error_message.text") == ""
    wait_until(lambda: qml(form, "username_textfield.activeFocus"))  # 一回來就能打帳號
    assert not qml(form, "loginButton.busy")
    for name in first.names:  # 舊的外殼註冊的名稱都是 null：另一端不會用到舊的物件
        assert context(app, name) is None, name
    assert app.controller.ui_prefs.account_id == ""  # 登入視窗開著時不留這個帳號的偏好（S1、S2）
    assert QuitGuard.instance().installed is False or not any(  # 舊的外殼不再攔 ⌘Q
        QuitGuard.instance().blocking(owner) for owner in list(QuitGuard.instance()._owners))
    app.controller.ui_prefs.animationScale = 0

    shell = app.log_in(second.username, second.password)
    assert shell.title() == second.title
    wait_until(lambda: qml(shell, "header.userName") == (STAFF_NAME if second is STAFF else ADMIN_NAME))
    for name in second.names:
        assert context(app, name) is not None, name
    stale = [name for name in first.names if name not in second.names]  # 只有另一端才有的名稱
    assert stale and all(qml(shell, f"{name} === null") for name in stale), stale
    # 共用的頁面用 typeof … !== "undefined" 判斷：null 時一樣拿到 null，不會拿到舊的物件
    if second is ADMIN:
        assert qml(shell, 'typeof deviceFinder !== "undefined" ? deviceFinder : null') is None
    log_out(app, shell)  # 再登出一次也正常


def test_the_next_staff_member_sees_only_their_own_services(app, db):
    """同一台電腦換人：A 登出、B 登入，Services 只有 B 的服務，頁首是 B 的名字。"""
    db.execute("INSERT INTO tbl_account (account_id, account_username, account_password, account_name, account_level) "
               "VALUES (%s, 'lee', %s, '李小華', '1')", (OTHER_STAFF_ID, hash_password("CHANGE-ME-lee-0000")))
    db.add_service("S20261030090000", "2026-10-30 09:00:00")  # A（佔位的服務人員）的
    db.add_service("S20261031100000", "2026-10-31 10:00:00", account_id=OTHER_STAFF_ID)  # B 的

    def listed(shell):
        qml(shell, "servicesButton.click()")
        wait_until(lambda: qml(shell, "mainContentLoader.status === Loader.Ready"))
        wait_until(lambda: idle(app.controller.tasks))
        model = context(app, "serviceManager").listModel
        return [model.data(model.index(row, 0), role) for row in range(model.rowCount())
                for role, name in model.roleNames().items() if bytes(name) == b"serviceId"]

    shell = app.log_in(STAFF.username, STAFF.password)
    assert listed(shell) == ["S20261030090000"]
    log_out(app, shell)
    shell = app.log_in("lee", "CHANGE-ME-lee-0000")
    wait_until(lambda: qml(shell, "header.userName") == "李小華")
    assert qml(shell, "serverHome.getID()") == OTHER_STAFF_ID
    assert listed(shell) == ["S20261031100000"]


@BOTH_SIDES
def test_after_log_out_the_old_home_object_is_released(app, side):
    shell = app.log_in(side.username, side.password)
    home = weakref.ref(context(app, side.home))
    pieces = [weakref.ref(context(app, name)) for name in side.names]
    log_out(app, shell)
    wait_until(lambda: idle(app.controller.tasks))  # 被取消的工作收尾完，callback 不再留著舊的物件
    gc.collect()  # 測試期間自動的 GC 關著，在主執行緒收（T34）
    assert home() is None
    assert [ref for ref in pieces if ref() is not None] == []


def test_log_out_cancels_the_accounts_background_work_and_clears_its_notifications(app):
    shell = app.log_in(STAFF.username, STAFF.password)
    home = context(app, STAFF.home)
    services = app.services
    services.notifier.notify("success", "Analysis complete", "S20261030090000 is ready.", "done:S1",
                             "open-analysis:S20261030090000", "View Results")
    home.watcher.add("S20261030090000")  # 有一筆在分析中：監看在計時
    assert home.watcher.polling
    seen, delivered = [], []

    def download(token, report):  # 例如明細頁還在下載的檔案：一直做到被取消
        seen.append("started")
        while not token.cancelled:
            time.sleep(0.01)
        seen.append("cancelled")
        token.raise_if_cancelled()

    services.tasks.submit("io", "media:S20261030090000:cam0.avi", download, on_ok=delivered.append,
                          on_err=delivered.append)
    wait_until(lambda: seen == ["started"])
    log_out(app, shell)
    assert not home.watcher.polling  # 監看停了
    assert toasts(services.notifier) == []  # 下一個帳號看不到這個帳號的通知
    wait_until(lambda: idle(services.tasks))
    assert seen == ["started", "cancelled"] and delivered == []  # 取消的工作不交回結果
    assert services.tasks.submit("db", "after-log-out", lambda token, report: 1)  # runner 照常可以用（不是 shutdown）
    wait_until(lambda: idle(services.tasks))


# ── launch() 註冊的名稱 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("module", [server_home_init, admin_home_init], ids=["staff", "admin"])
def test_context_names_list_every_name_launch_registers(module):
    """登出時清掉的是 CONTEXT_NAMES：launch() 多註冊一個名稱，這張表也要加（不然換帳號時另一端看得到舊的物件）。"""
    source = inspect.getsource(module.launch)
    registered = (set(re.findall(r'setContextProperty\("(\w+)"', source))
                  | set(re.findall(r'^\s+"(\w+)": home\b', source, re.MULTILINE)))
    assert registered == set(module.CONTEXT_NAMES)


def test_launch_registers_every_context_name(engine, services):
    for module, account in ((server_home_init, STAFF_ID), (admin_home_init, "00000000-0000-0000-0000-000000000001")):
        home = module.launch(engine, account, services)
        for name in module.CONTEXT_NAMES:
            assert engine.rootContext().contextProperty(name) is not None, name
        assert QuitGuard.instance().blocking(home.session)  # 外殼開著：⌘Q 先問
