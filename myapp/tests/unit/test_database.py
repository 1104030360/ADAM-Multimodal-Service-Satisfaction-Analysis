"""database.py：連線設定、連不上資料庫時的行為（步驟 2-2）。真的登入與重連見整合測試。"""
import mysql.connector

from myapp.database import DatabaseConnector, connection_config
from tests.fakes import FakeConnection, FakeDatabase


def refuse(**config):
    raise mysql.connector.InterfaceError("Can't connect to MySQL server (fake)")


def test_connection_settings_come_from_environment(monkeypatch):
    monkeypatch.setenv("DB_HOST", "db.example")
    monkeypatch.setenv("DB_NAME", "adam")
    monkeypatch.setenv("DB_USER", "app")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    assert connection_config() == {
        "host": "db.example", "port": 3306, "database": "adam", "user": "app",
        "password": "secret", "connection_timeout": 10,
    }


def test_ca_file_turns_on_certificate_and_hostname_checks(monkeypatch):
    monkeypatch.setenv("DB_PORT", "3307")
    monkeypatch.setenv("DB_SSL_CA", "/certs/global-bundle.pem")
    config = connection_config()
    assert config["port"] == 3307
    assert config["ssl_ca"] == "/certs/global-bundle.pem"
    assert config["ssl_verify_cert"] and config["ssl_verify_identity"]


def test_login_fails_quietly_while_database_is_down(monkeypatch):
    monkeypatch.setattr(mysql.connector, "connect", refuse)
    db = DatabaseConnector()
    assert db.check_login("placeholder_staff", "CHANGE-ME-staff-0000") == {"success": False}
    assert db.get_account_name("00000000-0000-0000-0000-000000000002") == ""


def test_connects_on_next_use_when_database_was_down_at_startup(monkeypatch):
    monkeypatch.setattr(mysql.connector, "connect", refuse)
    DatabaseConnector()  # app 啟動時資料庫還沒好
    later = FakeConnection()
    monkeypatch.setattr(mysql.connector, "connect", lambda **config: later)
    assert DatabaseConnector().connection is later  # 同一個共用物件，這次連上了


def test_lost_connection_does_not_raise(monkeypatch):
    stale = FakeConnection(ping_error=mysql.connector.OperationalError("Lost connection (fake)"))
    monkeypatch.setattr(mysql.connector, "connect", lambda **config: stale)
    assert DatabaseConnector().connection is stale  # 只印出錯誤，畫面照常運作


def test_login_fails_quietly_on_database_error(monkeypatch):
    # 例如 2-5 改完沒重新載入資料庫：PROCEDURE adam.sp_get_login does not exist
    database = FakeDatabase(fail_on="sp_get_login")
    monkeypatch.setattr(mysql.connector, "connect", database.connect)
    assert DatabaseConnector().check_login("placeholder_staff", "pw") == {"success": False}
