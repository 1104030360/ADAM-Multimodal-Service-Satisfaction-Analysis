"""服務人員端（登入後的畫面）的 Python 物件。

F2（前端重建第二波）：資料庫的讀寫都搬到 db lane（ENG-01、ENG-06、ENG-10）。
- launch() 先把外殼顯示出來（GUI 執行緒 0 個網路往返），帳號名稱、首頁分數、分析清單在 db lane 載入。
- 頁面的資料一律是屬性（Property＋notify），清單是增量模型（RowListModel），不再用一次性的 signal 推資料（ENG-08）。
  每個區塊都有 loading、error、ready 三種狀態；「沒有資料」由頁面依資料判斷（DS-25～27、DS-33）。
- db lane 的工作都是 fn(token, report)，裡面用 services.db.cursor()；預存程序用 execute("CALL …")。
- 還沒搬的（第三波）：明細頁的下載（MediaService）、送出分析的 POST、錄影視窗（RecordWidget）、報表（PDFHandler）。
"""
import logging
import sys
import time
import requests
import os
from mysql.connector import Error
from PySide6.QtGui import QImage
from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, QTimer, QUrl, Signal, Slot
from datetime import datetime, timedelta
import pyaudio
from pathlib import Path
from myapp.database import DatabaseConnector
from myapp.db_lane import DbLane
from myapp.notifications import Notifier, user_message
from myapp.services import AppServices
from myapp.tasks import TaskRunner
from myapp.ui_prefs import UiPrefs
from .record_widget import RecordWidget
from myapp import storage
import shutil
import cv2
import fitz
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# TaskRunner 的 key（同一個 key 同時只跑一個，ENG-14）
ACCOUNT_NAME_KEY = "staff:account-name"
HOME_SCORES_KEY = "staff:home-scores"
ANALYSIS_KEY = "staff:analysis"
SEARCH_KEY = "staff:search"
LAUNCH_KEYS = (ACCOUNT_NAME_KEY, HOME_SCORES_KEY, ANALYSIS_KEY)  # launch() 在背景載入的三件事
ANALYSIS_REFRESH_TOAST_KEY = "analysis-refresh"  # 分析頁自動更新失敗的警告（同一次斷線只跳一則）

_FIRST_ROLE = int(Qt.ItemDataRole.UserRole)


def error_text(error):
    """區塊錯誤（InlineError）的兩段文字：原因（ENG-12 的英文訊息）、收在 Details 裡的技術細節。"""
    _, title, detail, _ = user_message(error)
    reason = f"{title}. {detail}" if detail else title
    return reason, f"{type(error).__name__}: {error}"


def format_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def score_or_none(value):
    """分數欄位：-1 表示沒有資料（db_schema.sql）。沒有資料一律是 None，畫面顯示「—」，不顯示 -1 或 0。"""
    return None if value is None or value < 0 else float(value)


class PDFHandler(QObject):
    pdfPageChanged = Signal(str)  # 用于发射图片路径的信号

    def __init__(self):
        super().__init__()
        self.doc = None
        # 获取 myapp/src/myapp 目录的绝对路径
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 回到第二个 myapp 目录

    @Slot(str)
    def open_pdf(self, filename):
        try:
            # 使用基于 myapp/src/myapp 目录的相对路径指向 pdf 文件夹
            filepath = os.path.join(self.base_dir, "pdf", filename)  # 在 myapp/src/myapp 目录下的 pdf 文件夹
            
            # 打印文件路径以确保路径正确
            print(f"Looking for PDF at: {filepath}")
            
            # 检查文件是否存在
            if not os.path.exists(filepath):
                print(f"Error: File '{filepath}' not found.")
                return
            
            # 打开 PDF 文档
            self.doc = fitz.open(filepath)
            
            if len(self.doc) > 0:
                self.render_page()  # 渲染第一页
            else:
                print(f"Error: PDF '{filename}' is empty or cannot be opened.")
        except Exception as e:
            print(f"Error opening PDF: {e}")

    def render_page(self):
        if self.doc is not None:
            try:
                # 只处理第一页
                page = self.doc.load_page(0)  # 加载第一页
                pix = page.get_pixmap()

                # 将页面保存为图像，并保存到 pdf 目录
                image_path = os.path.join(self.base_dir, "pdf", "single_page.png")  # 保存到 myapp/src/myapp 目录下的 pdf 文件夹
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                image.save(image_path)  # 保存图片
                
                # 使用相对路径发射信号，通知 QML 更新图片
                self.pdfPageChanged.emit(image_path)  # 直接使用相对路径
            except Exception as e:
                print(f"Error rendering page: {e}")


class ChartsModel(QObject):
    chartsSourceChanged = Signal()

    def __init__(self):
        super().__init__()
        self._charts_sources = []

    @Property(list, notify=chartsSourceChanged)
    def chartsSources(self):
        return self._charts_sources

    @chartsSources.setter
    def chartsSources(self, sources):
        if self._charts_sources != sources:
            self._charts_sources = sources
            self.chartsSourceChanged.emit()


