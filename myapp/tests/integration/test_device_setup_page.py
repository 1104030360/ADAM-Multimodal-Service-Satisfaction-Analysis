"""設定頁的 Devices（設備設定與測試）和錄影頁從存的設定開始（new-feature PLAN 13.5 S4，T41）。

- 服務人員端：Set Up Devices → 兩個角色的預覽、麥克風的音量 → Save；Cancel、離開設定頁都會放開裝置。
- 錄影頁從存的設定開始選，這一次還是可以換；存的設定對不上（拔插過、存的裝置沒接）要先按 Confirm Devices。
- 同一支鏡頭選兩次、沒有權限、裝置不夠、錄影視窗正在用鏡頭，都有看得懂的說明。
- 管理員端沒有 Devices（不因為設定多出錄影的權限）。
"""
import pytest
from PySide6.QtCore import QObject, Signal

from myapp import devices
from myapp.devices import CameraInfo, MicrophoneInfo
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, wait_until

pytestmark = pytest.mark.integration


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


@pytest.fixture
def live(cameras, microphone):
    """一直有畫面、一直有聲音的假裝置：三支鏡頭、兩支麥克風。"""
    cameras.connected = (0, 1, 2)
    cameras.frames = 10 ** 6
    cameras.frame_interval = 0.01
    cameras.size = (320, 240)
    microphone.devices = [("MacBook Pro Microphone", 1), ("Shure MV7", 1)]
    microphone.chunks = 10 ** 6
    microphone.chunk_interval = 0.005
    microphone.sample = 9000
    return cameras


class SpyRecordWidget(QObject):
    """只記下參數的錄影視窗（真的錄影視窗在 test_record_widget.py 測）。"""

    closed = Signal()
    recordingChanged = Signal(bool)
    opened = []

    def __init__(self, cam0_index, cam1_index, mic_index, account_id, *, saver, camera_names, microphone_name):
        super().__init__()
        self.recording = False
        SpyRecordWidget.opened.append((cam0_index, cam1_index, mic_index))

    def show(self):
        pass

    def close(self):
        self.closed.emit()

    def start(self):
        self.recording = True
        self.recordingChanged.emit(True)


@pytest.fixture
def recorder_windows(monkeypatch):
    monkeypatch.setattr(server_home_init, "RecordWidget", SpyRecordWidget)
    SpyRecordWidget.opened = []
    return SpyRecordWidget.opened


def launch_staff(engine, services):
    home = server_home_init.launch(engine, STAFF_ID, services)
    return home, engine.rootObjects()[-1]


def show(window, page):
    """和側邊欄一樣換頁（背景載入），等新頁面好了。"""
    qml(window, f'window.showPage("{page}")')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready")
               and qml(window, "currentPage") == page)
    return qml(window, "mainContentLoader.item")


def find(parent, name):
    found = parent.findChild(QObject, name)
    assert found is not None, name
    return found


def open_device_setup(window):
    page = show(window, "server_setting_button.qml")
    section = find(page, "deviceSettings")
    assert section.isVisible()
    qml(find(section, "setUpDevicesButton"), "click()")
    return page, section


def wait_live(setup):
    wait_until(lambda: (setup.customerPreview, setup.staffPreview, setup.microphoneState) == ("live", "live", "live")
               and setup.customerFrame != "" and setup.staffFrame != "",
               describe=lambda: (setup.status, setup.customerPreview, setup.staffPreview, setup.microphoneState))


def released(cameras, microphone):
    return cameras.live() == [] and microphone.open_streams() == []


def save_setup(cameras, microphones, customer, staff, microphone):
    devices.DeviceSetupStore().save(devices.saved_setup(cameras, microphones, customer, staff, microphone))


# ---------------------------------------------------------------- 預覽、釋放

def test_device_setup_previews_both_roles_and_releases_them_when_leaving(engine, services, qml_warnings, live,
                                                                          microphone):
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    setup = home.deviceFinder.setup
    assert find(section, "deviceSetupPanel").isVisible() and not find(section, "deviceSummary").isVisible()
    wait_live(setup)
    assert live.live() == [0, 1] and len(microphone.open_streams()) == 1
    for tile in ("customerTile", "staffTile"):
        image = find(find(section, tile), "previewImage")
        wait_until(lambda: qml(image, "status === Image.Ready") and qml(image, "implicitWidth") == 320)
        assert qml(image, "visible") and qml(image, "source").toString().startswith("image://devicepreview/")
    meter = find(section, "microphoneLevel")
    wait_until(lambda: qml(meter, "lit") > 0)
    assert find(section, "microphoneMessage").property("text") == "Speak to check the level. Nothing is recorded."
    assert qml(find(find(section, "customerTile"), "preview"), "Accessible.name") == (
        "Live preview from the customer camera")

    show(window, "server_home_button.qml")  # 離開設定頁：鏡頭和麥克風都放開
    wait_until(lambda: released(live, microphone))
    assert not setup.active
    del page, section


