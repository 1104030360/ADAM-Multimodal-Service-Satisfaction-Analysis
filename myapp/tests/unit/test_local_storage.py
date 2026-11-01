"""設定頁 Help 的 Storage（new-feature S5，T40）：快取大小、剩餘空間、Clear Download Cache，以及 Quit 用的同一套清除。

- 只清 media.download_cache_dirs()；錄影（目前目錄的 video/、audio/）、還沒存完的錄影（pending_saves）一定留著。
- 掃描、清除都在 io lane；用到快取的工作（media:、pdf:）在跑時不能清。
"""
import os
import shutil
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject

from myapp import local_storage, media
from myapp.local_storage import CacheStorage, Protected
from myapp.notifications import Notifier
from myapp.pending_saves import PendingSave, PendingSaveStore
from myapp.tasks import CancelToken, Cancelled, TaskRunner
from tests.integration.helpers import toasts, wait_until

STAFF = "00000000-0000-0000-0000-000000000002"


def write(path, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def remember_pending(tmp_path, service_id="S20261101093000", folder=None):
    """一段還沒存完的錄影（pending_saves 記在 QSettings）；回傳三個檔案的路徑。"""
    folder = folder or tmp_path
    files = (write(folder / "video" / f"{service_id}_cam0.avi"), write(folder / "video" / f"{service_id}_cam1.avi"),
             write(folder / "audio" / f"{service_id}_audio.wav"))
    PendingSaveStore().put(PendingSave(service_id, STAFF, tuple(str(path) for path in files),
                                       datetime(2026, 11, 1, 9, 30), datetime(2026, 11, 1, 9, 35), "upload"))
    return files


# ---------------------------------------------------------------- 文字


@pytest.mark.parametrize("size, text", [
    (0, "Empty"), (1, "1 bytes"), (999, "999 bytes"), (1000, "1 KB"), (532_000, "532 KB"),
    (123_400_000, "123.4 MB"), (1_500_000_000, "1.5 GB"), (494_300_000_000, "494.3 GB"),
    (2_000_000_000_000, "2.0 TB"), (5_000_000_000_000_000, "5000.0 TB"), (-5, "Empty"),
])
def test_size_text_uses_decimal_units_like_finder(size, text):
    assert local_storage.size_text(size) == text


# ---------------------------------------------------------------- 大小、剩餘空間


def test_folders_size_counts_each_file_once_and_skips_links(tmp_path):
    write(tmp_path / "media" / "a.avi", 1000)
    write(tmp_path / "media" / "sub" / "b.png", 200)
    os.link(tmp_path / "media" / "a.avi", tmp_path / "media" / "hard.avi")      # 硬連結：同一個檔案
    os.symlink(tmp_path / "media" / "a.avi", tmp_path / "media" / "soft.avi")   # 符號連結不算
    write(tmp_path / "elsewhere" / "big.bin", 5000)
    os.symlink(tmp_path / "elsewhere", tmp_path / "media" / "linked-folder")    # 不跟進去
    folders = [tmp_path / "media", tmp_path / "media" / "sub", tmp_path / "missing"]  # 重疊的資料夾也只算一次
    assert local_storage.folders_size(folders) == 1200


def test_folders_size_and_clearing_stop_when_cancelled(tmp_path):
    write(tmp_path / "media" / "a.avi")
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        local_storage.folders_size([tmp_path / "media"], token)
    with pytest.raises(Cancelled):
        local_storage.clear_folders([tmp_path / "media"], Protected(), token)
    assert (tmp_path / "media" / "a.avi").exists()


def test_folders_size_ignores_files_that_disappear(tmp_path, monkeypatch):
    write(tmp_path / "media" / "a.avi", 7)
    write(tmp_path / "media" / "gone.part", 3)
    real_lstat = os.lstat
    monkeypatch.setattr(local_storage.os, "lstat",
                        lambda path: (_ for _ in ()).throw(FileNotFoundError()) if str(path).endswith(".part")
                        else real_lstat(path))
    assert local_storage.folders_size([tmp_path / "media"]) == 7


def test_disk_usage_merges_places_on_the_same_disk_and_uses_the_nearest_existing_folder(tmp_path):
    disks = local_storage.disk_usage({"recordings": str(tmp_path / "video"), "cache": str(tmp_path / "a" / "b")})
    assert len(disks) == 1 and disks[0]["places"] == ("recordings", "cache")
    usage = shutil.disk_usage(tmp_path)
    assert disks[0]["total"] == usage.total and disks[0]["free"] > 0


def test_disk_usage_lists_each_disk_and_skips_places_it_cannot_read(tmp_path, monkeypatch):
    real_stat, real_usage = os.stat, shutil.disk_usage
    devices = {str(tmp_path / "cache"): 99, str(tmp_path / "unreadable"): 77}

    def fake_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        return SimpleNamespace(st_dev=devices[str(path)], st_mode=info.st_mode) if str(path) in devices else info

    def fake_usage(path):
        if str(path).endswith("unreadable"):
            raise PermissionError(path)
        return real_usage(path)

    (tmp_path / "cache").mkdir()
    (tmp_path / "unreadable").mkdir()
    monkeypatch.setattr(local_storage.os, "stat", fake_stat)
    monkeypatch.setattr(local_storage.shutil, "disk_usage", fake_usage)
    disks = local_storage.disk_usage({"recordings": str(tmp_path), "cache": str(tmp_path / "cache"),
                                      "other": str(tmp_path / "unreadable")})
    assert [disk["places"] for disk in disks] == [("recordings",), ("cache",)]


# ---------------------------------------------------------------- 保護清單


def test_protected_locations_cover_recordings_home_and_every_pending_file(tmp_path):
    files = remember_pending(tmp_path)
    other = remember_pending(tmp_path, "S20261101100000")
    PendingSaveStore()._settings.setValue("pendingSaves/broken", "not json")  # 看不懂的紀錄略過，不刪
    protected = local_storage.protected_locations()
    assert {os.path.realpath(tmp_path / "video"), os.path.realpath(tmp_path / "audio"),
            os.path.realpath(os.path.expanduser("~"))} <= set(protected.dirs)
    assert {os.path.realpath(path) for path in (*files, *other)} == protected.files
    assert PendingSaveStore()._settings.value("pendingSaves/broken") == "not json"


@pytest.mark.parametrize("folder, unsafe", [
    ("media", False), ("init/media", False), (".", True), ("video", True), ("/", True),
])
def test_unsafe_reason_refuses_folders_that_hold_recordings_or_a_disk_root(tmp_path, folder, unsafe):
    protected = local_storage.protected_locations()
    path = folder if folder == "/" else str(tmp_path / folder)
    assert bool(local_storage.unsafe_reason(path, protected)) is unsafe


def test_cache_task_prefixes_cover_downloads_and_report_pages():
    assert local_storage.cache_dirs_in_use({"media:S1:S1_cam0.avi"})
    assert local_storage.cache_dirs_in_use({"upload:S1", "pdf:report.pdf"})
    assert not local_storage.cache_dirs_in_use({"upload:S1", "help:check:files", "storage:scan"})


# ---------------------------------------------------------------- 清除


def test_clear_folders_empties_the_caches_and_keeps_the_folders(tmp_path):
    write(tmp_path / "media" / "S1_cam0.avi")
    write(tmp_path / "media" / "half.avi.0123.download.part")
    write(tmp_path / "charts" / "sub" / "c.png")
    (tmp_path / "media" / "empty-sub").mkdir()
    failures = local_storage.clear_folders([tmp_path / "media", tmp_path / "charts", tmp_path / "never-made"])
    assert failures == []
    assert list((tmp_path / "media").iterdir()) == [] and list((tmp_path / "charts").iterdir()) == []
    assert not (tmp_path / "never-made").exists()  # 沒用過的快取資料夾不建立


def test_clear_folders_never_touches_recordings_even_when_the_cache_folder_is_misconfigured(tmp_path):
    recording = write(tmp_path / "video" / "S1_cam0.avi")
    audio = write(tmp_path / "audio" / "S1_audio.wav")
    cached = write(tmp_path / "S1_chart.png")
    failures = local_storage.clear_folders([tmp_path])  # 例如 LOCAL_MEDIA_DIR 指到目前目錄（家目錄）
    assert [path for path, _exc in failures] == [str(tmp_path)]
    assert recording.exists() and audio.exists() and cached.exists()  # 整個資料夾都不清


def test_clear_folders_keeps_pending_files_that_sit_inside_a_cache_folder(tmp_path):
    files = remember_pending(tmp_path, folder=tmp_path / "media" / "odd")  # 設定錯了：錄影在快取資料夾裡
    write(tmp_path / "media" / "S1_cam0.avi")
    write(tmp_path / "media" / "deep" / "x.png")
    os.symlink(tmp_path / "elsewhere", tmp_path / "media" / "dangling")
    (tmp_path / "media" / "linked").symlink_to(tmp_path / "media" / "odd")
    assert local_storage.clear_folders([tmp_path / "media"]) == []
    assert all(path.exists() for path in files)
    assert not (tmp_path / "media" / "S1_cam0.avi").exists() and not (tmp_path / "media" / "deep").exists()
    assert not (tmp_path / "media" / "linked").exists()  # 連結本身刪掉，指到的資料夾留著


def test_clear_folders_reports_what_it_could_not_remove(tmp_path):
    write(tmp_path / "media" / "locked" / "a.avi")
    (tmp_path / "media" / "locked").chmod(0o500)  # 不能刪裡面的檔案
    try:
        failures = local_storage.clear_folders([tmp_path / "media"], Protected())
        assert failures and all(isinstance(exc, OSError) for _path, exc in failures)
        assert (tmp_path / "media" / "locked" / "a.avi").exists()
    finally:
        (tmp_path / "media" / "locked").chmod(0o700)


def test_clear_folders_reports_files_it_could_not_remove_one_by_one(tmp_path, monkeypatch):
    remember_pending(tmp_path, folder=tmp_path / "media")  # 有保護的檔案在裡面：一個一個刪
    write(tmp_path / "media" / "busy.png")
    real_remove = os.remove
    monkeypatch.setattr(local_storage.os, "remove", lambda path: (_ for _ in ()).throw(PermissionError(path))
                        if str(path).endswith("busy.png") else real_remove(path))
    failures = local_storage.clear_folders([tmp_path / "media"])
    assert [os.path.basename(path) for path, _exc in failures] == ["busy.png"]


def test_clear_folders_reports_a_folder_it_cannot_recreate(tmp_path, monkeypatch):
    write(tmp_path / "media" / "a.avi")
    monkeypatch.setattr(local_storage.os, "makedirs", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("ro")))
    failures = local_storage.clear_folders([tmp_path / "media"], Protected())
    assert [path for path, _exc in failures] == [os.path.abspath(tmp_path / "media")]