class MediaModel(QObject):
    cam0SourceChanged = Signal()
    cam1SourceChanged = Signal()
    audioSourceChanged = Signal()

    def __init__(self):
        super().__init__()
        self._cam0_source = ""
        self._cam1_source = ""
        self._audio_source = ""

    @Property(str, notify=cam0SourceChanged)
    def cam0Source(self):
        return self._cam0_source

    @cam0Source.setter
    def cam0Source(self, source):
        if self._cam0_source != source:
            self._cam0_source = source
            self.cam0SourceChanged.emit()

    @Property(str, notify=cam1SourceChanged)
    def cam1Source(self):
        return self._cam1_source

    @cam1Source.setter
    def cam1Source(self, source):
        if self._cam1_source != source:
            self._cam1_source = source
            self.cam1SourceChanged.emit()

    @Property(str, notify=audioSourceChanged)
    def audioSource(self):
        return self._audio_source

    @audioSource.setter
    def audioSource(self, source):
        if self._audio_source != source:
            self._audio_source = source
            self.audioSourceChanged.emit()


class MediaService:
    def __init__(self, service_id, db_connector):
        self.service_id = service_id
        self.local_media_dir = os.getenv('LOCAL_MEDIA_DIR', os.path.join(os.path.dirname(__file__), "media"))
        self.local_charts_dir = os.getenv('LOCAL_CHARTS_DIR', os.path.join(os.path.dirname(__file__), "charts"))
        self.db_connector = db_connector

        if not os.path.exists(self.local_media_dir):
            os.makedirs(self.local_media_dir)
        if not os.path.exists(self.local_charts_dir):
            os.makedirs(self.local_charts_dir)

    def get_video_urls_from_db(self):
        try:
            conn = self.db_connector.connection
            cursor = conn.cursor()
            # 每個鏡頭取最新的一列；LIMIT 1 也確保結果都讀完了，才能執行下一個查詢
            query = ("SELECT video_url FROM tbl_video_storage WHERE service_id = %s AND video_name LIKE %s "
                     "ORDER BY video_id DESC LIMIT 1")

            cursor.execute(query, (self.service_id, '%cam0%'))
            row = cursor.fetchone()
            cam0_url = row[0] if row else None

            cursor.execute(query, (self.service_id, '%cam1%'))
            row = cursor.fetchone()
            cam1_url = row[0] if row else None

            return cam0_url, cam1_url
        except Error as e:
            print(f"Error querying MySQL: {e}")
            return None, None
        finally:
            if conn.is_connected():
                cursor.close()

    def get_audio_url_from_db(self):
        try:
            conn = self.db_connector.connection
            cursor = conn.cursor()
            query = "SELECT audio_url FROM tbl_audio_storage WHERE service_id = %s ORDER BY audio_id DESC LIMIT 1"
            cursor.execute(query, (self.service_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Error as e:
            print(f"Error querying MySQL: {e}")
            return None
        finally:
            if conn.is_connected():
                cursor.close()

    def get_charts_urls_from_db(self):
        try:
            conn = self.db_connector.connection
            cursor = conn.cursor()
            query = "SELECT charts_url FROM tbl_charts_storage WHERE service_id = %s ORDER BY charts_id"
            cursor.execute(query, (self.service_id,))
            result = cursor.fetchall()
            return [row[0] for row in result] if result else []
        except Error as e:
            print(f"Error querying MySQL: {e}")
            return []
        finally:
            if conn.is_connected():
                cursor.close()

    def download_media(self, media_url, media_type):
        media_name = os.path.basename(media_url)
        folder = 'audio/' if media_type == 'audio' else 'video/'
        local_file_path = os.path.join(self.local_media_dir, media_name)

        try:
            storage.download(f'{folder}{media_name}', local_file_path)
            print(f"Downloaded {media_name} to {local_file_path}")
            return local_file_path
        except Exception as e:
            print(f"Error downloading media from S3: {e}")
            return None

    def download_chart(self, chart_url):
        chart_name = os.path.basename(chart_url)
        local_file_path = os.path.join(self.local_charts_dir, chart_name)

        try:
            storage.download(f'charts/{chart_name}', local_file_path)
            print(f"Downloaded {chart_name} to {local_file_path}")
            return local_file_path
        except Exception as e:
            print(f"Error downloading chart from S3: {e}")
            return None

    def run(self, media_model, charts_model):
        # 先清空：查不到資料時，畫面不能沿用上一筆服務的影片和圖表
        media_model.cam0Source = ""
        media_model.cam1Source = ""
        media_model.audioSource = ""
        charts_model.chartsSources = []

        cam0_url, cam1_url = self.get_video_urls_from_db()

        if cam0_url:
            cam0_path = self.download_media(cam0_url, 'video')
            media_model.cam0Source = QUrl.fromLocalFile(cam0_path).toString() if cam0_path else ""

        if cam1_url:
            cam1_path = self.download_media(cam1_url, 'video')
            media_model.cam1Source = QUrl.fromLocalFile(cam1_path).toString() if cam1_path else ""

        audio_url = self.get_audio_url_from_db()
        if audio_url:
            audio_path = self.download_media(audio_url, 'audio')
            media_model.audioSource = QUrl.fromLocalFile(audio_path).toString() if audio_path else ""

        downloaded_paths = []
        for chart_url in self.get_charts_urls_from_db():
            chart_path = self.download_chart(chart_url)
            if chart_path:
                downloaded_paths.append(QUrl.fromLocalFile(chart_path).toString())
        charts_model.chartsSources = downloaded_paths


class RowListModel(QAbstractListModel):
    """依主鍵增量更新的清單模型（ENG-08）。角色名稱就是每一列 dict 的鍵。

    replace(rows) 只對真的變動的列發 remove、insert、move、dataChanged：清單不整份重設，
    捲動位置和沒變的 delegate 都留著（以前是 model = null 再設回去，捲動位置會跳回最上面，UX-21）。
    """

    countChanged = Signal()

    def __init__(self, roles, key, parent=None):
        super().__init__(parent)
        self._roles = tuple(roles)
        self._key = key
        self._rows = []

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._rows)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        return {_FIRST_ROLE + i: name.encode() for i, name in enumerate(self._roles)}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        offset = int(role) - _FIRST_ROLE
        if not index.isValid() or not 0 <= index.row() < len(self._rows) or not 0 <= offset < len(self._roles):
            return None
        return self._rows[index.row()][self._roles[offset]]

    def rows(self):
        """目前的每一列（複本），給 Python 端和測試讀。"""
        return [dict(row) for row in self._rows]

    def replace(self, rows):
        """換成 rows（依畫面順序，主鍵不重複）。"""
        before = len(self._rows)
        wanted = {row[self._key] for row in rows}
        for i in reversed(range(len(self._rows))):
            if self._rows[i][self._key] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        for target, row in enumerate(rows):
            row = {name: row.get(name) for name in self._roles}
            key = row[self._key]
            current = next((i for i in range(target, len(self._rows)) if self._rows[i][self._key] == key), None)
            if current is None:
                self.beginInsertRows(QModelIndex(), target, target)
                self._rows.insert(target, row)
                self.endInsertRows()
                continue
            if current != target:  # 前面的都排好了，所以 current 一定在 target 後面
                self.beginMoveRows(QModelIndex(), current, current, QModelIndex(), target)
                self._rows.insert(target, self._rows.pop(current))
                self.endMoveRows()
            changed = [name for name in self._roles if self._rows[target][name] != row[name]]
            if changed:
                self._rows[target] = row
                index = self.index(target)
                self.dataChanged.emit(index, index, [_FIRST_ROLE + self._roles.index(name) for name in changed])
        if len(self._rows) != before:
            self.countChanged.emit()


# ---------------------------------------------------------------- 服務查詢（UX-24）

SEARCH_SQL = """
SELECT s.service_id, a.account_name AS name, s.service_start AS start_datetime
FROM tbl_service AS s
JOIN tbl_account AS a ON a.account_id = s.account_id
WHERE s.account_id = %s AND s.service_status = 3{conditions}
ORDER BY s.service_start DESC, s.service_id DESC
"""
RESULT_ROLES = ("service_id", "name", "start_datetime")  # 和以前的 ResultsModel 同名：QML 的表格照用


def parse_date(text):
    """「YYYY-MM-DD」→ datetime；空白 → None（不限）；格式不對丟 ValueError。"""
    text = text.strip()
    return datetime.strptime(text, "%Y-%m-%d") if text else None


def search_services(db, account_id, start, end):
    """在 db lane 執行：自己的、分析完成的服務，依開始時間新的在前。結束日當天整天都算。1 個往返。"""
    conditions, params = "", [account_id]
    if start is not None:
        conditions += " AND s.service_start >= %s"
        params.append(start)
    if end is not None:  # 條件用「小於隔天 00:00」
        conditions += " AND s.service_start < %s"
        params.append(end + timedelta(days=1))
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(SEARCH_SQL.format(conditions=conditions), tuple(params))
        rows = cursor.fetchall()
    return [{"service_id": str(row["service_id"]), "name": row["name"],
             "start_datetime": format_time(row["start_datetime"])} for row in rows]


class ServiceManager(QObject):
    """服務查詢頁（server_service_page.qml）的資料。

    - search(start, end)：先檢查日期格式（不連網路），格式不對只設 startDateInvalid／endDateInvalid、不送出；
      對了就在 db lane 查詢，replace=True：連按時只留最後一次的結果。
    - status：idle（還沒查過）、ready、error；busy 是查詢中。查到 0 筆＝ready 而且 resultsModel.count 是 0。
    - startDate、endDate：上一次送出的條件。換頁回來時欄位照舊，條件和結果對得起來（UX-24）。
    """

    statusChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    criteriaChanged = Signal()
    dateErrorsChanged = Signal()
    currentServiceIdChanged = Signal()

    def __init__(self, services, account_id):
        super(ServiceManager, self).__init__()
        self.services = services
        self.account_id = account_id
        self._currentServiceId = ""
        self.resultsModel = RowListModel(RESULT_ROLES, "service_id", self)
        self._status = "idle"
        self._busy = False
        self._start_date = self._end_date = ""
        self._start_invalid = self._end_invalid = False
        self._error_detail = self._error_technical = ""
        self._failure_count = 0

    @Property(str, notify=currentServiceIdChanged)
    def currentServiceId(self):
        return self._currentServiceId

    @Slot(str)
    def setCurrentServiceId(self, service_id):
        self._currentServiceId = service_id
        self.currentServiceIdChanged.emit()

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=criteriaChanged)
    def startDate(self):
        return self._start_date

    @Property(str, notify=criteriaChanged)
    def endDate(self):
        return self._end_date

    @Property(bool, notify=dateErrorsChanged)
    def startDateInvalid(self):
        return self._start_invalid

    @Property(bool, notify=dateErrorsChanged)
    def endDateInvalid(self):
        return self._end_invalid

    @Property(str, notify=errorChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=errorChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=errorChanged)
    def failureCount(self):
        return self._failure_count

    @Slot(str, str, result=bool)
    def search(self, start_text, end_text):
        """回傳是否已經送出查詢（日期格式不對就不送）。"""
        start, self._start_invalid = self._parse(start_text)
        end, self._end_invalid = self._parse(end_text)
        self.dateErrorsChanged.emit()
        if self._start_invalid or self._end_invalid:
            return False
        self._start_date, self._end_date = start_text.strip(), end_text.strip()
        self.criteriaChanged.emit()
        db, account_id = self.services.db, self.account_id
        self.services.tasks.submit("db", SEARCH_KEY, lambda token, report: search_services(db, account_id, start, end),
                                   on_ok=self._found, on_err=self._failed, replace=True)
        self._set_busy(True)
        return True

    @Slot()
    def retry(self):
        """區塊錯誤的 Retry：用上一次送出的條件再查一次。"""
        self.search(self._start_date, self._end_date)

    @staticmethod
    def _parse(text):
        try:
            return parse_date(text), False
        except ValueError:
            return None, True

    def _found(self, rows):
        self.resultsModel.replace(rows)
        if self._failure_count:
            self._failure_count = 0
            self.errorChanged.emit()
        self._set_status("ready")
        self._set_busy(False)

    def _failed(self, error):
        logger.warning("service search failed: %r", error)
        self._error_detail, self._error_technical = error_text(error)
        self._failure_count += 1
        self.errorChanged.emit()
        self._set_status("error")
        self._set_busy(False)

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    def _set_busy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


