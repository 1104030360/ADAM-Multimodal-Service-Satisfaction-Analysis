# report_pdf.py — 把 report_model.py 的頁面模型畫成 A4 直式、白底的 PDF（new-feature PLAN 7.4、8）
#
# 兩個分析服務共用同一份（說明見 report_model.py 開頭）。只用 matplotlib（兩個服務都已經有），版面和圖是同一套。
# - 座標一律是 pt，原點在左上角：整頁鋪一個 0～595 × 0～842 的 axes（y 軸反過來），長方形、文字都畫在上面；
#   圖表另外加 axes 疊在卡片上。
# - 字型：Noto Sans CJK TC（名字可以是中文），沒有的話退回 DejaVu Sans。PDF 裡是真的文字（Type 3 字型，附 ToUnicode），
#   可以選取、搜尋，測試也讀得出來。不用 pdf.fonttype 42：Noto CJK 是 CFF 字形，matplotlib 用 42 嵌入時中文字和
#   英文字都顯示不出來（2026-11-01 在 matplotlib 3.11.2 實測）。
# - 顏色只用 Theme.qml 的 token（PALETTE 的名稱就是 token 名稱），不用參考圖的色碼；
#   tools/check_report_copies.py 會和桌面端的 Theme.qml 比對。
# - 文字一定待在自己的框裡：放不下時先把字縮小，最小的字還放不下就截斷，結尾加「…」，不會超出卡片。
from __future__ import annotations

import functools
import io
import os
import re
import threading
from dataclasses import dataclass, field

import matplotlib
import numpy as np
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch
from matplotlib.ticker import MaxNLocator

import report_model as model

# Theme.qml 的 token：名稱、值都和桌面端一樣（白紙上的版本，new-feature PLAN 7.4）
PALETTE = {
    "textOnDark": "#FFFFFF",       # 頁面的白底（這個 token 在桌面端是飽和色上的白字，值相同）
    "chromeOnLight": "#0E1013",    # 白紙上的字：白底 19:1，色卡上 15:1 以上
    "paperTextMuted": "#4F555D",   # 次要的字、刻度、No data：白底 7.5:1，色卡上 6.1:1 以上
    "paper": "#E6E8EB",            # 卡片的框線、圖表的格線
    "textFaint": "#8E959F",        # NEUTRAL 的長條、圖表的軸線：白底 3.0:1（PLAN 寫的 textSecondary 在白底只有 1.95:1，看不清楚）
    "successOnLight": "#2E7D32",   # POSITIVE 的長條：白底 5.1:1
    "errorOnLight": "#C62828",     # NEGATIVE 的長條：白底 5.6:1
    "paperWarmTint": "#FCE3C7",    # Audio 分數卡的底（PLAN 的 warningTint 是深色介面用的，白紙上太暗）
    "paperGreenTint": "#DCF2D3",   # Text 分數卡的底（PLAN 的 successTint 同上）
    "paperMintTint": "#CFEFE6",    # Facial 分數卡的底：和 Text 不同的綠（PLAN 7.4）
}
PAGE = PALETTE["textOnDark"]
INK = PALETTE["chromeOnLight"]
MUTED = PALETTE["paperTextMuted"]
LINE = PALETTE["paper"]
AXIS = PALETTE["textFaint"]
BAR_COLORS = {"positive": PALETTE["successOnLight"], "neutral": PALETTE["textFaint"],
              "negative": PALETTE["errorOnLight"]}
CARD_TINTS = {"audio": PALETTE["paperWarmTint"], "text": PALETTE["paperGreenTint"],
              "facial": PALETTE["paperMintTint"]}

A4_W, A4_H = 595.28, 841.89  # pt
MARGIN = 36
GAP = 12
RADIUS = 7
PAD = 12
BODY_SIZES = (8.0, 7.5, 7.0, 6.5, 6.0)  # 右邊說明的字：放不下就一級一級縮小
ELLIPSIS = "…"
FACIAL_WINDOW_SEC = 5  # Facial 折線：每一點是前後一段時間的平均（每秒一個 -1／0／1 的值，直接畫會一直鋸齒）
FACIAL_WINDOWS = 40  # 長的服務把平均的範圍拉長：整條線大約 40 段，至少 FACIAL_WINDOW_SEC 秒
MEASURE_DPI = 288

