"""裝置的身分、探測、這台電腦存的裝置設定（myapp/devices.py，new-feature S4，T41）。

- 鏡頭用 AVFoundation 的 uniqueID 認（順序就是 OpenCV 的編號）；麥克風用名稱＋host API。
- resolve()：存的設定對到這次的清單。對得上才是 ok；拔插過、認不出身分、存的裝置沒接都要使用者重新確認，
  不會默默換成另一支鏡頭；兩個角色不會猜到同一支。
- 存的設定是這台電腦一份（QSettings），壞掉的當作沒存過。
"""
import json
import sys
import threading
from datetime import datetime

import pytest
import sounddevice
from PySide6.QtCore import QSettings

from myapp import devices
from myapp.devices import CameraInfo, MicrophoneInfo, SavedDevice, SavedSetup

# conftest 把 av_video_devices、media_access 換成假的；這裡留著真的，只用來確認在這台電腦上「讀得到、型別對」
REAL_AV_VIDEO_DEVICES = devices.av_video_devices
REAL_MEDIA_ACCESS = devices.media_access

FACETIME = CameraInfo(0, "FaceTime HD Camera", "uid-facetime")
BRIO_A = CameraInfo(1, "Logitech BRIO", "uid-brio-a")
BRIO_B = CameraInfo(2, "Logitech BRIO", "uid-brio-b")
BUILT_IN_MIC = MicrophoneInfo(2, "MacBook Pro Microphone", "Core Audio")
USB_MIC = MicrophoneInfo(4, "Shure MV7", "Core Audio")


def setup_for(cameras, microphones, customer, staff, microphone):
    return devices.saved_setup(cameras, microphones, customer, staff, microphone,
                               labels=(devices.display_names([c.name for c in cameras]),
                                       devices.display_names([m.name for m in microphones])),
                               now=datetime(2026, 11, 1, 14, 0, 0))


# ---------------------------------------------------------------- 探測

def test_cameras_are_named_and_identified_in_the_opencv_order(cameras):
    cameras.listed = [("uid-facetime", "FaceTime HD Camera"), ("uid-brio-a", "Logitech BRIO")]
    scan = devices.probe_cameras()
    assert scan.cameras == (CameraInfo(0, "FaceTime HD Camera", "uid-facetime"),
                            CameraInfo(1, "Logitech BRIO", "uid-brio-a"))
    assert scan.identified and scan.unavailable == () and scan.access == "granted"


def test_a_listed_camera_that_does_not_open_is_reported_as_unavailable(cameras):
    """別的 app 正在用（或沒有回應）：列得出來、打不開。清單裡沒有它，但說得出名字。"""
    cameras.listed = [("uid-facetime", "FaceTime HD Camera"), ("uid-brio-a", "Logitech BRIO"),
                      ("uid-brio-b", "Logitech BRIO")]
    cameras.connected = (0, 2)
    scan = devices.probe_cameras()
    assert [camera.index for camera in scan.cameras] == [0, 2]
    assert scan.cameras[1].uid == "uid-brio-b"
    assert scan.unavailable == ("Logitech BRIO",)


def test_a_camera_list_that_changes_while_probing_is_not_trusted(cameras, monkeypatch):
    """探測中插拔：編號和名稱可能對不上，這次當作認不出身分（存的設定要重新確認）。"""
    lists = iter([[("uid-a", "Cam A"), ("uid-b", "Cam B")], [("uid-b", "Cam B"), ("uid-a", "Cam A")]])
    monkeypatch.setattr(devices, "av_video_devices", lambda: next(lists))
    scan = devices.probe_cameras()
    assert not scan.identified
    assert scan.cameras == (CameraInfo(0, "Camera 0"), CameraInfo(1, "Camera 1"))


def test_microphones_carry_the_host_api(microphone):
    microphone.devices = [("MacBook Pro Speakers", 0), ("MacBook Pro Microphone", 1), ("Shure MV7", 1)]
    assert devices.probe_microphones() == [MicrophoneInfo(1, "MacBook Pro Microphone", "Core Audio"),
                                           MicrophoneInfo(2, "Shure MV7", "Core Audio")]
    assert microphone.rescans == 1 and microphone.initialized  # 重新列出裝置之後，PortAudio 照樣在執行


