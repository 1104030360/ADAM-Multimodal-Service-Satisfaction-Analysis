"""錄影存檔的整段流程（前端重建 F3，CONTRACTS §4 的 C1）：服務人員端的外殼、錄影頁的 Activate、錄影視窗、
moto 的 S3、測試用的 MySQL。鏡頭和麥克風是假的。

- 停止之後在背景上傳（io lane，upload:<sid>）、寫資料庫（db lane，record:save:<sid>）；GUI 執行緒 0 個往返
- 主視窗的通知用同一個 key 就地更新：進度 → Saved（Analyze）或 wasn't saved（Retry）（DS-21、DS-52）
- 失敗時檔案留著，Retry 之後存好（UX-10）
- 上傳中按 Exit、關掉主視窗、⌘Q 都先問（UX-29、DS-28）；錄影中關掉錄影視窗會停止並存檔（UX-09）
"""
import os
import threading

import botocore.exceptions
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from myapp import storage
from myapp.init import record_widget, server_home_init
from myapp.init.record_widget import FAILED, SAVED, SAVING, UPLOADING, QuitGuard
from myapp.init.server_home_init import ANALYSIS_KEY, LAUNCH_KEYS
from tests.fakes import ticking_clock
from tests.integration.helpers import STAFF_ID, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

S3_DOWN = botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com/adam-test")


class UploadGate:
    """包住真的 storage.upload_with_progress（moto）：記下每次在哪條執行緒呼叫；可以在某個檔案開始上傳前停住，
    或讓下一次上傳失敗。停著時持續回報 0：Throttle 只在被呼叫時送出，已經累積的進度才看得到。"""

    def __init__(self, monkeypatch):
        self.real = storage.upload_with_progress
        self.calls = []
        self.errors = []
        self.closed = False
        self._held = {}
        monkeypatch.setattr(storage, "upload_with_progress", self)

    def hold(self, key):
        self._held[key] = threading.Event()

    def release(self):
        for gate in self._held.values():
            gate.set()

    def close(self):
        """測試結束：還停著的上傳直接失敗，不要在 moto 收掉之後才真的送出去。"""
        self.closed = True
        self.release()

    def __call__(self, local_path, key, token=None, report=None):
        self.calls.append((key, threading.current_thread()))
        gate = self._held.get(key)
        while gate is not None and not gate.wait(0.02):
            token.raise_if_cancelled()
            report(0, os.path.getsize(local_path))
        token.raise_if_cancelled()
        if self.closed:
            raise RuntimeError("the test is over")
        if self.errors:
            raise self.errors.pop(0)
        return self.real(local_path, key, token, report)

    @property
    def on_gui_thread(self):
        return [key for key, thread in self.calls if thread is threading.main_thread()]


@pytest.fixture
def gate(monkeypatch):
    gate = UploadGate(monkeypatch)
    yield gate
    gate.close()  # 不留下等著的背景執行緒


@pytest.fixture
def staff(engine, services, s3, gate, monkeypatch):
    """登入後的外殼，打開錄影頁、按 Activate（預設是顧客鏡頭 0、員工鏡頭 1、第一支麥克風）。"""
    monkeypatch.setattr(record_widget, "datetime", ticking_clock())
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    qml(window, "mouseArea2.clicked(null)")  # 側邊欄的 Record
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready")
               and qml(window, "deviceFinder.status") == "ready")
    activate = window.findChild(QObject, "activateButton")
    qml(activate, "clicked()")
    finder = home.deviceFinder
    assert finder.recorder is not None and finder.recorder.isVisible()
    yield home, window, finder
    if finder.recorder is not None:
        finder.recorder.close()  # 錄影中的會先停止；預覽的計時器也停掉


def record(recorder, started=None):
    """按 Start Recording，等假鏡頭、假麥克風給完資料，按 Stop Recording。started(sid) 在開始之後、停止之前呼叫。"""
    recorder.button_start_recording.click()
    recording = recorder.current
    if started is not None:
        started(recording.service_id)
    try:
        wait_until(lambda: recording.frames_written >= 3 and recording.audio_chunks >= 3)
    finally:
        recorder.button_stop_recording.click()
    return recording


def job_of(finder, recording):
    return finder.saver.job(recording.service_id)


