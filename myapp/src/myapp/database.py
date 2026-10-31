import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv
from myapp.passwords import verify_password


def connection_config():
    """MySQL 連線設定，全部從環境變數讀取。DatabaseConnector 和錄影視窗共用這一份。"""
    config = dict(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', '3306')),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        connection_timeout=10,
    )
    if os.getenv('DB_SSL_CA'):  # RDS 的 global-bundle.pem 路徑：驗證伺服器憑證和主機名稱
        config.update(ssl_ca=os.getenv('DB_SSL_CA'), ssl_verify_cert=True, ssl_verify_identity=True)
    return config


class DatabaseConnector:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DatabaseConnector, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            print("Initializing DatabaseConnector")
            # 載入 .env
            load_dotenv()
            self.connection = self.create_connection()
            if self._connection:
                print("Database connection created successfully")
            else:
                print("Failed to create database connection")

    @property
    def connection(self):
        # 每次取用前先確認連線還在：啟動時沒連上、被關掉、閒置被切斷，都在這裡重新連線
        conn = getattr(self, '_connection', None)
        if conn is None:
            conn = self._connection = self.create_connection()
        else:
            try:
                conn.ping(reconnect=True, attempts=3, delay=1)
            except Error as e:
                print(f"Database reconnect failed: {e}")
        return conn

    @connection.setter
    def connection(self, value):
        self._connection = value

    def create_connection(self):
        try:
            # autocommit：不留著長交易，每次查詢都看得到其他連線剛寫入的資料
            connection = mysql.connector.connect(**connection_config(), autocommit=True)
            if connection.is_connected():
                print("Successfully connected to the database")
                return connection
            else:
                print("Failed to connect to the database")
                return None
        except Error as e:
            print(f"Error while connecting to MySQL: {e}")
            return None

    def check_login(self, username, password):
        try:
            print(f"Checking login for user: {username}")
            connection = self.connection
            if connection is None:
                print("Login failed: no database connection")
                return {'success': False}
            cursor = connection.cursor(dictionary=True)
            cursor.callproc('sp_get_login', [username])
            for result in cursor.stored_results():
                user = result.fetchone()
                if user and verify_password(password, user['account_password']):
                    print("Login successful")
                    return {'success': True, 'account_level': user['account_level'], 'account_id': user['account_id']}
                else:
                    print("Login failed: wrong username or password")
                    return {'success': False}
            return {'success': False}
        except Error as e:
            print(f"Error executing stored procedure: {e}")
            return {'success': False}

    def get_account_name(self, account_id):
        try:
            print(f"Getting account name for account ID: {account_id}")
            connection = self.connection
            if connection is None:
                return ""
            cursor = connection.cursor(dictionary=True)
            cursor.callproc('sp_get_account_name', [account_id])
            for result in cursor.stored_results():
                account = result.fetchone()
                if account:
                    print(f"Account name retrieved: {account['account_name']}")
                    return account['account_name']
                else:
                    print("Account name not found")
                    return ""
        except Error as e:
            print(f"Error executing stored procedure: {e}")
            return ""

    def close_connection(self):
        conn = getattr(self, '_connection', None)
        if conn and conn.is_connected():
            conn.close()
            print("Database connection closed")
