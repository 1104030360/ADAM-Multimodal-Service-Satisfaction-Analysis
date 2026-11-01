"""自己的帳號（myapp/account.py，new-feature N3、S6，T36）不需要資料庫的規則：欄位檢查、錯誤怎麼顯示、照片的處理、
條件更新的重試、同名的人怎麼分辨。真的資料庫、S3、登入的整合測試在 tests/integration/test_account_self_service.py。

背景工作用真的 TaskRunner，db lane 換成假的（FakeLane）：每一句 SQL 的結果由測試排好。
"""
import logging
import os
import struct
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import mysql.connector
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from myapp import account
from myapp.account import (AccountGone, AccountProfile, AccountSelfService, ChangedElsewhere, PhotoError,
                           WrongPassword, avatar_key, prepare_photo, round_photo, swap_photo_key)
from myapp.home_dashboard import ranking_rows
from myapp.init.admin_home_init import staff_choices
from myapp.notifications import Notifier
from myapp.tasks import TaskRunner
from tests.integration.helpers import toasts, wait_until

ACCOUNT_ID = "acc-1"
PROFILE = {"account_name": "Mia Chen", "account_username": "mia.chen", "account_level": "1",
           "account_occupation_name": "Service specialist", "account_photo_key": None}


class FakeCursor:
    def __init__(self, lane):
        self.lane = lane
        self.rowcount = 0
        self._row = None

    def execute(self, sql, params=()):
        self.lane.calls.append((" ".join(sql.split()), params))
        result = self.lane.results.pop(0) if self.lane.results else None
        if isinstance(result, Exception):
            raise result
        self.rowcount, self._row = result if isinstance(result, tuple) else (0, result)

    def fetchone(self):
        return self._row

    def nextset(self):
        return None


class FakeLane:
    """取代 DbLane：results 依序是每一句 SQL 的結果，(rowcount, fetchone 的列) 或例外。"""

    def __init__(self):
        self.results = []
        self.calls = []

    @contextmanager
    def cursor(self, dictionary=False):
        yield FakeCursor(self)


@pytest.fixture
def services():
    services = SimpleNamespace(tasks=TaskRunner(), notifier=Notifier(), db=FakeLane(), ui_prefs=None)
    yield services
    services.tasks.shutdown()


def settle(services):
    keys = ("staff:account-name", *AccountSelfService.KEYS.values(), AccountSelfService.PREPARE_KEY,
            AccountSelfService.UPLOAD_KEY, AccountSelfService.PHOTO_DB_KEY)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in keys))


@pytest.fixture
def me(services):
    """已經載入好的帳號資料（Mia Chen，服務人員），以及它的自助修改和發出的 signal。"""
    services.db.results = [(1, dict(PROFILE))]
    profile = AccountProfile(services, ACCOUNT_ID, "staff:account-name")
    profile.load()
    settle(services)
    services.db.calls.clear()
    self_service = AccountSelfService(services, profile)
    events = SimpleNamespace(saved=[], focus=[], edited=[])
    self_service.saved.connect(events.saved.append)
    self_service.focusRequested.connect(events.focus.append)
    self_service.accountEdited.connect(events.edited.append)
    return SimpleNamespace(profile=profile, account=self_service, events=events)


def mysql_error(errno, msg):
    return mysql.connector.Error(msg=msg, errno=errno)


# ---------------------------------------------------------------- 帳號資料


def test_profile_loads_every_field_in_one_query(services):
    services.db.results = [(1, dict(PROFILE))]
    profile = AccountProfile(services, ACCOUNT_ID, "staff:account-name")
    assert (profile.state, profile.name, profile.level) == ("loading", "", 0)
    profile.load()
    settle(services)
    assert (profile.state, profile.name, profile.username, profile.level, profile.jobTitle) == (
        "ready", "Mia Chen", "mia.chen", 1, "Service specialist")
    assert (profile.hasPhoto, profile.photoState, profile.photoSource) == (False, "none", "")
    [(sql, params)] = services.db.calls
    assert sql.startswith("SELECT account_name, account_username, account_level") and params == (ACCOUNT_ID,)


