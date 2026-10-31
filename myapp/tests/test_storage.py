import boto3
import pytest
from moto import mock_aws

from myapp import storage


@pytest.fixture
def s3_bucket(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "adam-test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    storage.s3_client.cache_clear()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="adam-test")
        yield
    storage.s3_client.cache_clear()


def test_upload_returns_s3_url(tmp_path, s3_bucket):
    video = tmp_path / "S20261030120000_cam0.avi"
    video.write_bytes(b"fake video")
    url = storage.upload(str(video), "video/S20261030120000_cam0.avi")
    assert url == "s3://adam-test/video/S20261030120000_cam0.avi"


def test_download_returns_local_path(tmp_path, s3_bucket):
    audio = tmp_path / "S20261030120000_audio.wav"
    audio.write_bytes(b"fake audio")
    storage.upload(str(audio), "audio/S20261030120000_audio.wav")
    target = tmp_path / "downloaded.wav"
    assert storage.download("audio/S20261030120000_audio.wav", str(target)) == str(target)
    assert target.read_bytes() == b"fake audio"


def test_bucket_requires_env(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError):
        storage.bucket()