def shown_toasts(window):
    """ToastHost 畫出來的每一則：標題、內文（含進度的數字）、動作按鈕的字。"""
    host = window.findChild(QObject, "toastHost")
    toast_list = host.findChild(QObject, "toastList")
    items = [item for item in toast_list.property("contentItem").childItems() if item.property("toastId")]
    return [{name: item.property(name) for name in ("toastId", "title", "bodyText", "actionLabel")} for item in items]


def quit_dialog(window):
    return window.findChild(QObject, "quitDialog")


def track_quit(engine):
    """Qt.quit() 會發 engine 的 quit（測試裡 app 沒有在 exec，不會真的結束）。"""
    quits = []
    engine.quit.connect(lambda: quits.append(True))
    return quits


# ── 存檔成功：進度就地更新、Saved 加上 Analyze、分析頁重新整理 ─────────────────

def test_upload_progress_updates_one_toast_in_place_then_saves_with_analyze(staff, services, db, s3, gate,
                                                                          no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    recording = record(recorder, started=lambda sid: gate.hold(f"video/{sid}_cam1.avi"))
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == UPLOADING and job.done == job.sizes[0])  # 第 1 個檔案傳完、停在第 2 個
    [toast] = toasts(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"]) == ("progress", f"Uploading {sid}", "File 2 of 3")
    wait_until(lambda: shown_toasts(window) and "File 2 of 3" in shown_toasts(window)[0]["bodyText"])
    [shown] = shown_toasts(window)
    assert shown["bodyText"] == f"{record_widget.transfer_text(job.done, job.total)} · File 2 of 3"
    assert recorder.status_bar.title.text() == f"Uploading {sid}"
    assert recorder.status_bar.detail.text().endswith(f"File 2 of 3: {sid}_cam1.avi")
    assert recorder.button_start_recording.isEnabled()  # 上傳時可以錄下一段（DS-34）
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]

    gate.release()
    wait_until(lambda: job.state == SAVED)
    [result] = toasts(services.notifier)
    assert result["toastId"] == toast["toastId"] and result["count"] == 1  # 同一則變成結果，不疊兩則
    assert (result["severity"], result["title"], result["detail"]) == ("success", f"Saved {sid}",
                                                                       "Video and audio uploaded.")
    assert (result["actionId"], result["actionLabel"]) == (f"analyze:{sid}", "Analyze")
    wait_until(lambda: shown_toasts(window) and shown_toasts(window)[0]["actionLabel"] == "Analyze")
    assert (recorder.status_bar.title.text(), recorder.status_bar.detail.text()) == (f"Saved {sid}",
                                                                                     "3 files uploaded")
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [(1,)]
    assert sorted(obj["Key"] for obj in s3.list_objects_v2(Bucket="adam-test")["Contents"]) == sorted(recording.keys)
    # 存好之後重新整理分析頁（CONTRACTS §4：home.serviceModel.refresh()）：Not Analyzed 多了這一筆
    model = home.serviceModel.notAnalyzedModel
    wait_until(lambda: [row["serviceId"] for row in model.rows()] == [sid])
    assert gate.calls and gate.on_gui_thread == []  # 三個檔案都在 io lane 上傳

    actions = []  # 狀態列的 Analyze 和通知上的一樣，交給送出分析的流程（C3）
    services.notifier.actionTriggered.connect(lambda action, toast_id: actions.append(action))
    recorder.status_bar.button.click()
    assert actions == [f"analyze:{sid}"] and toasts(services.notifier) == []


def test_gui_thread_makes_no_database_round_trips(db_proxy, staff, services, db, gate, no_network_on_gui_thread):
    # db_proxy 要排在 staff 前面：launch() 的 db lane 才會經過 proxy 連線
    home, window, finder = staff
    recorder = finder.recorder
    db_proxy.reset()  # launch() 已經連上、建好 cursor（T16：往返預算不含連線）
    hold = threading.Event()
    services.tasks.submit("db", "test:hold", lambda token, report: hold.wait(10))  # db lane 卡住：存檔排在後面
    try:
        recording = record(recorder)
        job = job_of(finder, recording)
        wait_until(lambda: job.state == SAVING)  # 上傳完了，寫資料庫排在 db lane
        assert db_proxy.snapshot()["rt"] == 0  # 開錄影視窗、錄影、停止、上傳：GUI 執行緒 0 個往返（以前 8＋20 個）
    finally:
        hold.set()
    wait_until(lambda: job.state == SAVED and not services.tasks.isBusy(ANALYSIS_KEY))
    # 寫資料庫 6 個（START TRANSACTION、4 個 CALL、COMMIT），加上分析頁重新整理 1 個
    assert db_proxy.snapshot()["rt"] == 7


