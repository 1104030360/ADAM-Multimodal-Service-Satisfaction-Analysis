"""ui_prefs.py：減少動態效果的設定（ENG-20、DS-43），以及每個帳號自己的視窗、播放、通知偏好（new-feature S1、S2，T39）。
設定檔在每個測試的暫存資料夾（tests/conftest.py）。"""
import time

import pytest
from PySide6.QtCore import SIGNAL, QCoreApplication, QSettings
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlContext, QQmlEngine, QQmlExpression

from myapp.ui_prefs import (ACCOUNT_PREFS, DEFAULT_VOLUME, MOTION_MODE_KEY, VOLUME_SAVE_DELAY_MS, UiPrefs, account_key,
                            open_settings)

ALICE = "00000000-0000-0000-0000-00000000000a"
BOB = "00000000-0000-0000-0000-00000000000b"
DEFAULTS = {"fullScreen": True, "playbackVolume": DEFAULT_VOLUME, "startPlaybackMuted": False,
            "showCompletionAlerts": True, "bounceDockIcon": True}


def test_settings_never_touch_the_real_preferences(tmp_path):
    settings = open_settings()
    assert settings.format() == QSettings.Format.IniFormat
    assert settings.fileName().startswith(str(tmp_path))


def test_defaults_follow_the_system_and_tests_turn_animations_off():
    prefs = UiPrefs()
    assert prefs.motionMode == 0
    assert prefs.animationScale == 0.0  # tests/conftest.py 設了 ADAM_ANIMATION_SCALE=0


def test_animation_scale_is_1_without_the_environment_variable(monkeypatch):
    monkeypatch.delenv("ADAM_ANIMATION_SCALE")
    assert UiPrefs().animationScale == 1.0


def test_environment_variable_sets_the_scale(monkeypatch):
    monkeypatch.setenv("ADAM_ANIMATION_SCALE", "2.5")
    assert UiPrefs().animationScale == 2.5


@pytest.mark.parametrize("raw", ["fast", "-1", "nan", "inf"])
def test_bad_environment_variable_is_ignored(monkeypatch, caplog, raw):
    monkeypatch.setenv("ADAM_ANIMATION_SCALE", raw)
    assert UiPrefs().animationScale == 1.0
    assert "ignoring ADAM_ANIMATION_SCALE" in caplog.text


def test_motion_mode_is_saved_and_notifies_once():
    prefs, changes = UiPrefs(), []
    prefs.motionModeChanged.connect(lambda: changes.append(prefs.motionMode))
    prefs.motionMode = 1
    prefs.motionMode = 1  # 沒變：不通知
    assert changes == [1]
    assert UiPrefs().motionMode == 1  # 重開 app 還在


def test_invalid_motion_mode_is_rejected():
    prefs = UiPrefs()
    with pytest.raises(ValueError):
        prefs.motionMode = 3
    assert prefs.motionMode == 0


@pytest.mark.parametrize("stored", ["abc", 7, -1])
def test_bad_stored_motion_mode_falls_back_to_system(caplog, stored):
    settings = open_settings()
    settings.setValue(MOTION_MODE_KEY, stored)
    settings.sync()
    assert UiPrefs().motionMode == 0
    assert "ignoring stored ui/motionMode" in caplog.text


def test_animation_scale_can_be_changed_at_runtime():
    prefs, changes = UiPrefs(), []
    prefs.animationScaleChanged.connect(lambda: changes.append(prefs.animationScale))
    prefs.animationScale = 0.5
    prefs.animationScale = 0.5
    assert changes == [0.5]
    for bad in (-1.0, float("nan")):
        with pytest.raises(ValueError):
            prefs.animationScale = bad


def test_qml_reads_and_writes_the_preferences():
    prefs = UiPrefs()
    engine = QQmlEngine()
    context = QQmlContext(engine.rootContext())
    context.setContextProperty("uiPrefs", prefs)
    expression = QQmlExpression(context, None, "uiPrefs.motionMode = 2, uiPrefs.animationScale")
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    assert value == 0.0 and prefs.motionMode == 2


# ── 每個帳號自己的偏好（S1、S2，PLAN 13.8）───────────────────────────────────

def values(prefs):
    return {name: getattr(prefs, name) for name in ACCOUNT_PREFS}


def stored(account_id, name):
    """磁碟上（另開一個 QSettings 讀）存的值；沒有存過是 None。"""
    return open_settings().value(account_key(account_id, name))


def pump_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "等太久"
        QCoreApplication.processEvents()
        time.sleep(0.005)


def test_account_preferences_start_with_todays_behaviour():
    prefs = UiPrefs()
    assert (prefs.account_id, values(prefs)) == ("", DEFAULTS)  # 還沒登入
    prefs.useAccount(ALICE)
    assert (prefs.account_id, values(prefs)) == (ALICE, DEFAULTS)  # 第一次用：全螢幕、音量 0.5、不靜音、通知都開
    assert all(stored(ALICE, name) is None for name in ACCOUNT_PREFS)  # 只讀不寫