# ---------------------------------------------------------------- 錄影頁：鏡頭和麥克風（UX-13、UX-14、ENG-06）

def probe_cameras():
    """試開 0～9 號鏡頭，回傳（清單文字, 鏡頭編號）。要在 GUI 執行緒呼叫，原因見 DeviceFinder。"""
    names, ids = [], []
    for camera_id in range(10):
        cap = cv2.VideoCapture(camera_id)
        if cap.isOpened():
            names.append(f"Camera {camera_id}")  # 使用編號代替名稱
            ids.append(camera_id)
            cap.release()  # 釋放攝影機
    return names, ids


def probe_microphones():
    """有輸入聲道的音訊裝置，回傳（名稱, PyAudio 裝置編號）。"""
    audio = pyaudio.PyAudio()
    try:
        names, ids = [], []
        for device_index in range(audio.get_host_api_info_by_index(0).get("deviceCount")):
            info = audio.get_device_info_by_host_api_device_index(0, device_index)
            if info.get("maxInputChannels") > 0:
                names.append(info.get("name"))
                ids.append(info.get("index", device_index))
        return names, ids
    finally:
        audio.terminate()  # 以前沒有 terminate（ENG-06）；PortAudio 有參照計數，不影響錄影視窗自己的那一份


class DeviceFinder(QObject):
    """錄影頁的鏡頭和麥克風清單。

    - 不在建構子探測（以前登入時就把 0～9 號鏡頭都打開一次，UX-02、UX-13）：打開錄影頁才探測，結果留著；
      「Scan Again」重新探測。
    - status：idle（還沒探測）、scanning、ready、error。
    - 探測在 GUI 執行緒做（T18）：OpenCV 在 macOS 上還沒取得相機權限時，只有在主執行緒才能跳出授權詢問
      （cv2 的訊息：「can not spin main run loop from other thread」）。所以先把 status 設成 scanning，
      等 SCAN_DELAY_MS 讓「Looking for cameras and microphones…」畫出來，再探測。
    """

    camerasChanged = Signal()
    microphonesChanged = Signal()
    statusChanged = Signal()

    SCAN_DELAY_MS = 50

    def __init__(self, server_home):
        super().__init__()
        self.server_home = server_home
        self._cameras = []
        self._camera_ids = []  # 清單第 n 項對應的鏡頭編號
        self._microphones = []
        self._microphone_ids = []  # 清單第 n 項對應的 PyAudio 裝置編號
        self._status = "idle"
        self._error_technical = ""

    @Property(list, notify=camerasChanged)
    def cameras(self):
        return self._cameras

    @Property(list, notify=microphonesChanged)
    def microphones(self):
        return self._microphones

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(str, notify=statusChanged)
    def errorTechnical(self):
        return self._error_technical

    @Slot()
    def ensureScanned(self):
        """錄影頁打開時呼叫：第一次才探測，之後用上次的結果。"""
        if self._status == "idle":
            self.rescan()

    @Slot()
    def rescan(self):
        if self._status == "scanning":
            return
        self._set_status("scanning")
        QTimer.singleShot(self.SCAN_DELAY_MS, self, self._scan)

    def _scan(self):
        try:
            cameras, camera_ids = probe_cameras()
            microphones, microphone_ids = probe_microphones()
        except Exception as error:  # 例如 PortAudio 初始化失敗：顯示區塊錯誤，可以重試
            logger.exception("device probe failed")
            self._error_technical = f"{type(error).__name__}: {error}"
            self._set_status("error")
            return
        self._error_technical = ""
        self._camera_ids, self._microphone_ids = camera_ids, microphone_ids
        if cameras != self._cameras:
            self._cameras = cameras
            self.camerasChanged.emit()
        if microphones != self._microphones:
            self._microphones = microphones
            self.microphonesChanged.emit()
        self._set_status("ready")

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    @Slot(int, int, int)
    def startRecording(self, cam0_index, cam1_index, mic_index):
        account_id = self.server_home.getID()  # Retrieve the account_id dynamically
        # QML 傳的是下拉選單的位置，換成真正的裝置編號
        widget = RecordWidget(self._camera_ids[cam0_index], self._camera_ids[cam1_index],
                              self._microphone_ids[mic_index], account_id)
        widget.show()


