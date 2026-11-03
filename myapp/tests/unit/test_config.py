"""設定檔（myapp/config.py，T51）：環境變數 → ~/Library/Application Support/ADAM/.env → repo 裡的 .env。

家目錄換成暫存資料夾（HOME），config 模組的位置（__file__）也換成暫存資料夾裡的路徑，測試不碰真的設定檔。
"""
import os

import pytest

from myapp import config


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    """打開 .env 的讀取（conftest 關掉了），家目錄指到暫存資料夾；load_dotenv 寫進去的變數在測試結束時還原。"""
    saved = dict(os.environ)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config, "_loaded", None)
    monkeypatch.setattr(config, "__file__", str(tmp_path / "repo" / "myapp" / "src" / "myapp" / "config.py"))
    for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "ONLY_IN_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    yield
    os.environ.clear()
    os.environ.update(saved)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def user_file(tmp_path):
    return tmp_path / "home" / "Library" / "Application Support" / "ADAM" / ".env"


COMPLETE = "DB_HOST=db.example\nDB_NAME=adam\nDB_USER=adam_app\nDB_PASSWORD=pw\n"


def test_the_application_support_file_is_read_first(tmp_path):
    expected = write(user_file(tmp_path), COMPLETE)
    write(tmp_path / "repo" / ".env", "DB_HOST=from-source\nONLY_IN_SOURCE=1\n")
    assert config.load() == expected
    assert os.environ["DB_HOST"] == "db.example"
    assert "ONLY_IN_SOURCE" not in os.environ  # 兩份不合併：找到第一份就不再讀第二份
    assert config.problem() == ""


def test_variables_already_set_win_over_the_file(tmp_path, monkeypatch):
    write(user_file(tmp_path), COMPLETE)
    monkeypatch.setenv("DB_HOST", "set-by-launchctl")
    config.load()
    assert os.environ["DB_HOST"] == "set-by-launchctl" and os.environ["DB_NAME"] == "adam"


def test_without_the_application_support_file_the_nearest_source_file_is_used(tmp_path):
    """briefcase dev、直接跑程式：和以前的 load_dotenv() 一樣，從模組的資料夾往上找（repo 根目錄或 myapp/）。"""
    write(tmp_path / "repo" / ".env", "DB_HOST=repo-root\n")
    expected = write(tmp_path / "repo" / "myapp" / ".env", COMPLETE)
    assert config.load() == expected
    assert os.environ["DB_HOST"] == "db.example"


def test_a_packaged_app_does_not_look_around_the_bundle(tmp_path, monkeypatch):
    """在 repo 裡 build 的 app 往上找得到開發用的 .env；搬到別的地方就找不到。打包的 app 一律不找。"""
    bundle = tmp_path / "repo" / "myapp" / "build" / "myapp" / "macos" / "app" / "ADAM.app"
    monkeypatch.setattr(config, "__file__", str(bundle / "Contents" / "Resources" / "app" / "myapp" / "config.py"))
    write(tmp_path / "repo" / ".env", COMPLETE)
    assert config.packaged()
    assert config.load() is None
    assert "DB_HOST" not in os.environ
    assert config.problem() == ("Can't find ADAM's settings. Put the .env file from your administrator in "
                                "~/Library/Application Support/ADAM/, then try again.")
    write(user_file(tmp_path), COMPLETE)  # 放好之後再找一次（登入畫面按 Log In）
    assert config.load() == user_file(tmp_path)
    assert config.problem() == ""


def test_nothing_is_read_when_dotenv_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "true")  # 測試（conftest）設的開關，python-dotenv 認得的值
    write(user_file(tmp_path), COMPLETE)
    assert config.load() is None
    assert "DB_HOST" not in os.environ


def test_no_settings_at_all_explains_where_the_file_goes():
    assert config.load() is None
    assert config.missing() == ["DB_HOST", "DB_NAME", "DB_USER"]
    assert "~/Library/Application Support/ADAM/" in config.problem()


def test_an_incomplete_file_names_what_is_missing(tmp_path):
    write(user_file(tmp_path), "DB_HOST=db.example\nDB_NAME=\n")
    config.load()
    assert config.problem() == ("The settings file ~/Library/Application Support/ADAM/.env is missing DB_NAME, "
                                "DB_USER. Add them, then try again.")


def test_settings_from_the_environment_alone_are_enough(monkeypatch):
    for name, value in (("DB_HOST", "db.example"), ("DB_NAME", "adam"), ("DB_USER", "adam_app")):
        monkeypatch.setenv(name, value)
    assert config.load() is None and config.problem() == ""


def test_paths_outside_the_home_folder_are_shown_in_full(tmp_path):
    assert config.display_path(tmp_path / "home" / "x" / ".env") == "~/x/.env"
    assert config.display_path("/etc/adam/.env") == "/etc/adam/.env"
