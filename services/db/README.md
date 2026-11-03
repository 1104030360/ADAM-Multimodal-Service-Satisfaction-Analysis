# services/db/：資料庫的 migration 與 runner

資料庫（RDS for MySQL 8.4，資料庫 `adam`）的結構從這裡改：每一次改動是 `migrations/` 裡的一個編號檔案，部署時由
runner（`migrate.sh`，打包成 `adam-db-migrations` 映像檔）在 EC2 上用專用的帳號 `adam_migrator` 套用；
只「加東西」的自動跑，會刪改資料的要人確認。CI 用拋棄式 MySQL 驗證升級的結果和 `schema.sql` 一樣。
計畫在 `docs/rebuild/cd/PLAN.md` 第 9 節，各部分之間的介面在 `docs/rebuild/cd/CONTRACTS.md` 第 2、5 節（都在開發的電腦上，不在 git 裡）。

| 檔案 | 內容 |
|---|---|
| `migrations/NNN_*.sql` | migration。001～007 是 runner 之前手動套用的（從 `docs/rebuild/new-feature/migrations/` 原樣搬來），008 起由 runner 套用 |
| `schema.sql` | 新建資料庫用的完整 schema（從 `docs/rebuild/db_schema.sql` 來）＝001 到最新一號套用完的結構；**沒有任何帳號**（拿掉了兩個佔位帳號） |
| `migrate.sh` | runner：`status`、`apply [--allow-destructive]`、`lint`（bash＋`mysql` 客戶端；bash 3.2 也能跑） |
| `lib/sqlscan.awk` | SQL 的詞法處理：找破壞性的語句（runner、CI）、結構比對時正規化程序內容（CI） |
| `baseline/check_001_007.sql` | 第一次執行的基準檢查：001～007 建的東西在不在（只讀 `information_schema`，297 項） |
| `certs/rds-global-bundle.pem` | RDS 的 CA（global bundle），映像檔的預設 `DB_SSL_CA` |
| `Dockerfile`、`.dockerignore` | `adam-db-migrations` 映像檔：`mysql:8.4`＋上面這些，不用 root 跑 |
| `.gitattributes` | `.sql` 等一律 LF：migration 的 SHA-256 記在正式資料庫裡，換行不能被 checkout 改掉 |
| `tools/verify.sh` | CI 的驗證（升級、重跑、權限、結構比對、破壞性檢查） |
| `tools/schema_dump.sql` | 結構比對用的傾印（比哪些、不比哪些寫在檔案開頭） |
| `tools/test_accounts.sql`、`tools/test_grants.sql` | 測試、CI 用：照正式環境建 `adam_admin`、`adam_migrator`、`adam_app`、`adam_analysis` 和它們的權限（只有測試用的密碼） |
| `tests/run.sh` | runner 和 `verify.sh` 的測試（真的 `mysql:8.4` 容器、建好的映像檔） |
| `tests/migrations/`、`tests/lint/` | 測試專用的 migration 和 lint 的範例（不會進映像檔） |

## runner

```bash
docker build -t adam-db-migrations services/db
docker run --rm -e DB_HOST -e DB_PASSWORD [-e DB_PORT -e DB_NAME -e DB_USER -e DB_SSL_MODE -e DB_SSL_CA -e MIGRATE_LOCK_WAIT] \
  adam-db-migrations <status | apply [--allow-destructive] | lint [檔案…]>
```

一律加 `--rm`：基底 `mysql:8.4` 宣告了 `VOLUME /var/lib/mysql`，沒有 `--rm` 每跑一次就留下一個匿名的 volume。
密碼用 `-e DB_PASSWORD`（值從環境變數帶進去），不要寫成 `-e DB_PASSWORD=…`（會出現在 `docker` 的 argv 裡）。

