# deploy/：EC2 上的部署腳本

分析服務（`services/analyzer/`）的自動部署在 EC2 上的那一半。GitHub Actions 建好、測好映像檔，推到 GHCR，把「應該跑哪一版」寫進
Parameter Store，再透過 SSM Run Command 叫這裡的 `deploy.sh`；EC2 停機時，下次開機由 `adam-deploy.service` 補上。
整體的計畫在 `docs/rebuild/cd/PLAN.md`（第 3 節是流程、第 8 節是 Console 的設定、第 9 節是 migration），各部分之間的約定在同一個資料夾的
`CONTRACTS.md`（這兩份只在維護者的電腦上，不在 git 裡）。

## 這些檔案

| 檔案 | 做什麼 | 在 EC2 上的位置 |
|---|---|---|
| `deploy.sh` | 部署：讀版本 → 拉映像檔 → migration → check-db → 換容器 → 等 ready；失敗就換回上一版 | `/opt/adam/deploy.sh`（每次部署由 workflow 換成那個 commit 的） |
| `adam-deploy.service` | systemd unit：開機時跑一次 `deploy.sh`（不帶參數） | `/etc/systemd/system/adam-deploy.service`（`install.sh` 裝） |
| `install.sh` | 第一次安裝（可以重跑）：檢查、裝上面兩個檔案、搬 `analysis.env`、enable unit、確認讀得到 Parameter Store | 不用留在 EC2 上 |
| `tests/run.sh` | 測試：在 `amazonlinux:2023` 的容器裡用假的 docker、aws、curl 跑 `deploy.sh`、`install.sh` 的每一條路 | － |

EC2 上其他的檔案：`/opt/adam/analysis.env`（分析服務的設定，600、root）、`/opt/adam/models`（模型，唯讀掛進 worker）、
`/opt/adam/deployed-commit`（現在跑的版本）、`/opt/adam/failed-commit`（最後一個部署失敗、換回過上一版的版本）、
`/opt/adam/deploy.conf`（選用，見「設定」）、log `/var/log/adam-deploy.log`。

## deploy.sh 做什麼

```text
讀 Parameter Store 的 /adam/analyzer/desired-commit
  ├─ 不存在 → 什麼都不做（0）
  ├─ 和 /opt/adam/deployed-commit 一樣 → 什麼都不做（0）
  └─ 和 /opt/adam/failed-commit 一樣、又沒帶 --retry-failed（開機時）→ 跳過（0）
事前檢查：analysis.env、模型的資料夾、docker、/adam/db/migrator-password、硬碟（要拉映像檔時至少 10 GB）
docker pull ghcr.io/1104030360/adam-analyzer:<commit>、adam-db-migrations:<commit>（本機已經有的不拉）
migration：adam-db-migrations 的 apply，帳號 adam_migrator，密碼只經過環境變數（docker run -e DB_PASSWORD）
check-db：新的映像檔、analysis.env 的帳號，python -m analysis_queue check-db（只讀）
  ↑ 到這裡任何一步失敗，舊的容器完全不動
現在的映像檔記成 adam-analyzer:previous
worker 先 docker stop（最多 30 秒：停止領新工作、手上那一筆交還給 SQS、登記 stopped）→ 換 API → 起 worker
等 /healthz 的 models: ready，而且每個新的 worker 自己回報 ready（最多 300 秒）
  ├─ 成功 → 寫 deployed-commit、清掉 failed-commit、印 python -m analysis_queue status、清掉用不到的舊映像檔
  └─ 失敗 → 寫 failed-commit，用 adam-analyzer:previous 照原樣重建、再等一次 ready
```

- **同一時間只跑一個**：`flock /run/adam-deploy.lock`，最多等 15 分鐘（`LOCK_WAIT`），等不到是 10。
- **容器照原樣重建**：`docker run` 的參數照 `docs/rebuild/sqs/DEPLOY.md` 第 6 節，只換映像檔和 `--env-file /opt/adam/analysis.env`，
  另外加兩樣：`--label adam.commit=<commit>`（看得出哪個容器是哪一版）、`--log-opt max-size=20m --log-opt max-file=5`（見「硬碟」）。
  原本有幾個 `adam-analysis-worker-*` 就重建幾個、名字不變；原本停著的照樣停著（`docker create`，不啟動；沒啟動過的容器，開機時 docker
  也不會自己啟動它），一個都沒有在跑時啟動第一個（部署要有 worker 才確認得了 ready），一個都沒有時建 `adam-analysis-worker-1`。