def test_clear_and_measure_reports_freed_and_remaining_bytes(tmp_path):
    write(tmp_path / "media" / "a.avi", 3000)
    write(tmp_path / "charts" / "c.png", 500)
    result = local_storage.clear_and_measure([tmp_path / "media", tmp_path / "charts"], Protected())
    assert result == local_storage.ClearResult(freed=3500, size=0, failures=0)


def test_quit_uses_the_same_folders_and_protection(tmp_path, monkeypatch):
    """media.clear_cache()（Quit）和設定頁的清除看同一份 download_cache_dirs()，也一樣不碰錄影。"""
    monkeypatch.setenv("LOCAL_MEDIA_DIR", str(tmp_path))  # 設定錯了：快取指到目前目錄
    recording = write(tmp_path / "video" / "S1_cam0.avi")
    write(tmp_path / "charts" / "c.png")
    assert media.download_cache_dirs() == [str(tmp_path), str(tmp_path / "charts"),
                                           os.path.join(os.path.dirname(str(tmp_path)), "reports")]
    media.clear_cache()
    assert recording.exists()
    assert list((tmp_path / "charts").iterdir()) == []


# ---------------------------------------------------------------- CacheStorage（設定頁）


@pytest.fixture
def services():
    tasks = TaskRunner(lanes={"io": 2})
    notifier = Notifier()
    yield SimpleNamespace(tasks=tasks, notifier=notifier)
    tasks.shutdown()


