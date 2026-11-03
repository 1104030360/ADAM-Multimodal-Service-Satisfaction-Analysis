# tests/unit/test_ollama_live.py — 給使用者的 Ollama Cloud 連線檢查（new-feature N6）。兩個分析服務共用同一份。
#
# 平常一律跳過。只有明確打開時才真的呼叫 /api/chat（只送假資料：假分數、5 句假的對話，不送任何顧客資料），
# 檢查金鑰、模型名稱、回答的格式都可以用。會用掉一點點額度。在 EC2 上（容器 adam-analysis 已經用 analysis.env 啟動）：
#   docker exec -e OLLAMA_LIVE_TEST=1 adam-analysis python -m pytest -q -s -p no:cacheprovider tests/unit/test_ollama_live.py
# 設定（OLLAMA_*）由 tests/conftest.py 收在一旁，只交給這個測試；其他測試永遠看不到，整套一起跑也不會送出去。
# 不用 pytest 的話：docker exec adam-analysis python -m ollama_explain --live-check
import os

import pytest

import ollama_explain

pytestmark = pytest.mark.skipif(os.environ.get("OLLAMA_LIVE_TEST") != "1",
                                reason="live Ollama check; run with OLLAMA_LIVE_TEST=1 and the service's analysis.env")


def test_ollama_cloud_answers_with_valid_output(ollama_live_environ):
    ok, text = ollama_explain.live_check(ollama_live_environ)
    print(text)  # 加 -s 時看得到 12 格的內容（都是假資料的說明）
    assert ok, text
