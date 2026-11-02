"""登入的人自己的帳號，服務人員端和管理員端共用（new-feature N3、S6，T36）。

- AccountProfile：名字、登入帳號、等級、職稱、照片。launch() 時在 db lane 載入（1 個往返，和以前只載名字一樣），
  有照片時在 io lane 下載到本機的快取（media.avatar_cache_dir()，Quit 時清掉）。
  頁首（PageHeader 的名字和頭像）、設定頁的 Account 區塊、新增帳號頁的等級上限都讀它；
  資料變了發 profileChanged／photoChanged，畫面的綁定跟著更新。
- AccountSelfService（兩端的 context property myAccount）：自己改顯示名稱、登入帳號、密碼、照片。
  一律用登入時的 account_id，沒有任何參數可以指定別的帳號；不經過管理員的 AccountManager.updateAccount()
  （它接受任意的 id、等級、密碼，PLAN 13.7）。每一種修改各有自己的 TaskRunner key：重複按 Save 不會送兩次。

改登入帳號、密碼（PLAN 13.7）：
1. db lane 上 CALL sp_get_account_password 取回目前的雜湊（adam_app 沒有 account_password 的 SELECT），
   用 passwords.verify_password() 比對使用者打的目前密碼；新密碼用 hash_password()（PBKDF2，含隨機 salt）。
2. CALL sp_change_account_password／sp_change_account_username：雜湊還是剛才驗證過的那一個才寫入，回傳 changed。
   0 表示這段期間在別處改過（ChangedElsewhere）：不拿過期的驗證覆寫新的值，請使用者再試一次。
3. 帳號重複由資料庫的唯一鍵（uk_account_username，定序不分大小寫）擋下（1062），不在畫面上預先查。
- 密碼不 trim，不寫進 QSettings、log、toast、錯誤的技術細節；表單由 QML 在關閉、成功時清空。
- 改登入帳號、密碼之後不登出，也不打斷錄影和上傳：下次登入時用新的。

照片（PLAN 第 6 節）：
- Choose…：io lane 上用 Qt 處理（不加新的套件）：QImageReader（setAutoTransform，照 EXIF 轉正）、
  原檔超過 5 MB 或不是圖片就拒絕、裁成置中的正方形、縮成 256×256、鋪在白底上、用 QImageWriter 存成 JPEG。
  重畫在新的 QImage 上，原檔的 EXIF（可能有拍攝地點）不會跟過去。另外存一張圓形的 PNG 給畫面顯示
  （QML 不用 shader 就能畫圓形頭像，offscreen 的測試也畫得出來）。
- Save：io lane 上傳到 S3 的 avatars/{account_id}-{YYYYMMDDHHMMSS}.jpg，成功後 db lane 把 account_photo_key 換成新的
  （條件是還是畫面上那一個，不然重讀一次再換，換掉的就是要刪的那一個），兩個都成功才在 io lane 刪舊的檔案；
  刪不掉只記 log。上傳失敗時原本的照片不動；寫資料庫失敗時刪掉剛上傳的檔案。
- Remove：先把欄位設成 NULL，成功後才刪 S3 上的檔案。
"""
import logging
import os
import re
import shutil
import uuid
from contextlib import suppress
from datetime import datetime, timedelta

from PySide6.QtCore import QCoreApplication, QObject, QUrl, Property, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QImage, QImageReader, QImageWriter, QPainter

from myapp import storage
from myapp.media import avatar_cache_dir
from myapp.notifications import user_message
from myapp.passwords import hash_password, verify_password

logger = logging.getLogger(__name__)

# 區塊的狀態（DS-25～27），和管理員端的 _Section 一樣
LOADING, ERROR, READY = "loading", "error", "ready"
# 照片的狀態：none（沒有照片）、loading（下載中）、ready、error（下載失敗：頁首退回預設的圖示）
PHOTO_NONE = "none"

NAME_MAX = 100          # tbl_account.account_name VARCHAR(100)
USERNAME_MAX = 64       # tbl_account.account_username VARCHAR(64)
AVATAR_SIZE = 256
PHOTO_MAX_BYTES = 5 * 1024 * 1024
DECODE_MAX_SIDE = 2048  # 很大的照片先在解碼時縮到這麼大（記憶體、速度），最後一樣是 256×256
JPEG_QUALITY = 90

# 帳號資料：1 個往返（以前服務人員端只 CALL sp_get_account_name 拿名字，也是 1 個）
PROFILE_SQL = ("SELECT account_name, account_username, account_level, account_occupation_name, account_photo_key "
               "FROM tbl_account WHERE account_id = %s")
