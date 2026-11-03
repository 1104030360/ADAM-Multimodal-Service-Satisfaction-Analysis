-- =============================================================================
-- new-feature 004：報表的 AI 說明（N6，Ollama Cloud）與同名員工（PLAN 13.7）。2026-11-01，T42
-- 對象：已經用 docs/rebuild/db_schema.sql 建好、而且照編號套用過 001～003 的資料庫（RDS、本機的 adam-mysql）。
--       新建的資料庫直接跑 db_schema.sql 就有這些，不用再跑這個檔。
-- 執行：用 adam_admin（或 RDS 主帳號）在 003 之後執行一次。可以重跑：這個檔案不刪、不改任何東西。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 004_ai_transcripts.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 004_ai_transcripts.sql
-- 內容：
--   1. 逐字稿的資料表 tbl_text_storage 和程序 sp_insert_text_storage：db_schema.sql 本來就有（第 7 節與程序區塊），
--      這裡只在「沒有」時建立（IF NOT EXISTS），已經有的不碰。程序不用 DROP/CREATE：DROP PROCEDURE 會連帶拿掉
--      別的帳號在這個程序上的 EXECUTE。CREATE PROCEDURE IF NOT EXISTS 要 MySQL 8.0.29 以上（RDS 用 8.4）。
--      已經有的時候 mysql 會顯示 Note 1050／1304（already exists），不是錯誤。
--   2. 最後一段的 GRANT 是註解：有建立 adam_analysis 最小權限帳號的才需要（db_schema.sql 的選用區塊），
--      把註解拿掉再執行。沒有這個帳號（例如本機用 root 連）的不要執行：MySQL 8 不能 GRANT 給不存在的帳號（1410）。
--      - sp_insert_text_storage 的 EXECUTE：假服務（第 0 階段）從 N6 起也寫一份假的逐字稿，讓報表的 AI 說明走得完；
--        移植版本來就要（選用區塊原本標 [移植版]，改成 [兩者]）。
--      - tbl_text_storage 兩個欄位的 SELECT：報表找逐字稿的位置（T38 第 9 節已經列過；001 沒有，這裡補上）。
--      - tbl_account.account_username 的 SELECT：員工、全公司、每次服務報表上，同名的人寫「名字 (登入帳號)」。
--      GRANT 是累加的：欄位層級的 SELECT 只會多一欄，原本的欄位不受影響；重跑也沒關係。
-- 不改資料表的欄位，不改 tbl_report（ai_model、ai_prompt_version 在 001 就有了）。
-- S3：分析服務的 EC2 role 要能讀 text/*（GetObject，報表讀逐字稿）；假服務還要寫 text/*（PutObject）。
--     這個檔案不改 IAM。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

-- tbl_text_storage：和 db_schema.sql 第 7 節相同
CREATE TABLE IF NOT EXISTS tbl_text_storage (
  text_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  service_id       VARCHAR(32)     NOT NULL,
  text_name        VARCHAR(255)    NOT NULL,
  text_url         VARCHAR(1024)   NOT NULL,
  text_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (text_id),
  UNIQUE KEY uk_text_service_name (service_id, text_name),
  CONSTRAINT fk_text_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '逐字稿位置（text/{service_id}_transcript.txt）；桌面端不讀';


DELIMITER $$

-- sp_insert_text_storage(service_id, 檔名, url)：和 db_schema.sql 的程序區塊相同（新增或更新逐字稿的位置）
CREATE PROCEDURE IF NOT EXISTS sp_insert_text_storage(
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

DELIMITER ;


-- =============================================================================
-- 最小權限帳號（選用；有建立 adam_analysis 才執行，把註解拿掉）。說明見 db_schema.sql 的選用區塊
-- =============================================================================
-- GRANT EXECUTE ON PROCEDURE adam.sp_insert_text_storage TO 'adam_analysis'@'%';
-- GRANT SELECT (service_id, text_name) ON adam.tbl_text_storage TO 'adam_analysis'@'%';
-- GRANT SELECT (account_username) ON adam.tbl_account TO 'adam_analysis'@'%';
