-- =============================================================================
-- new-feature 002：自己改登入帳號、密碼（S6）。2026-11-01，T36
-- 對象：已經用 docs/rebuild/db_schema.sql 建好、而且跑過 001 的資料庫（RDS、本機的 adam-mysql）。
--       新建的資料庫直接跑 db_schema.sql 就有這些，不用再跑這個檔。
-- 執行：用 adam_admin（或 RDS 主帳號）執行。只有 DROP/CREATE PROCEDURE，可以重跑。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 002_account_self_service.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 002_account_self_service.sql
-- 內容：3 個預存程序。不改資料表：改名、換照片用 adam_app 已經有的整表 UPDATE（001 已經把 account_photo_key
--       加進欄位層級的 SELECT）。
-- 程序以建立者的權限執行（不寫 DEFINER，RDS 不需要 SUPER）：執行這個檔的帳號要能讀寫 tbl_account。
-- 最後一段的 GRANT 是註解：有建立 adam_app 最小權限帳號的才需要（db_schema.sql 的選用區塊）。
-- S3：桌面端的 AWS profile 要能讀寫 avatars/*（PutObject、GetObject、DeleteObject，N3；001 的說明）。這個檔案不改 IAM。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

DELIMITER $$

-- -----------------------------------------------------------------------------
-- 改登入帳號、密碼都要先驗證目前的密碼（PLAN 13.7）：
--   1. sp_get_account_password(account_id)：取回目前的密碼雜湊，由 Python 的 passwords.verify_password() 比對。
--   2. sp_change_account_password／sp_change_account_username：密碼雜湊「還是剛才驗證過的那一個」才寫入
--      （compare-and-swap），回傳 1 列 (changed)；0 表示這段期間密碼在別處改過，桌面端請使用者再試一次。
-- adam_app 沒有 account_password 的 SELECT，不能自己寫 WHERE account_password = …，所以用程序。
-- 參數的 COLLATE utf8mb4_0900_bin 和 account_password 欄位相同，避免 1267（Illegal mix of collations）。
-- 帳號重複時 UPDATE 撞到 uk_account_username，丟 1062 給呼叫端。
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

DELIMITER ;


-- =============================================================================
-- 最小權限帳號（選用；有建立 adam_app 才執行，把註解拿掉）。說明見 db_schema.sql 的選用區塊
-- =============================================================================
-- GRANT EXECUTE ON PROCEDURE adam.sp_get_account_password TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_change_account_password TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_change_account_username TO 'adam_app'@'%';
