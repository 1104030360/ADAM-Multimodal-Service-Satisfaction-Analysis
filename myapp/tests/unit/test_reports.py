"""報表頁的 PDFHandler（兩端共用 myapp/media.py）：沒有來源時是空狀態（PLAN D7），有 PDF 時在 pdf lane 開檔、畫頁面（ENG-07）。

清單的每一份有名稱、頁數、日期（新版介面 PLAN 6-6）：列目錄在 GUI 執行緒，頁數在 pdf lane 數。
PDF 用 PyMuPDF 在暫存資料夾現做；等結果用 wait_until（一邊處理 Qt 事件），不用固定的 sleep。
"""
import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage

from myapp import media
from myapp.init import admin_home_init, server_home_init
from myapp.tasks import TaskRunner
from tests.integration.helpers import wait_until


@pytest.fixture
def source(tmp_path):
    """報表資料夾（以前寫死在套件的 src/myapp/pdf/）。"""
    folder = tmp_path / "pdf"
    folder.mkdir()
    return folder


@pytest.fixture
def runner():
    tasks = TaskRunner()
    yield tasks
    tasks.shutdown()


@pytest.fixture
def handler(source, runner):
    """用 app 共用的 TaskRunner（pdf lane）的 PDFHandler。"""
    return media.PDFHandler(SimpleNamespace(tasks=runner), reports_dir=str(source))


def make_pdf(path, pages, height=792):
    document = fitz.Document()
    for number in range(pages):
        document.new_page(width=612, height=height).insert_text((72, 72), f"Page {number + 1}")
    document.save(path)
    document.close()


def opened(handler, name, target_height=0):
    assert handler.open(name, target_height)
    wait_until(lambda: handler.viewState != "loading")
    return handler


def touch(path, moment):
    """把檔案的修改時間改成 moment（「YYYY-MM-DD HH:MM」，本地時間）：清單依它排序、顯示日期。"""
    stamp = datetime.strptime(moment, "%Y-%m-%d %H:%M").timestamp()
    os.utime(path, (stamp, stamp))


def counted(handler):
    """等頁數數完（pdf lane 上的 pdf:list 工作）。"""
    wait_until(lambda: all(report["pages"] for report in handler.reports))
    return handler.reports


def test_both_sides_use_the_shared_handler():
    assert server_home_init.PDFHandler is admin_home_init.PDFHandler is media.PDFHandler


def test_without_a_report_folder_the_page_is_empty(tmp_path):
    handler = media.PDFHandler(reports_dir=str(tmp_path / "missing"))
    assert handler.state == "idle"  # 還沒看過資料夾
    handler.refresh()
    assert (handler.state, handler.reports) == ("empty", [])
    assert not handler.open("example.pdf")  # 以前按 Open PDF 什麼都沒發生；現在沒有這份就不開


def test_the_default_source_is_the_package_pdf_folder():
    assert Path(media.REPORTS_DIR) == Path(media.__file__).parent / "pdf"
    assert media.PDFHandler().reports_dir == media.REPORTS_DIR


def test_only_pdf_files_are_listed_newest_first_with_title_and_date(handler, source):
    make_pdf(source / "September 2026.pdf", 1)
    make_pdf(source / "October 2026.PDF", 1)
    make_pdf(source / "b.pdf", 1)
    make_pdf(source / "a.pdf", 1)
    touch(source / "September 2026.pdf", "2026-10-01 09:00")
    touch(source / "October 2026.PDF", "2026-11-01 09:00")
    for name in ("a.pdf", "b.pdf"):  # 同一個時間：依檔名
        touch(source / name, "2026-08-31 18:30")
    (source / "notes.txt").write_text("not a report")
    (source / "folder.pdf").mkdir()
    handler.refresh()
    assert handler.state == "ready"
    assert [(report["name"], report["title"], report["modified"]) for report in handler.reports] == [
        ("October 2026.PDF", "October 2026", "Nov 1, 2026"),  # 副檔名不分大小寫，名稱不帶副檔名
        ("September 2026.pdf", "September 2026", "Oct 1, 2026"),
        ("a.pdf", "a", "Aug 31, 2026"),
        ("b.pdf", "b", "Aug 31, 2026")]