# ---------------------------------------------------------------- 首頁分數（UX-05、UX-06、DS-33）

SCORE_KINDS = ("total", "text", "audio", "facial")
SCORE_SIDES = (("customer", "customer"), ("staff", "server"))  # （畫面上的名稱, 資料表欄位的前綴）
PERIODS = ("daily", "weekly")


def _scores_sql():
    """每日和每週一次查完（1 個往返）。每週的範圍包含每日；每日的欄位用 CASE 只取今天的。

    -1 表示沒有資料，用 NULLIF 排除；完全沒有資料時 AVG 是 NULL（畫面顯示「—」，不顯示 0）。
    """
    today = "s.service_start >= CURDATE()"
    columns = [f"COUNT(CASE WHEN {today} THEN 1 END) AS daily_count", "COUNT(*) AS weekly_count"]
    for period in PERIODS:
        for side, prefix in SCORE_SIDES:
            for kind in SCORE_KINDS:
                value = f"NULLIF(sc.{prefix}_{kind}, -1)"
                if period == "daily":
                    value = f"CASE WHEN {today} THEN {value} END"
                columns.append(f"AVG({value}) AS {period}_{side}_{kind}")
    return ("SELECT " + ",\n       ".join(columns) + "\n"
            "FROM tbl_service AS s\n"
            "JOIN tbl_service_score AS sc ON sc.service_id = s.service_id\n"
            "WHERE s.account_id = %s AND s.service_status = 3\n"
            "  AND s.service_start >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)")


