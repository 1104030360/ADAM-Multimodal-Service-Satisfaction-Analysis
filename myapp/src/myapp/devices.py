"""錄影裝置的身分、探測，以及這台電腦存的裝置設定（new-feature PLAN 13.5 S4、13.8；T41）。

- **鏡頭的身分**：錄影用 OpenCV 的編號（cv2.VideoCapture(n)），編號會隨著插拔改變，不能當成身分。
  OpenCV 4.10 在 macOS 上的第 n 號，就是 AVFoundation 的
  `[[AVCaptureDevice devicesWithMediaType:AVMediaTypeVideo] arrayByAddingObjectsFromArray:
  [AVCaptureDevice devicesWithMediaType:AVMediaTypeMuxed]]` 的第 n 個（cv2.abi3.so 用 strings、nm 查到，T41 第 2 節）。
  所以這裡用同一個呼叫（ctypes 直接呼叫 Objective-C runtime，不加新的套件）拿到每個編號的 uniqueID 和名稱：
  uniqueID 是身分，名稱給人看。列出裝置不需要相機權限，也不會打開鏡頭。
- **麥克風的身分**：PyAudio 只給名稱和 host API（CoreAudio）。身分是「名稱＋host API」；同名的好幾支認不出來，
  要使用者確認。
- **權限**：`[AVCaptureDevice authorizationStatusForMediaType:]`，分得出還沒問、拒絕、允許。
- **存的設定**：每台電腦一份（QSettings，不分帳號；PLAN 13.8）。resolve() 把存的設定對到這次探測到的清單：
  身分對得上才算 ok；清單變了、身分認不出來就要使用者重新確認，不會默默換成另一支鏡頭。

探測要在 GUI 執行緒（T18：OpenCV 在 macOS 上只有主執行緒能跳出相機授權）。其他函式在哪個執行緒都可以。
"""
import ctypes
import ctypes.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

import cv2
import pyaudio
from PySide6.QtCore import QCoreApplication, QSettings

from myapp.ui_prefs import open_settings

logger = logging.getLogger(__name__)

MAX_CAMERAS = 10  # 認不出 AVFoundation 的清單時（不是 macOS），照舊試開 0～9 號

# 權限（authorizationStatusForMediaType: 的值）
ACCESS_UNKNOWN = "unknown"            # 不是 macOS，或讀不到
ACCESS_UNDETERMINED = "undetermined"  # 還沒問過：第一次在 GUI 執行緒打開鏡頭時，macOS 會問
ACCESS_RESTRICTED = "restricted"      # 被管理設定擋住
ACCESS_DENIED = "denied"
ACCESS_GRANTED = "granted"
ACCESS_BLOCKED = (ACCESS_DENIED, ACCESS_RESTRICTED)
_ACCESS_STATUS = {0: ACCESS_UNDETERMINED, 1: ACCESS_RESTRICTED, 2: ACCESS_DENIED, 3: ACCESS_GRANTED}

# 存的設定對到這次的清單（resolve）
SETUP_NONE = "none"        # 沒有存過：錄影頁照舊選前兩支鏡頭和第一支麥克風
SETUP_UNCHECKED = "unchecked"  # 有存過，但還沒探測（還不知道對不對得上）
SETUP_OK = "ok"            # 每個角色的裝置都認得出來
SETUP_CHANGED = "changed"  # 清單變了或認不出身分：要使用者重新確認角色
SETUP_MISSING = "missing"  # 至少一個存的裝置沒接
ROLES = ("customer", "staff", "microphone")

SETTINGS_KEY = "deviceSetup"  # QSettings 的 key：一段 JSON（SavedSetup.to_json）
SETUP_VERSION = 1


