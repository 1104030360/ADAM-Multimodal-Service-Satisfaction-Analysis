"""介面偏好：減少動態效果（ENG-20、DS-43）。context property 名稱是 `uiPrefs`。

外殼用 Binding 把這兩個值寫進 QML 的 Theme 單例；Theme 依它們算出所有動畫時間（ENG-18）。

- motionMode：0 跟隨系統、1 減少、2 完整。存在 QSettings("ADAM", "myapp") 的 "ui/motionMode"，重開 app 還在。
- animationScale：動畫時間的倍數，預設 1，不保存。環境變數 ADAM_ANIMATION_SCALE 優先（測試設 0，動畫全部瞬間完成）。
"""
import logging
import math
import os

from PySide6.QtCore import Property, QObject, QSettings, Signal

logger = logging.getLogger(__name__)

MOTION_FOLLOW_SYSTEM, MOTION_REDUCE, MOTION_FULL = 0, 1, 2
MOTION_MODES = (MOTION_FOLLOW_SYSTEM, MOTION_REDUCE, MOTION_FULL)
MOTION_MODE_KEY = "ui/motionMode"
SCALE_ENV = "ADAM_ANIMATION_SCALE"


def open_settings():
    """和 QSettings("ADAM", "myapp") 存在同一個地方（macOS 的偏好設定）。

    寫成 defaultFormat()，是讓測試可以改成 INI 格式、放到暫存資料夾（tests/conftest.py），不碰使用者真的設定。
    """
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "ADAM", "myapp")


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


class UiPrefs(QObject):
    motionModeChanged = Signal()
    animationScaleChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = open_settings()
        self._motion_mode = self._stored_motion_mode()
        self._animation_scale = _scale_from_env()

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
