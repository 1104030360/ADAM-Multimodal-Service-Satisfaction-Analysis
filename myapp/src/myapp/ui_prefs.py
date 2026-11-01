"""介面偏好。context property 名稱是 `uiPrefs`，main.py 在登入前就註冊，登入視窗和兩個外殼共用同一份。

這台電腦共用的（不分帳號）：
- motionMode（ENG-20、DS-43）：0 跟隨系統、1 減少、2 完整。存在 QSettings("ADAM", "myapp") 的 "ui/motionMode"，
  重開 app 還在。外殼用 Binding 把它寫進 QML 的 Theme 單例；Theme 依它算出所有動畫時間（ENG-18）。
- animationScale：動畫時間的倍數，預設 1，不保存。環境變數 ADAM_ANIMATION_SCALE 優先（測試設 0，動畫全部瞬間完成）。

每個帳號自己的（new-feature PLAN 13.2 S1、13.3 S2、13.8；T39）：
- 存在 "accountPrefs/<account_id>/<名稱>"。用不會變的 account_id 當 key，不用登入帳號（S6 可以改）；只存在這台電腦。
- 外殼的 launch() 呼叫 useAccount(account_id)：重新讀這個帳號的每一個值，沒有存過的用預設，
  所以同一次執行換了帳號（登出再登入），也不會帶到上一個帳號的值。還沒登入（account_id 是空字串）時只改記憶體、不寫入。
- 存的值壞了（例如手動改過設定檔）：用預設值，記一則 warning；下次改的時候蓋掉。

| 名稱 | 預設 | 用在 |
|---|---|---|
| fullScreen | True | 主視窗的模式。restoreWindow(window) 套用，之後視窗模式一變就記住（設定頁的選項、macOS 的綠色按鈕都算） |
| playbackVolume | 0.5 | 服務明細的播放器音量（0～1）。拖動時只改記憶體，停 500 ms 才寫入磁碟；換帳號、結束 app 時先寫入 |
| startPlaybackMuted | False | 每次打開服務明細時先靜音。靜音和音量分開存：靜音不會寫成音量 0，取消靜音就是存的音量 |
| showCompletionAlerts | True | 分析完成的 toast（ServiceModel.analysis_finished）。失敗、上傳錯誤的通知不受影響 |
| bounceDockIcon | True | app 不在前景時 Dock 圖示跳動（DockAttention）。徽章照舊更新 |
"""
import logging
import math
import os
from urllib.parse import quote

import shiboken6
from PySide6.QtCore import Property, QCoreApplication, QObject, QSettings, QTimer, Signal, Slot
from PySide6.QtGui import QWindow

logger = logging.getLogger(__name__)

MOTION_FOLLOW_SYSTEM, MOTION_REDUCE, MOTION_FULL = 0, 1, 2
MOTION_MODES = (MOTION_FOLLOW_SYSTEM, MOTION_REDUCE, MOTION_FULL)
MOTION_MODE_KEY = "ui/motionMode"
SCALE_ENV = "ADAM_ANIMATION_SCALE"

ACCOUNT_GROUP = "accountPrefs"
DEFAULT_VOLUME = 0.5
VOLUME_SAVE_DELAY_MS = 500  # 音量停止變動多久之後才寫入磁碟（拖動滑桿時不要每一格都寫）
# 會記住的視窗模式；最小化、隱藏（關閉視窗）不算
_REMEMBERED_VISIBILITY = (QWindow.Visibility.Windowed, QWindow.Visibility.Maximized, QWindow.Visibility.FullScreen)


def open_settings():
    """和 QSettings("ADAM", "myapp") 存在同一個地方（macOS 的偏好設定）。

    寫成 defaultFormat()，是讓測試可以改成 INI 格式、放到暫存資料夾（tests/conftest.py），不碰使用者真的設定。
    """
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "ADAM", "myapp")


def account_key(account_id, name):
    """帳號偏好的 key。account_id 是 UUID；萬一有「/」之類的字元也不會變成別的群組。"""
    return f"{ACCOUNT_GROUP}/{quote(account_id, safe='')}/{name}"


