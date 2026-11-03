"""設定頁的設備設定與測試（myapp/device_setup.py 的 DeviceSetup，deviceFinder.setup；new-feature S4，T41）。

- 按 Set Up Devices 才打開鏡頭和麥克風，關掉（Cancel、Save、離開、出錯）就釋放。測試不錄、不傳、不留資料。
- 預覽和錄影走同一條路（OpenCV 的同一個編號）；權限還沒決定時在 GUI 執行緒打開（T18），已經允許時在執行緒裡打開。
- 錄影視窗開著、錄影中、檔案還在寫時不打開；打開中遇到就關掉。
- 同一支鏡頭選兩次是錯誤、只開一次；Save 存成這台電腦的設定，錄影頁從這組開始。
"""
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlEngine

from myapp import device_setup, devices
from myapp.device_setup import FRAMES, CameraFeed, FrameStore, PreviewImageProvider
from myapp.init import server_home_init
from myapp.init.record_widget import FINISHING, Recording
from myapp.init.server_home_init import DeviceFinder
from myapp.notifications import Notifier
from myapp.tasks import TaskRunner
from tests.fakes import save_job
from tests.integration.helpers import wait_until

STAFF = SimpleNamespace(getID=lambda: "acc-1")


@pytest.fixture
def live(cameras, microphone):
    """一直有畫面、一直有聲音的假裝置（每張畫面、每段聲音之間稍微等一下，執行緒不空轉）。"""
    cameras.frames = 10 ** 6
    cameras.frame_interval = 0.01
    cameras.size = (320, 240)
    microphone.devices = [("MacBook Pro Microphone", 1), ("Shure MV7", 1)]
    microphone.chunks = 10 ** 6
    microphone.chunk_interval = 0.005
    microphone.sample = 9000
    return cameras


@pytest.fixture
def finder():
    finder = DeviceFinder(STAFF)
    yield finder
    finder.setup.closeSetup()


@pytest.fixture
def fast_problems(monkeypatch):
    """「沒有畫面」「沒有聲音」不必等 1 秒。"""
    monkeypatch.setattr(device_setup, "NO_PICTURE_AFTER_S", 0.15)


def opened(finder, previews=("live", "live"), microphone="live"):
    setup = finder.setup
    assert setup.openSetup()
    wait_until(lambda: (setup.customerPreview, setup.staffPreview, setup.microphoneState) == (*previews, microphone),
               describe=lambda: (finder.status, setup.customerPreview, setup.staffPreview, setup.microphoneState))
    return setup


def released(cameras, microphone):
    return cameras.live() == [] and microphone.open_streams() == []


# ---------------------------------------------------------------- 打開、預覽、釋放

def test_nothing_is_opened_until_device_setup_opens(finder, cameras, microphone):
    setup = finder.setup
    assert not setup.active and setup.status == "idle"
    assert (setup.customerPreview, setup.staffPreview, setup.microphoneState) == ("off", "off", "off")
    assert cameras.opened_on == [] and microphone.streams == []


def test_opening_rescans_then_previews_both_cameras_and_the_microphone(finder, live, microphone):
    setup = finder.setup
    finder.ensureScanned()
    wait_until(lambda: finder.status == "ready")
    probes = len(live.opened_on)
    assert setup.openSetup()
    assert setup.active and finder.status == "scanning"  # 裝置可能拔插過：每次打開都重新探測
    setup = opened(finder)
    assert len(live.opened_on) > probes
    assert (setup.customerCamera, setup.staffCamera, setup.microphone) == (0, 1, 0)
    assert setup.customerFrame.startswith("image://devicepreview/customer/")
    assert setup.staffFrame.startswith("image://devicepreview/staff/")
    wait_until(lambda: setup.microphoneLevel > 0.5)  # 9000／32768 ≈ -11 dBFS
    assert live.live() == [0, 1] and len(microphone.open_streams()) == 1
    # 麥克風照錄影的格式打開：測試順便確認錄影用得了這支麥克風
    assert microphone.opened_with == {"device": 0, "channels": Recording.CHANNELS, "samplerate": Recording.RATE,
                                      "dtype": Recording.DTYPE, "blocksize": Recording.CHUNK}
    image = FRAMES.image("customer")
    assert (image.width(), image.height()) == (320, 240)


