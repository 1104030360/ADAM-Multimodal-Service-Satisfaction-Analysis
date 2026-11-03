-- =============================================================================
-- new-feature 005：服務結果與待辦（PLAN1 W1，Ollama Cloud 讀說話者分離的逐字稿來填）。2026-11-01，T44
-- 對象：已經用 docs/rebuild/db_schema.sql 建好、而且照編號套用過 001～004 的資料庫（RDS、本機的 adam-mysql）。
--       新建的資料庫直接跑 db_schema.sql 就有這些，不用再跑這個檔。
-- 執行：用 adam_admin（或 RDS 主帳號）在 004 之後執行。可以重跑：每一個欄位和 CHECK 都先查 information_schema，
--       已經有的就跳過（MySQL 8.4 沒有 ADD COLUMN IF NOT EXISTS，所以用 PREPARE 依查詢結果決定要不要 ALTER）。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 005_service_outcome.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 005_service_outcome.sql
--   apply_001_004.sh 的做法也可以：這個檔可以重跑，不用像 001 那樣先判斷跑過沒有。
-- 內容：
--   1. tbl_service 加四個可為 NULL 的欄位（舊的服務都是 NULL＝還沒填），和 db_schema.sql 第 2 節相同：
--      service_outcome（五個值之一）、service_todos（一項一行）、service_outcome_model（用的模型）、
--      service_outcome_run（哪一次分析清的；只有分析服務讀寫，晚到的舊結果靠它擋掉）。
--   2. chk_service_outcome：NULL 或 Resolved、Documents missing、Handoff needed、Unresolved、Unknown。
--      service_outcome 用 utf8mb4_0900_bin，CHECK 才會擋下 'resolved' 這種大小寫不同的值。
--   不改程序（sp_insert_service 不用改），不 DROP 任何東西，所以不會拿掉其他帳號的 EXECUTE（T42 第 9 節）。
--   3. 最後一段的 GRANT 是註解：有建立 adam_analysis 最小權限帳號的才需要（db_schema.sql 的選用區塊），
--      把註解拿掉再執行。沒有這個帳號（例如本機用 root 連）的不要執行：MySQL 8 不能 GRANT 給不存在的帳號（1410）。
--      桌面端的 adam_app 已經有整張 tbl_service 的 SELECT（新欄位也讀得到），桌面端不寫這幾欄，不用另外給。
-- 順序：先套用這個檔（和 GRANT），再換新版的分析服務和桌面端。
--   - 分析服務：還沒套用時照樣分析、寫分數，只是不填服務結果，log 寫「apply migration 005 and its GRANT」。
--   - 桌面端：服務明細的查詢會讀這幾欄，還沒套用時明細頁打不開（Unknown column 's.service_outcome'，1054）。
-- S3：分析服務的 EC2 role 要能讀 text/*（GetObject）；N6 的 004 已經要了，這裡沒有新的。這個檔案不改 IAM。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

-- 1. 四個欄位（各自判斷，只做了一半的也補得齊）
SET @step := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_service' AND COLUMN_NAME = 'service_outcome') = 0,
  'ALTER TABLE tbl_service ADD COLUMN service_outcome VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NULL AFTER service_status',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;

SET @step := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_service' AND COLUMN_NAME = 'service_todos') = 0,
  'ALTER TABLE tbl_service ADD COLUMN service_todos TEXT NULL AFTER service_outcome',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;

SET @step := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_service' AND COLUMN_NAME = 'service_outcome_model') = 0,
  'ALTER TABLE tbl_service ADD COLUMN service_outcome_model VARCHAR(100) NULL AFTER service_todos',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;

SET @step := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_service' AND COLUMN_NAME = 'service_outcome_run') = 0,
  'ALTER TABLE tbl_service ADD COLUMN service_outcome_run CHAR(32) NULL AFTER service_outcome_model',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;

-- 2. CHECK（舊的服務都是 NULL，加上去不會失敗）
SET @step := IF((SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_service' AND CONSTRAINT_NAME = 'chk_service_outcome') = 0,
  'ALTER TABLE tbl_service ADD CONSTRAINT chk_service_outcome CHECK (service_outcome IS NULL OR service_outcome IN
     (''Resolved'', ''Documents missing'', ''Handoff needed'', ''Unresolved'', ''Unknown''))',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;


-- =============================================================================
-- 最小權限帳號（選用；有建立 adam_analysis 才執行，把註解拿掉）。說明見 db_schema.sql 的選用區塊
--   第一行：分析寫分數時清掉上一次的結果（四欄），背景工作寫回結果、待辦、模型（三欄）。
--   第二行：寫回時 WHERE service_outcome_run = 那一次的編號（移植版只有幾個欄位的 SELECT；假服務有整張表的，給了也沒關係）。
-- GRANT 是累加的，重跑沒關係。
-- =============================================================================
-- GRANT UPDATE (service_outcome, service_todos, service_outcome_model, service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';
-- GRANT SELECT (service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';
