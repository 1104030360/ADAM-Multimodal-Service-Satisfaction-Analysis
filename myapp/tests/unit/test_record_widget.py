"""錄影視窗與錄好之後的存檔（前端重建 F3，CONTRACTS §4 的 C1）。鏡頭、麥克風、時鐘、資料庫、S3 都是假的。

- 停止之後在背景收尾、上傳、寫資料庫；GUI 執行緒不等檔案寫完，馬上可以錄下一段（UX-08、ENG-03）
- 進度在狀態列和同一則通知上就地更新；成功有 Analyze，失敗留著檔案、可以重試（UX-10、DS-21、DS-23、DS-34、DS-52）
- 鏡頭、麥克風出問題時在原地說明（UX-11）；錄影中是不閃的紅點、經過時間、每秒最多 4 次的音量（UX-12）
- 關掉視窗、結束程式時先停止並存檔（UX-09）；⌘Q 由 QuitGuard 攔下來（UX-29）
- 還沒存完的記在本機，重開 app 之後同一個帳號還能 Retry；檔案不見了就說明存不了（T26）
- 錄影頁的 Saving 清單（SavesModel）：每一段的狀態、進度、原因；Retry、Analyze 和通知上的一樣（UD9、T29）
真的 MySQL、moto 和 QML 外殼的整段流程在 tests/integration/test_upload_flow.py。
"""
import gc
import os
import threading
import wave
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import botocore.exceptions
import cv2
import mysql.connector
import pytest
import sounddevice
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import QMessageBox

import tests.fakes
from myapp import storage
from myapp.db_lane import DbLane
from myapp.init import record_widget
from myapp.init.record_widget import (FAILED, FINISHING, SAVE_ROLES, SAVED, SAVING, UPLOADING, QuitGuard, Recording,
                                      RecordingSaver, RecordWidget, failure_detail, file_number,
                                      format_elapsed, level_from_peak, reduce_motion, transfer_text)
from myapp.init.server_home_init import SAVED_ROWS_KEPT, SavesModel
from myapp.notifications import Notifier
from myapp.pending_saves import PendingSave, PendingSaveStore
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import MOTION_FULL, MOTION_REDUCE, UiPrefs
from tests.fakes import FakeDatabase, FakeSaver, save_job, ticking_clock
from tests.integration.helpers import toasts, wait_until

REAL_VIDEO_CAPTURE = cv2.VideoCapture  # 讀錄好的 .avi 用真的；cameras fixture 會把 cv2.VideoCapture 換掉
S3_DOWN = botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com/adam-test")


# ── 替身與 fixture ────────────────────────────────────────────────────────────

