# tests/integration/test_fake_worker.py — 假分析在 worker（worker.py）裡跑（docs/rebuild/sqs/PLAN.md 第 2、3 節、T49）：
# analysis_queue.Worker 領到一則 → process(job)：下載、假分數、圖表、上傳（檔名帶這一次的 token）→ 一個交易提交
# （分數、圖表、逐字稿的位置、工作 done、狀態 3）→ 刪訊息 → 服務結果排入。失敗就重試，這一輪的次數用完才把狀態改回 1。
# 只有第 0 階段假服務有這份；移植版的在 analyzer/tests/。佇列本身（認領、續期、重複的訊息、DLQ）在共用的 tests/queue/。
import glob
import os
import tempfile
import wave

import pytest

import analysis_queue
from fake_helpers import FailFirst, Recorder, add_recording, chart_rows, job, keys, tag, text_rows

pytestmark = pytest.mark.integration
SID = "S20261102100000"
SCORE_COLUMNS = ("customer_total", "server_total", "customer_text", "server_text",
                 "customer_audio", "server_audio", "customer_facial", "server_facial")


def scores(db, sid=SID):
    rows = db.query(f"SELECT {', '.join(SCORE_COLUMNS)} FROM tbl_service_score WHERE service_id = %s", sid)
    return dict(zip(SCORE_COLUMNS, rows[0])) if rows else None


def test_a_job_goes_from_the_queue_to_status_3_under_its_own_file_names(api, service, db, tmp_path, queues, drain,
                                                                         worker_module):
    add_recording(service, db, tmp_path, SID)
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    assert db.status(SID) == 3
    row = job(db, SID)
    assert (row["job_state"], row["attempts"], row["lease_token"], row["last_error"]) == ("done", 1, None, None)
    have = {"cam0": True, "cam1": True, "audio": True}
    assert scores(db) == pytest.approx(worker_module.placeholder_scores(SID, have))

    run = tag(db, SID)  # 這一次的 token 的前 12 個字：圖表、逐字稿、report-data 的名字都帶它
    names = [f"{SID}_{run}_01_summary.png", f"{SID}_{run}_02_audio_level.png", f"{SID}_{run}_03_video_frames.png"]
    assert chart_rows(db, SID) == [(name, f"s3://{service.S3_BUCKET}/charts/{name}") for name in names]
    assert keys(service, "charts/") == [f"charts/{name}" for name in names]
    for name in names:
        assert service.s3.head_object(Bucket=service.S3_BUCKET, Key=f"charts/{name}")["ContentType"] == "image/png"
    assert row["report_data_key"] == analysis_queue.report_data_key(SID, row["result_token"])
    assert keys(service, "report-data/") == [row["report_data_key"]]  # 沒有固定名字的 report-data/{SID}.json
    transcript = f"{SID}_{run}_transcript.txt"
    assert text_rows(db, SID) == [(transcript, f"s3://{service.S3_BUCKET}/text/{transcript}")]
    assert keys(service, "text/") == [f"text/{transcript}"]

    assert (queues.count(queues.main), queues.count(queues.dlq)) == (0, 0)  # 提交之後才刪訊息
    assert glob.glob(os.path.join(tempfile.gettempdir(), f"{analysis_queue.WORKDIR_PREFIX}{SID}-*")) == []  # 錄影檔不留


def test_the_outcome_is_queued_after_the_commit_with_the_jobs_token(api, service, db, tmp_path, drain, worker_module,
                                                                    monkeypatch):
    enqueued = Recorder(db)
    monkeypatch.setattr(worker_module.outcome_jobs, "enqueue", enqueued)
    add_recording(service, db, tmp_path, SID)
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    token = job(db, SID)["result_token"]
    # 排入的時候已經提交了；服務結果的 run 編號就是 token
    assert enqueued.calls == [{"service_id": SID, "run_id": token, "status": 3, "job_state": "done",
                               "transcript_key": f"text/{SID}_{token[:12]}_transcript.txt"}]
    assert db.query("SELECT service_outcome_run FROM tbl_service WHERE service_id = %s", SID) == [(token,)]


def test_a_failing_analysis_is_retried_then_fails_and_retry_starts_a_new_round(api, service, db, tmp_path, queues,
                                                                              drain, worker_module, monkeypatch):
    add_recording(service, db, tmp_path, SID)
    during = []  # 每一次失敗時服務的狀態

    def counted(*args, **kwargs):
        during.append(db.status(SID))
        return True

    denied = FailFirst(worker_module.s3.upload_file, PermissionError("AccessDenied"), times=3, when=counted)
    monkeypatch.setattr(worker_module.s3, "upload_file", denied)  # 例如 EC2 role 還沒有 charts/* 的 PutObject
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["retry", "retry", "failed"]
    assert during == [2, 2, 2]  # 還有次數時維持 2，不是每次例外就寫回 1（教學 6-4）
    assert db.status(SID) == 1  # 這一輪的 3 次都失敗：桌面端顯示失敗、可以 Retry
    row = job(db, SID)
    assert (row["job_state"], row["attempts"], row["lease_token"]) == ("failed", 3, None)
    assert row["last_error"] == "PermissionError: AccessDenied"
    assert scores(db) is None and chart_rows(db, SID) == []
    assert (queues.count(queues.main), queues.count(queues.dlq)) == (0, 1)  # 最後一次失敗之後的那次接收移到 DLQ

    retry = api.post("/analyze", json={"service_id": SID})  # 桌面端的 Retry：開新的一輪，次數重算
    assert retry.get_json()["state"] == "queued" and db.status(SID) == 2
    assert drain() == ["done"]
    assert db.status(SID) == 3
    row = job(db, SID)
    assert (row["job_state"], row["attempts"], row["last_error"]) == ("done", 1, None)