def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：和 server_home_init 的 _tr 一樣，context 是 Devices。"""
    text = QCoreApplication.translate("Devices", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


# ---------------------------------------------------------------- macOS：AVFoundation（ctypes）

class _ObjC:
    """最小的 Objective-C 呼叫：objc_msgSend 依每次的參數型別轉成對的函式指標。只在 macOS 上建立。"""

    AVFOUNDATION = "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"

    def __init__(self):
        self.lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        self.avf = ctypes.cdll.LoadLibrary(self.AVFOUNDATION)
        self.lib.objc_getClass.restype = ctypes.c_void_p
        self.lib.objc_getClass.argtypes = [ctypes.c_char_p]
        self.lib.sel_registerName.restype = ctypes.c_void_p
        self.lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self.lib.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        self.lib.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        self._send = ctypes.cast(self.lib.objc_msgSend, ctypes.c_void_p).value

    def send(self, restype, receiver, selector, *args, argtypes=()):
        function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(self._send)
        return function(receiver, self.lib.sel_registerName(selector.encode()), *args)

    def responds(self, receiver, selector):
        """沒有這個方法時呼叫會丟 Objective-C 例外（整個程式結束），所以先問。"""
        return bool(self.send(ctypes.c_bool, receiver, "respondsToSelector:",
                              self.lib.sel_registerName(selector.encode()), argtypes=(ctypes.c_void_p,)))

    def constant(self, name):
        return ctypes.c_void_p.in_dll(self.avf, name).value

    def text(self, ns_string):
        if not ns_string:
            return ""
        raw = self.send(ctypes.c_char_p, ns_string, "UTF8String")
        return raw.decode("utf-8", "replace") if raw else ""

    def capture_device_class(self):
        return self.lib.objc_getClass(b"AVCaptureDevice")


@lru_cache(maxsize=1)
def _objc():
    if sys.platform != "darwin":
        return None
    try:
        return _ObjC()
    except Exception:
        logger.warning("AVFoundation isn't available; cameras are identified by number only", exc_info=True)
        return None


def av_video_devices():
    """AVFoundation 的鏡頭，順序和 OpenCV 的編號一樣：[(uniqueID, 名稱)]。不是 macOS、或讀不到時回傳 None。

    只列出裝置，不打開鏡頭，也不需要相機權限。
    """
    objc = _objc()
    if objc is None:
        return None
    pool = objc.lib.objc_autoreleasePoolPush()
    try:
        device_class = objc.capture_device_class()
        if not device_class or not objc.responds(device_class, "devicesWithMediaType:"):
            return None
        void = (ctypes.c_void_p,)
        video = objc.send(ctypes.c_void_p, device_class, "devicesWithMediaType:", objc.constant("AVMediaTypeVideo"),
                          argtypes=void)
        muxed = objc.send(ctypes.c_void_p, device_class, "devicesWithMediaType:", objc.constant("AVMediaTypeMuxed"),
                          argtypes=void)
        devices = objc.send(ctypes.c_void_p, video, "arrayByAddingObjectsFromArray:", muxed, argtypes=void)
        found = []
        for position in range(objc.send(ctypes.c_ulong, devices, "count")):
            device = objc.send(ctypes.c_void_p, devices, "objectAtIndex:", position, argtypes=(ctypes.c_ulong,))
            found.append((objc.text(objc.send(ctypes.c_void_p, device, "uniqueID")),
                          objc.text(objc.send(ctypes.c_void_p, device, "localizedName"))))
        return found
    except Exception:  # ctypes 的錯誤種類很多；列不出來就當作認不出身分
        logger.warning("listing cameras through AVFoundation failed", exc_info=True)
        return None
    finally:
        objc.lib.objc_autoreleasePoolPop(pool)


def media_access(kind):
    """相機（"video"）或麥克風（"audio"）的權限：ACCESS_*。只讀狀態，不會跳出詢問。"""
    objc = _objc()
    if objc is None:
        return ACCESS_UNKNOWN
    try:
        device_class = objc.capture_device_class()
        if not device_class or not objc.responds(device_class, "authorizationStatusForMediaType:"):
            return ACCESS_UNKNOWN
        media = objc.constant("AVMediaTypeVideo" if kind == "video" else "AVMediaTypeAudio")
        status = objc.send(ctypes.c_long, device_class, "authorizationStatusForMediaType:", media,
                           argtypes=(ctypes.c_void_p,))
    except Exception:
        logger.warning("reading the %s permission failed", kind, exc_info=True)
        return ACCESS_UNKNOWN
    return _ACCESS_STATUS.get(status, ACCESS_UNKNOWN)


# ---------------------------------------------------------------- 探測

@dataclass(frozen=True)
class CameraInfo:
    """探測到、打得開的一支鏡頭。index 是 OpenCV 的編號（錄影用）；uid 是 AVFoundation 的 uniqueID（認不出時是空的）。"""
    index: int
    name: str
    uid: str = ""

    @property
    def key(self):
        """清單比對用的身分：有 uid 用 uid，沒有就是名稱加編號。"""
        return self.uid or f"{self.name}#{self.index}"


@dataclass(frozen=True)
class MicrophoneInfo:
    """有輸入聲道的一支麥克風。index 是 PyAudio 的裝置編號（錄音用）。"""
    index: int
    name: str
    host_api: str = ""


@dataclass(frozen=True)
class CameraScan:
    cameras: tuple = ()        # CameraInfo：打得開的
    unavailable: tuple = ()    # 名稱：列得出來，但這次打不開（別的 app 正在用、沒有回應，或沒有權限）
    identified: bool = False   # 每支鏡頭都認得出身分（uid）
    access: str = ACCESS_UNKNOWN


def probe_cameras():
    """在 GUI 執行緒呼叫（T18）。AVFoundation 列得出幾支就試開幾支，認得出身分；列不出來時照舊試開 0～9 號。

    探測前後各列一次：兩次不一樣（探測中插拔），編號和名稱可能對不上，這次就當作認不出身分。
    """
    listed = av_video_devices()
    count = MAX_CAMERAS if listed is None else min(len(listed), MAX_CAMERAS)
    opened = []
    for index in range(count):
        capture = cv2.VideoCapture(index)
        if capture.isOpened():
            opened.append(index)
        capture.release()  # 打不開的也釋放（以前只釋放打得開的）
    identified = listed is not None and av_video_devices() == listed
    if not identified:
        if listed is not None:
            logger.warning("the camera list changed while probing; cameras can't be identified this time")
        cameras = tuple(CameraInfo(index, _tr("Camera %1", index)) for index in opened)
        return CameraScan(cameras, (), False, media_access("video"))
    cameras, unavailable = [], []
    for index, (uid, name) in enumerate(listed[:count]):
        name = name or _tr("Camera %1", index)
        if index in opened:
            cameras.append(CameraInfo(index, name, uid))
        else:
            unavailable.append(name)
    return CameraScan(tuple(cameras), tuple(unavailable), True, media_access("video"))


def probe_microphones():
    """有輸入聲道的音訊裝置（PyAudio 的第一個 host API，macOS 是 Core Audio）。"""
    audio = pyaudio.PyAudio()
    try:
        host = audio.get_host_api_info_by_index(0)
        host_name = host.get("name", "")
        found = []
        for device_index in range(host.get("deviceCount")):
            info = audio.get_device_info_by_host_api_device_index(0, device_index)
            if info.get("maxInputChannels") > 0:
                found.append(MicrophoneInfo(info.get("index", device_index), info.get("name"), host_name))
        return found
    finally:
        audio.terminate()  # PortAudio 有參照計數，不影響錄影視窗自己的那一份（ENG-06）


def display_names(names):
    """同名的裝置（例如兩支一樣的 USB 鏡頭）加上「 (2)」「 (3)」，下拉選單才分得出來。"""
    seen = {}
    labelled = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        labelled.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return labelled


# ---------------------------------------------------------------- 存的設定（這台電腦一份）

@dataclass(frozen=True)
class SavedDevice:
    name: str
    uid: str = ""        # 鏡頭：AVFoundation 的 uniqueID（存的時候認不出就是空的）
    index: int = -1      # 存的時候的編號：同名的好幾支時用來猜，一定要使用者確認
    host_api: str = ""   # 麥克風：PyAudio 的 host API 名稱
    label: str = ""      # 存的時候下拉選單上的名稱（同名的有「 (2)」）：只給人看，比對用 name

    @property
    def shown(self):
        return self.label or self.name


@dataclass(frozen=True)
class SavedSetup:
    customer: SavedDevice
    staff: SavedDevice
    microphone: SavedDevice
    camera_keys: tuple = ()   # 存的時候整份鏡頭清單的身分（CameraInfo.key）：認不出 uid 時，用它判斷清單有沒有變
    saved_at: str = ""        # ISO 8601，本機時間

    def to_json(self):
        def device(saved):
            return {"name": saved.name, "uid": saved.uid, "index": saved.index, "hostApi": saved.host_api,
                    "label": saved.label}

        return json.dumps({"version": SETUP_VERSION, "customer": device(self.customer), "staff": device(self.staff),
                           "microphone": device(self.microphone), "cameraKeys": list(self.camera_keys),
                           "savedAt": self.saved_at})

    @classmethod
    def from_json(cls, text):
        """壞掉、版本不認得、欄位型別不對都丟 ValueError。"""
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("version") != SETUP_VERSION:
            raise ValueError("unknown device setup version")

        def device(value):
            if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"]:
                raise ValueError("device without a name")
            uid, index, host_api = value.get("uid", ""), value.get("index", -1), value.get("hostApi", "")
            label = value.get("label", "")
            if not all(isinstance(text, str) for text in (uid, host_api, label)) or type(index) is not int:
                raise ValueError("device with a wrong field type")
            return SavedDevice(value["name"], uid, index, host_api, label)

        keys = data.get("cameraKeys", [])
        saved_at = data.get("savedAt", "")
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys) or not isinstance(saved_at, str):
            raise ValueError("camera list with a wrong field type")
        setup = cls(device(data.get("customer")), device(data.get("staff")), device(data.get("microphone")),
                    tuple(keys), saved_at)
        if setup.customer.uid and setup.customer.uid == setup.staff.uid:
            raise ValueError("the same camera for both roles")
        return setup


def saved_setup(cameras, microphones, customer, staff, microphone, labels=((), ()), now=None):
    """依這次的清單和三個位置建立要存的設定。labels 是（鏡頭, 麥克風）下拉選單上的名稱（display_names）。
    位置不合法時丟 ValueError。"""
    if not (0 <= customer < len(cameras) and 0 <= staff < len(cameras) and 0 <= microphone < len(microphones)):
        raise ValueError("a device isn't chosen")
    if customer == staff:
        raise ValueError("the same camera for both roles")
    camera_labels, microphone_labels = labels

    def label(names, position, fallback):
        return names[position] if position < len(names) else fallback

    def camera(position):
        info = cameras[position]
        return SavedDevice(info.name, info.uid, info.index, "", label(camera_labels, position, info.name))

    mic = microphones[microphone]
    return SavedSetup(camera(customer), camera(staff),
                      SavedDevice(mic.name, "", mic.index, mic.host_api, label(microphone_labels, microphone, mic.name)),
                      tuple(info.key for info in cameras),
                      (now or datetime.now()).isoformat(timespec="seconds"))


class DeviceSetupStore:
    """這台電腦（目前的 OS 使用者）的裝置設定：QSettings 的 deviceSetup，不分帳號（PLAN 13.8）。"""

    def __init__(self, settings=None):
        self._settings = settings or open_settings()

    def load(self):
        """回傳 SavedSetup；沒有存過、或存的內容壞掉時回傳 None（壞掉的寫一行 log，當作沒有存過）。"""
        raw = self._settings.value(SETTINGS_KEY, "")
        if not raw:
            return None
        try:
            return SavedSetup.from_json(str(raw))
        except (ValueError, TypeError) as error:
            logger.warning("ignoring the stored device setup: %s", error)
            return None

    def save(self, setup):
        """寫入並馬上存到磁碟；寫不進去時丟 OSError（設定頁顯示「Couldn't save」）。"""
        self._settings.setValue(SETTINGS_KEY, setup.to_json())
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            raise OSError(f"QSettings status {self._settings.status()}")

    def clear(self):
        self._settings.remove(SETTINGS_KEY)
        self._settings.sync()


