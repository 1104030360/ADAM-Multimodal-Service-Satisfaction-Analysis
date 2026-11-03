# tests/unit/test_report_model.py — 報表的資料 → 頁面模型（report_model.py；new-feature PLAN 7.4、8）
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）。不連資料庫、不連 S3。
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import report_model as model

SID = "S20261030143012"
START = datetime(2026, 10, 30, 14, 30, 12)
END = datetime(2026, 10, 30, 14, 36, 39)  # 387 秒
NOW = datetime(2026, 11, 1, 10, 5, tzinfo=model.REPORT_TZ)


def row(**changes):
    """SERVICE_SQL 的一列：服務人員（server_*）83.9／78.6／88.5／84.7，上一筆 85.6／89.3／81.9／86.4。"""
    base = {
        "service_id": SID, "account_id": "a1", "service_start": START, "service_end": END, "service_status": 3,
        "account_name": "Alex Morgan", "account_occupation_name": "Customer Service Lead",
        "account_photo_key": "avatars/a1.jpg", "score_service_id": SID,
        "server_total": 83.9, "server_audio": 78.6, "server_text": 88.5, "server_facial": 84.7,
        "customer_total": 80.2, "customer_audio": 76.0, "customer_text": 82.4, "customer_facial": 81.9,
        "previous_total": 85.6, "previous_audio": 89.3, "previous_text": 81.9, "previous_facial": 86.4,
        "transcript_name": None,
    }
    base.update(changes)
    return base


def data(**parts):
    """parse_report_data() 的結果：兩側都有比例與序列；parts 可以把某一部分換掉。"""
    raw = {
        "audio": {"staff": {"positive": 0.31, "neutral": 0.52, "negative": 0.17},
                  "customer": {"positive": 0.2, "neutral": 0.6, "negative": 0.2}},
        "text": {"staff": {"positive": 0.44, "neutral": 0.49, "negative": 0.07}, "customer": None},
        "facial": {"staff": [(0, 0), (1, 1), (2, 1)], "customer": [(0, -1)]},
    }
    raw.update(parts)
    built = model.build_report_data(SID, source="test", **raw)
    return model.parse_report_data(model.dumps_report_data(built))


def explanation(text="Clear greeting."):
    part = model.ModalityExplanation(model.Perspectives(f"C: {text}", f"S: {text}"),
                                     model.Perspectives(f"C+: {text}", f"S+: {text}"))
    return model.Explanation(part, part, part, model="example-model", prompt_version="v1",
                             generated_at=datetime(2026, 11, 1, 18, 5, tzinfo=timezone.utc))


# ---------- 數字 ----------
@pytest.mark.parametrize("now, previous, expected", [
    (83.9, 85.6, -2),  # (83.9 − 85.6) / 85.6 × 100 = −1.99
    (88.5, 81.9, 8),  # +8.06
    (78.6, 89.3, -12),  # −11.98
    (86, 80, 8),  # +7.5：四捨五入（不是銀行家捨入的 8，也不是 float 誤差的 7）
    (74, 80, -8),  # −7.5：遠離 0
    (82.2, 82.2, 0),
    (80, None, None),  # 沒有上一筆
    (80, -1, None),  # 上一筆是 -1（SQL 已經排除，這裡再保險一次）
    (80, 0, None),  # 上一筆是 0：不能除
    (-1, 80, None),  # 這次沒有資料：卡片寫 No data，不寫變化
    (None, 80, None),
])
def test_change_percent(now, previous, expected):
    assert model.change_percent(now, previous) == expected


def test_change_text_has_sign_and_the_report_wording():
    assert model.change_text(8, "service") == "+8% from previous service"
    assert model.change_text(-12, "service") == "-12% from previous service"
    assert model.change_text(0, "period") == "0% from previous period"
    assert model.change_text(None, "service") is None


@pytest.mark.parametrize("value, expected", [
    (83.94, "83.9"), (84.96, "85.0"), (100, "100.0"), (0, "0.0"), (-1, "No data"), (None, "No data")])
def test_score_text_has_one_decimal(value, expected):
    assert model.score_text(value) == expected


def test_average_excludes_minus_one_and_null():
    assert model.average([80, -1, None, 90]) == 85  # 和 AVG(NULLIF(col, -1)) 相同
    assert model.average([-1, None]) is None
    assert model.average([]) is None