def test_closing_releases_the_cameras_and_the_microphone(finder, live, microphone):
    setup = opened(finder)
    stream = microphone.open_streams()[0]
    setup.closeSetup()
    assert released(live, microphone)  # closeSetup 等執行緒放開裝置才回來
    assert stream.closed and not stream.started
    assert not setup.active
    assert (setup.customerPreview, setup.staffPreview, setup.microphoneState) == ("off", "off", "off")
    assert (setup.customerFrame, setup.staffFrame, setup.microphoneLevel) == ("", "", 0.0)
    assert FRAMES.image("customer") is None and FRAMES.image("staff") is None
    setup.closeSetup()  # 已經關了：什麼都不做


def test_cameras_open_on_the_gui_thread_until_access_is_granted(finder, live):
    """權限還沒決定：在 GUI 執行緒打開，macOS 才會問（T18）。已經允許：在預覽的執行緒裡打開，GUI 不等。"""
    live.access = "undetermined"
    setup = opened(finder)
    previews = live.opened_on[-2:]
    assert [thread for _index, thread in previews] == ["MainThread", "MainThread"]
    setup.closeSetup()

    live.access = "granted"
    opened(finder)
    assert sorted(thread for _index, thread in live.opened_on[-2:]) == ["device-preview-customer",
                                                                         "device-preview-staff"]


def test_changing_one_camera_reopens_only_that_preview(finder, live):
    live.connected = (0, 1, 2)
    setup = opened(finder)
    customer = setup.feeds()[0][0]
    setup.selectStaffCamera(2)
    wait_until(lambda: live.live() == [0, 2] and setup.staffPreview == "live")
    feeds, _meter = setup.feeds()
    assert feeds[0] is customer  # 沒換的不重開（不會閃一下）
    assert [feed.camera_index for feed in feeds] == [0, 2]
    assert setup.staffCamera == 2


def test_the_same_camera_twice_is_an_error_and_is_opened_once(finder, live):
    setup = opened(finder)
    setup.selectStaffCamera(0)
    assert setup.sameCamera and not setup.canSave
    wait_until(lambda: live.live() == [0])
    assert setup.staffPreview == "same" and setup.staffFrame == ""
    assert not setup.save()
    setup.selectStaffCamera(1)
    wait_until(lambda: setup.staffPreview == "live")
    assert not setup.sameCamera and setup.canSave


def test_changing_the_microphone_reopens_it(finder, live, microphone):
    setup = opened(finder)
    setup.selectMicrophone(1)
    wait_until(lambda: microphone.opened_with["device"] == 1 and setup.microphoneState == "live")
    assert len(microphone.open_streams()) == 1


def test_choices_are_ignored_while_closed(finder, live):
    setup = finder.setup
    setup.selectCustomerCamera(1)
    setup.rescan()
    assert setup.customerCamera == -1 and finder.status == "idle"


# ---------------------------------------------------------------- 存檔