# ---------------------------------------------------------------- 存的設定對到這次的清單

@dataclass(frozen=True)
class Resolution:
    """存的設定對到這次的清單：每個角色在清單裡的位置（-1：沒有），整體的狀態，哪些角色沒接、哪些要確認。"""
    state: str = SETUP_NONE
    customer: int = -1
    staff: int = -1
    microphone: int = -1
    missing: tuple = ()
    unconfirmed: tuple = ()
    saved: SavedSetup = field(default=None, compare=False)

    def selection(self):
        return {"customer": self.customer, "staff": self.staff, "microphone": self.microphone}


def _match_camera(saved, cameras, identified, known_keys=()):
    """（位置, 結果）：結果是 ok（身分對得上）、changed（猜得到但要確認）、missing（存的那支沒接）。
    known_keys 是存的時候整份清單的身分：那時就接著、而且不是這個角色的鏡頭，不會被猜成這個角色。"""
    if identified and saved.uid:
        for position, camera in enumerate(cameras):
            if camera.uid == saved.uid:
                return position, SETUP_OK
        # uniqueID 不一樣但同名：USB 鏡頭換了插孔，uniqueID 會變。很可能是同一支，但要使用者看過預覽再確認
        named = [position for position, camera in enumerate(cameras)
                 if camera.name == saved.name and camera.key not in known_keys]
        if not named:
            return -1, SETUP_MISSING
        by_index = [position for position in named if cameras[position].index == saved.index]
        return (by_index or named)[0], SETUP_CHANGED
    # 存的時候或這次認不出身分：只能照編號猜，一定要確認（清單完全沒變的情況在 resolve 處理）
    by_index = [position for position, camera in enumerate(cameras) if camera.index == saved.index]
    return (by_index[0] if by_index else -1), SETUP_CHANGED


