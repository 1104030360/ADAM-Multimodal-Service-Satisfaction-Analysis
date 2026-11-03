# analysis_service/worker.py — 第 0 階段假服務的 worker：從 SQS 領工作、做假分析（docs/rebuild/sqs/PLAN.md 第 2、3 節、T49）
# 和 API（app.py）同一個映像檔，另開容器跑 `python worker.py`。領工作、在 RDS 認領、續期、提交、重試、DLQ
# 都在共用的 analysis_queue.py；這裡只管「怎麼分析、結果寫到哪裡」：
#   process(job)：下載錄影、錄音 → 假分數、3 張圖 → 上傳（檔名帶這一次的 token）→ 交回 Prepared。不寫資料庫。
#   write_results(cur, …)：analysis_queue.commit() 確認 lease 之後，在同一個交易裡寫分數、圖表、逐字稿的位置。
# new-feature N4、N5：順便存一份報表要畫的假數字（report-data/{service_id}_{tag}.json，tag 是 token 的前 12 個字；
#   提交時記在 tbl_analysis_job.report_data_key，報表從那裡讀）。
# new-feature N6：有錄音時再存一份假的逐字稿（text/{service_id}_{tag}_transcript.txt），讓報表的 AI 說明走得完。
# PLAN1 W1：同一個交易清掉上一次的服務結果；提交成功之後才把這一次（run 編號＝token）排進這個 process 自己的 OutcomeJobs。
import logging
import os
import random
import sys
import wave
from contextlib import closing

import boto3
import cv2
import matplotlib
import numpy as np
from mysql.connector import pooling

import analysis_queue
import report_model
import service_outcome

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

log = logging.getLogger("adam-analysis.worker")

S3_BUCKET = os.environ["S3_BUCKET"]
SCORE_COLS = ["customer_total", "server_total", "customer_text", "server_text",
              "customer_audio", "server_audio", "customer_facial", "server_facial"]
# 登記逐字稿時資料庫還沒準備好：adam_analysis 沒有 sp_insert_text_storage 的 EXECUTE（1370）、程序不存在（1305）。
# 這兩個只撤回 CALL 那一句，同一個交易的其他寫入照樣提交（和 service_outcome.SETUP_ERRORS 同一個做法）
TRANSCRIPT_SETUP_ERRORS = frozenset({1305, 1370})
# tbl_analysis_worker.detail：python -m analysis_queue status 一看就知道這個 worker 是假服務
DETAIL = "phase 0 placeholder analysis (no models)"

s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
pool = pooling.MySQLConnectionPool(
    pool_name="adam-worker", pool_size=4,  # 主迴圈＋heartbeat＋服務結果的 worker，pool 用完會直接丟錯
    host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")), user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"],
    time_zone=os.getenv("DB_TIME_ZONE", "America/Los_Angeles"))  # new-feature PLAN R4：和 RDS 的 time_zone 相同

CJK_FONT = os.getenv("CJK_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if os.path.exists(CJK_FONT):
    font_manager.fontManager.addfont(CJK_FONT)
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=CJK_FONT).get_name()
plt.rcParams["axes.unicode_minus"] = False

# 服務結果與待辦（PLAN1 W1）：每個 worker process 自己的佇列與 worker（教學第 7 節），AI 慢或失敗不會擋住下一筆分析
outcome_jobs = service_outcome.OutcomeJobs(pool, s3, S3_BUCKET)


# ---------- DB helpers ----------
def q(sql, args=(), fetch=False):
    with closing(pool.get_connection()) as conn, closing(conn.cursor()) as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows


