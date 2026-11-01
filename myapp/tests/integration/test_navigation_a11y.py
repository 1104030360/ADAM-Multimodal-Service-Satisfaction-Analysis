"""F5 外殼與登入（T23）：側邊欄、換頁、開場圖、Reduce Motion 的設定。

- 側邊欄是真的按鈕（SidebarButton）：Tab 的順序、Return／Enter／Space、⌘1～⌘6、目前頁面、hover、按下、
  Accessible 的名稱（UX-41、UX-44、DS-30、DS-32、DS-51）。
- 換頁（DS-29、D10）：側邊欄在背景載入；新頁面淡入 150 ms，動畫時間是 0 時直接出現；唸出頁名。
- 開場圖（D3、M-10）：整段 ≤ 1 s；點一下、按任意鍵就跳過；減少動態時整段不出現。
- 動畫時間：tests/conftest.py 設了 ADAM_ANIMATION_SCALE=0（Theme 的 dur* 全是 0）。要看真的動畫時，測試自己把
  uiPrefs.animationScale 調大，動畫變慢，才看得到「正在跑」；只斷言設定的長度和最後的狀態，不量時間（CONTRACTS §0）。
- 每個測試都要零 QML 警告（engine.warnings）。
"""
import gc
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from myapp import main
from myapp.init import admin_home_init, server_home_init
from myapp.main import MainWindow
from myapp.services import CLOSE_DB_KEY
from myapp.ui_prefs import UiPrefs
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, wait_until

pytestmark = pytest.mark.integration

QML_DIR = Path(main.__file__).parent / "qml"
LOGIN_QML = QML_DIR / "pages" / "login.qml"
NAV_ICONS = QML_DIR / "components" / "icons" / "nav"

# 側邊欄由上到下：(objectName, 文字（DS-51）, 頁面, Lucide 圖示)；順序也是 ⌘1～⌘6
STAFF = SimpleNamespace(module=server_home_init, account=STAFF_ID, detail_page="server_service_detail_page.qml", pages=[
    ("homeButton", "Home", "server_home_button.qml", "house"),
    ("recordButton", "Record", "record_widget.qml", "video"),
    ("analysisButton", "Analysis", "analysis.qml", "brain-circuit"),
    ("servicesButton", "Services", "server_service_page.qml", "history"),
    ("reportsButton", "Reports", "server_reports_page.qml", "file-chart-column"),
    ("settingsButton", "Settings", "server_setting_button.qml", "settings"),
])
ADMIN = SimpleNamespace(module=admin_home_init, account=ADMIN_ID, detail_page="admin_service_detail_page.qml", pages=[
    ("homeButton", "Home", "admin_home_page.qml", "house"),
    ("addAccountButton", "Add Account", "admin_add_page.qml", "user-round-plus"),
    ("servicesButton", "Services", "admin_service_page.qml", "history"),
    ("reportsButton", "Reports", "admin_reports_page.qml", "file-chart-column"),
    ("settingsButton", "Settings", "admin_setting_page.qml", "settings"),
])
QUIT = ("exitButton", "Quit", None, "log-out")
BOTH_SIDES = pytest.mark.parametrize("side", [STAFF, ADMIN], ids=["staff", "admin"])
CTRL = Qt.KeyboardModifier.ControlModifier  # Qt 在 macOS 上的 Ctrl 就是 ⌘
# 自己把焦點放進第一個欄位的頁面（新增帳號頁，T19：一進來就能打帳號）。其他頁面換頁後焦點留在側邊欄（DS-29）
PAGES_THAT_TAKE_FOCUS = {"admin_add_page.qml"}


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


# ── 小工具 ───────────────────────────────────────────────────────────────────

def settled(side, home, services):
    """launch() 在背景載入的資料都回來了：之後的畫面和焦點不會再被它們改動。"""
    if side is STAFF:
        return not any(services.tasks.isBusy(key) for key in server_home_init.LAUNCH_KEYS)
    sections = (home.profile, home.serviceScoreManager, home.rankingModel, home.trendDataManager)
    return all(section.state != "loading" and not section.refreshing for section in sections)


