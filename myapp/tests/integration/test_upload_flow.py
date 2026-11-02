"""錄影存檔的整段流程（前端重建 F3，CONTRACTS §4 的 C1）：服務人員端的外殼、錄影頁的 Open Recorder、錄影視窗、
moto 的 S3、測試用的 MySQL。鏡頭和麥克風是假的。

- 停止之後在背景上傳（io lane，upload:<sid>）、寫資料庫（db lane，record:save:<sid>）；GUI 執行緒 0 個往返
- 主視窗的通知用同一個 key 就地更新：進度 → Saved（Analyze）或 wasn't saved（Retry）（DS-21、DS-52）
- 失敗時檔案留著，Retry 之後存好（UX-10）
- 上傳中按 Quit、關掉主視窗、⌘Q 都先問上傳的警告（UX-29、DS-28）；沒有在存時問「Quit ADAM?」（N1，
  Log Out 的整段在 test_log_out.py）；錄影中關掉錄影視窗會停止並存檔（UX-09）
- 沒存完的（失敗、或存到一半結束 app）重開之後同一個帳號登入還有 Retry；檔案不見了就說明存不了（T26）
- 錄影頁的 Saving 清單（deviceFinder.saves）：通知關掉之後還看得到，Retry、Analyze 和通知上的一樣（UD9、T29）
"""
import os
import threading
from datetime import datetime, timedelta

import botocore.exceptions
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtQml import QQmlApplicationEngine

from myapp import storage
from myapp.db_lane import DbLane
from myapp.init import record_widget, server_home_init
from myapp.init.record_widget import FAILED, SAVED, SAVING, UPLOADING, QuitGuard
from myapp.init.server_home_init import ANALYSIS_KEY, LAUNCH_KEYS
from myapp.notifications import Notifier
from myapp.pending_saves import PendingSave, PendingSaveStore
from myapp.services import CLOSE_DB_KEY, AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs
from tests.fakes import ticking_clock
from tests.integration.helpers import STAFF_ID, qml, toasts, unused_port, wait_until
from tests.integration.test_admin_feedback import click

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
    """登入後的外殼，打開錄影頁、按 Open Recorder（預設是顧客鏡頭 0、員工鏡頭 1、第一支麥克風）。"""
    monkeypatch.setattr(record_widget, "datetime", ticking_clock())
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
    qml(window, "recordButton.click()")  # 側邊欄的 Record
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready")
               and qml(window, "deviceFinder.status") == "ready")
    qml(window.findChild(QObject, "openRecorderButton"), "clicked()")
    finder = home.deviceFinder
    assert finder.recorder is not None and finder.recorder.isVisible()
    yield home, window, finder
    if finder.recorder is not None:
        finder.recorder.close()  # 錄影中的會先停止；預覽的計時器也停掉


def quit_app(services):
    """和結束 app 一樣：關掉資料庫連線，取消還在跑的背景工作（main.py 的 aboutToQuit，上傳就停在那裡）。"""
    if services.close_db():
        wait_until(lambda: not services.tasks.isBusy(CLOSE_DB_KEY))
    services.tasks.shutdown()


@pytest.fixture
def relaunch(db):
    """重開 app、同一個帳號再登入（T26）：新的 engine 和背景服務，本機的設定（QSettings）是同一份。
    relaunch(舊的 services) 先結束上一次；回傳 (home, 外殼視窗, services)。"""
    opened = []

    def relaunch_app(old_services=None):
        if old_services is not None:
            quit_app(old_services)
        engine = QQmlApplicationEngine()
        services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
        opened.append((engine, services))
        home = server_home_init.launch(engine, STAFF_ID, services)
        wait_until(lambda: not any(services.tasks.isBusy(key) for key in LAUNCH_KEYS))
        return home, engine.rootObjects()[-1], services

    yield relaunch_app
    for engine, services in opened:
        quit_app(services)
        engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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


# ── 錄影頁的 Saving 清單（新版介面 UD9、T29）────────────────────────────────────

