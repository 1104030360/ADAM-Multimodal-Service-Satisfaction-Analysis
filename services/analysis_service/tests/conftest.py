# tests/conftest.py — 單元測試與整合測試共用（new-feature N6）
import os

import pytest

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
