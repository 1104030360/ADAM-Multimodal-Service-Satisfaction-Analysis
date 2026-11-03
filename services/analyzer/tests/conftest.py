# tests/conftest.py — 單元測試與整合測試共用：環境變數、假時鐘
import os
import tempfile

import pytest

# 要在 import analyzer（會載入 DeepFace、Hugging Face）之前設定。測試的容器沒有掛載 /models
os.environ["DEEPFACE_HOME"] = tempfile.mkdtemp()  # DeepFace 在 import 時會建立資料夾
os.environ["HF_HOME"] = tempfile.mkdtemp()
os.environ["HF_HUB_OFFLINE"] = "1"  # 測試不用真模型；萬一載入了，立刻失敗，不會去下載
os.environ.setdefault("AWS_REGION", "us-east-1")
# new-feature N6：測試一律不連 Ollama Cloud。在 EC2 的容器裡（docker exec，環境裡有 analysis.env 的金鑰）跑測試時，
# 也不會把測試資料送出去、不會用掉額度；要的測試自己用 monkeypatch 指向 127.0.0.1 的假伺服器。
# OLLAMA_* 一律從環境拿掉；只有 OLLAMA_LIVE_TEST=1 時把它們收在一旁，只交給連線檢查
# （tests/unit/test_ollama_live.py 的 ollama_live_environ）。所以就算整套都用 OLLAMA_LIVE_TEST=1 跑，其他測試也看不到金鑰。
OLLAMA_NAMES = ("OLLAMA_API_KEY", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "OLLAMA_TIMEOUT_SEC", "OLLAMA_THINK")
_ollama_live = {name: os.environ.pop(name) for name in OLLAMA_NAMES if name in os.environ}
if os.environ.get("OLLAMA_LIVE_TEST") != "1":
    _ollama_live.clear()


class HiddenSettings(dict):
    """印出來（pytest 的錯誤訊息、repr）只有名稱，不顯示值。"""

    def __repr__(self):
        return f"<{', '.join(sorted(self)) or 'no OLLAMA_* settings'}; values hidden>"


@pytest.fixture
def ollama_live_environ():
    """連線檢查用的 OLLAMA_*：只有 OLLAMA_LIVE_TEST=1 時才有內容。"""
    return HiddenSettings(_ollama_live)


class FakeClock:
    """取代程式裡的 time 模組：sleep 不會真的等，只把時間往前推。

    on_sleep 用來模擬「程式在等待的時候，外面發生的事」，例如桌面端把狀態寫成 2。
    """

    def __init__(self):
        self.now = 0.0
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


@pytest.fixture
def clock():
    return FakeClock()
