"""錄影視窗（QtWidgets，PLAN D5）與錄好之後的存檔（前端重建 F3，CONTRACTS §4 的 C1）。

- `Recording`：一段錄影。錄音、錄影兩條執行緒和三個檔案（顧客鏡頭 cam0、員工鏡頭 cam1、麥克風）。
  和視窗分開：視窗關掉、或已經在錄下一段時，這一段照樣收尾、上傳（UX-08、UX-09）。
- `RecordingSaver`：停止之後在背景存檔。屬於 app（DeviceFinder 建立，和外殼活得一樣久），不屬於視窗（ENG-03）：
  1. io lane，key `upload:<sid>`：等兩條執行緒把檔案寫完，依序上傳三個檔案；進度是三個檔案加起來的 bytes。
  2. db lane，key `record:save:<sid>`：一個交易寫 sp_insert_service（狀態 1）、兩次 sp_insert_video_storage、
     一次 sp_insert_audio_storage，用 execute("CALL …")，共 6 個往返（以前在 GUI 執行緒新開連線、callproc，20 個）。
  3. 通知用同一個 key 就地更新（DS-21）：進度 → "Saved <sid>"（動作 Analyze，交給送出分析的流程）
     或 "<sid> wasn't saved"（動作 Retry，這裡自己處理）。失敗時本機的檔案留著（UX-10）。
  4. 還沒存完的記在本機（myapp/pending_saves.py，T26）：開始存檔、上傳完、失敗時寫入，存好就刪掉。
     下一次同一個帳號登入時 restore() 放回來，跳一樣的 "<sid> wasn't saved"＋Retry，從停下來的那一步繼續。
- `RecordWidget`：錄影視窗。狀態列照 DS-34；鏡頭、麥克風出問題時在原地說明，不跳對話框（UX-11）；
  錄影中是不閃的紅點、經過時間和音量（UX-12）。停止後馬上可以錄下一段，不必等上傳（DS-34）。
- `QuitGuard`：⌘Q 不會經過 QML 外殼的 onClosing（app 是 QApplication，結束時只問 QWidget 視窗；T20 實測），
  所以錄影中、存檔中時，DeviceFinder 在 app 上裝這個 filter 攔下 QEvent.Quit。

介面文字照 DS-50～52 的英文，經過 _tr()（日後可以加翻譯）。顏色取自 myapp/theme.py，和 qml/components/Theme.qml
是同一組值（QtWidgets 讀不到 QML 的 Theme 單例；tests/unit/test_ui_copy.py 比對兩邊）。
"""
import logging
import math
import os
import re
import threading
import time
import wave
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np
import pyaudio
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSvg import QSvgRenderer

from myapp import storage
from myapp.notifications import DISK_FULL, FILE_NOT_FOUND, NO_PERMISSION, user_message
from myapp.pending_saves import PendingSave, PendingSaveStore
from myapp.theme import RECORD_WINDOW, TOKENS
from myapp.ui_prefs import MOTION_FULL, MOTION_REDUCE

logger = logging.getLogger(__name__)

# TaskRunner 和通知的 key、通知上的動作（CONTRACTS §4）
UPLOAD_KEY = "upload:{}"
SAVE_KEY = "record:save:{}"
ANALYZE_ACTION = "analyze:{}"     # 送出分析的流程（C3）處理
RETRY_PREFIX = "retry-upload:"    # 這裡處理

FILE_COUNT = 3
POLL_MS = 250                 # 錄影中更新計時、音量、裝置狀態：每秒最多 4 次（DS-23）
NO_PICTURE_AFTER_S = 1.0      # 鏡頭或麥克風超過這麼久沒有資料，就在原地說明（UX-11）
FINISH_TIMEOUT_S = 30.0       # 停止後等錄影執行緒把檔案寫完，最多等這麼久（正常是幾十毫秒）
QUIT_WAIT_S = 5.0             # 結束程式時等檔案寫完，最多等這麼久

# 存檔的狀態：前三種還在進行（結束程式前要問，UX-29）
FINISHING, UPLOADING, SAVING, SAVED, FAILED = "finishing", "uploading", "saving", "saved", "failed"
IN_FLIGHT = (FINISHING, UPLOADING, SAVING)

# QSS 用的顏色：值都來自 myapp/theme.py（和 Theme.qml 同一組，tests/unit/test_ui_copy.py 比對兩邊），這裡不寫死
COLORS = {
    "window": RECORD_WINDOW["windowBg"],               # 錄影視窗的底色（新版介面的深色，設計稿 StaffRecorder）
    "surface": TOKENS["card"],                         # 狀態列、次要按鈕的底
    "text": TOKENS["textPrimary"],
    "secondary": TOKENS["textSecondary"],
    "secondary_border": TOKENS["actionSecondaryBorder"],
    "secondary_hover": TOKENS["actionSecondaryHover"],  # 次要按鈕的 hover、按下：和 QML 的 BusyButton 一樣（DS-12）
    "secondary_pressed": TOKENS["actionSecondaryPressed"],
    "disabled_text": TOKENS["actionDisabledText"],
    "disabled_bg": TOKENS["actionDisabledBg"],
    "track": TOKENS["track"],                          # 進度條的軌道
    "fill": TOKENS["progressFill"],
    "primary": TOKENS["actionPrimary"],                # 淺色膠囊（UD3），深色字 15.5:1
    "primary_hover": TOKENS["actionPrimaryHover"],
    "primary_pressed": TOKENS["actionPrimaryPressed"],
    "primary_text": TOKENS["actionPrimaryText"],
    "recording": TOKENS["recording"],                  # 只用在實心的錄影圓點
    "success": TOKENS["successOnDark"],
    "error": TOKENS["errorOnDark"],
    "focus": TOKENS["focusOnDark"],
    "preview": RECORD_WINDOW["previewBg"],             # 預覽框
    "preview_border": RECORD_WINDOW["previewBorder"],
}

# Lucide 圖示的內容（ISC 授權，見 myapp/src/myapp/images/icons/LICENSE-lucide.txt），畫的時候才上色
_ICONS = {
    "circle": '<circle cx="12" cy="12" r="10"/>',
    "dot": '<circle cx="12" cy="12" r="8.4" fill="{color}" stroke="none"/>',  # 錄影中的實心紅點（不閃）
    "loader": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    "upload": '<path d="M12 3v12"/><path d="m17 8-5-5-5 5"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>',
    "circle-check": '<circle cx="12" cy="12" r="10"/><path d="m16 9-5.5 5.5L8 12"/>',
    "circle-x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "video-off": ('<path d="M10.66 6H14a2 2 0 0 1 2 2v2.5l5.248-3.062A.5.5 0 0 1 22 7.87v8.196"/>'
                  '<path d="M16 16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2"/><path d="m2 2 20 20"/>'),
    "mic-off": ('<line x1="2" x2="22" y1="2" y2="22"/><path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2"/>'
                '<path d="M5 10v2a7 7 0 0 0 12 5"/><path d="M15 9.34V5a3 3 0 0 0-5.68-1.33"/>'
                '<path d="M9 9v3a3 3 0 0 0 5.12 2.12"/><line x1="12" x2="12" y1="19" y2="22"/>'),
    "square": '<rect width="18" height="18" x="3" y="3" rx="2"/>',
}


# ---------------------------------------------------------------- 小工具

