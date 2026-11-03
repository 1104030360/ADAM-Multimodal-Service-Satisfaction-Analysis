-- =============================================================================
-- 007：分析工作佇列（Amazon SQS，docs/AWS tutorial/05_Amazon SQS.md、docs/rebuild/sqs/PLAN.md）。2026-11-02，T49
-- 對象：已經用 docs/rebuild/db_schema.sql 建好、而且照編號套用過 001～006 的資料庫（RDS、本機的 adam-mysql）。
--       新建的資料庫直接跑 db_schema.sql 就有這些，不用再跑這個檔。
-- 執行：用 adam_admin（或 RDS 主帳號）在 006 之後執行。可以重跑：只有 CREATE TABLE IF NOT EXISTS，
--       不 DROP、不改既有的資料表與程序，所以不會拿掉其他帳號的 EXECUTE（T42 第 9 節）。
--   mysql -h <RDS 端點> -P 3306 -u adam_admin -p \
--         --ssl-ca=global-bundle.pem --ssl-mode=VERIFY_IDENTITY adam < 007_analysis_queue.sql
--   本機：docker exec -i -e MYSQL_PWD=<root 密碼> adam-mysql mysql -uroot adam < 007_analysis_queue.sql
--   RDS 也可以用 apply_007.sh（套用這個檔和 grants_007.sql，最後跑 check_007.sql）。
-- 內容：三張表，只有分析服務（adam_analysis）讀寫，桌面端不讀，和 db_schema.sql 第 12～14 節相同：
--   1. tbl_analysis_job：每筆服務一列的分析工作。跨 process 認領（lease_token、lease_until）、這一輪的嘗試次數、
--      最後的錯誤，以及提交成功的那一次（result_token、report_data_key）。
--   2. tbl_analysis_outbox：API 和工作在同一個交易寫入的「還要送到 SQS」紀錄；背景送出器送到 SQS 之後標成 sent。
--   3. tbl_analysis_worker：每個 worker process 一列（模型載入中、可以接工作、正在做哪一筆），/healthz 用它判斷
--      有沒有 worker 活著。
--   時間欄位（*_at、lease_until）一律是 UTC：程式用 UTC_TIMESTAMP(3) 寫入、比較，不受連線的 time_zone
--   （America/Los_Angeles，PLAN R4）和夏令時間影響；也只用資料庫這一個時鐘，各台主機的時鐘不準也沒關係。
--   最後一段的 GRANT 是註解：有建立 adam_analysis 最小權限帳號的，執行 grants_007.sql（內容相同）。
--   桌面端的 adam_app 不需要新的權限：狀態 2 改由分析服務在接受工作的交易裡寫入，桌面端只讀 service_status。
-- 順序：先套用這個檔和 grants_007.sql，再換新版的分析服務（API＋worker），最後換新版的桌面端
--   （新版桌面端收到 200 之後不再自己寫狀態 2；舊版的分析服務不會寫，所以不能先換桌面端）。
-- =============================================================================
SET NAMES utf8mb4;
USE adam;