def test_a_failed_attempt_leaves_only_files_nobody_refers_to(api, service, db, tmp_path, drain, worker_module,
                                                            monkeypatch):
    """第一次在上傳 report-data 時失敗（圖表已經傳上去了）：重試用新的 token，資料庫只引用提交的那一次。"""
    enqueued = Recorder(db)
    monkeypatch.setattr(worker_module.outcome_jobs, "enqueue", enqueued)
    put = FailFirst(worker_module.s3.put_object, ConnectionError("S3 timed out"),
                    when=lambda **kwargs: kwargs["Key"].startswith("report-data/"))
    monkeypatch.setattr(worker_module.s3, "put_object", put)
    add_recording(service, db, tmp_path, SID)
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["retry", "done"]
    token = job(db, SID)["result_token"]
    committed = [f"charts/{name}" for name, _ in chart_rows(db, SID)]
    assert len(committed) == 3 and all(key.startswith(f"charts/{SID}_{token[:12]}_") for key in committed)
    leftovers = sorted(set(keys(service, "charts/")) - set(committed))
    assert len(leftovers) == 3 and all(token[:12] not in key for key in leftovers)  # 第一次的圖：在 S3，沒有人引用
    assert keys(service, "report-data/") == [f"report-data/{SID}_{token[:12]}.json"]
    assert [call["run_id"] for call in enqueued.calls] == [token]  # 失敗的那一次沒有排入服務結果


def test_a_result_whose_lease_was_taken_over_is_not_committed(api, service, db, tmp_path, queues, worker, drain,
                                                             worker_module, monkeypatch):
    """算完之前 lease 被別的 worker 接手了（例如這個 worker 卡太久，lease 到期）：這一次的結果整個不寫，也不排服務結果。"""
    enqueued = Recorder(db)
    monkeypatch.setattr(worker_module.outcome_jobs, "enqueue", enqueued)
    process = worker.process

    def taken_over(work):
        prepared = process(work)
        db.query("UPDATE tbl_analysis_job SET lease_token = %s WHERE service_id = %s", "f" * 32, SID)
        return prepared

    monkeypatch.setattr(worker, "process", taken_over)
    add_recording(service, db, tmp_path, SID)
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["lost"]
    assert db.status(SID) == 2 and job(db, SID)["lease_token"] == "f" * 32  # 接手的 worker 還在做
    assert scores(db) is None and chart_rows(db, SID) == [] and text_rows(db, SID) == []
    assert enqueued.calls == []
    assert queues.count(queues.main) == 1  # 不刪：接手的提交之後，這一則再出現時會被當成重複的刪掉


def test_a_missing_recording_file_means_no_data_for_it(api, service, db, tmp_path, drain, caplog):
    """資料表有登記、S3 上卻沒有檔案：記一行 log，那一種就沒有資料，分析照樣完成（和 SQS 之前的假服務一樣）。"""
    add_recording(service, db, tmp_path, SID, cams=("cam0",), audio=False)
    missing = f"{SID}_cam1.avi"
    db.query("CALL sp_insert_video_storage(%s, %s, %s, NOW())", SID, missing,
             f"s3://{service.S3_BUCKET}/video/{missing}")
    api.post("/analyze", json={"service_id": SID})
    assert drain() == ["done"]
    assert db.status(SID) == 3
    result = scores(db)
    assert result["customer_facial"] >= 60
    assert (result["server_facial"], result["customer_audio"], result["server_text"]) == (-1, -1, -1)
    run = tag(db, SID)
    assert [name for name, _ in chart_rows(db, SID)] == [f"{SID}_{run}_01_summary.png",
                                                         f"{SID}_{run}_03_video_frames.png"]
    assert f"download failed: video/{missing}" in caplog.text
    assert text_rows(db, SID) == []  # 沒有錄音就沒有逐字稿


def test_the_audio_chart_mixes_stereo_down_and_refuses_8_bit_audio(worker_module, tmp_path):
    """桌面端錄的是 16-bit；雙聲道平均成一聲道，不是 16-bit 的錄音畫不出來（那一次分析失敗、重試）。"""
    for name, channels, width in (("stereo.wav", 2, 2), ("8bit.wav", 1, 1)):
        with wave.open(str(tmp_path / name), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(width)
            out.setframerate(8000)
            out.writeframes(bytes(8000 * channels * width))
    worker_module.chart_audio(str(tmp_path / "stereo.wav"), str(tmp_path / "stereo.png"))
    assert (tmp_path / "stereo.png").stat().st_size > 0
    with pytest.raises(ValueError, match="expect 16-bit PCM"):
        worker_module.chart_audio(str(tmp_path / "8bit.wav"), str(tmp_path / "8bit.png"))
