# 第 0 階段假服務（`analysis_service/`）

設計、部署和取捨的文件在 repo 的 `docs/rebuild/`（只在開發的電腦上，不在 git 裡）；下面的連結指向那裡。

這是我們寫的替身，**不是原本的分析服務**。原本的分析服務是 repo 根目錄的 `service_analyzer/`，運作方式整理在 [../original_analyzer.md](../../docs/rebuild/original_analyzer.md)。

- **用途**：在本機（`steps/00_prepare.md`）和第一次上 AWS（`steps/05_aws_setup.md`）時，先確認「送分析 → 從 S3 下載 → 寫資料庫 → 狀態改成 3 → 桌面端看得到結果」這條流程接得起來。它不跑任何模型，一筆服務大約 1 秒就做完。真的分析服務上線之後，本機開發和換回舊服務時還是用它。
- **之後**：步驟 6 用它的 API、佇列和 Dockerfile 當骨架，接上原本的分析函式（[steps/06_analyzer.md](../../docs/rebuild/steps/06_analyzer.md) 的 6-2）。所以 HTTP 介面的行為會沿用，但分數、圖表、逐字稿都換成原本的做法。
- **SQS（T49，2026-11-02）**：拆成 API（`app.py`）和 worker（`worker.py`）兩種容器、同一個映像檔；接受的工作經過 RDS 的 outbox 送到 Amazon SQS，由 worker 領去做。見下面的「SQS 分析工作佇列」。

## 檔案

| 檔案 | 內容 |
|---|---|
| `app.py` | API（Flask＋gunicorn）：`POST /analyze` 在一個交易裡接受工作（工作、outbox、狀態 1 → 2）、`GET /healthz`（從 RDS 讀 worker 的心跳與工作數）、`POST /reports`；outbox 的送出器（執行緒）把工作送到 SQS。不做分析 |
| `worker.py` | worker（`python worker.py`）：從 SQS 領工作，假分數與 3 張圖、報表要畫的假數字 `report-data/{service_id}_{tag}.json`、有錄音時一份假的逐字稿（N6），檔名都帶這一次的 tag（token 的前 12 個字）；提交之後才排入服務結果（W1） |
| `analysis_queue.py` | 和 `analyzer/` 共用（逐位元組相同）：接受工作的交易、outbox 送出器、worker 的認領／續期／提交／重試、`/healthz` 的內容、管理指令 `python -m analysis_queue status｜fail｜requeue｜check-sqs｜alive` |
| `reports.py` | `POST /reports`：報表自己的佇列與 worker、每份的時間上限、重新啟動時補做（new-feature N4、N5） |
| `report_model.py` | 報表的資料：SQL、頁面模型、report-data JSON 的格式、N6 的 `explain()`（交給 `ollama_explain.py`） |
| `ollama_explain.py` | N6：報表右側的 AI 說明，呼叫 Ollama Cloud 的 `/api/chat`，檢查引言、數字、長度；`python -m ollama_explain --live-check` 是給你的連線檢查（只標準函式庫） |
| `service_outcome.py` | PLAN1 W1：分析有逐字稿時，自己的 worker 用同一個 Ollama 用戶端填 `tbl_service` 的服務結果與待辦（檢查證據，不擋狀態 3） |
| `report_pdf.py` | 報表的版面：matplotlib 畫 A4 白底 PDF（每次服務、員工期間、全公司期間） |
| `Dockerfile` | `python:3.12-slim`；預設跑 API（gunicorn，只能 1 個 worker），worker 容器用同一個映像檔跑 `python worker.py`；用 uv 照 `uv.lock` 安裝套件（含測試用的 dev 群組）；映像檔約 1.5 GB |
| `pyproject.toml`、`uv.lock` | 只有假服務需要的套件，沒有 TensorFlow、DeepFace；`uv.lock` 鎖住每個套件的版本（Linux x86_64、aarch64） |
| `analysis.env.example` | 環境變數範本 |
| `tests/` | 單元測試與整合測試（拋棄式 MySQL 8.4＋moto 模擬的 S3、SQS），跑法見下面的「測試」 |
| `tools/` | `render_samples.py` 畫範例 PDF；`check_report_copies.py` 檢查和 `analyzer/` 共用的檔案沒有分岔 |
| `e2e/` | 端對端測試：拋棄式 MySQL 8.4＋moto（模擬 S3 和 SQS）＋這個服務的 API 和兩個 worker 容器，用戶端用 repo 原本的程式；用法寫在 `run_e2e.sh` 開頭 |