def test_staff_profile_failure_has_no_toast_and_a_missing_account_is_an_error(services):
    services.db.results = [mysql_error(2003, "Can't connect to MySQL server")]
    profile = AccountProfile(services, ACCOUNT_ID, "staff:account-name")  # 服務人員端：沒有 retry_action
    profile.load()
    settle(services)
    assert profile.state == "error" and toasts(services.notifier) == []
    assert profile.errorDetail == "Can't reach the database. Check your network and try again."

    services.db.results = [(0, None)]
    profile.load()
    settle(services)
    assert (profile.state, profile.level, profile.failureCount) == ("error", 0, 2)
    assert profile.errorDetail == "Your account wasn't found. Log in again."


# ---------------------------------------------------------------- 顯示名稱、登入帳號、密碼：送出前的檢查


def test_rename_checks_the_name_before_the_database(services, me):
    assert not me.account.rename("   ")
    assert me.account.errors["name"] == "Enter a name." and me.events.focus == ["name"]
    assert not me.account.rename("x" * 101)
    assert me.account.errors["name"] == "Use 100 characters or fewer."
    assert me.account.rename("  Mia Chen  ")  # 去掉前後空白之後沒有改：直接收起表單，不寫資料庫
    assert me.events.saved == ["name"] and me.account.errors["name"] == ""
    assert services.db.calls == [] and me.events.edited == []


def test_rename_writes_only_the_name_of_the_logged_in_account(services, me):
    services.db.results = [(1, None)]
    assert me.account.rename(" Mia Lin ")
    assert not me.account.rename("Someone Else")  # 還在存：不會送第二次
    settle(services)
    assert services.db.calls == [("UPDATE tbl_account SET account_name = %s WHERE account_id = %s",
                                  ("Mia Lin", ACCOUNT_ID))]
    assert me.profile.name == "Mia Lin"
    assert (me.events.saved, me.events.edited) == (["name"], ["name"])
    assert toasts(services.notifier)[0]["title"] == "Display name updated"


def test_rename_of_an_account_that_is_gone(services, me):
    services.db.results = [(0, None), (0, None)]  # UPDATE 沒有改到、帳號也查不到
    assert me.account.rename("Mia Lin")
    settle(services)
    assert me.account.errors["nameForm"] == "Your account wasn't found. Log in again."
    assert me.profile.name == "Mia Chen" and me.events.saved == []


def test_username_needs_a_name_and_the_current_password(services, me):
    assert not me.account.changeUsername("  ", "")
    assert (me.account.errors["username"], me.account.errors["usernamePassword"]) == (
        "Enter a username.", "Enter your current password.")
    assert me.events.focus == ["username"]
    assert not me.account.changeUsername("x" * 65, "pw")
    assert me.account.errors["username"] == "Use 64 characters or fewer."
    assert me.account.changeUsername(" mia.chen ", "pw")  # 沒有改
    assert me.events.saved == ["username"] and services.db.calls == []


def test_passwords_are_checked_as_typed_and_never_trimmed(services, me):
    assert not me.account.changePassword("", "", "")
    assert (me.account.errors["currentPassword"], me.account.errors["newPassword"],
            me.account.errors["confirmPassword"]) == ("Enter your current password.", "Enter a new password.", "")
    assert not me.account.changePassword("old", "new pw ", "")
    assert me.account.errors["confirmPassword"] == "Enter the new password again."
    assert not me.account.changePassword("old", "new pw ", "new pw")  # 結尾的空白也算：不 trim
    assert me.account.errors["confirmPassword"] == "The new passwords don't match."
    assert me.events.focus[-1] == "confirmPassword" and services.db.calls == []

    me.account.clearError("confirmPassword")  # 使用者改了這一格：它的錯誤先拿掉
    assert me.account.errors["confirmPassword"] == ""
    me.account.changePassword("", "x", "x")
    me.account.clearForm("password")  # Cancel：整個表單的錯誤都拿掉
    assert all(me.account.errors[field] == "" for field in account.FORM_FIELDS["password"])
    me.account.clearError("unknown")  # 不認得的欄位：不理它
    me.account.clearForm("unknown")


