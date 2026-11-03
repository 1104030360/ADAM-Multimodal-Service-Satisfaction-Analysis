"""畫出報表的範例 PDF（不連資料庫、不連 S3），用來看版面：new-feature PLAN 7.4、8。

在分析服務的映像檔裡執行（有 matplotlib 和中文字型；映像檔沒有 tools/，所以掛進去）：
    docker run --rm --network none -v "$PWD/tools:/app/tools:ro" -v "<輸出資料夾>:/out" adam-analysis:p0-n6 \
        python tools/render_samples.py /out
資料都是假的：人名、分數、序列、照片（程式畫的漸層，不是真人）、逐字稿、AI 的回答。
N6 的三份（service_ai_*）走真的 report_model.explain() → ollama_explain：AI 是這個程式在 127.0.0.1 開的假 /api/chat，
回答先經過引言、數字、長度的檢查才畫進去；不連 ollama.com。
"""
import io
import json
import os
import random
import sys
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import report_model as model  # noqa: E402
import report_pdf  # noqa: E402

NOW = datetime(2026, 11, 1, 10, 5, tzinfo=model.REPORT_TZ)
START = datetime(2026, 10, 30, 14, 30, 12)
END = datetime(2026, 10, 30, 14, 36, 39)


def fake_photo(size=256):
    """假的大頭照：藍綠漸層上一個淺色的圓（不是真人照片）。"""
    y, x = np.mgrid[0:size, 0:size] / size
    image = np.stack([0.25 + 0.3 * x, 0.45 + 0.25 * y, 0.6 + 0.2 * (1 - x)], axis=-1)
    disc = (x - 0.5) ** 2 + (y - 0.42) ** 2 < 0.06
    image[disc] = (0.93, 0.88, 0.80)
    return (image * 255).astype(np.uint8)


def series(seconds, seed):
    rnd = random.Random(seed)
    value, points = 0, []
    for second in range(seconds + 1):
        if rnd.random() < 0.25:
            value = rnd.choices([-1, 0, 1], [0.1, 0.3, 0.6])[0]
        points.append((second, value))
    return points


def report_data(seconds=387):
    return model.parse_report_data(model.dumps_report_data(model.build_report_data(
        "S20261030143012", source="sample",
        audio={"staff": {"positive": 0.31, "neutral": 0.52, "negative": 0.17},
               "customer": {"positive": 0.22, "neutral": 0.58, "negative": 0.20}},
        text={"staff": {"positive": 0.44, "neutral": 0.49, "negative": 0.07},
              "customer": {"positive": 0.30, "neutral": 0.55, "negative": 0.15}},
        facial={"staff": series(seconds, 1), "customer": series(seconds, 2)})))


def service_row(**changes):
    row = {
        "service_id": "S20261030143012", "account_id": "a1", "service_start": START, "service_end": END,
        "service_status": 3, "account_name": "Alex Morgan", "account_occupation_name": "Customer Service Lead",
        "account_photo_key": "avatars/a1.jpg", "score_service_id": "S20261030143012",
        "server_total": 83.9, "server_audio": 78.6, "server_text": 88.5, "server_facial": 84.7,
        "customer_total": 80.2, "customer_audio": 76.0, "customer_text": 82.4, "customer_facial": 81.9,
        "previous_total": 85.6, "previous_audio": 89.3, "previous_text": 81.9, "previous_facial": 86.4,
        "transcript_name": None,
    }
    row.update(changes)
    return row


LONG = ("The staff member greeted the customer warmly and kept a steady, calm tone throughout the visit. "
        "When the customer asked about the fee, the reply was clear but brief; a short pause before answering "
        "and a friendlier closing would have helped. 顧客說：「我想了解手續費怎麼算。」 ") * 3


def long_explanation():
    block = model.ModalityExplanation(model.Perspectives(LONG, LONG), model.Perspectives(LONG[:180], LONG))
    return model.Explanation(block, block, block, model="example-model-v1", prompt_version="2026-11-01",
                             generated_at=NOW)


