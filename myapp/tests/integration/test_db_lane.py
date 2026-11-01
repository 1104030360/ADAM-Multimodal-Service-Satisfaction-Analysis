"""DbLane 接真的 MySQL 8.4（ENG-01、ENG-02、ENG-10、ENG-21）。

往返次數由 latency_proxy 在線上數，只斷言次數，不斷言時間。斷線的測試把逾時設成 1 秒，
用 wait_until 等結果：結果要在逾時之內回來。
"""
import mysql.connector
import pytest

from myapp.database import connection_config
from myapp.db_lane import DbLane
from myapp.notifications import Notifier, user_message
from myapp.tasks import TaskRunner
from tests.integration.helpers import STAFF_ID, STAFF_NAME, toasts, wait_until

pytestmark = pytest.mark.integration


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def plain_tcp():
    """關掉 TLS，proxy 才看得懂每個封包是 PING 還是 QUERY。往返次數和走 TLS 時一樣。"""
    return {**connection_config(), "ssl_disabled": True}


def connection_id(lane):
    with lane.cursor() as cursor:
        cursor.execute("SELECT CONNECTION_ID()")
        return cursor.fetchall()[0][0]


def warm_up(lane):
    """第一次使用：連線（含 TLS 握手），兩種 cursor 建立時各 ping 一次。之後才開始數。"""
    for dictionary in (False, True):
        with lane.cursor(dictionary=dictionary) as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchall()


def test_each_query_is_one_round_trip(db_proxy):
    lane = DbLane()  # 和 app 一樣走 TLS（本機 MySQL 8.4 預設就有，和 RDS 相同）
    warm_up(lane)
    db_proxy.reset()
    with lane.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM tbl_account")
        assert cursor.fetchall() == [(2,)]
    with lane.cursor(dictionary=True) as cursor:
        cursor.execute("SELECT account_name FROM tbl_account WHERE account_username = %s", ("placeholder_staff",))
        assert cursor.fetchall() == [{"account_name": STAFF_NAME}]
    with lane.cursor(dictionary=True) as cursor:
        cursor.execute("CALL sp_get_login(%s)", ("placeholder_staff",))  # callproc 要 4 個往返
        assert cursor.fetchall()[0]["account_id"] == STAFF_ID
    with lane.cursor() as cursor:  # CALL 多出來的結果集已經讀掉了，下一個查詢不會 2014
        cursor.execute("SELECT 1")
        assert cursor.fetchall() == [(1,)]
    assert db_proxy.snapshot()["rt"] == 4
    lane.close()


def test_queries_never_ping_first(db_proxy):
    lane = DbLane(config_factory=plain_tcp)
    warm_up(lane)
    db_proxy.reset()
    for _ in range(3):
        connection_id(lane)
    assert db_proxy.snapshot()["cmds"] == {"QUERY": 3}
    lane.close()


def test_pings_once_after_being_idle(db_proxy):
    clock = Clock()
    lane = DbLane(config_factory=plain_tcp, clock=clock)
    warm_up(lane)
    clock.now += 31  # 閒置超過 30 秒
    db_proxy.reset()
    connection_id(lane)
    connection_id(lane)
    assert db_proxy.snapshot()["cmds"] == {"PING": 1, "QUERY": 2}
    lane.close()


def test_idle_connection_closed_by_the_server_is_replaced_quietly(db):
    clock = Clock()
    lane = DbLane(clock=clock)
    old = connection_id(lane)
    db.execute(f"KILL {old}")  # 像 RDS 的閒置逾時或重開機
    clock.now += 31
    assert connection_id(lane) != old  # ping 發現斷線、重連，查詢照樣成功
    lane.close()


def test_lost_connection_is_reported_and_the_next_use_reconnects(db):
    lane = DbLane()
    old = connection_id(lane)
    db.execute(f"KILL {old}")
    with pytest.raises(mysql.connector.Error) as lost:
        connection_id(lane)  # 剛用過，不先 ping：這次查詢失敗，交給使用者重試
    assert user_message(lost.value)[1] == "Can't reach the database"
    assert connection_id(lane) != old
    lane.close()


def test_failed_transaction_is_rolled_back(db):
    lane = DbLane()
    insert = ("INSERT INTO tbl_service (service_id, account_id, service_start, service_end, service_status) "
              "VALUES (%s, %s, NOW(), NOW(), 1)")
    with pytest.raises(ValueError):
        with lane.cursor() as cursor:
            cursor.execute("START TRANSACTION")
            cursor.execute(insert, ("S20261031000000", STAFF_ID))
            raise ValueError("呼叫端在交易中間出錯")
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    with lane.cursor() as cursor:  # 同一條連線回到 autocommit：別的連線馬上看得到
        cursor.execute(insert, ("S20261031000001", STAFF_ID))
    assert db.rows("SELECT service_id FROM tbl_service") == [("S20261031000001",)]
    lane.close()


@pytest.mark.parametrize("outage", ["kill", "stall"])
def test_outage_is_reported_within_the_timeouts(db_proxy, outage):
    """kill：連線被切斷、port 拒絕連線（資料庫重開機）。stall：封包都沒有回應（Wi-Fi 斷了）。

    已經連上的那條：kill 馬上斷線；stall 等 read_timeout。重連：kill 馬上被拒絕；stall 等 connect_timeout。
    兩次都在 GUI 執行緒收到錯誤，合併成同一則「連不上資料庫」的通知。
    """
    runner, notifier = TaskRunner(lanes={"db": 1}), Notifier()
    lane = DbLane(connect_timeout=1, read_timeout=1)
    results, errors = [], []

    def failed(error):
        errors.append(error)
        notifier.fromException(error, actionId="retry-db", actionLabel="Retry")

    def run(key):
        runner.submit("db", key, lambda token, report: connection_id(lane), on_ok=results.append, on_err=failed)

    run("connect")
    wait_until(lambda: results)
    getattr(db_proxy, outage)()
    run("old connection")
    wait_until(lambda: len(errors) == 1, timeout=3)
    run("reconnect")
    wait_until(lambda: len(errors) == 2, timeout=3)
    assert [user_message(error)[1] for error in errors] == ["Can't reach the database"] * 2
    [toast] = toasts(notifier)
    assert (toast["severity"], toast["count"], toast["actionLabel"]) == ("error", 2, "Retry")
    runner.submit("db", "close", lambda token, report: lane.close())
    wait_until(lambda: not runner.isBusy("close"))
    runner.shutdown()


def test_db_work_never_touches_the_network_on_the_gui_thread(db, no_network_on_gui_thread):
    runner = TaskRunner(lanes={"db": 1})
    lane = DbLane()
    names = []
    runner.submit("db", "name", lambda token, report: connection_id(lane), on_ok=names.append)
    wait_until(lambda: names)
    with pytest.raises(AssertionError, match="called on the GUI thread"):  # 對照：GUI 執行緒直接連線會被擋下
        with DbLane().cursor():
            pass
    no_network_on_gui_thread.clear()  # 這一次是故意的
    runner.submit("db", "close", lambda token, report: lane.close())
    wait_until(lambda: not runner.isBusy("close"))
    runner.shutdown()
