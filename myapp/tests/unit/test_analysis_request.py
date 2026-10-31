"""送分析的 HTTP 請求（步驟 3-6）。成功、失敗、狀態變化見整合測試 test_analysis.py。"""
from types import SimpleNamespace

import requests

from myapp.init.server_home_init import ServiceModel
from tests.fakes import FakeConnection


def test_analysis_request_gives_up_instead_of_freezing_the_window(monkeypatch):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("ANALYZE_API_URL", "http://analysis.test/analyze")
    model = ServiceModel(SimpleNamespace(connection=FakeConnection()), "acc-1")
    assert model.analyze_service("S20261030120000")
    # (連線逾時, 讀取逾時)：分析服務沒開或沒回應時，主執行緒最多等 3 + 10 秒
    assert sent["timeout"] == (3, 10)