def test_cancel_releases_the_devices_and_shows_the_summary(engine, services, qml_warnings, live, microphone):
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    wait_live(home.deviceFinder.setup)
    qml(find(section, "cancelDevicesButton"), "click()")
    assert released(live, microphone)
    assert find(section, "deviceSummary").isVisible() and not find(section, "deviceSetupPanel").isVisible()
    assert devices.DeviceSetupStore().load() is None  # Cancel 不存
    del page


# ---------------------------------------------------------------- 存檔、錄影頁從存的設定開始

def test_saved_devices_are_where_the_record_page_starts_and_can_still_change(engine, services, qml_warnings, live,
                                                                           microphone, recorder_windows):
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    setup = home.deviceFinder.setup
    wait_live(setup)
    qml(find(section, "setupCustomerCamera"), "activated(2)")  # 和使用者在下拉選單選一樣
    qml(find(section, "setupStaffCamera"), "activated(0)")
    qml(find(section, "setupMicrophone"), "activated(1)")
    assert (qml(find(section, "setupCustomerCamera"), "currentIndex"),
            qml(find(section, "setupStaffCamera"), "currentIndex"),
            qml(find(section, "setupMicrophone"), "currentIndex")) == (2, 0, 1)
    wait_live(setup)
    qml(find(section, "saveDevicesButton"), "click()")
    assert released(live, microphone)
    assert find(section, "deviceSummary").isVisible()
    assert find(section, "savedNotice").property("text") == "Saved. The Record page starts with these devices."
    assert [qml(section, f"savedName('{role}')") for role in devices.ROLES] == ["Camera 2", "Camera 0", "Shure MV7"]
    assert find(section, "setUpDevicesButton").property("text") == "Set Up Devices"

    record = show(window, "record_widget.qml")
    wait_until(lambda: qml(record, "formShown"))
    assert [qml(record, f"{name}.currentIndex") for name in ("cam0ComboBox", "cam1ComboBox", "micComboBox")] == [
        2, 0, 1]
    assert not find(record, "setupNotice").isVisible()
    assert qml(find(record, "openRecorderButton"), "enabled")

    qml(record, "cam0ComboBox.currentIndex = 1")  # 這一次換一支：存的設定不變
    qml(find(record, "openRecorderButton"), "click()")
    assert recorder_windows == [(1, 0, 1)]
    assert devices.DeviceSetupStore().load().customer.uid == "fake-camera-2"
    del page, record


def test_a_replugged_camera_must_be_confirmed_before_recording(engine, services, qml_warnings, live,
                                                                recorder_windows):
    live.listed = [("uid-a", "Cam A"), ("uid-b", "Cam B")]
    live.connected = (0, 1)
    save_setup([CameraInfo(0, "Cam A", "uid-a"), CameraInfo(1, "Cam B", "uid-b")],
               [MicrophoneInfo(0, "MacBook Pro Microphone", "Core Audio")], 1, 0, 0)
    live.listed = [("uid-a-other-port", "Cam A"), ("uid-b", "Cam B")]  # Cam A 換了插孔：uniqueID 變了
    home, window = launch_staff(engine, services)
    record = show(window, "record_widget.qml")
    wait_until(lambda: qml(record, "formShown"))
    assert home.deviceFinder.setupState == "changed"
    notice, button = find(record, "setupNotice"), find(record, "openRecorderButton")
    assert notice.isVisible() and not qml(button, "enabled")
    assert find(record, "setupNoticeText").property("text") == (
        "The connected devices changed since the device setup was saved, so the app can't tell which device is "
        "which. Check the choices below, then confirm.")
    assert find(record, "openRecorderHint").property("text") == "Confirm the devices above to open the recorder."
    assert [qml(record, f"{name}.currentIndex") for name in ("cam0ComboBox", "cam1ComboBox")] == [1, 0]  # 先猜，要確認

    qml(find(record, "confirmDevicesButton"), "click()")
    assert not notice.isVisible() and qml(button, "enabled")
    qml(button, "click()")
    assert recorder_windows == [(1, 0, 0)]

    page = show(window, "server_setting_button.qml")  # 設定頁的摘要也說明要重新設定
    assert find(page, "summaryMismatch").property("text").endswith("Set up the devices again.")
    del record, page


def test_a_missing_saved_camera_is_named_and_not_silently_replaced(engine, services, qml_warnings, live):
    cameras = [CameraInfo(0, "Cam A", "uid-a"), CameraInfo(1, "Cam B", "uid-b"), CameraInfo(2, "Cam C", "uid-c")]
    save_setup(cameras, [MicrophoneInfo(0, "MacBook Pro Microphone", "Core Audio")], 0, 2, 0)
    live.listed = [("uid-a", "Cam A"), ("uid-b", "Cam B")]  # Cam C 拔掉了
    live.connected = (0, 1)
    home, window = launch_staff(engine, services)
    record = show(window, "record_widget.qml")
    wait_until(lambda: qml(record, "formShown"))
    assert home.deviceFinder.setupMissing == ["staff"]
    assert find(record, "setupNoticeText").property("text") == (
        "The saved staff camera, Cam C, isn't connected. Check the choices below, then confirm.")
    assert [qml(record, f"{name}.currentIndex") for name in ("cam0ComboBox", "cam1ComboBox")] == [0, 1]
    assert not qml(find(record, "openRecorderButton"), "enabled")

    page, section = open_device_setup(window)  # 設備設定裡也說明
    wait_until(lambda: find(section, "setupMismatch").isVisible())
    assert find(section, "setupMismatch").property("text") == (
        "The saved staff camera, Cam C, isn't connected. Check each preview, then save again.")
    del record, page


