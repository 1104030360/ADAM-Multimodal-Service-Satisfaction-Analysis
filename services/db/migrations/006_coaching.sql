-- =============================================================================
-- new-feature 006：員工輔導與後續追蹤（PLAN1 W2）。2026-11-01，T45
-- 對象：已經用 docs/rebuild/db_schema.sql 建好、而且照編號套用過 001～004 的資料庫（RDS、本機的 adam-mysql）。
--       W1 的 005 和這個檔案不碰同一張表，誰先誰後都可以。新建的資料庫直接跑 db_schema.sql 就有這些，不用再跑這個檔。
-- 執行：用 adam_admin（或 RDS 主帳號）執行一次。可以重跑：資料表用 CREATE TABLE IF NOT EXISTS，程序用
--       CREATE PROCEDURE IF NOT EXISTS（MySQL 8.0.29 以上，RDS 用 8.4）。已經有的不碰、也不 DROP：
--       DROP PROCEDURE 會連帶拿掉別的帳號在那個程序上的 EXECUTE（T42 第 9 節）。已經有的時候 mysql 會顯示
--       Note 1050／1304（already exists），不是錯誤。要換掉程序的內容時，先 DROP 那一支再執行這個檔，然後重新 GRANT。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 006_coaching.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 006_coaching.sql
-- 內容：
--   1. 三張新的資料表：tbl_coaching_action（改善行動）、tbl_coaching_event（只新增、不修改的歷史）、
--      tbl_coaching_marker（時間註記）。不改既有的資料表，舊版桌面程式不受影響。
--   2. 13 個預存程序 sp_coaching_*。桌面端的讀寫全部經過它們，不直接讀寫這三張表。每個程序都帶呼叫者的
--      account_id，由資料庫依等級決定能看什麼、做什麼：
--        服務人員（等級 1）：只看得到自己是當事人的行動；只能回應（Respond）和確認（Acknowledge）。
--        管理員（等級 2～5）：看得到全部；建立、加減時間註記、連結後續服務、完成、調整、撤回、回應。
--      寫入的程序不 COMMIT：桌面端包在 START TRANSACTION … COMMIT 裡（和 db_schema.sql 的其他程序一樣，交易範圍由
--      呼叫端決定），中途失敗整筆 rollback。不能做的事用 SIGNAL SQLSTATE '45000' 回報（errno 1644），
--      MESSAGE_TEXT 是 coaching:<原因>：forbidden、not_found、not_open、changed（樂觀鎖的版本不對）、invalid、
--      not_staff、follow_up（不能當後續服務）、no_follow_up（沒有後續服務不能完成）、marker_service。
--   3. 最後一段的 GRANT 是註解：有建立 adam_app 最小權限帳號的才需要（db_schema.sql 的選用區塊），把註解拿掉再執行。
--      adam_app 只要這 13 個程序的 EXECUTE，不需要三張表的任何權限（程序以建立者的權限執行），
--      所以桌面端的帳號改不了、刪不掉歷史，也繞不過「只能往前走」的狀態規則。adam_analysis 不需要任何權限。
-- 限制（同 sp_list_reports，PLAN 7.2、T37、T43）：所有桌面程式共用 adam_app，「呼叫者是誰」是桌面端傳進來的參數，
--   拿到 .env 的人可以帶別人的 account_id 呼叫。正式跨分行或多人部署前，要改成每人自己的資料庫身分或後端 API（PLAN1
--   「共用實作原則與驗證」）。等級不代表分行：這一版是單一分行的假設，所有管理員看得到全部的行動。
-- S3、IAM：不用改（時間註記只存秒數，影片照舊從 video/ 下載）。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_list(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_detail(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_for_service(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_follow_up_options(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_create(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_add_marker(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_remove_marker(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_respond(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_acknowledge(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_link_follow_up(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_complete(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_adjust(
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
CREATE PROCEDURE IF NOT EXISTS sp_coaching_cancel(
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


-- =============================================================================
-- 最小權限帳號（選用；有建立 adam_app 才執行，把註解拿掉）。說明見 db_schema.sql 的選用區塊。
-- 只有程序的 EXECUTE：三張表不給 adam_app 任何權限（程序以建立者的權限執行）。adam_analysis 不需要。
-- =============================================================================
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_list TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_detail TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_for_service TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_follow_up_options TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_create TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_add_marker TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_remove_marker TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_respond TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_acknowledge TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_link_follow_up TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_complete TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_adjust TO 'adam_app'@'%';
-- GRANT EXECUTE ON PROCEDURE adam.sp_coaching_cancel TO 'adam_app'@'%';