# ---------- N6：假的逐字稿與假的 AI 回答 ----------
SAMPLE_TRANSCRIPT = [  # analyzer.py 的 process_text() 寫的格式：說話者一行（原文）、Translated 一行
    ("Customer", "Hi, I would like to ask about the handling fee."),
    ("Staff", "Of course. The fee is waived for transfers under the monthly limit."),
    ("Customer", "How is the handling fee calculated for larger amounts?"),
    ("Staff", "It is one percent of the amount, and I can show you the table."),
    ("Customer", "Okay, that makes sense, thank you."),
    ("Staff", "You are welcome. Is there anything else I can help with today?"),
]
# 模型的回答（12 格）。有兩句故意不合規則，畫出來時已經被拿掉：顧客 Audio 的「12 points」（輸入裡沒有這個數字）、
# 服務人員 Facial 的引言（逐字稿裡沒有這句）。
SAMPLE_ANSWER = {
    "audio": {
        "comment": {"customer": "The customer's voice was mostly neutral (58%) with 20% negative tone, giving 76.0. "
                                "That is 12 points below the staff member.",
                    "staff": "The staff member sounded calm and even; 52% of the voice was neutral and 31% positive."},
        "improvement": {"customer": "Acknowledge the fee question warmly before explaining it, so the customer "
                                    "feels heard.",
                        "staff": "Add a little more warmth to the greeting and the closing to lift the positive "
                                 "tone."}},
    "text": {
        "comment": {"customer": 'The customer asked "How is the handling fee calculated" and ended with thanks, '
                                'scoring 82.4.',
                    "staff": 'Clear, friendly answers such as "I can show you the table" kept the words positive '
                             '(88.5).'},
        "improvement": {"customer": "Confirm the customer understood the fee before moving on.",
                        "staff": "Offer the fee table before the customer has to ask a second time."}},
    "facial": {
        "comment": {"customer": "The customer's expression stayed mostly neutral with some positive moments (81.9).",
                    "staff": 'The staff member smiled often (84.7). They said "you are my favourite customer".'},
        "improvement": {"customer": "Keep eye contact while the customer reads the fee table.",
                        "staff": "Keep the friendly expression during the explanation too, not only at the "
                                 "greeting."}},
}


def transcript_text(speakers=("Customer", "Staff")):
    lines = []
    for speaker, english in SAMPLE_TRANSCRIPT:
        if speaker in speakers:
            lines += [f"{speaker}: (sample) {english}", f"Translated: {english}\n"]
    return "\n".join(lines)


class SampleS3:
    """只回逐字稿的假 S3（report_model.explain() 用 inputs.s3 下載 text/…）。"""

    def __init__(self, text):
        self.text = text

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.text.encode("utf-8"))}