def test_account_preferences_are_kept_by_account_id_across_a_relaunch():
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    prefs.startPlaybackMuted = True
    prefs.showCompletionAlerts = False
    prefs.bounceDockIcon = False
    prefs.playbackVolume = 0.8
    prefs.flush()
    assert stored(ALICE, "startPlaybackMuted") in (True, "true")
    relaunched = UiPrefs()  # 重開 app
    relaunched.useAccount(ALICE)
    assert values(relaunched) == DEFAULTS | {"playbackVolume": 0.8, "startPlaybackMuted": True,
                                             "showCompletionAlerts": False, "bounceDockIcon": False}
    keys = open_settings().allKeys()
    assert keys and all(key.startswith(f"accountPrefs/{ALICE}/") for key in keys)  # key 是 account_id，不是登入帳號


def test_switching_accounts_never_carries_values_over_and_notifies_only_what_changed():
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    prefs.startPlaybackMuted = True
    prefs.playbackVolume = 0.2
    changes = []
    for name in ACCOUNT_PREFS:
        getattr(prefs, f"{name}Changed").connect(lambda name=name: changes.append(name))
    prefs.accountChanged.connect(lambda: changes.append("account"))

    prefs.useAccount(BOB)  # 登出、換 Bob 登入
    assert values(prefs) == DEFAULTS
    assert changes == ["account", "playbackVolume", "startPlaybackMuted"]
    assert stored(ALICE, "playbackVolume") is not None and stored(BOB, "playbackVolume") is None  # Alice 的音量先寫進她自己的
    prefs.bounceDockIcon = False

    changes.clear()
    prefs.useAccount(ALICE)  # 再換回 Alice
    assert values(prefs) == DEFAULTS | {"playbackVolume": 0.2, "startPlaybackMuted": True}
    assert set(changes) == {"account", "playbackVolume", "startPlaybackMuted", "bounceDockIcon"}
    changes.clear()
    prefs.useAccount(ALICE)  # 同一個帳號：什麼都沒變
    assert changes == []

    prefs.useAccount("")  # 沒有帳號：回到預設
    assert values(prefs) == DEFAULTS


def test_changes_without_an_account_stay_in_memory():
    prefs = UiPrefs()
    prefs.startPlaybackMuted = True
    prefs.playbackVolume = 0.9
    prefs.flush()
    assert prefs.startPlaybackMuted and prefs.playbackVolume == 0.9
    assert open_settings().allKeys() == []


@pytest.mark.parametrize("name, raw", [
    ("fullScreen", "sometimes"), ("fullScreen", 7), ("startPlaybackMuted", "yes"), ("showCompletionAlerts", 2.5),
    ("bounceDockIcon", ""), ("playbackVolume", "loud"), ("playbackVolume", "nan"), ("playbackVolume", "1.5"),
    ("playbackVolume", -0.1), ("playbackVolume", True),
])
def test_a_corrupt_stored_value_falls_back_to_the_default_with_a_warning(caplog, name, raw):
    settings = open_settings()
    settings.setValue(account_key(ALICE, name), raw)
    settings.setValue(account_key(ALICE, "startPlaybackMuted" if name != "startPlaybackMuted" else "bounceDockIcon"),
                      "true" if name != "startPlaybackMuted" else "false")  # 同一個帳號其他好的值照用
    settings.sync()
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    assert getattr(prefs, name) == DEFAULTS[name]
    assert f"ignoring stored accountPrefs/{ALICE}/{name}" in caplog.text
    if name != "startPlaybackMuted":
        assert prefs.startPlaybackMuted is True
    else:
        assert prefs.bounceDockIcon is False


@pytest.mark.parametrize("raw, expected", [("false", False), ("TRUE", True), ("0", False), (1, True), (False, False)])
def test_booleans_read_back_from_either_settings_format(raw, expected):
    settings = open_settings()
    settings.setValue(account_key(ALICE, "showCompletionAlerts"), raw)
    settings.sync()
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    assert prefs.showCompletionAlerts is expected


def test_volume_writes_wait_until_the_slider_stops():
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    writes = []
    original = prefs._write
    prefs._write = lambda account_id, name, value: (writes.append((account_id, name, value)),
                                                    original(account_id, name, value))
    notified = []
    prefs.playbackVolumeChanged.connect(lambda: notified.append(prefs.playbackVolume))
    for step in range(1, 21):  # 拖動滑桿：20 格
        prefs.playbackVolume = step / 20
    assert notified == [step / 20 for step in range(1, 21)]  # 播放器馬上跟著變
    assert writes == [] and prefs.volume_save_pending and stored(ALICE, "playbackVolume") is None
    started = time.monotonic()
    pump_until(lambda: writes)
    assert time.monotonic() - started >= VOLUME_SAVE_DELAY_MS / 1000 * 0.8
    assert writes == [(ALICE, "playbackVolume", 1.0)] and not prefs.volume_save_pending  # 只寫一次，最後的值
    assert float(stored(ALICE, "playbackVolume")) == 1.0
    prefs.playbackVolume = 1.0  # 沒變：不寫
    assert not prefs.volume_save_pending


