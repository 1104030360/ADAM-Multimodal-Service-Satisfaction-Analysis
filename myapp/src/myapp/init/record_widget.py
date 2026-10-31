from PySide6 import QtCore, QtGui, QtWidgets
import sys
import cv2
import pyaudio
import wave
import threading
import os
from datetime import datetime
import mysql.connector
from myapp import storage
from myapp.database import connection_config
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()



class RecordWidget(QtWidgets.QWidget):
    # 錄音、錄影在背景執行緒執行；狀態文字透過 signal 交給主執行緒更新
    statusChanged = QtCore.Signal(str)

    def __init__(self, cam0_index, cam1_index, mic_index, account_id, parent=None):
        super().__init__(parent)

        self.customer_cam_index = cam0_index
        self.staff_cam_index = cam1_index
        self.mic_index = mic_index
        self.account_id = account_id
        print(f"RecordWidget initialized with account_id: {self.account_id}, customer_cam_index: {self.customer_cam_index}, staff_cam_index: {self.staff_cam_index}, mic_index: {self.mic_index}")

        self.timer_cameras = []  # Define a timer for each camera
        self.caps = []  # Store the VideoCapture for each camera
        self.CAM_NUMS = [self.customer_cam_index, self.staff_cam_index]  # Camera indices from main program

        self.recording = False
        self.audio_thread = None
        self.video_threads = []
        self.out_files = []
        self.video_filenames = []
        self.audio_filename = ""

        # Initialize timestamps
        self.start_time = None
        self.end_time = None

        # Fetch account name
        self.account_name = self.fetch_account_name()
        print(f"Fetched account_name: {self.account_name}")

        self.set_ui()
        self.slot_init()
        self.open_cameras()  # Directly open cameras

        # Ensure target directories exist
        os.makedirs('video', exist_ok=True)
        os.makedirs('audio', exist_ok=True)

    def fetch_account_name(self):
        print("Fetching account name from database")
        connection = mysql.connector.connect(**connection_config())
        cursor = connection.cursor()

        try:
            cursor.execute("SELECT account_name FROM tbl_account WHERE account_id = %s", (self.account_id,))
            result = cursor.fetchone()
            if result:
                print(f"Account name found: {result[0]}")
                return result[0]
            else:
                print("No account name found")
                return "Unknown User"
        except mysql.connector.Error as err:
            print(f"Error fetching account name: {err}")
            return "Error"
        finally:
            cursor.close()
            connection.close()

    def set_ui(self):
        print("Setting up UI")
        self.setWindowTitle("camera system")
        self.setFixedSize(1024, 640)
        self.setStyleSheet("background-color: #F0F0F0;")

        self.__layout_main = QtWidgets.QVBoxLayout(self)
        self.__layout_main.setContentsMargins(20, 20, 20, 20)
        self.__layout_main.setSpacing(10)

        # Status bar
        self.status_bar = QtWidgets.QLabel(f"Ready for {self.account_name}")
        self.status_bar.setAlignment(QtCore.Qt.AlignCenter)
        self.status_bar.setFixedHeight(30)
        self.status_bar.setStyleSheet('''
            QLabel {
                background-color: #FFFFFF;
                font-size: 16px;
                font-weight: bold;
                border-radius: 5px;
                padding: 5px;
            }
        ''')
        self.statusChanged.connect(self.status_bar.setText)

        # Labels for the camera feeds
        self.label_customer = QtWidgets.QLabel("Customer Camera")
        self.label_staff = QtWidgets.QLabel("Staff Camera")
        self.label_customer.setAlignment(QtCore.Qt.AlignCenter)
        self.label_staff.setAlignment(QtCore.Qt.AlignCenter)

        # Create two QLabel widgets for displaying the camera feeds
        self.label_cameras = [QtWidgets.QLabel() for _ in range(2)]
        for label_camera in self.label_cameras:
            label_camera.setFixedSize(480, 360)
            label_camera.setStyleSheet('''
                QWidget {
                    border-radius: 10px;
                    background-color: #D3D3D3;
                    border: 2px solid #B0B0B0;
                }
            ''')

        self.__layout_fun_button = QtWidgets.QHBoxLayout()
        self.button_start_recording = QtWidgets.QPushButton('Start recording')
        self.button_stop_recording = QtWidgets.QPushButton('stop recording')

        self.button_start_recording.setMinimumHeight(50)
        self.button_stop_recording.setMinimumHeight(50)

        self.__layout_fun_button.addWidget(self.button_start_recording)
        self.__layout_fun_button.addWidget(self.button_stop_recording)

        # Beautify buttons
        button_style = """
            QPushButton {
                background-color: #4CAF50; /* Brighter green when enabled */
                color: white;
                font-size: 18px;
                font-weight: bold;
                border-radius: 10px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #A9A9A9; /* Gray when disabled */
            }
        """
        self.button_start_recording.setStyleSheet(button_style)
        self.button_stop_recording.setStyleSheet(button_style)

        # Set cursor for enabled buttons
        self.button_start_recording.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.button_stop_recording.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

        # Set initial button states
        self.button_stop_recording.setEnabled(False)

        # Add the status bar to the layout
        self.__layout_main.addWidget(self.status_bar)

        # Layout for cameras and their labels
        self.__layout_camera = QtWidgets.QGridLayout()
        self.__layout_camera.setSpacing(10)
        self.__layout_camera.addWidget(self.label_customer, 0, 0)
        self.__layout_camera.addWidget(self.label_staff, 0, 1)
        self.__layout_camera.addWidget(self.label_cameras[0], 1, 0)
        self.__layout_camera.addWidget(self.label_cameras[1], 1, 1)

        self.__layout_main.addLayout(self.__layout_camera)
        self.__layout_main.addLayout(self.__layout_fun_button)

        print("UI setup complete")

    def slot_init(self):
        print("Initializing slots")
        self.button_start_recording.clicked.connect(self.start_recording)
        self.button_stop_recording.clicked.connect(self.stop_recording)

        # Initialize the timers and cameras
        for i, cam_num in enumerate(self.CAM_NUMS):
            timer_camera = QtCore.QTimer()
            self.timer_cameras.append(timer_camera)

            cap = cv2.VideoCapture()
            self.caps.append(cap)

            self.timer_cameras[i].timeout.connect(lambda i=i, cam_num=cam_num: self.show_camera(i, cam_num))
        print("Slots initialized")

    def open_cameras(self):
        print("Opening cameras")
        for i, cam_num in enumerate(self.CAM_NUMS):
            if not self.timer_cameras[i].isActive():
                flag = self.caps[i].open(cam_num)
                if not flag:
                    print(f"Warning: 請檢查相機 {cam_num} 是否連接正確")
                    msg = QtWidgets.QMessageBox.warning(self, 'warning', f"請檢查相機 {cam_num} 是否連接正確",
                                                        buttons=QtWidgets.QMessageBox.Ok)
                else:
                    self.timer_cameras[i].start(30)
                    print(f"Camera {cam_num} opened and timer started")
        print("Cameras open complete")

    def show_camera(self, index, cam_num):
        flag, image = self.caps[index].read()
        if flag:
            image = cv2.flip(image, 1)  # Flip horizontally if needed
            show = cv2.resize(image, (480, 360))  # Resize to fit your QLabel
            show = cv2.cvtColor(show, cv2.COLOR_BGR2RGB)  # Convert to RGB format
            show_image = QtGui.QImage(show.data, show.shape[1], show.shape[0], QtGui.QImage.Format_RGB888)
            self.label_cameras[index].setPixmap(QtGui.QPixmap.fromImage(show_image))
        else:
            print(f"Failed to read from camera {cam_num}")
    def record_audio(self):

        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 44100
        CHUNK = 1024
        WAVE_OUTPUT_FILENAME = f'audio/{self.service_id}_audio.wav'

        audio = pyaudio.PyAudio()
        stream = audio.open(format=FORMAT, channels=CHANNELS,
                            rate=RATE, input=True, input_device_index=self.mic_index,
                            frames_per_buffer=CHUNK)

        self.update_status("Recording...")
        print("Recording Audio...")
        frames = []

        while self.recording:
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

        print("Finished Recording Audio")
        stream.stop_stream()
        stream.close()
        audio.terminate()

        wf = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
        self.audio_filename = WAVE_OUTPUT_FILENAME
        print(f"Audio recorded and saved to {self.audio_filename}")


    def record_video(self):
        # 檢查資料夾是否存在，若不存在則創建
        if not os.path.exists('video'):
            os.makedirs('video')

        # 影片輸出檔案路徑
        output_filename0 = f'video/{self.service_id}_cam0.avi'
        output_filename1 = f'video/{self.service_id}_cam1.avi'

        # 打開兩台攝影機
        cap0 = cv2.VideoCapture(self.customer_cam_index)
        cap1 = cv2.VideoCapture(self.staff_cam_index)
        
        # 設置影像的寬和高
        cap0.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap0.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap1.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap1.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        # 設置影片參數
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        out0 = cv2.VideoWriter(output_filename0, fourcc, 30.0, (640, 480))
        out1 = cv2.VideoWriter(output_filename1, fourcc, 30.0, (640, 480))

        self.video_filenames.append(output_filename0)
        self.video_filenames.append(output_filename1)

        self.update_status("Camera recording started...")

        while self.recording:
            # 從兩台攝影機獲取畫面
            ret0, frame0 = cap0.read()
            ret1, frame1 = cap1.read()

            if ret0 and ret1:
                # 鏡頭實際輸出的尺寸可能不是 640x480；尺寸和 VideoWriter 不一致的影格會被直接丟掉
                out0.write(cv2.resize(frame0, (640, 480)))
                out1.write(cv2.resize(frame1, (640, 480)))


        # 釋放資源
        cap0.release()
        cap1.release()
        out0.release()
        out1.release()
        cv2.destroyAllWindows()

        self.update_status("Camera recording stopped.")

        print(f"Video saved to {output_filename0} and {output_filename1}")

    def start_recording(self):
        if not self.recording:
            self.recording = True
            self.button_start_recording.setEnabled(False)
            self.button_stop_recording.setEnabled(True)

            # 獲取當前時間
            self.start_time = datetime.now()
            print(f"Recording started at {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")

            self.service_id = 'S' + datetime.now().strftime('%Y%m%d%H%M%S')
            # 每段錄影重新開始：清掉上一段的檔名
            self.video_filenames = []
            self.audio_filename = ""

            # 啟動音訊錄製執行緒
            self.audio_thread = threading.Thread(target=self.record_audio)

            # 啟動影片錄製執行緒
            self.video_thread = threading.Thread(target=self.record_video)

            # 啟動所有執行緒
            self.audio_thread.start()
            self.video_thread.start()

    def stop_recording(self):
        if self.recording:
            self.recording = False

            # 等待音訊和影片錄製執行緒結束
            self.audio_thread.join()
            self.video_thread.join()
            # 執行緒最後送出的狀態還在事件佇列裡；先處理掉，免得之後蓋掉 "Saved ..."
            QtCore.QCoreApplication.sendPostedEvents(self.status_bar, QtCore.QEvent.Type.MetaCall)

            # 記錄結束時間
            self.end_time = datetime.now()
            print(f"Recording stopped at {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}")

            self.update_status("Ready for next recording")
            self.button_start_recording.setEnabled(True)
            self.button_stop_recording.setEnabled(False)

            # 上傳影片與音訊文件並更新資料庫
            self.upload_files_and_update_db()




    def upload_files_and_update_db(self):
        print("Uploading files to S3 and updating database")
        video_urls = [self.upload_to_s3(filename) for filename in self.video_filenames]
        audio_url = self.upload_to_s3(self.audio_filename)
        print(f"Uploaded video URLs: {video_urls}")
        print(f"Uploaded audio URL: {audio_url}")

        # 有任何一個檔案沒上傳成功就不寫資料庫，免得留下一筆找不到檔案的服務
        if len(video_urls) != 2 or not all(video_urls) or not audio_url:
            self.update_status(f"Upload failed, {self.service_id} was not saved")
            return

        if self.update_database(video_urls, audio_url):
            self.update_status(f"Saved {self.service_id}")
            print("Database update complete")
        else:
            self.update_status(f"Database error, {self.service_id} was not saved")

    def upload_to_s3(self, filename):
        if not filename or not os.path.exists(filename):
            print(f"Skip upload, file missing: {filename!r}")
            return ""
        key = filename.replace(os.sep, "/")  # 'video/S..._cam0.avi'：和 GCS 時代一樣，用本地相對路徑當 key
        try:
            url = storage.upload(filename, key)
            print(f"Uploaded {filename} to {url}")
            return url
        except Exception as e:
            print(f"Error uploading {filename} to S3: {e}")
            return ""

    def update_database(self, video_urls, audio_url):
        connection = mysql.connector.connect(**connection_config())
        cursor = connection.cursor()

        try:
            # Insert into tbl_service using stored procedure
            print(f"Inserting into tbl_service: {self.service_id}, {self.account_id}")
            cursor.callproc('sp_insert_service', (self.service_id, self.account_id, self.start_time, self.end_time, '1', 0))
            print("Inserted into tbl_service")

            # Insert into tbl_video_storage using stored procedure
            for i, video_url in enumerate(video_urls):
                cam_name = f"cam{i}"  # cam0 for customer, cam1 for staff
                print(f"Inserting video {video_url} into tbl_video_storage")
                cursor.callproc('sp_insert_video_storage', (self.service_id, f"{self.service_id}_{cam_name}.avi", video_url, datetime.now()))
                print(f"Inserted video {video_url} into tbl_video_storage")

            # Insert into tbl_audio_storage using stored procedure
            print(f"Inserting audio {audio_url} into tbl_audio_storage")
            cursor.callproc('sp_insert_audio_storage', (self.service_id, f"{self.service_id}_audio.wav", audio_url, datetime.now()))
            print(f"Inserted audio {audio_url} into tbl_audio_storage")

            connection.commit()
            return True
        except mysql.connector.Error as err:
            print(f"Error updating database: {err}")
            connection.rollback()
            return False
        finally:
            cursor.close()
            connection.close()

    def update_status(self, message):
        self.statusChanged.emit(message)
        print(f"Status updated: {message}")