"""介面文字與顏色的回歸測試（前端重建 F5，CONTRACTS §5 的 D2；PLAN D1、D2；DS-12、DS-13、DS-51、DS-71）。

改了頁面、元件或錄影視窗之後，下面幾種退步會讓測試失敗：

1. 介面字串出現中文（PLAN D1：介面全部是英文）。註解、docstring、log 不算。
2. 介面字串出現 Server 這個舊用詞（DS-51：一律用 Staff）。檔名、id 不算（server_home.qml、serverHome）。
3. QML 裡給人看的字串沒有包在 qsTr() 裡（日後加翻譯只要多一個翻譯檔）。
4. 動作用了 Material 的綠色、紅色、粉紅，或在頁面、元件裡寫死顏色（D2、DS-71）。白字在 #4CAF50 上只有 2.78:1，
   紅色分頁 #E53935 上 4.23:1；顏色一律取自 Theme（CONTRACTS §0），錄影視窗的 QSS 取自 myapp/theme.py。
5. myapp/theme.py 和 Theme.qml 的值不一樣（ENG-18 坑 5：單一來源是 Theme.qml）。

掃描器故意寫得很簡單，規則都在這個檔案裡：
- QML：qml_tokens() 跳過 // 和 /* */ 註解，取出 "…"、'…' 字串；緊接在 qsTr( 後面的字串算「已經包好」。
  is_ui_text() 決定哪些字串是給人看的：有英文字母，而且有空白、大寫字母或標點的才算。
  全小寫或駝峰、沒有空白的（狀態名稱、key、objectName、"primary"）、路徑和檔名、顏色都不算。
- Python：python_ui_strings() 用 ast 取出字串常數；docstring、logger／logging／print 的參數、raise 的例外訊息不算。
- 最後一節的自我測試用小片段確認：每條規則都抓得到違規，註解裡的字不會被誤判。
"""
import ast
import re
from collections import namedtuple
from pathlib import Path

import pytest

from myapp import theme
from myapp.init import record_widget

SRC = Path(__file__).resolve().parents[2] / "src" / "myapp"
PAGES = SRC / "qml" / "pages"
COMPONENTS = SRC / "qml" / "components"
THEME_QML = COMPONENTS / "Theme.qml"

# 新加的頁面和元件自動被掃描
QML_FILES = sorted([*PAGES.glob("*.qml"), *COMPONENTS.glob("*.qml")])
# 顏色規則不掃 Theme.qml：顏色就定義在那裡
QML_COLOUR_FILES = [path for path in QML_FILES if path != THEME_QML]
# 給使用者看的字串也來自 Python：錄影視窗（QtWidgets）、兩個 init 模組（通知、裝置名稱）、明細頁和報表（media.py）、
# 首頁的日期（home_dashboard.py）、Services 頁的標題、日期、長度（service_list.py）、報表的清單和通知（reports.py）、
# 帳號資料（account.py，T36）、設定頁 Help 的連線檢查與 Storage（diagnostics.py、local_storage.py，T40）、
# 鏡頭的名稱（devices.py、device_setup.py，T41）
PYTHON_FILES = [SRC / "init" / "record_widget.py", SRC / "init" / "server_home_init.py",
                SRC / "init" / "admin_home_init.py", SRC / "media.py", SRC / "home_dashboard.py",
                SRC / "service_list.py", SRC / "account.py", SRC / "reports.py",
                SRC / "diagnostics.py", SRC / "local_storage.py",
                SRC / "devices.py", SRC / "device_setup.py",
                SRC / "coaching.py",   # 員工輔導的清單、明細、歷史和通知（PLAN1 W2，T45）
                SRC / "config.py"]     # 找不到設定檔時登入畫面的說明（T51）

# 中日韓的字和全形標點：U+3000–303F、U+3400–4DBF、U+4E00–9FFF、U+F900–FAFF、U+FF00–FFEF
CJK = re.compile("[" + "".join(f"{chr(first)}-{chr(last)}" for first, last in (
    (0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF))) + "]")
