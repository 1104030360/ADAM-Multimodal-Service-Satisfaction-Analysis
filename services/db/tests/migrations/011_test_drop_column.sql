-- =============================================================================
-- 測試專用：破壞性的 migration（刪欄位）。ALTER TABLE 寫在字串裡（動態 SQL）也要被抓到；要 --allow-destructive 才跑。
-- -- migrate: destructive 刻意不標（tests/run.sh 另外複製一份加上標記）。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

SET @step := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_test_note' AND COLUMN_NAME = 'note_extra') = 1,
  'ALTER TABLE tbl_test_note DROP COLUMN note_extra',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;
