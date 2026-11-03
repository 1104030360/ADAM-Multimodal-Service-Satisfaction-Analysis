# tests/integration/test_reports.py — POST /reports 與報表的背景工作（reports_contract.md；new-feature PLAN 7、8）
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）。用到的 fixture：
#   service（匯入的 app 模組）、api（Flask test client）、db（root 連線）、wait_for_reports（等報表佇列做完）。
# 服務用最小權限的 adam_analysis 連資料庫（grants.sql），所以這些測試也驗證了報表需要的 GRANT。
import io
import re
import threading
from datetime import date, datetime, timedelta

import pytest
from PIL import Image
from pypdf import PdfReader

import report_model
import report_pdf
from report_helpers import (STAFF_ACCOUNT, Gate, add_account, add_report, add_scored_service, pdf_text,
                            report_row, set_account, wait_for_threads)

pytestmark = pytest.mark.integration
SID = "S20261030143012"
PREVIOUS = "S20261029100000"
START = datetime(2026, 10, 30, 14, 30, 12)
SCORES = dict(server_total=83.9, server_audio=78.6, server_text=88.5, server_facial=84.7,
              customer_total=80.2, customer_audio=76.0, customer_text=82.4, customer_facial=81.9)


def put(service, key, body, content_type="application/octet-stream"):
    service.s3.put_object(Bucket=service.S3_BUCKET, Key=key, Body=body, ContentType=content_type)


def get_pdf(service, report_id):
    obj = service.s3.get_object(Bucket=service.S3_BUCKET, Key=f"reports/{report_id}.pdf")
    return obj["ContentType"], obj["Body"].read()


def pdf_exists(service, report_id):
    listed = service.s3.list_objects_v2(Bucket=service.S3_BUCKET, Prefix=f"reports/{report_id}")
    return listed.get("KeyCount", 0) > 0


def report_data_json():
    return report_model.dumps_report_data(report_model.build_report_data(
        SID, source="test",
        audio={"staff": {"positive": 0.31, "neutral": 0.52, "negative": 0.17}, "customer": None},
        text={"staff": {"positive": 0.44, "neutral": 0.49, "negative": 0.07}, "customer": None},
        facial={"staff": [(t, 1 if t > 100 else 0) for t in range(388)], "customer": None}))


def photo_jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (90, 140, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def analysed_service(service, db):
    """一筆分析完成的服務（83.9／78.6／88.5／84.7）和同一位員工的上一筆（85.6／89.3／81.9／86.4）；
    員工有職稱和大頭照，S3 上有 report-data JSON。回傳 report_id（generating）。"""
    set_account(db, STAFF_ACCOUNT, account_name="Alex Morgan", account_occupation_name="Customer Service Lead",
                account_photo_key="avatars/staff.jpg")
    put(service, "avatars/staff.jpg", photo_jpeg(), "image/jpeg")
    add_scored_service(db, PREVIOUS, datetime(2026, 10, 29, 10), server_total=85.6, server_audio=89.3,
                       server_text=81.9, server_facial=86.4)
    add_scored_service(db, SID, START, START + timedelta(seconds=387), **SCORES)
    put(service, report_model.report_data_key(SID), report_data_json(), "application/json")
    return add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)


# ---------- HTTP ----------
@pytest.mark.parametrize("body", [
    "not json", ["abc"], {}, {"report_id": 5}, {"report_id": None}],
    ids=["not-json", "not-object", "missing", "number", "null"])
def test_invalid_body_is_400(api, body):
    if isinstance(body, str):
        r = api.post("/reports", data=body, content_type="application/json")
    else:
        r = api.post("/reports", json=body)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_report_id"


def test_unknown_report_is_404(api):
    r = api.post("/reports", json={"report_id": "00000000-0000-4000-8000-000000000000"})
    assert (r.status_code, r.get_json()) == (404, {"error": "report_not_found"})


