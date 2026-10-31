import sys
import os
from pathlib import Path
from PySide6.QtGui import QImage
from PySide6.QtCore import QUrl, QObject, Signal, Slot, Property, QStringListModel, Qt
from PySide6.QtGui import QGuiApplication, QStandardItemModel, QStandardItem
from PySide6.QtQml import QQmlApplicationEngine
from mysql.connector import Error
from datetime import datetime, timedelta
from myapp.database import DatabaseConnector
from myapp.passwords import hash_password
from myapp import storage
import shutil
import fitz


class PDFHandler(QObject):
    pdfPageChanged = Signal(str)  # 用于发射图片路径的信号
    currentPageChanged = Signal(int, int)  # 用于发射当前页和总页数的信号

    def __init__(self):
        super().__init__()
        self.doc = None
        self.current_page = 0
        self.total_pages = 0
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
            self.total_pages = len(self.doc)  # 获取总页数
            self.current_page = 0  # 重置为第一页
            
            if self.total_pages > 0:
                self.render_page()  # 渲染第一页
                self.currentPageChanged.emit(self.current_page + 1, self.total_pages)  # 发射当前页码和总页数
            else:
                print(f"Error: PDF '{filename}' is empty or cannot be opened.")
        except Exception as e:
            print(f"Error opening PDF: {e}")

    def render_page(self):
        if self.doc is not None:
            try:
                # 渲染当前页
                page = self.doc.load_page(self.current_page)
                pix = page.get_pixmap()

                # 将页面保存为图像，并保存到 pdf 目录
                image_path = os.path.join(self.base_dir, "pdf", f"page_{self.current_page + 1}.png")
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                image.save(image_path)
                
                # 发射图片路径信号，通知 QML 更新图片
                self.pdfPageChanged.emit(image_path)
            except Exception as e:
                print(f"Error rendering page: {e}")

    def render_all_pages(self):
        if self.doc is not None:
            try:
                # 遍历所有页面并渲染每一页
                for page_number in range(self.total_pages):
                    page = self.doc.load_page(page_number)  # 加载当前页面
                    pix = page.get_pixmap()

                    # 为每一页创建不同的图像文件名
                    image_path = os.path.join(self.base_dir, "pdf", f"page_{page_number + 1}.png")
                    image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                    image.save(image_path)
                    
                    # 打印已保存的图片路径
                    print(f"Saved image for page {page_number + 1} at: {image_path}")
                    
                    # 发射图片路径信号，通知 QML 更新图片
                    self.pdfPageChanged.emit(image_path)
            except Exception as e:
                print(f"Error rendering pages: {e}")

    @Slot()
    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.render_page()
            self.currentPageChanged.emit(self.current_page + 1, self.total_pages)

    @Slot()
    def previous_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.render_page()
            self.currentPageChanged.emit(self.current_page + 1, self.total_pages)


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


class AccountManager(QObject):
    accountAdded = Signal(bool, str)
    accountUpdated = Signal(bool, str)

    def __init__(self, current_account_level, db_connector, parent=None):
        super().__init__(parent)
        self.current_account_level = current_account_level
        self.db_connector = db_connector

    @Slot(str, str, str, str, str)
    def add_account(self, username, password, name, level, occupation):
        if int(level) > int(self.current_account_level):
            self.accountAdded.emit(False, "Cannot add account with a higher level than current account")
            return

        query = ("INSERT INTO tbl_account (account_id, account_username, account_password, account_name, account_level, account_occupation_name) "
                 "VALUES (UUID(), %s, %s, %s, %s, %s)")

        try:
            with self.db_connector.connection.cursor() as cursor:
                cursor.execute(query, (username, hash_password(password), name, level, occupation))
                self.db_connector.connection.commit()
            self.accountAdded.emit(True, "Account successfully added")
        except Error as e:
            self.accountAdded.emit(False, f"Failed to add account: {e}")

    @Slot(str, str, str, str, str, str)
    def update_account(self, account_id, username, password, name, level, occupation):
        if int(level) > int(self.current_account_level):
            self.accountUpdated.emit(False, "Cannot update account with a higher level than current account")
            return

        query = ("UPDATE tbl_account SET account_username=%s, account_password=%s, account_name=%s, "
                 "account_level=%s, account_occupation_name=%s WHERE account_id=%s")

        try:
            with self.db_connector.connection.cursor() as cursor:
                cursor.execute(query, (username, hash_password(password), name, level, occupation, account_id))
                self.db_connector.connection.commit()
            self.accountUpdated.emit(True, "Account successfully updated")
        except Error as e:
            self.accountUpdated.emit(False, f"Failed to update account: {e}")