def _match_microphone(saved, microphones):
    named = [position for position, mic in enumerate(microphones)
             if mic.name == saved.name and (not saved.host_api or not mic.host_api or mic.host_api == saved.host_api)]
    if len(named) == 1:
        return named[0], SETUP_OK
    if not named:
        return -1, SETUP_MISSING
    by_index = [position for position in named if microphones[position].index == saved.index]
    return (by_index or named)[0], SETUP_CHANGED  # 同名的好幾支：認不出是哪一支


def _first_other(count, taken):
    return next((position for position in range(count) if position != taken), -1)


def resolve(saved, cameras, microphones, identified):
    """把存的設定（SavedSetup 或 None）對到這次探測到的清單，回傳 Resolution。

    - 沒存過：SETUP_NONE，位置和錄影頁以前的預設一樣（第 1、2 支鏡頭，第 1 支麥克風）。
    - 每個角色都對得上身分：SETUP_OK。
    - 有存的裝置沒接：SETUP_MISSING；身分認不出、只能猜：SETUP_CHANGED。這兩種都要使用者重新確認，
      沒對上的角色先選一支不同的（不會兩個角色選同一支），但不能當作已經確認。
    """
    camera_count, microphone_count = len(cameras), len(microphones)
    if saved is None:
        return Resolution(SETUP_NONE, 0 if camera_count else -1,
                          (1 if camera_count > 1 else 0) if camera_count else -1, 0 if microphone_count else -1)
    customer, customer_result = _match_camera(saved.customer, cameras, identified, saved.camera_keys)
    staff, staff_result = _match_camera(saved.staff, cameras, identified, saved.camera_keys)
    if (not saved.customer.uid and not saved.staff.uid and not identified
            and tuple(camera.key for camera in cameras) == saved.camera_keys):
        # 存的時候和這次都認不出身分（不是 macOS）：清單一模一樣才算對得上，和以前照編號錄影一樣
        customer_result = SETUP_OK if customer >= 0 else customer_result
        staff_result = SETUP_OK if staff >= 0 else staff_result
    if customer >= 0 and customer == staff:  # 兩個角色猜到同一支：員工鏡頭另外選，要確認
        staff, staff_result = -1, SETUP_CHANGED
    microphone, microphone_result = _match_microphone(saved.microphone, microphones)

    results = {"customer": customer_result, "staff": staff_result, "microphone": microphone_result}
    missing = tuple(role for role in ROLES if results[role] == SETUP_MISSING)
    unconfirmed = tuple(role for role in ROLES if results[role] == SETUP_CHANGED)
    state = SETUP_MISSING if missing else SETUP_CHANGED if unconfirmed else SETUP_OK
    if customer < 0:
        customer = _first_other(camera_count, staff)
    if staff < 0:
        staff = _first_other(camera_count, customer)
    if microphone < 0 and microphone_count:
        microphone = 0
    return Resolution(state, customer, staff, microphone, missing, unconfirmed, saved)