RENAME_SQL = "UPDATE tbl_account SET account_name = %s WHERE account_id = %s"
EXISTS_SQL = "SELECT 1 FROM tbl_account WHERE account_id = %s"
# 照片：條件更新（<=> 連 NULL 也能比）；adam_app 有 account_photo_key 的 SELECT，不必經過預存程序
PHOTO_SQL = "UPDATE tbl_account SET account_photo_key = %s WHERE account_id = %s AND account_photo_key <=> %s"
PHOTO_KEY_SQL = "SELECT account_photo_key FROM tbl_account WHERE account_id = %s"


def _tr(text, *values):
    """介面上的英文（PLAN D1）：經過 translate；%1、%2… 一次換成 values（像 QString::arg）。"""
    text = QCoreApplication.translate("account", text)
    return re.sub(r"%(\d)", lambda match: str(values[int(match[1]) - 1]), text) if values else text


# ---------------------------------------------------------------- 例外


class AccountGone(LookupError):
    """登入的帳號已經不在資料庫裡（被刪掉了）。"""


class WrongPassword(Exception):
    """使用者打的目前密碼不對。"""


class ChangedElsewhere(Exception):
    """驗證之後、寫入之前，密碼（或照片）在別處改過了：這次不寫入。"""


class PhotoError(Exception):
    """選的檔案不能當照片。reason：too_large、not_image、unreadable。"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- 資料庫（都在 db lane；db 是 DbLane）


def load_profile(db, account_id):
    """{account_name, account_username, account_level, account_occupation_name, account_photo_key}；沒有這個帳號是 None。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(PROFILE_SQL, (account_id,))
        return cursor.fetchone()


def rename_account(db, account_id, name):
    """改顯示名稱（1 個往返）。0 列時多查一次：名字本來就一樣是成功，帳號不見了是 AccountGone。"""
    with db.cursor() as cursor:
        cursor.execute(RENAME_SQL, (name, account_id))
        if cursor.rowcount:
            return
        cursor.execute(EXISTS_SQL, (account_id,))
        if cursor.fetchone() is None:
            raise AccountGone(account_id)


def _verified_hash(cursor, account_id, password):
    """目前的密碼雜湊；password 不對丟 WrongPassword，帳號不見了丟 AccountGone。"""
    cursor.execute("CALL sp_get_account_password(%s)", (account_id,))
    row = cursor.fetchone()
    while cursor.nextset():  # CALL 多一個結果集：讀完才能送下一個指令
        pass
    if row is None:
        raise AccountGone(account_id)
    stored = row[0]
    if not verify_password(password, stored):
        raise WrongPassword()
    return stored if isinstance(stored, str) else bytes(stored).decode("utf-8")


def _changed(cursor):
    row = cursor.fetchone()
    while cursor.nextset():
        pass
    if not row or not int(row[0]):
        raise ChangedElsewhere()


def change_password(db, account_id, current_password, new_password):
    """驗證目前的密碼，再把雜湊換成新的（2 個往返）。驗證之後密碼在別處改過時丟 ChangedElsewhere。"""
    with db.cursor() as cursor:
        expected = _verified_hash(cursor, account_id, current_password)
        new_hash = hash_password(new_password)
        cursor.execute("CALL sp_change_account_password(%s, %s, %s)", (account_id, expected, new_hash))
        _changed(cursor)


def change_username(db, account_id, new_username, current_password):
    """驗證目前的密碼，再換登入帳號（2 個往返）。帳號重複時 mysql.connector 丟 1062（uk_account_username）。"""
    with db.cursor() as cursor:
        expected = _verified_hash(cursor, account_id, current_password)
        cursor.execute("CALL sp_change_account_username(%s, %s, %s)", (account_id, expected, new_username))
        _changed(cursor)


def swap_photo_key(db, account_id, new_key, expected_key, attempts=3):
    """把 account_photo_key 換成 new_key（None＝移除），回傳被換掉的舊 key（要刪的那一個）。

    條件更新：欄位還是 expected_key 才換（通常 1 個往返）。在別處換過時讀出現在的值，用它再換一次，
    所以回傳的一定是真的被這次換掉的 key，不會刪錯、也不會漏刪。一直搶不到丟 ChangedElsewhere。
    """
    with db.cursor() as cursor:
        for _ in range(attempts):
            cursor.execute(PHOTO_SQL, (new_key, account_id, expected_key))
            if cursor.rowcount:
                return expected_key
            cursor.execute(PHOTO_KEY_SQL, (account_id,))
            row = cursor.fetchone()
            if row is None:
                raise AccountGone(account_id)
            if row[0] == new_key:  # 已經是這個值了（UPDATE 沒有改到任何東西）：沒有要刪的
                return None
            expected_key = row[0]
    raise ChangedElsewhere()


# ---------------------------------------------------------------- 照片的檔案（都在 io lane）


def avatar_key(account_id, moment=None):
    """S3 的 key：avatars/{account_id}-{YYYYMMDDHHMMSS}.jpg（PLAN 第 6 節）。"""
    return f"avatars/{account_id}-{(moment or datetime.now()):%Y%m%d%H%M%S}.jpg"


