"""設定頁的 Devices：設備設定與測試（new-feature PLAN 13.5 S4；T41）。QML 讀 `deviceFinder.setup`（DeviceSetup）。

流程：按 Set Up Devices → 重新探測 → 選顧客鏡頭、員工鏡頭、麥克風 → 兩個角色的預覽和麥克風音量 → Save 存成
這台電腦的設定（myapp/devices.py 的 DeviceSetupStore）→ 錄影頁從這組設定開始選（DeviceFinder.startSelection）。

- **只在打開時用裝置**：按 Set Up Devices 才打開鏡頭和麥克風；Save、Cancel、離開設定頁（QML 的
  Component.onDestruction）、外殼關掉（ServerHome.stop）都會釋放。測試不建立服務、不上傳、不寫檔。
- **錄影時不打開**：錄影視窗開著（它的預覽正在用兩支鏡頭）、錄影中、錄好的檔案還在寫（finishing）時，
  blockedReason 說明原因，設定頁的按鈕停用；打開中變成這樣（例如從錄影視窗開始錄影）就馬上關掉。
- **預覽用 OpenCV，不用 Qt Multimedia**（T41 第 3 節）：和錄影視窗走同一條路（同一個編號、同一個 cv2.VideoCapture），
  預覽看到的就是錄影會錄到的；macOS 的相機權限也照 T18（還沒決定時在 GUI 執行緒打開，macOS 才會問）。
  每支鏡頭一條執行緒讀畫面，縮成最多 640×480、鏡像、轉成 QImage（QImage 可以在背景執行緒建立），每秒最多
  PREVIEW_FPS 張；GUI 執行緒每 POLL_MS 看一次有沒有新的畫面，有就換 Image 的網址（image://devicepreview/…）。
  鏡頭權限已經允許時，連打開也在那條執行緒做（和錄影一樣），GUI 執行緒不會卡住。
- **麥克風**：一條執行緒用和錄影一樣的格式（Recording 的 16-bit、單聲道、44.1 kHz）讀資料，只算峰值，資料不留。
- 裝置的執行緒不用 TaskRunner 的 lane：lane 給有結束的工作用，預覽會一直跑到關掉（和 Recording 的兩條執行緒一樣）。
"""
import logging
import threading
import time
from contextlib import suppress
from itertools import count

import cv2
import numpy as np
import pyaudio
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlEngine
from PySide6.QtQuick import QQuickImageProvider

from myapp import devices
from myapp.init.record_widget import NO_PICTURE_AFTER_S, Recording, level_from_peak, peak_of

logger = logging.getLogger(__name__)

PROVIDER_ID = "devicepreview"     # QML：image://devicepreview/<customer|staff>/<序號>
PREVIEW_SIZE = (640, 480)         # 預覽最大的尺寸：設定頁的預覽框約 340 寬，Retina 上剛好
PREVIEW_FPS = 12                  # 預覽每秒最多幾張：看得出動作就好，CPU 和材質上傳都省一半以上
POLL_MS = 80                      # GUI 執行緒多久看一次新的畫面、狀態和音量
STOP_WAIT_S = 0.5                 # 關掉時最多等執行緒釋放裝置多久（讀一張畫面約 33 ms）
CAMERA_ROLES = ("customer", "staff")

# 預覽（customerPreview、staffPreview）和麥克風（microphoneState）的狀態，QML 依這些字顯示說明
OFF, STARTING, LIVE, NO_PICTURE, NO_SIGNAL, UNAVAILABLE, DENIED, SAME = (
    "off", "starting", "live", "noPicture", "noSignal", "unavailable", "denied", "same")
# blockedReason：為什麼現在不能打開設備設定（DeviceFinder.devicesInUse）
IN_USE_RECORDING, IN_USE_RECORDER, IN_USE_FINISHING = "recording", "recorder", "finishing"


# ---------------------------------------------------------------- 預覽的畫面

