"""單元測試與整合測試共用的 fixture：隔離環境、Qt 應用程式、假裝置、moto 的 S3。"""
import os

# 這三行要在 import myapp 之前執行（record_widget.py 等模組一 import 就會呼叫 load_dotenv()）
os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # 不讀開發者的 .env
os.environ["QT_QPA_PLATFORM"] = "offscreen"  # Qt 不開真的視窗
os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"  # 萬一碰到真的鏡頭，也不跳出授權視窗

import boto3
import cv2
import pyaudio
import pytest
from moto import mock_aws
from PySide6.QtWidgets import QApplication

from myapp import storage
from myapp.database import DatabaseConnector
from tests.fakes import FakeCameras, FakeMicrophone

# app 會讀的環境變數：每個測試開始前全部清掉，需要的測試自己設定
APP_ENV_VARS = [
    "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SSL_CA",
    "S3_BUCKET", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_ENDPOINT_URL",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "ANALYZE_API_URL", "GOOGLE_APPLICATION_CREDENTIALS",
]


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """整個測試期間共用一個 QApplication（一個程式只能有一個）。"""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # 測試會一直開關視窗，不要因此結束 app
    return app


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch, tmp_path):
    """每個測試都從乾淨的狀態開始，而且不會寫進原始碼資料夾。"""
    for name in APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))  # 不讀 ~/.aws 裡真的設定與憑證
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("LOCAL_MEDIA_DIR", str(tmp_path / "media"))  # 下載的影片與圖表
    monkeypatch.setenv("LOCAL_CHARTS_DIR", str(tmp_path / "charts"))
    monkeypatch.chdir(tmp_path)  # 錄影檔寫在「目前目錄」的 video/、audio/

    # 共用的資料庫連線和 S3 client 都會被記住；每個測試前後都清掉
    DatabaseConnector._instance = None
    storage.s3_client.cache_clear()
    yield
    if DatabaseConnector._instance is not None:
        DatabaseConnector._instance.close_connection()
    DatabaseConnector._instance = None
    storage.s3_client.cache_clear()


@pytest.fixture(autouse=True)
def cameras(monkeypatch):
    """假鏡頭（每個測試都換上，絕不碰真的鏡頭）：0 號和 1 號接著。"""
    fake = FakeCameras()
    monkeypatch.setattr(cv2, "VideoCapture", fake)
    return fake


@pytest.fixture(autouse=True)
def microphone(monkeypatch):
    """假麥克風（每個測試都換上）：一支輸入裝置。"""
    fake = FakeMicrophone()
    monkeypatch.setattr(pyaudio, "PyAudio", fake)
    return fake


@pytest.fixture
def s3(monkeypatch):
    """moto 在記憶體裡模擬 S3（不連網路），並建立 adam-test bucket。"""
    monkeypatch.setenv("S3_BUCKET", "adam-test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="adam-test")
        yield client
