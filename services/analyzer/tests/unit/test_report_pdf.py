# tests/unit/test_report_pdf.py — PDF 的版面（report_pdf.py；new-feature PLAN 7.4、8）
# analysis_service/ 與 analyzer/ 共用同一份（tools/check_report_copies.py 檢查）。
# 換行、塞進框裡用假的量字（每個字寬 1）測；真的 PDF 用 pypdf 讀出文字和位置，檢查內容與由上到下的順序。
import io
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from pypdf import PdfReader

import report_model as model
import report_pdf

SID = "S20261030143012"
START = datetime(2026, 10, 30, 14, 30, 12)
NOW = datetime(2026, 11, 1, 10, 5, tzinfo=model.REPORT_TZ)
UNAVAILABLE = "Explanation unavailable"


def chars(text, size, bold=False):
    """假的量字：每個字寬 1（不管字級），換行的結果一眼就看得出來。"""
    return float(len(text))


def scaled(text, size, bold=False):
    """假的量字：每個字寬 size × 0.5，字縮小時一行放得下比較多字。"""
    return len(text) * size * 0.5


# ---------- 換行、截斷 ----------
def test_wrap_breaks_between_words_and_inside_long_words():
    assert report_pdf.wrap("aaa bbb ccc", 7, 8, False, chars) == ["aaa bbb", "ccc"]
    assert report_pdf.wrap("abcdefghij", 4, 8, False, chars) == ["abcd", "efgh", "ij"]  # 太長的字照字元切
    assert report_pdf.wrap("one\ntwo", 10, 8, False, chars) == ["one", "two"]
    assert report_pdf.wrap("", 10, 8, False, chars) == [""]


def test_wrap_breaks_chinese_between_any_two_characters():
    assert report_pdf.wrap("顧客說我想退貨", 3, 8, False, chars) == ["顧客說", "我想退", "貨"]
    assert report_pdf.wrap("Hi 顧客", 4, 8, False, chars) == ["Hi 顧", "客"]
    assert report_pdf.wrap("ok顧客", 3, 8, False, chars) == ["ok顧", "客"]  # 英文後面直接接中文


def test_wrap_never_loops_when_one_character_is_wider_than_the_line():
    assert report_pdf.wrap("ab c", 0.5, 8, False, chars) == ["a", "b", "c"]  # 每行至少放一個字


def test_ellipsize_keeps_the_line_inside_the_width():
    assert report_pdf.ellipsize("short", 10, 8, False, chars) == "short"
    assert report_pdf.ellipsize("abcdefghij", 5, 8, False, chars) == "abcd…"


def perspectives(customer, staff):
    return model.Perspectives(customer, staff)


def test_explanation_layout_order_and_unavailable_text():
    unavailable = perspectives(UNAVAILABLE, UNAVAILABLE)
    lines = report_pdf.layout_explanation(unavailable, unavailable, 240, 146, scaled)
    assert [line.text for line in lines] == [
        "AI COMMENT", "Customer", UNAVAILABLE, "Staff", UNAVAILABLE,
        "IMPROVEMENT", "Customer", UNAVAILABLE, "Staff", UNAVAILABLE]
    assert [line.bold for line in lines] == [True, True, False, True, False] * 2  # 小標粗體、正文在下一行
    assert all(line.muted for line in lines if line.text == UNAVAILABLE)  # 沒有說明用次要的顏色
    assert {line.size for line in lines if not line.bold} == {8.0}  # 放得下：用最大的字
    assert [line.y for line in lines] == sorted(line.y for line in lines)


def assert_inside(lines, width, height, measure):
    for line in lines:
        assert measure(line.text, line.size, line.bold) <= width + 1e-6, line
    assert lines[-1].y + lines[-1].size * 0.3 <= height + 1e-6