def page_item(window, name):
    """錄影頁畫面樹（childItems）裡 objectName 是 name 的項目：Repeater 建立的列沒有 QObject parent，findChild 找不到。"""
    pending = [qml(window, "mainContentLoader.item")]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name and item.isVisible():
            return item
        pending.extend(item.childItems())
    return None


def close_toasts(window, services):
    """關掉所有通知，等右下角的通知堆疊縮成 0（下一個畫格才縮，在那之前會擋住底下的點擊）。"""
    for toast in toasts(services.notifier):
        services.notifier.dismiss(toast["toastId"])
    wait_until(lambda: qml(window.findChild(QObject, "toastHost"), "height") == 0)


def test_saving_card_keeps_a_save_after_its_toast_is_closed_then_retries_and_analyzes_it(
        staff, services, db, s3, gate, analysis_service, no_network_on_gui_thread):
    """錄影頁的主要任務之二（PLAN 6-2）：通知關掉之後，在 Saving 卡片上看得到沒存好的那一段，按 Retry 存好、按 Analyze 送去分析。"""
    home, window, finder = staff
    assert not page_item(window, "savingCard")  # 還沒有任何存檔：不顯示這張卡片
    gate.errors = [S3_DOWN]
    recording = record(finder.recorder)
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == FAILED)
    [toast] = toasts(services.notifier)
    close_toasts(window, services)  # 通知關掉了：以前就再也看不到這一筆
    assert qml(window, "deviceFinder.saves.count") == 1  # QML 讀得到（外殼的 context property，不用新註冊）
    [row] = finder.saves.rows()
    assert (row["serviceId"], row["status"], row["canRetry"]) == (sid, "failed", True)
    wait_until(lambda: page_item(window, "saveRow") is not None)
    assert page_item(window, "saveStatus").property("text") == "Wasn't saved"
    assert page_item(window, "saveReason").property("text") == (
        "Can't reach cloud storage. The recording is still on this computer.")  # 和通知同一句
    assert qml(page_item(window, "saveRow"), "Accessible.name") == (  # 螢幕閱讀器唸一整列
        f"{sid}. Wasn't saved. Can't reach cloud storage. The recording is still on this computer.")
    assert not page_item(window, "saveAnalyze")

    click(window, page_item(window, "saveRetry"))  # Saving 卡片的 Retry（和通知上的 Retry 同一條路）
    wait_until(lambda: job.state == SAVED, describe=lambda: (job.state, job.error_detail))
    [row] = finder.saves.rows()
    assert (row["status"], row["attempt"], row["percent"], row["canAnalyze"]) == ("saved", 2, 100, True)
    wait_until(lambda: page_item(window, "saveStatus").property("text")
               == f"Saved at {job.saved_at:%H:%M} · not analyzed yet")
    assert not page_item(window, "saveRetry")
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [(1,)]
    assert sorted(obj["Key"] for obj in s3.list_objects_v2(Bucket="adam-test")["Contents"]) == sorted(recording.keys)

    # Saving 卡片的 Analyze：和通知上的一樣交給送出分析的流程（ServerHome → ServiceModel → 分析服務，狀態 2 是它寫的）
    close_toasts(window, services)  # 「Saved」那則疊在右下角（測試的視窗很小）
    click(window, page_item(window, "saveAnalyze"))
    wait_until(lambda: [row["serviceId"] for row in home.serviceModel.analyzingModel.rows()] == [sid])
    assert analysis_service.requests == [("/analyze", {"service_id": sid})]
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [(2,)]
    [row] = finder.saves.rows()
    assert (row["status"], row["canAnalyze"], row["analyzeRequested"]) == ("saved", False, True)
    assert page_item(window, "saveStatus").property("text") == f"Saved at {job.saved_at:%H:%M} · sent for analysis"
    assert not page_item(window, "saveAnalyze")  # 送去分析之後就沒有這顆按鈕了，不會送兩次
    assert qml(window, f"deviceFinder.saves.analyze('{sid}')") is False
    assert not finder.recorder.status_bar.button.isEnabled()  # 錄影視窗的 Analyze 也停用了


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

    qml(window, "exitButton.click()")  # 側邊欄的 Quit
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

    assert not qml(window.findChild(QObject, "quitConfirmDialog"), "visible")  # 外殼也攔了這次 ⌘Q：只開一個對話框
    qml(confirm, "clicked()")  # Quit Anyway
    wait_until(lambda: quits == [True])
    assert not QuitGuard.instance().blocking(finder)  # 接下來的 Quit 不再攔
    assert not QuitGuard.instance().blocking(home.session)
    # 結束時 main.py 的 tasks.shutdown() 取消上傳：這一筆沒有存進資料庫，檔案留在這台電腦上
    services.tasks.cancel(f"upload:{sid}")
    gate.release()
    wait_until(lambda: not services.tasks.isBusy(f"upload:{sid}"))
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    assert all(os.path.exists(path) for path in recording.files)


