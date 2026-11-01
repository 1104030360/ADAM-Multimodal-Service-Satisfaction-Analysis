"""notifications.py：Notifier 的規則（DS-21）與例外 → 訊息的對照（ENG-12）。不需要 QML，也不需要計時器。"""
import errno
import logging
import threading
from concurrent.futures import CancelledError

import boto3.exceptions
import mysql.connector
import pytest
import requests
import s3transfer.exceptions
from _mysql_connector import MySQLInterfaceError
from botocore.exceptions import (ClientError, ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError,
                                 NoCredentialsError, PartialCredentialsError, ReadTimeoutError)
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QQmlContext, QQmlEngine, QQmlExpression

from myapp import notifications
from myapp.notifications import Notifier, user_message
from myapp.tasks import Cancelled
from tests.integration.helpers import toasts as rows

CONTRACT_ROLES = {"toastId", "severity", "title", "detail", "actionLabel", "actionId", "timeoutMs",
                  "done", "total", "count", "revision"}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def notifier(clock):
    return Notifier(clock=clock)


def titles(notifier):
    return [row["title"] for row in rows(notifier)]


# ---------------------------------------------------------------- 模型


def test_model_has_the_contract_roles(notifier):
    toast_id = notifier.notify("info", "Sent for analysis", "S20261031093000 is being analyzed.")
    assert {bytes(name).decode() for name in notifier.model.roleNames().values()} == CONTRACT_ROLES
    assert rows(notifier) == [{
        "toastId": toast_id, "severity": "info", "title": "Sent for analysis",
        "detail": "S20261031093000 is being analyzed.", "actionLabel": "", "actionId": "", "timeoutMs": 5000,
        "done": 0.0, "total": 0.0, "count": 1, "revision": 0,
    }]


def test_model_returns_nothing_for_bad_index_or_role(notifier):
    notifier.notify("info", "Hello")
    model = notifier.model
    assert model.data(model.index(5, 0), 257) is None
    assert model.data(model.index(0, 0), 0) is None  # DisplayRole 不是我們的角色
    assert model.rowCount(model.index(0, 0)) == 0  # 清單沒有子項目


# ---------------------------------------------------------------- 停留時間


@pytest.mark.parametrize("severity, timeout", [
    ("success", 4000), ("info", 5000), ("warning", 8000), ("error", 0), ("progress", 0),
])
def test_default_timeouts(notifier, severity, timeout):
    notifier.notify(severity, "Title")
    assert rows(notifier)[0]["timeoutMs"] == timeout


@pytest.mark.parametrize("severity, timeout_ms, expected", [
    ("success", -1, 8000),   # 有動作按鈕：至少 8 秒
    ("warning", -1, 8000),
    ("info", 3000, 8000),
    ("info", 12000, 12000),
    ("error", -1, 0),        # 常駐的還是常駐
])
def test_toasts_with_an_action_stay_at_least_8_seconds(notifier, severity, timeout_ms, expected):
    notifier.notify(severity, "Analysis complete", "", "", "open-analysis:S1", "View Results", timeout_ms)
    assert rows(notifier)[0]["timeoutMs"] == expected


def test_explicit_timeout_is_kept(notifier):
    notifier.notify("info", "Short", timeoutMs=1500)
    assert rows(notifier)[0]["timeoutMs"] == 1500


def test_unknown_severity_is_rejected(notifier):
    with pytest.raises(ValueError, match="unknown severity"):
        notifier.notify("fatal", "Nope")


# ---------------------------------------------------------------- 同一件事只留一則


def test_same_key_updates_in_place(notifier):
    changes = []
    notifier.model.dataChanged.connect(lambda top, bottom, roles: changes.append(top.row()))
    first = notifier.notify("error", "Can't reach the database", key="db-offline")
    second = notifier.notify("error", "Can't reach the database", "Still offline.", key="db-offline")
    assert first == second
    [row] = rows(notifier)
    assert (row["count"], row["revision"], row["detail"]) == (2, 1, "Still offline.")
    assert changes == [0]