# ---------------------------------------------------------------- 錯誤與狀態

def test_the_same_camera_twice_is_an_error_and_cannot_be_saved(engine, services, qml_warnings, live):
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    setup = home.deviceFinder.setup
    wait_live(setup)
    qml(find(section, "setupStaffCamera"), "activated(0)")
    wait_until(lambda: setup.staffPreview == "same")
    error = find(section, "setupSameCameraError")
    assert error.isVisible() and error.property("text") == "This is the customer camera too. Choose a different camera."
    assert qml(find(section, "setupStaffCamera"), "invalid")
    assert not qml(find(section, "saveDevicesButton"), "enabled")
    assert find(find(section, "staffTile"), "previewMessage").property("text") == (
        "Choose a different camera to see the staff preview.")
    assert live.live() == [0]
    del page


def test_camera_access_off_is_explained_on_both_pages(engine, services, qml_warnings, live, microphone):
    live.access = "denied"
    live.listed = [("uid-a", "Cam A"), ("uid-b", "Cam B")]
    live.connected = ()
    microphone.access = "denied"
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    wait_until(lambda: home.deviceFinder.status == "ready")
    access = find(section, "setupAccessOff")
    assert access.isVisible() and find(access, "emptyTitle").property("text") == "Camera access is off"
    assert find(access, "emptyDescription").property("text") == (
        "Turn on Camera for this app in System Settings > Privacy & Security > Camera, then scan again.")
    assert not find(section, "cameraTiles").isVisible()
    assert released(live, microphone)

    record = show(window, "record_widget.qml")
    empty = find(record, "devicesEmpty")
    wait_until(empty.isVisible)
    assert find(empty, "emptyTitle").property("text") == "Camera access is off"
    del page, record


def test_microphone_access_off_is_explained(engine, services, qml_warnings, live, microphone):
    microphone.access = "denied"
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    wait_until(lambda: home.deviceFinder.setup.microphoneState == "denied")
    assert find(section, "microphoneMessage").property("text") == (
        "Microphone access is off. Turn on Microphone for this app in System Settings > Privacy & Security > "
        "Microphone.")
    assert microphone.streams == []
    del page


def test_missing_and_busy_devices_are_explained(engine, services, qml_warnings, live):
    live.listed = [("uid-a", "Cam A"), ("uid-b", "Cam B")]
    live.connected = (0,)  # Cam B 列得出來、打不開：別的 app 正在用
    home, window = launch_staff(engine, services)
    page, section = open_device_setup(window)
    wait_until(lambda: home.deviceFinder.status == "ready")
    not_enough = find(section, "setupNotEnough")
    assert not_enough.isVisible() and find(not_enough, "emptyTitle").property("text") == "Only one camera found"
    busy = find(section, "unavailableCameras")
    assert busy.isVisible() and busy.property("text") == (
        "Not available: Cam B. Another app may be using it, or it isn't responding.")

    assert not find(section, "setupRescanButton").isVisible()  # Scan Again 在說明裡，不重複
    live.connected = (0, 1)  # 那個 app 放開了：Scan Again
    qml(find(not_enough, "emptyAction"), "click()")
    wait_until(lambda: find(section, "cameraTiles").isVisible())
    assert not busy.isVisible() and not not_enough.isVisible() and find(section, "setupRescanButton").isVisible()
    del page


def test_device_setup_is_off_while_the_recorder_uses_the_cameras(engine, services, qml_warnings, live, microphone,
                                                                  recorder_windows):
    home, window = launch_staff(engine, services)
    finder = home.deviceFinder
    record = show(window, "record_widget.qml")
    wait_until(lambda: qml(record, "formShown"))
    qml(find(record, "openRecorderButton"), "click()")
    page = show(window, "server_setting_button.qml")
    section = find(page, "deviceSettings")
    button, reason = find(section, "setUpDevicesButton"), find(section, "blockedReason")
    assert not qml(button, "enabled")
    assert reason.property("text") == "Close the recorder window first. It's using the cameras."
    finder.recorder.start()
    assert reason.property("text") == (
        "Device Setup is off while recording. The recorder is using the cameras and microphone.")
    assert not finder.setup.openSetup() and not finder.setup.active and live.live() == []
    finder.recorder.recording = False
    finder.recorder.close()
    wait_until(lambda: qml(button, "enabled"))
    assert not reason.isVisible()
    del record, page


def test_admins_have_no_devices_section(engine, services, qml_warnings):
    admin_home_init.launch(engine, ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    qml(window, 'mainContentLoader.source = "../../qml/pages/admin_setting_page.qml"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    page = qml(window, "mainContentLoader.item")
    section = find(page, "deviceSettings")
    assert not section.isVisible() and qml(section, "devices") is None
    assert section.height() == 0 or not section.isVisible()
    del page
