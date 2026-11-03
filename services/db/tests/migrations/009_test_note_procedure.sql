-- =============================================================================
-- 測試專用：有 DELIMITER 的檔案。換掉程序的內容：同一個檔案裡 DROP＋CREATE＋重新 GRANT（不算破壞性）。
-- 註解、字串裡的 DROP TABLE tbl_service; 不算。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

DELIMITER $$

DROP PROCEDURE IF EXISTS sp_test_note_count $$
CREATE PROCEDURE sp_test_note_count(
  IN p_service_id VARCHAR(32) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'test only; -- not a comment; DROP TABLE tbl_service'
BEGIN
  -- DROP TABLE tbl_service; DELETE FROM tbl_account;
  /* TRUNCATE tbl_service; */
  SELECT COUNT(*) AS notes, 'a;b' AS semicolon_in_string
    FROM tbl_test_note
   WHERE service_id = p_service_id;
END $$

DELIMITER ;

GRANT EXECUTE ON PROCEDURE adam.sp_test_note_count TO 'adam_app'@'%';