def load_media(sid):
    videos = q("SELECT video_name, video_url FROM tbl_video_storage WHERE service_id=%s", (sid,), fetch=True)
    audios = q("SELECT audio_url FROM tbl_audio_storage WHERE service_id=%s", (sid,), fetch=True)

    def pick(cam):
        # 同一個錄影視窗連錄時，record_widget.py 不會清空 video_filenames，會殘留上一筆的列；
        # 檔名開頭就是 service_id（時間戳），取字典序最大＝最新的那一筆。
        cands = [u for _, u in videos if u and os.path.basename(u).endswith(f"_{cam}.avi")]
        return max(cands, key=os.path.basename) if cands else None

    audio = [u for (u,) in audios if u]
    return {"cam0": pick("cam0"), "cam1": pick("cam1"), "audio": audio[-1] if audio else None}


def write_results(cur, sid, scores, chart_names, transcript_name, run_id):
    """在 analysis_queue.commit() 的交易裡呼叫（lease 已經確認、服務那一列已經鎖住；不 commit、不開新連線）：
    分數、圖表（依顯示順序）、逐字稿的位置，加上清掉上一次的服務結果（PLAN1 W1）。

    回傳這一次能不能填服務結果：逐字稿登記好了，而且 clear() 成功（資料庫還沒套用 005 或它的 GRANT 時是 False）。
    """
    cur.execute("DELETE FROM tbl_service_score WHERE service_id=%s", (sid,))
    cols = ", ".join(["service_id"] + SCORE_COLS)
    marks = ", ".join(["%s"] * (len(SCORE_COLS) + 1))
    cur.execute(f"INSERT INTO tbl_service_score ({cols}) VALUES ({marks})", [sid] + [scores[c] for c in SCORE_COLS])
    cur.execute("DELETE FROM tbl_charts_storage WHERE service_id=%s", (sid,))
    for name in chart_names:  # 依顯示順序插入（charts_id 自增 → 用戶端讀到的順序）
        # charts_name 依 docs/rebuild/db_schema.sql；上傳時間欄位用資料表預設值
        cur.execute("INSERT INTO tbl_charts_storage (service_id, charts_name, charts_url) VALUES (%s, %s, %s)",
                    (sid, name, f"s3://{S3_BUCKET}/charts/{name}"))
    recorded = transcript_name is not None and record_transcript(cur, sid, transcript_name)
    # PLAN1 W1：上一次的結果與待辦清成 NULL、換成這次的編號；資料庫還沒套用 005 時跳過（分數照樣提交）
    cleared = service_outcome.clear(cur, sid, run_id)
    return recorded and cleared


def record_transcript(cur, sid, name):
    """用 sp_insert_text_storage 登記逐字稿的位置（和移植版一樣），在提交的同一個交易裡。回傳登記好了沒有。

    adam_analysis 還沒有這個程序的 EXECUTE（或程序不存在）時只記一行 log：分析照樣完成，
    報表的 AI 說明只是少了 Text 那一列（No data），服務結果（W1）也不填。其他錯誤往外丟（整個交易失敗、重試）。
    """
    try:
        cur.execute("CALL sp_insert_text_storage(%s, %s, %s)", (sid, name, f"s3://{S3_BUCKET}/text/{name}"))
    except Exception as exc:
        if getattr(exc, "errno", None) not in TRANSCRIPT_SETUP_ERRORS:
            raise
        log.warning("fake transcript not saved for %s (MySQL %s): grant adam_analysis EXECUTE on "
                    "sp_insert_text_storage", sid, exc.errno)
        return False
    return True


# ---------- 假分數與圖表 ----------
def placeholder_scores(sid, have):
    rnd = random.Random(sid)  # 同一個 service_id 每次產生相同的假分數

    def pick(ok):
        return round(rnd.uniform(60, 95), 1) if ok else -1

    s = {}
    for who, cam in (("customer", "cam0"), ("server", "cam1")):
        s[f"{who}_facial"] = pick(have[cam])
        s[f"{who}_audio"] = pick(have["audio"])
        s[f"{who}_text"] = pick(have["audio"])
        parts = [s[f"{who}_{m}"] for m in ("facial", "audio", "text") if s[f"{who}_{m}"] >= 0]
        s[f"{who}_total"] = round(sum(parts) / len(parts), 1) if parts else -1
    return s


