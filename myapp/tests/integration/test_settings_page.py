"""設定頁（components/SettingsView.qml，兩端共用）：英文、Basic 的單選鈕、鍵盤和輔助技術（F5、T23）。

- Window：Windowed／Full Screen 的選取狀態跟著主視窗，按下去會切換主視窗。
- Reduce Motion 存起來、切換 Theme 的測試在 test_navigation_a11y.py。
"""
import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest

from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, wait_until

pytestmark = pytest.mark.integration

FULL_SCREEN = QWindow.Visibility.FullScreen
SIDES = pytest.mark.parametrize("home_init, account_id, page", [
    (server_home_init, STAFF_ID, "server_setting_button.qml"),
    (admin_home_init, ADMIN_ID, "admin_setting_page.qml"),
], ids=["staff", "admin"])
OPTIONS = ["windowedOption", "fullScreenOption", "motionFollowSystem", "motionOn", "motionOff"]


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


def open_settings(engine, home_init, account_id, page, services):
    home_init.launch(engine, account_id, services)  # 主視窗一打開就是全螢幕
    window = engine.rootObjects()[-1]
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    page_item = qml(window, "mainContentLoader.item")  # 留著參照，子物件的 wrapper 才不會失效（T17）
    return window, page_item, page_item.findChild(QObject, "settingsView")


def option(view, name):
    found = view.findChild(QObject, name)
    assert found is not None, name
    return found


def checked(view):
    """（Windowed, Full Screen）各自有沒有選取。"""
    return option(view, "windowedOption").property("checked"), option(view, "fullScreenOption").property("checked")


@SIDES
def test_window_mode_options_follow_and_switch_main_window(engine, services, qml_warnings, home_init, account_id,
                                                           page):
    window, _page, view = open_settings(engine, home_init, account_id, page, services)
    assert window.visibility() == FULL_SCREEN
    assert checked(view) == (False, True)

    qml(option(view, "windowedOption"), "click()")
    wait_until(lambda: window.visibility() != FULL_SCREEN)
    assert checked(view) == (True, False)

    qml(option(view, "windowedOption"), "click()")  # 已經選的再按一次：還是視窗模式，不會兩個都沒選
    assert checked(view) == (True, False)

    qml(option(view, "fullScreenOption"), "click()")
    wait_until(lambda: window.visibility() == FULL_SCREEN)
    assert checked(view) == (False, True)


@SIDES
def test_settings_page_is_english_with_named_groups(engine, services, qml_warnings, home_init, account_id, page):
    _window, _page, view = open_settings(engine, home_init, account_id, page, services)
    assert option(view, "settingsTitle").property("text") == "Settings"
    assert [option(view, name).property("text") for name in OPTIONS] == [
        "Windowed", "Full Screen", "Follow System", "On", "Off"]
    assert option(view, "motionHint").property("text") == "Follow System uses the Reduce Motion setting on this Mac."
    # 輔助技術：每一組有名字，選項是單選鈕、名字就是文字（DS-51：區塊叫 Window、Reduce Motion）
    for section, name in (("windowSection", "Window"), ("motionSection", "Reduce Motion")):
        assert qml(option(view, section), "Accessible.role === Accessible.Grouping")
        assert qml(option(view, section), "Accessible.name") == name
    for name in OPTIONS:
        assert qml(option(view, name), "Accessible.role === Accessible.RadioButton")
        assert qml(option(view, name), "Accessible.name") == option(view, name).property("text")


def test_options_take_tab_focus_show_the_focus_ring_and_toggle_with_space(engine, services, qml_warnings):
    window, _page, view = open_settings(engine, server_home_init, STAFF_ID, "server_setting_button.qml", services)
    window.requestActivate()
    wait_until(window.isActive)
    windowed = option(view, "windowedOption")
    qml(windowed, "forceActiveFocus(Qt.TabFocusReason)")
    order = []
    for _ in OPTIONS:
        focused = window.activeFocusItem()
        order.append(focused.objectName())
        assert focused.property("visualFocus")  # 看得到焦點框（DS-30）
        assert qml(focused, "background.children[0].visible")
        QTest.keyClick(window, Qt.Key.Key_Tab)
    assert order == OPTIONS

    qml(windowed, "forceActiveFocus(Qt.TabFocusReason)")
    QTest.keyClick(window, Qt.Key.Key_Space)  # 只用鍵盤也能換成視窗模式
    wait_until(lambda: window.visibility() != FULL_SCREEN)
    assert checked(view) == (True, False)
    assert not qml(option(view, "motionOn"), "background.children[0].visible")  # 沒有焦點的不畫框
