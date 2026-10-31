import sys
import requests
import os
from mysql.connector import Error
from PySide6.QtGui import QImage
from PySide6.QtCore import QUrl, QObject, Signal, Slot, Property, QStringListModel, Qt
from PySide6.QtGui import QGuiApplication, QStandardItemModel, QStandardItem
from PySide6.QtQml import QQmlApplicationEngine
from datetime import datetime, timedelta
import pyaudio
from pathlib import Path
from myapp.database import DatabaseConnector  
from .record_widget import RecordWidget
from myapp import storage
import shutil
import cv2
import fitz
from dotenv import load_dotenv
load_dotenv()


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
            query = "SELECT video_url FROM tbl_video_storage WHERE service_id = %s AND video_name LIKE %s"
            
            cursor.execute(query, (self.service_id, '%cam0%'))
            cam0_url = cursor.fetchone()[0]
            
            cursor.execute(query, (self.service_id, '%cam1%'))
            cam1_url = cursor.fetchone()[0]
            
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
            query = "SELECT audio_url FROM tbl_audio_storage WHERE service_id = %s"
            cursor.execute(query, (self.service_id,))
            audio_url = cursor.fetchone()[0]
            return audio_url
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
            query = "SELECT charts_url FROM tbl_charts_storage WHERE service_id = %s"
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

        charts_urls = self.get_charts_urls_from_db()
        if charts_urls:
            downloaded_paths = []
            for chart_url in charts_urls:
                chart_path = self.download_chart(chart_url)
                if chart_path:
                    downloaded_paths.append(QUrl.fromLocalFile(chart_path).toString())
            charts_model.chartsSources = downloaded_paths


class ServiceManager(QObject):
    resultsChanged = Signal()
    currentServiceIdChanged = Signal()

    def __init__(self, db_connector, account_id):
        super(ServiceManager, self).__init__()
        self.db_connector = db_connector
        self.account_id = account_id
        self._currentServiceId = ""
        self._results = []
        self.resultsModel = ResultsModel()
        self.pdfmodel = PDFHandler()

    @Property(str, notify=currentServiceIdChanged)
    def currentServiceId(self):
        return self._currentServiceId

    @Slot(str)
    def setCurrentServiceId(self, service_id):
        self._currentServiceId = service_id
        self.currentServiceIdChanged.emit()
        print(f"Current service ID set to: {self._currentServiceId}")

    @Property('QVariant', notify=resultsChanged)
    def results(self):
        return self._results

    @Slot(str, str)
    def search_services(self, start_date, end_date):
        print(f"Received search inputs - Start Date: {start_date}, End Date: {end_date}")

        conn = self.db_connector.connection
        cursor = conn.cursor(dictionary=True)

        query = """
        SELECT s.service_id, a.account_name AS name, s.service_start AS start_datetime
        FROM tbl_service s
        JOIN tbl_account a ON s.account_id = a.account_id
        WHERE s.account_id = %s
        AND s.service_status = 3
        """
        
        params = [self.account_id] 

        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
                query += " AND s.service_start >= %s"
                params.append(start_date_obj)
            except ValueError:
                print("Invalid start date format. Please use YYYY-MM-DD.")
        
        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
                query += " AND s.service_start <= %s"
                params.append(end_date_obj)
            except ValueError:
                print("Invalid end date format. Please use YYYY-MM-DD.")
        
        print(f"Executing query: {query} with parameters: {params}")

        cursor.execute(query, tuple(params))
        results = cursor.fetchall()

        print(f"Query returned {len(results)} results")

        self.resultsModel.clear()
        self._results = []
        for result in results:
            self._results.append(result)
            service_id_item = QStandardItem(result['service_id'])
            name_item = QStandardItem(result['name'])
            datetime_item = QStandardItem(result['start_datetime'].strftime("%Y-%m-%d %H:%M:%S"))
            self.resultsModel.appendRow([service_id_item, name_item, datetime_item])

        self.resultsChanged.emit()
        cursor.close()


class ResultsModel(QStandardItemModel):
    ServiceIdRole = Qt.UserRole + 1
    NameRole = Qt.UserRole + 2
    StartDatetimeRole = Qt.UserRole + 3

    def __init__(self):
        super().__init__()

    def data(self, index, role):
        if not index.isValid():
            return None

        if role == self.ServiceIdRole:
            return index.siblingAtColumn(0).data(Qt.DisplayRole)
        elif role == self.NameRole:
            return index.siblingAtColumn(1).data(Qt.DisplayRole)
        elif role == self.StartDatetimeRole:
            return index.siblingAtColumn(2).data(Qt.DisplayRole)

        return super().data(index, role)

    def roleNames(self):
        roles = super().roleNames()
        roles[self.ServiceIdRole] = b'service_id'
        roles[self.NameRole] = b'name'
        roles[self.StartDatetimeRole] = b'start_datetime'
        return roles