| 指令 | 做什麼 |
|---|---|
| `status` | 只讀：已套用的（編號、檔名、SHA-256 前 12 碼、時間（UTC）、誰）、待套用的、待套用裡哪些是破壞性的。還沒有記錄時也做基準檢查（只讀） |
| `apply` | 拿鎖 → 第一次執行先做基準檢查 → 先檢查完全部（改過的檔案、順序、破壞性的）→ 第一次執行記錄 001～007 → 照編號套用待套用的，一個成功才記錄一個 |
| `apply --allow-destructive` | 同上，但待套用的裡面有破壞性的也照樣跑（只有人手動 Run workflow、勾選允許時才帶） |
| `lint [檔案…]` | 不連資料庫：列出檔案裡破壞性的語句（不給檔案＝`migrations/` 全部）。0：沒有；3：有 |

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `DB_HOST` | （必要） | |
| `DB_PASSWORD` | （必要） | 只從環境變數拿，交給 `mysql` 時用 `MYSQL_PWD`：不會出現在任何程序的 argv、log、錯誤訊息裡（錯誤訊息裡的主機名稱、密碼也會遮掉） |
| `DB_PORT` | `3306` | |
| `DB_NAME` | `adam` | migration 檔裡寫的是 `USE adam;`，所以實際上就是 `adam` |
| `DB_USER` | `adam_migrator` | |
| `DB_SSL_MODE` | `VERIFY_IDENTITY` | RDS 用預設值；本機、CI 的拋棄式 MySQL 用 `REQUIRED`（或 `DISABLED`） |
| `DB_SSL_CA` | 映像檔裡的 RDS global bundle | `VERIFY_CA`、`VERIFY_IDENTITY` 才用 |
| `MIGRATE_LOCK_WAIT` | `60` | 等鎖的秒數 |
| `MIGRATE_SOURCE_COMMIT` | 建置時的 `--build-arg SOURCE_COMMIT` | 記進 `tbl_schema_migration.source_commit`（7～40 個小寫十六進位字，其他的不記） |
| `MIGRATIONS_DIR` | 映像檔裡的 `migrations/` | 只給測試用（換一個資料夾） |

| 結束碼 | 意思（最後一行固定是 `migrate: result=<字> code=<數字>`） |
|---|---|
| 0 | 成功：`applied`（套用了）、`baseline`（第一次執行，只記錄了基準）、`up-to-date`（沒有要做的）；`status` 是 `ok`、`lint` 是 `clean` |
| 1 | `failed`（某個 migration 執行失敗：這一輪已經成功的照樣記錄，失敗的那個和後面的不跑）、`out-of-order`（待套用的編號比已經套用的最大編號小）、`interrupted`（收到 SIGTERM／SIGINT）、`error` |
| 2 | `usage`：用法或設定錯誤（少了 `DB_HOST`、`DB_PASSWORD`，`DB_SSL_MODE` 不對，讀不到 CA，migration 的檔名不對或編號重複） |
| 3 | `destructive`：待套用的裡面有破壞性的，而且沒有 `--allow-destructive`。**這一輪什麼都沒套用** |
| 4 | `changed`：已經套用的檔案內容（SHA-256）或檔名變了。什麼都沒套用 |
| 5 | `baseline-failed`：第一次執行的基準檢查沒過（001～007 不完整，或資料庫是空的）。什麼都沒記錄 |
| 6 | `lock-timeout`：等不到鎖（另一個 runner 在跑） |
| 7 | `no-connection`：連不上資料庫（主機、帳號、密碼、TLS、資料庫不存在） |

- 資料庫記錄有、映像檔沒有的 migration（換回舊版映像檔時會這樣）不算錯：印成 `db-only:`，結束碼照樣 0。
- **記錄**：`tbl_schema_migration`（runner 自己建）：`migration_id`（編號）、`file_name`、`sha256`、`applied_at`（UTC）、
  `applied_by`（`CURRENT_USER()`）、`baseline`（1＝第一次執行時記錄的 001～007，runner 沒有執行它們）、`source_commit`、`duration_ms`。
  不要手動改。真的要讓某一個重跑：用 master 帳號刪掉那一列，再 `apply`（migration 都可以重跑）。
- **鎖**：MySQL 的 `GET_LOCK('adam_schema_migration:adam')`，用一條一直開著的連線拿（背景的 `mysql` 從 FIFO 讀，runner 拿著寫的那一端）。
  拿到之後才讀記錄；runner 不管怎麼結束（成功、失敗、被 kill），那條連線都會斷，鎖就放掉。
- **先檢查完全部再動手**：改過的檔案（4）→ 順序（1）→ 破壞性的（3），都沒問題才記錄基準、開始套用。
- 每個 migration 用 `mysql --batch --show-warnings` 執行整個檔案（所以 `DELIMITER` 可以用）；遇到第一個錯誤就停。
  MySQL 的 DDL 不能放在交易裡，所以中途失敗時前面的語句已經生效：這就是 migration 都要可以重跑的原因。

## 第一次執行（基準）

`tbl_schema_migration` 不存在（或是空的）時是第一次執行：