## SQS 分析工作佇列（T49，2026-11-02）

計畫與介面在 [../sqs/PLAN.md](../../docs/rebuild/sqs/PLAN.md)，部署（EC2 上換成 API＋worker、換回舊服務）在 [../sqs/DEPLOY.md](../../docs/rebuild/sqs/DEPLOY.md)，取捨在 `docs/rebuild/tradeoff/T49_sqs_analysis_queue.md`，教學是 `docs/AWS tutorial/05_Amazon SQS.md`。

- **API**（`app.py`，預設的 CMD）：`POST /analyze {"service_id"}` 在一個交易裡建立（或重開）`tbl_analysis_job`、新增 `tbl_analysis_outbox`、把狀態 1 改成 2，commit 之後回 `200 queued`。已經在等或在做的回 `200 already_queued`（不再新增 outbox）；沒有這筆 `404`；已經完成 `409 already_completed`；格式不對 `400 invalid_service_id`；body 有真值的 `force` 回 `400 force_not_supported`（這一版不支援重跑已完成的服務，教學 5-1）；資料庫錯誤 `503 db_unavailable`。200 是「已經持久接受」，不是已經送到 SQS。
- **送出器**：API 裡的執行緒把 outbox 送到 `SQS_ANALYSIS_QUEUE_URL`，收到 MessageId 才標成 sent；失敗就退避重試。整合測試用 `OUTBOX_PUBLISHER=off` 關掉這條執行緒（自己呼叫 `publisher.run_once()`），正式環境不要設。
- **worker**（`python worker.py`，同一個映像檔）：一次領一則 → 在 RDS 認領 lease → 假分析（下載、假分數、圖表、上傳）→ 一個交易確認 lease、寫分數、圖表、逐字稿的位置、清服務結果、工作 done、狀態 3 → 刪訊息 → 服務結果排進這個 process 自己的佇列（W1，run 編號就是這一次的 token）。分析時每 60 秒續期 SQS 和 RDS。
- **失敗**：不刪訊息、交還 lease，服務維持 2；這一輪 3 次（`ANALYSIS_MAX_ATTEMPTS`）都失敗才把工作標成 failed、狀態改回 1（桌面端顯示失敗、Retry 會開新的一輪），訊息之後進 DLQ。下載不到的錄影、錄音不算失敗：記一行 log，那一種沒有資料（和以前一樣）。
- **輸出的檔名帶這一次的 tag**（token 的前 12 個字）：`charts/{sid}_{tag}_01_summary.png`…、`text/{sid}_{tag}_transcript.txt`、`report-data/{sid}_{tag}.json`（記在 `tbl_analysis_job.report_data_key`，報表從那裡讀）。被搶走 lease 的舊 worker 晚到的上傳不會蓋掉提交的那一份；資料庫只引用提交成功的那一次。
- **`/healthz`**：`{"status": "ok", "models", "workers", "queue_depth", "jobs", "outbox_pending", "oldest_pending_sec", "stale_jobs", "sqs"}`，都從 RDS 讀（`analysis_queue.health()`）。假服務沒有模型：有 worker 在等或在做就是 `"models": "ready"`，一個 worker 都沒有是 `"failed"`。資料庫連不上回 `503 {"status": "error", "database": "unavailable"}`。
- **管理**：`docker exec adam-analysis python -m analysis_queue status`（worker、卡住的工作、outbox）、`fail SID`、`requeue SID`、`check-sqs`（教學第 8 節）。
- **設定**：`analysis.env` 加上 `AWS_REGION`、`SQS_ANALYSIS_QUEUE_URL`，選用的 `ANALYSIS_*`（`analysis.env.example`）；`STATUS_WAIT_SEC` 拿掉了。資料庫要先套用 `new-feature/migrations/007_analysis_queue.sql` 和 `grants_007.sql`。
- 在 EC2 上跑假服務（例如換回假服務時），把 DEPLOY.md 第 6 步的映像檔換成這裡建的，worker 不用掛模型：

