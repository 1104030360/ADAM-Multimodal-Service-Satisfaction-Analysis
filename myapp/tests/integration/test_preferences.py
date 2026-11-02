"""每個帳號自己的偏好（new-feature PLAN 13.2 S1、13.3 S2、13.8；T39）：真的外殼、設定頁、服務明細、監看。

- Settings → General：Start Playback Muted（兩端）；Show Analysis Completion Alerts、Bounce Dock Icon（只有服務人員端）。
  改了立刻生效、存在這個帳號的偏好裡（account_id 當 key）。
- 主視窗：登入後照這個帳號上次的模式打開（第一次全螢幕）。
- 播放器：音量自動記住（停下來才寫入磁碟）；每次打開服務時照 Start Playback Muted 決定要不要靜音；
  靜音和音量分開，取消靜音回到原本的音量；都不會開始播放。
- 通知：關掉完成提示只少了那一則 toast（分析、清單的更新、Dock 的徽章、失敗的通知都照舊）；關掉 Dock 跳動只少了跳動。
- 同一次執行換帳號（A、B、再 A）：每個帳號只看到自己的值。
"""
import dataclasses
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine

from myapp.init import admin_home_init, server_home_init
from myapp.init.server_home_init import ANALYSIS_KEY, HOME_KEYS, LAUNCH_KEYS, WATCHER_KEY
from myapp.ui_prefs import DEFAULT_VOLUME, UiPrefs, account_key, open_settings
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, toasts, wait_until

pytestmark = pytest.mark.integration

STAFF_B = "00000000-0000-0000-0000-0000000000b2"
ADMIN_B = "00000000-0000-0000-0000-0000000000b1"
SID = "S20261031093000"
OTHER = "S20261031101500"
START = "2026-10-31 09:30:00"
FULL_SCREEN, WINDOWED = QWindow.Visibility.FullScreen, QWindow.Visibility.Windowed
MEDIA_KEYS = {"cam0": f"video/{SID}_cam0.avi", "cam1": f"video/{SID}_cam1.avi", "audio": f"audio/{SID}_audio.wav"}


@dataclasses.dataclass(frozen=True)
class Side:
    name: str
    module: object
    account: str
    other_account: str
    level: str
    settings_page: str
    services_page: str


STAFF = Side("staff", server_home_init, STAFF_ID, STAFF_B, "1", "server_setting_button.qml", "server_service_page.qml")
ADMIN = Side("admin", admin_home_init, ADMIN_ID, ADMIN_B, "5", "admin_setting_page.qml", "admin_service_page.qml")
BOTH_SIDES = pytest.mark.parametrize("side", [STAFF, ADMIN], ids=["staff", "admin"])


# ── fixture 與小工具 ─────────────────────────────────────────────────────────

@pytest.fixture
def engines():
    """一次執行裡開好幾個外殼（登出再登入、重開 app）：每個都是自己的 engine，QML 不能有任何警告。"""
    made, warnings = [], []

    def new():
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
        made.append(engine)
        return engine

    yield new
    assert warnings == []
    for engine in made:
        engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class Busy:
    """TaskRunner 現在在忙的 key（busyChanged 累計）；seen 是出現過的。"""

    def __init__(self, tasks):
        self.now, self.seen = set(), []
        tasks.busyChanged.connect(self._changed)

    def _changed(self, key, busy):
        if busy:
            self.now.add(key)
            self.seen.append(key)
        else:
            self.now.discard(key)


@pytest.fixture
def busy(services):
    return Busy(services.tasks)


def launch(side, engine, services, busy, account_id=None):
    """登入後打開 side 那一端；等背景載入的資料都回來。回傳 (home, 主視窗)。"""
    home = side.module.launch(engine, account_id or side.account, services)
    wait_until(lambda: not busy.now)
    return home, engine.rootObjects()[-1]


def relaunched(services):
    """重開 app：同一個資料庫、新的 UiPrefs（從磁碟重新讀）。"""
    return dataclasses.replace(services, ui_prefs=UiPrefs())