class FakeUploads:
    """取代 storage.upload_with_progress：記下上傳了哪些 key，進度分兩次回報。

    - errors：依序給每一次呼叫的例外（None 表示那次成功），用完之後都成功。
    - hold(key)：上傳到這個 key 的一半時停下來，等測試呼叫 release()；停著時像真的傳輸一樣持續回報 0。
    """

    def __init__(self):
        self.keys = []
        self.errors = []
        self.threads = []
        self._holds = {}

    def hold(self, key):
        self._holds[key] = threading.Event()

    def release(self, key=None):
        for held, gate in self._holds.items():
            if key is None or held == key:
                gate.set()

    def __call__(self, local_path, key, token=None, report=None):
        self.threads.append(threading.current_thread())
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        size = os.path.getsize(local_path)
        report(size // 2, size)
        gate = self._holds.get(key)
        while gate is not None and not gate.wait(0.02):
            token.raise_if_cancelled()
            report(0, size)  # Throttle 只在被呼叫時送出：持續回報，停在一半的進度才看得到
        report(size - size // 2, size)
        self.keys.append(key)
        return f"s3://adam-test/{key}"


@pytest.fixture
def database(monkeypatch):
    """DbLane 連到的假資料庫：記下每一句 SQL，可以讓某一句失敗。"""
    fake = FakeDatabase()
    monkeypatch.setattr(mysql.connector, "connect", fake.connect)
    return fake


@pytest.fixture
def uploads(monkeypatch):
    fake = FakeUploads()
    monkeypatch.setattr(storage, "upload_with_progress", fake)
    yield fake
    fake.release()  # 不留下等著的背景執行緒


@pytest.fixture
def services(database, uploads):
    services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
    yield services
    services.tasks.shutdown()


@pytest.fixture
def saved():
    return []


@pytest.fixture
def saver(services, saved):
    return RecordingSaver(services, on_saved=saved.append)


@pytest.fixture
def open_window(monkeypatch, cameras, microphone, saver):
    """回傳一個「開錄影視窗」的函式；測試結束時關掉所有視窗（錄影中的會先停止）。"""
    monkeypatch.setattr(record_widget, "datetime", ticking_clock())
    windows = []

    def open_record_window(**kwargs):
        options = dict(saver=saver, camera_names=("Camera 0", "Camera 1"), microphone_name="MacBook 麥克風")
        window = RecordWidget(0, 1, 0, "acc-1", **(options | kwargs))  # 顧客鏡頭 0、員工鏡頭 1、麥克風 0
        windows.append(window)
        return window

    yield open_record_window
    for window in windows:
        window.close()
        if window.last_recording is not None:
            window.last_recording.wait(5)
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def widget(open_window):
    return open_window()


@pytest.fixture
def restart(services):
    """結束 app 再開一次（T26）：舊的背景工作取消（main.py 的 aboutToQuit），換新的背景服務和 RecordingSaver。
    本機的設定（QSettings）、假資料庫、假上傳都是同一份。回傳 (新的 services, 新的 saver)。"""
    started = []

    def restart_app():
        services.tasks.shutdown()
        new = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
        started.append(new)
        return new, RecordingSaver(new)

    yield restart_app
    for new in started:
        new.tasks.shutdown()


def record(widget, frames=3, chunks=3):
    """錄一段：開始 → 等假鏡頭、假麥克風把資料給完 → 停止。回傳這一段的 Recording。"""
    widget.start_recording()
    recording = widget.current
    try:
        wait_until(lambda: recording.frames_written >= frames and recording.audio_chunks >= chunks)
    finally:
        widget.stop_recording()  # 等不到也要停止，不然錄影執行緒會一直跑
    return recording


def wait_for(saver, recording, state):
    wait_until(lambda: saver.job(recording.service_id).state == state)
    return saver.job(recording.service_id)


def status(widget):
    bar = widget.status_bar
    return bar.title.text(), bar.detail.text()


def only_toast(notifier):
    [toast] = toasts(notifier)
    return toast


def pending(account_id="acc-1"):
    """本機記著、還沒存完的錄影（T26）。每次新建一個 store：和重開 app 之後讀到的一樣。"""
    return PendingSaveStore().load(account_id)


def uploaded_urls(recording):
    return tuple(f"s3://adam-test/{key}" for key in recording.keys)


# ── 停止 → 背景存檔 ────────────────────────────────────────────────────────────

def test_stopped_recording_is_uploaded_and_saved_in_the_background(widget, saver, services, uploads, database,
                                                                  saved):
    assert status(widget) == ("Ready to record", "")
    # 三個裝置的名稱在最上面那一行的右邊（設計稿 StaffRecorder），預覽的標題列也有鏡頭的名稱
    assert widget.device_names.text() == "Customer: Camera 0 · Staff: Camera 1 · Microphone: MacBook 麥克風"
    assert widget.windowTitle() == "Recorder"
    recording = record(widget)
    sid = recording.service_id
    job = wait_for(saver, recording, SAVED)

    assert uploads.keys == [f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi", f"audio/{sid}_audio.wav"]
    assert all(thread is not threading.main_thread() for thread in uploads.threads)  # io lane，不是 GUI 執行緒
    statements = [statement for statement, _ in database.calls]
    assert statements == ["START TRANSACTION", record_widget.INSERT_SERVICE_SQL, record_widget.INSERT_VIDEO_SQL,
                          record_widget.INSERT_VIDEO_SQL, record_widget.INSERT_AUDIO_SQL, "COMMIT"]
    service_args = database.calls[1][1]
    assert service_args == (sid, "acc-1", recording.start_time, recording.end_time, 1)  # 狀態 1：等著送分析
    assert [args[1:3] for _, args in database.calls[2:5]] == [
        (f"{sid}_cam0.avi", f"s3://adam-test/video/{sid}_cam0.avi"),
        (f"{sid}_cam1.avi", f"s3://adam-test/video/{sid}_cam1.avi"),
        (f"{sid}_audio.wav", f"s3://adam-test/audio/{sid}_audio.wav")]

    assert status(widget) == (f"Saved {sid}", "3 files uploaded")
    assert widget.status_bar.button.isVisibleTo(widget) and widget.status_bar.button.text() == "Analyze"
    toast = only_toast(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"]) == ("success", f"Saved {sid}",
                                                                    "Video and audio uploaded.")
    assert (toast["actionId"], toast["actionLabel"]) == (f"analyze:{sid}", "Analyze")
    assert saved == [sid]  # DeviceFinder 用它重新整理分析頁
    assert job.done == job.total == sum(os.path.getsize(path) for path in recording.files)
    assert widget.announced == [(f"Recording {sid}", False), (f"Saved {sid}. 3 files uploaded", False)]


def test_stop_does_not_wait_for_the_files_on_the_gui_thread(widget, saver, monkeypatch):
    gate = threading.Event()
    write_wav = Recording._write_wav

    def slow_write(self, frames):
        gate.wait(5)  # 錄音檔還沒寫完（例如很長的一段）
        write_wav(self, frames)

    monkeypatch.setattr(Recording, "_write_wav", slow_write)
    try:
        recording = record(widget)
        # stop_recording() 已經回來：沒有在 GUI 執行緒等執行緒結束，狀態列是「Saving recording…」
        assert saver.job(recording.service_id).state == FINISHING and not recording.finished
        assert status(widget) == ("Saving recording…", recording.service_id)
        assert widget.button_start_recording.isEnabled()  # 下一位顧客不必等（DS-34）
        progress = widget.status_bar.progress
        assert progress.isVisibleTo(widget) and progress.maximum() == 1  # 減少動態（測試）：靜止的軌道
    finally:
        gate.set()
    wait_for(saver, recording, SAVED)


def test_a_new_recording_can_start_while_the_previous_one_uploads(widget, saver, uploads, services):
    first = record(widget)
    uploads.hold(f"video/{first.service_id}_cam1.avi")
    wait_until(lambda: saver.job(first.service_id).file_number == 2)
    second = record(widget)  # 第一段還在傳第 2 個檔案
    assert second.service_id != first.service_id
    assert saver.job(first.service_id).state == "uploading"
    uploads.release()
    wait_for(saver, first, SAVED)
    wait_for(saver, second, SAVED)
    assert len(uploads.keys) == 6
    assert status(widget) == (f"Saved {second.service_id}", "3 files uploaded")  # 狀態列跟著最後一段
    assert [toast["title"] for toast in toasts(services.notifier)] == [f"Saved {second.service_id}",
                                                                      f"Saved {first.service_id}"]


def test_progress_updates_the_status_bar_and_one_toast_in_place(widget, saver, uploads, services):
    recording = record(widget)
    sid = recording.service_id
    uploads.hold(f"video/{sid}_cam1.avi")
    job = saver.job(sid)
    wait_until(lambda: job.file_number == 2 and job.done > job.sizes[0])
    toast = only_toast(services.notifier)  # 還是同一則：進度就地更新，不會疊兩則
    assert (toast["severity"], toast["title"], toast["detail"]) == ("progress", f"Uploading {sid}", "File 2 of 3")
    assert (toast["done"], toast["total"]) == (job.done, job.total) and job.total == sum(job.sizes)
    title, detail = status(widget)
    assert title == f"Uploading {sid}"
    assert detail == f"{transfer_text(job.done, job.total)} · File 2 of 3: {sid}_cam1.avi"
    bar = widget.status_bar.progress
    assert bar.isVisibleTo(widget) and bar.value() == round(1000 * job.done / job.total)
    toast_id = toast["toastId"]

    uploads.release()
    wait_for(saver, recording, SAVED)
    toast = only_toast(services.notifier)
    assert toast["toastId"] == toast_id and toast["title"] == f"Saved {sid}"  # 同一則變成結果
    assert toast["count"] == 1  # 進度變成結果不算第二次


# ── 失敗與重試（UX-10）────────────────────────────────────────────────────────

def test_failed_upload_keeps_the_files_and_retry_uploads_again(widget, saver, uploads, services, database):
    uploads.errors = [S3_DOWN]
    recording = record(widget)
    sid = recording.service_id
    job = wait_for(saver, recording, FAILED)
    assert all(os.path.exists(path) for path in recording.files)  # 檔案還在這台電腦上
    assert database.calls == []  # 沒有上傳完，不寫資料庫
    toast = only_toast(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"]) == (
        "error", f"{sid} wasn't saved", "Can't reach cloud storage. The recording is still on this computer.")
    assert (toast["actionId"], toast["actionLabel"], toast["timeoutMs"]) == (f"retry-upload:{sid}", "Retry", 0)
    assert status(widget) == (f"{sid} wasn't saved", "Can't reach cloud storage. The recording is still on this computer.")
    assert widget.status_bar.button.text() == "Retry"
    assert widget.announced[-1] == (f"{sid} wasn't saved. Can't reach cloud storage. The recording is still on "
                                    "this computer.", True)

    uploads.hold(f"video/{sid}_cam0.avi")
    widget.status_bar.button.click()  # 錄影視窗的 Retry（通知上的 Retry 見下一個測試）
    wait_until(lambda: job.state == "uploading")
    assert status(widget)[0] == f"Uploading {sid} (attempt 2)"  # 重試時標題加 (attempt 2)（DS-23）
    assert only_toast(services.notifier)["title"] == f"Uploading {sid} (attempt 2)"  # 同一則錯誤變回進度
    uploads.release()
    wait_for(saver, recording, SAVED)
    assert uploads.keys == [f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi", f"audio/{sid}_audio.wav"]
    assert only_toast(services.notifier)["title"] == f"Saved {sid}"


def test_database_failure_retries_only_the_database(widget, saver, uploads, services, database):
    database.fail_on = "sp_insert_audio_storage"
    recording = record(widget)
    sid = recording.service_id
    job = wait_for(saver, recording, FAILED)
    toast = only_toast(services.notifier)
    assert toast["detail"] == ("The files uploaded, but the database couldn't be updated. Retry to finish saving.")
    assert len(uploads.keys) == 3

    database.fail_on = None
    services.notifier.trigger(toast["toastId"])  # 通知上的 Retry → actionTriggered("retry-upload:<sid>")
    wait_for(saver, recording, SAVED)
    assert len(uploads.keys) == 3  # 檔案已經上傳了，只重寫資料庫
    assert job.attempt == 2
    assert [statement for statement, _ in database.calls].count("COMMIT") == 1


def test_missing_file_is_not_offered_a_retry(widget, saver, uploads, services):
    uploads.errors = [FileNotFoundError(2, "No such file or directory")]
    recording = record(widget)
    wait_for(saver, recording, FAILED)
    toast = only_toast(services.notifier)
    assert toast["detail"] == "File not found on this computer. It may have been moved or deleted."
    assert toast["actionId"] == "" and not widget.status_bar.button.isVisibleTo(widget)
    assert saver.retry(recording.service_id) is False  # 沒有 Retry 的，直接呼叫也不重試
    assert saver.retry("S19990101000000") is False  # 不認得的服務
    assert pending() == []  # 重開 app 之後也不會再問（T26）


def test_other_actions_and_repeat_retries_are_ignored(widget, saver, uploads, services):
    uploads.errors = [S3_DOWN]
    recording = record(widget)
    job = wait_for(saver, recording, FAILED)
    uploads.hold(f"video/{recording.service_id}_cam0.avi")
    services.notifier.actionTriggered.emit(f"open-analysis:{recording.service_id}", 0)  # 別的流程的動作
    assert job.state == FAILED
    assert saver.retry(recording.service_id) is True
    assert saver.retry(recording.service_id) is False  # 已經在重試了
    uploads.release()
    wait_for(saver, recording, SAVED)


# ── Analyze（交給送出分析的流程）──────────────────────────────────────────────

def test_analyze_in_the_recorder_is_the_same_as_the_toast_action(widget, saver, services):
    actions = []
    services.notifier.actionTriggered.connect(lambda action, toast_id: actions.append(action))
    recording = record(widget)
    sid = recording.service_id
    wait_for(saver, recording, SAVED)
    widget.status_bar.button.click()
    assert actions == [f"analyze:{sid}"]
    assert toasts(services.notifier) == []  # 那則「Saved」也關掉了，和按通知上的 Analyze 一樣
    assert not widget.status_bar.button.isEnabled()  # 不能連按送兩次
    assert saver.request_analyze(sid) is False


# ── 重開 app 之後還能重試（T26）──────────────────────────────────────────────

def test_a_save_is_remembered_on_this_computer_until_it_is_saved(widget, saver, services, uploads):
    widget.start_recording()
    recording = widget.current
    sid = recording.service_id
    uploads.hold(f"video/{sid}_cam0.avi")
    wait_until(lambda: recording.frames_written >= 3 and recording.audio_chunks >= 3)
    widget.stop_recording()
    job = wait_for(saver, recording, UPLOADING)
    [remembered] = pending()  # 一開始存檔就記下來：app 中途結束也還在
    assert (remembered.service_id, remembered.account_id, remembered.stage, remembered.urls) == (sid, "acc-1", "upload",
                                                                                              None)
    assert remembered.files == tuple(recording.files)
    assert (remembered.start_time, remembered.end_time) == (recording.start_time, recording.end_time)

    hold = threading.Event()
    services.tasks.submit("db", "test:hold", lambda token, report: hold.wait(5))  # db lane 卡住：停在寫資料庫之前
    try:
        uploads.release()
        wait_until(lambda: job.state == SAVING)
        [remembered] = pending()  # 三個檔案都上傳了：之後只要寫資料庫
        assert (remembered.stage, remembered.urls) == ("database", uploaded_urls(recording))
    finally:
        hold.set()
    wait_for(saver, recording, SAVED)
    assert pending() == []  # 存好就刪掉


def test_failed_upload_is_offered_again_after_a_restart(widget, saver, uploads, database, restart):
    uploads.errors = [S3_DOWN]
    recording = record(widget)
    sid = recording.service_id
    wait_for(saver, recording, FAILED)
    assert [item.stage for item in pending()] == ["upload"]

    services, saver = restart()  # 結束 app，同一個帳號再登入
    [job] = saver.restore("acc-1")
    assert job.service_id == sid and job.state == FAILED and not job.in_flight and saver.in_flight() == []
    toast = only_toast(services.notifier)  # 和失敗時同一種通知：常駐的錯誤＋Retry
    assert (toast["severity"], toast["title"], toast["detail"], toast["timeoutMs"]) == (
        "error", f"{sid} wasn't saved", "The upload didn't finish. The recording is still on this computer.", 0)
    assert (toast["actionId"], toast["actionLabel"]) == (f"retry-upload:{sid}", "Retry")
    assert database.calls == []

    services.notifier.trigger(toast["toastId"])  # 通知上的 Retry
    wait_until(lambda: job.state == SAVED)
    assert uploads.keys == recording.keys  # 從頭上傳三個檔案（上次在第一個檔案就失敗了）
    assert job.attempt == 2
    assert database.calls[1][1] == (sid, "acc-1", recording.start_time, recording.end_time, 1)
    assert [statement for statement, _ in database.calls].count("COMMIT") == 1
    assert only_toast(services.notifier)["title"] == f"Saved {sid}"
    assert pending() == []


def test_database_failure_is_offered_again_and_only_the_database_is_retried(widget, saver, uploads, database,
                                                                           restart):
    database.fail_on = "sp_insert_audio_storage"
    recording = record(widget)
    sid = recording.service_id
    wait_for(saver, recording, FAILED)
    [remembered] = pending()
    assert (remembered.stage, remembered.urls) == ("database", uploaded_urls(recording))

    database.fail_on = None
    for path in recording.files:
        os.remove(path)  # 檔案都上傳了：本機的檔案不在了也存得好
    services, saver = restart()
    [job] = saver.restore("acc-1")
    toast = only_toast(services.notifier)
    assert (toast["title"], toast["detail"], toast["actionLabel"]) == (
        f"{sid} wasn't saved", "The files uploaded, but the database couldn't be updated. Retry to finish saving.", "Retry")
    calls = len(database.calls)
    assert saver.retry(sid) is True
    wait_until(lambda: job.state == SAVED)
    assert len(uploads.keys) == 3  # 沒有重傳
    assert [args[1:3] for _, args in database.calls[calls + 2:calls + 5]] == [
        (os.path.basename(path), url) for path, url in zip(recording.files, uploaded_urls(recording))]
    assert pending() == []


def test_a_save_left_in_the_old_folder_is_retried_from_there(services, uploads, database, restart, tmp_path):
    """T51 之前錄影寫在目前目錄（briefcase dev 時是 ~/video、~/audio）。本機記的是絕對路徑：錄影換到
    RECORDINGS_DIR 之後，上次沒存完的照樣從原本的地方上傳，S3 的 key 也一樣。"""
    sid = "S20261102135229"
    old = tmp_path / "old-home"
    files = [old / "video" / f"{sid}_cam0.avi", old / "video" / f"{sid}_cam1.avi", old / "audio" / f"{sid}_audio.wav"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    PendingSaveStore().put(PendingSave(sid, "acc-1", tuple(str(path) for path in files), datetime(2026, 11, 2, 13, 52),
                                       datetime(2026, 11, 2, 13, 53), "upload"))
    services, saver = restart()
    [job] = saver.restore("acc-1")
    assert job.recording.files == [str(path) for path in files]
    assert saver.retry(sid) is True
    wait_until(lambda: job.state == SAVED)
    assert uploads.keys == [f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi", f"audio/{sid}_audio.wav"]
    assert pending() == []


def test_recordings_go_to_the_recordings_folder(monkeypatch, tmp_path):
    """RECORDINGS_DIR（conftest 指到暫存資料夾），沒有設定是 ~/Movies/ADAM（T51）。底下照舊是 video/、audio/。"""
    sid, start = "S20261103090000", datetime(2026, 11, 3, 9, 0)
    assert Recording(sid, "acc-1", (0, 1), 0, start).files == [
        str(tmp_path / "video" / f"{sid}_cam0.avi"), str(tmp_path / "video" / f"{sid}_cam1.avi"),
        str(tmp_path / "audio" / f"{sid}_audio.wav")]
    monkeypatch.delenv("RECORDINGS_DIR")
    recording = Recording(sid, "acc-1", (0, 1), 0, start)
    assert recording.audio_file == os.path.join(os.path.expanduser("~"), "Movies", "ADAM", "audio", f"{sid}_audio.wav")
    assert recording.keys == [f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi", f"audio/{sid}_audio.wav"]
    monkeypatch.setenv("RECORDINGS_DIR", "~/ADAM recordings")  # .env 裡可以用 ~
    assert Recording(sid, "acc-1", (0, 1), 0, start).video_files[0] == os.path.join(
        os.path.expanduser("~"), "ADAM recordings", "video", f"{sid}_cam0.avi")


def test_save_cut_off_by_quitting_is_offered_after_a_restart(widget, saver, uploads, restart):
    widget.start_recording()
    recording = widget.current
    sid = recording.service_id
    uploads.hold(f"video/{sid}_cam1.avi")
    wait_until(lambda: recording.frames_written >= 3 and recording.audio_chunks >= 3)
    widget.stop_recording()
    wait_until(lambda: saver.job(sid).file_number == 2)

    services, saver = restart()  # Quit Anyway：aboutToQuit 的 tasks.shutdown() 取消上傳
    uploads.release()
    [job] = saver.restore("acc-1")
    assert only_toast(services.notifier)["detail"] == "The upload didn't finish. The recording is still on this computer."
    assert saver.retry(sid) is True
    wait_until(lambda: job.state == SAVED)
    assert pending() == []


def test_restart_drops_a_save_whose_files_are_gone(widget, saver, uploads, restart):
    uploads.errors = [S3_DOWN]
    recording = record(widget)
    sid = recording.service_id
    wait_for(saver, recording, FAILED)
    os.remove(recording.audio_file)  # 例如有人清掉了 ~/audio

    services, saver = restart()
    assert saver.restore("acc-1") == []  # 沒有可以 Retry 的
    toast = only_toast(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"], toast["timeoutMs"]) == (
        "error", f"{sid} can't be saved", "The recording is no longer on this computer. It may have been moved or deleted.",
        0)
    assert (toast["actionId"], toast["actionLabel"]) == ("", "")  # 重試也一樣：不給 Retry
    assert pending() == []  # 紀錄刪掉了，下次登入不會再說一次
    job = saver.job(sid)  # 錄影頁的 Saving 卡片留一列說明（T29），不能重試
    assert (job.state, job.retryable, job.in_flight) == (FAILED, False, False) and saver.in_flight() == []
    assert saver.retry(sid) is False
    assert saver.restore("acc-1") == [] and len(toasts(services.notifier)) == 1  # 再登入一次也不會再說


def test_only_this_accounts_saves_are_offered_and_only_once(restart):
    start = datetime(2026, 10, 30, 12, 0, 0)
    mine = PendingSave("S20261030120000", "acc-1", ("/gone/video/a.avi", "/gone/video/b.avi", "/gone/audio/c.wav"),
                       start, start + timedelta(minutes=5), "database",
                       ("s3://adam-test/video/a.avi", "s3://adam-test/video/b.avi", "s3://adam-test/audio/c.wav"))
    theirs = replace(mine, service_id="S20261030121500", account_id="acc-2")
    store = PendingSaveStore()
    store.put(mine)
    store.put(theirs)

    services, saver = restart()
    assert [job.service_id for job in saver.restore("acc-1")] == ["S20261030120000"]
    assert saver.restore("acc-1") == []  # 已經放回來了：不會再跳一則
    assert [toast["title"] for toast in toasts(services.notifier)] == ["S20261030120000 wasn't saved"]
    assert pending("acc-2") == [theirs]  # 別人的留著，等那個人登入


def test_database_write_that_already_happened_counts_as_saved(widget, saver, services, uploads, database):
    """上次 COMMIT 成功了，但結果回來之前 app 就結束了：Retry 會撞到主鍵（1062）。是這個帳號自己的，就算存好了。"""
    database.fail_on = "sp_insert_service"
    database.error = mysql.connector.errors.IntegrityError(msg="Duplicate entry for key 'tbl_service.PRIMARY'",
                                                           errno=1062, sqlstate="23000")
    database.rows = [{"account_id": "acc-1"}]
    recording = record(widget)
    sid = recording.service_id
    wait_for(saver, recording, SAVED)
    assert database.calls == [("START TRANSACTION", ()),
                              (record_widget.INSERT_SERVICE_SQL, (sid, "acc-1", recording.start_time,
                                                                  recording.end_time, 1)),
                              ("ROLLBACK", ()), (record_widget.SERVICE_OWNER_SQL, (sid,))]  # 不再寫一次
    assert only_toast(services.notifier)["title"] == f"Saved {sid}"
    assert pending() == []


@pytest.mark.parametrize("rows", [[{"account_id": "acc-2"}], []], ids=["another-account", "gone"])
def test_service_id_taken_by_someone_else_still_fails(widget, saver, services, uploads, database, rows):
    database.fail_on = "sp_insert_service"
    database.error = mysql.connector.errors.IntegrityError(msg="Duplicate entry", errno=1062, sqlstate="23000")
    database.rows = rows  # 別台電腦在同一秒開始錄影（db_schema.sql 的 tbl_service 說明）
    recording = record(widget)
    wait_for(saver, recording, FAILED)
    assert only_toast(services.notifier)["detail"] == (
        "The files uploaded, but the database couldn't be updated. Retry to finish saving.")
    assert [item.stage for item in pending()] == ["database"]  # Retry 一樣只寫資料庫


# ── 錄影頁的 Saving 清單（新版介面 UD9、T29）────────────────────────────────────

@pytest.fixture
def saves(saver):
    model = SavesModel()
    model.attach(saver)
    return model


def rows(model):
    """像 QML 的 delegate 一樣，用角色名稱（roleNames）讀出每一列。"""
    names = {bytes(name).decode(): role for role, name in model.roleNames().items()}
    return [{name: model.data(model.index(row, 0), role) for name, role in names.items()}
            for row in range(model.rowCount())]


def saves_row(model, service_id):
    return next(row for row in rows(model) if row["serviceId"] == service_id)


def test_saves_list_follows_a_save_in_place_from_finishing_to_saved(widget, saver, saves, uploads, services,
                                                                    monkeypatch):
    assert [bytes(name).decode() for name in saves.roleNames().values()] == list(SAVE_ROLES)
    assert saves.count == 0
    inserted = []
    saves.rowsInserted.connect(lambda parent, first, last: inserted.append((first, last)))
    gate = threading.Event()
    write_wav = Recording._write_wav

    def slow_write(self, frames):
        gate.wait(5)  # 錄音檔還沒寫完
        write_wav(self, frames)

    monkeypatch.setattr(Recording, "_write_wav", slow_write)
    try:
        recording = record(widget)
        sid = recording.service_id
        [row] = rows(saves)  # 停止之後馬上有一列（還在等檔案寫完）
        assert row == {"serviceId": sid, "status": "finishing", "attempt": 1, "fileNumber": 1, "fileCount": 3,
                       "bytesDone": 0.0, "bytesTotal": 0.0, "percent": 0, "reason": "", "canRetry": False,
                       "canAnalyze": False, "analyzeRequested": False, "savedAt": ""}
        uploads.hold(f"video/{sid}_cam1.avi")
    finally:
        gate.set()
    job = saver.job(sid)
    wait_until(lambda: job.file_number == 2 and job.done > job.sizes[0])
    [row] = rows(saves)
    assert (row["status"], row["fileNumber"], row["bytesDone"], row["bytesTotal"]) == ("uploading", 2, job.done,
                                                                                      job.total)
    assert row["percent"] == int(job.done / job.total * 100) and 0 < row["percent"] < 100

    hold = threading.Event()
    services.tasks.submit("db", "test:hold", lambda token, report: hold.wait(5))  # db lane 卡住：停在寫資料庫之前
    try:
        uploads.release()
        wait_until(lambda: job.state == SAVING)
        [row] = rows(saves)
        assert (row["status"], row["fileNumber"], row["percent"], row["bytesDone"]) == ("saving", 3, 100, job.total)
    finally:
        hold.set()
    wait_for(saver, recording, SAVED)
    [row] = rows(saves)
    assert (row["status"], row["percent"], row["canAnalyze"], row["canRetry"], row["reason"]) == (
        "saved", 100, True, False, "")
    assert row["savedAt"] == "12:00"  # 寫資料庫的時間（假時鐘：12:00:00 開始、:01 停止、:02 寫入）
    assert inserted == [(0, 0)] and saves.count == 1  # 同一列就地更新，沒有換掉重插


def test_saves_list_keeps_a_failed_save_after_its_toast_is_closed_and_retries_it(widget, saver, saves, uploads,
                                                                                services):
    uploads.errors = [S3_DOWN]
    recording = record(widget)
    sid = recording.service_id
    wait_for(saver, recording, FAILED)
    services.notifier.dismiss(only_toast(services.notifier)["toastId"])  # 通知關掉了：清單上還看得到（UD9）
    [row] = rows(saves)
    assert (row["status"], row["canRetry"], row["canAnalyze"]) == ("failed", True, False)
    assert row["reason"] == "Can't reach cloud storage. The recording is still on this computer."  # 和通知同一句

    uploads.hold(f"video/{sid}_cam0.avi")
    assert saves.retry(sid) is True  # 和通知上的 Retry（retry-upload:<sid>）走同一條路
    [row] = rows(saves)
    assert (row["status"], row["attempt"], row["reason"], row["canRetry"]) == ("uploading", 2, "", False)
    assert only_toast(services.notifier)["title"] == f"Uploading {sid} (attempt 2)"  # 進度一樣在通知上
    assert saves.retry(sid) is False  # 已經在重試了
    uploads.release()
    wait_for(saver, recording, SAVED)
    assert rows(saves)[0]["status"] == "saved"
    assert saves.retry("S19990101000000") is False and saves.analyze("S19990101000000") is False  # 不認得的服務


def test_saves_list_analyze_is_the_toasts_analyze(widget, saver, saves, services):
    actions = []
    services.notifier.actionTriggered.connect(lambda action, toast_id: actions.append(action))
    recording = record(widget)
    sid = recording.service_id
    assert saves.analyze(sid) is False  # 還沒存好
    wait_for(saver, recording, SAVED)
    assert saves.analyze(sid) is True
    assert actions == [f"analyze:{sid}"]  # 送出分析的流程（ServerHome._on_action）收的就是這個
    assert toasts(services.notifier) == []  # 那則「Saved」也關掉了，和按通知上的 Analyze 一樣
    [row] = rows(saves)
    assert (row["status"], row["canAnalyze"], row["analyzeRequested"]) == ("saved", False, True)
    assert not widget.status_bar.button.isEnabled()  # 錄影視窗的 Analyze 跟著停用
    assert saves.analyze(sid) is False and actions == [f"analyze:{sid}"]  # 不會送兩次


def test_analysis_sent_from_anywhere_turns_off_analyze(widget, saver, saves, services):
    """通知上的 Analyze、分析頁的 Analyze 都由送出分析的流程（ServiceModel）在 io lane 跑 analyze:<sid>
    （CONTRACTS §4）：Saving 清單和錄影視窗的 Analyze 跟著停用，不會再送一次。"""
    first = record(widget)
    wait_for(saver, first, SAVED)
    second = record(widget)
    wait_for(saver, second, SAVED)
    assert [row["serviceId"] for row in rows(saves)] == [second.service_id, first.service_id]  # 新的在上面
    hold = threading.Event()
    try:
        services.tasks.submit("db", f"analysis:sent:{second.service_id}", lambda token, report: hold.wait(5))
        assert saves_row(saves, second.service_id)["canAnalyze"] is True  # 別的 key：不算
        services.tasks.submit("io", f"analyze:{second.service_id}", lambda token, report: hold.wait(5))
        row = saves_row(saves, second.service_id)
        assert (row["canAnalyze"], row["analyzeRequested"]) == (False, True)
        assert saves_row(saves, first.service_id)["canAnalyze"] is True  # 別的服務不受影響
        assert not widget.status_bar.button.isEnabled()  # 錄影視窗跟著最後一段
        assert saver.request_analyze(second.service_id) is False
    finally:
        hold.set()
    wait_until(lambda: not services.tasks.isBusy(f"analyze:{second.service_id}"))
    assert saves_row(saves, second.service_id)["analyzeRequested"] is True  # 送不出去時由通知的 Retry、分析頁重送


def test_saves_list_shows_last_sessions_unfinished_saves(widget, saver, uploads, database, restart):
    uploads.errors = [S3_DOWN]
    upload_failed = record(widget)
    wait_for(saver, upload_failed, FAILED)
    database.fail_on = "sp_insert_audio_storage"
    database_failed = record(widget)
    wait_for(saver, database_failed, FAILED)
    uploads.errors = [S3_DOWN]
    gone = record(widget)
    wait_for(saver, gone, FAILED)
    os.remove(gone.video_files[0])  # 例如有人清掉了 ~/video
    database.fail_on = None

    services, saver = restart()  # 結束 app，同一個帳號再登入
    saves = SavesModel()
    saves.attach(saver)  # DeviceFinder 也是先建好 RecordingSaver、接上清單，再 restore()
    saver.restore("acc-1")
    assert [row["serviceId"] for row in rows(saves)] == [gone.service_id, database_failed.service_id,
                                                        upload_failed.service_id]
    gone_row, database_row, upload_row = rows(saves)
    assert (gone_row["status"], gone_row["canRetry"], gone_row["reason"]) == (
        "failed", False, "The recording is no longer on this computer. It may have been moved or deleted.")
    assert (database_row["canRetry"], database_row["fileNumber"], database_row["percent"]) == (True, 3, 100)
    assert database_row["reason"] == ("The files uploaded, but the database couldn't be updated. "
                                      "Retry to finish saving.")
    assert (upload_row["canRetry"], upload_row["fileNumber"], upload_row["percent"], upload_row["bytesTotal"]) == (
        True, 1, 0, 0.0)
    assert upload_row["reason"] == "The upload didn't finish. The recording is still on this computer."
    later = SavesModel()
    later.attach(saver)  # 之後才接上的清單也一樣
    assert rows(later) == rows(saves)

    assert saves.retry(gone.service_id) is False
    assert saves.retry(database_failed.service_id) is True and saves.retry(upload_failed.service_id) is True
    wait_until(lambda: [row["status"] for row in rows(saves)] == ["failed", "saved", "saved"])
    assert [row["attempt"] for row in rows(saves)] == [1, 2, 2]
    assert pending() == []


def test_saves_list_keeps_only_the_newest_saved_rows():
    saved = [save_job(f"S2026103012{minute:02d}00", SAVED) for minute in range(SAVED_ROWS_KEPT + 2)]
    failed = save_job("S20261030110000", FAILED, error_detail="Can't reach cloud storage.")
    uploading = save_job("S20261030130000", UPLOADING)
    saver = FakeSaver([failed, *saved, uploading])
    saves = SavesModel()
    assert saves.retry(failed.service_id) is False and saves.analyze(saved[0].service_id) is False  # 還沒接上
    saves.attach(saver)
    newest_saved = [job.service_id for job in reversed(saved)][:SAVED_ROWS_KEPT]
    assert [row["serviceId"] for row in rows(saves)] == [uploading.service_id, *newest_saved, failed.service_id]

    removed = []
    saves.rowsRemoved.connect(lambda parent, first, last: removed.append((first, last)))
    uploading.state = SAVED  # 最新的一段也存好了：最舊的那一筆已存好的拿掉，沒存好的留著
    saver.changed.emit(uploading.service_id)
    assert [row["serviceId"] for row in rows(saves)] == [uploading.service_id, *newest_saved[:-1],
                                                        failed.service_id]
    assert removed == [(SAVED_ROWS_KEPT, SAVED_ROWS_KEPT)]

    assert saves.retry(failed.service_id) is True and saves.analyze(uploading.service_id) is True
    assert saver.calls == [("retry", failed.service_id), ("analyze", uploading.service_id)]


def test_save_row_numbers_match_the_design():
    job = save_job("S20261101142000", UPLOADING, done=58.6e6, total=124.0e6, sizes=[60e6, 60e6, 4e6])
    row = job.row()
    assert (row["fileNumber"], row["percent"]) == (1, 47)  # 「58.6 / 124.0 MB · 47%」（無條件捨去）
    assert transfer_text(row["bytesDone"], row["bytesTotal"]) == "58.6 / 124.0 MB · 47%"
    job.done = 61e6
    assert job.row()["fileNumber"] == 2
    job = save_job("S20261101140500", SAVED, done=124.0e6, total=124.0e6, urls=["s3://a", "s3://b", "s3://c"],
                   saved_at=datetime(2026, 11, 1, 14, 12, 59))
    assert (job.row()["savedAt"], job.row()["percent"], job.row()["fileNumber"]) == ("14:12", 100, 3)
    job.state = FAILED
    assert job.row()["savedAt"] == ""  # 只有存好的有時間


# ── 裝置的問題（UX-11）────────────────────────────────────────────────────────

def test_camera_that_does_not_open_is_explained_in_its_preview(open_window, cameras, monkeypatch):
    cameras.connected = (0,)  # 員工鏡頭（1 號）沒接
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: pytest.fail("no modal dialogs"))
    widget = open_window()
    customer, staff = widget.previews
    assert staff.problem == "Camera 1 has no picture"
    assert staff.problem_body.text() == "Check the cable, or go back and choose another camera."
    assert staff.accessibleDescription() == "Camera 1 has no picture. Check the cable, or go back and choose another camera."
    assert customer.problem == ""
    assert [timer.isActive() for timer in widget.timer_cameras] == [True, False]


def test_camera_without_frames_is_explained_until_frames_come_back(widget, monkeypatch):
    monkeypatch.setattr(record_widget, "NO_PICTURE_AFTER_S", 0.05)
    preview = widget.previews[0]
    wait_until(lambda: preview.problem == "Camera 0 has no picture")  # 假鏡頭給完 3 張就沒有畫面了
    widget.caps[0].frames_left = 1000  # 例如重新接好
    wait_until(lambda: preview.problem == "")
    assert preview.image.pixmap().size().toTuple() == preview.frame_size()  # 縮成預覽框的大小（4:3）


def test_camera_that_stops_while_recording_is_explained(widget, cameras, monkeypatch):
    monkeypatch.setattr(record_widget, "NO_PICTURE_AFTER_S", 0.05)
    for capture in widget.caps:
        capture.frames_left = 10 ** 9  # 預覽一直有畫面
    widget.start_recording()
    wait_until(lambda: widget.previews[0].problem == "Camera 0 has no picture")  # 錄影的那一邊沒有畫面了
    assert widget.current.camera_problem(0, widget.current.last_frame_at[0] + 1)
    widget.stop_recording()


def test_microphone_failure_is_shown_and_the_video_is_still_saved(widget, saver, microphone, monkeypatch):
    microphone.broken = True
    monkeypatch.setattr(record_widget, "NO_PICTURE_AFTER_S", 0.05)
    widget.start_recording()
    recording = widget.current
    row = widget.microphone_row
    wait_until(lambda: row.problem)
    assert row.label.text() == ("MacBook 麥克風 isn't working. Check the cable, or go back and choose another "
                                "microphone.")
    wait_until(lambda: recording.frames_written >= 3)
    widget.stop_recording()
    wait_for(saver, recording, SAVED)  # 以前影片也一起丟掉（UX-11）
    assert isinstance(recording.audio_error, sounddevice.PortAudioError)
    with wave.open(recording.audio_file) as wav:
        assert wav.getnframes() == 0
    assert row.problem  # 停止之後還看得到，直到下一段開始
    widget.start_recording()
    assert not row.problem
    widget.stop_recording()


def test_stalled_microphone_is_shown(widget, monkeypatch):
    monkeypatch.setattr(record_widget, "NO_PICTURE_AFTER_S", 0.05)
    widget.start_recording()  # 假麥克風給完 3 段之後就沒有資料了
    wait_until(lambda: widget.microphone_row.problem)
    widget.stop_recording()


# ── 錄影中的狀態（UX-12）──────────────────────────────────────────────────────

def test_recording_shows_a_steady_dot_the_elapsed_time_and_the_level(widget, monkeypatch):
    monkeypatch.setattr(tests.fakes._FakeStream, "read", lambda self, frames: (b"\x00\x40" * frames, False))  # 16384：-6 dBFS
    assert widget.poll_timer.interval() == 250  # 計時、音量每秒最多更新 4 次
    widget.start_recording()
    bar = widget.status_bar
    assert status(widget) == ("Recording", "")  # 紅點、Recording、經過時間（設計稿沒有服務 ID）
    assert bar.elapsed.isVisibleTo(widget) and bar.elapsed.text() == "00:00:00"
    assert widget.announced[-1] == (f"Recording {widget.service_id}", False)  # 服務 ID 在唸的內容裡
    dot = bar.icon.pixmap().cacheKey()
    level = widget.microphone_row.level
    wait_until(lambda: level.value() > 80)
    assert level.isVisibleTo(widget) and level.accessibleName() == "Microphone level"
    assert bar.icon.pixmap().cacheKey() == dot  # 紅點不閃：同一張圖，沒有換過
    assert not widget.button_start_recording.isEnabled() and widget.button_stop_recording.isEnabled()
    widget.current._started_at -= 3725  # 已經錄了 1 小時 2 分 5 秒
    wait_until(lambda: bar.elapsed.text() == "01:02:05")
    widget.stop_recording()
    assert not level.isVisibleTo(widget) and not widget.poll_timer.isActive()


def test_recorder_window_follows_the_design_layout(widget, monkeypatch):
    """設計稿 StaffRecorder：兩個 4:3 的預覽並排；開始、停止在同一個位置；麥克風的音量只在錄影中出現。"""
    widget.show()
    QCoreApplication.processEvents()
    assert widget.size().toTuple() == (1280, 820)
    customer, staff = widget.previews
    assert customer.width() / customer.height() == pytest.approx(4 / 3, abs=0.01)
    assert (customer.y(), customer.size()) == (staff.y(), staff.size()) and customer.x() < staff.x()
    assert customer.width() == pytest.approx(608, abs=2)  # (1280 - 2×24 - 16) / 2
    assert [preview.accessibleName() for preview in widget.previews] == [
        "Live preview from the customer camera", "Live preview from the staff camera"]
    assert widget.label_customer.text() == "Customer camera" and widget.label_staff.text() == "Staff camera"

    start, stop = widget.button_start_recording, widget.button_stop_recording
    assert start.isVisibleTo(widget) and not stop.isVisibleTo(widget)  # 沒在錄：淺色的 Start Recording
    assert not widget.microphone_row.isVisibleTo(widget)
    assert widget.save_hint.text() == ("When you stop, the recording saves in the background. "
                                       "You can close this window.")
    start.setFocus()
    widget.start_recording()
    assert stop.isVisibleTo(widget) and not start.isVisibleTo(widget)  # 錄影中：同一個位置換成紅色的 Stop
    assert stop.hasFocus()  # 用鍵盤的人可以直接再按一次
    assert widget.microphone_row.isVisibleTo(widget) and widget.microphone_row.label.text() == "Microphone level"
    widget.stop_recording()
    assert start.isVisibleTo(widget) and start.hasFocus() and not widget.microphone_row.isVisibleTo(widget)

    widget.resize(960, 640)  # 視窗小的時候預覽照高度縮小，一樣是 4:3
    QCoreApplication.processEvents()
    assert customer.width() / customer.height() == pytest.approx(4 / 3, abs=0.01)
    assert customer.geometry().bottom() < widget.bottom_bar.geometry().top()


def test_focus_ring_only_for_keyboard_focus_and_it_follows_start_and_stop(widget):
    """DS-30：滑鼠點的、視窗打開時的焦點不畫焦點框；Tab 移過去的才畫，按下 Start 之後框移到 Stop。"""
    widget.show()
    widget.activateWindow()
    wait_until(widget.isActiveWindow)
    start, stop = widget.button_start_recording, widget.button_stop_recording
    start.setFocus(Qt.FocusReason.MouseFocusReason)
    assert start.hasFocus() and not start.keyboard_focus
    start.clearFocus()
    start.setFocus(Qt.FocusReason.TabFocusReason)  # 用 Tab 移過來
    assert start.keyboard_focus
    assert 'QPushButton[keyboardFocus="true"]' in start.styleSheet()  # 框只畫在這個屬性上
    widget.start_recording()
    assert stop.hasFocus() and stop.keyboard_focus and not start.keyboard_focus
    widget.stop_recording()
    assert start.hasFocus() and start.keyboard_focus


def test_preview_frames_fit_the_box_at_4_to_3():
    preview = record_widget.CameraPreview("Camera 0")
    assert preview.frame_size() == (640, 480)  # 還沒排版：錄影的大小
    preview.image.resize(800, 300)  # 比 4:3 寬：照高度
    assert preview.frame_size() == (400, 300)
    preview.image.resize(300, 600)  # 比 4:3 高：照寬度
    assert preview.frame_size() == (300, 225)


def test_level_meter_lights_bars_from_the_left():
    meter = record_widget.LevelMeter()
    assert meter.size().toTuple() == (93, 24) and meter.lit() == 0  # 12 格、每格 5 px、間隔 3 px
    meter.setValue(58)
    assert meter.lit() == 7  # 設計稿：12 格亮 7 格
    meter.setValue(100)
    assert meter.lit() == 12


def test_each_recording_has_its_own_service_id_and_files(widget, saver, uploads):
    first, second = record(widget), record(widget)
    assert first.service_id != second.service_id
    started = datetime.strptime(second.service_id, "S%Y%m%d%H%M%S")
    assert started == second.start_time == widget.start_time  # 按下 Start 時產生一次
    assert first.end_time - first.start_time == timedelta(seconds=1)  # 假時鐘：每呼叫一次走 1 秒
    wait_for(saver, first, SAVED)
    wait_for(saver, second, SAVED)
    assert sorted(uploads.keys) == sorted(first.keys + second.keys)


def test_recording_that_starts_in_the_same_second_is_not_started(widget, saver, monkeypatch):
    recording = record(widget)
    monkeypatch.setattr(record_widget, "datetime", SimpleNamespace(now=lambda: recording.start_time))
    widget.start_recording()  # 同一秒：service_id 會重複
    assert widget.current is None and widget.button_start_recording.isEnabled()
    wait_for(saver, recording, SAVED)


def test_microphone_overflow_does_not_stop_recording(widget, saver, microphone):
    microphone.overflow_at = 2  # 第 2 段資料讀取前，麥克風的緩衝區滿了
    recording = record(widget)
    assert recording.wait(5)
    with wave.open(recording.audio_file) as wav:
        assert wav.getnframes() == 3 * 1024  # 3 段都在


def test_frames_are_resized_to_640x480(widget, saver):
    recording = record(widget)  # 假鏡頭給的是 1280x720
    assert recording.wait(5)
    for path in recording.video_files:
        video = REAL_VIDEO_CAPTURE(path)
        shapes = []
        ok, frame = video.read()
        while ok:
            shapes.append(frame.shape)
            ok, frame = video.read()
        video.release()
        assert shapes == [(480, 640, 3)] * 3  # 尺寸不符的影格會被 OpenCV 直接丟掉


@pytest.mark.parametrize("broken", ["wave.open", "cv2.VideoWriter"])
def test_write_error_fails_the_save_with_its_reason(widget, saver, services, uploads, monkeypatch, broken):
    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    module, name = broken.split(".")
    monkeypatch.setattr(getattr(record_widget, module), name, disk_full)  # 錄音檔或影片寫不進去
    widget.start_recording()
    recording = widget.current
    # 影片一開始就寫不進去；錄音檔要停止之後才寫
    wait_until(lambda: recording.write_error is not None or recording.frames_written >= 3)
    widget.stop_recording()
    wait_for(saver, recording, FAILED)
    assert uploads.keys == []  # 檔案不完整：不上傳
    assert only_toast(services.notifier)["detail"] == "Not enough disk space. Free up space on this computer, then try again."
    assert pending() == []  # 重開 app 之後也一樣不上傳不完整的檔案（T26）


def test_recording_threads_that_do_not_finish_fail_the_save(widget, saver, services, monkeypatch):
    gate = threading.Event()
    write_wav = Recording._write_wav
    monkeypatch.setattr(Recording, "_write_wav", lambda self, *args: gate.wait(5) and write_wav(self, *args))
    monkeypatch.setattr(record_widget, "FINISH_TIMEOUT_S", 0.05)
    try:
        recording = record(widget)
        wait_for(saver, recording, FAILED)  # 例如鏡頭的驅動卡住：不會一直停在「Saving recording…」
        assert only_toast(services.notifier)["detail"] == "Something went wrong. The recording is still on this computer."
    finally:
        gate.set()
    recording.wait(5)


def test_saves_started_while_the_app_quits_are_not_left_in_flight(widget, saver, services):
    widget.start_recording()
    widget.start_recording()  # 錄影中再按一次：不做事
    recording = widget.current
    services.tasks.shutdown()  # 結束程式（aboutToQuit）之後才停止
    widget.stop_recording()
    widget.stop_recording()  # 已經停了：不做事
    job = saver.job(recording.service_id)
    assert job.state == FAILED and not job.in_flight and saver.in_flight() == []
    assert job.error_detail == "The app is closing. The recording is still on this computer."
    assert [(item.service_id, item.stage) for item in pending()] == [(recording.service_id, "upload")]  # 下次登入再問
    recording.wait(5)
    saver._finish_files()


# ── 關掉視窗、結束程式（UX-09）───────────────────────────────────────────────

def test_closing_the_window_while_recording_stops_and_saves(open_window, saver):
    widget = open_window()
    closed = []
    widget.closed.connect(lambda: closed.append(True))
    widget.start_recording()
    recording = widget.current
    wait_until(lambda: recording.frames_written >= 3)
    widget.close()
    assert recording.stopped and widget.current is None and closed == [True]
    assert not any(timer.isActive() for timer in widget.timer_cameras)  # 鏡頭的預覽也關了
    wait_for(saver, recording, SAVED)  # 視窗關了，存檔照樣做完


def test_quitting_while_recording_stops_and_finishes_the_files(widget, saver):
    widget.start_recording()
    recording = widget.current
    widget._on_about_to_quit()  # QCoreApplication.aboutToQuit
    assert recording.stopped and recording.finished  # 檔案寫完了才讓程式結束
    saver._finish_files()


# ── QuitGuard（⌘Q）────────────────────────────────────────────────────────────

class QuitOwner(QObject):
    """登記要擋 ⌘Q 的物件（實際上是錄影中、存檔中的 DeviceFinder）。"""

    def __init__(self):
        super().__init__()
        self.blocked = 0

    def quit_blocked(self):
        self.blocked += 1


def test_quit_guard_blocks_quit_events_while_someone_asks():
    assert QuitGuard.instance() is QuitGuard.instance()  # app 用的只有一個，不屬於任何頁面物件
    guard = QuitGuard()  # 這個測試自己的一個：不受其他測試還沒收掉的登記影響
    assert guard.eventFilter(None, QEvent(QEvent.Type.Timer)) is False  # 其他事件不管
    owner = QuitOwner()
    guard.set_blocking(owner, True)
    guard.set_blocking(owner, True)  # 重複登記沒關係
    try:
        assert guard.installed and guard.blocking(owner)
        quit_event = QEvent(QEvent.Type.Quit)
        QCoreApplication.sendEvent(QCoreApplication.instance(), quit_event)  # ⌘Q 送給 app 的就是這個
        assert not quit_event.isAccepted()  # macOS 因此取消結束（applicationShouldTerminate:）
        assert owner.blocked == 0
        wait_until(lambda: owner.blocked == 1)  # 下一輪事件才問使用者
    finally:
        guard.set_blocking(owner, False)
    assert not guard.blocking(owner) and not guard.installed  # 沒有人要擋：拿掉 filter


def test_quit_guard_forgets_owners_that_are_deleted():
    """登記的物件被 gc 收掉：不再擋，filter 本身還好好的（以前 filter 跟著頁面物件被收掉，Qt 呼叫它時 segfault）。"""
    guard = QuitGuard()
    owner = QuitOwner()
    guard.set_blocking(owner, True)
    del owner
    gc.collect()
    for _ in range(3):
        QCoreApplication.processEvents()  # 其他事件照常經過 filter
    assert guard.eventFilter(None, QEvent(QEvent.Type.Quit)) is False  # 不擋了，順便拿掉 filter
    assert not guard.installed


# ── 小工具 ───────────────────────────────────────────────────────────────────

def test_transfer_text_and_file_number():
    assert transfer_text(58.6e6, 124.0e6) == "58.6 / 124.0 MB · 47%"
    assert transfer_text(124.0e6, 124.0e6) == "124.0 / 124.0 MB · 100%"
    assert transfer_text(5, 0) == ""  # 還不知道總量
    assert transfer_text(-1, 10) == "0.0 / 0.0 MB · 0%"
    assert [file_number(done, [10, 20, 5]) for done in (0, 9, 10, 29, 30, 35)] == [1, 1, 2, 2, 3, 3]
    assert file_number(0, []) == 1


def test_elapsed_time_and_level():
    assert [format_elapsed(s) for s in (0, 59.9, 61, 3725, -3)] == ["00:00:00", "00:00:59", "00:01:01", "01:02:05",
                                                                    "00:00:00"]
    assert level_from_peak(0) == 0 and level_from_peak(32768) == 1 and level_from_peak(40000) == 1
    assert level_from_peak(32) == 0  # -60 dBFS（32768 的千分之一）以下
    assert 0.89 < level_from_peak(16384) < 0.91  # -6 dBFS


def test_failure_detail_says_what_happened_and_where_the_recording_is():
    access = botocore.exceptions.ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
    assert failure_detail(S3_DOWN, "upload") == "Can't reach cloud storage. The recording is still on this computer."
    assert failure_detail(access, "upload") == "Can't access cloud storage. The recording is still on this computer."
    assert failure_detail(RuntimeError("bug"), "upload") == "Something went wrong. The recording is still on this computer."
    assert failure_detail(PermissionError(13, "denied"), "upload") == (
        "Can't access a file on this computer. ADAM doesn't have permission to open or save it.")
    assert failure_detail(S3_DOWN, "database") == (
        "The files uploaded, but the database couldn't be updated. Retry to finish saving.")


def test_reduce_motion_follows_the_preferences():
    prefs = SimpleNamespace(animationScale=1.0, motionMode=MOTION_REDUCE)
    assert reduce_motion(prefs)
    prefs.motionMode = MOTION_FULL
    assert not reduce_motion(prefs)
    prefs.animationScale = 0  # 測試：不動
    assert reduce_motion(prefs)
    assert reduce_motion(None) in (True, False)  # 跟隨系統