def _scale_from_env():
    raw = os.environ.get(SCALE_ENV)
    if raw is None:
        return 1.0
    try:
        scale = float(raw)
    except ValueError:
        scale = -1.0
    if not math.isfinite(scale) or scale < 0:
        logger.warning("ignoring %s=%r: expected a number >= 0", SCALE_ENV, raw)
        return 1.0
    return scale


def _parse_bool(value):
    """QSettings 讀回來的布林：macOS 的偏好設定是 bool，INI 檔是 "true"／"false"。其他的都算壞掉。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
        return value.strip().lower() in ("true", "1")
    raise ValueError(f"not a boolean: {value!r}")


def _parse_volume(value):
    if isinstance(value, bool):  # float(True) 是 1.0，但存的是布林就是壞掉了
        raise ValueError(f"not a volume: {value!r}")
    volume = float(value)
    if not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError(f"not a volume: {value!r}")
    return volume


# 名稱 → (預設值, 解析存的值)。名稱也是 QML 的屬性名稱
ACCOUNT_PREFS = {
    "fullScreen": (True, _parse_bool),
    "playbackVolume": (DEFAULT_VOLUME, _parse_volume),
    "startPlaybackMuted": (False, _parse_bool),
    "showCompletionAlerts": (True, _parse_bool),
    "bounceDockIcon": (True, _parse_bool),
}


class UiPrefs(QObject):
    motionModeChanged = Signal()
    animationScaleChanged = Signal()
    accountChanged = Signal()
    fullScreenChanged = Signal()
    playbackVolumeChanged = Signal()
    startPlaybackMutedChanged = Signal()
    showCompletionAlertsChanged = Signal()
    bounceDockIconChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = open_settings()
        self._motion_mode = self._stored_motion_mode()
        self._animation_scale = _scale_from_env()
        self._account_id = ""
        self._values = {name: default for name, (default, _) in ACCOUNT_PREFS.items()}
        self._pending_volume = None  # (account_id, 音量)：還沒寫入磁碟的音量
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(VOLUME_SAVE_DELAY_MS)
        self._volume_timer.timeout.connect(self.flush)
        app = QCoreApplication.instance()
        if app is not None:  # 結束 app 之前，還在等的音量先寫進去
            app.aboutToQuit.connect(self.flush)

    # ------------------------------------------------------------ 減少動態效果（這台電腦共用）

    def _stored_motion_mode(self):
        value = self._settings.value(MOTION_MODE_KEY, MOTION_FOLLOW_SYSTEM)
        try:
            mode = int(value)
        except (TypeError, ValueError):
            mode = -1
        if mode not in MOTION_MODES:
            logger.warning("ignoring stored %s=%r", MOTION_MODE_KEY, value)
            return MOTION_FOLLOW_SYSTEM
        return mode

    @Property(int, notify=motionModeChanged)
    def motionMode(self):
        return self._motion_mode

    @motionMode.setter
    def motionMode(self, mode):
        if mode not in MOTION_MODES:
            raise ValueError(f"motionMode must be one of {MOTION_MODES}, not {mode!r}")
        if mode == self._motion_mode:
            return
        self._motion_mode = mode
        self._settings.setValue(MOTION_MODE_KEY, mode)
        self._settings.sync()
        self.motionModeChanged.emit()

    @Property(float, notify=animationScaleChanged)
    def animationScale(self):
        return self._animation_scale

    @animationScale.setter
    def animationScale(self, scale):
        if not math.isfinite(scale) or scale < 0:
            raise ValueError(f"animationScale must be a number >= 0, not {scale!r}")
        if scale == self._animation_scale:
            return
        self._animation_scale = float(scale)
        self.animationScaleChanged.emit()

    # ------------------------------------------------------------ 每個帳號自己的（S1、S2）

    @property
    def account_id(self):
        return self._account_id

    @Slot(str)
    def useAccount(self, account_id):
        """登入後（外殼的 launch()）：換成 account_id 的偏好。空字串是沒有帳號（值回到預設，不寫入）。

        上一個帳號還沒寫入的音量先寫進它自己的 key；每一個值都重新讀，有變的才通知。
        """
        self.flush()
        account_id = account_id or ""
        changed_account = account_id != self._account_id
        self._account_id = account_id
        values = self._load(account_id)
        changed = [name for name, value in values.items() if value != self._values[name]]
        self._values = values
        if changed_account:
            self.accountChanged.emit()
        for name in changed:
            getattr(self, f"{name}Changed").emit()

    def _load(self, account_id):
        values = {}
        for name, (default, parse) in ACCOUNT_PREFS.items():
            raw = self._settings.value(account_key(account_id, name)) if account_id else None
            if raw is None:
                values[name] = default
                continue
            try:
                values[name] = parse(raw)
            except (TypeError, ValueError):
                logger.warning("ignoring stored %s=%r, using %r", account_key(account_id, name), raw, default)
                values[name] = default
        return values

    def _set(self, name, value):
        if value == self._values[name]:
            return
        self._values[name] = value
        if name == "playbackVolume":  # 拖動滑桿：等停下來才寫
            self._pending_volume = (self._account_id, value)
            self._volume_timer.start()
        else:
            self._write(self._account_id, name, value)
        getattr(self, f"{name}Changed").emit()

    def _write(self, account_id, name, value):
        if not account_id:  # 還沒登入：只改記憶體
            return
        self._settings.setValue(account_key(account_id, name), value)
        self._settings.sync()

    @Slot()
    def flush(self):
        """把還在等的音量寫進磁碟（計時到了、換帳號、結束 app）。"""
        self._volume_timer.stop()
        pending, self._pending_volume = self._pending_volume, None
        if pending is not None:
            self._write(pending[0], "playbackVolume", pending[1])

    @property
    def volume_save_pending(self):
        return self._pending_volume is not None

    @Property(bool, notify=fullScreenChanged)
    def fullScreen(self):
        """主視窗上次是不是全螢幕（restoreWindow() 記住的）。設定頁的選項直接看視窗，不讀這個。"""
        return self._values["fullScreen"]

    @Property(float, notify=playbackVolumeChanged)
    def playbackVolume(self):
        return self._values["playbackVolume"]

    @playbackVolume.setter
    def playbackVolume(self, volume):
        if isinstance(volume, bool) or not math.isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError(f"playbackVolume must be a number from 0 to 1, not {volume!r}")
        self._set("playbackVolume", float(volume))

    @Property(bool, notify=startPlaybackMutedChanged)
    def startPlaybackMuted(self):
        return self._values["startPlaybackMuted"]

    @startPlaybackMuted.setter
    def startPlaybackMuted(self, muted):
        self._set("startPlaybackMuted", bool(muted))

    @Property(bool, notify=showCompletionAlertsChanged)
    def showCompletionAlerts(self):
        return self._values["showCompletionAlerts"]

    @showCompletionAlerts.setter
    def showCompletionAlerts(self, show):
        self._set("showCompletionAlerts", bool(show))

    @Property(bool, notify=bounceDockIconChanged)
    def bounceDockIcon(self):
        return self._values["bounceDockIcon"]

    @bounceDockIcon.setter
    def bounceDockIcon(self, bounce):
        self._set("bounceDockIcon", bool(bounce))

    # ------------------------------------------------------------ 主視窗的模式（S1）

    def restoreWindow(self, window):
        """launch() 載入外殼之後呼叫：照這個帳號上次的模式打開主視窗（第一次是全螢幕），之後模式一變就記住。

        記的是這次登入的帳號；登出之後舊視窗的變化（例如關掉時）不會寫到下一個帳號。
        """
        if self.fullScreen:
            window.showFullScreen()
        else:
            window.showNormal()
        account_id = self._account_id
        window.visibilityChanged.connect(lambda visibility: self._window_changed(account_id, window))

    def _window_changed(self, account_id, window):
        if shiboken6.isValid(self):  # 測試裡 UiPrefs 可能比視窗先被刪掉
            QTimer.singleShot(0, self, lambda: self._remember_window(account_id, window))

    def _remember_window(self, account_id, window):
        """視窗模式變了之後（下一輪事件）：還開著的話記住它現在是不是全螢幕。

        晚一輪才讀，是讓關閉視窗途中的變化（先離開全螢幕、再隱藏）不被當成使用者選的模式。
        """
        if account_id != self._account_id or not shiboken6.isValid(window) or not window.isVisible():
            return
        visibility = window.visibility()
        if visibility not in _REMEMBERED_VISIBILITY:
            return
        self._set("fullScreen", visibility == QWindow.Visibility.FullScreen)