@pytest.mark.parametrize("status", ["ready", "failed"])
def test_report_that_is_not_generating_is_409(api, db, analysed_service, status):
    db.query("UPDATE tbl_report SET report_status = %s WHERE report_id = %s", status, analysed_service)
    r = api.post("/reports", json={"report_id": analysed_service})
    assert (r.status_code, r.get_json()) == (409, {"error": "report_not_generating", "report_status": status})


def test_database_error_is_503(api, service, monkeypatch):
    def broken(report_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(service.report_jobs, "get_status", broken)
    r = api.post("/reports", json={"report_id": "x"})
    assert (r.status_code, r.get_json()) == (503, {"error": "db_unavailable"})


# ---------- 每次服務報表 ----------
def test_service_report_becomes_ready_with_the_pdf_in_s3(api, service, db, analysed_service, wait_for_reports):
    r = api.post("/reports", json={"report_id": analysed_service})
    assert (r.status_code, r.get_json()) == (202, {"report_id": analysed_service, "state": "queued"})
    wait_for_reports()

    assert report_row(db, analysed_service) == {
        "report_status": "ready", "failure_reason": None, "ai_model": None, "ai_prompt_version": None,
        "published_at": None, "published_by": None}  # 不碰發佈的欄位；N6 暫停中，模型欄位留 NULL
    content_type, data = get_pdf(service, analysed_service)
    assert content_type == "application/pdf"
    text = pdf_text(data)
    for expected in ("Alex Morgan", "Customer Service Lead", SID, "FROM 2026-10-30 14:30:12",
                     "TO 2026-10-30 14:36:39", "83.9", "-2% from previous service",  # (83.9 − 85.6) / 85.6
                     "-12% from previous service", "+8% from previous service", "0.31", "0.44", "POSITIVE"):
        assert expected in text, expected
    assert text.count("Explanation unavailable") == 12
    assert "No data" not in text


def test_each_card_finds_its_own_previous_service(api, service, db, wait_for_reports):
    """「from previous service」（PLAN 7.4）：同一位、比這次早、狀態 3、那個分數不是 -1 的最近一筆，每張卡各找各的。"""
    add_account(db, "staff-z", "Zoe")
    add_scored_service(db, SID, START, START + timedelta(seconds=387), **SCORES)
    # 最近的上一筆（狀態 3）：Audio、Facial 是 -1
    add_scored_service(db, "S20261029100000", datetime(2026, 10, 29, 10), server_total=85.6, server_audio=-1,
                       server_text=81.9, server_facial=-1)
    # 更早的一筆（狀態 3）：Audio 有分數，Facial 還是 -1
    add_scored_service(db, "S20261028100000", datetime(2026, 10, 28, 10), server_total=99.0, server_audio=60.0,
                       server_text=50.0, server_facial=-1)
    # 不算的：比上一筆新但沒分析完（狀態 2）、別人的、比這次晚的；分數都有，算進去的話會出現別的百分比
    everything = dict(server_total=50.0, server_audio=50.0, server_text=50.0, server_facial=50.0)
    add_scored_service(db, "S20261030090000", datetime(2026, 10, 30, 9), status=2, **everything)
    add_scored_service(db, "S20261030080000", datetime(2026, 10, 30, 8), account="staff-z", **everything)
    add_scored_service(db, "S20261031100000", datetime(2026, 10, 31, 10), **everything)
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    text = pdf_text(get_pdf(service, report_id)[1])
    changes = re.findall(r"([+-]\d+%) from previous service", text)
    # Overall 比 85.6：-2%；Audio 跳過 -1，比更早的 60.0：+31%；Text 比 81.9：+8%；Facial 沒有可以比的：不寫
    assert sorted(changes) == sorted(["-2%", "+31%", "+8%"]), text
    assert "+69%" not in text  # Facial 84.7 比 50.0（狀態 2、別人、比較晚的那幾筆）


def test_repeated_post_is_queued_once(api, service, db, analysed_service, wait_for_reports, monkeypatch):
    gate = Gate(report_pdf.render)
    monkeypatch.setattr(report_pdf, "render", gate)
    first = api.post("/reports", json={"report_id": analysed_service})
    assert gate.entered.wait(10)
    second = api.post("/reports", json={"report_id": analysed_service})  # 桌面端重送：已經在做
    gate.open()
    wait_for_reports()
    assert [first.status_code, second.status_code] == [202, 202]
    assert [first.get_json()["state"], second.get_json()["state"]] == ["queued", "already_queued"]
    assert gate.calls == 1
    assert report_row(db, analysed_service)["report_status"] == "ready"


def test_missing_report_data_and_photo_still_make_a_report(api, service, db, wait_for_reports):
    set_account(db, STAFF_ACCOUNT, account_photo_key="avatars/missing.jpg")  # S3 上沒有這張照片
    add_scored_service(db, SID, START, None, **{**SCORES, "server_facial": -1})
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    text = pdf_text(get_pdf(service, report_id)[1])
    assert text.count("No data") == 4  # Facial 卡（-1）＋三張圖（沒有 report-data JSON：不估算）
    assert "POSITIVE" not in text and " TO " not in text  # 不畫空的軸；沒有結束時間只有 FROM
    assert "from previous service" not in text  # 沒有上一筆


def test_unreadable_report_data_and_oversized_photo_are_left_out(api, service, db, analysed_service,
                                                                  wait_for_reports):
    put(service, report_model.report_data_key(SID), b"{not json", "application/json")
    put(service, "avatars/staff.jpg", b"\xff" * (5 * 1024 * 1024 + 1), "image/jpeg")  # 超過 5 MB
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "ready"
    data = get_pdf(service, analysed_service)[1]
    assert pdf_text(data).count("No data") == 3  # 三張圖；分數卡照常
    assert len(PdfReader(io.BytesIO(data)).pages[0].images) == 0  # 預設頭像


@pytest.mark.parametrize("job_state", ["done", "running"])
def test_report_data_of_the_committed_analysis_is_used(api, service, db, analysed_service, wait_for_reports, job_state):
    """T49：SQS 之後的分析把 report-data 存在帶 token 的 key，記在工作那一列；提交成功（done）才讀它。
    還沒提交的（running：可能是被搶走 lease 的舊 worker 上傳的）不讀，照舊讀 report-data/{SID}.json。"""
    key = f"report-data/{SID}_0123456789ab.json"
    lease = (None, None) if job_state == "done" else ("f" * 32, "UTC_TIMESTAMP(3) + INTERVAL 300 SECOND")
    db.query("INSERT INTO tbl_analysis_job (service_id, job_state, attempts, max_attempts, lease_token, lease_until, "
             f"result_token, report_data_key, accepted_at, updated_at) VALUES (%s, %s, 1, 3, %s, {lease[1] or 'NULL'}, "
             "%s, %s, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))",
             SID, job_state, lease[0], "0123456789ab" + "0" * 20, key)
    put(service, key, report_data_json(), "application/json")
    put(service, report_model.report_data_key(SID), b"{not json", "application/json")  # 舊的 key 讀不出來
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "ready"
    text = pdf_text(get_pdf(service, analysed_service)[1])
    if job_state == "done":
        assert "No data" not in text and "0.31" in text
    else:
        assert text.count("No data") == 3


def test_oversized_report_data_is_left_out(api, service, db, analysed_service, wait_for_reports):
    put(service, report_model.report_data_key(SID), b" " * (5 * 1024 * 1024 + 1), "application/json")
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "ready"
    assert pdf_text(get_pdf(service, analysed_service)[1]).count("No data") == 3


def test_report_that_is_no_longer_generating_is_skipped(service, db, wait_for_reports):
    add_scored_service(db, SID, START, None, **SCORES)
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT, status="ready")
    service.report_jobs.enqueue(report_id)  # 例如重新啟動時排入、桌面端剛好改了狀態
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    assert not pdf_exists(service, report_id)