@contextmanager
def fake_ollama(status=200):
    """127.0.0.1 上的假 /api/chat：每次都回 SAMPLE_ANSWER（status 不是 200 時回錯誤）。"""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = {"model": "gemma4:31b", "done": True, "done_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(SAMPLE_ANSWER)}} if status == 200 \
                else {"error": "you have reached your usage limit"}
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    saved = {name: os.environ.get(name) for name in ("OLLAMA_BASE_URL", "OLLAMA_API_KEY", "OLLAMA_MODEL")}
    os.environ.update(OLLAMA_BASE_URL=f"http://127.0.0.1:{server.server_address[1]}",
                      OLLAMA_API_KEY="sample-not-a-real-key", OLLAMA_MODEL="gemma4:31b")
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def ai_page(row, speakers=("Customer", "Staff"), status=200):
    """每次服務報表，說明走真的 report_model.explain()（假的 /api/chat、假的逐字稿）。"""
    data = report_data()
    with fake_ollama(status):
        explanation = model.explain(model.report_inputs(row, data, SampleS3(transcript_text(speakers)), "sample"))
    return model.service_page(row, data, explanation, NOW)


def staff_rows(start, end, seed):
    rnd = random.Random(seed)
    rows, day = [], start
    while day <= end:
        for number in range(rnd.choice([0, 1, 2, 3])):
            moment = datetime.combine(day, datetime.min.time()) + timedelta(hours=9 + 2 * number, minutes=13)
            total = round(rnd.uniform(62, 95), 1)
            rows.append({"service_id": moment.strftime("S%Y%m%d%H%M%S"), "service_start": moment,
                         "server_total": total, "server_audio": round(rnd.uniform(60, 95), 1),
                         "server_text": round(rnd.uniform(60, 95), 1),
                         "server_facial": -1 if rnd.random() < 0.1 else round(rnd.uniform(60, 95), 1)})
        day += timedelta(days=1)
    return rows


def company_rows(start, end):
    names = ["Alex Morgan", "王小明", "Jamie Chen", "Priya Patel", "陳美玲", "Sam Rivera", "Taylor Kim",
             "Jordan Lee", "林志豪", "Morgan Davis", "Chris Wong", "Robin Park", "李佳穎", "Casey Liu",
             "Drew Martin", "Avery Hall", "黃家豪", "Riley Scott", "Quinn Baker", "Skyler Young", "張雅婷",
             "Jesse Allen", "Rowan King", "Emerson Wright", "Hayden Lopez", "Parker Hill", "Reese Green",
             "Logan Adams", "Kendall Nelson", "Blake Carter", "Finley Mitchell", "Sage Roberts"]
    rows = []
    for number, name in enumerate(names):
        for row in staff_rows(start, end, number):
            row.update(account_id=f"acc-{number:02d}", account_name=name,
                       customer_total=round(row["server_total"] - random.Random(row["service_id"]).uniform(-6, 8), 1),
                       customer_audio=70.0, customer_text=75.0, customer_facial=-1)
            rows.append(row)
    return rows


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    photo = fake_photo()
    samples = {
        "service_full": (model.service_page(service_row(), report_data(), None, NOW), photo),
        "service_minus_one": (model.service_page(service_row(server_audio=-1, server_facial=-1, previous_audio=None),
                                                 report_data(), None, NOW), photo),
        "service_no_previous": (model.service_page(service_row(previous_total=None, previous_audio=None,
                                                               previous_text=None, previous_facial=None),
                                                   report_data(), None, NOW), photo),
        "service_no_series": (model.service_page(service_row(), None, None, NOW), photo),
        "service_chinese": (model.service_page(service_row(account_name="王小明", account_occupation_name="  ",
                                                           service_end=None), report_data(), None, NOW), None),
        "service_long_explanation": (model.service_page(
            service_row(customer_facial=-1, account_name="Maximilian Alexander Featherstonehaugh-Worthington"),
            report_data(), long_explanation(), NOW), photo),
    }
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    rows = staff_rows(date(2026, 9, 1), end, 7)
    samples["staff_period"] = (model.staff_page(
        {"account_name": "Alex Morgan", "account_occupation_name": "Customer Service Lead"}, rows, start, end,
        NOW), photo)
    samples["staff_period_empty"] = (model.staff_page(
        {"account_name": "王小明", "account_occupation_name": None}, [], start, end, NOW), None)
    samples["company_period"] = (model.company_page(company_rows(date(2026, 9, 1), end), start, end, NOW), None)
    # N6（T42）：(a) 完整 (b) 顧客 Audio 是 -1、逐字稿只有顧客的句子（服務人員的 Text 沒有句子）(c) AI 不能用（429）
    ai_row = service_row(transcript_name="S20261030143012_transcript.txt")
    samples["service_ai_full"] = (ai_page(ai_row), photo)
    samples["service_ai_partial"] = (ai_page({**ai_row, "customer_audio": -1}, speakers=("Customer",)), photo)
    samples["service_ai_unavailable"] = (ai_page(ai_row, status=429), photo)
    # PLAN 13.7：全公司的排名裡兩位同名的人（後面加登入帳號）
    same = company_rows(date(2026, 9, 1), end)
    for row in same:
        row["account_username"] = row["account_id"].replace("acc-", "user")
        if row["account_id"] in ("acc-00", "acc-02"):
            row["account_name"] = "Alex Morgan"
    samples["company_same_name"] = (model.company_page(same, start, end, NOW), None)
    for name, (page, image) in samples.items():
        path = out / f"{name}.pdf"
        path.write_bytes(report_pdf.render(page, image, title=f"ADAM sample {name}"))
        print(path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples")