def test_same_title_within_two_seconds_is_ignored(notifier, clock):
    first = notifier.notify("info", "Sent for analysis")
    clock.now += 1.9
    assert notifier.notify("info", "Sent for analysis") == first
    assert titles(notifier) == ["Sent for analysis"]
    clock.now += 0.2  # 距離上一則（被忽略的不算）超過 2 秒
    notifier.notify("info", "Sent for analysis")
    assert titles(notifier) == ["Sent for analysis"] * 2


def test_different_keys_with_the_same_title_are_both_kept(notifier):
    """兩個上傳都失敗：標題一樣，但各自有自己的 Retry，不能被當成重複。"""
    notifier.notify("error", "Can't reach cloud storage", key="upload:S1", actionId="retry-upload:S1")
    notifier.notify("error", "Can't reach cloud storage", key="upload:S2", actionId="retry-upload:S2")
    assert [row["actionId"] for row in rows(notifier)] == ["retry-upload:S2", "retry-upload:S1"]


def test_keyed_toast_blocks_a_keyless_duplicate(notifier):
    notifier.notify("error", "Can't reach the database", key="db-offline")
    notifier.notify("error", "Can't reach the database")
    assert len(rows(notifier)) == 1


# ---------------------------------------------------------------- 最多 3 則、排隊


def test_fourth_toast_pushes_out_the_oldest_transient_one(notifier):
    for title in ("one", "two", "three", "four"):
        notifier.notify("success", title)
    assert titles(notifier) == ["four", "three", "two"]  # 新的在第 0 列（ListView.BottomToTop 的最下面）


def test_sticky_toasts_are_not_pushed_out(notifier):
    notifier.notify("error", "error one")
    notifier.notify("success", "saved")
    notifier.notify("error", "error two")
    notifier.notify("info", "new")
    assert titles(notifier) == ["new", "error two", "error one"]


def test_new_toast_waits_when_three_sticky_ones_are_shown(notifier):
    overflow = []
    notifier.overflowCountChanged.connect(lambda: overflow.append(notifier.overflowCount))
    ids = [notifier.notify("error", f"error {n}") for n in range(3)]
    waiting = notifier.notify("info", "waiting")
    assert titles(notifier) == ["error 2", "error 1", "error 0"]
    assert notifier.overflowCount == 1 and overflow == [1]
    notifier.dismiss(ids[0])
    assert titles(notifier) == ["waiting", "error 2", "error 1"]
    assert notifier.overflowCount == 0 and overflow == [1, 0]
    assert rows(notifier)[0]["toastId"] == waiting


def test_waiting_toast_can_be_updated_or_dismissed(notifier):
    for n in range(3):
        notifier.notify("error", f"error {n}")
    waiting = notifier.notify("progress", "Uploading S1", key="upload:S1")
    notifier.notify("error", "S1 wasn't saved", key="upload:S1")  # 還在排隊時就有結果了
    assert notifier.overflowCount == 1 and len(rows(notifier)) == 3
    notifier.dismiss(waiting)
    assert notifier.overflowCount == 0
    notifier.dismiss(12345)  # 不存在：沒事


# ---------------------------------------------------------------- 動作


def test_trigger_emits_action_and_closes_the_toast(notifier):
    triggered = []
    notifier.actionTriggered.connect(lambda action, toast: triggered.append((action, toast)))
    toast_id = notifier.notify("success", "Analysis complete", "", "analysis:S1", "open-analysis:S1", "View Results")
    notifier.trigger(toast_id)
    assert triggered == [("open-analysis:S1", toast_id)]
    assert rows(notifier) == []
    notifier.trigger(toast_id)  # 已經關了：沒事
    assert triggered == [("open-analysis:S1", toast_id)]