class FrameStore:
    """每個角色最新的一張預覽（QImage）。鏡頭的執行緒寫、image provider 讀，所以要鎖。

    owner 是寫入的 CameraFeed：換了鏡頭之後，舊的執行緒收尾時只會清掉自己的畫面，不會清掉新的。
    序號整個 app 一直往上加，同一個角色換了鏡頭之後網址也不會重複（QML 的 Image 才會重新讀）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frames = {}  # 角色 → (owner, 序號, QImage)
        self._serials = count(1)

    def put(self, role, owner, image):
        with self._lock:
            self._frames[role] = (owner, next(self._serials), image)

    def serial(self, role, owner):
        """這個 owner 最新一張的序號；沒有（還沒有畫面、或已經是別人的）回傳 0。"""
        with self._lock:
            entry = self._frames.get(role)
            return entry[1] if entry and entry[0] is owner else 0

    def image(self, role):
        with self._lock:
            entry = self._frames.get(role)
            return entry[2] if entry else None

    def clear(self, role, owner=None):
        with self._lock:
            entry = self._frames.get(role)
            if entry and (owner is None or entry[0] is owner):
                del self._frames[role]


FRAMES = FrameStore()  # 整個 app 一份：image provider 註冊在 engine 上，比外殼活得久（登出再登入還是同一個）


class PreviewImageProvider(QQuickImageProvider):
    """image://devicepreview/<角色>/<序號>：回傳那個角色最新的一張。序號只是讓網址每次不同。"""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, image_id, size, requested_size):
        image = FRAMES.image(image_id.split("/", 1)[0])
        if image is None:  # 剛關掉：給一張透明的，不要讓 QML 發警告
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(0)
        return image


def ensure_preview_provider(engine):
    """在這個 engine 註冊 image://devicepreview（只註冊一次；engine 接手 provider 的壽命）。"""
    if engine is not None and engine.imageProvider(PROVIDER_ID) is None:
        engine.addImageProvider(PROVIDER_ID, PreviewImageProvider())


def preview_image(frame):
    """BGR 的一張畫面 → 縮到 PREVIEW_SIZE 以內（保持比例）、左右翻轉（像照鏡子，和錄影視窗一樣）、RGB 的 QImage。"""
    height, width = frame.shape[:2]
    scale = min(PREVIEW_SIZE[0] / width, PREVIEW_SIZE[1] / height, 1.0)
    if scale < 1.0:
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB))
    return QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()  # copy：脫離 numpy


class CameraFeed:
    """一支鏡頭的預覽。執行緒只寫自己的欄位，GUI 執行緒用計時器讀 state()（和 Recording 一樣，不用 signal）。

    open_here：鏡頭權限還沒決定（或不知道）時是 True，在 GUI 執行緒打開，macOS 才會跳出詢問（T18）；
    已經允許時在執行緒裡打開，GUI 執行緒不必等 AVFoundation 啟動。
    """

    def __init__(self, role, camera_index, *, open_here=False, clock=time.monotonic):
        self.role = role
        self.camera_index = camera_index
        self._open_here = open_here
        self._clock = clock
        self._stop = threading.Event()
        self._thread = None
        self._capture = None
        self.opened = None          # None：還不知道
        self.opened_at = None
        self.last_frame_at = None
        self.error = None
        self.released = False

    def start(self):
        if self._open_here:
            capture = cv2.VideoCapture(self.camera_index)
            if not capture.isOpened():
                capture.release()
                self.opened, self.released = False, True
                return
            self._capture = capture
        self._thread = threading.Thread(target=self._run, name=f"device-preview-{self.role}", daemon=True)
        self._thread.start()

    def _run(self):
        capture = self._capture
        try:
            if capture is None:
                capture = cv2.VideoCapture(self.camera_index)
            if not capture.isOpened():
                self.opened = False
                return
            # 和錄影一樣要 640×480：預覽順便確認鏡頭給得出錄影要的格式
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, Recording.FRAME_SIZE[0])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, Recording.FRAME_SIZE[1])
            self.opened_at = self._clock()
            self.opened = True
            shown_at = None
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    self._stop.wait(0.01)  # 讀不到（拔掉、別的 app 搶走）：不要空轉
                    continue
                now = self._clock()
                self.last_frame_at = now
                if shown_at is None or now - shown_at >= 1 / PREVIEW_FPS:
                    shown_at = now
                    FRAMES.put(self.role, self, preview_image(frame))
        except Exception as error:  # 鏡頭中途出錯：預覽框說明，執行緒結束、釋放鏡頭
            logger.exception("the %s camera preview failed", self.role)
            self.error = error
        finally:
            if capture is not None:
                with suppress(Exception):
                    capture.release()
            FRAMES.clear(self.role, self)
            self.released = True

    def stop(self, wait=STOP_WAIT_S):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(wait)

    def state(self, now):
        if self.opened is False or self.error is not None:
            return UNAVAILABLE
        if self.opened is None:
            return STARTING
        last = self.last_frame_at if self.last_frame_at is not None else self.opened_at
        if now - last > NO_PICTURE_AFTER_S:
            return NO_PICTURE
        return LIVE if self.last_frame_at is not None else STARTING


