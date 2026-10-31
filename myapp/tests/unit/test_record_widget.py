"""錄影視窗（步驟 1-4、2-3、3-1～3-5）。鏡頭、麥克風、時鐘、資料庫、S3 上傳都是假的。"""
import threading
import wave
from datetime import datetime, timedelta

import cv2
import mysql.connector
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMessageBox

from myapp import storage
from myapp.init import record_widget
from myapp.init.record_widget import RecordWidget
from tests.fakes import FakeDatabase, record, ticking_clock

REAL_VIDEO_CAPTURE = cv2.VideoCapture  # 讀錄好的 .avi 用真的；cameras fixture 會把 cv2.VideoCapture 換掉


@pytest.fixture
def database(monkeypatch):
    """錄影視窗自己開的資料庫連線：查帳號名稱會得到「王小明」。"""
    fake = FakeDatabase(rows=[("王小明",)])
    monkeypatch.setattr(mysql.connector, "connect", fake.connect)
    return fake


@pytest.fixture
def uploads(monkeypatch):
    """假的 S3 上傳：依序記下 key。"""
    keys = []

    def fake_upload(local_path, key):
        keys.append(key)
        return f"s3://adam-test/{key}"

    monkeypatch.setattr(storage, "upload", fake_upload)
    return keys


@pytest.fixture
def open_window(monkeypatch, cameras, microphone, database, uploads):
    """回傳一個「開錄影視窗」的函式；測試結束時關掉所有視窗。"""
    monkeypatch.setattr(record_widget, "datetime", ticking_clock())
    windows = []

    def open_record_window():
        window = RecordWidget(0, 1, 0, "acc-1")  # 顧客鏡頭 0、員工鏡頭 1、麥克風 0
        windows.append(window)
        return window

    yield open_record_window
    for window in windows:
        for timer in window.timer_cameras:
            timer.stop()
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def widget(open_window):
    return open_window()


def test_status_from_recording_thread_is_shown_by_gui_thread(widget):
    worker = threading.Thread(target=widget.update_status, args=("Recording...",))
    worker.start()
    worker.join()
    assert widget.status_bar.text() == "Ready for 王小明"  # 還排在主執行緒的事件佇列裡
    QCoreApplication.processEvents()
    assert widget.status_bar.text() == "Recording..."


def test_status_ends_with_saved_message(widget, cameras, microphone):
    service_id = record(widget, cameras, microphone)
    # 錄影執行緒最後送出的 "Camera recording stopped." 不能蓋掉 "Saved ..."
    assert widget.status_bar.text() == f"Saved {service_id}"


def test_each_recording_uploads_its_own_files_under_one_service_id(widget, cameras, microphone, uploads):
    service_ids = []
    for _ in range(2):  # 同一個視窗連錄兩段
        service_ids.append(record(widget, cameras, microphone))
        # service_id 在按下「開始」時產生一次（和 start_time 同一秒）；錄音執行緒不能再產生一個
        started = datetime.strptime(service_ids[-1], "S%Y%m%d%H%M%S")
        assert abs(started - widget.start_time) <= timedelta(seconds=1)
    first, second = service_ids
    assert first != second
    assert uploads == [
        f"video/{first}_cam0.avi", f"video/{first}_cam1.avi", f"audio/{first}_audio.wav",
        f"video/{second}_cam0.avi", f"video/{second}_cam1.avi", f"audio/{second}_audio.wav",
    ]


def test_microphone_overflow_does_not_stop_recording(widget, cameras, microphone):
    microphone.overflow_at = 2  # 第 2 段資料讀取前，麥克風的緩衝區滿了
    service_id = record(widget, cameras, microphone)
    with wave.open(f"audio/{service_id}_audio.wav") as wav:
        assert wav.getnframes() == 3 * 1024  # 3 段都在


def test_frames_are_resized_to_640x480(widget, cameras, microphone):
    service_id = record(widget, cameras, microphone)  # 假鏡頭給的是 1280x720
    for camera in ("cam0", "cam1"):
        video = REAL_VIDEO_CAPTURE(f"video/{service_id}_{camera}.avi")
        shapes = []
        ok, frame = video.read()
        while ok:
            shapes.append(frame.shape)
            ok, frame = video.read()
        video.release()
        assert shapes == [(480, 640, 3)] * 3  # 尺寸不符的影格會被 OpenCV 直接丟掉


def test_nothing_is_saved_when_microphone_fails(widget, cameras, microphone, database, monkeypatch):
    microphone.broken = True
    thread_errors = []
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)  # 錄音執行緒會因此結束
    service_id = record(widget, cameras, microphone)
    assert widget.status_bar.text() == f"Upload failed, {service_id} was not saved"
    assert not [name for name, _ in database.calls if name.startswith("sp_insert")]
    assert [error.exc_type for error in thread_errors] == [OSError]


def test_nothing_is_saved_when_s3_upload_fails(widget, cameras, microphone, database, monkeypatch):
    def s3_down(local_path, key):
        raise ConnectionError("S3 unreachable (fake)")

    monkeypatch.setattr(storage, "upload", s3_down)
    service_id = record(widget, cameras, microphone)
    assert widget.status_bar.text() == f"Upload failed, {service_id} was not saved"
    assert not [name for name, _ in database.calls if name.startswith("sp_insert")]


def test_database_error_is_reported(widget, cameras, microphone, database):
    database.fail_on = "sp_insert_audio_storage"
    service_id = record(widget, cameras, microphone)
    assert widget.status_bar.text() == f"Database error, {service_id} was not saved"


def test_camera_preview_fits_label(widget):
    widget.show_camera(0, 0)
    assert widget.label_cameras[0].pixmap().size().toTuple() == (480, 360)


def test_missing_camera_shows_warning(open_window, cameras, monkeypatch):
    cameras.connected = (0,)  # 員工鏡頭（1 號）沒接
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, **kwargs: warnings.append(text))
    widget = open_window()
    assert warnings == ["請檢查相機 1 是否連接正確"]
    assert [timer.isActive() for timer in widget.timer_cameras] == [True, False]