class TrendDataManager(QObject):
    # Signals to update the series in QML
    serverPointAdded = Signal(float, float)
    customerPointAdded = Signal(float, float)

    def __init__(self, db_connector):
        super(TrendDataManager, self).__init__()
        self.db_connector = db_connector

    @Slot(int, int)  # month and year as parameters
    def load_trends_for_month(self, month, year):
        """Fetch data from the database for the selected month and emit signals."""
        try:
            connection = self.db_connector.connection
            cursor = connection.cursor()

            start_date = datetime(year, month, 1)
            end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)

            query = """
            SELECT 
                DAY(service.service_start) AS day,
                AVG(score.server_total) AS server_score,
                AVG(score.customer_total) AS customer_score
            FROM tbl_service_score AS score
            JOIN tbl_service AS service ON score.service_id = service.service_id
            WHERE service.service_start BETWEEN %s AND %s
            GROUP BY DAY(service.service_start)
            ORDER BY DAY(service.service_start)
            """
            cursor.execute(query, (start_date, end_date))
            results = cursor.fetchall()

            server_scores = {day: 0 for day in range(1, 32)}
            customer_scores = {day: 0 for day in range(1, 32)}

            for day, server_score, customer_score in results:
                server_scores[day] = float(server_score)
                customer_scores[day] = float(customer_score)

            print(f"Server Scores for {month}/{year}: {server_scores}")
            print(f"Customer Scores for {month}/{year}: {customer_scores}")

            for day in range(1, 32):
                self.serverPointAdded.emit(day, server_scores[day])
                self.customerPointAdded.emit(day, customer_scores[day])

            cursor.close()

        except Error as e:
            print(f"Error fetching data: {e}")


class ServiceScoreManager(QObject):
    updateDailyServiceCount = Signal(float)
    updateWeeklyServiceCount = Signal(float)
    updateDailyScores = Signal(dict, dict)
    updateWeeklyScores = Signal(dict, dict)

    def __init__(self, db_connector):
        super(ServiceScoreManager, self).__init__()
        self.db_connector = db_connector

    @Slot()
    def load_scores(self):
        daily_scores = self.get_service_scores('daily')
        weekly_scores = self.get_service_scores('weekly')

        self.updateDailyServiceCount.emit(daily_scores[0])
        self.updateWeeklyServiceCount.emit(weekly_scores[0])

        daily_customer_scores = {
            'total': daily_scores[1],
            'text': daily_scores[3],
            'audio': daily_scores[5],
            'facial': daily_scores[7]
        }

        daily_server_scores = {
            'total': daily_scores[2],
            'text': daily_scores[4],
            'audio': daily_scores[6],
            'facial': daily_scores[8]
        }

        weekly_customer_scores = {
            'total': weekly_scores[1],
            'text': weekly_scores[3],
            'audio': weekly_scores[5],
            'facial': weekly_scores[7]
        }

        weekly_server_scores = {
            'total': weekly_scores[2],
            'text': weekly_scores[4],
            'audio': weekly_scores[6],
            'facial': weekly_scores[8]
        }

        self.updateDailyScores.emit(daily_customer_scores, daily_server_scores)
        self.updateWeeklyScores.emit(weekly_customer_scores, weekly_server_scores)

    def get_service_scores(self, time_filter):
        query = """
        SELECT 
            COUNT(service.service_id), 
            AVG(NULLIF(score.customer_total, -1)), AVG(NULLIF(score.server_total, -1)),
            AVG(NULLIF(score.customer_text, -1)), AVG(NULLIF(score.server_text, -1)),
            AVG(NULLIF(score.customer_audio, -1)), AVG(NULLIF(score.server_audio, -1)),
            AVG(NULLIF(score.customer_facial, -1)), AVG(NULLIF(score.server_facial, -1))
        FROM tbl_service AS service
        JOIN tbl_service_score AS score ON service.service_id = score.service_id
        WHERE service.service_status = '3'
        """

        if time_filter == 'daily':
            query += " AND service.service_start >= CURDATE()"
        elif time_filter == 'weekly':
            query += " AND service.service_start >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)"

        try:
            connection = self.db_connector.connection
            cursor = connection.cursor()
            cursor.execute(query)
            result = cursor.fetchone()
            return [float(x) if x is not None else 0 for x in result]
        except Error as e:
            print(f"Error: {e}")
            return [0, 0, 0, 0, 0, 0, 0, 0, 0]
        finally:
            if connection.is_connected():
                cursor.close()


