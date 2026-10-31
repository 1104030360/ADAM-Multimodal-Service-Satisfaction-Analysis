"""點開一筆服務：從 S3 下載影片、音訊、圖表（步驟 1-5、3-9）。服務人員端、管理員端各有一份 MediaService。"""
import pytest
from PySide6.QtCore import QUrl

from myapp.database import DatabaseConnector
from myapp.init import admin_home_init, server_home_init

pytestmark = pytest.mark.integration

BOTH_COPIES = pytest.mark.parametrize("module", [server_home_init, admin_home_init], ids=["staff", "admin"])
SID = "S20261030120000"
MEDIA_KEYS = [f"video/{SID}_cam0.avi", f"video/{SID}_cam1.avi", f"audio/{SID}_audio.wav"]
CHART_KEYS = [f"charts/{SID}_02_audio_level.png", f"charts/{SID}_01_summary.png"]  # 分析服務寫入的順序


def add_service_with_files(db, s3, keys):
    """資料庫記錄 MEDIA_KEYS 和 CHART_KEYS；S3 上只有 keys 這幾個檔案。"""
    db.add_service(SID, "2026-10-30 12:00:00")
    for key in MEDIA_KEYS + CHART_KEYS:
        db.add_media(SID, key.split("/")[0], f"s3://adam-test/{key}")
    for key in keys:
        s3.put_object(Bucket="adam-test", Key=key, Body=key.encode())


def show(module, service_id, media=None, charts=None):
    media = media or module.MediaModel()
    charts = charts or module.ChartsModel()
    module.MediaService(service_id, DatabaseConnector()).run(media, charts)
    return media, charts


def local_url(tmp_path, folder, key):
    return QUrl.fromLocalFile(str(tmp_path / folder / key.split("/")[-1])).toString()


@BOTH_COPIES
def test_viewer_downloads_media_and_charts_in_database_order(module, db, s3, tmp_path):
    add_service_with_files(db, s3, MEDIA_KEYS + CHART_KEYS)
    media, charts = show(module, SID)
    assert [media.cam0Source, media.cam1Source, media.audioSource] == [
        local_url(tmp_path, "media", key) for key in MEDIA_KEYS]
    assert charts.chartsSources == [local_url(tmp_path, "charts", key) for key in CHART_KEYS]
    assert (tmp_path / "charts" / f"{SID}_01_summary.png").read_bytes() == CHART_KEYS[1].encode()


@BOTH_COPIES
def test_viewer_clears_previous_service_when_next_one_has_no_files(module, db, s3):
    add_service_with_files(db, s3, MEDIA_KEYS + CHART_KEYS)
    db.add_service("S20261030130000", "2026-10-30 13:00:00")  # 沒有任何檔案紀錄
    media, charts = show(module, SID)
    show(module, "S20261030130000", media, charts)  # 同一個畫面接著點另一筆
    assert [media.cam0Source, media.cam1Source, media.audioSource, charts.chartsSources] == ["", "", "", []]


@BOTH_COPIES
def test_viewer_leaves_out_files_missing_from_s3(module, db, s3, tmp_path):
    add_service_with_files(db, s3, [f"video/{SID}_cam1.avi"])  # 只有員工鏡頭的檔案真的在 S3
    media, charts = show(module, SID)
    assert [media.cam0Source, media.cam1Source, media.audioSource] == [
        "", local_url(tmp_path, "media", f"video/{SID}_cam1.avi"), ""]
    assert charts.chartsSources == []
