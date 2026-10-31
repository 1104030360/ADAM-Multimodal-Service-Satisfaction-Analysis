"""側邊欄「Exit」呼叫的 clearDir()：清掉下載的影片與圖表快取（兩端各一份）。"""
import pytest

from myapp.init import admin_home_init, server_home_init

HOMES = pytest.mark.parametrize(
    "module, home", [(server_home_init, "ServerHome"), (admin_home_init, "AdminHome")], ids=["staff", "admin"])


def fill(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.png").write_bytes(b"cached")


def clear_dir(module, home):
    # clearDir 沒有用到 self，所以不必建立整個 ServerHome／AdminHome（那需要資料庫）
    getattr(module, home).clearDir(None)


@HOMES
def test_exit_empties_media_and_chart_cache(module, home, tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_MEDIA_DIR")  # 沒有另外設定時，快取在模組旁邊的 media/、charts/
    monkeypatch.delenv("LOCAL_CHARTS_DIR")
    # clearDir 用模組檔案的位置找資料夾；指到暫存資料夾，才不會刪到真的快取
    monkeypatch.setattr(module, "__file__", str(tmp_path / "init" / "module.py"))
    fill(tmp_path / "init" / "media")
    fill(tmp_path / "init" / "charts")
    clear_dir(module, home)
    assert list((tmp_path / "init" / "media").iterdir()) == []
    assert list((tmp_path / "init" / "charts").iterdir()) == []


@HOMES
def test_exit_empties_cache_dirs_set_in_env(module, home, tmp_path, monkeypatch):
    # 萬一 clearDir 還是清模組旁邊的資料夾，也只會清到暫存資料夾
    monkeypatch.setattr(module, "__file__", str(tmp_path / "init" / "module.py"))
    fill(tmp_path / "media")  # conftest 把 LOCAL_MEDIA_DIR、LOCAL_CHARTS_DIR 設成這兩個資料夾
    fill(tmp_path / "charts")
    clear_dir(module, home)
    assert list((tmp_path / "media").iterdir()) == []
    assert list((tmp_path / "charts").iterdir()) == []