def _tr(text, *values):
    """介面上的英文（PLAN D1、DS-51）：經過 translate，日後加翻譯檔就能換語言；%1、%2… 一次換成 values
    （像 QString::arg，使用者的資料裡就算有「%2」也不會被換掉）。和 media.py 的 _tr 一樣，context 是 RecordWidget。"""
    text = QtCore.QCoreApplication.translate("RecordWidget", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def transfer_text(done, total):
    """DS-23 的數字「58.6 / 124.0 MB · 47%」，和 QML 的 Theme.transferText 一樣：MB 一位小數，百分比無條件捨去。"""
    if not total > 0:
        return ""
    done = max(0.0, min(float(done), float(total)))
    return _tr("%1 / %2 MB · %3%", f"{done / 1e6:.1f}", f"{total / 1e6:.1f}", math.floor(done / total * 100))


def file_number(done, sizes):
    """已經傳了 done bytes 時，正在傳第幾個檔案（從 1 算）。"""
    boundary = 0
    for number, size in enumerate(sizes, 1):
        boundary += size
        if done < boundary:
            return number
    return max(len(sizes), 1)


def format_elapsed(seconds):
    """錄影的經過時間「00:12:34」。"""
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def peak_of(data):
    """16-bit PCM 一段資料的峰值（0～32768）。"""
    samples = np.frombuffer(data, dtype=np.int16)
    return int(np.abs(samples.astype(np.int32)).max()) if samples.size else 0


def level_from_peak(peak):
    """峰值 → 音量條的 0～1：-60 dBFS 以下是 0，0 dBFS 是 1。"""
    if peak <= 0:
        return 0.0
    dbfs = 20 * math.log10(min(peak, 32768) / 32768)
    return max(0.0, min(1.0, (dbfs + 60) / 60))


def failure_detail(error, stage):
    """DS-52 的「<sid> wasn't saved」內文：先講原因，再講檔案還在。最多兩句；技術代碼不放進來（寫進 log）。"""
    if stage == "database":
        return _tr("The files uploaded, but the database couldn't be updated. Retry to finish saving.")
    _, title, detail, _ = user_message(error)
    if title in (FILE_NOT_FOUND.title, NO_PERMISSION.title, DISK_FULL.title):  # 本機檔案的問題：照 ENG-12 的說明
        return f"{title}. {detail}"
    return _tr("%1. The recording is still on this computer.", title)


def restored_detail(stage):
    """重開 app 之後，上次沒存完的那一筆的內文（T26）。當時的原因可能已經不成立了（例如網路好了），只講停在哪一步。"""
    if stage == "database":
        return failure_detail(None, stage)
    return _tr("The upload didn't finish. The recording is still on this computer.")


def reduce_motion(ui_prefs):
    """和 QML 的 Theme.reduceMotion 同一個判斷；animationScale 0（測試）也不動。"""
    if ui_prefs is not None:
        if ui_prefs.animationScale == 0 or ui_prefs.motionMode == MOTION_REDUCE:
            return True
        if ui_prefs.motionMode == MOTION_FULL:
            return False
    style_hints = QtGui.QGuiApplication.styleHints()  # 要留著參照：只留 accessibility() 的話，它會先被刪掉
    accessibility = style_hints.accessibility()
    return accessibility.motionPreference() == QtCore.Qt.MotionPreference.ReducedMotion


def icon_pixmap(name, color, size):
    """把 Lucide 圖示畫成 size×size（邏輯像素）的 QPixmap；依螢幕的 devicePixelRatio 畫，Retina 上才清楚。"""
    ratio = QtGui.QGuiApplication.instance().devicePixelRatio()
    body = _ICONS[name].replace("{color}", color)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    pixels = max(1, round(size * ratio))
    pixmap = QtGui.QPixmap(pixels, pixels)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    QSvgRenderer(QtCore.QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def _font(pixel_size, *, bold=False, tabular=False):
    font = QtGui.QFont()
    font.setPixelSize(pixel_size)
    if bold:
        font.setWeight(QtGui.QFont.Weight.Bold)
    if tabular:  # 數字等寬：計時、MB 數字跳動時不會左右晃
        font.setFeature(QtGui.QFont.Tag("tnum"), 1)
    return font


def _label(name, pixel_size, color, *, bold=False, tabular=False, center=False, wrap=False):
    label = QtWidgets.QLabel()
    label.setObjectName(name)
    label.setFont(_font(pixel_size, bold=bold, tabular=tabular))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    if center:
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(wrap)
    return label


def _progress_bar(name, width, accessible_name):
    """DS-23 的進度條：高 6、圓角 3、軌道 #E0E0E0、填色 #333333。數值直接設定，不做補間。"""
    bar = QtWidgets.QProgressBar()
    bar.setObjectName(name)
    bar.setFixedSize(width, 6)
    bar.setTextVisible(False)
    bar.setAccessibleName(accessible_name)
    bar.setStyleSheet(f"QProgressBar {{ background: {COLORS['track']}; border: none; border-radius: 3px; }}"
                      f"QProgressBar::chunk {{ background: {COLORS['fill']}; border-radius: 3px; }}")
    return bar


def _button_style(primary, radius=10, pixel_size=18):
    """DS-12 的按鈕：主要是石墨灰底白字，次要是白底、#596069 框線、#333333 字；hover、按下和 QML 的 BusyButton 一樣。"""
    if primary:
        normal, hover, pressed = (f"background: {COLORS['primary']}; color: {COLORS['primary_text']}; border: none;",
                                  f"background: {COLORS['primary_hover']};", f"background: {COLORS['primary_pressed']};")
    else:
        normal, hover, pressed = (f"background: {COLORS['surface']}; color: {COLORS['text']}; "
                                  f"border: 1px solid {COLORS['secondary_border']};",
                                  f"background: {COLORS['secondary_hover']};",
                                  f"background: {COLORS['secondary_pressed']};")
    return (f"QPushButton {{ {normal} border-radius: {radius}px; padding: 0 16px; font-size: {pixel_size}px; "
            "font-weight: bold; }"
            f"QPushButton:hover {{ {hover} }}"
            f"QPushButton:pressed {{ {pressed} }}"
            f"QPushButton:focus {{ border: 2px solid {COLORS['focus']}; }}"
            f"QPushButton:disabled {{ background: {COLORS['disabled_bg']}; color: {COLORS['disabled_text']}; "
            "border: none; }")


BUTTON_ICON_GAP = 10  # 按鈕圖示和文字的間距（設計稿）；QSS 沒有這個屬性，畫在圖示右邊的透明空白裡


def _button_icon(name, color, size):
    """按鈕的圖示（寬 size + BUTTON_ICON_GAP）：停用時換成停用的灰色。"""
    icon = QtGui.QIcon()
    for mode, tint in ((QtGui.QIcon.Mode.Normal, color), (QtGui.QIcon.Mode.Disabled, COLORS["disabled_text"])):
        glyph = icon_pixmap(name, tint, size)
        ratio = glyph.devicePixelRatio()
        padded = QtGui.QPixmap(round((size + BUTTON_ICON_GAP) * ratio), glyph.height())
        padded.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(padded)
        painter.drawPixmap(0, 0, glyph)
        painter.end()
        padded.setDevicePixelRatio(ratio)
        icon.addPixmap(padded, mode)
    return icon


# ---------------------------------------------------------------- 一段錄影

class Recording:
    """一段錄影：錄音、錄影兩條執行緒，以及它們寫的三個檔案。

    - 執行緒只寫自己的欄位；GUI 執行緒用計時器讀（音量、哪支鏡頭沒畫面、麥克風有沒有出錯），不用 signal。
    - 麥克風打不開或中途壞掉：記下錯誤，影片照錄，錄音檔留下已經錄到的部分（以前整段的影片也一起丟掉，UX-11）。
    - 兩支鏡頭都讀到畫面才寫入（和以前一樣），兩支影片的影格數相同、時間對得上。
    - 檔案用絕對路徑：上傳在背景執行，不受之後目前目錄改變的影響。S3 的 key 照舊是 video/…、audio/…。
    """

    SAMPLE_FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 44100
    CHUNK = 1024
    FRAME_SIZE = (640, 480)
    FPS = 30.0

    def __init__(self, service_id, account_id, camera_ids, mic_index, start_time, folder="."):
        self.service_id = service_id
        self.account_id = account_id
        self.camera_ids = tuple(camera_ids)
        self.mic_index = mic_index
        self.start_time = start_time
        self.end_time = None
        folder = os.path.abspath(folder)
        self.video_files = [os.path.join(folder, "video", f"{service_id}_cam{i}.avi") for i in range(2)]
        self.audio_file = os.path.join(folder, "audio", f"{service_id}_audio.wav")
        self._stop = threading.Event()
        self._threads = []
        self._started_at = None  # time.monotonic()
        self._stopped_at = None
        # 錄影執行緒寫、GUI 執行緒讀
        self.frames_written = 0
        self.camera_opened = [None, None]  # None：還不知道
        self.last_frame_at = [None, None]  # 每支鏡頭最後一次讀到畫面的時間（monotonic）
        self.audio_chunks = 0
        self.last_chunk_at = None
        self.audio_error = None  # 麥克風的問題（錄影照常）
        self.write_error = None  # 寫檔失敗（例如磁碟滿了）：這一段不能上傳
        self._peak = 0

    @classmethod
    def restored(cls, pending):
        """重開 app 之後讀回來的一段（PendingSave，T26）：檔案早就寫好了，沒有錄影執行緒，路徑用記下來的。"""
        recording = cls(pending.service_id, pending.account_id, (), None, pending.start_time)
        recording.video_files, recording.audio_file = list(pending.files[:2]), pending.files[2]
        recording.end_time = pending.end_time
        recording._stop.set()
        return recording

    @property
    def files(self):
        return [*self.video_files, self.audio_file]

    @property
    def keys(self):
        """S3 的 key，和三個檔案同順序。"""
        return [f"{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}" for path in self.files]

    @property
    def stopped(self):
        return self._stop.is_set()

    @property
    def finished(self):
        """兩條執行緒都結束了：三個檔案都寫好了。"""
        return self.stopped and not any(thread.is_alive() for thread in self._threads)

    def start(self):
        for path in self.files:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._started_at = time.monotonic()
        # daemon：萬一沒有經過 stop（程式當掉），也不會讓程式關不掉（UX-09）；正常結束時 RecordWidget 會先停、等寫完
        self._threads = [threading.Thread(target=self._record_audio, name=f"audio-{self.service_id}", daemon=True),
                         threading.Thread(target=self._record_video, name=f"video-{self.service_id}", daemon=True)]
        for thread in self._threads:
            thread.start()

    def stop(self, end_time):
        if not self._stop.is_set():
            self.end_time = end_time
            self._stopped_at = time.monotonic()
            self._stop.set()

    def wait(self, timeout=None):
        """等兩條執行緒把檔案寫完。回傳是否都結束了。不要在 GUI 執行緒等（UX-08），結束程式時例外。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self._threads)

    def elapsed(self, now=None):
        if self._started_at is None:
            return 0.0
        end = self._stopped_at if self._stopped_at is not None else (time.monotonic() if now is None else now)
        return end - self._started_at

    def take_level(self):
        """上次讀之後的最大音量（0～1），讀完歸零。"""
        peak, self._peak = self._peak, 0
        return level_from_peak(peak)

    def camera_problem(self, index, now):
        """錄影執行緒這邊看到的鏡頭問題：打不開，或超過 NO_PICTURE_AFTER_S 秒沒有畫面。"""
        if self.camera_opened[index] is False:
            return True
        return self._silent_for(self.last_frame_at[index], now)

    def microphone_problem(self, now):
        """麥克風打不開、中途出錯，或超過 NO_PICTURE_AFTER_S 秒沒有聲音資料（安靜時也會有資料）。"""
        return self.audio_error is not None or self._silent_for(self.last_chunk_at, now)

    def _silent_for(self, last, now):
        if self._started_at is None or self.stopped:
            return False
        return now - (self._started_at if last is None else last) > NO_PICTURE_AFTER_S

    def _record_audio(self):
        frames = []
        audio = pyaudio.PyAudio()
        sample_width = audio.get_sample_size(self.SAMPLE_FORMAT)
        stream = None
        try:
            stream = audio.open(format=self.SAMPLE_FORMAT, channels=self.CHANNELS, rate=self.RATE, input=True,
                                input_device_index=self.mic_index, frames_per_buffer=self.CHUNK)
            while not self._stop.is_set():
                data = stream.read(self.CHUNK, exception_on_overflow=False)  # 溢位時丟掉那一段，不要中斷錄音
                if data:
                    frames.append(data)
                    self.audio_chunks += 1
                    self.last_chunk_at = time.monotonic()
                    self._peak = max(self._peak, peak_of(data))
        except Exception as error:  # 打不開、拔掉：視窗在原地說明，影片照錄（UX-11）
            logger.exception("recording audio for %s failed", self.service_id)
            self.audio_error = error
        finally:
            if stream is not None:
                with suppress(Exception):
                    stream.stop_stream()
                with suppress(Exception):
                    stream.close()
            audio.terminate()
        self._write_wav(frames, sample_width)

    def _write_wav(self, frames, sample_width):
        try:
            with wave.open(self.audio_file, "wb") as wav:
                wav.setnchannels(self.CHANNELS)
                wav.setsampwidth(sample_width)
                wav.setframerate(self.RATE)
                wav.writeframes(b"".join(frames))
        except Exception as error:
            logger.exception("writing %s failed", self.audio_file)
            self.write_error = self.write_error or error

    def _record_video(self):
        captures = [cv2.VideoCapture(camera_id) for camera_id in self.camera_ids]
        writers = []
        try:
            for index, capture in enumerate(captures):
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.FRAME_SIZE[0])
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.FRAME_SIZE[1])
                self.camera_opened[index] = bool(capture.isOpened())
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            writers = [cv2.VideoWriter(path, fourcc, self.FPS, self.FRAME_SIZE) for path in self.video_files]
            while not self._stop.is_set():
                ok0, frame0 = captures[0].read()
                ok1, frame1 = captures[1].read()
                now = time.monotonic()
                if ok0:
                    self.last_frame_at[0] = now
                if ok1:
                    self.last_frame_at[1] = now
                if ok0 and ok1:
                    # 鏡頭實際輸出的尺寸可能不是 640x480；尺寸和 VideoWriter 不一致的影格會被直接丟掉
                    writers[0].write(cv2.resize(frame0, self.FRAME_SIZE))
                    writers[1].write(cv2.resize(frame1, self.FRAME_SIZE))
                    self.frames_written += 1
                elif not (ok0 or ok1):
                    self._stop.wait(0.01)  # 兩支都讀不到（沒接、拔掉）：不要空轉
        except Exception as error:
            logger.exception("recording video for %s failed", self.service_id)
            self.write_error = self.write_error or error
        finally:
            for capture in captures:
                capture.release()
            for writer in writers:
                writer.release()


# ---------------------------------------------------------------- 背景存檔

@dataclass(eq=False)
class SaveJob:
    """一段錄影的存檔：狀態、進度、結果。只在 GUI 執行緒改（sizes 由上傳的工作在第一次回報進度前寫好）。"""

    recording: Recording
    state: str = FINISHING
    attempt: int = 1
    done: float = 0.0
    total: float = 0.0
    sizes: list = field(default_factory=list)
    urls: list = None           # 三個檔案都上傳了才有；之後重試只寫資料庫
    error_title: str = ""
    error_detail: str = ""
    retryable: bool = True
    toast_id: int = 0
    analyze_requested: bool = False

    @property
    def service_id(self):
        return self.recording.service_id

    @property
    def in_flight(self):
        return self.state in IN_FLIGHT

    @property
    def title(self):
        """「Uploading S20261031093000」；重試時加「(attempt 2)」（DS-23）。"""
        if self.attempt > 1:
            return _tr("Uploading %1 (attempt %2)", self.service_id, self.attempt)
        return _tr("Uploading %1", self.service_id)

    @property
    def file_number(self):
        return file_number(self.done, self.sizes)

    @property
    def file_name(self):
        return os.path.basename(self.recording.files[self.file_number - 1])


def upload_recording(job, token, report):
    """在 io lane 執行：等錄影執行緒把檔案寫完，依序上傳三個檔案，回傳三個 s3:// URL。

    report(增加的 bytes, 總量)：總量是三個檔案加起來（storage 給的是單一檔案的大小，這裡換成總量）。
    重試時從頭上傳三個檔案（S3 會覆蓋同一個 key）。
    """
    recording = job.recording
    if not recording.wait(FINISH_TIMEOUT_S):
        raise TimeoutError(f"the recording threads of {recording.service_id} didn't finish")
    if recording.write_error is not None:
        raise recording.write_error
    sizes = [os.path.getsize(path) for path in recording.files]
    total = sum(sizes)
    job.sizes = sizes  # GUI 執行緒收到下面這一次進度之後才讀（queued signal 保證先後）
    report(0, total)
    urls = []
    for path, key in zip(recording.files, recording.keys):
        token.raise_if_cancelled()
        urls.append(storage.upload_with_progress(path, key, token, lambda done, size: report(done, total)))
    return urls


INSERT_SERVICE_SQL = "CALL sp_insert_service(%s, %s, %s, %s, %s, @insert_service_result)"
INSERT_VIDEO_SQL = "CALL sp_insert_video_storage(%s, %s, %s, %s)"
INSERT_AUDIO_SQL = "CALL sp_insert_audio_storage(%s, %s, %s, %s)"
SERVICE_OWNER_SQL = "SELECT account_id FROM tbl_service WHERE service_id = %s"
DUPLICATE_KEY = 1062  # ER_DUP_ENTRY


def save_recording(db, recording, urls, uploaded_at):
    """在 db lane 執行：同一個交易寫服務（狀態 1）、兩支影片、一個音訊。中間失敗就整筆不算（DbLane 會 rollback）。

    6 個往返：START TRANSACTION、4 個 CALL、COMMIT。
    重試時這一段可能已經在資料庫裡了：上次 COMMIT 成功，但結果還沒回來 app 就結束了（Quit Anyway、當掉），
    重開之後的 Retry 會撞到主鍵（T26）。同一個帳號的同一個 service_id 就當作已經存好，不再寫一次（多 2 個往返）；
    別人的（兩台電腦在同一秒開始錄影）照樣失敗。
    """
    service_id = recording.service_id
    names = [os.path.basename(path) for path in recording.files]
    with db.cursor(dictionary=True) as cursor:  # 登入後已經建好的那一種 cursor：不必多一次建立時的 PING（T16）
        cursor.execute("START TRANSACTION")
        try:
            cursor.execute(INSERT_SERVICE_SQL,
                           (service_id, recording.account_id, recording.start_time, recording.end_time, 1))
        except Exception as error:
            if getattr(error, "errno", None) != DUPLICATE_KEY or not _saved_before(cursor, recording):
                raise
            logger.info("%s is already in the database; not saving it again", service_id)
            return
        for name, url in zip(names[:2], urls[:2]):
            cursor.execute(INSERT_VIDEO_SQL, (service_id, name, url, uploaded_at))
        cursor.execute(INSERT_AUDIO_SQL, (service_id, names[2], urls[2], uploaded_at))
        cursor.execute("COMMIT")


def _saved_before(cursor, recording):
    """撞到主鍵時：是不是這個帳號自己上次寫好的那一筆。先 ROLLBACK，交易不能開著；服務和檔案是同一個交易寫的，
    所以有服務就有三個檔案的紀錄。"""
    cursor.execute("ROLLBACK")
    cursor.execute(SERVICE_OWNER_SQL, (recording.service_id,))
    row = cursor.fetchone()
    return row is not None and row["account_id"] == recording.account_id


class RecordingSaver(QtCore.QObject):
    """停止錄影之後的存檔（UX-08、UX-10、ENG-03）：上傳 → 寫資料庫 → 通知。

    屬於 app，不屬於錄影視窗：視窗關了、或已經在錄下一段，還是會做完。結果用同一個 key 的通知就地更新。
    services 是 AppServices；on_saved(service_id) 在寫好資料庫之後呼叫（DeviceFinder 用它重新整理分析頁）。
    還沒存完的記在本機（PendingSaveStore，T26），重開 app 之後由 restore() 放回來，一樣可以 Retry。
    """

    changed = QtCore.Signal(str)        # service_id：這一筆的狀態或進度變了
    inFlightChanged = QtCore.Signal()   # 還在存的筆數或 bytes 變了（結束前的確認對話框用）

    def __init__(self, services, on_saved=None, parent=None):
        super().__init__(parent)
        self.services = services
        self._on_saved = on_saved
        self._jobs = {}  # service_id → SaveJob
        self._pending = PendingSaveStore()
        services.tasks.progressed.connect(self._on_progress)
        services.notifier.actionTriggered.connect(self._on_action)
        QtCore.QCoreApplication.instance().aboutToQuit.connect(self._finish_files)

    def job(self, service_id):
        return self._jobs.get(service_id)

    def in_flight(self):
        """還在收尾、上傳或寫資料庫的，依加入的順序。"""
        return [job for job in self._jobs.values() if job.in_flight]

    def add(self, recording):
        """停止錄影之後呼叫：在背景等檔案寫完、上傳、寫資料庫。先記在本機：app 中途結束，下次登入還能 Retry。"""
        job = SaveJob(recording)
        self._jobs[recording.service_id] = job
        self._remember(job, "upload")
        self._upload(job)
        return job

    def restore(self, account_id):
        """登入時（launch()）：這個帳號上次沒存完的錄影放回來，每一筆跳一則和失敗時一樣的通知（Retry，T26）。

        Retry 從停下來的那一步繼續：還沒上傳完的重傳三個檔案，已經上傳的只寫資料庫。
        還沒上傳、檔案又不在這台電腦上了：刪掉紀錄，說明存不了（沒有 Retry）。回傳放回來的 SaveJob。
        """
        restored = []
        for pending in self._pending.load(account_id):
            service_id = pending.service_id
            if service_id in self._jobs:  # 已經放回來過了
                continue
            recording = Recording.restored(pending)
            missing = [path for path in recording.files if not os.path.exists(path)]
            if pending.stage == "upload" and missing:
                logger.warning("%s can't be saved: %s no longer exist", service_id, missing)
                self._pending.remove(service_id)
                self.services.notifier.notify(
                    "error", _tr("%1 can't be saved", service_id),
                    _tr("The recording is no longer on this computer. It may have been moved or deleted."),
                    UPLOAD_KEY.format(service_id))
                continue
            job = SaveJob(recording, state=FAILED, urls=list(pending.urls) if pending.urls else None,
                          error_title=_tr("%1 wasn't saved", service_id), error_detail=restored_detail(pending.stage))
            self._jobs[service_id] = job
            self._show_failure(job)
            self._emit(job)
            restored.append(job)
        return restored

    def retry(self, service_id):
        """失敗之後重試（通知或錄影視窗的 Retry）。檔案都上傳過了就只寫資料庫（DS-52「Retry to finish saving」）。"""
        job = self._jobs.get(service_id)
        if job is None or job.state != FAILED:
            return False
        job.attempt += 1
        job.error_title = job.error_detail = ""
        return self._save(job) if job.urls else self._upload(job)

    def request_analyze(self, service_id):
        """錄影視窗的 Analyze：和按通知上的 Analyze 一樣，發 analyze:<sid> 給送出分析的流程（CONTRACTS §4）。"""
        job = self._jobs.get(service_id)
        if job is None or job.state != SAVED or job.analyze_requested:
            return False
        job.analyze_requested = True
        notifier = self.services.notifier
        notifier.dismiss(job.toast_id)  # 和 Notifier.trigger() 一樣：先關掉那則通知，再發動作
        notifier.actionTriggered.emit(ANALYZE_ACTION.format(service_id), job.toast_id)
        self._emit(job)
        return True

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    def _upload(self, job):
        key = UPLOAD_KEY.format(job.service_id)
        if not self.services.tasks.submit("io", key, lambda token, report: upload_recording(job, token, report),
                                          on_ok=lambda urls: self._uploaded(job, urls),
                                          on_err=lambda error: self._failed(job, error, "upload")):
            return self._not_submitted(job, key)
        job.state = UPLOADING if job.recording.finished else FINISHING
        job.done = job.total = 0.0
        self.services.notifier.progress(key, 0, 0, job.title, _tr("File %1 of %2", 1, FILE_COUNT))
        self._emit(job)
        return True

    @QtCore.Slot(str, float, float)
    def _on_progress(self, key, done, total):
        prefix, _, service_id = key.partition(":")
        job = self._jobs.get(service_id) if prefix == "upload" else None
        if job is None or job.state not in (FINISHING, UPLOADING):
            return
        job.state = UPLOADING
        job.done, job.total = done, total
        self.services.notifier.progress(key, done, total, job.title, _tr("File %1 of %2", job.file_number, FILE_COUNT))
        self._emit(job)

    def _uploaded(self, job, urls):
        job.urls = list(urls)
        job.done = job.total
        self._remember(job, "database")  # 三個檔案都上傳了：之後（重開 app 也是）只要寫資料庫
        self._save(job)

    def _save(self, job):
        key = SAVE_KEY.format(job.service_id)
        db, recording, urls, uploaded_at = self.services.db, job.recording, list(job.urls), datetime.now()
        if not self.services.tasks.submit("db", key,
                                          lambda token, report: save_recording(db, recording, urls, uploaded_at),
                                          on_ok=lambda result: self._saved(job),
                                          on_err=lambda error: self._failed(job, error, "database")):
            return self._not_submitted(job, key)
        job.state = SAVING
        self.services.notifier.progress(UPLOAD_KEY.format(job.service_id), job.total, job.total, job.title,
                                        _tr("Saving to the database…"))
        self._emit(job)
        return True

    def _saved(self, job):
        service_id = job.service_id
        job.state = SAVED
        self._pending.remove(service_id)
        job.toast_id = self.services.notifier.notify(
            "success", _tr("Saved %1", service_id), _tr("Video and audio uploaded."), UPLOAD_KEY.format(service_id),
            ANALYZE_ACTION.format(service_id), _tr("Analyze"))
        self._emit(job)
        if self._on_saved is not None:
            self._on_saved(service_id)

    def _failed(self, job, error, stage):
        service_id = job.service_id
        logger.warning("saving %s failed while %s: %r", service_id,  # 技術細節只進 log（DS-50）
                       "uploading" if stage == "upload" else "writing the database", error, exc_info=error)
        job.state = FAILED
        job.error_title = _tr("%1 wasn't saved", service_id)
        job.error_detail = failure_detail(error, stage)
        job.retryable = not isinstance(error, FileNotFoundError)  # 檔案不見了，重試也一樣
        if job.retryable and job.recording.write_error is None:
            self._remember(job, stage)
        else:  # 檔案不見了，或沒寫完整（不上傳不完整的檔案，T20 第 4 節）：重開 app 之後也存不了
            self._pending.remove(service_id)
        self._show_failure(job)
        self._emit(job)

    def _show_failure(self, job):
        """「<sid> wasn't saved」：常駐的錯誤通知，能重試的附 Retry（retry-upload:<sid>，CONTRACTS §4）。"""
        service_id = job.service_id
        job.toast_id = self.services.notifier.notify(
            "error", job.error_title, job.error_detail, UPLOAD_KEY.format(service_id),
            RETRY_PREFIX + service_id if job.retryable else "", _tr("Retry") if job.retryable else "")

    def _remember(self, job, stage):
        """記在本機（T26）：app 中途結束或當掉，下次同一個帳號登入時還能 Retry。stage 是 Retry 要從哪一步開始。"""
        recording = job.recording
        self._pending.put(PendingSave(job.service_id, recording.account_id, tuple(recording.files),
                                      recording.start_time, recording.end_time, stage,
                                      tuple(job.urls) if job.urls else None))

    def _not_submitted(self, job, key):
        # 只會發生在結束程式的途中（TaskRunner 已經 shutdown）：記成失敗，不算在進行中
        logger.warning("couldn't start %s; the app is shutting down", key)
        job.state = FAILED
        job.error_title = _tr("%1 wasn't saved", job.service_id)
        job.error_detail = _tr("The app is closing. The recording is still on this computer.")
        self._emit(job)
        return False

    @QtCore.Slot(str, int)
    def _on_action(self, action_id, toast_id):
        if action_id.startswith(RETRY_PREFIX):  # 其他前綴是別的流程的（CONTRACTS §4），不認得就不管
            self.retry(action_id[len(RETRY_PREFIX):])

    def _emit(self, job):
        self.changed.emit(job.service_id)
        self.inFlightChanged.emit()

    @QtCore.Slot()
    def _finish_files(self):
        """結束程式時：上傳會被取消（TaskRunner.shutdown），但剛停止的錄影要把檔案寫完，留在這台電腦上。"""
        for job in self._jobs.values():
            if job.state == FINISHING:
                job.recording.wait(QUIT_WAIT_S)


# ---------------------------------------------------------------- 結束程式前先問（UX-29）

class QuitGuard(QtCore.QObject):
    """裝在 app 上的 event filter：有人登記要擋的時候，擋下 QEvent.Quit，改在下一輪事件呼叫每個人的 quit_blocked()。

    ⌘Q、Dock 的「結束」、Qt.quit() 都會變成送給 app 的 QEvent.Quit。這個 app 的 QApplication 收到時只關 QWidget 視窗，
    QML 外殼的 onClosing 不會被呼叫（T20 實測），所以要在 app 這一層攔。

    - 一個 app 只有一個（instance()），由這個類別留著，不屬於任何頁面物件：Qt 直接呼叫 app 層級的 filter，
      Python 那一半一定要還在。（原本每個 DeviceFinder 有一個子物件當 filter，被 gc 收掉的那一刻 Qt 還在呼叫它，
      測試裡因此 segfault 過一次。）
    - set_blocking(owner, True/False)：owner（例如錄影中、存檔中的 DeviceFinder）用弱參照登記，被刪掉就不算。
    - app 層級的 filter 會看到每一個事件：沒有人登記時拿掉，平常的事件不經過 Python。
    """

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._owners = weakref.WeakSet()
        self.installed = False

    def set_blocking(self, owner, blocking):
        if blocking:
            self._owners.add(owner)
        else:
            self._owners.discard(owner)
        self._install(bool(self._owners))

    def blocking(self, owner):
        return owner in self._owners

    def _install(self, installed):
        if installed != self.installed:
            app = QtCore.QCoreApplication.instance()
            if installed:
                app.installEventFilter(self)
            else:
                app.removeEventFilter(self)  # 在 eventFilter 裡拿掉也安全（Qt 的文件）
            self.installed = installed

    def eventFilter(self, watched, event):
        if event.type() != QtCore.QEvent.Type.Quit:
            return False
        owners = list(self._owners)
        if not owners:  # 登記的都已經被刪掉了
            self._install(False)
            return False
        event.ignore()  # macOS 上 applicationShouldTerminate: 因此回傳 NSTerminateCancel
        for owner in owners:  # 不在 AppKit 的 terminate 回呼裡開對話框；owner 先被刪掉的話 Qt 會取消這一次
            QtCore.QTimer.singleShot(0, owner, owner.quit_blocked)
        return True


# ---------------------------------------------------------------- 錄影視窗

class StatusBar(QtWidgets.QFrame):
    """錄影視窗的狀態列（DS-34）：56 px、白底、圓角 5。左邊圖示＋標題（16 粗）＋說明（14），
    右邊依狀態是計時、進度條或一個動作按鈕。"""

    actionClicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFixedHeight(56)
        self.setStyleSheet(f"#statusBar {{ background: {COLORS['surface']}; border-radius: 5px; }}")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)
        self.icon = QtWidgets.QLabel()
        self.icon.setObjectName("statusIcon")
        self.icon.setFixedSize(20, 20)
        texts = QtWidgets.QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        self.title = _label("statusTitle", 16, COLORS["text"], bold=True)
        self.detail = _label("statusDetail", 14, COLORS["secondary"], tabular=True)
        for label in (self.title, self.detail):
            label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            texts.addWidget(label)
        self.elapsed = _label("elapsed", 20, COLORS["text"], bold=True, tabular=True)
        self.progress = _progress_bar("statusProgress", 240, "")
        self.button = QtWidgets.QPushButton()
        self.button.setObjectName("statusAction")
        self.button.setFixedHeight(40)
        self.button.setStyleSheet(_button_style(primary=False, radius=5, pixel_size=16))
        self.button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.button.clicked.connect(self.actionClicked)
        layout.addWidget(self.icon)
        layout.addLayout(texts, 1)
        for widget in (self.elapsed, self.progress, self.button):
            widget.hide()
            layout.addWidget(widget)
        self.state = ""
        self._icon_key = None

    def show_state(self, state, icon, color, title, detail="", *, elapsed=None, progress=None, action="",
                   action_enabled=True, still=False):
        """progress：(done, total) 是有總量的進度；"busy" 是不定進度（still 為真時是靜止的軌道，減少動態）。"""
        self.state = state
        if self._icon_key != (icon, color):
            self._icon_key = (icon, color)
            self.icon.setPixmap(icon_pixmap(icon, color, 20))
        self.title.setText(title)
        self.detail.setText(detail)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
        self.layout().setContentsMargins(16, 0, 8 if action else 16, 0)  # 有按鈕時右邊 8（設計稿）
        self.elapsed.setVisible(elapsed is not None)
        if elapsed is not None:
            self.elapsed.setText(elapsed)
        self.progress.setVisible(progress is not None)
        if progress == "busy":
            self.progress.setRange(0, 1 if still else 0)
            self.progress.setValue(0)
        elif progress is not None:
            done, total = progress
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(1000 * min(done, total) / total) if total > 0 else 0)
        self.progress.setAccessibleName(title)
        self.button.setVisible(bool(action))
        self.button.setText(action)
        self.button.setEnabled(action_enabled)

    def set_elapsed(self, text):
        self.elapsed.setText(text)


