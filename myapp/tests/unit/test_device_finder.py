"""錄影頁的裝置清單與「開始錄影」按鈕（server_home_init.DeviceFinder）。

F2：建立時不探測（以前登入時就把鏡頭都打開一次）；打開錄影頁才探測，結果留著，可以重新探測（UX-13、ENG-06）。
"""
from types import SimpleNamespace

import cv2
import pytest
from PySide6.QtCore import QCoreApplication

from myapp.init import server_home_init
from myapp.init.server_home_init import DeviceFinder
from tests.integration.helpers import wait_until

STAFF = SimpleNamespace(getID=lambda: "acc-1")  # 只需要 getID() 的假 ServerHome


@pytest.fixture
def camera_opens(cameras, monkeypatch):
    """每打開一次鏡頭（cv2.VideoCapture）就記一筆編號。"""
    opened = []

    def open_camera(index=None):
        opened.append(index)
        return cameras(index)

    monkeypatch.setattr(cv2, "VideoCapture", open_camera)
    return opened


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


def scanned(finder):
    finder.ensureScanned()
    wait_until(lambda: finder.status != "scanning")
    return finder


def test_nothing_is_probed_until_the_record_page_asks(camera_opens, microphone, monkeypatch):
    audio_inits = []
    monkeypatch.setattr(server_home_init.pyaudio, "PyAudio", lambda: audio_inits.append(1) or microphone)
    finder = DeviceFinder(STAFF)
    assert (finder.status, finder.cameras, finder.microphones) == ("idle", [], [])
    assert camera_opens == [] and audio_inits == []  # 登入時不開鏡頭、不初始化音訊（UX-02）

    finder.ensureScanned()
    assert finder.status == "scanning"  # 先讓「Looking for…」畫出來，下一輪事件才探測
    assert camera_opens == []
    wait_until(lambda: finder.status == "ready")
    assert camera_opens == list(range(10)) and audio_inits == [1]

    finder.ensureScanned()  # 再回到錄影頁：用上次的結果
    QCoreApplication.processEvents()
    assert len(camera_opens) == 10 and finder.status == "ready"


def test_camera_list_has_only_cameras_that_open(cameras, microphone):
    cameras.connected = (0, 2)
    assert scanned(DeviceFinder(STAFF)).cameras == ["Camera 0", "Camera 2"]


def test_microphone_list_has_only_input_devices(cameras, microphone):
    microphone.devices = [("MacBook 喇叭", 0), ("USB 麥克風", 1)]
    assert scanned(DeviceFinder(STAFF)).microphones == ["USB 麥克風"]


def test_portaudio_is_released_after_probing(cameras, microphone, monkeypatch):
    released = []
    monkeypatch.setattr(microphone, "terminate", lambda: released.append(True))
    scanned(DeviceFinder(STAFF))
    assert released == [True]  # 以前沒有 terminate（ENG-06）


def test_rescan_picks_up_a_camera_plugged_in_later(cameras, microphone):
    cameras.connected = (0,)
    finder = scanned(DeviceFinder(STAFF))
    changes = []
    finder.camerasChanged.connect(lambda: changes.append(list(finder.cameras)))
    assert finder.cameras == ["Camera 0"]
    cameras.connected = (0, 1)
    finder.rescan()
    finder.rescan()  # 探測中再按一次：不重複探測
    wait_until(lambda: finder.status == "ready")
    assert changes == [["Camera 0", "Camera 1"]]


def test_probe_failure_is_reported_and_can_be_retried(cameras, microphone):
    def broken(index):
        raise OSError(-9999, "Unanticipated host error (fake)")

    microphone.get_host_api_info_by_index = broken  # 蓋過替身的方法（只有這個測試的替身）
    finder = scanned(DeviceFinder(STAFF))
    assert finder.status == "error"
    assert finder.errorTechnical == "OSError: [Errno -9999] Unanticipated host error (fake)"
    del microphone.get_host_api_info_by_index  # 例如重新接上音訊裝置
    finder.rescan()
    wait_until(lambda: finder.status != "scanning")
    assert (finder.status, finder.errorTechnical) == ("ready", "")
    assert finder.microphones == ["MacBook 麥克風"]


def test_start_recording_opens_selected_devices_for_logged_in_staff(cameras, microphone, opened_windows):
    cameras.connected = (1, 2)  # 0 號鏡頭沒接：清單是 Camera 1、Camera 2
    microphone.devices = [("MacBook 喇叭", 0), ("USB 麥克風", 1)]  # 清單只有 USB 麥克風（裝置 1）
    scanned(DeviceFinder(STAFF)).startRecording(0, 1, 0)  # 選清單的第 1、2、1 項
    assert opened_windows == [(1, 2, 1, "acc-1"), "shown"]  # 傳給錄影視窗的是裝置編號