def color_for(v):
    if v < 0:
        return "#9e9e9e"
    return "#2e7d32" if v >= 85 else ("#ef6c00" if v >= 70 else "#c62828")


def chart_summary(sid, scores, path):
    labels = [("total", "總分"), ("facial", "人臉"), ("audio", "聲音"), ("text", "文字")]
    fig, axes = plt.subplots(1, 2, figsize=(16, 9), dpi=100, sharey=True)
    for ax, (who, title) in zip(axes, (("customer", "顧客"), ("server", "服務人員"))):
        vals = [scores[f"{who}_{k}"] for k, _ in labels]
        bars = ax.bar([n for _, n in labels], [max(v, 0) for v in vals], color=[color_for(v) for v in vals])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, max(v, 0) + 1.5,
                    "無資料" if v < 0 else f"{v:.1f}", ha="center", fontsize=16)
        ax.axhline(85, ls="--", c="#2e7d32", lw=1)
        ax.axhline(70, ls="--", c="#ef6c00", lw=1)
        ax.set_ylim(0, 105)
        ax.set_title(title, fontsize=20)
    fig.suptitle(f"服務 {sid} 分數總覽（第 0 階段假資料）", fontsize=24)
    fig.text(0.5, 0.45, "PLACEHOLDER", fontsize=90, color="gray", alpha=0.15,
             ha="center", va="center", rotation=20)
    fig.savefig(path)
    plt.close(fig)


def chart_audio(wav_path, path, win_sec=0.5):
    with wave.open(wav_path, "rb") as wf:
        sr, n, ch, sw = wf.getframerate(), wf.getnframes(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(n)
    if sw != 2:
        raise ValueError(f"expect 16-bit PCM, got sampwidth={sw}")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    hop = max(1, int(sr * win_sec))
    usable = len(x) // hop * hop
    frames = x[:usable].reshape(-1, hop) if usable else x.reshape(1, -1)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) / 32768.0 + 1e-9)
    t = np.arange(len(db)) * win_sec
    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    ax.plot(t, db, lw=1)
    ax.set_xlabel("秒")
    ax.set_ylabel("音量 (dBFS)")
    ax.set_title(f"錄音音量（長度 {n / sr:.1f} 秒、{sr} Hz、{ch} 聲道）", fontsize=20)
    fig.savefig(path)
    plt.close(fig)


