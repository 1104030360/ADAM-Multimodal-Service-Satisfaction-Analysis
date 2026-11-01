"""管理員端不需要真的資料庫的規則：帳號等級與必填欄位、日期格式、區塊的狀態。首頁三張卡片的規則在 test_home_dashboard.py。

背景工作用真的 TaskRunner，db lane 換成假的 DbLane（FakeLane）：查詢的結果、例外都由測試決定。
"""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import mysql.connector
import pytest

from myapp.init.admin_home_init import AccountManager, AccountProfile, ServiceManager
from myapp.notifications import Notifier
from myapp.tasks import TaskRunner
from tests.integration.helpers import toasts, wait_until


class FakeCursor:
    def __init__(self, lane):
        self.lane = lane
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.lane.calls.append((" ".join(sql.split()), params))
        if self.lane.error is not None:
            raise self.lane.error

    def fetchone(self):
        return self.lane.rows[0] if self.lane.rows else None

    def fetchall(self):
        return list(self.lane.rows)


class FakeLane:
    """取代 DbLane：記下每一句 SQL；rows 是查詢的結果，error 是要丟出的例外。"""

    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []

    @contextmanager
    def cursor(self, dictionary=False):
        yield FakeCursor(self)


@pytest.fixture
def services():
    services = SimpleNamespace(tasks=TaskRunner(), notifier=Notifier(), db=FakeLane(), ui_prefs=None)
    yield services
    services.tasks.shutdown()


TASK_KEYS = ("account:add", "account:update", "admin:profile", "admin:search", "admin:staff-names")


def settle(services):
    """等管理員端的背景工作都交回結果（on_ok、on_err 在 GUI 執行緒執行）。"""
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in TASK_KEYS))


def mysql_error(errno, msg):
    return mysql.connector.Error(msg=msg, errno=errno)


# ---------------------------------------------------------------- 新增帳號：欄位與等級（D9、DS-52）


@pytest.fixture
def accounts(services):
    """等級 3 的管理員的 AccountManager，以及它發出的 (成功與否, 訊息) 和焦點要求。"""
    manager = AccountManager(services)
    manager.currentLevel = 3
    events = SimpleNamespace(added=[], updated=[], focus=[])
    manager.accountAdded.connect(lambda ok, message: events.added.append((ok, message)))
    manager.accountUpdated.connect(lambda ok, message: events.updated.append((ok, message)))
    manager.focusRequested.connect(events.focus.append)
    return manager, events


def errors(manager):
    return (manager.usernameError, manager.passwordError, manager.nameError, manager.levelError)


def test_assignable_levels_go_up_to_own_level(services):
    manager = AccountManager(services)
    assert manager.assignableLevels == []  # 還不知道自己的等級：選單是空的
    manager.currentLevel = 3
    assert manager.assignableLevels == [1, 2, 3]


def test_empty_required_fields_are_reported_and_nothing_is_saved(services, accounts):
    manager, events = accounts
    assert not manager.addAccount("  ", "", " ", "1", "")
    assert errors(manager) == ("Enter a username.", "Enter a password.", "Enter a name.", "")
    assert events.focus == ["username"]  # 焦點移到第一個有錯的欄位
    assert events.added == [(False, "Enter a username.")]
    assert services.db.calls == []

    manager.clearFieldError("username")  # 使用者改了這個欄位：它的錯誤先拿掉
    assert errors(manager) == ("", "Enter a password.", "Enter a name.", "")


def test_level_above_own_level_is_rejected_before_saving(services, accounts):
    manager, events = accounts
    assert not manager.addAccount("boss", "pw", "老闆", "5", "")
    assert manager.levelError == "Level 5 is above your level (3). Choose level 3 or lower."
    assert events.focus == ["level"]
    assert not manager.updateAccount("acc-9", "boss", "pw", "老闆", "4", "")  # 修改也一樣擋
    assert events.updated == [(False, "Level 4 is above your level (3). Choose level 3 or lower.")]
    assert services.db.calls == []


@pytest.mark.parametrize("level, message", [
    ("", "Choose a level."),
    ("0", "Choose a level."),
    ("abc", "Choose a level."),
])
def test_missing_or_invalid_level_is_a_field_error(accounts, level, message):
    manager, _ = accounts
    assert not manager.addAccount("x", "pw", "X", level, "")
    assert manager.levelError == message


def test_level_is_unknown_until_the_profile_loads(services):
    manager = AccountManager(services)
    assert not manager.addAccount("x", "pw", "X", "1", "")
    assert manager.levelError == "Your account level hasn't loaded yet. Try again in a moment."


def test_added_account_is_hashed_and_announced(services, accounts):
    manager, events = accounts
    assert manager.addAccount(" new_staff ", "S3cret-pass", "新同事", "1", "  ")
    assert manager.busy
    assert not manager.addAccount("other", "pw", "Other", "1", "")  # 還在新增：不重複送出
    settle(services)
    sql, params = services.db.calls[0]
    assert sql.startswith("INSERT INTO tbl_account")
    assert params[0] == "new_staff" and params[1].startswith("pbkdf2_sha256$") and params[2:] == ("新同事", "1", None)
    assert events.added == [(True, "Account added")]
    [toast] = toasts(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"]) == ("success", "Account added",
                                                                    "新同事 can log in as new_staff (level 1 · Staff).")
    assert not manager.busy