@pytest.fixture
def cache(tmp_path):
    write(tmp_path / "media" / "S1_cam0.avi", 1_500_000)
    write(tmp_path / "charts" / "c.png", 500_000)
    return [str(tmp_path / "media"), str(tmp_path / "charts")]


def make_storage(services, folders):
    return CacheStorage(services, lambda: folders)


def wait_ready(storage):
    wait_until(lambda: storage.state in ("ready", "error"))


def test_refresh_measures_the_cache_and_the_free_space_on_the_io_lane(services, cache):
    storage = make_storage(services, cache)
    assert storage.state == "idle" and storage.cacheText == "" and not storage.canClear
    assert storage.refresh()
    assert storage.state == "loading" and storage.scanning and storage.cacheText == "Calculating…"
    assert not storage.refresh()  # 不重複送出
    wait_ready(storage)
    assert storage.state == "ready" and storage.cacheBytes == 2_000_000 and storage.cacheText == "2.0 MB"
    assert [disk["label"] for disk in storage.disks] == ["Free space"]
    assert storage.disks[0]["text"].endswith(" free of " + local_storage.size_text(shutil.disk_usage(cache[0]).total))
    assert storage.canClear and storage.blockedReason == ""


def test_refresh_keeps_showing_the_last_size_while_measuring_again(services, cache):
    storage = make_storage(services, cache)
    storage.refresh()
    wait_ready(storage)
    storage.refresh()
    assert storage.state == "loading" and storage.cacheText == "2.0 MB"
    wait_ready(storage)


