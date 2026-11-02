"""設定頁（components/SettingsView.qml，兩端共用）：英文、Basic 的單選鈕、鍵盤和輔助技術（F5、T23）。

- 新版介面（PLAN 6-7）：一張卡片兩組，選項是整塊可以點的方塊；頁面標題在外殼的頁首。
- Window：Windowed／Full Screen 的選取狀態跟著主視窗，按下去會切換主視窗。
- Reduce Motion 存起來、切換 Theme 的測試在 test_navigation_a11y.py。
"""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
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


def reveal(item):
    """item 在設定頁的 Flickable 裡、沒有整個露出來時，先捲到它（Account 區塊在 General 上面，離線的視窗只有 800 高）。"""
    flickable = item.parentItem()
    while flickable is not None and not flickable.inherits("QQuickFlickable"):
        flickable = flickable.parentItem()
    top = item.mapToItem(flickable, QPointF(0, 0)).y()
    if 0 <= top and top + item.height() <= flickable.height():
        return
    highest = max(0.0, flickable.property("contentHeight") - flickable.height())
    flickable.setProperty("contentY", min(highest, max(0.0, flickable.property("contentY") + top)))


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
    window, _page, view = open_settings(engine, home_init, account_id, page, services)
    assert qml(window, "header.title") == "Settings"  # 頁面標題在外殼的頁首，頁面自己不畫（新版介面 PLAN 6-1）
    # 偏好立刻生效；帳號（兩端）和設備設定（只有服務人員端）要按 Save（new-feature S4、S6）
    assert qml(window, "header.subtitle") == {
        "server_setting_button.qml": "Preferences apply right away. Account and device changes apply when you save.",
        "admin_setting_page.qml": "Preferences apply right away. Account changes apply when you save."}[page]
    assert [option(view, name).property("text") for name in OPTIONS] == [
        "Windowed", "Full Screen", "Follow System", "On", "Off"]
    assert option(option(view, "motionSection"), "groupHint").property("text") == (
        "Follow System uses the Reduce Motion setting on this Mac. On turns off page fades and other animations.")
    assert not option(option(view, "windowSection"), "groupHint").isVisible()  # Window 沒有說明
    # 輔助技術：每一組有名字，選項是單選鈕、名字就是文字（DS-51：區塊叫 Window、Reduce Motion）
    for section, name in (("windowSection", "Window"), ("motionSection", "Reduce Motion")):
        assert qml(option(view, section), "Accessible.role === Accessible.Grouping")
        assert qml(option(view, section), "Accessible.name") == name
    for name in OPTIONS:
        assert qml(option(view, name), "Accessible.role === Accessible.RadioButton")
        assert qml(option(view, name), "Accessible.name") == option(view, name).property("text")


@SIDES
def test_options_are_whole_tiles_that_show_which_one_is_chosen(engine, services, qml_warnings, home_init, account_id,
                                                                page):
    """新版介面 PLAN 6-7：選項是整塊可以點的方塊（52 高）；選到的方塊底色、框線不同，圓圈裡有點（不只靠顏色）。"""
    window, _page, view = open_settings(engine, home_init, account_id, page, services)
    wait_until(window.isExposed)  # offscreen 的全螢幕是 800×600
    tiles = [option(view, name) for name in OPTIONS]
    assert {tile.height() for tile in tiles} == {52}
    assert tiles[0].width() == tiles[1].width() and tiles[2].width() == tiles[3].width() == tiles[4].width()
    follow, on = option(view, "motionFollowSystem"), option(view, "motionOn")
    assert qml(follow, "background.color") == qml(follow, "Theme.tileSelected")
    assert qml(on, "background.color") == qml(on, "Theme.tile")
    assert qml(follow, "indicator.children[0].visible") and not qml(on, "indicator.children[0].visible")

    reveal(on)
    right_edge = on.mapToScene(QPointF(on.width() - 12, on.height() / 2)).toPoint()  # 離圓圈和字都很遠的地方
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, right_edge)
    assert services.ui_prefs.motionMode == 1
    assert qml(on, "background.color") == qml(on, "Theme.tileSelected") and qml(on, "indicator.children[0].visible")
    assert qml(follow, "background.color") == qml(follow, "Theme.tile")


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