1. 用 `baseline/check_001_007.sql` 檢查 001～007 建的東西都在。這個帳號（`adam_migrator`）讀不到 `mysql.*` 系統表、看不到別人建的程序內容，
   所以只檢查 `information_schema` 看得到的**名稱**：
   - 001～003、005～007（migration 檔自己建的）：資料表、每個欄位、索引、限制條件（主鍵、外鍵、CHECK）、程序和它的參數。
   - 000（001 之前的 `db_schema.sql`）和 004（`db_schema.sql` 本來就有、004 只在沒有時建立）：只看資料表、欄位、程序在不在
     （用舊版 `db_schema.sql` 建的資料庫，索引、參數的名稱可能不一樣，不想因為這種差別擋住部署）。
   - 不檢查：權限（舊的 `check_*.sql` 的 `app:`、`analysis:` 那幾列）、欄位的型別、程序的內容。
2. 有缺的：一項一行印出 `baseline: MISSING <編號> <種類> <名稱>`，結束碼 5，**什麼都沒記錄**（不建記錄表），下次照樣是第一次執行。
   資料庫是空的時會說要先用 `schema.sql` 建立。
3. 都在：（其他檢查也過了之後）建 `tbl_schema_migration`，001～007 記成已套用（`baseline=1`，SHA-256 是映像檔裡那份檔案的），**不重跑**；
   接著照常套用 008 以後的。

### 正式的 RDS 第一次跑 runner 時會怎樣

前提：PLAN 第 8 節第 8、9 步做完（`adam_migrator`、`REQUIRE SSL`、密碼在 Parameter Store），EC2 連得到 RDS。

- 001～007 都手動套用過（使用者說過已經套用）→ 297 項都在 → 建 `tbl_schema_migration`、記下 001～007 → 現在沒有 008 → `result=baseline code=0`。
  之後每次部署：`applied: 7 個（001～007…）`、`result=up-to-date`。
- 有缺的 → 結束碼 5，deploy.sh 停在 migration（舊版照常跑），log 裡有缺哪些。照編號手動補套用缺的那個 migration
  （用 master 帳號，舊的 `docs/rebuild/new-feature/migrations/apply_*.sh`），再重新部署。
- 基準不檢查權限：不確定 `adam_app`、`adam_analysis` 的權限時，照舊用 `docs/rebuild/new-feature/migrations/check_rds.sh`（master 帳號）檢查。

## 破壞性的語句

runner 在套用之前、CI 在合併之前，用 `lib/sqlscan.awk` 檢查檔案（拿掉註解、字串之後一句一句看；程序內容裡的語句也看；
字串的內容開頭是 SQL 關鍵字的——像 005 那種 `SET @step := '…ALTER TABLE…'` 再 `PREPARE` 的動態 SQL——也拆開來看）。算破壞性的：

- `DROP TABLE`、`DROP VIEW`、`DROP INDEX`、`DROP DATABASE`、`DROP TRIGGER`、`DROP EVENT`、`DROP USER` 等（`DROP TEMPORARY TABLE`、`DROP PREPARE` 不算）
- `TRUNCATE`（函式 `TRUNCATE(x, d)` 不算）、`RENAME TABLE`、`REVOKE`、`REPLACE INTO`（函式 `REPLACE()` 不算）
- `ALTER TABLE` 裡的 `DROP`（欄位、索引、外鍵、CHECK、預設值…）、`RENAME`、`MODIFY`、`CHANGE`、`CONVERT TO`、`DISCARD`、分割區的 `TRUNCATE`／`REMOVE`／`COALESCE`／`REORGANIZE`／`EXCHANGE`
- 沒有 `WHERE` 的 `UPDATE … SET`、`DELETE FROM`（`ON UPDATE`、`ON DELETE`、`ON DUPLICATE KEY UPDATE`、`FOR UPDATE` 不算）
- **`DROP PROCEDURE`／`DROP FUNCTION`（我們的決定）**：MySQL 刪程序時會連同所有帳號在它上面的 `EXECUTE` 一起拿掉（T42 第 9 節）。
  - 同一個檔案在 `DROP` 之後重新 `CREATE` 同一個名字，**而且之後有 `GRANT EXECUTE ON PROCEDURE <名字>`**：不算破壞性的（換掉程序內容的標準寫法，自動跑）。
  - 同一個檔案先建立、最後又刪掉（暫時的輔助程序）：不算。
  - 刪了沒有再建立（拿掉程序：還在呼叫它的舊版會壞）、或重新建立了但沒有重新 GRANT：破壞性的。
  - 靜態檢查只看得到「有沒有 GRANT」，看不到「GRANT 給了所有原本有權限的帳號沒有」：CI 的 `verify.sh` 會用正式環境的帳號和權限
    比對套用前後，`adam_app`、`adam_analysis` 少了任何一個權限就失敗（例如只 GRANT 給 `adam_app`，忘了 `adam_analysis`）。