class DeviceFinder(QObject):
    camerasChanged = Signal()
    microphonesChanged = Signal()

    def __init__(self, server_home):
        super().__init__()
        self.server_home = server_home
        self._cameras = []
        self._microphones = []
        self.load_device_options()

    @Property(list, notify=camerasChanged)
    def cameras(self):
        return self._cameras

    @Property(list, notify=microphonesChanged)
    def microphones(self):
        return self._microphones

    def load_device_options(self):
        self._cameras = self.get_available_cameras()
        self._microphones = self.get_available_microphones()
        self.camerasChanged.emit()
        self.microphonesChanged.emit()

    def get_available_cameras(self):
        available_cameras = []
        for camera_id in range(10):  # 假設最多測試10個攝影機
            cap = cv2.VideoCapture(camera_id)
            if cap.isOpened():
                available_cameras.append(f"Camera {camera_id}")  # 使用編號代替名稱
                cap.release()  # 釋放攝影機
        return available_cameras

    def get_available_microphones(self):
        available_microphones = []
        p = pyaudio.PyAudio()
        number_of_devices = p.get_host_api_info_by_index(0).get('deviceCount')
        for device_index in range(0, number_of_devices):
            device_info = p.get_device_info_by_host_api_device_index(0, device_index)
            if device_info.get('maxInputChannels') > 0:
                available_microphones.append(device_info.get('name'))
        return available_microphones

    @Slot(int, int, int)
    def startRecording(self, cam0_index, cam1_index, mic_index):
        account_id = self.server_home.getID()  # Retrieve the account_id dynamically
        # Directly create and show the RecordWidget
        widget = RecordWidget(cam0_index, cam1_index, mic_index, account_id)
        widget.show()


class ServiceScoreManager(QObject):
    updateDailyServiceCount = Signal(int)
    updateWeeklyServiceCount = Signal(int)
    updateDailyScores = Signal(dict, dict)  # Two dictionaries for customer and server
    updateWeeklyScores = Signal(dict, dict)  # Two dictionaries for customer and server

    def __init__(self, account_id):
        super().__init__()
        self.account_id = account_id
        self.db_connector = DatabaseConnector()

    @Slot()
    def load_data(self):
        daily_scores = self.get_service_scores('daily')
        weekly_scores = self.get_service_scores('weekly')

        # Emit signals to update QML UI
        self.updateDailyServiceCount.emit(daily_scores[0])
        self.updateWeeklyServiceCount.emit(weekly_scores[0])
        self.updateDailyScores.emit(
            {
                'total': daily_scores[1],
                'text': daily_scores[3],
                'audio': daily_scores[5],
                'facial': daily_scores[7]
            },
            {
                'total': daily_scores[2],
                'text': daily_scores[4],
                'audio': daily_scores[6],
                'facial': daily_scores[8]
            }
        )
        self.updateWeeklyScores.emit(
            {
                'total': weekly_scores[1],
                'text': weekly_scores[3],
                'audio': weekly_scores[5],
                'facial': weekly_scores[7]
            },
            {
                'total': weekly_scores[2],
                'text': weekly_scores[4],
                'audio': weekly_scores[6],
                'facial': weekly_scores[8]
            }
        )

    def get_service_scores(self, time_filter):
        query = ""
        if time_filter == 'daily':
            query = """
            SELECT 
                COUNT(service.service_id), 
                AVG(NULLIF(score.customer_total, -1)), AVG(NULLIF(score.server_total, -1)),
                AVG(NULLIF(score.customer_text, -1)), AVG(NULLIF(score.server_text, -1)),
                AVG(NULLIF(score.customer_audio, -1)), AVG(NULLIF(score.server_audio, -1)),
                AVG(NULLIF(score.customer_facial, -1)), AVG(NULLIF(score.server_facial, -1))
            FROM tbl_service AS service
            JOIN tbl_service_score AS score ON service.service_id = score.service_id
            WHERE service.account_id = %s 
            AND service.service_status = '3'
            AND service.service_start >= CURDATE()
            """
        elif time_filter == 'weekly':
            query = """
            SELECT 
                COUNT(service.service_id), 
                AVG(NULLIF(score.customer_total, -1)), AVG(NULLIF(score.server_total, -1)),
                AVG(NULLIF(score.customer_text, -1)), AVG(NULLIF(score.server_text, -1)),
                AVG(NULLIF(score.customer_audio, -1)), AVG(NULLIF(score.server_audio, -1)),
                AVG(NULLIF(score.customer_facial, -1)), AVG(NULLIF(score.server_facial, -1))
            FROM tbl_service AS service
            JOIN tbl_service_score AS score ON service.service_id = score.service_id
            WHERE service.account_id = %s 
            AND service.service_status = '3'
            AND service.service_start >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            """

        cursor = self.db_connector.connection.cursor()
        cursor.execute(query, (self.account_id,))
        result = cursor.fetchone()

        if result:
            # Convert Decimal to float and replace None with 0
            processed_result = [float(x) if x is not None else 0 for x in result]
            return processed_result
        else:
            # Return default values if no results are found
            return [0, 0, 0, 0, 0, 0, 0, 0, 0]
        
        
