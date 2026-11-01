"""測試替身（test double）：取代鏡頭、麥克風、時鐘與 MySQL 連線，測試不碰真的裝置。"""
import itertools
import threading
import time
from datetime import datetime, timedelta

import mysql.connector
import numpy as np
from PySide6.QtCore import QObject, Signal


class FakeCameras:
    """取代 cv2.VideoCapture，也取代 AVFoundation 的鏡頭清單和相機權限（devices.av_video_devices、media_access）。

    每支鏡頭只給 frames 張畫面，之後就回報「讀不到」，所以錄到的張數是固定的。
    畫面尺寸故意不是 640x480，用來檢查 3-4 的 resize。

    裝置設定（T41）用到的：
    - listed：AVFoundation 列出的 [(uniqueID, 名稱)]，順序就是 OpenCV 的編號。"auto"（預設）是 0 號到
      connected 最大的編號都列得出來，名稱「Camera n」、uniqueID「fake-camera-n」；None 是列不出來（不是 macOS）。
      connected 是其中打得開的編號：列得出來、打不開的就是「別的 app 正在用」。
    - access：相機權限（devices.ACCESS_*）。frame_interval：每張畫面之間等幾秒（預覽測試不要空轉）。
    - broken_read：讀畫面時丟的例外（鏡頭中途出錯）。live()：還沒釋放的鏡頭編號。
    """

    def __init__(self, connected=(0, 1), frames=3, size=(1280, 720), listed="auto", access="granted",
                 frame_interval=0.0):
        self.connected = connected  # 接著的鏡頭編號
        self.frames = frames
        self.size = size  # (寬, 高)
        self.frames_read = 0  # 所有鏡頭總共給出幾張畫面
        self.listed = listed
        self.access = access
        self.frame_interval = frame_interval
        self.broken_read = None
        self.opened_on = []  # 每次打開成功時的 (編號, 執行緒名稱)
        self._live = []

    def __call__(self, index=None):  # 程式寫 cv2.VideoCapture(index) 時會呼叫這裡
        return _FakeCamera(self, index)

    def av_devices(self):
        """取代 devices.av_video_devices()。"""
        if self.listed is None:
            return None
        if self.listed == "auto":
            return [(f"fake-camera-{index}", f"Camera {index}") for index in range(max(self.connected, default=-1) + 1)]
        return list(self.listed)

    def live(self):
        """打開了、還沒釋放的鏡頭編號。"""
        return sorted(camera.index for camera in self._live)


class _FakeCamera:
    def __init__(self, rig, index):
        self.rig = rig
        self.index = index
        self.frames_left = rig.frames
        self._track()

    def _track(self):
        if self.isOpened() and self not in self.rig._live:
            self.rig._live.append(self)
            self.rig.opened_on.append((self.index, threading.current_thread().name))

    def open(self, index):
        self.index = index
        self._track()
        return self.isOpened()

    def isOpened(self):
        return self.index in self.rig.connected

    def set(self, prop, value):
        return True

    def read(self):
        if self.rig.broken_read is not None:
            raise self.rig.broken_read
        if self.rig.frame_interval:
            time.sleep(self.rig.frame_interval)
        if self.frames_left == 0:
            time.sleep(0.001)  # 沒畫面了；稍微等一下，不要讓錄影執行緒空轉
            return False, None
        self.frames_left -= 1
        self.rig.frames_read += 1
        width, height = self.rig.size
        return True, np.full((height, width, 3), 128, np.uint8)

    def release(self):
        if self in self.rig._live:
            self.rig._live.remove(self)


class FakeMicrophone:
    """取代 pyaudio.PyAudio，也取代麥克風權限（devices.media_access("audio")）。

    devices：(名稱, 輸入聲道數) 的清單。每次錄音給 chunks 段資料。
    overflow_at：第幾段資料要模擬緩衝區溢位（3-3）。broken：開啟麥克風就失敗。
    裝置設定（T41）用到的：access（麥克風權限）、host_api（host API 的名稱）、sample（每個樣本的值，決定音量）、
    chunk_interval（每段之間等幾秒）、open_streams()（還沒關的錄音）、terminated（terminate() 的次數）。
    """

    def __init__(self, devices=(("MacBook 麥克風", 1),), chunks=3, overflow_at=None, broken=False, access="granted",
                 host_api="Core Audio", sample=1, chunk_interval=0.0):
        self.devices = list(devices)
        self.chunks = chunks
        self.overflow_at = overflow_at
        self.broken = broken
        self.access = access
        self.host_api = host_api
        self.sample = sample
        self.chunk_interval = chunk_interval
        self.chunks_read = 0  # 總共給出幾段資料
        self.opened_with = None  # 最後一次 open() 的參數
        self.terminated = 0
        self.streams = []

    def __call__(self):  # 程式寫 pyaudio.PyAudio() 時會呼叫這裡
        return self

    def open(self, **kwargs):
        self.opened_with = kwargs
        if self.broken:
            raise OSError(-9996, "Invalid input device (fake)")
        stream = _FakeStream(self)
        self.streams.append(stream)
        return stream

    def open_streams(self):
        return [stream for stream in self.streams if not stream.closed]

    def get_sample_size(self, sample_format):
        return 2  # paInt16

    def terminate(self):
        self.terminated += 1

    def get_host_api_info_by_index(self, index):
        return {"deviceCount": len(self.devices), "name": self.host_api}

    def get_device_info_by_host_api_device_index(self, host_api, index):
        name, input_channels = self.devices[index]
        return {"index": index, "name": name, "maxInputChannels": input_channels}