# 中文字型：Debian 的 fonts-noto-cjk。舊版 matplotlib（移植版用 3.9.2）不會自己掃描 .ttc，所以明確加進來；
# addfont 只讀 .ttc 的第一個字型（Noto Sans CJK JP），掃描得到的話優先用繁體中文的 TC。
CJK_FONT_FILES = [os.getenv("CJK_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]
for _path in CJK_FONT_FILES:
    if os.path.exists(_path):
        font_manager.fontManager.addfont(_path)
_available = {f.name for f in font_manager.fontManager.ttflist}
_cjk = [name for name in ("Noto Sans CJK TC", "Noto Sans CJK JP") if name in _available][:1]
FAMILY = _cjk + [name for name in ("DejaVu Sans",) if name in _available] or ["sans-serif"]
# matplotlib 的 rcParams 是全域的，分析工作可能同時在另一條執行緒畫 PNG：字型、顏色都在每個元素上指定，
# 不改全域設定；這裡只有 PNG 用不到的 pdf.fonttype（Type 3，見開頭的說明）。同一時間只畫一份報表。
RC = {"pdf.fonttype": 3}
RENDER_LOCK = threading.Lock()

_CJK = re.compile(r"[ᄀ-ᇿ⺀-鿿ꥠ-꥿가-퟿豈-﫿︰-﹏＀-￯]")


# =============================================================================
# 量字、換行、塞進框裡（純函式：measure(text, size, bold) → 寬度 pt，測試可以換成假的）
# =============================================================================
def _tokens(paragraph):
    """一段文字 → 可以換行的單位：英文是一個字（連著後面的空白），中日韓文字一個字一個單位。"""
    tokens, word = [], ""
    for ch in paragraph:
        if ch == " ":
            tokens.append(word + ch)
            word = ""
        elif _CJK.match(ch):
            if word:
                tokens.append(word)
                word = ""
            tokens.append(ch)
        else:
            word += ch
    if word:
        tokens.append(word)
    return tokens


def _longest_prefix(text, width, fits):
    """text 開頭最多幾個字放得進 width（至少 1 個，不然會一直卡住）。"""
    low, high = 1, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(text[:middle]):
            low = middle
        else:
            high = middle - 1
    return low


def wrap(text, width, size, bold, measure):
    """換行：每一行都不超過 width。太長的英文字（例如網址）照字元切開。

    一行的寬度先把每個單位的寬度加起來估（量字有快取，同一個字只量一次），排滿一行時再量一次整行：
    字距調整讓整行超出時，把最後一個單位移到下一行。每次都量整行的話，N6 的長說明要量上萬次，會花幾十秒。
    """
    def width_of(value):
        return measure(value, size, bold)

    def fits(value):
        return width_of(value) <= width

    space = width_of("a a") - width_of("aa")
    lines = []
    for paragraph in str(text).split("\n"):
        tokens = _tokens(paragraph)
        if not tokens:
            lines.append("")
        start = 0
        while start < len(tokens):
            first = tokens[start].lstrip()
            if not fits(first.rstrip()):  # 一個單位就比一行寬：照字元切開
                cut = _longest_prefix(first.rstrip(), width, fits)
                lines.append(first[:cut])
                tokens[start] = first[cut:]
                if not tokens[start].strip():
                    start += 1
                continue
            used = width_of(first.rstrip()) + (space if first.endswith(" ") else 0)
            end = start + 1
            while end < len(tokens) and used + width_of(tokens[end].rstrip()) <= width:
                used += width_of(tokens[end].rstrip()) + (space if tokens[end].endswith(" ") else 0)
                end += 1
            while end - start > 1 and not fits("".join([first, *tokens[start + 1:end]]).rstrip()):
                end -= 1
            lines.append("".join([first, *tokens[start + 1:end]]).rstrip())
            start = end
    return lines


def ellipsize(text, width, size, bold, measure):
    """截斷成一行，結尾加「…」，整行不超過 width。"""
    text = text.rstrip()
    if measure(text, size, bold) <= width:
        return text
    while text and measure(text.rstrip() + ELLIPSIS, size, bold) > width:
        text = text[:-1]
    return text.rstrip() + ELLIPSIS


@dataclass(frozen=True)
class TextLine:
    text: str
    y: float  # 從框的上緣到這一行基線的距離
    size: float
    bold: bool
    muted: bool


def _allocate(needs, available):
    """可以用的行數平均分給幾段文字：每段輪流拿一行，用不完的讓給還不夠的（短的段落不會被截斷）。"""
    given = [0] * len(needs)
    while available > 0:
        progressed = False
        for index, need in enumerate(needs):
            if available > 0 and given[index] < need:
                given[index] += 1
                available -= 1
                progressed = True
        if not progressed:
            break
    return given


def layout_explanation(comment, improvement, width, height, measure):
    """一列右邊的文字（PLAN 7.4）：AI COMMENT（Customer、Staff）、IMPROVEMENT（Customer、Staff）。

    小標粗體、正文在下一行、兩個觀點之間空一行。先用 8 pt，放不下就一級一級縮小到 6 pt；
    6 pt 還放不下時把正文截斷（每段至少留下能留的行數，最後一行加「…」）。回傳 TextLine 的清單，保證不超出 width × height。
    """
    placeholders = {model.EXPLANATION_UNAVAILABLE, model.NO_DATA}
    blocks = [("AI COMMENT", comment), ("IMPROVEMENT", improvement)]
    bodies = [(perspectives.customer, perspectives.staff) for _, perspectives in blocks]

    def arrange(size, limits=None):
        heading, line, blank = size + 1.5, size * 1.3, size * 0.55
        lines, y, index = [], 0.0, 0
        for block_number, (title, _) in enumerate(blocks):
            if block_number:
                y += size * 0.9
            y += heading * 1.05
            lines.append(TextLine(title, y, heading, True, False))
            y += 2
            for side_number, (label, body) in enumerate(zip(("Customer", "Staff"), bodies[block_number])):
                if side_number:
                    y += blank
                y += line
                lines.append(TextLine(label, y, size, True, False))
                wrapped = wrap(body, width, size, False, measure)
                limit = None if limits is None else limits[index]
                if limit is not None and len(wrapped) > limit:
                    wrapped = wrapped[:limit]
                    if wrapped:
                        wrapped[-1] = ellipsize(wrapped[-1] + ELLIPSIS, width, size, False, measure)
                for text in wrapped:
                    y += line
                    lines.append(TextLine(text, y, size, False, body in placeholders))
                index += 1
        return lines, y + size * 0.3  # 最後一行的下伸部分

    for size in BODY_SIZES:
        lines, total = arrange(size)
        if total <= height:
            return lines
    size = BODY_SIZES[-1]
    needs = [len(wrap(body, width, size, False, measure)) for pair in bodies for body in pair]
    fixed = arrange(size, [0] * len(needs))[1]
    available = max(0, int((height - fixed) // (size * 1.3)))
    lines, _ = arrange(size, _allocate(needs, available))
    return lines


# =============================================================================
# 一頁：整頁的 axes（pt，左上角是原點）加上量字的 renderer
# =============================================================================
@dataclass
class Sheet:
    fig: Figure
    ax: object
    renderer: object
    texts: list = field(default_factory=list)  # (文字, x, y) 依畫的順序：測試用來檢查內容和位置
    widths: dict = field(default_factory=dict)  # 量過的寬度：(字, 字級, 粗體) → pt
    boxes: list = field(default_factory=list)  # (名稱, x, y, w, h, [TextLine])：說明的框與裡面的每一行


@functools.lru_cache(maxsize=64)
def _font(size, bold=False):
    return FontProperties(family=FAMILY, size=size, weight="bold" if bold else "normal")


def new_sheet():
    # dpi 只影響量字：Agg 量的寬度會對齊整數像素，72 dpi 時每個字可能差 0.5 pt，一行下來會超出框；
    # 用 288 dpi 量，誤差小到看不出來。存成 PDF 時 matplotlib 一律用 72 dpi 的座標，和這個值無關。
    fig = Figure(figsize=(A4_W / 72, A4_H / 72), dpi=MEASURE_DPI, facecolor=PAGE)
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, A4_W)
    ax.set_ylim(A4_H, 0)
    ax.axis("off")
    return Sheet(fig, ax, canvas.get_renderer())


def measure_with(sheet):
    """measure(text, size, bold) → 寬度 pt。同一頁量過的字記在 sheet.widths，不再量第二次。"""
    def measure(text, size, bold=False):
        if not text:
            return 0.0
        key = (text, size, bold)
        if key not in sheet.widths:
            width, _, _ = sheet.renderer.get_text_width_height_descent(text, _font(size, bold), ismath=False)
            sheet.widths[key] = width * 72 / sheet.fig.dpi
        return sheet.widths[key]
    return measure


def text(sheet, x, y, value, size, bold=False, color=INK, ha="left", va="baseline"):
    sheet.ax.text(x, y, value, fontproperties=_font(size, bold), color=color, ha=ha, va=va, parse_math=False)
    sheet.texts.append((value, x, y))


def fit_line(sheet, x, y, value, size, bold, width, color=INK, ha="left", min_size=None):
    """一行字放進 width：先縮小（最小 min_size），還放不下就截斷加「…」。"""
    measure = measure_with(sheet)
    min_size = min_size or size
    while size > min_size and measure(value, size, bold) > width:
        size = max(min_size, size - 1)
    text(sheet, x, y, ellipsize(value, width, size, bold, measure), size, bold, color, ha)


def box(sheet, x, y, w, h, fill=PAGE, edge=LINE, radius=RADIUS):
    sheet.ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                      facecolor=fill, edgecolor=edge if edge else fill, linewidth=0.8))


def rule(sheet, y):
    sheet.ax.plot([MARGIN, A4_W - MARGIN], [y, y], color=LINE, linewidth=0.8)


def chart_axes(sheet, x, y, w, h):
    ax = sheet.fig.add_axes((x / A4_W, 1 - (y + h) / A4_H, w / A4_W, h / A4_H))
    ax.set_facecolor("none")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.6)
    ax.tick_params(length=2, width=0.6, labelsize=6.5, colors=AXIS, labelcolor=MUTED, labelfontfamily=FAMILY)
    return ax


