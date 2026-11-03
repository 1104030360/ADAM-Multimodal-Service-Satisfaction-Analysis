# report_model.py — 報表的資料與頁面模型（new-feature PLAN 7.4、8；N4、N5）
#
# 兩個分析服務共用同一份：analysis_service/（第 0 階段假服務）和 analyzer/（移植版）的
# report_model.py、report_pdf.py、reports.py 必須逐位元組相同。改了一邊就複製到另一邊，
# 再跑 analysis_service/tools/check_report_copies.py（也比對 PDF 用的顏色和桌面端 Theme.qml）。
#
# 這個檔案只有純函式、SQL 和 N6 的介面，不 import matplotlib、Flask、boto3：單元測試不必連任何東西。
# N6 的 AI 說明在 ollama_explain.py（只用標準函式庫；沒有設定 OLLAMA_API_KEY、OLLAMA_MODEL 時不連任何地方）。
# - 分數 -1 是「沒有資料」：平均一律排除 -1（和 AVG(NULLIF(col, -1)) 相同）；那張卡和那張圖寫 No data，不寫變化。
# - 只算 service_status = 3（分析完成）的服務。
# - 期間的一天是 America/Los_Angeles 的日曆日（PLAN R4）。service_start 存的是當地的牆上時間（DATETIME，沒有時區），
#   所以期間 [period_start, period_end] 就是 service_start >= period_start 00:00 且 < period_end 的隔天 00:00；
#   夏令時間切換那天（23 或 25 小時）也一樣，不必換算。
# - 圖的數字來自分析服務自己存的 report-data/{service_id}.json（REPORT_DATA_* 這一段），不從 PNG 反推。
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import ollama_explain

REPORT_TZ = ZoneInfo("America/Los_Angeles")  # PLAN R4：報表的一天、頁尾的產生時間
NO_DATA = "No data"
EXPLANATION_UNAVAILABLE = "Explanation unavailable"  # PLAN 7.4：沒有說明時四格都寫這句
MODALITIES = ("audio", "text", "facial")  # 由上到下的三列，順序固定
LABELS = {"overall": "Overall", "audio": "Audio", "text": "Text", "facial": "Facial"}  # DS-51
SIDES = ("staff", "customer")  # report-data JSON 的兩側；分數欄位 server_* 是服務人員（staff）
CATEGORIES = ("positive", "neutral", "negative")  # Audio、Text 長條由左到右
CATEGORY_LABELS = {"positive": "POSITIVE", "neutral": "NEUTRAL", "negative": "NEGATIVE"}
REPORT_KINDS = ("service", "staff", "company")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # 和桌面端的清單、明細同一個格式
TOP_COUNT = 3  # 員工報表：最高分、最低分各列 3 筆（PLAN 8）


# =============================================================================
# N6（AI 的分數說明，Ollama Cloud，PLAN 9、R2）：explain() 交給 ollama_explain.explain_report()，再換成下面的 Explanation。
# 沒有設定、呼叫失敗、逾時或回答不合格式時是 None：報表四格都寫 Explanation unavailable，tbl_report.ai_model、
# ai_prompt_version 留 NULL。explain() 在報表的背景工作裡呼叫（reports.py），受 REPORT_TIMEOUT_SEC 限制；
# AI 自己的上限 OLLAMA_TIMEOUT_SEC（預設 90 秒）一定比它短 60 秒以上。
# =============================================================================
@dataclass(frozen=True)
class Perspectives:  # 一段（AI COMMENT 或 IMPROVEMENT）：Customer 與 Staff 兩個觀點的文字
    customer: str
    staff: str


@dataclass(frozen=True)
class ModalityExplanation:  # 一列圖右邊的文字
    comment: Perspectives  # AI COMMENT
    improvement: Perspectives  # IMPROVEMENT


@dataclass(frozen=True)
class Explanation:
    audio: ModalityExplanation
    text: ModalityExplanation
    facial: ModalityExplanation
    model: str  # 寫進 tbl_report.ai_model
    prompt_version: str  # 寫進 tbl_report.ai_prompt_version
    generated_at: datetime