# Server、servers 是一個獨立的字才算；server_home.qml、serverHome、server_total 不算（見 says_server）
SERVER = re.compile(r"(?<![A-Za-z0-9_])servers?(?![A-Za-z0-9_])", re.IGNORECASE)
HEX_COLOUR = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})\b")
# Material 的綠色、紅色、粉紅（含相近的色系）：動作色只用 Theme 的石墨灰，紅綠留給狀態（DS-12、DS-13）
MATERIAL_ACCENT = re.compile(r"\bMaterial\.(?:Green|LightGreen|Lime|Teal|Red|DeepOrange|Pink)\b")
IDENTIFIER = re.compile(r"^[a-z][A-Za-z0-9_]*$")
SVG_PATH = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz][-\d\s.,MmLlHhVvCcSsQqTtAaZz]*$")   # Lucide 圖示的路徑資料
# 這幾種屬性的字串不是給人看的：objectName（含 key + "Tab" 這種拼接）、字型名稱、視窗狀態、
# 快捷鍵（Shortcut 的 sequence："Ctrl+1"，macOS 上顯示成 ⌘1）；console.* 是 log
NOT_UI_PREFIX = re.compile(r"(?:^|[{;])\s*(?:objectName|font\.family|visibility|sequence)\s*:[^;{}]*$|\bconsole\.\w+\(")

QmlString = namedtuple("QmlString", "line text wrapped prefix")   # prefix：同一行在字串前面的程式碼


# ---------------------------------------------------------------- 掃描器

def qml_tokens(source):
    """回傳（去掉註解的程式碼, [QmlString]）。

    註解換成空白（換行留著，行號不變）；字串原樣留在程式碼裡。wrapped：字串緊接在 qsTr( 後面。
    QML 檔沒有用到正規表示式和反引號字串，所以不處理。
    """
    code, strings = [], []
    i, line, n = 0, 1, len(source)
    while i < n:
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            code.append(" " * (end - i))
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            code.append(re.sub(r"[^\n]", " ", source[i:end]))
            line += source.count("\n", i, end)
            i = end
        elif source[i] in "\"'":
            quote, end = source[i], i + 1
            while end < n and source[end] != quote:
                end += 2 if source[end] == "\\" else 1
            before = "".join(code)
            prefix = before[before.rfind("\n") + 1:]
            wrapped = re.search(r"\bqsTr\(\s*$", prefix) is not None
            strings.append(QmlString(line, source[i + 1:end], wrapped, prefix))
            code.append(source[i:end + 1])
            line += source.count("\n", i, end)
            i = end + 1
        else:
            if source[i] == "\n":
                line += 1
            code.append(source[i])
            i += 1
    return "".join(code), strings


def is_ui_text(text, prefix=""):
    """這個字串看起來是給人看的（要包 qsTr）嗎？狀態名稱、key、路徑、顏色、圖示路徑、只有符號的都不是。"""
    if not re.search(r"[A-Za-z]", text):     # 「—」「 · 」「--:-- / --:--」
        return False
    if NOT_UI_PREFIX.search(prefix):          # objectName: key + "Tab"、font.family: "Arial"、console.log("…")
        return False
    if SVG_PATH.match(text) and re.search(r"\d", text):
        return False
    if IDENTIFIER.match(text):                # "loading"、"notAnalyzed"、"primary"、"text"
        return False
    if " " not in text and re.search(r"[/.]", text):   # "../../qml/pages/x.qml"、"icons/x.svg"
        return False
    if HEX_COLOUR.fullmatch(text):
        return False
    return re.search(r"[A-Z\s.,…?!:%]", text) is not None


def says_server(text):
    """用了 Server 這個舊用詞嗎（DS-51：用 Staff）？大寫開頭的一律算；小寫的只在句子裡（有空白）才算，
    所以程式裡當 key 的 "server"（例如 server_home_init.py 對應資料庫欄位的 key）不算。"""
    return any(match[0][0].isupper() or " " in text.strip() for match in SERVER.finditer(text))


def is_log_call(node):
    function = node.func
    if isinstance(function, ast.Name):
        return function.id == "print"
    return (isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name)
            and function.value.id in ("logger", "logging"))


def python_ui_strings(source):
    """Python 原始碼裡可能給人看的字串常數：[(行號, 內容)]。f-string 的固定部分也算。"""
    tree = ast.parse(source)
    skipped = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skipped.add(id(first.value))  # docstring
        if isinstance(node, ast.Call) and is_log_call(node):
            skipped.update(id(sub) for sub in ast.walk(node))
        if isinstance(node, ast.Raise) and node.exc is not None:
            skipped.update(id(sub) for sub in ast.walk(node.exc))  # 技術訊息，只會出現在 Details 和 log
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped]