def test_time_range_and_job_title():
    assert model.time_range(START, END) == (("FROM", "2026-10-30 14:30:12"), ("TO", "2026-10-30 14:36:39"))
    assert model.time_range(START, None) == (("FROM", "2026-10-30 14:30:12"),)  # 沒有結束時間：只有開始
    assert model.job_title("  Lead ") == "Lead"
    assert model.job_title("   ") is None and model.job_title(None) is None  # 空白就省略，不寫死職稱


# ---------- 每次服務報表 ----------
def test_service_page_with_full_data():
    page = model.service_page(row(), data(), None, NOW)
    assert page.service_id == SID
    assert (page.name, page.job_title) == ("Alex Morgan", "Customer Service Lead")
    assert page.overall == model.ScoreCard("overall", "Overall", "83.9", "-2% from previous service")
    assert page.cards == (
        model.ScoreCard("audio", "Audio", "78.6", "-12% from previous service"),
        model.ScoreCard("text", "Text", "88.5", "+8% from previous service"),
        model.ScoreCard("facial", "Facial", "84.7", "-2% from previous service"))
    assert [r.title for r in page.rows] == ["AUDIO", "TEXT", "FACIAL"]  # 由上到下的順序固定
    assert page.rows[0].chart == model.Bars(0.31, 0.52, 0.17)  # 服務人員那一側
    assert page.rows[1].chart == model.Bars(0.44, 0.49, 0.07)
    assert page.rows[2].chart == model.Series(((0.0, 0.0), (1.0, 1.0), (2.0, 1.0)), 387.0)  # 橫軸是服務的長度
    assert page.footer == "Generated 2026-11-01 10:05 (America/Los_Angeles)"
    assert page.ai_note is None


def test_without_explanation_every_cell_is_unavailable():
    page = model.service_page(row(), data(), None, NOW)
    unavailable = model.Perspectives("Explanation unavailable", "Explanation unavailable")
    for chart_row in page.rows:
        assert (chart_row.comment, chart_row.improvement) == (unavailable, unavailable)
    assert model.explain(model.report_inputs(row(), data())) is None  # 沒有 Ollama 的設定（tests/conftest.py 清掉）


def test_minus_one_score_is_no_data_without_change_or_chart():
    page = model.service_page(row(server_audio=-1, server_total=-1), data(), None, NOW)
    assert page.overall == model.ScoreCard("overall", "Overall", "No data", None)
    assert page.cards[0] == model.ScoreCard("audio", "Audio", "No data", None)
    assert page.rows[0].chart is None  # 有序列也不畫：分數是 -1 的那張圖是 No data
    assert page.rows[1].chart is not None


def test_no_previous_service_means_no_change_line():
    page = model.service_page(row(previous_total=None, previous_audio=None, previous_text=0,
                                  previous_facial=None), data(), None, NOW)
    assert [c.change for c in (page.overall, *page.cards)] == [None, None, None, None]


def test_missing_report_data_means_no_charts():
    assert [r.chart for r in model.service_page(row(), None, None, NOW).rows] == [None, None, None]
    partial = model.service_page(row(), data(text={"staff": None}, facial={"staff": None}), None, NOW)
    assert [r.chart is None for r in partial.rows] == [False, True, True]


def test_facial_axis_follows_the_service_length():
    page = model.service_page(row(service_end=START + timedelta(seconds=1)), data(), None, NOW)
    assert page.rows[2].chart.x_max == 2.0  # 影片比資料庫的起訖長：畫到最後一個點
    no_end = model.service_page(row(service_end=None), data(), None, NOW)
    assert no_end.time_range == (("FROM", "2026-10-30 14:30:12"),)
    assert no_end.rows[2].chart.x_max == 2.0  # 沒有結束時間：不畫假的 120 秒
    one_point = model.service_page(row(service_end=None), data(facial={"staff": [(0, 1)]}), None, NOW)
    assert one_point.rows[2].chart.x_max == 1.0  # 只有第 0 秒：軸至少 1 秒寬


def test_blank_job_title_and_chinese_name():
    page = model.service_page(row(account_name="王小明", account_occupation_name=" "), data(), None, NOW)
    assert (page.name, page.job_title) == ("王小明", None)