# =============================================================================
# 共用的區塊
# =============================================================================
def header(sheet, middle, time_range):
    """頁首（PLAN 7.4）：左 ADAM、中間服務 ID（或報表名稱）、右邊 FROM … TO …，下面一條細線。

    中間的字置中在 ADAM 和 FROM 之間的空位裡；放不下就縮小，再放不下就截斷，不會和兩邊重疊。
    """
    measure = measure_with(sheet)
    text(sheet, MARGIN, 54, "ADAM", 20, True)
    parts = []  # (字, 粗體, 顏色, 後面的空隙)：空隙直接給 pt，不靠空白字元（量字時結尾的空白不算寬度）
    for label, value in time_range:
        parts += [(label, True, MUTED, 4), (value, False, INK, 10)]
    total = sum(measure(value, 8, bold) + gap for value, bold, _, gap in parts) - parts[-1][3]
    x = A4_W - MARGIN - total
    left, right = MARGIN + measure("ADAM", 20, True) + 16, x - 16
    fit_line(sheet, (left + right) / 2, 52, middle, 10.5, True, right - left, ha="center", min_size=8)
    for value, bold, color, gap in parts:
        text(sheet, x, 52, value, 8, bold, color)
        x += measure(value, 8, bold) + gap
    rule(sheet, 66)


