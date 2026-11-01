"""ui_prefs.py：減少動態效果的設定（ENG-20、DS-43）。設定檔在每個測試的暫存資料夾（tests/conftest.py）。"""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtQml import QQmlContext, QQmlEngine, QQmlExpression

from myapp.ui_prefs import MOTION_MODE_KEY, UiPrefs, open_settings


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
