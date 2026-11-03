"""run_e2e.sh 的每一步。在分析服務映像檔的拋棄式容器裡執行（boto3、mysql-connector、pypdf、OpenCC 都在裡面），
經由 adam-e2e-net 連到 MySQL（adam-e2e-mysql）、moto（adam-e2e-s3，S3＋SQS）和分析服務的 API（adam-e2e-api）；
worker（adam-e2e-worker）從 moto 的佇列領工作。

桌面端那一側照抄桌面端的寫法：錄影的登記（record_widget.py 的 save_recording）、新增每次服務的報表
（reports.py 的 INSERT_SERVICE_SQL）。SQS 之後（T49）桌面端收到 200 不再自己寫狀態 2：API 在接受工作的同一個交易裡寫。
這裡用 root 連資料庫；分析服務自己用最小權限的 adam_analysis（tests/integration/grants.sql），所以也驗證了這組權限夠用。

用法：python e2e_steps.py <步驟> ...（連線的設定由 run_e2e.sh 用環境變數給；結果存在 /out）
  prepare                          等 MySQL 和 moto 準備好，建立 bucket、DLQ 和主佇列（設定和 Console 的相同）
  add SID AUDIO [--video CAM0 CAM1] [--dialogue]
                                   上傳錄音（和兩支錄影），在同一個交易裡登記這筆服務（狀態 1）；--dialogue 是合成的對話
  analyze SID [--restarted]        POST /analyze：狀態 2、工作和 outbox 都在；等到 3（完成）或回到 1（失敗）；
                                   做完之後工作是 done、outbox 已送出、佇列和 DLQ 都是空的。--restarted：途中重新啟動過 worker
  verify SID                       分數、圖、逐字稿、report-data：資料庫和 S3 都檢查，存一份到 /out/SID/
  report SID                       新增這筆服務的報表，POST /reports，等到 ready，下載 PDF，檢查三張圖有沒有畫
  summary SID ...                  每筆服務的分析時間和記憶體峰值（/out/memory.log 由 run_e2e.sh 記錄）
每一步都印 PASS／FAIL，有任何 FAIL 時結束碼是 1。逐字稿只印合成的對話；使用者的錄音只印數量。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import mysql.connector
import opencc
from botocore.exceptions import BotoCoreError, ClientError
from pypdf import PdfReader

import dialogue_truth

API = os.environ["API"]
BUCKET = os.environ["S3_BUCKET"]
OUT = Path("/out")
STAFF_ACCOUNT = "00000000-0000-0000-0000-000000000002"  # 測試用 schema 的種子帳號：錄影的服務人員
ADMIN_ACCOUNT = "00000000-0000-0000-0000-000000000001"  # 按 Generate 的管理員
CHARTS = ("emotion_TEXT", "emotion_AUDIO", "emotion_wave_face", "customer_emotions_face", "staff_emotions_face")
SCORES = ("customer_text", "customer_audio", "customer_facial", "customer_total",
          "server_text", "server_audio", "server_facial", "server_total")
ANALYZE_TIMEOUT_SEC = 1800  # 一筆分析最多等多久（EC2 的目標是 1 分鐘的錄音 10 分鐘內分析完）
QUEUE_URL = os.environ["SQS_ANALYSIS_QUEUE_URL"]  # moto 的主佇列（run_e2e.sh 給）；DLQ 叫 adam-analysis-dlq
REPORT_TIMEOUT_SEC = 600    # 和桌面端一樣：10 分鐘還在 generating 就算失敗
DOWN_POLLS = 30             # /healthz 連續幾次沒回應就不等了（容器停了，例如記憶體不夠被終止；忙的時候也會慢一點）
MIN_ATTRIBUTION = 0.8       # 合成的對話：至少這麼多的字歸對人（兩個人都算成服務人員是 60%，對調是 0%）

# 桌面端的 SQL（原文）
INSERT_SERVICE_SQL = "CALL sp_insert_service(%s, %s, %s, %s, %s, @insert_service_result)"
INSERT_VIDEO_SQL = "CALL sp_insert_video_storage(%s, %s, %s, %s)"
INSERT_AUDIO_SQL = "CALL sp_insert_audio_storage(%s, %s, %s, %s)"
INSERT_REPORT_SQL = (
    "INSERT INTO tbl_report (report_id, report_kind, service_id, staff_account_id, report_status, s3_key, created_by) "
    "SELECT %s, 'service', s.service_id, s.account_id, 'generating', %s, %s "
    "FROM tbl_service AS s WHERE s.service_id = %s AND s.service_status = 3")

s3 = boto3.client("s3")  # 端點、金鑰、區域都來自環境變數（AWS_ENDPOINT_URL 指向 moto）
sqs = boto3.client("sqs", region_name=os.environ["AWS_REGION"])  # SQS 一定要區域（boto3 自己只讀 AWS_DEFAULT_REGION）
results = []


def step(title):
    print(f"\n== {title}", flush=True)


def check(ok, message):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + message, flush=True)
    return ok


def info(message):
    print("     " + message, flush=True)


# ---------- 連線 ----------
def connect(user, password, wait_sec=0):
    """MySQL 容器第一次啟動要先跑 schema、grants.sql（這段時間不開 TCP）：wait_sec 內連不上就重試。"""
    deadline = time.monotonic() + wait_sec
    while True:
        try:
            return mysql.connector.connect(host=os.environ["DB_HOST"], user=user, password=password,
                                           database=os.environ["DB_NAME"], autocommit=True)
        except mysql.connector.Error:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def root(wait_sec=0):
    return connect("root", os.environ["DB_ROOT_PASSWORD"], wait_sec)


def query(sql, *args):
    """SELECT 回傳所有的列；其他回傳影響的列數。"""
    with closing(root()) as conn, closing(conn.cursor()) as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.with_rows else cur.rowcount


def post(path, payload):
    request = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # 桌面端的讀取逾時也是 10 秒
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def healthy():
    try:
        with urllib.request.urlopen(API + "/healthz", timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def get(key):
    """S3 上的檔案；沒有這個檔是 None。"""
    try:
        return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None


def save(sid, name, data):
    folder = OUT / sid
    folder.mkdir(exist_ok=True)
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    (folder / name).write_bytes(data)


def load(sid, name):
    path = OUT / sid / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------- 步驟 ----------
def prepare():
    step("MySQL 8.4 與 moto")
    with closing(root(wait_sec=300)):
        check(True, "MySQL 已經載入 schema 和 grants.sql")
    user = os.environ["DB_USER"]
    with closing(connect(user, os.environ["DB_PASSWORD"])) as conn, closing(conn.cursor()) as cur:
        cur.execute("SHOW STATUS LIKE 'Ssl_cipher'")
        cipher = cur.fetchone()[1]
    check(cipher, f"分析服務的帳號 {user} 連得上（grants.sql 規定要 TLS：{cipher or '沒有'}）")
    deadline = time.monotonic() + 300  # moto 的容器要先 pip install
    while True:
        try:
            s3.create_bucket(Bucket=BUCKET)
            break
        except (BotoCoreError, ClientError):
            if time.monotonic() > deadline:
                raise
            time.sleep(2)
    check(True, f"moto 建好 bucket {BUCKET}")
    # 和 Console 的設定相同（教學第 3 節）：先建 DLQ，主佇列 visibility 15 分鐘、保留 4 天、long polling 20 秒、接收 3 次進 DLQ
    dlq = sqs.create_queue(QueueName="adam-analysis-dlq", Attributes={"MessageRetentionPeriod": "1209600"})["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    url = sqs.create_queue(QueueName="adam-analysis", Attributes={
        "VisibilityTimeout": "900", "MessageRetentionPeriod": "345600", "ReceiveMessageWaitTimeSeconds": "20",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"})})["QueueUrl"]
    check(url.endswith("/123456789012/adam-analysis") and QUEUE_URL.endswith("/123456789012/adam-analysis"),
          "moto 建好 adam-analysis-dlq 和 adam-analysis（maxReceiveCount 3）")


def queue_counts(name):
    """佇列裡等著的、被領走還沒刪的（近似值）。"""
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    attributes = sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])
    return (int(attributes["Attributes"]["ApproximateNumberOfMessages"]),
            int(attributes["Attributes"]["ApproximateNumberOfMessagesNotVisible"]))


def job(sid):
    rows = query("SELECT job_state, attempts, result_token, report_data_key FROM tbl_analysis_job "
                 "WHERE service_id = %s", sid)
    return rows[0] if rows else None


def add(sid, audio, video, dialogue):
    step(f"錄影完成：{sid}")
    with wave.open(audio, "rb") as recording:
        seconds = recording.getnframes() / recording.getframerate()
    start = datetime.strptime(sid[1:], "%Y%m%d%H%M%S")  # 桌面端的 service_id 就是開始錄影的時間
    files = [(path, f"video/{sid}_{cam}.avi") for path, cam in zip(video or (), ("cam0", "cam1"))]
    files.append((audio, f"audio/{sid}_audio.wav"))
    for path, key in files:
        s3.upload_file(path, BUCKET, key)
    uploaded_at = datetime.now()
    with closing(root()) as conn, closing(conn.cursor()) as cur:
        cur.execute("START TRANSACTION")  # 和 save_recording 一樣：服務和檔案在同一個交易裡
        cur.execute(INSERT_SERVICE_SQL, (sid, STAFF_ACCOUNT, start, start + timedelta(seconds=seconds), 1))
        for _, key in files:
            sql = INSERT_AUDIO_SQL if key.startswith("audio/") else INSERT_VIDEO_SQL
            cur.execute(sql, (sid, os.path.basename(key), f"s3://{BUCKET}/{key}", uploaded_at))
        cur.execute("COMMIT")
    media = ["cam0", "cam1", "audio"] if video else ["audio"]
    save(sid, "service.json", {"media": media, "seconds": round(seconds, 2), "dialogue": dialogue})
    check(True, f"上傳 {', '.join(key for _, key in files)}，登記服務（狀態 1，錄音 {seconds:.1f} 秒）")


def analyze(sid, restarted=False):
    step(f"分析 {sid}")
    code, body = post("/analyze", {"service_id": sid})
    if not check(code == 200 and body.get("state") == "queued", f"POST /analyze 回 {code} {body}"):
        return
    started = time.monotonic()
    # 桌面端不再寫 2（T49）：API 回 200 之前，工作、outbox、狀態 2 已經在同一個交易裡 commit
    status = str(query("SELECT service_status FROM tbl_service WHERE service_id = %s", sid)[0][0])
    outbox = query("SELECT COUNT(*) FROM tbl_analysis_outbox WHERE service_id = %s", sid)[0][0]
    check(status == "2" and job(sid) is not None and outbox == 1,
          f"200 之前 API 已經寫好：狀態 {status}、工作 {job(sid) and job(sid)[0]}、outbox {outbox} 筆")
    code, body = post("/analyze", {"service_id": sid})  # 桌面端重送：同一份工作，不再多一筆 outbox
    check(code == 200 and body.get("state") == "already_queued", f"再送一次回 {code} {body.get('state')}")
    down = 0
    while time.monotonic() - started < ANALYZE_TIMEOUT_SEC and down < DOWN_POLLS:
        status = str(query("SELECT service_status FROM tbl_service WHERE service_id = %s", sid)[0][0])
        if status in ("1", "3"):
            break
        down = 0 if healthy() else down + 1
        time.sleep(1)
    seconds = time.monotonic() - started
    save(sid, "analysis.json", {"status": status, "seconds": round(seconds, 1)})
    if status == "3":
        check(True, f"狀態 1 → 2 → 3：分析完成，{seconds:.1f} 秒")
        check_queue_after(sid, restarted)
        return
    if status == "1":
        check(False, f"分析失敗：狀態回到 1（{seconds:.1f} 秒），桌面端可以再按 Analyze")
        rows = {table: query(f"SELECT COUNT(*) FROM {table} WHERE service_id = %s", sid)[0][0]
                for table in ("tbl_service_score", "tbl_charts_storage", "tbl_text_storage")}
        check(not any(rows.values()), f"失敗時沒有留下一半的結果：{rows}")
    elif down >= DOWN_POLLS:
        check(False, f"分析服務沒有回應（{seconds:.1f} 秒，狀態還是 {status}）：容器可能停了")
    else:
        check(False, f"{ANALYZE_TIMEOUT_SEC} 秒內沒有分析完（狀態還是 {status}）")


def check_queue_after(sid, restarted):
    """做完之後：工作 done（只認領過 1 次；途中重新啟動 worker 交還的那一次不算）、outbox 已送出、訊息刪掉、DLQ 是空的。"""
    state, attempts, token, key = job(sid)
    check(state == "done" and attempts == 1 and token and key,
          f"工作 {state}，認領 {attempts} 次" + ("（途中重新啟動過 worker，交還的那一次不算）" if restarted else ""))
    sent = query("SELECT outbox_state FROM tbl_analysis_outbox WHERE service_id = %s", sid)
    check([state for (state,) in sent] == ["sent"], f"outbox：{[state for (state,) in sent]}")
    deadline = time.monotonic() + 30  # moto 的 NotVisible 會在刪除之後馬上變回 0
    while queue_counts("adam-analysis") != (0, 0) and time.monotonic() < deadline:
        time.sleep(1)
    check(queue_counts("adam-analysis") == (0, 0) and queue_counts("adam-analysis-dlq") == (0, 0),
          f"主佇列（等著、領走未刪）{queue_counts('adam-analysis')}、DLQ {queue_counts('adam-analysis-dlq')}")
    code, body = post("/analyze", {"service_id": sid})
    check(code == 409, f"完成之後再送回 {code} {body}（不重跑）")


def simplified_only():
    """只有簡體才用的字：OpenCC 簡轉繁的字表裡，對應的繁體不包含自己（们、这、发）。台、后、里這種繁體也用的不算。

    不用「s2twp 轉過不會再變」來判斷：s2twp 還會換成台灣的慣用詞（藍牙 → 藍芽），本來就是繁體的句子也會變。
    """
    table = Path(opencc.__file__).parent / "dictionary" / "STCharacters.txt"
    rows = (line.partition("\t") for line in table.read_text(encoding="utf-8").splitlines())
    return {char for char, _, choices in rows if char not in choices.split()}


def parse_transcript(text):
    """逐字稿 → [(角色, 中文, 英文), ...] 和格式不對的行。格式：「Staff: 中文」或「Customer: 中文」，下一行「Translated: 英文」。"""
    lines = iter(line for line in text.splitlines() if line.strip())
    sentences, problems = [], []
    for line in lines:
        role, _, zh = line.partition(": ")
        translated = next(lines, "")
        if role in ("Staff", "Customer") and translated.startswith("Translated: "):
            sentences.append((role, zh, translated[len("Translated: "):]))
        else:
            problems.append(line)
    return sentences, problems


def verify(sid):
    service = load(sid, "service.json")
    video, dialogue = "cam0" in service["media"], service["dialogue"]
    step(f"結果：{sid}（{'合成的對話' if dialogue else '使用者的錄影'}）")

    rows = query(f"SELECT {', '.join(SCORES)} FROM tbl_service_score WHERE service_id = %s", sid)
    if check(len(rows) == 1, f"tbl_service_score 有 {len(rows)} 列"):
        scores = dict(zip(SCORES, (float(value) for value in rows[0])))
        check(all(value == -1 or 0 <= value <= 100 for value in scores.values()), f"分數：{scores}")
        # 合成的對話有兩個人說話；錄影的兩支鏡頭各有一個人（cam0 顧客、cam1 服務人員）
        needed = ["server_text", "server_audio"] + (["server_facial", "customer_facial"] if video else []) \
            + (["customer_text", "customer_audio"] if dialogue else [])
        missing = [column for column in needed if scores[column] < 0]
        check(not missing, f"這些分數不是 -1：{', '.join(needed)}" + (f"（-1：{', '.join(missing)}）" if missing else ""))

    state, _, token, report_key = job(sid) or (None, 0, "", None)
    tag = (token or "")[:12]  # 輸出檔名帶這一次的 token（T49）：{sid}_{tag}_…
    expected = [f"{sid}_{tag}_{name}.png" for name in (CHARTS if video else CHARTS[:2])]  # 沒有錄影就沒有臉部的三張圖
    rows = query("SELECT charts_name, charts_url FROM tbl_charts_storage WHERE service_id = %s ORDER BY charts_id", sid)
    check([name for name, _ in rows] == expected,
          f"tbl_charts_storage 有 {len(rows)} 列，順序：{', '.join(name[len(sid) + 14:-4] for name, _ in rows)}"
          f"（檔名帶 token {tag}）")
    check(all(url == f"s3://{BUCKET}/charts/{name}" for name, url in rows), "圖的位置都是 s3://<bucket>/charts/<檔名>")
    images = {name: get(f"charts/{name}") for name in expected}
    check(all(data and data.startswith(b"\x89PNG") for data in images.values()), f"S3 有 {len(expected)} 張 PNG")
    for name, data in images.items():
        if data:
            save(sid, name, data)

    name = f"{sid}_{tag}_transcript.txt"
    rows = query("SELECT text_name, text_url FROM tbl_text_storage WHERE service_id = %s", sid)
    check(rows == [(name, f"s3://{BUCKET}/text/{name}")], f"tbl_text_storage 有 {len(rows)} 列：text/{name}")
    data = get(f"text/{name}")
    if check(data is not None, f"S3 有 text/{name}"):
        save(sid, "transcript.txt", data)
        check_transcript(data.decode("utf-8"), dialogue)

    data = get(report_key) if report_key else None
    if check(report_key == f"report-data/{sid}_{tag}.json" and data is not None,
             f"S3 有 {report_key}（記在工作那一列，報表從那裡讀）"):
        save(sid, "report-data.json", data)
        report_data = json.loads(data)
        check((report_data.get("schema"), report_data.get("version"), report_data.get("service_id"))
              == ("adam.report-data", 1, sid), "report-data 的 schema、版本、service_id")
        present = {(part, side): bool((report_data.get(part) or {}).get(side))
                   for part in ("audio", "text", "facial") for side in ("staff", "customer")}
        needed = [("audio", "staff"), ("text", "staff")] + ([("facial", "staff")] if video else []) \
            + ([("audio", "customer"), ("text", "customer")] if dialogue else [])
        check(all(present[key] for key in needed),
              "report-data 有：" + "、".join(f"{part} {side}" for (part, side), ok in present.items() if ok))


def check_transcript(text, dialogue):
    sentences, problems = parse_transcript(text)
    check(sentences and not problems,
          f"逐字稿 {len(sentences)} 句，每句是 Staff:／Customer: 加下一行的 Translated:"
          + (f"（{len(problems)} 行格式不對）" if problems else ""))
    simplified = simplified_only()
    found = sorted({char for _, zh, _ in sentences for char in zh if char in simplified})
    check(not found, f"中文是繁體：{len(found)} 個只有簡體用的字" + (f"（{''.join(found[:10])}）" if found else ""))
    not_english = [en for _, _, en in sentences if not re.search(r"[A-Za-z]", en) or re.search(r"[㐀-鿿]", en)]
    check(not not_english, f"Translated: 是英文：{len(not_english)} 句不是")
    roles = Counter(role for role, _, _ in sentences)
    info(f"Staff {roles['Staff']} 句、Customer {roles['Customer']} 句，中文 {sum(len(zh) for _, zh, _ in sentences)} 字")
    if not dialogue:
        return  # 使用者的錄音是個人資料：只印數量
    for role, zh, en in sentences:
        info(f"{role}: {zh}")
        info(f"    {en}")
    truth = dialogue_truth.turns()
    pairs = dialogue_truth.aligned_speakers([(role, zh) for role, zh, _ in sentences], truth)
    correct = sum(heard == expected for heard, expected in pairs) / len(pairs) if pairs else 0
    total = len(dialogue_truth.normalize("".join(turn["text"] for turn in truth)))
    error = dialogue_truth.cer("".join(zh for _, zh, _ in sentences), "".join(turn["text"] for turn in truth))
    check(correct >= MIN_ATTRIBUTION, f"兩個人分得開：{correct:.0%} 的字歸對人（對得上標準答案 {len(pairs)}/{total} 字）")
    info(f"字錯率 {error:.1%}（標準答案是 fixtures/dialogue_zh_tw.txt）")


def report(sid):
    video = "cam0" in load(sid, "service.json")["media"]
    step(f"每次服務的報表：{sid}")
    report_id = str(uuid.uuid4())
    key = f"reports/{report_id}.pdf"
    if not check(query(INSERT_REPORT_SQL, report_id, key, ADMIN_ACCOUNT, sid) == 1, f"新增報表 {report_id}（generating）"):
        return
    code, body = post("/reports", {"report_id": report_id})
    if not check(code == 202, f"POST /reports 回 {code} {body}"):
        return
    started = time.monotonic()
    while True:
        status, reason = query("SELECT report_status, failure_reason FROM tbl_report WHERE report_id = %s", report_id)[0]
        if status != "generating" or time.monotonic() - started > REPORT_TIMEOUT_SEC:
            break
        time.sleep(2)
    if not check(status == "ready", f"報表是 {status}{f'（{reason}）' if reason else ''}，{time.monotonic() - started:.1f} 秒"):
        return
    pdf = get(key)
    if not check(pdf and pdf.startswith(b"%PDF"), f"S3 有 {key}"):
        return
    save(sid, "report.pdf", pdf)
    text = " ".join(" ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).split())
    # Audio、Text 是長條圖（橫軸有 POSITIVE），Facial 是折線圖（縱軸也有 POSITIVE，橫軸是 Seconds from start），沒畫的寫 No data
    facial = "Seconds from start" in text
    bars = text.count("POSITIVE") - facial
    if video:
        check(bars == 2 and facial and "No data" not in text, "報表的 Audio、Text、Facial 三張圖都有畫")
    else:
        check(bars == 2 and not facial and "No data" in text, "報表的 Audio、Text 有畫；沒有錄影，Facial 是 No data")
    info("AI 說明：" + ("沒有（本機不設 OLLAMA_*）" if "Explanation unavailable" in text else "有"))


def to_bytes(usage):
    """docker stats 的 MemUsage（「1.234GiB」）→ bytes。"""
    match = re.fullmatch(r"([\d.]+)([KMG]?i?B)", usage)
    if not match:
        return None
    return float(match[1]) * {"B": 1, "KiB": 2 ** 10, "MiB": 2 ** 20, "GiB": 2 ** 30}.get(match[2], 0)


def gib(size):
    return f"{size / 2 ** 30:.2f} GiB" if size else "—"


def summary(sids):
    """/out/memory.log（run_e2e.sh 寫的）：「秒 sample docker-stats的用量 RSS加總」（每 2～3 秒）、
    「秒 event 名稱 [SID]」、「秒 cgroup-peak bytes」。記憶體峰值都印兩個：docker stats／RSS。"""
    samples, events, cgroup_peak = [], {}, None
    for line in (OUT / "memory.log").read_text().splitlines():
        when, kind, *rest = line.split()
        if kind == "sample" and len(rest) == 2:
            samples.append((int(when), to_bytes(rest[0]), to_bytes(rest[1])))
        elif kind == "event":
            events[" ".join(rest)] = int(when)
        elif kind == "cgroup-peak":
            cgroup_peak = int(rest[0])

    def peak(start=0, end=float("inf")):
        if start is None or end is None:
            return "—"
        window = [sample for sample in samples if start <= sample[0] <= end]
        return "／".join(gib(max((sample[column] or 0 for sample in window), default=None)) for column in (1, 2))

    step(f"結果（WHISPER_MODEL={os.environ.get('WHISPER_MODEL', '?')}；記憶體峰值是 docker stats／RSS）")
    loaded = events.get("analyzer-start"), events.get("ready")
    if None not in loaded:
        info(f"模型載入：{loaded[1] - loaded[0]} 秒，記憶體峰值 {peak(*loaded)}")
    for sid in sids:
        service, analysis = load(sid, "service.json") or {}, load(sid, "analysis.json") or {}
        kind = "合成的對話" if service.get("dialogue") else "使用者的錄影"
        media = "錄音＋錄影" if "cam0" in service.get("media", ()) else "錄音"
        status = {"3": "完成", "1": "失敗"}.get(analysis.get("status"), "沒有完成")
        seconds = analysis.get("seconds")
        timing = f"分析 {seconds} 秒（錄音長度的 {seconds / service['seconds']:.2f} 倍）" if seconds and service else "—"
        info(f"{sid} {kind}（{media} {service.get('seconds', '?')} 秒）：{status}，{timing}，"
             f"記憶體峰值 {peak(events.get(f'start {sid}'), events.get(f'end {sid}'))}")
    info(f"整段的記憶體峰值 {peak()}（{len(samples)} 筆，每 2～3 秒一筆）；cgroup 的 memory.peak {gib(cgroup_peak)}"
         "（含這個容器讀進來的檔案快取）")


def main():
    parser = argparse.ArgumentParser(description="run_e2e.sh 的每一步")
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("prepare")
    adding = steps.add_parser("add")
    adding.add_argument("sid")
    adding.add_argument("audio")
    adding.add_argument("--video", nargs=2, metavar=("CAM0", "CAM1"))
    adding.add_argument("--dialogue", action="store_true")
    steps.add_parser("analyze").add_argument("sid")
    steps.choices["analyze"].add_argument("--restarted", action="store_true")
    for name in ("verify", "report"):
        steps.add_parser(name).add_argument("sid")
    steps.add_parser("summary").add_argument("sids", nargs="+")
    args = parser.parse_args()
    if args.step == "prepare":
        prepare()
    elif args.step == "add":
        add(args.sid, args.audio, args.video, args.dialogue)
    elif args.step == "summary":
        summary(args.sids)
    elif args.step == "analyze":
        analyze(args.sid, args.restarted)
    else:
        {"verify": verify, "report": report}[args.step](args.sid)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