def test_explanation_is_copied_per_side_and_minus_one_side_is_no_data():
    page = model.service_page(row(customer_facial=-1), data(), explanation(), NOW)
    audio = page.rows[0]
    assert audio.comment == model.Perspectives("C: Clear greeting.", "S: Clear greeting.")
    assert audio.improvement == model.Perspectives("C+: Clear greeting.", "S+: Clear greeting.")
    facial = page.rows[2]
    assert facial.comment == model.Perspectives("No data", "S: Clear greeting.")  # 顧客那一側是 -1，另一側照寫
    assert page.ai_note == "AI-generated explanation · example-model · 2026-11-01 10:05"  # 18:05 UTC → LA（PST）


def test_blank_explanation_text_is_no_data():
    blank = explanation(" ")
    part = model.ModalityExplanation(model.Perspectives("", "Staff text"), blank.audio.improvement)
    page = model.service_page(row(), data(), model.Explanation(part, part, part, "m", "v1", NOW), NOW)
    assert page.rows[0].comment == model.Perspectives("No data", "Staff text")
    assert page.ai_note == "AI-generated explanation · m · 2026-11-01 10:05"


def test_report_inputs_carry_both_sides_and_the_transcript_location():
    inputs = model.report_inputs(row(transcript_name=f"{SID}_transcript.txt"), data())
    assert inputs.scores["staff"] == {"total": 83.9, "audio": 78.6, "text": 88.5, "facial": 84.7}
    assert inputs.scores["customer"]["facial"] == 81.9
    assert inputs.report_data["audio"]["customer"] == {"positive": 0.2, "neutral": 0.6, "negative": 0.2}
    assert inputs.transcript_key == f"text/{SID}_transcript.txt"
    assert (inputs.service_start, inputs.service_end) == (START, END)
    assert (inputs.s3, inputs.bucket) == (None, None)
    client = object()
    with_s3 = model.report_inputs(row(), None, client, "bucket")
    assert with_s3.transcript_key is None
    assert with_s3.s3 is client and with_s3.bucket == "bucket"  # explain() 要逐字稿時自己下載


# ---------- report-data JSON ----------
def test_report_data_round_trip_keeps_both_sides():
    built = model.build_report_data(
        SID, source="analyzer", generated_at=datetime(2026, 11, 1, 10, 0, tzinfo=model.REPORT_TZ),
        audio={"staff": {"positive": 0.123456, "neutral": 0.5, "negative": 0.376544}},
        text=None, facial={"customer": [(2, 0), (0.5, -1)]})
    assert built["schema"] == "adam.report-data" and built["version"] == 1
    assert built["generated_at"] == "2026-11-01T18:00:00Z"  # 存 UTC（11/1 已經是 PST，UTC−8）
    assert built["audio"] == {"staff": {"positive": 0.1235, "neutral": 0.5, "negative": 0.3765}, "customer": None}
    assert built["facial"]["customer"] == {"points": [[0.5, -1], [2, 0]]}  # 依時間排好
    parsed = model.parse_report_data(model.dumps_report_data(built))
    assert parsed["facial"] == {"staff": None, "customer": [(0.5, -1.0), (2.0, 0.0)]}
    assert parsed["text"] == {"staff": None, "customer": None}


@pytest.mark.parametrize("raw", [
    None, b"not json", b"[]", b'{"schema": "other", "version": 1}', b'{"schema": "adam.report-data", "version": 2}'])
def test_unreadable_report_data_is_none(raw):
    assert model.parse_report_data(raw) is None


@pytest.mark.parametrize("value", [
    {"positive": 0.5, "neutral": 0.5},  # 少一個
    {"positive": 1.5, "neutral": 0, "negative": 0},  # 超出 0～1
    {"positive": True, "neutral": 0, "negative": 0},  # 布林不是數字
    {"positive": 0, "neutral": 0, "negative": 0},  # 全部是 0
    {"positive": float("nan"), "neutral": 0.5, "negative": 0.5},
    "0.5",
])
def test_invalid_proportions_are_no_data(value):
    raw = json.dumps({"schema": "adam.report-data", "version": 1,
                      "audio": {"staff": value, "customer": {"positive": 1, "neutral": 0, "negative": 0}}})
    parsed = model.parse_report_data(raw)
    assert parsed["audio"] == {"staff": None, "customer": {"positive": 1.0, "neutral": 0.0, "negative": 0.0}}


@pytest.mark.parametrize("points", [
    [], [[0, 2]], [[-1, 0]], [[0]], [["a", 0]], [[0, True]], "points"])
def test_invalid_facial_points_are_no_data(points):
    raw = json.dumps({"schema": "adam.report-data", "version": 1, "facial": {"staff": {"points": points}}})
    assert model.parse_report_data(raw)["facial"]["staff"] is None