def show(window, page):
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def find(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def open_settings_page(side, window):
    page = show(window, side.settings_page)  # 留著 page 的參照，子物件的 wrapper 才不會失效（T17）
    return page, find(page, "settingsView")


def stored(account_id, name):
    return open_settings().value(account_key(account_id, name))


def is_true(value):
    return value in (True, "true")


def checked(view, *names):
    return tuple(find(view, name).property("checked") for name in names)


# ── Settings → General ───────────────────────────────────────────────────────

@BOTH_SIDES
def test_general_settings_show_playback_on_both_sides_and_notifications_only_for_staff(side, engines, services,
                                                                                        busy):
    _home, window = launch(side, engines(), services, busy)
    _page, view = open_settings_page(side, window)
    muted = find(view, "startMutedSection")
    assert qml(muted, "Accessible.role === Accessible.Grouping") and qml(muted, "Accessible.name") == (
        "Start Playback Muted")
    assert find(muted, "groupHint").property("text") == (
        "Each service opens with the sound off. Unmute in the player to hear it at your last volume.")
    assert [find(view, name).property("text") for name in ("startMutedOn", "startMutedOff")] == ["On", "Off"]
    assert checked(view, "startMutedOn", "startMutedOff") == (False, True)  # 第一次：不靜音（和以前一樣）
    assert {find(view, name).height() for name in ("startMutedOn", "startMutedOff")} == {52}

    alerts, dock = find(view, "completionAlertsSection"), find(view, "dockBounceSection")
    if side is ADMIN:  # 管理員端沒有分析完成的通知：不顯示用不到的選項（PLAN 13.1）
        assert not alerts.isVisible() and not dock.isVisible()
        return
    assert alerts.isVisible() and dock.isVisible()
    assert qml(alerts, "Accessible.name") == "Show Analysis Completion Alerts"
    assert qml(dock, "Accessible.name") == "Bounce Dock Icon"
    assert find(alerts, "groupHint").property("text") == (
        "Results still appear on the Analysis page. Failed analyses and upload errors are always shown.")
    assert find(dock, "groupHint").property("text") == (
        "When ADAM is in the background, the Dock icon bounces when an analysis finishes or fails. "
        "The number on the icon still updates.")
    assert checked(view, "completionAlertsOn", "completionAlertsOff", "dockBounceOn", "dockBounceOff") == (
        True, False, True, False)  # 第一次：兩個都開（和以前一樣）
    for name in ("startMutedOn", "completionAlertsOff", "dockBounceOff"):
        assert qml(find(view, name), "Accessible.role === Accessible.RadioButton")


@BOTH_SIDES
def test_settings_options_apply_at_once_and_are_kept_for_this_account(side, engines, services, busy):
    _home, window = launch(side, engines(), services, busy)
    _page, view = open_settings_page(side, window)
    qml(find(view, "startMutedOn"), "click()")
    assert services.ui_prefs.startPlaybackMuted
    assert checked(view, "startMutedOn", "startMutedOff") == (True, False)
    assert is_true(stored(side.account, "startPlaybackMuted"))
    if side is STAFF:
        qml(find(view, "completionAlertsOff"), "click()")
        qml(find(view, "dockBounceOff"), "click()")
        assert not services.ui_prefs.showCompletionAlerts and not services.ui_prefs.bounceDockIcon
        assert checked(view, "completionAlertsOn", "dockBounceOn") == (False, False)

    again = relaunched(services)  # 重開 app
    _home, window = launch(side, engines(), again, Busy(again.tasks))
    _page, view = open_settings_page(side, window)
    assert checked(view, "startMutedOn", "startMutedOff") == (True, False)
    if side is STAFF:
        assert checked(view, "completionAlertsOff", "dockBounceOff") == (True, True)
    qml(find(view, "startMutedOff"), "click()")  # 改回來
    assert not again.ui_prefs.startPlaybackMuted and not is_true(stored(side.account, "startPlaybackMuted"))


# ── 主視窗的模式 ─────────────────────────────────────────────────────────────

@BOTH_SIDES
def test_main_window_reopens_in_the_accounts_last_mode(side, engines, services, busy):
    _home, window = launch(side, engines(), services, busy)
    assert window.visibility() == FULL_SCREEN  # 第一次：全螢幕（和以前一樣）
    _page, view = open_settings_page(side, window)
    qml(find(view, "windowedOption"), "click()")
    wait_until(lambda: not services.ui_prefs.fullScreen)

    again = relaunched(services)
    _home, window = launch(side, engines(), again, Busy(again.tasks))
    assert window.visibility() == WINDOWED
    _page, view = open_settings_page(side, window)
    assert checked(view, "windowedOption", "fullScreenOption") == (True, False)

    window.showFullScreen()  # macOS 的綠色按鈕、⌃⌘F 也算
    wait_until(lambda: again.ui_prefs.fullScreen)
    third = relaunched(services)
    _home, window = launch(side, engines(), third, Busy(third.tasks))
    assert window.visibility() == FULL_SCREEN


# ── 同一次執行換帳號：A、B、再 A ─────────────────────────────────────────────

@BOTH_SIDES
def test_switching_accounts_shows_only_each_accounts_own_preferences(side, engines, services, busy, db):
    db.add_account(side.other_account, f"{side.name}-b", f"Second {side.name}", level=side.level)
    prefs = services.ui_prefs
    _home, window_a = launch(side, engines(), services, busy)
    _page, view = open_settings_page(side, window_a)
    qml(find(view, "windowedOption"), "click()")
    qml(find(view, "startMutedOn"), "click()")
    if side is STAFF:
        qml(find(view, "completionAlertsOff"), "click()")
    prefs.playbackVolume = 0.8  # 播放器上調的
    wait_until(lambda: not prefs.fullScreen)

    # A 登出、B 登入（同一個 UiPrefs，和 app 裡一樣）：都是預設值，A 還沒寫入的音量先寫進 A 自己的 key
    _home, window_b = launch(side, engines(), services, busy, account_id=side.other_account)
    assert window_b.visibility() == FULL_SCREEN
    assert (prefs.playbackVolume, prefs.startPlaybackMuted, prefs.showCompletionAlerts) == (DEFAULT_VOLUME, False,
                                                                                           True)
    assert float(stored(side.account, "playbackVolume")) == 0.8 and stored(side.other_account, "playbackVolume") is None
    _page, view = open_settings_page(side, window_b)
    assert checked(view, "fullScreenOption", "startMutedOff") == (True, True)
    if side is STAFF:
        assert checked(view, "completionAlertsOn") == (True,)
    qml(find(view, "dockBounceOff" if side is STAFF else "startMutedOn"), "click()")
    prefs.playbackVolume = 0.3
    window_a.showFullScreen()  # A 的舊視窗之後才變：不會寫進 B（也不會蓋掉 A 上次的模式）
    for _ in range(5):
        QCoreApplication.processEvents()
    assert stored(side.other_account, "fullScreen") is None and not is_true(stored(side.account, "fullScreen"))

    # B 登出、A 再登入：A 自己的值都回來，B 的沒有帶過來
    _home, window_a2 = launch(side, engines(), services, busy)
    assert window_a2.visibility() == WINDOWED
    assert (prefs.playbackVolume, prefs.startPlaybackMuted, prefs.bounceDockIcon) == (0.8, True, True)
    _page, view = open_settings_page(side, window_a2)
    assert checked(view, "windowedOption", "startMutedOn") == (True, True)
    if side is STAFF:
        assert checked(view, "completionAlertsOff", "dockBounceOn") == (True, True)
    assert float(stored(side.other_account, "playbackVolume")) == 0.3  # B 的音量也寫進 B 自己的


def test_a_corrupt_preference_falls_back_to_the_default(engines, services, busy, caplog):
    settings = open_settings()
    settings.setValue(account_key(STAFF_ID, "fullScreen"), "sideways")
    settings.setValue(account_key(STAFF_ID, "playbackVolume"), "11")
    settings.setValue(account_key(STAFF_ID, "startPlaybackMuted"), "true")
    settings.sync()
    _home, window = launch(STAFF, engines(), services, busy)
    assert window.visibility() == FULL_SCREEN and services.ui_prefs.playbackVolume == DEFAULT_VOLUME
    assert services.ui_prefs.startPlaybackMuted  # 好的值照用
    assert f"ignoring stored accountPrefs/{STAFF_ID}/fullScreen" in caplog.text


# ── 播放器：音量、靜音 ───────────────────────────────────────────────────────

def add_service_with_media(db, s3):
    db.add_service(SID, START)
    for kind, key in MEDIA_KEYS.items():
        db.add_media(SID, key.split("/")[0], f"s3://adam-test/{key}")
        s3.put_object(Bucket="adam-test", Key=key, Body=f"bytes of {key}".encode())


def open_detail(window, home):
    detail = qml(window, f'openServiceDetail("{SID}", "{START}")')
    wait_until(lambda: home.mediaModel.state == "ready" and not home.mediaModel.pending)
    return detail


class Player:
    """明細頁的播放器：靜音按鈕、音量滑桿、錄音的 AudioOutput。"""

    def __init__(self, detail):
        self.detail = detail  # 留著參照（T17）
        self.mute = find(detail, "muteButton")
        self.slider = find(detail, "volumeSlider")
        self.output = find(detail, "playbackOutput")

    @property
    def state(self):
        """(靜音, AudioOutput 的音量, 滑桿, 靜音按鈕的文字)"""
        return (self.output.property("muted"), round(self.output.property("volume"), 3),
                round(self.slider.property("value"), 3), self.mute.property("text"))

    def playing(self):
        return qml(self.slider, "[cam0Player, cam1Player, audioPlayer].some("
                                "p => p.playbackState !== MediaPlayer.StoppedState)")

    def icon(self):
        return qml(self.mute, "contentItem.children[0].source.toString()").rsplit("/", 1)[-1]

    def nudge(self, times=1):
        """和按方向鍵一樣：滑桿往上一格，發出 moved()（不是直接設 value，綁定才不會斷）。"""
        for _ in range(times):
            qml(self.slider, "volumeSlider.increase(), volumeSlider.moved()")


@BOTH_SIDES
def test_player_starts_muted_when_asked_and_unmuting_restores_the_saved_volume(side, engines, services, busy, db, s3,
                                                                                no_network_on_gui_thread):
    add_service_with_media(db, s3)
    home, window = launch(side, engines(), services, busy)
    prefs = services.ui_prefs
    prefs.playbackVolume = 0.8
    prefs.startPlaybackMuted = True
    player = Player(open_detail(window, home))
    assert player.state == (True, 0.8, 0.8, "Unmute")  # 靜音，但音量還是存的 0.8（不是 0）
    assert player.icon() == "volume-x_iconNeutral.svg" and qml(player.mute, "Accessible.name") == "Unmute"
    assert qml(player.slider, "Qt.colorEqual(handle.color, Theme.textMuted)")  # 滑桿變淡：現在聽不到
    assert player.mute.height() >= 44 and player.mute.width() >= 44
    assert not player.playing()  # 打開、套用設定都不會開始播放

    qml(player.mute, "click()")  # 取消靜音：回到存的音量
    assert player.state == (False, 0.8, 0.8, "Mute") and player.icon() == "volume-2_iconNeutral.svg"
    assert qml(player.slider, "Qt.colorEqual(handle.color, Theme.actionPrimary)")
    qml(player.mute, "click()")  # 再靜音：音量不動
    assert player.state[:3] == (True, 0.8, 0.8) and prefs.playbackVolume == 0.8
    player.nudge()  # 靜音中調音量：取消靜音，記住新的音量
    assert player.state == (False, 0.9, 0.9, "Mute") and prefs.playbackVolume == 0.9
    qml(player.mute, "click()")
    prefs.flush()
    assert float(stored(side.account, "playbackVolume")) == 0.9  # 靜音從來不寫成音量 0
    assert not player.playing()

    show(window, side.services_page)  # 回到 Services，再打開一次：又照設定先靜音，音量是上次的
    player = Player(open_detail(window, home))
    assert player.state == (True, 0.9, 0.9, "Unmute")

    prefs.startPlaybackMuted = False  # Settings 改成不靜音：下一次打開才套用，也不會開始播放
    assert player.state[0] is True and not player.playing()
    show(window, side.services_page)
    player = Player(open_detail(window, home))
    assert player.state == (False, 0.9, 0.9, "Mute")


@BOTH_SIDES
def test_player_volume_is_remembered_without_writing_on_every_step(side, engines, services, busy, db, s3,
                                                                     monkeypatch, no_network_on_gui_thread):
    add_service_with_media(db, s3)
    home, window = launch(side, engines(), services, busy)
    prefs = services.ui_prefs
    writes = []
    original = prefs._write
    monkeypatch.setattr(prefs, "_write", lambda account_id, name, value: (writes.append((name, value)),
                                                                          original(account_id, name, value)))
    player = Player(open_detail(window, home))
    assert player.state == (False, 0.5, 0.5, "Mute")  # 第一次：0.5、不靜音（和以前一樣）
    qml(player.slider, "volumeSlider.decrease(), volumeSlider.decrease(), volumeSlider.moved()")
    player.nudge(5)  # 拖動：一格一格變
    assert player.state[1] == 0.8 and prefs.playbackVolume == pytest.approx(0.8)
    assert writes == [] and prefs.volume_save_pending  # 還在動：不寫入磁碟
    wait_until(lambda: not prefs.volume_save_pending)
    assert writes == [("playbackVolume", pytest.approx(0.8))]  # 停下來之後寫一次

    again = relaunched(services)  # 重開 app：用上次的音量
    home, window = launch(side, engines(), again, Busy(again.tasks))
    player = Player(open_detail(window, home))
    assert player.state == (False, 0.8, 0.8, "Mute")


# ── 通知：分析完成的提示、Dock 跳動（服務人員端）──────────────────────────────

def set_status(db, service_id, status):
    db.execute("UPDATE tbl_service SET service_status = %s WHERE service_id = %s", (status, service_id))


def titled(services, title):
    return [toast for toast in toasts(services.notifier) if toast["title"] == title]


def poll(home):
    home.watcher.tick()
    wait_until(lambda: not any(home.services.tasks.isBusy(key) for key in (WATCHER_KEY, ANALYSIS_KEY, *HOME_KEYS)))


@pytest.fixture
def dock(monkeypatch):
    """app 在不在前景由測試決定；記下 Dock 的跳動和徽章。"""
    state = SimpleNamespace(state=Qt.ApplicationState.ApplicationActive, badges=[], alerts=[])
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: state.state))
    monkeypatch.setattr(QGuiApplication, "setBadgeNumber", lambda app, number: state.badges.append(number))
    monkeypatch.setattr(QWindow, "alert", lambda window, msec: state.alerts.append(msec))
    return state


