# analyzer/e2e/：在 Mac 上跑真的分析服務（real-analyze）

[`../../real-analyze/PLAN.md`](../../real-analyze/PLAN.md) 的 P1 第 5 點：用真的模型在本機跑完整的分析，比較 Whisper 的大小。Mac 的時間只當參考（Docker 用 10 核；EC2 是 2 vCPU）。正式的 `Dockerfile` 只複製 `*.py` 和 `tests/`，不會複製這個資料夾。

| 檔案 | 內容 | 在哪裡跑 |
|---|---|---|
| `make_dialogue.py` | 用 macOS 的 `say` 產生兩個人的對話（服務人員 Eddy、顧客 Meijia，11 句，約 59 秒，16 kHz） | Mac（`python3`，只用標準函式庫） |
| `fixtures/` | `dialogue.wav`、標準答案 `dialogue_zh_tw.txt`、每一句的時間 `dialogue_turns.json`。沒有個人資料 | — |
| `run_e2e.sh` | 端對端測試：拋棄式 MySQL 8.4＋moto＋真的分析服務 | Mac（bash） |
| `e2e_steps.py` | `run_e2e.sh` 的每一步（桌面端那一側、檢查、結果表） | 映像檔的拋棄式容器 |
| `bench_whisper.py` | `small`、`medium`、`large-v3-turbo` 的時間、記憶體、字錯率，和經過 `speech.py` 的說話者歸屬 | 映像檔的容器 |
| `dialogue_truth.py` | 標準答案的讀取、正規化、字錯率、每個字歸給誰 | 映像檔的容器 |

## 端對端測試

```bash
services/analyzer/e2e/run_e2e.sh                                   # medium，合成的對話＋S20261101203243
WHISPER_MODEL=small OUT=/tmp/adam-e2e-small services/analyzer/e2e/run_e2e.sh
CODE=services/analyzer services/analyzer/e2e/run_e2e.sh        # 用資料夾裡的 *.py，不重建映像檔
```

- **需要**：映像檔 `adam-analyzer:dev`（`docker build -t adam-analyzer:dev services/analyzer`）、`~/adam-models`（`download_models.py`）、`fixtures/dialogue.wav`、`mysql:8.4` 和 `python:3.12-slim`（moto 每次用 pip 安裝，要連網；分析服務本身不連網）。其他設定寫在 `run_e2e.sh` 開頭。
- **流程**：MySQL 第一次啟動時載入 git 裡的測試用 schema `myapp/tests/fixtures/schema.sql`（`SCHEMA=` 可以換）和 `../tests/integration/grants.sql`；分析服務用最小權限的 `adam_analysis` 連線，所以也驗證了這組權限夠用。每筆服務照桌面端的寫法上傳、登記（狀態 1），等 `/healthz` 的模型載入好，`POST /analyze`，收到 200 改成狀態 2，等到 3（完成）或回到 1（失敗）。
- **檢查**：1 列分數、圖表的列（有錄影 5 張，依固定順序；只有錄音是 TEXT、AUDIO 兩張）、1 列逐字稿、S3 上的圖、逐字稿（`Staff:`／`Customer:` 加下一行英文；中文沒有只有簡體才用的字）和 `report-data/<ID>.json`；合成的對話另外檢查兩個人分得開（至少 80% 的字歸對人）、印出字錯率。接著和桌面端一樣新增每次服務的報表，等到 `ready`，檢查 PDF 上的 Audio、Text、Facial 圖。有任何一項失敗時結束碼是 1；分析失敗（狀態回到 1）時也檢查沒有留下一半的結果。
- **結果表**：每筆服務的分析時間、模型載入時間、分析服務的記憶體峰值。記憶體每 2～3 秒記一筆，印兩個數字：`docker stats`，和容器裡所有行程的 RSS 加總。模型檔是 mmap 的：別的容器先讀過的部分不算在 `docker stats` 裡（剛開機的 EC2 上會算），所以要看 RSS。另外印 cgroup 的 `memory.peak`。
- **輸出**（`OUT`，預設 `mktemp`）：每筆服務一個資料夾（逐字稿、`report-data.json`、圖、`report.pdf`；桌面端的 `myapp/.venv` 有 PyMuPDF 時還有第 1 頁的 PNG）、`memory.log`、`analyzer.log`（分析服務全部的 log：每一步的時間；`speech.py` 只記數量和時間，不記逐字稿）。使用者的錄影是個人資料：`OUT` 不要放在 repo 裡；畫面上只印合成對話的逐字稿。
- **本機不跑**：報表的 AI 說明、服務結果（W1）。兩者都要 `OLLAMA_*`，這裡不設，所以報表寫 Explanation unavailable。
- **記憶體**：真的模型要好幾 GB；Docker Desktop 的記憶體不夠時，容器會被終止（最後印的容器狀態是 `OOMKilled=true`）。一次只跑一個載入真模型的容器（`run_e2e.sh` 或 `bench_whisper.py`）。

## 比較 Whisper 的大小

```bash
docker run --rm --network none -e HF_HUB_OFFLINE=1 -v "$HOME/adam-models:/models:ro" \
  -v "$PWD/services/analyzer/e2e:/e2e:ro" -v "$HOME/audio:/data:ro" -v /tmp/adam-bench:/out \
  adam-analyzer:dev python /e2e/bench_whisper.py --out /out/bench.json /data/S20261101030634_audio.wav
```

- **faster-whisper**：每個大小在自己的子行程裡載入、轉寫（選項和 `speech.py` 相同：`zh`、VAD、`word_timestamps`、beam 5、不給提示、int8、所有的核心），印出載入時間、記憶體峰值、每個檔案的轉寫時間與 RTF（轉寫時間 ÷ 錄音長度），以及合成對話的字錯率。
- **分析服務的語音這一步**：pyannote 用 `analyzer.diarize()` 分一次合成對話，每個大小再用 `speech.transcribe_and_translate()` 轉寫，照分析服務的規則（片段最多的是服務人員）對成 Staff、Customer，印出歸對人的字的比例（attribution）、逐字稿的字錯率、這一步的時間和記憶體峰值（Whisper＋翻譯模型），以及合成對話的逐字稿。
- 字錯率先把兩邊都轉成台灣繁體（OpenCC `s2twp`）、阿拉伯數字換成國字、去掉標點和空白。使用者的錄音只印時間和字數。`S20261101101543` 幾乎沒有聲音（VAD 找不到有人說話），不適合拿來比較。