def test_log_out_while_uploading_asks_with_the_upload_warning(engine, staff, services, db, gate,
                                                              no_network_on_gui_thread):
    """上傳中按 Log Out（N1）：和 Quit 同一個上傳的警告，不是「Log out?」，不疊兩層。Keep Uploading 什麼都不變；
    Log Out Anyway 取消上傳，檔案和本機的紀錄留著，同一個帳號下次登入時可以 Retry（T26）。"""
    home, window, finder = staff
    recorder = finder.recorder
    recording = record(recorder, started=lambda sid: gate.hold(f"video/{sid}_cam1.avi"))
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == UPLOADING and job.done == job.sizes[0])
    dialog = quit_dialog(window)
    cancel, confirm = dialog.findChild(QObject, "cancelButton"), dialog.findChild(QObject, "confirmButton")

    qml(window, "logoutButton.click()")
    wait_until(lambda: qml(dialog, "opened"))
    assert not qml(window.findChild(QObject, "logoutDialog"), "visible")
    assert dialog.property("title") == "Log out while uploading?"
    amount = f"{job.done / 1e6:.1f} / {job.total / 1e6:.1f} MB"
    assert dialog.property("body") == (
        f"{sid} is still uploading ({amount}). If you log out now, this service won't be saved.")
    assert (cancel.property("text"), confirm.property("text")) == ("Keep Uploading", "Log Out Anyway")
    wait_until(cancel.hasActiveFocus)  # 預設是安全的選項
    qml(cancel, "clicked()")
    wait_until(lambda: not qml(dialog, "visible"))
    assert job.state == UPLOADING and not home.session.ended and window.isVisible()

    qml(window, "logoutButton.click()")
    wait_until(lambda: qml(dialog, "opened"))
    qml(confirm, "clicked()")  # Log Out Anyway
    assert home.session.ended and not window.isVisible()
    assert finder.recorder is None  # 錄影視窗也關了
    wait_until(lambda: not services.tasks.isBusy(f"upload:{sid}"))  # 上傳取消了
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
    assert all(os.path.exists(path) for path in recording.files)
    assert [pending.service_id for pending in PendingSaveStore().load(STAFF_ID)] == [sid]  # 下次登入有 Retry
    assert not QuitGuard.instance().blocking(finder) and not QuitGuard.instance().blocking(home.session)
    assert toasts(services.notifier) == []  # 這個帳號的通知不留給下一個帳號


def test_quit_without_uploads_asks_quit_adam_first(engine, staff, services, db, no_network_on_gui_thread):
    """沒有在錄、沒有在存：以前直接結束（UX-29），N1 改成一律先問「Quit ADAM?」，不是上傳的警告。"""
    home, window, finder = staff
    quits = track_quit(engine)
    assert not QuitGuard.instance().blocking(finder)  # deviceFinder 不攔；攔 ⌘Q 的是外殼（session）
    assert QuitGuard.instance().blocking(home.session)
    qml(window, "exitButton.click()")
    confirm = window.findChild(QObject, "quitConfirmDialog")
    wait_until(lambda: qml(confirm, "opened"))
    assert quits == [] and not qml(quit_dialog(window), "visible")
    qml(confirm.findChild(QObject, "confirmButton"), "clicked()")  # Quit
    wait_until(lambda: quits == [True])
    assert not QuitGuard.instance().blocking(home.session)


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
    qml(window, "exitButton.click()")  # 錄影中按 Quit：先停止並存檔
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