def video_thumbs(video_path, k=6):
    cap = cv2.VideoCapture(video_path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    imgs = []
    for i in (np.linspace(0, n - 1, num=k, dtype=int) if n > 0 else []):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if ok:
            imgs.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()
    return n, imgs


def chart_frames(cam0, cam1, path, k=6):
    fig, axes = plt.subplots(2, k, figsize=(16, 9), dpi=100)
    for row, (label, vp) in enumerate((("顧客 cam0", cam0), ("員工 cam1", cam1))):
        n, imgs = video_thumbs(vp, k) if vp else (0, [])
        for j in range(k):
            ax = axes[row][j]
            ax.axis("off")
            if j < len(imgs):
                ax.imshow(imgs[j])
        axes[row][0].set_title(f"{label}：{n} 影格", loc="left", fontsize=16)
    fig.suptitle("影片抽樣畫面（確認下載與解碼正常）", fontsize=22)
    fig.savefig(path)
    plt.close(fig)


def video_seconds(path):
    cap = cv2.VideoCapture(path)
    frames, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0, cap.get(cv2.CAP_PROP_FPS) or 0
    cap.release()
    return frames / fps if frames > 0 and fps > 0 else 0


def fake_report_data(sid, scores, local):
    """報表要畫的數字（report-data JSON，格式見 report_model.py）：和假分數一致的比例與每秒的表情，不是分析出來的。

    Audio、Text：分數越高，POSITIVE 越多、NEGATIVE 越少。Facial：影片有幾秒就有幾個點，表情偶爾換一次。
    同一個 service_id 每次產生一樣的數字。
    """
    rnd = random.Random(f"{sid}:report-data")

    def proportions(score):
        if score < 0:
            return None
        tone = (score - 60) / 35  # 60～95 → 0～1
        positive = max(0.02, 0.15 + 0.55 * tone + rnd.uniform(-0.08, 0.08))
        negative = max(0.02, 0.30 - 0.25 * tone + rnd.uniform(-0.05, 0.05))
        neutral = max(0.02, 1 - positive - negative)
        total = positive + neutral + negative
        return {"positive": positive / total, "neutral": neutral / total, "negative": negative / total}

    def wave(score, cam):
        seconds = int(video_seconds(local[cam])) if score >= 0 and cam in local else 0
        if seconds <= 0:
            return None
        tone = (score - 60) / 35
        weights = [0.25 - 0.2 * tone, 0.55 - 0.4 * tone, 0.2 + 0.6 * tone]  # NEGATIVE、NEUTRAL、POSITIVE
        value, points = 0, []
        for second in range(seconds + 1):
            if rnd.random() < 0.25:
                value = rnd.choices([-1, 0, 1], weights)[0]
            points.append((second, value))
        return points

    return report_model.build_report_data(
        sid, source="placeholder",
        audio={"staff": proportions(scores["server_audio"]), "customer": proportions(scores["customer_audio"])},
        text={"staff": proportions(scores["server_text"]), "customer": proportions(scores["customer_text"])},
        facial={"staff": wave(scores["server_facial"], "cam1"), "customer": wave(scores["customer_facial"], "cam0")})


# new-feature N6：假的逐字稿。只用來走完「報表 → 讀逐字稿 → AI 說明」的流程（PLAN 9：可以先用假資料把流程做好，
# 但不要拿給使用者看）。每一句都標 (placeholder)，一看就知道是假的；格式和 analyzer.py 的 process_text() 寫的一樣：
# 說話者一行（原文）、Translated 一行（英文）、空一行。PLAN1 W1 從它填出來的服務結果也是假的，同樣不給使用者看（T44）。
FAKE_TRANSCRIPT = (
    ("Customer", "（假逐字稿）你好，我想查一下我的訂單。", "(placeholder) Hello, I would like to check on my order."),
    ("Staff", "（假逐字稿）好的，可以給我您的訂單號碼嗎？", "(placeholder) Sure, could you give me your order number?"),
    ("Customer", "（假逐字稿）我已經等了一陣子了。", "(placeholder) I have been waiting for a while now."),
    ("Staff", "（假逐字稿）抱歉讓您久等，我馬上幫您處理。",
     "(placeholder) Sorry for the wait, I will take care of it right away."),
    ("Customer", "（假逐字稿）好，謝謝。", "(placeholder) Okay, thank you."),
    ("Staff", "（假逐字稿）不客氣，還有其他需要嗎？", "(placeholder) You are welcome, is there anything else?"),
)


def fake_transcript():
    lines = []
    for speaker, original, english in FAKE_TRANSCRIPT:
        lines += [f"{speaker}: {original}", f"Translated: {english}\n"]
    return "\n".join(lines)


def save_fake_transcript(sid, name):
    """上傳 text/{name}（登記位置在提交的交易裡，record_transcript()）。回傳上傳了沒有。

    做不到時（EC2 role 還沒有 text/* 的 PutObject）只記一行 log：分析照樣完成，
    報表的 AI 說明只是少了 Text 那一列（No data），服務結果（W1）也不填。
    """
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=f"text/{name}", Body=fake_transcript().encode("utf-8"),
                      ContentType="text/plain; charset=utf-8")
    except Exception as exc:
        log.warning("fake transcript not saved for %s (%s)", sid, type(exc).__name__)
        return False
    return True