```bash
docker run -d --name adam-analysis --restart unless-stopped --env-file analysis.env -p 5000:5000 adam-analysis:<標籤>
docker run -d --name adam-analysis-worker-1 --hostname adam-analysis-worker-1 --restart unless-stopped \
  --stop-timeout 30 --env-file analysis.env --health-cmd "python -m analysis_queue alive" --health-interval 60s \
  adam-analysis:<標籤> python worker.py
```

- **本機用 moto 開發**：moto server（例如 `adam-s3`）上先建立 `adam-analysis-dlq` 和 `adam-analysis`（DLQ、Maximum receives 3），`SQS_ANALYSIS_QUEUE_URL` 填它回傳的網址（`http://adam-s3:5000/123456789012/adam-analysis`），再設 `AWS_ENDPOINT_URL=http://adam-s3:5000`。moto 重新啟動後佇列和 bucket 一樣會清空，要重建。

### 測試

在這個資料夾。映像檔把套件裝在 `/app/.venv`，原始碼掛在 `/src`（不能掛在 `/app` 上面，會蓋掉 `.venv`）；只改了 `.py` 不用重新 build：

```bash
docker build -t adam-pytest-analysis:sqs .
docker run --rm --network none -v "$PWD:/src:ro" -w /src adam-pytest-analysis:sqs python -m pytest -q -m "not integration"   # 單元測試
docker network create adam-pytest-net
docker run -d --name adam-pytest-mysql --network adam-pytest-net -e MYSQL_ROOT_PASSWORD=adam-test-root \
  -v "$PWD/../../myapp/tests/fixtures/schema.sql:/docker-entrypoint-initdb.d/1-schema.sql:ro" \
  -v "$PWD/tests/integration/grants.sql:/docker-entrypoint-initdb.d/2-grants.sql:ro" mysql:8.4
docker run --rm --network adam-pytest-net -e DB_HOST=adam-pytest-mysql -v "$PWD:/src:ro" -w /src adam-pytest-analysis:sqs \
  python -m pytest --cov --cov-report=term-missing                                                  # 全部＋覆蓋率
docker rm -fv adam-pytest-mysql && docker network rm adam-pytest-net
```

- 整合測試在同一個行程裡跑 API 和 worker：S3、SQS 都是 moto（每個測試重建 bucket、主佇列和 DLQ），送出器不開執行緒，`drain()` 送 outbox、讓 worker 一則一則做（`tests/integration/conftest.py`）。
- pytest 用 `--import-mode=importlib`（`pyproject.toml`）：`tests/unit/` 和 `tests/queue/` 都有 `test_analysis_queue.py`，預設的模式會撞名。整合測試互相 import 的小工具（`report_helpers.py`、`fake_helpers.py`）靠 `pythonpath` 裡的 `tests/integration` 找到。
- 假服務自己的：`test_fake_api.py`（`/analyze`、`/healthz`）、`test_fake_worker.py`（提交、檔名帶 tag、重試後狀態 1、lease 被接手）、`test_fake_transcript.py`、`test_fake_outcome.py`、`test_analysis_report_data.py`。佇列本身（重送、重複的訊息、DLQ、管理指令）在共用的 `tests/unit/test_analysis_queue.py`、`tests/queue/`。

## 報表（new-feature N4、N5，2026-11-01）

介面是 `docs/rebuild/new-feature/` 的 PLAN 第 7、8、10 節；取捨與驗證見 `docs/rebuild/tradeoff/T38_reports_service.md`。

