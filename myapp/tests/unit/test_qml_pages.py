"""16 個 QML 頁面都能編譯：抓打錯字、少了 import、PySide6 升級後不能用的元件。"""
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

PAGES = sorted((Path(__file__).resolve().parents[2] / "src" / "myapp" / "qml" / "pages").glob("*.qml"))


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_page_compiles(page):
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(page)))
    assert component.isReady(), component.errorString()