SCORES_SQL = _scores_sql()


def empty_scores():
    return {"count": 0, **{side: {kind: None for kind in SCORE_KINDS} for side, _ in SCORE_SIDES}}


def load_scores(db, account_id):
    """在 db lane 執行：{"daily": {...}, "weekly": {...}}，各有 count 和 customer／staff 的 4 個平均分數。"""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(SCORES_SQL, (account_id,))
        row = cursor.fetchone()  # 沒有 GROUP BY 的聚合查詢一定剛好一列
    return {period: {"count": int(row[f"{period}_count"] or 0),
                     **{side: {kind: score_or_none(row[f"{period}_{side}_{kind}"]) for kind in SCORE_KINDS}
                        for side, _ in SCORE_SIDES}}
            for period in PERIODS}


class ServiceScoreManager(QObject):
    """首頁（server_home_button.qml）的分數。

    - daily、weekly：{"count", "customer": {total, text, audio, facial}, "staff": {…}}；沒有資料的分數是 null。
    - status：loading（還沒有資料）、ready、error。busy 是載入中：ready 時舊的分數留著、變淡（DS-33）。
    """

    statusChanged = Signal()
    busyChanged = Signal()
    scoresChanged = Signal()
    errorChanged = Signal()

    def __init__(self, services, account_id):
        super().__init__()
        self.services = services
        self.account_id = account_id
        self._status = "loading"
        self._busy = False
        self._scores = {period: empty_scores() for period in PERIODS}
        self._error_detail = self._error_technical = ""
        self._failure_count = 0

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property('QVariantMap', notify=scoresChanged)
    def daily(self):
        return self._scores["daily"]

    @Property('QVariantMap', notify=scoresChanged)
    def weekly(self):
        return self._scores["weekly"]

    @Property(str, notify=errorChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=errorChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=errorChanged)
    def failureCount(self):
        return self._failure_count

    @Slot()
    def refresh(self):
        """在 db lane 重新載入（1 個往返）。已經在載入就不再送一次。"""
        db, account_id = self.services.db, self.account_id
        if self.services.tasks.submit("db", HOME_SCORES_KEY, lambda token, report: load_scores(db, account_id),
                                      on_ok=self._loaded, on_err=self._failed):
            self._set_busy(True)

    def _loaded(self, scores):
        if scores != self._scores:
            self._scores = scores
            self.scoresChanged.emit()
        if self._failure_count:
            self._failure_count = 0
            self.errorChanged.emit()
        self._set_status("ready")
        self._set_busy(False)

    def _failed(self, error):
        logger.warning("loading home scores failed: %r", error)
        self._error_detail, self._error_technical = error_text(error)
        self._failure_count += 1
        self.errorChanged.emit()
        self._set_status("error")  # DS-33：失敗時整排換成區塊錯誤
        self._set_busy(False)

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    def _set_busy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