def test_open_recorder_brings_the_recording_window_back_instead_of_opening_another(staff, no_network_on_gui_thread):
    home, window, finder = staff
    recorder = finder.recorder
    button = window.findChild(QObject, "openRecorderButton")
    hint = window.findChild(QObject, "openRecorderHint")
    assert button.property("text") == "Open Recorder"
    assert hint.property("text") == ("The recorder opens in its own window. "
                                     "Recording starts when you press Start Recording there.")
    recorder.button_start_recording.click()
    assert button.property("text") == "Show Recorder"  # 錄影中：按下去是帶回錄影視窗
    assert hint.property("text") == "Recording is in progress in the recorder window."
    qml(button, "clicked()")
    assert finder.recorder is recorder and recorder.recording  # 沒有開第二個，錄影也沒停
    recorder.button_stop_recording.click()
    assert button.property("text") == "Open Recorder"
    wait_until(lambda: finder.uploadCount == 0)  # 存完才結束這個測試


# ── 重開 app 之後還能重試（T26）──────────────────────────────────────────────

def test_failed_upload_is_offered_again_after_a_restart(staff, services, db, s3, gate, relaunch,
                                                        no_network_on_gui_thread):
    home, window, finder = staff
    gate.errors = [S3_DOWN]
    recording = record(finder.recorder)
    sid = recording.service_id
    wait_until(lambda: job_of(finder, recording).state == FAILED)

    home, window, services = relaunch(services)  # 結束 app，同一個帳號再登入
    [toast] = toasts(services.notifier)  # 和失敗時同一種通知：常駐的錯誤＋Retry
    assert (toast["severity"], toast["title"], toast["detail"], toast["timeoutMs"]) == (
        "error", f"{sid} wasn't saved", "The upload didn't finish. The recording is still on this computer.", 0)
    assert (toast["actionId"], toast["actionLabel"]) == (f"retry-upload:{sid}", "Retry")
    wait_until(lambda: shown_toasts(window) and shown_toasts(window)[0]["actionLabel"] == "Retry")
    assert shown_toasts(window)[0]["title"] == f"{sid} wasn't saved"

    qml(window.findChild(QObject, "toastHost"), f"trigger({toast['toastId']})")  # 按通知上的 Retry
    job = home.deviceFinder.saver.job(sid)
    wait_until(lambda: job.state == SAVED, describe=lambda: (job.state, job.error_detail))
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [(1,)]
    assert sorted(obj["Key"] for obj in s3.list_objects_v2(Bucket="adam-test")["Contents"]) == sorted(recording.keys)
    assert toasts(services.notifier)[0]["title"] == f"Saved {sid}"
    assert PendingSaveStore().load(STAFF_ID) == []
    model = home.serviceModel.notAnalyzedModel  # 存好之後一樣重新整理分析頁
    wait_until(lambda: [row["serviceId"] for row in model.rows()] == [sid])


def test_quitting_mid_upload_is_offered_at_the_next_login(staff, services, db, s3, gate, relaunch,
                                                          no_network_on_gui_thread):
    home, window, finder = staff
    recording = record(finder.recorder, started=lambda sid: gate.hold(f"video/{sid}_cam1.avi"))
    sid = recording.service_id
    job = job_of(finder, recording)
    wait_until(lambda: job.state == UPLOADING and job.done == job.sizes[0])  # 第 1 個檔案傳完、停在第 2 個
    finder.allowQuit()  # Quit Anyway（對話框在 test_quit_while_uploading_asks_first_from_exit_close_and_cmd_q）

    home, window, services = relaunch(services)  # aboutToQuit 的 tasks.shutdown() 取消上傳；再登入一次
    gate.release()
    [toast] = toasts(services.notifier)
    assert (toast["title"], toast["detail"], toast["actionLabel"]) == (
        f"{sid} wasn't saved", "The upload didn't finish. The recording is still on this computer.", "Retry")
    services.notifier.trigger(toast["toastId"])
    job = home.deviceFinder.saver.job(sid)
    wait_until(lambda: job.state == SAVED, describe=lambda: (job.state, job.error_detail))
    assert db.rows("SELECT service_status FROM tbl_service WHERE service_id = %s", (sid,)) == [(1,)]
    assert PendingSaveStore().load(STAFF_ID) == []