- **換容器的那幾秒**：API 停幾秒，桌面端剛好送出分析時會跳 Retry；worker 手上的分析交還給 SQS，由新的 worker 重做（這一次不算一次嘗試）。
  記憶體裡的佇列會不見：API 正在產生的報表（桌面端 10 分鐘後顯示 Timed out，重新產生即可）、worker 還沒做完的服務結果（W1，欄位留 NULL）。
- **analysis.env 不 source**：裡面有秘密、值可能有特殊字元，只用 `sed` 取出 `DB_HOST`、`DB_PORT`、`DB_NAME` 給 migration。
- **環境**：SSM、systemd 給的環境變數很少，`deploy.sh` 自己設 `PATH`、`HOME=/root`、`LC_ALL=C`。

### 怎麼判斷新版 ready

只看 `/healthz` 會被舊的 worker 騙：新舊 worker 的 `worker_id` 一樣（`hostname:1`，`--hostname` 一樣、process 都是 PID 1），
舊的被 kill（30 秒內沒停下來、沒登記 `stopped`）時，它最後的 `ready` 在 120 秒內（`ANALYSIS_WORKER_STALE_SEC`）還算數；
新的 worker 要是一直重新啟動、根本沒登記過，`/healthz` 還是說 `ready`。所以 ready 要同時符合：

1. `/healthz` 回 200、`"models": "ready"`：新的 API 起來了、連得到資料庫、資料庫裡有 ready 的 worker。
2. 每個這次啟動的 worker，**它自己**登記的狀態是 `ready` 或 `busy`：用 `docker exec` 讀容器裡的 `analysis_queue.ALIVE_FILE`
   （`Worker.report()` 每次登記都寫，在容器自己的 `/tmp`，新的容器讀不到舊 worker 的），而且檔案的時間晚於這個容器最近一次啟動
   （`docker inspect` 的 `StartedAt`；重新啟動之前的 process 留下的不算）。

另外兩種情況不等滿 300 秒：worker 自己登記了 `failed`（載入模型失敗，不會自己好）；新的容器重新啟動到 3 次（`MAX_RESTARTS`）。

沒採用的做法：只等舊 worker 登記 `stopped`（被 kill 的就沒有，還要另外等 120 秒）；只看 `docker inspect` 的狀態或 RestartCount
（看不出模型載好了沒）；`status` 的輸出（新舊 worker 的 `worker_id` 一樣，分不出來）。

## 第一次安裝（EC2）

先做完 `docs/rebuild/cd/PLAN.md` 第 8 節（特別是第 5 步 EC2 角色的 `ADAMDeployReads`、第 8、9 步 `adam_migrator` 和它的密碼）。
GHCR 的 `adam-analyzer`、`adam-db-migrations` 兩個套件要設成 Public（第一次推上去之後才有），EC2 才能不登入就拉。

1. **把檔案拿到 EC2 上**，兩種擇一：
   - 從 GitHub（`deploy/` 已經合併到 `main` 之後；要固定版本就把 `main` 換成 commit 的 SHA）：

     ```bash
     mkdir -p ~/adam-deploy && cd ~/adam-deploy
     for f in deploy.sh adam-deploy.service install.sh; do
       curl -fsSLO "https://raw.githubusercontent.com/1104030360/ADAM-Multimodal-Service-Satisfaction-Analysis/main/deploy/$f"
     done
     ```

   - 從 Mac（repo 根目錄）：`scp -i <金鑰檔> -r deploy ec2-user@<Elastic IP>:~/adam-deploy`