def test_page_counts_arrive_from_the_pdf_lane_and_are_not_counted_twice(handler, source, monkeypatch):
    make_pdf(source / "long.pdf", 4)
    make_pdf(source / "short.pdf", 1)
    (source / "broken.pdf").write_bytes(b"this is not a pdf")
    threads = []
    real_open = fitz.open
    monkeypatch.setattr(fitz, "open", lambda *args, **kwargs: threads.append(threading.current_thread().name)
                        or real_open(*args, **kwargs))
    handler.refresh()
    assert {report["name"]: report["pages"] for report in handler.reports} == {
        "long.pdf": 0, "short.pdf": 0, "broken.pdf": 0}  # 列目錄時還不知道：0＝還在數，不是「0 頁」
    assert {report["name"]: report["pages"] for report in counted(handler)} == {
        "long.pdf": 4, "short.pdf": 1, "broken.pdf": -1}  # 讀不出來的是 -1（清單只是不寫頁數）
    assert len(threads) == 3 and all(name.startswith("pdf-lane") for name in threads)  # PyMuPDF 只在 pdf lane 用

    threads.clear()
    handler.refresh()  # 檔案沒變：頁數直接用，不再開檔
    assert {report["name"]: report["pages"] for report in handler.reports} == {
        "long.pdf": 4, "short.pdf": 1, "broken.pdf": -1}
    assert threads == []

    make_pdf(source / "short.pdf", 2)  # 換了內容（大小、修改時間不同）：只重新數這一份
    handler.refresh()
    assert {report["name"]: report["pages"] for report in counted(handler)}["short.pdf"] == 2
    assert len(threads) == 1 and threads[0].startswith("pdf-lane")


def test_counting_applies_only_to_the_same_version_of_a_report(handler, source):
    """數頁數的期間清單換過：結果只套用檔名和版本都對得上的那幾份。"""
    make_pdf(source / "report.pdf", 2)
    handler.refresh()
    handler._counted({("report.pdf", (1, 1)): 9, ("gone.pdf", (1, 1)): 3})  # 舊版本、已經不在清單裡的
    assert handler.reports[0]["pages"] != 9
    assert counted(handler)[0]["pages"] == 2


def test_counting_stops_when_cancelled(source):
    make_pdf(source / "a.pdf", 1)
    token = SimpleNamespace(cancelled=True)
    assert media.count_pages(str(source), [("a.pdf", (1, 1))], token) == {}
    assert media.count_pages(str(source), [("a.pdf", (1, 1)), ("missing.pdf", (1, 1))]) == {
        ("a.pdf", (1, 1)): 1, ("missing.pdf", (1, 1)): -1}


def test_titles_drop_only_the_pdf_extension():
    assert [media.report_title(name) for name in ("October 2026.pdf", "a.b.PDF", "notes", "")] == [
        "October 2026", "a.b", "notes", ""]


def test_pages_render_on_the_pdf_lane_and_stay_inside_the_document(handler, source, tmp_path, monkeypatch):
    make_pdf(source / "report.pdf", 2)
    threads = []
    real_open = fitz.open
    monkeypatch.setattr(fitz, "open", lambda *args, **kwargs: threads.append(threading.current_thread().name)
                        or real_open(*args, **kwargs))
    handler.refresh()
    opened(handler, "report.pdf")
    assert (handler.viewState, handler.current, handler.page, handler.pageCount) == ("ready", "report.pdf", 1, 2)
    assert handler.currentTitle == "report"  # 檢視器的標題不帶副檔名
    assert threads and all(name.startswith("pdf-lane") for name in threads)  # PyMuPDF 只在 pdf lane 用
    first = handler.pageSource
    assert Path(QUrl(first).toLocalFile()).parent == tmp_path / "reports"  # 不寫進套件資料夾（ENG-07）
    assert handler.currentFileUrl == QUrl.fromLocalFile(str(source / "report.pdf")).toString()

    assert not handler.previousPage()  # 已經在第 1 頁
    assert handler.nextPage()
    wait_until(lambda: handler.viewState == "ready")
    assert handler.page == 2 and handler.pageSource != first
    assert not handler.nextPage()  # 已經在最後一頁
    assert handler.previousPage()
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.page, handler.pageSource) == (1, first)  # 畫過的頁面直接用


