# services/analyzer/：真的分析服務（移植版）

原本的分析程式（`service_analyzer/`，裡面寫死了密鑰，所以不在 git 裡）移植到 AWS 之後的版本：pyannote、faster-whisper、
Wav2Vec2、DeepFace 都在 EC2 的 CPU 上跑，分析工作經由 Amazon SQS 交給 worker（API 和 worker 用同一個映像檔）。
和第 0 階段的假服務 `../analysis_service/` 共用報表、AI 說明、服務結果、分析佇列的程式（`check_report_copies.py` 檢查）。
設計、部署和取捨的文件在 repo 的 `docs/rebuild/`（只在開發的電腦上，不在 git 裡）；下面的連結指向那裡。

| 檔案 | 內容 | 在哪一步 |
|---|---|---|
| `app.py` | API：接受分析（工作、outbox、狀態 2 同一個交易）、把 outbox 送到 SQS、`/healthz`、`/reports`；不載入模型 | 06 的 6-3 (3)；[SQS](../../docs/rebuild/sqs/PLAN.md) |
| `worker.py` | 分析的 worker（`python worker.py`）：載入模型、從 SQS 領工作、下載與上傳（檔名帶 token）、寫資料庫 | [SQS](../../docs/rebuild/sqs/PLAN.md) |
| `analysis_queue.py` | 分析工作佇列（和 `analysis_service/` 共用同一份）：接受、outbox、認領、續期、提交、管理指令 | [SQS](../../docs/rebuild/sqs/PLAN.md) |
| `analyzer.py` | 三個模態的分析與 5 張圖 | 06 的 6-3 (1) |
| `predict_0.py` | 總分（有 `.pkl` 就用，沒有就平均） | 06 的 6-3 (2) |
| `speech.py` | Whisper（faster-whisper）轉寫、依 pyannote 對齊說話者、OpenCC 轉繁體、opus-mt 翻成英文 | [real-analyze](../../docs/rebuild/real-analyze/PLAN.md)（取代 06 的 6-4） |
| `download_models.py` | 第一次部署時下載模型；pyannote 下載過之後不再需要 token | 06 的 6-5、6-6；[real-analyze](../../docs/rebuild/real-analyze/DEPLOY.md) |
| `pyproject.toml`、`uv.lock` | 依賴（torch 從 CPU 專用的來源）、測試套件、pytest 與覆蓋率的設定；`uv.lock` 只為 Linux 解析 | 06 的 6-5 |
| `Dockerfile`、`.dockerignore` | 用 uv 照 `uv.lock` 建置映像檔 | 06 的 6-5 |
| `analysis.env.example` | 設定範本 | 06 的 6-5 |
| `tests/conftest.py`、`tests/unit/` | 563 個單元測試（1 個要真的 Ollama，平常跳過；175 個是 `analysis_queue.py` 的）：不連網、不載入模型 | 06 的 6-5 |
| `tests/integration/` | 106 個整合測試：拋棄式 MySQL 8.4、moto（S3、SQS）、最小權限帳號（`grants.sql`） | 07 的 7-8 |
| `tests/queue/` | 45 個 `analysis_queue.py` 的整合測試（拋棄式 MySQL 8.4＋moto 的 SQS；和 `analysis_service/` 共用同一份） | [SQS](../../docs/rebuild/sqs/PLAN.md) |
| `e2e/` | Mac 上用真的模型跑完整的分析（拋棄式 MySQL＋moto）、Whisper 大小的比較、合成的兩人對話 | real-analyze 的 P1 |

驗證程度（2026-11-01，real-analyze）：470 個測試通過、1 個跳過，分支覆蓋率 99.16%；Mac 上用真的模型跑過端對端（`e2e/run_e2e.sh`，`medium` 與 `large-v3-turbo` 各 49 項檢查全過）。還沒驗證的是 EC2 上的速度（[DEPLOY.md](../../docs/rebuild/real-analyze/DEPLOY.md)）。

## 報表（new-feature N4、N5，2026-11-01）

加上 `POST /reports`：`reports.py`、`report_model.py`、`report_pdf.py` 和 `analysis_service/` 的逐位元組相同（說明、權限、HTTP 介面見 `../analysis_service/README.md` 的「報表」；取捨見 `docs/rebuild/tradeoff/T38_reports_service.md`）。這個資料夾另外改了：

