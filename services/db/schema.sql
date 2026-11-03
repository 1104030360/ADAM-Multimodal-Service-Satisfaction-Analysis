-- =============================================================================
-- services/db/schema.sql：新建資料庫用的完整 schema（2026-11-02 從 docs/rebuild/db_schema.sql 搬進 git）。
--   結構＝migrations/ 從 001 套用到最新一號的結果；tools/verify.sh 在 CI 比對這兩者，也比對桌面端測試用的
--   myapp/tests/fixtures/schema.sql（那份多了兩個佔位帳號，結構要一樣）。
--   改結構時：新增一個 migration，同樣的改動也寫進這個檔案和 myapp/tests/fixtures/schema.sql（README「寫 migration 的規則」）。
--   和原本的 db_schema.sql 不一樣的地方：拿掉了最後的兩個佔位帳號（placeholder_admin、placeholder_staff）——
--   它們的密碼在公開的測試裡，正式的資料庫不能有。新建資料庫之後自己建第一個管理員：README「新建資料庫」。
--   已經有資料的資料庫（RDS、本機的 adam-mysql）不要重跑這個檔：裡面的 DROP PROCEDURE 會連帶拿掉 adam_app、
--   adam_analysis 在那些程序上的 EXECUTE（T42 第 9 節）。要用 migrate.sh apply 套用 migration。
--   下面原本的說明裡提到的 db_report.md、original_analyzer.md、steps/ 等文件在 docs/rebuild/（不在 git 裡）。
-- =============================================================================
-- ADAM：資料庫重建腳本（桌面用戶端＋分析服務）
-- 目標：MySQL 8.0.17 以上（需要 utf8mb4_0900_bin 與會生效的 CHECK）。
--       在 RDS 上請選 MySQL 8.4：RDS for MySQL 8.0 已於 2026-07-31 結束標準支援，
--       之後只能以付費的 Extended Support 執行（見 db_report.md 第 e 節）。
-- 來源：repo 內沒有任何原始 .sql。內容從兩處反推：
--   1. 桌面用戶端的程式碼：6 張表、5 個預存程序。
--   2. 原本的分析服務（repo 根目錄的 service_analyzer/，整理在 original_analyzer.md）：
--      它呼叫的 3 個預存程序，以及它寫入的逐字稿資料表。這 3 個程序只知道名稱與參數順序，
--      內容是依呼叫方式推得的。
-- 內容：14 張表（含 [BEGIN SQS] 的 3 張）、26 個預存程序（桌面端 23 個、分析服務 3 個），
--       最後一段是選用的最小權限帳號設定（預設是註解）。沒有任何帳號（tbl_account 是空的）。
-- 新功能（docs/rebuild/new-feature/PLAN.md，2026-11-01）：tbl_account.account_photo_key（N3）、
--   tbl_report 與 sp_list_reports（N4、N5）、sp_list_service_report_states（N4，分析頁的報表狀態）。
--   已經建好的資料庫（RDS、本機的 adam-mysql）重跑本檔
--   不會補上新欄位，要套用 services/db/migrations/ 底下的檔案（照編號，各一次；現在由 migrate.sh 套用）。
--   S6（自己改登入帳號、密碼，T36）：sp_get_account_password、sp_change_account_password、
--   sp_change_account_username（migrations/002_account_self_service.sql）。
--   N6（報表的 AI 說明，T42）：不改資料表與程序；假服務也寫逐字稿，選用區塊的 adam_analysis 多了
--   sp_insert_text_storage 的 EXECUTE（標成 [兩者]），報表的同名員工（PLAN 13.7）要 tbl_account.account_username；
--   已經建好的資料庫見 migrations/004_ai_transcripts.sql。
--   PLAN1 W1（服務結果與待辦，T44）：tbl_service 多四個可為 NULL 的欄位 service_outcome、service_todos、
--   service_outcome_model、service_outcome_run 和 chk_service_outcome；不改程序（sp_insert_service 不用改）。
--   選用區塊的 adam_analysis 多了這四欄的 UPDATE、service_outcome_run 的 SELECT。
--   已經建好的資料庫見 migrations/005_service_outcome.sql（可以重跑）。
-- PLAN1 W2（員工輔導與後續追蹤，T45）：tbl_coaching_action、tbl_coaching_marker、tbl_coaching_event 和
--   13 個 sp_coaching_* 程序，整段在主要程序區塊後面的 [BEGIN PLAN1 W2]…[END PLAN1 W2]；
--   已經建好的資料庫見 migrations/006_coaching.sql（可以重跑，不 DROP）。
-- SQS（分析工作佇列，T49）：tbl_analysis_job、tbl_analysis_outbox、tbl_analysis_worker，整段在 [BEGIN SQS]…[END SQS]；
--   狀態 2 改由分析服務接受工作時寫入。已經建好的資料庫見 migrations/007_analysis_queue.sql（可以重跑，不 DROP）。
--
-- 標記：
--   「確定」= 由程式碼直接決定，例如欄位名稱、程序參數的數量與順序、
--             結果集的欄位名稱、字串型別的需求。
--   「推測」= 程式碼沒有直接限定的設計選擇，例如長度、數值型別、索引、
--             CHECK、沒有任何程式讀取的欄位。
--
-- 依據路徑縮寫（行號以目前 main 分支為準）：
--   db  = myapp/src/myapp/database.py
--   rw  = myapp/src/myapp/init/record_widget.py
--   sh  = myapp/src/myapp/init/server_home_init.py
--   ah  = myapp/src/myapp/init/admin_home_init.py
--   qml = myapp/src/myapp/qml/pages/
--   sa  = service_analyzer/（原本的分析服務；不在 git 裡）
--
-- 已實測：在拋棄式 Docker 容器中以 MySQL 8.0.46 與 8.4.13 執行本檔（連續執行兩次確認
--   可重複執行），並用 mysql-connector-python 8.0.26（requirements.txt 的版本）
--   直接呼叫 repo 裡的 database.py、record_widget.py、server_home_init.py、
--   admin_home_init.py 的實際方法（PySide6、GCS 等以替身模組取代）。
--   加入分析服務的部分之後，又在 MySQL 8.4.13 上連續載入兩次，以 mysql-connector-python
--   9.0.0（原分析服務的版本）與 26.10.0，照原分析服務的寫法（cursor.execute("CALL ...")）
--   各呼叫兩次，並以選用區塊的 adam_analysis 權限執行。
--   結果記錄在 db_report.md 的附錄 A。
--
-- 執行方式（任選一種）：
--   mysql -h <RDS 端點> -P 3306 -u <主帳號> -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY < db_schema.sql
--   或在 MySQL Workbench 開啟本檔後整份執行（Workbench 支援 DELIMITER）。
--   不要用不支援 DELIMITER 的工具（例如 RDS Query Editor）。
--
-- 可重複執行：資料表用 CREATE TABLE IF NOT EXISTS，不會刪除既有資料；
--   預存程序先 DROP 再 CREATE（會拿掉其他帳號在程序上的 EXECUTE，所以只在新建時跑，見最上面）。
--   注意：如果資料表已經以舊定義存在，IF NOT EXISTS 不會更新它的欄位或索引。
--   用本檔較早的版本（6 張表、5 個程序）建立的資料庫，重跑本檔會補上 tbl_text_storage
--   與 3 個新程序，但 tbl_charts_storage 不會自動加上 UNIQUE 鍵，要另外執行一次：
--     ALTER TABLE tbl_charts_storage ADD UNIQUE KEY uk_charts_service_name (service_id, charts_name);
--   沒有這個鍵，sp_insert_charts_storage 重跑時會多插入一份圖表列。
--
-- RDS 相容性：
--   不含 DEFINER 子句、觸發程序與自訂函式，所以不需要 SUPER，
--   也不需要調整 log_bin_trust_function_creators。
--   參數群組建議（在 AWS 主控台設定，無法用本檔設定）：
--     time_zone        = America/Los_Angeles   服務現場的時區（new-feature PLAN R4，含夏令時間）。
--                                      用戶端以本機時間寫入時間欄位，但查詢使用伺服器端的 CURDATE()；
--                                      RDS 預設 UTC，「今日」會偏移。分析服務的 DB_TIME_ZONE、期間報表切日也用這個。
--     require_secure_transport = ON    RDS 預設 OFF。
--     transaction_isolation = READ-COMMITTED   只在無法修改用戶端時使用。這不是根治：
--                                      用戶端應改成 autocommit=True，原因見 db_report.md 第 c 節。
-- =============================================================================

SET NAMES utf8mb4;

-- 資料庫名稱：桌面端 .env 的 DB_NAME（db:32、rw:26）與分析服務的 DB_NAME 必須相同。
--   原本的分析服務寫死的是 ADAM（大寫，sa/app.py:12-17、sa/analyzer.py:23-28、
--   sa/analysis_upload.py:12-17）；這裡用 adam（預設，待你決定）。
--   RDS（Linux）的資料庫名稱分大小寫，ADAM 和 adam 是兩個不同的資料庫。
--   要改名時，下面的 CREATE DATABASE、USE，以及最後選用區塊每一行的 adam. 都要一起改。
CREATE DATABASE IF NOT EXISTS adam
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE adam;


