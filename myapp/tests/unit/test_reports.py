"""報告頁的 PDF（兩份不同的 PDFHandler）。PDF 用 PyMuPDF 在暫存資料夾現做。"""
from pathlib import Path

import fitz
import pytest

from myapp.init import admin_home_init, server_home_init


@pytest.fixture
def report_dir(tmp_path):
    """假的套件資料夾：PDFHandler 讀寫的是 <base_dir>/pdf/。"""
    (tmp_path / "pdf").mkdir()
    return tmp_path


def make_pdf(path, pages):
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    document.save(path)


def open_handler(module, report_dir):
    handler = module.PDFHandler()
    handler.base_dir = str(report_dir)  # 預設是套件資料夾 src/myapp；測試不能寫進原始碼
    shown = []
    handler.pdfPageChanged.connect(shown.append)
    return handler, shown


def test_staff_report_shows_first_page_as_image(report_dir):
    make_pdf(report_dir / "pdf" / "example.pdf", pages=2)
    handler, shown = open_handler(server_home_init, report_dir)
    handler.open_pdf("example.pdf")
    assert shown == [str(report_dir / "pdf" / "single_page.png")]
    assert Path(shown[0]).stat().st_size > 0


def test_admin_report_pages_stay_inside_document(report_dir):
    make_pdf(report_dir / "pdf" / "example2.pdf", pages=2)
    handler, shown = open_handler(admin_home_init, report_dir)
    pages = []
    handler.currentPageChanged.connect(lambda page, total: pages.append((page, total)))
    handler.open_pdf("example2.pdf")
    handler.previous_page()  # 已經在第 1 頁：不動
    handler.next_page()
    handler.next_page()  # 已經在最後一頁：不動
    handler.previous_page()
    assert pages == [(1, 2), (2, 2), (1, 2)]
    assert [Path(p).name for p in shown] == ["page_1.png", "page_2.png", "page_1.png"]


@pytest.mark.parametrize("module", [server_home_init, admin_home_init], ids=["staff", "admin"])
def test_missing_report_shows_nothing(module, report_dir):
    handler, shown = open_handler(module, report_dir)
    handler.open_pdf("example.pdf")  # repo 裡沒有 pdf/ 資料夾，實際上就是這個情況
    assert shown == []