| 檔案 | 改了什麼 |
|---|---|
| `analyzer.py` | 分析時順便算報表要畫的數字：Text 是 VADER 的 pos／neu／neg 平均（`text_proportions`），Audio 是三類機率的平均再除以總和（`audio_proportions`），Facial 是每個影格的秒數與 -1／0／1（`Analyzer.facial_series`）；`process_service()` 多回傳 `report_data`，`process_text()`、`process_audio()` 多回傳比例 |
| `app.py` | 把 `report_data` 存成 `report-data/{service_id}.json`（和圖表一樣先上傳再寫資料庫）；掛上 `/reports` 與報表的 worker；`DB_TIME_ZONE` 預設 `America/Los_Angeles`（new-feature PLAN R4，原本是 `+08:00`） |
| `Dockerfile` | `TZ=America/Los_Angeles`；加裝 `fonts-noto-cjk`（PDF 上的中文名字） |
| `pyproject.toml`、`uv.lock` | `matplotlib` 3.9.2 → 3.11.2（和假服務相同；3.9.2 嵌入 Noto CJK 時 PDF 的字讀不出來）；dev 群組加 `pypdf` |
| `analysis.env.example` | `DB_TIME_ZONE=America/Los_Angeles`、`REPORT_TIMEOUT_SEC=180` |
| `tests/` | 共用的報表測試（`test_report_*.py`、`test_reports.py`、`report_helpers.py`）；`test_analyzer.py`、`test_jobs.py` 加上 report-data 的檢查；`grants.sql` 加上報表的權限 |

## AI 說明（new-feature N6，2026-11-01）

`ollama_explain.py` 和它的測試（`tests/unit/test_ollama_explain.py`、`test_ollama_live.py`、`tests/integration/test_report_explanations.py`）和 `analysis_service/` 的逐位元組相同；說明、設定、連線檢查見 `../analysis_service/README.md` 的「AI 說明」，取捨見 `docs/rebuild/tradeoff/T42_ai_explanations.md`。這個資料夾另外改了：`analysis.env.example`（`OLLAMA_*`）、`pyproject.toml`（覆蓋率加上 `ollama_explain`）、`tests/conftest.py`（測試一律拿掉 `OLLAMA_*`）、`tests/integration/grants.sql`（`tbl_account.account_username`）。逐字稿用 `process_text()` 本來就寫的 `text/{service_id}_transcript.txt`，`analyzer.py` 沒有改。輕量映像檔裡 352 個通過、1 個跳過（連線檢查），分支覆蓋率 98.54%。

## 服務結果與待辦（PLAN1 W1，2026-11-01）

`service_outcome.py` 和它的測試（`tests/unit/test_service_outcome.py`、`tests/integration/test_outcome_jobs.py`）和 `analysis_service/` 的逐位元組相同；說明見 `../analysis_service/README.md` 的「服務結果與待辦」，取捨見 `docs/rebuild/tradeoff/T44_service_outcome.md`。這個資料夾另外改了：`app.py`（`write_results()` 的交易最後清掉上一次的服務結果；有逐字稿時排進 `outcome_jobs`）、`pyproject.toml`（覆蓋率加上 `service_outcome`）、`analysis.env.example`（說明 `OLLAMA_*` 也給 W1 用）、`tests/integration/conftest.py`（`FakeAnalysis.transcript_text`、等 `outcome_jobs` 的佇列）、`tests/integration/grants.sql`（四欄的 UPDATE、`service_outcome_run` 的 SELECT）、新的 `tests/integration/test_outcome_flow.py`。`analyzer.py` 不用改：`process_text()` 本來就寫說話者分離的逐字稿。輕量映像檔（`adam-pytest-analyzer-harness:w1`）裡 449 個通過、1 個跳過，分支覆蓋率 98.67%。

**沒有在真的映像檔驗證**：這台 Mac 沒有建 torch＋TensorFlow 的映像檔。2026-11-01 用 `tools/light_harness/` 的輕量映像檔（用法寫在它的 `Dockerfile` 開頭；正式的 `Dockerfile` 不會複製 `tools/`）跑了這個資料夾的全部測試（Python 3.11；torch、numpy、matplotlib 3.11.2、OpenCV（headless）、Flask、boto3、mysql-connector、VADER、pandas、scikit-learn 和 `uv.lock` 同版本；DeepFace、pyannote.audio、transformers、librosa 換成空殼，測試本來就會換掉它們）：155 個通過，分支覆蓋率 98.1%。真的模型算出來的比例與序列、`uv sync --locked` 換了 matplotlib 之後能不能裝起來，要在 EC2 上建映像檔時確認。

## 語音轉文字改用 Whisper（real-analyze，2026-11-01）

帳號是 AWS Free plan，Transcribe、Translate 只列在 Paid plan，所以改成全部在這台機器上跑（[PLAN](../../docs/rebuild/real-analyze/PLAN.md)、取捨 [T48](../../docs/rebuild/tradeoff/T48_real_analyzer_whisper.md)）：

- `speech.py`：faster-whisper（`WHISPER_MODEL`，預設 `medium`，CPU、int8）轉成中文 → 每個字歸給 pyannote 時間重疊最多的說話者
  → OpenCC `s2twp` 轉成台灣繁體 → opus-mt-zh-en 逐句翻成英文（給 VADER）。回傳的格式和以前一樣，逐字稿的檔案格式也一樣。
- `analyzer.py`：pyannote 每筆服務只跑一次，同時給逐字稿與聲音情緒用；文字和聲音用同一個角色對應（片段多的是服務人員）。
- 模型（`/models`）：多了 `whisper/`（`WHISPER_DIR`）與 `hf/` 裡的 opus-mt。下載與部署見 [DEPLOY.md](../../docs/rebuild/real-analyze/DEPLOY.md)；
  Mac 上的端對端與 Whisper 大小比較在 `e2e/`。