def test_trigger_without_action_just_closes(notifier):
    triggered = []
    notifier.actionTriggered.connect(lambda action, toast: triggered.append(action))
    notifier.trigger(notifier.notify("info", "Hello"))
    assert triggered == [] and rows(notifier) == []


def test_retry_handler_can_reuse_the_same_key(notifier):
    """按 Retry 之後馬上用同一個 key 顯示上傳進度：新的這則不能被 trigger 關掉。"""
    notifier.actionTriggered.connect(lambda action, toast: notifier.progress("upload:S1", 0, 100, "Uploading S1"))
    failed = notifier.notify("error", "S1 wasn't saved", key="upload:S1", actionId="retry-upload:S1",
                             actionLabel="Retry")
    notifier.trigger(failed)
    [row] = rows(notifier)
    assert (row["severity"], row["title"]) == ("progress", "Uploading S1")


# ---------------------------------------------------------------- 進度


def test_progress_updates_numbers_in_place(notifier):
    changes = []
    notifier.model.dataChanged.connect(lambda top, bottom, roles: changes.append(sorted(roles)))
    notifier.progress("upload:S1", 0, 124, "Uploading S1", "File 1 of 3")
    notifier.progress("upload:S1", 58.6, 124)
    [row] = rows(notifier)
    assert (row["severity"], row["timeoutMs"], row["done"], row["total"]) == ("progress", 0, 58.6, 124.0)
    assert (row["title"], row["detail"], row["count"], row["revision"]) == ("Uploading S1", "File 1 of 3", 1, 0)
    roles = {bytes(name).decode(): role for role, name in notifier.model.roleNames().items()}
    assert changes == [sorted([roles["done"], roles["total"]])]  # 只有數字變了，文字的 binding 不必重算
    notifier.progress("upload:S1", 100, 124, "Uploading S1", "File 2 of 3")
    notifier.progress("upload:S1", 124, 124, "Saving S1")
    assert (rows(notifier)[0]["title"], rows(notifier)[0]["detail"]) == ("Saving S1", "File 2 of 3")
    assert changes[1:] == [sorted([roles["done"], roles["total"], roles["detail"]]),
                           sorted([roles["done"], roles["total"], roles["title"]])]


def test_progress_turns_into_the_result(notifier):
    notifier.progress("upload:S1", 124, 124, "Uploading S1")
    notifier.notify("success", "Saved S1", "Video and audio uploaded.", "upload:S1", "analyze:S1", "Analyze")
    [row] = rows(notifier)
    assert (row["severity"], row["title"], row["timeoutMs"]) == ("success", "Saved S1", 8000)
    assert (row["count"], row["revision"]) == (1, 1)  # 同一件事：計數不加，revision 加 1 讓 QML 開始計時
    assert (row["done"], row["total"]) == (0.0, 0.0)  # 結果不再帶進度條


def test_failed_toast_turns_back_into_progress_on_retry(notifier):
    notifier.notify("error", "S1 wasn't saved", key="upload:S1", actionId="retry-upload:S1", actionLabel="Retry")
    notifier.progress("upload:S1", 0, 0)
    [row] = rows(notifier)
    assert (row["severity"], row["timeoutMs"], row["actionId"], row["revision"]) == ("progress", 0, "", 1)
    assert row["title"] == "S1 wasn't saved"  # 沒給新標題就保留


def test_progress_needs_a_key(notifier):
    with pytest.raises(ValueError):
        notifier.progress("", 1, 2)


# ---------------------------------------------------------------- 例外 → 通知


def test_cancelled_work_shows_nothing(notifier):
    assert notifier.fromException(Cancelled()) == 0
    assert rows(notifier) == []


def test_offline_errors_share_one_toast(notifier):
    notifier.fromException(mysql.connector.errors.OperationalError(msg="Lost connection", errno=2013))
    notifier.fromException(mysql.connector.errors.DatabaseError(msg="Can't connect", errno=2003))
    [row] = rows(notifier)
    assert (row["title"], row["severity"], row["timeoutMs"], row["count"]) == ("Can't reach the database", "error",
                                                                             0, 2)


