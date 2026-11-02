"""自己的帳號（new-feature N3、S6，T36）：真的 MySQL 8.4（db_schema.sql 的三個預存程序）、moto 的 S3、兩端的設定頁。

- 顯示名稱、登入帳號、密碼：成功、空白、重複、目前的密碼不對、兩次新密碼不一樣、取消、重複送出、斷線，
  以及「驗證之後密碼在別處改過」不會被過期的驗證蓋掉。改完用新的帳號密碼登入（verify_login，和登入視窗同一個函式），舊的不行。
- 照片：上傳、換、移除（S3 上的舊檔刪掉）、太大和不是圖片的檔案、EXIF 拿掉、上傳失敗和寫資料庫失敗時原本的照片不動、
  啟動時下載、下載失敗時頁首退回預設的圖示。
- 同名的服務人員：管理員 Services 的選單和首頁的排名分得開（用 account_id，不用名字）。
網路、資料庫的工作都不在 GUI 執行緒（no_network_on_gui_thread）。
"""
import itertools
import os
import struct
import threading
from datetime import datetime

import botocore.exceptions
import mysql.connector
import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest

from myapp import account, storage
from myapp.database import verify_login
from myapp.init import admin_home_init, server_home_init
from myapp.passwords import hash_password
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, toasts, unused_port, wait_until

pytestmark = pytest.mark.integration

OLD_PASSWORD = "old pass "      # 結尾有空白：密碼不 trim
NEW_PASSWORD = "n3w-pass"
_keys = itertools.count()
SIDES = pytest.mark.parametrize("home_init, account_id, page", [
    (server_home_init, STAFF_ID, "server_setting_button.qml"),
    (admin_home_init, ADMIN_ID, "admin_setting_page.qml"),
], ids=["staff", "admin"])


# ── 小工具 ───────────────────────────────────────────────────────────────────

def on_db_lane(services, fn):
    """在 db lane 上執行 fn(lane) 並等它做完（db lane 的連線只能在那條執行緒用）。"""
    done = []
    services.tasks.submit("db", f"test:{next(_keys)}", lambda token, report: fn(services.db),
                          on_ok=lambda result: done.append(result), on_err=lambda exc: done.append(exc))
    wait_until(lambda: done)
    return done[0]


def can_log_in(services, username, password):
    """和登入視窗一樣用 verify_login（sp_get_login＋PBKDF2）；回傳登入成功的 account_id，失敗是 None。"""
    result = on_db_lane(services, lambda lane: verify_login(lane, username, password))
    return result["accountId"] if result else None


def stored_password(db, account_id=STAFF_ID):
    return db.rows("SELECT account_password FROM tbl_account WHERE account_id = %s", (account_id,))[0][0]


def account_row(db, account_id=STAFF_ID):
    return db.rows("SELECT account_username, account_name, account_photo_key FROM tbl_account WHERE account_id = %s",
                   (account_id,))[0]


def launch(engine, services, home_init=server_home_init, account_id=STAFF_ID):
    home = home_init.launch(engine, account_id, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: home.profile.state == "ready")
    return home, window


def open_settings(window, page="server_setting_button.qml"):
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    page_item = qml(window, "mainContentLoader.item")  # 留著參照，子物件的 wrapper 才不會失效（T17）
    return page_item, page_item.findChild(QObject, "accountSettings")


def find(root, name):
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def press(button):
    qml(button, "clicked()")


def type_into(view, field, text):
    qml(find(view, field), f"text = {text!r}")


def busy_keys(services):
    return [key for key in (*account.AccountSelfService.KEYS.values(), account.AccountSelfService.PREPARE_KEY,
                            account.AccountSelfService.UPLOAD_KEY, account.AccountSelfService.PHOTO_DB_KEY)
            if services.tasks.isBusy(key)]


def settle(services):
    wait_until(lambda: not busy_keys(services))


