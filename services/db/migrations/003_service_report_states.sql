-- =============================================================================
-- new-feature 003：分析頁的報表狀態（N4，PLAN 7.3 的「PDF」按鈕）。2026-11-01
-- 對象：已經用 docs/rebuild/db_schema.sql 建好、而且套用過 001_photo_and_reports.sql 的資料庫
--       （RDS、本機的 adam-mysql）。新建的資料庫直接跑 db_schema.sql 就有，不用再跑這個檔。
-- 內容：新增預存程序 sp_list_service_report_states(account_id)。只新增程序，不改資料表，舊版桌面程式不受影響。
-- 執行：用 adam_admin（或 RDS 主帳號）執行。DROP/CREATE PROCEDURE，可以重跑。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 003_service_report_states.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 003_service_report_states.sql
-- 最後一段的 GRANT 是註解：有建立 adam_app 最小權限帳號的才需要（db_schema.sql 的選用區塊）。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

DELIMITER $$

-- -----------------------------------------------------------------------------
-- sp_list_service_report_states(account_id)（new-feature PLAN 7.3，2026-11-01 新增，migration 003）
-- 呼叫：服務人員端分析頁 Completed 清單的 Report 欄（Not generated yet／Not published yet／打開 PDF），
--       db lane 上用 cursor.execute("CALL sp_list_service_report_states(%s)", (account_id,))。
-- sp_list_reports 不給服務人員看還沒發佈的列（R6），所以另外一支：只回傳「狀態」，不回傳還沒發佈的報表本身。
--   - 只看 staff_account_id = 自己的每次服務報表（管理員沒有自己的服務：0 列；找不到帳號：0 列）。
--   - 每筆服務一列，只列已經有 PDF（report_status = ready）的服務：
--       report_state = 'published'：發佈過；published_report_id 是最新一筆已發佈的
--                                  （和 sp_list_reports 給服務人員的那一筆相同）。
--       report_state = 'unpublished'：有 PDF、但還沒有任何一筆發佈；published_report_id 是 NULL。
--   - 沒有出現的服務：還沒有 PDF（沒產生過，或只有產生中、失敗的）。
-- 限制同 sp_list_reports：所有桌面程式共用 adam_app（PLAN 7.2、第 11 節）。
-- -----------------------------------------------------------------------------
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


-- =============================================================================
-- 最小權限帳號（選用；有建立 adam_app 才執行，把註解拿掉）。說明見 db_schema.sql 的選用區塊
-- =============================================================================
-- GRANT EXECUTE ON PROCEDURE adam.sp_list_service_report_states TO 'adam_app'@'%';