2. **安裝**：`sudo bash ~/adam-deploy/install.sh`
   - 檢查 docker、aws、curl、flock、systemctl、timeout，docker 和 amazon-ssm-agent 在跑、docker 開機會啟動。少了指令會停下來；其他的列在最後。
   - 裝 `/opt/adam/deploy.sh`、`/etc/systemd/system/adam-deploy.service`。
   - `/home/ec2-user/analyzer/analysis.env` 搬到 `/opt/adam/analysis.env`（600、root）。`/opt/adam/analysis.env` 已經有就不蓋，
     只提醒舊的那一份可以刪。
   - `systemctl enable adam-deploy.service`。**不啟動**：不在安裝時突然換掉正在跑的容器，也不和 GitHub 送來的部署排隊。
   - 讀一次 Parameter Store：`desired-commit` 還沒有不是錯（第一次跑 workflow 之後才有）；密碼只問「解密之後長度大於 0 嗎」，不印出來。
3. **第一次部署**：GitHub → Actions → Deploy analyzer → Run workflow。或者 Parameter Store 已經有版本、想直接在 EC2 上部署：
   `sudo /opt/adam/deploy.sh --retry-failed`。
4. 第一次部署成功之後，照「硬碟」刪掉手動建的舊映像檔。

從現在起容器由 `deploy.sh` 換：不要再照 `sqs/DEPLOY.md` 手動 `docker run`（下次部署會照「原本有哪些容器」重建）。
`analysis.env` 改了、要讓容器重新讀：`sudo rm /opt/adam/deployed-commit && sudo /opt/adam/deploy.sh --retry-failed`
（同一版照一般的部署再走一次：容器重建，migration、check-db 照樣跑，`previous` 不變）。

`adam-deploy.service` 改了要在 EC2 上重跑一次 `install.sh`（先照第 1 步拿新的檔案）；`deploy.sh` 不用，workflow 每次部署都換成那個 commit 的。

## GitHub Actions 怎麼叫它

`deploy-analyzer.yml` 送給 SSM Run Command（`AWS-RunShellScript`）的指令：部署哪個 commit，就用那個 commit 的 `deploy.sh`。

```bash
set -eu
install -d -m 755 /opt/adam
curl -fsSL --retry 3 -o /opt/adam/.deploy.sh.new "https://raw.githubusercontent.com/<repo>/<commit>/deploy/deploy.sh"
chmod 755 /opt/adam/.deploy.sh.new
mv -f /opt/adam/.deploy.sh.new /opt/adam/deploy.sh   # 換 inode：正在跑的舊 deploy.sh 不受影響
/opt/adam/deploy.sh --retry-failed [--allow-destructive-migrations]
```

- `--retry-failed`：`desired-commit` 等於 `/opt/adam/failed-commit` 也照樣再試。workflow 一律帶；開機時不帶（同一個失敗的版本不要每次開機都換過去再換回來）。
- `--allow-destructive-migrations`：轉給 migration 的 `--allow-destructive`。只有手動 Run workflow、勾選「允許破壞性的 migration」時才帶，先在 RDS 建 snapshot。
- SSM 的 `executionTimeout` 要比最久的部署長：等鎖 15 分鐘＋第一次拉 6 GB 的映像檔、等 ready、換回上一版，建議 3600 秒。

### 結束碼

| 結束碼 | `result=` | 意思 | 舊版還在跑嗎 | 怎麼辦 |
|---|---|---|---|---|
| 0 | `deployed` | 部署好了 | － | |
| 0 | `up-to-date`、`no-desired-commit`、`skipped-failed` | 沒有要做的：已經是這一版、還沒有 desired-commit、開機時遇到失敗過的版本 | － | |
| 10 | `locked` | 等不到鎖（另一個 `deploy.sh` 在跑，通常是開機時的 `adam-deploy.service`） | 是 | 等它跑完（`systemctl status adam-deploy`），再 Run workflow |
| 11 | `precheck-failed` | 事前檢查沒過：輸出說是哪一項（`analysis.env`、模型、docker、Parameter Store、硬碟、參數） | 是 | 照輸出修；讀不到 Parameter Store 是 EC2 角色的 `ADAMDeployReads` |
| 12 | `pull-failed` | 拉不到映像檔 | 是 | GHCR 的套件是不是 Public、workflow 的 push 有沒有成功 |
| 13 | `destructive-migration` | 有破壞性的 migration 待套用（這一輪什麼都沒套用） | 是 | RDS 建 snapshot → 手動 Run workflow、勾選允許破壞性的 migration |
| 14 | `migration-failed` | migration 失敗（輸出附 runner 的最後幾十行） | 是 | `services/db/README.md` |
| 15 | `check-db-failed` | 新版要的資料表、欄位、權限不全（輸出列出缺的） | 是 | 通常是少了 migration 或 GRANT |
| 16 | `rolled-back` | 新版 5 分鐘內沒有 ready（或容器建不起來），已經換回上一版 | 是（上一版） | 看附上的 `/healthz` 和容器 log；修好再合併，或 Run workflow 重試 |
| 17 | `rollback-failed` | 換回上一版也失敗，或沒有上一版 | 不一定 | 分析服務可能停了：見「出問題時」 |