def test_a_pending_volume_is_written_when_the_app_quits():
    app = QCoreApplication.instance()
    before = app.receivers(SIGNAL("aboutToQuit()"))
    prefs = UiPrefs()
    assert app.receivers(SIGNAL("aboutToQuit()")) == before + 1  # flush 接在 aboutToQuit 上
    prefs.useAccount(ALICE)
    prefs.playbackVolume = 0.35
    prefs.flush()  # 不在測試裡真的發 aboutToQuit：別的物件也接了它（例如 TaskRunner 的 shutdown）
    assert not prefs.volume_save_pending and float(stored(ALICE, "playbackVolume")) == 0.35


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_volume_is_rejected(bad):
    prefs = UiPrefs()
    with pytest.raises(ValueError):
        prefs.playbackVolume = bad
    assert prefs.playbackVolume == DEFAULT_VOLUME


def test_mute_and_volume_are_separate():
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    prefs.playbackVolume = 0.7
    prefs.startPlaybackMuted = True
    prefs.flush()
    assert prefs.playbackVolume == 0.7 and float(stored(ALICE, "playbackVolume")) == 0.7  # 靜音不會變成音量 0
    prefs.startPlaybackMuted = False
    assert prefs.playbackVolume == 0.7


def test_qml_reads_and_writes_the_account_preferences():
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    engine = QQmlEngine()
    context = QQmlContext(engine.rootContext())
    context.setContextProperty("uiPrefs", prefs)
    expression = QQmlExpression(context, None, "uiPrefs.startPlaybackMuted = true, uiPrefs.playbackVolume = 0.25, "
                                               "uiPrefs.bounceDockIcon = false, "
                                               "uiPrefs.fullScreen && uiPrefs.showCompletionAlerts")
    value, _ = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    assert value is True
    assert (prefs.startPlaybackMuted, prefs.playbackVolume, prefs.bounceDockIcon) == (True, 0.25, False)


# ── 主視窗的模式（S1）─────────────────────────────────────────────────────────

@pytest.fixture
def windows():
    made = []
    yield made
    for window in made:
        window.destroy()


def new_window(windows):
    window = QWindow()
    windows.append(window)
    return window


def test_window_opens_full_screen_the_first_time_and_remembers_the_last_mode(windows):
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    window = new_window(windows)
    prefs.restoreWindow(window)
    assert window.visibility() == QWindow.Visibility.FullScreen
    window.showNormal()  # 設定頁的 Windowed，或 macOS 的綠色按鈕
    pump_until(lambda: not prefs.fullScreen)
    assert stored(ALICE, "fullScreen") in (False, "false")
    window.showMinimized()  # 最小化不算一種模式
    QCoreApplication.processEvents()
    window.hide()  # 關掉視窗也不算
    QCoreApplication.processEvents()
    assert not prefs.fullScreen

    relaunched = UiPrefs()  # 重開 app，同一個帳號登入
    relaunched.useAccount(ALICE)
    again = new_window(windows)
    relaunched.restoreWindow(again)
    assert again.visibility() == QWindow.Visibility.Windowed
    again.showFullScreen()
    pump_until(lambda: relaunched.fullScreen)
    assert stored(ALICE, "fullScreen") in (True, "true")


def test_an_old_window_never_writes_into_the_next_account(windows):
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    alice_window = new_window(windows)
    prefs.restoreWindow(alice_window)
    prefs.useAccount(BOB)  # Alice 登出、Bob 登入
    bob_window = new_window(windows)
    prefs.restoreWindow(bob_window)
    alice_window.showNormal()  # 舊的視窗在關掉的途中改了模式
    for _ in range(5):
        QCoreApplication.processEvents()
    assert prefs.fullScreen and stored(BOB, "fullScreen") is None and stored(ALICE, "fullScreen") is None
    assert bob_window.visibility() == QWindow.Visibility.FullScreen


def test_a_change_seen_after_the_window_is_gone_is_ignored(windows):
    prefs = UiPrefs()
    prefs.useAccount(ALICE)
    window = new_window(windows)
    prefs.restoreWindow(window)
    window.showNormal()
    window.hide()  # 同一輪就關掉：到下一輪讀的時候已經不在畫面上
    for _ in range(5):
        QCoreApplication.processEvents()
    assert prefs.fullScreen and stored(ALICE, "fullScreen") is None