def avatar_files(key):
    """這個 key 在本機快取的兩個檔案：(JPEG, 圓形的 PNG)。檔名取 key 最後一段，不會跑出快取資料夾。"""
    base = os.path.splitext(os.path.basename(key))[0]
    folder = avatar_cache_dir()
    return os.path.join(folder, base + ".jpg"), os.path.join(folder, base + ".png")


def square_photo(image, size=AVATAR_SIZE):
    """置中裁成正方形、縮成 size×size、鋪在白底上（透明的 PNG 存成 JPEG 才不會變黑）。回傳新的 QImage（沒有原檔的中繼資料）。"""
    side = min(image.width(), image.height())
    cropped = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    scaled = cropped.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    canvas = QImage(size, size, QImage.Format.Format_RGB32)
    canvas.fill(Qt.GlobalColor.white)
    painter = QPainter(canvas)
    painter.drawImage(0, 0, scaled)
    painter.end()
    return canvas


def round_photo(image, size=AVATAR_SIZE):
    """圓形、外面透明的版本（頁首和設定頁顯示用）：用照片當筆刷畫一個反鋸齒的圓。"""
    if image.width() != size or image.height() != size:
        image = square_photo(image, size)
    rounded = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(image))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return rounded


def _save(image, path, image_format, quality=-1):
    """先寫暫存檔、成功才改名：中途失敗不會留下半張圖。"""
    temp = f"{path}.{uuid.uuid4().hex}.part"
    writer = QImageWriter(temp, image_format)
    writer.setQuality(quality)
    try:
        if not writer.write(image):
            raise OSError(writer.errorString())
        os.replace(temp, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.remove(temp)
        raise
    return path


def prepare_photo(source, folder=None):
    """把使用者選的檔案做成上傳用的 JPEG 和顯示用的圓形 PNG（在快取資料夾，檔名是 pending-<隨機>），回傳 (jpg, png)。

    超過 5 MB、不是圖片、讀不到時丟 PhotoError。
    """
    try:
        size = os.path.getsize(source)
    except OSError as exc:
        raise PhotoError("unreadable") from exc
    if size > PHOTO_MAX_BYTES:
        raise PhotoError("too_large")
    reader = QImageReader(source)
    reader.setAutoTransform(True)  # 手機直拍的照片 EXIF 寫「轉 90 度」：照它轉正，再丟掉 EXIF
    stored = reader.size()  # 還沒轉正的大小；等比例縮，轉不轉都一樣
    if stored.isValid() and max(stored.width(), stored.height()) > DECODE_MAX_SIDE:
        reader.setScaledSize(stored.scaled(DECODE_MAX_SIDE, DECODE_MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise PhotoError("not_image")
    folder = folder or avatar_cache_dir()
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, f"pending-{uuid.uuid4().hex}")
    square = square_photo(image)
    jpg = _save(square, base + ".jpg", b"jpeg", JPEG_QUALITY)
    png = _save(round_photo(square), base + ".png", b"png")
    return jpg, png


def fetch_avatar(key, token=None):
    """這個 key 的圓形 PNG：快取裡有就直接用，沒有就從 S3 下載 JPEG 再做。回傳 PNG 的路徑。"""
    jpg, png = avatar_files(key)
    if os.path.exists(png):
        return png
    os.makedirs(os.path.dirname(png), exist_ok=True)
    if not os.path.exists(jpg):
        storage.download_with_progress(key, jpg, token)
    image = QImage(jpg)
    if image.isNull():
        raise PhotoError("not_image")
    return _save(round_photo(image), png, b"png")


def upload_photo(pending, key, token=None):
    """上傳 pending 的 JPEG 到 key。先把兩個檔案複製成這個 key 的快取：存好之後頁首直接用，不必再下載。"""
    jpg, png = pending
    final_jpg, final_png = avatar_files(key)
    shutil.copyfile(jpg, final_jpg)
    shutil.copyfile(png, final_png)
    storage.upload_with_progress(final_jpg, key, token)
    return key


def remove_files(paths):
    for path in paths:
        with suppress(FileNotFoundError):
            os.remove(path)


def clean_up(old_key=None, local=()):
    """換掉或移除照片之後：刪 S3 上的舊檔和本機的舊快取。失敗只記 log（PLAN：刪不掉不顯示）。"""
    if old_key:
        try:
            storage.delete(old_key)
        except Exception as exc:
            logger.warning("deleting the old photo %s failed: %r", old_key, exc)
        local = (*local, *avatar_files(old_key))
    remove_files(local)


def file_url(path):
    return QUrl.fromLocalFile(path).toString() if path else ""


# ---------------------------------------------------------------- 帳號資料


class AccountProfile(QObject):
    """登入的人的名字、登入帳號、等級、職稱、照片（兩端共用）。

    - state：loading（還沒有資料）、ready、error；已經有資料時重新載入，refreshing 為 true（DS-25）。
      錯誤：errorDetail 是給人看的原因，errorTechnical 收在「Details」裡，failureCount 是連續失敗的次數。
    - key：TaskRunner 的 key（管理員端 admin:profile、服務人員端 staff:account-name，沿用以前的 key）。
    - retry_action：不是空的時，載入失敗跳一則附 Retry 的通知（管理員端：名字和等級是整個管理員端都要用的）。
    - 照片：photoSource 是本機圓形 PNG 的 file:// URL，沒有照片或下載失敗是空字串（頁首退回預設的圖示）。
    """

    stateChanged = Signal()
    profileChanged = Signal()
    photoChanged = Signal()

    def __init__(self, services, account_id, key, *, retry_action="", parent=None):
        super().__init__(parent)
        self.services = services
        self.account_id = account_id
        self.key = key
        self._retry_action = retry_action
        self._state = LOADING
        self._refreshing = False
        self._error_detail = self._error_technical = ""
        self._failures = 0
        self._toast_id = 0
        self._name = self._username = self._job_title = ""
        self._level = 0  # 0＝還不知道
        self._photo_key = None
        self._photo_state = PHOTO_NONE
        self._photo_source = ""
        if retry_action and services is not None:
            services.notifier.actionTriggered.connect(self._on_action)

    # ------------------------------------------------------------ QML 讀的屬性

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(bool, notify=stateChanged)
    def refreshing(self):
        return self._refreshing

    @Property(str, notify=stateChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=stateChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=stateChanged)
    def failureCount(self):
        return self._failures

    @Property(str, notify=profileChanged)
    def name(self):
        return self._name

    @Property(str, notify=profileChanged)
    def username(self):
        return self._username

    @Property(int, notify=profileChanged)
    def level(self):
        return self._level

    @Property(str, notify=profileChanged)
    def jobTitle(self):
        return self._job_title

    @Property(bool, notify=photoChanged)
    def hasPhoto(self):
        return bool(self._photo_key)

    @Property(str, notify=photoChanged)
    def photoState(self):
        return self._photo_state

    @Property(str, notify=photoChanged)
    def photoSource(self):
        return self._photo_source

    @property
    def photo_key(self):
        return self._photo_key

    # ------------------------------------------------------------ 載入

    @Slot(result=bool)
    def load(self):
        """在 db lane 載入（1 個往返）。回傳是否送出（同一個 key 已經在載入時不重複送）。"""
        db, account_id = self.services.db, self.account_id
        submitted = self.services.tasks.submit("db", self.key, lambda token, report: load_profile(db, account_id),
                                               on_ok=self._loaded, on_err=self._failed)
        if submitted:
            if self._state == READY:
                self._set_state(refreshing=True)
            else:
                self._set_state(state=LOADING)
        return submitted

    def _loaded(self, row):
        if row is None:  # 帳號被刪掉了：當成錯誤，不要讓等級變成 0 卻看起來正常
            self._failed(AccountGone(self.account_id))
            return
        self.update_fields(name=row["account_name"] or "", username=row.get("account_username") or "",
                           level=int(row["account_level"]), job_title=row.get("account_occupation_name") or "")
        self._failures = 0
        self._set_state(state=READY, refreshing=False, detail="", technical="", force=True)
        if self._toast_id:
            self.services.notifier.dismiss(self._toast_id)
            self._toast_id = 0
        self.set_photo(row.get("account_photo_key") or None)

    def _failed(self, exc):
        logger.warning("loading account %s failed: %r", self.account_id, exc)
        self._failures += 1
        _, title, detail, _ = user_message(exc)
        if isinstance(exc, AccountGone):
            title, detail = _tr("Your account wasn't found"), _tr("Log in again.")
        text = str(exc)
        self._set_state(state=ERROR, refreshing=False, detail=f"{title}. {detail}" if detail else title,
                        technical=f"{type(exc).__name__}: {text}" if text else type(exc).__name__, force=True)
        if self._retry_action:
            self._toast_id = self.services.notifier.fromException(exc, key=self.key, actionId=self._retry_action)

    def update_fields(self, name=None, username=None, level=None, job_title=None):
        """改了自己的資料之後（或載入時）更新；有變才發 profileChanged。"""
        before = (self._name, self._username, self._level, self._job_title)
        if name is not None:
            self._name = name
        if username is not None:
            self._username = username
        if level is not None:
            self._level = level
        if job_title is not None:
            self._job_title = job_title
        if before != (self._name, self._username, self._level, self._job_title):
            self.profileChanged.emit()

    def _set_state(self, state=None, refreshing=None, detail=None, technical=None, force=False):
        before = (self._state, self._refreshing, self._error_detail, self._error_technical)
        if state is not None:
            self._state = state
        if refreshing is not None:
            self._refreshing = refreshing
        if detail is not None:
            self._error_detail = detail
        if technical is not None:
            self._error_technical = technical
        if force or before != (self._state, self._refreshing, self._error_detail, self._error_technical):
            self.stateChanged.emit()

    # ------------------------------------------------------------ 照片

    def set_photo(self, key, local_png=None):
        """照片換成 key（None＝沒有照片）。local_png：已經在快取裡的圓形 PNG（剛存好的照片），不必再下載。"""
        key = key or None
        if key == self._photo_key and self._photo_state in (READY, LOADING) and local_png is None:
            return  # 同一張、已經有了或正在下載
        if key != self._photo_key:
            self._photo_key = key
            self.photoChanged.emit()  # hasPhoto；畫面的照片等下面換好再發一次
        if key is None:
            self.services.tasks.cancel(self._photo_task_key())
            self._set_photo(PHOTO_NONE, "")
        elif local_png is not None:
            self.services.tasks.cancel(self._photo_task_key())
            self._set_photo(READY, file_url(local_png))
        else:
            submitted = self.services.tasks.submit(
                "io", self._photo_task_key(), lambda token, report: fetch_avatar(key, token),
                on_ok=lambda path: self._photo_fetched(key, path), on_err=lambda exc: self._photo_failed(key, exc),
                replace=True)
            if submitted:  # 下載中：舊的照片先留著（換照片時頁首不會先閃成預設圖）
                self._set_photo(LOADING, self._photo_source)

    def refresh_photo(self):
        """下載快取被清掉之後（設定頁的 Clear Download Cache）：照片的檔案沒了，重新下載（畫面上的圖先留著）。"""
        if self._photo_key:
            self._photo_state = PHOTO_NONE  # 讓 set_photo 不當成「已經有了」
            self.set_photo(self._photo_key)

    def _photo_task_key(self):
        return f"avatar:{self.account_id}"  # local_storage.CACHE_TASK_PREFIXES 有 avatar:：下載中不能清快取

    def _photo_fetched(self, key, path):
        if key == self._photo_key:
            self._set_photo(READY, file_url(path))

    def _photo_failed(self, key, exc):
        logger.warning("loading the photo %s failed: %r", key, exc)
        if key == self._photo_key:
            self._set_photo(ERROR, "")

    def _set_photo(self, state, source):
        if (state, source) != (self._photo_state, self._photo_source):
            self._photo_state, self._photo_source = state, source
            self.photoChanged.emit()

    @Slot(str, int)
    def _on_action(self, action_id, toast_id):
        if action_id == self._retry_action:
            self.load()


# ---------------------------------------------------------------- 自己改帳號資料（設定頁的 Account 區塊）

FORM_FIELDS = {
    "name": ("name", "nameForm"),
    "username": ("username", "usernamePassword", "usernameForm"),
    "password": ("currentPassword", "newPassword", "confirmPassword", "passwordForm"),
    "photo": ("photo",),
}
ALL_FIELDS = tuple(field for fields in FORM_FIELDS.values() for field in fields)


class AccountSelfService(QObject):
    """設定頁 Account 區塊的動作（context property myAccount）。一律改登入的那個帳號（profile.account_id）。

    - rename(name)、changeUsername(newUsername, currentPassword)、changePassword(current, new, confirm)：
      先在 GUI 執行緒檢查欄位（不碰網路），錯誤寫進 errors[欄位]、焦點移到第一個有錯的欄位（focusRequested）；
      再送到 db lane。回傳是否已經送出（欄位有錯、或同一種修改還在進行中，回傳 False）。
      成功：toast、saved(form)（QML 收起表單、清空欄位）、accountEdited(form)（兩端重新整理清單）。
      表單層級的錯誤（連不上、在別處改過）寫在 errors.nameForm／usernameForm／passwordForm，表單留著可以再按 Save。
    - 照片：choosePhoto(url) → 預覽（previewSource）→ savePhoto() 或 discardPhoto()；removePhoto()。
      photoWork 是 ""、preparing、saving、removing；錯誤在 errors.photo。
    - errors 的欄位：name、nameForm、username、usernamePassword、usernameForm、currentPassword、newPassword、
      confirmPassword、passwordForm、photo。
    """

    busyChanged = Signal()
    errorsChanged = Signal()
    previewChanged = Signal()
    saved = Signal(str)           # name、username、password、photo、photoRemoved
    focusRequested = Signal(str)  # errors 的欄位名稱
    accountEdited = Signal(str)   # name、username、password、photo

    KEYS = {"name": "account:self:name", "username": "account:self:username", "password": "account:self:password"}
    PREPARE_KEY = "account:self:photo:prepare"
    UPLOAD_KEY = "account:self:photo:upload"
    PHOTO_DB_KEY = "account:self:photo:db"

    def __init__(self, services, profile, parent=None):
        super().__init__(parent)
        self.services = services
        self._profile = profile
        self._errors = dict.fromkeys(ALL_FIELDS, "")
        self._pending = None      # (jpg, png)：選好、處理好、還沒存的照片
        self._photo_work = ""
        self._used_keys = set()   # 這次登入用過的 S3 key：同一秒內再存一次也不會撞到（失敗時的清理不會刪到新的）
        services.tasks.busyChanged.connect(self._on_busy_changed)

    # ------------------------------------------------------------ QML 讀的屬性

    @Property(QObject, constant=True)
    def profile(self):
        return self._profile

    @Property("QVariantMap", notify=errorsChanged)
    def errors(self):
        return dict(self._errors)

    @Property(bool, notify=busyChanged)
    def nameBusy(self):
        return self.services.tasks.isBusy(self.KEYS["name"])

    @Property(bool, notify=busyChanged)
    def usernameBusy(self):
        return self.services.tasks.isBusy(self.KEYS["username"])

    @Property(bool, notify=busyChanged)
    def passwordBusy(self):
        return self.services.tasks.isBusy(self.KEYS["password"])

    @Property(str, notify=busyChanged)
    def photoWork(self):
        return self._photo_work

    @Property(str, notify=previewChanged)
    def previewSource(self):
        return file_url(self._pending[1]) if self._pending else ""

    @Property(bool, notify=previewChanged)
    def hasPendingPhoto(self):
        return self._pending is not None

    # ------------------------------------------------------------ 顯示名稱、登入帳號、密碼

    @Slot(str, result=bool)
    def rename(self, name):
        key = self.KEYS["name"]
        if self.services.tasks.isBusy(key):
            return False
        name = name.strip()
        error = _tr("Enter a name.") if not name else (
            _tr("Use %1 characters or fewer.", NAME_MAX) if len(name) > NAME_MAX else "")
        if not self._check("name", {"name": error}):
            return False
        if name == self._profile.name:  # 沒有改：直接收起表單，不寫資料庫
            self.saved.emit("name")
            return True
        db, account_id = self.services.db, self._profile.account_id
        return self.services.tasks.submit("db", key, lambda token, report: rename_account(db, account_id, name),
                                          on_ok=lambda result: self._renamed(name),
                                          on_err=lambda exc: self._form_failed("name", exc))

    @Slot(str, str, result=bool)
    def changeUsername(self, new_username, current_password):
        key = self.KEYS["username"]
        if self.services.tasks.isBusy(key):
            return False
        username = new_username.strip()
        error = _tr("Enter a username.") if not username else (
            _tr("Use %1 characters or fewer.", USERNAME_MAX) if len(username) > USERNAME_MAX else "")
        password_error = "" if current_password else _tr("Enter your current password.")
        if not self._check("username", {"username": error, "usernamePassword": password_error}):
            return False
        if username == self._profile.username:  # 沒有改
            self.saved.emit("username")
            return True
        db, account_id = self.services.db, self._profile.account_id
        return self.services.tasks.submit(
            "db", key, lambda token, report: change_username(db, account_id, username, current_password),
            on_ok=lambda result: self._username_changed(username),
            on_err=lambda exc: self._form_failed("username", exc))

    @Slot(str, str, str, result=bool)
    def changePassword(self, current_password, new_password, confirm_password):
        """密碼照原樣用（不 trim）；規則和新增帳號一樣：不能空白（AccountManager）。"""
        key = self.KEYS["password"]
        if self.services.tasks.isBusy(key):
            return False
        confirm_error = ""
        if new_password and not confirm_password:
            confirm_error = _tr("Enter the new password again.")
        elif new_password and confirm_password != new_password:
            confirm_error = _tr("The new passwords don't match.")
        if not self._check("password", {
                "currentPassword": "" if current_password else _tr("Enter your current password."),
                "newPassword": "" if new_password else _tr("Enter a new password."),
                "confirmPassword": confirm_error}):
            return False
        db, account_id = self.services.db, self._profile.account_id
        return self.services.tasks.submit(
            "db", key, lambda token, report: change_password(db, account_id, current_password, new_password),
            on_ok=lambda result: self._password_changed(), on_err=lambda exc: self._form_failed("password", exc))

    @Slot(str)
    def clearError(self, field):
        """使用者改了這個欄位：它的錯誤和表單的錯誤先拿掉（下次送出再檢查）。"""
        form = next((name for name, fields in FORM_FIELDS.items() if field in fields), None)
        if form is None:
            return
        changed = {field: ""}
        if form != "photo":
            changed[FORM_FIELDS[form][-1]] = ""
        self._set_errors(changed)

    @Slot(str)
    def clearForm(self, form):
        """打開、取消表單時：這個表單的錯誤都拿掉。欄位的內容由 QML 清空（密碼不留在 Python）。"""
        self._set_errors(dict.fromkeys(FORM_FIELDS.get(form, ()), ""))

    # ------------------------------------------------------------ 照片

    @Slot(str, result=bool)
    def choosePhoto(self, url):
        """FileDialog 選的檔案（file:// URL 或路徑）：在 io lane 處理成預覽。存檔、移除進行中時不接受。"""
        if self._photo_work in ("saving", "removing"):
            return False
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        self._set_errors({"photo": ""})
        submitted = self.services.tasks.submit(
            "io", self.PREPARE_KEY, lambda token, report: prepare_photo(path),
            on_ok=self._photo_prepared, on_err=self._photo_rejected, replace=True)
        if submitted:
            self._set_photo_work("preparing")
        return submitted

    @Slot(result=bool)
    def savePhoto(self):
        """上傳預覽的照片（io lane），再換資料庫的 key（db lane），都成功才刪舊的。回傳是否送出。"""
        if self._photo_work or self._pending is None:
            return False
        key, pending = self._new_key(), self._pending
        submitted = self.services.tasks.submit(
            "io", self.UPLOAD_KEY, lambda token, report: upload_photo(pending, key, token),
            on_ok=lambda result: self._photo_uploaded(key, pending),
            on_err=lambda exc: self._photo_failed(exc, _tr("%1. Your current photo is unchanged.")))
        if submitted:
            self._set_errors({"photo": ""})
            self._set_photo_work("saving")
        return submitted

    @Slot()
    def discardPhoto(self):
        """預覽的照片不要了（Cancel）：刪掉處理好的檔案，回到目前的照片。"""
        if self._photo_work == "saving":
            return
        self.services.tasks.cancel(self.PREPARE_KEY)
        if self._photo_work == "preparing":
            self._set_photo_work("")
        self._set_errors({"photo": ""})
        self._drop_pending()

    @Slot(result=bool)
    def removePhoto(self):
        """移除照片：先把資料庫的 key 設成 NULL，成功後才刪 S3 上的檔案。"""
        if self._photo_work or not self._profile.hasPhoto:
            return False
        db, account_id, expected = self.services.db, self._profile.account_id, self._profile.photo_key
        submitted = self.services.tasks.submit(
            "db", self.PHOTO_DB_KEY, lambda token, report: swap_photo_key(db, account_id, None, expected),
            on_ok=self._photo_removed,
            on_err=lambda exc: self._photo_failed(exc, _tr("%1. Your photo is unchanged.")))
        if submitted:
            self._set_errors({"photo": ""})
            self._set_photo_work("removing")
        return submitted

    # ------------------------------------------------------------ 以下都在 GUI 執行緒

    def _new_key(self, clock=datetime.now):
        moment = clock()
        key = avatar_key(self._profile.account_id, moment)
        while key in self._used_keys:
            moment += timedelta(seconds=1)
            key = avatar_key(self._profile.account_id, moment)
        self._used_keys.add(key)
        return key

    def _check(self, form, errors):
        """套用這個表單的欄位錯誤（表單層級的錯誤一起清掉）；有錯時把焦點移到第一個有錯的欄位。"""
        fields = FORM_FIELDS[form]
        self._set_errors({**dict.fromkeys(fields, ""), **errors})
        first = next((field for field in fields if self._errors[field]), None)
        if first is not None:
            self.focusRequested.emit(first)
            return False
        return True

    def _set_errors(self, changes):
        if any(self._errors[field] != text for field, text in changes.items()):
            self._errors.update(changes)
            self.errorsChanged.emit()

    def _set_photo_work(self, work):
        if work != self._photo_work:
            self._photo_work = work
            self.busyChanged.emit()

    def _done(self, form, title, detail=""):
        self.services.notifier.notify("success", title, detail, f"account:self:{form}")
        self.saved.emit(form)
        self.accountEdited.emit(form)

    def _renamed(self, name):
        self._profile.update_fields(name=name)
        self._done("name", _tr("Display name updated"), name)

    def _username_changed(self, username):
        self._profile.update_fields(username=username)
        self._done("username", _tr("Username updated"), _tr("Use %1 the next time you log in.", username))

    def _password_changed(self):
        self._done("password", _tr("Password changed"), _tr("Use the new password the next time you log in."))

    def _form_failed(self, form, exc):
        """修改沒有成功：原因寫在欄位或表單上。log 只記例外的類別和錯誤碼，不記內容（可能有密碼的雜湊）。"""
        errno = getattr(exc, "errno", None)
        logger.warning("changing the account (%s) failed: %s %s", form, type(exc).__name__, errno or "")
        if isinstance(exc, WrongPassword):
            field = "usernamePassword" if form == "username" else "currentPassword"
            self._fail_field(form, field, _tr("The current password is incorrect."))
        elif form == "username" and errno == 1062:  # ER_DUP_ENTRY：uk_account_username（定序不分大小寫）
            self._fail_field(form, "username", _tr("This username is taken. Choose another one."))
        elif isinstance(exc, ChangedElsewhere):
            self._fail_field(form, FORM_FIELDS[form][-1],
                             _tr("Your account was changed on another computer. Check your details and try again."))
            self._profile.load()
        elif isinstance(exc, AccountGone):
            self._fail_field(form, FORM_FIELDS[form][-1], _tr("Your account wasn't found. Log in again."))
        else:
            _, title, detail, _ = user_message(exc)
            message = _tr("%1. Nothing was changed.", title) + (f" {detail}" if detail else "")
            self._fail_field(form, FORM_FIELDS[form][-1], message)

    def _fail_field(self, form, field, message):
        self._set_errors({**dict.fromkeys(FORM_FIELDS[form], ""), field: message})
        self.focusRequested.emit(field)

    # 照片

    def _photo_prepared(self, files):
        self._drop_pending()
        self._pending = files
        self.previewChanged.emit()
        self._set_photo_work("")

    def _photo_rejected(self, exc):
        self._set_photo_work("")
        reason = getattr(exc, "reason", "")
        if reason == "too_large":
            message = _tr("This photo is larger than 5 MB. Choose a smaller one.")
        elif reason == "not_image":
            message = _tr("This file isn't a photo we can open. Choose a JPEG, PNG or HEIC image.")
        elif reason == "unreadable":
            message = _tr("This file can't be opened. Choose another photo.")
        else:
            logger.warning("preparing the photo failed: %r", exc)
            _, title, detail, _ = user_message(exc)
            message = f"{title}. {detail}" if detail else title
        self._set_errors({"photo": message})

    def _photo_uploaded(self, key, pending):
        db, account_id, expected = self.services.db, self._profile.account_id, self._profile.photo_key
        submitted = self.services.tasks.submit(
            "db", self.PHOTO_DB_KEY, lambda token, report: swap_photo_key(db, account_id, key, expected),
            on_ok=lambda old: self._photo_saved(key, pending, old),
            on_err=lambda exc: self._photo_db_failed(key, exc))
        if not submitted:  # app 正在結束（TaskRunner 已經關了）
            self._set_photo_work("")

    def _photo_saved(self, key, pending, old_key):
        self._set_photo_work("")
        self._profile.set_photo(key, avatar_files(key)[1])
        if self._pending == pending:
            self._pending = None
            self.previewChanged.emit()
        self._clean_up(old_key if old_key != key else None, pending)  # 舊的照片和這次的預覽檔
        self._done("photo", _tr("Photo updated"))

    def _photo_db_failed(self, key, exc):
        """S3 上已經有新的檔案，但資料庫沒有換：刪掉新的檔案（盡量），原本的照片不動，預覽留著可以再存。"""
        self._clean_up(key)
        self._photo_failed(exc, _tr("%1. Your current photo is unchanged."))

    def _photo_failed(self, exc, template):
        logger.warning("saving the photo failed: %r", exc)
        self._set_photo_work("")
        if isinstance(exc, ChangedElsewhere):
            title = _tr("Your photo was changed on another computer")
            self._profile.load()
        elif isinstance(exc, AccountGone):
            title = _tr("Your account wasn't found")
        else:
            title = user_message(exc)[1]
        self._set_errors({"photo": template.replace("%1", title)})

    def _photo_removed(self, old_key):
        self._set_photo_work("")
        self._profile.set_photo(None)
        self._clean_up(old_key)
        self._done("photoRemoved", _tr("Photo removed"))

    def _drop_pending(self):
        if self._pending is not None:
            files, self._pending = self._pending, None
            self.previewChanged.emit()
            self._clean_up(None, files)

    def _clean_up(self, old_key, local=()):
        """在 io lane 刪舊的照片（S3 和本機的快取）；每次一個新的 key，不會被擋掉。結果不顯示。"""
        if old_key or local:
            self.services.tasks.submit("io", f"account:self:photo:clean:{uuid.uuid4().hex}",
                                       lambda token, report: clean_up(old_key, local))

    @Slot()
    def cacheCleared(self):
        """設定頁清掉了下載快取（helpCenter.storage.cleared）：預覽的檔案沒了就不留預覽；目前的照片重新下載。
        處理、上傳中的照片工作會擋住清除（local_storage.CACHE_TASK_PREFIXES 有 account:self:photo:）。"""
        if self._pending is not None and not self._photo_work:
            self._pending = None
            self.previewChanged.emit()
        self._profile.refresh_photo()

    @Slot(str, bool)
    def _on_busy_changed(self, key, busy):
        if key in self.KEYS.values():
            self.busyChanged.emit()