def launch(side, engine, services):
    """登入後的主畫面，視窗是作用中的（快捷鍵、按鍵只送到作用中的視窗）。

    先收掉前面測試留下的主畫面物件，並清掉沒人用的 QML property cache。原因（T23）：PySide 在 connect 沒有
    @Slot 的方法時，給那個物件一個自己的 metaobject（AdminHome、ServerHome 和幾個模型都是）；物件被 gc 收掉後，
    新物件的 metaobject 可能剛好用到同一個位址，QML 依位址快取的 property cache 就對不上，
    偶爾讀到 adminHome.profile 是 undefined。同一個程式裡只有一個主畫面的 app 不會遇到，測試會。
    """
    gc.collect()
    engine.clearComponentCache()
    home = side.module.launch(engine, side.account, services)
    window = engine.rootObjects()[-1]
    wait_until(window.isExposed)
    window.requestActivate()
    wait_until(window.isActive)
    wait_until(lambda: settled(side, home, services))
    return SimpleNamespace(home=home, window=window)


def child(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def theme(window, token):
    return qml(window, f"Theme.{token}")


def center(item):
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def page_ready(window, file):
    return qml(window, "currentPage") == file and qml(window, "mainContentLoader.status === Loader.Ready")


def wait_for_page(window, file):
    wait_until(lambda: page_ready(window, file))
    return qml(window, "mainContentLoader.item")


def announced(window):
    return qml(window, "announced.join('|')")


def current_buttons(window, side):
    return [name for name, *_ in side.pages if child(window, name).property("current")]


# ── 側邊欄：真的按鈕、名稱、Tab、按鍵、⌘1～⌘6 ─────────────────────────────

@BOTH_SIDES
def test_sidebar_buttons_are_real_buttons_named_in_sidebar_order(side, engine, services, qml_warnings):
    ui = launch(side, engine, services)
    names, labels = zip(*[(name, label) for name, label, *_ in side.pages + [QUIT]])
    buttons = [child(ui.window, name) for name in names]
    assert [button.property("text") for button in buttons] == list(labels)  # DS-51
    for button, label in zip(buttons, labels):
        assert button.inherits("QQuickAbstractButton")  # 不再是 Rectangle＋MouseArea（UX-41）
        assert qml(button, "Accessible.role === Accessible.Button")
        assert qml(button, "Accessible.name") == label
        assert qml(button, "focusPolicy === Qt.StrongFocus") and button.property("hoverEnabled")  # UX-44
    tops = [button.mapToScene(QPointF(0, 0)).y() for button in buttons]
    assert tops == sorted(tops)
    assert current_buttons(ui.window, side) == ["homeButton"]
    assert qml(child(ui.window, "homeButton"), "Accessible.description") == "Current page"
    assert qml(child(ui.window, "exitButton"), "Accessible.description") == ""


@BOTH_SIDES
def test_tab_reaches_every_sidebar_button_in_order_with_a_focus_ring(side, engine, services, qml_warnings):
    ui = launch(side, engine, services)
    names = [name for name, *_ in side.pages + [QUIT]]
    qml(ui.window, "contentItem.forceActiveFocus()")  # 從頭開始
    reached = []
    for _ in names:
        QTest.keyClick(ui.window, Qt.Key.Key_Tab)
        focused = ui.window.activeFocusItem()
        reached.append(focused.objectName())
        assert focused.property("visualFocus") and child(focused, "focusRing").isVisible()  # DS-30
        assert qml(child(focused, "focusRing"), "border.color") == theme(ui.window, "focusOnDark")
    assert reached == names
    assert [name for name in names if child(ui.window, name).findChild(QObject, "focusRing").isVisible()] == ["exitButton"]


def test_return_enter_and_space_activate_the_focused_button_and_focus_stays(engine, services, qml_warnings):
    ui = launch(STAFF, engine, services)
    for name, key, file in (("analysisButton", Qt.Key.Key_Return, "analysis.qml"),
                            ("servicesButton", Qt.Key.Key_Space, "server_service_page.qml"),
                            ("reportsButton", Qt.Key.Key_Enter, "server_reports_page.qml")):
        button = child(ui.window, name)
        qml(button, "forceActiveFocus(Qt.TabFocusReason)")
        QTest.keyClick(ui.window, key)
        wait_for_page(ui.window, file)
        assert button.hasActiveFocus() and button.property("current")  # 焦點留在側邊欄（DS-29）
    assert announced(ui.window) == "Analysis|Services|Reports"


@BOTH_SIDES
def test_command_number_keys_switch_pages_in_sidebar_order(side, engine, services, qml_warnings):
    ui = launch(side, engine, services)
    order = list(range(1, len(side.pages))) + [0]  # ⌘2… 依序，最後回到 ⌘1（一開始就在首頁）
    for index in order:
        name, label, file, _icon = side.pages[index]
        QTest.keyClick(ui.window, Qt.Key(Qt.Key.Key_1.value + index), CTRL)
        assert qml(ui.window, "currentPage") == file  # 側邊欄的「目前頁面」立刻換（DS-29）
        page = wait_for_page(ui.window, file)
        button = child(ui.window, name)
        assert current_buttons(ui.window, side) == [name]
        if file in PAGES_THAT_TAKE_FOCUS:
            assert page.isAncestorOf(ui.window.activeFocusItem())
        else:
            assert button.hasActiveFocus() and button.property("visualFocus")  # 和按那個按鈕一樣，焦點在它上面
    assert announced(ui.window) == "|".join(side.pages[index][1] for index in order)
    if side is ADMIN:  # 管理員端只有 5 頁：⌘6 沒有作用
        QTest.keyClick(ui.window, Qt.Key.Key_6, CTRL)
        assert qml(ui.window, "currentPage") == side.pages[0][2]


def test_shortcuts_wait_while_a_modal_dialog_is_open(engine, services, qml_warnings):
    ui = launch(STAFF, engine, services)
    dialog = child(ui.window, "quitDialog")
    qml(dialog, "open()")
    wait_until(lambda: qml(dialog, "opened"))
    QTest.keyClick(ui.window, Qt.Key.Key_3, CTRL)
    assert qml(ui.window, "currentPage") == "server_home_button.qml"  # 「Quit while uploading?」開著：不換頁
    QTest.keyClick(ui.window, Qt.Key.Key_Escape)  # 等於 Keep Uploading
    wait_until(lambda: not qml(dialog, "visible"))
    QTest.keyClick(ui.window, Qt.Key.Key_3, CTRL)
    wait_for_page(ui.window, "analysis.qml")


# ── 目前頁面、hover、按下（DS-32、UX-44）─────────────────────────────────────

def test_current_page_is_light_and_moves_the_moment_a_page_is_requested(engine, services, qml_warnings):
    ui = launch(STAFF, engine, services)
    home, record, reports = (child(ui.window, name) for name in ("homeButton", "recordButton", "reportsButton"))
    # 目前頁面：#E6E8EB 底、#0E1013 字和圖示（DS-32）
    assert child(home, "currentBackground").isVisible()
    assert qml(child(home, "currentBackground"), "color") == theme(ui.window, "navItemCurrent")
    assert child(home, "navLabel").property("color") == theme(ui.window, "navItemCurrentText")
    assert child(home, "navIcon").property("source").toString().endswith("icons/nav/house_navItemCurrentText.svg")
    # 其他按鈕：#444444 底、白字、亮色圖示
    assert not child(record, "currentBackground").isVisible()
    assert child(record, "navBackground").property("color") == theme(ui.window, "navItem")
    assert child(record, "navLabel").property("color") == theme(ui.window, "textOnDark")
    assert child(record, "navIcon").property("source").toString().endswith("icons/nav/video_chromeIcon.svg")

    old_page = qml(ui.window, "mainContentLoader.item")
    qml(reports, "click()")
    # 側邊欄立刻換；頁面在背景載入（ENG-19）：舊頁面已經拿掉，新頁面還沒建好，不會兩頁疊在一起
    assert reports.property("current") and not home.property("current")
    assert child(reports, "currentBackground").isVisible() and not child(home, "currentBackground").isVisible()
    assert qml(ui.window, "mainContentLoader.status === Loader.Loading")
    assert qml(ui.window, "mainContentLoader.item === null") and not old_page.isVisible()
    page = wait_for_page(ui.window, "server_reports_page.qml")

    qml(reports, "click()")  # 同一頁再按一次：不重新載入
    assert qml(ui.window, "mainContentLoader.item") is page and announced(ui.window) == "Reports"

    # 明細頁（Details 換的頁，同步）在側邊欄算 Services
    qml(ui.window, f'mainContentLoader.source = "../../qml/pages/{STAFF.detail_page}"')
    assert qml(ui.window, "mainContentLoader.status === Loader.Ready")
    assert current_buttons(ui.window, STAFF) == ["servicesButton"]
    assert announced(ui.window) == "Reports|Service Details"


def test_hover_and_press_change_the_colour_and_a_mouse_click_shows_no_focus_ring(engine, services, qml_warnings):
    ui = launch(STAFF, engine, services)
    record = child(ui.window, "recordButton")
    background = child(record, "navBackground")
    QTest.mouseMove(ui.window, center(record))
    wait_until(lambda: record.property("hovered"))
    assert background.property("color") == theme(ui.window, "navItemHover")  # 以前沒開 hoverEnabled，從來不會出現
    QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center(record))
    assert record.property("down") and background.property("color") == theme(ui.window, "navItemPressed")
    QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center(record))
    wait_for_page(ui.window, "record_widget.qml")
    assert record.property("current") and record.hasActiveFocus() and not record.property("visualFocus")
    assert not child(record, "focusRing").isVisible()  # 滑鼠點的不畫焦點框（DS-30）

    # 完整動態：顏色在 100 ms（durFast）內變過去，最後一樣
    services.ui_prefs.animationScale = 1
    assert theme(ui.window, "durFast") == 100
    reports = child(ui.window, "reportsButton")
    QTest.mouseMove(ui.window, center(reports))
    wait_until(lambda: child(reports, "navBackground").property("color") == theme(ui.window, "navItemHover"))
    QTest.mouseMove(ui.window, QPoint(ui.window.width() - 40, 40))  # 移到內容區
    wait_until(lambda: child(reports, "navBackground").property("color") == theme(ui.window, "navItem"))