# ---------- 期間（LA 的日曆日，PLAN R4） ----------
def test_period_bounds_are_calendar_days_including_dst_changes():
    # 2026-11-01 是夏令時間結束的那天（25 小時）；service_start 存當地的牆上時間，所以一天就是日曆日
    assert model.period_bounds(date(2026, 11, 1), date(2026, 11, 1)) == (
        datetime(2026, 11, 1), datetime(2026, 11, 2))
    assert model.in_period(datetime(2026, 11, 1, 1, 30), date(2026, 11, 1), date(2026, 11, 1))  # 重複的那一小時
    assert model.in_period(datetime(2026, 11, 1, 23, 59, 59), date(2026, 11, 1), date(2026, 11, 1))
    assert not model.in_period(datetime(2026, 11, 2), date(2026, 11, 1), date(2026, 11, 1))
    assert not model.in_period(datetime(2026, 10, 31, 23, 59, 59), date(2026, 11, 1), date(2026, 11, 1))
    # 2026-03-08 夏令時間開始（23 小時）
    assert model.in_period(datetime(2026, 3, 8, 3, 0), date(2026, 3, 8), date(2026, 3, 8))
    with pytest.raises(ValueError):
        model.period_bounds(date(2026, 11, 2), date(2026, 11, 1))


def test_previous_period_has_the_same_number_of_days():
    assert model.previous_period(date(2026, 10, 1), date(2026, 10, 31)) == (date(2026, 8, 31), date(2026, 9, 30))
    assert model.previous_period(date(2026, 3, 1), date(2026, 3, 1)) == (date(2026, 2, 28), date(2026, 2, 28))
    assert model.period_query_bounds(date(2026, 10, 1), date(2026, 10, 31)) == (
        datetime(2026, 8, 31), datetime(2026, 11, 1))
    assert len(model.period_days(date(2026, 10, 1), date(2026, 10, 31))) == 31
    assert model.as_date("2026-10-01") == date(2026, 10, 1)
    assert model.as_date(datetime(2026, 10, 1, 5)) == date(2026, 10, 1)


# ---------- 員工、全公司報表 ----------
def staff_row(sid, start, total, audio=70.0, text=75.0, facial=80.0):
    return {"service_id": sid, "service_start": start, "server_total": total, "server_audio": audio,
            "server_text": text, "server_facial": facial}


def test_staff_page_counts_averages_and_compares_with_the_previous_period():
    rows = [
        staff_row("S1", datetime(2026, 9, 30, 10), 80.0),  # 上一期（9/1～9/30）
        staff_row("S2", datetime(2026, 10, 1, 0, 0), 90.0, audio=-1),
        staff_row("S3", datetime(2026, 10, 15, 9), 80.0),
        staff_row("S4", datetime(2026, 10, 31, 23, 59), -1),  # 沒有總分：算一筆服務，不算平均
    ]
    page = model.staff_page({"account_name": "Alex", "account_occupation_name": ""}, rows,
                            date(2026, 10, 1), date(2026, 10, 31), NOW)
    assert page.job_title is None
    assert page.services == model.ScoreCard("services", "Completed services", "3", "+200% from previous period")
    assert page.overall == model.ScoreCard("overall", "Overall", "85.0", "+6% from previous period")  # (85−80)/80
    assert page.cards[0] == model.ScoreCard("audio", "Audio", "70.0", "0% from previous period")  # -1 不算
    assert page.time_range == (("FROM", "2026-10-01"), ("TO", "2026-10-31"))
    assert "2026-08-31 to 2026-09-30" in page.previous_note  # 同樣 31 天
    assert len(page.trend.days) == 31
    overall = page.trend.lines[0].values
    assert (overall[0], overall[14], overall[30], overall[1]) == (90.0, 80.0, None, None)  # 沒有分數的日子是 None
    assert [line.service_id for line in page.highest] == ["S2", "S3"]
    assert page.lowest == ()  # 只有兩筆有總分：不會在最低分再列一次


def test_highest_and_lowest_do_not_overlap_and_ties_keep_time_order():
    rows = [staff_row(f"S{n}", datetime(2026, 10, 1 + n), total) for n, total in
            enumerate([70.0, 95.0, 70.0, 88.0, 60.0, 91.0, 75.0])]
    highest, lowest = model.highest_and_lowest(rows)
    assert [line.service_id for line in highest] == ["S1", "S5", "S3"]
    assert [line.service_id for line in lowest] == ["S4", "S0", "S2"]  # 70.0 同分：早的在前
    assert highest[0] == model.ServiceLine("S1", "2026-10-02 00:00", "95.0", "70.0", "75.0", "80.0")