# ---------- 一筆工作（analysis_queue.Worker 呼叫） ----------
def download_media(sid, workdir):
    """把這筆的錄影、錄音下載到 workdir，回傳 {cam0、cam1、audio: 本機路徑}。
    下載不到的只記一行 log、那一種就沒有資料（分數 -1、少一張圖），和 SQS 之前一樣不算失敗。"""
    local = {}
    for kind, url in load_media(sid).items():
        if not url:
            continue
        name = os.path.basename(url)
        prefix = "audio" if kind == "audio" else "video"
        dst = os.path.join(workdir, name)
        try:
            s3.download_file(S3_BUCKET, f"{prefix}/{name}", dst)  # 與用戶端相同的 key 慣例
            local[kind] = dst
        except Exception:
            log.warning("download failed: %s/%s", prefix, name)
    return local


def process(job):
    """一筆假分析（PLAN 第 2 節的 process(job)）：下載 → 假分數、圖表 → 上傳輸出 → 交回 analysis_queue.Prepared。

    不寫資料庫、不改狀態，失敗就讓例外丟出去：重試、次數、最後一次失敗時狀態 2 → 1 都由 analysis_queue 負責。
    輸出的檔名都帶這一次的 token（analysis_queue.run_name()）：被搶走 lease 的舊 worker 晚到的上傳不會蓋掉
    提交的那一份，資料庫只引用提交成功的那一次。暫存的 job.workdir 由 analysis_queue 建立、做完一定刪。
    """
    sid, token, work = job.service_id, job.token, job.workdir
    local = download_media(sid, work)
    have = {k: k in local for k in ("cam0", "cam1", "audio")}
    scores = placeholder_scores(sid, have)

    def output(name):  # 這一次的檔名：{sid}_{tag}_01_summary.png…（在 workdir 裡用同一個名字畫、上傳）
        return analysis_queue.run_name(sid, token, f"{sid}_{name}")

    names = [output("01_summary.png")]
    chart_summary(sid, scores, os.path.join(work, names[-1]))
    if "audio" in local:
        names.append(output("02_audio_level.png"))
        chart_audio(local["audio"], os.path.join(work, names[-1]))
    if "cam0" in local or "cam1" in local:
        names.append(output("03_video_frames.png"))
        chart_frames(local.get("cam0"), local.get("cam1"), os.path.join(work, names[-1]))
    for name in names:
        s3.upload_file(os.path.join(work, name), S3_BUCKET, f"charts/{name}",
                       ExtraArgs={"ContentType": "image/png"})
    # 報表（/reports）要畫的數字：和圖表一樣先上傳；key 帶 token，提交時記在 tbl_analysis_job.report_data_key
    data_key = analysis_queue.report_data_key(sid, token)
    s3.put_object(Bucket=S3_BUCKET, Key=data_key,
                  Body=report_model.dumps_report_data(fake_report_data(sid, scores, local)),
                  ContentType="application/json")
    transcript = output("transcript.txt") if have["audio"] else None  # 和移植版一樣：有錄音才有逐字稿
    if transcript is not None and not save_fake_transcript(sid, transcript):
        transcript = None

    fill = {"outcome": False}  # write() 寫完之後：這一次能不能填服務結果

    def write(cur):
        fill["outcome"] = write_results(cur, sid, scores, names, transcript, token)

    def after_commit():
        if fill["outcome"]:  # PLAN1 W1：提交之後才排入，放進自己的佇列就好，不等 AI（狀態 3 已經提交了）
            outcome_jobs.enqueue(sid, token, f"text/{transcript}")

    return analysis_queue.Prepared(write, report_data_key=data_key, after_commit=after_commit)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    outcome_jobs.start()
    # 清暫存資料夾 → 登記 → 一直領工作，直到 SIGTERM（假服務沒有模型要載入）
    sys.exit(analysis_queue.serve(pool, process, detail=DETAIL))
