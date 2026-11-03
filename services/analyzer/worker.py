# analyzer/worker.py — 移植版分析服務的 worker：python worker.py（和 API 同一個映像檔，另外的容器；一台 EC2 可以開兩個）。
# SQS（docs/rebuild/sqs/PLAN.md 第 2、3 節、T49）：analysis_queue.serve() 登記、載入模型、從 SQS 一次領一則、在 RDS 認領、
#   續期、提交、刪訊息。這裡只管「怎麼分析、結果寫到哪裡」：
#   process(job)：下載錄影、錄音到 job.workdir → analyzer.process_service → 總分 → 輸出改成帶 token 的檔名上傳 → Prepared。
#   寫資料庫（write_results）在 analysis_queue.commit() 的交易裡，和工作 done、服務狀態 3 一起提交。
#   失敗就讓例外丟出去：重試、次數、狀態 2 → 1 都由 analysis_queue 負責（PLAN 6-4），這裡不改狀態。
# PLAN1 W1：同一個交易清掉上一次的服務結果；提交之後有逐字稿就排進這個 process 自己的 OutcomeJobs（run 編號就是 token）。
import logging
import os
import sys

import boto3
from mysql.connector import pooling

import analysis_queue
import report_model
import service_outcome
from predict_0 import predict_total_scores

S3_BUCKET = os.environ["S3_BUCKET"]
# /healthz 看得到的 worker 說明（tbl_analysis_worker.detail）。和 speech.py 讀同一個變數、同一個預設；
# 不 import speech（會 import torch），serve() 才能在 import 那些大套件之前就登記 loading
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")

s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
pool = pooling.MySQLConnectionPool(
    pool_name="adam-worker", pool_size=4,  # 主迴圈 + heartbeat + 服務結果的 worker + 關機時交還，pool 用完會直接丟錯
    host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")), user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"],
    time_zone=os.getenv("DB_TIME_ZONE", "America/Los_Angeles"))  # new-feature PLAN R4：和 RDS 的 time_zone 相同
# 服務結果與待辦（PLAN1 W1）：每個 worker process 自己一份（教學第 7 節），AI 慢或失敗不會擋住下一筆分析
outcome_jobs = service_outcome.OutcomeJobs(pool, s3, S3_BUCKET)


# ---------- 模型 ----------
def load_models():
    """serve() 先登記 loading 才呼叫。import analyzer（torch、TensorFlow、DeepFace）也要十幾秒，算在載入裡：
    這段時間 /healthz 是 loading，不是 failed；import 失敗也會登記成 failed、寫下原因，不會讓容器一直重新啟動。"""
    import analyzer
    analyzer.load_models()


# ---------- DB ----------
def load_media(sid):
    """這筆服務的錄影、錄音在 S3 的位置 {"cam0", "cam1", "audio"}，沒有的是 None。"""
    with analysis_queue.transaction(pool) as cur:
        cur.execute("SELECT video_name, video_url FROM tbl_video_storage WHERE service_id=%s", (sid,))
        videos = cur.fetchall()
        cur.execute("SELECT audio_url FROM tbl_audio_storage WHERE service_id=%s", (sid,))
        audios = cur.fetchall()

    def pick(cam):
        # 同一個錄影視窗連錄時，record_widget.py 不會清空 video_filenames，會殘留上一筆的列；
        # 檔名開頭就是 service_id（時間戳），取字典序最大＝最新的那一筆。
        cands = [u for _, u in videos if u and os.path.basename(u).endswith(f"_{cam}.avi")]
        return max(cands, key=os.path.basename) if cands else None

    audio = [u for (u,) in audios if u]
    return {"cam0": pick("cam0"), "cam1": pick("cam1"), "audio": audio[-1] if audio else None}


