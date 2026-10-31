import pytest

from myapp import storage


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