# ---------------------------------------------------------------- 分析頁（ENG-05、UX-19～21）

# 三個分頁一次查完（以前 3 個查詢、12 個往返）：重新整理 = 1 個往返
ANALYSIS_SQL = """
SELECT s.service_id, s.service_start, s.service_end, s.service_status, sc.customer_total, sc.server_total
FROM tbl_service AS s
LEFT JOIN tbl_service_score AS sc ON sc.service_id = s.service_id
WHERE s.account_id = %s AND s.service_status IN (1, 2, 3)
ORDER BY s.service_start DESC, s.service_id DESC
"""
# 狀態只會往前走（1 → 2 → 3）：分析服務可能已經寫好 3，不能被這裡蓋回 2
MARK_SENT_SQL = "UPDATE tbl_service SET service_status = 2 WHERE service_id = %s AND service_status < 2"
SERVICE_ROLES = ("serviceId", "startTime", "endTime", "status", "staffScore", "customerScore", "waitingSince",
                 "failed")


def list_services(db, account_id, mark_sent=None):
    """在 db lane 執行：自己的服務（新的在前）。mark_sent 是剛送出分析的服務，先把它改成 2（多 1 個往返）。"""
    with db.cursor(dictionary=True) as cursor:
        if mark_sent:
            cursor.execute(MARK_SENT_SQL, (mark_sent,))
        cursor.execute(ANALYSIS_SQL, (account_id,))
        return cursor.fetchall()


