"""錄影頁的裝置清單與「開始錄影」按鈕（server_home_init.DeviceFinder）。"""
from types import SimpleNamespace

import pytest

from myapp.init import server_home_init
from myapp.init.server_home_init import DeviceFinder

STAFF = SimpleNamespace(getID=lambda: "acc-1")  # 只需要 getID() 的假 ServerHome


@pytest.fixture
def opened_windows(monkeypatch):
    """把 RecordWidget 換成只記下參數的替身（真的錄影視窗在 test_record_widget.py 測）。"""
    opened = []

    class SpyRecordWidget:
        def __init__(self, cam0_index, cam1_index, mic_index, account_id):
            opened.append((cam0_index, cam1_index, mic_index, account_id))

        def show(self):
            opened.append("shown")

    monkeypatch.setattr(server_home_init, "RecordWidget", SpyRecordWidget)
    return opened


def test_camera_list_has_only_cameras_that_open(cameras, microphone):
    cameras.connected = (0, 2)
    assert DeviceFinder(STAFF).cameras == ["Camera 0", "Camera 2"]


def test_microphone_list_has_only_input_devices(cameras, microphone):
    microphone.devices = [("MacBook 喇叭", 0), ("USB 麥克風", 1)]
    assert DeviceFinder(STAFF).microphones == ["USB 麥克風"]


def test_start_recording_opens_selected_devices_for_logged_in_staff(cameras, microphone, opened_windows):
    cameras.connected = (1, 2)  # 0 號鏡頭沒接：清單是 Camera 1、Camera 2
    microphone.devices = [("MacBook 喇叭", 0), ("USB 麥克風", 1)]  # 清單只有 USB 麥克風（裝置 1）
    DeviceFinder(STAFF).startRecording(0, 1, 0)  # 選清單的第 1、2、1 項
    assert opened_windows == [(1, 2, 1, "acc-1"), "shown"]  # 傳給錄影視窗的是裝置編號