def test_messages_keep_user_text_that_looks_like_a_placeholder(services, accounts):
    manager, _ = accounts
    manager.addAccount("100%2off", "pw", "50%1", "1", "")
    settle(services)
    [toast] = toasts(services.notifier)
    assert toast["detail"] == "50%1 can log in as 100%2off (level 1 · Staff)."  # 名字裡的「%2」「%1」不會被替換


def test_added_admin_is_announced_with_the_admin_label(services, accounts):
    manager, _ = accounts
    manager.addAccount("lead", "pw", "Lead", "3", "")
    settle(services)
    [toast] = toasts(services.notifier)
    assert toast["detail"] == "Lead can log in as lead (level 3 · Admin)."  # 和選單同一個寫法（PLAN D9）


def test_duplicate_username_becomes_a_field_error(services, accounts):
    manager, events = accounts
    services.db.error = mysql_error(1062, "Duplicate entry 'staff07' for key 'tbl_account.uk_account_username'")
    manager.addAccount("staff07", "pw", "王小明", "1", "")
    settle(services)
    assert manager.usernameError == "Username staff07 is already taken. Choose another one."
    assert events.focus == ["username"] and events.added[-1][0] is False
    assert toasts(services.notifier) == []  # 欄位錯誤就夠了，不另外跳通知


def test_level_rejected_by_the_database_becomes_a_field_error(services, accounts):
    manager, events = accounts
    services.db.error = mysql_error(3819, "Check constraint 'chk_account_level' is violated.")
    manager.addAccount("x", "pw", "X", "2", "")
    settle(services)
    assert manager.levelError == "Choose a level."
    assert events.focus == ["level"]


def test_offline_add_offers_retry_that_reuses_the_hash(services, accounts):
    manager, events = accounts
    services.db.error = mysql_error(2003, "Can't connect to MySQL server on '127.0.0.1:1' (61)")
    manager.addAccount("new_staff", "S3cret-pass", "新同事", "1", "")
    settle(services)
    [toast] = toasts(services.notifier)
    assert (toast["severity"], toast["title"], toast["actionLabel"], toast["timeoutMs"]) == (
        "error", "Account wasn't saved", "Retry", 0)  # 常駐，直到重試或關掉
    assert toast["detail"] == "Can't reach the database. Your entries are still here, so you can retry."
    first_hash = services.db.calls[0][1][1]

    services.db.error = None
    services.notifier.trigger(toast["toastId"])  # 按通知上的 Retry
    settle(services)
    assert services.db.calls[1][1][1] == first_hash  # 明碼已經丟掉了，重試用同一個雜湊
    assert [(t["severity"], t["title"]) for t in toasts(services.notifier)] == [("success", "Account added")]
    assert events.added == [(False, toast["detail"]), (True, "Account added")]


def test_retry_that_hits_a_duplicate_clears_the_stale_offline_toast(services, accounts):
    manager, events = accounts
    services.db.error = mysql_error(2013, "Lost connection to MySQL server during query")
    manager.addAccount("staff07", "pw", "王小明", "1", "")
    settle(services)
    assert [t["title"] for t in toasts(services.notifier)] == ["Account wasn't saved"]

    # 斷線前其實已經有人用了這個帳號名稱：改按表單上的 Add Account，這次得到 1062
    services.db.error = mysql_error(1062, "Duplicate entry 'staff07' for key 'tbl_account.uk_account_username'")
    manager.addAccount("staff07", "pw", "王小明", "1", "")
    settle(services)
    assert toasts(services.notifier) == []  # 「沒有存到」的通知不留著，問題寫在欄位上
    assert manager.usernameError == "Username staff07 is already taken. Choose another one."
    services.notifier.actionTriggered.emit(AccountManager.RETRY_ACTION, 0)  # 舊通知的 Retry 也不會再送一次
    assert not manager.busy


def test_other_database_errors_are_reported_without_retry(services, accounts):
    manager, events = accounts
    services.db.error = mysql_error(1146, "Table 'adam.tbl_account' doesn't exist")
    manager.addAccount("x", "pw", "X", "1", "")
    settle(services)
    [toast] = toasts(services.notifier)
    assert (toast["title"], toast["actionLabel"]) == ("Account wasn't saved", "")
    assert toast["detail"] == "Database request failed. If this keeps happening, contact your administrator."


def test_update_account_reports_success_and_failure(services, accounts):
    manager, events = accounts
    assert manager.updateAccount("acc-2", "staff02", "New-pass", "員工", "1", "")
    settle(services)
    sql, params = services.db.calls[0]
    assert sql.startswith("UPDATE tbl_account") and params[-1] == "acc-2" and params[1].startswith("pbkdf2_sha256$")
    assert events.updated == [(True, "Account updated")]
    services.db.error = mysql_error(1062, "Duplicate entry 'staff01' for key 'tbl_account.uk_account_username'")
    manager.updateAccount("acc-2", "staff01", "pw", "員工", "1", "")
    settle(services)
    assert manager.usernameError == "Username staff01 is already taken. Choose another one."
    services.db.error = mysql_error(2013, "Lost connection to MySQL server during query")
    manager.updateAccount("acc-2", "staff02", "pw", "員工", "1", "")
    settle(services)
    assert toasts(services.notifier)[0]["title"] == "Account wasn't updated"
    assert [ok for ok, _ in events.updated] == [True, False, False]