def test_database_write_that_happened_before_quitting_is_not_written_twice(db, relaunch, no_network_on_gui_thread):
    """上次 COMMIT 成功了，但結果回來之前 app 就結束了（Quit Anyway、當掉）：資料庫已經有這一筆，本機的紀錄還在。
    重開之後的 Retry 撞到主鍵（真的 MySQL 的 1062），是這個帳號自己的那一筆，就當作存好了。"""
    sid, start = "S20261030120000", datetime(2026, 10, 30, 12, 0, 0)
    keys = (f"video/{sid}_cam0.avi", f"video/{sid}_cam1.avi", f"audio/{sid}_audio.wav")
    urls = tuple(f"s3://adam-test/{key}" for key in keys)
    db.add_service(sid, start, status=1)
    for url in urls[:2]:
        db.add_media(sid, "video", url)
    db.add_media(sid, "audio", urls[2])
    PendingSaveStore().put(PendingSave(sid, STAFF_ID, tuple(f"/Users/staff/{key}" for key in keys), start,
                                       start + timedelta(minutes=5), "database", urls))

    home, window, services = relaunch()
    [toast] = toasts(services.notifier)
    assert toast["detail"] == "The files uploaded, but the database couldn't be updated. Retry to finish saving."
    services.notifier.trigger(toast["toastId"])
    job = home.deviceFinder.saver.job(sid)
    wait_until(lambda: job.state == SAVED, describe=lambda: (job.state, job.error_detail))
    assert toasts(services.notifier)[0]["title"] == f"Saved {sid}"
    assert db.rows("SELECT COUNT(*) FROM tbl_service WHERE service_id = %s", (sid,)) == [(1,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_video_storage WHERE service_id = %s", (sid,)) == [(2,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_audio_storage WHERE service_id = %s", (sid,)) == [(1,)]
    assert PendingSaveStore().load(STAFF_ID) == []


def test_login_drops_saves_whose_files_are_gone_and_keeps_other_staffs(db, relaunch, tmp_path,
                                                                       no_network_on_gui_thread):
    start = datetime(2026, 10, 30, 12, 0, 0)
    gone = PendingSave("S20261030120000", STAFF_ID, tuple(str(tmp_path / "video" / name) for name in
                                                          ("a.avi", "b.avi", "c.wav")),
                       start, start + timedelta(minutes=5), "upload")  # 例如有人清掉了 ~/video
    theirs = PendingSave("S20261030121500", "another-staff", gone.files, start, None, "upload")
    PendingSaveStore().put(gone)
    PendingSaveStore().put(theirs)

    home, window, services = relaunch()
    [toast] = toasts(services.notifier)
    assert (toast["severity"], toast["title"], toast["detail"], toast["actionLabel"]) == (
        "error", "S20261030120000 can't be saved",
        "The recording is no longer on this computer. It may have been moved or deleted.", "")
    wait_until(lambda: shown_toasts(window) and shown_toasts(window)[0]["title"] == "S20261030120000 can't be saved")
    assert shown_toasts(window)[0]["actionLabel"] == ""  # 沒有 Retry
    # 錄影頁的 Saving 卡片留一列說明，一樣不能重試（T29）；不算「還在存」，結束時不必問
    assert [(row["serviceId"], row["status"], row["canRetry"]) for row in home.deviceFinder.saves.rows()] == [
        ("S20261030120000", "failed", False)]
    assert home.deviceFinder.uploadCount == 0
    assert PendingSaveStore().load(STAFF_ID) == []  # 紀錄刪掉了，下次登入不會再說一次
    assert PendingSaveStore().load("another-staff") == [theirs]  # 別人的留著
    assert db.rows("SELECT COUNT(*) FROM tbl_service") == [(0,)]