def test_long_explanation_shrinks_then_truncates_inside_the_box():
    medium = "word " * 40  # 200 字
    lines = report_pdf.layout_explanation(perspectives(medium, medium), perspectives(medium, medium),
                                          240, 146, scaled)
    assert_inside(lines, 240, 146, scaled)
    assert min(line.size for line in lines if not line.bold) < 8.0  # 先縮小

    huge = "word " * 400
    lines = report_pdf.layout_explanation(perspectives(huge, "Short staff note."), perspectives(huge, huge),
                                          240, 146, scaled)
    assert_inside(lines, 240, 146, scaled)
    bodies = [line.text for line in lines if not line.bold]
    assert any(text.endswith("…") for text in bodies)  # 放不下的截斷，結尾加「…」
    assert "Short staff note." in bodies  # 短的那段不會被截斷
    assert [line.text for line in lines if line.bold and line.text in ("AI COMMENT", "IMPROVEMENT")] == [
        "AI COMMENT", "IMPROVEMENT"]  # 標題都還在


def test_box_smaller_than_the_headings_drops_the_bodies():
    # 報表的框是固定的 146 pt，不會發生；萬一框比小標還矮，正文一行都不放，不會一直往下長
    lines = report_pdf.layout_explanation(perspectives("a " * 50, "b"), perspectives("c", "d"), 100, 60, scaled)
    assert [line.text for line in lines if not line.bold] == []


def test_allocate_gives_short_texts_what_they_need():
    assert report_pdf._allocate([1, 10, 2, 10], 9) == [1, 3, 2, 3]  # 輪流一行：短的拿夠了，剩下的給長的
    assert report_pdf._allocate([1, 1], 5) == [1, 1]
    assert report_pdf._allocate([3, 3], 0) == [0, 0]


def test_facial_average_and_window():
    times, values = report_pdf.moving_average([(0, 1), (1, -1), (2, 1), (10, 0)], window=3)
    assert list(times) == [0, 1, 2, 10]
    assert list(values) == pytest.approx([0, 1 / 3, 0, 0])  # 第 0 秒：0、1 秒的平均；第 10 秒只有自己
    assert report_pdf.facial_window(60) == 5 and report_pdf.facial_window(1200) == 30


# ---------- 真的 PDF ----------
def fragments(data):
    """PDF 每一頁畫出來的文字：[(x, 從上往下的 y, 字)]，照畫的順序。字距調整會把一個字串切成好幾段。"""
    pages = []
    for page in PdfReader(io.BytesIO(data)).pages:
        height, items = float(page.mediabox.height), []

        def visit(text, cm, tm, font, size):
            if text:  # 只有空白的那一段也留著：字串在空白處被切開時，空白是獨立的一段
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                items.append((x, height - y, text))

        page.extract_text(visitor_text=visit)
        pages.append(items)
    return pages


def joined(items):
    """整頁的字接起來（連續的空白、換行都變成一個空白）：用來找字串、比先後順序。"""
    return " ".join("".join(text for _, _, text in items).split())


def ys(items, value):
    return [y for _, y, text in items if text.strip() == value]


def service_row(**changes):
    row = {
        "service_id": SID, "account_id": "a1", "service_start": START, "service_end": START + timedelta(seconds=387),
        "service_status": 3, "account_name": "Alex Morgan", "account_occupation_name": "Customer Service Lead",
        "account_photo_key": None, "score_service_id": SID,
        "server_total": 83.9, "server_audio": 78.6, "server_text": 88.5, "server_facial": 84.7,
        "customer_total": 80.2, "customer_audio": 76.0, "customer_text": 82.4, "customer_facial": 81.9,
        "previous_total": 85.6, "previous_audio": 89.3, "previous_text": 81.9, "previous_facial": 86.4,
        "transcript_name": None,
    }
    row.update(changes)
    return row