def test_save_stores_this_computers_setup_and_the_record_page_starts_from_it(finder, live, microphone):
    live.listed = [("uid-facetime", "FaceTime HD Camera"), ("uid-brio-a", "Logitech BRIO"),
                   ("uid-brio-b", "Logitech BRIO")]
    live.connected = (0, 1, 2)
    setup = opened(finder)
    assert finder.cameras == ["FaceTime HD Camera", "Logitech BRIO", "Logitech BRIO (2)"]
    setup.selectCustomerCamera(2)
    setup.selectStaffCamera(0)
    setup.selectMicrophone(1)
    wait_until(lambda: (setup.customerPreview, setup.staffPreview) == ("live", "live"))
    changes = []
    finder.setupChanged.connect(lambda: changes.append(finder.startSelection))
    assert setup.save()
    assert not setup.active and released(live, microphone)  # 存好就關掉、放開裝置
    assert setup.justSaved and not setup.saveFailed
    saved = devices.DeviceSetupStore().load()
    assert (saved.customer.uid, saved.staff.uid, saved.microphone.name) == ("uid-brio-b", "uid-facetime", "Shure MV7")
    assert (setup.savedCustomer, setup.savedStaff, setup.savedMicrophone) == (
        "Logitech BRIO (2)", "FaceTime HD Camera", "Shure MV7")
    assert finder.setupState == "ok"
    assert changes == [{"customer": 2, "staff": 0, "microphone": 1}]
    assert finder.savedSetup == {"customer": "Logitech BRIO (2)", "staff": "FaceTime HD Camera",
                                 "microphone": "Shure MV7"}

    # 錄影頁（另一個 DeviceFinder，像重開 app）：探測之後從存的這組開始
    other = DeviceFinder(STAFF)
    assert other.setupState == "unchecked"
    other.ensureScanned()
    wait_until(lambda: other.status == "ready")
    assert (other.setupState, other.startSelection) == ("ok", {"customer": 2, "staff": 0, "microphone": 1})


def test_a_failed_save_keeps_device_setup_open(finder, live, monkeypatch):
    setup = opened(finder)
    monkeypatch.setattr(devices.DeviceSetupStore, "save", lambda self, setup: (_ for _ in ()).throw(OSError("full")))
    assert not setup.save()
    assert setup.saveFailed and not setup.justSaved and setup.active
    assert live.live() == [0, 1]  # 還開著，可以再按一次
    assert finder.setupState == "none"


def test_reopening_clears_the_saved_message(finder, live):
    setup = opened(finder)
    assert setup.save() and setup.justSaved
    opened(finder)
    assert not setup.justSaved


# ---------------------------------------------------------------- 狀態：沒有權限、打不開、沒有畫面、裝置不夠

def test_camera_access_denied_opens_nothing(finder, live, microphone):
    live.access = "denied"
    live.listed = [("uid-a", "Camera 0"), ("uid-b", "Camera 1")]  # 列得出來（列出裝置不需要權限）
    live.connected = ()  # 沒有權限時 OpenCV 一支都打不開
    setup = finder.setup
    assert setup.openSetup()
    wait_until(lambda: finder.status == "ready")
    assert setup.cameraAccess == "denied" and finder.cameraAccess == "denied"
    assert finder.cameras == [] and finder.unavailableCameras == ["Camera 0", "Camera 1"]
    assert live.live() == [] and not setup.canSave


def test_microphone_access_denied_opens_no_microphone(finder, live, microphone):
    microphone.access = "denied"
    setup = opened(finder, microphone="denied")
    assert setup.microphoneAccess == "denied" and microphone.streams == []
    assert setup.microphoneLevel == 0.0


def test_a_camera_that_another_app_holds_is_reported(finder, live):
    setup = finder.setup
    finder.ensureScanned()
    wait_until(lambda: finder.status == "ready")
    live.connected = (1,)  # 探測之後，0 號被別的 app 拿走了
    setup.openSetup()
    wait_until(lambda: finder.status == "ready")
    # 重新探測：0 號打不開，清單只剩 1 號，說得出是哪一支打不開
    assert finder.cameras == ["Camera 1"] and finder.unavailableCameras == ["Camera 0"]
    live.connected = (0, 1)
    setup.rescan()
    wait_until(lambda: setup.customerPreview == "live")
    live.connected = (1,)  # 預覽打開之前又被拿走
    setup.selectCustomerCamera(1)
    setup.selectCustomerCamera(0)
    wait_until(lambda: setup.customerPreview == "unavailable")


def test_a_camera_that_fails_while_previewing_is_released(finder, live, microphone):
    setup = opened(finder)
    live.broken_read = OSError("the camera was unplugged")
    wait_until(lambda: (setup.customerPreview, setup.staffPreview) == ("unavailable", "unavailable"))
    assert live.live() == []
    assert setup.customerFrame == "" and FRAMES.image("customer") is None