def footer(sheet, left, right=None, page=None):
    text(sheet, MARGIN, A4_H - 20, left, 6.5, color=MUTED)
    note = " · ".join(part for part in (right, page) if part)
    if note:
        text(sheet, A4_W - MARGIN, A4_H - 20, note, 6.5, color=MUTED, ha="right")


def photo_circle(sheet, x, y, diameter, image):
    """圓形大頭照；沒有照片（或讀不出來）時畫一個中性的預設頭像，不用別人的照片。"""
    if image is not None:
        ax = sheet.fig.add_axes((x / A4_W, 1 - (y + diameter) / A4_H, diameter / A4_W, diameter / A4_H))
        ax.axis("off")
        shown = ax.imshow(image, extent=(0, 1, 0, 1), interpolation="none", aspect="auto")
        shown.set_clip_path(Circle((0.5, 0.5), 0.5, transform=ax.transAxes))
        return
    radius = diameter / 2
    cx, cy = x + radius, y + radius
    back = Circle((cx, cy), radius, facecolor=LINE, edgecolor="none")
    sheet.ax.add_patch(back)
    head = Circle((cx, cy - radius * 0.22), radius * 0.30, facecolor=AXIS, edgecolor="none")
    shoulders = Ellipse((cx, cy + radius * 0.68), radius * 1.25, radius * 0.95, facecolor=AXIS, edgecolor="none")
    for part in (head, shoulders):
        sheet.ax.add_patch(part)
        part.set_clip_path(back)