def test_microphones_are_not_rescanned_while_one_is_open(microphone):
    """錄音中重新初始化 PortAudio 會關掉開著的串流（最後一次 Pa_Terminate）：這時用上次列的清單（T51）。"""
    with devices.open_microphone(0, "int16", 1, 44100, 1024) as stream:
        assert stream.read(1024)[0]
        microphone.devices.append(("USB 麥克風", 1))
        assert [mic.name for mic in devices.probe_microphones()] == ["MacBook 麥克風", "USB 麥克風"]
        assert microphone.rescans == 0 and microphone.open_streams() == [stream]
    assert microphone.open_streams() == []
    devices.probe_microphones()
    assert microphone.rescans == 1


def test_a_failed_rescan_is_retried_on_the_next_probe(microphone, monkeypatch):
    """上次初始化失敗（PortAudio 沒有在執行）：下一次探測照樣初始化，不會卡在 _terminate() 的錯誤。"""
    microphone.initialized = False
    assert [mic.name for mic in devices.probe_microphones()] == ["MacBook 麥克風"]
    assert microphone.rescans == 1


def test_a_microphone_is_opened_in_the_recording_format_and_closed(microphone):
    with devices.open_microphone(3, "int16", 1, 44100, 1024) as stream:
        assert stream.started
    assert microphone.opened_with == {"device": 3, "channels": 1, "samplerate": 44100, "dtype": "int16",
                                      "blocksize": 1024}
    assert stream.closed and not stream.started


def test_a_microphone_that_cannot_open_leaves_nothing_counted(microphone):
    microphone.broken = True
    with pytest.raises(sounddevice.PortAudioError):
        with devices.open_microphone(0, "int16", 1, 44100, 1024):
            pass  # pragma: no cover  打不開，不會進來
    microphone.broken = False
    devices.probe_microphones()
    assert microphone.rescans == 1  # 失敗的那一次沒有被算成「開著」：照樣重新列出裝置


def test_the_opencv_camera_order_was_checked_for_this_version():
    """devices.py 的對應（OpenCV 的第 n 號＝AVFoundation devicesWithMediaType: 的第 n 個）是看 opencv-python
    4.10.0.84 的 cv2.abi3.so 確認的（T41 第 2 節）。升級 OpenCV 之後這裡會失敗：先重新確認新版的順序
    （strings／nm 找 devicesWithMediaType:、AVCaptureDeviceDiscoverySession），再改這個版本號。"""
    import cv2

    assert cv2.__version__.startswith("4.10."), cv2.__version__


def test_devices_with_the_same_name_get_a_number():
    assert devices.display_names(["Logitech BRIO", "FaceTime", "Logitech BRIO", "Logitech BRIO"]) == [
        "Logitech BRIO", "FaceTime", "Logitech BRIO (2)", "Logitech BRIO (3)"]


def test_avfoundation_is_read_only_on_macos():
    """真的 AVFoundation：只列出裝置、讀權限（不打開鏡頭，也不會跳出詢問）。型別對、值在預期的範圍裡。"""
    listed = REAL_AV_VIDEO_DEVICES()
    access = REAL_MEDIA_ACCESS("video"), REAL_MEDIA_ACCESS("audio")
    if sys.platform != "darwin":
        assert listed is None and access == (devices.ACCESS_UNKNOWN, devices.ACCESS_UNKNOWN)
        return
    assert isinstance(listed, list)
    assert all(isinstance(uid, str) and isinstance(name, str) for uid, name in listed)
    assert set(access) <= {devices.ACCESS_UNDETERMINED, devices.ACCESS_RESTRICTED, devices.ACCESS_DENIED,
                           devices.ACCESS_GRANTED}


def test_avfoundation_is_listed_from_any_thread():
    """列出裝置不限主執行緒（探測在 GUI 執行緒，測試清單只是讀）。"""
    results = []
    thread = threading.Thread(target=lambda: results.append(REAL_AV_VIDEO_DEVICES()))
    thread.start()
    thread.join(10)
    assert results and (results[0] is None) == (sys.platform != "darwin")


def test_without_the_objective_c_runtime_nothing_is_identified(monkeypatch):
    devices._objc.cache_clear()
    monkeypatch.setattr(devices.sys, "platform", "linux")
    try:
        assert REAL_AV_VIDEO_DEVICES() is None
        assert REAL_MEDIA_ACCESS("video") == devices.ACCESS_UNKNOWN
    finally:
        devices._objc.cache_clear()


