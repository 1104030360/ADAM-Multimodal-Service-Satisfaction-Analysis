"""db_lane.py 的規則，用假的連線測（不需要 Docker）。往返次數、斷線、逾時用真的 MySQL 測，見整合測試。"""
import threading

import mysql.connector
import pytest
from _mysql_connector import MySQLInterfaceError

from myapp.db_lane import DbLane, is_connection_error
from tests.fakes import FakeConnection, FakeDatabase


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class PingCountingConnection(FakeConnection):
    def __init__(self, database, ping_error=None):
        super().__init__(database, ping_error)
        self.pings = 0

    def ping(self, **kwargs):
        self.pings += 1
        super().ping(**kwargs)


@pytest.fixture
def database(monkeypatch):
    """取代 mysql.connector.connect：記下每次連線的設定，回傳可以數 ping 的假連線。"""
    fake = FakeDatabase(rows=[(1,)])
    fake.configs = []

    def connect(**config):
        fake.configs.append(config)
        connection = PingCountingConnection(fake)
        fake.connections.append(connection)
        return connection

    monkeypatch.setattr(mysql.connector, "connect", connect)
    return fake


def query(lane, sql="SELECT 1", dictionary=False):
    with lane.cursor(dictionary=dictionary) as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def test_connects_on_first_use_with_short_timeouts(database):
    lane = DbLane(config_factory=lambda: {"host": "db.example", "connection_timeout": 10})
    assert database.connections == []  # 建立時不連線
    assert query(lane) == [(1,)]
    assert database.configs == [{"host": "db.example", "connection_timeout": 5, "autocommit": True,
                                 "read_timeout": 10}]


def test_read_timeout_can_be_turned_off(database):
    query(DbLane(config_factory=dict, read_timeout=None))
    assert "read_timeout" not in database.configs[0]


def test_reuses_one_cursor_per_kind_and_never_pings_while_busy(database):
    clock = Clock()
    lane = DbLane(config_factory=dict, clock=clock)
    for _ in range(3):
        query(lane)
        query(lane, dictionary=True)
        clock.now += 29  # 每次間隔都沒超過 30 秒
    [connection] = database.connections
    assert connection.cursors_opened == 2 and connection.pings == 0


def test_pings_after_idle_and_reconnects_when_the_ping_fails(database):
    clock = Clock()
    lane = DbLane(config_factory=dict, clock=clock)
    query(lane)
    clock.now += 31
    query(lane)
    assert database.connections[0].pings == 1  # 閒置太久：先 ping，連線還在就照用
    database.connections[0].ping_error = mysql.connector.InterfaceError("Connection to MySQL is not available")
    clock.now += 31
    assert query(lane) == [(1,)]  # ping 失敗：關掉、重連，查詢照樣成功
    assert len(database.connections) == 2 and database.connections[0].closed


def test_connection_error_drops_the_connection(database):
    lane = DbLane(config_factory=dict)
    query(lane)
    with pytest.raises(mysql.connector.OperationalError):
        with lane.cursor():
            raise mysql.connector.errors.OperationalError(msg="Lost connection", errno=2013)
    assert database.connections[0].closed
    query(lane)
    assert len(database.connections) == 2  # 下一次使用時重連


def test_sql_error_keeps_the_connection_and_rolls_back(database):
    lane = DbLane(config_factory=dict)
    with pytest.raises(mysql.connector.Error):
        with lane.cursor() as cursor:
            database.connections[0].in_transaction = True  # 像 START TRANSACTION 之後
            database.fail_on = "INSERT"
            cursor.execute("INSERT INTO tbl_account VALUES (1)")
    connection = database.connections[0]
    assert connection.rolled_back and not connection.closed


def test_failed_cleanup_drops_the_connection(database, monkeypatch):
    lane = DbLane(config_factory=dict)
    query(lane)
    cursor = lane._cursors[False]
    monkeypatch.setattr(cursor, "nextset", lambda: (_ for _ in ()).throw(MySQLInterfaceError("Lost")), raising=False)
    query(lane)  # 查詢本身成功；收尾失敗只會丟掉連線
    assert database.connections[0].closed
    query(lane)
    assert len(database.connections) == 2


def test_failing_connect_raises_and_retries_next_time(monkeypatch):
    attempts = []

    def refuse(**config):
        attempts.append(config)
        raise mysql.connector.errors.DatabaseError(msg="Can't connect to MySQL server", errno=2003)

    monkeypatch.setattr(mysql.connector, "connect", refuse)
    lane = DbLane(config_factory=dict)
    for _ in range(2):
        with pytest.raises(mysql.connector.DatabaseError):
            query(lane)
    assert len(attempts) == 2


def test_cursor_creation_failure(database, monkeypatch):
    lane = DbLane(config_factory=dict)
    query(lane, dictionary=True)
    connection = database.connections[0]

    def lost(**kwargs):
        raise mysql.connector.errors.OperationalError("MySQL Connection not available.")

    monkeypatch.setattr(connection, "cursor", lost)
    with pytest.raises(mysql.connector.OperationalError):
        query(lane)  # 第一次要建 tuple cursor：建的時候發現斷線
    assert connection.closed

    second = database.connect()
    monkeypatch.setattr(mysql.connector, "connect", lambda **config: second)
    monkeypatch.setattr(second, "cursor", lambda **kwargs: (_ for _ in ()).throw(TypeError("bad argument")))
    with pytest.raises(TypeError):
        query(lane)
    assert not second.closed  # 不是連線錯誤：連線留著


def test_only_the_owning_thread_may_use_it(database):
    lane = DbLane(config_factory=dict)
    query(lane)
    errors = []

    def from_other_thread():
        for call in (lambda: query(lane), lane.close):
            try:
                call()
            except RuntimeError as error:
                errors.append(str(error))

    thread = threading.Thread(target=from_other_thread)
    thread.start()
    thread.join(5)
    assert errors == ["DbLane belongs to the db lane thread; use it only inside db lane tasks"] * 2
    lane.close()
    assert database.connections[0].closed
    thread = threading.Thread(target=lambda: query(lane))  # 關掉之後，下一個用的執行緒重新擁有它
    thread.start()
    thread.join(5)
    assert len(database.connections) == 2


@pytest.mark.parametrize("error, expected", [
    (mysql.connector.errors.OperationalError(msg="Lost connection", errno=2013), True),
    (mysql.connector.errors.DatabaseError(msg="Commands out of sync", errno=2014), True),
    (mysql.connector.errors.OperationalError("MySQL Connection not available."), True),
    (mysql.connector.errors.DatabaseError(msg="Server closed idle connection", errno=4031), True),
    (mysql.connector.errors.OperationalError(msg="Too many connections", errno=1040, sqlstate="08004"), True),
    (MySQLInterfaceError("Lost connection"), True),
    (mysql.connector.errors.ProgrammingError(msg="Syntax error", errno=1064, sqlstate="42000"), False),
    (mysql.connector.errors.IntegrityError(msg="Duplicate entry", errno=1062, sqlstate="23000"), False),
    (mysql.connector.errors.ProgrammingError("Not all parameters were used in the SQL statement"), False),
    (mysql.connector.errors.InternalError("Unread result found"), False),
    (ValueError("bug in the caller"), False),
], ids=["2013", "2014", "not-available", "4031", "08004", "raw-c-error", "1064", "1062", "bad-params", "unread",
        "not-mysql"])
def test_is_connection_error(error, expected):
    assert is_connection_error(error) is expected