- `POST /reports {"report_id": "<uuid>"}`：桌面端先在 `tbl_report` 寫一列 `generating`。回 `202`（已排入，重送也是 202）、`400`（不是 JSON 或沒有字串的 `report_id`）、`404`（沒有這一列）、`409`（不是 `generating`）、`503`（資料庫連不上）。
- 背景產生 PDF，上傳到 S3 的 `reports/{report_id}.pdf`（`application/pdf`），再把狀態改成 `ready`；失敗或超過 `REPORT_TIMEOUT_SEC`（預設 180 秒，包含 N6 一次約 90 秒的 AI 說明）就是 `failed`，`failure_reason` 是給管理員看的英文短句。兩種更新都帶 `WHERE report_status = 'generating'`。不碰 `published_at`、`published_by`。
- 圖的數字來自分析時存的 report-data：SQS 之後是提交成功的那一次的 `report-data/{service_id}_{tag}.json`（`tbl_analysis_job.report_data_key`），之前分析的服務是 `report-data/{service_id}.json`；都沒有時三張圖寫 No data。
- **共用檔案**：`report_model.py`、`report_pdf.py`、`reports.py` 和 `tests/unit/test_report_*.py`、`tests/integration/test_reports.py`、`tests/integration/report_helpers.py` 和 `analyzer/` 裡的逐位元組相同。改一邊就複製到另一邊，再在 repo 根目錄跑 `python3 -I services/analysis_service/tools/check_report_copies.py`（也檢查 PDF 的顏色是不是 `Theme.qml` 的 token）。
- **權限**：資料庫要 `db_schema.sql` 選用區塊裡報表那幾行 GRANT（`tbl_report` 的 SELECT 與四個欄位的 UPDATE、`tbl_account` 六個欄位（含 `account_username`，同名的人寫「名字 (登入帳號)」）、`tbl_text_storage` 兩個欄位、`sp_insert_text_storage` 的 EXECUTE）；已經建好的資料庫用 `new-feature/migrations/004_ai_transcripts.sql`。S3 要 `reports/*` 的 PutObject、`avatars/*` 和 `report-data/*` 的 GetObject、`report-data/*` 的 PutObject、`text/*` 的 GetObject 與 PutObject（N6）。

## AI 說明（new-feature N6，2026-11-01）

取捨、驗證、你要做的事見 `docs/rebuild/tradeoff/T42_ai_explanations.md`。

- 每次服務報表右側的 AI COMMENT／IMPROVEMENT（Customer、Staff）由 `ollama_explain.py` 呼叫 Ollama Cloud 產生：`POST {OLLAMA_BASE_URL}/api/chat`，`Authorization: Bearer {OLLAMA_API_KEY}`，`"stream": false`，`format` 是 JSON schema。送出兩側的分數、統計和逐字稿的英文翻譯；PDF 不印逐字稿。
- 設定在 `analysis.env`（`OLLAMA_BASE_URL`、`OLLAMA_API_KEY`、`OLLAMA_MODEL`，選用 `OLLAMA_TIMEOUT_SEC`、`OLLAMA_THINK`）。沒有金鑰或模型時不連任何地方，四格寫 Explanation unavailable；改了要重新建立容器（`docker restart` 不會重讀 `--env-file`）。
- 任何失敗（401／403／404／429／5xx、逾時、連線、回答不合格式）報表都照樣產生，四格寫 Explanation unavailable，`ai_model`、`ai_prompt_version` 留 NULL；log 只記原因，不記金鑰和逐字稿。
- **連線檢查**（只送假資料，會用掉一點額度）：`docker exec adam-analysis python -m ollama_explain --live-check`。
- **假服務的分數和逐字稿都是假的**：說明沒有意義，不要拿給使用者看（PLAN 9）。

測試的指令在上面「SQS 分析工作佇列」的「測試」。