def test_service_deleted_after_the_request_fails(api, db, wait_for_reports):
    add_scored_service(db, SID, START, None, **SCORES)
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
    db.query("SET FOREIGN_KEY_CHECKS = 0")
    db.query("DELETE FROM tbl_service WHERE service_id = %s", SID)
    db.query("SET FOREIGN_KEY_CHECKS = 1")
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["failure_reason"] == "The service was not found."


def test_service_without_completed_analysis_fails(api, service, db, wait_for_reports):
    add_scored_service(db, SID, START, None, status=2, no_scores=True)
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "failed"
    assert report_row(db, report_id)["failure_reason"] == "The service has no completed analysis."
    assert not pdf_exists(service, report_id)


def test_upload_error_fails_with_a_short_reason(api, service, db, analysed_service, wait_for_reports,
                                                monkeypatch):
    def denied(**kwargs):
        raise RuntimeError("AccessDenied: arn:aws:iam::123456789012:role/secret")

    monkeypatch.setattr(service.report_jobs.s3, "put_object", denied)
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    row = report_row(db, analysed_service)
    assert (row["report_status"], row["failure_reason"]) == ("failed", "Could not upload the PDF.")  # 不放錯誤細節


def test_render_error_fails(api, db, analysed_service, wait_for_reports, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("bad layout")

    monkeypatch.setattr(report_pdf, "render", broken)
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    assert report_row(db, analysed_service)["failure_reason"] == "Could not create the PDF."


def test_unexpected_error_fails_with_a_generic_reason(api, service, db, analysed_service, wait_for_reports,
                                                      monkeypatch):
    def broken(report_id, cancelled):
        raise KeyError("oops")

    monkeypatch.setattr(service.report_jobs, "run", broken)
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    assert report_row(db, analysed_service)["failure_reason"] == "Report generation failed."


def test_time_limit_marks_timed_out_and_drops_the_late_pdf(api, service, db, analysed_service, wait_for_reports,
                                                           monkeypatch):
    gate = Gate(report_pdf.render)
    monkeypatch.setattr(report_pdf, "render", gate)
    monkeypatch.setattr(service.report_jobs, "time_limit", 0.5)
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()  # worker 等了 0.5 秒就放棄，接著做下一份
    assert report_row(db, analysed_service)["report_status"] == "failed"
    assert report_row(db, analysed_service)["failure_reason"] == "Timed out"
    gate.open()  # 超時的那份這時才畫完：不上傳、不把 failed 改成 ready
    wait_for_threads("report-")
    assert report_row(db, analysed_service)["report_status"] == "failed"
    assert not pdf_exists(service, analysed_service)


def test_row_failed_by_the_desktop_is_not_overwritten(api, service, db, analysed_service, wait_for_reports,
                                                      monkeypatch):
    gate = Gate(report_pdf.render)
    monkeypatch.setattr(report_pdf, "render", gate)
    api.post("/reports", json={"report_id": analysed_service})
    assert gate.entered.wait(10)
    # 產生中，桌面端把超過 10 分鐘的 generating 改成 failed（reports_contract.md 第 4 點）
    db.query("UPDATE tbl_report SET report_status = 'failed', failure_reason = 'Timed out' WHERE report_id = %s",
             analysed_service)
    gate.open()
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "failed"  # WHERE report_status = 'generating'


def test_report_location_must_be_its_own_pdf(api, service, db, wait_for_reports):
    add_scored_service(db, SID, START, None, **SCORES)
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT, s3_key="video/overwrite.avi")
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["failure_reason"] == "The report request is not valid."
    assert service.s3.list_objects_v2(Bucket=service.S3_BUCKET).get("KeyCount", 0) == 0