class ServiceModel(QObject):
    """分析頁（analysis.qml）的三個清單：completedModel、analyzingModel、notAnalyzedModel（RowListModel）。

    - status：loading（還沒有資料）、ready、error；busy 是重新整理中。已經有資料時重新整理失敗，清單留著，
      改跳一則警告（DS-52「自動更新失敗」），恢復後自己關掉。
    - waitingSince：分析中的服務從什麼時候開始等（epoch ms）。資料庫沒有送出時間（UX-19），
      所以是這個 app 送出的時間；不是這裡送的，就是這次執行第一次看到它在分析中的時間（T18）。
    - failed：這次執行看過它是 2、後來又變回 1（分析服務失敗時會這樣做，D4、DS-22）。
    - analyzeService()：送出分析的 POST 照舊在 GUI 執行緒（第三波才搬到 io lane）；收到 200 之後，
      改狀態和重新整理清單在 db lane 一起做。
    """

    statusChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()

    def __init__(self, services, account_id):
        super(ServiceModel, self).__init__()
        self.services = services
        self.account_id = account_id
        self._completed = RowListModel(SERVICE_ROLES, "serviceId", self)
        self._analyzing = RowListModel(SERVICE_ROLES, "serviceId", self)
        self._not_analyzed = RowListModel(SERVICE_ROLES, "serviceId", self)
        self._status = "loading"
        self._busy = False
        self._error_detail = self._error_technical = ""
        self._failure_count = 0
        self._last_status = {}  # 上一次看到的狀態
        self._waiting_since = {}
        self._failed = set()
        self._refresh_warning = 0  # 自動更新失敗那一則警告的 toastId

    @Property(QObject, constant=True)
    def completedModel(self):
        return self._completed

    @Property(QObject, constant=True)
    def analyzingModel(self):
        return self._analyzing

    @Property(QObject, constant=True)
    def notAnalyzedModel(self):
        return self._not_analyzed

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=errorChanged)
    def errorDetail(self):
        return self._error_detail

    @Property(str, notify=errorChanged)
    def errorTechnical(self):
        return self._error_technical

    @Property(int, notify=errorChanged)
    def failureCount(self):
        return self._failure_count

    @Slot()
    def refresh(self):
        """在 db lane 重新整理三個清單（1 個往返）。上一次還沒回來就不再送。"""
        db, account_id = self.services.db, self.account_id
        if self.services.tasks.submit("db", ANALYSIS_KEY, lambda token, report: list_services(db, account_id),
                                      on_ok=self._loaded, on_err=self._failed_to_load):
            self._set_busy(True)

    def _loaded(self, rows):
        self._apply(rows)
        if self._failure_count:
            self._failure_count = 0
            self.errorChanged.emit()
        self._set_status("ready")
        self._set_busy(False)
        if self._refresh_warning:  # 恢復了：自動更新失敗的警告自己關掉
            self.services.notifier.dismiss(self._refresh_warning)
            self._refresh_warning = 0

    def _failed_to_load(self, error):
        logger.warning("loading the analysis lists failed: %r", error)
        self._set_busy(False)
        if self._status == "ready":
            if not self._refresh_warning:  # 同一次斷線只跳一則
                cause = user_message(error)[1]
                self._refresh_warning = self.services.notifier.notify(
                    "warning", "Can't refresh analysis status", f"{cause}. Retrying in 15 seconds.",
                    ANALYSIS_REFRESH_TOAST_KEY)
            return
        self._error_detail, self._error_technical = error_text(error)
        self._failure_count += 1
        self.errorChanged.emit()
        self._set_status("error")

    def _apply(self, rows):
        now = time.time() * 1000
        groups = {1: [], 2: [], 3: []}
        statuses = {}
        for row in rows:
            service_id, status = str(row["service_id"]), int(row["service_status"])
            statuses[service_id] = status
            if status == 1 and self._last_status.get(service_id) == 2:
                self._failed.add(service_id)
            elif status != 1:
                self._failed.discard(service_id)
            groups[status].append({
                "serviceId": service_id,
                "startTime": format_time(row["service_start"]),
                "endTime": format_time(row["service_end"]),
                "status": status,
                "staffScore": score_or_none(row["server_total"]),
                "customerScore": score_or_none(row["customer_total"]),
                "waitingSince": self._waiting_since.setdefault(service_id, now) if status == 2 else 0.0,
                "failed": service_id in self._failed,
            })
        self._last_status = statuses
        self._waiting_since = {sid: since for sid, since in self._waiting_since.items() if statuses.get(sid) == 2}
        self._failed &= set(statuses)
        self._completed.replace(groups[3])
        self._analyzing.replace(groups[2])
        self._not_analyzed.replace(groups[1])

    @Slot(str, result=bool)
    def analyzeService(self, service_id):
        if not self.analyze_service(service_id):
            print(f"Failed to analyze service {service_id}")
            return False
        self._waiting_since[service_id] = time.time() * 1000  # 從現在開始等
        db, account_id = self.services.db, self.account_id
        self.services.tasks.submit(
            "db", f"staff:analyze:{service_id}",
            lambda token, report: list_services(db, account_id, mark_sent=service_id),
            on_ok=self._sent, on_err=lambda error: self._send_not_recorded(service_id, error))
        return True

    def _sent(self, rows):
        self._apply(rows)
        self._set_status("ready")

    def _send_not_recorded(self, service_id, error):
        # 分析服務已經收到了；只是狀態沒改成 2，這一列會留在 Not Analyzed，再按一次 Analyze 就好
        logger.warning("marking %s as sent failed: %r", service_id, error)
        self.services.notifier.fromException(error, key=f"analyze:{service_id}")

    def analyze_service(self, service_id):
        url = os.getenv('ANALYZE_API_URL')
        payload = {'service_id': service_id}

        try:
            # (連線逾時, 讀取逾時)，單位是秒；分析服務收到請求就會立刻回 200，不會等分析做完
            response = requests.post(url, json=payload, timeout=(3, 10))
            if response.status_code == 200:
                print(f"Request for service_id {service_id} was successfully received by the server.")
                return True
            else:
                print(f"Server returned error status: {response.status_code}. The service_id might not have been processed.")
                return False
        except Exception as e:
            print(f"Request failed: {e}")
            return False

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    def _set_busy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


# ---------------------------------------------------------------- 外殼

