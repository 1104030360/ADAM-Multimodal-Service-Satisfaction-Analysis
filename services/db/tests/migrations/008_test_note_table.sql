-- =============================================================================
-- 測試專用（services/db/tests/run.sh、verify.sh 的測試），不會進 migrations/。
-- 加一張表、給 adam_app 權限：只加東西、可以重跑。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

CREATE TABLE IF NOT EXISTS tbl_test_note (
  note_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  service_id VARCHAR(32)     NOT NULL,
  note       VARCHAR(300)    NOT NULL DEFAULT '',
  note_extra VARCHAR(100)    NULL,
  created_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (note_id),
  KEY idx_test_note_service (service_id),
  CONSTRAINT fk_test_note_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_test_note CHECK (CHAR_LENGTH(note) <= 300)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '測試專用';

GRANT SELECT, INSERT ON adam.tbl_test_note TO 'adam_app'@'%';