2026-11-01：111 個測試通過（單元 74、整合 37），分支覆蓋率 96.6%。另外用 gunicorn 跑真的容器（拋棄式 MySQL＋moto server），從 `/analyze` 到三種 `/reports` 都走過一次 HTTP。加上 N6 之後（映像檔 `adam-analysis:p0-n6`）：313 個通過、1 個跳過（連線檢查），分支覆蓋率 97.75%。
`tests/unit/test_ollama_live.py` 平常一律跳過；`OLLAMA_*` 由 `tests/conftest.py` 從環境拿掉，所以在有 `analysis.env` 的容器裡跑測試也不會連 Ollama。

## 服務結果與待辦（PLAN1 W1，2026-11-01）

取捨、驗證、你要做的事見 `docs/rebuild/tradeoff/T44_service_outcome.md`。

- 分析寫分數的同一個交易，把 `tbl_service` 的 `service_outcome`、`service_todos`、`service_outcome_model` 清成 NULL、`service_outcome_run` 換成這次的編號；有逐字稿時排進 `service_outcome.py` 自己的佇列（和報表一樣一條 worker），不等 AI 就寫狀態 3。
- worker 把逐字稿的英文翻譯（不送分數）送到 Ollama Cloud，要回答五個結果之一（`Resolved`、`Documents missing`、`Handoff needed`、`Unresolved`、`Unknown`）和待辦，每一個都要附逐字稿裡的原句當證據；對不上的結果變 `Unknown`、對不上的待辦拿掉，最多 5 項。寫回時 `WHERE service_outcome_run = 這次的編號`：等 AI 時又重跑了的話，舊的結果寫不進去。
- 沒有逐字稿、AI 關著、任何失敗（401／404／429／5xx、逾時、格式不對）：三欄維持 NULL（桌面端顯示 Unknown），分數、圖表、狀態照常。
- 資料庫要套用 `new-feature/migrations/005_service_outcome.sql` 和它的 GRANT；還沒套用時分析照樣完成，只是不填，log 寫 `apply migration 005 and its GRANT`。
- **共用檔案**：`service_outcome.py`、`tests/unit/test_service_outcome.py`、`tests/integration/test_outcome_jobs.py` 和 `analyzer/` 的逐位元組相同（`tools/check_report_copies.py`）。整條流程的測試在 `tests/integration/test_fake_outcome.py`。
- **假逐字稿填出來的結果也是假的**：步驟 6 的真分析服務上線之前，不要給使用者看。

2026-11-01（映像檔 `adam-pytest-analysis:w1`）：411 個通過、1 個跳過（連線檢查），分支覆蓋率 97.99%（`service_outcome.py` 100%）。

## 和原本的分析服務有什麼不同