def test_explanation_is_printed_and_recorded(api, service, db, analysed_service, wait_for_reports, monkeypatch):
    seen = []
    part = report_model.ModalityExplanation(report_model.Perspectives("Customer view.", "Staff view."),
                                            report_model.Perspectives("Customer next.", "Staff next."))

    def fake_explain(inputs):
        seen.append(inputs)
        return report_model.Explanation(part, part, part, model="example-model", prompt_version="p1",
                                        generated_at=datetime(2026, 11, 1, 10, 5, tzinfo=report_model.REPORT_TZ))

    monkeypatch.setattr(report_model, "explain", fake_explain)
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    row = report_row(db, analysed_service)
    assert (row["report_status"], row["ai_model"], row["ai_prompt_version"]) == ("ready", "example-model", "p1")
    text = pdf_text(get_pdf(service, analysed_service)[1])
    assert "AI-generated explanation · example-model · 2026-11-01 10:05" in text
    assert text.count("Staff view.") == 3 and "Explanation unavailable" not in text
    inputs, = seen  # explain() 拿到兩側的分數和 report-data（N6 的輸入）
    assert inputs.scores["customer"]["total"] == 80.2 and inputs.scores["staff"]["total"] == 83.9
    assert inputs.report_data["audio"]["staff"]["positive"] == 0.31 and inputs.transcript_key is None


