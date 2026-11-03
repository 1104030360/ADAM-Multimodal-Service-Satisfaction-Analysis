-- =============================================================================
-- 測試專用（tools/verify.sh、tests/run.sh）：在拋棄式的 MySQL 8.4 照正式環境建立帳號。密碼只給這種資料庫用。
-- 用 root 在載入 schema.sql 之前執行（GRANT … ON adam.* 不需要資料庫已經存在）。
--   adam_admin：像 RDS 的 master 帳號（主要的權限，沒有 SYSTEM_USER、SUPER）。schema.sql 用它載入，程序的 DEFINER
--     才會和正式環境一樣：root 有 SYSTEM_USER，root 建的程序 adam_migrator 改不了也刪不掉（1227），RDS 上不會這樣。
--   adam_migrator：PLAN 第 8 節第 8 步，權限一字不差（沒有 DROP、讀不到 mysql.*、不能建帳號）。
--   adam_app、adam_analysis：桌面端、分析服務。權限在 test_grants.sql（要等資料表、程序建好才能給）。
-- =============================================================================
CREATE USER 'adam_admin'@'%' IDENTIFIED BY 'adam-test-only-admin';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, RELOAD, PROCESS, REFERENCES, INDEX, ALTER, SHOW DATABASES,
  CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, REPLICATION SLAVE, REPLICATION CLIENT, CREATE VIEW, SHOW VIEW,
  CREATE ROUTINE, ALTER ROUTINE, CREATE USER, EVENT, TRIGGER ON *.* TO 'adam_admin'@'%' WITH GRANT OPTION;

CREATE USER 'adam_migrator'@'%' IDENTIFIED BY 'adam-test-only-migrator' REQUIRE SSL;
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, CREATE ROUTINE, ALTER ROUTINE, EXECUTE
  ON adam.* TO 'adam_migrator'@'%' WITH GRANT OPTION;

CREATE USER 'adam_app'@'%' IDENTIFIED BY 'adam-test-only-app' REQUIRE SSL;
CREATE USER 'adam_analysis'@'%' IDENTIFIED BY 'adam-test-only-analysis' REQUIRE SSL;