def test_page_resolution_follows_the_displayed_height(handler, source):
    make_pdf(source / "report.pdf", 1, height=800)
    handler.refresh()
    opened(handler, "report.pdf", target_height=1200)  # 顯示 600 px 高、2 倍的螢幕
    assert QImage(QUrl(handler.pageSource).toLocalFile()).height() == 1200
    default = media.render_page(str(source / "report.pdf"), 1, 0, str(source.parent / "out"))[2]
    assert QImage(default).height() == 800 * media.DEFAULT_ZOOM  # 不知道顯示多大時用 2 倍
    huge = media.render_page(str(source / "report.pdf"), 1, 100_000, str(source.parent / "out"))[2]
    assert QImage(huge).height() == 800 * media.MAX_ZOOM  # 最多 4 倍


def test_a_changed_pdf_gets_a_new_image_name(handler, source):
    """Image 依 URL 快取：同一個檔名換了內容，畫出來的圖檔名也要換，才不會顯示到舊圖（ENG-07）。"""
    make_pdf(source / "report.pdf", 1)
    handler.refresh()
    first = opened(handler, "report.pdf").pageSource
    make_pdf(source / "report.pdf", 3)
    handler.refresh()
    assert opened(handler, "report.pdf").pageSource != first
    assert handler.pageCount == 3


def test_a_damaged_pdf_shows_an_error_that_can_be_retried(handler, source):
    (source / "broken.pdf").write_bytes(b"this is not a pdf")
    handler.refresh()
    opened(handler, "broken.pdf")
    assert handler.viewState == "error"
    assert handler.errorDetail == "This PDF can't be read. It may be damaged."
    assert "FileDataError" in handler.errorTechnical
    assert handler.failureCount == 1
    assert handler.retry()
    wait_until(lambda: handler.viewState != "loading")
    assert handler.failureCount == 2
    make_pdf(source / "broken.pdf", 1)  # 換成好的檔案再試
    assert handler.retry()
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.failureCount, handler.errorDetail) == (0, "")
    assert not handler.retry()  # 沒有錯誤時不重試


def test_a_report_that_disappears_says_so(handler, source):
    make_pdf(source / "report.pdf", 1)
    handler.refresh()
    (source / "report.pdf").unlink()
    opened(handler, "report.pdf")  # 清單還是舊的：檔案在開之前被刪掉了
    assert handler.viewState == "error"
    assert handler.errorDetail == ("This PDF isn't in the reports folder anymore. "
                                   "It may have been moved or deleted.")
    handler.refresh()  # 重新看資料夾：選的那一份不在了，檢視器回到「還沒選」
    assert (handler.state, handler.viewState, handler.current, handler.pageSource) == ("empty", "idle", "", "")


def test_other_failures_use_the_general_message(handler, source, monkeypatch):
    make_pdf(source / "report.pdf", 1)
    handler.refresh()
    monkeypatch.setattr(media, "reports_cache_dir", lambda: str(source / "report.pdf"))  # 快取位置是檔案：存不了圖
    opened(handler, "report.pdf")
    assert handler.viewState == "error"
    assert handler.errorDetail.startswith("Something went wrong.")
    assert handler.errorTechnical.startswith("FileExistsError")


def test_without_services_the_handler_uses_its_own_pdf_lane(source):
    """ServerHome、AdminHome 建立 PDFHandler() 時沒有給 services：用自己的一條 pdf lane，照樣不在 GUI 執行緒開檔。"""
    make_pdf(source / "report.pdf", 1)
    handler = media.PDFHandler(reports_dir=str(source))
    handler.refresh()
    try:
        opened(handler, "report.pdf")
        assert handler.viewState == "ready"
    finally:
        handler._tasks().shutdown()


def test_bind_switches_to_the_shared_pdf_lane(source, runner):
    make_pdf(source / "report.pdf", 1)
    handler = media.PDFHandler(reports_dir=str(source))
    handler.bind(SimpleNamespace(tasks=runner))
    handler.refresh()
    seen = []
    runner.busyChanged.connect(lambda key, busy: seen.append((key, busy)))
    opened(handler, "report.pdf")
    assert ("pdf:report.pdf", True) in seen  # key 是 pdf:<檔名>（CONTRACTS §4）
    assert handler._own_tasks is None


def test_switching_reports_ignores_the_previous_result(handler, source):
    make_pdf(source / "a.pdf", 1)
    make_pdf(source / "b.pdf", 2)
    handler.refresh()
    handler.open("a.pdf")
    handler.open("b.pdf")  # 還沒畫完就換一份：a 的結果回來時不能蓋掉 b
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.current, handler.pageCount) == ("b.pdf", 2)