class RankingModel(QObject):
    updateRanking = Signal(list, list, list, list)

    def __init__(self, db_connector):
        super(RankingModel, self).__init__()
        self.db_connector = db_connector

    @Slot()
    def load_ranking(self):
        query = """
        SELECT acc.account_name, score.customer_total, score.server_total
        FROM tbl_service_score AS score
        JOIN tbl_service AS service ON score.service_id = service.service_id
        JOIN tbl_account AS acc ON service.account_id = acc.account_id
        WHERE service.service_status = '3'
        """

        rankings = self.execute_query(query)
        
        customers, servers = {}, {}
        for name, customer_score, server_score in rankings:
            if name not in customers:
                customers[name] = []
                servers[name] = []
            customers[name].append(customer_score)
            servers[name].append(server_score)
        
        avg_customers = [(name, round(sum(scores) / len(scores), 2)) for name, scores in customers.items()]
        avg_servers = [(name, round(sum(scores) / len(scores), 2)) for name, scores in servers.items()]

        top5Customer = sorted(avg_customers, key=lambda x: x[1], reverse=True)[:5]
        bottom5Customer = sorted(avg_customers, key=lambda x: x[1])[:5]

        top5Server = sorted(avg_servers, key=lambda x: x[1], reverse=True)[:5]
        bottom5Server = sorted(avg_servers, key=lambda x: x[1])[:5]

        top5Customer = [{"name": name, "score": score} for name, score in top5Customer]
        bottom5Customer = [{"name": name, "score": score} for name, score in bottom5Customer]
        top5Server = [{"name": name, "score": score} for name, score in top5Server]
        bottom5Server = [{"name": name, "score": score} for name, score in bottom5Server]

        self.updateRanking.emit(top5Customer, bottom5Customer, top5Server, bottom5Server)

    def execute_query(self, query):
        try:
            connection = self.db_connector.connection
            cursor = connection.cursor()
            cursor.execute(query)
            return cursor.fetchall()
        except Error as e:
            print(f"Error: {e}")
            return []
        finally:
            if connection.is_connected():
                cursor.close()