def score_card(sheet, x, y, w, h, card, fill, edge=None, value_size=20):
    """分數卡：標題（文字，不只靠顏色分辨）、數字、和上一筆／上一期比的變化。"""
    box(sheet, x, y, w, h, fill=fill, edge=edge)
    fit_line(sheet, x + PAD, y + PAD + 7, card.label.upper(), 7.5, True, w - 2 * PAD)
    if card.value == model.NO_DATA:
        text(sheet, x + PAD, y + PAD + 7 + value_size * 0.95, model.NO_DATA, value_size * 0.7, True, MUTED)
    else:
        text(sheet, x + PAD, y + PAD + 7 + value_size * 1.05, card.value, value_size, True)
    if card.change:
        fit_line(sheet, x + PAD, y + h - PAD + 1, card.change, 7, False, w - 2 * PAD, MUTED)


def no_data(sheet, x, y, w, h):
    text(sheet, x + w / 2, y + h / 2, model.NO_DATA, 9, True, MUTED, ha="center", va="center")


def bar_chart(sheet, x, y, w, h, bars):
    """Audio、Text：POSITIVE、NEUTRAL、NEGATIVE 的比例；縱軸 0.0～1.0，軸上有文字，長條上寫數字（不只靠顏色）。"""
    ax = chart_axes(sheet, x, y, w, h)
    values = [bars.positive, bars.neutral, bars.negative]
    positions = range(len(values))
    ax.bar(positions, values, width=0.55, color=[BAR_COLORS[c] for c in model.CATEGORIES], zorder=2)
    for position, value in zip(positions, values):
        ax.text(position, value + 0.03, f"{value:.2f}", ha="center", va="bottom", color=INK,
                fontproperties=_font(6.5))
    ax.set_ylim(0, 1.0)
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels([f"{v:.1f}" for v in (0, 0.2, 0.4, 0.6, 0.8, 1.0)])
    ax.set_xticks(list(positions))
    ax.set_xticklabels([model.CATEGORY_LABELS[c] for c in model.CATEGORIES])
    ax.set_xlim(-0.6, len(values) - 0.4)
    ax.grid(axis="y", color=LINE, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)


def facial_window(x_max):
    """Facial 折線的平均範圍（秒）：至少 5 秒；服務越長範圍越大，整條線大約 40 段。"""
    return max(FACIAL_WINDOW_SEC, int(round(x_max / FACIAL_WINDOWS)))


def moving_average(points, window=FACIAL_WINDOW_SEC):
    """每一點換成前後 window 秒內所有點的平均（點的間隔不一定相同：偵測不到臉的那一秒沒有點）。"""
    times = np.array([t for t, _ in points], dtype=float)
    values = np.array([v for _, v in points], dtype=float)
    half = window / 2
    low = np.searchsorted(times, times - half, side="left")
    high = np.searchsorted(times, times + half, side="right")
    sums = np.concatenate(([0.0], np.cumsum(values)))
    return times, (sums[high] - sums[low]) / (high - low)


def line_chart(sheet, x, y, w, h, series):
    """Facial：從這次服務開始算的秒數；縱軸由下到上 NEGATIVE、NEUTRAL、POSITIVE。"""
    ax = chart_axes(sheet, x, y, w, h)
    window = facial_window(series.x_max)
    times, values = moving_average(series.points, window)
    ax.plot(times, values, color=INK, linewidth=1.1, zorder=3)
    ax.plot(times[-1:], values[-1:], marker="o", markersize=3, color=INK, zorder=4, clip_on=False)
    ax.set_xlim(0, series.x_max)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True))  # 秒數用整數
    ax.set_ylim(-1.15, 1.15)
    ax.set_yticks([-1, 0, 1])
    ax.set_yticklabels(["NEGATIVE", "NEUTRAL", "POSITIVE"])
    ax.grid(axis="y", color=LINE, linewidth=0.6, zorder=0)
    ax.set_xlabel(f"Seconds from start ({window}-second average)", fontproperties=_font(6.5),
                  color=MUTED, labelpad=2)


def explanation_box(sheet, x, y, w, h, name, comment, improvement):
    inner_w, inner_h = w - 2 * PAD, h - 2 * PAD
    lines = layout_explanation(comment, improvement, inner_w, inner_h, measure_with(sheet))
    for line in lines:
        text(sheet, x + PAD, y + PAD + line.y, line.text, line.size, line.bold, MUTED if line.muted else INK)
    sheet.boxes.append((name, x + PAD, y + PAD, inner_w, inner_h, lines))