@dataclass(frozen=True)
class ReportInputs:
    """每次服務報表交給 explain() 的資料：都是產生報表時已經查到的；要用哪些由 explain() 決定。

    scores：{"customer": {...}, "staff": {...}}，各有 total、audio、text、facial；-1 是沒有資料。
    report_data：parse_report_data() 的結果（兩側都有），沒有那份 JSON 是 None。
    transcript_key：逐字稿在 S3 的 key（tbl_text_storage 的 text/{service_id}_transcript.txt），沒有是 None。
        這裡不下載；explain() 要逐字稿時用 s3、bucket 自己下載（s3.get_object(Bucket=bucket, Key=transcript_key)）。
    s3、bucket：服務的 boto3 S3 client 與 bucket 名稱。
    """
    service_id: str
    service_start: datetime
    service_end: datetime | None
    scores: dict
    report_data: dict | None
    transcript_key: str | None
    s3: object = field(default=None, repr=False, compare=False)
    bucket: str | None = None


def explain(inputs: ReportInputs) -> Explanation | None:
    """N6：用 Ollama Cloud 產生分數說明（ollama_explain.py）。逐字稿由它用 inputs.s3 自己下載。

    回傳的文字已經檢查過：引言是同一側說過的話、數字出現在送出的資料裡、每格最多 2 句 150 字元；
    分數是 -1 的那一側（和 Text 沒有逐字稿句子的那一側）是 No data；檢查完什麼都不剩的那一格是
    Explanation unavailable。沒有說明時是 None。
    """
    result = ollama_explain.explain_report(inputs)
    if result is None:
        return None

    def modality(name):
        cells = result.cells[name]
        return ModalityExplanation(comment=Perspectives(**cells["comment"]),
                                   improvement=Perspectives(**cells["improvement"]))

    return Explanation(audio=modality("audio"), text=modality("text"), facial=modality("facial"),
                       model=result.model, prompt_version=result.prompt_version, generated_at=result.generated_at)


def explanation_cells(explanation, modality, staff_score, customer_score):
    """一列右邊的四格：(AI COMMENT, IMPROVEMENT)，各是 Perspectives。

    - 沒有說明（None）：四格都是 Explanation unavailable（PLAN 7.4「整次說明失敗時」）。
    - 有說明：某一側的分數是 -1 或那一側沒有文字，那一側寫 No data；另一側照寫（PLAN 7.4、9）。
    """
    if explanation is None:
        unavailable = Perspectives(EXPLANATION_UNAVAILABLE, EXPLANATION_UNAVAILABLE)
        return unavailable, unavailable
    part = getattr(explanation, modality)

    def side(text, score_value):
        text = (text or "").strip()
        return text if has_score(score_value) and text else NO_DATA

    def block(perspectives):
        return Perspectives(side(perspectives.customer, customer_score), side(perspectives.staff, staff_score))

    return block(part.comment), block(part.improvement)


def ai_note(explanation):
    """頁面上的小字：AI-generated explanation · 模型 · 產生時間（PLAN 9）。沒有說明時不印。"""
    if explanation is None:
        return None
    when = explanation.generated_at
    if when.tzinfo is not None:
        when = when.astimezone(REPORT_TZ)
    return f"AI-generated explanation · {explanation.model} · {when.strftime('%Y-%m-%d %H:%M')}"


# =============================================================================
# report-data/{service_id}.json：分析工作順便存下的圖表數字（schema adam.report-data 第 1 版）
#   {"schema": "adam.report-data", "version": 1, "service_id": "S…", "source": "placeholder" | "analyzer",
#    "generated_at": "2026-11-01T17:00:00Z",
#    "audio":  {"staff": {"positive": 0.31, "neutral": 0.52, "negative": 0.17} | null, "customer": {…} | null},
#    "text":   {"staff": {…} | null, "customer": {…} | null},
#    "facial": {"staff": {"points": [[秒, 值], …]} | null, "customer": {…} | null}}
# - Audio、Text：三種比例（0～1，合計約 1）。Facial：從錄影開始算的秒數與那一秒的表情，-1 NEGATIVE、0 NEUTRAL、1 POSITIVE。
# - 兩側都存：報表只畫 staff（這是服務人員的成績單），customer 留給 N6。
# - 某一側沒有資料是 null。讀的時候不合格式的那一部分當成沒有資料，不猜。
# =============================================================================
REPORT_DATA_SCHEMA = "adam.report-data"
REPORT_DATA_VERSION = 1