class MicrophoneMeter:
    """麥克風的音量：一條執行緒照錄影的格式讀資料、只記峰值，資料不留（測試不保留聲音）。"""

    def __init__(self, device_index, *, clock=time.monotonic):
        self.device_index = device_index
        self._clock = clock
        self._stop = threading.Event()
        self._thread = None
        self._peak = 0
        self.opened = None
        self.opened_at = None
        self.last_chunk_at = None
        self.error = None
        self.released = False

    def start(self):
        self._thread = threading.Thread(target=self._run, name="device-microphone-test", daemon=True)
        self._thread.start()

    def _run(self):
        audio = stream = None
        try:
            audio = pyaudio.PyAudio()
            stream = audio.open(format=Recording.SAMPLE_FORMAT, channels=Recording.CHANNELS, rate=Recording.RATE,
                                input=True, input_device_index=self.device_index,
                                frames_per_buffer=Recording.CHUNK)
            self.opened_at = self._clock()
            self.opened = True
            while not self._stop.is_set():
                data = stream.read(Recording.CHUNK, exception_on_overflow=False)
                if data:
                    self.last_chunk_at = self._clock()
                    self._peak = max(self._peak, peak_of(data))
                else:
                    self._stop.wait(0.005)
        except Exception as error:  # 打不開（別的 app 獨佔、拔掉、格式不支援）
            logger.warning("the microphone test failed: %r", error)
            self.error = error
            if self.opened is None:
                self.opened = False
        finally:
            if stream is not None:
                with suppress(Exception):
                    stream.stop_stream()
                with suppress(Exception):
                    stream.close()
            if audio is not None:
                with suppress(Exception):
                    audio.terminate()
            self.released = True

    def stop(self, wait=STOP_WAIT_S):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(wait)

    def take_level(self):
        """上次讀之後的最大音量（0～1），讀完歸零。"""
        peak, self._peak = self._peak, 0
        return level_from_peak(peak)

    def state(self, now):
        if self.opened is False or self.error is not None:
            return UNAVAILABLE
        if self.opened is None:
            return STARTING
        last = self.last_chunk_at if self.last_chunk_at is not None else self.opened_at
        return NO_SIGNAL if now - last > NO_PICTURE_AFTER_S else LIVE


# ---------------------------------------------------------------- 設定頁的 Devices