# ── 換頁的淡入與唸頁名（DS-29、D10）──────────────────────────────────────────

@BOTH_SIDES
def test_page_switch_is_instant_when_the_animation_scale_is_zero(side, engine, services, qml_warnings):
    ui = launch(side, engine, services)
    assert theme(ui.window, "durBase") == 0  # tests/conftest.py：animationScale 0
    name, label, file, _icon = side.pages[3]
    qml(child(ui.window, name), "click()")
    page = wait_for_page(ui.window, file)
    assert page.property("opacity") == 1 and not qml(ui.window, "pageFade.running")  # 沒有停在透明的起點
    assert announced(ui.window) == label


@BOTH_SIDES
def test_page_switch_fades_the_new_page_in_with_full_motion(side, engine, services, qml_warnings):
    ui = launch(side, engine, services)
    services.ui_prefs.animationScale = 1
    assert theme(ui.window, "durBase") == 150  # D10
    fade = child(ui.window, "pageFade")
    assert (fade.property("from"), fade.property("to"), fade.property("duration")) == (0, 1, 150)
    assert qml(fade, "easing.type === Easing.OutCubic")  # DS-29

    services.ui_prefs.animationScale = 10  # 慢 10 倍，才看得到「正在淡入」
    name, label, file, _icon = side.pages[3]
    qml(child(ui.window, name), "click()")
    page = wait_for_page(ui.window, file)
    assert qml(ui.window, "pageFade.running && pageFade.target === mainContentLoader.item")
    wait_until(lambda: not qml(ui.window, "pageFade.running"), timeout=10)
    assert page.property("opacity") == 1

    services.ui_prefs.motionMode = 1  # 減少動態：直接出現
    name, label, file, _icon = side.pages[4]
    qml(child(ui.window, name), "click()")
    page = wait_for_page(ui.window, file)
    assert page.property("opacity") == 1 and not qml(ui.window, "pageFade.running")
    assert announced(ui.window) == "|".join(side.pages[index][1] for index in (3, 4))