-- =============================================================================
-- 1. tbl_account：帳號
--    讀寫位置：db:50（sp_check_login）、db:67（sp_get_account_name）、rw:73、
--    ah:304-305（INSERT）、ah:321-322（UPDATE）、ah:481-484、ah:565、
--    ah:582-590、ah:698、sh:290-292
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_account (
  -- 確定：欄位名稱（ah:304、ah:698、rw:73）。
  -- 確定：必須是字串型別，因為新增帳號時用 UUID() 產生 36 字元的值（ah:305），
  --       QML 也以字串傳遞（main.py:44-50 @Slot(str)）。
  -- 推測：VARCHAR(36)。
  account_id              VARCHAR(36)  NOT NULL,

  -- 確定：欄位名稱（ah:304、ah:321）。登入以帳號名稱查詢（db:50）。
  -- 推測：長度 64；UNIQUE，避免 sp_check_login 遇到同名帳號時任選一筆。
  --       沿用預設定序 utf8mb4_0900_ai_ci，所以登入時帳號名稱不分大小寫，
  --       UNIQUE 也會擋下只差大小寫的重複帳號。
  account_username        VARCHAR(64)  NOT NULL,

  -- 確定：欄位名稱（ah:304、ah:321）。用戶端直接寫入 TextField 的明碼
  --       （ah:309、ah:326），登入時也送出明碼（db:50）。
  -- 推測：VARCHAR(255)，日後改存雜湊時長度仍夠用（見 db_report.md 第 d 節）。
  --       刻意使用 utf8mb4_0900_bin：預設的 _ai_ci 定序會讓 'Password' = 'password'、
  --       'é' = 'e'。0900_bin 是 NO PAD 定序，結尾空白也會被比對。
  --       sp_check_login 從不回傳這個欄位；即使直接讀取，connector 8.0.26 拿到的也是 str（已實測）。
  account_password        VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,

  -- 確定：欄位名稱（db:71-72、rw:73、sh:290、ah:481、ah:565、ah:582、ah:590）。
  -- 確定：NOT NULL。搜尋結果會執行 QStandardItem(result['name'])（sh:327、ah:619），
  --       值為 None 時無法建立這個項目。
  -- 推測：長度 100。不設 UNIQUE，但用戶端把它當成唯一識別使用：
  --       排名以姓名分組（ah:491-496），管理端搜尋也以姓名篩選（ah:590）。
  account_name            VARCHAR(100) NOT NULL,

  -- 確定：必須是字串。login.qml:142 以 result.account_level === "1" 嚴格比較；
  --       若欄位是整數，Python 會拿到 int 1，結果為 false，服務人員就會被導向管理端。
  --       QML 下拉選單只提供 "1"～"5"（qml/admin_add_page.qml:74）；
  --       管理端用 int() 轉換後比較（ah:300、ah:317）；
  --       ah:565 以 account_level = 1 篩選服務人員。
  -- 推測：CHAR(1)，以 CHECK 限制在 1～5（對應下拉選單）。等級 1 是服務人員，
  --       其他等級進入管理端。
  account_level           CHAR(1)      NOT NULL DEFAULT '1',

  -- 確定：欄位名稱（ah:304、ah:321-322）。只會寫入，用戶端沒有任何地方讀取它。
  -- 推測：可為 NULL，長度 100。
  account_occupation_name VARCHAR(100) NULL,

  -- new-feature N3（2026-11-01）：大頭照在 S3 的 key，avatars/{account_id}-{時間}.jpg。
  -- 沒有照片是 NULL，畫面顯示預設圖。只新增可為 NULL 的欄位，舊版桌面程式不受影響。
  account_photo_key       VARCHAR(255) NULL,

  PRIMARY KEY (account_id),
  UNIQUE KEY uk_account_username (account_username),   -- 推測：服務 sp_check_login
  KEY idx_account_name (account_name),                 -- 推測：服務 ah:565（DISTINCT）與 ah:590（姓名篩選）
  CONSTRAINT chk_account_level CHECK (account_level IN ('1', '2', '3', '4', '5'))  -- 推測
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '帳號。account_level 1=服務人員，其他=管理端（login.qml:142）';


-- =============================================================================
-- 2. tbl_service：一次錄影 = 一筆服務
--    寫入：rw:387（sp_insert_service）、sh:611-620（狀態改為 2）、分析服務（狀態改為 3）
--    讀取：sh:289-295、sh:466-492、sh:553-583、ah:356-366、ah:440-455、ah:480-486、ah:581-605
--    原分析服務只寫 service_status，從不讀這張表（sa/app.py:19-28、46）。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_service (
  -- 確定：欄位名稱。值為 'S' + YYYYMMDDHHMMSS，共 15 字元（rw:225、rw:322）。
  --       必須是字串：sh:326 與 ah:618 會執行 QStandardItem(result['service_id'])。
  -- 推測：VARCHAR(32)，保留空間。目前的格式只精確到秒，兩台電腦在同一秒開始錄影時，
  --       主鍵會衝突（db_report.md 第 c 節）；日後若加上後綴，長度仍然足夠。
  service_id      VARCHAR(32)      NOT NULL,

  -- 確定：欄位名稱，以及 JOIN 到 tbl_account（sh:292-293、ah:484、ah:584、sh:556）。
  -- 推測：外鍵；刪除帳號時，若仍有服務紀錄則拒絕刪除。
  account_id      VARCHAR(36)      NOT NULL,

  -- 確定：欄位名稱（sh:290、sh:477、ah:358、ah:363、ah:595）。
  --       用戶端會呼叫 .strftime()（sh:328、sh:595、ah:620），所以必須是時間型別，
  --       而且搜尋結果路徑沒有檢查 None。
  -- 推測：DATETIME（存本機時間，不做時區轉換），NOT NULL。
  service_start   DATETIME         NOT NULL,

  -- 確定：欄位名稱（sh:554、sh:566、sh:578）。process_services 允許 None（sh:596）。
  -- 推測：DATETIME，可為 NULL。
  service_end     DATETIME         NULL,

  -- 確定：欄位名稱，以及 1=已錄完上傳、2=已送分析、3=分析完成
  --       （rw:387 寫入 '1'、sh:630 寫入 '2'、sh:556/568/582 讀取）。
  --       程式混用字串 '3'（sh:476、ah:449）與整數 3（sh:294）。
  -- 推測：TINYINT UNSIGNED。數值欄位與字串 '3' 比較時，MySQL 把常數轉成數字，索引仍可使用；
  --       反過來，CHAR 欄位與整數 3 比較時，索引就無法使用。
  --       Python 端一律以 str() 轉換（sh:597），所以型別不影響畫面。
  --       原分析服務完成時寫入整數 3，沒有任何 WHERE 條件；失敗時不改狀態，會一直停在 2。
  --       移植後的分析服務：完成時 SET 3 WHERE service_status IN (1,2)；失敗時把 2 改回 1
  --       （預設，待你決定）。兩者都不需要新的狀態值；目前的用戶端也不會顯示其他狀態。
  service_status  TINYINT UNSIGNED NOT NULL DEFAULT 1,

  -- PLAN1 W1（2026-11-01，T44）：分析服務讀這筆服務的逐字稿（已做說話者分離），用 Ollama Cloud 填的服務結果與待辦。
  --   桌面端只讀（服務明細的 media.DETAIL_SQL），不寫。NULL＝還沒填：沒有逐字稿、AI 關著、這次的呼叫失敗；
  --   舊的服務都是 NULL。重跑分析時，寫分數的同一個交易先清成 NULL，再由背景工作填這一次的（service_outcome.py）。
  --   和 service_status 分開：不表示分析進度，也不改分數。
  -- service_outcome：Resolved、Documents missing、Handoff needed、Unresolved、Unknown 其中一個（chk_service_outcome）。
  --   utf8mb4_0900_bin：CHECK 用欄位的定序比較，預設的 ai_ci 會放過 'resolved'、'Resolvéd'，這裡一個字都不能差。
  service_outcome       VARCHAR(32)  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NULL,
  -- service_todos：待辦，一項一行（逐字稿裡有人說了的下一步，最多 5 項）；沒有待辦是空字串。
  service_todos         TEXT         NULL,
  -- service_outcome_model：這次用的 OLLAMA_MODEL（例如 gemma4:31b），畫面上和「AI-generated」一起顯示。
  service_outcome_model VARCHAR(100) NULL,
  -- service_outcome_run：哪一次分析清的（分析服務產生的編號，uuid4 的 32 個十六進位字）。寫回時 WHERE 這一欄等於
  --   那一次的編號，所以等 AI 時又重跑了的話，晚到的舊結果寫不進去。只有分析服務讀寫，桌面端不讀。
  service_outcome_run   CHAR(32)     NULL,

  PRIMARY KEY (service_id),
  -- 推測：服務人員端的查詢都是「account_id = ? AND service_status = ? [AND service_start 範圍]」
  --       （sh:293-310、sh:475-477、sh:489-491、sh:556、sh:568、sh:582）。這個索引同時滿足外鍵需求。
  KEY idx_service_account_status_start (account_id, service_status, service_start),
  -- 推測：管理端儀表板與排名的條件是「service_status = '3' [AND service_start >= ?]」（ah:449-455、ah:485）。
  KEY idx_service_status_start (service_status, service_start),
  -- 推測：趨勢圖用 BETWEEN（ah:363），管理端搜尋用日期範圍（ah:595、ah:602），兩者都不帶狀態條件。
  KEY idx_service_start (service_start),
  CONSTRAINT fk_service_account FOREIGN KEY (account_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_service_status CHECK (service_status IN (1, 2, 3)),  -- 推測
  CONSTRAINT chk_service_outcome CHECK (service_outcome IS NULL OR service_outcome IN
    ('Resolved', 'Documents missing', 'Handoff needed', 'Unresolved', 'Unknown'))   -- PLAN1 W1
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '服務（一次錄影）。service_status 1=已上傳 2=已送分析 3=分析完成';


-- =============================================================================
-- 3. tbl_service_score：分析分數（由分析服務寫入）
--    寫入：原分析服務呼叫 sp_insert_service_score（sa/analysis_upload.py:25-46）；
--          第 0 階段假服務直接 DELETE＋INSERT（analysis_service/app.py）。
--    讀取：sh:468-474、sh:482-488、sh:578-581（LEFT JOIN）、ah:359-362、ah:442-448、ah:481-483
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_service_score (
  -- 確定：欄位名稱與 JOIN（sh:474、sh:581、ah:362、ah:448、ah:483）。
  -- 推測：每筆服務最多一列，所以 service_id 就是主鍵。sh:581 以 LEFT JOIN 取完成清單，
  --       COUNT(service.service_id)（sh:468、ah:442）也以「一服務一列」為前提；
  --       重複的列會讓清單重複，次數也會被灌高。
  --       重跑時：sp_insert_service_score 以這個主鍵做 upsert；假服務在同一個交易內先 DELETE 再 INSERT。
  --       兩種做法都只會留下一列。
  service_id       VARCHAR(32) NOT NULL,

  -- 確定：以下 8 個欄位名稱（sh:469-472、ah:443-446、ah:359-360、ah:481、sh:579）。
  -- 確定：-1 表示沒有資料。兩個 ServiceScoreManager 用 AVG(NULLIF(col, -1)) 排除它
  --       （sh:469-472、ah:443-446）。
  -- 確定：分數範圍是 0～100。QML 會除以 100 畫成圓環（qml/server_home_button.qml:151、
  --       qml/admin_home_page.qml:162），趨勢圖 Y 軸固定在 0～100（qml/admin_home_page.qml:741-748）。
  -- 原分析服務寫入的值（original_analyzer.md 第 3 節）：6 個模態分數是 20～100、小數 2 位，
  --       沒有資料時給 60，從不寫 -1；兩個總分是模型的原始輸出，沒有四捨五入也沒有限制範圍，
  --       超出 0～100 時會被下面的 CHECK 擋下，整筆寫入失敗。
  --       移植後的分析服務：沒有資料寫 -1；總分四捨五入到小數 2 位並限制在 0～100
  --       （預設，待你決定；見 analysis_report.md 第 3 節）。
  -- 推測：使用 DOUBLE，不用 DECIMAL。排名在 Python 計算平均（ah:498-499），再把結果放進
  --       Signal(list, …) 送給 QML（ah:472、ah:512）。DECIMAL 會讓 Python 拿到 decimal.Decimal，
  --       PySide6 不會把它轉成 JS 數字（未實測）。DOUBLE 對應 Python float，沒有這個問題。
  -- 推測：NOT NULL DEFAULT -1。排名直接執行 sum(scores)（ah:498），值為 NULL 時會丟出 TypeError。
  -- 推測：CHECK 只允許 -1 或 0～100。分析服務若寫入超出範圍的值會直接報錯，不會靜默寫入。
  customer_total   DOUBLE      NOT NULL DEFAULT -1,
  server_total     DOUBLE      NOT NULL DEFAULT -1,
  customer_text    DOUBLE      NOT NULL DEFAULT -1,
  server_text      DOUBLE      NOT NULL DEFAULT -1,
  customer_audio   DOUBLE      NOT NULL DEFAULT -1,
  server_audio     DOUBLE      NOT NULL DEFAULT -1,
  customer_facial  DOUBLE      NOT NULL DEFAULT -1,
  server_facial    DOUBLE      NOT NULL DEFAULT -1,

  -- 推測：沒有程式讀這個欄位，只為了方便維運而保留。sp_insert_service_score 重跑時
  --       會把它更新成最新一次寫入的時間（和假服務 DELETE＋INSERT 的結果一致）。
  --       不可加入 account_id、service_status、service_start、service_end 這些欄位名稱：
  --       sh:577-582 的 LEFT JOIN 沒有替欄位加上表名前綴，加入後會出現 ambiguous column 錯誤。
  score_created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (service_id),
  CONSTRAINT fk_score_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_score_customer_total  CHECK (customer_total  = -1 OR customer_total  BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_total    CHECK (server_total    = -1 OR server_total    BETWEEN 0 AND 100),
  CONSTRAINT chk_score_customer_text   CHECK (customer_text   = -1 OR customer_text   BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_text     CHECK (server_text     = -1 OR server_text     BETWEEN 0 AND 100),
  CONSTRAINT chk_score_customer_audio  CHECK (customer_audio  = -1 OR customer_audio  BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_audio    CHECK (server_audio    = -1 OR server_audio    BETWEEN 0 AND 100),
  CONSTRAINT chk_score_customer_facial CHECK (customer_facial = -1 OR customer_facial BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_facial   CHECK (server_facial   = -1 OR server_facial   BETWEEN 0 AND 100)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析分數 0~100，-1=沒有資料。customer_*=顧客、server_*=服務人員';


-- =============================================================================
-- 4. tbl_video_storage：影片（每筆服務 2 列：cam0=顧客、cam1=員工）
--    寫入：rw:391-394（sp_insert_video_storage）
--    讀取：sh:154-160、ah:188-194；原分析服務 sa/analyzer.py:254-271
--          （SELECT video_name, video_url，依名稱含 cam0／cam1 分鏡頭，符合的列有多筆時最後一筆勝出）
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_video_storage (
  -- 推測：代理主鍵。用戶端從不讀取它。
  video_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  -- 確定：欄位名稱，以及 sp_insert_video_storage 的第 1 個參數（rw:394、sh:154）。
  service_id        VARCHAR(32)     NOT NULL,

  -- 確定：欄位名稱（sh:154 的 video_name LIKE %s），值為 '{service_id}_cam0.avi' 或
  --       '{service_id}_cam1.avi'（rw:392-394）。讀取時以 LIKE '%cam0%' / '%cam1%' 找出對應鏡頭，
  --       並用 fetchone()[0] 取值（sh:156-160），所以同一服務的同一鏡頭只能有一列。
  -- 推測：UNIQUE(service_id, video_name)。
  video_name        VARCHAR(255)    NOT NULL,

  -- 確定：欄位名稱（sh:154）。值為 'gs://{bucket}/video/{檔名}'（rw:373）；
  --       上傳失敗時寫入空字串（rw:377-378）。
  --       桌面端讀取時只取 os.path.basename(url)，再到固定的 'video/' 資料夾下載（sh:201-206），
  --       所以換成 s3://bucket/video/{檔名} 也能運作。
  --       但原分析服務只接受 gs:// 開頭的 URL，bucket 也從 URL 取（sa/analyzer.py:273-293），
  --       遇到 s3:// 會丟 ValueError；移植後的分析服務要改成只用檔名、從 S3_BUCKET 下載。
  -- 推測：長度 1024。請存物件位置，不要存會過期的預簽 URL。
  video_url         VARCHAR(1024)   NOT NULL DEFAULT '',

  -- 確定：sp_insert_video_storage 第 4 個參數是 datetime.now()（rw:394）。
  -- 推測：欄位名稱與型別。
  video_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (video_id),
  UNIQUE KEY uk_video_service_name (service_id, video_name),   -- 推測：服務 sh:154 與 ah:188，並防止重複
  CONSTRAINT fk_video_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '錄影檔位置；video_name 含 cam0（顧客）或 cam1（員工）';


-- =============================================================================
-- 5. tbl_audio_storage：音訊（每筆服務 1 列）
--    寫入：rw:399（sp_insert_audio_storage）
--    讀取：sh:174-176、ah:208-210；原分析服務 sa/analyzer.py:243-252（SELECT audio_url，取第一列）
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_audio_storage (
  audio_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,   -- 推測：代理主鍵

  -- 確定：欄位名稱，以及 sp_insert_audio_storage 的第 1 個參數（rw:399、sh:174）。
  -- 推測：UNIQUE。讀取端用 fetchone()[0]（sh:176）只取一列。依 connector 原始碼推斷（未實測），
  --       多出的列會成為未讀結果，關閉 cursor 時會丟出 InternalError。
  service_id        VARCHAR(32)     NOT NULL,

  -- 確定：sp_insert_audio_storage 第 2 個參數的值是 '{service_id}_audio.wav'（rw:399）。
  -- 推測：欄位名稱，比照 video_name 命名。
  audio_name        VARCHAR(255)    NOT NULL,

  -- 確定：欄位名稱（sh:174、sa/analyzer.py:247），格式同 video_url；桌面端只取檔名，
  --       固定到 'audio/' 下載（sh:202）。原分析服務的限制同 video_url，而且會把這個 URL
  --       直接交給 Google STT（sa/analyzer.py:230），所以它一定要是 gs://。
  audio_url         VARCHAR(1024)   NOT NULL DEFAULT '',

  -- 確定：第 4 個參數是 datetime.now()（rw:399）。推測：欄位名稱。
  audio_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (audio_id),
  UNIQUE KEY uk_audio_service (service_id),
  CONSTRAINT fk_audio_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '錄音檔位置';


-- =============================================================================
-- 6. tbl_charts_storage：分析圖表（由分析服務寫入）
--    寫入：原分析服務每次成功分析寫 5 列，每張圖呼叫一次 sp_insert_charts_storage
--          （sa/analysis_upload.py:66-81）；第 0 階段假服務直接 DELETE＋INSERT 1～3 列。
--    讀取：sh:189-192、ah:223-226（fetchall，沒有 ORDER BY）
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_charts_storage (
  -- 推測：代理主鍵，依寫入順序遞增。步驟 3 的用戶端以 ORDER BY charts_id 決定顯示順序。
  charts_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  service_id         VARCHAR(32)     NOT NULL,                 -- 確定（sh:189）

  -- 確定：原分析服務以 sp_insert_charts_storage 的第 2 個參數傳入圖表的檔名
  --       （sa/analysis_upload.py:79），例如 '{service_id}_emotion_TEXT.png'。
  -- 推測：欄位名稱、可為 NULL。桌面端不讀這個欄位。
  charts_name        VARCHAR(255)    NULL,

  -- 確定：欄位名稱（sh:189）。讀取端只取 os.path.basename(url)，固定到 'charts/' 下載
  --       （sh:216-220），而且所有服務共用同一個本機快取資料夾。
  --       因此分析服務的檔名必須全域唯一；原分析服務的 5 個檔名都以 service_id 開頭，
  --       例如 '{service_id}_emotion_AUDIO.png'。不同服務使用同一個檔名時，儲存空間上的檔案會互相覆蓋。
  -- 格式：原分析服務存 https://storage.googleapis.com/<bucket>/charts/<檔名>（sa/analysis_upload.py:77）；
  --       假服務與移植後的分析服務存 s3://<bucket>/charts/<檔名>。桌面端只取檔名，三種都能用。
  charts_url         VARCHAR(1024)   NOT NULL,

  charts_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,   -- 推測

  PRIMARY KEY (charts_id),
  -- 推測：讓 sp_insert_charts_storage 重跑時更新原本那一列，不會重複插入；charts_name 是 NULL 的列
  --       不受限制（MySQL 的 UNIQUE 允許多個 NULL）。它也服務 sh:189 與 ah:223 的查詢與外鍵。
  -- 顯示順序：桌面端原本的查詢沒有 ORDER BY。MySQL 8.4.13 實測會用這個索引，所以結果依
  --       charts_name 的字母順序，和原分析服務在 Windows 上依 os.listdir 寫入的順序相同。
  --       步驟 3 的用戶端加上 ORDER BY charts_id 之後，改為依寫入順序。
  UNIQUE KEY uk_charts_service_name (service_id, charts_name),
  CONSTRAINT fk_charts_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析圖表位置；檔名需含 service_id 以免互相覆蓋';


-- =============================================================================
-- 7. tbl_text_storage：逐字稿（每筆服務 1 列，由分析服務寫入；桌面端從不讀取）
--    寫入：原分析服務呼叫 sp_insert_text_storage（sa/analysis_upload.py:48-64），
--          檔案是 text/{service_id}_transcript.txt。
-- 推測：資料表名稱與所有欄位名稱。原分析服務只透過預存程序寫入，看不到資料表本身；
--       這裡比照 tbl_charts_storage 的命名與結構。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_text_storage (
  text_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,   -- 推測：代理主鍵

  -- 確定：sp_insert_text_storage 的第 1 個參數是 service_id。
  service_id       VARCHAR(32)     NOT NULL,

  -- 確定：第 2 個參數是檔名，例如 '{service_id}_transcript.txt'（sa/analysis_upload.py:62）。
  text_name        VARCHAR(255)    NOT NULL,

  -- 確定：第 3 個參數是 URL。原分析服務存 https://storage.googleapis.com/<bucket>/text/<檔名>
  --       （sa/analysis_upload.py:60）；移植後存 s3://<bucket>/text/<檔名>。
  text_url         VARCHAR(1024)   NOT NULL,

  text_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,   -- 推測

  PRIMARY KEY (text_id),
  UNIQUE KEY uk_text_service_name (service_id, text_name),   -- 推測：讓 sp_insert_text_storage 重跑時不重複
  CONSTRAINT fk_text_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '逐字稿位置（text/{service_id}_transcript.txt）；桌面端不讀';


-- =============================================================================
-- 8. tbl_report：報表（new-feature PLAN 7.1，N4、N5，2026-11-01 新增）
--    寫入：桌面端（管理員）在 db lane 新增一列（report_status = generating），再請分析服務的 /reports 產生；
--          分析服務只把 report_status 走到 ready 或 failed（PLAN R1）；發佈由桌面端寫 published_at、published_by（R6）。
--    讀取：桌面端一律經由 sp_list_reports，依帳號等級決定看得到哪些列（PLAN 7.2）。
--    同一個對象可以重新產生，每次一筆新紀錄，舊的留下（R5）；畫面只顯示每個對象最新的一筆。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_report (
  -- 桌面端用 uuid4 產生（36 字元）。
  report_id          VARCHAR(36)  NOT NULL,

  -- service：每次服務一份；staff：每位員工一段期間；company：全公司一段期間。
  report_kind        VARCHAR(16)  NOT NULL,

  -- 每次服務報表才有。
  service_id         VARCHAR(32)  NULL,

  -- 每次服務、員工報表才有（每次服務報表是那筆服務的 account_id）；全公司報表是 NULL。
  staff_account_id   VARCHAR(36)  NULL,

  -- 員工、全公司報表的期間，含頭尾兩天。日期依 America/Los_Angeles 切（PLAN R4）。
  period_start       DATE         NULL,
  period_end         DATE         NULL,

  -- generating：產生中；ready：PDF 已上傳（不表示服務人員看得到，看不看得到由 published_at 決定）；failed：失敗。
  report_status      VARCHAR(16)  NOT NULL DEFAULT 'generating',

  -- failed 的原因（英文短句，畫面直接顯示，PLAN 7.3）。7.1 的欄位表沒有列，為了「顯示 failed 與原因」而加。
  failure_reason     VARCHAR(255) NULL,

  -- reports/{report_id}.pdf
  s3_key             VARCHAR(255) NOT NULL,

  -- 產生報表的管理員。
  created_by         VARCHAR(36)  NOT NULL,

  -- 毫秒：同一秒內重新產生時，仍分得出哪一筆比較新。
  created_at         DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

  -- 管理員按 Publish 的時間與帳號。NULL 代表還沒發佈，服務人員看不到（R6）。已發佈再按一次不改時間。
  published_at       DATETIME     NULL,
  published_by       VARCHAR(36)  NULL,

  -- N6 才用：說明是哪個模型、哪一版提示產生的。
  ai_model           VARCHAR(100) NULL,
  ai_prompt_version  VARCHAR(32)  NULL,

  PRIMARY KEY (report_id),
  -- sp_list_reports 依對象分組取最新一筆；服務人員只看自己的
  KEY idx_report_staff_kind (staff_account_id, report_kind, created_at),
  KEY idx_report_service (service_id, created_at),
  -- 把逾時的 generating 改成 failed
  KEY idx_report_status_created (report_status, created_at),
  CONSTRAINT fk_report_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_report_staff FOREIGN KEY (staff_account_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_report_created_by FOREIGN KEY (created_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_report_published_by FOREIGN KEY (published_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_report_kind CHECK (report_kind IN ('service', 'staff', 'company')),
  CONSTRAINT chk_report_status CHECK (report_status IN ('generating', 'ready', 'failed')),
  -- 每一種報表該有的欄位
  CONSTRAINT chk_report_subject CHECK (
       (report_kind = 'service' AND service_id IS NOT NULL AND staff_account_id IS NOT NULL
        AND period_start IS NULL AND period_end IS NULL)
    OR (report_kind = 'staff' AND service_id IS NULL AND staff_account_id IS NOT NULL
        AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)
    OR (report_kind = 'company' AND service_id IS NULL AND staff_account_id IS NULL
        AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
  -- 只有 ready 能發佈；發佈的時間和帳號一起寫
  CONSTRAINT chk_report_published CHECK (
    (published_at IS NULL AND published_by IS NULL)
    OR (published_at IS NOT NULL AND published_by IS NOT NULL AND report_status = 'ready'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '報表（new-feature N4、N5）。PDF 在 S3 的 reports/{report_id}.pdf；published_at 是 NULL 時服務人員看不到';


-- =============================================================================
-- 預存程序（13 個）：桌面端 10 個、原分析服務 3 個
--   參數數量、順序、IN/OUT 必須與呼叫端完全一致。
--   桌面端用 cursor.callproc(...)：mysql-connector 會先執行 SET @_<程序名>_argN = <值>，
--   再執行 CALL，最後以 SELECT 讀回所有參數。只有「程序內 SELECT 產生的結果集」會出現在
--   cursor.stored_results()。
--   原分析服務用 cursor.execute("CALL ...", 參數)，不讀任何結果，所以它的 3 個程序不回傳結果集。
--   所有程序都沒有 COMMIT，交易範圍由呼叫端決定。
-- =============================================================================
DELIMITER $$

-- -----------------------------------------------------------------------------
-- sp_check_login(username, password)
-- 呼叫：db:50 cursor(dictionary=True).callproc('sp_check_login', [username, password])
-- 確定：2 個 IN 參數，順序為 username、password。
-- 確定：用戶端只看 stored_results() 的「第一個」結果集，並對它執行 fetchone()（db:51-58）。
--       - 成功：第一列必須是 dict，且含 'account_level' 與 'account_id' 兩個鍵（db:55）。
--       - 失敗：必須回傳 0 列。若回傳一列全為 NULL 的資料，`if user:` 仍會成立，
--         用戶端就會當成登入成功。
--       - 不可以只用 OUT 參數回傳：沒有結果集時 for 迴圈不會執行，
--         check_login() 回傳 None，QML 會把 result.success 當成 undefined。
--       - account_level 必須是字串（login.qml:142 用 === "1"）。這裡回傳 CHAR(1) 欄位，
--         所以 Python 端拿到的是 str。
-- 推測：用明碼比對，因為現在的用戶端寫入與登入都使用明碼。
--       改成雜湊的方式見 db_report.md 第 d 節。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_check_login $$
DROP PROCEDURE IF EXISTS sp_get_login $$
CREATE PROCEDURE sp_get_login(
  IN p_username VARCHAR(64) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '登入：回傳帳號的 id、等級與密碼雜湊，由 Python 比對；找不到帳號時回傳 0 列'
BEGIN
  SELECT a.account_id,
         a.account_level,
         a.account_password
    FROM tbl_account AS a
   WHERE a.account_username = p_username
   LIMIT 1;
END $$

-- -----------------------------------------------------------------------------
-- sp_get_account_name(account_id)
-- 呼叫：db:67 callproc('sp_get_account_name', [account_id])
--       server_home.qml:47、admin_home.qml:50 透過 getAccountName() 顯示在側邊欄。
-- 確定：1 個 IN 參數。第一個結果集的第一列要有 'account_name' 鍵（db:68-72）；
--       找不到時回傳 0 列，用戶端會回傳 ""（db:74-75）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_get_account_name $$
CREATE PROCEDURE sp_get_account_name(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '回傳 1 列 (account_name)；找不到時回傳 0 列'
BEGIN
  SELECT a.account_name
    FROM tbl_account AS a
   WHERE a.account_id = p_account_id;
END $$

-- -----------------------------------------------------------------------------
-- new-feature S6（2026-11-01，T36）：自己改登入帳號、改密碼。三個程序一起用：
--   1. sp_get_account_password(account_id)：取回目前的密碼雜湊，由 Python 的 passwords.verify_password() 比對
--      使用者輸入的目前密碼（和 sp_get_login 一樣，比對不在資料庫做）。
--   2. sp_change_account_password／sp_change_account_username：只有密碼雜湊「還是剛才驗證過的那一個」才寫入
--      （compare-and-swap），回傳 1 列 (changed)。0 表示這段期間密碼在別處改過（或帳號不見了），
--      桌面端顯示「changed elsewhere, try again」，不拿過期的驗證覆寫新的值。
-- 為什麼要用預存程序：adam_app 對 tbl_account 只有欄位層級的 SELECT，沒有 account_password，
--   所以不能直接寫 UPDATE … WHERE account_password = …（WHERE 裡的欄位要有 SELECT）。
--   程序以建立者的權限執行，adam_app 只需要 EXECUTE。
-- 參數的 COLLATE utf8mb4_0900_bin 和 account_password 欄位一樣：不然比較時是兩個不同定序的
--   隱含值，MySQL 會丟 1267（Illegal mix of collations）。
-- 帳號重複：UPDATE 撞到 uk_account_username，丟 1062 給呼叫端（桌面端顯示「This username is taken」）。
--   帳號的比較照欄位的定序（utf8mb4_0900_ai_ci：不分大小寫），不在桌面端預先查。
-- 限制：和 sp_get_login 一樣，拿到 adam_app 帳密的人可以讀到雜湊；桌面端的身分檢查不是後端隔離（PLAN 13.7）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_get_account_password $$
CREATE PROCEDURE sp_get_account_password(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'S6：回傳 1 列 (account_password)，由 Python 比對目前的密碼；找不到帳號時回傳 0 列'
BEGIN
  SELECT a.account_password
    FROM tbl_account AS a
   WHERE a.account_id = p_account_id;
END $$

DROP PROCEDURE IF EXISTS sp_change_account_password $$
CREATE PROCEDURE sp_change_account_password(
  IN p_account_id    VARCHAR(36)  CHARACTER SET utf8mb4,
  IN p_expected_hash VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin,
  IN p_new_hash      VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin
)
  MODIFIES SQL DATA
  COMMENT 'S6：密碼雜湊還是 p_expected_hash 才換成 p_new_hash；回傳 1 列 (changed)，0＝已經在別處改過'
BEGIN
  UPDATE tbl_account
     SET account_password = p_new_hash
   WHERE account_id = p_account_id
     AND account_password = p_expected_hash;
  SELECT ROW_COUNT() AS changed;
END $$

DROP PROCEDURE IF EXISTS sp_change_account_username $$
CREATE PROCEDURE sp_change_account_username(
  IN p_account_id    VARCHAR(36)  CHARACTER SET utf8mb4,
  IN p_expected_hash VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin,
  IN p_new_username  VARCHAR(64)  CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'S6：密碼雜湊還是 p_expected_hash 才換登入帳號；回傳 1 列 (changed)，0＝已經在別處改過；重複時丟 1062'
BEGIN
  UPDATE tbl_account
     SET account_username = p_new_username
   WHERE account_id = p_account_id
     AND account_password = p_expected_hash;
  SELECT ROW_COUNT() AS changed;
END $$

-- -----------------------------------------------------------------------------
-- sp_insert_service(service_id, account_id, start, end, status, <第 6 個參數>)
-- 呼叫：rw:387 callproc('sp_insert_service',
--         (self.service_id, self.account_id, self.start_time, self.end_time, '1', 0))
-- 確定：6 個參數，順序如上。第 5 個參數傳入字串 '1'，第 6 個參數傳入 0。
--       用戶端忽略回傳值與結果集；整次錄影在同一個交易內，由 rw:402 commit。
-- 推測：第 6 個參數是 OUT。用 0 當 OUT 佔位符是 mysql-connector 官方文件 callproc 範例的寫法；
--       tbl_service 也沒有任何被用戶端讀取的第 6 個欄位。宣告成 IN 應該也能執行（推斷，未實測）。
--       這裡回傳 ROW_COUNT()（成功時為 1）。
-- 時間參數：Python 的 datetime.now() 帶有微秒，存進 DATETIME 時會四捨五入到秒。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_service $$
CREATE PROCEDURE sp_insert_service(
  IN  p_service_id     VARCHAR(32) CHARACTER SET utf8mb4,
  IN  p_account_id     VARCHAR(36) CHARACTER SET utf8mb4,
  IN  p_service_start  DATETIME,
  IN  p_service_end    DATETIME,
  IN  p_service_status TINYINT UNSIGNED,
  OUT p_result         INT
)
  MODIFIES SQL DATA
  COMMENT '新增服務（錄影結束時呼叫，狀態 1）；OUT p_result = 影響列數'
BEGIN
  INSERT INTO tbl_service (service_id, account_id, service_start, service_end, service_status)
  VALUES (p_service_id, p_account_id, p_service_start, p_service_end, p_service_status);
  SET p_result = ROW_COUNT();
END $$

-- -----------------------------------------------------------------------------
-- sp_insert_video_storage(service_id, video_name, video_url, upload_time)
-- 呼叫：rw:391-394，每次錄影呼叫 2 次（cam0、cam1）。
--       已知 bug：同一個視窗錄第 2 段時會呼叫 4 次，見 db_report.md 第 c 節。
-- 確定：4 個 IN 參數，順序如上；沒有 OUT，也不需要結果集。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_video_storage $$
CREATE PROCEDURE sp_insert_video_storage(
  IN p_service_id        VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_video_name        VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_video_url         VARCHAR(1024) CHARACTER SET utf8mb4,
  IN p_video_upload_time DATETIME
)
  MODIFIES SQL DATA
  COMMENT '新增錄影檔位置'
BEGIN
  INSERT INTO tbl_video_storage (service_id, video_name, video_url, video_upload_time)
  VALUES (p_service_id, p_video_name, p_video_url, p_video_upload_time);
END $$

-- -----------------------------------------------------------------------------
-- sp_insert_audio_storage(service_id, audio_name, audio_url, upload_time)
-- 呼叫：rw:399，每次錄影呼叫 1 次。
-- 確定：4 個 IN 參數，順序如上；沒有 OUT，也不需要結果集。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_audio_storage $$
CREATE PROCEDURE sp_insert_audio_storage(
  IN p_service_id        VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_audio_name        VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_audio_url         VARCHAR(1024) CHARACTER SET utf8mb4,
  IN p_audio_upload_time DATETIME
)
  MODIFIES SQL DATA
  COMMENT '新增錄音檔位置'
BEGIN
  INSERT INTO tbl_audio_storage (service_id, audio_name, audio_url, audio_upload_time)
  VALUES (p_service_id, p_audio_name, p_audio_url, p_audio_upload_time);
END $$

-- =============================================================================
-- 以下 3 個程序只有原本的分析服務會呼叫，桌面端從不呼叫。
-- 依原分析服務的呼叫推得；原始內容不明（original_analyzer.md 第 3.7、5 節）。
--   確定：程序名稱，以及參數的數量與順序。
--   確定：以 cursor.execute("CALL ...", 參數) 呼叫，接著 commit，不讀任何結果。
--         所以這裡不回傳結果集，也沒有 OUT 參數。
--   推測：寫入方式。原程式的註解說分數是「新增或更新」，這裡 3 個都做成 upsert
--         （以主鍵或 UNIQUE 鍵判斷）：同一筆服務重跑時只更新，不會多出重複的列。
--   移植後的分析服務沿用這 3 個程序，在同一個交易裡呼叫，最後一次 commit。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- sp_insert_service_score(service_id, 顧客人臉, 顧客聲音, 顧客文字,
--                         服務人員人臉, 服務人員聲音, 服務人員文字, 顧客總分, 服務人員總分)
-- 呼叫：sa/analysis_upload.py:30-43
--       cursor.execute("CALL sp_insert_service_score(%s, %s, %s, %s, %s, %s, %s, %s, %s)", data)
-- 確定：9 個 IN 參數，順序如上（和資料表欄位的順序不同）。分數 JSON 裡的 staff 對應 server_* 欄位。
-- 推測：INSERT ... ON DUPLICATE KEY UPDATE，以主鍵 service_id 判斷。
-- 寫入失敗的情況：值超出 -1 或 0～100（CHECK，錯誤 3819）、NULL（NOT NULL，錯誤 1048）、
--   service_id 不在 tbl_service（外鍵，錯誤 1452）。原分析服務的總分沒有限制範圍，可能踩到第一種。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_service_score $$
CREATE PROCEDURE sp_insert_service_score(
  IN p_service_id      VARCHAR(32) CHARACTER SET utf8mb4,
  IN p_customer_facial DOUBLE,
  IN p_customer_audio  DOUBLE,
  IN p_customer_text   DOUBLE,
  IN p_server_facial   DOUBLE,
  IN p_server_audio    DOUBLE,
  IN p_server_text     DOUBLE,
  IN p_customer_total  DOUBLE,
  IN p_server_total    DOUBLE
)
  MODIFIES SQL DATA
  COMMENT '新增或更新一筆服務的 8 個分數（分析服務呼叫）；不回傳結果集'
BEGIN
  INSERT INTO tbl_service_score
    (service_id, customer_facial, customer_audio, customer_text,
     server_facial, server_audio, server_text, customer_total, server_total)
  VALUES
    (p_service_id, p_customer_facial, p_customer_audio, p_customer_text,
     p_server_facial, p_server_audio, p_server_text, p_customer_total, p_server_total)
  ON DUPLICATE KEY UPDATE
    customer_facial  = p_customer_facial,
    customer_audio   = p_customer_audio,
    customer_text    = p_customer_text,
    server_facial    = p_server_facial,
    server_audio     = p_server_audio,
    server_text      = p_server_text,
    customer_total   = p_customer_total,
    server_total     = p_server_total,
    score_created_at = CURRENT_TIMESTAMP;
END $$

-- -----------------------------------------------------------------------------
-- sp_insert_charts_storage(service_id, 檔名, url)
-- 呼叫：sa/analysis_upload.py:75-79，每張圖呼叫一次。原分析服務依 os.listdir 的順序呼叫；
--       移植後的分析服務依固定順序呼叫（預設，待你決定；見 analysis_report.md 第 2 節）。
-- 確定：3 個 IN 參數，順序如上。
-- 推測：寫入的欄位，以及用 uk_charts_service_name 做 upsert：同一筆服務、同一個檔名
--       重跑時只更新 url 與時間，charts_id 不變，所以依 charts_id 排的顯示順序也不變。
--       檔名傳 NULL 時不會被當成重複，每次都會新增一列。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_charts_storage $$
CREATE PROCEDURE sp_insert_charts_storage(
  IN p_service_id  VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_charts_name VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_charts_url  VARCHAR(1024) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT '新增或更新一張圖表的位置（分析服務呼叫）；不回傳結果集'
BEGIN
  INSERT INTO tbl_charts_storage (service_id, charts_name, charts_url)
  VALUES (p_service_id, p_charts_name, p_charts_url)
  ON DUPLICATE KEY UPDATE
    charts_url         = p_charts_url,
    charts_upload_time = CURRENT_TIMESTAMP;
END $$

-- -----------------------------------------------------------------------------
-- sp_insert_text_storage(service_id, 檔名, url)
-- 呼叫：sa/analysis_upload.py:58-62，text/ 底下每個以 service_id 開頭的檔案呼叫一次
--       （實際上只有 {service_id}_transcript.txt）。
-- 確定：3 個 IN 參數，順序如上。
-- 推測：資料表與欄位名稱，以及用 uk_text_service_name 做 upsert。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_text_storage $$
CREATE PROCEDURE sp_insert_text_storage(
  IN p_service_id VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_text_name  VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_text_url   VARCHAR(1024) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT '新增或更新逐字稿的位置（分析服務呼叫）；不回傳結果集'
BEGIN
  INSERT INTO tbl_text_storage (service_id, text_name, text_url)
  VALUES (p_service_id, p_text_name, p_text_url)
  ON DUPLICATE KEY UPDATE
    text_url         = p_text_url,
    text_upload_time = CURRENT_TIMESTAMP;
END $$

-- -----------------------------------------------------------------------------
-- sp_list_reports(account_id)（new-feature PLAN 7.2，2026-11-01 新增）
-- 呼叫：桌面端的報表清單，db lane 上用 cursor.execute("CALL sp_list_reports(%s)", (account_id,))。
-- 由資料庫依等級決定回傳哪些列，桌面端不自己組條件：
--   - 服務人員（等級 '1'）：只有自己的（staff_account_id = 自己）、已發佈的，不含 company。
--   - 管理員（等級 '2'～'5'）：全部，含尚未發佈。
--   - 找不到帳號：0 列。
-- 每個對象（種類、服務、員工、期間）只回傳最新的一筆（R5）。服務人員先篩掉未發佈的再取最新，
--   所以新的一筆還沒發佈時，仍看到上一筆已發佈的，不會變成空白（7.1）。
-- 新的在前。staff_name、staff_username 是報表對象目前的名字和登入帳號（company 是 NULL）；
--   service_start、service_end 只有每次服務報表才有。
-- 限制：所有桌面程式共用 adam_app，這裡擋不住直接查資料表的人（PLAN 7.2、第 11 節）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_list_reports $$
CREATE PROCEDURE sp_list_reports(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '報表清單：服務人員只有自己已發佈的（不含全公司），管理員全部；每個對象最新一筆'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;

  SELECT a.account_level INTO v_level
    FROM tbl_account AS a
   WHERE a.account_id = p_account_id;

  SELECT r.report_id, r.report_kind, r.service_id, r.staff_account_id,
         a.account_name AS staff_name, a.account_username AS staff_username,
         r.period_start, r.period_end, r.report_status, r.failure_reason, r.s3_key,
         r.created_by, r.created_at, r.published_at, r.published_by,
         r.ai_model, r.ai_prompt_version,
         s.service_start, s.service_end
    FROM (
      SELECT t.*,
             ROW_NUMBER() OVER (PARTITION BY t.report_kind, t.service_id, t.staff_account_id,
                                             t.period_start, t.period_end
                                ORDER BY t.created_at DESC, t.report_id DESC) AS newest
        FROM tbl_report AS t
       WHERE v_level IS NOT NULL
         AND (v_level <> '1'
              OR (t.staff_account_id = p_account_id
                  AND t.published_at IS NOT NULL
                  AND t.report_kind <> 'company'))
    ) AS r
    LEFT JOIN tbl_account AS a ON a.account_id = r.staff_account_id
    LEFT JOIN tbl_service AS s ON s.service_id = r.service_id
   WHERE r.newest = 1
   ORDER BY r.created_at DESC, r.report_id DESC;
END $$

-- -----------------------------------------------------------------------------
-- sp_list_service_report_states(account_id)（new-feature PLAN 7.3，2026-11-01 新增，migration 003）
-- 呼叫：服務人員端分析頁 Completed 清單的 Report 欄（Not generated yet／Not published yet／打開 PDF），
--       db lane 上用 cursor.execute("CALL sp_list_service_report_states(%s)", (account_id,))。
-- sp_list_reports 不給服務人員看還沒發佈的列（R6），所以另外一支：只回傳「狀態」，不回傳還沒發佈的報表本身。
--   - 只看 staff_account_id = 自己的每次服務報表（管理員沒有自己的服務：0 列；找不到帳號：0 列）。
--   - 每筆服務一列，只列已經有 PDF（report_status = ready）的服務：
--       report_state = 'published'：發佈過；published_report_id 是最新一筆已發佈的
--                                  （和 sp_list_reports 給服務人員的那一筆相同）。
--       report_state = 'unpublished'：有 PDF、但還沒有任何一筆發佈；published_report_id 是 NULL。
--   - 沒有出現的服務：還沒有 PDF（沒產生過，或只有產生中、失敗的）。
-- 限制同 sp_list_reports：所有桌面程式共用 adam_app（PLAN 7.2、第 11 節）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_list_service_report_states $$
CREATE PROCEDURE sp_list_service_report_states(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '自己每筆服務的報表狀態：published（附最新已發佈的 report_id）或 unpublished；不回傳未發佈的報表'
BEGIN
  SELECT r.service_id,
         IF(p.report_id IS NULL, 'unpublished', 'published') AS report_state,
         p.report_id AS published_report_id
    FROM (
      SELECT DISTINCT t.service_id
        FROM tbl_report AS t
       WHERE t.report_kind = 'service'
         AND t.staff_account_id = p_account_id
         AND t.report_status = 'ready'
    ) AS r
    LEFT JOIN (
      SELECT t.service_id, t.report_id,
             ROW_NUMBER() OVER (PARTITION BY t.service_id
                                ORDER BY t.created_at DESC, t.report_id DESC) AS newest
        FROM tbl_report AS t
       WHERE t.report_kind = 'service'
         AND t.staff_account_id = p_account_id
         AND t.published_at IS NOT NULL
    ) AS p ON p.service_id = r.service_id AND p.newest = 1
   ORDER BY r.service_id;
END $$

DELIMITER ;


-- #############################################################################
-- [BEGIN PLAN1 W2] 員工輔導與後續追蹤（2026-11-01，T45）：3 張表、13 個預存程序
--   內容和 migrations/006_coaching.sql 相同，只差程序在這裡是 DROP + CREATE（這個檔案的慣例），
--   006 用 CREATE … IF NOT EXISTS、不 DROP（T42 第 9 節）。GRANT 在最後選用區塊的 [BEGIN PLAN1 W2 GRANTS]。
--   這一整段只依賴上面的 tbl_service、tbl_account，可以原樣搬到別的 schema（例如測試用的精簡 schema）。
--   桌面端的讀寫全部經過這 13 個程序：每個都帶呼叫者的 account_id，由資料庫依等級決定能看什麼、做什麼
--   （服務人員只有自己的行動、只能回應和確認；管理員全部）。寫入的程序不 COMMIT，桌面端包在交易裡。
--   不能做的事用 SIGNAL SQLSTATE '45000'（errno 1644），MESSAGE_TEXT 是 coaching:<原因>（說明見 migration 006）。
-- #############################################################################

-- =============================================================================
-- tbl_coaching_action：改善行動。一筆＝主管從一筆服務（原始服務）訂的一項改善目標。
--   員工（staff_account_id）是原始服務的 account_id，由 sp_coaching_create 寫入，不是畫面選的。
--   狀態只往前走：open（進行中）→ completed（看過後續服務、寫了結論）或 cancelled（撤回，寫原因）。
--   「調整」不是狀態：目標、期限換成新的，仍是 open；看過的後續服務從這裡拿掉，舊的值都在 tbl_coaching_event。
--   version：樂觀鎖。主管的每一次修改（連結、完成、調整、撤回）都 +1，帶的版本不對就不改（coaching:changed）。
--   回應、確認、時間註記不改 version：它們是另外的列，和主管的修改不衝突。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_coaching_action (
  -- 桌面端用 uuid4 產生（36 字元）；重試時用同一個，撞到主鍵就是上一次已經寫好了
  action_id            VARCHAR(36)   NOT NULL,
  -- 原始服務：主管看的那一筆（作為例子），之後不能改
  source_service_id    VARCHAR(32)   NOT NULL,
  staff_account_id     VARCHAR(36)   NOT NULL,
  created_by           VARCHAR(36)   NOT NULL,
  -- 改善目標：員工要練的一句話，例如「服務結束前說明下一步，並確認顧客理解」
  goal                 VARCHAR(300)  NOT NULL,
  -- 主管評語：為什麼訂這個目標（員工靠目標和這段話，PLAN1「服務人員依靠什麼」）
  manager_comment      VARCHAR(2000) NOT NULL,
  -- 追蹤期限（到期時主管另選一筆後續服務來看）
  due_date             DATE          NOT NULL,
  action_status        VARCHAR(16)   NOT NULL DEFAULT 'open',
  -- 後續服務：同一位員工、分析完成、比原始服務晚的一筆（sp_coaching_link_follow_up 檢查）。調整時清掉
  follow_up_service_id VARCHAR(32)   NULL,
  -- 員工按 Acknowledge 的時間：只代表已讀、已回應，不是完成。目標調整時清掉（要再確認一次新的目標）
  acknowledged_at      DATETIME(3)   NULL,
  -- completed：主管的結論；cancelled：撤回的原因
  conclusion           VARCHAR(2000) NULL,
  concluded_by         VARCHAR(36)   NULL,
  concluded_at         DATETIME(3)   NULL,
  created_at           DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at           DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  version              INT UNSIGNED  NOT NULL DEFAULT 1,

  PRIMARY KEY (action_id),
  KEY idx_coaching_staff_status (staff_account_id, action_status, due_date),   -- 服務人員的清單
  KEY idx_coaching_status_due (action_status, due_date),                     -- 管理員的清單
  KEY idx_coaching_source (source_service_id),
  KEY idx_coaching_follow_up (follow_up_service_id),
  CONSTRAINT fk_coaching_source FOREIGN KEY (source_service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_staff FOREIGN KEY (staff_account_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_created_by FOREIGN KEY (created_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_follow_up FOREIGN KEY (follow_up_service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_concluded_by FOREIGN KEY (concluded_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_coaching_status CHECK (action_status IN ('open', 'completed', 'cancelled')),
  CONSTRAINT chk_coaching_goal CHECK (CHAR_LENGTH(TRIM(goal)) > 0),
  CONSTRAINT chk_coaching_comment CHECK (CHAR_LENGTH(TRIM(manager_comment)) > 0),
  -- 後續服務不能是原始服務本身（同一位員工、比較晚、分析完成要查別的表，在程序裡檢查）
  CONSTRAINT chk_coaching_follow_up CHECK (follow_up_service_id IS NULL OR follow_up_service_id <> source_service_id),
  -- 進行中沒有結論；完成要有後續服務和結論；撤回要有原因。IS NOT NULL 要寫出來：CHECK 遇到 NULL 算通過
  CONSTRAINT chk_coaching_concluded CHECK (
       (action_status = 'open' AND conclusion IS NULL AND concluded_by IS NULL AND concluded_at IS NULL)
    OR (action_status = 'completed' AND follow_up_service_id IS NOT NULL AND conclusion IS NOT NULL
        AND CHAR_LENGTH(TRIM(conclusion)) > 0 AND concluded_by IS NOT NULL AND concluded_at IS NOT NULL)
    OR (action_status = 'cancelled' AND conclusion IS NOT NULL AND CHAR_LENGTH(TRIM(conclusion)) > 0
        AND concluded_by IS NOT NULL AND concluded_at IS NOT NULL))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'W2 改善行動：原始服務、目標、期限、後續服務、結論；讀寫一律經由 sp_coaching_*';


-- =============================================================================
-- tbl_coaching_marker：時間註記。只屬於一筆服務的影片：offset_seconds 從那一筆自己的 service_start（影片開頭）算，
--   原始服務的 02:15 和後續服務的 02:15 不是同一秒（PLAN1 W2）。只能加在原始服務或目前的後續服務上。
--   拿掉時不刪：寫 removed_at、removed_by，並在 tbl_coaching_event 留一列。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_coaching_marker (
  marker_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  action_id      VARCHAR(36)     NOT NULL,
  service_id     VARCHAR(32)     NOT NULL,
  offset_seconds INT UNSIGNED    NOT NULL,
  note           VARCHAR(300)    NOT NULL DEFAULT '',
  created_by     VARCHAR(36)     NOT NULL,
  created_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  removed_by     VARCHAR(36)     NULL,
  removed_at     DATETIME(3)     NULL,

  PRIMARY KEY (marker_id),
  KEY idx_coaching_marker_action (action_id, service_id, offset_seconds),
  KEY idx_coaching_marker_service (service_id),
  CONSTRAINT fk_coaching_marker_action FOREIGN KEY (action_id)
    REFERENCES tbl_coaching_action (action_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_marker_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_marker_created_by FOREIGN KEY (created_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_marker_removed_by FOREIGN KEY (removed_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_coaching_marker_offset CHECK (offset_seconds < 86400),
  CONSTRAINT chk_coaching_marker_removed CHECK ((removed_by IS NULL AND removed_at IS NULL)
                                                OR (removed_by IS NOT NULL AND removed_at IS NOT NULL))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'W2 時間註記：秒數從那一筆服務自己的開頭算；拿掉的留著（removed_at）';


-- =============================================================================
-- tbl_coaching_event：歷史，只新增、不修改（adam_app 沒有這張表的權限，只能經由程序新增）。
--   每一次改變都有一列：建立、雙方的回應、確認、連結後續服務、加或拿掉時間註記、完成、調整、撤回。
--   author_role 是寫的當下的身分（admin、staff）：等級之後改了，舊的回應仍看得出是誰的立場。
--   調整：goal_before／goal_after、due_before／due_after 是舊的和新的值；service_id 是那一次看過的後續服務。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_coaching_event (
  event_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  action_id      VARCHAR(36)     NOT NULL,
  event_type     VARCHAR(24)     NOT NULL,
  author_id      VARCHAR(36)     NOT NULL,
  author_role    VARCHAR(8)      NOT NULL,
  -- 回應的內容、建立時的評語、完成的結論、調整或撤回的原因、時間註記的說明
  body           VARCHAR(2000)   NULL,
  goal_before    VARCHAR(300)    NULL,
  goal_after     VARCHAR(300)    NULL,
  due_before     DATE            NULL,
  due_after      DATE            NULL,
  -- 建立：原始服務；連結、完成、調整：後續服務；時間註記：它所在的服務
  service_id     VARCHAR(32)     NULL,
  marker_id      BIGINT UNSIGNED NULL,
  marker_seconds INT UNSIGNED    NULL,
  created_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

  PRIMARY KEY (event_id),
  KEY idx_coaching_event_action (action_id, created_at),
  CONSTRAINT fk_coaching_event_action FOREIGN KEY (action_id)
    REFERENCES tbl_coaching_action (action_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_event_author FOREIGN KEY (author_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_event_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_event_marker FOREIGN KEY (marker_id)
    REFERENCES tbl_coaching_marker (marker_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_coaching_event_type CHECK (event_type IN ('created', 'response', 'acknowledged', 'follow_up_linked',
                                                           'marker_added', 'marker_removed', 'completed', 'adjusted',
                                                           'cancelled')),
  CONSTRAINT chk_coaching_event_role CHECK (author_role IN ('admin', 'staff'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'W2 歷史：只新增、不修改；誰、什麼時候、改了什麼（舊值和新值）';


DELIMITER $$

-- -----------------------------------------------------------------------------
-- sp_coaching_list(caller)：清單。服務人員只有自己的，管理員全部。進行中的在前，再依期限、建立時間。
--   last_staff_at、last_admin_at：雙方最後一次動作的時間（管理員的清單標「Staff replied」）。
-- 呼叫：db lane 上 cursor.execute("CALL sp_coaching_list(%s)", (account_id,))，1 個往返。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_list $$
CREATE PROCEDURE sp_coaching_list(
  IN p_caller VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：改善行動的清單；服務人員只有自己的，管理員全部'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;

  SELECT a.action_id, a.source_service_id, src.service_start AS source_start, src.service_end AS source_end,
         a.staff_account_id, st.account_name AS staff_name, st.account_username AS staff_username,
         a.created_by, cr.account_name AS created_by_name,
         a.goal, a.due_date, a.action_status,
         a.follow_up_service_id, fu.service_start AS follow_up_start, fu.service_end AS follow_up_end,
         a.acknowledged_at, a.concluded_by, cb.account_name AS concluded_by_name, a.concluded_at,
         a.created_at, a.updated_at, a.version, ev.last_staff_at, ev.last_admin_at
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS src ON src.service_id = a.source_service_id
    JOIN tbl_account AS st ON st.account_id = a.staff_account_id
    LEFT JOIN tbl_account AS cr ON cr.account_id = a.created_by
    LEFT JOIN tbl_account AS cb ON cb.account_id = a.concluded_by
    LEFT JOIN tbl_service AS fu ON fu.service_id = a.follow_up_service_id
    LEFT JOIN (SELECT e.action_id,
                      MAX(IF(e.author_role = 'staff', e.created_at, NULL)) AS last_staff_at,
                      MAX(IF(e.author_role = 'admin', e.created_at, NULL)) AS last_admin_at
                 FROM tbl_coaching_event AS e
                GROUP BY e.action_id) AS ev ON ev.action_id = a.action_id
   WHERE v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller)
   ORDER BY a.action_status = 'open' DESC, a.due_date, a.created_at DESC, a.action_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_detail(caller, action_id)：一項行動的明細，3 個結果集（1 個往返）：
--   1. 行動（清單的欄位，加上主管評語和結論）；2. 時間註記（沒有拿掉的，依服務的時間、秒數排）；
--   3. 歷史（舊的在前）。看不到的行動（別人的、不存在的）3 個結果集都是 0 列，不透露存不存在。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_detail $$
CREATE PROCEDURE sp_coaching_detail(
  IN p_caller    VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：一項行動的明細（行動、時間註記、歷史）；看不到的 3 個結果集都是 0 列'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_visible INT DEFAULT 0;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  SELECT COUNT(*) INTO v_visible
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id
     AND v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller);

  SELECT a.action_id, a.source_service_id, src.service_start AS source_start, src.service_end AS source_end,
         a.staff_account_id, st.account_name AS staff_name, st.account_username AS staff_username,
         a.created_by, cr.account_name AS created_by_name,
         a.goal, a.due_date, a.action_status,
         a.follow_up_service_id, fu.service_start AS follow_up_start, fu.service_end AS follow_up_end,
         a.acknowledged_at, a.concluded_by, cb.account_name AS concluded_by_name, a.concluded_at,
         a.created_at, a.updated_at, a.version, ev.last_staff_at, ev.last_admin_at,
         a.manager_comment, a.conclusion
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS src ON src.service_id = a.source_service_id
    JOIN tbl_account AS st ON st.account_id = a.staff_account_id
    LEFT JOIN tbl_account AS cr ON cr.account_id = a.created_by
    LEFT JOIN tbl_account AS cb ON cb.account_id = a.concluded_by
    LEFT JOIN tbl_service AS fu ON fu.service_id = a.follow_up_service_id
    LEFT JOIN (SELECT e.action_id,
                      MAX(IF(e.author_role = 'staff', e.created_at, NULL)) AS last_staff_at,
                      MAX(IF(e.author_role = 'admin', e.created_at, NULL)) AS last_admin_at
                 FROM tbl_coaching_event AS e
                WHERE e.action_id = p_action_id
                GROUP BY e.action_id) AS ev ON ev.action_id = a.action_id
   WHERE a.action_id = p_action_id AND v_visible > 0;

  SELECT m.marker_id, m.service_id, s.service_start, m.offset_seconds, m.note,
         m.created_by, au.account_name AS created_by_name, m.created_at
    FROM tbl_coaching_marker AS m
    JOIN tbl_service AS s ON s.service_id = m.service_id
    LEFT JOIN tbl_account AS au ON au.account_id = m.created_by
   WHERE m.action_id = p_action_id AND m.removed_at IS NULL AND v_visible > 0
   ORDER BY s.service_start, m.offset_seconds, m.marker_id;

  SELECT e.event_id, e.event_type, e.author_id, au.account_name AS author_name, e.author_role, e.body,
         e.goal_before, e.goal_after, e.due_before, e.due_after, e.service_id, s.service_start,
         e.marker_id, e.marker_seconds, e.created_at
    FROM tbl_coaching_event AS e
    LEFT JOIN tbl_account AS au ON au.account_id = e.author_id
    LEFT JOIN tbl_service AS s ON s.service_id = e.service_id
   WHERE e.action_id = p_action_id AND v_visible > 0
   ORDER BY e.created_at, e.event_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_for_service(caller, service_id)：管理員明細頁的輔導卡。2 個結果集（1 個往返）：
--   1. 和這筆服務有關的行動：relation 是 source（從這筆訂的）、follow_up（這筆是目前的後續服務）、
--      reviewed（調整時看過這一筆）；2. 這筆服務上的時間註記（沒有拿掉的，依秒數排）。
--   看得到的範圍和清單一樣（服務人員只有自己的）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_for_service $$
CREATE PROCEDURE sp_coaching_for_service(
  IN p_caller     VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_service_id VARCHAR(32) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：和一筆服務有關的行動（原始、後續、調整時看過的）和這筆服務上的時間註記'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;

  SELECT a.action_id, a.source_service_id, a.staff_account_id, st.account_name AS staff_name,
         st.account_username AS staff_username, a.goal, a.due_date, a.action_status,
         a.follow_up_service_id, a.version, a.created_at,
         CASE WHEN a.source_service_id = p_service_id THEN 'source'
              WHEN a.follow_up_service_id = p_service_id THEN 'follow_up'
              ELSE 'reviewed' END AS relation
    FROM tbl_coaching_action AS a
    JOIN tbl_account AS st ON st.account_id = a.staff_account_id
   WHERE v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller)
     AND (a.source_service_id = p_service_id
          OR a.follow_up_service_id = p_service_id
          OR EXISTS (SELECT 1 FROM tbl_coaching_event AS e
                      WHERE e.action_id = a.action_id AND e.event_type = 'adjusted' AND e.service_id = p_service_id))
   ORDER BY a.action_status = 'open' DESC, a.created_at DESC, a.action_id;

  SELECT m.marker_id, m.action_id, m.offset_seconds, m.note, m.created_by, au.account_name AS created_by_name,
         m.created_at
    FROM tbl_coaching_marker AS m
    JOIN tbl_coaching_action AS a ON a.action_id = m.action_id
    LEFT JOIN tbl_account AS au ON au.account_id = m.created_by
   WHERE m.service_id = p_service_id AND m.removed_at IS NULL
     AND v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller)
   ORDER BY m.offset_seconds, m.marker_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_follow_up_options(caller, action_id)：能當後續服務的服務（只有管理員；服務人員 0 列）。
--   同一位員工、分析完成（3）、不是原始服務、比原始服務晚；調整過的話，還要比調整時看過的後續服務晚。
--   新的在前，最多 50 筆。sp_coaching_link_follow_up 用同一套規則再檢查一次。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_follow_up_options $$
CREATE PROCEDURE sp_coaching_follow_up_options(
  IN p_caller    VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：能當後續服務的服務（管理員）：同一位員工、分析完成、比原始服務和看過的後續服務晚'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_source VARCHAR(32) DEFAULT NULL;
  DECLARE v_after DATETIME DEFAULT NULL;
  DECLARE v_reviewed DATETIME DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  SELECT a.staff_account_id, a.source_service_id, s.service_start INTO v_staff, v_source, v_after
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS s ON s.service_id = a.source_service_id
   WHERE a.action_id = p_action_id;
  SELECT MAX(s.service_start) INTO v_reviewed
    FROM tbl_coaching_event AS e
    JOIN tbl_service AS s ON s.service_id = e.service_id
   WHERE e.action_id = p_action_id AND e.event_type = 'adjusted';
  IF v_reviewed IS NOT NULL AND v_reviewed > v_after THEN
    SET v_after = v_reviewed;
  END IF;

  SELECT s.service_id, s.service_start, s.service_end
    FROM tbl_service AS s
   WHERE v_level IS NOT NULL AND v_level <> '1' AND v_staff IS NOT NULL
     AND s.account_id = v_staff AND s.service_status = 3
     AND s.service_id <> v_source AND s.service_start > v_after
   ORDER BY s.service_start DESC, s.service_id DESC
   LIMIT 50;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_create(caller, action_id, source_service_id, goal, comment, due_date)：主管從一筆服務建立改善目標。
--   員工是那筆服務的 account_id（要是等級 1，coaching:not_staff）。寫一列行動和一列 created 歷史。
--   時間註記另外用 sp_coaching_add_marker，同一個交易（桌面端：START TRANSACTION、這個、每個註記一個、COMMIT）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_create $$
CREATE PROCEDURE sp_coaching_create(
  IN p_caller            VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id         VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_source_service_id VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_goal              VARCHAR(300)  CHARACTER SET utf8mb4,
  IN p_comment           VARCHAR(2000) CHARACTER SET utf8mb4,
  IN p_due_date          DATE
)
  MODIFIES SQL DATA
  COMMENT 'W2：主管從一筆服務建立改善目標（員工是那筆服務的 account_id）；呼叫端包在交易裡'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_staff_level CHAR(1) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT s.account_id, ac.account_level INTO v_staff, v_staff_level
    FROM tbl_service AS s
    JOIN tbl_account AS ac ON ac.account_id = s.account_id
   WHERE s.service_id = p_source_service_id;
  IF v_staff IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_staff_level <> '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_staff';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_goal, ''))) = 0 OR CHAR_LENGTH(TRIM(COALESCE(p_comment, ''))) = 0
     OR p_due_date IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  INSERT INTO tbl_coaching_action
    (action_id, source_service_id, staff_account_id, created_by, goal, manager_comment, due_date)
  VALUES (p_action_id, p_source_service_id, v_staff, p_caller, TRIM(p_goal), TRIM(p_comment), p_due_date);
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body, goal_after, due_after, service_id)
  VALUES (p_action_id, 'created', p_caller, 'admin', TRIM(p_comment), TRIM(p_goal), p_due_date, p_source_service_id);
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_add_marker(caller, action_id, service_id, offset_seconds, note)：加一個時間註記（管理員，進行中）。
--   只能加在原始服務或目前的後續服務上（coaching:marker_service）；秒數 0～86399。回傳 1 列 (marker_id)。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_add_marker $$
CREATE PROCEDURE sp_coaching_add_marker(
  IN p_caller         VARCHAR(36)  CHARACTER SET utf8mb4,
  IN p_action_id      VARCHAR(36)  CHARACTER SET utf8mb4,
  IN p_service_id     VARCHAR(32)  CHARACTER SET utf8mb4,
  IN p_offset_seconds INT,
  IN p_note           VARCHAR(300) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：在原始服務或目前的後續服務上加一個時間註記（管理員）；回傳 1 列 (marker_id)'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_source VARCHAR(32) DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;
  DECLARE v_marker BIGINT UNSIGNED DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.source_service_id, a.follow_up_service_id INTO v_status, v_source, v_follow_up
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF p_service_id IS NULL OR NOT (p_service_id = v_source OR p_service_id <=> v_follow_up) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:marker_service';
  END IF;
  IF p_offset_seconds IS NULL OR p_offset_seconds < 0 OR p_offset_seconds >= 86400 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  INSERT INTO tbl_coaching_marker (action_id, service_id, offset_seconds, note, created_by)
  VALUES (p_action_id, p_service_id, p_offset_seconds, TRIM(COALESCE(p_note, '')), p_caller);
  SET v_marker = LAST_INSERT_ID();
  INSERT INTO tbl_coaching_event
    (action_id, event_type, author_id, author_role, body, service_id, marker_id, marker_seconds)
  VALUES (p_action_id, 'marker_added', p_caller, 'admin', NULLIF(TRIM(COALESCE(p_note, '')), ''), p_service_id,
          v_marker, p_offset_seconds);
  SELECT v_marker AS marker_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_remove_marker(caller, marker_id)：拿掉一個時間註記（管理員，進行中）。不刪：寫 removed_at、removed_by，
--   並留一列 marker_removed 歷史（秒數、所在的服務、說明）。已經拿掉的什麼都不做。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_remove_marker $$
CREATE PROCEDURE sp_coaching_remove_marker(
  IN p_caller    VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_marker_id BIGINT UNSIGNED
)
  MODIFIES SQL DATA
  COMMENT 'W2：拿掉一個時間註記（管理員）；標成已拿掉、寫一列歷史，不刪資料'
body: BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_action VARCHAR(36) DEFAULT NULL;
  DECLARE v_service VARCHAR(32) DEFAULT NULL;
  DECLARE v_seconds INT UNSIGNED DEFAULT NULL;
  DECLARE v_note VARCHAR(300) DEFAULT NULL;
  DECLARE v_removed DATETIME(3) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT m.action_id, m.service_id, m.offset_seconds, m.note, m.removed_at, a.action_status
    INTO v_action, v_service, v_seconds, v_note, v_removed, v_status
    FROM tbl_coaching_marker AS m
    JOIN tbl_coaching_action AS a ON a.action_id = m.action_id
   WHERE m.marker_id = p_marker_id;
  IF v_action IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_removed IS NOT NULL THEN
    LEAVE body;
  END IF;

  UPDATE tbl_coaching_marker
     SET removed_by = p_caller, removed_at = NOW(3)
   WHERE marker_id = p_marker_id AND removed_at IS NULL;
  IF ROW_COUNT() = 1 THEN
    INSERT INTO tbl_coaching_event
      (action_id, event_type, author_id, author_role, body, service_id, marker_id, marker_seconds)
    VALUES (v_action, 'marker_removed', p_caller, 'admin', NULLIF(v_note, ''), v_service, p_marker_id, v_seconds);
  END IF;
END body $$

-- -----------------------------------------------------------------------------
-- sp_coaching_respond(caller, action_id, body)：回應（補充情境、回應、提出不同看法；主管也可以回）。
--   服務人員只能回自己的行動（別人的當作不存在，coaching:not_found）；只有進行中的行動可以回。只新增一列歷史。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_respond $$
CREATE PROCEDURE sp_coaching_respond(
  IN p_caller    VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_body      VARCHAR(2000) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：回應（服務人員：自己的行動；管理員：全部）；只新增一列歷史'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.staff_account_id, a.action_status INTO v_staff, v_status
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_staff IS NULL OR (v_level = '1' AND v_staff <> p_caller) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_body, ''))) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body)
  VALUES (p_action_id, 'response', p_caller, IF(v_level = '1', 'staff', 'admin'), TRIM(p_body));
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_acknowledge(caller, action_id, expected_version)：員工確認看過目前的目標（只代表已回應，不是完成）。
--   只有那位員工自己能按；目標在這段期間調整過（version 不對）就是 coaching:changed，要先看新的目標。
--   已經確認過的什麼都不做。不改 version。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_acknowledge $$
CREATE PROCEDURE sp_coaching_acknowledge(
  IN p_caller           VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED
)
  MODIFIES SQL DATA
  COMMENT 'W2：員工確認看過目前的目標（只代表已回應）；版本不對是 coaching:changed'
body: BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_acknowledged DATETIME(3) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level <> '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.staff_account_id, a.action_status, a.version, a.acknowledged_at
    INTO v_staff, v_status, v_version, v_acknowledged
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_staff IS NULL OR v_staff <> p_caller THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF v_acknowledged IS NOT NULL THEN
    LEAVE body;
  END IF;

  UPDATE tbl_coaching_action
     SET acknowledged_at = NOW(3)
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open'
     AND acknowledged_at IS NULL;
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role)
  VALUES (p_action_id, 'acknowledged', p_caller, 'staff');
END body $$

-- -----------------------------------------------------------------------------
-- sp_coaching_link_follow_up(caller, action_id, expected_version, service_id)：連結（或換掉）後續服務（管理員，進行中）。
--   規則和 sp_coaching_follow_up_options 一樣（不合就是 coaching:follow_up）。換掉時，舊的後續服務上的時間註記
--   一起拿掉（各留一列 marker_removed 歷史）：它們只屬於那一部影片。已經是這一筆的什麼都不做。version +1。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_link_follow_up $$
CREATE PROCEDURE sp_coaching_link_follow_up(
  IN p_caller           VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_service_id       VARCHAR(32) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：連結或換掉後續服務（管理員）：同一位員工、分析完成、比原始服務和看過的後續服務晚、不是原始服務'
body: BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_source VARCHAR(32) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;
  DECLARE v_after DATETIME DEFAULT NULL;
  DECLARE v_reviewed DATETIME DEFAULT NULL;
  DECLARE v_eligible INT DEFAULT 0;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.staff_account_id, a.source_service_id, a.action_status, a.version, a.follow_up_service_id, s.service_start
    INTO v_staff, v_source, v_status, v_version, v_follow_up, v_after
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS s ON s.service_id = a.source_service_id
   WHERE a.action_id = p_action_id;
  IF v_staff IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  SELECT MAX(s.service_start) INTO v_reviewed
    FROM tbl_coaching_event AS e
    JOIN tbl_service AS s ON s.service_id = e.service_id
   WHERE e.action_id = p_action_id AND e.event_type = 'adjusted';
  IF v_reviewed IS NOT NULL AND v_reviewed > v_after THEN
    SET v_after = v_reviewed;
  END IF;
  SELECT COUNT(*) INTO v_eligible
    FROM tbl_service AS s
   WHERE s.service_id = p_service_id AND s.account_id = v_staff AND s.service_status = 3
     AND s.service_id <> v_source AND s.service_start > v_after;
  IF v_eligible = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:follow_up';
  END IF;
  IF p_service_id <=> v_follow_up THEN
    LEAVE body;
  END IF;

  UPDATE tbl_coaching_action
     SET follow_up_service_id = p_service_id, version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open';
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, service_id)
  VALUES (p_action_id, 'follow_up_linked', p_caller, 'admin', p_service_id);
  IF v_follow_up IS NOT NULL THEN
    INSERT INTO tbl_coaching_event
      (action_id, event_type, author_id, author_role, body, service_id, marker_id, marker_seconds)
    SELECT m.action_id, 'marker_removed', p_caller, 'admin', NULLIF(m.note, ''), m.service_id, m.marker_id,
           m.offset_seconds
      FROM tbl_coaching_marker AS m
     WHERE m.action_id = p_action_id AND m.service_id = v_follow_up AND m.removed_at IS NULL
     ORDER BY m.offset_seconds, m.marker_id;
    UPDATE tbl_coaching_marker
       SET removed_by = p_caller, removed_at = NOW(3)
     WHERE action_id = p_action_id AND service_id = v_follow_up AND removed_at IS NULL;
  END IF;
END body $$

-- -----------------------------------------------------------------------------
-- sp_coaching_complete(caller, action_id, expected_version, conclusion)：結案為完成（管理員）。
--   要有後續服務（coaching:no_follow_up）和結論（coaching:invalid）。員工的確認、AI 分數都不會讓行動完成：
--   只有這支程序把狀態改成 completed。version +1。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_complete $$
CREATE PROCEDURE sp_coaching_complete(
  IN p_caller           VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_conclusion       VARCHAR(2000) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：結案為完成（管理員）：要有後續服務和結論'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.version, a.follow_up_service_id INTO v_status, v_version, v_follow_up
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF v_follow_up IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:no_follow_up';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_conclusion, ''))) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  UPDATE tbl_coaching_action
     SET action_status = 'completed', conclusion = TRIM(p_conclusion), concluded_by = p_caller,
         concluded_at = NOW(3), version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open'
     AND follow_up_service_id IS NOT NULL;
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body, service_id)
  VALUES (p_action_id, 'completed', p_caller, 'admin', TRIM(p_conclusion), v_follow_up);
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_adjust(caller, action_id, expected_version, reason, goal, due_date)：調整（管理員，進行中）。
--   換成新的目標、期限，原因必填；舊的值和新的值都寫進 adjusted 歷史（goal_before／after、due_before／after），
--   那一次看過的後續服務也記在歷史（service_id），並從行動上拿掉：下一次要選更晚的一筆。
--   員工的確認清掉（要再確認新的目標）。version +1。沒有後續服務也可以調整（例如改期限）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_adjust $$
CREATE PROCEDURE sp_coaching_adjust(
  IN p_caller           VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_reason           VARCHAR(2000) CHARACTER SET utf8mb4,
  IN p_goal             VARCHAR(300)  CHARACTER SET utf8mb4,
  IN p_due_date         DATE
)
  MODIFIES SQL DATA
  COMMENT 'W2：調整目標、期限（管理員）：原因必填，舊的值留在歷史；看過的後續服務拿掉、員工要再確認'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_goal VARCHAR(300) DEFAULT NULL;
  DECLARE v_due DATE DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.version, a.goal, a.due_date, a.follow_up_service_id
    INTO v_status, v_version, v_goal, v_due, v_follow_up
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_reason, ''))) = 0 OR CHAR_LENGTH(TRIM(COALESCE(p_goal, ''))) = 0
     OR p_due_date IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  UPDATE tbl_coaching_action
     SET goal = TRIM(p_goal), due_date = p_due_date, follow_up_service_id = NULL, acknowledged_at = NULL,
         version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open';
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event
    (action_id, event_type, author_id, author_role, body, goal_before, goal_after, due_before, due_after, service_id)
  VALUES (p_action_id, 'adjusted', p_caller, 'admin', TRIM(p_reason), v_goal, TRIM(p_goal), v_due, p_due_date,
          v_follow_up);
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_cancel(caller, action_id, expected_version, reason)：撤回（管理員，進行中）：建錯了、不再適用等。
--   原因必填（寫在 conclusion）；行動和歷史都留著，狀態是 cancelled。version +1。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_cancel $$
CREATE PROCEDURE sp_coaching_cancel(
  IN p_caller           VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_reason           VARCHAR(2000) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：撤回（管理員）：原因必填；行動和歷史都留著'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.version INTO v_status, v_version
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_reason, ''))) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  UPDATE tbl_coaching_action
     SET action_status = 'cancelled', conclusion = TRIM(p_reason), concluded_by = p_caller, concluded_at = NOW(3),
         version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open';
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body)
  VALUES (p_action_id, 'cancelled', p_caller, 'admin', TRIM(p_reason));
END $$

DELIMITER ;
-- [END PLAN1 W2]
-- #############################################################################

-- #############################################################################
-- [BEGIN SQS] 分析工作佇列（2026-11-02，T49，docs/rebuild/sqs/PLAN.md）：3 張表，只有分析服務讀寫，桌面端不讀。
--   已經建好的資料庫見 migrations/007_analysis_queue.sql（可以重跑，不 DROP）。GRANT 在最後選用區塊的 [SQS] 那幾行。
--   時間欄位（*_at、lease_until）一律是 UTC：分析服務用 UTC_TIMESTAMP(3) 寫入、比較，不受 time_zone 與夏令時間影響。
-- #############################################################################
-- 12. tbl_analysis_job：分析工作：一筆服務一列（這一版不支援已完成服務的 force 重跑，PLAN 5-1）
CREATE TABLE IF NOT EXISTS tbl_analysis_job (
  service_id      VARCHAR(32)      NOT NULL,
  -- queued：等 worker 領；running：有 worker 拿著 lease 在算；done：結果已提交（服務狀態 3）；
  -- failed：這一輪的嘗試用完、或管理員判定失敗（服務狀態回 1，桌面端的 Retry 會開新的一輪）
  job_state       VARCHAR(16)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'queued',
  -- 這一輪已經認領幾次（每次認領 +1；收到 SIGTERM 正常交還時不算）。到 max_attempts 還失敗就是 failed。
  -- 這是同一筆服務所有 SQS 訊息合計的次數；SQS 的 maxReceiveCount 只管單一訊息（PLAN 第 8 節）
  attempts        TINYINT UNSIGNED NOT NULL DEFAULT 0,
  max_attempts    TINYINT UNSIGNED NOT NULL DEFAULT 3,
  -- 認領時產生的 uuid4（32 個十六進位字）。只有 lease 還沒到期、token 相同的 worker 能續期、提交；
  -- 輸出檔名也帶它（PLAN 6-4），舊 worker 晚到的上傳不會蓋掉接手者引用的檔案
  lease_token     CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NULL,
  lease_until     DATETIME(3)      NULL,
  last_error      VARCHAR(1000)    NULL,
  -- 提交成功的那一次：它的 token（也是服務結果 W1 的 run 編號）和 report-data 的 S3 key（報表從這裡讀）
  result_token    CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NULL,
  report_data_key VARCHAR(255)     NULL,
  accepted_at     DATETIME(3)      NOT NULL,   -- API 最後一次接受（新建或重開這一輪）
  started_at      DATETIME(3)      NULL,       -- 最後一次認領
  finished_at     DATETIME(3)      NULL,       -- done 或 failed 的時間
  updated_at      DATETIME(3)      NOT NULL,

  PRIMARY KEY (service_id),
  -- 找卡住的工作（queued／running 很久沒有動靜）
  KEY idx_analysis_job_state_updated (job_state, updated_at),
  CONSTRAINT fk_analysis_job_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_analysis_job_state CHECK (job_state IN ('queued', 'running', 'done', 'failed')),
  -- 只有 running 有 lease；token 和到期時間一起有、一起沒有
  CONSTRAINT chk_analysis_job_lease CHECK ((lease_token IS NULL) = (lease_until IS NULL)
                                           AND (job_state = 'running' OR lease_token IS NULL)),
  CONSTRAINT chk_analysis_job_attempts CHECK (attempts <= max_attempts)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析工作（SQS）：認領、續期、嘗試次數；時間都是 UTC';

-- 13. tbl_analysis_outbox：和工作同一個交易寫入，送出器送到 SQS 之後標成 sent（PLAN 5-2）
CREATE TABLE IF NOT EXISTS tbl_analysis_outbox (
  event_id        CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- uuid4，也放進 SQS 訊息
  service_id      VARCHAR(32)      NOT NULL,
  outbox_state    VARCHAR(8)       CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending',
  send_attempts   INT UNSIGNED     NOT NULL DEFAULT 0,
  -- 下一次可以送的時間：送失敗時往後退避；送出器拿走一筆時也先往後推，兩個 process 不會同時送同一筆
  next_attempt_at DATETIME(3)      NOT NULL,
  last_error      VARCHAR(1000)    NULL,
  sqs_message_id  VARCHAR(128)     NULL,
  created_at      DATETIME(3)      NOT NULL,
  sent_at         DATETIME(3)      NULL,

  PRIMARY KEY (event_id),
  KEY idx_analysis_outbox_due (outbox_state, next_attempt_at),
  KEY idx_analysis_outbox_service (service_id),
  CONSTRAINT fk_analysis_outbox_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_analysis_outbox_state CHECK (outbox_state IN ('pending', 'sent')),
  CONSTRAINT chk_analysis_outbox_sent CHECK ((outbox_state = 'sent') = (sent_at IS NOT NULL))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '還要送到 SQS 的分析工作（transactional outbox）；時間都是 UTC';

-- 14. tbl_analysis_worker：每個 worker process 一列，/healthz 用 seen_at 判斷還活著沒有
CREATE TABLE IF NOT EXISTS tbl_analysis_worker (
  worker_id       VARCHAR(64)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- 主機名稱:pid
  -- loading：載入模型中；ready：等工作；busy：正在做 service_id；failed：模型載入失敗；stopped：正常結束
  worker_state    VARCHAR(16)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  service_id      VARCHAR(32)      NULL,       -- busy 時是哪一筆（不設外鍵：服務刪了也不影響）
  detail          VARCHAR(255)     NULL,       -- 例如 Whisper 的大小、載入失敗的原因
  started_at      DATETIME(3)      NOT NULL,
  seen_at         DATETIME(3)      NOT NULL,

  PRIMARY KEY (worker_id),
  KEY idx_analysis_worker_seen (seen_at),
  CONSTRAINT chk_analysis_worker_state CHECK (worker_state IN ('loading', 'ready', 'busy', 'failed', 'stopped'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析 worker 的心跳；時間都是 UTC';
-- [END SQS]


-- =============================================================================
-- 種子資料：沒有。原本這裡有兩個佔位帳號（placeholder_admin、placeholder_staff），它們的密碼在公開的測試裡，
-- 所以搬進 git 時拿掉了（桌面端測試用的 myapp/tests/fixtures/schema.sql 還有，測試需要）。
-- 新建的資料庫用 README「新建資料庫」的步驟建第一個管理員（等級 '5'：ah:300 會擋下建立比自己等級高的帳號，
-- 等級 5 才能在桌面端的 Add Account 建立 1～5 的所有帳號）。
-- =============================================================================


-- =============================================================================
-- [BEGIN OPTIONAL APP USERS]（選用，預設不執行）
-- 建議用戶端不要使用 RDS 主帳號，改用下面的最小權限帳號。
-- 用法：取消註解，把 <…> 換成強密碼後再執行。
-- 下列權限已用 repo 中所有用戶端程式路徑實測過（見 db_report.md 的「驗證紀錄」）。
-- REQUIRE SSL：這個帳號只能經由 TLS 連線。mysql-connector-python 8.0.26 在伺服器支援時會自動使用 TLS。
--
-- 登入改用 sp_get_login，它會回傳密碼雜湊，由用戶端比對；預存程序以定義者權限執行。
-- tbl_account 的欄位層級 SELECT 只擋得住直接讀表；拿到 adam_app 帳密，仍可透過 sp_get_login 讀到雜湊。
-- 但這個帳號仍然可以 UPDATE tbl_account（AccountManager 需要），限制見 db_report.md 第 d 節。
--
-- CREATE USER 'adam_app'@'%' IDENTIFIED BY '<APP_PASSWORD>' REQUIRE SSL;
-- GRANT SELECT ON adam.tbl_service TO 'adam_app'@'%';
-- GRANT SELECT ON adam.tbl_service_score TO 'adam_app'@'%';
-- GRANT SELECT ON adam.tbl_video_storage TO 'adam_app'@'%';
-- GRANT SELECT ON adam.tbl_audio_storage TO 'adam_app'@'%';
-- GRANT SELECT ON adam.tbl_charts_storage TO 'adam_app'@'%';
-- GRANT SELECT (account_id, account_username, account_name, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_app'@'%';
-- GRANT INSERT, UPDATE ON adam.tbl_account TO 'adam_app'@'%';
-- GRANT UPDATE (service_status) ON adam.tbl_service TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_get_login TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_get_account_name TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_service TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_video_storage TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_audio_storage TO 'adam_app'@'%';
-- new-feature N4（2026-11-01）：管理員新增報表、發佈、把逾時的 generating 改成 failed；清單一律經由 sp_list_reports
-- GRANT SELECT, INSERT ON adam.tbl_report TO 'adam_app'@'%';
-- GRANT UPDATE (report_status, failure_reason, published_at, published_by) ON adam.tbl_report TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_list_reports TO 'adam_app'@'%';
-- new-feature N3、S6（2026-11-01，T36）：照片換 account_photo_key 用上面整表的 UPDATE（WHERE 讀 account_photo_key，
--   在欄位層級的 SELECT 裡）；改名也是整表的 UPDATE。改登入帳號、密碼要先驗證目前的密碼，經由這三個程序
-- GRANT EXECUTE ON PROCEDURE adam.sp_get_account_password TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_change_account_password TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_change_account_username TO 'adam_app'@'%';
-- new-feature N4（2026-11-01，migration 003）：分析頁的報表狀態（只有狀態，不含未發佈的報表）
-- GRANT EXECUTE ON PROCEDURE adam.sp_list_service_report_states TO 'adam_app'@'%';
--
-- 分析服務的帳號 adam_analysis：第 0 階段假服務（analysis_service/app.py）和移植後的原分析服務
-- （steps/06_analyzer.md）共用。每一行最後標明誰需要它：
--   [假服務]  只有假服務需要。換成移植版之後可以用 REVOKE 收回。
--   [移植版]  移植後的原分析服務需要。
--   [兩者]    兩邊都需要。
-- 假服務直接 DELETE＋INSERT 分數與圖表，所以要那兩張表的寫入權限。移植版只透過 3 個預存程序寫入；
-- 程序以建立者（執行本檔的帳號）的權限執行，所以它只需要 EXECUTE，不需要分數、圖表、逐字稿資料表的權限。
-- 移植版的 UPDATE ... WHERE service_id = %s AND service_status IN (1,2) 會讀這兩欄，所以要它們的 SELECT。
-- CREATE USER 'adam_analysis'@'%' IDENTIFIED BY '<ANALYSIS_PASSWORD>' REQUIRE SSL;
-- GRANT SELECT ON adam.tbl_service TO 'adam_analysis'@'%';                                  -- [假服務] 它也只讀 service_id、service_status
-- GRANT SELECT (service_id, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';     -- [移植版]
-- GRANT UPDATE (service_status) ON adam.tbl_service TO 'adam_analysis'@'%';                 -- [兩者]
-- GRANT SELECT ON adam.tbl_video_storage TO 'adam_analysis'@'%';                            -- [兩者]
-- GRANT SELECT ON adam.tbl_audio_storage TO 'adam_analysis'@'%';                            -- [兩者]
-- GRANT SELECT (account_id, account_name) ON adam.tbl_account TO 'adam_analysis'@'%';       -- [假服務] 目前沒有程式用到，可刪
-- GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_service_score TO 'adam_analysis'@'%';    -- [假服務]
-- GRANT SELECT, INSERT, DELETE ON adam.tbl_charts_storage TO 'adam_analysis'@'%';           -- [假服務]
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_service_score TO 'adam_analysis'@'%';           -- [移植版]
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_charts_storage TO 'adam_analysis'@'%';          -- [移植版]
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_text_storage TO 'adam_analysis'@'%';            -- [兩者] 假服務從 N6 起也寫假的逐字稿
-- new-feature N4、N5（2026-11-01）：/reports 讀報表要的資料，產生後把狀態走到 ready 或 failed（兩者都需要）。
--   分數、服務、帳號的讀取權限比上面的多，因為報表要用：服務的起訖時間、上一筆服務、姓名、職稱、照片
-- GRANT SELECT ON adam.tbl_report TO 'adam_analysis'@'%';                                   -- [兩者]
-- GRANT UPDATE (report_status, failure_reason, ai_model, ai_prompt_version) ON adam.tbl_report TO 'adam_analysis'@'%';  -- [兩者]
-- GRANT SELECT (service_id, account_id, service_start, service_end, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';  -- [移植版]
-- GRANT SELECT ON adam.tbl_service_score TO 'adam_analysis'@'%';                            -- [移植版]
-- GRANT SELECT (account_id, account_name, account_username, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_analysis'@'%';  -- [兩者] account_username：同名的人分得出來（PLAN 13.7，N6 的 004）
--   逐字稿的位置交給 N6 的 explain()（內容由 explain() 從 S3 的 text/ 讀；假服務從 N6 起寫一份假的逐字稿）
-- GRANT SELECT (service_id, text_name) ON adam.tbl_text_storage TO 'adam_analysis'@'%';     -- [兩者]
-- PLAN1 W1（2026-11-01，T44，migration 005）：寫分數時清掉上一次的服務結果、換成這次的編號；背景工作寫回結果與待辦，
--   WHERE service_outcome_run = 那一次的編號（所以要讀這一欄）。桌面端 adam_app 用上面整張表的 SELECT 讀，不用另外給。
--   沒有這兩行時分析照樣完成，只是服務結果一直是 NULL（分析服務的 log 說要套用 005 和它的 GRANT）。
-- GRANT UPDATE (service_outcome, service_todos, service_outcome_model, service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';  -- [兩者]
-- GRANT SELECT (service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';           -- [移植版] 假服務有整張表的 SELECT
-- [BEGIN PLAN1 W2 GRANTS]（2026-11-01，migration 006，T45）：員工輔導。adam_app 只要 13 個程序的 EXECUTE，
--   三張 tbl_coaching_* 不給任何權限（程序以建立者的權限執行）；adam_analysis 不需要
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_list TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_detail TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_for_service TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_follow_up_options TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_create TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_add_marker TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_remove_marker TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_respond TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_acknowledge TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_link_follow_up TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_complete TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_adjust TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_cancel TO 'adam_app'@'%';
-- [END PLAN1 W2 GRANTS]
-- [SQS]（2026-11-02，migration 007，T49）：分析工作佇列。API 接受工作、送出器、worker、管理指令都用 adam_analysis；
--   outbox、worker 要 DELETE（清掉七天前已送出的紀錄、很久沒出現的 worker）。adam_app 不需要
-- GRANT SELECT, INSERT, UPDATE ON adam.tbl_analysis_job TO 'adam_analysis'@'%';                 -- [兩者]
-- GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_outbox TO 'adam_analysis'@'%';      -- [兩者]
-- GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_worker TO 'adam_analysis'@'%';      -- [兩者]
-- [END OPTIONAL APP USERS]
-- =============================================================================