def test_empty_staff_period():
    page = model.staff_page({"account_name": "王小明", "account_occupation_name": None}, [],
                            date(2026, 10, 1), date(2026, 10, 7), NOW)
    assert page.services == model.ScoreCard("services", "Completed services", "0", None)
    assert page.overall.value == "No data" and page.overall.change is None
    assert page.trend is None and page.highest == () and page.lowest == ()


def company_row(sid, account, name, start, server, customer):
    return {**staff_row(sid, start, server), "account_id": account, "account_name": name,
            "customer_total": customer, "customer_audio": 70.0, "customer_text": -1, "customer_facial": 60.0}


def test_rank_staff_follows_the_dashboard_rules():
    day = datetime(2026, 10, 2)
    rows = [company_row("S1", "b", "Bea", day, 80.0, 70.0), company_row("S2", "b", "Bea", day, 90.0, -1),
            company_row("S3", "a", "Ann", day, 85.0, 90.0), company_row("S4", "c", "Cal", day, -1, 95.0),
            company_row("S5", "d", "Ann", day, 85.0, 50.0)]
    assert model.rank_staff(rows) == (
        model.RankLine(1, "Ann", 1, "85.0", "90.0"),  # 同分照名字、再照帳號（a 在 d 前）
        model.RankLine(2, "Ann", 1, "85.0", "50.0"),
        model.RankLine(3, "Bea", 2, "85.0", "70.0"),  # 名字比 Ann 後面
        model.RankLine(4, "Cal", 1, "No data", "95.0"))  # 沒有分數的排最後


def test_rank_staff_tells_same_name_staff_apart_by_username():
    """PLAN 13.7：同名的人各是一列，只有同名的那幾列在名字後面加登入帳號（和桌面端首頁的排名一樣）。"""
    day = datetime(2026, 10, 2)
    rows = [{**company_row("S1", "a", "王小明", day, 90.0, 80.0), "account_username": "wang.a"},
            {**company_row("S2", "b", "王小明", day, 70.0, 60.0), "account_username": "wang.b"},
            {**company_row("S3", "c", "Bea", day, 80.0, 70.0), "account_username": "bea"}]
    assert [line.name for line in model.rank_staff(rows)] == ["王小明 (wang.a)", "Bea", "王小明 (wang.b)"]
    assert model.display_name("Ann", None, True) == "Ann" and model.display_name(None, "x", False) == ""


def test_service_and_staff_pages_add_the_username_only_when_the_name_is_shared():
    shared = {"account_username": "alex.m", "same_name_count": 1}
    assert model.service_page(row(**shared), data()).name == "Alex Morgan (alex.m)"
    assert model.service_page(row(account_username="alex.m", same_name_count=0), data()).name == "Alex Morgan"
    account = {"account_name": "Alex", "account_occupation_name": "", "account_username": "alex"}
    assert model.staff_page({**account, "same_name_count": 2}, [], date(2026, 10, 1), date(2026, 10, 31)).name \
        == "Alex (alex)"
    assert model.staff_page(account, [], date(2026, 10, 1), date(2026, 10, 31)).name == "Alex"


def test_company_page():
    rows = [company_row("S1", "a", "Ann", datetime(2026, 9, 30, 9), 70.0, 60.0),
            company_row("S2", "a", "Ann", datetime(2026, 10, 1, 9), 80.0, 90.0),
            company_row("S3", "b", "Bea", datetime(2026, 10, 2, 9), 90.0, 70.0)]
    page = model.company_page(rows, date(2026, 10, 1), date(2026, 10, 31), NOW)
    assert page.services.value == "2" and page.services.change == "+100% from previous period"
    assert page.staff_count == model.ScoreCard("staff", "Staff with services", "2", "+100% from previous period")
    assert page.staff_scores[0] == model.ScoreCard("overall", "Overall", "85.0", "+21% from previous period")
    assert page.customer_scores[2] == model.ScoreCard("text", "Text", "No data", None)
    assert [line.name for line in page.ranking] == ["Bea", "Ann"]
    assert [line.label for line in page.trend.lines] == ["Staff", "Customer"]
    assert page.trend.lines[1].dashed