class ServiceModel(QObject):
    uncompletedModelChanged = Signal()
    inprogressModelChanged = Signal()
    completedModelChanged = Signal()

    def __init__(self, db_connector, account_id):
        super(ServiceModel, self).__init__()
        self.db_connector = db_connector
        self.account_id = account_id
        self._uncompletedModel = []
        self._inprogressModel = []
        self._completedModel = []

        # Fetch the data when the model is initialized
        self.fetch_uncompleted_services()
        self.fetch_inprogress_services()
        self.fetch_completed_services()

    @Property('QVariantList', notify=uncompletedModelChanged)
    def uncompletedModel(self):
        return self._uncompletedModel

    @Property('QVariantList', notify=inprogressModelChanged)
    def inprogressModel(self):
        return self._inprogressModel

    @Property('QVariantList', notify=completedModelChanged)
    def completedModel(self):
        return self._completedModel

    def execute_query(self, query, params):
        """Helper function to execute a query and return the results."""
        try:
            connection = self.db_connector.connection
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, params)
            return cursor.fetchall()
        except Error as e:
            print(f"Error: {e}")
            return []
        finally:
            if connection.is_connected():
                cursor.close()

    @Slot()
    def fetch_uncompleted_services(self):
        query = """
        SELECT tbl_service.service_id, service_start, service_end, service_status
        FROM tbl_service
        WHERE account_id = %s AND service_status = '1'
        """
        services = self.execute_query(query, (self.account_id,))
        self._uncompletedModel = self.process_services(services, include_scores=False)
        print(f"Uncompleted Services: {self._uncompletedModel}")
        self.uncompletedModelChanged.emit()

    @Slot()
    def fetch_inprogress_services(self):
        query = """
        SELECT tbl_service.service_id, service_start, service_end, service_status
        FROM tbl_service
        WHERE account_id = %s AND service_status = '2'
        """
        services = self.execute_query(query, (self.account_id,))
        self._inprogressModel = self.process_services(services, include_scores=False)
        print(f"In-progress Services: {self._inprogressModel}")
        self.inprogressModelChanged.emit()

    @Slot()
    def fetch_completed_services(self):
        query = """
        SELECT tbl_service.service_id, service_start, service_end, service_status, 
               customer_total, server_total
        FROM tbl_service
        LEFT JOIN tbl_service_score ON tbl_service.service_id = tbl_service_score.service_id
        WHERE account_id = %s AND service_status = '3'
        """
        services = self.execute_query(query, (self.account_id,))
        self._completedModel = self.process_services(services, include_scores=True)
        print(f"Completed Services: {self._completedModel}")
        self.completedModelChanged.emit()

    def process_services(self, services, include_scores):
        """Process the services list to ensure data integrity."""
        processed_services = []
        for service in services:
            processed_service = {
                'service_id': str(service.get('service_id', '')),
                'service_start': service.get('service_start').strftime('%Y-%m-%d %H:%M:%S') if service.get('service_start') else '',
                'service_end': service.get('service_end').strftime('%Y-%m-%d %H:%M:%S') if service.get('service_end') else '',
                'service_status': str(service.get('service_status', ''))
            }
            if include_scores:
                processed_service['customer_total'] = str(service.get('customer_total', ''))
                processed_service['server_total'] = str(service.get('server_total', ''))
            else:
                processed_service['customer_total'] = ''
                processed_service['server_total'] = ''
            processed_services.append(processed_service)
        print(f"Processed Services: {processed_services}")
        return processed_services

    def update_service_status(self, service_id, new_status):
        """Update the service status in the database."""
        query = """
        UPDATE tbl_service
        SET service_status = %s
        WHERE service_id = %s
        """
        try:
            connection = self.db_connector.connection
            cursor = connection.cursor()
            cursor.execute(query, (new_status, service_id))
            connection.commit()  # Ensure the update is committed
        except Error as e:
            print(f"Error updating service status: {e}")
        finally:
            if connection.is_connected():
                cursor.close()

    @Slot(str)
    def analyzeService(self, service_id):
        if self.analyze_service(service_id):
            self.update_service_status(service_id, '2')  # Update status to '2'
            # Refresh the models
            self.fetch_uncompleted_services()
            self.fetch_inprogress_services()
            self.fetch_completed_services()
        else:
            print(f"Failed to analyze service {service_id}")

    def analyze_service(self, service_id):
        url = os.getenv('ANALYZE_API_URL')
        payload = {'service_id': service_id}
        
        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                print(f"Request for service_id {service_id} was successfully received by the server.")
                return True
            else:
                print(f"Server returned error status: {response.status_code}. The service_id might not have been processed.")
                return False
        except Exception as e:
            print(f"Request failed: {e}")
            return False