def test_view_results_reaches_the_row_even_while_a_sidebar_page_is_loading(engine, services, db, qml_warnings):
    sid = "S20261030120000"
    db.add_service(sid, "2026-10-30 12:00:00", status=3)
    db.add_scores(sid, customer=80, server=70)
    ui = launch(STAFF, engine, services)
    qml(child(ui.window, "reportsButton"), "click()")
    assert qml(ui.window, "mainContentLoader.status === Loader.Loading")  # 側邊欄的換頁還在背景載入
    ui.home.openAnalysisRequested.emit(sid)  # 通知的 View Results（C3 的 Connections）
    page = qml(ui.window, "mainContentLoader.item")  # 要馬上標出那一列：同步建好
    assert page is not None and qml(ui.window, "currentPage") == "analysis.qml"
    assert (qml(page, "selectedTab"), qml(page, "highlightedServiceId")) == ("completed", sid)
    assert current_buttons(ui.window, STAFF) == ["analysisButton"]
    assert announced(ui.window).endswith("Analysis")


# ── 圖示 ─────────────────────────────────────────────────────────────────────

def test_nav_icons_are_precoloured_lucide_svgs_and_every_button_draws_one(engine, services, qml_warnings):
    ui = launch(STAFF, engine, services)
    page_icons = {icon for side in (STAFF, ADMIN) for *_, icon in side.pages}
    expected = {f"{icon}_{token}.svg" for icon in page_icons for token in ("chromeIcon", "navItemCurrentText")}
    assert {path.name for path in NAV_ICONS.iterdir()} == expected | {"log-out_chromeIcon.svg"}  # Quit 不會是目前頁面
    for path in NAV_ICONS.iterdir():
        svg = path.read_text(encoding="utf-8")
        assert svg.startswith("<!-- @license lucide-static v1.58.0 - ISC -->"), path.name
        strokes = re.findall(r'stroke="(#[0-9A-F]{6})"', svg)
        assert len(strokes) == 1 and "currentColor" not in svg, path.name
        assert QColor(strokes[0]) == theme(ui.window, path.stem.rsplit("_", 1)[1]), path.name
    images = [child(child(ui.window, name), "navIcon") for name, *_ in STAFF.pages + [QUIT]]
    wait_until(lambda: all(qml(image, "status === Image.Ready") for image in images))


