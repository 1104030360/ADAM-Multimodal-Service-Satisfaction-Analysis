"""側邊欄「Quit」呼叫的 clearDir()：先取消還在下載的檔案，再清掉影片、圖表、報表頁面、報表 PDF 的快取（兩端共用 myapp/media.py）。"""
import pytest

from myapp import local_storage, media
from myapp.db_lane import DbLane
from myapp.init import admin_home_init, server_home_init
from myapp.notifications import Notifier
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs
from tests.integration.helpers import ADMIN_ID, STAFF_ID


@pytest.fixture
def services():
    """建立主畫面需要的背景服務；不連資料庫（DbLane 第一次用到才連）。"""
    services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
    yield services
    services.tasks.shutdown()


@pytest.fixture(params=["staff", "admin"])
def home(request, services):
    if request.param == "staff":
        return server_home_init.ServerHome(STAFF_ID, services)
    return admin_home_init.AdminHome(ADMIN_ID, services)


def fill(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.png").write_bytes(b"cached")
    (folder / "half.avi.0123.download.part").write_bytes(b"half")  # 下載到一半的暫存檔也要清掉


def test_quit_empties_the_caches_next_to_the_module_by_default(home, tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_MEDIA_DIR")  # 沒有另外設定時，快取在 init/ 底下的 media/、charts/、reports/、reports-pdf/
    monkeypatch.delenv("LOCAL_CHARTS_DIR")
    monkeypatch.setattr(media, "CACHE_ROOT", str(tmp_path / "init"))  # 不能刪到真的快取
    for name in ("media", "charts", "reports", "reports-pdf"):
        fill(tmp_path / "init" / name)
    home.clearDir()
    for name in ("media", "charts", "reports", "reports-pdf"):
        assert list((tmp_path / "init" / name).iterdir()) == []  # 資料夾留著，裡面清空


def test_quit_empties_the_cache_dirs_set_in_env(home, tmp_path):
    # conftest 把 LOCAL_MEDIA_DIR、LOCAL_CHARTS_DIR 設成這兩個資料夾；報表頁面圖和影片放在同一層
    for name in ("media", "charts", "reports", "reports-pdf"):
        fill(tmp_path / name)
    home.clearDir()
    for name in ("media", "charts", "reports", "reports-pdf"):
        assert list((tmp_path / name).iterdir()) == []


def test_quit_does_not_create_cache_dirs_that_were_never_used(home, tmp_path):
    home.clearDir()
    assert not (tmp_path / "media").exists() and not (tmp_path / "reports").exists()
    assert not (tmp_path / "reports-pdf").exists()


def test_quit_cancels_downloads_before_clearing(home, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(home.mediaModel, "close", lambda: order.append("cancel downloads"))
    real_rmtree = local_storage.shutil.rmtree  # media.clear_cache() 用 local_storage.clear_folders() 清（T40）
    monkeypatch.setattr(local_storage.shutil, "rmtree",
                        lambda path, **kwargs: order.append("clear") or real_rmtree(path, **kwargs))
    fill(tmp_path / "media")
    home.clearDir()
    assert order == ["cancel downloads", "clear"]  # 下載還在寫檔時不清，免得清到一半又冒出新檔案
