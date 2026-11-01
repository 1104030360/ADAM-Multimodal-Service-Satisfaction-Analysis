"""服務明細與報表（前端重建 F3，CONTRACTS §4 的 C2）：先換頁、每個檔案在背景下載、單一檔案失敗原地重試、離開就取消。

- Details 按下去就換頁，GUI 執行緒 0 個網路往返；檔案清單 1 個往返（db lane），每個檔案一個 io 工作（UX-22、ENG-04）。
- 每個檔案在自己的位置顯示進度；失敗只影響它自己，Retry、Details、第 3 次失敗的建議（UX-23、DS-27）。
- 離開明細頁取消還沒下載完的，下載好的留在快取；再打開不再下載（T21）。
- 圖表有幾張沒載入：警告列和 Retry（DS-52）。報表沒有來源時是空狀態；有 PDF 時在 pdf lane 畫頁面（D7、ENG-07）。
- 服務人員端、管理員端都跑；每個測試都要求 QML 零警告，GUI 執行緒碰網路就失敗（no_network_on_gui_thread）。
"""
import os
import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import count

import botocore.exceptions
import fitz
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl
from PySide6.QtGui import QColor, QImage

from myapp import media, storage
from myapp.init import admin_home_init, server_home_init
from myapp.tasks import Cancelled
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, wait_until

pytestmark = pytest.mark.integration

SID = "S20261030120000"
START = "2026-10-30 12:00:00"
MEDIA_KEYS = {"cam0": f"video/{SID}_cam0.avi", "cam1": f"video/{SID}_cam1.avi", "audio": f"audio/{SID}_audio.wav"}
CHART_NAMES = ["emotion_TEXT", "emotion_AUDIO", "emotion_FACIAL", "summary", "timeline"]
CHART_KEYS = [f"charts/{SID}_{name}.png" for name in CHART_NAMES]
_keys = count(1)


@dataclass(frozen=True)
class Side:
    name: str
    module: object
    account: str
    search_page: str
    detail_page: str


STAFF = Side("staff", server_home_init, STAFF_ID, "server_service_page.qml", "server_service_detail_page.qml")
ADMIN = Side("admin", admin_home_init, ADMIN_ID, "admin_service_page.qml", "admin_service_detail_page.qml")
BOTH_SIDES = pytest.mark.parametrize("side", [STAFF, ADMIN], ids=["staff", "admin"])


# ── 測試資料 ─────────────────────────────────────────────────────────────────