- 檔案裡有一行 `-- migrate: destructive`（後面可以寫原因）：不管內容，一律當成破壞性的。

限制：只是詞法檢查，不是完整的 SQL 解析。字串的內容開頭像 SQL 的（例如資料是 `'DROP TABLE x'`）會被當成動態 SQL 檢查而誤判；
動態組出來的名稱（`CONCAT('DROP ', …)`）看不出來；有 `WHERE` 但等於全部的 `UPDATE`／`DELETE` 也看不出來。最後一道是 PR 的 review。

**真的要刪東西時**（expand／contract 的 contract）：

1. 等「不再用到它」的程式版本上線、確定不會換回更舊的版本之後，才加這個 migration，檔案裡標 `-- migrate: destructive <原因>`
   （沒標的話 CI 不給合併；標了 CI 照樣列出來）。
2. 合併之後自動部署會停在這一步（runner 結束碼 3，deploy.sh 13），舊版照常跑。
3. 先在 RDS 建 snapshot，再到 GitHub 手動 Run workflow、勾選「允許破壞性的 migration」（runner 收到 `--allow-destructive`）。
4. `adam_migrator` 沒有 `DROP` 權限：`ALTER TABLE … DROP COLUMN` 做得到（只要 `ALTER`），但 `DROP TABLE`、`DROP VIEW`、`TRUNCATE`、`RENAME TABLE`
   會失敗（1142）。要用到它們時，先用 master 帳號暫時 `GRANT DROP ON adam.* TO 'adam_migrator'@'%'`，跑完再 `REVOKE DROP ON adam.* FROM 'adam_migrator'@'%'`。

## 寫 migration 的規則

1. **檔名** `NNN_小寫英數字和底線.sql`，編號比目前最大的大（CI 檢查），不重複。**合併之後不能改、不能刪、不能改名**：
   正式環境記了它的 SHA-256，改了部署會停在結束碼 4。要修就再加一個新的。
2. **一個檔案一件事**：一個功能的資料表、程序、GRANT 放在一起；不相干的分開。開頭用註解寫目的、對象、重跑時的行為（像 001～007）。
   開頭寫 `SET NAMES utf8mb4;`、`USE adam;`。
3. **可以重跑**：`CREATE TABLE IF NOT EXISTS`、`CREATE PROCEDURE IF NOT EXISTS`；加欄位、索引、CHECK 先查 `information_schema`
   再決定要不要 `ALTER`（MySQL 8.4 沒有 `ADD COLUMN IF NOT EXISTS`，寫法照 005 或 `tests/migrations/010_test_note_index.sql`）。
   GRANT 本來就可以重跑。原因：執行到一半中斷、或執行完但記錄失敗時，下一次會重跑；新建的資料庫（`schema.sql` 已經有全部的結構）
   第一次 apply 也會把 008 以後的全部重跑一次。CI 會把新的 migration 直接再執行一次。
4. **只加東西（expand），先加後刪**：要改名、改型別、刪欄位或資料表時，先加新的、讓程式改用新的；等用不到舊的那一版上線之後，
   再用一個破壞性的 migration 手動刪（上一節）。這樣自動跑過的 migration 之後，換回上一版的映像檔也不怕。
5. **程序**：不要只 `DROP PROCEDURE`。要改內容時，新開一個名字（呼叫端改過去，舊的之後再刪），或在**同一個檔案**裡
   `DROP PROCEDURE IF EXISTS`＋`CREATE PROCEDURE`＋**重新 GRANT 給所有原本有 EXECUTE 的帳號**（查 `schema.sql` 最後的選用區塊、`tools/test_grants.sql`）。
   參數改了（個數、順序、型別）就一定要新開名字：還在跑的舊版會用舊的參數呼叫。
6. **不要寫 `DEFINER`**：runner 建的程序 DEFINER 是 `adam_migrator`，程序用它的權限執行。**所以不要刪掉 `adam_migrator` 這個帳號**
   （刪了它建的程序全部不能執行，1449）；要停用它就改密碼。