class CameraPreview(QtWidgets.QFrame):
    """一支鏡頭的預覽框（480×360）。打不開或沒有畫面時在框裡說明（DS-34、UX-11），不擋另一支鏡頭。"""

    SIZE = (480, 360)

    def __init__(self, camera_name, parent=None):
        super().__init__(parent)
        self.camera_name = camera_name
        self.setObjectName("cameraPreview")
        self.setFixedSize(*self.SIZE)
        self.setStyleSheet(f"#cameraPreview {{ background: {COLORS['preview']}; "
                           f"border: 2px solid {COLORS['preview_border']}; border-radius: 10px; }}")
        self._stack = QtWidgets.QStackedLayout(self)
        self.image = QtWidgets.QLabel()
        self.image.setObjectName("cameraImage")
        self.image.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.message = QtWidgets.QWidget()
        self.message.setObjectName("cameraProblem")
        box = QtWidgets.QVBoxLayout(self.message)
        box.setContentsMargins(32, 32, 32, 32)
        box.setSpacing(10)
        box.addStretch(1)
        self.problem_icon = QtWidgets.QLabel()
        self.problem_icon.setPixmap(icon_pixmap("video-off", COLORS["text"], 32))
        self.problem_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.problem_title = _label("problemTitle", 16, COLORS["text"], bold=True, center=True, wrap=True)
        self.problem_body = _label("problemBody", 14, COLORS["text"], center=True, wrap=True)
        for widget in (self.problem_icon, self.problem_title, self.problem_body):
            box.addWidget(widget)
        box.addStretch(1)
        self._stack.addWidget(self.image)
        self._stack.addWidget(self.message)
        self.problem = ""  # 顯示中的問題（標題）；空字串＝有畫面

    def show_frame(self, image):
        self.image.setPixmap(QtGui.QPixmap.fromImage(image))

    def set_problem(self, problem):
        title = _tr("%1 has no picture", self.camera_name) if problem else ""
        if title == self.problem:
            return
        self.problem = title
        if problem:
            hint = _tr("Check the cable, or go back and choose another camera.")
            self.problem_title.setText(title)
            self.problem_body.setText(hint)
            self._stack.setCurrentWidget(self.message)
            self.setAccessibleDescription(f"{title}. {hint}")
        else:
            self._stack.setCurrentWidget(self.image)
            self.setAccessibleDescription("")


