"""整個 app 共用的那一條資料庫連線（步驟 2-2）：看得到新資料、斷線後自動重連。"""
import pytest

from myapp.database import DatabaseConnector
from myapp.init.server_home_init import ServiceModel
from tests.integration.helpers import STAFF_ID

pytestmark = pytest.mark.integration


def test_sees_rows_written_by_another_connection(db):
    model = ServiceModel(DatabaseConnector(), STAFF_ID)  # 第一次查詢：還沒有服務
    db.add_service("S20261030120000", "2026-10-30 12:00:00", status=1)  # 例如錄影視窗用自己的連線寫入
    model.fetch_uncompleted_services()
    # 沒開 autocommit 時，同一個交易一直讀第一次查詢的快照，看不到這一筆
    assert [service["service_id"] for service in model.uncompletedModel] == ["S20261030120000"]


def test_reconnects_after_server_drops_the_connection(db):
    connector = DatabaseConnector()
    old_id = connector.connection.connection_id
    db.execute(f"KILL {old_id}")  # 伺服器端切斷：像 RDS 閒置斷線或資料庫重開機
    assert connector.check_login("placeholder_staff", "CHANGE-ME-staff-0000")["success"]
    assert connector.connection.connection_id != old_id