def test_disk_labels_name_each_place_when_they_are_on_different_disks():
    label = CacheStorage._disk_label
    assert (label(("recordings", "cache")), label(("recordings",)), label(("cache",))) == (
        "Free space", "Free space for recordings", "Free space for the cache")


def test_scan_failure_shows_unavailable_and_blocks_clearing(services, cache, monkeypatch):
    monkeypatch.setattr(local_storage, "scan", lambda *args: (_ for _ in ()).throw(PermissionError("no")))
    storage = make_storage(services, cache)
    storage.refresh()
    wait_ready(storage)
    assert storage.state == "error" and storage.cacheText == "Unavailable"
    assert not storage.canClear and not storage.clear() and storage.blockedReason == ""


def test_clear_removes_only_the_cache_and_measures_again(services, cache, tmp_path):
    recording = write(tmp_path / "video" / "S1_cam0.avi")
    pending = remember_pending(tmp_path, "S20261101120000")
    storage = make_storage(services, cache)
    cleared = []
    storage.cleared.connect(lambda: cleared.append(True))
    storage.refresh()
    wait_ready(storage)
    assert storage.clear()
    assert storage.clearing and storage.state == "loading" and not storage.canClear
    assert not storage.clear()  # 不重複送出
    wait_until(lambda: not storage.clearing and storage.state == "ready")
    assert cleared == [True]
    assert all(not os.listdir(folder) for folder in cache)
    assert recording.exists() and all(path.exists() for path in pending)
    assert storage.cacheBytes == 0 and storage.cacheText == "Empty" and storage.resultText == ""
    assert storage.blockedReason == "The download cache is empty." and not storage.canClear
    assert [(toast["severity"], toast["title"], toast["detail"]) for toast in toasts(services.notifier)] == [
        ("success", "Download cache cleared",
         "Freed 2.0 MB. Videos, charts and reports download again when you open them.")]