-- 1. 分析工作：一筆服務一列（這一版不支援已完成服務的 force 重跑，PLAN 5-1）
CREATE TABLE IF NOT EXISTS tbl_analysis_job (
  service_id      VARCHAR(32)      NOT NULL,
  -- queued：等 worker 領；running：有 worker 拿著 lease 在算；done：結果已提交（服務狀態 3）；
  -- failed：這一輪的嘗試用完、或管理員判定失敗（服務狀態回 1，桌面端的 Retry 會開新的一輪）
  job_state       VARCHAR(16)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'queued',
  -- 這一輪已經認領幾次（每次認領 +1；收到 SIGTERM 正常交還時不算）。到 max_attempts 還失敗就是 failed。
  -- 這是同一筆服務所有 SQS 訊息合計的次數；SQS 的 maxReceiveCount 只管單一訊息（PLAN 第 8 節）
  attempts        TINYINT UNSIGNED NOT NULL DEFAULT 0,
  max_attempts    TINYINT UNSIGNED NOT NULL DEFAULT 3,
  -- 認領時產生的 uuid4（32 個十六進位字）。只有 lease 還沒到期、token 相同的 worker 能續期、提交；
  -- 輸出檔名也帶它（PLAN 6-4），舊 worker 晚到的上傳不會蓋掉接手者引用的檔案
  lease_token     CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NULL,
  lease_until     DATETIME(3)      NULL,
  last_error      VARCHAR(1000)    NULL,
  -- 提交成功的那一次：它的 token（也是服務結果 W1 的 run 編號）和 report-data 的 S3 key（報表從這裡讀）
  result_token    CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NULL,
  report_data_key VARCHAR(255)     NULL,
  accepted_at     DATETIME(3)      NOT NULL,   -- API 最後一次接受（新建或重開這一輪）
  started_at      DATETIME(3)      NULL,       -- 最後一次認領
  finished_at     DATETIME(3)      NULL,       -- done 或 failed 的時間
  updated_at      DATETIME(3)      NOT NULL,

  PRIMARY KEY (service_id),
  -- 找卡住的工作（queued／running 很久沒有動靜）
  KEY idx_analysis_job_state_updated (job_state, updated_at),
  CONSTRAINT fk_analysis_job_service FOREIGN KEY (service_id)
    REFERENCES tbl_service (service_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT chk_analysis_job_state CHECK (job_state IN ('queued', 'running', 'done', 'failed')),
  -- 只有 running 有 lease；token 和到期時間一起有、一起沒有
  CONSTRAINT chk_analysis_job_lease CHECK ((lease_token IS NULL) = (lease_until IS NULL)
                                           AND (job_state = 'running' OR lease_token IS NULL)),
  CONSTRAINT chk_analysis_job_attempts CHECK (attempts <= max_attempts)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析工作（SQS）：認領、續期、嘗試次數；時間都是 UTC';

-- 2. outbox：和工作同一個交易寫入，送出器送到 SQS 之後標成 sent（PLAN 5-2）
CREATE TABLE IF NOT EXISTS tbl_analysis_outbox (
  event_id        CHAR(32)         CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- uuid4，也放進 SQS 訊息
  service_id      VARCHAR(32)      NOT NULL,
  outbox_state    VARCHAR(8)       CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending',
  send_attempts   INT UNSIGNED     NOT NULL DEFAULT 0,
  -- 下一次可以送的時間：送失敗時往後退避；送出器拿走一筆時也先往後推，兩個 process 不會同時送同一筆
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

-- 3. worker：每個 worker process 一列，/healthz 用 seen_at 判斷還活著沒有
CREATE TABLE IF NOT EXISTS tbl_analysis_worker (
  worker_id       VARCHAR(64)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- 主機名稱:pid
  -- loading：載入模型中；ready：等工作；busy：正在做 service_id；failed：模型載入失敗；stopped：正常結束
  worker_state    VARCHAR(16)      CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  service_id      VARCHAR(32)      NULL,       -- busy 時是哪一筆（不設外鍵：服務刪了也不影響）
  detail          VARCHAR(255)     NULL,       -- 例如 Whisper 的大小、載入失敗的原因
  started_at      DATETIME(3)      NOT NULL,
  seen_at         DATETIME(3)      NOT NULL,

  PRIMARY KEY (worker_id),
  KEY idx_analysis_worker_seen (seen_at),
  CONSTRAINT chk_analysis_worker_state CHECK (worker_state IN ('loading', 'ready', 'busy', 'failed', 'stopped'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = '分析 worker 的心跳；時間都是 UTC';

-- 4. GRANT（選用）：有 adam_analysis 最小權限帳號的才執行，內容和 grants_007.sql 相同。
--    API 接受工作、送出器、worker、管理指令都用這個帳號。outbox 和 worker 要 DELETE：清掉七天前已送出的紀錄、
--    很久沒出現的 worker。桌面端的 adam_app 不需要。
-- GRANT SELECT, INSERT, UPDATE ON adam.tbl_analysis_job TO 'adam_analysis'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_outbox TO 'adam_analysis'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_worker TO 'adam_analysis'@'%';
