# tests/integration/test_jobs.py — 一筆分析工作：/analyze → outbox → SQS → worker.process（從 S3 下載 → 分析（假的）→ 輸出改成
#   帶 token 的檔名上傳）→ 一個交易寫分數、逐字稿、圖表、工作 done、狀態 3 → 刪訊息（docs/rebuild/sqs/PLAN.md 第 2 節、教學 6-4）
import os

import pytest

import analysis_queue
import report_model
from report_helpers import STAFF_ACCOUNT, add_report, pdf_text, report_row

pytestmark = pytest.mark.integration
SID = "S20261030120000"
PREVIOUS = "S20261030115500"  # 同一個錄影視窗的上一段錄影


def analyze(api, db, add_recording, sid=SID):
    """桌面端的順序：建立服務、上傳並登記錄影（狀態 1）→ 按 Analyze。回傳錄影的內容。"""
    db.add_service(sid, 1)
    files = add_recording(sid)
    assert api.post("/analyze", json={"service_id": sid}).status_code == 200
    return files


def test_the_worker_uploads_token_named_results_then_commits_them(service, api, db, queues, add_recording,
                                                                   fake_analysis, drain):
    bucket = service.S3_BUCKET
    uploaded = analyze(api, db, add_recording)
    assert drain() == 1

    # 分析拿到的是從 S3 下載到暫存資料夾的檔案（Whisper 也讀本機的錄音）；暫存資料夾用完就刪掉（錄影是個人資料）
    call, = fake_analysis.calls
    assert call["files"] == uploaded
    assert call["media"]["audio"] == os.path.join(call["work"], f"{SID}_audio.wav")
    assert not os.path.exists(call["work"])

    # 提交的是這一次：工作 done、記下 token 和 report-data 的 key，lease 交還；訊息刪掉了，outbox 標成已送
    job = db.job(SID)
    token = job["result_token"]
    assert len(token) == 32
    assert job == {"job_state": "done", "attempts": 1, "max_attempts": 3, "lease_token": None, "result_token": token,
                   "report_data_key": analysis_queue.report_data_key(SID, token), "last_error": None}
    assert queues.count() == (0, 0) and queues.dead() == 0
    assert [row["outbox_state"] for row in db.outbox(SID)] == ["sent"]

    # 圖表與逐字稿的檔名帶 token（{SID}_{token 前 12 字}_…）：被搶走 lease 的舊 worker 晚到的上傳蓋不掉
    charts = [analysis_queue.run_name(SID, token, name) for name in fake_analysis.charts(SID)]
    assert charts[0] == f"{SID}_{token[:12]}_emotion_TEXT.png"
    for name in charts:
        assert service.s3.head_object(Bucket=bucket, Key=f"charts/{name}")["ContentType"] == "image/png"
    transcript = f"{SID}_{token[:12]}_transcript.txt"
    stored_text = service.s3.get_object(Bucket=bucket, Key=f"text/{transcript}")
    assert stored_text["ContentType"] == "text/plain; charset=utf-8"
    assert stored_text["Body"].read().decode("utf-8") == fake_analysis.transcript_text

    # 資料庫：8 個分數各在正確的欄位（server_* 是服務人員），圖表依分析回傳的順序，URL 是 s3://，狀態 3
    assert db.scores(SID) == {
        "customer_text": 71, "customer_audio": 72, "customer_facial": 73, "customer_total": 72,
        "server_text": 81, "server_audio": 82, "server_facial": 83, "server_total": 82}
    assert db.texts(SID) == [(transcript, f"s3://{bucket}/text/{transcript}")]
    assert db.charts(SID) == [(name, f"s3://{bucket}/charts/{name}") for name in charts]
    assert db.status(SID) == 3

    # 報表要畫的數字（new-feature N4）：存在這一次的 key（記在工作那一列），不再寫 report-data/{SID}.json
    stored = service.s3.get_object(Bucket=bucket, Key=job["report_data_key"])
    assert stored["ContentType"] == "application/json"
    data = report_model.parse_report_data(stored["Body"].read())
    assert data["audio"]["staff"] == {"positive": 0.5, "neutral": 0.3, "negative": 0.2}
    assert data["facial"]["customer"] == [(0.0, 1.0), (1.0, 0.0)]
    listed = service.s3.list_objects_v2(Bucket=bucket, Prefix="report-data/")["Contents"]
    assert [obj["Key"] for obj in listed] == [job["report_data_key"]]


