"""點開一筆服務的同步版（ENG-21：MediaService.run 直接測得到）：從 S3 下載影片、音訊、圖表（步驟 1-5、3-9）。

畫面用的是背景版（MediaModel.open，見 test_media_flow.py）；兩者走同一套狀態。services 是 None 時
（直接建立 ServerHome 的呼叫端，例如 test_recording.py）load_service_media 也是走這一條，在呼叫的執行緒做完。
兩端共用 myapp/media.py，兩個 init 模組 import 的是同一份。
"""
import mysql.connector
import pytest
from PySide6.QtCore import QUrl

from myapp import media
from myapp.database import DatabaseConnector
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import STAFF_ID

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


def show(module, service_id, media_model=None, charts=None):
    media_model = media_model or module.MediaModel()
    charts = charts or module.ChartsModel()
    module.MediaService(service_id, DatabaseConnector()).run(media_model, charts)
    return media_model, charts


def local_url(tmp_path, folder, key):
    return QUrl.fromLocalFile(str(tmp_path / folder / key.split("/")[-1])).toString()


def states(media_model):
    return [media_model.slot(kind).state for kind in ("cam0", "cam1", "audio")]


def test_both_sides_share_one_implementation():
    for name in ("MediaService", "MediaModel", "ChartsModel", "PDFHandler"):
        assert getattr(server_home_init, name) is getattr(admin_home_init, name) is getattr(media, name)


@BOTH_COPIES
def test_viewer_downloads_media_and_charts_in_database_order(module, db, s3, tmp_path):
    add_service_with_files(db, s3, MEDIA_KEYS + CHART_KEYS)
    media_model, charts = show(module, SID)  # 同步：回來時已經下載完
    assert (media_model.state, states(media_model)) == ("ready", ["ready"] * 3)
    assert [media_model.cam0Source, media_model.cam1Source, media_model.audioSource] == [
        local_url(tmp_path, "media", key) for key in MEDIA_KEYS]
    assert charts.chartsSources == [local_url(tmp_path, "charts", key) for key in CHART_KEYS]
    assert (tmp_path / "charts" / f"{SID}_01_summary.png").read_bytes() == CHART_KEYS[1].encode()
    assert media_model.canPlay


@BOTH_COPIES
def test_viewer_clears_previous_service_when_next_one_has_no_files(module, db, s3):
    add_service_with_files(db, s3, MEDIA_KEYS + CHART_KEYS)
    db.add_service("S20261030130000", "2026-10-30 13:00:00")  # 沒有任何檔案紀錄
    media_model, charts = show(module, SID)
    show(module, "S20261030130000", media_model, charts)  # 同一個畫面接著點另一筆
    assert [media_model.cam0Source, media_model.cam1Source, media_model.audioSource, charts.chartsSources] == [
        "", "", "", []]
    assert states(media_model) == ["missing"] * 3 and charts.state == "empty"  # 說清楚是「沒有」，不是空白
    assert media_model.serviceId == "S20261030130000"


@BOTH_COPIES
def test_viewer_marks_files_missing_from_s3_as_failed(module, db, s3, tmp_path):
    add_service_with_files(db, s3, [f"video/{SID}_cam1.avi"])  # 只有員工鏡頭的檔案真的在 S3
    media_model, charts = show(module, SID)
    assert [media_model.cam0Source, media_model.cam1Source, media_model.audioSource] == [
        "", local_url(tmp_path, "media", f"video/{SID}_cam1.avi"), ""]
    assert states(media_model) == ["error", "ready", "error"]  # 以前只 print，畫面上看起來像沒有這個檔案
    assert charts.chartsSources == [] and (charts.state, charts.failedCount) == ("error", 2)


def test_viewer_reports_a_listing_failure_and_can_retry():
    class Unreachable:  # 舊的共用連線連不上資料庫
        @property
        def connection(self):
            raise mysql.connector.errors.InterfaceError(errno=2003, msg="Can't connect to MySQL server")

    media_model, charts = media.MediaModel(), media.ChartsModel()
    media.MediaService(SID, Unreachable()).run(media_model, charts)
    assert (media_model.state, media_model.failureCount, media_model.busy) == ("error", 1, False)
    assert media_model.errorDetail == "Can't reach the database. Check your network and try again."
    assert media_model.errorTechnical.startswith("InterfaceError")
    assert charts.state == "loading"  # 清單沒查到：圖表也還不知道有幾張（畫面顯示整頁的錯誤）
    assert media_model.reload()  # Retry：再查一次（還是連不上）
    assert media_model.failureCount == 2
    assert not media.MediaModel().reload()  # 沒有錯誤時不重試


def test_the_synchronous_path_stops_following_background_progress(db, s3, services, tmp_path):
    """先用過背景版（有 services）的模型，再走同步版：不再接 TaskRunner 的進度，結果照樣寫進同一個模型。"""
    add_service_with_files(db, s3, MEDIA_KEYS + CHART_KEYS)
    media_model, charts = media.MediaModel(services), media.ChartsModel()
    show(server_home_init, SID, media_model, charts)
    services.tasks.progressed.emit(media.task_key(SID, f"{SID}_cam0.avi"), 1.0, 2.0)  # 沒有人在聽了
    assert (media_model.slot("cam0").done, media_model.slot("cam0").total) == (0.0, 0.0)
    assert media_model.canPlay and charts.readyCount == 2


def test_staff_home_without_services_loads_synchronously(db, s3, tmp_path):
    """直接建立的 ServerHome（沒有 services）：load_service_media 照舊同步，回來時就能播放（test_recording.py 用這個）。"""
    add_service_with_files(db, s3, MEDIA_KEYS + CHART_KEYS)
    home = server_home_init.ServerHome(STAFF_ID)
    home.load_service_media(SID)
    assert home.mediaModel.cam0Source == local_url(tmp_path, "media", MEDIA_KEYS[0])
    assert home.chartsModel.readyCount == 2
    home.clearDir()
    assert list((tmp_path / "media").iterdir()) == []


def test_the_synchronous_path_also_fills_the_header(db, s3):
    """頁首和分數列（新版介面 U2，T31）在同步版也查得到：舊的共用連線是 tuple 的 cursor，用欄位名稱對應。"""
    db.add_service(SID, "2026-10-30 12:00:00", status=1)
    db.add_media(SID, "audio", f"s3://adam-test/{MEDIA_KEYS[2]}")
    s3.put_object(Bucket="adam-test", Key=MEDIA_KEYS[2], Body=b"audio")
    media_model, charts = show(server_home_init, SID)
    assert media_model.headerText == "Oct 30, 2026 · 12:00–12:05 · Not analyzed"
    assert media_model.staffName == "服務人員（佔位）" and media_model.serviceFound
    assert media_model.slot("audio").state == "ready" and charts.state == "empty"