7. **GRANT 直接寫在 migration 裡**（不要像 001～007 那樣註解掉），對象是 `'adam_app'@'%'`、`'adam_analysis'@'%'`，只給 `adam.*` 裡的權限
   （`adam_migrator` 只有 `adam.*` 的 `WITH GRANT OPTION`）。帳號不存在時 GRANT 會失敗（1410）：
   - 正式環境（RDS）兩個帳號都在；CI（`verify.sh`、`tests/run.sh`）照正式環境建好了。
   - 本機的 `adam-mysql`、其他沒有這兩個帳號的資料庫：**先建兩個鎖住、不能登入的帳號**（下一節），之後所有 migration 的 GRANT 都跑得過。
   - runner 不會跳過失敗的 GRANT：正式環境打錯帳號名稱時要失敗，不能默默少給權限。
   - 要給新的帳號權限：先用 master 帳號在 RDS 建帳號（`adam_migrator` 不能建帳號），`tools/test_accounts.sql` 也加上，再合併帶 GRANT 的 migration。
   - 不要 `REVOKE`（破壞性的）。
8. **FUNCTION、TRIGGER**：正式環境開了自動備份，也就開了 binlog；沒有 SUPER 的帳號建 FUNCTION、TRIGGER 會失敗
   （1419 `You do not have the SUPER privilege and binary logging is enabled`，在開了 binlog 的 MySQL 8.4 上用 `adam_migrator` 驗證過）。
   要用的話先在 RDS 的參數群組把 `log_bin_trust_function_creators` 設成 `1`，FUNCTION 還要宣告 `DETERMINISTIC`、`NO SQL` 或 `READS SQL DATA`。
   **程序（PROCEDURE）不受影響**，能用程序就用程序。
9. **同樣的改動也寫進 `schema.sql` 和 `myapp/tests/fixtures/schema.sql`**（桌面端和分析服務的測試用它）。CI 比對三者的結構，
   連欄位順序都比：加欄位用 `AFTER`，`schema.sql` 裡的順序要一樣。
10. 不要在 migration 裡用 `SOURCE`、`USE` 別的資料庫、`SET GLOBAL`；改資料的 `UPDATE`／`DELETE` 一定有 `WHERE`。大量的資料搬移另外處理（會鎖表）。
11. **部署順序**：migration 在換分析服務之前跑（deploy.sh 先跑 runner，成功才換）；新版桌面端要等 migration 上線之後再發佈。
    舊版程式在新的結構上要能跑（第 4 點保證）。

## 在本機的 adam-mysql 套用

`adam-mysql` 用 root 連（密碼是 repo 根目錄 `.env` 的 `DB_PASSWORD`），沒有 `adam_migrator`、`adam_app`、`adam_analysis`。
runner 和 `adam-mysql` 共用網路（`--network container:adam-mysql`），用 `127.0.0.1` 連：

```bash
docker build -t adam-db-migrations:local services/db
read -r -s -p "adam-mysql 的 root 密碼: " DB_PASSWORD && echo && export DB_PASSWORD
# 只要做一次：建兩個鎖住、不能登入的帳號，migration 的 GRANT 才跑得過（規則第 7 點）
MYSQL_PWD=$DB_PASSWORD docker run --rm -e MYSQL_PWD --network container:adam-mysql mysql:8.4 \
  mysql -h127.0.0.1 -uroot -e "CREATE USER IF NOT EXISTS 'adam_app'@'%' ACCOUNT LOCK; CREATE USER IF NOT EXISTS 'adam_analysis'@'%' ACCOUNT LOCK;"
docker run --rm --network container:adam-mysql -e DB_HOST=127.0.0.1 -e DB_USER=root -e DB_PASSWORD -e DB_SSL_MODE=REQUIRED \
  adam-db-migrations:local status
docker run --rm --network container:adam-mysql -e DB_HOST=127.0.0.1 -e DB_USER=root -e DB_PASSWORD -e DB_SSL_MODE=REQUIRED \
  adam-db-migrations:local apply
```

- 第一次會做基準檢查：`adam-mysql` 要已經照編號套用過 001～007（CLAUDE.md「既有的資料庫要另外套用」）。缺的會列出來（結束碼 5），補套用之後再跑。
- 用 root 跑時程序的 DEFINER 是 root，和正式環境不同，本機沒關係。

## 新建資料庫

