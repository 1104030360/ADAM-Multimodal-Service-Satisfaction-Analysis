"""錄影頁的裝置清單與「開始錄影」按鈕（server_home_init.DeviceFinder）。

F2：建立時不探測（以前登入時就把鏡頭都打開一次）；打開錄影頁才探測，結果留著，可以重新探測（UX-13、ENG-06）。
F3：同時只開一個錄影視窗；錄好之後的存檔（RecordingSaver）屬於這裡；結束前要不要問（uploadCount）、
⌘Q 的 QuitGuard 只在錄影中或存檔中才裝上（UX-09、UX-29）。
T26：登入時 restore_saves() 把這個帳號上次沒存完的錄影放回 RecordingSaver。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import cv2
import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from myapp.init import server_home_init
from myapp.init.record_widget import QuitGuard
from myapp.init.server_home_init import DeviceFinder
from myapp.notifications import Notifier
from myapp.pending_saves import PendingSave, PendingSaveStore
from myapp.tasks import TaskRunner
from tests.integration.helpers import toasts, wait_until

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
def staff():
    """有背景服務的假 ServerHome：錄影要用 services 存檔；存好之後重新整理 serviceModel。"""
    tasks = TaskRunner()
    home = SimpleNamespace(getID=lambda: "acc-1", refreshed=[],
                           services=SimpleNamespace(tasks=tasks, notifier=Notifier(), db=None, ui_prefs=None))
    home.serviceModel = SimpleNamespace(refresh=lambda: home.refreshed.append(True))
    yield home
    tasks.shutdown()


class SpyRecordWidget(QObject):
    """只記下參數的錄影視窗替身（真的錄影視窗在 test_record_widget.py 測）。"""

    closed = Signal()
    recordingChanged = Signal(bool)
    opened = []

    def __init__(self, cam0_index, cam1_index, mic_index, account_id, *, saver, camera_names, microphone_name):
        super().__init__()
        self.recording = False
        self.calls = []
        self.opened.append((cam0_index, cam1_index, mic_index, account_id, camera_names, microphone_name))
        self.saver = saver

    def show(self):
        self.calls.append("show")

    def showNormal(self):
        self.calls.append("showNormal")

    def raise_(self):
        self.calls.append("raise")

    def activateWindow(self):
        self.calls.append("activate")

    def close(self):
        self.calls.append("close")
        self.closed.emit()

    def stop_recording(self):
        self.calls.append("stop")
        self.recording = False
        self.recordingChanged.emit(False)

    def start(self):
        self.recording = True
        self.recordingChanged.emit(True)


@pytest.fixture
def opened_windows(monkeypatch):
    """把 RecordWidget 換成 SpyRecordWidget；回傳每次打開時的參數。"""
    monkeypatch.setattr(server_home_init, "RecordWidget", SpyRecordWidget)
    SpyRecordWidget.opened = []
    return SpyRecordWidget.opened


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


def test_start_recording_opens_selected_devices_for_logged_in_staff(cameras, microphone, opened_windows, staff):
    cameras.connected = (1, 2)  # 0 號鏡頭沒接：清單是 Camera 1、Camera 2
    microphone.devices = [("MacBook 喇叭", 0), ("USB 麥克風", 1)]  # 清單只有 USB 麥克風（裝置 1）
    finder = scanned(DeviceFinder(staff))
    finder.startRecording(0, 1, 0)  # 選清單的第 1、2、1 項
    # 傳給錄影視窗的是裝置編號；名稱顯示在狀態列（DS-34）
    assert opened_windows == [(1, 2, 1, "acc-1", ("Camera 1", "Camera 2"), "USB 麥克風")]
    assert finder.recorder.calls == ["show"] and finder.recorder.saver is finder.saver


def test_only_one_recorder_is_open(cameras, microphone, opened_windows, staff):
    finder = scanned(DeviceFinder(staff))
    finder.startRecording(0, 1, 0)
    first = finder.recorder
    first.start()
    finder.startRecording(1, 0, 0)  # 錄影中又按 Activate：帶到最前面，不開第二個
    assert finder.recorder is first and len(opened_windows) == 1
    assert first.calls == ["show", "showNormal", "raise", "activate"]
    first.stop_recording()
    finder.startRecording(1, 0, 0)  # 沒在錄：換成這次選的裝置
    assert first.calls[-2:] == ["stop", "close"] and len(opened_windows) == 2
    assert finder.recorder is not first and opened_windows[-1][:2] == (1, 0)
    finder.recorder.close()  # 關掉視窗
    assert finder.recorder is None and not finder.recording


class FakeSaver(QObject):
    inFlightChanged = Signal()

    def __init__(self):
        super().__init__()
        self.jobs = []

    def in_flight(self):
        return list(self.jobs)


def test_uploads_in_flight_are_summed_for_the_quit_dialog(staff):
    finder = DeviceFinder(staff)
    assert (finder.uploadCount, finder.uploadServiceId, finder.uploadDone, finder.uploadTotal) == (0, "", 0, 0)
    finder._saver = saver = FakeSaver()
    saver.inFlightChanged.connect(finder._uploads_changed)
    changes = []
    finder.uploadsChanged.connect(lambda: changes.append(finder.uploadCount))
    saver.jobs = [SimpleNamespace(service_id="S20261031093000", done=58.6e6, total=124.0e6)]
    saver.inFlightChanged.emit()
    assert (finder.uploadCount, finder.uploadServiceId, finder.uploadDone, finder.uploadTotal) == (
        1, "S20261031093000", 58.6e6, 124.0e6)
    saver.jobs.append(SimpleNamespace(service_id="S20261031100000", done=0, total=0))  # 檔案還在寫：還不知道大小
    saver.inFlightChanged.emit()
    assert (finder.uploadCount, finder.uploadServiceId, finder.uploadTotal) == (2, "S20261031093000", 0)
    saver.jobs = []  # 都存好了：不再擋 ⌘Q（也不留下登記給之後的測試）
    saver.inFlightChanged.emit()
    assert changes == [1, 2, 0] and not QuitGuard.instance().blocking(finder)


def test_quit_guard_is_installed_only_while_recording_or_saving(cameras, microphone, opened_windows, staff):
    finder = scanned(DeviceFinder(staff))
    guard = QuitGuard.instance()
    requests = []
    finder.quitRequested.connect(lambda: requests.append(True))
    assert not guard.blocking(finder)  # 平常不登記（app 層級的 filter 會看到每一個事件）
    finder.startRecording(0, 1, 0)
    recorder = finder.recorder
    recorder.start()
    assert finder.recording and guard.blocking(finder) and guard.installed
    finder.quit_blocked()  # ⌘Q 被擋下來之後：先停止並存檔，再請外殼問
    assert recorder.calls[-1] == "stop" and requests == [True]
    assert not finder.recording and not guard.blocking(finder)  # 替身沒有真的存檔
    assert finder.stopRecording() is False  # 沒在錄

    finder._saver = saver = FakeSaver()
    saver.jobs = [SimpleNamespace(service_id="S20261031093000", done=0, total=0)]
    finder._uploads_changed()
    assert guard.blocking(finder)
    finder.allowQuit()  # Quit Anyway：之後的 QEvent.Quit 不再攔
    assert not guard.blocking(finder) and not finder._should_block_quit()


def test_saved_recording_refreshes_the_analysis_lists(staff):
    finder = DeviceFinder(staff)
    finder._saved("S20261031093000")
    assert staff.refreshed == [True]
    DeviceFinder(STAFF)._saved("S20261031093000")  # 沒有 serviceModel 的 ServerHome：不做事


def test_login_puts_back_the_staffs_unfinished_saves(staff):
    """launch() 的 start() 呼叫 restore_saves()：這個帳號上次沒存完的錄影放回 RecordingSaver，附 Retry（T26）。"""
    start = datetime(2026, 10, 30, 12, 0, 0)
    store = PendingSaveStore()
    store.put(PendingSave("S20261030121500", "acc-2", ("/x/a.avi", "/x/b.avi", "/x/c.wav"), start, None, "upload"))
    finder = DeviceFinder(staff)
    assert finder.restore_saves() == [] and finder._saver is None  # 只有別人的：照舊等第一次錄影才建立
    store.put(PendingSave("S20261030120000", "acc-1", ("/gone/a.avi", "/gone/b.avi", "/gone/c.wav"), start,
                          start + timedelta(minutes=5), "database", ("s3://b/a.avi", "s3://b/b.avi", "s3://b/c.wav")))
    changes = []
    finder.uploadsChanged.connect(lambda: changes.append(finder.uploadCount))
    [job] = finder.restore_saves()
    assert job.service_id == "S20261030120000" and finder.saver.job("S20261030120000") is job
    assert finder.uploadCount == 0 and changes == [0]  # 等著 Retry，不算「還在存」：結束時不必問
    assert not QuitGuard.instance().blocking(finder)
    assert [toast["actionId"] for toast in toasts(staff.services.notifier)] == ["retry-upload:S20261030120000"]
