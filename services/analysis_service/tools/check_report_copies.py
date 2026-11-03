"""檢查報表的共用檔案沒有分岔（new-feature N4、N5；N6 的 AI 說明、PLAN1 W1 的服務結果、T49 的分析工作佇列也在清單裡）。在 Mac 上、repo 根目錄執行：

    python3 -I services/analysis_service/tools/check_report_copies.py

1. analysis_service/ 與 analyzer/ 的報表程式和共用測試逐位元組相同（兩個服務各自打包，只能複製，不能 import 對方）。
2. report_pdf.py 的 PALETTE 每個顏色都是桌面端 Theme.qml 的 token，名稱與值都相同（PDF 不自己發明顏色）。
全部相同時印 OK、結束碼 0；否則列出差異，結束碼 1。只讀檔案，不改任何東西。
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SERVICES = HERE.parents[2]  # tools → analysis_service → services
REPO = SERVICES.parent  # services → repo
SHARED = [
    "report_model.py", "report_pdf.py", "reports.py",
    "tests/unit/test_report_model.py", "tests/unit/test_report_pdf.py",
    "tests/integration/test_reports.py", "tests/integration/report_helpers.py",
    # new-feature N6：AI 說明（Ollama Cloud）
    "ollama_explain.py", "tests/unit/test_ollama_explain.py", "tests/unit/test_ollama_live.py",
    "tests/integration/test_report_explanations.py",
    # PLAN1 W1：服務結果與待辦（同一個 Ollama 用戶端）
    "service_outcome.py", "tests/unit/test_service_outcome.py", "tests/integration/test_outcome_jobs.py",
    # SQS（T49）：分析工作佇列（API 的接受交易、outbox 送出器、worker、管理指令）
    "analysis_queue.py", "tests/unit/test_analysis_queue.py", "tests/queue/conftest.py",
    "tests/queue/test_queue_flow.py",
]
THEME = REPO / "myapp" / "src" / "myapp" / "qml" / "components" / "Theme.qml"


def theme_tokens(source):
    return {name: value.upper() for name, value in
            re.findall(r'readonly property color (\w+):\s*"(#[0-9A-Fa-f]{6})"', source)}


def palette(source):
    block = re.search(r"^PALETTE = \{(.*?)^\}", source, re.S | re.M)
    if block is None:
        return {}
    return {name: value.upper() for name, value in re.findall(r'"(\w+)":\s*"(#[0-9A-Fa-f]{6})"', block[1])}


def main():
    problems = []
    for name in SHARED:
        left, right = SERVICES / "analysis_service" / name, SERVICES / "analyzer" / name
        if not left.exists() or not right.exists():
            problems.append(f"missing: {left if not left.exists() else right}")
        elif left.read_bytes() != right.read_bytes():
            problems.append(f"different: analysis_service/{name} and analyzer/{name}")
    tokens = theme_tokens(THEME.read_text(encoding="utf-8"))
    colours = palette((SERVICES / "analysis_service" / "report_pdf.py").read_text(encoding="utf-8"))
    if not colours:
        problems.append("report_pdf.py has no PALETTE")
    for name, value in colours.items():
        if tokens.get(name) != value:
            problems.append(f"PALETTE {name} = {value}, Theme.qml has {tokens.get(name)}")
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"OK: {len(SHARED)} shared files identical, {len(colours)} colours match Theme.qml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