class ServiceManager(QObject):
    namesChanged = Signal()
    resultsChanged = Signal()
    currentServiceIdChanged = Signal()

    def __init__(self, db_connector):
        super(ServiceManager, self).__init__()
        self.db_connector = db_connector
        self._currentServiceId = ""
        self._names = []
        self._results = []
        self.resultsModel = ResultsModel()
    
    @Property(str, notify=currentServiceIdChanged)
    def currentServiceId(self):
        return self._currentServiceId

    @Slot(str)
    def setCurrentServiceId(self, service_id):
        self._currentServiceId = service_id
        self.currentServiceIdChanged.emit()
        print(f"Current service ID set to: {self._currentServiceId}")

    @Property(list, notify=namesChanged)
    def names(self):
        return self._names

    @Property('QVariant', notify=resultsChanged)
    def results(self):
        return self._results

    @Slot()
    def load_names(self):
        conn = self.db_connector.connection
        cursor = conn.cursor()

        # Load names
        cursor.execute("SELECT DISTINCT account_name FROM tbl_account WHERE account_level = 1")
        names = cursor.fetchall()

        # Set the names and emit signal
        self._names = [name[0] for name in names]
        self.namesChanged.emit()

        cursor.close()

    @Slot(str, str, str)
    def search_services(self, name, start_date, end_date):
        print(f"Received search inputs - Name: {name}, Start Date: {start_date}, End Date: {end_date}")

        conn = self.db_connector.connection
        cursor = conn.cursor(dictionary=True)

        query = """
        SELECT s.service_id, a.account_name AS name, s.service_start AS start_datetime
        FROM tbl_service s
        JOIN tbl_account a ON s.account_id = a.account_id
        WHERE 1=1
        """
        
        params = []
        if name:
            query += " AND a.account_name = %s"
            params.append(name)
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


class AdminHome(QObject):
    def __init__(self, account_id):
        super().__init__()
        self.account_id = account_id
        print(f"AdminHome initialized with account_id: {self.account_id}")
        self.db_connector = DatabaseConnector()
        self.serviceScoreManager = ServiceScoreManager(self.db_connector)
        self.rankingModel = RankingModel(self.db_connector)
        self.trendDataManager = TrendDataManager(self.db_connector)
        self.serviceManager = ServiceManager(self.db_connector)
        self.accountManager = None  # Will initialize after fetching account level
        self.chartsModel = ChartsModel()
        self.mediaModel = MediaModel()
        self.pdfmodel = PDFHandler()

        # Fetch account level and initialize AccountManager
        self.initialize_account_manager()


    @Slot(result=str)
    def getAccountName(self):
        return self.db_connector.get_account_name(self.account_id)

    @Slot(result=str)
    def getID(self):
        return self.account_id
    
    @Slot()
    def onLoadHome(self):
        self.load_home_data()

    @Slot()
    def load_home_data(self):
        self.serviceScoreManager.load_scores()
        self.rankingModel.load_ranking()
        self.trendDataManager.load_trends()

    def initialize_account_manager(self):
        try:
            connection = self.db_connector.connection
            cursor = connection.cursor()
            cursor.execute("SELECT account_level FROM tbl_account WHERE account_id = %s", (self.account_id,))
            result = cursor.fetchone()
            if result:
                account_level = result[0]
                # Pass db_connector to AccountManager
                self.accountManager = AccountManager(account_level, self.db_connector)
            else:
                print(f"No account found for account_id: {self.account_id}")
        except Error as e:
            print(f"Error fetching account level: {e}")
    
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
    home = AdminHome(account_id)
    engine.rootContext().setContextProperty("adminHome", home)
    engine.rootContext().setContextProperty("serviceScoreManager", home.serviceScoreManager)
    engine.rootContext().setContextProperty("rankingModel", home.rankingModel)
    engine.rootContext().setContextProperty("trendDataManager", home.trendDataManager)
    engine.rootContext().setContextProperty("serviceManager", home.serviceManager)
    engine.rootContext().setContextProperty("resultsModel", home.serviceManager.resultsModel)
    engine.rootContext().setContextProperty("accountManager", home.accountManager)  # Set the AccountManager
    engine.rootContext().setContextProperty("chartsModel", home.chartsModel)
    engine.rootContext().setContextProperty("mediaModel", home.mediaModel)
    engine.rootContext().setContextProperty("pdfHandler", home.pdfmodel)

    qml_file = Path(__file__).resolve().parent.parent / "qml/pages/admin_home.qml"
    engine.load(QUrl.fromLocalFile(qml_file))

    if not engine.rootObjects():
        sys.exit(-1)

    window = engine.rootObjects()[0]
    window.showFullScreen()

    def on_window_closed():
        home.db_connector.close_connection()

    window.destroyed.connect(on_window_closed)

    home.serviceScoreManager.load_scores()
    home.rankingModel.load_ranking()