class ServerHome(QObject):
    def __init__(self, account_id):
        super().__init__()
        self.account_id = account_id
        print(f"ServerHome initialized with account_id: {self.account_id}")
        self.db_connector = DatabaseConnector()
        self.deviceFinder = DeviceFinder(self)
        self.serviceScoreManager = ServiceScoreManager(self.account_id)
        self.serviceModel = ServiceModel(self.db_connector, self.account_id)
        self.serviceManager = ServiceManager(self.db_connector,self.account_id)
        self.chartsModel = ChartsModel()
        self.mediaModel = MediaModel()
        self.pdfmodel = PDFHandler()

    @Slot()
    def load_home_data(self):
        self.serviceScoreManager.load_data()


    @Slot()
    def load_analysis_data(self):
        self.serviceModel.fetch_uncompleted_services()
        self.serviceModel.fetch_inprogress_services()
        self.serviceModel.fetch_completed_services()

    @Slot()
    def onLoadHome(self):
        self.load_home_data()

    @Slot()
    def onLoadAnalysis(self):
        self.load_analysis_data()

    @Slot(result=str)
    def getAccountName(self):
        return self.db_connector.get_account_name(self.account_id)

    @Slot(result=str)
    def getID(self):
        return self.account_id
    
    @Slot(str)
    def load_service_media(self, service_id):
        media_service = MediaService(service_id, self.db_connector)
        media_service.run(self.mediaModel, self.chartsModel)

    @Slot()
    def clearDir(self):
        # 獲取當前檔案所在的路徑
        current_dir = os.path.dirname(__file__)
        
        # 定義 media 和 charts 資料夾的路徑
        media_dir = os.path.join(current_dir, 'media')
        charts_dir = os.path.join(current_dir, 'charts')
        
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
def launch(engine, account_id):
    home = ServerHome(account_id)
    engine.rootContext().setContextProperty("serverHome", home)
    engine.rootContext().setContextProperty("deviceFinder", home.deviceFinder)  # Set deviceFinder as a context property
    engine.rootContext().setContextProperty("serviceScoreManager", home.serviceScoreManager)  # Set serviceScoreManager as a context property
    engine.rootContext().setContextProperty("serviceModel", home.serviceModel)  # Set serviceModel as a context property
    engine.rootContext().setContextProperty("serviceManager", home.serviceManager)
    engine.rootContext().setContextProperty("resultsModel", home.serviceManager.resultsModel)
    engine.rootContext().setContextProperty("chartsModel", home.chartsModel)
    engine.rootContext().setContextProperty("mediaModel", home.mediaModel)
    engine.rootContext().setContextProperty("pdfHandler", home.pdfmodel)


    qml_file = Path(__file__).resolve().parent.parent / "qml/pages/server_home.qml"
    engine.load(QUrl.fromLocalFile(qml_file))

    if not engine.rootObjects():
        sys.exit(-1)

    window = engine.rootObjects()[0]
    window.showFullScreen()

    # Ensure the database connection is closed when the window is closed
    def on_window_closed():
        home.db_connector.close_connection()

    window.destroyed.connect(on_window_closed)
    
    # Load data after the QML is loaded
    home.serviceScoreManager.load_data()