# ── 失敗：檔案留著，Retry 之後存好（UX-10）──────────────────────────────────────

def test_failed_upload_keeps_the_files_and_retry_saves_it(staff, services, db, s3, gate, no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    gate.errors = [S3_DOWN]  # 第一個檔案就連不上雲端
    recording = record(recorder)
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == FAILED)
    assert all(os.path.getsize(path) > 0 for path in recording.files)  # 錄好的檔案都還在這台電腦上
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    [toast] = toasts(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"], toast["timeoutMs"]) == (
        "error", f"{sid} wasn't saved", "Can't reach cloud storage. The recording is still on this computer.", 0)
    assert (toast["actionId"], toast["actionLabel"]) == (f"retry-upload:{sid}", "Retry")
    assert recorder.status_bar.title.text() == f"{sid} wasn't saved"
    assert recorder.status_bar.button.text() == "Retry"
    wait_until(lambda: shown_toasts(window) and shown_toasts(window)[0]["actionLabel"] == "Retry")

    host = window.findChild(QObject, "toastHost")
    qml(host, f"trigger({toast['toastId']})")  # 按通知上的 Retry
    wait_until(lambda: job.state == SAVED)
    assert job.attempt == 2
    assert db.rows("SELECT COUNT(*) FROM tbl_video_storage WHERE service_id = %s", (sid,)) == [(2,)]
    [result] = toasts(services.notifier)  # 按通知的動作會先關掉那則（Notifier.trigger），之後的進度和結果是新的一則
    assert result["title"] == f"Saved {sid}"


def test_database_failure_is_retried_without_uploading_again(staff, services, db, gate, monkeypatch,
                                                            no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    port = os.environ["DB_PORT"]
    done = []
    services.tasks.submit("db", "test:conn", lambda token, report: services.db.close(), on_ok=done.append)
    wait_until(lambda: done)  # db lane 下次要重新連線……
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # ……而資料庫連不上
    recording = record(recorder)
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == FAILED)
    [toast] = toasts(services.notifier)
    assert toast["detail"] == "The files uploaded, but the database couldn't be updated. Retry to finish saving."
    uploads = len(gate.calls)
    assert uploads == 3

    monkeypatch.setenv("DB_PORT", port)  # 網路恢復
    recorder.status_bar.button.click()  # 錄影視窗的 Retry
    wait_until(lambda: job.state == SAVED)
    assert len(gate.calls) == uploads  # 只重寫資料庫
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [(1,)]


# ── 結束前先問（UX-29、DS-28）；錄影中關掉、結束會停止並存檔（UX-09）──────────

def test_quit_while_uploading_asks_first_from_exit_close_and_cmd_q(engine, staff, services, db, gate,
                                                                  no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    quits = track_quit(engine)
    recording = record(recorder, started=lambda sid: gate.hold(f"video/{sid}_cam1.avi"))
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == UPLOADING and job.done == job.sizes[0])
    dialog = quit_dialog(window)

    qml(window, "mouseArea4.clicked(null)")  # 側邊欄的 Exit
    wait_until(lambda: qml(dialog, "opened"))
    assert quits == [] and window.isVisible()
    assert dialog.property("title") == "Quit while uploading?"
    amount = f"{job.done / 1e6:.1f} / {job.total / 1e6:.1f} MB"
    assert dialog.property("body") == f"{sid} is still uploading ({amount}). If you quit now, this service won't be saved."
    cancel, confirm = dialog.findChild(QObject, "cancelButton"), dialog.findChild(QObject, "confirmButton")
    assert (cancel.property("text"), confirm.property("text")) == ("Keep Uploading", "Quit Anyway")
    wait_until(cancel.hasActiveFocus)  # 預設是安全的選項
    qml(cancel, "clicked()")
    wait_until(lambda: not qml(dialog, "visible"))
    assert quits == [] and job.state == UPLOADING

    window.close()  # 關掉主視窗（onClosing）：一樣先問，視窗留著
    wait_until(lambda: qml(dialog, "opened"))
    assert window.isVisible()
    qml(dialog, "reject()")  # Esc
    wait_until(lambda: not qml(dialog, "visible"))

    # ⌘Q：送給 app 的 QEvent.Quit。QML 的 onClosing 收不到（T20），QuitGuard 擋下來之後一樣問
    quit_event = QEvent(QEvent.Type.Quit)
    QCoreApplication.sendEvent(QCoreApplication.instance(), quit_event)
    assert not quit_event.isAccepted()  # macOS 因此取消結束
    wait_until(lambda: qml(dialog, "opened"))
    assert quits == []

    qml(confirm, "clicked()")  # Quit Anyway
    wait_until(lambda: quits == [True])
    assert not QuitGuard.instance().blocking(finder)  # 接下來的 Quit 不再攔
    # 結束時 main.py 的 tasks.shutdown() 取消上傳：這一筆沒有存進資料庫，檔案留在這台電腦上
    services.tasks.cancel(f"upload:{sid}")
    gate.release()
    wait_until(lambda: not services.tasks.isBusy(f"upload:{sid}"))
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    assert all(os.path.exists(path) for path in recording.files)