@pytest.mark.parametrize("form, exc, field, message", [
    ("password", WrongPassword(), "currentPassword", "The current password is incorrect."),
    ("username", WrongPassword(), "usernamePassword", "The current password is incorrect."),
    ("username", mysql_error(1062, "Duplicate entry 'mia' for key 'tbl_account.uk_account_username'"), "username",
     "This username is taken. Choose another one."),
    ("password", AccountGone(ACCOUNT_ID), "passwordForm", "Your account wasn't found. Log in again."),
    ("name", mysql_error(2003, "Can't connect to MySQL server"), "nameForm",
     "Can't reach the database. Nothing was changed. Check your network and try again."),
    ("password", mysql_error(1146, "Table doesn't exist"), "passwordForm",
     "Database request failed. Nothing was changed. If this keeps happening, contact your administrator."),
])
def test_failures_are_written_on_the_field_or_the_form(services, me, form, exc, field, message):
    me.account._form_failed(form, exc)
    assert me.account.errors[field] == message and me.events.focus == [field]
    assert me.events.saved == [] and toasts(services.notifier) == []


def test_changed_elsewhere_reloads_the_profile(services, me):
    services.db.results = [(1, dict(PROFILE, account_username="mia.c"))]
    me.account._form_failed("username", ChangedElsewhere())
    assert me.account.errors["usernameForm"] == (
        "Your account was changed on another computer. Check your details and try again.")
    settle(services)
    assert me.profile.username == "mia.c"  # 重新載入，摘要是最新的


def test_logs_never_contain_what_the_exception_says(services, me, caplog):
    """錯誤訊息可能帶著 SQL 的參數（雜湊）：log 只記類別和錯誤碼。"""
    caplog.set_level(logging.WARNING)
    me.account._form_failed("password", mysql_error(1406, "Data too long: pbkdf2_sha256$600000$secret-hash"))
    assert "secret-hash" not in caplog.text and "Error 1406" in caplog.text


# ---------------------------------------------------------------- 照片：條件更新


def test_swap_photo_key_uses_the_known_key_first():
    lane = FakeLane()
    lane.results = [(1, None)]
    assert swap_photo_key(lane, ACCOUNT_ID, "avatars/new.jpg", "avatars/old.jpg") == "avatars/old.jpg"
    [(sql, params)] = lane.calls
    assert "account_photo_key <=> %s" in sql and params == ("avatars/new.jpg", ACCOUNT_ID, "avatars/old.jpg")


def test_swap_photo_key_retries_with_the_key_it_finds():
    """在別處換過了：讀出現在的值、用它再換一次，回傳的是真的被換掉的那一個（要刪的）。"""
    lane = FakeLane()
    lane.results = [(0, None), (1, ("avatars/other.jpg",)), (1, None)]
    assert swap_photo_key(lane, ACCOUNT_ID, None, "avatars/old.jpg") == "avatars/other.jpg"
    assert [params for _, params in lane.calls] == [(None, ACCOUNT_ID, "avatars/old.jpg"), (ACCOUNT_ID,),
                                                    (None, ACCOUNT_ID, "avatars/other.jpg")]


def test_swap_photo_key_edge_cases():
    lane = FakeLane()
    lane.results = [(0, None), (1, ("avatars/new.jpg",))]  # 已經是這個值（同一秒存兩次）：沒有要刪的
    assert swap_photo_key(lane, ACCOUNT_ID, "avatars/new.jpg", None) is None
    lane.results = [(0, None), (0, None)]
    with pytest.raises(AccountGone):
        swap_photo_key(lane, ACCOUNT_ID, "avatars/new.jpg", None)
    lane.results = [(0, None), (1, ("a",)), (0, None), (1, ("b",))]
    with pytest.raises(ChangedElsewhere):
        swap_photo_key(lane, ACCOUNT_ID, "avatars/new.jpg", None, attempts=2)