def test_retry_action_only_for_retryable_errors(notifier):
    notifier.fromException(requests.ConnectionError(), key="analyze:S1", actionId="retry-analyze:S1")
    notifier.fromException(NoCredentialsError(), key="upload:S1", actionId="retry-upload:S1")
    access, offline = rows(notifier)
    assert (offline["actionId"], offline["actionLabel"]) == ("retry-analyze:S1", "Retry")
    assert (access["actionId"], access["actionLabel"]) == ("", "")  # 憑證錯誤：重試沒有用


def test_caller_key_wins_over_category_key(notifier):
    notifier.progress("upload:S1", 10, 100, "Uploading S1")
    notifier.fromException(EndpointConnectionError(endpoint_url="https://s3.example"), key="upload:S1",
                           actionId="retry-upload:S1", actionLabel="Retry")
    [row] = rows(notifier)  # 進度那一則就地變成錯誤，不會另外多一則 s3-offline
    assert (row["severity"], row["title"]) == ("error", "Can't reach cloud storage")


def test_unexpected_error_is_logged_with_traceback(notifier, caplog):
    try:
        raise ValueError("bug")
    except ValueError as error:
        notifier.fromException(error)
    assert titles(notifier) == ["Something went wrong"]
    assert caplog.records[-1].levelno == logging.ERROR and caplog.records[-1].exc_info


def test_known_error_is_logged_as_warning(notifier, caplog):
    notifier.fromException(requests.ReadTimeout())
    assert caplog.records[-1].levelno == logging.WARNING


def test_clear_removes_everything(notifier):
    for n in range(4):
        notifier.notify("error", f"error {n}")
    overflow = []
    notifier.overflowCountChanged.connect(lambda: overflow.append(notifier.overflowCount))
    notifier.clear()
    assert rows(notifier) == [] and notifier.overflowCount == 0 and overflow == [0]
    notifier.notify("error", "error 0")  # 清掉之後，同樣的標題可以馬上再出現
    assert len(rows(notifier)) == 1


# ---------------------------------------------------------------- 從背景執行緒呼叫


def in_thread(call):
    results = []
    thread = threading.Thread(target=lambda: results.append(call()))
    thread.start()
    thread.join(5)
    return results[0]


def test_calls_from_a_worker_thread_appear_after_processing_events(notifier):
    toast_id = in_thread(lambda: notifier.notify("success", "Saved S1"))
    assert rows(notifier) == []  # 還在 GUI 執行緒的佇列裡
    QCoreApplication.processEvents()
    assert [row["toastId"] for row in rows(notifier)] == [toast_id]

    in_thread(lambda: notifier.progress("upload:S2", 1, 2, "Uploading S2"))
    in_thread(lambda: notifier.fromException(requests.ConnectTimeout()))
    QCoreApplication.processEvents()
    assert titles(notifier) == ["Can't reach the analysis service", "Uploading S2", "Saved S1"]

    triggered = []
    notifier.actionTriggered.connect(lambda action, toast: triggered.append(action))
    action_toast = notifier.notify("success", "Done", actionId="open", actionLabel="Open")
    in_thread(lambda: notifier.trigger(action_toast))
    in_thread(lambda: notifier.dismiss(toast_id))
    QCoreApplication.processEvents()
    assert triggered == ["open"] and "Saved S1" not in titles(notifier)

    in_thread(notifier.clear)
    QCoreApplication.processEvents()
    assert rows(notifier) == []


# ---------------------------------------------------------------- QML 呼叫得到