# ---------------------------------------------------------------- 帳號資料（側邊欄的名字、等級上限）


def test_profile_loads_name_and_level_in_the_background(services):
    services.db.rows = [{"account_name": "系統管理員（佔位）", "account_level": "5"}]
    profile = AccountProfile(services, "acc-1")
    assert profile.state == "loading" and profile.name == ""
    profile.load()
    settle(services)
    assert (profile.state, profile.name, profile.level) == ("ready", "系統管理員（佔位）", 5)


def test_profile_failure_offers_retry_and_clears_the_toast_once_loaded(services):
    services.db.error = mysql_error(2003, "Can't connect to MySQL server")
    profile = AccountProfile(services, "acc-1")
    profile.load()
    settle(services)
    assert profile.state == "error"
    assert profile.errorDetail == "Can't reach the database. Check your network and try again."
    [toast] = toasts(services.notifier)
    assert toast["actionLabel"] == "Retry"

    services.db.error = None
    services.db.rows = [{"account_name": "管理員", "account_level": "3"}]
    services.notifier.trigger(toast["toastId"])
    settle(services)
    assert (profile.state, profile.level) == ("ready", 3)
    assert toasts(services.notifier) == []


def test_missing_account_is_an_error_not_level_zero(services):
    profile = AccountProfile(services, "gone")
    profile.load()
    settle(services)
    assert (profile.state, profile.level) == ("error", 0)


# ---------------------------------------------------------------- 服務查詢：日期格式、條件


def test_search_rejects_bad_dates_without_querying(services):
    manager = ServiceManager(services)
    assert manager.state == "idle"
    assert not manager.search("", "2026/10/01", "2026-10-31")
    assert manager.startDateError == "Enter the date as YYYY-MM-DD, for example 2026-10-31."
    assert manager.endDateError == ""
    assert services.db.calls == [] and manager.state == "idle"
    manager.clearDateError("start")
    assert manager.startDateError == ""


def test_search_covers_the_whole_end_day_and_remembers_the_query(services):
    manager = ServiceManager(services)
    assert manager.search("王小明", " 2026-10-01 ", "2026-10-31")
    settle(services)
    sql, params = services.db.calls[0]
    assert "a.account_name = %s" in sql and params[0] == "王小明"
    assert [p.isoformat() for p in params[1:-1]] == ["2026-10-01T00:00:00", "2026-11-01T00:00:00"]
    assert "s.service_status = 3" in sql and params[-1] == 8  # 只有分析完成的；一次 8 筆（UD7）
    assert manager.state == "empty"
    assert manager.lastQuery == {"name": "王小明", "start": "2026-10-01", "end": "2026-10-31"}

    services.db.error = mysql_error(2013, "Lost connection to MySQL server during query")
    manager.retrySearch()  # 區塊錯誤的 Retry 用同一組條件
    settle(services)
    assert manager.state == "error" and services.db.calls[1] == services.db.calls[0]


def test_search_with_only_an_end_date(services):
    manager = ServiceManager(services)
    start = datetime(2026, 10, 30, 9)
    services.db.rows = [{"service_id": "S20261030090000", "name": "王小明", "service_start": start,
                         "service_end": start, "customer_total": 90.0, "server_total": -1.0,
                         "total_count": 1, "first_start": start, "last_start": start}]
    assert manager.search("", "", "2026-10-31")
    settle(services)
    sql, params = services.db.calls[0]
    assert "s.service_start >= %s" not in sql and [p.isoformat() for p in params[:-1]] == ["2026-11-01T00:00:00"]
    assert manager.state == "ready" and manager.lastQuery == {"name": "", "start": "", "end": "2026-10-31"}
    assert manager.listModel.rowCount() == 1
    # 沒填開始日：範圍的開頭用查到的服務最早的那一天（T31 第 2 節）
    assert (manager.countText, manager.subtitle) == ("1 service", "All staff · Oct 30 – Oct 31, 2026 · newest first")


def test_staff_names_load_in_the_background(services):
    manager = ServiceManager(services)
    services.db.rows = [("王小明",), ("服務人員（佔位）",)]
    assert manager.loadNames()
    settle(services)
    assert (manager.namesState, manager.names) == ("ready", ["王小明", "服務人員（佔位）"])
    services.db.error = mysql_error(2003, "Can't connect to MySQL server")
    manager.loadNames()
    assert manager.namesState == "loading"
    settle(services)
    assert manager.namesState == "error" and manager.names == ["王小明", "服務人員（佔位）"]  # 舊的名單留著
    services.db.error, services.db.rows = None, []
    manager.loadNames()
    settle(services)
    assert manager.namesState == "empty"