class _FakeStream:
    def __init__(self, microphone):
        self.microphone = microphone
        self.reads = 0
        self.closed = False

    def read(self, chunk, exception_on_overflow=True):
        if self.microphone.chunk_interval:
            time.sleep(self.microphone.chunk_interval)
        if self.reads == self.microphone.chunks:
            time.sleep(0.001)
            return b""
        self.reads += 1
        if self.reads == self.microphone.overflow_at and exception_on_overflow:
            raise OSError(-9981, "Input overflowed")  # PyAudio 預設遇到溢位就丟例外
        self.microphone.chunks_read += 1
        return int(self.microphone.sample).to_bytes(2, "little", signed=True) * chunk  # chunk 個 16-bit 樣本

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


def ticking_clock(start=datetime(2026, 10, 30, 12, 0, 0)):
    """取代 datetime：每呼叫一次 now() 就往後走 1 秒，service_id 是固定、可預期的值。"""
    ticks = (start + timedelta(seconds=n) for n in itertools.count())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(ticks)

    return Clock


class FakeDatabase:
    """取代 mysql.connector.connect()：記下每一句 SQL／預存程序，可以指定查詢結果或讓某一句失敗。"""

    def __init__(self, rows=(), fail_on=None, error=None):
        self.rows = list(rows)  # fetchone()／fetchall() 回傳的資料
        self.fail_on = fail_on  # SQL 或預存程序名稱裡含有這段文字就丟 mysql.connector.Error
        self.error = error  # fail_on 那一句丟的例外（例如帶 errno 的）；None 是沒有 errno 的 mysql.connector.Error
        self.calls = []  # (SQL 或預存程序名稱, 參數)
        self.connections = []

    def connect(self, **config):
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection


class FakeConnection:
    def __init__(self, database=None, ping_error=None):
        self.database = database or FakeDatabase()
        self.ping_error = ping_error
        self.committed = self.rolled_back = self.closed = False
        self.in_transaction = False  # 真的連線在 START TRANSACTION 之後是 True
        self.cursors_opened = 0

    def cursor(self, dictionary=False, buffered=None):
        self.cursors_opened += 1
        return _FakeCursor(self.database)

    def ping(self, **kwargs):
        if self.ping_error:
            raise self.ping_error

    def is_connected(self):
        return not self.closed

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, database):
        self.database = database

    def execute(self, statement, params=()):
        self._run(statement, params)

    def callproc(self, name, args=()):
        self._run(name, args)

    def _run(self, statement, params):
        self.database.calls.append((statement, params))
        if self.database.fail_on and self.database.fail_on in statement:
            raise self.database.error or mysql.connector.Error(f"fake error in {statement}")

    def stored_results(self):  # 預存程序的結果集
        return [self]

    def nextset(self):  # 沒有下一個結果集
        return None

    def fetchone(self):
        return self.database.rows[0] if self.database.rows else None

    def fetchall(self):
        return list(self.database.rows)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class FakeSaver(QObject):
    """錄影頁的 Saving 清單（SavesModel）會用到的 RecordingSaver 介面：changed、jobs()、retry()、request_analyze()。
    jobs 是 save_job() 做的 SaveJob；改了之後 emit changed，清單就重排。"""

    changed = Signal(str)

    def __init__(self, jobs=()):
        super().__init__()
        self.job_list = list(jobs)
        self.calls = []

    def jobs(self):
        return list(self.job_list)

    def retry(self, service_id):
        self.calls.append(("retry", service_id))
        return True

    def request_analyze(self, service_id):
        self.calls.append(("analyze", service_id))
        return True


def save_job(service_id, state, **fields):
    """一段錄影的存檔（SaveJob），狀態和欄位直接指定；檔案不必存在。"""
    from myapp.init.record_widget import Recording, SaveJob  # 用到才 import：裡面有 cv2、PyAudio
    from myapp.pending_saves import PendingSave

    start = datetime(2026, 10, 30, 12, 0, 0)
    files = ("/x/video/a.avi", "/x/video/b.avi", "/x/audio/c.wav")
    return SaveJob(Recording.restored(PendingSave(service_id, "acc-1", files, start, None, "upload")), state=state,
                   **fields)


def every_save_status():
    """錄影頁 Saving 卡片的每一種列（新的在前）：收尾中、上傳中（第 2 次）、寫資料庫、存好了、已送去分析、
    沒存好（可以重試）、存不了（檔案不見了）。"""
    from myapp.init.record_widget import FAILED, FINISHING, SAVED, SAVING, UPLOADING

    uploaded = ["s3://adam-test/a", "s3://adam-test/b", "s3://adam-test/c"]
    return [
        save_job("S20261101150000", FINISHING),
        save_job("S20261101142000", UPLOADING, attempt=2, done=58.6e6, total=124.0e6, sizes=[55e6, 55e6, 14e6]),
        save_job("S20261101141000", SAVING, done=124.0e6, total=124.0e6, urls=uploaded),
        save_job("S20261101140500", SAVED, done=124.0e6, total=124.0e6, urls=uploaded,
                 saved_at=datetime(2026, 11, 1, 14, 12)),
        save_job("S20261101130000", SAVED, urls=uploaded, saved_at=datetime(2026, 11, 1, 13, 7),
                 analyze_requested=True),
        save_job("S20261031170500", FAILED,
                 error_detail="The upload didn't finish. The recording is still on this computer."),
        save_job("S20261031160000", FAILED, retryable=False,
                 error_detail="The recording is no longer on this computer. It may have been moved or deleted."),
    ]