@pytest.fixture
def password(db):
    """服務人員的密碼換成已知的 OLD_PASSWORD（種子帳號的密碼是佔位的雜湊）。"""
    db.execute("UPDATE tbl_account SET account_password = %s, account_username = 'mia.chen', "
               "account_name = 'Mia Chen' WHERE account_id = %s", (hash_password(OLD_PASSWORD), STAFF_ID))
    return OLD_PASSWORD


@pytest.fixture
def qml_warnings(engine):
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


def cut_database(services, db, monkeypatch):
    """斷線：db lane 已經連上的那條被切斷，重連也連不上。回傳恢復用的函式。"""
    [row] = on_db_lane(services, lambda lane: _query(lane, "SELECT CONNECTION_ID()"))
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    db.execute(f"KILL {row[0]}")
    return lambda: monkeypatch.setenv("DB_PORT", port)


def _query(lane, sql, params=()):
    with lane.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


# ── 照片的測試資料 ───────────────────────────────────────────────────────────

def photo(path, width=640, height=480, colours=("red", "blue"), exif_orientation=None):
    """左半邊 colours[0]、右半邊 colours[1] 的 JPEG；exif_orientation 不是 None 時加一段 EXIF（方向和 GPS）。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colours[1]))
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 2, height, QColor(colours[0]))
    painter.end()
    assert image.save(str(path), "JPEG", 95)
    if exif_orientation is not None:
        gps_offset = 8 + 2 + 2 * 12 + 4
        ifd0 = (struct.pack("<H", 2) + struct.pack("<HHIHH", 0x0112, 3, 1, exif_orientation, 0)
                + struct.pack("<HHII", 0x8825, 4, 1, gps_offset) + struct.pack("<I", 0))
        gps = struct.pack("<H", 1) + struct.pack("<HHI4s", 0x0001, 2, 2, b"N\0\0\0") + struct.pack("<I", 0)
        payload = b"Exif\0\0" + b"II*\0" + struct.pack("<I", 8) + ifd0 + gps
        raw = path.read_bytes()
        path.write_bytes(raw[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + raw[2:])
    return path


def s3_keys(s3):
    return sorted(item["Key"] for item in s3.list_objects_v2(Bucket="adam-test").get("Contents", []))


def s3_image(s3, key):
    data = s3.get_object(Bucket="adam-test", Key=key)["Body"].read()
    return data, QImage.fromData(data, "JPEG")


def choose_and_save(home, path, services):
    me = home.myAccount
    assert me.choosePhoto(f"file://{path}")
    wait_until(lambda: me.hasPendingPhoto and me.photoWork == "")
    assert me.savePhoto()
    assert me.photoWork == "saving"
    wait_until(lambda: me.photoWork == "", describe=lambda: me.errors["photo"])
    settle(services)


# ── 顯示名稱 ────────────────────────────────────────────────────────────────

def test_rename_from_the_settings_page_updates_the_database_header_and_summary(engine, services, db, password,
                                                                             qml_warnings, no_network_on_gui_thread):
    home, window = launch(engine, services)
    page, view = open_settings(window)
    assert qml(window, "header.userName") == "Mia Chen"
    assert find(view, "nameRow").property("value") == "Mia Chen"
    press(find(view, "editNameButton"))
    assert qml(view, "openForm") == "name" and find(view, "nameField").property("text") == "Mia Chen"
    type_into(view, "nameField", "  Mia Lin  ")
    press(find(find(view, "nameButtons"), "saveButton"))
    wait_until(lambda: qml(view, "openForm") == "")  # 存好：表單收起來
    assert account_row(db)[1] == "Mia Lin"  # 前後的空白去掉
    assert qml(window, "header.userName") == "Mia Lin" == home.accountName  # 頁首跟著換
    assert find(view, "nameRow").property("value") == "Mia Lin"
    assert find(view, "nameField").property("text") == ""
    assert toasts(services.notifier)[0]["title"] == "Display name updated"
    assert can_log_in(services, "mia.chen", OLD_PASSWORD) == STAFF_ID  # 改名不影響登入


def test_empty_name_is_a_field_error_and_nothing_is_written(engine, services, db, password, qml_warnings):
    home, window = launch(engine, services)
    page, view = open_settings(window)
    press(find(view, "editNameButton"))
    type_into(view, "nameField", "   ")
    press(find(find(view, "nameButtons"), "saveButton"))
    assert home.myAccount.errors["name"] == "Enter a name."
    assert qml(find(view, "nameField"), "error") == "Enter a name."
    assert qml(view, "openForm") == "name"  # 表單留著
    assert not home.myAccount.nameBusy and account_row(db)[1] == "Mia Chen"


# ── 登入帳號 ────────────────────────────────────────────────────────────────

def test_username_change_applies_at_the_next_log_in(engine, services, db, password, no_network_on_gui_thread):
    home, _window = launch(engine, services, admin_home_init, ADMIN_ID)  # 管理員端：同一個自助修改
    db.execute("UPDATE tbl_account SET account_password = %s WHERE account_id = %s",
               (hash_password(OLD_PASSWORD), ADMIN_ID))
    assert can_log_in(services, "placeholder_admin", OLD_PASSWORD) == ADMIN_ID
    assert home.myAccount.changeUsername("  boss.wang  ", OLD_PASSWORD)
    settle(services)
    assert account_row(db, ADMIN_ID)[0] == "boss.wang" and home.profile.username == "boss.wang"
    assert can_log_in(services, "boss.wang", OLD_PASSWORD) == ADMIN_ID
    assert can_log_in(services, "placeholder_admin", OLD_PASSWORD) is None  # 舊的不再是登入的別名
    [toast] = toasts(services.notifier)[:1]
    assert (toast["title"], toast["detail"]) == ("Username updated", "Use boss.wang the next time you log in.")
    assert account_row(db)[0] == "mia.chen"  # 別的帳號不受影響


def test_a_taken_username_is_a_field_error_whatever_the_case(engine, services, db, password):
    home, _window = launch(engine, services)
    assert home.myAccount.changeUsername("PLACEHOLDER_ADMIN", OLD_PASSWORD)  # 定序不分大小寫：和管理員的一樣
    settle(services)
    assert home.myAccount.errors["username"] == "This username is taken. Choose another one."
    assert account_row(db)[0] == "mia.chen" and can_log_in(services, "mia.chen", OLD_PASSWORD) == STAFF_ID


def test_wrong_current_password_changes_nothing(engine, services, db, password):
    home, _window = launch(engine, services)
    before = stored_password(db)
    assert home.myAccount.changeUsername("mia.c", "old pass")  # 少了結尾的空白：不對（不 trim）
    assert home.myAccount.changePassword("nope", NEW_PASSWORD, NEW_PASSWORD)
    settle(services)
    assert home.myAccount.errors["usernamePassword"] == "The current password is incorrect."
    assert home.myAccount.errors["currentPassword"] == "The current password is incorrect."
    assert account_row(db)[0] == "mia.chen" and stored_password(db) == before


# ── 密碼 ────────────────────────────────────────────────────────────────────

def test_password_change_works_at_the_next_log_in_and_stores_only_a_hash(engine, services, db, password,
                                                                         no_network_on_gui_thread):
    home, _window = launch(engine, services)
    assert home.myAccount.changePassword(OLD_PASSWORD, NEW_PASSWORD, NEW_PASSWORD)
    assert home.myAccount.passwordBusy
    settle(services)
    stored = stored_password(db)
    assert stored.startswith("pbkdf2_sha256$600000$") and NEW_PASSWORD not in stored
    assert can_log_in(services, "mia.chen", NEW_PASSWORD) == STAFF_ID
    assert can_log_in(services, "mia.chen", OLD_PASSWORD) is None
    assert toasts(services.notifier)[0]["title"] == "Password changed"
    assert home.profile.state == "ready"  # 不登出，畫面照常


def test_mismatched_confirmation_is_shown_under_the_field_and_never_sent(engine, services, db, password,
                                                                        qml_warnings):
    home, window = launch(engine, services)
    page, view = open_settings(window)
    window.requestActivate()
    wait_until(window.isActive)
    before = stored_password(db)
    press(find(view, "changePasswordButton"))
    assert qml(view, "openForm") == "password"
    type_into(view, "currentPasswordField", OLD_PASSWORD)
    type_into(view, "newPasswordField", NEW_PASSWORD)
    type_into(view, "confirmPasswordField", NEW_PASSWORD + "!")
    press(find(find(view, "passwordButtons"), "saveButton"))
    assert qml(find(view, "confirmPasswordField"), "error") == "The new passwords don't match."
    assert qml(find(view, "confirmPasswordField"), "input.activeFocus")  # 焦點移到那一格
    assert not home.myAccount.passwordBusy and stored_password(db) == before
    # 密碼欄預設看不到；Show 之後看得到，表單關掉又藏起來
    assert qml(find(view, "newPasswordField"), "input.echoMode === TextInput.Password")
    press(find(view, "showPasswordsButton"))
    assert qml(find(view, "newPasswordField"), "input.echoMode === TextInput.Normal")
    QTest.keyClick(window, Qt.Key.Key_Escape)  # Esc：取消
    assert qml(view, "openForm") == "" and not qml(view, "passwordShown")
    assert [qml(find(view, name), "text") for name in ("currentPasswordField", "newPasswordField",
                                                       "confirmPasswordField")] == ["", "", ""]
    assert home.myAccount.errors["confirmPassword"] == ""


def test_a_stale_verification_cannot_overwrite_a_newer_password(engine, services, db, password, monkeypatch):
    """驗證完、寫入前，另一台電腦把密碼改了：這次不寫入，請使用者再試一次；另一台的新密碼留著。"""
    home, _window = launch(engine, services)
    other = hash_password("from another mac")
    real_hash = account.hash_password

    def meanwhile(new_password):  # 在 db lane 上、驗證之後才算新的雜湊：這時另一台電腦改了密碼
        db.execute("UPDATE tbl_account SET account_password = %s WHERE account_id = %s", (other, STAFF_ID))
        return real_hash(new_password)

    monkeypatch.setattr(account, "hash_password", meanwhile)
    assert home.myAccount.changePassword(OLD_PASSWORD, NEW_PASSWORD, NEW_PASSWORD)
    settle(services)
    assert home.myAccount.errors["passwordForm"] == (
        "Your account was changed on another computer. Check your details and try again.")
    assert stored_password(db) == other
    assert can_log_in(services, "mia.chen", "from another mac") == STAFF_ID
    assert can_log_in(services, "mia.chen", NEW_PASSWORD) is None
    assert toasts(services.notifier) == []


def test_two_changes_with_the_same_stale_hash_only_the_first_wins(db, password):
    """預存程序本身的條件更新：兩個人都驗證了同一個雜湊，先到的寫入（changed 1），後到的 changed 0。"""
    stale = stored_password(db)
    first, second = hash_password("first"), hash_password("second")
    results = []
    for new_hash in (first, second):
        with db.connection.cursor() as cursor:
            cursor.execute("CALL sp_change_account_password(%s, %s, %s)", (STAFF_ID, stale, new_hash))
            results.append(cursor.fetchone()[0])
            while cursor.nextset():
                pass
    assert results == [1, 0] and stored_password(db) == first
    with db.connection.cursor() as cursor:
        cursor.execute("CALL sp_change_account_username(%s, %s, %s)", (STAFF_ID, stale, "late"))
        assert cursor.fetchone()[0] == 0
        while cursor.nextset():
            pass
    assert account_row(db)[0] == "mia.chen"


def test_double_submit_sends_one_change(engine, services, db, password):
    home, _window = launch(engine, services)
    gate = threading.Event()
    services.tasks.submit("db", "test:hold", lambda token, report: gate.wait(10))  # db lane 先卡住
    assert home.myAccount.changePassword(OLD_PASSWORD, NEW_PASSWORD, NEW_PASSWORD)
    assert not home.myAccount.changePassword(OLD_PASSWORD, "second", "second")  # 還在處理：擋下來
    assert home.myAccount.rename("Mia Lin") and not home.myAccount.rename("Mia Lee")
    gate.set()
    settle(services)
    assert can_log_in(services, "mia.chen", NEW_PASSWORD) == STAFF_ID
    assert account_row(db)[1] == "Mia Lin"


def test_offline_changes_nothing_and_the_form_can_retry(engine, services, db, password, monkeypatch,
                                                        no_network_on_gui_thread):
    home, _window = launch(engine, services)
    before = stored_password(db)
    restore = cut_database(services, db, monkeypatch)
    offline = "Can't reach the database. Nothing was changed. Check your network and try again."
    assert home.myAccount.rename("Mia Lin")
    assert home.myAccount.changePassword(OLD_PASSWORD, NEW_PASSWORD, NEW_PASSWORD)
    assert home.myAccount.changeUsername("mia.lin", OLD_PASSWORD)
    settle(services)
    assert [home.myAccount.errors[field] for field in ("nameForm", "passwordForm", "usernameForm")] == [offline] * 3
    assert account_row(db)[:2] == ("mia.chen", "Mia Chen") and stored_password(db) == before
    assert home.profile.name == "Mia Chen"

    restore()  # 連線恢復：同一個表單再按一次 Save
    assert home.myAccount.rename("Mia Lin")
    settle(services)
    assert account_row(db)[1] == "Mia Lin" and home.myAccount.errors["nameForm"] == ""


# ── 表單：一次一個、取消 ─────────────────────────────────────────────────────

@SIDES
def test_account_summary_and_one_form_at_a_time(engine, services, db, password, qml_warnings, home_init, account_id,
                                                page):
    home, window = launch(engine, services, home_init, account_id)
    _page, view = open_settings(window, page)
    assert find(view, "roleRow").property("value") == ("Staff" if account_id == STAFF_ID else "Admin · level 5")
    assert find(view, "passwordRow").property("value") == "••••••••"  # 不讀回密碼
    assert qml(find(view, "passwordRow"), "Accessible.name") == "Password: Hidden"
    press(find(view, "editNameButton"))
    type_into(view, "nameField", "draft")
    press(find(view, "editUsernameButton"))  # 換開另一個：前一個收起、內容清掉
    assert qml(view, "openForm") == "username"
    assert not find(view, "nameForm").property("visible") and find(view, "usernameForm").property("visible")
    assert find(view, "nameField").property("text") == ""
    type_into(view, "usernamePasswordField", "secret")
    press(find(find(view, "usernameButtons"), "cancelButton"))
    assert qml(view, "openForm") == "" and find(view, "usernamePasswordField").property("text") == ""
    assert not find(view, "usernameForm").property("visible")
    for name in ("editNameButton", "editUsernameButton", "changePasswordButton"):  # 至少 44 高
        assert find(view, name).property("height") >= 44


def test_profile_error_in_settings_offers_retry(engine, services, db, monkeypatch, qml_warnings):
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    home = server_home_init.launch(engine, STAFF_ID, services)
    window = engine.rootObjects()[-1]
    wait_until(lambda: home.profile.state == "error")
    _page, view = open_settings(window)
    error = find(view, "profileError")
    assert error.property("visible") and error.property("title") == "Your account details didn't load"
    assert not find(view, "editNameButton").property("enabled")
    assert find(view, "nameRow").property("value") == "Loading…"
    monkeypatch.setenv("DB_PORT", port)
    qml(error, "retry()")
    wait_until(lambda: home.profile.state == "ready")
    assert not error.property("visible") and find(view, "editNameButton").property("enabled")


# ── 照片 ────────────────────────────────────────────────────────────────────

def test_photo_upload_replace_and_remove(engine, services, db, password, s3, tmp_path, qml_warnings,
                                         no_network_on_gui_thread):
    home, window = launch(engine, services)
    _page, view = open_settings(window)
    header = qml(window, "header")
    assert not find(header, "headerPhoto").property("visible") and find(header, "headerAvatarIcon").property("visible")

    # 第一張：手機直拍（EXIF 要轉 90 度，還有 GPS）
    choose_and_save(home, photo(tmp_path / "phone.jpg", 600, 400, exif_orientation=6), services)
    [first] = s3_keys(s3)
    assert first.startswith(f"avatars/{STAFF_ID}-") and len(first) == len(f"avatars/{STAFF_ID}-20261101093005.jpg")
    assert account_row(db)[2] == first
    data, image = s3_image(s3, first)
    assert (image.width(), image.height()) == (256, 256)
    assert b"Exif" not in data and b"\xff\xe1" not in data  # EXIF（含拍攝地點）沒有跟過去
    assert s3.head_object(Bucket="adam-test", Key=first)["ContentType"] == "image/jpeg"
    assert home.profile.photoSource.startswith("file://") and home.profile.hasPhoto
    wait_until(lambda: find(header, "headerPhoto").property("visible"))  # 頁首換成照片
    assert find(view, "removePhotoButton").property("visible")
    assert toasts(services.notifier)[0]["title"] == "Photo updated"

    # 換一張：新的 key，舊的檔案刪掉
    choose_and_save(home, photo(tmp_path / "second.jpg", colours=("green", "yellow")), services)
    wait_until(lambda: len(s3_keys(s3)) == 1 and s3_keys(s3)[0] != first)
    [second] = s3_keys(s3)
    assert account_row(db)[2] == second and second > first

    # 移除：先清資料庫，再刪檔案；頁首回到預設的圖示
    assert home.myAccount.removePhoto()
    settle(services)
    wait_until(lambda: s3_keys(s3) == [])
    assert account_row(db)[2] is None and not home.profile.hasPhoto and home.profile.photoSource == ""
    wait_until(lambda: find(header, "headerAvatarIcon").property("visible"))
    assert toasts(services.notifier)[0]["title"] == "Photo removed"


def test_oversized_and_non_image_files_are_rejected(engine, services, db, password, s3, tmp_path):
    home, _window = launch(engine, services)
    big = tmp_path / "big.jpg"
    big.write_bytes(photo(tmp_path / "small.jpg").read_bytes() + os.urandom(account.PHOTO_MAX_BYTES))
    text = tmp_path / "notes.jpg"
    text.write_text("not a photo")
    for path, message in ((big, "This photo is larger than 5 MB. Choose a smaller one."),
                          (text, "This file isn't a photo we can open. Choose a JPEG, PNG or HEIC image.")):
        assert home.myAccount.choosePhoto(f"file://{path}")
        settle(services)
        assert home.myAccount.errors["photo"] == message and not home.myAccount.hasPendingPhoto
    assert s3_keys(s3) == [] and account_row(db)[2] is None


def test_upload_failure_keeps_the_old_photo_and_the_preview(engine, services, db, password, s3, tmp_path,
                                                             monkeypatch):
    home, _window = launch(engine, services)
    choose_and_save(home, photo(tmp_path / "first.jpg"), services)
    [first] = s3_keys(s3)
    source = home.profile.photoSource

    def offline(*args, **kwargs):
        raise botocore.exceptions.EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com")

    real_upload = storage.upload_with_progress
    monkeypatch.setattr(storage, "upload_with_progress", offline)
    home.myAccount.choosePhoto(f"file://{photo(tmp_path / 'second.jpg', colours=('green', 'yellow'))}")
    settle(services)
    assert home.myAccount.savePhoto()
    settle(services)
    assert home.myAccount.errors["photo"] == "Can't reach cloud storage. Your current photo is unchanged."
    assert s3_keys(s3) == [first] and account_row(db)[2] == first and home.profile.photoSource == source
    assert home.myAccount.hasPendingPhoto  # 預覽留著：網路好了再按 Save

    monkeypatch.setattr(storage, "upload_with_progress", real_upload)  # 網路好了
    assert home.myAccount.savePhoto()
    settle(services)
    wait_until(lambda: len(s3_keys(s3)) == 1 and s3_keys(s3)[0] != first)
    assert account_row(db)[2] == s3_keys(s3)[0] and home.myAccount.errors["photo"] == ""


def test_database_failure_after_upload_deletes_the_new_file(engine, services, db, password, s3, tmp_path,
                                                             monkeypatch):
    home, _window = launch(engine, services)
    choose_and_save(home, photo(tmp_path / "first.jpg"), services)
    [first] = s3_keys(s3)

    def lost(*args, **kwargs):
        raise mysql.connector.Error(msg="Lost connection to MySQL server during query", errno=2013)

    monkeypatch.setattr(account, "swap_photo_key", lost)
    home.myAccount.choosePhoto(f"file://{photo(tmp_path / 'second.jpg', colours=('green', 'yellow'))}")
    settle(services)
    assert home.myAccount.savePhoto()
    settle(services)
    assert home.myAccount.errors["photo"] == "Can't reach the database. Your current photo is unchanged."
    wait_until(lambda: s3_keys(s3) == [first])  # 剛上傳的新檔案刪掉了
    assert account_row(db)[2] == first


def test_photo_is_downloaded_at_launch_and_a_missing_file_falls_back_to_the_icon(engine, services, db, s3,
                                                                                 tmp_path, qml_warnings):
    key = f"avatars/{STAFF_ID}-20261031120000.jpg"
    s3.put_object(Bucket="adam-test", Key=key, Body=photo(tmp_path / "me.jpg").read_bytes())
    db.execute("UPDATE tbl_account SET account_photo_key = %s WHERE account_id = %s", (key, STAFF_ID))
    home, window = launch(engine, services)
    wait_until(lambda: home.profile.photoState == "ready")
    header = qml(window, "header")
    wait_until(lambda: find(header, "headerPhoto").property("visible"))
    png = home.profile.photoSource.removeprefix("file://")
    assert os.path.exists(png) and os.path.dirname(png).endswith("avatars")
    image = QImage(png)
    assert image.hasAlphaChannel() and image.pixelColor(2, 2).alpha() == 0  # 圓形，外面透明

    # Quit 的 clearDir() 把照片的快取一起清掉（media.clear_cache）
    home.clearDir()
    assert os.listdir(os.path.dirname(png)) == []

    # 另一個帳號的照片已經不在 S3 上：頁首退回預設的圖示，不顯示錯誤
    db.execute("UPDATE tbl_account SET account_photo_key = %s WHERE account_id = %s",
               ("avatars/gone-20261031120000.jpg", ADMIN_ID))
    admin, admin_window = launch(engine, services, admin_home_init, ADMIN_ID)
    wait_until(lambda: admin.profile.photoState == "error")
    admin_header = qml(admin_window, "header")
    assert admin.profile.photoSource == "" and find(admin_header, "headerAvatarIcon").property("visible")
    assert toasts(services.notifier) == []


def test_photo_preview_in_settings(engine, services, db, password, s3, tmp_path, qml_warnings):
    home, window = launch(engine, services)
    _page, view = open_settings(window)
    assert find(view, "choosePhotoButton").property("text") == "Choose…"
    assert not find(view, "removePhotoButton").property("visible")  # 沒有照片：沒有 Remove
    home.myAccount.choosePhoto(f"file://{photo(tmp_path / 'me.jpg')}")
    settle(services)
    assert find(view, "savePhotoButton").property("visible") and find(view, "discardPhotoButton").property("visible")
    assert find(view, "choosePhotoButton").property("text") == "Choose Another…"
    wait_until(lambda: find(view, "photoImage").property("visible"))
    assert qml(find(view, "photoPreview"), "Accessible.name") == "New photo preview"
    press(find(view, "discardPhotoButton"))
    assert not home.myAccount.hasPendingPhoto and not find(view, "savePhotoButton").property("visible")
    assert s3_keys(s3) == [] and account_row(db)[2] is None


# ── 同名的服務人員（PLAN 13.7）────────────────────────────────────────────────

def test_staff_with_the_same_name_stay_apart_in_services_and_the_ranking(engine, services, db,
                                                                         no_network_on_gui_thread):
    twin = "00000000-0000-0000-0000-000000000003"
    db.execute("UPDATE tbl_account SET account_name = 'Mia Chen', account_username = 'mia.chen' "
               "WHERE account_id = %s", (STAFF_ID,))
    db.add_account(twin, "mia.lee", "Mia Chen")
    today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    db.add_service("S20990101090000", f"{today:%Y-%m-%d %H:%M:%S}")
    db.add_scores("S20990101090000", customer=90, server=88)
    db.add_service("S20990101100000", f"{today.replace(hour=10):%Y-%m-%d %H:%M:%S}", account_id=twin)
    db.add_scores("S20990101100000", customer=70, server=72)

    home, _window = launch(engine, services, admin_home_init, ADMIN_ID)
    manager = home.serviceManager
    manager.loadNames()
    wait_until(lambda: manager.namesState == "ready")
    assert manager.names == ["Mia Chen (mia.chen)", "Mia Chen (mia.lee)"]
    assert manager.staffIds == [STAFF_ID, twin]
    for staff_id, service_id in ((STAFF_ID, "S20990101090000"), (twin, "S20990101100000")):
        assert manager.search(staff_id, "", "")
        wait_until(lambda: manager.state == "ready" and not manager.refreshing)
        assert [row["serviceId"] for row in manager.listModel.rows()] == [service_id]
    assert manager.lastQuery == {"staffId": twin, "start": "", "end": ""}
    assert manager.staffText == "Mia Chen (mia.lee)"

    ranking = home.homeDashboard.ranking
    wait_until(lambda: ranking.state == "ready")
    assert [(row["accountId"], row["label"], row["count"]) for row in ranking.byCustomer] == [
        (STAFF_ID, "Mia Chen (mia.chen)", 1), (twin, "Mia Chen (mia.lee)", 1)]


# ── 設定頁的 Clear Download Cache（S5，T40）──────────────────────────────────

def test_clearing_the_download_cache_waits_for_photo_work_and_downloads_the_photo_again(
        engine, services, db, password, s3, tmp_path, monkeypatch):
    home, _window = launch(engine, services)
    choose_and_save(home, photo(tmp_path / "me.jpg"), services)
    png = home.profile.photoSource.removeprefix("file://")
    assert os.path.exists(png)
    cache = home.helpCenter.storage
    assert cache.refresh()
    wait_until(lambda: cache.state == "ready" and not cache.scanning)
    assert cache.canClear

    # 照片處理中（account:self:photo:prepare）：快取正在被用，不能清
    gate, real_prepare = threading.Event(), account.prepare_photo
    monkeypatch.setattr(account, "prepare_photo", lambda path: gate.wait(10) and real_prepare(path))
    assert home.myAccount.choosePhoto(f"file://{photo(tmp_path / 'next.jpg', colours=('green', 'yellow'))}")
    wait_until(lambda: cache.inUse)
    assert not cache.canClear and not cache.clear()
    gate.set()
    settle(services)
    assert home.myAccount.hasPendingPhoto and not cache.inUse

    assert cache.clear()
    wait_until(lambda: not cache.clearing)
    assert not home.myAccount.hasPendingPhoto  # 預覽的檔案被清掉了：不留一個打不開的預覽
    wait_until(lambda: os.path.exists(png) and home.profile.photoState == "ready")  # 目前的照片重新下載
    assert home.profile.photoSource == f"file://{png}"
