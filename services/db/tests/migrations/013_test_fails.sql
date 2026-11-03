-- 測試專用：中途失敗（第二句的資料表不存在）。第一句成功了也不會記錄，後面的 migration 不跑。
SET NAMES utf8mb4;
USE adam;

CREATE TABLE IF NOT EXISTS tbl_test_partial (id INT NOT NULL PRIMARY KEY);
ALTER TABLE tbl_test_does_not_exist ADD COLUMN x INT NULL;