def report_data_key(service_id):
    """SQS（T49）之前分析的服務存在這裡。之後的每一次分析各有自己的 key（report-data/{service_id}_{token}.json），
    記在 tbl_analysis_job.report_data_key（SERVICE_SQL 的 report_data_key 欄），報表優先讀那一個。"""
    return f"report-data/{service_id}.json"


def _proportions(value):
    """{"positive", "neutral", "negative"} → 檢查過的 dict；不合格式（缺欄位、超出 0～1、全部是 0）是 None。"""
    if not isinstance(value, dict):
        return None
    result = {}
    for category in CATEGORIES:
        number = value.get(category)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) \
                or not 0 <= number <= 1:
            return None
        result[category] = float(number)
    return result if sum(result.values()) > 0 else None


def _points(value):
    """{"points": [[秒, 值], …]} → 依時間排好的 [(秒, 值), …]；不合格式或空的是 None。"""
    if isinstance(value, dict):
        value = value.get("points")
    if not isinstance(value, list) or not value:
        return None
    points = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        t, v = item
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (t, v)) \
                or t < 0 or not -1 <= v <= 1:
            return None
        points.append((float(t), float(v)))
    return sorted(points)


def build_report_data(service_id, *, audio, text, facial, source, generated_at=None):
    """分析工作存的 JSON（dict）。audio、text：{"staff": 比例或 None, "customer": …}；facial：{"staff": [(秒, 值)…] 或 None, …}。"""
    generated_at = generated_at or datetime.now(ZoneInfo("UTC"))

    def proportions(side):
        value = _proportions(side)
        return None if value is None else {k: round(v, 4) for k, v in value.items()}

    def points(side):
        value = _points(side)
        return None if value is None else {"points": [[round(t, 2), round(v, 4)] for t, v in value]}

    return {
        "schema": REPORT_DATA_SCHEMA, "version": REPORT_DATA_VERSION, "service_id": service_id, "source": source,
        "generated_at": generated_at.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audio": {side: proportions((audio or {}).get(side)) for side in SIDES},
        "text": {side: proportions((text or {}).get(side)) for side in SIDES},
        "facial": {side: points((facial or {}).get(side)) for side in SIDES},
    }