def test_qml_can_call_notifier_with_fewer_arguments(notifier):
    engine = QQmlEngine()
    context = QQmlContext(engine.rootContext())
    context.setContextProperty("notifier", notifier)
    for script in ('notifier.notify("info", "Two")',
                   'notifier.notify("info", "Three", "detail")',
                   'notifier.notify("info", "Four", "detail", "k4")',
                   'notifier.notify("info", "Five", "detail", "k5", "act")',
                   'notifier.notify("info", "Six", "detail", "k6", "act", "Go")',
                   'notifier.notify("error", "Seven", "detail", "k7", "act", "Go", 0)',
                   'notifier.progress("p", 1, 4)',
                   'notifier.model.rowCount() + notifier.overflowCount'):
        expression = QQmlExpression(context, None, script)
        value, _ = expression.evaluate()
        assert not expression.hasError(), f"{script}: {expression.error().toString()}"
    assert value == 3  # 非常駐的被擠掉，沒有排隊
    progress, seven, six = rows(notifier)
    assert (progress["severity"], progress["done"], progress["total"]) == ("progress", 1.0, 4.0)
    assert (seven["title"], seven["timeoutMs"], seven["actionId"], seven["actionLabel"]) == ("Seven", 0, "act", "Go")
    assert (six["title"], six["detail"], six["actionId"], six["timeoutMs"]) == ("Six", "detail", "act", 8000)
    QQmlExpression(context, None, "notifier.clear()").evaluate()
    assert rows(notifier) == []


# ---------------------------------------------------------------- 例外 → 訊息（ENG-12）


def client_error(code, status, operation="GetObject"):
    return ClientError({"Error": {"Code": code, "Message": "fake"}, "ResponseMetadata": {"HTTPStatusCode": status}},
                       operation)


def upload_failed(cause):
    try:
        try:
            raise cause
        except ClientError as error:
            raise boto3.exceptions.S3UploadFailedError(f"Failed to upload: {error}")
    except boto3.exceptions.S3UploadFailedError as wrapped:
        return wrapped


def raw_mysql_error(number):
    error = MySQLInterfaceError("Lost connection to MySQL server during query")
    error.errno = number
    return error


DB_OFFLINE = ("error", "Can't reach the database", "Check your network and try again.", True)
DB_ERROR = ("error", "Database request failed", "If this keeps happening, contact your administrator.", False)
S3_OFFLINE = ("error", "Can't reach cloud storage", "Check your network and try again.", True)
S3_ACCESS = ("error", "Can't access cloud storage",
             "The storage credentials are missing, expired, or don't have permission. Contact your administrator.",
             False)
S3_NOT_FOUND = ("warning", "File not found in cloud storage", "It may have been moved or deleted.", False)
UNEXPECTED = ("error", "Something went wrong",
              "An unexpected error occurred and was logged. If it keeps happening, contact your administrator.", False)