def read(path):
    return path.read_text(encoding="utf-8")


def qml_strings(path):
    return qml_tokens(read(path))[1]


def qml_code(path):
    return qml_tokens(read(path))[0]


def theme_qml_colours(source):
    """Theme.qml 的顏色 token：{名稱: "#RRGGBB"}。別名（toastBg: sidebarBg）換成它指到的值。"""
    literal = dict(re.findall(r'readonly property color (\w+):\s*"(#[0-9A-Fa-f]{6})"', source))
    aliases = dict(re.findall(r"readonly property color (\w+):\s*([A-Za-z]\w*)\s*(?://.*)?$", source, re.M))
    return literal | {name: literal[target] for name, target in aliases.items() if target in literal}


def ids(paths):
    return [path.name for path in paths]


# ---------------------------------------------------------------- 規則

@pytest.mark.parametrize("path", QML_FILES, ids=ids(QML_FILES))
def test_qml_strings_have_no_chinese(path):
    """介面全部是英文（PLAN D1）。資料（人名、服務 ID）來自資料庫，不會寫在 QML 裡。"""
    assert [(item.line, item.text) for item in qml_strings(path) if CJK.search(item.text)] == []


@pytest.mark.parametrize("path", QML_FILES, ids=ids(QML_FILES))
def test_qml_strings_never_say_server(path):
    """DS-51：服務人員一律是 Staff（Top 5 Staff、Staff Scores、Show Staff Trend），不用 Server。"""
    assert [(item.line, item.text) for item in qml_strings(path) if says_server(item.text)] == []


@pytest.mark.parametrize("path", QML_FILES, ids=ids(QML_FILES))
def test_qml_ui_text_is_wrapped_in_qstr(path):
    unwrapped = [(item.line, item.text) for item in qml_strings(path)
                 if not item.wrapped and is_ui_text(item.text, item.prefix)]
    assert unwrapped == []


@pytest.mark.parametrize("path", QML_COLOUR_FILES, ids=ids(QML_COLOUR_FILES))
def test_qml_colours_come_from_theme(path):
    """沒有 Material 的綠、紅、粉紅（主要按鈕、選取的分頁改成石墨灰，D2）；也沒有寫死的顏色（CONTRACTS §0）。"""
    code = qml_code(path)
    assert MATERIAL_ACCENT.findall(code) == []
    assert [(item.line, item.text) for item in qml_strings(path) if HEX_COLOUR.search(item.text)] == []


@pytest.mark.parametrize("path", PYTHON_FILES, ids=ids(PYTHON_FILES))
def test_python_ui_strings_have_no_chinese_or_server(path):
    strings = python_ui_strings(read(path))
    assert [(line, text) for line, text in strings if CJK.search(text)] == []
    assert [(line, text) for line, text in strings if says_server(text)] == []


def test_record_widget_colours_come_from_theme_py():
    """錄影視窗的 QSS 顏色都來自 myapp/theme.py（和 Theme.qml 同一組值），record_widget.py 裡沒有寫死的顏色。"""
    path = SRC / "init" / "record_widget.py"
    assert [(line, text) for line, text in python_ui_strings(read(path)) if HEX_COLOUR.search(text)] == []
    allowed = set(theme.TOKENS.values()) | set(theme.RECORD_WINDOW.values())
    assert {name: value for name, value in record_widget.COLORS.items() if value not in allowed} == {}
    # 主要按鈕是石墨灰（白字 12.63:1），不是 Material Green（2.78:1）
    assert record_widget.COLORS["primary"] == theme.TOKENS["actionPrimary"] == "#E6E8EB"  # 淺色膠囊（UD3）
    style = record_widget._button_style(primary=True)
    assert "#4CAF50" not in style.upper() and theme.TOKENS["actionPrimary"] in style


