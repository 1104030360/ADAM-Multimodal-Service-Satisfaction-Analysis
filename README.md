# Multimodal AI System for Service Satisfaction Analysis

# 專案總覽

這是一個採用 PySide6 (Qt for Python) 和 Briefcase 打造的 AI 服務監控與分析應用程式，可用於跨平台打包。此應用程式提供即時服務錄製功能，包含影音擷取、人臉/聲音/文字分析，並設有管理儀表板以監控效能。

## 主要功能

*   即時影音錄製
*   人臉、聲音、文字分析
*   管理員儀表板
*   使用者權限管理
*   雲端儲存整合

## 架構

### 應用程式結構

本專案採用多層式架構，明確區分以下幾個部分：

1.  **前端 (QML)**：位於 `myapp/src/myapp/qml/pages/`，包含登入、服務人員主頁和管理員主頁的 UI 定義。
2.  **後端 (Python)**：位於 `myapp/src/myapp/`，包含業務邏輯、資料庫存取和媒體處理。
3.  **資料庫層**：透過 `database.py` 使用預存程序存取 MySQL 資料庫。
4.  **雲端儲存**：整合 Google Cloud Storage 以儲存媒體檔案（影片/音訊/圖表）。

### 關鍵元件

*   **主要進入點**：`myapp/src/myapp/main.py` - 應用程式初始化和 QML 引擎設定。
*   **資料庫連接器**：`myapp/src/myapp/database.py` - 用於 MySQL 連線管理的單例模式。
*   **初始化模組**：`myapp/src/myapp/init/` - 包含不同使用者類型的初始化邏輯：
    *   `server_home_init.py` - 服務人員/員工視圖，具備錄製和分析功能。
    *   `admin_home_init.py` - 管理員視圖，具備報告、排名和帳號管理功能。
    *   `record_widget.py` - 用於服務擷取的影音錄製小工具。

### 資料流程

1.  使用者透過 `main.py` → `DatabaseConnector.check_login()` 進行驗證。
2.  根據帳號等級，啟動對應的主頁（`server_home_init.launch()` 或 `admin_home_init.launch()`）。
3.  設定 QML 上下文屬性，將 Python 物件公開給 QML。
4.  QML UI 透過 Qt Slots/Signals 與 Python 後端互動。
5.  媒體檔案上傳至 S3，並將 URL 儲存於 MySQL。
6.  分析 API 端點非同步處理服務資料。

# 如何使用

### 環境設定

```bash
brew install portaudio uv     # PyAudio 編譯時需要 PortAudio
uv python install 3.13
cd myapp
uv sync                       # 照 uv.lock 建立 myapp/.venv 並安裝套件
```

### 執行應用程式

```bash
cd myapp
uv run briefcase dev   # 開發模式
uv run briefcase run   # 執行已打包的應用程式
```

### 建置應用程式

```bash
cd myapp
uv run briefcase build     # 建立可發布的套件
uv run briefcase package   # 建立安裝程式
```

### 測試

```bash
cd myapp
uv run pytest              # 執行測試
uv run pytest -k storage   # 只跑名稱含 storage 的測試
```

# 設定

### 環境變數

應用程式需要在根目錄下建立一個 `.env` 檔案，並包含以下內容：

*   `DB_HOST` - MySQL 資料庫主機
*   `DB_NAME` - 資料庫名稱
*   `DB_USER` - 資料庫使用者名稱
*   `DB_PASSWORD` - 資料庫密碼
*   `S3_BUCKET` - S3 bucket 名稱
*   `AWS_REGION` - bucket 所在的 region，例如 `us-east-1`
*   `AWS_PROFILE` - `~/.aws/credentials` 裡的 profile 名稱（可選；本機用 moto 測試時不要設）
*   `LOCAL_MEDIA_DIR` - 本地媒體檔案目錄（可選）
*   `LOCAL_CHARTS_DIR` - 本地圖表圖片目錄（可選）
*   `ANALYZE_API_URL` - 服務分析 API 端點 URL

**重要**：切勿將 `.env` 檔案或任何憑證提交至版本庫。

### Briefcase 設定

`pyproject.toml` 檔案包含所有打包設定，包括：

*   相依套件列表
*   平台特定需求
*   資源檔案（QML、圖片、初始化檔案）

# 資料庫結構

應用程式使用以下幾個主要資料表：

*   `tbl_account` - 使用者帳號，包含等級（1=服務人員, 2+=管理員等級）
*   `tbl_service` - 服務紀錄，包含狀態（1=未完成, 2=進行中, 3=已完成）
*   `tbl_service_score` - 分析分數（客戶/服務人員、文字/聲音/人臉）
*   `tbl_video_storage`, `tbl_audio_storage`, `tbl_charts_storage` - 媒體檔案 URL 儲存

# 開發注意事項

### QML-Python 整合

*   透過 `engine.rootContext().setContextProperty()` 將 Python 物件公開給 QML。
*   使用 `@Slot()` 裝飾器讓方法可以從 QML 呼叫。
*   使用 `Signal()` 進行 Python → QML 通訊。
*   使用 `Property()` 搭配 `notify` 信號實現響應式屬性。

### 資料庫存取

*   一律使用單例的 `DatabaseConnector` 實例。
*   建議使用預存程序進行資料庫操作。
*   使用字典游標以名稱存取欄位：`cursor(dictionary=True)`。
*   務必在 `finally` 區塊中關閉游標。

### 媒體處理

*   影片/音訊上傳至 S3，並將 URL 儲存於 MySQL。
*   本地下載會快取於 `init/media/` 和 `init/charts/`。
*   使用 `MediaService` 類別從 S3 下載。
*   在載入新服務資料前，呼叫 `clearDir()` 清理本地媒體檔案。

### 服務錄製

*   攝影機和麥克風枚舉由 `DeviceFinder` 處理。
*   錄製由 `RecordWidget`（獨立視窗）管理。
*   需要 `account_id` 將錄製與使用者關聯。
*   使用 OpenCV 進行影像擷取，PyAudio 進行聲音擷取。

# 程式碼組織備註

*   以 `Manager` 結尾的模型處理業務邏輯和 API 呼叫。
*   以 `Model` 結尾的模型處理 QML 的資料表示。
*   所有需要與 QML 互動的 QObject 子類別都必須呼叫 `super().__init__()`。
*   PDF 處理使用 PyMuPDF (fitz) 將頁面渲染為圖片以供顯示。

# 開發提示

*   修改 QML 時，使用 `uv run briefcase dev` 可立即反映變更。
*   新增 Python 相依套件時，在 `myapp/` 執行 `uv add 套件==版本`（會同時更新 `pyproject.toml` 和 `uv.lock`）。
*   資料庫連線為單例，修改會影響整個應用程式。
*   QML 除錯輸出會顯示在主控台，請尋找以 `qml:` 為前綴的訊息。
*   S3 需要 AWS 憑證：`AWS_PROFILE` 或環境變數；本機用 moto 測試時不需要真的憑證。