def test_a_broken_objective_c_runtime_is_logged_not_raised(monkeypatch, caplog):
    devices._objc.cache_clear()
    monkeypatch.setattr(devices, "_ObjC", lambda: (_ for _ in ()).throw(OSError("no libobjc")))
    monkeypatch.setattr(devices.sys, "platform", "darwin")
    try:
        assert REAL_AV_VIDEO_DEVICES() is None
        assert REAL_MEDIA_ACCESS("audio") == devices.ACCESS_UNKNOWN
    finally:
        devices._objc.cache_clear()
    assert "AVFoundation isn't available" in caplog.text


class _BrokenObjC:
    """send() 一律失敗（例如 ctypes 的型別錯）：列不出來、讀不到權限，但不會讓程式結束。"""

    class lib:
        @staticmethod
        def objc_autoreleasePoolPush():
            return 1

        @staticmethod
        def objc_autoreleasePoolPop(pool):
            pass

    def capture_device_class(self):
        return 1

    def responds(self, receiver, selector):
        return True

    def send(self, *args, **kwargs):
        raise ValueError("bad call")

    def constant(self, name):
        return 2


def test_failing_objective_c_calls_are_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(devices, "_objc", lambda: _BrokenObjC())
    assert REAL_AV_VIDEO_DEVICES() is None
    assert REAL_MEDIA_ACCESS("video") == devices.ACCESS_UNKNOWN
    assert "listing cameras through AVFoundation failed" in caplog.text


def test_a_runtime_without_the_old_api_is_not_called(monkeypatch):
    """devicesWithMediaType: 被拿掉的 macOS：先問 respondsToSelector:，不呼叫（呼叫會丟 Objective-C 例外）。"""
    broken = _BrokenObjC()
    broken.responds = lambda receiver, selector: False
    monkeypatch.setattr(devices, "_objc", lambda: broken)
    assert REAL_AV_VIDEO_DEVICES() is None
    assert REAL_MEDIA_ACCESS("video") == devices.ACCESS_UNKNOWN


# ---------------------------------------------------------------- 存的設定

def test_saved_setup_round_trips_through_json():
    setup = setup_for([FACETIME, BRIO_A, BRIO_B], [BUILT_IN_MIC, USB_MIC], 0, 2, 1)
    assert setup.staff == SavedDevice("Logitech BRIO", "uid-brio-b", 2, "", "Logitech BRIO (2)")
    assert setup.staff.shown == "Logitech BRIO (2)"
    assert setup.microphone == SavedDevice("Shure MV7", "", 4, "Core Audio", "Shure MV7")
    assert setup.camera_keys == ("uid-facetime", "uid-brio-a", "uid-brio-b")
    assert setup.saved_at == "2026-11-01T14:00:00"
    assert SavedSetup.from_json(setup.to_json()) == setup


@pytest.mark.parametrize("customer, staff, microphone", [(0, 0, 0), (0, 5, 0), (-1, 1, 0), (0, 1, 3)])
def test_an_invalid_choice_is_not_saved(customer, staff, microphone):
    with pytest.raises(ValueError):
        devices.saved_setup([FACETIME, BRIO_A], [BUILT_IN_MIC], customer, staff, microphone)


def test_the_store_is_per_computer_and_survives_a_restart():
    setup = setup_for([FACETIME, BRIO_A], [BUILT_IN_MIC], 1, 0, 0)
    devices.DeviceSetupStore().save(setup)
    assert devices.DeviceSetupStore().load() == setup  # 新的 QSettings（像重開 app）也讀得到
    devices.DeviceSetupStore().clear()
    assert devices.DeviceSetupStore().load() is None


@pytest.mark.parametrize("stored", [
    "not json",
    json.dumps({"version": 99}),
    json.dumps([1, 2]),
    json.dumps({"version": 1, "customer": {"name": ""}, "staff": {"name": "B"}, "microphone": {"name": "M"}}),
    json.dumps({"version": 1, "customer": {"name": "A", "index": "0"}, "staff": {"name": "B"},
                "microphone": {"name": "M"}}),
    json.dumps({"version": 1, "customer": {"name": "A"}, "staff": {"name": "B"}, "microphone": {"name": "M"},
                "cameraKeys": "abc"}),
    json.dumps({"version": 1, "customer": {"name": "A", "uid": "x"}, "staff": {"name": "B", "uid": "x"},
                "microphone": {"name": "M"}}),
])
def test_a_broken_stored_setup_counts_as_not_saved(stored, caplog):
    settings = devices.open_settings()
    settings.setValue(devices.SETTINGS_KEY, stored)
    settings.sync()
    assert devices.DeviceSetupStore().load() is None
    assert "ignoring the stored device setup" in caplog.text