def png_bytes(color="#2E7D32"):
    image = QImage(16, 12, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def body(key):
    return png_bytes() if key.endswith(".png") else f"bytes of {key}".encode()


def add_service(db, s3, sid=SID, media_kinds=("cam0", "cam1", "audio"), charts=3, uploaded=None):
    """資料庫記錄 media_kinds 和前 charts 張圖表；S3 上只有 uploaded 這幾個 key（預設全部都有）。"""
    db.add_service(sid, START)
    keys = [key.replace(SID, sid) for kind, key in MEDIA_KEYS.items() if kind in media_kinds]
    keys += [key.replace(SID, sid) for key in CHART_KEYS[:charts]]
    for key in keys:
        db.add_media(sid, key.split("/")[0], f"s3://adam-test/{key}")
    for key in keys if uploaded is None else uploaded:
        s3.put_object(Bucket="adam-test", Key=key, Body=body(key))
    return keys


# ── fixture 與小工具 ─────────────────────────────────────────────────────────

class BusyKeys:
    """TaskRunner 現在在忙的 key，以及出現過的 key（busyChanged 累計）。"""

    def __init__(self, tasks):
        self.now, self.seen = set(), []
        tasks.busyChanged.connect(self._changed)

    def _changed(self, key, busy):
        if busy:
            self.now.add(key)
            self.seen.append(key)
        else:
            self.now.discard(key)

    def media(self, prefix=f"media:{SID}:"):
        return [key for key in self.seen if key.startswith(prefix)]


@pytest.fixture
def busy(services):
    return BusyKeys(services.tasks)


@pytest.fixture
def qml_warnings(engine):
    """這個 engine 發出的 QML 警告；測試結束時必須是空的。"""
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


@pytest.fixture
def s3_off_gui_thread(monkeypatch):
    """no_network_on_gui_thread 管 storage.download；這裡再管背景下載用的 download_with_progress。"""
    violations = []
    original = storage.download_with_progress

    def guarded(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            violations.append("download_with_progress")
            raise AssertionError("download_with_progress() called on the GUI thread")
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "download_with_progress", guarded)
    yield violations
    assert violations == []


@pytest.fixture
def ui(engine, services, busy, db, s3, qml_warnings, no_network_on_gui_thread, s3_off_gui_thread):
    """所有測試共用的條件：真的 TaskRunner、拋棄式 MySQL、moto 的 S3；GUI 執行緒不准碰網路；QML 零警告。"""
    return engine


@contextmanager
def lane_held(tasks, lane="db"):
    """lane 卡在一個等待中的工作上：期間送出的工作都排在後面。離開 with 才放行。"""
    gate = threading.Event()
    key = f"test:hold:{next(_keys)}"
    tasks.submit(lane, key, lambda token, report: gate.wait(10))
    try:
        yield
    finally:
        gate.set()
        wait_until(lambda: not tasks.isBusy(key))


def launch(side, engine, services, busy):
    home = side.module.launch(engine, side.account, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: not busy.now)  # 首頁、帳號資料都載入完了
    return home, window


def show(window, page):
    """和側邊欄的按鈕一樣換頁。"""
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def find(root, name):
    """用 objectName 找子物件；畫面樹（childItems）也找，ListView、Repeater 建立的項目 findChild 找不到。"""
    found = root.findChild(QObject, name)
    if found is not None:
        return found
    matches = find_all(root, name)
    assert matches, f"找不到 {name}"
    return matches[0]


def find_all(root, name):
    found, pending = [], [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
    return found


def open_from_search(side, window, busy):
    """服務查詢頁：查全部，按第一列的 Details（按下去之前不處理事件）。回傳 Details 按鈕和查詢頁（留著參照）。"""
    page = show(window, side.search_page)
    qml(page, "submit()" if side is STAFF else "search()")  # 日期空白：不限
    wait_until(lambda: not busy.now)
    view = find(page, "resultList")
    wait_until(lambda: find_all(view, "detailsButton"))  # delegate 在下一次畫面更新才建立
    return find_all(view, "detailsButton")[0], page


def open_detail(side, home, window, sid=SID):
    """和 Details 按鈕一樣：先換頁，下載在背景。"""
    home.load_service_media(sid, START)
    return show(window, side.detail_page)


def settled(home):
    """明細頁的每個檔案都結束了（好了、沒有、失敗）。"""
    model = home.mediaModel
    return (model.state == "ready" and not model.pending
            and home.chartsModel.state in ("empty", "ready", "error"))


def local(folder, key, tmp_path):
    return tmp_path / folder / key.split("/")[-1]


def url(path):
    return QUrl.fromLocalFile(str(path)).toString()


def task(key, sid=SID):
    return media.task_key(sid, key.split("/")[-1])


def leftovers(tmp_path):
    """下載的暫存檔（.download、.part）：取消、失敗之後都不能留下。"""
    return [path.name for folder in ("media", "charts") if (tmp_path / folder).exists()
            for path in (tmp_path / folder).iterdir() if path.suffix in (".download", ".part")]


# ── 換頁與下載 ───────────────────────────────────────────────────────────────

@BOTH_SIDES
def test_details_switches_page_at_once_and_downloads_in_the_background(side, ui, services, busy, db, s3, db_proxy,
                                                                       tmp_path):
    keys = add_service(db, s3)
    home, window = launch(side, ui, services, busy)
    button, search_page = open_from_search(side, window, busy)
    with lane_held(services.tasks):
        db_proxy.reset()
        qml(button, "clicked()")
        # 換頁是同步的：按下去就已經在明細頁，頁首有服務 ID 和時間；清單還沒查（db lane 被卡住）
        assert qml(window, "mainContentLoader.source.toString()").endswith(side.detail_page)
        detail = qml(window, "mainContentLoader.item")
        assert find(detail, "serviceIdText").property("text") == SID
        assert find(detail, "startTimeText").property("text") == START
        assert home.mediaModel.state == "loading"
        assert db_proxy.snapshot()["rt"] == 0  # GUI 執行緒 0 個往返（背景的也還沒開始）
    wait_until(lambda: settled(home))
    assert db_proxy.snapshot()["rt"] == 1  # 所有檔案的位置一個查詢就拿到（以前 4 個查詢）
    model = home.mediaModel
    for kind in ("cam0", "cam1", "audio"):
        slot = model.slot(kind)
        assert slot.state == "ready"
        assert slot.source == url(local("media", MEDIA_KEYS[kind], tmp_path))
    assert local("media", MEDIA_KEYS["cam0"], tmp_path).read_bytes() == body(MEDIA_KEYS["cam0"])
    assert model.cam0Source == model.slot("cam0").source  # 以前的名稱照樣能用
    assert home.chartsModel.chartsSources == [url(local("charts", key, tmp_path)) for key in keys[3:]]
    # 每個檔案一個工作（key 帶檔名）。兩條佇列各一次一個：圖表依序、影片和錄音依序；一開始兩條各送出第一個
    started = busy.media()
    assert started[:2] == [task(keys[3]), task(keys[0])]
    assert [key for key in started if key.endswith(".png")] == [task(key) for key in keys[3:]]
    assert [key for key in started if not key.endswith(".png")] == [task(key) for key in keys[:3]]
    assert leftovers(tmp_path) == []
    assert model.canPlay and find(detail, "playButton").property("enabled")
    assert find(detail, "playHint").property("text") == ""


class GatedDownloads:
    """storage.download_with_progress 的替身：gated 裡的 key 卡住，由測試一步一步放行（回報進度、完成、失敗）；
    其他 key 照常從 moto 下載。取消時丟 Cancelled，和真的一樣。"""

    def __init__(self, real, gated):
        self.real = real
        self.commands = {key: queue.Queue() for key in gated}
        self.started, self.cancelled = [], []
        self._lock = threading.Lock()

    def __call__(self, key, local_path, token=None, report=None):
        assert threading.current_thread() is not threading.main_thread(), "下載跑在 GUI 執行緒"
        with self._lock:
            self.started.append(key)
        commands = self.commands.get(key)
        if commands is None:
            return self.real(key, local_path, token, report)
        token.on_cancel(lambda: commands.put(("cancel",)))
        while True:
            command, *args = commands.get(timeout=10)
            if command == "cancel":
                with self._lock:
                    self.cancelled.append(key)
                raise Cancelled()
            if command == "progress":
                report(*args)
            elif command == "fail":
                raise args[0]
            else:  # finish
                with open(local_path, "wb") as file:
                    file.write(body(key))
                return local_path

    def send(self, key, *command):
        self.commands[key].put(command)


def texts(item):
    """item 底下看得到的 Text 的文字。"""
    found, pending = [], [item]
    while pending:
        current = pending.pop(0)
        if current.inherits("QQuickText") and current.isVisible():
            found.append(current.property("text"))
        pending.extend(current.childItems())
    return found


@BOTH_SIDES
def test_each_file_shows_its_own_progress_and_play_waits_for_all_of_them(side, ui, services, busy, db, s3,
                                                                          monkeypatch):
    add_service(db, s3, charts=0)
    cam0, cam1 = MEDIA_KEYS["cam0"], MEDIA_KEYS["cam1"]
    gate = GatedDownloads(storage.download_with_progress, [cam0, cam1])
    monkeypatch.setattr(storage, "download_with_progress", gate)
    home, window = launch(side, ui, services, busy)
    detail = open_detail(side, home, window)
    model = home.mediaModel
    wait_until(lambda: model.slot("cam0").state == "downloading")
    assert (model.slot("cam1").state, model.slot("cam1").waitingFor) == ("waiting", "cam0")
    assert (model.slot("audio").state, model.slot("audio").waitingFor) == ("waiting", "cam1")

    gate.send(cam0, "progress", 1_000_000, 4_000_000)
    wait_until(lambda: model.slot("cam0").done == 1_000_000)
    assert model.slot("cam0").total == 4_000_000
    assert (model.slot("cam1").done, model.slot("cam1").total) == (0, 0)  # 進度只在自己的位置
    # 畫面：過了 300 ms 才出現文字（DS-25）。顧客那一格是進度，員工那一格是排隊中
    progress = find(find(detail, "cam0Box"), "progress")
    wait_until(progress.isVisible)
    assert find(progress, "progressLabel").property("text") == "Downloading customer video"
    assert find(progress, "progressAmount").property("text") == "1.0 / 4.0 MB · 25%"
    waiting = find(find(detail, "cam1Box"), "waitingState")
    wait_until(waiting.isVisible)
    assert texts(waiting) == ["Waiting to download", "Queued after the customer video"]
    audio_status = find(detail, "audioStatus")
    wait_until(audio_status.isVisible)
    assert audio_status.property("text") == "Waiting to download the audio · Queued after the staff video"
    # 還沒下載完不能播放；時間用等寬數字
    play, time_text = find(detail, "playButton"), find(detail, "timeText")
    assert not play.property("enabled")
    assert find(detail, "playHint").property("text") == "You can play the videos after they download."
    assert time_text.property("text") == "--:-- / --:--"
    assert qml(time_text, "font.features")["tnum"] == 1
    # 強調色是 Theme 的石墨灰（以前是 Material 預設的粉紅）
    volume = find(detail, "volumeSlider")
    assert qml(volume, "Qt.colorEqual(handle.color, Theme.actionPrimary)")

    gate.send(cam0, "progress", 3_000_000, 4_000_000)  # 到 100%：不受節流，馬上送出
    gate.send(cam0, "finish")
    wait_until(lambda: model.slot("cam0").state == "ready")
    wait_until(lambda: model.slot("cam1").state == "downloading")
    # 員工鏡頭的下載斷掉：只有它那一格顯示錯誤（DS-52 的文字），錄音照樣接著下載
    gate.send(cam1, "fail", botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.example"))
    wait_until(lambda: model.slot("cam1").state == "error")
    assert model.slot("cam1").errorDetail == (
        f"The download of {SID}_cam1.avi was interrupted. Check your network and try again.")
    wait_until(lambda: not model.pending)
    assert model.canPlay and play.property("enabled")
    assert find(detail, "playHint").property("text") == "Customer video and audio are ready to play."
    assert time_text.property("text") != "--:-- / --:--"

    error = find(find(detail, "cam1Box"), "errorState")
    qml(error, "retry()")
    wait_until(lambda: model.slot("cam1").state == "downloading")
    assert not model.canPlay  # 重新下載中：等它好了再一起播
    gate.send(cam1, "finish")
    wait_until(lambda: settled(home))
    assert [model.slot(kind).state for kind in ("cam0", "cam1", "audio")] == ["ready"] * 3
    assert find(detail, "playHint").property("text") == ""


@BOTH_SIDES
def test_a_file_that_fails_shows_an_error_with_retry_in_its_own_place(side, ui, services, busy, db, s3, tmp_path):
    missing = MEDIA_KEYS["cam0"]
    keys = add_service(db, s3)
    s3.delete_object(Bucket="adam-test", Key=missing)  # 資料庫有，S3 上沒有
    home, window = launch(side, ui, services, busy)
    detail = open_detail(side, home, window)
    wait_until(lambda: settled(home))
    model, cam0 = home.mediaModel, home.mediaModel.slot("cam0")
    assert (cam0.state, cam0.failureCount) == ("error", 1)
    assert cam0.errorDetail == f"{SID}_cam0.avi isn't in cloud storage. It may have been moved or deleted."
    assert cam0.errorTechnical.startswith(f"{SID}_cam0.avi · ClientError")
    # 其他的照常可以用
    assert [model.slot(kind).state for kind in ("cam1", "audio")] == ["ready", "ready"]
    assert home.chartsModel.readyCount == 3
    assert model.canPlay
    assert find(detail, "playHint").property("text") == "Staff video and audio are ready to play."
    assert not model.retry("cam1")  # 好的那一個沒有東西可以重試

    error = find(find(detail, "cam0Box"), "errorState")
    wait_until(error.isVisible)
    assert find(error, "errorTitle").property("text") == "Video failed to load"
    assert find(error, "detailsButton").isVisible()  # 技術細節收在 Details 裡，預設收起
    assert not find(error, "errorTechnical").isVisible()
    assert not find(error, "errorHint").isVisible()
    for failures in (2, 3):  # 還是沒有：再失敗兩次，第 3 次多一行建議
        qml(error, "retry()")
        wait_until(lambda: cam0.failureCount == failures)
    wait_until(lambda: find(error, "errorHint").isVisible())
    assert leftovers(tmp_path) == []

    s3.put_object(Bucket="adam-test", Key=missing, Body=body(missing))
    qml(error, "retry()")
    wait_until(lambda: cam0.state == "ready")
    assert not error.isVisible()
    assert local("media", missing, tmp_path).read_bytes() == body(missing)
    assert find(detail, "playHint").property("text") == ""
    assert keys  # 圖表沒有被重新下載
    assert busy.media().count(task(missing)) == 4


@BOTH_SIDES
def test_leaving_the_page_cancels_unfinished_downloads_and_keeps_finished_ones(side, ui, services, busy, db, s3,
                                                                              tmp_path, monkeypatch):
    keys = add_service(db, s3, charts=2)
    cam0, cam1, audio = MEDIA_KEYS["cam0"], MEDIA_KEYS["cam1"], MEDIA_KEYS["audio"]
    gate = GatedDownloads(storage.download_with_progress, [cam1])
    monkeypatch.setattr(storage, "download_with_progress", gate)
    home, window = launch(side, ui, services, busy)
    detail = open_detail(side, home, window)
    model = home.mediaModel
    wait_until(lambda: model.slot("cam1").state == "downloading" and home.chartsModel.state == "ready")
    gate.send(cam1, "progress", 1_000_000, 4_000_000)
    wait_until(lambda: model.slot("cam1").done == 1_000_000)
    assert model.slot("audio").state == "waiting"

    qml(find(detail, "backButton"), "clicked()")
    # 換頁的當下就取消：還在下載的停下來，排隊中的不會開始
    assert qml(window, "mainContentLoader.source.toString()").endswith(side.search_page)
    wait_until(lambda: not services.tasks.isBusy(task(cam1)))
    assert gate.cancelled == [cam1]
    assert audio not in gate.started
    assert leftovers(tmp_path) == []
    assert not local("media", cam1, tmp_path).exists()
    # 下載好的留在快取
    assert local("media", cam0, tmp_path).exists()
    assert all(local("charts", key, tmp_path).exists() for key in keys[3:])

    started = len(gate.started)
    open_detail(side, home, window)
    wait_until(lambda: model.slot("cam1").state == "downloading")
    assert model.slot("cam0").state == "ready" and home.chartsModel.readyCount == 2  # 直接用快取
    gate.send(cam1, "finish")
    wait_until(lambda: settled(home))
    assert gate.started[started:] == [cam1, audio]  # 只下載上次沒有完成的


@BOTH_SIDES
def test_opening_the_same_service_again_uses_the_cache(side, ui, services, busy, db, s3, db_proxy, monkeypatch):
    add_service(db, s3)
    downloads = []
    real = storage.download_with_progress

    def counted(key, *args, **kwargs):
        downloads.append(key)
        return real(key, *args, **kwargs)

    monkeypatch.setattr(storage, "download_with_progress", counted)
    home, window = launch(side, ui, services, busy)
    open_detail(side, home, window)
    wait_until(lambda: settled(home))
    assert len(downloads) == 6
    show(window, side.search_page)  # 離開，再點同一筆
    wait_until(lambda: not busy.now)  # 管理員的查詢頁一打開會載入服務人員名單

    db_proxy.reset()
    seen = len(busy.seen)
    open_detail(side, home, window)
    wait_until(lambda: settled(home))
    assert len(downloads) == 6  # 一個都沒有重新下載
    assert busy.seen[seen:] == [media.list_key(SID)]  # 只查了檔案清單，沒有下載的工作
    assert db_proxy.snapshot()["rt"] == 1
    assert home.mediaModel.canPlay and home.chartsModel.readyCount == 3


@BOTH_SIDES
def test_charts_that_did_not_load_show_a_warning_bar_with_retry(side, ui, services, busy, db, s3):
    keys = add_service(db, s3, charts=5)
    for key in CHART_KEYS[3:]:  # 5 張裡有 2 張不在 S3
        s3.delete_object(Bucket="adam-test", Key=key)
    home, window = launch(side, ui, services, busy)
    detail = open_detail(side, home, window)
    wait_until(lambda: settled(home))
    charts = home.chartsModel
    assert (charts.state, charts.readyCount, charts.failedCount, charts.totalCount) == ("ready", 3, 2, 5)
    assert charts.errorDetail.endswith("isn't in cloud storage. It may have been moved or deleted.")

    qml(find(detail, "tabBar"), "setCurrentIndex(1)")
    warning = find(detail, "chartsWarning")
    wait_until(warning.isVisible)
    assert find(warning, "chartsWarningTitle").property("text") == "2 charts didn't load"
    assert find(warning, "chartsWarningText").property("text") == "You can view the other 3 now."
    counter = find(detail, "chartCounter")
    assert counter.property("text") == "1 / 3"
    assert not find(detail, "previousChartButton").property("enabled")
    qml(find(detail, "nextChartButton"), "clicked()")
    assert counter.property("text") == "2 / 3"
    image = find(detail, "chartImage")
    wait_until(lambda: qml(image, "status === Image.Ready"))
    assert qml(image, "asynchronous")
    assert qml(image, "source.toString()") == charts.chartsSources[1]
    assert 0 < qml(image, "sourceSize.width") <= qml(image, "Math.ceil(width * Screen.devicePixelRatio)")

    for key in CHART_KEYS[3:]:
        s3.put_object(Bucket="adam-test", Key=key, Body=body(key))
    qml(find(warning, "chartsRetryButton"), "clicked()")
    wait_until(lambda: charts.readyCount == 5)
    assert charts.failedCount == 0 and not warning.isVisible()
    assert counter.property("text") == "2 / 5"
    assert busy.media().count(task(keys[0])) == 1  # 影片沒有被重新下載


def test_when_no_chart_loads_the_charts_tab_shows_an_error_with_retry(ui, services, busy, db, s3):
    add_service(db, s3, charts=2, uploaded=list(MEDIA_KEYS.values()))
    home, window = launch(STAFF, ui, services, busy)
    detail = open_detail(STAFF, home, window)
    wait_until(lambda: settled(home))
    charts = home.chartsModel
    assert (charts.state, charts.failedCount, charts.failureCount) == ("error", 2, 1)
    assert charts.errorTechnical.count("\n") == 1  # 每一張一行
    qml(find(detail, "tabBar"), "setCurrentIndex(1)")
    error = find(detail, "chartsError")
    wait_until(error.isVisible)
    assert find(error, "errorTitle").property("text") == "Charts didn't load"
    assert not find(detail, "chartsWarning").isVisible()
    for key in CHART_KEYS[:2]:
        s3.put_object(Bucket="adam-test", Key=key, Body=body(key))
    qml(error, "retry()")
    wait_until(lambda: charts.state == "ready" and charts.readyCount == 2)
    assert not error.isVisible()


def test_a_service_without_files_says_so_in_each_place(ui, services, busy, db, s3):
    db.add_service(SID, START, status=1)  # 還沒分析、也沒有檔案的紀錄（管理員查得到所有狀態）
    home, window = launch(ADMIN, ui, services, busy)
    detail = open_detail(ADMIN, home, window)
    wait_until(lambda: settled(home))
    model = home.mediaModel
    assert [model.slot(kind).state for kind in ("cam0", "cam1", "audio")] == ["missing"] * 3
    assert not model.canPlay
    assert find(detail, "playHint").property("text") == "There's nothing to play for this service."
    missing = find(find(detail, "cam0Box"), "missingState")
    assert missing.isVisible() and missing.property("title") == "No customer video"
    assert find(detail, "audioStatus").property("text") == "This service doesn't have audio."
    qml(find(detail, "tabBar"), "setCurrentIndex(1)")
    empty = find(detail, "chartsEmpty")
    wait_until(empty.isVisible)
    assert empty.property("title") == "No charts yet"


@BOTH_SIDES
def test_when_the_file_list_cannot_load_the_page_offers_retry(side, ui, services, busy, db, s3, db_proxy,
                                                              mysql_server, monkeypatch):
    add_service(db, s3)
    home, window = launch(side, ui, services, busy)
    db_proxy.kill()  # 連線斷掉，新的連線也被拒絕
    detail = open_detail(side, home, window)
    wait_until(lambda: home.mediaModel.state == "error" and not home.mediaModel.busy)
    error = find(detail, "listError")
    wait_until(error.isVisible)
    assert error.property("title") == "Couldn't load this service"
    assert error.property("detail") == "Can't reach the database. Check your network and try again."
    assert not find(detail, "noServiceState").isVisible()
    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))  # 資料庫恢復（直接連，不經過 proxy）
    qml(error, "retry()")
    wait_until(lambda: settled(home))
    assert not error.isVisible()
    assert home.mediaModel.canPlay


def test_the_detail_page_without_a_service_offers_to_go_back(ui, services, busy):
    home, window = launch(STAFF, ui, services, busy)
    detail = show(window, STAFF.detail_page)  # 沒有按 Details（例如直接換到這一頁）
    empty = find(detail, "noServiceState")
    assert empty.isVisible() and empty.property("title") == "No service selected"
    qml(empty, "actionTriggered()")
    assert qml(window, "mainContentLoader.source.toString()").endswith(STAFF.search_page)


# ── 報表 ────────────────────────────────────────────────────────────────────

def reports_page(side):
    return "server_reports_page.qml" if side is STAFF else "admin_reports_page.qml"


def make_pdf(path, pages):
    document = fitz.Document()  # 不用 fitz.open：no_network_on_gui_thread 把它包起來了，測試自己做 PDF 不算
    for number in range(pages):
        document.new_page().insert_text((72, 72), f"Page {number + 1}")
    document.save(path)
    document.close()


@BOTH_SIDES
def test_reports_page_says_there_are_no_reports_yet(side, ui, services, busy):
    assert not os.path.exists(media.REPORTS_DIR)  # 報表還沒有來源（PLAN D7）
    home, window = launch(side, ui, services, busy)
    page = show(window, reports_page(side))
    empty = find(page, "reportsEmpty")
    assert empty.isVisible()
    assert empty.property("title") == "No reports yet"
    assert empty.property("description") == "Reports will appear here once they're available."
    assert not find(page, "reportList").isVisible()
    assert home.pdfmodel.state == "empty"


@BOTH_SIDES
def test_reports_open_on_the_pdf_lane_and_page_through(side, ui, services, busy, tmp_path, monkeypatch):
    source = tmp_path / "report-source"
    source.mkdir()
    make_pdf(source / f"{SID}.pdf", pages=2)
    (source / "notes.txt").write_text("not a report")
    opened_on = []
    real_open = fitz.open  # no_network_on_gui_thread 已經包過：在 GUI 執行緒開就失敗
    monkeypatch.setattr(fitz, "open", lambda *args, **kwargs: opened_on.append(threading.current_thread().name)
                        or real_open(*args, **kwargs))
    home, window = launch(side, ui, services, busy)
    home.pdfmodel.reports_dir = str(source)
    page = show(window, reports_page(side))
    view = find(page, "reportList")
    wait_until(lambda: find_all(view, "reportItem"))
    [item] = find_all(view, "reportItem")
    assert item.property("text") == f"{SID}.pdf"
    assert find(page, "chooseReport").isVisible()

    qml(item, "clicked()")
    handler = home.pdfmodel
    wait_until(lambda: handler.viewState == "ready")
    assert (handler.page, handler.pageCount) == (1, 2)
    assert opened_on and all(name.startswith("pdf-lane") for name in opened_on)
    image = find(page, "pageImage")
    wait_until(lambda: qml(image, "status === Image.Ready"))
    assert find(page, "pageCounter").property("text") == "Page 1 of 2"
    assert not find(page, "previousPageButton").property("enabled")
    qml(find(page, "nextPageButton"), "clicked()")
    wait_until(lambda: handler.page == 2 and handler.viewState == "ready")
    assert find(page, "pageCounter").property("text") == "Page 2 of 2"
    assert not find(page, "nextPageButton").property("enabled")
    rendered = sorted(path.name for path in (tmp_path / "reports").iterdir())
    assert len(rendered) == 2 and all(name.startswith(SID) and name.endswith(".png") for name in rendered)
    handler._tasks().shutdown()  # ServerHome、AdminHome 建立時沒有給 services：它自己的 pdf lane（T21）


@BOTH_SIDES
def test_going_back_before_the_file_list_arrives_cancels_it(side, ui, services, busy, db, s3, monkeypatch):
    add_service(db, s3)
    downloads = []
    monkeypatch.setattr(storage, "download_with_progress", lambda key, *args, **kwargs: downloads.append(key))
    home, window = launch(side, ui, services, busy)
    with lane_held(services.tasks):  # 清單還沒查：使用者就按了 Back
        detail = open_detail(side, home, window)
        assert home.mediaModel.state == "loading" and home.chartsModel.state == "loading"
        qml(find(detail, "backButton"), "clicked()")
        assert (home.mediaModel.state, home.mediaModel.busy, home.chartsModel.state) == ("idle", False, "idle")
    wait_until(lambda: not busy.now)
    assert media.list_key(SID) in busy.seen and busy.media() == []  # 查詢被取消，沒有開始任何下載
    assert downloads == []


# ── 不需要畫面的部分：檔案清單、下載、錯誤的文字 ─────────────────────────────

class Rows:
    """假的 cursor：execute 記下參數，fetchall 回傳 rows。"""

    def __init__(self, rows):
        self.rows, self.params = rows, None

    def execute(self, sql, params):
        self.params = params

    def fetchall(self):
        return self.rows


def test_file_list_reads_tuple_and_dict_rows_and_skips_rows_without_a_name():
    url = f"s3://adam-test/video/{SID}_cam0.avi"
    tuples = Rows([(1, 0, "cam0", url), (4, 7, b"chart", "s3://adam-test/charts/"), (3, 0, "audio", None)])
    assert media.list_files(tuples, SID) == [media.MediaFile("cam0", url, f"{SID}_cam0.avi")]
    assert tuples.params == (SID, "%cam0%", SID, "%cam1%", SID, SID)
    dicts = Rows([{"part": 4, "seq": 1, "kind": "chart", "url": f"s3://adam-test/charts/{SID}_a.png"}])
    [chart] = media.list_files(dicts, SID)
    assert (chart.kind, chart.key) == ("chart", f"charts/{SID}_a.png")


def test_fetch_uses_a_cached_file_without_downloading(tmp_path, monkeypatch):
    file = media.MediaFile("audio", f"s3://adam-test/audio/{SID}_audio.wav", f"{SID}_audio.wav")
    assert file.key == f"audio/{SID}_audio.wav" and file.local_path == str(tmp_path / "media" / file.name)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / file.name).write_bytes(b"cached")
    monkeypatch.setattr(storage, "download_with_progress", lambda *args, **kwargs: pytest.fail("不該下載"))
    assert media.fetch(file) == file.local_path


def test_a_failed_download_leaves_no_temporary_file(tmp_path, monkeypatch):
    file = media.MediaFile("cam1", f"s3://adam-test/video/{SID}_cam1.avi", f"{SID}_cam1.avi")

    def broken(key, local_path, token=None, report=None):
        open(local_path, "wb").close()  # 暫存檔已經寫好，改名之前出事
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage, "download_with_progress", broken)
    with pytest.raises(OSError):
        media.fetch(file)
    assert list((tmp_path / "media").iterdir()) == []


def test_failure_text_names_the_file_and_says_what_to_do():
    name = f"{SID}_cam0.avi"
    offline = botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.example")
    assert media.describe_failure(offline, name)[0] == (
        f"The download of {name} was interrupted. Check your network and try again.")
    disk_full, technical = media.describe_failure(OSError(28, "No space left on device"), name)
    assert disk_full == "Not enough disk space. Free up space on this computer, then try again."
    assert technical == f"{name} · OSError: [Errno 28] No space left on device"
    assert media.technical_text(Cancelled()) == "Cancelled"
