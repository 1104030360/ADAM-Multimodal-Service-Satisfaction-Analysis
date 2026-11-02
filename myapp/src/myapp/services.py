"""登入後的畫面共用的背景服務（前端重建 F2）。

MainWindow 建立一份，登入後交給 server_home_init.launch()／admin_home_init.launch()。
頁面的 Python 物件從這裡拿 TaskRunner、Notifier、DbLane、UiPrefs，不再各自在 GUI 執行緒連資料庫。
"""
from dataclasses import dataclass

from myapp.db_lane import DbLane
from myapp.notifications import Notifier
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs

CLOSE_DB_KEY = "db:close"


@dataclass(frozen=True)
class AppServices:
    tasks: TaskRunner
    notifier: Notifier
    db: DbLane        # 只能在 tasks 的 db lane 工作裡用（DbLane 會檢查執行緒）
    ui_prefs: UiPrefs

    def close_db(self):
        """在 db lane 上關掉 DbLane 的連線（只能在它自己的執行緒關）。回傳是否已經送出。"""
        return self.tasks.submit("db", CLOSE_DB_KEY, lambda token, report: self.db.close())