class DeviceSetup(QObject):
    """設定頁 Devices 的資料與動作（deviceFinder.setup）。探測結果、存的設定都在 DeviceFinder，這裡只負責
    「設備設定打開的時候」：選擇、預覽、音量、存檔。DeviceFinder 建立它（parent 是 DeviceFinder）。"""

    activeChanged = Signal()
    statusChanged = Signal()      # 探測的狀態、清單、權限、打不開的鏡頭
    selectionChanged = Signal()   # 三個選擇、同一支鏡頭、能不能存
    previewChanged = Signal()     # 預覽和麥克風的狀態、畫面的網址
    levelChanged = Signal()
    blockedChanged = Signal()
    savedChanged = Signal()       # 存的設定、對到這次清單的結果、剛存好、存不進去

    def __init__(self, finder, store, *, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self._finder = finder
        self._store = store
        self._clock = clock
        self._active = False
        self._selection = {"customer": -1, "staff": -1, "microphone": -1}
        self._touched = False           # 這次打開之後，使用者自己改過選擇
        self._chosen = {}               # 選到的身分（_remember_choice），重新探測之後用來找回同一支
        self._feeds = {role: None for role in CAMERA_ROLES}
        self._meter = None
        self._states = {"customer": OFF, "staff": OFF, "microphone": OFF}
        self._frames = {role: "" for role in CAMERA_ROLES}
        self._level = 0.0
        self._camera_access = devices.ACCESS_UNKNOWN
        self._microphone_access = devices.ACCESS_UNKNOWN
        self._just_saved = False
        self._save_failed = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        finder.statusChanged.connect(self._on_finder_status)
        finder.setupChanged.connect(self._on_setup_changed)
        finder.devicesInUseChanged.connect(self._on_devices_in_use)

    # ------------------------------------------------------------ 屬性

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Property(str, notify=statusChanged)
    def status(self):
        return self._finder.status

    @Property(str, notify=statusChanged)
    def errorTechnical(self):
        return self._finder.errorTechnical

    @Property(list, notify=statusChanged)
    def cameras(self):
        return self._finder.cameras

    @Property(list, notify=statusChanged)
    def microphones(self):
        return self._finder.microphones

    @Property(list, notify=statusChanged)
    def unavailableCameras(self):
        return self._finder.unavailableCameras

    @Property(str, notify=statusChanged)
    def cameraAccess(self):
        return self._camera_access

    @Property(str, notify=statusChanged)
    def microphoneAccess(self):
        return self._microphone_access

    @Property(int, notify=selectionChanged)
    def customerCamera(self):
        return self._selection["customer"]

    @Property(int, notify=selectionChanged)
    def staffCamera(self):
        return self._selection["staff"]

    @Property(int, notify=selectionChanged)
    def microphone(self):
        return self._selection["microphone"]

    @Property(bool, notify=selectionChanged)
    def sameCamera(self):
        return self._selection["customer"] >= 0 and self._selection["customer"] == self._selection["staff"]

    @Property(bool, notify=selectionChanged)
    def canSave(self):
        cameras, microphones = self._finder.camera_infos, self._finder.microphone_infos
        selection = self._selection
        return (self._active and self._finder.status == "ready" and not self.sameCamera
                and 0 <= selection["customer"] < len(cameras) and 0 <= selection["staff"] < len(cameras)
                and 0 <= selection["microphone"] < len(microphones))

    @Property(str, notify=previewChanged)
    def customerPreview(self):
        return self._states["customer"]

    @Property(str, notify=previewChanged)
    def staffPreview(self):
        return self._states["staff"]

    @Property(str, notify=previewChanged)
    def microphoneState(self):
        return self._states["microphone"]

    @Property(str, notify=previewChanged)
    def customerFrame(self):
        return self._frames["customer"]

    @Property(str, notify=previewChanged)
    def staffFrame(self):
        return self._frames["staff"]

    @Property(float, notify=levelChanged)
    def microphoneLevel(self):
        return self._level

    @Property(str, notify=blockedChanged)
    def blockedReason(self):
        return self._finder.devicesInUse

    @Property(str, notify=savedChanged)
    def setupState(self):
        return self._finder.setupState

    @Property(str, notify=savedChanged)
    def savedCustomer(self):
        return self._saved_name("customer")

    @Property(str, notify=savedChanged)
    def savedStaff(self):
        return self._saved_name("staff")

    @Property(str, notify=savedChanged)
    def savedMicrophone(self):
        return self._saved_name("microphone")

    @Property(list, notify=savedChanged)
    def missingRoles(self):
        return list(self._finder.resolution.missing)

    @Property(list, notify=savedChanged)
    def unconfirmedRoles(self):
        return list(self._finder.resolution.unconfirmed)

    @Property(bool, notify=savedChanged)
    def justSaved(self):
        return self._just_saved

    @Property(bool, notify=savedChanged)
    def saveFailed(self):
        return self._save_failed

    def _saved_name(self, role):
        saved = self._finder.saved_setup
        return getattr(saved, role).shown if saved is not None else ""

    # ------------------------------------------------------------ 動作

    @Slot(result=bool)
    @Slot(QObject, result=bool)
    def openSetup(self, view=None):
        """Set Up Devices：重新探測（裝置可能拔插過），探測好之後打開選到的鏡頭和麥克風。
        view 是設定頁的 QML 項目：用它找到 engine，註冊預覽的 image provider。錄影中等情況不打開，回傳 False。"""
        if self._finder.devicesInUse:
            return False
        if view is not None:
            context = QQmlEngine.contextForObject(view)
            ensure_preview_provider(context.engine() if context is not None else None)
        self._touched = False
        self._set_saved_flags(False, False)
        self._set_active(True)
        self._finder.rescan()  # 探測好（statusChanged → ready）才選裝置、打開預覽
        return True

    @Slot()
    def closeSetup(self):
        """Cancel、離開設定頁、外殼關掉：釋放鏡頭和麥克風。沒打開時什麼都不做。"""
        self._release()
        self._set_active(False)

    @Slot()
    def rescan(self):
        """Scan Again：先放開鏡頭和麥克風（探測要逐一打開），探測好再打開選到的。"""
        if not self._active:
            return
        self._release()
        self._finder.rescan()

    @Slot(int)
    def selectCustomerCamera(self, position):
        self._select("customer", position)

    @Slot(int)
    def selectStaffCamera(self, position):
        self._select("staff", position)

    @Slot(int)
    def selectMicrophone(self, position):
        self._select("microphone", position)

    @Slot(result=bool)
    def save(self):
        """Save：存成這台電腦的設定，錄影頁下次從這組開始選。存好就關掉設備設定（釋放裝置）。"""
        if not self.canSave:
            return False
        finder = self._finder
        try:
            setup = devices.saved_setup(finder.camera_infos, finder.microphone_infos, self._selection["customer"],
                                        self._selection["staff"], self._selection["microphone"],
                                        labels=(finder.cameras, finder.microphones))
            self._store.save(setup)
        except (OSError, ValueError):
            logger.exception("saving the device setup failed")
            self._set_saved_flags(False, True)
            return False
        finder.reload_setup()
        self.closeSetup()
        self._set_saved_flags(True, False)
        return True

    # ------------------------------------------------------------ 探測、選擇、預覽

    @Slot()
    def _on_finder_status(self):
        self._refresh_access()
        self.statusChanged.emit()
        self.selectionChanged.emit()  # canSave 跟著探測的狀態
        if not self._active:
            return
        if self._finder.status == "ready":
            self._apply_scan()
        else:  # 探測中（不是這裡發起的）或失敗：不要拿著鏡頭
            self._release()

    @Slot()
    def _on_setup_changed(self):
        self.savedChanged.emit()

    @Slot()
    def _on_devices_in_use(self):
        if self._finder.devicesInUse and self._active:
            self.closeSetup()  # 例如錄影視窗打開了：把鏡頭讓給它
        self.blockedChanged.emit()

    def _refresh_access(self):
        camera, microphone = devices.media_access("video"), devices.media_access("audio")
        if (camera, microphone) != (self._camera_access, self._microphone_access):
            self._camera_access, self._microphone_access = camera, microphone
            self.statusChanged.emit()

    def _apply_scan(self):
        """探測好了：選擇照存的設定（這次打開之後使用者改過的，照他改的身分找回來），打開預覽。"""
        finder = self._finder
        resolution = finder.resolution
        selection = resolution.selection()
        if self._touched:
            selection = {role: self._find_again(role, position) for role, position in self._chosen.items()}
            selection = {role: selection[role] if selection[role] >= 0 else resolution.selection()[role]
                         for role in selection}
        self._selection = selection
        self._remember_choice()
        self.selectionChanged.emit()
        self._sync_devices()
        self._timer.start()

    def _remember_choice(self):
        """記下目前選的是哪一支（身分），重新探測之後清單順序變了也找得回來。"""
        cameras, microphones = self._finder.camera_infos, self._finder.microphone_infos
        self._chosen = {}
        for role in CAMERA_ROLES:
            position = self._selection[role]
            self._chosen[role] = cameras[position].key if 0 <= position < len(cameras) else None
        position = self._selection["microphone"]
        mic = microphones[position] if 0 <= position < len(microphones) else None
        self._chosen["microphone"] = (mic.name, mic.index) if mic is not None else None

    def _find_again(self, role, chosen):
        if chosen is None:
            return -1
        if role == "microphone":
            matches = [position for position, mic in enumerate(self._finder.microphone_infos)
                       if (mic.name, mic.index) == chosen]
        else:
            matches = [position for position, camera in enumerate(self._finder.camera_infos) if camera.key == chosen]
        return matches[0] if matches else -1

    def _select(self, role, position):
        if not self._active or position == self._selection[role]:
            return
        self._selection[role] = position
        self._touched = True
        self._remember_choice()
        self._set_saved_flags(False, False)
        self.selectionChanged.emit()
        self._sync_devices()

    def _sync_devices(self):
        """依目前的選擇開、關鏡頭和麥克風：沒換的不動（不會閃一下），換了的先關舊的再開新的。"""
        cameras = self._finder.camera_infos
        blocked = self._camera_access in devices.ACCESS_BLOCKED
        open_here = self._camera_access != devices.ACCESS_GRANTED  # 還沒決定或不知道：在 GUI 執行緒打開（T18）
        for role in CAMERA_ROLES:
            position = self._selection[role]
            wanted = cameras[position].index if 0 <= position < len(cameras) else None
            if role == "staff" and self.sameCamera:
                wanted = None  # 同一支鏡頭不開兩次：員工的預覽框說明要換一支
            feed = self._feeds[role]
            if feed is not None and (wanted is None or blocked or feed.camera_index != wanted):
                feed.stop()
                self._feeds[role] = feed = None
            if feed is None and wanted is not None and not blocked:
                feed = CameraFeed(role, wanted, open_here=open_here, clock=self._clock)
                self._feeds[role] = feed
                feed.start()
        microphones = self._finder.microphone_infos
        position = self._selection["microphone"]
        wanted = microphones[position].index if 0 <= position < len(microphones) else None
        mic_blocked = self._microphone_access in devices.ACCESS_BLOCKED
        if self._meter is not None and (wanted is None or mic_blocked or self._meter.device_index != wanted):
            self._meter.stop()
            self._meter = None
        if self._meter is None and wanted is not None and not mic_blocked:
            self._meter = MicrophoneMeter(wanted, clock=self._clock)
            self._meter.start()
        self._poll()

    @Slot()
    def _poll(self):
        """每 POLL_MS：新的畫面換網址、預覽和麥克風的狀態、音量。"""
        now = self._clock()
        states, frames = {}, {}
        camera_blocked = self._camera_access in devices.ACCESS_BLOCKED
        for role in CAMERA_ROLES:
            feed = self._feeds[role]
            if not self._active or self._finder.status != "ready":
                states[role] = OFF
            elif role == "staff" and self.sameCamera:
                states[role] = SAME
            elif camera_blocked:
                states[role] = DENIED
            elif feed is None:
                states[role] = OFF
            else:
                states[role] = feed.state(now)
            serial = FRAMES.serial(role, feed) if feed is not None else 0
            frames[role] = f"image://{PROVIDER_ID}/{role}/{serial}" if serial and states[role] != OFF else ""
        meter = self._meter
        if not self._active or self._finder.status != "ready":
            states["microphone"] = OFF
        elif self._microphone_access in devices.ACCESS_BLOCKED:
            states["microphone"] = DENIED
        else:
            states["microphone"] = meter.state(now) if meter is not None else OFF
        level = meter.take_level() if meter is not None and states["microphone"] == LIVE else 0.0
        if states != self._states or frames != self._frames:
            self._states, self._frames = states, frames
            self.previewChanged.emit()
        if level != self._level:
            self._level = level
            self.levelChanged.emit()

    def _release(self):
        """關掉所有預覽和麥克風（各等最多 STOP_WAIT_S，讓裝置真的放開）。"""
        self._timer.stop()
        for role in CAMERA_ROLES:
            if self._feeds[role] is not None:
                self._feeds[role].stop()
                self._feeds[role] = None
        if self._meter is not None:
            self._meter.stop()
            self._meter = None
        self._poll()

    def feeds(self):
        """測試用：目前開著的（CameraFeed…, MicrophoneMeter）。"""
        return [feed for feed in self._feeds.values() if feed is not None], self._meter

    def _set_active(self, active):
        if active != self._active:
            self._active = active
            self.activeChanged.emit()
            self.selectionChanged.emit()  # canSave
            self._poll()

    def _set_saved_flags(self, just_saved, save_failed):
        if (just_saved, save_failed) != (self._just_saved, self._save_failed):
            self._just_saved, self._save_failed = just_saved, save_failed
            self.savedChanged.emit()
