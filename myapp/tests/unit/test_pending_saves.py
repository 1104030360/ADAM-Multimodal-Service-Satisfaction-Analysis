"""還沒存完的錄影記在本機（T26，myapp/pending_saves.py）。

- 存在 QSettings("ADAM", "myapp")：測試裡是每個測試自己的暫存資料夾裡的 INI（tests/conftest.py）
- 一段錄影一個 key，值是 JSON；換一個新的 store（等於重開 app）還讀得到
- 只讀得到這個帳號的；看不懂的紀錄刪掉，不會每次登入都出錯
RecordingSaver 什麼時候寫、什麼時候刪，以及重開之後的 Retry，在 test_record_widget.py。
"""
import json
import logging
import os
import stat
from dataclasses import replace
from datetime import datetime

import pytest
from PySide6.QtCore import QSettings

from myapp.pending_saves import GROUP, PendingSave, PendingSaveStore
from myapp.ui_prefs import open_settings

START = datetime(2026, 10, 30, 12, 0, 0, 123456)  # datetime.now() 帶微秒
END = datetime(2026, 10, 30, 12, 9, 59, 654321)
FILES = (("video", "cam0.avi"), ("video", "cam1.avi"), ("audio", "audio.wav"))


def record(service_id="S20261030120000", account_id="acc-1", stage="upload", **changes):
    """一筆紀錄：檔案在 /Users/staff/video、audio 底下；stage 是 database 時有三個 URL。"""
    files = tuple(f"/Users/staff/{folder}/{service_id}_{name}" for folder, name in FILES)
    urls = tuple(f"s3://adam-test/{folder}/{service_id}_{name}" for folder, name in FILES)
    return replace(PendingSave(service_id, account_id, files, START, END, stage,
                               urls if stage == "database" else None), **changes)


def stored_keys():
    settings = open_settings()
    settings.beginGroup(GROUP)
    try:
        return settings.childKeys()
    finally:
        settings.endGroup()


def test_a_record_is_still_there_after_a_restart():
    uploaded = record(stage="database")
    PendingSaveStore().put(uploaded)
    assert PendingSaveStore().load("acc-1") == [uploaded]  # 新的 store、新的 QSettings：和重開 app 一樣
    assert PendingSaveStore().load("acc-2") == []
    # 和「減少動態」存在同一個地方；值是一段看得懂的 JSON
    raw = json.loads(open_settings().value(f"{GROUP}/S20261030120000"))
    assert raw == {"version": 1, "service_id": "S20261030120000", "account_id": "acc-1",
                   "files": list(uploaded.files), "start_time": "2026-10-30T12:00:00.123456",
                   "end_time": "2026-10-30T12:09:59.654321", "stage": "database", "urls": list(uploaded.urls)}


def test_records_are_per_account_and_in_recording_order():
    store = PendingSaveStore()
    later, earlier = record("S20261030130000"), record("S20261030120000", stage="database")
    other = record("S20261030123000", account_id="acc-2")
    for item in (later, other, earlier):
        store.put(item)
    assert store.load("acc-1") == [earlier, later]  # 依 service_id，也就是開始的時間
    assert store.load("acc-2") == [other]  # 別人的留著，等那個人登入


def test_put_overwrites_and_remove_forgets_one_record():
    store = PendingSaveStore()
    store.put(record())
    store.put(record("S20261030130000"))
    store.put(record(stage="database"))  # 上傳完了：同一個 key 改成 database
    assert [item.stage for item in store.load("acc-1")] == ["database", "upload"]
    store.remove("S20261030120000")
    store.remove("S19990101000000")  # 本來就沒有：不會出錯
    assert [item.service_id for item in store.load("acc-1")] == ["S20261030130000"]


def test_end_time_can_be_missing():
    unfinished = record(end_time=None)
    PendingSaveStore().put(unfinished)
    assert PendingSaveStore().load("acc-1") == [unfinished]


GOOD = record("S20261030120000")


@pytest.mark.parametrize("key, value", [
    ("S20261030130000", "not json"),
    ("S20261030130000", "[1, 2, 3]"),
    ("S20261030130000", json.dumps({"service_id": "S20261030130000"})),  # 少了欄位
    ("S20261030130000", record("S20261030130000").to_json().replace('"version": 1', '"version": 2')),
    ("S20261030130000", record("S20261030130000").to_json().replace('"upload"', '"analyze"')),
    ("S20261030130000", json.dumps({**json.loads(record("S20261030130000").to_json()), "files": ["/a", "/b"]})),
    ("S20261030130000", json.dumps({**json.loads(record("S20261030130000").to_json()), "stage": "database"})),
    ("S20261030130000", json.dumps({**json.loads(record("S20261030130000").to_json()), "urls": ["s3://a"] * 3})),
    ("S20261030130000", json.dumps({**json.loads(record("S20261030130000").to_json()), "start_time": "noon"})),
    ("S20261030130000", json.dumps({**json.loads(record("S20261030130000").to_json()), "start_time": 12})),
    ("S20261030130000", json.dumps({**json.loads(record("S20261030130000").to_json()), "account_id": 7})),
    ("S20261030140000", record("S20261030130000").to_json()),  # key 和內容的 service_id 不一樣
], ids=["not-json", "not-a-dict", "missing-fields", "newer-version", "unknown-stage", "two-files",
        "database-without-urls", "upload-with-urls", "bad-time", "time-not-text", "account-not-text", "wrong-key"])
def test_unreadable_records_are_dropped_and_logged(caplog, key, value):
    store = PendingSaveStore()
    store.put(GOOD)
    settings = open_settings()
    settings.setValue(f"{GROUP}/{key}", value)
    settings.sync()
    with caplog.at_level(logging.WARNING, logger="myapp.pending_saves"):
        assert store.load("acc-1") == [GOOD]  # 好的那一筆照樣讀得到
    assert stored_keys() == ["S20261030120000"]  # 看不懂的刪掉：下次登入不會再遇到
    assert f"dropping the unreadable pending save {key}" in caplog.text


def test_a_store_that_cannot_be_written_only_logs(tmp_path, caplog):
    locked = tmp_path / "settings" / "ADAM"  # conftest 把 INI 放在 tmp_path/settings；這個資料夾不能寫
    locked.mkdir(parents=True)
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        store = PendingSaveStore()
        with caplog.at_level(logging.WARNING, logger="myapp.pending_saves"):
            store.put(GOOD)  # 不丟例外：存檔照常進行，只是重開 app 之後沒有 Retry
            store.remove(GOOD.service_id)
        assert "couldn't remember S20261030120000" in caplog.text
        assert "couldn't forget S20261030120000" in caplog.text
        assert str(QSettings.Status.AccessError) in caplog.text
    finally:
        os.chmod(locked, stat.S_IRWXU)