class MicrophoneRow(QtWidgets.QWidget):
    """麥克風那一行：名稱，錄影中加上音量條（每秒最多更新 4 次）；出問題時在這裡說明（UX-11、UX-12）。"""

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.setObjectName("microphoneRow")
        self.setFixedHeight(24)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.icon = QtWidgets.QLabel()
        self.icon.setFixedSize(16, 16)
        self.icon.setPixmap(icon_pixmap("mic-off", COLORS["error"], 16))
        self.icon.hide()
        self.label = _label("microphoneLabel", 14, COLORS["secondary"])
        self.level = _progress_bar("levelMeter", 160, _tr("Microphone level"))
        self.level.setRange(0, 100)
        self.level.hide()
        layout.addStretch(1)
        for widget in (self.icon, self.label, self.level):
            layout.addWidget(widget)
        layout.addStretch(1)
        self.problem = False
        self._show_name()

    def start(self):
        self.level.setValue(0)
        self.level.show()
        self.set_problem(False)

    def stop(self):
        self.level.hide()

    def set_level(self, level):
        self.level.setValue(round(100 * level))

    def set_problem(self, problem):
        if problem == self.problem:
            return
        self.problem = problem
        if problem:
            self.icon.show()
            self.label.setText(_tr("%1 isn't working. Check the cable, or go back and choose another microphone.",
                                   self.name))
            self.label.setStyleSheet(f"color: {COLORS['error']}; background: transparent;")
        else:
            self._show_name()

    def _show_name(self):
        self.icon.hide()
        self.label.setText(_tr("Microphone: %1", self.name))
        self.label.setStyleSheet(f"color: {COLORS['secondary']}; background: transparent;")