| 項目 | 假服務 | 原本的分析服務 |
|---|---|---|
| 分數 | 以 `service_id` 當亂數種子的假分數，60～95、小數 1 位；缺少的媒體對應的分數寫 -1；總分是有資料的模態的平均 | 文字、聲音、人臉三種真的分析，以 60 為基準加減最多 40（20～100、小數 2 位）；沒有資料時給 60；總分由兩個 `.pkl` 模型算出 |
| 圖表 | 3 張：`{sid}_{tag}_01_summary.png`（中文、有 PLACEHOLDER 浮水印）、`{sid}_{tag}_02_audio_level.png`（有音訊時）、`{sid}_{tag}_03_video_frames.png`（有影片時），1600×900；tag 是這一次分析的 token 前 12 個字（T49） | 5 張：`{sid}_emotion_TEXT.png`、`_emotion_AUDIO.png`、`_emotion_wave_face.png`、`_customer_emotions_face.png`、`_staff_emotions_face.png`，英文標題 |
| 逐字稿 | 有錄音時寫一份假的 `text/{sid}_{tag}_transcript.txt`（每句標 `(placeholder)`，6 句），格式和原本的一樣，只給 N6、W1 的流程用 | `text/{sid}_transcript.txt`，並寫入逐字稿資料表 |
| 寫資料庫 | worker 提交時一個交易：確認 lease、DELETE＋INSERT `tbl_service_score`、`tbl_charts_storage`，`sp_insert_text_storage`，清服務結果、工作 done、狀態 3 | 呼叫 3 個預存程序（`sp_insert_service_score`、`sp_insert_text_storage`、`sp_insert_charts_storage`），每一步各開一條連線 |
| 存的 URL | `s3://<bucket>/charts/<檔名>` | `https://storage.googleapis.com/<bucket>/charts/<檔名>` |
| 讀媒體 | 從資料庫的 URL 取檔名，到 S3 的 `video/`、`audio/` 下載；同一個鏡頭有多列時取檔名最新的 | 只接受 `gs://` URL，從 URL 裡的 bucket 下載 |
| 狀態 | API 接受工作時寫 2（同一個交易，桌面端不再寫）；提交時寫 3；失敗會重試，這一輪 3 次都失敗才改回 1（訊息進 DLQ）；工作記在 RDS、訊息在 SQS，容器重新啟動不會遺失 | 完成時無條件寫 3；失敗時停在 2，沒有任何恢復 |
| API | 驗證 `S` 加 14 位 ASCII 數字（`fullmatch`）；400、404、409、503；`/healthz`（worker 的心跳、工作數、outbox）；重複送出回 `already_queued`、不會重排；不支援 `force`（400 `force_not_supported`） | 只有 `POST /analyze`；缺 `service_id` 回 400、body 不是 JSON 時回 HTML 的 500，其他一律回 200 |
| 設定 | 環境變數 | 全部寫死在程式裡 |
| 執行環境 | API 和 worker 都不跑模型，t3.small 就夠 | 移植後用 `python:3.11-slim`，記憶體 8 GiB（實測峰值約 3 GB），見 `../analyzer/` |

## 資料庫名稱

`analysis.env.example` 的 `DB_NAME=adam`。原本的分析服務寫死的是 `ADAM`（大寫）。RDS 的資料庫名稱分大小寫，桌面端和分析服務的 `DB_NAME` 一定要相同；預設用 `adam`，待你決定。

## 時區

`DB_TIME_ZONE` 預設是 `America/Los_Angeles`（new-feature PLAN R4，原本是 `+08:00`），和 RDS 參數群組的 `time_zone`、報表切一天的時區相同；容器的 `TZ` 也是它。用具名時區時，MySQL 要有時區資料表（RDS 有；官方 `mysql:8.4` 映像檔第一次啟動時會載入）。

## 資料庫權限

`db_schema.sql` 最後的選用區塊建立 `adam_analysis`，每一行 GRANT 都標了 `[假服務]`、`[移植版]` 或 `[兩者]`。只跑假服務時，`[假服務]` 和 `[兩者]` 就夠；換成移植版之後，可以收回 `[假服務]` 的權限。

## 測試結果

2026-10-30 用加入分析服務部分之後的 `db_schema.sql` 重跑 `e2e/run_e2e.sh`：15/15 通過。2026-10-31 改用 uv 之後，在做完步驟 2-7 的複本上重跑：14/14 通過（2-7 拿掉了一項檢查）。

2026-11-02（SQS，T49；映像檔 `adam-pytest-analysis:w1`，原始碼掛在 `/src`）：446 個通過、1 個跳過（連線檢查），單元 343、整合 103。分支覆蓋率 88.65%：`analysis_queue.py` 只有 52%，它的測試（`tests/unit/test_analysis_queue.py`、`tests/queue/`）是共用的、另外寫；其他檔案合計 99%（`worker.py` 98%、`app.py` 96%）。另外用真的行程走過一次：拋棄式 MySQL＋moto server（S3、SQS）＋gunicorn 的 API＋`python worker.py`，`/analyze` 到狀態 3 約 1 秒、`status`、`check-sqs`、`alive` 都正常，`docker stop` worker 結束碼 0。
`e2e/` 還是 SQS 之前的寫法（一個容器、用戶端自己寫狀態 2），要改成 API＋worker＋moto 的 SQS 才能再跑。
