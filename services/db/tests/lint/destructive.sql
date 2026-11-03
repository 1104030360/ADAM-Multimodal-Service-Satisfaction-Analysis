-- 測試專用（tests/run.sh 的 lint）：下面每一句都是破壞性的
DROP TABLE tbl_x;
DROP INDEX idx_a ON tbl_x;
DROP VIEW v_x;
TRUNCATE TABLE tbl_x;
RENAME TABLE tbl_a TO tbl_b;
ALTER TABLE tbl_x DROP COLUMN c1;
ALTER TABLE tbl_x DROP c2;
ALTER TABLE tbl_x MODIFY COLUMN c3 VARCHAR(10);
ALTER TABLE tbl_x CHANGE COLUMN c4 c5 INT;
ALTER TABLE tbl_x RENAME COLUMN c6 TO c7;
DELETE FROM tbl_x;
UPDATE tbl_x SET a = 1;
REVOKE SELECT ON adam.tbl_x FROM 'adam_app'@'%';
REPLACE INTO tbl_x (a) VALUES (1);
/*!80000 DROP TABLE tbl_hidden */;
DROP PROCEDURE IF EXISTS sp_removed;
SET @s := 'DROP TABLE tbl_dynamic';
DELIMITER $$
DROP PROCEDURE IF EXISTS sp_regrant_missing $$
CREATE PROCEDURE sp_regrant_missing() BEGIN SELECT 1; END $$
CREATE PROCEDURE sp_body() BEGIN DELETE FROM tbl_x; END $$
DELIMITER ;