def test_theme_py_matches_theme_qml():
    """ENG-18 坑 5：QtWidgets 讀不到 QML 的 Theme 單例，theme.py 的每個 token 都要和 Theme.qml 一樣。"""
    colours = theme_qml_colours(read(THEME_QML))
    assert {name: value for name, value in theme.TOKENS.items() if name not in colours} == {}
    assert {name: (value, colours[name]) for name, value in theme.TOKENS.items()
            if value.upper() != colours[name].upper()} == {}
    # 錄影視窗自己的值（視窗底色、預覽框）不是 Theme 的 token，名稱也不能和 token 撞在一起
    assert set(theme.RECORD_WINDOW) & set(colours) == set()



# ---------------------------------------------------------------- 掃描器本身（抓得到違規、不誤判註解）

SAMPLE = """\
import QtQuick
import QtQuick.Controls.Material
// 註解裡的中文、Server、Material.Green、"#4CAF50" 都不算
/* 區塊註解：text: "伺服器" */
Item {
    objectName: "serverHome"
    property string state: "notAnalyzed"
    Text { text: qsTr("Top 5 Staff") }
    Text { text: "服務人員" }
    Text { text: "Server Scores" }
    Text { text: cond ? qsTr("Daily Scores") : "Weekly Scores" }
    Button { Material.accent: Material.Green }
    Rectangle { color: "#E53935"; Image { source: "../components/icons/x_chromeIcon.svg" } }
    Text { text: "2d" + " · " + "—"; font.family: "Helvetica Neue" }
    Button {
        objectName: key + "Tab"
        onClicked: console.log("Clicked Analyze")
    }
    readonly property string icon: "M15 18l-6-6 6-6"
    Shortcut { sequence: "Ctrl+1"; onActivated: page.open() }
}
"""


def test_scanner_finds_each_kind_of_violation_but_skips_comments():
    code, strings = qml_tokens(SAMPLE)
    assert [(s.line, s.text) for s in strings if CJK.search(s.text)] == [(9, "服務人員")]
    assert [(s.line, s.text) for s in strings if says_server(s.text)] == [(10, "Server Scores")]
    # objectName 的拼接、console.log、字型名稱、圖示路徑、快捷鍵都不是給人看的字串
    assert [(s.line, s.text) for s in strings if not s.wrapped and is_ui_text(s.text, s.prefix)] == [
        (10, "Server Scores"), (11, "Weekly Scores")]
    assert MATERIAL_ACCENT.findall(code) == ["Material.Green"]  # 註解裡的那一個不算
    assert [s.text for s in strings if HEX_COLOUR.search(s.text)] == ["#E53935"]
    assert [s.text for s in strings if s.wrapped] == ["Top 5 Staff", "Daily Scores"]


def test_scanner_keeps_line_numbers_and_handles_escapes():
    code, strings = qml_tokens('/* a\nb */ Text {\n  text: qsTr("say \\"hi\\"")  // x\n  y: \'it\'\n}\n')
    assert [(s.line, s.text, s.wrapped) for s in strings] == [(3, 'say \\"hi\\"', True), (4, "it", False)]
    assert code.count("\n") == 5 and "// x" not in code and code.splitlines()[0].strip() == ""


def test_python_scanner_skips_docstrings_logs_and_exceptions():
    source = '''
"""模組說明：服務人員（Server）"""
import logging
logger = logging.getLogger(__name__)

def f(sid):
    """函式說明：中文。"""
    logger.warning("Server %s 失敗", sid)
    print("中文 log")
    if not sid:
        raise ValueError("伺服器 Server error")
    label = "Customer camera"
    return f"Saved {sid}", "服務", "Server Scores"
'''
    assert sorted(python_ui_strings(source)) == [
        (12, "Customer camera"), (13, "Saved "), (13, "Server Scores"), (13, "服務")]
    assert theme_qml_colours('readonly property color a: "#ABCDEF"\nreadonly property color b: a   // 別名\n') == {
        "a": "#ABCDEF", "b": "#ABCDEF"}


@pytest.mark.parametrize("text, expected", [
    ("Server Scores", True), ("Top 5 Servers", True), ("SERVER", True), ("Ask the server to retry", True),
    ("server", False),            # 程式裡的 key（資料庫欄位 server_total 對應到 "server"）
    ("server_home.qml", False), ("serverHome", False), ("server_total", False), ("Staff Scores", False),
])
def test_says_server(text, expected):
    assert says_server(text) is expected