class _ReadOnlySettings:
    def setValue(self, key, value):
        pass

    def sync(self):
        pass

    def status(self):
        return QSettings.Status.AccessError


def test_a_store_that_cannot_write_raises():
    with pytest.raises(OSError):
        devices.DeviceSetupStore(_ReadOnlySettings()).save(setup_for([FACETIME, BRIO_A], [BUILT_IN_MIC], 0, 1, 0))


# ---------------------------------------------------------------- 存的設定對到這次的清單

def test_nothing_saved_starts_like_before():
    resolution = devices.resolve(None, [FACETIME, BRIO_A], [BUILT_IN_MIC], True)
    assert (resolution.state, resolution.selection()) == ("none", {"customer": 0, "staff": 1, "microphone": 0})
    assert devices.resolve(None, [FACETIME], [], True).selection() == {"customer": 0, "staff": 0, "microphone": -1}
    assert devices.resolve(None, [], [], True).selection() == {"customer": -1, "staff": -1, "microphone": -1}


def test_saved_devices_are_found_by_identity_even_after_the_numbers_change():
    saved = setup_for([FACETIME, BRIO_A, BRIO_B], [BUILT_IN_MIC, USB_MIC], 2, 0, 1)
    # 重開機之後順序變了：BRIO_B 變成 0 號，FaceTime 變成 2 號，麥克風的編號也變了
    now = [CameraInfo(0, "Logitech BRIO", "uid-brio-b"), CameraInfo(1, "Logitech BRIO", "uid-brio-a"),
           CameraInfo(2, "FaceTime HD Camera", "uid-facetime")]
    mics = [MicrophoneInfo(0, "Shure MV7", "Core Audio"), MicrophoneInfo(1, "MacBook Pro Microphone", "Core Audio")]
    resolution = devices.resolve(saved, now, mics, True)
    assert resolution.state == "ok" and resolution.missing == () and resolution.unconfirmed == ()
    assert resolution.selection() == {"customer": 0, "staff": 2, "microphone": 0}


def test_a_replugged_camera_with_a_new_identity_must_be_confirmed():
    """USB 鏡頭換了插孔，uniqueID 會變：同名的那支很可能是同一支，先選它，但要使用者看過再確認。"""
    saved = setup_for([FACETIME, BRIO_A], [BUILT_IN_MIC], 1, 0, 0)
    now = [FACETIME, CameraInfo(1, "Logitech BRIO", "uid-brio-new-port")]
    resolution = devices.resolve(saved, now, [BUILT_IN_MIC], True)
    assert resolution.state == "changed" and resolution.unconfirmed == ("customer",)
    assert resolution.selection() == {"customer": 1, "staff": 0, "microphone": 0}


def test_two_cameras_with_the_same_name_and_new_identities_are_a_guess():
    saved = setup_for([BRIO_A, BRIO_B], [BUILT_IN_MIC], 0, 1, 0)
    now = [CameraInfo(0, "Logitech BRIO", "uid-x"), CameraInfo(1, "Logitech BRIO", "uid-y")]
    resolution = devices.resolve(saved, now, [BUILT_IN_MIC], True)
    assert resolution.state == "changed" and resolution.unconfirmed == ("customer", "staff")
    assert resolution.selection() == {"customer": 1, "staff": 0, "microphone": 0}  # 照存的編號（1、2 號）猜，兩支不同


def test_a_missing_saved_camera_is_reported_and_never_replaced_by_the_other_role():
    """存的員工鏡頭沒接。剩下的同名鏡頭存的時候就接著、而且不是員工鏡頭，不能被猜成它（說它沒接）。"""
    saved = setup_for([FACETIME, BRIO_A, BRIO_B], [BUILT_IN_MIC], 0, 2, 0)
    resolution = devices.resolve(saved, [FACETIME, BRIO_A], [BUILT_IN_MIC], True)
    assert resolution.state == "missing" and resolution.missing == ("staff",)
    assert resolution.selection()["customer"] == 0
    assert resolution.selection()["staff"] == 1  # 先選另一支（不是顧客鏡頭），要使用者確認


