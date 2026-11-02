-- Test-only MySQL 8.4 schema: no production credentials or user grants.
-- Snapshot of the rebuild schema including migrations 001-007 (2026-11-02).
-- Synthetic placeholder accounts are required by tests/integration/.
-- Update this fixture when changing tables or stored procedures.

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS adam
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE adam;

CREATE TABLE IF NOT EXISTS tbl_account (
  account_id              VARCHAR(36)  NOT NULL,

  account_username        VARCHAR(64)  NOT NULL,

  account_password        VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,

  account_name            VARCHAR(100) NOT NULL,

  account_level           CHAR(1)      NOT NULL DEFAULT '1',

  account_occupation_name VARCHAR(100) NULL,

  account_photo_key       VARCHAR(255) NULL,

  PRIMARY KEY (account_id),
  UNIQUE KEY uk_account_username (account_username),
  KEY idx_account_name (account_name),
  CONSTRAINT chk_account_level CHECK (account_level IN ('1', '2', '3', '4', '5'))  -- 推測
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '帳號。account_level 1=服務人員，其他=管理端（login.qml:142）';

CREATE TABLE IF NOT EXISTS tbl_service (
  service_id      VARCHAR(32)      NOT NULL,

  account_id      VARCHAR(36)      NOT NULL,

  service_start   DATETIME         NOT NULL,

  service_end     DATETIME         NULL,

  service_status  TINYINT UNSIGNED NOT NULL DEFAULT 1,

  service_outcome       VARCHAR(32)  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NULL,
  service_todos         TEXT         NULL,
  service_outcome_model VARCHAR(100) NULL,
  service_outcome_run   CHAR(32)     NULL,

  PRIMARY KEY (service_id),
  KEY idx_service_account_status_start (account_id, service_status, service_start),
  KEY idx_service_status_start (service_status, service_start),
  KEY idx_service_start (service_start),
  CONSTRAINT fk_service_account FOREIGN KEY (account_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_service_status CHECK (service_status IN (1, 2, 3)),
  CONSTRAINT chk_service_outcome CHECK (service_outcome IS NULL OR service_outcome IN
    ('Resolved', 'Documents missing', 'Handoff needed', 'Unresolved', 'Unknown'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '服務（一次錄影）。service_status 1=已上傳 2=已送分析 3=分析完成';

CREATE TABLE IF NOT EXISTS tbl_service_score (
  service_id       VARCHAR(32) NOT NULL,

  customer_total   DOUBLE      NOT NULL DEFAULT -1,
  server_total     DOUBLE      NOT NULL DEFAULT -1,
  customer_text    DOUBLE      NOT NULL DEFAULT -1,
  server_text      DOUBLE      NOT NULL DEFAULT -1,
  customer_audio   DOUBLE      NOT NULL DEFAULT -1,
  server_audio     DOUBLE      NOT NULL DEFAULT -1,
  customer_facial  DOUBLE      NOT NULL DEFAULT -1,
  server_facial    DOUBLE      NOT NULL DEFAULT -1,

  score_created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (service_id),
  CONSTRAINT fk_score_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_score_customer_total  CHECK (customer_total  = -1 OR customer_total  BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_total    CHECK (server_total    = -1 OR server_total    BETWEEN 0 AND 100),
  CONSTRAINT chk_score_customer_text   CHECK (customer_text   = -1 OR customer_text   BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_text     CHECK (server_text     = -1 OR server_text     BETWEEN 0 AND 100),
  CONSTRAINT chk_score_customer_audio  CHECK (customer_audio  = -1 OR customer_audio  BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_audio    CHECK (server_audio    = -1 OR server_audio    BETWEEN 0 AND 100),
  CONSTRAINT chk_score_customer_facial CHECK (customer_facial = -1 OR customer_facial BETWEEN 0 AND 100),
  CONSTRAINT chk_score_server_facial   CHECK (server_facial   = -1 OR server_facial   BETWEEN 0 AND 100)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析分數 0~100，-1=沒有資料。customer_*=顧客、server_*=服務人員';

CREATE TABLE IF NOT EXISTS tbl_video_storage (
  video_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  service_id        VARCHAR(32)     NOT NULL,

  video_name        VARCHAR(255)    NOT NULL,

  video_url         VARCHAR(1024)   NOT NULL DEFAULT '',

  video_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (video_id),
  UNIQUE KEY uk_video_service_name (service_id, video_name),
  CONSTRAINT fk_video_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '錄影檔位置；video_name 含 cam0（顧客）或 cam1（員工）';

CREATE TABLE IF NOT EXISTS tbl_audio_storage (
  audio_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  service_id        VARCHAR(32)     NOT NULL,

  audio_name        VARCHAR(255)    NOT NULL,

  audio_url         VARCHAR(1024)   NOT NULL DEFAULT '',

  audio_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (audio_id),
  UNIQUE KEY uk_audio_service (service_id),
  CONSTRAINT fk_audio_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '錄音檔位置';

CREATE TABLE IF NOT EXISTS tbl_charts_storage (
  charts_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  service_id         VARCHAR(32)     NOT NULL,

  charts_name        VARCHAR(255)    NULL,

  charts_url         VARCHAR(1024)   NOT NULL,

  charts_upload_time DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (charts_id),
  UNIQUE KEY uk_charts_service_name (service_id, charts_name),
  CONSTRAINT fk_charts_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析圖表位置；檔名需含 service_id 以免互相覆蓋';

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

CREATE TABLE IF NOT EXISTS tbl_report (
  report_id          VARCHAR(36)  NOT NULL,

  report_kind        VARCHAR(16)  NOT NULL,

  service_id         VARCHAR(32)  NULL,

  staff_account_id   VARCHAR(36)  NULL,

  period_start       DATE         NULL,
  period_end         DATE         NULL,

  report_status      VARCHAR(16)  NOT NULL DEFAULT 'generating',

  failure_reason     VARCHAR(255) NULL,

  s3_key             VARCHAR(255) NOT NULL,

  created_by         VARCHAR(36)  NOT NULL,

  created_at         DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

  published_at       DATETIME     NULL,
  published_by       VARCHAR(36)  NULL,

  ai_model           VARCHAR(100) NULL,
  ai_prompt_version  VARCHAR(32)  NULL,

  PRIMARY KEY (report_id),
  KEY idx_report_staff_kind (staff_account_id, report_kind, created_at),
  KEY idx_report_service (service_id, created_at),
  KEY idx_report_status_created (report_status, created_at),
  CONSTRAINT fk_report_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_report_staff FOREIGN KEY (staff_account_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_report_created_by FOREIGN KEY (created_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_report_published_by FOREIGN KEY (published_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_report_kind CHECK (report_kind IN ('service', 'staff', 'company')),
  CONSTRAINT chk_report_status CHECK (report_status IN ('generating', 'ready', 'failed')),
  CONSTRAINT chk_report_subject CHECK (
       (report_kind = 'service' AND service_id IS NOT NULL AND staff_account_id IS NOT NULL
        AND period_start IS NULL AND period_end IS NULL)
    OR (report_kind = 'staff' AND service_id IS NULL AND staff_account_id IS NOT NULL
        AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)
    OR (report_kind = 'company' AND service_id IS NULL AND staff_account_id IS NULL
        AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
  CONSTRAINT chk_report_published CHECK (
    (published_at IS NULL AND published_by IS NULL)
    OR (published_at IS NOT NULL AND published_by IS NOT NULL AND report_status = 'ready'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '報表（new-feature N4、N5）。PDF 在 S3 的 reports/{report_id}.pdf；published_at 是 NULL 時服務人員看不到';

DELIMITER $$

DROP PROCEDURE IF EXISTS sp_check_login $$
DROP PROCEDURE IF EXISTS sp_get_login $$
CREATE PROCEDURE sp_get_login(
  IN p_username VARCHAR(64) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '登入：回傳帳號的 id、等級與密碼雜湊，由 Python 比對；找不到帳號時回傳 0 列'
BEGIN
  SELECT a.account_id,
         a.account_level,
         a.account_password
    FROM tbl_account AS a
   WHERE a.account_username = p_username
   LIMIT 1;
END $$

DROP PROCEDURE IF EXISTS sp_get_account_name $$
CREATE PROCEDURE sp_get_account_name(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '回傳 1 列 (account_name)；找不到時回傳 0 列'
BEGIN
  SELECT a.account_name
    FROM tbl_account AS a
   WHERE a.account_id = p_account_id;
END $$

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

DROP PROCEDURE IF EXISTS sp_insert_service $$
CREATE PROCEDURE sp_insert_service(
  IN  p_service_id     VARCHAR(32) CHARACTER SET utf8mb4,
  IN  p_account_id     VARCHAR(36) CHARACTER SET utf8mb4,
  IN  p_service_start  DATETIME,
  IN  p_service_end    DATETIME,
  IN  p_service_status TINYINT UNSIGNED,
  OUT p_result         INT
)
  MODIFIES SQL DATA
  COMMENT '新增服務（錄影結束時呼叫，狀態 1）；OUT p_result = 影響列數'
BEGIN
  INSERT INTO tbl_service (service_id, account_id, service_start, service_end, service_status)
  VALUES (p_service_id, p_account_id, p_service_start, p_service_end, p_service_status);
  SET p_result = ROW_COUNT();
END $$

DROP PROCEDURE IF EXISTS sp_insert_video_storage $$
CREATE PROCEDURE sp_insert_video_storage(
  IN p_service_id        VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_video_name        VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_video_url         VARCHAR(1024) CHARACTER SET utf8mb4,
  IN p_video_upload_time DATETIME
)
  MODIFIES SQL DATA
  COMMENT '新增錄影檔位置'
BEGIN
  INSERT INTO tbl_video_storage (service_id, video_name, video_url, video_upload_time)
  VALUES (p_service_id, p_video_name, p_video_url, p_video_upload_time);
END $$

DROP PROCEDURE IF EXISTS sp_insert_audio_storage $$
CREATE PROCEDURE sp_insert_audio_storage(
  IN p_service_id        VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_audio_name        VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_audio_url         VARCHAR(1024) CHARACTER SET utf8mb4,
  IN p_audio_upload_time DATETIME
)
  MODIFIES SQL DATA
  COMMENT '新增錄音檔位置'
BEGIN
  INSERT INTO tbl_audio_storage (service_id, audio_name, audio_url, audio_upload_time)
  VALUES (p_service_id, p_audio_name, p_audio_url, p_audio_upload_time);
END $$

DROP PROCEDURE IF EXISTS sp_insert_service_score $$
CREATE PROCEDURE sp_insert_service_score(
  IN p_service_id      VARCHAR(32) CHARACTER SET utf8mb4,
  IN p_customer_facial DOUBLE,
  IN p_customer_audio  DOUBLE,
  IN p_customer_text   DOUBLE,
  IN p_server_facial   DOUBLE,
  IN p_server_audio    DOUBLE,
  IN p_server_text     DOUBLE,
  IN p_customer_total  DOUBLE,
  IN p_server_total    DOUBLE
)
  MODIFIES SQL DATA
  COMMENT '新增或更新一筆服務的 8 個分數（分析服務呼叫）；不回傳結果集'
BEGIN
  INSERT INTO tbl_service_score
    (service_id, customer_facial, customer_audio, customer_text,
     server_facial, server_audio, server_text, customer_total, server_total)
  VALUES
    (p_service_id, p_customer_facial, p_customer_audio, p_customer_text,
     p_server_facial, p_server_audio, p_server_text, p_customer_total, p_server_total)
  ON DUPLICATE KEY UPDATE
    customer_facial  = p_customer_facial,
    customer_audio   = p_customer_audio,
    customer_text    = p_customer_text,
    server_facial    = p_server_facial,
    server_audio     = p_server_audio,
    server_text      = p_server_text,
    customer_total   = p_customer_total,
    server_total     = p_server_total,
    score_created_at = CURRENT_TIMESTAMP;
END $$

DROP PROCEDURE IF EXISTS sp_insert_charts_storage $$
CREATE PROCEDURE sp_insert_charts_storage(
  IN p_service_id  VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_charts_name VARCHAR(255)  CHARACTER SET utf8mb4,
  IN p_charts_url  VARCHAR(1024) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT '新增或更新一張圖表的位置（分析服務呼叫）；不回傳結果集'
BEGIN
  INSERT INTO tbl_charts_storage (service_id, charts_name, charts_url)
  VALUES (p_service_id, p_charts_name, p_charts_url)
  ON DUPLICATE KEY UPDATE
    charts_url         = p_charts_url,
    charts_upload_time = CURRENT_TIMESTAMP;
END $$

DROP PROCEDURE IF EXISTS sp_insert_text_storage $$
CREATE PROCEDURE sp_insert_text_storage(
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

DROP PROCEDURE IF EXISTS sp_list_reports $$
CREATE PROCEDURE sp_list_reports(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '報表清單：服務人員只有自己已發佈的（不含全公司），管理員全部；每個對象最新一筆'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;

  SELECT a.account_level INTO v_level
    FROM tbl_account AS a
   WHERE a.account_id = p_account_id;

  SELECT r.report_id, r.report_kind, r.service_id, r.staff_account_id,
         a.account_name AS staff_name, a.account_username AS staff_username,
         r.period_start, r.period_end, r.report_status, r.failure_reason, r.s3_key,
         r.created_by, r.created_at, r.published_at, r.published_by,
         r.ai_model, r.ai_prompt_version,
         s.service_start, s.service_end
    FROM (
      SELECT t.*,
             ROW_NUMBER() OVER (PARTITION BY t.report_kind, t.service_id, t.staff_account_id,
                                             t.period_start, t.period_end
                                ORDER BY t.created_at DESC, t.report_id DESC) AS newest
        FROM tbl_report AS t
       WHERE v_level IS NOT NULL
         AND (v_level <> '1'
              OR (t.staff_account_id = p_account_id
                  AND t.published_at IS NOT NULL
                  AND t.report_kind <> 'company'))
    ) AS r
    LEFT JOIN tbl_account AS a ON a.account_id = r.staff_account_id
    LEFT JOIN tbl_service AS s ON s.service_id = r.service_id
   WHERE r.newest = 1
   ORDER BY r.created_at DESC, r.report_id DESC;
END $$

DROP PROCEDURE IF EXISTS sp_list_service_report_states $$
CREATE PROCEDURE sp_list_service_report_states(
  IN p_account_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT '自己每筆服務的報表狀態：published（附最新已發佈的 report_id）或 unpublished；不回傳未發佈的報表'
BEGIN
  SELECT r.service_id,
         IF(p.report_id IS NULL, 'unpublished', 'published') AS report_state,
         p.report_id AS published_report_id
    FROM (
      SELECT DISTINCT t.service_id
        FROM tbl_report AS t
       WHERE t.report_kind = 'service'
         AND t.staff_account_id = p_account_id
         AND t.report_status = 'ready'
    ) AS r
    LEFT JOIN (
      SELECT t.service_id, t.report_id,
             ROW_NUMBER() OVER (PARTITION BY t.service_id
                                ORDER BY t.created_at DESC, t.report_id DESC) AS newest
        FROM tbl_report AS t
       WHERE t.report_kind = 'service'
         AND t.staff_account_id = p_account_id
         AND t.published_at IS NOT NULL
    ) AS p ON p.service_id = r.service_id AND p.newest = 1
   ORDER BY r.service_id;
END $$

DELIMITER ;

-- [BEGIN PLAN1 W2] 員工輔導與後續追蹤（2026-11-01，T45）：3 張表、13 個預存程序
--   內容和 new-feature/migrations/006_coaching.sql 相同，只差程序在這裡是 DROP + CREATE（這個檔案的慣例），
--   006 用 CREATE … IF NOT EXISTS、不 DROP（T42 第 9 節）。GRANT 在最後選用區塊的 [BEGIN PLAN1 W2 GRANTS]。
--   這一整段只依賴上面的 tbl_service、tbl_account，可以原樣搬到別的 schema（例如測試用的精簡 schema）。
--   桌面端的讀寫全部經過這 13 個程序：每個都帶呼叫者的 account_id，由資料庫依等級決定能看什麼、做什麼
--   （服務人員只有自己的行動、只能回應和確認；管理員全部）。寫入的程序不 COMMIT，桌面端包在交易裡。
--   不能做的事用 SIGNAL SQLSTATE '45000'（errno 1644），MESSAGE_TEXT 是 coaching:<原因>（說明見 migration 006）。
-- #############################################################################

-- =============================================================================
-- tbl_coaching_action：改善行動。一筆＝主管從一筆服務（原始服務）訂的一項改善目標。
--   員工（staff_account_id）是原始服務的 account_id，由 sp_coaching_create 寫入，不是畫面選的。
--   狀態只往前走：open（進行中）→ completed（看過後續服務、寫了結論）或 cancelled（撤回，寫原因）。
--   「調整」不是狀態：目標、期限換成新的，仍是 open；看過的後續服務從這裡拿掉，舊的值都在 tbl_coaching_event。
--   version：樂觀鎖。主管的每一次修改（連結、完成、調整、撤回）都 +1，帶的版本不對就不改（coaching:changed）。
--   回應、確認、時間註記不改 version：它們是另外的列，和主管的修改不衝突。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_coaching_action (
  -- 桌面端用 uuid4 產生（36 字元）；重試時用同一個，撞到主鍵就是上一次已經寫好了
  action_id            VARCHAR(36)   NOT NULL,
  -- 原始服務：主管看的那一筆（作為例子），之後不能改
  source_service_id    VARCHAR(32)   NOT NULL,
  staff_account_id     VARCHAR(36)   NOT NULL,
  created_by           VARCHAR(36)   NOT NULL,
  -- 改善目標：員工要練的一句話，例如「服務結束前說明下一步，並確認顧客理解」
  goal                 VARCHAR(300)  NOT NULL,
  -- 主管評語：為什麼訂這個目標（員工靠目標和這段話，PLAN1「服務人員依靠什麼」）
  manager_comment      VARCHAR(2000) NOT NULL,
  -- 追蹤期限（到期時主管另選一筆後續服務來看）
  due_date             DATE          NOT NULL,
  action_status        VARCHAR(16)   NOT NULL DEFAULT 'open',
  -- 後續服務：同一位員工、分析完成、比原始服務晚的一筆（sp_coaching_link_follow_up 檢查）。調整時清掉
  follow_up_service_id VARCHAR(32)   NULL,
  -- 員工按 Acknowledge 的時間：只代表已讀、已回應，不是完成。目標調整時清掉（要再確認一次新的目標）
  acknowledged_at      DATETIME(3)   NULL,
  -- completed：主管的結論；cancelled：撤回的原因
  conclusion           VARCHAR(2000) NULL,
  concluded_by         VARCHAR(36)   NULL,
  concluded_at         DATETIME(3)   NULL,
  created_at           DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at           DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  version              INT UNSIGNED  NOT NULL DEFAULT 1,

  PRIMARY KEY (action_id),
  KEY idx_coaching_staff_status (staff_account_id, action_status, due_date),   -- 服務人員的清單
  KEY idx_coaching_status_due (action_status, due_date),                     -- 管理員的清單
  KEY idx_coaching_source (source_service_id),
  KEY idx_coaching_follow_up (follow_up_service_id),
  CONSTRAINT fk_coaching_source FOREIGN KEY (source_service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_staff FOREIGN KEY (staff_account_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_created_by FOREIGN KEY (created_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_follow_up FOREIGN KEY (follow_up_service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_concluded_by FOREIGN KEY (concluded_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_coaching_status CHECK (action_status IN ('open', 'completed', 'cancelled')),
  CONSTRAINT chk_coaching_goal CHECK (CHAR_LENGTH(TRIM(goal)) > 0),
  CONSTRAINT chk_coaching_comment CHECK (CHAR_LENGTH(TRIM(manager_comment)) > 0),
  -- 後續服務不能是原始服務本身（同一位員工、比較晚、分析完成要查別的表，在程序裡檢查）
  CONSTRAINT chk_coaching_follow_up CHECK (follow_up_service_id IS NULL OR follow_up_service_id <> source_service_id),
  -- 進行中沒有結論；完成要有後續服務和結論；撤回要有原因。IS NOT NULL 要寫出來：CHECK 遇到 NULL 算通過
  CONSTRAINT chk_coaching_concluded CHECK (
       (action_status = 'open' AND conclusion IS NULL AND concluded_by IS NULL AND concluded_at IS NULL)
    OR (action_status = 'completed' AND follow_up_service_id IS NOT NULL AND conclusion IS NOT NULL
        AND CHAR_LENGTH(TRIM(conclusion)) > 0 AND concluded_by IS NOT NULL AND concluded_at IS NOT NULL)
    OR (action_status = 'cancelled' AND conclusion IS NOT NULL AND CHAR_LENGTH(TRIM(conclusion)) > 0
        AND concluded_by IS NOT NULL AND concluded_at IS NOT NULL))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'W2 改善行動：原始服務、目標、期限、後續服務、結論；讀寫一律經由 sp_coaching_*';


-- =============================================================================
-- tbl_coaching_marker：時間註記。只屬於一筆服務的影片：offset_seconds 從那一筆自己的 service_start（影片開頭）算，
--   原始服務的 02:15 和後續服務的 02:15 不是同一秒（PLAN1 W2）。只能加在原始服務或目前的後續服務上。
--   拿掉時不刪：寫 removed_at、removed_by，並在 tbl_coaching_event 留一列。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_coaching_marker (
  marker_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  action_id      VARCHAR(36)     NOT NULL,
  service_id     VARCHAR(32)     NOT NULL,
  offset_seconds INT UNSIGNED    NOT NULL,
  note           VARCHAR(300)    NOT NULL DEFAULT '',
  created_by     VARCHAR(36)     NOT NULL,
  created_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  removed_by     VARCHAR(36)     NULL,
  removed_at     DATETIME(3)     NULL,

  PRIMARY KEY (marker_id),
  KEY idx_coaching_marker_action (action_id, service_id, offset_seconds),
  KEY idx_coaching_marker_service (service_id),
  CONSTRAINT fk_coaching_marker_action FOREIGN KEY (action_id)
    REFERENCES tbl_coaching_action (action_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_marker_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_marker_created_by FOREIGN KEY (created_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_marker_removed_by FOREIGN KEY (removed_by)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_coaching_marker_offset CHECK (offset_seconds < 86400),
  CONSTRAINT chk_coaching_marker_removed CHECK ((removed_by IS NULL AND removed_at IS NULL)
                                                OR (removed_by IS NOT NULL AND removed_at IS NOT NULL))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'W2 時間註記：秒數從那一筆服務自己的開頭算；拿掉的留著（removed_at）';


-- =============================================================================
-- tbl_coaching_event：歷史，只新增、不修改（adam_app 沒有這張表的權限，只能經由程序新增）。
--   每一次改變都有一列：建立、雙方的回應、確認、連結後續服務、加或拿掉時間註記、完成、調整、撤回。
--   author_role 是寫的當下的身分（admin、staff）：等級之後改了，舊的回應仍看得出是誰的立場。
--   調整：goal_before／goal_after、due_before／due_after 是舊的和新的值；service_id 是那一次看過的後續服務。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_coaching_event (
  event_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  action_id      VARCHAR(36)     NOT NULL,
  event_type     VARCHAR(24)     NOT NULL,
  author_id      VARCHAR(36)     NOT NULL,
  author_role    VARCHAR(8)      NOT NULL,
  -- 回應的內容、建立時的評語、完成的結論、調整或撤回的原因、時間註記的說明
  body           VARCHAR(2000)   NULL,
  goal_before    VARCHAR(300)    NULL,
  goal_after     VARCHAR(300)    NULL,
  due_before     DATE            NULL,
  due_after      DATE            NULL,
  -- 建立：原始服務；連結、完成、調整：後續服務；時間註記：它所在的服務
  service_id     VARCHAR(32)     NULL,
  marker_id      BIGINT UNSIGNED NULL,
  marker_seconds INT UNSIGNED    NULL,
  created_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

  PRIMARY KEY (event_id),
  KEY idx_coaching_event_action (action_id, created_at),
  CONSTRAINT fk_coaching_event_action FOREIGN KEY (action_id)
    REFERENCES tbl_coaching_action (action_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_event_author FOREIGN KEY (author_id)
    REFERENCES tbl_account (account_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_event_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_coaching_event_marker FOREIGN KEY (marker_id)
    REFERENCES tbl_coaching_marker (marker_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT chk_coaching_event_type CHECK (event_type IN ('created', 'response', 'acknowledged', 'follow_up_linked',
                                                           'marker_added', 'marker_removed', 'completed', 'adjusted',
                                                           'cancelled')),
  CONSTRAINT chk_coaching_event_role CHECK (author_role IN ('admin', 'staff'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'W2 歷史：只新增、不修改；誰、什麼時候、改了什麼（舊值和新值）';


DELIMITER $$

-- -----------------------------------------------------------------------------
-- sp_coaching_list(caller)：清單。服務人員只有自己的，管理員全部。進行中的在前，再依期限、建立時間。
--   last_staff_at、last_admin_at：雙方最後一次動作的時間（管理員的清單標「Staff replied」）。
-- 呼叫：db lane 上 cursor.execute("CALL sp_coaching_list(%s)", (account_id,))，1 個往返。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_list $$
CREATE PROCEDURE sp_coaching_list(
  IN p_caller VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：改善行動的清單；服務人員只有自己的，管理員全部'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;

  SELECT a.action_id, a.source_service_id, src.service_start AS source_start, src.service_end AS source_end,
         a.staff_account_id, st.account_name AS staff_name, st.account_username AS staff_username,
         a.created_by, cr.account_name AS created_by_name,
         a.goal, a.due_date, a.action_status,
         a.follow_up_service_id, fu.service_start AS follow_up_start, fu.service_end AS follow_up_end,
         a.acknowledged_at, a.concluded_by, cb.account_name AS concluded_by_name, a.concluded_at,
         a.created_at, a.updated_at, a.version, ev.last_staff_at, ev.last_admin_at
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS src ON src.service_id = a.source_service_id
    JOIN tbl_account AS st ON st.account_id = a.staff_account_id
    LEFT JOIN tbl_account AS cr ON cr.account_id = a.created_by
    LEFT JOIN tbl_account AS cb ON cb.account_id = a.concluded_by
    LEFT JOIN tbl_service AS fu ON fu.service_id = a.follow_up_service_id
    LEFT JOIN (SELECT e.action_id,
                      MAX(IF(e.author_role = 'staff', e.created_at, NULL)) AS last_staff_at,
                      MAX(IF(e.author_role = 'admin', e.created_at, NULL)) AS last_admin_at
                 FROM tbl_coaching_event AS e
                GROUP BY e.action_id) AS ev ON ev.action_id = a.action_id
   WHERE v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller)
   ORDER BY a.action_status = 'open' DESC, a.due_date, a.created_at DESC, a.action_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_detail(caller, action_id)：一項行動的明細，3 個結果集（1 個往返）：
--   1. 行動（清單的欄位，加上主管評語和結論）；2. 時間註記（沒有拿掉的，依服務的時間、秒數排）；
--   3. 歷史（舊的在前）。看不到的行動（別人的、不存在的）3 個結果集都是 0 列，不透露存不存在。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_detail $$
CREATE PROCEDURE sp_coaching_detail(
  IN p_caller    VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：一項行動的明細（行動、時間註記、歷史）；看不到的 3 個結果集都是 0 列'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_visible INT DEFAULT 0;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  SELECT COUNT(*) INTO v_visible
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id
     AND v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller);

  SELECT a.action_id, a.source_service_id, src.service_start AS source_start, src.service_end AS source_end,
         a.staff_account_id, st.account_name AS staff_name, st.account_username AS staff_username,
         a.created_by, cr.account_name AS created_by_name,
         a.goal, a.due_date, a.action_status,
         a.follow_up_service_id, fu.service_start AS follow_up_start, fu.service_end AS follow_up_end,
         a.acknowledged_at, a.concluded_by, cb.account_name AS concluded_by_name, a.concluded_at,
         a.created_at, a.updated_at, a.version, ev.last_staff_at, ev.last_admin_at,
         a.manager_comment, a.conclusion
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS src ON src.service_id = a.source_service_id
    JOIN tbl_account AS st ON st.account_id = a.staff_account_id
    LEFT JOIN tbl_account AS cr ON cr.account_id = a.created_by
    LEFT JOIN tbl_account AS cb ON cb.account_id = a.concluded_by
    LEFT JOIN tbl_service AS fu ON fu.service_id = a.follow_up_service_id
    LEFT JOIN (SELECT e.action_id,
                      MAX(IF(e.author_role = 'staff', e.created_at, NULL)) AS last_staff_at,
                      MAX(IF(e.author_role = 'admin', e.created_at, NULL)) AS last_admin_at
                 FROM tbl_coaching_event AS e
                WHERE e.action_id = p_action_id
                GROUP BY e.action_id) AS ev ON ev.action_id = a.action_id
   WHERE a.action_id = p_action_id AND v_visible > 0;

  SELECT m.marker_id, m.service_id, s.service_start, m.offset_seconds, m.note,
         m.created_by, au.account_name AS created_by_name, m.created_at
    FROM tbl_coaching_marker AS m
    JOIN tbl_service AS s ON s.service_id = m.service_id
    LEFT JOIN tbl_account AS au ON au.account_id = m.created_by
   WHERE m.action_id = p_action_id AND m.removed_at IS NULL AND v_visible > 0
   ORDER BY s.service_start, m.offset_seconds, m.marker_id;

  SELECT e.event_id, e.event_type, e.author_id, au.account_name AS author_name, e.author_role, e.body,
         e.goal_before, e.goal_after, e.due_before, e.due_after, e.service_id, s.service_start,
         e.marker_id, e.marker_seconds, e.created_at
    FROM tbl_coaching_event AS e
    LEFT JOIN tbl_account AS au ON au.account_id = e.author_id
    LEFT JOIN tbl_service AS s ON s.service_id = e.service_id
   WHERE e.action_id = p_action_id AND v_visible > 0
   ORDER BY e.created_at, e.event_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_for_service(caller, service_id)：管理員明細頁的輔導卡。2 個結果集（1 個往返）：
--   1. 和這筆服務有關的行動：relation 是 source（從這筆訂的）、follow_up（這筆是目前的後續服務）、
--      reviewed（調整時看過這一筆）；2. 這筆服務上的時間註記（沒有拿掉的，依秒數排）。
--   看得到的範圍和清單一樣（服務人員只有自己的）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_for_service $$
CREATE PROCEDURE sp_coaching_for_service(
  IN p_caller     VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_service_id VARCHAR(32) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：和一筆服務有關的行動（原始、後續、調整時看過的）和這筆服務上的時間註記'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;

  SELECT a.action_id, a.source_service_id, a.staff_account_id, st.account_name AS staff_name,
         st.account_username AS staff_username, a.goal, a.due_date, a.action_status,
         a.follow_up_service_id, a.version, a.created_at,
         CASE WHEN a.source_service_id = p_service_id THEN 'source'
              WHEN a.follow_up_service_id = p_service_id THEN 'follow_up'
              ELSE 'reviewed' END AS relation
    FROM tbl_coaching_action AS a
    JOIN tbl_account AS st ON st.account_id = a.staff_account_id
   WHERE v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller)
     AND (a.source_service_id = p_service_id
          OR a.follow_up_service_id = p_service_id
          OR EXISTS (SELECT 1 FROM tbl_coaching_event AS e
                      WHERE e.action_id = a.action_id AND e.event_type = 'adjusted' AND e.service_id = p_service_id))
   ORDER BY a.action_status = 'open' DESC, a.created_at DESC, a.action_id;

  SELECT m.marker_id, m.action_id, m.offset_seconds, m.note, m.created_by, au.account_name AS created_by_name,
         m.created_at
    FROM tbl_coaching_marker AS m
    JOIN tbl_coaching_action AS a ON a.action_id = m.action_id
    LEFT JOIN tbl_account AS au ON au.account_id = m.created_by
   WHERE m.service_id = p_service_id AND m.removed_at IS NULL
     AND v_level IS NOT NULL
     AND (v_level <> '1' OR a.staff_account_id = p_caller)
   ORDER BY m.offset_seconds, m.marker_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_follow_up_options(caller, action_id)：能當後續服務的服務（只有管理員；服務人員 0 列）。
--   同一位員工、分析完成（3）、不是原始服務、比原始服務晚；調整過的話，還要比調整時看過的後續服務晚。
--   新的在前，最多 50 筆。sp_coaching_link_follow_up 用同一套規則再檢查一次。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_follow_up_options $$
CREATE PROCEDURE sp_coaching_follow_up_options(
  IN p_caller    VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id VARCHAR(36) CHARACTER SET utf8mb4
)
  READS SQL DATA
  COMMENT 'W2：能當後續服務的服務（管理員）：同一位員工、分析完成、比原始服務和看過的後續服務晚'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_source VARCHAR(32) DEFAULT NULL;
  DECLARE v_after DATETIME DEFAULT NULL;
  DECLARE v_reviewed DATETIME DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  SELECT a.staff_account_id, a.source_service_id, s.service_start INTO v_staff, v_source, v_after
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS s ON s.service_id = a.source_service_id
   WHERE a.action_id = p_action_id;
  SELECT MAX(s.service_start) INTO v_reviewed
    FROM tbl_coaching_event AS e
    JOIN tbl_service AS s ON s.service_id = e.service_id
   WHERE e.action_id = p_action_id AND e.event_type = 'adjusted';
  IF v_reviewed IS NOT NULL AND v_reviewed > v_after THEN
    SET v_after = v_reviewed;
  END IF;

  SELECT s.service_id, s.service_start, s.service_end
    FROM tbl_service AS s
   WHERE v_level IS NOT NULL AND v_level <> '1' AND v_staff IS NOT NULL
     AND s.account_id = v_staff AND s.service_status = 3
     AND s.service_id <> v_source AND s.service_start > v_after
   ORDER BY s.service_start DESC, s.service_id DESC
   LIMIT 50;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_create(caller, action_id, source_service_id, goal, comment, due_date)：主管從一筆服務建立改善目標。
--   員工是那筆服務的 account_id（要是等級 1，coaching:not_staff）。寫一列行動和一列 created 歷史。
--   時間註記另外用 sp_coaching_add_marker，同一個交易（桌面端：START TRANSACTION、這個、每個註記一個、COMMIT）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_create $$
CREATE PROCEDURE sp_coaching_create(
  IN p_caller            VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id         VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_source_service_id VARCHAR(32)   CHARACTER SET utf8mb4,
  IN p_goal              VARCHAR(300)  CHARACTER SET utf8mb4,
  IN p_comment           VARCHAR(2000) CHARACTER SET utf8mb4,
  IN p_due_date          DATE
)
  MODIFIES SQL DATA
  COMMENT 'W2：主管從一筆服務建立改善目標（員工是那筆服務的 account_id）；呼叫端包在交易裡'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_staff_level CHAR(1) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT s.account_id, ac.account_level INTO v_staff, v_staff_level
    FROM tbl_service AS s
    JOIN tbl_account AS ac ON ac.account_id = s.account_id
   WHERE s.service_id = p_source_service_id;
  IF v_staff IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_staff_level <> '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_staff';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_goal, ''))) = 0 OR CHAR_LENGTH(TRIM(COALESCE(p_comment, ''))) = 0
     OR p_due_date IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  INSERT INTO tbl_coaching_action
    (action_id, source_service_id, staff_account_id, created_by, goal, manager_comment, due_date)
  VALUES (p_action_id, p_source_service_id, v_staff, p_caller, TRIM(p_goal), TRIM(p_comment), p_due_date);
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body, goal_after, due_after, service_id)
  VALUES (p_action_id, 'created', p_caller, 'admin', TRIM(p_comment), TRIM(p_goal), p_due_date, p_source_service_id);
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_add_marker(caller, action_id, service_id, offset_seconds, note)：加一個時間註記（管理員，進行中）。
--   只能加在原始服務或目前的後續服務上（coaching:marker_service）；秒數 0～86399。回傳 1 列 (marker_id)。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_add_marker $$
CREATE PROCEDURE sp_coaching_add_marker(
  IN p_caller         VARCHAR(36)  CHARACTER SET utf8mb4,
  IN p_action_id      VARCHAR(36)  CHARACTER SET utf8mb4,
  IN p_service_id     VARCHAR(32)  CHARACTER SET utf8mb4,
  IN p_offset_seconds INT,
  IN p_note           VARCHAR(300) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：在原始服務或目前的後續服務上加一個時間註記（管理員）；回傳 1 列 (marker_id)'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_source VARCHAR(32) DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;
  DECLARE v_marker BIGINT UNSIGNED DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.source_service_id, a.follow_up_service_id INTO v_status, v_source, v_follow_up
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF p_service_id IS NULL OR NOT (p_service_id = v_source OR p_service_id <=> v_follow_up) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:marker_service';
  END IF;
  IF p_offset_seconds IS NULL OR p_offset_seconds < 0 OR p_offset_seconds >= 86400 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  INSERT INTO tbl_coaching_marker (action_id, service_id, offset_seconds, note, created_by)
  VALUES (p_action_id, p_service_id, p_offset_seconds, TRIM(COALESCE(p_note, '')), p_caller);
  SET v_marker = LAST_INSERT_ID();
  INSERT INTO tbl_coaching_event
    (action_id, event_type, author_id, author_role, body, service_id, marker_id, marker_seconds)
  VALUES (p_action_id, 'marker_added', p_caller, 'admin', NULLIF(TRIM(COALESCE(p_note, '')), ''), p_service_id,
          v_marker, p_offset_seconds);
  SELECT v_marker AS marker_id;
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_remove_marker(caller, marker_id)：拿掉一個時間註記（管理員，進行中）。不刪：寫 removed_at、removed_by，
--   並留一列 marker_removed 歷史（秒數、所在的服務、說明）。已經拿掉的什麼都不做。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_remove_marker $$
CREATE PROCEDURE sp_coaching_remove_marker(
  IN p_caller    VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_marker_id BIGINT UNSIGNED
)
  MODIFIES SQL DATA
  COMMENT 'W2：拿掉一個時間註記（管理員）；標成已拿掉、寫一列歷史，不刪資料'
body: BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_action VARCHAR(36) DEFAULT NULL;
  DECLARE v_service VARCHAR(32) DEFAULT NULL;
  DECLARE v_seconds INT UNSIGNED DEFAULT NULL;
  DECLARE v_note VARCHAR(300) DEFAULT NULL;
  DECLARE v_removed DATETIME(3) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT m.action_id, m.service_id, m.offset_seconds, m.note, m.removed_at, a.action_status
    INTO v_action, v_service, v_seconds, v_note, v_removed, v_status
    FROM tbl_coaching_marker AS m
    JOIN tbl_coaching_action AS a ON a.action_id = m.action_id
   WHERE m.marker_id = p_marker_id;
  IF v_action IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_removed IS NOT NULL THEN
    LEAVE body;
  END IF;

  UPDATE tbl_coaching_marker
     SET removed_by = p_caller, removed_at = NOW(3)
   WHERE marker_id = p_marker_id AND removed_at IS NULL;
  IF ROW_COUNT() = 1 THEN
    INSERT INTO tbl_coaching_event
      (action_id, event_type, author_id, author_role, body, service_id, marker_id, marker_seconds)
    VALUES (v_action, 'marker_removed', p_caller, 'admin', NULLIF(v_note, ''), v_service, p_marker_id, v_seconds);
  END IF;
END body $$

-- -----------------------------------------------------------------------------
-- sp_coaching_respond(caller, action_id, body)：回應（補充情境、回應、提出不同看法；主管也可以回）。
--   服務人員只能回自己的行動（別人的當作不存在，coaching:not_found）；只有進行中的行動可以回。只新增一列歷史。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_respond $$
CREATE PROCEDURE sp_coaching_respond(
  IN p_caller    VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_body      VARCHAR(2000) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：回應（服務人員：自己的行動；管理員：全部）；只新增一列歷史'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.staff_account_id, a.action_status INTO v_staff, v_status
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_staff IS NULL OR (v_level = '1' AND v_staff <> p_caller) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_body, ''))) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body)
  VALUES (p_action_id, 'response', p_caller, IF(v_level = '1', 'staff', 'admin'), TRIM(p_body));
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_acknowledge(caller, action_id, expected_version)：員工確認看過目前的目標（只代表已回應，不是完成）。
--   只有那位員工自己能按；目標在這段期間調整過（version 不對）就是 coaching:changed，要先看新的目標。
--   已經確認過的什麼都不做。不改 version。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_acknowledge $$
CREATE PROCEDURE sp_coaching_acknowledge(
  IN p_caller           VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED
)
  MODIFIES SQL DATA
  COMMENT 'W2：員工確認看過目前的目標（只代表已回應）；版本不對是 coaching:changed'
body: BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_acknowledged DATETIME(3) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level <> '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.staff_account_id, a.action_status, a.version, a.acknowledged_at
    INTO v_staff, v_status, v_version, v_acknowledged
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_staff IS NULL OR v_staff <> p_caller THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF v_acknowledged IS NOT NULL THEN
    LEAVE body;
  END IF;

  UPDATE tbl_coaching_action
     SET acknowledged_at = NOW(3)
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open'
     AND acknowledged_at IS NULL;
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role)
  VALUES (p_action_id, 'acknowledged', p_caller, 'staff');
END body $$

-- -----------------------------------------------------------------------------
-- sp_coaching_link_follow_up(caller, action_id, expected_version, service_id)：連結（或換掉）後續服務（管理員，進行中）。
--   規則和 sp_coaching_follow_up_options 一樣（不合就是 coaching:follow_up）。換掉時，舊的後續服務上的時間註記
--   一起拿掉（各留一列 marker_removed 歷史）：它們只屬於那一部影片。已經是這一筆的什麼都不做。version +1。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_link_follow_up $$
CREATE PROCEDURE sp_coaching_link_follow_up(
  IN p_caller           VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36) CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_service_id       VARCHAR(32) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：連結或換掉後續服務（管理員）：同一位員工、分析完成、比原始服務和看過的後續服務晚、不是原始服務'
body: BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_staff VARCHAR(36) DEFAULT NULL;
  DECLARE v_source VARCHAR(32) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;
  DECLARE v_after DATETIME DEFAULT NULL;
  DECLARE v_reviewed DATETIME DEFAULT NULL;
  DECLARE v_eligible INT DEFAULT 0;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.staff_account_id, a.source_service_id, a.action_status, a.version, a.follow_up_service_id, s.service_start
    INTO v_staff, v_source, v_status, v_version, v_follow_up, v_after
    FROM tbl_coaching_action AS a
    JOIN tbl_service AS s ON s.service_id = a.source_service_id
   WHERE a.action_id = p_action_id;
  IF v_staff IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  SELECT MAX(s.service_start) INTO v_reviewed
    FROM tbl_coaching_event AS e
    JOIN tbl_service AS s ON s.service_id = e.service_id
   WHERE e.action_id = p_action_id AND e.event_type = 'adjusted';
  IF v_reviewed IS NOT NULL AND v_reviewed > v_after THEN
    SET v_after = v_reviewed;
  END IF;
  SELECT COUNT(*) INTO v_eligible
    FROM tbl_service AS s
   WHERE s.service_id = p_service_id AND s.account_id = v_staff AND s.service_status = 3
     AND s.service_id <> v_source AND s.service_start > v_after;
  IF v_eligible = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:follow_up';
  END IF;
  IF p_service_id <=> v_follow_up THEN
    LEAVE body;
  END IF;

  UPDATE tbl_coaching_action
     SET follow_up_service_id = p_service_id, version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open';
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, service_id)
  VALUES (p_action_id, 'follow_up_linked', p_caller, 'admin', p_service_id);
  IF v_follow_up IS NOT NULL THEN
    INSERT INTO tbl_coaching_event
      (action_id, event_type, author_id, author_role, body, service_id, marker_id, marker_seconds)
    SELECT m.action_id, 'marker_removed', p_caller, 'admin', NULLIF(m.note, ''), m.service_id, m.marker_id,
           m.offset_seconds
      FROM tbl_coaching_marker AS m
     WHERE m.action_id = p_action_id AND m.service_id = v_follow_up AND m.removed_at IS NULL
     ORDER BY m.offset_seconds, m.marker_id;
    UPDATE tbl_coaching_marker
       SET removed_by = p_caller, removed_at = NOW(3)
     WHERE action_id = p_action_id AND service_id = v_follow_up AND removed_at IS NULL;
  END IF;
END body $$

-- -----------------------------------------------------------------------------
-- sp_coaching_complete(caller, action_id, expected_version, conclusion)：結案為完成（管理員）。
--   要有後續服務（coaching:no_follow_up）和結論（coaching:invalid）。員工的確認、AI 分數都不會讓行動完成：
--   只有這支程序把狀態改成 completed。version +1。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_complete $$
CREATE PROCEDURE sp_coaching_complete(
  IN p_caller           VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_conclusion       VARCHAR(2000) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：結案為完成（管理員）：要有後續服務和結論'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.version, a.follow_up_service_id INTO v_status, v_version, v_follow_up
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF v_follow_up IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:no_follow_up';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_conclusion, ''))) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  UPDATE tbl_coaching_action
     SET action_status = 'completed', conclusion = TRIM(p_conclusion), concluded_by = p_caller,
         concluded_at = NOW(3), version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open'
     AND follow_up_service_id IS NOT NULL;
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body, service_id)
  VALUES (p_action_id, 'completed', p_caller, 'admin', TRIM(p_conclusion), v_follow_up);
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_adjust(caller, action_id, expected_version, reason, goal, due_date)：調整（管理員，進行中）。
--   換成新的目標、期限，原因必填；舊的值和新的值都寫進 adjusted 歷史（goal_before／after、due_before／after），
--   那一次看過的後續服務也記在歷史（service_id），並從行動上拿掉：下一次要選更晚的一筆。
--   員工的確認清掉（要再確認新的目標）。version +1。沒有後續服務也可以調整（例如改期限）。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_adjust $$
CREATE PROCEDURE sp_coaching_adjust(
  IN p_caller           VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_reason           VARCHAR(2000) CHARACTER SET utf8mb4,
  IN p_goal             VARCHAR(300)  CHARACTER SET utf8mb4,
  IN p_due_date         DATE
)
  MODIFIES SQL DATA
  COMMENT 'W2：調整目標、期限（管理員）：原因必填，舊的值留在歷史；看過的後續服務拿掉、員工要再確認'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;
  DECLARE v_goal VARCHAR(300) DEFAULT NULL;
  DECLARE v_due DATE DEFAULT NULL;
  DECLARE v_follow_up VARCHAR(32) DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.version, a.goal, a.due_date, a.follow_up_service_id
    INTO v_status, v_version, v_goal, v_due, v_follow_up
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_reason, ''))) = 0 OR CHAR_LENGTH(TRIM(COALESCE(p_goal, ''))) = 0
     OR p_due_date IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  UPDATE tbl_coaching_action
     SET goal = TRIM(p_goal), due_date = p_due_date, follow_up_service_id = NULL, acknowledged_at = NULL,
         version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open';
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event
    (action_id, event_type, author_id, author_role, body, goal_before, goal_after, due_before, due_after, service_id)
  VALUES (p_action_id, 'adjusted', p_caller, 'admin', TRIM(p_reason), v_goal, TRIM(p_goal), v_due, p_due_date,
          v_follow_up);
END $$

-- -----------------------------------------------------------------------------
-- sp_coaching_cancel(caller, action_id, expected_version, reason)：撤回（管理員，進行中）：建錯了、不再適用等。
--   原因必填（寫在 conclusion）；行動和歷史都留著，狀態是 cancelled。version +1。
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_coaching_cancel $$
CREATE PROCEDURE sp_coaching_cancel(
  IN p_caller           VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_action_id        VARCHAR(36)   CHARACTER SET utf8mb4,
  IN p_expected_version INT UNSIGNED,
  IN p_reason           VARCHAR(2000) CHARACTER SET utf8mb4
)
  MODIFIES SQL DATA
  COMMENT 'W2：撤回（管理員）：原因必填；行動和歷史都留著'
BEGIN
  DECLARE v_level CHAR(1) DEFAULT NULL;
  DECLARE v_status VARCHAR(16) DEFAULT NULL;
  DECLARE v_version INT UNSIGNED DEFAULT NULL;

  SELECT ac.account_level INTO v_level
    FROM tbl_account AS ac
   WHERE ac.account_id = p_caller;
  IF v_level IS NULL OR v_level = '1' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:forbidden';
  END IF;
  SELECT a.action_status, a.version INTO v_status, v_version
    FROM tbl_coaching_action AS a
   WHERE a.action_id = p_action_id;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_found';
  END IF;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:not_open';
  END IF;
  IF v_version <> p_expected_version THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  IF CHAR_LENGTH(TRIM(COALESCE(p_reason, ''))) = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:invalid';
  END IF;

  UPDATE tbl_coaching_action
     SET action_status = 'cancelled', conclusion = TRIM(p_reason), concluded_by = p_caller, concluded_at = NOW(3),
         version = version + 1
   WHERE action_id = p_action_id AND version = p_expected_version AND action_status = 'open';
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'coaching:changed';
  END IF;
  INSERT INTO tbl_coaching_event (action_id, event_type, author_id, author_role, body)
  VALUES (p_action_id, 'cancelled', p_caller, 'admin', TRIM(p_reason));
END $$

DELIMITER ;
-- [END PLAN1 W2]

-- [BEGIN SQS] 分析工作佇列（2026-11-02，T49）：3 張表，和 new-feature/migrations/007_analysis_queue.sql 相同（拿掉註解）。
--   只有分析服務讀寫，桌面端不讀；狀態 2 由分析服務在接受工作的交易裡寫。
CREATE TABLE IF NOT EXISTS tbl_analysis_job (
  service_id      VARCHAR(32)      NOT NULL,
  job_state       VARCHAR(16)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'queued',
  attempts        TINYINT UNSIGNED NOT NULL DEFAULT 0,
  max_attempts    TINYINT UNSIGNED NOT NULL DEFAULT 3,
  lease_token     CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NULL,
  lease_until     DATETIME(3)      NULL,
  last_error      VARCHAR(1000)    NULL,
  result_token    CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NULL,
  report_data_key VARCHAR(255)     NULL,
  accepted_at     DATETIME(3)      NOT NULL,
  started_at      DATETIME(3)      NULL,
  finished_at     DATETIME(3)      NULL,
  updated_at      DATETIME(3)      NOT NULL,

  PRIMARY KEY (service_id),
  KEY idx_analysis_job_state_updated (job_state, updated_at),
  CONSTRAINT fk_analysis_job_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_analysis_job_state CHECK (job_state IN ('queued', 'running', 'done', 'failed')),
  CONSTRAINT chk_analysis_job_lease CHECK ((lease_token IS NULL) = (lease_until IS NULL)
                                           AND (job_state = 'running' OR lease_token IS NULL)),
  CONSTRAINT chk_analysis_job_attempts CHECK (attempts <= max_attempts)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析工作（SQS）：認領、續期、嘗試次數；時間都是 UTC';

CREATE TABLE IF NOT EXISTS tbl_analysis_outbox (
  event_id        CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  service_id      VARCHAR(32)      NOT NULL,
  outbox_state    VARCHAR(8)       CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending',
  send_attempts   INT UNSIGNED     NOT NULL DEFAULT 0,
  next_attempt_at DATETIME(3)      NOT NULL,
  last_error      VARCHAR(1000)    NULL,
  sqs_message_id  VARCHAR(128)     NULL,
  created_at      DATETIME(3)      NOT NULL,
  sent_at         DATETIME(3)      NULL,

  PRIMARY KEY (event_id),
  KEY idx_analysis_outbox_due (outbox_state, next_attempt_at),
  KEY idx_analysis_outbox_service (service_id),
  CONSTRAINT fk_analysis_outbox_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_analysis_outbox_state CHECK (outbox_state IN ('pending', 'sent')),
  CONSTRAINT chk_analysis_outbox_sent CHECK ((outbox_state = 'sent') = (sent_at IS NOT NULL))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '還要送到 SQS 的分析工作（transactional outbox）；時間都是 UTC';

CREATE TABLE IF NOT EXISTS tbl_analysis_worker (
  worker_id       VARCHAR(64)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  worker_state    VARCHAR(16)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  service_id      VARCHAR(32)      NULL,
  detail          VARCHAR(255)     NULL,
  started_at      DATETIME(3)      NOT NULL,
  seen_at         DATETIME(3)      NOT NULL,

  PRIMARY KEY (worker_id),
  KEY idx_analysis_worker_seen (seen_at),
  CONSTRAINT chk_analysis_worker_state CHECK (worker_state IN ('loading', 'ready', 'busy', 'failed', 'stopped'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析 worker 的心跳；時間都是 UTC';
-- [END SQS]

INSERT INTO tbl_account
  (account_id, account_username, account_password, account_name, account_level, account_occupation_name)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'placeholder_admin', 'pbkdf2_sha256$600000$cgVY313FJUR69y4dXxyOMQ==$eHWSr/MfoTPlHfdiqXAtoZ1hjAd+yHCHtKEpMohYVIs=',
   '系統管理員（佔位）', '5', '管理員'),
  ('00000000-0000-0000-0000-000000000002', 'placeholder_staff', 'pbkdf2_sha256$600000$doxDRtoZkx6jkZtdTm6mag==$KXUvW7wqZBcyrKgl9Kz6tgqgfpGhVxCHEfBFN+kO7OE=',
   '服務人員（佔位）',   '1', '服務人員')
ON DUPLICATE KEY UPDATE account_id = account_id;