def test_reports_read_the_committed_report_data(service, api, db, add_recording, fake_analysis, drain,
                                                wait_for_reports):
    """分析完的服務馬上可以產生報表：報表讀工作那一列記的 report-data（不是 report-data/{SID}.json）。"""
    analyze(api, db, add_recording)
    drain()
    report_id = add_report(db, "service", service_id=SID, staff=STAFF_ACCOUNT)
    assert api.post("/reports", json={"report_id": report_id}).status_code == 202
    wait_for_reports()
    assert report_row(db, report_id)["report_status"] == "ready"
    pdf = service.s3.get_object(Bucket=service.S3_BUCKET, Key=f"reports/{report_id}.pdf")["Body"].read()
    text = pdf_text(pdf)
    assert all(value in text for value in ("0.50", "0.30", "0.20"))  # 服務人員的 Audio 三種比例
    assert text.count("No data") == 2  # 這次的 report-data 沒有服務人員的 Text、Facial


def test_without_report_data_the_job_has_no_key(service, api, db, add_recording, fake_analysis, drain):
    fake_analysis.with_report_data = False
    analyze(api, db, add_recording)
    drain()
    assert db.status(SID) == 3 and db.job(SID)["report_data_key"] is None
    assert service.s3.list_objects_v2(Bucket=service.S3_BUCKET, Prefix="report-data/").get("KeyCount") == 0


def test_leftover_rows_of_the_previous_recording_are_ignored(service, api, db, add_recording, fake_analysis, drain):
    db.add_service(SID, 1)
    # 同一個錄影視窗連錄兩段時，第二段也會登記上一段的兩個影片（record_widget.py 沒有清空清單）
    for cam in ("cam0", "cam1"):
        name = f"{PREVIOUS}_{cam}.avi"
        service.s3.put_object(Bucket=service.S3_BUCKET, Key=f"video/{name}", Body=b"previous recording")
        db.add_media(SID, name)
    uploaded = add_recording(SID)

    api.post("/analyze", json={"service_id": SID})
    drain()
    assert fake_analysis.calls[0]["files"] == uploaded  # 每個鏡頭取檔名最新的那一個


def test_recording_without_files_is_completed_as_no_data(api, db, drain):
    db.add_service(SID, 1)
    # 桌面端上傳失敗時，位置寫成空字串。這裡用真的 analyzer.process_service：沒有檔案就用不到模型
    for name in (f"{SID}_cam0.avi", f"{SID}_cam1.avi", f"{SID}_audio.wav"):
        db.add_media(SID, name, url="")

    api.post("/analyze", json={"service_id": SID})
    drain()
    assert db.status(SID) == 3
    assert set(db.scores(SID).values()) == {-1}  # 8 個分數都是 -1（沒有資料），資料庫的 CHECK 也接受
    assert db.charts(SID) == [] and db.texts(SID) == []


def test_recording_missing_from_s3_fails_after_the_last_attempt(api, db, queues, fake_analysis, drain):
    db.add_service(SID, 1)
    db.add_media(SID, f"{SID}_audio.wav")  # 資料表有位置，S3 卻沒有這個檔案

    api.post("/analyze", json={"service_id": SID})
    drain()  # 三次都下載失敗
    job = db.job(SID)
    assert db.status(SID) == 1  # 改回 1，使用者可以再按一次「分析」
    assert (job["job_state"], job["attempts"]) == ("failed", 3) and "404" in job["last_error"]
    assert fake_analysis.calls == []  # 下載失敗就是工作失敗，不會拿缺檔的資料算出一堆 -1
    assert db.scores(SID) is None
    assert queues.dead() == 1


def test_failed_database_write_leaves_no_partial_results(service, api, db, add_recording, fake_analysis,
                                                         deny_procedure, drain):
    deny_procedure("sp_insert_charts_storage")  # 分數、逐字稿都寫了，寫第一張圖時才失敗
    analyze(api, db, add_recording)
    drain()
    job = db.job(SID)
    assert db.status(SID) == 1 and (job["job_state"], job["attempts"]) == ("failed", 3)
    assert "1370" in job["last_error"]  # execute command denied
    assert (db.scores(SID), db.texts(SID), db.charts(SID)) == (None, [], [])  # 同一個交易：整筆回滾
    assert job["report_data_key"] is None and job["result_token"] is None
    # 每一次都先上傳了自己的一份（檔名各帶自己的 token），資料庫一份都沒有引用
    listed = service.s3.list_objects_v2(Bucket=service.S3_BUCKET, Prefix="charts/")["Contents"]
    assert len(listed) == 3 * len(fake_analysis.CHARTS)
