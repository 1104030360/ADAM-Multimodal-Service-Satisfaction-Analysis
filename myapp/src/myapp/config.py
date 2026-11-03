"""桌面端的設定檔（.env，T51）：啟動時由 main() 讀一次，之後各模組照舊用 os.getenv() 讀環境變數。

找的順序，只讀找到的第一份（兩份不合併）：
1. 已經設好的環境變數：一律優先，檔案裡的同名變數不會蓋掉它（load_dotenv 的 override=False）。
2. ~/Library/Application Support/ADAM/.env：打包的 app 讀這一份。資料庫的密碼、IAM user 的金鑰每台電腦各放一份，
   不包進 app（pyproject 的 cleanup_paths 會拿掉誤放進原始碼資料夾的 .env）。
3. 從這個檔案的資料夾往上找到的第一個 .env（repo 根目錄或 myapp/）：以前三個地方各自呼叫 load_dotenv() 的找法，
   briefcase dev 用。打包的 app 不找這一份：在 repo 裡 build 的 app 往上找得到開發用的 .env，搬到別的地方就找不到，
   同一個 app 的行為會跟著放的位置改變。

PYTHON_DOTENV_DISABLED（測試設的）是 python-dotenv 的開關：設了就不讀任何檔案，只看環境變數。
登入要用的變數（LOGIN_SETTINGS）少了時，problem() 是登入畫面上的說明：檔案要放在哪裡、少了哪幾個。
"""
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtCore import QCoreApplication

logger = logging.getLogger(__name__)

FILE_NAME = ".env"
LOGIN_SETTINGS = ("DB_HOST", "DB_NAME", "DB_USER")  # 少了就登不進去（DB_PASSWORD 可以是空的）
_DISABLED = {"1", "true", "t", "yes", "y"}  # 和 python-dotenv 判斷 PYTHON_DOTENV_DISABLED 的值一樣

_loaded = None  # 讀過的那一份（Path）；還沒讀到任何一份是 None


def _tr(text, *values):
    """介面上的英文（PLAN D1）：經過 translate，日後可以加翻譯；%1、%2… 一次換成 values。"""
    text = QCoreApplication.translate("Settings", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


def user_folder():
    """打包的 app 讀設定檔的資料夾。"""
    return Path.home() / "Library" / "Application Support" / "ADAM"


def packaged():
    """從 briefcase build 出來的 .app 執行（程式在 ….app/Contents/Resources/app/ 底下），不是 briefcase dev。"""
    return ".app/Contents/" in Path(__file__).resolve().as_posix()


def source_file():
    """以前的找法：從這個檔案的資料夾往上，第一個找到的 .env。"""
    for folder in Path(__file__).resolve().parents:
        candidate = folder / FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def find_file():
    """照順序找第 2、3 份，回傳第一個存在的檔案；都沒有是 None。"""
    candidate = user_folder() / FILE_NAME
    if candidate.is_file():
        return candidate
    return None if packaged() else source_file()


def disabled():
    return os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in _DISABLED


def load():
    """main() 啟動時呼叫；登入時設定不完整也會再呼叫一次（使用者可能剛把檔案放好）。回傳這次讀的檔案或 None。"""
    global _loaded
    if disabled():
        return None
    path = find_file()
    if path is not None:
        load_dotenv(path, override=False)
        _loaded = path
        logger.info("settings loaded from %s", display_path(path))
    return path


def missing(names=LOGIN_SETTINGS):
    """還沒設定（或是空的）變數。"""
    return [name for name in names if not os.getenv(name)]


def display_path(path):
    """給人看的路徑：家目錄寫成 ~。"""
    path = Path(path)
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


def problem():
    """登入畫面上的說明（英文）：登入要用的變數都有了是空字串。"""
    names = missing()
    if not names:
        return ""
    if _loaded is None:
        return _tr("Can't find ADAM's settings. Put the .env file from your administrator in %1, then try again.",
                   display_path(user_folder()) + "/")
    return _tr("The settings file %1 is missing %2. Add them, then try again.",
               display_path(_loaded), ", ".join(names))
