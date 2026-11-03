-- 測試專用：加一個欄位（可以重跑）。
SET NAMES utf8mb4;
USE adam;

SET @step := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_test_note' AND COLUMN_NAME = 'note_author') = 0,
  'ALTER TABLE tbl_test_note ADD COLUMN note_author VARCHAR(36) NULL AFTER note',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;
