"""設定頁的「視窗模式／全螢幕模式」：勾選狀態跟著主視窗，按下去會切換主視窗。"""
import pytest
from PySide6.QtGui import QWindow

from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, wait_until

pytestmark = pytest.mark.integration

FULL_SCREEN = QWindow.Visibility.FullScreen


def checked(settings):
    """（視窗模式, 全螢幕模式）各自有沒有打勾。"""
    return qml(settings, "exitFullscreenCheckbox.checked"), qml(settings, "fullscreenCheckbox.checked")


@pytest.mark.parametrize("home_init, account_id, page", [
    (server_home_init, STAFF_ID, "server_setting_button.qml"),
    (admin_home_init, ADMIN_ID, "admin_setting_page.qml"),
], ids=["staff", "admin"])
def test_window_mode_checkboxes_follow_and_switch_main_window(engine, db, home_init, account_id, page):
    home_init.launch(engine, account_id)  # 主視窗一打開就是全螢幕
    window = engine.rootObjects()[0]
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    settings = qml(window, "mainContentLoader.item")
    assert window.visibility() == FULL_SCREEN
    assert checked(settings) == (False, True)

    qml(settings, "exitFullscreenCheckbox.click()")
    wait_until(lambda: window.visibility() != FULL_SCREEN)
    assert checked(settings) == (True, False)

    qml(settings, "exitFullscreenCheckbox.click()")  # 已經勾的再按一次：還是視窗模式，不會兩個都沒勾
    assert checked(settings) == (True, False)

    qml(settings, "fullscreenCheckbox.click()")
    wait_until(lambda: window.visibility() == FULL_SCREEN)
    assert checked(settings) == (False, True)