def test_slow_explanation_counts_against_the_time_limit(api, service, db, analysed_service, wait_for_reports,
                                                        monkeypatch):
    gate = Gate()
    monkeypatch.setattr(report_model, "explain", gate)  # AI 一直沒回應
    monkeypatch.setattr(service.report_jobs, "time_limit", 0.5)
    r = api.post("/reports", json={"report_id": analysed_service})
    assert r.status_code == 202  # explain() 在背景工作裡：HTTP 馬上回答
    wait_for_reports()
    assert report_row(db, analysed_service)["failure_reason"] == "Timed out"
    gate.open()
    wait_for_threads("report-")
    assert report_row(db, analysed_service)["report_status"] == "failed"
    assert not pdf_exists(service, analysed_service)


def test_default_time_limit_leaves_room_for_one_ai_call(service):
    assert service.report_jobs.time_limit >= 180  # AI 說明一次最多約 90 秒（OLLAMA_TIMEOUT_SEC），加上畫圖、上傳


def test_explanation_error_still_makes_a_report(api, db, analysed_service, wait_for_reports, monkeypatch):
    def broken(inputs):
        raise TimeoutError("model did not answer")

    monkeypatch.setattr(report_model, "explain", broken)
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    row = report_row(db, analysed_service)
    assert (row["report_status"], row["ai_model"]) == ("ready", None)


def test_transcript_location_is_passed_to_explain(api, service, db, analysed_service, wait_for_reports,
                                                  monkeypatch):
    db.query("INSERT INTO tbl_text_storage (service_id, text_name, text_url) VALUES (%s, %s, %s)",
             SID, f"{SID}_transcript.txt", f"s3://bucket/text/{SID}_transcript.txt")
    seen = []
    monkeypatch.setattr(report_model, "explain", lambda inputs: seen.append(inputs))
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    inputs = seen[0]
    assert inputs.transcript_key == f"text/{SID}_transcript.txt"  # 只傳位置，不下載
    assert inputs.s3 is service.s3 and inputs.bucket == service.S3_BUCKET  # explain() 需要時自己下載


def test_reports_do_not_wait_behind_analyses(api, db, analysed_service, wait_for_reports):
    """分析在 worker 容器裡做（SQS，T49）：API 收下了一筆分析、還沒有 worker 領走時，報表照樣產生。"""
    add_scored_service(db, "S20261030150000", START, None, status=1, no_scores=True)
    assert api.post("/analyze", json={"service_id": "S20261030150000"}).status_code == 200
    api.post("/reports", json={"report_id": analysed_service})
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "ready"  # 報表有自己的 worker，不排在分析後面
    assert db.query("SELECT job_state FROM tbl_analysis_job WHERE service_id = %s", "S20261030150000") \
        == [("queued",)]