- 不再需要 Transcribe、Translate 的 IAM 權限；RDS 的權限見 [grants_real_analyzer.sql](../../docs/rebuild/real-analyze/grants_real_analyzer.sql)。

## 分析工作佇列（SQS，T49，2026-11-02）

原本的佇列在 gunicorn 的記憶體裡（`queue.Queue`、`inflight`、啟動時的 `recover()`），process 重新啟動就不見了；現在改成 RDS 的工作與 outbox＋Amazon SQS＋獨立的 worker process（教學 [05_Amazon SQS.md](../../AWS%20tutorial/05_Amazon%20SQS.md)、[PLAN](../../docs/rebuild/sqs/PLAN.md)、部署 [DEPLOY.md](../../docs/rebuild/sqs/DEPLOY.md)、取捨 [T49](../../docs/rebuild/tradeoff/T49_sqs_analysis_queue.md)）。同一個映像檔跑兩種容器：

- **API**（預設的 CMD，`gunicorn app:app`，不載入模型）：`POST /analyze` 由 `analysis_queue.accept()` 在一個交易裡建立工作、outbox、狀態 1 → 2，commit 之後回 200（桌面端不再自己寫 2）；body 有 `force` 回 400 `force_not_supported`（這一版不重跑已完成的服務）。同一個 process 的送出器把 outbox 送到 SQS（`OUTBOX_PUBLISHER=off` 時不開，給測試用）。`/healthz` 從 RDS 讀 worker 的心跳（`models`）、等待與執行中的工作數（`queue_depth`）、outbox；資料庫連不上是 503。`/reports` 不變，只在 API 跑一份。
- **worker**（`python worker.py`，載入模型；一台可以開兩個）：`analysis_queue.serve()` 先登記 loading 再載入模型，一次領一則、在 RDS 認領、分析期間續期。`worker.process()` 把錄影、錄音下載到暫存資料夾（做完一定刪掉）、分析，圖表、逐字稿、report-data 用帶 token 的檔名上傳（`{service_id}_{token 前 12 字}_…`，被搶走 lease 的舊 worker 晚到的上傳蓋不掉提交的那一份）；分數、檔案位置、清掉上一次的服務結果，和工作 done、狀態 3 在同一個交易提交，report-data 的 key 記在工作那一列（報表從那裡讀），提交之後才排服務結果（W1，run 編號就是 token）。失敗時狀態維持 2、交還給下一次，這一輪 3 次都失敗才改回 1（訊息之後進 DLQ）。
- 上面幾節寫在 `app.py` 的分析部分（下載與上傳、`report-data`、`write_results()`、`outcome_jobs`）現在都在 `worker.py`；`report-data/{service_id}.json` 只剩 SQS 之前分析的服務，報表找不到工作那一列記的 key 時才讀它。
- `analysis_queue.py` 和 `analysis_service/` 的逐位元組相同（`tools/check_report_copies.py` 檢查）；管理指令是 `python -m analysis_queue status｜fail｜requeue｜check-sqs｜alive`。
- 這個資料夾另外改了：`Dockerfile`（註解；worker 容器用 `--health-cmd "python -m analysis_queue alive"` 蓋掉 API 的 HEALTHCHECK）、`analysis.env.example`（`SQS_ANALYSIS_QUEUE_URL`、選用的 `ANALYSIS_*`，拿掉 `STATUS_WAIT_SEC`）、`pyproject.toml`（覆蓋率加上 `worker`、`analysis_queue`）、`tests/integration/`（moto 的主佇列＋DLQ；`drain()` 是 `publisher.run_once()` → `Worker.poll_once()`；`test_api.py`、`test_jobs.py`、`test_status.py`、`test_healthz.py`、`test_outcome_flow.py` 改寫成新的行為，拿掉 `finalize_status`、`recover()`、`force` 重跑的測試）。
- 驗證（2026-11-02）：完整映像檔 `adam-analyzer:merged`（不載入模型）裡跑這個資料夾的全部測試，713 個通過、1 個跳過（單元 563、整合 106（移植版自己的 49 個）、`tests/queue/` 45），分支覆蓋率 99.34%（`analysis_queue.py` 100%、`worker.py` 98%、`app.py` 96%，沒跑到的只有開送出器的執行緒和 `__main__`）。另外在同一個 process 裡用真的入口跑過一次（moto、拋棄式 MySQL、沒有錄影檔所以不用模型）：`import app` 開了送出器的執行緒，HTTP 的 `/analyze` → SQS → `worker.main()` 的主迴圈 → 狀態 3，載入中的 `/healthz` 是 loading，SIGTERM 之後 worker 登記 stopped、結束碼 0。真的模型、兩個 worker 的記憶體還沒驗證（端對端 `e2e/`、EC2）。