def test_quit_without_uploads_quits_at_once(engine, staff, services, db, no_network_on_gui_thread):
    home, window, finder = staff
    quits = track_quit(engine)
    assert not QuitGuard.instance().blocking(finder)  # 沒有在錄、沒有在存：⌘Q 照常結束
    qml(window, "mouseArea4.clicked(null)")
    assert quits == [True] and not qml(quit_dialog(window), "opened")


def test_quit_while_recording_stops_and_saves_then_asks(engine, staff, services, db, gate,
                                                       no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    quits = track_quit(engine)
    recorder.button_start_recording.click()
    recording = recorder.current
    gate.hold(f"video/{recording.service_id}_cam0.avi")
    wait_until(lambda: recording.frames_written >= 3 and recording.audio_chunks >= 3)
    assert QuitGuard.instance().blocking(finder)  # 錄影中：⌘Q 會被攔下來
    qml(window, "mouseArea4.clicked(null)")  # 錄影中按 Exit：先停止並存檔
    assert recording.stopped and recorder.current is None
    dialog = quit_dialog(window)
    assert qml(dialog, "visible") and quits == []
    sid = recording.service_id
    body = dialog.property("body")  # 檔案可能還在寫、還不知道大小：先不寫 MB
    assert body.startswith(f"{sid} is still uploading") and body.endswith(". If you quit now, this service won't be saved.")
    job = job_of(finder, recording)
    wait_until(lambda: job.total > 0)  # 檔案寫好了：對話框跟著寫出大小
    assert dialog.property("body") == (
        f"{sid} is still uploading (0.0 / {job.total / 1e6:.1f} MB). If you quit now, this service won't be saved.")
    gate.release()
    wait_until(lambda: job.state == SAVED)  # 選 Keep Uploading 之前就存好了：對話框自己關掉，不必再問
    wait_until(lambda: not qml(dialog, "visible"))
    assert quits == [] and not QuitGuard.instance().blocking(finder)
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (recording.service_id,)) == [(1,)]


def test_closing_the_recorder_while_recording_stops_and_saves(staff, services, db, no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    recorder.button_start_recording.click()
    recording = recorder.current
    wait_until(lambda: recording.frames_written >= 3 and recording.audio_chunks >= 3)
    recorder.close()  # 錄影中關掉錄影視窗
    assert recording.stopped and finder.recorder is None
    job = job_of(finder, recording)
    wait_until(lambda: job.state == SAVED)  # 視窗關了，存檔照樣做完；結果在主視窗的通知
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (recording.service_id,)) == [(1,)]
    assert toasts(services.notifier)[0]["title"] == f"Saved {recording.service_id}"


def test_activate_brings_the_recording_window_back_instead_of_opening_another(staff, no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    activate = window.findChild(QObject, "activateButton")
    assert activate.property("text") == "Activate"
    recorder.button_start_recording.click()
    assert activate.property("text") == "Show Recording Window"  # 錄影中：按下去是帶回錄影視窗
    qml(activate, "clicked()")
    assert finder.recorder is recorder and recorder.recording  # 沒有開第二個，錄影也沒停
    recorder.button_stop_recording.click()
    assert activate.property("text") == "Activate"
    wait_until(lambda: finder.uploadCount == 0)  # 存完才結束這個測試