class RecordWidget(QtWidgets.QWidget):
    """錄影視窗。DeviceFinder.startRecording() 打開；saver 是 RecordingSaver。

    - 按 Start Recording 時產生這一段的 service_id（'S' + 開始的時間）；按 Stop Recording 之後交給 saver
      在背景收尾、上傳、寫資料庫，馬上可以錄下一段（UX-08、DS-34）。
    - 關掉視窗、結束程式時，錄影中的先停止並存檔，再關鏡頭（UX-09；以前鏡頭和麥克風會一直開著）。
    - 狀態列跟著「錄影中的那一段」，沒有在錄時跟著最後停止的那一段（DS-34）。
    """

    closed = QtCore.Signal()
    recordingChanged = QtCore.Signal(bool)

    def __init__(self, cam0_index, cam1_index, mic_index, account_id, parent=None, *, saver,
                 camera_names=None, microphone_name=None):
        super().__init__(parent)
        self.customer_cam_index = cam0_index
        self.staff_cam_index = cam1_index
        self.mic_index = mic_index
        self.account_id = account_id
        self.saver = saver
        self.camera_names = tuple(camera_names or (_tr("Camera %1", cam0_index), _tr("Camera %1", cam1_index)))
        self.microphone_name = microphone_name or _tr("Microphone %1", mic_index)
        self.CAM_NUMS = [cam0_index, cam1_index]  # 顧客、員工
        self.timer_cameras = []  # 每支鏡頭的預覽計時器
        self.caps = []  # 每支鏡頭的預覽 VideoCapture
        self.current = None  # 錄影中的 Recording
        self.last_recording = None  # 最後停止的那一段
        self.service_id = None
        self.start_time = self.end_time = None
        self.announced = []  # 唸過的狀態（文字, 是否 assertive），測試和除錯用
        self._preview_opened = [False, False]
        self._preview_since = [None, None]
        self._preview_last_frame = [None, None]
        self._announced_state = None

        self.set_ui()
        self.slot_init()
        self.open_cameras()
        self.saver.changed.connect(self._on_save_changed)
        QtCore.QCoreApplication.instance().aboutToQuit.connect(self._on_about_to_quit)
        self._refresh_status()

    @property
    def recording(self):
        return self.current is not None

    # ------------------------------------------------------------ 畫面

    def set_ui(self):
        self.setWindowTitle(_tr("Recording"))
        self.setFixedSize(1024, 640)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(COLORS["window"]))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.status_bar = StatusBar(self)
        self.status_bar.actionClicked.connect(self._on_status_action)
        layout.addWidget(self.status_bar)

        cameras = QtWidgets.QGridLayout()
        cameras.setSpacing(10)
        self.label_customer = _label("customerCameraLabel", 14, COLORS["text"], center=True)
        self.label_customer.setText(_tr("Customer Camera"))
        self.label_staff = _label("staffCameraLabel", 14, COLORS["text"], center=True)
        self.label_staff.setText(_tr("Staff Camera"))
        self.previews = [CameraPreview(name) for name in self.camera_names]
        self.previews[0].setAccessibleName(_tr("Customer Camera"))
        self.previews[1].setAccessibleName(_tr("Staff Camera"))
        self.label_cameras = [preview.image for preview in self.previews]  # 預覽畫面的 QLabel（舊名稱）
        cameras.addWidget(self.label_customer, 0, 0)
        cameras.addWidget(self.label_staff, 0, 1)
        cameras.addWidget(self.previews[0], 1, 0)
        cameras.addWidget(self.previews[1], 1, 1)
        layout.addLayout(cameras)

        self.microphone_row = MicrophoneRow(self.microphone_name)
        layout.addWidget(self.microphone_row)
        layout.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(10)
        self.button_start_recording = QtWidgets.QPushButton(_tr("Start Recording"))
        self.button_start_recording.setStyleSheet(_button_style(primary=True))
        self.button_start_recording.setIcon(_button_icon("dot", COLORS["recording"], 12))
        self.button_start_recording.setIconSize(QtCore.QSize(12 + BUTTON_ICON_GAP, 12))
        self.button_stop_recording = QtWidgets.QPushButton(_tr("Stop Recording"))
        self.button_stop_recording.setStyleSheet(_button_style(primary=False))
        self.button_stop_recording.setIcon(_button_icon("square", COLORS["text"], 16))
        self.button_stop_recording.setIconSize(QtCore.QSize(16 + BUTTON_ICON_GAP, 16))
        for button in (self.button_start_recording, self.button_stop_recording):
            button.setMinimumHeight(50)
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            buttons.addWidget(button)
        self.button_stop_recording.setEnabled(False)
        layout.addLayout(buttons)

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(POLL_MS)
        self.poll_timer.timeout.connect(self._poll)

    def slot_init(self):
        self.button_start_recording.clicked.connect(self.start_recording)
        self.button_stop_recording.clicked.connect(self.stop_recording)
        for index, cam_num in enumerate(self.CAM_NUMS):
            timer = QtCore.QTimer(self)
            timer.timeout.connect(lambda index=index, cam_num=cam_num: self.show_camera(index, cam_num))
            self.timer_cameras.append(timer)
            self.caps.append(cv2.VideoCapture())

    def open_cameras(self):
        """打開兩支鏡頭的預覽。打不開的在它的預覽框裡說明（以前跳 QMessageBox，DS-34），另一支照常。"""
        now = time.monotonic()
        for index, cam_num in enumerate(self.CAM_NUMS):
            if self.timer_cameras[index].isActive():
                continue
            opened = bool(self.caps[index].open(cam_num))
            self._preview_opened[index] = opened
            self._preview_since[index] = now
            if opened:
                self.timer_cameras[index].start(30)
            else:
                logger.warning("camera %s didn't open for the preview", cam_num)
            self._update_camera(index, now)

    def show_camera(self, index, cam_num):
        flag, image = self.caps[index].read()
        now = time.monotonic()
        if flag:
            self._preview_last_frame[index] = now
            image = cv2.flip(image, 1)  # 左右翻轉，像照鏡子
            show = cv2.cvtColor(cv2.resize(image, CameraPreview.SIZE), cv2.COLOR_BGR2RGB)
            self.previews[index].show_frame(QtGui.QImage(show.data, show.shape[1], show.shape[0], show.strides[0],
                                                         QtGui.QImage.Format.Format_RGB888))
        self._update_camera(index, now)

    def _update_camera(self, index, now):
        """預覽或錄影任一邊打不開、或超過 NO_PICTURE_AFTER_S 秒沒有畫面，就在那支鏡頭的預覽框裡說明。"""
        problem = not self._preview_opened[index]
        if not problem:
            last = self._preview_last_frame[index]
            problem = now - (self._preview_since[index] if last is None else last) > NO_PICTURE_AFTER_S
        if self.current is not None:
            problem = problem or self.current.camera_problem(index, now)
        self.previews[index].set_problem(problem)

    @QtCore.Slot()
    def _poll(self):
        """錄影中每 250 ms：經過時間、音量、鏡頭和麥克風的狀態。"""
        recording = self.current
        if recording is None:
            return
        now = time.monotonic()
        self.status_bar.set_elapsed(format_elapsed(recording.elapsed(now)))
        self.microphone_row.set_level(recording.take_level())
        self.microphone_row.set_problem(recording.microphone_problem(now))
        for index in range(2):
            self._update_camera(index, now)

    # ------------------------------------------------------------ 錄影

    @QtCore.Slot()
    def start_recording(self):
        """開始錄影：這一段的 service_id 是 'S' + 開始的時間，只在這裡產生一次。"""
        if self.current is not None:
            return
        start_time = datetime.now()
        service_id = "S" + start_time.strftime("%Y%m%d%H%M%S")
        if self.saver.job(service_id) is not None:
            # 和上一段在同一秒開始：service_id 會重複（資料表的主鍵）。過一秒再按就好
            logger.warning("not starting: %s is already used by the previous recording", service_id)
            return
        recording = Recording(service_id, self.account_id, self.CAM_NUMS, self.mic_index, start_time)
        recording.start()
        self.current = recording
        self.service_id, self.start_time, self.end_time = service_id, start_time, None
        self.button_start_recording.setEnabled(False)
        self.button_stop_recording.setEnabled(True)
        self.microphone_row.start()
        self.poll_timer.start()
        self._refresh_status()
        self.recordingChanged.emit(True)

    @QtCore.Slot()
    def stop_recording(self):
        """停止錄影：交給 saver 在背景收尾、上傳、寫資料庫（UX-08）。GUI 執行緒不等，馬上可以錄下一段。"""
        recording = self.current
        if recording is None:
            return
        self.end_time = datetime.now()
        recording.stop(self.end_time)
        self.current = None
        self.last_recording = recording
        self.poll_timer.stop()
        self.microphone_row.stop()
        self.button_start_recording.setEnabled(True)
        self.button_stop_recording.setEnabled(False)
        self.saver.add(recording)
        self._refresh_status()
        self.recordingChanged.emit(False)

    def closeEvent(self, event):
        """關掉視窗：錄影中的先停止並存檔（存檔在背景繼續），再關鏡頭（UX-09）。"""
        self.stop_recording()
        self._release_cameras()
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()

    def _release_cameras(self):
        for timer in self.timer_cameras:
            timer.stop()
        for capture in self.caps:
            capture.release()

    @QtCore.Slot()
    def _on_about_to_quit(self):
        """結束程式：錄影中的先停止，等檔案寫完再讓程式結束（上傳會被取消，檔案留在這台電腦上）。"""
        recording = self.current
        self.stop_recording()
        if recording is not None:
            recording.wait(QUIT_WAIT_S)
        self._release_cameras()

    # ------------------------------------------------------------ 狀態列（DS-34）

    @QtCore.Slot(str)
    def _on_save_changed(self, service_id):
        if self.current is None and self.last_recording is not None and service_id == self.last_recording.service_id:
            self._refresh_status()

    def _refresh_status(self):
        bar = self.status_bar
        recording = self.current
        if recording is not None:
            bar.show_state("recording", "dot", COLORS["recording"], _tr("Recording"), recording.service_id,
                           elapsed=format_elapsed(recording.elapsed()))
            self._announce(("recording", recording.service_id), _tr("Recording %1", recording.service_id))
            return
        job = self.saver.job(self.last_recording.service_id) if self.last_recording is not None else None
        if job is None:
            cam0, cam1 = self.camera_names
            bar.show_state("ready", "circle", COLORS["secondary"], _tr("Ready to record"),
                           _tr("Customer camera: %1 · Staff camera: %2 · Microphone: %3", cam0, cam1,
                               self.microphone_name))
        elif job.state == FINISHING:
            bar.show_state("finishing", "loader", COLORS["secondary"], _tr("Saving recording…"), job.service_id,
                           progress="busy", still=reduce_motion(self.saver.services.ui_prefs))
        elif job.state == UPLOADING:
            file_text = _tr("File %1 of %2: %3", job.file_number, FILE_COUNT, job.file_name)
            amount = transfer_text(job.done, job.total)
            bar.show_state("uploading", "upload", COLORS["text"], job.title,
                           f"{amount} · {file_text}" if amount else file_text, progress=(job.done, job.total))
        elif job.state == SAVING:
            amount = transfer_text(job.total, job.total)
            detail = _tr("Saving to the database…")
            bar.show_state("saving", "upload", COLORS["text"], job.title, f"{amount} · {detail}" if amount else detail,
                           progress=(job.total, job.total))
        elif job.state == SAVED:
            uploaded = _tr("%1 files uploaded", FILE_COUNT)
            bar.show_state("saved", "circle-check", COLORS["success"], _tr("Saved %1", job.service_id), uploaded,
                           action=_tr("Analyze"), action_enabled=not job.analyze_requested)
            self._announce(("saved", job.service_id), f"{_tr('Saved %1', job.service_id)}. {uploaded}")
        else:
            bar.show_state("failed", "circle-x", COLORS["error"], job.error_title, job.error_detail,
                           action=_tr("Retry") if job.retryable else "")
            self._announce(("failed", job.service_id, job.attempt), f"{job.error_title}. {job.error_detail}",
                           assertive=True)

    @QtCore.Slot()
    def _on_status_action(self):
        job = self.saver.job(self.last_recording.service_id) if self.last_recording is not None else None
        if job is None or self.current is not None:
            return
        if job.state == SAVED:
            self.saver.request_analyze(job.service_id)
        elif job.state == FAILED:
            self.saver.retry(job.service_id)

    def _announce(self, key, text, assertive=False):
        """錄影開始、完成、失敗各唸一次（DS-34）。"""
        if key == self._announced_state:
            return
        self._announced_state = key
        self.announced.append((text, assertive))
        event = QtGui.QAccessibleAnnouncementEvent(self.status_bar, text)
        event.setPoliteness(QtGui.QAccessible.AnnouncementPoliteness.Assertive if assertive
                            else QtGui.QAccessible.AnnouncementPoliteness.Polite)
        QtGui.QAccessible.updateAccessibility(event)