# ── Reduce Motion（DS-43、ENG-20）───────────────────────────────────────────

@BOTH_SIDES
def test_reduce_motion_setting_is_saved_and_switches_the_theme(side, engine, services, qml_warnings):
    ui = launch(side, engine, services)
    services.ui_prefs.animationScale = 1  # 看得出「完整」和「減少」的差別
    name, _label, file, _icon = side.pages[-1]
    qml(child(ui.window, name), "click()")
    page = wait_for_page(ui.window, file)  # 留著參照，子物件的 wrapper 才不會失效（T17）
    view = child(page, "settingsView")
    follow, on, off = (child(view, option) for option in ("motionFollowSystem", "motionOn", "motionOff"))

    def selected():
        return [option.property("checked") for option in (follow, on, off)]

    assert selected() == [True, False, False] and services.ui_prefs.motionMode == 0
    assert theme(ui.window, "reduceMotion") == theme(ui.window, "systemReduceMotion")

    qml(on, "click()")
    assert services.ui_prefs.motionMode == 1 and UiPrefs().motionMode == 1  # 存在 QSettings：重開 app 還在
    assert theme(ui.window, "reduceMotion") and theme(ui.window, "durBase") == 0
    assert selected() == [False, True, False]

    qml(off, "click()")
    assert services.ui_prefs.motionMode == 2 and UiPrefs().motionMode == 2
    assert not theme(ui.window, "reduceMotion") and theme(ui.window, "durBase") == 150
    assert selected() == [False, False, True]

    qml(follow, "click()")
    assert services.ui_prefs.motionMode == 0 and UiPrefs().motionMode == 0
    assert theme(ui.window, "reduceMotion") == theme(ui.window, "systemReduceMotion")

    services.ui_prefs.motionMode = 1  # 從別的地方改：選項跟著
    assert selected() == [False, True, False]


