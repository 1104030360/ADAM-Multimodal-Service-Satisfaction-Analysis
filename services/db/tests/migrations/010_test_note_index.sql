-- =============================================================================
-- 測試專用：先查 information_schema 再決定要不要 ALTER（MySQL 8.4 沒有 ADD INDEX IF NOT EXISTS），可以重跑。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

SET @step := IF((SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
                   AND TABLE_NAME = 'tbl_test_note' AND INDEX_NAME = 'idx_test_note_created') = 0,
  'ALTER TABLE tbl_test_note ADD INDEX idx_test_note_created (created_at)',
  'DO 0');
PREPARE step FROM @step;
EXECUTE step;
DEALLOCATE PREPARE step;