def test_recent_generating_reports_are_recovered_after_a_restart(service, db, analysed_service, wait_for_reports):
    old = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT,
                     created_at=datetime.now() - timedelta(minutes=30))
    service.report_jobs.recover()  # 重新啟動時做的事：佇列在記憶體裡，重啟後就空了
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "ready"
    assert report_row(db, old)["report_status"] == "generating"  # 超過 10 分鐘的留給桌面端改成 failed


def test_report_row_deleted_before_the_worker_starts_is_skipped(service, db, wait_for_reports):
    service.report_jobs.enqueue("00000000-0000-4000-8000-00000000dead")
    wait_for_reports()  # 沒有這一列：什麼都不做，worker 繼續


# ---------- 員工、全公司報表 ----------
def seed_period(db):
    """10 月：這位員工 3 筆分析完成（90、80、-1）＋ 1 筆還在分析；9 月（上一期）1 筆 80；另一位員工 10 月 1 筆 70。"""
    add_account(db, "staff-b", "王小明")
    add_scored_service(db, "S20261001080000", datetime(2026, 10, 1, 8), server_total=90.0, server_audio=70.0,
                       customer_total=85.0)
    add_scored_service(db, "S20261015120000", datetime(2026, 10, 15, 12), server_total=80.0, server_audio=-1,
                       customer_total=75.0)
    add_scored_service(db, "S20261031235959", datetime(2026, 10, 31, 23, 59, 59), customer_total=60.0)
    add_scored_service(db, "S20261020100000", datetime(2026, 10, 20, 10), status=2, no_scores=True)
    add_scored_service(db, "S20260930100000", datetime(2026, 9, 30, 10), server_total=80.0, server_audio=70.0)
    add_scored_service(db, "S20261101000000", datetime(2026, 11, 1, 0, 0), server_total=10.0)  # 下一期
    add_scored_service(db, "S20261010100000", datetime(2026, 10, 10, 10), account="staff-b", server_total=70.0,
                       customer_total=95.0)


def test_staff_period_report(api, service, db, wait_for_reports):
    seed_period(db)
    set_account(db, STAFF_ACCOUNT, account_name="Alex Morgan", account_occupation_name="")
    report_id = add_report(db, "staff", staff=STAFF_ACCOUNT, start=date(2026, 10, 1), end=date(2026, 10, 31))
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    text = pdf_text(get_pdf(service, report_id)[1])
    for expected in ("Staff report", "FROM 2026-10-01 TO 2026-10-31", "Alex Morgan", "COMPLETED SERVICES 3",
                     "+200% from previous period",  # 3 筆比 1 筆
                     "OVERALL 85.0", "+6% from previous period",  # (90 + 80) / 2 比 80：+6.25%
                     "S20261001080000", "S20261015120000", "2026-08-31 to 2026-09-30"):
        assert expected in text, expected
    assert "S20261020100000" not in text and "S20261101000000" not in text  # 沒分析完、不在期間的不算
    assert "王小明" not in text  # 別人的服務不算
    assert text.index("S20261001080000") < text.index("S20261015120000")  # 最高分在前


def test_company_period_report(api, service, db, wait_for_reports):
    seed_period(db)
    report_id = add_report(db, "company", start=date(2026, 10, 1), end=date(2026, 10, 31))
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    text = pdf_text(get_pdf(service, report_id)[1])
    for expected in ("Company report", "COMPLETED SERVICES 4", "+300% from previous period",  # 4 筆比 1 筆
                     "STAFF WITH SERVICES 2", "+100% from previous period", "STAFF RANKING", "王小明",
                     "Staff 80.0 (0%)",  # 員工 (90 + 80 + 70) / 3，上一期 80
                     "Customer 78.8"):  # 顧客 (85 + 75 + 60 + 95) / 4；上一期沒有顧客分數，不寫變化
        assert expected in text, expected
    seed_name = db.query("SELECT account_name FROM tbl_account WHERE account_id = %s", STAFF_ACCOUNT)[0][0]
    assert text.index(seed_name) < text.index("王小明")  # 排名：85.0 比 70.0 前面


