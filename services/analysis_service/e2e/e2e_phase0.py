"""端對端測試：反推的 schema + 第 0 階段分析服務（API＋兩個 worker，SQS 由 moto 模擬）+ 用戶端原本的程式路徑。

用戶端那一側盡量照抄 repo 的寫法：
- 登入、帳號名稱：直接 import myapp.database.DatabaseConnector
- 錄影完成：照 record_widget.py:387-402 的參數呼叫三個預存程序
- 送分析、讀清單與圖表：照 server_home_init.py 的原始 SQL。SQS 之後（T49）桌面端收到 200 不再自己寫狀態 2，
  由 API 在接受工作的交易裡寫
用法：由 run_e2e.sh 呼叫（python e2e_phase0.py <輸出目錄>）；DB_*、S3_PORT、API_PORT 由環境變數提供。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import cv2
import mysql.connector
import numpy as np

REPO = Path(__file__).resolve().parents[3]  # e2e → analysis_service → services → repo
sys.path.insert(0, str(REPO / "myapp" / "src"))
from myapp.database import DatabaseConnector  # noqa: E402

OUT = sys.argv[1]
API = f"http://127.0.0.1:{os.environ.get('API_PORT', '15056')}"
S3_ENDPOINT = f"http://127.0.0.1:{os.environ.get('S3_PORT', '15055')}"
BUCKET = "adam-media-e2e"
DB = dict(host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "3306")), database=os.environ["DB_NAME"],
          user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])

# server_home_init.py:577-583、:189、:611-615 原文
COMPLETED_SQL = """
        SELECT tbl_service.service_id, service_start, service_end, service_status,
               customer_total, server_total
        FROM tbl_service
        LEFT JOIN tbl_service_score ON tbl_service.service_id = tbl_service_score.service_id
        WHERE account_id = %s AND service_status = '3'
        """
CHARTS_SQL = "SELECT charts_url FROM tbl_charts_storage WHERE service_id = %s"

results = []
s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1",
                  aws_access_key_id="testing", aws_secret_access_key="testing")
sqs = boto3.client("sqs", endpoint_url=S3_ENDPOINT, region_name="us-east-1",
                   aws_access_key_id="testing", aws_secret_access_key="testing")


def step(title):
    print(f"\n== {title}", flush=True)


def check(ok, msg):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + msg, flush=True)


def post(payload):
    req = urllib.request.Request(f"{API}/analyze", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), time.time() - t0


def queue_counts(name):
    """佇列裡等著的、被領走還沒刪的（近似值）。"""
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    a = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages",
                                                                "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])


def scalar(conn, sql, *args):
    c = conn.cursor()
    c.execute(sql, args)
    rows = c.fetchall()
    c.close()
    return rows


def register(sid, account_id, urls):
    """照 record_widget.py 呼叫三個預存程序（它自己開一條連線）。"""
    rw = mysql.connector.connect(**DB)
    cur = rw.cursor()
    start = datetime.now() - timedelta(seconds=3)
    cur.callproc("sp_insert_service", (sid, account_id, start, datetime.now(), "1", 0))
    for i, url in enumerate(urls[:2]):
        cur.callproc("sp_insert_video_storage", (sid, f"{sid}_cam{i}.avi", url, datetime.now()))
    cur.callproc("sp_insert_audio_storage", (sid, f"{sid}_audio.wav", urls[2], datetime.now()))
    rw.commit()
    rw.close()


def make_media(sid):
    os.makedirs(f"{OUT}/video", exist_ok=True)
    os.makedirs(f"{OUT}/audio", exist_ok=True)
    paths = []
    for cam, color in (("cam0", (40, 120, 220)), ("cam1", (60, 180, 60))):
        path = f"{OUT}/video/{sid}_{cam}.avi"
        out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"XVID"), 30, (640, 480))  # 與 record_widget.py 相同
        assert out.isOpened(), "XVID VideoWriter 無法開啟"
        for i in range(90):
            frame = np.full((480, 640, 3), color, np.uint8)
            cv2.putText(frame, f"{cam} frame {i}", (40 + i * 3, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            out.write(frame)
        out.release()
        paths.append(path)
    rate, secs = 44100, 3
    t = np.arange(rate * secs) / rate
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * (t % 1 < 0.6) * 32767).astype(np.int16)
    apath = f"{OUT}/audio/{sid}_audio.wav"
    with wave.open(apath, "wb") as wf:  # paInt16、單聲道、44.1 kHz，與 record_widget.py 相同
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(tone.tobytes())
    return paths, apath


# 1. 登入：用戶端原本的 DatabaseConnector
step("登入（myapp.database.DatabaseConnector）")
db = DatabaseConnector()
staff = db.check_login("placeholder_staff", "CHANGE-ME-staff-0000")
check(staff.get("success") and staff.get("account_level") == "1",
      f"服務人員登入成功，account_level={staff.get('account_level')!r}（login.qml 用 === \"1\" 比對）")
check(db.check_login("placeholder_staff", "wrong-password").get("success") is False, "密碼錯誤時回傳 success=False")
admin = db.check_login("placeholder_admin", "CHANGE-ME-admin-0000")
check(admin.get("success") and admin.get("account_level") != "1", "管理員登入成功（等級不是 1，會進管理員端）")
account_id = staff["account_id"]
check(db.get_account_name(account_id) == "服務人員（佔位）", "sp_get_account_name 取得側邊欄名稱")

# 2. 錄影完成：上傳 S3，照 record_widget.py 呼叫預存程序（它自己開一條連線）
step("模擬錄影完成（record_widget.py:387-402）")
sid = "S" + datetime.now().strftime("%Y%m%d%H%M%S")
videos, audio = make_media(sid)
s3.create_bucket(Bucket=BUCKET)
urls = []
for path in videos + [audio]:
    key = os.path.relpath(path, OUT)  # 'video/S..._cam0.avi'：本地相對路徑當 key，和用戶端相同
    s3.upload_file(path, BUCKET, key)
    urls.append(f"s3://{BUCKET}/{key}")
register(sid, account_id, urls)
check(True, f"三個預存程序照用戶端參數呼叫成功，service_id={sid}")

# 3. 送分析：用戶端只認 200；狀態 2 由 API 在同一個交易裡寫好（T49），用戶端接著重新查清單
step("送分析（server_home_init.py 的 analyzeService）")
watch = mysql.connector.connect(**DB, autocommit=True)
code, body, secs = post({"service_id": sid})
check(code == 200 and secs < 1.0, f"POST /analyze 回 {code} {body}，耗時 {secs:.2f} 秒（要在 1 秒內）")
status = scalar(watch, "SELECT service_status FROM tbl_service WHERE service_id=%s", sid)[0][0]
jobs = scalar(watch, "SELECT job_state FROM tbl_analysis_job WHERE service_id=%s", sid)
outbox = scalar(watch, "SELECT COUNT(*) FROM tbl_analysis_outbox WHERE service_id=%s", sid)[0][0]
check(str(status) in ("2", "3") and len(jobs) == 1 and outbox == 1,
      f"回 200 之前已經 commit：狀態 {status}（用戶端沒寫）、工作 {jobs[0][0] if jobs else None}、outbox {outbox} 筆")
code, body, _ = post({"service_id": sid})
check(code in (200, 409), f"馬上再送一次回 {code} {body.get('state') or body.get('error')}（不多一份工作）")
conn = db.connection
cur = conn.cursor(dictionary=True)
cur.execute(COMPLETED_SQL, (account_id,))
cur.fetchall()  # 這次 SELECT 會建立 REPEATABLE READ 快照
cur.close()

# 4. 用另一條 autocommit 連線觀察 worker 把狀態改成 3
step("等分析服務完成")
final, t0 = None, time.time()
while time.time() - t0 < 120:
    wc = watch.cursor()
    wc.execute("SELECT service_status FROM tbl_service WHERE service_id=%s", (sid,))
    final = wc.fetchone()[0]
    wc.close()
    if str(final) in ("1", "3"):
        break
    time.sleep(1)
check(str(final) == "3", f"狀態 1→2→3 完成（最後狀態 {final}，約 {time.time() - t0:.1f} 秒）")
state, attempts, token = scalar(watch, "SELECT job_state, attempts, result_token FROM tbl_analysis_job "
                                       "WHERE service_id=%s", sid)[0]
check(state == "done" and attempts == 1, f"工作 {state}，認領 {attempts} 次")
sent = scalar(watch, "SELECT outbox_state FROM tbl_analysis_outbox WHERE service_id=%s", sid)
check([s for (s,) in sent] == ["sent"], f"outbox：{[s for (s,) in sent]}")
for _ in range(30):  # 刪除之後 NotVisible 很快變回 0
    if queue_counts("adam-analysis") == (0, 0):
        break
    time.sleep(1)
check(queue_counts("adam-analysis") == (0, 0) and queue_counts("adam-analysis-dlq") == (0, 0),
      f"訊息做完就刪掉：主佇列 {queue_counts('adam-analysis')}、DLQ {queue_counts('adam-analysis-dlq')}")

# 5. autocommit 之後：共用連線不必 commit 就看得到服務寫入的 3
step("用戶端共用連線（autocommit）")
cur = conn.cursor(dictionary=True)
cur.execute(COMPLETED_SQL, (account_id,))
fresh = cur.fetchall()
cur.close()
check(len(fresh) == 1, f"不必 commit 就看得到：{[(r['service_id'], r['customer_total'], r['server_total']) for r in fresh]}")

# 6. 分數列
step("tbl_service_score")
wc = watch.cursor(dictionary=True)
wc.execute("SELECT * FROM tbl_service_score WHERE service_id=%s", (sid,))
rows = wc.fetchall()
wc.close()
cols = ["customer_total", "server_total", "customer_text", "server_text",
        "customer_audio", "server_audio", "customer_facial", "server_facial"]
check(len(rows) == 1, f"恰好一列分數（{len(rows)} 列）")
if rows:
    vals = {c: rows[0][c] for c in cols}
    check(all(v == -1 or 0 <= float(v) <= 100 for v in vals.values()), f"8 個分數都在 0–100：{vals}")

# 7. 圖表：照用戶端的讀法（SELECT → basename → charts/<檔名>）
step("圖表（server_home_init.py:185-227 的讀法）")
cur = conn.cursor()
cur.execute(CHARTS_SQL, (sid,))
chart_urls = [r[0] for r in cur.fetchall()]
cur.close()
os.makedirs(f"{OUT}/charts", exist_ok=True)
got = []
for url in chart_urls:
    name = os.path.basename(url)
    s3.download_file(BUCKET, f"charts/{name}", f"{OUT}/charts/{name}")
    got.append(name)
tag = (token or "")[:12]
check(len(got) == 3 and got == sorted(got) and all(name.startswith(f"{sid}_{tag}_") for name in got),
      f"下載 {len(got)} 張圖，順序 {got}（檔名帶這一次的 token {tag}，T49）")

# 8. 錯誤碼
step("錯誤碼")
code, body, _ = post({"service_id": sid})
check(code == 409, f"已完成的服務再送一次回 {code} {body}")
code, body, _ = post({"service_id": "bad-id"})
check(code == 400, f"格式錯誤回 {code}")
code, body, _ = post({"service_id": "S20000101000000"})
check(code == 404, f"不存在的服務回 {code}")
code, body, _ = post({"service_id": sid, "force": True})
check(code == 400 and body.get("error") == "force_not_supported", f"force 重跑回 {code} {body}（這一版不支援）")

# 9. 兩個 worker 同時領：三筆一起送，每一筆只做一次（T49）
step("兩個 worker、三筆一起送")
sids = [f"S{(datetime.now() + timedelta(seconds=10 + i)).strftime('%Y%m%d%H%M%S')}" for i in range(3)]
for each in sids:
    register(each, account_id, urls)
    check(post({"service_id": each})[0] == 200, f"送出 {each}")
t0 = time.time()
while time.time() - t0 < 120:
    done = scalar(watch, "SELECT COUNT(*) FROM tbl_service WHERE service_id IN (%s, %s, %s) AND service_status = 3", *sids)
    if done[0][0] == 3:
        break
    time.sleep(1)
rows = scalar(watch, "SELECT j.service_id, j.job_state, j.attempts, "
                     "(SELECT COUNT(*) FROM tbl_charts_storage AS c WHERE c.service_id = j.service_id) "
                     "FROM tbl_analysis_job AS j WHERE j.service_id IN (%s, %s, %s) ORDER BY j.service_id", *sids)
check(len(rows) == 3 and all(r[1] == "done" and r[2] == 1 and r[3] == 3 for r in rows),
      f"三筆都完成、各認領 1 次、各 3 張圖：{[(r[0], r[1], r[2], r[3]) for r in rows]}（{time.time() - t0:.1f} 秒）")
workers = scalar(watch, "SELECT worker_id, worker_state FROM tbl_analysis_worker ORDER BY worker_id")
check(len(workers) == 2, f"兩個 worker 都登記了：{workers}")

print(f"\n結果：{sum(results)}/{len(results)} 項通過", flush=True)
sys.exit(0 if all(results) else 1)
