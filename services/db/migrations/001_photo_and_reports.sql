-- =============================================================================
-- new-feature 001：帳號照片（N3）、報表（N4、N5）。2026-11-01
-- 對象：已經用 docs/rebuild/db_schema.sql 建好的資料庫（RDS、本機的 adam-mysql）。
--       新建的資料庫直接跑 db_schema.sql 就有這些，不用再跑這個檔。
-- 執行：用 adam_admin（或 RDS 主帳號）執行一次。ALTER TABLE 不能重跑（欄位已經存在會報錯）；
--       CREATE TABLE IF NOT EXISTS 與 DROP/CREATE PROCEDURE 可以重跑。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 001_photo_and_reports.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 001_photo_and_reports.sql
-- 最後一段的 GRANT 是註解：有建立 adam_app、adam_analysis 最小權限帳號的才需要（db_schema.sql 的選用區塊）。
-- S3：桌面端的 AWS profile 要能讀寫 avatars/*（PutObject、GetObject、DeleteObject）、讀 reports/*（GetObject）；
--     分析服務的 EC2 role 要能讀 avatars/*、寫 reports/*（PLAN 6、7.4）。這個檔案不改 IAM。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

-- N3：大頭照在 S3 的 key（avatars/{account_id}-{時間}.jpg）；沒有照片是 NULL
ALTER TABLE tbl_account
  ADD COLUMN account_photo_key VARCHAR(255) NULL AFTER account_occupation_name;


-- =============================================================================
-- tbl_report：報表（new-feature PLAN 7.1，N4、N5，2026-11-01 新增）
--    寫入：桌面端（管理員）在 db lane 新增一列（report_status = generating），再請分析服務的 /reports 產生；
--          分析服務只把 report_status 走到 ready 或 failed（PLAN R1）；發佈由桌面端寫 published_at、published_by（R6）。
--    讀取：桌面端一律經由 sp_list_reports，依帳號等級決定看得到哪些列（PLAN 7.2）。
--    同一個對象可以重新產生，每次一筆新紀錄，舊的留下（R5）；畫面只顯示每個對象最新的一筆。
-- =============================================================================
CREATE TABLE IF NOT EXISTS tbl_report (
  -- 桌面端用 uuid4 產生（36 字元）。
  report_id          VARCHAR(36)  NOT NULL,

  -- service：每次服務一份；staff：每位員工一段期間；company：全公司一段期間。
  report_kind        VARCHAR(16)  NOT NULL,

  -- 每次服務報表才有。
  service_id         VARCHAR(32)  NULL,

  -- 每次服務、員工報表才有（每次服務報表是那筆服務的 account_id）；全公司報表是 NULL。
  staff_account_id   VARCHAR(36)  NULL,

  -- 員工、全公司報表的期間，含頭尾兩天。日期依 America/Los_Angeles 切（PLAN R4）。
  period_start       DATE         NULL,
  period_end         DATE         NULL,

  -- generating：產生中；ready：PDF 已上傳（不表示服務人員看得到，看不看得到由 published_at 決定）；failed：失敗。
  report_status      VARCHAR(16)  NOT NULL DEFAULT 'generating',

  -- failed 的原因（英文短句，畫面直接顯示，PLAN 7.3）。7.1 的欄位表沒有列，為了「顯示 failed 與原因」而加。
  failure_reason     VARCHAR(255) NULL,

  -- reports/{report_id}.pdf
  s3_key             VARCHAR(255) NOT NULL,

  -- 產生報表的管理員。
  created_by         VARCHAR(36)  NOT NULL,

  -- 毫秒：同一秒內重新產生時，仍分得出哪一筆比較新。
  created_at         DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

  -- 管理員按 Publish 的時間與帳號。NULL 代表還沒發佈，服務人員看不到（R6）。已發佈再按一次不改時間。
  published_at       DATETIME     NULL,
  published_by       VARCHAR(36)  NULL,

  -- N6 才用：說明是哪個模型、哪一版提示產生的。
  ai_model           VARCHAR(100) NULL,
  ai_prompt_version  VARCHAR(32)  NULL,

  PRIMARY KEY (report_id),
  -- sp_list_reports 依對象分組取最新一筆；服務人員只看自己的
  KEY idx_report_staff_kind (staff_account_id, report_kind, created_at),
  KEY idx_report_service (service_id, created_at),
  -- 把逾時的 generating 改成 failed
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
  -- 每一種報表該有的欄位
  CONSTRAINT chk_report_subject CHECK (
       (report_kind = 'service' AND service_id IS NOT NULL AND staff_account_id IS NOT NULL
        AND period_start IS NULL AND period_end IS NULL)
    OR (report_kind = 'staff' AND service_id IS NULL AND staff_account_id IS NOT NULL
        AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)
    OR (report_kind = 'company' AND service_id IS NULL AND staff_account_id IS NULL
        AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
  -- 只有 ready 能發佈；發佈的時間和帳號一起寫
  CONSTRAINT chk_report_published CHECK (
    (published_at IS NULL AND published_by IS NULL)
    OR (published_at IS NOT NULL AND published_by IS NOT NULL AND report_status = 'ready'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '報表（new-feature N4、N5）。PDF 在 S3 的 reports/{report_id}.pdf；published_at 是 NULL 時服務人員看不到';


DELIMITER $$

-- -----------------------------------------------------------------------------
-- sp_list_reports(account_id)（new-feature PLAN 7.2，2026-11-01 新增）
-- 呼叫：桌面端的報表清單，db lane 上用 cursor.execute("CALL sp_list_reports(%s)", (account_id,))。
-- 由資料庫依等級決定回傳哪些列，桌面端不自己組條件：
--   - 服務人員（等級 '1'）：只有自己的（staff_account_id = 自己）、已發佈的，不含 company。
--   - 管理員（等級 '2'～'5'）：全部，含尚未發佈。
--   - 找不到帳號：0 列。
-- 每個對象（種類、服務、員工、期間）只回傳最新的一筆（R5）。服務人員先篩掉未發佈的再取最新，
--   所以新的一筆還沒發佈時，仍看到上一筆已發佈的，不會變成空白（7.1）。
-- 新的在前。staff_name、staff_username 是報表對象目前的名字和登入帳號（company 是 NULL）；
--   service_start、service_end 只有每次服務報表才有。
-- 限制：所有桌面程式共用 adam_app，這裡擋不住直接查資料表的人（PLAN 7.2、第 11 節）。
-- -----------------------------------------------------------------------------
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

DELIMITER ;


-- =============================================================================
-- 最小權限帳號（選用；有建立這兩個帳號才執行，把註解拿掉）。說明見 db_schema.sql 的選用區塊
-- =============================================================================
-- GRANT SELECT (account_id, account_username, account_name, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_app'@'%';
-- GRANT SELECT, INSERT ON adam.tbl_report TO 'adam_app'@'%';
-- GRANT UPDATE (report_status, failure_reason, published_at, published_by) ON adam.tbl_report TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_list_reports TO 'adam_app'@'%';
--
-- GRANT SELECT ON adam.tbl_report TO 'adam_analysis'@'%';
-- GRANT UPDATE (report_status, failure_reason, ai_model, ai_prompt_version) ON adam.tbl_report TO 'adam_analysis'@'%';
-- GRANT SELECT (service_id, account_id, service_start, service_end, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
-- GRANT SELECT ON adam.tbl_service_score TO 'adam_analysis'@'%';
-- GRANT SELECT (account_id, account_name, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_analysis'@'%';