def table(sheet, x, y, w, columns, rows, empty_text, size=7.5, row_h=16):
    """簡單的表格：columns = [(標題, 寬度比例, 對齊)]；rows = [[字串…]]。回傳表格的下緣。"""
    total = sum(weight for _, weight, _ in columns)
    widths = [w * weight / total for _, weight, _ in columns]

    def cells(values, bold, color, baseline):
        left = x
        for value, width, (_, _, align) in zip(values, widths, columns):
            anchor = left + (width - 4 if align == "right" else 0)
            fit_line(sheet, anchor, baseline, value, size, bold, width - 6, color, ha=align)
            left += width

    cells([title for title, _, _ in columns], True, MUTED, y + 10)
    sheet.ax.plot([x, x + w], [y + 15, y + 15], color=LINE, linewidth=0.8)
    bottom = y + 15
    if not rows:
        text(sheet, x, bottom + 13, empty_text, size, False, MUTED)
        return bottom + row_h + 2
    for values in rows:
        cells(values, False, INK, bottom + 12)
        bottom += row_h
        sheet.ax.plot([x, x + w], [bottom + 1, bottom + 1], color=LINE, linewidth=0.5)
    return bottom + 2


def trend_chart(sheet, x, y, w, h, trend):
    """每天的平均：0～100，85、70 的虛線是分數帶（Good、Fair）；沒有服務的日子線斷開，不補 0。"""
    ax = chart_axes(sheet, x, y, w, h)
    positions = np.arange(len(trend.days))
    for line in trend.lines:
        values = np.array([np.nan if v is None else v for v in line.values], dtype=float)
        ax.plot(positions, values, color=INK if not line.dashed else MUTED, linewidth=1.2,
                linestyle="--" if line.dashed else "-", marker="o", markersize=2.5, label=line.label, zorder=3)
    for level, name in ((85, "Good"), (70, "Fair")):  # 標籤畫在圖的右邊外面，不會被線蓋住
        ax.axhline(level, color=AXIS, linewidth=0.6, linestyle=(0, (3, 3)), zorder=1)
        ax.text(1.01, level, name, transform=ax.get_yaxis_transform(), ha="left", va="center",
                fontproperties=_font(6), color=MUTED, clip_on=False)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.grid(axis="y", color=LINE, linewidth=0.6, zorder=0)
    step = max(1, int(np.ceil(len(trend.days) / 8)))
    ticks = list(range(0, len(trend.days), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([trend.days[i].strftime("%m-%d") for i in ticks])
    ax.set_xlim(-0.5, len(trend.days) - 0.5)
    if len(trend.lines) > 1:
        legend = ax.legend(loc="lower left", frameon=False, prop=_font(6.5), ncol=len(trend.lines))
        for label in legend.get_texts():
            label.set_color(MUTED)


def section_title(sheet, x, y, value):
    text(sheet, x, y, value, 9, True)


# =============================================================================
# 三種報表
# =============================================================================
def draw_service(page, photo=None):
    """每次服務報表，一頁（PLAN 7.4 的四段：頁首、人員與總分、三張分數卡、Audio／Text／Facial 三列）。"""
    sheet = new_sheet()
    width = A4_W - 2 * MARGIN
    header(sheet, page.service_id, page.time_range)

    # 人員與總分
    top, size = 82, 88
    photo_circle(sheet, MARGIN, top, size, photo)
    overall_w = 160
    name_x = MARGIN + size + 18
    name_w = A4_W - MARGIN - overall_w - GAP - name_x
    fit_line(sheet, name_x, top + size / 2 - (2 if page.job_title else -7), page.name, 20, True, name_w, min_size=12)
    if page.job_title:
        fit_line(sheet, name_x, top + size / 2 + 16, page.job_title, 11, False, name_w, MUTED, min_size=8)
    score_card(sheet, A4_W - MARGIN - overall_w, top, overall_w, size, page.overall, PAGE, edge=LINE,
               value_size=30)

    # 三張分數卡
    top = 186
    card_w = (width - 2 * GAP) / 3
    for number, card in enumerate(page.cards):
        score_card(sheet, MARGIN + number * (card_w + GAP), top, card_w, 66, card, CARD_TINTS[card.key])

    # 三列：左邊圖、右邊說明，各約一半；順序固定，不並排
    top = 266
    bottom = A4_H - 38
    row_h = (bottom - top - 2 * GAP) / 3
    half = (width - GAP) / 2
    for number, row in enumerate(page.rows):
        y = top + number * (row_h + GAP)
        box(sheet, MARGIN, y, half, row_h)
        section_title(sheet, MARGIN + PAD, y + PAD + 8, row.title)
        chart_x, chart_y = MARGIN + 48, y + PAD + 20
        chart_w, chart_h = half - 48 - PAD - 2, row_h - PAD - 20 - (34 if row.key == "facial" else 24)
        if row.chart is None:
            no_data(sheet, MARGIN, y + 20, half, row_h - 20)
        elif isinstance(row.chart, model.Series):
            line_chart(sheet, chart_x, chart_y, chart_w, chart_h, row.chart)
        else:
            bar_chart(sheet, chart_x, chart_y, chart_w, chart_h, row.chart)
        right = MARGIN + half + GAP
        box(sheet, right, y, half, row_h)
        explanation_box(sheet, right, y, half, row_h, row.key, row.comment, row.improvement)
    footer(sheet, page.footer, page.ai_note)
    return [sheet]


def _period_cards(sheet, top, cards):
    width = A4_W - 2 * MARGIN
    card_w = (width - (len(cards) - 1) * GAP) / len(cards)
    for number, card in enumerate(cards):
        fill = CARD_TINTS.get(card.key, PAGE)
        score_card(sheet, MARGIN + number * (card_w + GAP), top, card_w, 66, card, fill,
                   edge=None if card.key in CARD_TINTS else LINE)


def _service_rows(lines):
    return [[line.service_id, line.start, line.overall, line.audio, line.text, line.facial] for line in lines]


SERVICE_COLUMNS = [("Service ID", 2.2, "left"), ("Start", 2.0, "left"), ("Overall", 1, "right"),
                   ("Audio", 1, "right"), ("Text", 1, "right"), ("Facial", 1, "right")]


def draw_staff(page, photo=None):
    """每位員工、一段期間，一頁：人員與平均、四張卡、每天的趨勢、最高分與最低分各 3 筆（PLAN 8）。"""
    sheet = new_sheet()
    width = A4_W - 2 * MARGIN
    header(sheet, "Staff report", page.time_range)
    top, size = 82, 88
    photo_circle(sheet, MARGIN, top, size, photo)
    overall_w = 160
    name_x = MARGIN + size + 18
    name_w = A4_W - MARGIN - overall_w - GAP - name_x
    fit_line(sheet, name_x, top + size / 2 - (2 if page.job_title else -7), page.name, 20, True, name_w, min_size=12)
    if page.job_title:
        fit_line(sheet, name_x, top + size / 2 + 16, page.job_title, 11, False, name_w, MUTED, min_size=8)
    score_card(sheet, A4_W - MARGIN - overall_w, top, overall_w, size, page.overall, PAGE, edge=LINE,
               value_size=30)
    _period_cards(sheet, 186, (page.services, *page.cards))
    fit_line(sheet, MARGIN, 268, page.previous_note, 7, False, width, MUTED)

    top = 282
    box(sheet, MARGIN, top, width, 210)
    section_title(sheet, MARGIN + PAD, top + PAD + 8, "DAILY OVERALL SCORE")
    if page.trend is None:
        no_data(sheet, MARGIN, top + 20, width, 190)
    else:
        trend_chart(sheet, MARGIN + 34, top + PAD + 22, width - 34 - PAD - 26, 210 - PAD - 22 - 26, page.trend)

    top = 506
    for title, lines in (("HIGHEST-SCORING SERVICES", page.highest), ("LOWEST-SCORING SERVICES", page.lowest)):
        section_title(sheet, MARGIN, top + 8, title)
        empty = "No other services with an overall score in this period." \
            if title.startswith("LOWEST") and page.highest \
            else "No completed services with an overall score in this period."
        top = table(sheet, MARGIN, top + 16, width, SERVICE_COLUMNS, _service_rows(lines), empty) + 18
    footer(sheet, page.footer)
    return [sheet]


RANK_COLUMNS = [("Rank", 0.7, "right"), ("Staff", 4, "left"), ("Services", 1.2, "right"),
                ("Staff score", 1.4, "right"), ("Customer score", 1.6, "right")]
RANK_ROW = 16


def _scores_table(sheet, top, page):
    width = A4_W - 2 * MARGIN
    section_title(sheet, MARGIN, top + 8, "AVERAGE SCORES")
    columns = [("", 1.6, "left")] + [(model.LABELS[key], 1.6, "right") for key in ("overall", "audio", "text",
                                                                                    "facial")]
    rows = []
    for label, cards in (("Staff", page.staff_scores), ("Customer", page.customer_scores)):
        rows.append([label] + [card.value + (f"  ({card.change.split(' ')[0]})" if card.change else "")
                               for card in cards])
    return table(sheet, MARGIN, top + 16, width, columns, rows, "")


def draw_company(page, photo=None):
    """全公司、一段期間：服務數、員工數、兩側平均、每天的趨勢、員工排名（PLAN 8）。排名放不下時接著下一頁。"""
    width = A4_W - 2 * MARGIN
    sheets = []
    sheet = new_sheet()
    sheets.append(sheet)
    header(sheet, "Company report", page.time_range)
    _period_cards(sheet, 82, (page.services, page.staff_count))
    fit_line(sheet, MARGIN, 164, page.previous_note + " Changes are shown in parentheses.", 7, False, width, MUTED)
    top = _scores_table(sheet, 176, page) + 14

    box(sheet, MARGIN, top, width, 200)
    section_title(sheet, MARGIN + PAD, top + PAD + 8, "DAILY OVERALL SCORE")
    if page.trend is None:
        no_data(sheet, MARGIN, top + 20, width, 180)
    else:
        trend_chart(sheet, MARGIN + 34, top + PAD + 22, width - 34 - PAD - 26, 200 - PAD - 22 - 26, page.trend)
    top += 214

    rows = [[str(line.rank), line.name, str(line.services), line.staff, line.customer] for line in page.ranking]
    bottom = A4_H - 44
    title = "STAFF RANKING"
    while True:
        section_title(sheet, MARGIN, top + 8, title)
        fit = max(1, int((bottom - top - 16 - 15) // RANK_ROW))
        chunk, rows = rows[:fit], rows[fit:]
        table(sheet, MARGIN, top + 16, width, RANK_COLUMNS, chunk,
              "No completed services in this period.", row_h=RANK_ROW)
        if not rows:
            break
        sheet = new_sheet()
        sheets.append(sheet)
        header(sheet, "Company report", page.time_range)
        title, top = "STAFF RANKING (CONTINUED)", 82
    for number, sheet in enumerate(sheets, 1):
        footer(sheet, page.footer, page=f"Page {number} of {len(sheets)}" if len(sheets) > 1 else None)
    return sheets


DRAW = {model.ServicePage: draw_service, model.StaffPage: draw_staff, model.CompanyPage: draw_company}


def to_pdf(sheets, title):
    buffer = io.BytesIO()
    with PdfPages(buffer, metadata={"Title": title, "Author": "ADAM", "Creator": "ADAM analysis service"}) as pdf:
        for sheet in sheets:
            pdf.savefig(sheet.fig, dpi=300)  # dpi 只影響照片：字和圖都是向量
    return buffer.getvalue()


def draw(page, photo=None):
    """頁面模型 → Sheet 的清單（還沒存成 PDF；測試用來檢查位置）。要在 rendering() 裡面呼叫。"""
    return DRAW[type(page)](page, photo)


class rendering:
    """with rendering(): …：同一時間只畫一份，matplotlib 的設定只在這段時間生效。"""

    def __enter__(self):
        RENDER_LOCK.acquire()
        self._rc = matplotlib.rc_context(RC)
        self._rc.__enter__()

    def __exit__(self, *exc):
        try:
            self._rc.__exit__(*exc)
        finally:
            RENDER_LOCK.release()


def render(page, photo=None, title="ADAM report"):
    """頁面模型 → PDF 的 bytes。photo 是 load_photo() 的結果或 None（預設頭像）。"""
    with rendering():
        return to_pdf(draw(page, photo), title)


def load_photo(data, max_side=512):
    """大頭照的 bytes → RGB 陣列（裁成正方形、最長 512 px）；讀不出來是 None（畫預設頭像，報表照樣產生）。"""
    if not data:
        return None
    try:
        from PIL import Image  # matplotlib 本來就依賴 Pillow
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            side = min(image.size)
            left, top = (image.width - side) // 2, (image.height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            if side > max_side:
                image = image.resize((max_side, max_side), Image.LANCZOS)
            return np.asarray(image)
    except Exception:  # noqa: BLE001 — 任何讀不出來的檔案都當成沒有照片
        return None
