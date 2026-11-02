import pytest
from botocore.exceptions import ClientError

from myapp import storage
from myapp.tasks import Cancelled, CancelToken


def test_upload_returns_s3_url(tmp_path, s3):
    video = tmp_path / "S20261030120000_cam0.avi"
    video.write_bytes(b"fake video")
    url = storage.upload(str(video), "video/S20261030120000_cam0.avi")
    assert url == "s3://adam-test/video/S20261030120000_cam0.avi"
    # S3 記下檔案類型，之後用瀏覽器或 presigned URL 下載才不會出問題
    head = s3.head_object(Bucket="adam-test", Key="video/S20261030120000_cam0.avi")
    assert head["ContentType"] == "video/x-msvideo"


def test_download_returns_local_path(tmp_path, s3):
    audio = tmp_path / "S20261030120000_audio.wav"
    audio.write_bytes(b"fake audio")
    storage.upload(str(audio), "audio/S20261030120000_audio.wav")
    target = tmp_path / "downloaded.wav"
    assert storage.download("audio/S20261030120000_audio.wav", str(target)) == str(target)
    assert target.read_bytes() == b"fake audio"


def test_bucket_requires_env():
    with pytest.raises(RuntimeError):
        storage.bucket()


# ---- 背景工作用：有進度、可以取消（F3）----------------------------------------------------


def progress_log():
    """記下 report(增加的 bytes, 總量) 的每一次呼叫。"""
    calls = []
    return calls, lambda done, total: calls.append((done, total))


def test_upload_with_progress_reports_every_byte_and_keeps_content_type(tmp_path, s3):
    video = tmp_path / "S20261030120000_cam0.avi"
    video.write_bytes(b"x" * 300_000)
    calls, report = progress_log()
    url = storage.upload_with_progress(str(video), "video/S20261030120000_cam0.avi", report=report)
    assert url == "s3://adam-test/video/S20261030120000_cam0.avi"
    assert sum(done for done, _ in calls) == 300_000  # 增加量加起來就是整個檔案
    assert {total for _, total in calls} == {300_000}
    head = s3.head_object(Bucket="adam-test", Key="video/S20261030120000_cam0.avi")
    assert head["ContentType"] == "video/x-msvideo"


def test_download_with_progress_writes_through_a_part_file(tmp_path, s3):
    s3.put_object(Bucket="adam-test", Key="charts/S20261030120000_score.png", Body=b"p" * 200_000)
    target = tmp_path / "S20261030120000_score.png"
    calls, report = progress_log()
    assert storage.download_with_progress("charts/S20261030120000_score.png", str(target), report=report) == str(target)
    assert target.read_bytes() == b"p" * 200_000
    assert not (tmp_path / "S20261030120000_score.png.part").exists()
    assert sum(done for done, _ in calls) == 200_000
    assert calls[-1][1] == 200_000  # 下載的總量要等 HEAD 之後才知道，最後一定知道


def test_cancelled_transfers_raise_cancelled_and_leave_nothing_behind(tmp_path, s3):
    token = CancelToken()
    token.cancel()  # 使用者在工作開始前就按了取消
    video = tmp_path / "S20261030120000_cam1.avi"
    video.write_bytes(b"v" * 1000)
    with pytest.raises(Cancelled):
        storage.upload_with_progress(str(video), "video/S20261030120000_cam1.avi", token=token)
    assert s3.list_objects_v2(Bucket="adam-test").get("KeyCount", 0) == 0

    s3.put_object(Bucket="adam-test", Key="audio/S20261030120000_audio.wav", Body=b"a" * 1000)
    target = tmp_path / "S20261030120000_audio.wav"
    target.write_bytes(b"cached copy")  # 之前下載過的快取
    with pytest.raises(Cancelled):
        storage.download_with_progress("audio/S20261030120000_audio.wav", str(target), token=token)
    assert target.read_bytes() == b"cached copy"  # 沒有被蓋掉
    assert not (tmp_path / "S20261030120000_audio.wav.part").exists()


def test_failed_download_removes_the_part_file(tmp_path, s3):
    target = tmp_path / "missing.avi"
    with pytest.raises(ClientError):
        storage.download_with_progress("video/missing.avi", str(target))
    assert not target.exists() and not (tmp_path / "missing.avi.part").exists()