非預期的錯誤（`result=error`）或被中斷（`result=interrupted`）時是其他的結束碼。

### 輸出

- stdout 精簡（SSM 只拿得到前 24,000 個字元）：一個步驟一兩行；失敗時附上相關的輸出、`/healthz` 和容器 log 的最後 25 行；
  成功時印 `python -m analysis_queue status`（最多 60 行）。附上的這些合計最多約 14 KB（一行最多 300 位元組），超過的只說在 log 裡，
  最後一行一定在 SSM 拿得到的範圍內。
- 完整的（每個指令的全部輸出）append 到 `/var/log/adam-deploy.log`（640、root）；超過 5 MB 就換成 `.1`（只留一份舊的）。
- 最後一行固定是 `deploy: result=<字> code=<數字> commit=<sha>`；還不知道 commit 時（沒有 desired-commit、讀不到 Parameter Store、
  等不到鎖、參數不對）是 `commit=none`。
- 密碼不會出現在輸出、log、任何指令的參數裡；有人用 `bash -x` 除錯時，經手密碼的那幾行會先關掉 trace。

## 開機時

`adam-deploy.service`：`Type=oneshot`，在 `network-online.target` 和 `docker.service` 之後跑 `/opt/adam/deploy.sh`（不帶參數），
最多 45 分鐘。失敗只會讓這個 unit 變成 failed，舊的容器照樣由 docker 的 `--restart unless-stopped` 跑起來，SSH、SSM agent 不等它。

- 停機期間合併的版本：開機時部署（`desired-commit` 和 `deployed-commit` 不一樣）。
- 失敗、換回過上一版的版本（`failed-commit`）：開機時不再試，GitHub 的部署（`--retry-failed`）才會再試。
- 只有 16、17 會寫 `failed-commit`。12～15 沒有換過容器，下次開機照樣再試（拉映像檔、migration、check-db 都很快，問題可能已經修好）。

## 出問題時

```bash
sudo tail -n 120 /var/log/adam-deploy.log                     # 完整的紀錄（每個指令的輸出）
systemctl status adam-deploy; journalctl -u adam-deploy -b    # 開機時那一次
cat /opt/adam/deployed-commit /opt/adam/failed-commit
sudo docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Label "adam.commit"}}'
sudo docker exec adam-analysis python -m analysis_queue status
sudo docker logs --tail 100 adam-analysis-worker-1
```

### 換回上一版

- **用 GitHub**（建議）：Actions → Deploy analyzer → Run workflow，選上一個 commit。它照一般的部署走（migration 只加不刪，舊版照樣能用），
  `deployed-commit` 也跟著改。
- **GitHub 用不了時，直接在 EC2 上換成 `adam-analyzer:previous`**（`deployed-commit` 不會改，下一次部署照常）：

```bash
sudo docker stop adam-analysis-worker-1 && sudo docker rm adam-analysis-worker-1   # 有 worker-2 也一樣
sudo docker rm -f adam-analysis
sudo docker run -d --name adam-analysis --restart unless-stopped --env-file /opt/adam/analysis.env -p 5000:5000 \
  --log-opt max-size=20m --log-opt max-file=5 adam-analyzer:previous
sudo docker run -d --name adam-analysis-worker-1 --hostname adam-analysis-worker-1 --restart unless-stopped \
  --stop-timeout 30 --env-file /opt/adam/analysis.env -v /opt/adam/models:/models:ro \
  --health-cmd "python -m analysis_queue alive" --health-interval 60s --health-start-period 300s \
  --log-opt max-size=20m --log-opt max-file=5 adam-analyzer:previous python worker.py
for i in $(seq 1 60); do curl -s http://127.0.0.1:5000/healthz | grep -q '"models": *"ready"' && break; sleep 5; done
curl -s http://127.0.0.1:5000/healthz
```

