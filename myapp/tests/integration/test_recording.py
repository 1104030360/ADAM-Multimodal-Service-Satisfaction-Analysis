"""錄影 → 上傳 S3 → 寫入資料庫（步驟 1-4、2-3、3-5；F3 起在背景做）。鏡頭和麥克風是假的，S3 是 moto。

錄影視窗、外殼、通知的整段流程在 test_upload_flow.py；這裡只確認寫進 S3 和資料庫的內容。
"""
from datetime import datetime

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from myapp.init import record_widget
from myapp.init.record_widget import SAVED, Recording, RecordingSaver, RecordWidget, save_recording
from tests.fakes import ticking_clock
from tests.integration.helpers import STAFF_ID, wait_until

pytestmark = pytest.mark.integration


@pytest.fixture
def recorder(services, s3, monkeypatch):
    monkeypatch.setattr(record_widget, "datetime", ticking_clock())
    widget = RecordWidget(0, 1, 0, STAFF_ID, saver=RecordingSaver(services))
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def record(widget):
    widget.start_recording()
    recording = widget.current
    try:
        wait_until(lambda: recording.frames_written >= 3 and recording.audio_chunks >= 3)
    finally:
        widget.stop_recording()
    return recording


def test_recording_is_uploaded_and_saved(recorder, db, s3):
    recording = record(recorder)
    sid = recording.service_id
    wait_until(lambda: recorder.saver.job(sid).state == SAVED)

    objects = s3.list_objects_v2(Bucket="adam-test")["Contents"]
    assert sorted(obj["Key"] for obj in objects) == [
        f"audio/{sid}_audio.wav", f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi"]
    for path, key in zip(recording.files, recording.keys):  # S3 上的就是剛剛錄的檔案
        with open(path, "rb") as local:
            assert s3.get_object(Bucket="adam-test", Key=key)["Body"].read() == local.read()
    assert db.rows("SELECT account_id, service_status, service_start, service_end FROM tbl_service "
                   "WHERE service_id = %s", (sid,)) == [(STAFF_ID, 1, recording.start_time, recording.end_time)]
    assert db.rows("SELECT video_name, video_url FROM tbl_video_storage ORDER BY video_id") == [
        (f"{sid}_cam0.avi", f"s3://adam-test/video/{sid}_cam0.avi"),
        (f"{sid}_cam1.avi", f"s3://adam-test/video/{sid}_cam1.avi")]
    assert db.rows("SELECT audio_name, audio_url FROM tbl_audio_storage") == [
        (f"{sid}_audio.wav", f"s3://adam-test/audio/{sid}_audio.wav")]


def test_database_writes_are_all_or_nothing(services, db):
    recording = Recording("S20261030120000", STAFF_ID, (0, 1), 0, datetime(2026, 10, 30, 12, 0, 0))
    recording.end_time = datetime(2026, 10, 30, 12, 5, 0)
    too_long = "s3://adam-test/audio/" + "x" * 1100  # audio_url 最多 1024 字：最後一個預存程序會失敗
    urls = ["s3://adam-test/video/S20261030120000_cam0.avi", "s3://adam-test/video/S20261030120000_cam1.avi",
            too_long]
    errors = []
    services.tasks.submit("db", "test:save", lambda token, report: save_recording(services.db, recording, urls,
                                                                                  recording.end_time),
                          on_err=errors.append)
    wait_until(lambda: errors)
    # 前面已經寫入的服務和兩支影片也一起取消，不會留下半筆資料
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_video_storage") == [(0,)]

    done = []  # 交易結束了：同一條連線還能用，下一次照常寫入
    urls[2] = "s3://adam-test/audio/S20261030120000_audio.wav"
    services.tasks.submit("db", "test:save", lambda token, report: save_recording(services.db, recording, urls,
                                                                                  recording.end_time),
                          on_ok=done.append)
    wait_until(lambda: done)
    assert db.rows("SELECT COUNT(*) FROM tbl_video_storage") == [(2,)]