def test_a_camera_without_pictures_says_so(finder, live, fast_problems):
    live.frames = 0
    setup = opened(finder, previews=("noPicture", "noPicture"))
    assert setup.customerFrame == ""


def test_a_broken_microphone_is_reported_and_released(finder, live, microphone):
    microphone.broken = True
    attempts = microphone.attempts
    setup = opened(finder, microphone="unavailable")
    assert microphone.attempts > attempts and microphone.open_streams() == []
    assert setup.microphoneLevel == 0.0


def test_a_silent_microphone_says_so(finder, live, microphone, fast_problems):
    microphone.chunks = 0
    opened(finder, microphone="noSignal")


def test_not_enough_devices_cannot_be_saved(finder, live, microphone):
    live.connected = (0,)
    microphone.devices = [("Speakers", 0)]
    setup = finder.setup
    setup.openSetup()
    wait_until(lambda: finder.status == "ready")
    assert finder.cameras == ["Camera 0"] and finder.microphones == []
    assert not setup.canSave and not setup.save()


def test_a_failed_scan_releases_and_can_be_retried(finder, live, microphone, monkeypatch):
    setup = opened(finder)
    probe, broken = devices.probe_microphones, [True]

    def flaky_probe():  # 不用 monkeypatch.undo()：它會連 conftest 換上的假鏡頭一起換回真的
        if broken[0]:
            raise OSError("PortAudio")
        return probe()

    monkeypatch.setattr(devices, "probe_microphones", flaky_probe)
    setup.rescan()
    wait_until(lambda: finder.status == "error")
    assert released(live, microphone) and setup.active
    assert setup.errorTechnical == "OSError: PortAudio"
    broken[0] = False
    setup.rescan()
    wait_until(lambda: setup.customerPreview == "live")


# ---------------------------------------------------------------- 重新探測

def test_rescanning_keeps_the_chosen_cameras_by_identity(finder, live):
    live.listed = [("uid-a", "Cam A"), ("uid-b", "Cam B"), ("uid-c", "Cam C")]
    live.connected = (0, 1, 2)
    setup = opened(finder)
    setup.selectCustomerCamera(2)
    setup.selectStaffCamera(0)
    live.listed = [("uid-c", "Cam C"), ("uid-a", "Cam A"), ("uid-b", "Cam B")]  # 拔插之後順序變了
    setup.rescan()
    wait_until(lambda: finder.status == "ready" and setup.customerPreview == "live")
    assert finder.cameras == ["Cam C", "Cam A", "Cam B"]
    assert (setup.customerCamera, setup.staffCamera) == (0, 1)  # 還是 Cam C、Cam A


def test_a_rescan_started_elsewhere_releases_the_previews_first(finder, live, microphone):
    setup = opened(finder)
    finder.rescan()  # 例如錄影頁（理論上同時只有一頁）：探測要逐一打開鏡頭
    assert finder.status == "scanning" and released(live, microphone)
    wait_until(lambda: setup.customerPreview == "live")


# ---------------------------------------------------------------- 錄影在用裝置時

class SpyRecordWidget(QObject):
    closed = Signal()
    recordingChanged = Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.recording = False

    def show(self):
        pass

    def close(self):
        self.closed.emit()

    def start(self):
        self.recording = True
        self.recordingChanged.emit(True)

    def stop_recording(self):
        self.recording = False
        self.recordingChanged.emit(False)


@pytest.fixture
def recording_finder(monkeypatch):
    monkeypatch.setattr(server_home_init, "RecordWidget", SpyRecordWidget)
    tasks = TaskRunner()
    home = SimpleNamespace(getID=lambda: "acc-1",
                           services=SimpleNamespace(tasks=tasks, notifier=Notifier(), db=None, ui_prefs=None))
    finder = DeviceFinder(home)
    finder.ensureScanned()
    wait_until(lambda: finder.status == "ready")
    yield finder
    finder.setup.closeSetup()
    tasks.shutdown()