# ── 開場圖（D3、DS-72／M-10）───────────────────────────────────────────────

@pytest.fixture
def login_app(engine):
    """登入視窗的 Python 端；load() 先設好動態設定再載入 login.qml。這幾個測試不登入，不連資料庫。"""
    controller = MainWindow(engine)

    def load(**prefs):
        for name, value in prefs.items():
            setattr(controller.ui_prefs, name, value)
        engine.load(QUrl.fromLocalFile(str(LOGIN_QML)))
        window = engine.rootObjects()[0]
        wait_until(window.isExposed)
        return window

    yield SimpleNamespace(controller=controller, load=load)
    if controller.services.close_db():
        wait_until(lambda: not controller.tasks.isBusy(CLOSE_DB_KEY))
    controller.tasks.shutdown()


def current_screen(window):
    return qml(window, "stackView.currentItem.objectName")


def test_splash_fades_in_and_hands_over_to_the_login_form_within_one_second(login_app, qml_warnings):
    window = login_app.load(animationScale=1)
    assert qml(window, "splashShown") and current_screen(window) == "splash"
    splash = qml(window, "stackView.currentItem")
    fade, hold = child(splash, "splashFade"), child(splash, "splashTimer")
    assert (fade.property("from"), fade.property("to"), fade.property("duration")) == (0, 1, 400)  # M-10
    assert qml(fade, "target.objectName") == "splashLogo"
    assert hold.property("interval") == 800  # 淡入 400＋停 400
    assert qml(window, "formFadeIn.duration") == 150  # 表單淡入
    assert qml(window, "splashTotal") == 950 <= 1000  # 整段最多 1 s（D3）

    wait_until(lambda: current_screen(window) == "loginForm" and not qml(window, "stackView.busy"))
    form = qml(window, "stackView.currentItem")
    wait_until(lambda: qml(form, "username_textfield.activeFocus"))  # 表單的行為照舊：一進來就能打帳號
    assert qml(window, "stackView.depth") == 1


@pytest.mark.parametrize("skip", ["click", "key"])
def test_a_click_or_any_key_skips_the_splash(login_app, qml_warnings, skip):
    window = login_app.load(animationScale=10)  # 不跳過的話整段要 9.5 秒
    window.requestActivate()
    wait_until(window.isActive)
    assert current_screen(window) == "splash" and qml(window, "stackView.currentItem.activeFocus")
    if skip == "click":
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(window.width() // 2, window.height() // 2))
    else:
        QTest.keyClick(window, Qt.Key.Key_A)
    assert current_screen(window) == "loginForm"  # 按下去就換，不等計時
    wait_until(lambda: not qml(window, "stackView.busy"), timeout=10)
    form = qml(window, "stackView.currentItem")
    wait_until(lambda: qml(form, "username_textfield.activeFocus"))


def test_reduce_motion_opens_straight_on_the_login_form(login_app, qml_warnings):
    login_app.controller.ui_prefs.motionMode = 1
    window = login_app.load(animationScale=1)
    assert qml(window, "Theme.reduceMotion") and not qml(window, "splashShown")
    assert current_screen(window) == "loginForm" and qml(window, "stackView.depth") == 1
    assert window.findChild(QObject, "splash") is None  # 開場圖整段沒有建立（DS-43）
    form = qml(window, "stackView.currentItem")
    wait_until(lambda: qml(form, "username_textfield.activeFocus"))