def test_avatar_keys_follow_the_plan_and_never_repeat(services, me):
    assert avatar_key("acc-1", datetime(2026, 11, 1, 9, 30, 5)) == "avatars/acc-1-20261101093005.jpg"
    moment = datetime(2026, 11, 1, 9, 30, 5)
    keys = [me.account._new_key(lambda: moment) for _ in range(3)]  # 同一秒存三次：key 都不一樣
    assert keys == ["avatars/acc-1-20261101093005.jpg", "avatars/acc-1-20261101093006.jpg",
                    "avatars/acc-1-20261101093007.jpg"]


# ---------------------------------------------------------------- 照片：本機的處理


def write_image(path, width, height, colours=(QColor("red"), QColor("blue")), image_format="JPEG"):
    """左半邊 colours[0]、右半邊 colours[1] 的圖。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(colours[1])
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 2, height, colours[0])
    painter.end()
    assert image.save(str(path), image_format, 95)
    return path


def exif_segment(orientation):
    """一段 EXIF（APP1）：方向（Orientation）和 GPS 的緯度（GPSLatitudeRef = N），模擬手機拍的照片。"""
    gps_offset = 8 + 2 + 2 * 12 + 4
    ifd0 = struct.pack("<H", 2)
    ifd0 += struct.pack("<HHIHH", 0x0112, 3, 1, orientation, 0)       # Orientation，SHORT
    ifd0 += struct.pack("<HHII", 0x8825, 4, 1, gps_offset)             # GPS IFD 的位置
    ifd0 += struct.pack("<I", 0)
    gps = struct.pack("<H", 1) + struct.pack("<HHI4s", 0x0001, 2, 2, b"N\0\0\0") + struct.pack("<I", 0)
    tiff = b"II*\0" + struct.pack("<I", 8) + ifd0 + gps
    payload = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def jpeg_with_exif(path, width, height, orientation=6):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image = QImage(str(write_image(path, width, height)))
    image.save(buffer, "JPEG", 95)
    raw = bytes(data)
    path.write_bytes(raw[:2] + exif_segment(orientation) + raw[2:])  # 緊接在 SOI 之後
    return path


def close_to(colour, expected, tolerance=60):
    return all(abs(a - b) <= tolerance for a, b in zip(colour.getRgb()[:3], expected.getRgb()[:3]))


def test_prepare_photo_crops_scales_and_saves_jpeg(tmp_path):
    jpg, png = prepare_photo(str(write_image(tmp_path / "wide.png", 600, 300, image_format="PNG")),
                             str(tmp_path / "out"))
    image = QImage(jpg)
    assert (image.width(), image.height()) == (256, 256)
    assert bytes(QImageReader(jpg).format()) == b"jpeg"
    assert os.path.basename(jpg).startswith("pending-") and jpg.endswith(".jpg") and png.endswith(".png")
    # 置中裁成正方形：600×300 取中間的 300×300，左半紅、右半藍
    assert close_to(image.pixelColor(30, 128), QColor("red")) and close_to(image.pixelColor(226, 128), QColor("blue"))
    rounded = QImage(png)
    assert rounded.hasAlphaChannel() and rounded.pixelColor(2, 2).alpha() == 0  # 圓形外面透明
    assert rounded.pixelColor(128, 128).alpha() == 255


def test_prepare_photo_turns_the_photo_upright_and_drops_exif(tmp_path):
    """手機直拍（EXIF Orientation 6，要轉 90 度）：照它轉正；存出來的 JPEG 沒有 EXIF（含 GPS）。"""
    source = jpeg_with_exif(tmp_path / "phone.jpg", 300, 200)
    assert b"Exif" in source.read_bytes()
    jpg, _png = prepare_photo(str(source), str(tmp_path / "out"))
    data = open(jpg, "rb").read()
    assert b"Exif" not in data and b"\xff\xe1" not in data
    image = QImage(jpg)
    # 存的時候左紅右藍，轉 90 度之後是上紅下藍；沒有轉的話上面會是左紅右藍
    assert close_to(image.pixelColor(20, 20), QColor("red")) and close_to(image.pixelColor(236, 20), QColor("red"))
    assert close_to(image.pixelColor(20, 236), QColor("blue")) and close_to(image.pixelColor(236, 236), QColor("blue"))


def test_transparent_png_is_put_on_white(tmp_path):
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    path = tmp_path / "clear.png"
    image.save(str(path), "PNG")
    jpg, _ = prepare_photo(str(path), str(tmp_path / "out"))
    assert close_to(QImage(jpg).pixelColor(128, 128), QColor("white"), 5)  # 不是黑色


def test_huge_images_are_decoded_smaller(tmp_path):
    jpg, _ = prepare_photo(str(write_image(tmp_path / "big.jpg", 4200, 2100)), str(tmp_path / "out"))
    assert QImage(jpg).size().width() == 256


@pytest.mark.parametrize("content, reason", [
    (b"\xff\xd8" + os.urandom(account.PHOTO_MAX_BYTES), "too_large"),
    (b"hello, this is not a photo", "not_image"),
    (None, "unreadable"),
])
def test_prepare_photo_rejects(tmp_path, content, reason):
    path = tmp_path / "file.jpg"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(PhotoError) as error:
        prepare_photo(str(path), str(tmp_path / "out"))
    assert error.value.reason == reason
    assert not (tmp_path / "out").exists() or os.listdir(tmp_path / "out") == []


def test_round_photo_squares_other_sizes():
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    rounded = round_photo(image, size=64)
    assert (rounded.width(), rounded.height()) == (64, 64)


@pytest.mark.parametrize("reason, message", [
    ("too_large", "This photo is larger than 5 MB. Choose a smaller one."),
    ("not_image", "This file isn't a photo we can open. Choose a JPEG, PNG or HEIC image."),
    ("unreadable", "This file can't be opened. Choose another photo."),
])
def test_rejected_photos_explain_why(services, me, reason, message):
    me.account._photo_rejected(PhotoError(reason))
    assert me.account.errors["photo"] == message and me.account.photoWork == ""


def test_choosing_a_photo_shows_a_preview_and_cancel_drops_it(services, me, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_MEDIA_DIR", str(tmp_path / "cache" / "media"))
    source = write_image(tmp_path / "me.jpg", 400, 400)
    assert me.account.choosePhoto(f"file://{source}")
    assert me.account.photoWork == "preparing"
    settle(services)
    assert me.account.hasPendingPhoto and me.account.previewSource.startswith("file://")
    jpg, png = me.account._pending
    assert os.path.dirname(jpg) == str(tmp_path / "cache" / "avatars")
    me.account.discardPhoto()
    assert not me.account.hasPendingPhoto and me.account.previewSource == ""
    wait_until(lambda: not os.path.exists(jpg) and not os.path.exists(png))  # 預覽檔在 io lane 刪掉
    assert not me.account.savePhoto() and not me.account.removePhoto()  # 沒有預覽、沒有照片：不送
    me.account.choosePhoto(str(tmp_path / "missing.jpg"))
    settle(services)
    assert me.account.errors["photo"] == "This file can't be opened. Choose another photo."


# ---------------------------------------------------------------- 同名的人（PLAN 13.7）


def test_staff_menu_adds_the_username_only_when_names_repeat():
    rows = [("a1", "Leo Park", "leo"), ("a2", "Mia Chen", "mia.chen"), ("a3", "Mia Chen", "mia.lee")]
    assert staff_choices(rows) == [("a1", "Leo Park"), ("a2", "Mia Chen (mia.chen)"), ("a3", "Mia Chen (mia.lee)")]


def test_ranking_keeps_staff_with_the_same_name_apart():
    row = {"services": 2, "customer": 80.0, "staff": 81.0}
    rows = ranking_rows([dict(row, account_id="a2", account_name="Mia Chen", account_username="mia.chen"),
                         dict(row, account_id="a3", account_name="Mia Chen", account_username="mia.lee"),
                         dict(row, account_id="a1", account_name="Leo Park", account_username="leo")])
    assert [(r["accountId"], r["label"]) for r in rows] == [
        ("a2", "Mia Chen (mia.chen)"), ("a3", "Mia Chen (mia.lee)"), ("a1", "Leo Park")]
    assert ranking_rows([dict(row, account_id="a1", account_name="Leo Park")])[0]["label"] == "Leo Park"
