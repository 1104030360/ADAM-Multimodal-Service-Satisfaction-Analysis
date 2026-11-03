"""送分析的 HTTP 請求（步驟 3-6、F3）：在 io lane 執行的 post_analysis()，以及 toast 的內文。
送出的整個流程（按鈕忙碌、重新整理清單、通知）見整合測試 test_analysis.py、test_analysis_flow.py。"""
from types import SimpleNamespace

import pytest
import requests

from myapp.init.server_home_init import post_analysis, toast_reason
from myapp.notifications import user_message

URL = "http://analysis.test/analyze"


@pytest.fixture
def posts(monkeypatch):
    """取代 requests.post：記下每次的參數，回傳 status（預設 200）。"""
    sent = SimpleNamespace(calls=[], status=200)

    def fake_post(url, **kwargs):
        sent.calls.append((url, kwargs))
        return SimpleNamespace(status_code=sent.status)

    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def test_request_sends_the_service_id_and_gives_up_instead_of_waiting_forever(posts):
    assert post_analysis(URL, "S20261030120000") is None
    [(url, kwargs)] = posts.calls
    assert (url, kwargs["json"]) == (URL, {"service_id": "S20261030120000"})
    # (連線逾時, 讀取逾時)：分析服務沒開或沒回應時，io lane 的這個工作最多等 3 + 10 秒
    assert kwargs["timeout"] == (3, 10)


@pytest.mark.parametrize("status", [202, 404, 409, 500, 503])
def test_anything_but_200_is_an_http_error(posts, status):
    posts.status = status  # 分析服務只有收下工作時才回 200（services/analyzer/app.py）
    with pytest.raises(requests.HTTPError) as raised:
        post_analysis(URL, "S20261030120000")
    assert raised.value.response.status_code == status
    assert user_message(raised.value)[1] == "The analysis service returned an error"


def test_missing_url_is_a_setup_error_not_a_network_error(posts):
    with pytest.raises(RuntimeError, match="ANALYZE_API_URL"):
        post_analysis("", "S20261030120000")
    assert posts.calls == []


@pytest.mark.parametrize("error, reason", [
    (requests.ConnectionError("refused"), "Can't reach the analysis service. Check your network and try again."),
    (requests.ReadTimeout("slow"), "The analysis service didn't respond. It may be busy."),
    (requests.HTTPError("HTTP 500"), "The analysis service returned an error. Try again in a moment."),
    (RuntimeError("ANALYZE_API_URL is not set"), "Something went wrong. An unexpected error occurred and was logged."),
], ids=["offline", "timeout", "http", "unexpected"])
def test_toast_reason_is_the_cause_and_one_next_step(error, reason):
    # DS-52 的範本；DS-21：toast 的內文最多兩行，所以下一步只取第一句
    assert toast_reason(error) == reason