同樣的指令把 `adam-analyzer:previous` 換成 `ghcr.io/1104030360/adam-analyzer:<commit>`，就是手動換成本機有的某一版。

### 鎖

鎖跟著 process：`deploy.sh` 結束（包括被 kill）就放掉，`/run` 開機時也會清空。結束碼 10 時看是誰在跑：`ps -ef | grep '[d]eploy.sh'`。

## 設定（`/opt/adam/deploy.conf`，選用）

bash 語法，`deploy.sh` 一開始讀（先用 `bash -n` 檢查語法）。只寫要改的，例如 `MIN_FREE_GB=8`。`install.sh` 也讀前面幾個。

| 變數 | 預設 | 說明 |
|---|---|---|
| `REGION` | `us-east-1` | Parameter Store 的區域 |
| `DESIRED_PARAM` | `/adam/analyzer/desired-commit` | 要部署的 commit |
| `PASSWORD_PARAM` | `/adam/db/migrator-password` | `adam_migrator` 的密碼（SecureString） |
| `ENV_FILE` | `/opt/adam/analysis.env` | 分析服務的設定 |
| `MODELS_DIR` | `/opt/adam/models` | 模型，唯讀掛到 worker 的 `/models` |
| `ANALYZER_REPO` | `ghcr.io/1104030360/adam-analyzer` | 分析服務的映像檔（tag 是 commit） |
| `MIGRATIONS_REPO` | `ghcr.io/1104030360/adam-db-migrations` | migration 的映像檔 |
| `PREVIOUS_TAG` | `adam-analyzer:previous` | 上一版 |
| `MIGRATOR_USER` | `adam_migrator` | migration 的帳號 |
| `API_NAME`、`WORKER_PREFIX` | `adam-analysis`、`adam-analysis-worker-` | 容器的名字 |
| `API_PORT` | `5000` | API 對外的 port（容器裡固定 5000） |
| `HEALTH_URL` | `http://127.0.0.1:$API_PORT/healthz` | |
| `HEALTH_TIMEOUT`、`HEALTH_INTERVAL` | `300`、`5` | 等 ready 最多幾秒、多久看一次 |
| `MAX_RESTARTS` | `3` | 新的容器重新啟動到幾次就不等了 |
| `MIN_FREE_GB` | `10` | 要拉映像檔時，docker 的資料夾所在的硬碟至少要剩幾 GB |
| `LOCK_WAIT` | `900` | 另一個 `deploy.sh` 在跑時最多等幾秒 |
| `PULL_TIMEOUT`、`PULL_RETRY_DELAY` | `1800`、`10` | 一次 pull 最多幾秒、失敗（網路）之後隔幾秒再試（最多 3 次） |
| `MIGRATE_TIMEOUT`、`CHECK_DB_TIMEOUT` | `900`、`300` | |
| `DOCKER_TIMEOUT`、`AWS_TIMEOUT` | `120`、`60` | 其他 docker、aws 指令最多幾秒（docker daemon 卡住時不會一直等） |
| `LOG_FILE`、`LOG_MAX_BYTES` | `/var/log/adam-deploy.log`、`5000000` | |
| `LOCK_FILE`、`DEPLOYED_FILE`、`FAILED_FILE` | `/run/adam-deploy.lock`、`/opt/adam/deployed-commit`、`/opt/adam/failed-commit` | |
| `LOG_OPTS` | `(--log-opt max-size=20m --log-opt max-file=5)` | 容器的 log 選項（bash 陣列；`LOG_OPTS=()` 是不加） |
| `STDOUT_BUDGET` | `14000` | 附在 stdout 的詳細輸出最多幾個位元組（再大，最後一行可能超出 SSM 拿得到的 24,000 個字元） |

## 硬碟

