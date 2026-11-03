# tests/integration/fake_helpers.py — 假服務自己的整合測試共用的小工具（只有 analysis_service/ 有；和 analyzer/ 共用的在
# report_helpers.py）。都用測試自己的 root 連線（conftest.py 的 db）準備資料、讀結果。
import wave
from datetime import datetime

import cv2
import numpy as np

from report_helpers import add_scored_service

JOB_COLUMNS = ("job_state", "attempts", "max_attempts", "lease_token", "result_token", "report_data_key", "last_error")


def avi(path, seconds=3, fps=5):
    """seconds 秒的灰色小影片（MJPG、32×32），回傳檔案內容。"""
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (32, 32))
    for _ in range(seconds * fps):
        out.write(np.full((32, 32, 3), 128, np.uint8))
    out.release()
    return path.read_bytes()


def wav(path, seconds=1, rate=8000):
    """seconds 秒的靜音（16-bit、單聲道），回傳檔案內容。"""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(np.zeros(rate * seconds, np.int16).tobytes())
    return path.read_bytes()


def add_recording(service, db, tmp_path, sid, cams=("cam0", "cam1"), audio=True, start=None):
    """桌面端存好的一段錄影（狀態 1，還沒送分析）：S3 上有檔案，資料表用預存程序登記位置（和 record_widget.py 一樣）。"""
    add_scored_service(db, sid, start or datetime(2026, 11, 2, 9), status=1, no_scores=True)
    for cam in cams:
        name = f"{sid}_{cam}.avi"
        service.s3.put_object(Bucket=service.S3_BUCKET, Key=f"video/{name}", Body=avi(tmp_path / name))
        db.query("CALL sp_insert_video_storage(%s, %s, %s, NOW())", sid, name,
                 f"s3://{service.S3_BUCKET}/video/{name}")
    if audio:
        name = f"{sid}_audio.wav"
        service.s3.put_object(Bucket=service.S3_BUCKET, Key=f"audio/{name}", Body=wav(tmp_path / name))
        db.query("CALL sp_insert_audio_storage(%s, %s, %s, NOW())", sid, name,
                 f"s3://{service.S3_BUCKET}/audio/{name}")


def job(db, sid):
    """tbl_analysis_job 的一列（dict）；沒有是 None。"""
    rows = db.query(f"SELECT {', '.join(JOB_COLUMNS)} FROM tbl_analysis_job WHERE service_id = %s", sid)
    return dict(zip(JOB_COLUMNS, rows[0])) if rows else None


def tag(db, sid):
    """提交成功的那一次的 tag（token 的前 12 個字）：這一次的輸出檔名都帶它。"""
    return job(db, sid)["result_token"][:12]


def chart_rows(db, sid):
    """圖表列，照 charts_id（桌面端顯示的順序）。"""
    return db.query("SELECT charts_name, charts_url FROM tbl_charts_storage WHERE service_id = %s ORDER BY charts_id",
                    sid)


def text_rows(db, sid):
    return db.query("SELECT text_name, text_url FROM tbl_text_storage WHERE service_id = %s ORDER BY text_name", sid)


def keys(service, prefix):
    """S3 上 prefix 開頭的 key（排好）。"""
    listed = service.s3.list_objects_v2(Bucket=service.S3_BUCKET, Prefix=prefix)
    return sorted(item["Key"] for item in listed.get("Contents", []))


class FailFirst:
    """取代一個函式：符合 when(*args, **kwargs) 的呼叫，前 times 次丟出 error，之後（和其他呼叫）照原本的做。"""

    def __init__(self, function, error, times=1, when=lambda *args, **kwargs: True):
        self.function, self.error, self.left, self.when = function, error, times, when
        self.failed = 0

    def __call__(self, *args, **kwargs):
        if self.left > 0 and self.when(*args, **kwargs):
            self.left -= 1
            self.failed += 1
            raise self.error
        return self.function(*args, **kwargs)


class Recorder:
    """取代 outcome_jobs.enqueue：記下每一次排入（和當時資料庫裡的狀態），不真的排進服務結果的佇列。"""

    def __init__(self, db):
        self.db, self.calls = db, []

    def __call__(self, service_id, run_id, transcript_key):
        status = self.db.status(service_id)
        state = job(self.db, service_id)["job_state"]
        self.calls.append({"service_id": service_id, "run_id": run_id, "transcript_key": transcript_key,
                           "status": status, "job_state": state})