class ServerHome(QObject):
    """服務人員端的外殼（server_home.qml 的 serverHome）。services 是 AppServices；
    只有直接建立、不經過 launch() 時（例如測試明細頁的下載）可以是 None。"""

    accountNameChanged = Signal()

    def __init__(self, account_id, services=None):
        super().__init__()
        self.account_id = account_id
        self.services = services
        self._account_name = ""
        print(f"ServerHome initialized with account_id: {self.account_id}")
        self.deviceFinder = DeviceFinder(self)  # 不在這裡探測：打開錄影頁時才做
        self.serviceScoreManager = ServiceScoreManager(services, account_id)
        self.serviceModel = ServiceModel(services, account_id)
        self.serviceManager = ServiceManager(services, account_id)
        self.chartsModel = ChartsModel()
        self.mediaModel = MediaModel()
        self.pdfmodel = PDFHandler()

    @Property(str, notify=accountNameChanged)
    def accountName(self):
        return self._account_name

    def start(self):
        """launch() 呼叫：帳號名稱和各頁的資料都在 db lane 載入，GUI 執行緒不碰網路。"""
        self.loadAccountName()
        self.serviceScoreManager.refresh()
        self.serviceModel.refresh()

    @Slot()
    def loadAccountName(self):
        db, account_id = self.services.db, self.account_id

        def load(token, report):
            with db.cursor(dictionary=True) as cursor:
                cursor.execute("CALL sp_get_account_name(%s)", (account_id,))
                row = cursor.fetchone()
            return row["account_name"] if row else ""

        self.services.tasks.submit("db", ACCOUNT_NAME_KEY, load, on_ok=self._set_account_name,
                                   on_err=self._account_name_failed)

    def _set_account_name(self, name):
        if name != self._account_name:
            self._account_name = name
            self.accountNameChanged.emit()

    def _account_name_failed(self, error):
        # 名字只是顯示用：同一次斷線，首頁的分數會顯示區塊錯誤；按 Retry 或 Home 時再載一次
        logger.warning("loading the account name failed: %r", error)

    @Slot()
    def onLoadHome(self):
        if not self._account_name:
            self.loadAccountName()
        self.serviceScoreManager.refresh()

    @Slot()
    def onLoadAnalysis(self):
        self.serviceModel.refresh()

    @Slot(result=str)
    def getAccountName(self):
        """舊的介面：回傳已經載入的名字，不連資料庫（QML 改綁 accountName 屬性）。"""
        return self._account_name

    @Slot(result=str)
    def getID(self):
        return self.account_id

    @Slot(str)
    def load_service_media(self, service_id):
        # 第三波才搬到背景：照舊在 GUI 執行緒下載。舊的共用連線用到時才建立，launch() 不連資料庫
        media_service = MediaService(service_id, DatabaseConnector())
        media_service.run(self.mediaModel, self.chartsModel)

    @Slot()
    def clearDir(self):
        # 獲取當前檔案所在的路徑
        current_dir = os.path.dirname(__file__)

        # 和 MediaService 用同一組資料夾：有設定 LOCAL_MEDIA_DIR／LOCAL_CHARTS_DIR 時清那裡
        media_dir = os.getenv('LOCAL_MEDIA_DIR', os.path.join(current_dir, 'media'))
        charts_dir = os.getenv('LOCAL_CHARTS_DIR', os.path.join(current_dir, 'charts'))

        # 清除 media 資料夾的內容
        if os.path.exists(media_dir):
            shutil.rmtree(media_dir)
            os.makedirs(media_dir)
            print(f"Cleared and recreated {media_dir}")
        # 清除 charts 資料夾的內容
        if os.path.exists(charts_dir):
            shutil.rmtree(charts_dir)
            os.makedirs(charts_dir)
            print(f"Cleared and recreated {charts_dir}")


def _register_services(engine, services):
    """外殼的 QML 用到 tasks、notifier、uiPrefs。main.py 在登入前就註冊好了；
    沒經過 MainWindow 的呼叫端（測試）由這裡補上，已經有的不蓋掉。"""
    context = engine.rootContext()
    for name, obj in (("tasks", services.tasks), ("notifier", services.notifier), ("uiPrefs", services.ui_prefs)):
        if context.contextProperty(name) is None:
            context.setContextProperty(name, obj)


def launch(engine, account_id, services=None):
    """登入後打開服務人員端：外殼馬上出現，資料在 db lane 載入（GUI 執行緒 0 個網路往返，ENG-06、ENG-21）。

    services：main.py 的 AppServices（TaskRunner、Notifier、DbLane、UiPrefs）。回傳 ServerHome。
    沒給時（還沒改成傳 services 的舊呼叫端）自己建一份和 main.py 一樣的，外殼關掉時一起收掉。
    """
    standalone = services is None
    if standalone:
        services = AppServices(TaskRunner(), Notifier(), DbLane(), UiPrefs())
    _register_services(engine, services)
    home = ServerHome(account_id, services)
    context = engine.rootContext()
    context.setContextProperty("serverHome", home)
    context.setContextProperty("deviceFinder", home.deviceFinder)
    context.setContextProperty("serviceScoreManager", home.serviceScoreManager)
    context.setContextProperty("serviceModel", home.serviceModel)
    context.setContextProperty("serviceManager", home.serviceManager)
    context.setContextProperty("resultsModel", home.serviceManager.resultsModel)
    context.setContextProperty("chartsModel", home.chartsModel)
    context.setContextProperty("mediaModel", home.mediaModel)
    context.setContextProperty("pdfHandler", home.pdfmodel)

    opened = len(engine.rootObjects())
    qml_file = Path(__file__).resolve().parent.parent / "qml/pages/server_home.qml"
    engine.load(QUrl.fromLocalFile(qml_file))
    if len(engine.rootObjects()) == opened:
        sys.exit(-1)

    # 剛載入的外殼（第 0 個是登入視窗，UX-04）。外殼自己也設了 FullScreen
    window = engine.rootObjects()[-1]
    window.showFullScreen()

    # setContextProperty 不會留住 Python 物件（PySide6 6.12 實測）：home 和各頁的模型只被自己的循環參照留著，
    # 下一次 gc 就會被刪掉，QML 拿到 null。destroyed 的連線留著 closed，closed 的預設值留著 home，
    # 所以 home 和外殼活得一樣久
    def closed(keep_alive=home):
        if standalone:
            services.tasks.shutdown()

    window.destroyed.connect(closed)

    home.start()
    return home
