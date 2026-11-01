"""測試替身（test double）：取代鏡頭、麥克風、時鐘與 MySQL 連線，測試不碰真的裝置。"""
import itertools
import time
from datetime import datetime, timedelta

import mysql.connector
import numpy as np


class FakeCameras:
    """取代 cv2.VideoCapture。

    每支鏡頭只給 frames 張畫面，之後就回報「讀不到」，所以錄到的張數是固定的。
    畫面尺寸故意不是 640x480，用來檢查 3-4 的 resize。
    """

    def __init__(self, connected=(0, 1), frames=3, size=(1280, 720)):
        self.connected = connected  # 接著的鏡頭編號
        self.frames = frames
        self.size = size  # (寬, 高)
        self.frames_read = 0  # 所有鏡頭總共給出幾張畫面

    def __call__(self, index=None):  # 程式寫 cv2.VideoCapture(index) 時會呼叫這裡
        return _FakeCamera(self, index)


class _FakeCamera:
    def __init__(self, rig, index):
        self.rig = rig
        self.index = index
        self.frames_left = rig.frames

    def open(self, index):
        self.index = index
        return self.isOpened()

    def isOpened(self):
        return self.index in self.rig.connected

    def set(self, prop, value):
        return True

    def read(self):
        if self.frames_left == 0:
            time.sleep(0.001)  # 沒畫面了；稍微等一下，不要讓錄影執行緒空轉
            return False, None
        self.frames_left -= 1
        self.rig.frames_read += 1
        width, height = self.rig.size
        return True, np.full((height, width, 3), 128, np.uint8)

    def release(self):
        pass


class FakeMicrophone:
    """取代 pyaudio.PyAudio。

    devices：(名稱, 輸入聲道數) 的清單。每次錄音給 chunks 段資料。
    overflow_at：第幾段資料要模擬緩衝區溢位（3-3）。broken：開啟麥克風就失敗。
    """

    def __init__(self, devices=(("MacBook 麥克風", 1),), chunks=3, overflow_at=None, broken=False):
        self.devices = list(devices)
        self.chunks = chunks
        self.overflow_at = overflow_at
        self.broken = broken
        self.chunks_read = 0  # 總共給出幾段資料
        self.opened_with = None  # 最後一次 open() 的參數

    def __call__(self):  # 程式寫 pyaudio.PyAudio() 時會呼叫這裡
        return self

    def open(self, **kwargs):
        self.opened_with = kwargs
        if self.broken:
            raise OSError(-9996, "Invalid input device (fake)")
        return _FakeStream(self)

    def get_sample_size(self, sample_format):
        return 2  # paInt16

    def terminate(self):
        pass

    def get_host_api_info_by_index(self, index):
        return {"deviceCount": len(self.devices)}

    def get_device_info_by_host_api_device_index(self, host_api, index):
        name, input_channels = self.devices[index]
        return {"index": index, "name": name, "maxInputChannels": input_channels}


class _FakeStream:
    def __init__(self, microphone):
        self.microphone = microphone
        self.reads = 0

    def read(self, chunk, exception_on_overflow=True):
        if self.reads == self.microphone.chunks:
            time.sleep(0.001)
            return b""
        self.reads += 1
        if self.reads == self.microphone.overflow_at and exception_on_overflow:
            raise OSError(-9981, "Input overflowed")  # PyAudio 預設遇到溢位就丟例外
        self.microphone.chunks_read += 1
        return b"\x01\x00" * chunk  # chunk 個 16-bit 樣本

    def stop_stream(self):
        pass

    def close(self):
        pass


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