def launch_staff_watching(engines, services, db, *service_ids):
    for hour, service_id in enumerate(service_ids, start=9):
        db.add_service(service_id, f"2026-10-31 {hour:02d}:30:00", status=2)
    home = server_home_init.launch(engines(), STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    assert sorted(home.watcher.watched) == sorted(service_ids)
    return home


def test_completion_alerts_off_hides_only_the_completion_toast(engines, services, db, dock, busy,
                                                               no_network_on_gui_thread):
    home = launch_staff_watching(engines, services, db, SID, OTHER)
    services.ui_prefs.showCompletionAlerts = False
    busy.seen.clear()
    set_status(db, SID, 3)  # 完成
    set_status(db, OTHER, 1)  # 失敗（分析服務把狀態改回 1）
    poll(home)
    assert titled(services, "Analysis complete") == []
    [failed] = titled(services, "Analysis failed")  # 失敗照樣跳，附 Retry
    assert (failed["actionLabel"], failed["actionId"]) == ("Retry", f"retry-analyze:{OTHER}")
    assert [row["serviceId"] for row in home.serviceModel.completedModel.rows()] == [SID]  # 結果照樣在分析頁
    assert {ANALYSIS_KEY, *HOME_KEYS} <= set(busy.seen)  # 清單和首頁的卡片照樣重新整理
    assert home.watcher.watched == []
    assert (dock.alerts, dock.badges) == ([], [])  # 在前景：Dock 什麼都不做（和以前一樣）

    services.ui_prefs.showCompletionAlerts = True  # 打開之後的下一筆照常跳
    db.add_service("S20261031110000", "2026-10-31 11:00:00", status=3)
    home.watcher.add("S20261031110000")
    poll(home)
    assert len(titled(services, "Analysis complete")) == 1


def test_completion_alerts_off_in_the_background_still_bounces_and_counts(engines, services, db, dock,
                                                                         no_network_on_gui_thread):
    home = launch_staff_watching(engines, services, db, SID)
    services.ui_prefs.showCompletionAlerts = False
    dock.state = Qt.ApplicationState.ApplicationInactive
    set_status(db, SID, 3)
    poll(home)
    assert titled(services, "Analysis complete") == []
    assert (dock.alerts, dock.badges) == ([0], [1])  # Dock 的跳動和徽章由另一個選項管
    dock.state = Qt.ApplicationState.ApplicationActive
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert dock.badges == [1, 0]


def test_dock_bounce_off_keeps_the_toasts_and_the_badge(engines, services, db, dock, no_network_on_gui_thread):
    home = launch_staff_watching(engines, services, db, SID, OTHER)
    services.ui_prefs.bounceDockIcon = False
    dock.state = Qt.ApplicationState.ApplicationInactive
    set_status(db, SID, 3)
    set_status(db, OTHER, 1)
    poll(home)
    assert len(titled(services, "Analysis complete")) == 1 and len(titled(services, "Analysis failed")) == 1
    assert (dock.alerts, dock.badges) == ([], [1, 2])  # 不跳，但徽章照樣數
    dock.state = Qt.ApplicationState.ApplicationActive
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert dock.badges == [1, 2, 0]

    db.add_service("S20261031110000", "2026-10-31 11:00:00", status=3)  # 在前景：只有 toast（和以前一樣）
    home.watcher.add("S20261031110000")
    poll(home)
    assert len(titled(services, "Analysis complete")) == 2 and (dock.alerts, dock.badges) == ([], [1, 2, 0])


def test_sending_failures_are_shown_with_completion_alerts_off(engines, services, db, analysis_service,
                                                              no_network_on_gui_thread):
    db.add_service(SID, START, status=1)
    home = server_home_init.launch(engines(), STAFF_ID, services)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    services.ui_prefs.showCompletionAlerts = False
    analysis_service.status = 500
    assert home.serviceModel.analyzeService(SID)
    wait_until(lambda: titled(services, "Couldn't send for analysis"))
    [toast] = titled(services, "Couldn't send for analysis")
    assert toast["actionLabel"] == "Retry"


def test_notification_preferences_belong_to_the_account(engines, services, db, dock, busy, no_network_on_gui_thread):
    db.add_account(STAFF_B, "staff-b", "Second staff")
    services.ui_prefs.useAccount(STAFF_ID)
    services.ui_prefs.showCompletionAlerts = False
    services.ui_prefs.bounceDockIcon = False
    db.add_service(SID, START, status=2, account_id=STAFF_B)
    home = server_home_init.launch(engines(), STAFF_B, services)  # 另一位服務人員登入：兩個都是開的
    wait_until(lambda: not busy.now)
    dock.state = Qt.ApplicationState.ApplicationInactive
    set_status(db, SID, 3)
    poll(home)
    assert len(titled(services, "Analysis complete")) == 1 and (dock.alerts, dock.badges) == ([0], [1])