硬碟 40 GB：模型約 5 GB，一個分析服務的映像檔約 5～6 GB（套件沒改時，新舊兩版共用大部分的層），照 real-analyze/DEPLOY.md 加的 swap 4 GB。
GHCR 的映像檔是在 GitHub 上建的，和 EC2 上手動建的 `:1`、`:2` 不共用層：第一次部署要整個拉（約 6 GB）。

- **拉之前**：要拉映像檔時，docker 的資料夾所在的硬碟至少要剩 `MIN_FREE_GB`（10 GB）。不夠就先清掉下面「成功之後」會清的那些，
  還是不夠就停（11）、列出現在的映像檔。本機已經有的映像檔不拉，也不看硬碟。
- **成功之後**：只清 `deploy.sh` 自己拉的兩個 repo（`ANALYZER_REPO`、`MIGRATIONS_REPO`）：分析服務留現在的和 `adam-analyzer:previous`
  指的那一個，migration 只留這一版的。`docker rmi` 不加 `-f`，還有容器在用的 docker 會拒絕。
- **手動建的映像檔一律不碰**（`adam-analyzer:1`、`:2`、`mysql:8.4`、e2e 的）：`deploy.sh` 分不出哪些還有用，刪錯了救不回來。
  第一次部署之後 `adam-analyzer:previous` 就是 `:2`；`:1` 是改走 SQS 之前的版本，配現在的桌面端會誤報分析失敗（sqs/DEPLOY.md 第 10 節），
  不會再用到，可以馬上刪（硬碟不夠 10 GB、第一次部署停在 11 時，先刪它）。
  第二次部署成功之後 previous 換成第一次部署的 commit，`:2` 也可以刪：

  ```bash
  sudo docker image ls adam-analyzer          # previous 和 :2 的 IMAGE ID 不一樣了才刪 :2
  sudo docker rmi adam-analyzer:1 adam-analyzer:2
  sudo docker builder prune                   # 在 EC2 上建置留下的快取（不影響映像檔）
  sudo docker system df                       # 現在用了多少
  ```

- **容器的 log**：docker 預設的 json-file 不輪替；worker 連不上 SQS 時每 5 秒、連不上資料庫時每次心跳都寫一行警告，幾個月會長到幾百 MB。`deploy.sh` 建的容器都加上
  `--log-opt max-size=20m --log-opt max-file=5`（每個容器最多 100 MB）；每次部署都是新的容器，舊的 log 跟著容器刪掉。
- **部署的 log**：`/var/log/adam-deploy.log` 超過 5 MB 換成 `.1`，最多約 10 MB。

## 測試

```bash
bash deploy/tests/run.sh
```

- 在 `amazonlinux:2023`（和 EC2 一樣的發行版、bash、coreutils、flock）的容器裡，用 `tests/fakes/` 的假 docker、aws、curl、df、systemctl
  跑 `tests/cases.sh`：沒有要做的三種、正常部署（第一次、第二次、兩個 worker、停著的 worker、沒有 worker）、pull 失敗和重試、migration 回 3 和其他錯誤、
  check-db 失敗、事前檢查的每一項、等不到鎖、硬碟不夠（和先清舊映像檔就夠）、新版沒 ready 換回上一版（逾時、載入模型失敗、舊 worker 留下的心跳、
  API 起不來、容器建不起來）、換回也失敗、開機時跳過失敗過的版本、`--retry-failed`、密碼不出現在輸出、log、參數裡（包括 `bash -x`）、log 輪替、
  `install.sh`（第一次、重跑不蓋掉 `analysis.env`、各種提醒、少了指令）、`systemd-analyze verify`。
- `deploy.sh` 一律用 `env -i` 執行（和 SSM Run Command 一樣幾乎沒有環境變數）。假的 docker 只認 `deploy.sh` 用到的參數，用錯就失敗。
- 本機有 shellcheck 的話也檢查 `deploy/` 底下所有的 `.sh`。
- `SHOW=正常部署 bash deploy/tests/run.sh` 印出名稱有這幾個字的情境的輸出；`PLATFORM=linux/amd64` 指定平台。
- 第一次要網路（建測試用的映像檔時 dnf 要下載 flock、systemd）；容器 `cdC-deploy-test-<pid>`、映像檔 `cdc-deploy-test:<pid>` 跑完都刪掉。
