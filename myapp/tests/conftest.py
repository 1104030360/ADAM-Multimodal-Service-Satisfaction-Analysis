"""單元測試與整合測試共用的 fixture：隔離環境、Qt 應用程式、假裝置、moto 的 S3、GUI 執行緒不碰網路。"""
import os

# 這幾行要在 import myapp 之前執行
os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # config.load() 不讀開發者的 .env（T51；test_config.py 自己打開）
os.environ["QT_QPA_PLATFORM"] = "offscreen"  # Qt 不開真的視窗
os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"  # 萬一碰到真的鏡頭，也不跳出授權視窗
os.environ["ADAM_ANIMATION_SCALE"] = "0"  # 動畫時間全部變 0（UiPrefs.animationScale → Theme），測試不必等動畫

import gc
import threading

import boto3
import cv2
import mysql.connector
import pytest
import requests
from moto import mock_aws
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from myapp import devices, storage
from myapp.database import DatabaseConnector
from tests.fakes import FakeCameras, FakeMicrophone

# UiPrefs 的設定改存成 INI 檔，路徑由 isolated_app 指到每個測試的暫存資料夾：不碰使用者真的偏好設定
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

# app 會讀的環境變數：每個測試開始前全部清掉，需要的測試自己設定
APP_ENV_VARS = [
    "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SSL_CA",
    "S3_BUCKET", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_ENDPOINT_URL",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "ANALYZE_API_URL", "REPORTS_API_URL", "GOOGLE_APPLICATION_CREDENTIALS",
]


def pytest_addoption(parser):
    group = parser.getgroup("adam")
    group.addoption("--mysql-backend", choices=("docker", "native"), default="docker",
                    help="Start a disposable MySQL 8.4 server with Docker or local MySQL binaries.")
    group.addoption("--require-integration", action="store_true",
                    help="Fail if integration-test prerequisites are unavailable instead of skipping.")


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """整個測試期間共用一個 QApplication（一個程式只能有一個）。"""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # 測試會一直開關視窗，不要因此結束 app
    return app


@pytest.fixture(scope="session", autouse=True)
def garbage_collected_on_the_main_thread():
    """Python 的循環 GC 只在主執行緒、每個測試結束時跑（T34）。

    GC 平常由「剛好在配置物件的那條執行緒」觸發。測試自己建的、沒有 parent 的 QObject（例如單元測試的清單模型）
    在循環參照裡時，可能在背景執行緒（TaskRunner、db lane）上被刪掉：解構時 PySide 要等 GIL，主執行緒卻拿著 GIL
    等同一個 Qt 的連線鎖，兩邊互等，整套測試卡住（2026-11-01 整合時遇到一次）。app 自己的物件都有 parent，不受影響。
    """
    gc.disable()
    yield
    gc.enable()


@pytest.fixture(autouse=True)
def collect_garbage():
    """每個測試結束、其他 fixture 都收完之後，在主執行緒把循環的垃圾收掉。"""
    yield
    gc.collect()


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
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))

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
    """假麥克風（每個測試都換上，絕不碰真的麥克風）：一支輸入裝置。換掉的是 sounddevice 的函式（tests/fakes.py）。"""
    fake = FakeMicrophone()
    fake.install(monkeypatch)
    return fake


@pytest.fixture(autouse=True)
def media_devices(cameras, microphone, monkeypatch):
    """AVFoundation 的鏡頭清單和相機、麥克風權限也換成假的（myapp/devices.py，T41）：不讀這台電腦真的裝置。
    清單、權限由 cameras（FakeCameras.listed、access）和 microphone（FakeMicrophone.access）決定。"""
    monkeypatch.setattr(devices, "av_video_devices", cameras.av_devices)
    monkeypatch.setattr(devices, "media_access",
                        lambda kind: cameras.access if kind == "video" else microphone.access)


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


class GuiThreadNetworkError(AssertionError):
    """在 GUI 執行緒（主執行緒）連資料庫、傳 S3、送 HTTP、開 PDF。"""


@pytest.fixture
def no_network_on_gui_thread(monkeypatch):
    """不是 autouse，需要的測試自己開（F2 之後逐步開啟）。

    在主執行緒呼叫 mysql.connector.connect、storage.upload、storage.download、requests.post、fitz.open
    就丟 GuiThreadNetworkError；在背景執行緒照常執行。app 的程式就算把例外吞掉，測試結束時也會失敗。
    回傳違規清單；故意觸發的測試要自己清掉。
    """
    import fitz  # PyMuPDF；只有用到這個 fixture 才 import

    violations = []

    def guarded(name, original):
        def call(*args, **kwargs):
            if threading.current_thread() is threading.main_thread():
                violations.append(name)
                raise GuiThreadNetworkError(f"{name}() called on the GUI thread")
            return original(*args, **kwargs)
        return call

    for module, attribute in ((mysql.connector, "connect"), (storage, "upload"), (storage, "download"),
                              (storage, "upload_with_progress"), (storage, "download_with_progress"),
                              (requests, "post"), (fitz, "open")):
        name = f"{module.__name__}.{attribute}"
        monkeypatch.setattr(module, attribute, guarded(name, getattr(module, attribute)))
    yield violations
    assert violations == [], f"GUI 執行緒碰了網路：{violations}"