CASES = [
    (Cancelled(), ("", "", "", False)),
    (CancelledError(), ("", "", "", False)),
    (mysql.connector.errors.DatabaseError(msg="Can't connect", errno=2003, sqlstate="HY000"), DB_OFFLINE),
    (mysql.connector.errors.OperationalError(msg="Lost connection", errno=2013), DB_OFFLINE),
    (mysql.connector.errors.OperationalError(msg="Lost connection", errno=2055), DB_OFFLINE),
    (mysql.connector.errors.DatabaseError(msg="Unknown host", errno=2005), DB_OFFLINE),
    (mysql.connector.errors.DatabaseError(msg="Server closed idle connection", errno=4031), DB_OFFLINE),
    (mysql.connector.errors.OperationalError(msg="Too many connections", errno=1040, sqlstate="08004"), DB_OFFLINE),
    (mysql.connector.errors.InterfaceError("Connection to MySQL is not available"), DB_OFFLINE),
    (mysql.connector.errors.OperationalError("MySQL Connection not available."), DB_OFFLINE),
    (raw_mysql_error(2013), DB_OFFLINE),
    (raw_mysql_error(1146), DB_ERROR),
    (mysql.connector.errors.ProgrammingError(msg="Table doesn't exist", errno=1146, sqlstate="42S02"), DB_ERROR),
    (mysql.connector.errors.IntegrityError(msg="Duplicate entry", errno=1062, sqlstate="23000"), DB_ERROR),
    (mysql.connector.errors.DatabaseError(msg="Commands out of sync", errno=2014, sqlstate="HY000"), DB_ERROR),
    (mysql.connector.Error("something else"), DB_ERROR),
    (EndpointConnectionError(endpoint_url="https://s3.example"), S3_OFFLINE),
    (ConnectTimeoutError(endpoint_url="https://s3.example"), S3_OFFLINE),
    (ReadTimeoutError(endpoint_url="https://s3.example"), S3_OFFLINE),
    (ConnectionClosedError(endpoint_url="https://s3.example"), S3_OFFLINE),
    (boto3.exceptions.RetriesExceededError(ReadTimeoutError(endpoint_url="https://s3.example")), S3_OFFLINE),
    (s3transfer.exceptions.RetriesExceededError(TimeoutError("timed out")), S3_OFFLINE),  # TransferManager 直接丟的
    (NoCredentialsError(), S3_ACCESS),
    (PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY"), S3_ACCESS),
    (client_error("AccessDenied", 403, "PutObject"), S3_ACCESS),
    (client_error("ExpiredToken", 400), S3_ACCESS),
    (upload_failed(client_error("AccessDenied", 403, "PutObject")), S3_ACCESS),
    (client_error("NoSuchKey", 404), S3_NOT_FOUND),
    (client_error("404", 404, "HeadObject"), S3_NOT_FOUND),
    (client_error("NoSuchBucket", 404), UNEXPECTED),  # 設定錯誤，不是「找不到檔案」
    (client_error("InternalError", 500), UNEXPECTED),
    (boto3.exceptions.S3UploadFailedError("no cause"), UNEXPECTED),
    (requests.ConnectionError(), ("error", "Can't reach the analysis service", "Check your network and try again.",
                                  True)),
    (requests.ConnectTimeout(), ("error", "Can't reach the analysis service", "Check your network and try again.",
                                 True)),
    (requests.ReadTimeout(), ("error", "The analysis service didn't respond", "It may be busy. Try again in a moment.",
                              True)),
    (requests.HTTPError("503 Server Error"), ("error", "The analysis service returned an error",
                                              "Try again in a moment. If it keeps happening, contact your "
                                              "administrator.", True)),
    (FileNotFoundError(errno.ENOENT, "No such file"), ("error", "File not found on this computer",
                                                       "It may have been moved or deleted.", False)),
    (PermissionError(errno.EACCES, "Permission denied"), ("error", "Can't access a file on this computer",
                                                          "ADAM doesn't have permission to open or save it.", False)),
    (OSError(errno.ENOSPC, "No space left on device"), ("error", "Not enough disk space",
                                                        "Free up space on this computer, then try again.", False)),
    (OSError(errno.EDQUOT, "Disc quota exceeded"), ("error", "Not enough disk space",
                                                    "Free up space on this computer, then try again.", False)),
    (OSError(errno.EIO, "Input/output error"), UNEXPECTED),
    (ValueError("bug"), UNEXPECTED),
]


@pytest.mark.parametrize("error, expected", CASES,
                         ids=[f"{n:02d}-{type(error).__name__}" for n, (error, _) in enumerate(CASES)])
def test_user_message(error, expected):
    assert user_message(error) == expected


def test_messages_follow_the_copy_rules():
    """ENGLISH_COPY.md：句子式大小寫、沒有驚嘆號、技術代碼不放進文字；標題不加句點，內文是完整的句子。"""
    messages = [value for value in vars(notifications).values()
                if isinstance(value, notifications._Message) and value.severity]
    assert len(messages) == 12
    for message in messages:
        for text in (message.title, message.detail):
            assert "!" not in text and not any(character.isdigit() for character in text), text
            assert text[0].isupper(), text
        assert not message.title.endswith("."), message.title
        assert message.detail.endswith("."), message.detail
        assert message.detail.count(". ") <= 1, f"最多兩句：{message.detail}"
