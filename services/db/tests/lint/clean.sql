-- 測試專用（tests/run.sh 的 lint）：沒有破壞性的語句。註解、字串、ON DELETE、函式 REPLACE() 等都不算。
-- DROP TABLE tbl_service; TRUNCATE tbl_service;
/* DELETE FROM tbl_account; */
# UPDATE tbl_account SET account_level = '5';
CREATE TABLE IF NOT EXISTS tbl_clean (
  id         INT      NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  parent     INT      NULL,
  PRIMARY KEY (id),
  CONSTRAINT fk_clean FOREIGN KEY (parent) REFERENCES tbl_clean (id) ON DELETE CASCADE ON UPDATE RESTRICT
) COMMENT = 'not destructive: DROP TABLE in a string; TRUNCATE';
INSERT INTO tbl_clean (id) VALUES (1) ON DUPLICATE KEY UPDATE parent = NULL;
UPDATE tbl_clean SET parent = NULL WHERE id = 1;
DELETE FROM tbl_clean WHERE id = 2;
SELECT REPLACE('a', 'b', 'c'), TRUNCATE(1.5, 0) FROM tbl_clean WHERE id = 1 FOR UPDATE;
GRANT SELECT, UPDATE, DELETE, DROP ON adam.tbl_clean TO 'adam_app'@'%';
DROP TEMPORARY TABLE IF EXISTS tmp_x;
DEALLOCATE PREPARE step;
DROP PREPARE step2;
CREATE OR REPLACE VIEW v_clean AS SELECT id FROM tbl_clean;
INSERT INTO tbl_clean (id) SELECT 3 FROM DUAL WHERE 'it''s -- not a comment; DROP TABLE x' <> '';
SELECT "double \" quoted; DROP TABLE x" AS s;
SET @step := IF(1 = 1, 'ALTER TABLE tbl_clean ADD COLUMN c INT NULL AFTER parent', 'DO 0');
DELIMITER //
DROP PROCEDURE IF EXISTS sp_helper //
CREATE PROCEDURE sp_helper() BEGIN UPDATE tbl_clean SET parent = NULL WHERE id = 1; END //
CALL sp_helper() //
DROP PROCEDURE sp_helper //
DROP PROCEDURE IF EXISTS sp_kept //
CREATE PROCEDURE sp_kept() BEGIN SELECT 1; END //
DELIMITER ;
GRANT EXECUTE ON PROCEDURE adam.sp_kept TO 'adam_app'@'%';