def test_clear_reports_files_it_could_not_remove(services, cache, monkeypatch):
    monkeypatch.setattr(local_storage, "clear_folders", lambda folders, protected, token=None: [("x", OSError())])
    storage = make_storage(services, cache)
    storage.refresh()
    wait_ready(storage)
    storage.clear()
    wait_until(lambda: not storage.clearing and storage.state == "ready")
    assert storage.resultFailed and storage.resultText == (
        "Some files couldn't be removed. Close any open videos or reports, then try again.")
    assert toasts(services.notifier) == []
    assert storage.cacheText == "2.0 MB" and storage.canClear  # 大小重新量過；可以再試一次


def test_clear_failure_is_reported_and_the_size_is_measured_again(services, cache, monkeypatch):
    monkeypatch.setattr(local_storage, "clear_and_measure",
                        lambda *args: (_ for _ in ()).throw(PermissionError(13, "denied")))
    storage = make_storage(services, cache)
    cleared = []
    storage.cleared.connect(lambda: cleared.append(True))
    storage.refresh()
    wait_ready(storage)
    storage.clear()
    wait_until(lambda: not storage.clearing and storage.state == "ready")
    assert storage.resultFailed and storage.resultText == (
        "Can't access a file on this computer. ADAM doesn't have permission to open or save it.")
    assert cleared == [True]


def test_clearing_is_blocked_while_a_download_or_report_uses_the_cache(services, cache):
    storage = make_storage(services, cache)
    storage.refresh()
    wait_ready(storage)
    release = threading.Event()
    services.tasks.submit("io", "media:S1:S1_cam0.avi", lambda token, report: release.wait(5))
    assert storage.inUse and not storage.canClear and not storage.clear()
    assert storage.blockedReason == "A video, chart or report is downloading or opening. Try again when it's done."
    services.tasks.submit("io", "upload:S2", lambda token, report: release.wait(5))  # 上傳不用快取
    release.set()
    wait_until(lambda: not storage.inUse)
    assert storage.canClear and storage.blockedReason == ""
    wait_until(lambda: not services.tasks.isBusy("upload:S2"))


def test_clear_while_measuring_cancels_the_scan(services, cache, monkeypatch):
    storage = make_storage(services, cache)
    storage.refresh()
    wait_ready(storage)
    started, release = threading.Event(), threading.Event()
    real_scan = local_storage.scan

    def slow_scan(*args):
        started.set()
        release.wait(5)
        return real_scan(*args)

    monkeypatch.setattr(local_storage, "scan", slow_scan)
    storage.refresh()
    started.wait(5)
    assert storage.scanning and storage.canClear
    assert storage.clear()
    assert not storage.scanning
    monkeypatch.setattr(local_storage, "scan", real_scan)
    release.set()
    wait_until(lambda: not storage.clearing and storage.state == "ready")
    assert storage.cacheText == "Empty"


def test_without_services_nothing_runs():
    storage = CacheStorage(None, lambda: [])
    assert not storage.refresh() and not storage.clear() and not storage.canClear


def test_default_places_are_the_recordings_and_the_first_cache_folder(services, tmp_path, cache):
    storage = make_storage(services, cache)
    assert storage._places() == {"recordings": str(tmp_path / "video"), "cache": cache[0]}
    assert CacheStorage(services, lambda: [])._places()["cache"] == os.getcwd()


def test_report_pages_are_forgotten_after_clearing(tmp_path):
    """清掉報表頁面圖之後，PDFHandler 放掉目前的頁面，下次打開報表頁時重新畫（不顯示已經刪掉的圖）。"""
    handler = media.PDFHandler(reports_dir=str(tmp_path))
    changes = []
    handler.viewChanged.connect(lambda: changes.append(True))
    handler.forget_pages()
    assert changes == []  # 沒有打開任何報表：什麼都不做
    handler._current, handler._page, handler._page_count = "a.pdf", 2, 3
    handler._source, handler._view = "file:///x.png", "ready"
    handler.forget_pages()
    assert (handler.current, handler.page, handler.pageCount, handler.pageSource, handler.viewState) == (
        "", 0, 0, "", "idle")
    assert changes == [True]


def test_objects_have_parents_and_slots(services):
    parent = QObject()
    storage = CacheStorage(services, lambda: [], parent=parent)
    assert storage.parent() is parent
