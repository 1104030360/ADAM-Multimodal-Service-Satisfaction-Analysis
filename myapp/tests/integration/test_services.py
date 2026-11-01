"""登入後的畫面拿到的背景服務（AppServices）：MainWindow 交給 launch()，db lane 連得到資料庫。"""
import pytest

from myapp import main
from myapp.main import MainWindow
from tests.integration.helpers import STAFF_ID, wait_until

pytestmark = pytest.mark.integration


def test_db_lane_work_reads_the_database_off_the_gui_thread(services, no_network_on_gui_thread):
    def count_accounts(token, report):
        with services.db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM tbl_account")
            return cursor.fetchone()[0]

    results = []
    assert services.tasks.submit("db", "count", count_accounts, on_ok=results.append)
    wait_until(lambda: results)
    assert results == [2]  # db_schema.sql 的兩個佔位帳號


@pytest.mark.parametrize("slot, init_module", [
    ("launchServerHome", main.server_home_init),
    ("launchAdminHome", main.admin_home_init),
], ids=["staff", "admin"])
def test_main_window_hands_its_services_to_launch(engine, db, monkeypatch, slot, init_module):
    calls = []
    monkeypatch.setattr(init_module, "launch", lambda *args: calls.append(args))
    window = MainWindow(engine)
    getattr(window, slot)(STAFF_ID)
    [(_, account_id, services)] = calls
    assert account_id == STAFF_ID
    assert (services.tasks, services.notifier, services.ui_prefs) == (window.tasks, window.notifier, window.ui_prefs)