def write_results(cur, sid, result, customer_total, staff_total, charts, transcript, run_id):
    """在 analysis_queue.commit() 的交易裡（lease 已經確認、服務那一列已經鎖住）：分數 → 逐字稿 → 圖表（依固定順序）
    → 清掉上一次的服務結果（PLAN1 W1）。不 commit、不開新連線：失敗就整個交易 rollback，這一次算失敗。
    charts、transcript 是上傳時帶 token 的檔名。

    回傳這次分析能不能填服務結果（資料庫還沒套用 migration 005 或它的 GRANT 時是 False，分數照樣提交）。
    """
    t, a, f = result["text"], result["audio"], result["face"]
    # 參數順序沿用原本的程式；原本叫 staff 的，對應資料表的 server_* 欄位
    cur.execute("CALL sp_insert_service_score(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (sid, f["customer"], a["customer"], t["customer"],
                 f["staff"], a["staff"], t["staff"], customer_total, staff_total))
    if transcript:
        cur.execute("CALL sp_insert_text_storage(%s, %s, %s)", (sid, transcript, f"s3://{S3_BUCKET}/text/{transcript}"))
    for name in charts:  # charts_id 自增，寫入順序就是用戶端的顯示順序
        cur.execute("CALL sp_insert_charts_storage(%s, %s, %s)", (sid, name, f"s3://{S3_BUCKET}/charts/{name}"))
    # 上一次的結果與待辦清成 NULL、換成這次的編號：和這次的逐字稿在同一個交易裡（service_outcome.py 開頭第 1 點）
    return service_outcome.clear(cur, sid, run_id)


# ---------- 一筆工作 ----------
def process(job):
    """analysis_queue.Worker 交來的一筆（PLAN 第 2 節）：下載 → 分析 → 上傳帶 token 的輸出 → Prepared。這裡不寫資料庫。

    錄影、錄音只放在 job.workdir（個人資料；做完、失敗都由 analysis_queue 刪掉）。下載、分析、上傳的錯誤都往外丟。
    """
    import analyzer  # load_models() 已經 import 過；測試不載入模型，conftest 先 import、換掉 process_service

    sid, token, work = job.service_id, job.token, job.workdir
    local = {}
    for kind, url in load_media(sid).items():
        if not url:
            continue
        name = os.path.basename(url)
        prefix = "audio" if kind == "audio" else "video"
        dst = os.path.join(work, name)
        s3.download_file(S3_BUCKET, f"{prefix}/{name}", dst)  # 與用戶端相同的 key 慣例；下載失敗＝這一次失敗
        local[kind] = dst

    result = analyzer.process_service(sid, local, work)
    customer_total, staff_total = predict_total_scores(result)

    # 先上傳，提交時才寫資料庫。檔名帶這一次的 token（PLAN 6-4）：被搶走 lease 的舊 worker 晚到的上傳，
    # 不會蓋掉提交的那一份；桌面端用網址的檔名當快取的名字，所以換的是檔名，不是多一層資料夾
    charts = []
    for name in result["charts"]:  # TEXT、AUDIO、wave_face、customer_face、staff_face 的固定順序
        stored = analysis_queue.run_name(sid, token, name)
        s3.upload_file(os.path.join(work, name), S3_BUCKET, f"charts/{stored}", ExtraArgs={"ContentType": "image/png"})
        charts.append(stored)
    transcript = None
    if result["transcript"]:
        transcript = analysis_queue.run_name(sid, token, result["transcript"])
        s3.upload_file(os.path.join(work, result["transcript"]), S3_BUCKET, f"text/{transcript}",
                       ExtraArgs={"ContentType": "text/plain; charset=utf-8"})
    report_key = None
    if result.get("report_data"):  # 報表（/reports）要畫的數字；key 記在工作那一列，報表只讀提交的那一份
        report_key = analysis_queue.report_data_key(sid, token)
        s3.put_object(Bucket=S3_BUCKET, Key=report_key, Body=report_model.dumps_report_data(result["report_data"]),
                      ContentType="application/json")

    written = {}

    def write(cur):
        written["outcome"] = write_results(cur, sid, result, customer_total, staff_total, charts, transcript, token)

    def after_commit():
        # PLAN1 W1：放進自己的佇列就好，不等 AI（不延後刪訊息、領下一筆）
        if written.get("outcome") and transcript:
            outcome_jobs.enqueue(sid, token, f"text/{transcript}")

    return analysis_queue.Prepared(write=write, report_data_key=report_key, after_commit=after_commit)


# ---------- 主程式 ----------
def main():
    """清掉留下的暫存資料夾、登記、載入模型、一直領工作，直到 SIGTERM（analysis_queue.serve()）。回傳結束碼。"""
    logging.basicConfig(level=logging.INFO)
    outcome_jobs.start()
    return analysis_queue.serve(pool, process, load_models=load_models, detail=f"whisper {WHISPER_MODEL}")


if __name__ == "__main__":
    sys.exit(main())
