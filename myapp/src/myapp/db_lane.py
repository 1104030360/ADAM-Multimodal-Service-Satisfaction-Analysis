"""db lane 專用的 MySQL 連線（ENG-01、ENG-02、ENG-10）。

TaskRunner 的 db lane 只有一條執行緒，這條連線只屬於它：在 db lane 的工作裡用

    def load(token, report):
        with db.cursor(dictionary=True) as cursor:
            cursor.execute("CALL sp_get_login(%s)", (username,))   # 不用 callproc：callproc 要 4 個往返
            return cursor.fetchall()

    tasks.submit("db", "login", load, on_ok=..., on_err=...)

- 一般的查詢只有 1 個網路往返：cursor 建一次就一直重用（mysql-connector 的 C 擴充建 cursor 時會先 ping），
  不在每次查詢前 ping；閒置超過 idle_ping_after 秒才先 ping 一次，連線死了就重連。
- 連不上時，在 connect_timeout 秒內丟出 mysql.connector 的例外，交給 Notifier.fromException（ENG-02）。
  連線中斷就丟掉，下一次使用時再重連；不自動重試查詢（呼叫端才知道能不能重做）。
- 已經連上、但網路不再回應（Wi-Fi 斷掉）時，查詢最多等 read_timeout 秒。
- 轉換期間舊的 DatabaseConnector 單例照用，兩者不共用連線（F2 才把呼叫的地方搬過來）。
"""
import logging
import threading
import time
from contextlib import contextmanager

import mysql.connector

from myapp.database import connection_config

try:  # C 擴充有時直接丟出自己的例外，不是 mysql.connector.Error（例如斷線後呼叫 rollback()）
    from _mysql_connector import MySQLInterfaceError
except ImportError:  # pragma: no cover  沒有 C 擴充時，connector 會改用純 Python 的實作
    MySQLInterfaceError = ()

logger = logging.getLogger(__name__)


def is_connection_error(exc):
    """這個例外之後，連線是不是已經不能用了。

    算：用戶端錯誤（2000～2999，例如 2013 查詢中斷線、2014 指令順序錯亂）、沒有 errno 的「連線不可用」
    （InterfaceError、OperationalError）、伺服器因為閒置切斷（4031）、SQLSTATE 08（連線例外）、C 擴充直接丟出的例外。
    不算：SQL 本身的錯誤（語法、重複鍵、找不到資料表）和參數寫錯，連線還能繼續用。
    """
    if isinstance(exc, MySQLInterfaceError):
        return True
    if not isinstance(exc, mysql.connector.Error):
        return False
    if exc.errno == -1:
        return isinstance(exc, (mysql.connector.InterfaceError, mysql.connector.OperationalError))
    return 2000 <= exc.errno < 3000 or exc.errno == 4031 or (exc.sqlstate or "").startswith("08")


class DbLane:
    """db lane 那一條執行緒專用的 MySQL 連線（ENG-10）。只能在 db lane 的工作裡用。"""

    def __init__(self, config_factory=connection_config, idle_ping_after=30.0, connect_timeout=5,
                 *, read_timeout=10, clock=time.monotonic):
        """config_factory()：連線設定（預設讀 DB_* 環境變數）；逾時的單位都是秒，read_timeout=None 表示不限。"""
        self._config_factory = config_factory
        self._idle_ping_after = idle_ping_after
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._clock = clock
        self._connection = None
        self._cursors = {}  # dictionary → 重用的 buffered cursor
        self._owner = None  # 擁有這條連線的執行緒
        self._last_used = None

    @contextmanager
    def cursor(self, dictionary=False):
        """取得重用的 buffered cursor：需要時才連線；閒置超過 idle_ping_after 秒才先 ping。

        離開 with 時讀完剩下的結果集（不需要網路往返）。發生 SQL 錯誤時 rollback 還開著的交易；
        發生連線錯誤時丟掉這條連線，下一次使用時重連。例外照樣往外丟。
        """
        self._check_owner()
        cursor = self._cursor(bool(dictionary))
        try:
            yield cursor
        except BaseException as exc:
            self._after_error(exc, cursor)
            raise
        else:
            self._tidy(cursor)
        finally:
            self._last_used = self._clock()

    def close(self):
        """關掉連線。和查詢一樣，要在 db lane 的工作裡呼叫。"""
        self._check_owner()
        self._drop()

    def _check_owner(self):
        if self._connection is not None and self._owner != threading.get_ident():
            raise RuntimeError("DbLane belongs to the db lane thread; use it only inside db lane tasks")

    def _cursor(self, dictionary):
        connection = self._connect_if_needed()
        cursor = self._cursors.get(dictionary)
        if cursor is None:
            try:  # C 擴充的 cursor() 會先 ping 一次（1 個往返），所以每種 cursor 只建一次
                cursor = connection.cursor(dictionary=dictionary, buffered=True)
            except Exception as exc:
                if is_connection_error(exc):
                    self._drop()
                raise
            self._cursors[dictionary] = cursor
        return cursor

    def _connect_if_needed(self):
        if self._connection is not None and self._clock() - self._last_used > self._idle_ping_after:
            try:  # 閒置太久，連線可能已經被伺服器或 NAT 切斷：先 ping，死了就重連一次
                self._connection.ping(reconnect=False)
            except Exception as exc:
                logger.info("idle MySQL connection is gone (%s); reconnecting", exc)
                self._drop()
        if self._connection is None:
            config = {**self._config_factory(), "connection_timeout": self._connect_timeout, "autocommit": True}
            if self._read_timeout:
                config["read_timeout"] = self._read_timeout
            self._connection = mysql.connector.connect(**config)
            self._owner = threading.get_ident()
            self._last_used = self._clock()
        return self._connection

    def _after_error(self, exc, cursor):
        if self._connection is None:
            return
        if is_connection_error(exc):
            self._drop()
        else:  # SQL 錯誤或呼叫端的 bug：連線還能用，但交易不能留著，不然之後的查詢都會在這個交易裡
            self._tidy(cursor, rollback=True)

    def _tidy(self, cursor, rollback=False):
        """讓連線回到乾淨的狀態。CALL 會多一個結果集，不讀完下一個指令會得到 2014（Commands out of sync）。"""
        try:
            while cursor.nextset():
                pass
            if rollback and self._connection.in_transaction:
                self._connection.rollback()
        except Exception as exc:
            logger.warning("dropping the MySQL connection after cleanup failed: %s", exc)
            self._drop()

    def _drop(self):
        connection, self._connection = self._connection, None
        self._cursors = {}
        self._owner = None
        self._last_used = None
        if connection is not None:
            try:
                connection.close()
            except Exception:  # 連線本來就壞了；關不掉也沒關係
                pass