def test_a_missing_camera_whose_twin_is_the_other_role_is_still_missing():
    saved = setup_for([BRIO_A, BRIO_B], [BUILT_IN_MIC], 0, 1, 0)
    resolution = devices.resolve(saved, [BRIO_A, FACETIME], [BUILT_IN_MIC], True)
    assert resolution.missing == ("staff",) and resolution.selection() == {"customer": 0, "staff": 1, "microphone": 0}


def test_a_missing_microphone_is_reported():
    saved = setup_for([FACETIME, BRIO_A], [BUILT_IN_MIC, USB_MIC], 0, 1, 1)
    resolution = devices.resolve(saved, [FACETIME, BRIO_A], [BUILT_IN_MIC], True)
    assert (resolution.state, resolution.missing, resolution.microphone) == ("missing", ("microphone",), 0)


def test_two_microphones_with_the_saved_name_must_be_confirmed():
    saved = setup_for([FACETIME, BRIO_A], [USB_MIC], 0, 1, 0)
    mics = [MicrophoneInfo(3, "Shure MV7", "Core Audio"), MicrophoneInfo(4, "Shure MV7", "Core Audio")]
    resolution = devices.resolve(saved, [FACETIME, BRIO_A], mics, True)
    assert (resolution.state, resolution.unconfirmed, resolution.microphone) == ("changed", ("microphone",), 1)


def test_a_microphone_on_another_host_api_is_not_the_saved_one():
    saved = setup_for([FACETIME, BRIO_A], [USB_MIC], 0, 1, 0)
    mics = [MicrophoneInfo(4, "Shure MV7", "JACK")]
    assert devices.resolve(saved, [FACETIME, BRIO_A], mics, True).missing == ("microphone",)


def test_both_roles_never_guess_the_same_camera():
    """兩個角色的 uniqueID 都不見了、剩一支同名的：員工鏡頭不能也選它。"""
    saved = setup_for([BRIO_A, BRIO_B], [BUILT_IN_MIC], 0, 1, 0)
    now = [CameraInfo(0, "Logitech BRIO", "uid-z"), CameraInfo(1, "FaceTime HD Camera", "uid-facetime")]
    resolution = devices.resolve(saved, now, [BUILT_IN_MIC], True)
    assert resolution.state == "changed" and resolution.unconfirmed == ("customer", "staff")
    assert resolution.selection() == {"customer": 0, "staff": 1, "microphone": 0}


def test_unidentified_cameras_are_trusted_only_when_the_list_did_not_change():
    """不是 macOS（認不出 uniqueID）：存的時候和這次的清單一模一樣才算對得上；清單變了就要確認。"""
    plain = [CameraInfo(0, "Camera 0"), CameraInfo(1, "Camera 1")]
    saved = setup_for(plain, [BUILT_IN_MIC], 1, 0, 0)
    assert devices.resolve(saved, plain, [BUILT_IN_MIC], False).state == "ok"
    more = [*plain, CameraInfo(2, "Camera 2")]
    resolution = devices.resolve(saved, more, [BUILT_IN_MIC], False)
    assert resolution.state == "changed" and resolution.selection() == {"customer": 1, "staff": 0, "microphone": 0}


def test_a_setup_saved_with_identities_cannot_be_confirmed_without_them():
    saved = setup_for([FACETIME, BRIO_A], [BUILT_IN_MIC], 1, 0, 0)
    plain = [CameraInfo(0, "Camera 0"), CameraInfo(1, "Camera 1")]
    resolution = devices.resolve(saved, plain, [BUILT_IN_MIC], False)
    assert resolution.state == "changed" and resolution.missing == ()  # 認不出來，不能說它沒接
    assert resolution.selection() == {"customer": 1, "staff": 0, "microphone": 0}


def test_a_setup_saved_without_identities_is_guessed_by_number_once_identified():
    plain = [CameraInfo(0, "Camera 0"), CameraInfo(1, "Camera 1")]
    saved = setup_for(plain, [BUILT_IN_MIC], 1, 0, 0)
    resolution = devices.resolve(saved, [FACETIME, BRIO_A], [BUILT_IN_MIC], True)
    assert resolution.state == "changed" and resolution.selection() == {"customer": 1, "staff": 0, "microphone": 0}
