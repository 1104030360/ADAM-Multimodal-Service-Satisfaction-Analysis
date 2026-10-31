"""錄影 → 上傳 S3 → 寫入資料庫 → 點開可以播放（步驟 1-4、2-3、3-5）。鏡頭和麥克風是假的。"""
from datetime import datetime

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl

from myapp.init import record_widget
from myapp.init.record_widget import RecordWidget
from myapp.init.server_home_init import ServerHome
from tests.fakes import record, ticking_clock
from tests.integration.helpers import STAFF_ID, STAFF_NAME

pytestmark = pytest.mark.integration


@pytest.fixture
def recorder(db, s3, monkeypatch):
    monkeypatch.setattr(record_widget, "datetime", ticking_clock())
    widget = RecordWidget(0, 1, 0, STAFF_ID)
    yield widget
    for timer in widget.timer_cameras:
        timer.stop()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_recording_is_uploaded_saved_and_playable(recorder, db, s3, cameras, microphone, tmp_path):
    assert recorder.status_bar.text() == f"Ready for {STAFF_NAME}"
    sid = record(recorder, cameras, microphone)

    objects = s3.list_objects_v2(Bucket="adam-test")["Contents"]
    assert sorted(obj["Key"] for obj in objects) == [
        f"audio/{sid}_audio.wav", f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi"]
    assert db.rows("SELECT account_id, service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [
        (STAFF_ID, 1)]  # 1 = 已上傳，等著送分析
    assert db.rows("SELECT video_name, video_url FROM tbl_video_storage ORDER BY video_id") == [
        (f"{sid}_cam0.avi", f"s3://adam-test/video/{sid}_cam0.avi"),
        (f"{sid}_cam1.avi", f"s3://adam-test/video/{sid}_cam1.avi")]
    assert db.rows("SELECT audio_url FROM tbl_audio_storage") == [(f"s3://adam-test/audio/{sid}_audio.wav",)]

    # 服務頁點開這筆：從 S3 下載的就是剛剛錄的檔案
    home = ServerHome(STAFF_ID)
    home.load_service_media(sid)
    downloaded = tmp_path / "media" / f"{sid}_cam0.avi"
    assert home.mediaModel.cam0Source == QUrl.fromLocalFile(str(downloaded)).toString()
    assert downloaded.read_bytes() == (tmp_path / "video" / f"{sid}_cam0.avi").read_bytes()
    assert home.mediaModel.audioSource.endswith(f"/media/{sid}_audio.wav")


def test_database_writes_are_all_or_nothing(recorder, db):
    recorder.service_id = "S20261030120000"
    recorder.start_time = recorder.end_time = datetime(2026, 10, 30, 12, 0, 0)
    videos = ["s3://adam-test/video/S20261030120000_cam0.avi", "s3://adam-test/video/S20261030120000_cam1.avi"]
    too_long = "s3://adam-test/audio/" + "x" * 1100  # audio_url 最多 1024 字：最後一個預存程序會失敗
    assert recorder.update_database(videos, too_long) is False
    # 前面已經寫入的服務和兩支影片也一起取消，不會留下半筆資料
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_video_storage") == [(0,)]