1. 建 RDS for MySQL 8.4（參數群組：`time_zone = America/Los_Angeles`、`require_secure_transport = ON`；要建 FUNCTION、TRIGGER 才需要
   `log_bin_trust_function_creators = 1`），打開自動備份。
2. 用 master 帳號載入 `schema.sql`（建立 `adam`、全部的資料表和程序）：
   `mysql -h <endpoint> -u <master> -p --ssl-ca=services/db/certs/rds-global-bundle.pem --ssl-mode=VERIFY_IDENTITY < services/db/schema.sql`
3. 建帳號和權限：`adam_app`、`adam_analysis` 照 `schema.sql` 最後的選用區塊（拿掉註解、換掉密碼）；`adam_migrator` 照 CD 計畫第 8 節第 8 步。
4. 跑一次 runner 的 `apply`（部署時會自動跑）：基準檢查會過（`schema.sql` 已經有 001～007），記下 001～007，
   再把 008 以後的重跑一次（它們都可以重跑，也會補上它們的 GRANT）。
5. **建第一個管理員**（`schema.sql` 沒有任何帳號）。在 repo 根目錄產生密碼的雜湊（桌面端的 `myapp/src/myapp/passwords.py`，只用標準函式庫；
   密碼不會顯示、不會留在 shell 的歷史裡）：

   ```bash
   python3 -I -c 'import getpass, sys; sys.path.insert(0, "myapp/src"); from myapp.passwords import hash_password; print(hash_password(getpass.getpass("新管理員的密碼: ")))'
   ```

   再用 master 帳號連 RDS，在 `mysql>` 提示字元下執行（`<…>` 換掉；雜湊整串貼在單引號裡）：

   ```sql
   INSERT INTO adam.tbl_account
     (account_id, account_username, account_password, account_name, account_level, account_occupation_name)
   VALUES (UUID(), '<登入帳號>', '<上面印出的 pbkdf2_sha256$… 雜湊>', '<名字>', '5', '<職稱>');
   ```

   等級 `5` 才能在桌面端的 Add Account 建立 1～5 的所有帳號。用這個帳號登入桌面端，其他帳號都在那裡建。

## CI

| 指令（repo 根目錄，Linux，有 docker） | 做什麼 |
|---|---|
| `bash services/db/tests/run.sh` | 建映像檔，用真的 `mysql:8.4` 測 runner（用法、lint、連線、基準成功和失敗、新的 migration、DELIMITER、重跑、SHA-256、破壞性、db-only、順序、中途失敗、等不到鎖、SIGTERM／SIGKILL 之後鎖放掉、`adam_migrator` 的權限和正式環境一樣、密碼不在輸出和 argv 裡）和 `verify.sh`（六個情境，各自是暫時的 git repo）。205 項；`mysql:8.4` 已經下載時約 2～5 分鐘 |
| `bash services/db/tools/verify.sh --base-ref origin/main` | 要完整的 git 歷史（`fetch-depth: 0`）。檢查項目寫在檔案開頭；`base` 還沒有 `services/db/` 時略過升級比對，照樣做其他檢查 |

- 容器、網路、映像檔的名稱前綴是 `ADAM_DB_TEST_PREFIX`（預設 `adam-db-test-`），不對外開 port，結束時刪掉；MySQL 容器同一時間只有一個。
- 驗證時 `schema.sql` 用 `adam_admin` 載入（像 RDS 的 master：沒有 `SYSTEM_USER`）。用 root 載入的話，root 建的程序 `adam_migrator` 刪不掉（1227），和正式環境不一樣。

## RDS 的 CA 為什麼放在 repo 裡

`certs/rds-global-bundle.pem` 是 https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem （2026-11-02 下載，114 張憑證，
SHA-256 `90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b`，和使用者在步驟 5 下載的 `~/aws/global-bundle.pem` 一樣）。

- 放在 repo：建置不用連網路、每次建出來的一樣、換憑證會出現在 PR 的 diff 裡；測試會檢查映像檔裡的和 repo 的一樣。
- 不在建置時下載：多一個網路的失敗點；沒有固定 checksum 就不知道裝進去的是什麼，固定了又等於要手動更新。
- 會過期嗎：us-east-1 的根憑證 RSA2048 G1 到 2061 年、RSA4096／ECC384 G1 到 2121 年。AWS 加了新的 CA、而且 RDS 改用它時才需要更新：
  下載同一個網址、確認內容只有憑證、換掉檔案、在 PR 寫上新的 SHA-256。
