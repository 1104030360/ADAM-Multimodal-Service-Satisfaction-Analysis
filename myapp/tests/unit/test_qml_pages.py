"""16 個 QML 頁面和共用元件（qml/components/）都能編譯：抓打錯字、少了 import、PySide6 升級後不能用的元件。"""
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

QML_DIR = Path(__file__).resolve().parents[2] / "src" / "myapp" / "qml"
PAGES = sorted((QML_DIR / "pages").glob("*.qml"))
COMPONENTS = sorted((QML_DIR / "components").glob("*.qml"))


def compile_in_bare_engine(path):
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
    assert component.isReady(), component.errorString()


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_page_compiles(page):
    compile_in_bare_engine(page)


@pytest.mark.parametrize("component", COMPONENTS, ids=lambda component: component.name)
def test_component_compiles(component):
    compile_in_bare_engine(component)