def test_same_name_staff_are_told_apart_by_username(api, service, db, wait_for_reports):
    """PLAN 13.7：兩位都叫 Alex Morgan 時，排名、員工報表、每次服務報表的名字後面加登入帳號；不同名的不加。"""
    seed_period(db)  # 王小明（staff-b）只有一位
    set_account(db, STAFF_ACCOUNT, account_name="Alex Morgan")
    add_account(db, "staff-c", "Alex Morgan")
    add_scored_service(db, "S20261012100000", datetime(2026, 10, 12, 10), account="staff-c", server_total=75.0)
    seed_username = db.query("SELECT account_username FROM tbl_account WHERE account_id = %s", STAFF_ACCOUNT)[0][0]
    reports = {
        "company": add_report(db, "company", start=date(2026, 10, 1), end=date(2026, 10, 31)),
        "staff": add_report(db, "staff", staff=STAFF_ACCOUNT, start=date(2026, 10, 1), end=date(2026, 10, 31)),
        "service": add_report(db, "service", service_id="S20261012100000", staff="staff-c"),
    }
    for report_id in reports.values():
        api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    texts = {kind: pdf_text(get_pdf(service, report_id)[1]) for kind, report_id in reports.items()}
    company = texts["company"]
    assert f"Alex Morgan ({seed_username})" in company and "Alex Morgan (user-staff-c)" in company
    assert "王小明 (" not in company and "王小明" in company
    assert f"Alex Morgan ({seed_username})" in texts["staff"]
    assert "Alex Morgan (user-staff-c)" in texts["service"]

    set_account(db, "staff-c", account_name="Sam Rivera")  # 改名之後不再同名：不加
    again = add_report(db, "staff", staff=STAFF_ACCOUNT, start=date(2026, 10, 1), end=date(2026, 10, 31))
    api.post("/reports", json={"report_id": again})
    wait_for_reports()
    assert "Alex Morgan (" not in pdf_text(get_pdf(service, again)[1])


def test_staff_report_for_a_deleted_account_fails(api, db, wait_for_reports):
    add_account(db, "gone", "Gone")
    report_id = add_report(db, "staff", staff="gone", start=date(2026, 10, 1), end=date(2026, 10, 31))
    db.query("SET FOREIGN_KEY_CHECKS = 0")
    db.query("DELETE FROM tbl_account WHERE account_id = 'gone'")
    db.query("SET FOREIGN_KEY_CHECKS = 1")
    api.post("/reports", json={"report_id": report_id})
    wait_for_reports()
    assert report_row(db, report_id)["failure_reason"] == "The staff account was not found."


def test_worker_survives_a_database_outage_while_recording_the_result(service, db, analysed_service,
                                                                      wait_for_reports, monkeypatch):
    failures = threading.Event()

    def broken(*args):
        failures.set()
        raise RuntimeError("lost connection")

    with monkeypatch.context() as outage:  # 讀資料、寫 failed 都失敗：worker 記 log，不會停
        outage.setattr(service.report_jobs, "rows", broken)
        outage.setattr(service.report_jobs, "execute", broken)
        service.report_jobs.enqueue(analysed_service)
        wait_for_reports()
    assert failures.is_set()
    assert report_row(db, analysed_service)["report_status"] == "generating"
    service.report_jobs.enqueue(analysed_service)  # 資料庫回來之後，同一份還能再做
    wait_for_reports()
    assert report_row(db, analysed_service)["report_status"] == "ready"