def report_data():
    return model.parse_report_data(model.dumps_report_data(model.build_report_data(
        SID, source="test",
        audio={"staff": {"positive": 0.31, "neutral": 0.52, "negative": 0.17}},
        text={"staff": {"positive": 0.44, "neutral": 0.49, "negative": 0.07}},
        facial={"staff": [(t, (t // 30) % 3 - 1) for t in range(388)]})))


def photo():
    return np.full((64, 64, 3), 120, np.uint8)


def test_service_report_is_one_a4_page_in_the_planned_order():
    data = report_pdf.render(model.service_page(service_row(), report_data(), None, NOW), photo())
    assert data.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    assert [round(float(v)) for v in reader.pages[0].mediabox.upper_right] == [595, 842]  # A4 直式
    assert len(reader.pages[0].images) == 1  # 大頭照
    page, = fragments(data)
    text = joined(page)
    for expected in ("ADAM", SID, "FROM", "2026-10-30 14:30:12", "TO", "2026-10-30 14:36:39", "Alex Morgan",
                     "Customer Service Lead", "OVERALL", "83.9", "-2% from previous service", "78.6",
                     "-12% from previous service", "+8% from previous service", "POSITIVE", "NEUTRAL", "NEGATIVE",
                     "0.31", "0.52", "0.17", "Seconds from start"):
        assert expected in text, expected
    assert text.count(UNAVAILABLE) == 12  # 三列 × 四格
    # 由上到下：頁首 → 人員與總分 → 三張分數卡 → Audio、Text、Facial 三列
    top = {label: ys(page, label) for label in ("ADAM", "OVERALL", "AUDIO", "TEXT", "FACIAL", "AI COMMENT",
                                                  "IMPROVEMENT")}
    assert top["ADAM"][0] < top["OVERALL"][0] < top["AUDIO"][0]
    assert top["AUDIO"][0] == top["TEXT"][0] == top["FACIAL"][0]  # 三張卡在同一列
    rows = [top["AUDIO"][1], top["TEXT"][1], top["FACIAL"][1]]
    assert rows == sorted(rows) and rows[0] > top["AUDIO"][0]  # 三列由上到下，順序固定
    assert len(top["AI COMMENT"]) == len(top["IMPROVEMENT"]) == 3
    for row_top, comment, improvement in zip(rows, top["AI COMMENT"], top["IMPROVEMENT"]):
        assert row_top - 5 < comment < improvement < row_top + 170  # 每一列右邊：AI COMMENT 在 IMPROVEMENT 上面
    chart_x = [x for x, _, t in page if t.strip() == "AUDIO"][1]
    comment_x = [x for x, _, t in page if t.strip() == "AI COMMENT"][0]
    assert comment_x > 595 / 2 > chart_x  # 圖在左、說明在右


def test_minus_one_and_missing_series_say_no_data():
    row = service_row(server_audio=-1, account_name="王小明", account_occupation_name="", service_end=None)
    data = report_pdf.render(model.service_page(row, None, None, NOW), None)
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages[0].images) == 0  # 沒有照片：畫的是預設頭像，不是別人的照片
    page, = fragments(data)
    text = joined(page)
    assert "王小明" in text  # 中文名字是真的文字
    assert text.count("No data") == 4  # Audio 卡（-1）、三張圖（沒有序列）
    assert "-12% from previous service" not in text  # -1 的那張卡不寫變化
    assert ys(page, "TO") == [] and "2026-10-30 14:30:12" in text  # 沒有結束時間：只有 FROM
    assert "POSITIVE" not in text  # 不畫空的軸


def test_long_explanation_fits_its_boxes_and_prints_the_ai_label():
    long = ("The staff member greeted the customer warmly and explained the fee clearly. "
            "顧客說：「我想了解手續費怎麼算。」 ") * 12
    part = model.ModalityExplanation(model.Perspectives(long, "Short note."), model.Perspectives(long, long))
    explanation = model.Explanation(part, part, part, "example-model", "v1", NOW)
    page = model.service_page(service_row(customer_facial=-1), report_data(), explanation, NOW)
    with report_pdf.rendering():
        sheets = report_pdf.draw(page, None)
        measure = report_pdf.measure_with(sheets[0])
        data = report_pdf.to_pdf(sheets, "test")
    assert len(sheets[0].boxes) == 3
    for name, x, y, width, height, lines in sheets[0].boxes:
        assert_inside(lines, width, height, measure)
    page_items, = fragments(data)
    text = joined(page_items)
    assert "AI-generated explanation · example-model · 2026-11-01 10:05" in text
    assert "…" in text and "Short note." in text and UNAVAILABLE not in text
    assert "No data" in text  # Facial 顧客那一側是 -1
    for name, x, y, width, height, lines in sheets[0].boxes:  # PDF 裡的每一行也都在框裡
        inside = [(fx, fy) for fx, fy, t in page_items
                  if t.strip() and x - 1 <= fx <= x + width and y <= fy <= y + height + 2]
        assert len(inside) >= len(lines)


def test_long_name_and_service_id_shrink_or_truncate():
    row = service_row(account_name="Maximilian Alexander Featherstonehaugh-Worthington the Third",
                      service_id="S" + "9" * 31)
    with report_pdf.rendering():
        sheet, = report_pdf.draw(model.service_page(row, None, None, NOW), None)
    texts = [value for value, _, _ in sheet.texts]
    assert any(value.startswith("Maximilian") and value.endswith("…") for value in texts)


def staff_rows():
    rows = []
    for day in range(1, 32):
        start = datetime(2026, 10, day, 10)
        rows.append({"service_id": start.strftime("S%Y%m%d%H%M%S"), "service_start": start,
                     "server_total": 60.0 + day, "server_audio": 70.0, "server_text": -1, "server_facial": 80.0})
    return rows


def test_staff_period_report():
    page = model.staff_page({"account_name": "Alex Morgan", "account_occupation_name": "Lead"}, staff_rows(),
                            date(2026, 10, 1), date(2026, 10, 31), NOW)
    data = report_pdf.render(page, photo())
    page_items, = fragments(data)
    text = joined(page_items)
    for expected in ("Staff report", "FROM", "2026-10-01", "2026-10-31", "Alex Morgan", "COMPLETED SERVICES", "31",
                     "OVERALL", "76.0", "DAILY OVERALL SCORE", "HIGHEST-SCORING SERVICES", "S20261031100000",
                     "91.0", "LOWEST-SCORING SERVICES", "S20261001100000", "61.0"):
        assert expected in text, expected
    assert ys(page_items, "HIGHEST-SCORING SERVICES")[0] < ys(page_items, "LOWEST-SCORING SERVICES")[0]
    assert text.index("S20261031100000") < text.index("S20261030100000") < text.index("S20261029100000")


def test_empty_staff_period_says_so():
    page = model.staff_page({"account_name": "王小明", "account_occupation_name": None}, [],
                            date(2026, 10, 1), date(2026, 10, 7), NOW)
    text = joined(fragments(report_pdf.render(page, None))[0])
    assert "No completed services with an overall score in this period." in text
    assert "No data" in text


def test_company_report_continues_the_ranking_on_more_pages():
    rows = []
    for number in range(45):
        start = datetime(2026, 10, 1 + number % 28, 9)
        rows.append({"service_id": f"S{number:014d}", "service_start": start, "account_id": f"acc-{number:02d}",
                     "account_name": f"Staff {number:02d}", "server_total": 50.0 + number, "server_audio": 70.0,
                     "server_text": 70.0, "server_facial": 70.0, "customer_total": 80.0, "customer_audio": -1,
                     "customer_text": 70.0, "customer_facial": 70.0})
    page = model.company_page(rows, date(2026, 10, 1), date(2026, 10, 31), NOW)
    data = report_pdf.render(page)
    pages = fragments(data)
    assert len(pages) >= 2
    first, last = joined(pages[0]), joined(pages[-1])
    assert "Company report" in first and "STAFF RANKING" in first and "AVERAGE SCORES" in first
    assert "STAFF RANKING (CONTINUED)" in last and f"Page {len(pages)} of {len(pages)}" in last
    everything = "".join(joined(p) for p in pages)
    assert everything.index("Staff 44") < everything.index("Staff 43") < everything.index("Staff 00")  # 高分在前


def test_load_photo_crops_to_a_square_and_rejects_garbage():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), (10, 200, 30)).save(buffer, format="JPEG")
    image = report_pdf.load_photo(buffer.getvalue())
    assert image.shape == (512, 512, 3)  # 裁成正方形、縮到 512
    assert report_pdf.load_photo(b"not an image") is None
    assert report_pdf.load_photo(b"") is None and report_pdf.load_photo(None) is None


def test_palette_uses_theme_token_names():
    # 名稱都是 Theme.qml 的 token（tools/check_report_copies.py 比對值）；長條的三個顏色不只是顏色，軸上有字
    assert report_pdf.BAR_COLORS == {"positive": "#2E7D32", "neutral": "#8E959F", "negative": "#C62828"}
    assert set(report_pdf.CARD_TINTS) == {"audio", "text", "facial"}
    assert len(set(report_pdf.CARD_TINTS.values())) == 3  # Facial 的綠和 Text 的不同