def test_device_setup_stays_closed_while_the_recorder_uses_the_devices(recording_finder):
    finder, setup = recording_finder, recording_finder.setup
    reasons = []
    setup.blockedChanged.connect(lambda: reasons.append(setup.blockedReason))
    finder.startRecording(0, 1, 0)
    assert setup.blockedReason == "recorder" and not setup.openSetup() and not setup.active
    finder.recorder.start()
    assert setup.blockedReason == "recording" and not setup.openSetup()
    finder.recorder.stop_recording()
    finder.recorder.close()
    assert setup.blockedReason == "" and setup.openSetup()
    assert reasons == ["recorder", "recording", "recorder", ""]


def test_opening_the_recorder_closes_an_open_device_setup(recording_finder, live, microphone):
    setup = opened(recording_finder)
    recording_finder.startRecording(0, 1, 0)
    assert not setup.active and released(live, microphone)


def test_a_recording_still_writing_its_files_blocks_device_setup(recording_finder):
    finder, setup = recording_finder, recording_finder.setup
    finishing = save_job("S20261101150000", FINISHING)
    saver = SimpleNamespace(in_flight=lambda: [finishing] if finishing.state == FINISHING else [])
    finder._saver = saver
    finder._uploads_changed()
    assert setup.blockedReason == "finishing" and not setup.openSetup()
    finishing.state = "uploading"  # 檔案寫完、開始上傳：裝置已經放開
    finder._uploads_changed()
    assert setup.blockedReason == ""


def test_the_shell_closing_closes_device_setup(live, microphone, monkeypatch):
    monkeypatch.setattr(server_home_init, "AnalysisWatcher", None)
    home = server_home_init.ServerHome("acc-1")  # 沒有 services：沒有監看
    setup = opened(home.deviceFinder)
    home.stop()
    assert not setup.active and released(live, microphone)


# ---------------------------------------------------------------- 預覽的畫面

def test_preview_frames_are_shrunk_mirrored_and_rgb():
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[:, :640] = (255, 0, 0)  # BGR：左半邊是藍色
    image = device_setup.preview_image(frame)
    assert (image.width(), image.height()) == (640, 360)  # 保持比例，最多 640×480
    assert image.pixelColor(600, 100).blue() == 255  # 鏡像之後藍色在右邊
    assert image.pixelColor(40, 100).blue() == 0


def test_frames_belong_to_the_feed_that_wrote_them():
    store = FrameStore()
    old, new = object(), object()
    image = QImage(4, 3, QImage.Format.Format_RGB888)
    store.put("staff", old, image)
    store.put("staff", new, image)
    assert store.serial("staff", old) == 0 and store.serial("staff", new) > 0
    store.clear("staff", old)  # 舊的執行緒收尾：不會清掉新的畫面
    assert store.image("staff") is image
    store.clear("staff")
    assert store.image("staff") is None


def test_the_preview_provider_returns_the_latest_frame_or_a_blank_one():
    provider = PreviewImageProvider()
    image = QImage(8, 6, QImage.Format.Format_RGB888)
    feed = object()
    FRAMES.put("customer", feed, image)
    try:
        assert provider.requestImage("customer/7", None, None) is image
    finally:
        FRAMES.clear("customer", feed)
    blank = provider.requestImage("customer/8", None, None)
    assert (blank.width(), blank.height()) == (1, 1)


def test_the_preview_provider_is_registered_once_per_engine():
    engine = QQmlEngine()
    device_setup.ensure_preview_provider(engine)
    first = engine.imageProvider(device_setup.PROVIDER_ID)
    device_setup.ensure_preview_provider(engine)
    assert engine.imageProvider(device_setup.PROVIDER_ID) is first
    device_setup.ensure_preview_provider(None)  # 找不到 engine：不做事
    del engine


def test_a_feed_that_cannot_open_on_the_gui_thread_is_released_at_once(cameras):
    cameras.connected = ()
    feed = CameraFeed("customer", 0, open_here=True)
    feed.start()
    assert feed.opened is False and feed.released and feed.state(0.0) == "unavailable"
    feed.stop()
