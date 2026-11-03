# tests/integration/test_analysis_report_data.py — 假分析順便存報表要畫的數字（report-data JSON）
# SQS（T49）之後每一次分析各有自己的 key（report-data/{service_id}_{tag}.json），提交時記在 tbl_analysis_job.report_data_key，
# 報表從那裡讀。只有第 0 階段假服務有這個檔案；移植版的在 analyzer/tests/。
import json

import pytest

import report_model
from fake_helpers import add_recording, job, keys
from report_helpers import STAFF_ACCOUNT, add_report, pdf_text, report_row

pytestmark = pytest.mark.integration
SID = "S20261030120000"


def stored_report_data(service, key):
    obj = service.s3.get_object(Bucket=service.S3_BUCKET, Key=key)
    return obj["ContentType"], json.loads(obj["Body"].read())


def test_analysis_saves_report_data_for_its_run_and_the_report_draws_it(api, service, db, tmp_path, drain,
                                                                        wait_for_reports):
    add_recording(service, db, tmp_path, SID)
    assert api.post("/analyze", json={"service_id": SID}).status_code == 200
    assert drain() == ["done"]
    assert db.status(SID) == 3

    key = job(db, SID)["report_data_key"]
    assert key == f"report-data/{SID}_{job(db, SID)['result_token'][:12]}.json"
    assert keys(service, "report-data/") == [key]  # 不寫固定名字的 report-data/{SID}.json（舊 worker 晚到會蓋掉）
    content_type, data = stored_report_data(service, key)
    assert content_type == "application/json"
    assert (data["schema"], data["version"], data["service_id"], data["source"]) == (
        "adam.report-data", 1, SID, "placeholder")
    for part in ("audio", "text"):
        for side in ("staff", "customer"):  # 兩側都存（customer 留給 N6）
            assert sum(data[part][side].values()) == pytest.approx(1, abs=0.001)
    for side in ("staff", "customer"):
        points = data["facial"][side]["points"]
        assert [t for t, _ in points] == [0, 1, 2, 3]  # 3 秒的影片：每秒一個點
        assert {v for _, v in points} <= {-1, 0, 1}
    assert report_model.parse_report_data(json.dumps(data)) is not None

    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    pdf = service.s3.get_object(Bucket=service.S3_BUCKET, Key=f"reports/{report_id}.pdf")["Body"].read()
    text = pdf_text(pdf)
    assert "No data" not in text and text.count("POSITIVE") == 3  # 兩張長條圖、Facial 折線的軸


def test_missing_media_is_saved_as_no_data(api, service, db, tmp_path, drain):
    add_recording(service, db, tmp_path, SID, cams=())  # 只有錄音，沒有影片
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    _, data = stored_report_data(service, job(db, SID)["report_data_key"])
    assert data["facial"] == {"staff": None, "customer": None}  # 臉的分數是 -1：沒有序列
    assert data["audio"]["staff"] is not None