def dumps_report_data(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_report_data(raw):
    """S3 上的 JSON（bytes 或 str）→ {"audio": {"staff": dict|None, "customer": …}, "text": …, "facial": {"staff": [(秒, 值)…]|None, …}}。

    讀不到、不是這個 schema 或版本不對時回傳 None（整份都當成沒有資料）；只有某一部分不合格式時，只有那一部分是 None。
    """
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != REPORT_DATA_SCHEMA \
            or data.get("version") != REPORT_DATA_VERSION:
        return None

    def part(name, check):
        value = data.get(name)
        value = value if isinstance(value, dict) else {}
        return {side: check(value.get(side)) for side in SIDES}

    return {"audio": part("audio", _proportions), "text": part("text", _proportions),
            "facial": part("facial", _points)}


# =============================================================================
# 數字的規則
# =============================================================================
def has_score(value):
    """有分數：不是 NULL、不是 -1（沒有資料）。"""
    return value is not None and float(value) >= 0


def score_text(value):
    """分數一律一位小數；沒有資料是 No data。取位數的方法和桌面端的 score() 一樣（round）。"""
    return f"{round(float(value), 1):.1f}" if has_score(value) else NO_DATA


def average(values):
    """平均，排除 NULL 與 -1（和 AVG(NULLIF(col, -1)) 相同）。沒有任何分數是 None。"""
    numbers = [float(v) for v in values if has_score(v)]
    return sum(numbers) / len(numbers) if numbers else None


def change_percent(now, previous):
    """和上一筆（上一期）比的變化：(這次 − 上次) / 上次 × 100，四捨五入成整數（PLAN 7.4）。

    任一邊沒有資料，或上次是 0 時是 None：不顯示那一行，不用 0% 或 N/A 佔位。
    用十進位計算：78.6、83.9 這種一位小數在二進位裡不是剛好的值，用 float 的話 7.5 可能變成 7.4999…。
    """
    if not has_score(now) or not has_score(previous) or float(previous) == 0:
        return None
    current, before = Decimal(repr(float(now))), Decimal(repr(float(previous)))
    return int(((current - before) * 100 / before).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def change_text(percent, against):
    """「+8% from previous service」「-12% from previous period」；正數加 +。None（沒有可比的）→ None。"""
    if percent is None:
        return None
    sign = "+" if percent > 0 else ""
    return f"{sign}{percent}% from previous {against}"


def time_range(start, end):
    """頁首右邊：(("FROM", 開始), ("TO", 結束))；沒有結束時間只有 FROM。"""
    parts = [("FROM", start.strftime(TIME_FORMAT))]
    if end is not None:
        parts.append(("TO", end.strftime(TIME_FORMAT)))
    return tuple(parts)


def job_title(value):
    """職稱（account_occupation_name）：空白就省略（None），不寫死 Service specialist。"""
    value = (value or "").strip()
    return value or None


def display_name(name, username, same_name):
    """同名的人分得出來（PLAN 13.7）：有別的帳號用同一個名字時寫「名字 (登入帳號)」，和桌面端的清單、首頁排名一樣。"""
    name = name or ""
    return f"{name} ({username})" if same_name and username else name


def account_name(row):
    """SERVICE_SQL、ACCOUNT_SQL 的一列 → 頁面上的名字（same_name_count：別的帳號用同一個名字的數目）。"""
    return display_name(row["account_name"], row.get("account_username"), (row.get("same_name_count") or 0) > 0)


def generated_text(now=None):
    now = now or datetime.now(REPORT_TZ)
    return f"Generated {now.astimezone(REPORT_TZ).strftime('%Y-%m-%d %H:%M')} (America/Los_Angeles)"


# =============================================================================
# 期間（員工報表、全公司報表）
# =============================================================================
def period_bounds(start, end):
    """[start, end]（含頭尾的 LA 日期）→ (第一天 00:00, 最後一天的隔天 00:00)：service_start >= 前者且 < 後者。"""
    if end < start:
        raise ValueError("period_end is before period_start")
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def previous_period(start, end):
    """前一段同樣長（同樣天數）的期間：緊接在 start 之前。"""
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    return previous_end - timedelta(days=days - 1), previous_end


def period_days(start, end):
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def period_range(start, end):
    return (("FROM", start.isoformat()), ("TO", end.isoformat()))


def in_period(moment, start, end):
    lower, upper = period_bounds(start, end)
    return lower <= moment < upper


# =============================================================================
# 頁面模型：版面（report_pdf.py）只照這些欄位畫，不再做任何判斷
# =============================================================================
@dataclass(frozen=True)
class ScoreCard:
    key: str  # overall、audio、text、facial、services、staff
    label: str
    value: str  # "83.9"、"12" 或 "No data"
    change: str | None  # 「+8% from previous service」；沒有就不畫那一行


@dataclass(frozen=True)
class Bars:  # Audio、Text：POSITIVE、NEUTRAL、NEGATIVE 的比例（0～1）
    positive: float
    neutral: float
    negative: float


@dataclass(frozen=True)
class Series:  # Facial：(秒, 值)，值 -1 NEGATIVE、0 NEUTRAL、1 POSITIVE；橫軸 0～x_max 秒
    points: tuple
    x_max: float


@dataclass(frozen=True)
class ChartRow:
    key: str  # audio、text、facial
    title: str  # AUDIO、TEXT、FACIAL
    chart: Bars | Series | None  # None：No data（不畫空的軸）
    comment: Perspectives
    improvement: Perspectives


@dataclass(frozen=True)
class ServicePage:
    service_id: str
    time_range: tuple
    name: str
    job_title: str | None
    overall: ScoreCard
    cards: tuple  # Audio、Text、Facial 的 ScoreCard
    rows: tuple  # Audio、Text、Facial 的 ChartRow
    ai_note: str | None
    footer: str


@dataclass(frozen=True)
class TrendLine:
    label: str
    values: tuple  # 每天一個值，沒有服務的日子是 None（線斷開）
    dashed: bool = False


@dataclass(frozen=True)
class Trend:
    days: tuple
    lines: tuple


@dataclass(frozen=True)
class ServiceLine:  # 員工報表的最高分、最低分清單
    service_id: str
    start: str
    overall: str
    audio: str
    text: str
    facial: str


@dataclass(frozen=True)
class StaffPage:
    time_range: tuple
    previous_note: str
    name: str
    job_title: str | None
    services: ScoreCard
    overall: ScoreCard
    cards: tuple
    trend: Trend | None
    highest: tuple
    lowest: tuple
    footer: str


@dataclass(frozen=True)
class RankLine:
    rank: int
    name: str
    services: int
    staff: str
    customer: str


@dataclass(frozen=True)
class CompanyPage:
    time_range: tuple
    previous_note: str
    services: ScoreCard
    staff_count: ScoreCard
    staff_scores: tuple  # Overall、Audio、Text、Facial（服務人員那一側）
    customer_scores: tuple  # 顧客那一側
    ranking: tuple
    trend: Trend | None
    footer: str


def _bars(proportions):
    return None if proportions is None else Bars(*(proportions[c] for c in CATEGORIES))


def _series(points, start, end):
    """橫軸是這次服務的長度（service_end − service_start 秒）；沒有結束時間就畫到最後一個點，不畫假的 120 秒。"""
    if not points:
        return None
    x_max = points[-1][0]
    if end is not None:
        x_max = max(x_max, (end - start).total_seconds())
    return Series(tuple(points), x_max if x_max > 0 else 1.0)


def service_page(row, report_data, explanation=None, now=None):
    """每次服務報表（PLAN 7.4）。row 是 SERVICE_SQL 的一列；report_data 是 parse_report_data() 的結果或 None。"""
    staff = {m: row[f"server_{m}"] for m in ("total", *MODALITIES)}
    customer = {m: row[f"customer_{m}"] for m in ("total", *MODALITIES)}
    start, end = row["service_start"], row["service_end"]

    def card(key, column):
        return ScoreCard(key, LABELS[key], score_text(staff[column]),
                         change_text(change_percent(staff[column], row[f"previous_{column}"]), "service"))

    data = report_data or {}
    rows = []
    for modality in MODALITIES:
        chart = None
        if has_score(staff[modality]):  # 分數是 -1：卡片和圖都是 No data
            side = (data.get(modality) or {}).get("staff")
            chart = _series(side, start, end) if modality == "facial" else _bars(side)
        comment, improvement = explanation_cells(explanation, modality, staff[modality], customer[modality])
        rows.append(ChartRow(modality, LABELS[modality].upper(), chart, comment, improvement))
    return ServicePage(
        service_id=row["service_id"], time_range=time_range(start, end), name=account_name(row),
        job_title=job_title(row["account_occupation_name"]), overall=card("overall", "total"),
        cards=tuple(card(m, m) for m in MODALITIES), rows=tuple(rows), ai_note=ai_note(explanation),
        footer=generated_text(now))


def report_inputs(row, report_data, s3=None, bucket=None):
    """交給 explain() 的資料（N6）：兩側的分數、兩側的 report-data、服務 ID 與起訖、逐字稿的位置、S3 client 與 bucket。"""
    return ReportInputs(
        service_id=row["service_id"], service_start=row["service_start"], service_end=row["service_end"],
        scores={side: {m: row[f"{prefix}_{m}"] for m in ("total", *MODALITIES)}
                for side, prefix in (("staff", "server"), ("customer", "customer"))},
        report_data=report_data,
        transcript_key=f"text/{row['transcript_name']}" if row.get("transcript_name") else None,
        s3=s3, bucket=bucket)


def _service_line(row):
    return ServiceLine(row["service_id"], row["service_start"].strftime("%Y-%m-%d %H:%M"),
                       *(score_text(row[f"server_{m}"]) for m in ("total", *MODALITIES)))


def highest_and_lowest(rows, count=TOP_COUNT):
    """總分（server_total）最高、最低的服務各 count 筆；沒有總分的不列。同分時早的在前。
    服務少於 2 × count 筆時，最低分只列沒有出現在最高分裡的，同一筆不會列兩次。"""
    scored = [r for r in rows if has_score(r["server_total"])]
    highest = sorted(scored, key=lambda r: (-float(r["server_total"]), r["service_start"], r["service_id"]))[:count]
    taken = {r["service_id"] for r in highest}
    lowest = sorted((r for r in scored if r["service_id"] not in taken),
                    key=lambda r: (float(r["server_total"]), r["service_start"], r["service_id"]))[:count]
    return tuple(map(_service_line, highest)), tuple(map(_service_line, lowest))


def daily_trend(rows, days, lines):
    """每天的平均（LA 日曆日，service_start 的日期）。lines：[(標籤, 欄位, 虛線)]；沒有任何分數時是 None。"""
    by_day = {day: [] for day in days}
    for row in rows:
        day = row["service_start"].date()
        if day in by_day:
            by_day[day].append(row)
    result = []
    for label, column, dashed in lines:
        values = tuple(None if (v := average(r[column] for r in by_day[day])) is None else round(v, 1)
                       for day in days)
        result.append(TrendLine(label, values, dashed))
    if all(v is None for line in result for v in line.values):
        return None
    return Trend(tuple(days), tuple(result))


def _split(rows, start, end):
    current = [r for r in rows if in_period(r["service_start"], start, end)]
    before = previous_period(start, end)
    previous = [r for r in rows if in_period(r["service_start"], *before)]
    return current, previous


def _previous_note(start, end):
    before_start, before_end = previous_period(start, end)
    return f"Changes compare with the previous period of the same length ({before_start.isoformat()} to " \
           f"{before_end.isoformat()})."


def _count_card(key, label, now, previous):
    return ScoreCard(key, label, str(now), change_text(change_percent(now, previous), "period"))


def _average_card(key, current, previous, column):
    now, before = average(r[column] for r in current), average(r[column] for r in previous)
    return ScoreCard(key, LABELS[key], score_text(now), change_text(change_percent(now, before), "period"))


def staff_page(account, rows, start, end, now=None):
    """每位員工、一段期間（PLAN 8）。rows 是 STAFF_SERVICES_SQL 的結果（本期加上一期，只有分析完成的）。"""
    current, previous = _split(rows, start, end)
    highest, lowest = highest_and_lowest(current)
    return StaffPage(
        time_range=period_range(start, end), previous_note=_previous_note(start, end),
        name=account_name(account), job_title=job_title(account["account_occupation_name"]),
        services=_count_card("services", "Completed services", len(current), len(previous)),
        overall=_average_card("overall", current, previous, "server_total"),
        cards=tuple(_average_card(m, current, previous, f"server_{m}") for m in MODALITIES),
        trend=daily_trend(current, period_days(start, end), [("Overall", "server_total", False)]),
        highest=highest, lowest=lowest, footer=generated_text(now))


def rank_staff(rows):
    """員工排名，規則和首頁的 Staff Ranking 一樣（home_dashboard.py 的 RANKING_SQL、rank_staff）：
    每位員工分析完成的服務數、兩側總分的平均（排除 -1）；依服務人員那一側的平均由高到低，沒有分數的排最後，
    同分照名字、再照帳號，順序固定。同名的人各是一列，名字後面加上登入帳號「王小明 (wang)」（PLAN 13.7）。"""
    staff = {}
    for row in rows:
        entry = staff.setdefault(row["account_id"], {"name": row["account_name"],
                                                     "username": row.get("account_username"), "rows": []})
        entry["rows"].append(row)
    names = Counter(entry["name"] for entry in staff.values())
    ranked = []
    for account_id, entry in staff.items():
        server, customer = average(r["server_total"] for r in entry["rows"]), \
            average(r["customer_total"] for r in entry["rows"])
        ranked.append((server is None, -(server or 0.0), entry["name"], account_id, len(entry["rows"]), server,
                       customer, display_name(entry["name"], entry["username"], names[entry["name"]] > 1)))
    ranked.sort(key=lambda item: item[:4])
    return tuple(RankLine(position, item[7], item[4], score_text(item[5]), score_text(item[6]))
                 for position, item in enumerate(ranked, 1))


def company_page(rows, start, end, now=None):
    """全公司、一段期間（PLAN 8）。rows 是 COMPANY_SERVICES_SQL 的結果（本期加上一期）。只有管理員看得到（桌面端擋）。"""
    current, previous = _split(rows, start, end)

    def scores(prefix):
        return tuple(_average_card(key, current, previous, f"{prefix}_{column}")
                     for key, column in (("overall", "total"), ("audio", "audio"), ("text", "text"),
                                         ("facial", "facial")))

    return CompanyPage(
        time_range=period_range(start, end), previous_note=_previous_note(start, end),
        services=_count_card("services", "Completed services", len(current), len(previous)),
        staff_count=_count_card("staff", "Staff with services", len({r["account_id"] for r in current}),
                                len({r["account_id"] for r in previous})),
        staff_scores=scores("server"), customer_scores=scores("customer"), ranking=rank_staff(current),
        trend=daily_trend(current, period_days(start, end),
                          [("Staff", "server_total", False), ("Customer", "customer_total", True)]),
        footer=generated_text(now))


# =============================================================================
# SQL（分析服務自己的帳號 adam_analysis 讀；需要的 GRANT 見 db_schema.sql 最後的選用區塊）
# =============================================================================
REPORT_SQL = """
SELECT report_id, report_kind, service_id, staff_account_id, period_start, period_end, report_status, s3_key
FROM tbl_report
WHERE report_id = %s
"""

STATUS_SQL = "SELECT report_status FROM tbl_report WHERE report_id = %s"

# 桌面端把超過 10 分鐘的 generating 改成 failed（reports_contract.md 第 4 點）；重新啟動時只補做還沒逾時的
RECOVER_SQL = """
SELECT report_id FROM tbl_report
WHERE report_status = 'generating' AND created_at >= NOW(3) - INTERVAL %s MINUTE
ORDER BY created_at, report_id
"""

# 狀態只會從 generating 走到 ready 或 failed；桌面端已經改成 failed（逾時）的不會被蓋掉
READY_SQL = """
UPDATE tbl_report
SET report_status = 'ready', failure_reason = NULL, ai_model = %s, ai_prompt_version = %s
WHERE report_id = %s AND report_status = 'generating'
"""
FAILED_SQL = """
UPDATE tbl_report
SET report_status = 'failed', failure_reason = %s
WHERE report_id = %s AND report_status = 'generating'
"""


def _previous_score(column):
    # 同一位、比這次早、分析完成、這個分數不是 -1 的最近一筆（PLAN 7.4）；每個分數各找各的
    return (f"(SELECT p_sc.{column} FROM tbl_service AS p "
            f"JOIN tbl_service_score AS p_sc ON p_sc.service_id = p.service_id "
            f"WHERE p.account_id = s.account_id AND p.service_status = 3 AND p.service_start < s.service_start "
            f"AND p_sc.{column} <> -1 "
            f"ORDER BY p.service_start DESC, p.service_id DESC LIMIT 1)")


SCORE_COLUMNS = [f"{side}_{m}" for side in ("server", "customer") for m in ("total", *MODALITIES)]

# 同名的人（PLAN 13.7）：別的帳號用一模一樣的名字（不分大小寫、重音的比較太寬，所以用 _bin）
_SAME_NAME_COUNT = ("(SELECT COUNT(*) FROM tbl_account AS o WHERE o.account_id <> a.account_id "
                    "AND o.account_name = a.account_name COLLATE utf8mb4_0900_bin) AS same_name_count")

# 每次服務報表要的全部資料，1 個往返：服務、人、兩側分數、四個「上一筆」、逐字稿的位置
SERVICE_SQL = (
    "SELECT s.service_id, s.account_id, s.service_start, s.service_end, s.service_status,\n"
    "       a.account_name, a.account_username, a.account_occupation_name, a.account_photo_key,\n"
    "       " + _SAME_NAME_COUNT + ",\n"
    "       sc.service_id AS score_service_id, " + ", ".join(f"sc.{c}" for c in SCORE_COLUMNS) + ",\n"
    + ",\n".join(f"       {_previous_score('server_' + m)} AS previous_{m}" for m in ("total", *MODALITIES)) + ",\n"
    # SQS 之後（T49）只有提交成功的那一次會登記逐字稿，一筆服務只有一列；用 text_name 排序（adam_analysis 只有這兩欄的 SELECT）
    "       (SELECT t.text_name FROM tbl_text_storage AS t WHERE t.service_id = s.service_id\n"
    "        ORDER BY t.text_name LIMIT 1) AS transcript_name,\n"
    # T49：提交成功的那一次分析的 report-data（舊 worker 晚到的上傳不會被讀到）；SQS 之前分析的是 NULL
    "       (SELECT j.report_data_key FROM tbl_analysis_job AS j WHERE j.service_id = s.service_id\n"
    "        AND j.job_state = 'done') AS report_data_key\n"
    "FROM tbl_service AS s\n"
    "JOIN tbl_account AS a ON a.account_id = s.account_id\n"
    "LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id\n"
    "WHERE s.service_id = %s"
)

ACCOUNT_SQL = (
    "SELECT a.account_id, a.account_name, a.account_username, a.account_occupation_name, a.account_photo_key,\n"
    "       " + _SAME_NAME_COUNT + "\n"
    "FROM tbl_account AS a\n"
    "WHERE a.account_id = %s"
)

# 本期加上一期（同樣長）一次查完，在 Python 分開；只有分析完成的服務（LEFT JOIN：沒有分數列的也算一筆服務）
STAFF_SERVICES_SQL = """
SELECT s.service_id, s.service_start,
       sc.server_total, sc.server_audio, sc.server_text, sc.server_facial
FROM tbl_service AS s
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
WHERE s.account_id = %s AND s.service_status = 3 AND s.service_start >= %s AND s.service_start < %s
ORDER BY s.service_start, s.service_id
"""

COMPANY_SERVICES_SQL = """
SELECT s.service_id, s.account_id, a.account_name, a.account_username, s.service_start,
       sc.server_total, sc.server_audio, sc.server_text, sc.server_facial,
       sc.customer_total, sc.customer_audio, sc.customer_text, sc.customer_facial
FROM tbl_service AS s
JOIN tbl_account AS a ON a.account_id = s.account_id
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
WHERE s.service_status = 3 AND s.service_start >= %s AND s.service_start < %s
ORDER BY s.service_start, s.service_id
"""


def period_query_bounds(start, end):
    """STAFF_SERVICES_SQL、COMPANY_SERVICES_SQL 的範圍：上一期的第一天 00:00 到本期最後一天的隔天 00:00。"""
    before_start, _ = previous_period(start, end)
    return period_bounds(before_start, end)


def as_date(value):
    """DATE 欄位：mysql-connector 回傳 date；萬一是字串（YYYY-MM-DD）也接受。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
